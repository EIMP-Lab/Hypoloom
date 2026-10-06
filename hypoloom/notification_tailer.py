"""Explicitly launched, read-only product-event notifications (at-least-once).

The only writes are logs and atomic per-sink cursors under a disjoint output
root. Cursors are delivery progress, never research authority. Remote success
followed by a crash before cursor replacement can duplicate a notification.
Telegram uses the documented JSON sendMessage API, without paid broadcasts:
https://core.telegram.org/bots/api#sendmessage
"""
from __future__ import annotations

import argparse
import errno
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import stat
import sys
import tempfile
import time
import threading
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


class NotificationDeliveryError(NotificationError):
    def __init__(self, code, *, phase, retryable=True, http_status=None, errno=None, retry_after=None):
        self.details = {"code": code, "phase": phase, "retryable": retryable,
                        "http_status": http_status, "errno": errno, "retry_after_seconds": retry_after}
        super().__init__(f"{code} ({phase})" + (f" HTTP {http_status}" if http_status is not None else "")
            + "; " + ("retry pending" if retryable else "operator action required"))


def delivery_exception(error, phase):
    """Only fixed labels/numeric codes cross the private transport boundary."""
    if isinstance(error, NotificationDeliveryError): return error
    code = ("dns_failed" if isinstance(error, socket.gaierror) else
            "tls_certificate_failed" if isinstance(error, ssl.SSLCertVerificationError) else
            "tls_failed" if isinstance(error, ssl.SSLError) else
            "timeout" if isinstance(error, TimeoutError) else
            {errno.ENETUNREACH: "network_unreachable", errno.EHOSTUNREACH: "host_unreachable",
             errno.ECONNREFUSED: "connection_refused", errno.ECONNRESET: "connection_reset",
             errno.EPERM: "permission_denied", errno.EACCES: "permission_denied"}.get(getattr(error, "errno", None),
                "local_write_failed" if phase in {"log_write", "cursor_write"} else "transport_failed"))
    return NotificationDeliveryError(code, phase=phase, errno=getattr(error, "errno", None),
        retryable=code not in {"tls_certificate_failed", "permission_denied"})


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass
class DeliveryReport:
    delivered: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)


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
    phase = "connect"
    try:
        connection.connect()
        phase = "request"
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request("POST", path, body=json.dumps(document, ensure_ascii=False).encode("utf-8"),
                           headers={"Content-Type": "application/json", **headers})
        phase = "response"
        response = connection.getresponse()
        body = response.read(65537)
        if len(body) > 65536:
            raise NotificationError("notification HTTP response exceeds 64 KiB")
        retry_after = response.getheader("Retry-After")
        return HTTPResponse(response.status, body, {} if retry_after is None else {"retry-after": retry_after})
    except (OSError, http.client.HTTPException) as exc:
        raise delivery_exception(exc, phase) from None
    finally:
        connection.close()


def notification_secret(environ: Mapping[str, str], config: Mapping[str, Any], field: str) -> str:
    name = config.get(field)
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
        raise NotificationError(f"missing or invalid environment variable name for {field}")
    value = environ.get(name)
    if not value:
        raise NotificationError(f"missing environment variable {name}")
    return value

def notification_remote(sink: str, config: Mapping[str, Any], environ: Mapping[str, str]) -> tuple[str, dict[str, str], str | None]:
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
            token = notification_secret(environ, config, "bot_token_env")
            chat_id = notification_secret(environ, config, "chat_id_env")
        if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) is None:
            raise NotificationError("invalid Telegram bot token format")
        return f"https://api.telegram.org/bot{token}/sendMessage", {}, chat_id
    entries = None
    if config.get("credentials_file") is not None:
        if config.get("url_env") is not None or config.get("token_env") is not None:
            raise NotificationError("ambiguous secret source; use a Profile/file or environment names")
        from .credentials import WEBHOOK_URL_KEY
        try:
            entries = read_credentials_file(config["credentials_file"])
            url = credential(entries, WEBHOOK_URL_KEY)
        except CredentialError as exc:
            raise NotificationError(str(exc)) from None
    else:
        url = notification_secret(environ, config, "url_env")
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
    from .credentials import WEBHOOK_TOKEN_KEY
    token = entries.get(WEBHOOK_TOKEN_KEY) if entries is not None else (
        notification_secret(environ, config, "token_env") if config.get("token_env") else None)
    if token:
        if "\r" in token or "\n" in token:
            raise NotificationError("invalid webhook token format")
        headers["Authorization"] = "Bearer " + token
    return url, headers, None


