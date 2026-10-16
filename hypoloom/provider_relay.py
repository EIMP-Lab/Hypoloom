"""Host-owned, fixed-endpoint Anthropic transport for a single Agent Attempt.

Credentials remain on this side of the Unix socket.  Request bytes are bounded
per call; response bytes and upstream call count are bounded across the relay's
lifetime.  These transport limits are not a provider-price or dollar oracle.
"""

from __future__ import annotations

import http.client
import json
import math
import re
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .runtime_diagnostics import RuntimeDiagnostics


_PATHS = frozenset({"/v1/messages", "/v1/messages/count_tokens"})
_CONTEXT_1M_BETA = "context-1m-2025-08-07"
_UNSUPPORTED_MODES = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
_ERROR_BODY = b'{"type":"error","error":{"type":"api_error","message":"provider relay request failed"}}'
_EFFORT_ERROR_BODY = b'{"type":"error","error":{"type":"invalid_request_error","message":"provider relay effort mismatch: native CLI did not preserve configured effort"}}'
_TOKEN_ERROR_BODY = b'{"type":"error","error":{"type":"invalid_request_error","message":"Agent token budget exhausted; no further generation admitted"}}'


def validate_provider_connection(base_url: str, provider_environment: Mapping[str, str]):
    """Validate the actual relay inputs without opening sockets or contacting a provider."""
    if any(
        str(provider_environment.get(name, "")).lower() not in {"", "0", "false"}
        for name in _UNSUPPORTED_MODES
    ):
        raise ValueError(
            "provider relay supports Anthropic-compatible HTTP only; Bedrock/Vertex/Foundry are unsupported"
        )
    try:
        if not isinstance(base_url, str): raise ValueError("invalid endpoint")
        endpoint = urlsplit(base_url)
        _ = endpoint.port  # Validate malformed port values without opening a connection.
        valid_endpoint = (
            endpoint.scheme in {"http", "https"}
            and bool(endpoint.hostname)
            and not endpoint.username
            and not endpoint.password
            and not endpoint.query
            and not endpoint.fragment
        )
    except ValueError:
        valid_endpoint = False
    if not valid_endpoint:
        raise ValueError("invalid fixed provider endpoint")
    credentials = {
        name: provider_environment.get(name)
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
        if provider_environment.get(name)
    }
    if not credentials or any(
        not isinstance(value, str) or "\r" in value or "\n" in value
        for value in credentials.values()
    ):
        raise ValueError("provider relay requires valid Host authentication")
    return endpoint, credentials


