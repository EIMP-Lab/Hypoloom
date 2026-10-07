"""HTTP observation and explicit operator Grounding answers for one Hypoloom run.

The server owns no reducer, active-run pointer, or durable state. Its disposable
run-bound refresh reader coalesces authorized requests for the
configured ``state_root`` and ``run_id`` and may enrich it through the existing
read-only Host ``STATUS`` command.  Static assets are deliberately public; the
snapshot endpoint is protected by one ephemeral in-memory bearer token so that
another user on the same shared host cannot read run contents by guessing the
loopback port.  The standalone CLI owns a foreground lifecycle; the Textual TUI
may own the same server in a thread for exactly its own process lifetime.
Read links remain read-only; a separately granted ephemeral answer token permits
only operator-submitted ANSWER requests through the existing Host socket.
"""

from __future__ import annotations

import argparse
import gzip
import hmac
import json
import math
import secrets
import select
import socket
import sqlite3
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .monitor import MonitorError, MonitorSnapshot, build_observer_snapshot
from .notification_tailer import load_notification_config
from .observation_api import build_product_observation, read_experiment_log
from .observer_refresh import ObserverRefresh
from .state_store import StateStoreError


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 0
_ASSET_ROOT = Path(__file__).with_name("web_observer_assets")
_ASSETS: Mapping[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/style.css": ("style.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/messages.json": ("messages.json", "application/json; charset=utf-8"),
}
_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Cache-Control", "no-store"),
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    ),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
)

SnapshotBuilder = Callable[[str | Path, str], MonitorSnapshot]


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _require_loopback_host(value: object) -> str:
    host = _require_text(value, "host")
    if host != DEFAULT_HOST:
        raise ValueError(
            f"host must be {DEFAULT_HOST}; use an SSH tunnel for remote access"
        )
    return host