def notification_log_path(config: Mapping[str, Any]) -> PurePosixPath:
    path = config.get("path", "events.jsonl")
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise NotificationError("log path must be relative to the notification run output")
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] == "cursors":
        raise NotificationError("log path must stay outside cursors and inside the notification run output")
    if config.get("level", "info").lower() not in _LEVELS:
        raise NotificationError("unsupported notification log level")
    return relative


def notification_environment(credentials_file: str | None = None, *, environ=None) -> Mapping[str, str]:
    """A selected existing file replaces ambient notification values, never model auth."""
    if credentials_file is not None:
        return read_credentials_file(credentials_file)
    return os.environ if environ is None else environ


def notification_proxy(config, environ, proxy_env):
    if config.get("credentials_file") is not None:
        if proxy_env is not None:
            raise NotificationError("use the Profile proxy_url or --notification-proxy-env, not both")
        from .credentials import NOTIFICATION_PROXY_KEY
        try:
            value = credential(read_credentials_file(config["credentials_file"]), NOTIFICATION_PROXY_KEY)
        except CredentialError as exc:
            raise NotificationError(str(exc)) from None
    else:
        value = None if proxy_env is None else notification_secret(environ, {"proxy_env": proxy_env}, "proxy_env")
    if value is not None:
        _proxy_address(value)
    return value


def notification_output_root(state_root: Path, output_root: str | Path) -> Path:
    candidate = Path(output_root).absolute()
    _safe_path(candidate)
    resolved = candidate.resolve()
    state = Path(state_root).resolve()
    if resolved == state or resolved in state.parents or state in resolved.parents:
        raise NotificationError("notification output root must be disjoint from state root")
    return resolved


def notification_preflight(config: Mapping[str, Any], *, state_root: Path,
                           output_root: Path | None = None, environ=None, proxy_env: str | None = None) -> dict:
    """Use runtime validators without sending, listening or creating delivery progress."""
    environment = notification_environment(environ=environ)
    output = notification_output_root(state_root,
        output_root if output_root is not None else state_root.with_name(state_root.name + "-notifications"))
    sinks = config.get("sinks", {})
    if "log" in sinks: notification_log_path(sinks["log"])
    enabled = [name for name, value in sinks.items() if name == "log" or value.get("enabled", False)]
    for name in ("telegram", "webhook"):
        if name in enabled: notification_remote(name, sinks[name], environment)
    proxy = notification_proxy(config, environment, proxy_env)
    if "dashboard" in enabled:
        from .web_observer import _require_loopback_host
        _require_loopback_host(sinks["dashboard"].get("host", "127.0.0.1"))
    details = {}
    for name in enabled:
        sink = sinks[name]
        details[name] = {"events": config.get("events", {}).get(name, sorted(_EVENT_TYPES) if name == "log" else [])}
        # Source references suffice for review; recipients, tokens and webhook
        # paths remain in their private files, never in interactive transcripts.
        details[name].update({key: sink[key] for key in
            ("credentials_file", "bot_token_env", "chat_id_env", "url_env", "token_env", "host", "port", "path", "level") if key in sink})
    return {"enabled_sinks": enabled, "sinks": details, "proxy": "configured" if proxy else "direct",
            "output_root": str(output), "network_probe_performed": False,
            "delivery_status": "unverified", "message": "Configuration valid; remote delivery has not been verified. Use notify --test to check access, --send-test to send one message."}


def check_delivery_response(response, sink, *, now):
    from .provider_observation import retry_after_seconds
    retry_after = retry_after_seconds(response.headers.get("retry-after"), now=now)
    data = None
    if sink == "telegram":
        try: data = json.loads(response.body)
        except (ValueError, UnicodeError): pass
        if isinstance(data, dict):
            value = data.get("parameters", {}).get("retry_after") if isinstance(data.get("parameters"), dict) else None
            if type(value) is int and value >= 0: retry_after = retry_after_seconds(str(value), now=now)
    if 200 <= response.status < 300 and (sink != "telegram" or isinstance(data, dict) and data.get("ok") is True): return data
    code = {401: "authentication_failed", 403: "forbidden", 404: "endpoint_not_found", 429: "rate_limited"}.get(response.status, "http_error")
    if isinstance(data, dict):
        description = str(data.get("description", "")).lower()
        if "chat not found" in description: code = "chat_not_found"
        elif "blocked" in description: code = "bot_blocked"
        elif "kicked" in description or "not a member" in description: code = "bot_not_a_member"
    retryable = response.status in {408, 429} or response.status >= 500 or 200 <= response.status < 300
    raise NotificationDeliveryError(code, phase="api_response", retryable=retryable,
        http_status=response.status, retry_after=retry_after)