class _ExchangeCapture:
    """Optional, non-authoritative copy; IO failures never change transport."""

    def __init__(self, relay: ProviderRelay) -> None:
        from .provider_observation import ProviderObservation
        self.observation = ProviderObservation(service=relay.provider_service)
        self.relay = relay
        self._counted_tokens = 0
        self.request_document = None
        self.request_protocol = None
        self.parent = relay.diagnostics
        self.recorder = None
        self.writer = None
        self.errors: list[dict[str, str]] = []
        self.data: dict[str, Any] = {
            "provider_service": relay.provider_service,
            "started_at": time.time(), "ended_at": None,
            "request_bytes_received": 0, "request_bytes_forwarded": 0,
            "response_bytes_received": 0, "response_bytes_forwarded": 0,
            "request_body_complete": False, "response_body_complete": False,
            "failure_stage": None, "failure_reason": None,
            "response_partial_reason": "not_observed",
        }
        with relay._lock:
            relay._exchange_count += 1
            self.data["exchange"] = relay._exchange_count
        if relay.diagnostics is not None:
            self.recorder = self.call(
                "child", relay.diagnostics.child,
                f"provider/{self.data['exchange']:06d}",
                secrets=tuple(secret.decode() for secret in relay._secrets),
            )
            self.save()

    def call(self, operation: str, function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            # Exception messages can contain endpoint URLs or authentication.
            error = {"operation": operation, "exception_type": type(exc).__name__}
            if error not in self.errors:
                self.errors.append(error)
                try:
                    if self.parent is not None:
                        self.parent.event("provider_capture_error", exchange=self.data.get("exchange"), **error)
                except Exception:
                    pass
                try:
                    print(f"provider diagnostics {operation} failed: {type(exc).__name__}", file=sys.stderr)
                except (OSError, ValueError):
                    pass
            return None

    def save(self) -> None:
        if self.recorder is not None:
            self.call("metadata", self.recorder.write_json, "exchange.json", self.data)

    def request(self, body: bytes, complete: bool) -> None:
        self.data.update(request_bytes_received=len(body), request_body_complete=complete)
        if self.recorder is not None:
            self.call("request_body", self.recorder.write_bytes, "request.body", body)
        self.save()

    def response(self, response: http.client.HTTPResponse) -> None:
        headers = {}
        for name in ("request-id", "x-request-id", "anthropic-request-id", "cf-ray"):
            value = response.getheader(name)
            if value and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value):
                headers[name] = value
        for name in ("content-type", "content-encoding", "content-length", "retry-after"):
            value = response.getheader(name, "").split(";", 1)[0].strip()
            if name == "retry-after":
                from .provider_observation import retry_after_seconds
                seconds = retry_after_seconds(value, now=time.time())
                if seconds is not None:
                    headers[name] = str(math.ceil(seconds))
                continue
            pattern = r"[A-Za-z0-9.+_-]+/[A-Za-z0-9.+_-]+" if name == "content-type" else r"[A-Za-z0-9._-]{1,128}"
            if value and re.fullmatch(pattern, value):
                headers[name] = value
        self.data.update(upstream_status=response.status, response_headers=headers,
                         response_started_at=time.time(), response_partial_reason="interrupted")
        if self.recorder is not None:
            self.writer = self.call("response_open", self.recorder.stream, "response.body")
        self.save()

    def write(self, chunk: bytes) -> None:
        self.call("response_observation", self.observation.feed, chunk)
        # Observe before forwarding message_stop, so a following CLI request
        # cannot race the completion of this handler's diagnostic writes.
        self._observe_tokens()
        if self.writer is not None:
            self.call("response_body", self.writer.write, chunk)

    def eof(self, response: http.client.HTTPResponse) -> None:
        complete = response.length in (None, 0)
        self.data.update(response_body_complete=complete,
                         response_partial_reason=None if complete else "incomplete_content_length")

    def finish(self) -> None:
        self._observe_tokens()
        observed = self.call("response_observation", self.observation.finish)
        if observed is not None:
            self.data.update(observed)
        self.relay._observe_context(self)
        if self.writer is not None:
            self.call("response_close", self.writer.close)
            self.data["response_capture"] = self.call("response_summary", self.writer.summary)
        self.data["ended_at"] = time.time()
        self.data["capture_errors"] = self.errors
        if self.recorder is not None:
            self.data["capture"] = self.call("summary", self.recorder.summary)
        self.save()

    def _observe_tokens(self) -> None:
        if self.data.get("request_path") != "/v1/messages" or self.data.get("upstream_status") != 200:
            return
        observed = self.observation.finish()
        if observed["message_complete"]:
            from .context_management import input_usage
            usage = observed["usage"]
            inputs = input_usage(usage, (self.relay.context_policy or {}).get(
                "input_usage_semantics", "separate_cache"))
            # Unknown semantics cannot weaken existing token-budget enforcement.
            # Keep its conservative bound, never advertise it as exact usage.
            tokens = sum(usage.values()) if inputs is None else inputs + usage.get("output_tokens", 0)
            with self.relay._lock:
                self.relay._observed_tokens += max(0, tokens - self._counted_tokens)
            self._counted_tokens = max(tokens, self._counted_tokens)

    def rejected_body(self, relay: ProviderRelay, response: http.client.HTTPResponse,
                      connection: socket.socket) -> None:
        # Previously rejected bodies were not consumed. Observe them only after
        # sending the original error, under the existing remaining byte/deadline
        # ceilings. These copy reads cannot spend the next valid call's budget.
        with relay._lock:
            remaining = relay.max_response_bytes - relay._response_bytes
        try:
            while True:
                relay._remaining()
                if response.isclosed():
                    self.eof(response)
                    break
                if remaining <= 0:
                    self.data["response_partial_reason"] = "response_byte_limit"
                    break
                connection.settimeout(relay._remaining())
                chunk = response.read1(min(64 * 1024, remaining))
                self.data["response_bytes_received"] += len(chunk)
                self.write(chunk)
                remaining -= len(chunk)
                self.save()
                if not chunk:
                    self.eof(response)
                    break
        except (_RelayRejected, OSError, ValueError, http.client.HTTPException) as exc:
            self.data["response_partial_reason"] = (
                "relay_closed" if relay._closed.is_set() else
                "deadline" if time.monotonic() >= relay.deadline_at else "response_read_error"
            )
            self.data["response_capture_exception_type"] = type(exc).__name__


