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


def sanitize_text(value: str, secrets: Iterable[str | bytes] = ()) -> str:
    for secret in _secret_bytes(secrets):
        text = secret.decode("utf-8", errors="ignore")
        if text:
            value = value.replace(text, REDACTED)
    value = _AUTH.sub(REDACTED, value)
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
    """Decode the existing typed text envelope; opaque content stays explicit."""
    media, encoded = value.get("media_type", ""), value.get("content_base64")
    if not isinstance(encoded, str) or not isinstance(media, str) or not (
        media.startswith("text/") or media.endswith("+json") or media in {
            "application/json", "application/x-python", "application/javascript"}):
        return None
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
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
        secrets = self.recorder._secrets
        keep = 0 if final else max((len(item) - 1 for item in secrets), default=0)
        output = bytearray()
        while self._pending:
            safe_end = max(0, len(self._pending) - keep)
            matches = [(self._pending.find(secret), secret) for secret in secrets]
            matches = [(index, secret) for index, secret in matches if index >= 0]
            match = min(matches, key=lambda item: (item[0], -len(item[1]))) if matches else None
            if match is not None and match[0] < safe_end:
                index, secret = match
                output.extend(self._pending[:index] + REDACTED.encode())
                self._pending = self._pending[index + len(secret):]
                self._redactions += 1
                self.recorder._redaction()
            else:
                output.extend(self._pending[:safe_end])
                self._pending = self._pending[safe_end:]
                break
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


def record_host_exception(state_root: Path, exc: BaseException, *,
                          secrets: Iterable[str | bytes] = (), **context: Any) -> None:
    recorder = RuntimeDiagnostics(Path(state_root) / "diagnostics", secrets=secrets)
    recorder._append("host.jsonl", {"timestamp": _now(), "kind": "host_exception", **context,
        "exception_type": type(exc).__name__, "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))})
