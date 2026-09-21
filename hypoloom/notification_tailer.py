"""Explicitly launched, read-only product-event notifications (at-least-once).

The only writes are logs and atomic per-sink cursors under a disjoint output
root. Cursors are delivery progress, never research authority. Remote success
followed by a crash before cursor replacement can duplicate a notification.
Telegram uses the documented JSON sendMessage API, without paid broadcasts:
https://core.telegram.org/bots/api#sendmessage
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .artifacts import _SAFE_ID
from .credentials import (TELEGRAM_BOT_TOKEN_KEY, TELEGRAM_CHAT_ID_KEY, CredentialError,
                          credential, read_credentials_file)
from .observation_api import PRODUCT_EVENT_TYPES, build_product_observation
from .state_store import StateStore


_EVENT_TYPES = PRODUCT_EVENT_TYPES
_LEVELS = {"debug": 10, "info": 20, "warning": 30, "warn": 30, "error": 40, "critical": 50}


class NotificationError(ValueError):
    """A safe-to-display configuration or delivery error; never include secrets."""


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes = b""


@dataclass
class DeliveryReport:
    delivered: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_notification_config(state_root: str | Path, run_id: str) -> dict[str, Any]:
    """Read immutable admitted Profile-v4 settings; never initialize a store."""
    facts = StateStore(state_root).list_facts(run_id, "GoalDefined")
    if len(facts) != 1:
        raise NotificationError("run must have exactly one GoalDefined fact")
    contract = json.loads(facts[0]["payload_json"])["research_contract"]
    return contract.get("methodology", {}).get("controls", {}).get("product", {}).get("notify", {})


def _safe_path(path: Path) -> None:
    for item in (*reversed(path.parents), path):
        if item.is_symlink():
            raise NotificationError("notification output must not follow symlinks")
        if item.exists() and (not item.is_dir() if item != path else not (item.is_file() or item.is_dir())):
            raise NotificationError("notification output contains a non-regular path")


def _read_cursor(path: Path) -> dict[str, int]:
    _safe_path(path)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(not isinstance(key, str) or not key or type(revision) is not int or revision < 1 for key, revision in value.items()):
        raise NotificationError("invalid notification cursor; not resetting delivery progress")
    return value


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _make_directories(path: Path) -> None:
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _sync_directory(created.parent)


def _write_cursor(path: Path, value: Mapping[str, int]) -> None:
    _safe_path(path)
    _make_directories(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=".cursor-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _proxy_address(url: str) -> tuple[str, int]:
    try:
        if not isinstance(url, str) or any(ord(char) < 33 or ord(char) == 127 for char in url):
            raise ValueError
        parts = urlsplit(url)
        port = parts.port if parts.port is not None else 80
        if (parts.scheme != "http" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.path not in {"", "/"}
                or any(char in url for char in "?#\\") or not 1 <= port <= 65535):
            raise ValueError
        return parts.hostname, port
    except (ValueError, TypeError):
        raise NotificationError("invalid notification proxy; require a credential-free HTTP proxy origin") from None


def post_json(url: str, document: Mapping[str, Any], *, headers: Mapping[str, str], timeout: float,
              proxy_url: str | None = None) -> HTTPResponse:
    """Bounded POST; optional explicit CONNECT, no ambient proxy or redirects."""
    parts = urlsplit(url)
    proxy = None if proxy_url is None else _proxy_address(proxy_url)
    if proxy is not None and parts.scheme == "https":
        connection = http.client.HTTPSConnection(*proxy, timeout=timeout)
        # Standard library verifies the tunnel target, not the proxy hostname.
        # Never send the Telegram token/path or webhook auth in CONNECT headers.
        connection.set_tunnel(parts.hostname, parts.port or 443)
    else:
        connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parts.hostname, parts.port, timeout=timeout)
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request("POST", path, body=json.dumps(document, ensure_ascii=False).encode("utf-8"),
                           headers={"Content-Type": "application/json", **headers})
        response = connection.getresponse()
        body = response.read(65537)
        if len(body) > 65536:
            raise NotificationError("notification HTTP response exceeds 64 KiB")
        return HTTPResponse(response.status, body)
    finally:
        connection.close()


class NotificationTailer:
    def __init__(self, state_root: str | Path, run_id: str, output_root: str | Path, *,
                 environ: Mapping[str, str] | None = None, transport: Callable[..., HTTPResponse] | None = None,
                 proxy_env: str | None = None, observation_reader: Callable[[], Mapping[str, Any]] | None = None,
                 dashboard_port: Callable[[], int | None] | None = None):
        if not isinstance(run_id, str) or _SAFE_ID.fullmatch(run_id) is None:
            raise NotificationError("invalid notification run identity")
        self.state_root = Path(state_root).resolve()
        candidate = Path(output_root).absolute()
        _safe_path(candidate)
        self.output_root = candidate.resolve()
        if (self.output_root == self.state_root or self.output_root in self.state_root.parents
                or self.state_root in self.output_root.parents):
            raise NotificationError("notification output root must be disjoint from state root")
        self.run_id = run_id
        self.observation_reader = observation_reader
        self.dashboard_port = dashboard_port
        self.directory = self.output_root / run_id
        self.environ = os.environ if environ is None else environ
        self.transport = post_json if transport is None else transport
        self.proxy_url = None if proxy_env is None else self._secret({"proxy_env": proxy_env}, "proxy_env")
        if self.proxy_url is not None:
            _proxy_address(self.proxy_url)
        config = load_notification_config(self.state_root, run_id)
        self.sinks = config.get("sinks", {})
        self.events = config.get("events", {})
        if not isinstance(self.sinks, dict) or not isinstance(self.events, dict):
            raise NotificationError("notification sinks and event selections must be mappings")
        self.log_path = None
        if "log" in self.sinks:
            path = self.sinks["log"].get("path", "events.jsonl")
            if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
                raise NotificationError("log path must be relative to the notification run output")
            relative = PurePosixPath(path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] == "cursors":
                raise NotificationError("log path must stay outside cursors and inside the notification run output")
            if self.sinks["log"].get("level", "info").lower() not in _LEVELS:
                raise NotificationError("unsupported notification log level")
            self.log_path = self.directory.joinpath(*relative.parts)

    def _secret(self, config: Mapping[str, Any], field: str) -> str:
        name = config.get(field)
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise NotificationError(f"missing or invalid environment variable name for {field}")
        value = self.environ.get(name)
        if not value:
            raise NotificationError(f"missing environment variable {name}")
        return value

    def _remote(self, sink: str) -> tuple[str, dict[str, str], str | None]:
        config = self.sinks[sink]
        if sink == "telegram":
            if config.get("credentials_file") is not None:
                if config.get("bot_token_env") is not None or config.get("chat_id_env") is not None:
                    raise NotificationError("ambiguous secret source; use exactly one of credentials_file or bot_token_env/chat_id_env")
                try:
                    entries = read_credentials_file(config["credentials_file"])
                    token = credential(entries, TELEGRAM_BOT_TOKEN_KEY)
                    chat_id = credential(entries, TELEGRAM_CHAT_ID_KEY)
                except CredentialError as exc:
                    raise NotificationError(str(exc)) from None
            else:
                token = self._secret(config, "bot_token_env")
                chat_id = self._secret(config, "chat_id_env")
            if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) is None:
                raise NotificationError("invalid Telegram bot token format")
            return f"https://api.telegram.org/bot{token}/sendMessage", {}, chat_id
        url = self._secret(config, "url_env")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.fragment or any(ord(char) < 33 for char in url):
            raise NotificationError("invalid webhook URL; use a credential-free HTTP(S) URL")
        if parts.scheme == "http":
            try:
                loopback = ipaddress.ip_address(parts.hostname).is_loopback
            except ValueError:
                loopback = parts.hostname == "localhost"
            if not loopback:
                raise NotificationError("webhook requires HTTPS except for a loopback receiver")
        headers = {}
        if config.get("token_env"):
            token = self._secret(config, "token_env")
            if "\r" in token or "\n" in token:
                raise NotificationError("invalid webhook token format")
            headers["Authorization"] = "Bearer " + token
        return url, headers, None

    def _deliver(self, sink: str, event: Mapping[str, Any], remote) -> None:
        if sink == "log":
            path = self.log_path
            assert path is not None
            _safe_path(path)
            _make_directories(path.parent)
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise NotificationError("notification log must be a regular file")
                handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _sync_directory(path.parent)
            return
        url, headers, chat_id = remote
        document = event
        if sink == "telegram":
            # Only public identifiers/status, never operator text or raw logs.
            payload = event.get("payload", {})
            summary = " · ".join(str(payload[key]) for key in ("role", "node", "experiment_id", "outcome", "verdict", "error_code") if payload.get(key) is not None)
            text = f"Hypoloom {event['type']}\nRun: {self.run_id}\nEvent: {event['event_id']}"
            if summary: text += "\n" + summary
            if event["type"] in {"grounding_question", "grounding_answered"}:
                requested = event["type"] == "grounding_question"
                text += "\n👤 Human decision requested" if requested else "\n✅ Human answer recorded"
                ordinal = payload.get("ordinal")
                if type(ordinal) is int and ordinal > 0:
                    text += f"\nQuestion: {ordinal}"
                if requested:
                    text += ("\nTUI: press h → Review answer → Submit answer."
                             "\nWeb: choose Browser in the TUI decision form for an operator answer link."
                             "\nTelegram replies do not submit an answer.")
                else:
                    text += "\nView the recorded answer and current run state in TUI/Web."
            if event["type"] == "provider_auth_cooldown":
                until = payload.get("cooldown_until")
                when = (datetime.fromtimestamp(float(until), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    if type(until) in (int, float) else "the configured cooldown end")
                text += (f"\n⚠️ Provider authentication rejected (HTTP {payload.get('http_status')})."
                    f"\nNew Agent dispatch is paused until {when}."
                    "\nAfter rotating the credential: TUI press p → Confirm resume, or CLI: hypoloom provider-retry-clear."
                    "\nWithout action, dispatch resumes automatically when the cooldown ends.")
            if event["type"] == "provider_auth_resumed":
                text += "\n✅ Provider dispatch cooldown cleared by operator; scheduling resumes."
            dashboard = self.sinks.get("dashboard", {})
            if dashboard.get("enabled") and dashboard.get("host", "127.0.0.1") in {"127.0.0.1", "localhost"}:
                # The owning service supplies its actual bound port, including
                # None on bind failure. Standalone delivery cannot discover it.
                port = self.dashboard_port() if self.dashboard_port is not None else dashboard.get("port")
                if type(port) is int and 1 <= port <= 65535:
                    label = "local/tunnel" if self.dashboard_port is not None else "configured; local/tunnel"
                    text += f"\nDashboard ({label}): http://127.0.0.1:{port}/"
                else:
                    text += "\nDashboard: see TUI Web / local access or Host startup log for the current address."
            if len(text) > 4096:
                raise NotificationError("Telegram event identity exceeds message limit")
            document = {"chat_id": chat_id, "text": text}
        options = {"proxy_url": self.proxy_url} if self.proxy_url is not None and urlsplit(url).scheme == "https" else {}
        response = self.transport(url, document, headers=headers, timeout=10.0, **options)
        if not 200 <= response.status < 300:
            raise NotificationError(f"notification HTTP status {response.status}; retry pending")
        if sink == "telegram":
            result = json.loads(response.body)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise NotificationError("Telegram API rejected notification; retry pending")

    def poll_once(self) -> DeliveryReport:
        report = DeliveryReport()
        observation = (build_product_observation(self.state_root, self.run_id)
                       if self.observation_reader is None else self.observation_reader())
        events = sorted(observation["events"], key=lambda event: (event["source"], event["source_id"], event["revision"]))
        for sink in ("log", "telegram", "webhook"):
            if sink not in self.sinks or (sink != "log" and not self.sinks[sink].get("enabled", False)):
                continue
            report.delivered[sink] = 0
            selected = self.events.get(sink, sorted(_EVENT_TYPES) if sink == "log" else [])
            if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
                report.errors.append(f"{sink}: event selection must be a list of names")
                continue
            unknown = set(selected) - _EVENT_TYPES
            if unknown:
                report.warnings.append(f"{sink}: no implemented event source for {', '.join(sorted(unknown))}")
            if not selected:
                report.warnings.append(f"{sink}: no event types explicitly selected; nothing delivered")
                continue
            try:
                path = self.directory / "cursors" / (sink + ".json")
                cursor = _read_cursor(path)
                remote = self._remote(sink) if sink != "log" else None
                for event in events:
                    key = f"{event['source']}:{event['source_id']}"
                    if event["type"] not in selected or event["revision"] <= cursor.get(key, 0):
                        continue
                    if sink == "log":
                        level = 40 if event["type"] in {"experiment_failed", "error"} else 30 if event["type"] == "budget_warning" else 20
                        if level < _LEVELS[self.sinks["log"].get("level", "info").lower()]:
                            continue
                    self._deliver(sink, event, remote)
                    next_cursor = {**cursor, key: event["revision"]}
                    _write_cursor(path, next_cursor)
                    cursor = next_cursor
                    report.delivered[sink] += 1
            except NotificationError as error:
                report.errors.append(f"{sink}: {error}")
            except (OSError, ValueError, http.client.HTTPException) as error:
                # Exception strings can include URLs, bearer tokens or response bodies.
                report.errors.append(f"{sink}: {type(error).__name__}; delivery pending, retry on next poll")
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, at-least-once notification tailer; never resumes a run")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--once", action="store_true", help="poll once; exit 1 on delivery failure")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--proxy-env", help="explicit environment variable containing an HTTP CONNECT proxy; default: direct")
    args = parser.parse_args(argv)
    if not math.isfinite(args.poll_interval) or args.poll_interval <= 0:
        parser.error("poll interval must be finite and positive")
    reader = None
    try:
        if not args.once:
            from .observer_refresh import ObserverRefresh
            reader = ObserverRefresh(args.state_root, args.run_id, refresh_seconds=args.poll_interval)
        tailer = NotificationTailer(args.state_root, args.run_id, args.output_root, proxy_env=args.proxy_env,
            observation_reader=None if reader is None else lambda: reader.get()["activity"])
        while True:
            try:
                result = tailer.poll_once()
            except Exception as error:
                result = DeliveryReport(errors=[f"observation: {type(error).__name__}; retry on next poll"])
            print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
            if args.once:
                return 1 if result.errors else 0
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        return 130
    except NotificationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(f"notification startup failed: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if reader is not None: reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