class _RelayRejected(Exception):
    def __init__(self, status: int, code: str | None = None) -> None:
        self.status = status
        self.code = code


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class ProviderRelay:
    """Serve one fixed provider/model with Host-held authentication.

    ``deadline_at`` is an absolute ``time.monotonic()`` deadline.  Construction
    binds and starts a new Unix endpoint; an existing path is never replaced.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        base_url: str,
        provider_environment: Mapping[str, str],
        model: str,
        max_request_bytes: int,
        max_response_bytes: int,
        max_requests: int,
        deadline_at: float,
        expected_effort: str | None = None,
        diagnostics: RuntimeDiagnostics | None = None,
        token_budget: int | None = None,
        context_policy: Mapping[str, Any] | None = None,
        traffic_binding=None,
    ) -> None:
        endpoint, credentials = validate_provider_connection(base_url, provider_environment)
        from .provider_observation import provider_service
        self.provider_service = provider_service(base_url)
        port = endpoint.port
        for limit in (max_request_bytes, max_response_bytes, max_requests):
            if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
                raise ValueError("provider relay limits must be positive integers")
        if token_budget is not None and (type(token_budget) is not int or token_budget <= 0):
            raise ValueError("provider relay token budget must be a positive integer")
        if not isinstance(deadline_at, (int, float)) or not math.isfinite(deadline_at):
            raise ValueError("provider relay requires a finite monotonic deadline")
        if not isinstance(model, str) or not model:
            raise ValueError("provider relay requires an exact model")
        if expected_effort is not None and expected_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("provider relay requires a supported explicit effort")
        self.socket_path = Path(socket_path)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise FileExistsError(self.socket_path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.expected_effort = expected_effort
        # Claude's observed [1m] CLI selector is a context option, not part
        # of its Anthropic JSON model. Keep the configured identity and admit
        # only this exact wire mapping with its explicit context opt-in.
        self._context_1m = model.lower().endswith("[1m]")
        self._wire_model = model[:-4] if self._context_1m else model
        if not self._wire_model:
            raise ValueError("provider relay requires an exact base model")
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.max_requests = max_requests
        self.deadline_at = float(deadline_at)
        self.token_budget = token_budget
        self.context_policy = context_policy
        self.traffic_binding = traffic_binding
        self.context_limit_reached = False
        # Derived, bounded observations belong to this fixed connection/model.
        # They disappear with this relay and never modify the forwarded request.
        self._context_anchor = None
        self._context_count = None
        self._latest_context_capacity = None
        self._latest_context_exchange = None
        self._context_serial = threading.Lock()
        self._observed_tokens = 0
        self.token_limit_reached = False
        self._scheme, self._hostname, self._port = (
            endpoint.scheme,
            endpoint.hostname,
            port,
        )
        self._base_path = endpoint.path.rstrip("/")
        self._headers = {
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
        }
        if "ANTHROPIC_API_KEY" in credentials:
            self._headers["x-api-key"] = credentials["ANTHROPIC_API_KEY"]
        if "ANTHROPIC_AUTH_TOKEN" in credentials:
            self._headers["Authorization"] = (
                "Bearer " + credentials["ANTHROPIC_AUTH_TOKEN"]
            )
        self._secrets = tuple(value.encode() for value in credentials.values())
        self._lookbehind = max(map(len, self._secrets)) - 1
        self._request_count = 0
        self._response_bytes = 0
        self._response_bytes_received = 0
        self._response_limit_reached = False
        self.diagnostics = diagnostics
        self._exchange_count = 0
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._sockets: set[socket.socket] = set()
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_arguments: object) -> None:
                return

            def setup(self) -> None:
                super().setup()
                with relay._lock:
                    relay._sockets.add(self.connection)

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    with relay._lock:
                        relay._sockets.discard(self.connection)

            def send_error(
                self, code: int, message: str | None = None, explain: str | None = None
            ) -> None:
                # Never reflect upstream error bodies, reason phrases, URLs,
                # request headers or exception text across the credential boundary.
                body = (b'{"type":"error","error":{"type":"invalid_request_error","message":"Hypoloom working context capacity reached; end this invocation for Host reconstruction."}}'
                        if message == "context_capacity" else _EFFORT_ERROR_BODY if message == "effort_mismatch" else
                        _TOKEN_ERROR_BODY if message == "token_budget_exceeded" else _ERROR_BODY)
                if message == "context_capacity" and getattr(self, "context_estimate_source", None) == "utf8_bytes_conservative_estimate":
                    body = b'{"type":"error","error":{"type":"invalid_request_error","message":"Hypoloom cannot confirm that this request fits the configured working context limit using the available byte estimate; end this invocation for Host input review or reconstruction."}}'
                if message == "response_byte_limit":
                    transport = relay.response_transport()
                    body = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                        "message": (f"Hypoloom local API response transport limit exceeded: "
                            f"{transport['received_bytes']} bytes received across this invocation; "
                            f"limit {transport['limit_bytes']} bytes. No further upstream requests admitted.")}}).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                upstream = None
                upstream_socket = None
                response = None
                started = False
                capture = _ExchangeCapture(relay)
                stage = "request_validation"
                capture_rejected = False
                context_locked = False
                traffic_ticket = None
                try:
                    self.connection.settimeout(relay._remaining())
                    parsed = urlsplit(self.path)
                    if (
                        parsed.scheme
                        or parsed.netloc
                        or parsed.fragment
                        or parsed.path not in _PATHS
                    ):
                        raise _RelayRejected(404)
                    capture.data["request_path"] = parsed.path
                    if self.headers.get("Transfer-Encoding") is not None:
                        raise _RelayRejected(400)
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1:
                        raise _RelayRejected(411)
                    try:
                        length = int(lengths[0])
                    except ValueError:
                        raise _RelayRejected(400) from None
                    if not 0 < length <= relay.max_request_bytes:
                        stage = "request_byte_limit"
                        raise _RelayRejected(413)
                    stage = "request_read"
                    body = self.rfile.read(length)
                    capture.request(body, len(body) == length)
                    if len(body) != length:
                        raise _RelayRejected(400)
                    stage = "request_json"
                    try:
                        document = json.loads(
                            body,
                            object_pairs_hook=_strict_object,
                            parse_constant=_reject_constant,
                        )
                    except (ValueError, UnicodeError):
                        raise _RelayRejected(400) from None
                    stage = "request_model"
                    if (
                        not isinstance(document, dict)
                        or document.get("model") != relay._wire_model
                    ):
                        raise _RelayRejected(400)
                    stage = "request_context"
                    capture.data["requested_model"] = document["model"]
                    if relay._context_1m and (relay.context_policy or {}).get("cc_1m_requested", True) and parsed.path == "/v1/messages" and _CONTEXT_1M_BETA not in {
                        token.strip()
                        for token in self.headers.get("anthropic-beta", "").split(",")
                    }:
                        raise _RelayRejected(400)
                    stage = "request_effort"
                    if relay.expected_effort is not None and parsed.path == "/v1/messages":
                        output_config = document.get("output_config")
                        if not isinstance(output_config, dict) or output_config.get("effort") != relay.expected_effort:
                            raise _RelayRejected(400, "effort_mismatch")
                    stage = "request_limit"
                    if (relay.context_policy or {}).get("capacity_measurement_version", 0) >= 25:
                        # A following request may arrive after message_stop but
                        # before the preceding HTTP EOF. Finish its observation
                        # before selecting a measurement; deadline still bounds
                        # all waiting and upstream work in this single Attempt.
                        context_locked = relay._context_serial.acquire(timeout=relay._remaining())
                        if not context_locked:
                            raise _RelayRejected(504)
                        capture.request_document = document
                        capture.request_protocol = (self.headers.get("anthropic-version"),
                            self.headers.get("anthropic-beta"), parsed.query)
                    if relay.context_policy is not None and parsed.path == "/v1/messages":
                        from .context_management import request_capacity
                        anchor, counted = relay._context_anchor, relay._context_count
                        if anchor is not None and anchor["protocol"] != capture.request_protocol:
                            anchor = None
                            relay._context_anchor = None
                        if counted is not None and counted["protocol"] != capture.request_protocol:
                            counted = None
                        capacity = request_capacity(document, relay.context_policy,
                            anchor=anchor, exact_count=counted)
                        capture.data["context_capacity"] = capacity
                        if relay.context_policy.get("capacity_measurement_version", 0) >= 25:
                            self.context_estimate_source = capacity["estimate_source"]
                            relay._latest_context_capacity = capacity
                            relay._latest_context_exchange = capture.data["exchange"]
                            if (capacity.get("estimate_reason") == "history_or_configuration_changed"
                                    and relay.context_policy.get("capacity_measurement_version", 0) < 28):
                                relay._context_anchor = None
                        if relay.context_limit_reached or not capacity["fits"]:
                            relay.context_limit_reached = True
                            raise _RelayRejected(400, "context_capacity")
                    relay._reserve_request(generation=parsed.path == "/v1/messages")
                    headers = dict(relay._headers)
                    for name in ("anthropic-version", "anthropic-beta"):
                        value = self.headers.get(name)
                        if value is not None:
                            headers[name] = value
                    connection_type = (
                        http.client.HTTPSConnection
                        if relay._scheme == "https"
                        else http.client.HTTPConnection
                    )
                    upstream = connection_type(
                        relay._hostname, relay._port, timeout=relay._remaining()
                    )
                    stage = "upstream_connect"
                    if relay.traffic_binding is not None:
                        from .provider_traffic import ProviderDeferred
                        traffic, owner, connection = relay.traffic_binding[:3]
                        stage = "provider_queue"
                        try:
                            traffic_ticket = traffic.acquire(model=relay._wire_model, owner=owner,
                                connection=connection, deadline=relay.deadline_at, cancelled=relay._closed,
                                attempt_id=relay.traffic_binding[3] if len(relay.traffic_binding) > 3 else None)
                        except ProviderDeferred as exc:
                            if exc.retry_after is not None:
                                capture.data['response_headers'] = {'retry-after': str(math.ceil(exc.retry_after))}
                            raise _RelayRejected(503 if exc.code in {'local_request_queue', 'service_unavailable'} else 429, exc.code) from None
                        capture.data['upstream_started_at'] = traffic_ticket[1]['started_at']
                        upstream.timeout = relay._remaining()
                        stage = "upstream_connect"
                    upstream.connect()
                    upstream_socket = upstream.sock
                    with relay._lock:
                        relay._sockets.add(upstream_socket)
                    stage = "upstream_request"
                    upstream.request(
                        "POST", relay._base_path + self.path, body=body, headers=headers
                    )
                    capture.data["request_bytes_forwarded"] = len(body)
                    capture.save()
                    stage = "upstream_headers"
                    upstream_socket.settimeout(relay._remaining())
                    response = upstream.getresponse()
                    capture.response(response)
                    stage = "upstream_status"
                    capture_rejected = True
                    if not 200 <= response.status < 300:
                        raise _RelayRejected(
                            response.status if 400 <= response.status < 600 else 502
                        )
                    stage = "response_content_type"
                    content_type = (
                        response.getheader("Content-Type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in {"application/json", "text/event-stream"}:
                        raise _RelayRejected(502)
                    stage = "response_content_encoding"
                    if (
                        response.getheader("Content-Encoding", "identity").lower()
                        != "identity"
                    ):
                        raise _RelayRejected(502)
                    capture_rejected = False
                    pending = b""
                    while True:
                        stage = "response_read"
                        relay._remaining()
                        if response.isclosed():
                            chunk = b""
                        else:
                            upstream_socket.settimeout(relay._remaining())
                            chunk = response.read1(64 * 1024)
                        if chunk:
                            capture.data["response_bytes_received"] += len(chunk)
                            stage = "response_byte_limit"
                            try:
                                relay._reserve_response(len(chunk))
                            except _RelayRejected:
                                with relay._lock:
                                    available = relay.max_response_bytes - relay._response_bytes
                                capture.write(chunk[:available])
                                raise
                            capture.write(chunk)
                        pending += chunk
                        # Retain only the longest credential minus one byte, so
                        # credentials split across transport chunks cannot escape.
                        # Detection terminates the response instead of modifying
                        # provider output into a potentially valid candidate.
                        stage = "credential_scan"
                        if any(secret in pending for secret in relay._secrets):
                            raise _RelayRejected(502)
                        safe_length = (
                            len(pending)
                            if not chunk
                            else max(0, len(pending) - relay._lookbehind)
                        )
                        if safe_length:
                            stage = "response_forward"
                            if not started:
                                self.send_response(response.status)
                                self.send_header("Content-Type", content_type)
                                self.end_headers()
                                started = True
                                capture.data["relay_status"] = response.status
                            self.wfile.write(pending[:safe_length])
                            self.wfile.flush()
                            capture.data["response_bytes_forwarded"] += safe_length
                            pending = pending[safe_length:]
                        capture.save()
                        if not chunk:
                            if not started:
                                self.send_response(response.status)
                                self.send_header("Content-Type", content_type)
                                self.end_headers()
                                capture.data["relay_status"] = response.status
                            capture.eof(response)
                            break
                except _RelayRejected as exc:
                    reason = ("relay_closed" if relay._closed.is_set() else "deadline") if exc.status == 504 else exc.code or stage
                    capture.data.update(failure_stage=stage, failure_reason=reason,
                                        response_partial_reason=reason)
                    if reason == "response_byte_limit":
                        capture.data["response_transport"] = relay.response_transport()
                    if not started:
                        self.send_error(exc.status, exc.code)
                        capture.data["relay_status"] = exc.status
                    if capture_rejected and (capture.recorder is not None or relay.traffic_binding is not None):
                        capture.rejected_body(relay, response, upstream_socket)
                except (OSError, ValueError, http.client.HTTPException) as exc:
                    if isinstance(exc, http.client.IncompleteRead) and exc.partial:
                        capture.data["response_bytes_received"] += len(exc.partial)
                        with relay._lock:
                            available = relay.max_response_bytes - relay._response_bytes
                        capture.write(exc.partial[:available])
                    reason = "relay_closed" if relay._closed.is_set() else "deadline" if time.monotonic() >= relay.deadline_at else "transport_error"
                    capture.data.update(failure_stage=stage, failure_reason=reason,
                                        exception_type=type(exc).__name__, response_partial_reason=reason)
                    if not started:
                        self.send_error(502)
                        capture.data["relay_status"] = 502
                finally:
                    if response is not None:
                        response.close()
                    if upstream is not None:
                        upstream.close()
                    with relay._lock:
                        relay._sockets.discard(upstream_socket)
                    try:
                        capture.finish()
                    finally:
                        if traffic_ticket is not None:
                            relay.traffic_binding[0].finish(traffic_ticket, capture.data)
                        if context_locked:
                            relay._context_serial.release()

        self.server = _UnixHTTPServer(str(self.socket_path), Handler)
        self.socket_path.chmod(0o600)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True
        )
        self.thread.start()

    def _remaining(self) -> float:
        remaining = self.deadline_at - time.monotonic()
        if self._closed.is_set() or remaining <= 0:
            raise _RelayRejected(504)
        return remaining

    def _observe_context(self, capture: _ExchangeCapture) -> None:
        """Reuse complete same-request provider results, without count probes."""
        if ((self.context_policy or {}).get("capacity_measurement_version", 0) < 25
                or capture.request_document is None):
            return
        data = capture.data
        from .provider_observation import empty_generation_response
        if (data.get("upstream_status") != 200 or data.get("failure_reason") is not None
                or data.get("response_body_complete") is not True or empty_generation_response(data)):
            return
        from .context_management import counting_material, usage_anchor
        if data.get("request_path") == "/v1/messages/count_tokens":
            observation = capture.observation
            if not observation.json_response or observation.dropping_line:
                return
            try:
                response = json.loads(observation.buffer)
            except (ValueError, UnicodeError, RecursionError):
                return
            value = response.get("input_tokens") if isinstance(response, dict) else None
            if type(value) is int and value >= 0:
                self._context_count = {"document": counting_material(capture.request_document,
                    version=self.context_policy.get("capacity_measurement_version", 25)),
                    "input_tokens": value, "exchange": data["exchange"],
                    "protocol": capture.request_protocol}
        elif (data.get("request_path") == "/v1/messages" and data.get("message_complete") is True
                and not capture.observation.invalid_usage):
            anchor = usage_anchor(capture.request_document, data.get("usage") or {},
                                  self.context_policy, exchange=data["exchange"])
            self._context_anchor = ({**anchor, "protocol": capture.request_protocol}
                                    if anchor is not None else None)

    def context_notice(self) -> str | None:
        """Best-effort progress-saving advice from existing observations only."""
        capacity = self._latest_context_capacity
        if capacity is None or self.context_limit_reached:
            return None
        estimate = capacity["estimated_input_tokens"]
        source = capacity["estimate_source"]
        anchor = self._context_anchor
        if anchor is not None and anchor.get("exchange") == self._latest_context_exchange:
            estimate = anchor["input_tokens"]
            source = "provider_usage_final"
        limit = capacity["available_input_tokens"]
        if estimate < limit * 0.8:
            return None
        label = {"provider_usage_final": "last complete provider input",
            "provider_count_tokens": "same-request provider count",
            "provider_usage_plus_delta_estimate": "measured history plus estimated new text",
            "utf8_bytes_conservative_estimate": "unconfirmed byte-based input estimate"}[source]
        return (f"Working context is near its configured input limit ({label}: {estimate}; limit: {limit}). "
                "Preserve confirmed sources, draft paths, completed checks, unresolved issues and the next step "
                "in existing summary fields or this task's permitted writable authoring space; finish bounded work soon. "
                "This advice is not evidence of provider overflow or scientific acceptance.")

    def _reserve_request(self, *, generation: bool = True) -> None:
        self._remaining()
        with self._lock:
            if self._response_limit_reached:
                raise _RelayRejected(400, "response_byte_limit")
            if generation and self.token_budget is not None and self._observed_tokens >= self.token_budget:
                self.token_limit_reached = True
                raise _RelayRejected(400, "token_budget_exceeded")
            if self._request_count >= self.max_requests:
                raise _RelayRejected(429)
            self._request_count += 1

    def _reserve_response(self, count: int) -> None:
        self._remaining()
        with self._lock:
            self._response_bytes_received += count
            if self._response_limit_reached or self._response_bytes + count > self.max_response_bytes:
                self._response_limit_reached = True
                raise _RelayRejected(400, "response_byte_limit")
            self._response_bytes += count

    def response_transport(self) -> dict[str, object]:
        with self._lock:
            return {"scope": "api_response_cumulative", "received_bytes": self._response_bytes_received,
                "limit_bytes": self.max_response_bytes, "limit_reached": self._response_limit_reached}

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._lock:
            active = tuple(self._sockets)
        for connection in active:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.socket_path.unlink(missing_ok=True)
