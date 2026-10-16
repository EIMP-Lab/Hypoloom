"""Disposable, bounded standard-library network operations.

The Host remains the owner of budgets, retries and credentials. A worker owns
one blocking network operation and no descendants or durable state. This uses
the bounded pipe/process-group pattern from git_authoring_workspace and the
parent-pipe EOF lifetime rule from runner; a Future timeout alone cannot stop
getaddrinfo or a peer that keeps dripping HTTP headers.
"""

from __future__ import annotations

import importlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Mapping


class NetworkOperationError(OSError):
    def __init__(self, code: str, *, http_status: int | None = None,
                 transport_phase: str | None = None, received_bytes: int = 0,
                 retry_after_seconds: float | None = None) -> None:
        super().__init__(f"network operation {code}")
        self.code = code
        self.http_status = http_status
        self.transport_phase = transport_phase
        self.received_bytes = received_bytes
        self.retry_after_seconds = retry_after_seconds


_PROGRESS_PHASES = {"dns", "tcp_connect", "tls_handshake", "request_send",
                    "response_headers", "response_body", "response_decode"}
_WORKER_PROGRESS = False


def report_network_progress(phase: str, *, received_bytes: int = 0,
                            http_status: int | None = None) -> None:
    """Send only safe phase/byte counters, never URLs, headers or exception text."""
    if _WORKER_PROGRESS:
        _emit({"progress": {"transport_phase": phase, "received_bytes": received_bytes,
                            "http_status": http_status}})


def _error_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    phase = value.get("transport_phase")
    status = value.get("http_status")
    size = value.get("received_bytes", 0)
    delay = value.get("retry_after_seconds")
    return {"transport_phase": phase if phase in _PROGRESS_PHASES else None,
            "http_status": status if type(status) is int and 100 <= status <= 599 else None,
            "received_bytes": size if type(size) is int and size >= 0 else 0,
            "retry_after_seconds": float(delay) if type(delay) in (int, float)
                and math.isfinite(delay) and delay >= 0 else None}


def _reap(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # This is an infrastructure failure, not a successfully bounded request.
        raise NetworkOperationError("cleanup_failed") from None


def run_network_operation(operation: str, payload: Mapping[str, Any], *,
                          deadline: float, max_output_bytes: int,
                          cancel_event: threading.Event | None = None) -> dict[str, Any]:
    """Run a fixed internal module:function call and reap before returning.

    ``deadline`` is the caller's absolute monotonic deadline, including process
    startup and pipe I/O. Output is a bounded JSON object; binary bodies use
    base64 in the operation's ordinary response. A separate <=1 s kill/wait is
    reserved for cleanup after expiry, with cleanup failure reported explicitly.
    Authentication stays in private stdin; the child receives no ambient env.
    """
    if not isinstance(operation, str) or operation.count(":") != 1:
        raise ValueError("network operation must name a fixed module:function")
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ValueError("network output bound must be positive")
    if not math.isfinite(deadline):
        raise ValueError("network deadline must be finite")
    if cancel_event is not None and cancel_event.is_set():
        raise NetworkOperationError("cancelled")
    if time.monotonic() >= deadline:
        raise NetworkOperationError("timed_out")
    request = json.dumps({"operation": operation, "payload": dict(payload), "deadline": deadline},
                         ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(request) > 128 * 1024:
        raise ValueError("network operation input exceeds its byte bound")
    try:
        process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={}, start_new_session=True, close_fds=True)
    except OSError:
        raise NetworkOperationError("worker_failed") from None
    selector = selectors.DefaultSelector()
    latest = _error_fields({})
    output = bytearray()
    pending = memoryview(request)
    result: dict[str, Any] | None = None
    failure: NetworkOperationError | None = None
    try:
        assert process.stdin is not None and process.stdout is not None
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "input")
        selector.register(process.stdout, selectors.EVENT_READ, "output")
        while selector.get_map():
            if cancel_event is not None and cancel_event.is_set():
                raise NetworkOperationError("cancelled", **latest)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkOperationError("timed_out", **latest)
            for key, _ in selector.select(min(remaining, 0.05)):
                if key.data == "input":
                    try:
                        count = os.write(key.fileobj.fileno(), pending[:65536])
                    except BrokenPipeError:
                        raise NetworkOperationError("worker_failed", **latest) from None
                    pending = pending[count:]
                    if not pending:
                        selector.unregister(key.fileobj)
                        # Keep the private pipe open: EOF is the parent-death signal.
                    continue
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > max_output_bytes:
                    raise NetworkOperationError("response_limit", **latest)
                while b"\n" in output:
                    line, _, rest = output.partition(b"\n")
                    output = bytearray(rest)
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise NetworkOperationError("worker_failed", **latest) from None
                    if not isinstance(event, dict):
                        raise NetworkOperationError("worker_failed", **latest)
                    if "progress" in event:
                        if not isinstance(event["progress"], dict):
                            raise NetworkOperationError("worker_failed", **latest)
                        latest = _error_fields(event["progress"])
                    elif "result" in event and isinstance(event["result"], dict):
                        result = event["result"]
                    elif "error" in event and isinstance(event["error"], dict):
                        code = event["error"].get("code", "transport_failed")
                        if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 80:
                            code = "transport_failed"
                        details = _error_fields(event["error"])
                        details["transport_phase"] = details["transport_phase"] or latest["transport_phase"]
                        details["received_bytes"] = max(details["received_bytes"], latest["received_bytes"])
                        failure = NetworkOperationError(code, **details)
                    else:
                        raise NetworkOperationError("worker_failed", **latest)
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                raise NetworkOperationError("cancelled", **latest)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkOperationError("timed_out", **latest)
            try:
                process.wait(timeout=min(remaining, 0.05))
            except subprocess.TimeoutExpired:
                pass
        if time.monotonic() >= deadline:
            raise NetworkOperationError("timed_out", **latest)
        if failure is not None:
            raise failure
        if process.returncode != 0 or result is None or output:
            raise NetworkOperationError("worker_failed", **latest)
        return result
    finally:
        try:
            _reap(process)
        finally:
            selector.close()
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    pipe.close()


def _emit(value: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n")
    sys.stdout.flush()


def _parent_lifetime() -> None:
    # Only this thread reads after the single framed request. The parent retains
    # its write end until this worker exits; crash/forced Host termination is EOF.
    try:
        while os.read(sys.stdin.fileno(), 4096):
            pass
    finally:
        os._exit(125)


def _worker_main() -> None:
    global _WORKER_PROGRESS
    request = json.loads(sys.stdin.buffer.readline(128 * 1024 + 1))
    threading.Thread(target=_parent_lifetime, daemon=True, name="network-parent-lifetime").start()
    # -I excludes cwd and ambient PYTHONPATH; use this installed/source package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    # Imports through hypoloom.network_io must share progress state with __main__.
    sys.modules["hypoloom.network_io"] = sys.modules[__name__]
    _WORKER_PROGRESS = True
    try:
        if time.monotonic() >= request["deadline"]:
            raise NetworkOperationError("timed_out")
        module, name = request["operation"].split(":", 1)
        value = getattr(importlib.import_module(module), name)(request["payload"])
        if not isinstance(value, dict):
            raise NetworkOperationError("worker_failed")
        _emit({"result": value})
    except Exception as error:
        code = getattr(error, "code", "timed_out" if isinstance(error, TimeoutError) else "transport_failed")
        code = getattr(code, "value", code)
        _emit({"error": {"code": code, **_error_fields(vars(error))}})


if __name__ == "__main__":
    _worker_main()
