"""Private, non-authoritative runtime evidence and credential-redacted copies."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
import traceback
from typing import Any, BinaryIO, Iterable, Mapping

from .diagnostics import _CREDENTIAL_JSON_KEY

REDACTED = "[REDACTED]"
OMITTED_EMBEDDED_CONTENT = "[OMITTED: opaque or invalid embedded content]"
_AUTH = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[^\s\"';,]+")
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:authorization|x-api-key|[\w-]*(?:api[_-]?key|auth[_-]?token|access[_-]?token|"
    r"refresh[_-]?token|password|passwd|secret|private[_-]?key))\s*[\"']?\s*[:=]\s*[\"']?)"
    r"(?!\[REDACTED\])[^\s\"';&,}]+"
)
_URL_AUTH = re.compile(r"(https?://)[^/@\s]+:[^/@\s]+@", re.I)


def _secret_bytes(secrets: Iterable[str | bytes]) -> tuple[bytes, ...]:
    return tuple(sorted({value.encode() if isinstance(value, str) else value
                         for value in secrets if value}, key=len, reverse=True))


def text_credential_values(value: str) -> tuple[str, ...]:
    """Use the existing assignment recognizer for repeated values in text."""
    if ":" not in value and "=" not in value:
        return ()
    secrets = []
    for match in _ASSIGNMENT.finditer(value):
        secret = match[0][len(match[1]):]
        if secret.lower() in {"bearer", "basic"}:
            authorization = _AUTH.match(value, match.start() + len(match[1]))
            if authorization is None:
                continue
            secret = authorization[0].split(None, 1)[1]
        secrets.append(secret)
    return tuple(secrets)


def sanitize_text(value: str, secrets: Iterable[str | bytes] = ()) -> str:
    for secret in _secret_bytes((*secrets, *text_credential_values(value))):
        text = secret.decode("utf-8", errors="ignore")
        if text:
            value = value.replace(text, REDACTED)
    value = _AUTH.sub(REDACTED, value)
    if ":" in value or "=" in value:
        value = _ASSIGNMENT.sub(lambda match: match[1] + REDACTED, value)
    return _URL_AUTH.sub(r"\1[REDACTED]@", value)


def sanitize_payload(value: Any, secrets: Iterable[str | bytes] = ()) -> Any:
    secrets = tuple(secrets)
    if isinstance(value, Mapping):
        clean = {}
        for key, item in value.items():
            if _CREDENTIAL_JSON_KEY.fullmatch(str(key)) or str(key).lower() == "x-api-key":
                item = REDACTED
            elif key == "content_base64":
                raw = decode_embedded_content(value)
                if raw is None:
                    item = OMITTED_EMBEDDED_CONTENT
                else:
                    try:
                        decoded = json.loads(raw)
                    except ValueError:
                        decoded = raw
                    sanitized = sanitize_payload(decoded, secrets)
                    if sanitized != decoded:
                        text = sanitized if isinstance(sanitized, str) else json.dumps(sanitized, ensure_ascii=False)
                        item = base64.b64encode(text.encode()).decode("ascii")
            else:
                item = sanitize_payload(item, secrets)
            clean[sanitize_text(str(key), secrets)] = item
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item, secrets) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except ValueError:
            pass
        else:
            sanitized = sanitize_payload(decoded, secrets)
            if sanitized != decoded:
                return json.dumps(sanitized, ensure_ascii=False)
    return sanitize_text(value, secrets) if isinstance(value, str) else value


def decode_embedded_content(value: Mapping[str, Any]) -> str | None:
    """Decode bounded typed text or an exact, validated WorkspaceArtifact.

    A bare content_base64 field is insufficient. Unknown envelopes, unsafe
    workspace paths and binary content remain omitted in diagnostic copies.
    """
    media, encoded = value.get("media_type", ""), value.get("content_base64")
    if not isinstance(encoded, str) or len(encoded) > 4 * 1024 * 1024 * 4 // 3 + 4:
        return None
    try:
        if set(value) == {"path", "content_base64"}:
            from .agent_aci import WorkspaceArtifact
            raw = WorkspaceArtifact.from_dict(value).content_bytes()
        elif isinstance(media, str) and (media.startswith("text/") or media.endswith("+json")
                or media in {"application/json", "application/x-python", "application/javascript"}):
            raw = base64.b64decode(encoded, validate=True)
        else:
            return None
        text = raw.decode("utf-8")
        return text if all(ord(char) >= 32 or char in "\n\r\t" for char in text) else None
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise OSError("diagnostic directory must not be a symlink")
    if not path.exists():
        _private_directory(path.parent)
        path.mkdir(mode=0o700)
    if not path.is_dir():
        raise NotADirectoryError(str(path))


class RuntimeDiagnostics:
    """Best-effort private IO; failures remain in summary and system stderr."""

    def __init__(self, root: Path, *, secrets: Iterable[str | bytes] = (),
                 _shared: dict[str, Any] | None = None, _disabled: bool = False) -> None:
        self.root = Path(root)
        self._disabled = _disabled
        self._secrets = _secret_bytes(secrets)
        self._shared = _shared if _shared is not None else {
            "capture_errors": [], "redaction_count": 0, "lock": threading.RLock()}
        if self._disabled:
            return
        try:
            _private_directory(self.root)
            self.root.chmod(0o700)
        except OSError as exc:
            self._disabled = True
            self._error("mkdir", exc)

    @classmethod
    def for_agent(cls, state_root: Path, request: Any, *,
                  secrets: Iterable[str | bytes] = ()) -> RuntimeDiagnostics:
        recorder = cls(Path(state_root) / "diagnostics", secrets=secrets)
        return recorder.child("agents").child(request.run_id).child(request.attempt_id)

    def child(self, relative: str, *, secrets: Iterable[str | bytes] = ()) -> RuntimeDiagnostics:
        disabled = self._disabled
        try:
            root = self._path(relative)
        except (OSError, ValueError) as exc:
            self._error("child", exc)
            root, disabled = self.root, True
        return RuntimeDiagnostics(root, secrets=(*self._secrets, *_secret_bytes(secrets)),
                                  _shared=self._shared, _disabled=disabled)

    def _path(self, relative: str) -> Path:
        if self._disabled:
            raise OSError("diagnostic directory is unavailable")
        path = Path(relative)
        if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
            raise ValueError("diagnostic path must be relative without traversal")
        current = self.root
        if current.is_symlink():
            raise OSError("diagnostic root must not be a symlink")
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise OSError("diagnostic path must not contain symlinks")
        return current

    def _error(self, operation: str, exc: BaseException) -> None:
        error = {"operation": operation, "exception_type": type(exc).__name__,
                 "message": sanitize_text(str(exc), self._secrets), "timestamp": _now()}
        with self._shared["lock"]:
            self._shared["capture_errors"].append(error)
        try:
            print("diagnostic capture failed: " + json.dumps(error), file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass

    def _redaction(self, count: int = 1) -> None:
        with self._shared["lock"]:
            self._shared["redaction_count"] += count

    def write_json(self, relative: str, value: Any) -> None:
        try:
            safe = sanitize_payload(value, self._secrets)
            if safe != value:
                self._redaction()
            self.write_bytes(relative, (json.dumps(safe, ensure_ascii=False, indent=2) + "\n").encode())
        except (TypeError, ValueError, UnicodeError) as exc:
            self._error(relative, exc)

    def write_bytes(self, relative: str, value: bytes) -> None:
        for secret in self._secrets:
            count = value.count(secret)
            if count:
                value = value.replace(secret, REDACTED.encode())
                self._redaction(count)
        temporary: str | None = None
        try:
            path = self._path(relative)
            _private_directory(path.parent)
            fd, temporary = tempfile.mkstemp(prefix=".diagnostic-", dir=path.parent)
            with os.fdopen(fd, "wb") as output:
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except (OSError, ValueError) as exc:
            self._error(relative, exc)
        finally:
            if temporary is not None and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError as exc:
                    self._error(relative, exc)

    def _open(self, relative: str, *, append: bool = False) -> BinaryIO:
        path = self._path(relative)
        _private_directory(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND if append else os.O_RDWR | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "ab" if append else "w+b", buffering=0)

    def open_stdio(self, relative: str) -> BinaryIO:
        """Direct durable fd, exclusively for the credential-free full-CC child."""
        try:
            return self._open(relative)
        except (OSError, ValueError) as exc:
            self._error(relative, exc)
            return tempfile.TemporaryFile(mode="w+b")

    def stream(self, relative: str) -> _DiagnosticStream:
        return _DiagnosticStream(self, relative)

    def event(self, kind: str, **fields: Any) -> None:
        self._append("events.jsonl", {"timestamp": _now(), "kind": kind, **fields})

    def _append(self, relative: str, value: Any) -> None:
        try:
            safe = sanitize_payload(value, self._secrets)
            if safe != value:
                self._redaction()
            with self._shared["lock"], self._open(relative, append=True) as output:
                remaining = memoryview((json.dumps(safe, ensure_ascii=False) + "\n").encode())
                while remaining:
                    count = output.write(remaining)
                    if not count:
                        raise OSError("diagnostic write made no progress")
                    remaining = remaining[count:]
                os.fsync(output.fileno())
        except (OSError, ValueError, TypeError) as exc:
            self._error(relative, exc)

    def summary(self) -> dict[str, Any]:
        with self._shared["lock"]:
            return {"root": str(self.root), "capture_errors": list(self._shared["capture_errors"]),
                    "redacted": bool(self._shared["redaction_count"]),
                    "redaction_count": self._shared["redaction_count"]}


def _redact_stream(pending: bytes, secrets: tuple[bytes, ...], *, final: bool):
    """One cross-chunk redactor for synchronous files and deferred copies."""
    keep = 0 if final else max((len(item) - 1 for item in secrets), default=0)
    output, redactions = bytearray(), 0
    while pending:
        safe_end = max(0, len(pending) - keep)
        matches = [(pending.find(secret), secret) for secret in secrets]
        matches = [(index, secret) for index, secret in matches if index >= 0]
        match = min(matches, key=lambda item: (item[0], -len(item[1]))) if matches else None
        if match is not None and match[0] < safe_end:
            index, secret = match
            output.extend(pending[:index] + REDACTED.encode())
            pending = pending[index + len(secret):]
            redactions += 1
        else:
            output.extend(pending[:safe_end])
            pending = pending[safe_end:]
            break
    return bytes(output), pending, redactions


class _DiagnosticStream:
    def __init__(self, recorder: RuntimeDiagnostics, relative: str) -> None:
        self.recorder, self.relative = recorder, relative
        self._pending = b""
        self._closed = False
        self._output: BinaryIO | None = None
        self._errors: list[str] = []
        self._received = self._written = self._redactions = 0
        try:
            self._output = recorder._open(relative)
        except (OSError, ValueError) as exc:
            self._error(exc)

    def _error(self, exc: BaseException) -> None:
        self._errors.append(type(exc).__name__)
        self.recorder._error(self.relative, exc)

    def write(self, value: bytes) -> int:
        self._received += len(value)
        if self._closed:
            self._error(ValueError("diagnostic stream is closed"))
            return len(value)
        if self._output is not None and not self._errors:
            self._pending += value
            self._drain(final=False)
        return len(value)

    def _drain(self, *, final: bool) -> None:
        output, self._pending, count = _redact_stream(self._pending, self.recorder._secrets, final=final)
        self._redactions += count
        if count:
            self.recorder._redaction(count)
        if output and self._output is not None:
            try:
                remaining = memoryview(output)
                while remaining:
                    count = self._output.write(remaining)
                    if not count:
                        raise OSError("diagnostic write made no progress")
                    self._written += count
                    remaining = remaining[count:]
            except OSError as exc:
                self._error(exc)

    def close(self) -> None:
        if self._closed:
            return
        self._drain(final=True)
        self._closed = True
        if self._output is not None:
            try:
                os.fsync(self._output.fileno())
            except OSError as exc:
                self._error(exc)
            finally:
                try:
                    self._output.close()
                except OSError as exc:
                    self._error(exc)

    def summary(self) -> dict[str, Any]:
        return {"bytes_received": self._received, "bytes_written": self._written,
                "redacted": bool(self._redactions), "redaction_count": self._redactions,
                "capture_errors": list(self._errors), "complete": self._closed and not self._errors,
                "pending_bytes": len(self._pending)}

    def __enter__(self) -> _DiagnosticStream:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderDiagnosticWriter:
    """Host-owned bounded copy queue. No producer performs file I/O.

    Charges include the item currently in the worker. Control reservations keep
    body backpressure from preventing stream close/final metadata. Idle workers
    retire; a stuck worker is never replaced. This is not a durable work queue.
    """
    def __init__(self, *, max_bytes=32 * 1024 * 1024, control_bytes=4 * 1024 * 1024,
                 exchange_bytes=4 * 1024 * 1024, max_items=4096):
        self.max_bytes, self.control_bytes = max_bytes, control_bytes
        self.exchange_bytes, self.max_items = exchange_bytes, max_items
        self._condition = threading.Condition()
        self._queue = deque()
        self._bytes = self._body_bytes = 0
        self._thread = None
        self._closed = False

    def reserve(self, size, *, body=False):
        with self._condition:
            if (self._closed or self._bytes + size > self.max_bytes or
                    body and self._body_bytes + size > self.max_bytes - self.control_bytes):
                return False
            self._bytes += size
            if body: self._body_bytes += size
            return True

    def release(self, size, *, body=False):
        with self._condition:
            self._bytes -= size
            if body: self._body_bytes -= size

    def _submit(self, capture, kind, payload, cost=0):
        body = kind in {"request", "body"}
        with self._condition:
            previous = capture._body_job if kind == "body" else None
            combine = previous is not None and len(previous[2]) + len(payload) <= 256 * 1024
            # Finalization uses the reservation made when the capture opened.
            if kind != "final" and (self._closed or not combine and len(self._queue) >= self.max_items):
                return False
            if cost and not self.reserve(cost, body=body): return False
            if combine:
                item = previous
                item[2].extend(payload)
                item[3] += cost
            else:
                item = [capture, kind, bytearray(payload) if kind == "body" else payload, cost]
                self._queue.append(item)
                capture._body_job = item if kind == "body" else None
                if kind == "metadata": capture._metadata_job = item
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="hypoloom-provider-diagnostics", daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return True

    def _run(self):
        while True:
            with self._condition:
                if not self._queue:
                    self._condition.wait_for(lambda: self._queue or self._closed, timeout=0.1)
                if not self._queue:
                    self._thread = None
                    self._condition.notify_all()
                    return
                # Keep the latest metadata even when a stream goes quiet. A
                # delayed metadata write must not hold up other body/final jobs.
                while self._queue:
                    now = time.monotonic()
                    ready = next((item for item in self._queue
                        if self._closed or item[1] != "metadata"
                        or now >= item[0]._last_metadata + 1.0), None)
                    if ready is not None:
                        self._queue.remove(ready)
                        self._queue.appendleft(ready)
                        break
                    due = min(item[0]._last_metadata + 1.0 for item in self._queue)
                    self._condition.wait(timeout=max(0, due - now))
                if not self._queue:
                    continue
                deadline = time.monotonic() + 0.1
                while (len(self._queue) == 1 and self._queue[0][1] == "body"
                       and len(self._queue[0][2]) < 256 * 1024 and not self._closed):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: break
                    self._condition.wait(timeout=remaining)
                item = self._queue.popleft()
                if item[0]._body_job is item: item[0]._body_job = None
                if item[0]._metadata_job is item: item[0]._metadata_job = None
            capture, kind, payload, cost = item
            try:
                capture._write(kind, payload)
            except Exception as exc:
                capture._error(kind, type(exc).__name__)
            finally:
                with self._condition:
                    self.release(cost, body=kind in {"request", "body"})
                    if kind in {"request", "body"}: capture._pending_bytes -= cost
                    if kind == "metadata": capture._last_metadata = time.monotonic()
                    if kind == "final": self.release(capture._reservation)
                    self._condition.notify_all()

    def close(self, timeout=2.0):
        with self._condition:
            self._closed = True
            thread = self._thread
        if thread is not None: thread.join(timeout=timeout)
        # A kernel write cannot be cancelled. Report rather than hide residue.
        with self._condition:
            return (thread is None or not thread.is_alive()) and self._bytes == 0


class DeferredProviderCapture:
    """Private provider copy; all filesystem operations belong to the writer."""
    _reservation = 64 * 1024

    def __init__(self, writer, parent, exchange, secrets):
        self.backend, self.parent, self.exchange = writer, parent, exchange
        self.secrets = _secret_bytes((*getattr(parent, "_secrets", ()), *secrets))
        self.errors = []
        self._pending = b""
        self._pending_bytes = self._received = self._redactions = 0
        self._last_metadata = 0.0
        self._body_job = None
        self._metadata_job = None
        self._body_stopped = False
        self._response = False
        self._recorder = self._stream = None
        self._enabled = writer.reserve(self._reservation)
        if not self._enabled: self._error("capture", "queue_capacity")

    def _error(self, operation, code):
        item = {"operation": operation, "exception_type": code}
        if item not in self.errors: self.errors.append(item)

    def _body(self, kind, value):
        with self.backend._condition:
            cost = len(value) + 128
            if (self._pending_bytes + cost > self.backend.exchange_bytes or
                    not self.backend._submit(self, kind, value, cost)):
                self._error(kind, "queue_capacity")
                return False
            self._pending_bytes += cost
            return True

    def request(self, value):
        if not self._enabled: return
        if len(value) + 128 > self.backend.exchange_bytes:
            self._error("request", "queue_capacity")
            return
        self._copy("request", value, final=True)

    def response(self):
        self._response = True

    def write(self, value):
        self._received += len(value)
        if not self._enabled or self._body_stopped: return
        if not self._copy("body", value, final=False):
            self._body_stopped = True
            self._pending = b""  # Never flush an uncertain secret prefix after overflow.

    def _copy(self, kind, value, *, final):
        pending = self._pending if kind == "body" else b""
        shortest = min(map(len, self.secrets), default=len(REDACTED))
        expansion = max(1, (len(REDACTED) + shortest - 1) // shortest)
        # Reserve scratch space before redaction, including bytearray -> bytes
        # and unusually short secrets that expand. The original transport buffer
        # belongs to the relay; only its extra diagnostic copies are charged here.
        scratch = 2 * (len(pending) + len(value)) * (1 + expansion) + 128
        if not self.backend.reserve(scratch, body=True):
            self._error(kind, "queue_capacity")
            return False
        try:
            safe, tail, count = _redact_stream(pending + value, self.secrets, final=final)
            self._redactions += count
            if kind == "body": self._pending = tail
            return (not safe and kind != "request") or self._body(kind, safe)
        finally:
            self.backend.release(scratch, body=True)

    def save(self, value):
        if not self._enabled: return
        payload = json.dumps(sanitize_payload(value, self.secrets)).encode()
        if len(payload) > self._reservation // 4:
            self._error("metadata", "capture_size")
            return
        with self.backend._condition:
            if self._metadata_job is not None:
                cost = len(payload) * 4 + 512
                delta = cost - self._metadata_job[3]
                if delta > 0 and not self.backend.reserve(delta): return
                if delta < 0: self.backend.release(-delta)
                self._metadata_job[2:4] = [payload, cost]
                return
            self.backend._submit(self, "metadata", payload, len(payload) * 4 + 512)

    def finish(self, value):
        if not self._enabled: return
        if self._pending and not self._body_stopped:
            if not self._copy("body", b"", final=True):
                self._pending = b""
        payload = json.dumps(sanitize_payload(value, self.secrets)).encode()
        if len(payload) > self._reservation // 4:
            self._error("metadata", "capture_size")
            payload = json.dumps({key: value[key] for key in ("exchange", "started_at", "ended_at")}).encode()
        with self.backend._condition:
            # Final metadata supersedes the queued intermediate view, and must
            # follow all accepted body copies without waiting for its cadence.
            if self._metadata_job is not None:
                self.backend._queue.remove(self._metadata_job)
                self.backend.release(self._metadata_job[3])
                self._metadata_job = None
            if not self.backend._submit(self, "final", payload):
                self._error("capture", "writer_closed")
                self.backend.release(self._reservation)

    def _call(self, operation, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            self._error(operation, type(exc).__name__)
            # Error output also stays off the network path, with no private text.
            try:
                self.parent.event("provider_capture_error", exchange=self.exchange,
                                  operation=operation, exception_type=type(exc).__name__)
            except Exception:
                pass
            try:
                print(f"provider diagnostics {operation} failed: {type(exc).__name__}", file=sys.stderr)
            except (OSError, ValueError):
                pass

    def _write(self, kind, payload):
        if self._recorder is None:
            self._recorder = self._call("child", self.parent.child,
                f"provider/{self.exchange:06d}", secrets=self.secrets)
        recorder = self._recorder
        if recorder is None: return
        if kind == "request":
            self._call("request_body", recorder.write_bytes, "request.body", payload)
        elif kind == "body":
            if self._stream is None:
                self._stream = self._call("response_open", recorder.stream, "response.body")
            if self._stream is not None:
                self._call("response_body", self._stream.write, bytes(payload))
        else:
            value = json.loads(payload)
            if kind == "final":
                if self._response and self._stream is None:
                    self._stream = self._call("response_open", recorder.stream, "response.body")
                if self._stream is not None:
                    self._call("response_close", self._stream.close)
                    summary = self._call("response_summary", self._stream.summary) or {"complete": False}
                    summary.update(bytes_received=self._received, redacted=bool(self._redactions),
                                   redaction_count=self._redactions)
                    if self.errors:
                        summary.update(complete=False, capture_errors=list(self.errors))
                    value["response_capture"] = summary
                value["capture_errors"] = [*value.get("capture_errors", []), *self.errors]
                capture = self._call("summary", recorder.summary) or {}
                if self._redactions:
                    capture.update(redacted=True, redaction_count=capture.get("redaction_count", 0) + self._redactions)
                value["capture"] = capture
            self._call("metadata", recorder.write_json, "exchange.json", value)


def record_host_exception(state_root: Path, exc: BaseException, *,
                          secrets: Iterable[str | bytes] = (), **context: Any) -> None:
    recorder = RuntimeDiagnostics(Path(state_root) / "diagnostics", secrets=secrets)
    recorder._append("host.jsonl", {"timestamp": _now(), "kind": "host_exception", **context,
        "exception_type": type(exc).__name__, "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))})