class WebObserverServer(ThreadingHTTPServer):
    """One HTTP observer bound to one explicit run projection."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        state_root: str | Path,
        run_id: str,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        token: str | None = None,
        snapshot_builder: SnapshotBuilder = build_observer_snapshot,
        asset_root: str | Path = _ASSET_ROOT,
        gpu_pool_socket: str | Path | None = None,
        gpu_pool_server_uid: int | None = None,
    ) -> None:
        host = _require_loopback_host(host)
        _require_text(run_id, "run_id")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 0 <= port <= 65535
        ):
            raise ValueError("port must be an integer from 0 through 65535")
        if token is not None:
            _require_text(token, "token")
        if not callable(snapshot_builder):
            raise ValueError("snapshot_builder must be callable")

        self.state_root = Path(state_root).expanduser().resolve()
        self.run_id = run_id
        self._access_token = token or secrets.token_urlsafe(24)
        self.snapshot_builder = snapshot_builder
        self.refresh_reader = ObserverRefresh(self.state_root, run_id)
        self.shared_refresh = snapshot_builder is build_observer_snapshot
        self.asset_root = Path(asset_root).resolve()
        if gpu_pool_server_uid is not None and (type(gpu_pool_server_uid) is not int or gpu_pool_server_uid < 0 or gpu_pool_socket is None):
            raise ValueError("pool server UID requires an explicit socket and nonnegative integer")
        self.gpu_pool_socket = None if gpu_pool_socket is None else Path(gpu_pool_socket).expanduser().absolute()
        self.gpu_pool_server_uid = gpu_pool_server_uid
        self.log_requests = True
        self._closing = threading.Event()
        self._active_request_lock = threading.Lock()
        self._active_requests: set[socket.socket] = set()
        self._support_lock = threading.Lock()
        self._trends_lock = threading.Lock()
        self._answer_token: str | None = None
        self._provider_token: str | None = None
        self._literature_token: str | None = None
        super().__init__((host, port), WebObserverRequestHandler)

    @property
    def access_token(self) -> str:
        """Return the ephemeral token for embedding in the one startup URL."""

        return self._access_token

    @property
    def is_closing(self) -> bool:
        """Return whether this process has begun the irreversible stop path."""

        return self._closing.is_set()

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        """Accept and register a connection before a handler can outlive shutdown."""

        request, client_address = super().get_request()
        with self._active_request_lock:
            self._active_requests.add(request)
        return request, client_address

    def verify_request(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> bool:
        """Reject a connection accepted concurrently with shutdown."""

        return not self.is_closing and super().verify_request(request, client_address)

    def process_request(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        """Avoid creating a handler after the owner has begun shutdown."""

        if self.is_closing:
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.shutdown_request(request)
            raise

    def shutdown_request(self, request: socket.socket) -> None:
        try:
            super().shutdown_request(request)
        finally:
            with self._active_request_lock:
                self._active_requests.discard(request)

    def begin_shutdown(self) -> None:
        """Reject new work and close every connection already accepted.

        This non-blocking step is safe to repeat.  A threaded owner should call it
        before the inherited blocking ``shutdown()`` and final ``server_close()``.
        """

        with self._active_request_lock:
            self._closing.set()
            requests = tuple(self._active_requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def shutdown(self) -> None:
        self.begin_shutdown()
        super().shutdown()

    def server_close(self) -> None:
        self.begin_shutdown()
        super().server_close()
        self.refresh_reader.close()

    def handle_error(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        error = sys.exc_info()[1]
        if not self.log_requests and isinstance(
            error,
            (BrokenPipeError, ConnectionResetError),
        ):
            return
        super().handle_error(request, client_address)

    def startup_url(self) -> str:
        """Return the token URL printed or displayed by the owning process."""

        address = self.server_address
        host = str(address[0])
        port = int(address[1])
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{port}/#token={quote(self._access_token, safe='')}"

    def human_answer_url(self) -> str:
        """Grant an answer link to the local operator; never expose it via GET."""
        with self._active_request_lock:
            if self._answer_token is None:
                self._answer_token = secrets.token_urlsafe(24)
            return self.startup_url() + "&answer_token=" + quote(self._answer_token, safe="") + "&human=1"

    def provider_control_url(self) -> str:
        """Separate operator grant; read and answer links cannot rotate APIs."""
        with self._active_request_lock:
            if self._provider_token is None:
                self._provider_token = secrets.token_urlsafe(24)
            return self.startup_url() + "&provider_token=" + quote(self._provider_token, safe="")

    def literature_access_url(self) -> str:
        """Separate operator grant for institutional acquisition actions only."""
        with self._active_request_lock:
            if self._literature_token is None:
                self._literature_token = secrets.token_urlsafe(24)
            return self.startup_url() + "&literature_token=" + quote(self._literature_token, safe="")


class WebObserverRequestHandler(BaseHTTPRequestHandler):
    """Serve fixed assets and one bearer-protected snapshot resource."""

    protocol_version = "HTTP/1.1"
    server_version = "HypoloomObserver"
    sys_version = ""

    @property
    def observer(self) -> WebObserverServer:
        return cast(WebObserverServer, self.server)

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format: str, *args: object) -> None:
        if self.observer.log_requests:
            super().log_message(format, *args)

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        headers: Sequence[tuple[str, str]] = (),
        compress: bool = False,
    ) -> None:
        if self.observer.is_closing:
            self.close_connection = True
            return
        if compress:
            headers = (*headers, ('Vary', 'Accept-Encoding'))
            for encoding in self.headers.get('Accept-Encoding', '').split(','):
                name, *parameters = encoding.split(';')
                if name.strip().lower() != 'gzip': continue
                quality = 1.0
                try:
                    for parameter in parameters:
                        key, _, value = parameter.strip().partition('=')
                        if key.lower() == 'q': quality = float(value)
                except ValueError:
                    quality = 0.0
                if 0 < quality <= 1 and len(body) >= 1024:
                    body = gzip.compress(body, compresslevel=1)
                    headers = (*headers, ('Content-Encoding', 'gzip'))
                break
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self) -> None:
        self._send(
            HTTPStatus.NOT_FOUND,
            b'{"error":"not found"}\n',
            "application/json; charset=utf-8",
        )

    def _method_not_allowed(self) -> None:
        self._send(
            HTTPStatus.METHOD_NOT_ALLOWED,
            b'{"error":"method not allowed"}\n',
            "application/json; charset=utf-8",
            headers=(("Allow", "GET"),),
        )

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.observer.access_token}"
        return hmac.compare_digest(
            supplied.encode("utf-8"),
            expected.encode("utf-8"),
        )

    def _require_authorization(self) -> bool:
        if not self._authorized():
            self._send(
                HTTPStatus.UNAUTHORIZED,
                b'{"error":"unauthorized"}\n',
                "application/json; charset=utf-8",
                headers=(("WWW-Authenticate", "Bearer"),),
            )
            return False
        return True

    def _snapshot(self) -> None:
        if not self._require_authorization(): return
        try:
            snapshot = self.observer.refresh_reader.get()['snapshot'] if self.observer.shared_refresh else self.observer.snapshot_builder(
                self.observer.state_root,
                self.observer.run_id,
            )
            body = (snapshot.to_json() + "\n").encode("utf-8")
        except (MonitorError, OSError, ValueError) as exc:
            self.log_error("snapshot unavailable: %s", exc)
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE,
                b'{"error":"snapshot unavailable"}\n',
                "application/json; charset=utf-8",
            )
            return
        self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

    def _selected_product(self, projection):
        config = load_notification_config(self.observer.state_root, self.observer.run_id)
        selection = config.get('events', {}).get('dashboard')
        if selection is not None:
            return {**projection, 'events': [event for event in projection['events'] if event['type'] in selection]}
        return projection

    @staticmethod
    def _history_page(values, *, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('invalid history page')
        end = max(0, len(values) - offset)
        return values[max(0, end - limit):end], {'offset': offset, 'limit': limit,
            'total': len(values), 'has_more': offset + limit < len(values)}

    def _refresh(self) -> None:
        if not self._require_authorization(): return
        try:
            if urlsplit(self.path).query: raise ValueError('refresh has no selectors')
            if not self.observer.shared_refresh:
                # Preserve the public injected snapshot seam. An opaque custom
                # source cannot establish a matching product/SQLite transaction.
                custom = self.observer.snapshot_builder(self.observer.state_root, self.observer.run_id)
                body = json.dumps({'snapshot': json.loads(custom.to_json()), 'activity': None},
                    ensure_ascii=False, separators=(',', ':')).encode()
                self._send(HTTPStatus.OK, body, 'application/json; charset=utf-8')
                return
            previous = self.headers.get('X-Hypoloom-Observed-At')
            if previous is not None:
                previous = float(previous)
                if not math.isfinite(previous): raise ValueError('invalid observation time')
            observation = self.observer.refresh_reader.poll()
            snapshot = observation['snapshot']
            if snapshot is None:
                code = HTTPStatus.SERVICE_UNAVAILABLE if observation['observation']['state'] == 'error' else HTTPStatus.ACCEPTED
                self._send(code, json.dumps(observation).encode(), 'application/json; charset=utf-8')
                return
            if previous == snapshot.observed_at:
                # The client's existing observation time only avoids retransmission;
                # it grants no access and never substitutes for a run revision.
                body = json.dumps({'unchanged': True, 'observation': observation['observation'],
                    'oracle': snapshot.oracle}, ensure_ascii=False, separators=(',', ':')).encode()
                self._send(HTTPStatus.OK, body, 'application/json; charset=utf-8', compress=True)
                return
            activity = {key: value for key, value in self._selected_product(observation['activity']).items() if key != 'summary'}
            for key, limit in (('events', 100), ('experiments', 50)):
                activity[key], activity[key + '_page'] = self._history_page(activity[key], limit=limit)
            body = json.dumps({'snapshot': snapshot.to_dict(), 'activity': activity,
                'observation': observation['observation']},
                ensure_ascii=False, separators=(',', ':')).encode()
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"invalid refresh request"}\n', 'application/json'); return
        except (MonitorError, OSError, sqlite3.Error, StateStoreError):
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"refresh unavailable"}\n', 'application/json'); return
        self._send(HTTPStatus.OK, body, 'application/json; charset=utf-8', compress=True)

    def _trends(self) -> None:
        if not self._require_authorization(): return
        if urlsplit(self.path).query:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"trends has no selectors"}', 'application/json')
            return
        if not self.observer._trends_lock.acquire(blocking=False):
            self._send(HTTPStatus.TOO_MANY_REQUESTS, b'{"error":"trends being prepared"}', 'application/json')
            return
        try:
            from .observer_trends import build_run_trends
            result = build_run_trends(self.observer.state_root, self.observer.run_id)
            self._send(HTTPStatus.OK, json.dumps(result, separators=(',', ':')).encode(),
                       'application/json; charset=utf-8', compress=True)
        except (ValueError, OSError, sqlite3.Error, StateStoreError):
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"trends unavailable"}', 'application/json')
        finally:
            self.observer._trends_lock.release()

    def _product(self, name: str) -> None:
        if not self._require_authorization(): return
        try:
            if name.startswith("experiments/"):
                parts = name.split("/")
                if len(parts) != 3 or parts[2] != "logs": self._not_found(); return
                query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                if set(query) - {"stream", "limit"} or any(len(items) != 1 for items in query.values()):
                    raise ValueError("invalid log query")
                result = read_experiment_log(self.observer.state_root, self.observer.run_id,
                    unquote(parts[1]), stream=query.get("stream", ["stdout"])[0],
                    limit=int(query.get("limit", ["65536"])[0]))
            else:
                projection = (self.observer.refresh_reader.get()['activity'] if self.observer.shared_refresh
                    else build_product_observation(self.observer.state_root, self.observer.run_id))
                if name in {"events", "activity"}:
                    # This immutable GoalDefined selection affects only the
                    # Dashboard event list, never the canonical projection or
                    # another sink. Omission means all; [] deliberately none.
                    projection = self._selected_product(projection)
                result = projection if name == "activity" else {"run_id": projection["run_id"], "observed_at": projection["observed_at"], name: projection[name]}
                if name in {'events', 'experiments'}:
                    query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
                    if set(query) - {'offset', 'limit'} or any(len(items) != 1 for items in query.values()):
                        raise ValueError('invalid history selection')
                    result[name], result[name + '_page'] = self._history_page(result[name],
                        offset=int(query.get('offset', ['0'])[0]), limit=int(query.get('limit', ['100'])[0]))
            body = (json.dumps(result, ensure_ascii=False) + "\n").encode()
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"invalid observation request"}\n', "application/json; charset=utf-8"); return
        except (MonitorError, OSError, sqlite3.Error, StateStoreError):
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"observation unavailable"}\n', "application/json; charset=utf-8"); return
        self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

    def _details(self) -> None:
        if not self._require_authorization(): return
        from .observer_details import read_observer_detail
        try:
            query = parse_qs(urlsplit(self.path).query, strict_parsing=True, keep_blank_values=True)
            allowed = {"kind", "role", "name", "commit", "base_commit", "attempt_id",
                "session_id", "experiment_id", "component_id", "offset", "limit", "task_scope", "execution_role"}
            if set(query) - allowed or any(len(items) != 1 or not items[0] for items in query.values()):
                raise ValueError("invalid detail selection")
            kind = query.pop("kind")[0] if "kind" in query else ""
            selectors = {key: int(value[0]) if key in {"offset", "limit"} else value[0]
                for key, value in query.items()}
            result = read_observer_detail(self.observer.state_root, self.observer.run_id, kind, **selectors)
            if kind == "resources" and self.observer.gpu_pool_socket is not None:
                from .observer_details import read_observer_pool
                result["shared_pool"] = read_observer_pool(self.observer.gpu_pool_socket,
                    expected_server_uid=self.observer.gpu_pool_server_uid)
            body = (json.dumps(result, ensure_ascii=False) + "\n").encode()
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"invalid detail request"}\n', "application/json; charset=utf-8"); return
        except (MonitorError, OSError, sqlite3.Error, StateStoreError):
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"detail unavailable"}\n', "application/json; charset=utf-8"); return
        self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

    def _log_websocket(self, experiment_id: str) -> None:
        from wsproto import ConnectionType, WSConnection
        from wsproto.events import AcceptConnection, CloseConnection, Ping, Request, TextMessage
        from wsproto.utilities import LocalProtocolError, RemoteProtocolError
        offered = [item.strip() for item in self.headers.get("Sec-WebSocket-Protocol", "").split(",")]
        browser_auth = any(hmac.compare_digest(item.encode(), ("bearer." + self.observer.access_token).encode()) for item in offered)
        if not browser_auth and not self._require_authorization(): return
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://127.0.0.1:{self.observer.server_address[1]}":
            self._send(HTTPStatus.FORBIDDEN, b'{"error":"origin rejected"}\n', "application/json"); return
        if "hypoloom.logs" not in offered:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"log subprotocol required"}\n', "application/json"); return
        try:
            query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
            if set(query) - {"stream", "limit"} or any(len(items) != 1 for items in query.values()): raise ValueError
            arguments = {"stream": query.get("stream", ["stdout"])[0], "limit": int(query.get("limit", ["65536"])[0])}
            current = read_experiment_log(self.observer.state_root, self.observer.run_id, experiment_id, **arguments)
            ws = WSConnection(ConnectionType.SERVER)
            ws.initiate_upgrade_connection([(key.encode("ascii"), value.encode("latin-1")) for key, value in self.headers.items()], self.path)
            if not any(isinstance(event, Request) for event in ws.events()): raise ValueError
        except (ValueError, MonitorError, StateStoreError, OSError, sqlite3.Error, RemoteProtocolError):
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"log subscription unavailable"}\n', "application/json"); return
        self.close_connection = True
        try:
            self.connection.settimeout(5)
            headers = [(key.encode(), value.encode()) for key, value in _SECURITY_HEADERS]
            self.connection.sendall(ws.send(AcceptConnection(subprotocol="hypoloom.logs", extra_headers=headers)))
            previous = None
            while not self.observer.is_closing:
                body = json.dumps(current, ensure_ascii=False)
                if body != previous:
                    self.connection.sendall(ws.send(TextMessage(data=body))); previous = body
                if select.select([self.connection], [], [], 0.5)[0]:
                    data = self.connection.recv(4096)
                    if not data: return
                    ws.receive_data(data)
                    for event in ws.events():
                        if isinstance(event, CloseConnection): self.connection.sendall(ws.send(event.response())); return
                        if isinstance(event, Ping): self.connection.sendall(ws.send(event.response()))
                        elif isinstance(event, TextMessage):
                            self.connection.sendall(ws.send(CloseConnection(code=1008, reason="read-only subscription"))); return
                current = read_experiment_log(self.observer.state_root, self.observer.run_id, experiment_id, **arguments)
        except (OSError, ValueError, MonitorError, StateStoreError, sqlite3.Error, LocalProtocolError, RemoteProtocolError):
            return

    def _asset(self, path: str) -> None:
        asset = _ASSETS.get(path)
        if asset is None:
            self._not_found()
            return
        name, content_type = asset
        try:
            body = (self.observer.asset_root / name).read_bytes()
        except OSError as exc:
            self.log_error("static asset unavailable: %s", exc)
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                b'{"error":"static asset unavailable"}\n',
                "application/json; charset=utf-8",
            )
            return
        self._send(HTTPStatus.OK, body, content_type)

    def _support(self) -> None:
        if not self._require_authorization():
            return
        from .support_bundle import SupportBundleError, build_support_bundle, support_zip
        try:
            description = None
            if self.command == "POST":
                if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                    raise ValueError
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError
                self.connection.settimeout(5)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError
                description = json.loads(raw)
            query = parse_qs(urlsplit(self.path).query, strict_parsing=True, keep_blank_values=True)
            if set(query) - {"format", "private", "scope", "attempt_id", "max_mib"} or any(len(values) != 1 for values in query.values()):
                raise ValueError
            kind = query.get("format", ["preview"])[0]
            private = query.get("private", ["0"])[0]
            scope = query.get("scope", ["failures"])[0]
            max_mib = int(query.get("max_mib", ["20"])[0])
            if kind not in {"preview", "zip"} or private not in {"0", "1"} or scope not in {"failures", "run"} or max_mib not in {5, 20, 50}:
                raise ValueError
            attempt_id = query.get("attempt_id", [None])[0]
            if attempt_id == "":
                raise ValueError
        except (TypeError, ValueError):
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"invalid support selector"}', "application/json")
            return
        if not self.observer._support_lock.acquire(blocking=False):
            self._send(HTTPStatus.TOO_MANY_REQUESTS, b'{"error":"another support copy is being prepared"}', "application/json",
                       headers=(("Retry-After", "2"),))
            return
        try:
            report, files = build_support_bundle(self.observer.state_root, self.observer.run_id,
                attempt_id=attempt_id, include_private_data=private == "1", recent_failures=scope == "failures",
                max_bytes=max_mib * 1024 * 1024, issue_description=description)
            if kind == "preview":
                body = json.dumps({"report": report, "readme": files["README.md"].decode(),
                    "diagnostics": json.loads(files["debug.json"])}, ensure_ascii=False).encode()
                self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
            else:
                filename = "hypoloom-support-private.zip" if private == "1" else "hypoloom-support.zip"
                self._send(HTTPStatus.OK, support_zip(files), "application/zip",
                    headers=(("Content-Disposition", f'attachment; filename="{filename}"'),))
        except (SupportBundleError, OSError, ValueError):
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"support copy unavailable; check the selected Attempt and source"}', "application/json")
        finally:
            self.observer._support_lock.release()

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlsplit(self.path).path
        if path == "/api/support":
            self._support()
            return
        if path == '/api/refresh':
            self._refresh()
            return
        if path == '/api/trends':
            self._trends()
            return
        if path == "/api/snapshot":
            self._snapshot()
            return
        if path == "/api/details":
            self._details()
            return
        if path.startswith("/api/experiments/") and path.endswith("/logs/ws"):
            parts = path.split("/")
            if len(parts) != 6: self._not_found(); return
            self._log_websocket(unquote(parts[3])); return
        if path in {"/api/graph", "/api/experts", "/api/experiments", "/api/events", "/api/activity", "/api/final_report"} or path.startswith("/api/experiments/"):
            self._product(path.removeprefix("/api/")); return
        self._asset(path)

    def do_HEAD(self) -> None:  # noqa: N802 - this observer is intentionally GET-only
        self._method_not_allowed()

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self.close_connection = True
        if urlsplit(self.path).path == "/api/support":
            self._support()
            return
        scopes = {
            "/api/human-answer": ("answer", "X-Hypoloom-Answer-Token"),
            "/api/provider-retry": ("provider", "X-Hypoloom-Provider-Token"),
            "/api/resource-recheck": ("provider", "X-Hypoloom-Provider-Token"),
            "/api/literature-retry": ("provider", "X-Hypoloom-Provider-Token"),
            "/api/notification-retry": ("provider", "X-Hypoloom-Provider-Token"),
            "/api/literature-access": ("literature", "X-Hypoloom-Literature-Token"),
        }
        scope = scopes.get(self.path)
        if scope is None:
            self._method_not_allowed()
            return
        if not self._require_authorization():
            return
        resource = self.path == "/api/resource-recheck"
        literature = self.path == "/api/literature-retry"
        notification = self.path == "/api/notification-retry"
        scope_name, header_name = scope
        token = getattr(self.observer, f"_{scope_name}_token")
        supplied = self.headers.get(header_name, "")
        try:
            origin = urlsplit(self.headers.get("Origin", ""))
        except ValueError:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"invalid origin"}', "application/json")
            return
        if (not token or not hmac.compare_digest(supplied.encode(), token.encode())
                or origin.scheme != "http" or origin.netloc != self.headers.get("Host")
                or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
                or origin.path not in {"", "/"} or origin.query or origin.fragment
                or self.headers.get("Sec-Fetch-Site") not in {None, "same-origin"}):
            self._send(HTTPStatus.FORBIDDEN, b'{"error":"scoped operator link and same-origin request required"}', "application/json")
            return
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, b'{"error":"JSON body with Content-Length required"}', "application/json")
            return
        from .control_protocol import MAX_FRAME_BYTES, ProtocolError
        from .human_answers import submit_answer
        request = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_FRAME_BYTES:
                raise ValueError("invalid answer body length")
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete answer body")
            values = json.loads(raw)
            if scope_name == "provider":
                from .control_protocol import ControlRequest, SCHEMA_VERSION
                if not isinstance(values, dict) or set(values) not in ({"request_id"}, {"request_id", "settings_path"}):
                    raise ValueError("provider recovery requires request_id and optional settings_path")
                if (resource or literature or notification) and set(values) != {"request_id"}:
                    raise ValueError("dependency retry accepts only request_id")
                if "settings_path" in values and (not isinstance(values["settings_path"], str) or not values["settings_path"].startswith("/")):
                    raise ValueError("settings_path must be an absolute Host-side connection file path")
                request = ControlRequest.from_mapping({"schema_version": SCHEMA_VERSION, "request_id": values["request_id"],
                    "expected_revision": None, "command": "NOTIFICATION_RETRY" if notification else "RESOURCE_RECHECK" if resource else "PROVIDER_RETRY_CLEAR", "payload": {"run_id": self.observer.run_id,
                        **({"sink": "telegram"} if notification else {"reason": "operator"}),
                        **({"service": "openalex"} if literature else {}),
                        **({"settings_path": values["settings_path"]} if "settings_path" in values else {})}})
            elif scope_name == "literature":
                import uuid as uuid_module
                from .control_protocol import ControlRequest, SCHEMA_VERSION
                if not isinstance(values, dict) or not {"provider", "action"} <= set(values) or set(values) - {"provider", "action", "url", "path", "mode", "request_id", "expected_revision"}:
                    raise ValueError("institutional access requires provider, action and optional url/path")
                provider_name, action = values["provider"], values["action"]
                if not isinstance(provider_name, str) or not provider_name:
                    raise ValueError("provider must be non-empty text")
                if not isinstance(action, str) or action not in {"open", "retry", "import", "skip", "restore", "mode", "verify"}:
                    raise ValueError("unsupported institutional action")
                if action in {"skip", "restore", "mode"} and (not values.get("request_id") or type(values.get("expected_revision")) is not int):
                    raise ValueError("preference controls require request_id and expected_revision")
                payload = {"run_id": self.observer.run_id, "provider": provider_name, "action": action}
                if "mode" in values:
                    payload["mode"] = values["mode"]
                if "url" in values:
                    if not isinstance(values["url"], str) or not values["url"]:
                        raise ValueError("url must be non-empty text")
                    payload["url"] = values["url"]
                if "path" in values:
                    if not isinstance(values["path"], str) or not values["path"].startswith("/"):
                        raise ValueError("path must be an absolute Host-side PDF path")
                    payload["path"] = values["path"]
                if action == "import" and not {"url", "path"} <= set(payload):
                    raise ValueError("import requires url and an absolute Host-side PDF path")
                request = ControlRequest.from_mapping({"schema_version": SCHEMA_VERSION,
                    "request_id": values.get("request_id", "web-literature-" + str(uuid_module.uuid4())),
                    "expected_revision": values.get("expected_revision"), "command": "LITERATURE_ACCESS", "payload": payload})
            else:
                from .human_answers import answer_request
                answer_request(self.observer.run_id, values)
        except (ValueError, OSError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "invalid_answer", "message": str(exc)}).encode(), "application/json")
            return
        try:
            if scope_name == "answer":
                response = submit_answer(self.observer.state_root, self.observer.run_id, values)
            else:
                from .control_protocol import request_over_socket
                response = request_over_socket(self.observer.state_root / "control.sock", request, timeout=5)
        except (OSError, ProtocolError):
            self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                b'{"error":"delivery_unknown","message":"Host reply not confirmed. Check current state or retry the same request."}', "application/json")
            return
        code = {"accepted": HTTPStatus.OK, "conflict": HTTPStatus.CONFLICT,
            "rejected": HTTPStatus.BAD_REQUEST, "error": HTTPStatus.SERVICE_UNAVAILABLE}[response.status]
        self._send(code, json.dumps(response.to_mapping(), ensure_ascii=False).encode(), "application/json")

    def do_PUT(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._method_not_allowed()


def create_server(
    state_root: str | Path,
    run_id: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    snapshot_builder: SnapshotBuilder = build_observer_snapshot,
    gpu_pool_socket: str | Path | None = None,
    gpu_pool_server_uid: int | None = None,
) -> WebObserverServer:
    """Create, bind, and return a server without starting its request loop."""

    return WebObserverServer(
        state_root,
        run_id,
        host=host,
        port=port,
        token=token,
        snapshot_builder=snapshot_builder,
        gpu_pool_socket=gpu_pool_socket,
        gpu_pool_server_uid=gpu_pool_server_uid,
    )


def run_web_observer(
    state_root: str | Path,
    run_id: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    snapshot_builder: SnapshotBuilder = build_observer_snapshot,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    gpu_pool_socket: str | Path | None = None,
    gpu_pool_server_uid: int | None = None,
    allow_human_answers: bool = False,
    allow_provider_control: bool = False,
    allow_literature_control: bool = False,
) -> int:
    """Run the observer in the foreground until interrupted."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        if not callable(snapshot_builder):
            raise ValueError("snapshot_builder must be callable")
        snapshot_builder(state_root, run_id)
        server = create_server(
            state_root,
            run_id,
            host=host,
            port=port,
            token=token,
            snapshot_builder=snapshot_builder,
            **({"gpu_pool_socket": gpu_pool_socket, "gpu_pool_server_uid": gpu_pool_server_uid}
               if gpu_pool_socket is not None or gpu_pool_server_uid is not None else {}),
        )
    except (MonitorError, OSError, ValueError) as exc:
        print(f"web observer: cannot start: {exc}", file=errors)
        return 2
    with server:
        url = server.human_answer_url() if allow_human_answers else server.startup_url()
        if allow_provider_control:
            url += "&provider_token=" + quote(parse_qs(urlsplit(server.provider_control_url()).fragment)["provider_token"][0], safe="")
        if allow_literature_control:
            url += "&literature_token=" + quote(parse_qs(urlsplit(server.literature_access_url()).fragment)["literature_token"][0], safe="")
        print(f"web observer: {url}", file=output, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hypoloom.web_observer",
        description="Foreground, read-only Web observer for one explicit Hypoloom run.",
    )
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
        help="loopback Web port (default: 0, OS-selected free port)")
    parser.add_argument("--allow-human-answers", action="store_true", help="grant an operator link for reviewed Grounding answers")
    parser.add_argument("--allow-provider-control", action="store_true", help="grant an operator recovery link for API connection selection and GPU resource recheck")
    parser.add_argument("--allow-literature-control", action="store_true", help="grant an operator link for institutional full-text actions")
    parser.add_argument("--gpu-pool-socket", help="explicit authorized aggregate capacity socket; no discovery")
    parser.add_argument("--gpu-pool-server-uid", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_web_observer(
        args.state_root,
        args.run_id,
        host=DEFAULT_HOST,
        port=args.port,
        gpu_pool_socket=args.gpu_pool_socket,
        gpu_pool_server_uid=args.gpu_pool_server_uid,
        allow_human_answers=args.allow_human_answers,
        allow_provider_control=args.allow_provider_control,
        allow_literature_control=args.allow_literature_control,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "WebObserverServer",
    "build_parser",
    "create_server",
    "main",
    "run_web_observer",
]