class NotificationTailer:
    def __init__(self, state_root: str | Path, run_id: str, output_root: str | Path, *,
                 environ: Mapping[str, str] | None = None, transport: Callable[..., HTTPResponse] | None = None,
                 proxy_env: str | None = None, observation_reader: Callable[[], Mapping[str, Any]] | None = None,
                 dashboard_port: Callable[[], int | None] | None = None, clock=time.time):
        if not isinstance(run_id, str) or _SAFE_ID.fullmatch(run_id) is None:
            raise NotificationError("invalid notification run identity")
        self.state_root = Path(state_root).resolve()
        self.output_root = notification_output_root(self.state_root, output_root)
        self.run_id = run_id
        self.observation_reader = observation_reader
        self.dashboard_port = dashboard_port
        self.directory = self.output_root / run_id
        self.environ = os.environ if environ is None else environ
        self.transport = post_json if transport is None else transport
        self.clock = clock
        self._health_lock = threading.Lock()
        self._health = {}
        config = load_notification_config(self.state_root, run_id)
        self.proxy_url = notification_proxy(config, self.environ, proxy_env)
        self.sinks = config.get("sinks", {})
        self.events = config.get("events", {})
        if not isinstance(self.sinks, dict) or not isinstance(self.events, dict):
            raise NotificationError("notification sinks and event selections must be mappings")
        self.log_path = None
        if "log" in self.sinks:
            relative = notification_log_path(self.sinks["log"])
            self.log_path = self.directory.joinpath(*relative.parts)

    def health(self):
        with self._health_lock:
            return {name: {**value,
                    **({"route": "http_proxy" if self.proxy_url else "direct"} if name == "telegram" else {}),
                    "failure": dict(value["failure"]) if value.get("failure") else None}
                    for name, value in self._health.items()}

    def retry(self, sink="telegram"):
        if sink not in {"telegram", "webhook"} or not self.sinks.get(sink, {}).get("enabled"):
            raise NotificationError("notification sink is not enabled")
        with self._health_lock:
            self._health.setdefault(sink, {}).update(state="recovering", next_retry_at=None, failure_count=0)

    def _update_health(self, sink, **values):
        with self._health_lock:
            self._health.setdefault(sink, {"state": "unverified", "pending": 0, "failure_count": 0,
                "last_attempt_at": None, "last_success_at": None, "next_retry_at": None, "failure": None}).update(values)

    def _secret(self, config: Mapping[str, Any], field: str) -> str:
        return notification_secret(self.environ, config, field)

    def log_literature_event(self, event: Mapping[str, Any]) -> None:
        """Live operational diagnostics, local log only; no research cursor/fact."""
        if event.get("run_id") != self.run_id or event.get("type") != "literature_access":
            raise NotificationError("invalid literature log scope")
        if self.log_path is not None:
            level = 30 if event["payload"]["to"] in {"fetch_unavailable", "browser_unavailable", "failed"} else 20
            if level >= _LEVELS[self.sinks["log"].get("level", "info").lower()]:
                self._deliver("log", event, None)

    def _remote(self, sink: str) -> tuple[str, dict[str, str], str | None]:
        return notification_remote(sink, self.sinks[sink], self.environ)

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
            if event["type"] == "notification_backlog":
                text += "\nEarlier routine notifications: " + ", ".join(f"{key}: {value}" for key, value in payload["counts"].items())
                text += "\nThis summarizes earlier events; consult Web/TUI for the current state."
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
            if event["type"] == "resource_blocked":
                text += "\nGPU infrastructure blocked: " + str(event.get("code", "unknown")) + ". Existing experiments continue. Repair the bound pool, then use TUI g or hypoloom resource-recheck."
            if event["type"] == "resource_rechecked":
                text += "\nGPU pool rechecked. Blocked nodes may retry within their existing budgets."
            if event["type"] in {"provider_auth_cooldown", "provider_waiting"}:
                until = payload.get("next_retry_at", payload.get("cooldown_until"))
                when = (datetime.fromtimestamp(float(until), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    if type(until) in (int, float) else "unknown; operator action required")
                text += (f"\n⚠️ API unavailable: {payload.get('reason', 'auth_failed')} (HTTP {payload.get('http_status')})."
                    f"\nNext recovery attempt: {when}. This is not a quota reset estimate."
                    "\nSubmitted experiments continue. TUI: p; CLI: hypoloom provider-retry-clear."
                    "\nTo switch API, select a protected connection file with --settings.")
            if event["type"] in {"provider_auth_resumed", "provider_retry_requested"}:
                text += "\nProvider retry requested by operator. Access is not yet confirmed; awaiting a successful response."
            if event["type"] == "provider_recovered":
                text += "\n✅ API response received; provider availability recovered. Research progress remains subject to task and budget limits."
            if event["type"].startswith("literature_"):
                text += f"\nLiterature service: {payload.get('active_service', payload.get('service', 'openalex'))}; {payload.get('reason') or event['type']}."
                if payload.get("active_service") == "crossref": text += "\nCrossref fallback succeeded; OpenAlex recovery remains unconfirmed."
                if payload.get("requires_operator"):
                    text += "\nOperator action required. Repair the literature connection, then use Literature retry in Web/TUI or provider-retry-clear --service openalex."
                elif payload.get("next_retry_at") is not None:
                    text += "\nNext attempt: " + datetime.fromtimestamp(payload['next_retry_at'], timezone.utc).isoformat()
                if event["type"] == "literature_recovered": text += "\nAn actual retrieval succeeded; research remains subject to task and budget limits."
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
        check_delivery_response(response, sink, now=self.clock())

    def poll_once(self) -> DeliveryReport:
        report = DeliveryReport()
        observation = (build_product_observation(self.state_root, self.run_id)
                       if self.observation_reader is None else self.observation_reader())
        events = sorted(observation["events"], key=lambda event: (event["source"], event["source_id"], event["revision"]))
        routine = {"run_started", "new_best_result", "expert_round_done", "stage_started", "stage_finished",
                   "hypothesis_added", "experiment_started", "experiment_submitted", "experiment_finished"}
        for sink in ("log", "telegram", "webhook"):
            if sink not in self.sinks or (sink != "log" and not self.sinks[sink].get("enabled", False)):
                continue
            report.delivered[sink] = 0
            self._update_health(sink)
            selected = self.events.get(sink, sorted(_EVENT_TYPES) if sink == "log" else [])
            if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
                report.errors.append(f"{sink}: event selection must be a list of names")
                continue
            selected = list(selected)
            if "provider_auth_cooldown" in selected: selected.append("provider_waiting")
            if "provider_auth_resumed" in selected: selected.extend(("provider_retry_requested", "provider_recovered"))
            if "error" in selected: selected.extend(("literature_waiting", "literature_retry_requested", "literature_recovered"))
            unknown = set(selected) - _EVENT_TYPES
            if unknown: report.warnings.append(f"{sink}: no implemented event source for {', '.join(sorted(unknown))}")
            if not selected:
                report.warnings.append(f"{sink}: no event types explicitly selected; nothing delivered")
                continue
            phase = "cursor_read"
            try:
                path = self.directory / "cursors" / (sink + ".json")
                cursor = _read_cursor(path)
                pending = [event for event in events if event["type"] in selected
                    and event["revision"] > cursor.get(f"{event['source']}:{event['source_id']}", 0)]
                if sink == "log":
                    level = _LEVELS[self.sinks["log"].get("level", "info").lower()]
                    pending = [event for event in pending if (40 if event["type"] in {"experiment_failed", "error"}
                        else 30 if event["type"] == "budget_warning" else 20) >= level]
                self._update_health(sink, pending=len(pending))
                health = self.health()[sink]
                if health.get("state") == "waiting_operator" or (health.get("next_retry_at") or 0) > self.clock():
                    continue
                if not pending: continue
                phase = "configuration"
                remote = self._remote(sink) if sink != "log" else None
                batches = [[event] for event in pending]
                # A summary advances the same existing source cursors only after
                # acknowledgment. Human decisions/errors are never swallowed.
                if sink == "telegram":
                    batches, ordinary = [], []
                    for event in pending:
                        if event["type"] in routine:
                            ordinary.append(event)
                        else:
                            batches.extend([ordinary] if len(ordinary) >= 5 else [[item] for item in ordinary])
                            ordinary = []
                            batches.append([event])
                    batches.extend([ordinary] if len(ordinary) >= 5 else [[item] for item in ordinary])
                if sink != "log": batches = batches[:5]
                for batch in batches:
                    event = batch[0]
                    if len(batch) > 1:
                        from collections import Counter
                        event = {**event, "type": "notification_backlog",
                            "payload": {"counts": dict(sorted(Counter(e["type"] for e in batch).items()))}}
                    phase = "log_write" if sink == "log" else "transport"
                    self._update_health(sink, last_attempt_at=self.clock())
                    self._deliver(sink, event, remote)
                    next_cursor = dict(cursor)
                    for source in batch:
                        key = f"{source['source']}:{source['source_id']}"
                        next_cursor[key] = max(next_cursor.get(key, 0), source["revision"])
                    phase = "cursor_write"
                    _write_cursor(path, next_cursor)
                    cursor = next_cursor
                    report.delivered[sink] += len(batch)
                    self._update_health(sink, state="healthy", last_success_at=self.clock(), failure_count=0,
                        next_retry_at=None, failure=None, pending=max(0, len(pending) - report.delivered[sink]))
            except (NotificationError, OSError, ValueError, http.client.HTTPException) as error:
                if isinstance(error, NotificationDeliveryError): failure = error
                elif isinstance(error, NotificationError):
                    failure = NotificationDeliveryError("configuration_invalid" if phase == "configuration" else "delivery_validation_failed",
                        phase=phase, retryable=False)
                else: failure = delivery_exception(error, phase)
                count = self.health()[sink].get("failure_count", 0) + 1
                delay = min(30.0 * 2 ** min(count - 1, 4), 300.0)
                retry_after = failure.details.get("retry_after_seconds")
                if retry_after is not None: delay = max(delay, retry_after)
                self._update_health(sink, state="cooldown" if failure.details["retryable"] else "waiting_operator",
                    failure_count=count, failure=failure.details,
                    next_retry_at=self.clock() + delay if failure.details["retryable"] else None)
                # Existing safe configuration errors remain useful to operators.
                report.errors.append(f"{sink}: {error if isinstance(error, NotificationError) else failure}")
        report.health = self.health()
        return report


def test_notification(config, *, environ=None, proxy_env=None, send=False, transport=post_json):
    """Explicit Telegram probe, independent of delivery cursors and research state."""
    result = {"steps": [], "message_sent": False}
    environment = os.environ if environ is None else environ
    try:
        if not config.get("sinks", {}).get("telegram", {}).get("enabled"):
            raise NotificationError("Telegram is not enabled")
        url, _, chat = notification_remote("telegram", config["sinks"]["telegram"], environment)
        proxy = notification_proxy(config, environment, proxy_env)
        plan = [("getMe", {}), ("getChat", {"chat_id": chat})]
        if send: plan.append(("sendMessage", {"chat_id": chat, "text": "Hypoloom notification test — explicitly requested by the operator. This message does not control research."}))
        for method, document in plan:
            options = {"proxy_url": proxy} if proxy else {}
            response = transport(url.rsplit("/", 1)[0] + "/" + method, document, headers={}, timeout=10, **options)
            check_delivery_response(response, "telegram", now=time.time())
            result["steps"].append({"method": method, "http_status": response.status, "ok": True})
            if method == "sendMessage": result["message_sent"] = True
        result["ok"] = True
    except (NotificationError, OSError, ValueError, http.client.HTTPException) as exc:
        error = exc if isinstance(exc, NotificationDeliveryError) else (
            NotificationDeliveryError("configuration_invalid", phase="configuration", retryable=False)
            if isinstance(exc, NotificationError) else delivery_exception(exc, "transport"))
        result.update(ok=False, failure=error.details)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, at-least-once notification tailer; never resumes a run")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--test", action="store_true", help="check Bot authentication and recipient without sending")
    action.add_argument("--send-test", action="store_true", help="check access and send one labelled diagnostic message")
    action.add_argument("--retry", action="store_true", help="request retry of the managed Telegram sink")
    parser.add_argument("--once", action="store_true", help="poll once; exit 1 on delivery failure")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--proxy-env", help="explicit environment variable containing an HTTP CONNECT proxy; default: direct")
    args = parser.parse_args(argv)
    if not (args.test or args.send_test or args.retry) and not args.output_root: parser.error("delivery requires an explicit --output-root")
    if not math.isfinite(args.poll_interval) or args.poll_interval <= 0:
        parser.error("poll interval must be finite and positive")
    reader = None
    try:
        if args.test or args.send_test:
            result = test_notification(load_notification_config(args.state_root, args.run_id), proxy_env=args.proxy_env, send=args.send_test)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["ok"] else 1
        if args.retry:
            from .control_protocol import ControlRequest, request_over_socket
            import uuid
            reply = request_over_socket(Path(args.state_root) / "control.sock", ControlRequest(1,
                "notification-retry-" + uuid.uuid4().hex, None, "NOTIFICATION_RETRY", {"run_id": args.run_id, "sink": "telegram"}), timeout=5)
            print(json.dumps(reply.to_mapping(), ensure_ascii=False))
            return 0 if reply.status == "accepted" else 1
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
