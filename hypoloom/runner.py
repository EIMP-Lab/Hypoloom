"""Bounded local process-group adapter.

The Runner owns OS processes and returns observations.  It deliberately has no
database path or connection; optional registration remains the caller's duty.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping

from .domain import AttemptTerminalReason


RUNNER_VERSION = "local-process-group/3"


class RunnerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessRequest:
    attempt_id: str
    attempt_ordinal: int
    execution_id: str
    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    timeout_seconds: float
    terminate_grace_seconds: float = 0.5
    kill_grace_seconds: float = 1.0
    stdout_path: str | None = None
    stderr_path: str | None = None

    def __post_init__(self) -> None:
        for name in ("attempt_id", "execution_id", "cwd"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be non-empty trimmed text")
        if isinstance(self.attempt_ordinal, bool) or not isinstance(self.attempt_ordinal, int):
            raise ValueError("attempt_ordinal must be an integer")
        if self.attempt_ordinal <= 0:
            raise ValueError("attempt_ordinal must be positive")
        if not isinstance(self.argv, tuple) or not self.argv:
            raise ValueError("argv must be a non-empty tuple")
        if not all(isinstance(item, str) and item and "\x00" not in item for item in self.argv):
            raise ValueError("argv entries must be non-empty strings without NUL")
        if not isinstance(self.env, Mapping):
            raise ValueError("env must be a mapping")
        for key, value in self.env.items():
            if not isinstance(key, str) or not key or "=" in key or "\x00" in key:
                raise ValueError("environment names must be non-empty and contain no '=' or NUL")
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("environment values must be strings without NUL")
        for name in ("timeout_seconds", "terminate_grace_seconds", "kill_grace_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if not 0 < float(value) < float("inf"):
                raise ValueError(f"{name} must be finite and positive")
        for name in ("stdout_path", "stderr_path"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be non-empty text or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_ordinal": self.attempt_ordinal,
            "execution_id": self.execution_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "env": dict(self.env),
            "timeout_seconds": self.timeout_seconds,
            "terminate_grace_seconds": self.terminate_grace_seconds,
            "kill_grace_seconds": self.kill_grace_seconds,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProcessRequest:
        expected = {
            "attempt_id",
            "attempt_ordinal",
            "execution_id",
            "argv",
            "cwd",
            "env",
            "timeout_seconds",
            "terminate_grace_seconds",
            "kill_grace_seconds",
            "stdout_path",
            "stderr_path",
        }
        if set(value) != expected:
            raise ValueError(f"ProcessRequest keys differ: {sorted(set(value) ^ expected)}")
        argv = value["argv"]
        if not isinstance(argv, (list, tuple)):
            raise ValueError("argv must be an array")
        env = value["env"]
        if not isinstance(env, Mapping):
            raise ValueError("env must be an object")
        return cls(**{**dict(value), "argv": tuple(argv), "env": dict(env)})


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessObservation:
    attempt_id: str
    attempt_ordinal: int
    execution_id: str
    terminal_reason: AttemptTerminalReason
    returncode: int | None
    pid: int
    process_group_id: int
    started_at: float
    ended_at: float
    stdout_path: str | None
    stderr_path: str | None

    def __post_init__(self) -> None:
        for name in ("attempt_id", "execution_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be non-empty trimmed text")
        if (
            isinstance(self.attempt_ordinal, bool)
            or not isinstance(self.attempt_ordinal, int)
            or self.attempt_ordinal <= 0
        ):
            raise ValueError("attempt_ordinal must be a positive integer")
        if not isinstance(self.terminal_reason, AttemptTerminalReason):
            raise ValueError("terminal_reason must be AttemptTerminalReason")
        if self.returncode is not None and (
            isinstance(self.returncode, bool) or not isinstance(self.returncode, int)
        ):
            raise ValueError("returncode must be an integer or None")
        if (
            self.terminal_reason is AttemptTerminalReason.SUCCEEDED
            and self.returncode != 0
        ):
            raise ValueError("SUCCEEDED Process observation requires returncode 0")
        for name in ("pid", "process_group_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("started_at", "ended_at"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        for name in ("stdout_path", "stderr_path"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value or "\x00" in value
            ):
                raise ValueError(f"{name} must be non-empty text without NUL or None")

    @property
    def reason(self) -> AttemptTerminalReason:
        return self.terminal_reason

    @property
    def exit_code(self) -> int | None:
        return self.returncode

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_ordinal": self.attempt_ordinal,
            "execution_id": self.execution_id,
            "terminal_reason": self.terminal_reason.value,
            "returncode": self.returncode,
            "pid": self.pid,
            "process_group_id": self.process_group_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProcessObservation:
        data = dict(value)
        data["terminal_reason"] = AttemptTerminalReason(data["terminal_reason"])
        return cls(**data)


@dataclass(slots=True)
class RunningProcess:
    request: ProcessRequest
    pid: int
    process_group_id: int
    started_at: float
    _process: subprocess.Popen[bytes] = field(repr=False)
    _guardian: subprocess.Popen[bytes] = field(repr=False)
    _guardian_writer: int = field(repr=False)
    _stdout_file: BinaryIO | None = field(repr=False)
    _stderr_file: BinaryIO | None = field(repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)
    _closed: bool = field(default=False, repr=False)

    @property
    def pgid(self) -> int:
        return self.process_group_id

    @property
    def returncode(self) -> int | None:
        return self._process.poll()


_BEFORE_EXEC = r"""
import os, sys
fd = int(sys.argv[1])
try:
    released = os.read(fd, 1) == b'\x01'
finally:
    os.close(fd)
if not released:
    raise SystemExit(125)
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
"""


_GUARDIAN = r"""
import os, signal, sys, time
fd, pgid, grace = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
try:
    while os.read(fd, 4096):
        pass
finally:
    try:
        os.close(fd)
    except OSError:
        pass
try:
    os.killpg(pgid, signal.SIGTERM)
except ProcessLookupError:
    raise SystemExit(0)
except PermissionError:
    raise SystemExit(1)
deadline = time.monotonic() + grace
while time.monotonic() < deadline:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        raise SystemExit(0)
    time.sleep(0.01)
try:
    os.killpg(pgid, signal.SIGKILL)
except ProcessLookupError:
    pass
"""


class LocalRunner:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._handles: dict[str, RunningProcess] = {}
        self._handles_lock = threading.RLock()

    def start(
        self,
        request: ProcessRequest,
        *,
        before_exec: Callable[[int], None] | None = None,
    ) -> RunningProcess:
        """Start a group, optionally registering it before workload execution.

        With registration, the isolated child waits on a pipe until the caller
        returns successfully. EOF aborts it even if the owner died before the
        guardian was installed. An exec failure after release is observed as a
        failed Process; callers without registration retain direct Popen errors.
        """
        cwd = Path(request.cwd)
        if cwd.is_symlink() or not cwd.is_dir():
            raise RunnerError(f"working directory does not exist: {cwd}")
        with self._handles_lock:
            previous = self._handles.get(request.attempt_id)
            if previous is not None and not previous._closed:
                raise RunnerError(f"attempt already running: {request.attempt_id}")

        stdout_file = _open_log(cwd, request.stdout_path)
        stderr_file = _open_log(cwd, request.stderr_path)
        guardian_reader, guardian_writer = os.pipe()
        process: subprocess.Popen[bytes] | None = None
        guardian: subprocess.Popen[bytes] | None = None
        release_reader: int | None = None
        release_writer: int | None = None
        try:
            argv = request.argv
            if before_exec is not None:
                release_reader, release_writer = os.pipe()
                argv = (
                    sys.executable, "-I", "-S", "-c", _BEFORE_EXEC,
                    str(release_reader), *request.argv,
                )
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=dict(request.env),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file if stdout_file is not None else subprocess.DEVNULL,
                stderr=stderr_file if stderr_file is not None else subprocess.DEVNULL,
                pass_fds=() if release_reader is None else (release_reader,),
                close_fds=True,
                start_new_session=True,
            )
            process_group_id = os.getpgid(process.pid)
            guardian = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    _GUARDIAN,
                    str(guardian_reader),
                    str(process_group_id),
                    str(request.terminate_grace_seconds),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(guardian_reader,),
                close_fds=True,
                start_new_session=True,
            )
            if before_exec is not None:
                before_exec(process_group_id)
                os.write(release_writer, b"\x01")
        except BaseException:
            os.close(guardian_writer)
            if process is not None:
                _signal_group(process.pid, signal.SIGKILL)
                _bounded_wait(process, request.kill_grace_seconds)
            if guardian is not None:
                try:
                    guardian.wait(timeout=request.kill_grace_seconds + request.terminate_grace_seconds)
                except subprocess.TimeoutExpired:
                    guardian.kill()
                    _bounded_wait(guardian, request.kill_grace_seconds)
            _close_log(stdout_file)
            _close_log(stderr_file)
            raise
        finally:
            os.close(guardian_reader)
            for fd in (release_reader, release_writer):
                if fd is not None:
                    os.close(fd)

        handle = RunningProcess(
            request=request,
            pid=process.pid,
            process_group_id=process_group_id,
            started_at=self._clock(),
            _process=process,
            _guardian=guardian,
            _guardian_writer=guardian_writer,
            _stdout_file=stdout_file,
            _stderr_file=stderr_file,
        )
        with self._handles_lock:
            self._handles[request.attempt_id] = handle
        return handle

    def run(
        self,
        request: ProcessRequest,
        cancel_event: threading.Event | None = None,
    ) -> ProcessObservation:
        handle = self.start(request)
        try:
            return self.wait(handle, cancel_event=cancel_event)
        except BaseException:
            self.cancel(handle)
            self._finalize(handle)
            raise

    def wait(
        self,
        handle: RunningProcess,
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProcessObservation:
        request = handle.request
        effective_timeout = request.timeout_seconds if timeout is None else min(timeout, request.timeout_seconds)
        if isinstance(effective_timeout, bool) or not isinstance(effective_timeout, (int, float)):
            raise ValueError("timeout must be numeric")
        if effective_timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = handle.started_at + float(effective_timeout)
        reason: AttemptTerminalReason | None = None
        try:
            while True:
                returncode = handle._process.poll()
                if returncode is not None:
                    reason = (
                        AttemptTerminalReason.CANCELLED
                        if handle._cancel_requested
                        else AttemptTerminalReason.SUCCEEDED
                        if returncode == 0
                        else AttemptTerminalReason.FAILED
                    )
                    break
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel(handle)
                    reason = AttemptTerminalReason.CANCELLED
                    break
                if self._clock() >= deadline:
                    if handle._process.poll() is None:
                        self._terminate_group(handle)
                        reason = AttemptTerminalReason.TIMED_OUT
                        break
                    continue
                self._sleep(min(0.02, max(0.001, deadline - self._clock())))
            _bounded_wait(handle._process, request.kill_grace_seconds)
            if reason is None:
                raise RunnerError("process ended without a terminal reason")
            return ProcessObservation(
                attempt_id=request.attempt_id,
                attempt_ordinal=request.attempt_ordinal,
                execution_id=request.execution_id,
                terminal_reason=reason,
                returncode=handle._process.poll(),
                pid=handle.pid,
                process_group_id=handle.process_group_id,
                started_at=handle.started_at,
                ended_at=self._clock(),
                stdout_path=request.stdout_path,
                stderr_path=request.stderr_path,
            )
        finally:
            self._finalize(handle)

    def cancel(
        self,
        handle_or_attempt: RunningProcess | str,
        *,
        terminate_grace_seconds: float | None = None,
    ) -> bool:
        if terminate_grace_seconds is not None:
            if isinstance(terminate_grace_seconds, bool) or not isinstance(
                terminate_grace_seconds, (int, float)
            ):
                raise ValueError("terminate_grace_seconds must be numeric")
            if not 0 < float(terminate_grace_seconds) < float("inf"):
                raise ValueError("terminate_grace_seconds must be finite and positive")
        handle = self._resolve(handle_or_attempt)
        if handle is None:
            return False
        with handle._lock:
            if handle._process.poll() is not None:
                return False
            handle._cancel_requested = True
        self._terminate_group(handle, terminate_grace_seconds=terminate_grace_seconds)
        return True

    def cleanup(self, handle_or_attempt: RunningProcess | str) -> None:
        handle = self._resolve(handle_or_attempt)
        if handle is None:
            return
        if handle._process.poll() is None:
            self.cancel(handle)
        self._finalize(handle)

    def _terminate_group(
        self,
        handle: RunningProcess,
        *,
        terminate_grace_seconds: float | None = None,
    ) -> None:
        grace = (
            handle.request.terminate_grace_seconds
            if terminate_grace_seconds is None
            else float(terminate_grace_seconds)
        )
        with handle._lock:
            if handle._process.poll() is not None and not process_group_exists(handle.process_group_id):
                return
            _signal_group(handle.process_group_id, signal.SIGTERM)
            deadline = self._clock() + grace
            while self._clock() < deadline and process_group_exists(handle.process_group_id):
                handle._process.poll()
                self._sleep(0.01)
            if process_group_exists(handle.process_group_id):
                _signal_group(handle.process_group_id, signal.SIGKILL)
            _bounded_wait(handle._process, handle.request.kill_grace_seconds)

    def _finalize(self, handle: RunningProcess) -> None:
        with handle._lock:
            if handle._closed:
                return
            if handle._process.poll() is None:
                self._terminate_group(handle)
            try:
                os.close(handle._guardian_writer)
            except OSError:
                pass
            try:
                handle._guardian.wait(timeout=handle.request.kill_grace_seconds + handle.request.terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                handle._guardian.kill()
                handle._guardian.wait(timeout=handle.request.kill_grace_seconds)
            _close_log(handle._stdout_file)
            _close_log(handle._stderr_file)
            handle._closed = True
        with self._handles_lock:
            if self._handles.get(handle.request.attempt_id) is handle:
                del self._handles[handle.request.attempt_id]

    def _resolve(self, handle_or_attempt: RunningProcess | str) -> RunningProcess | None:
        if isinstance(handle_or_attempt, RunningProcess):
            return handle_or_attempt
        with self._handles_lock:
            return self._handles.get(handle_or_attempt)


def process_group_exists(process_group_id: int) -> bool:
    if isinstance(process_group_id, bool) or not isinstance(process_group_id, int) or process_group_id <= 0:
        return False
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _open_log(cwd: Path, path_value: str | None) -> BinaryIO | None:
    if path_value is None:
        return None
    path = Path(path_value)
    if path.is_absolute() or "\\" in path_value or any(part in {"", ".", ".."} for part in path.parts):
        raise RunnerError(f"log path must be a safe relative path: {path_value!r}")
    parent = cwd
    for component in path.parts[:-1]:
        parent = parent / component
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if parent.is_symlink() or not parent.is_dir():
            raise RunnerError(f"log parent is not a real directory: {parent}")
    destination = cwd / path
    if destination.is_symlink():
        raise RunnerError(f"log destination may not be a symlink: {destination}")
    return destination.open("xb")


def _close_log(stream: BinaryIO | None) -> None:
    if stream is not None:
        stream.close()


def _signal_group(process_group_id: int, sig: signal.Signals) -> None:
    try:
        os.killpg(process_group_id, sig)
    except ProcessLookupError:
        pass


def _bounded_wait(process: subprocess.Popen[bytes], timeout: float) -> None:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"process {process.pid} did not terminate after SIGKILL") from exc


__all__ = [
    "LocalRunner",
    "ProcessObservation",
    "ProcessRequest",
    "RUNNER_VERSION",
    "RunnerError",
    "RunningProcess",
    "process_group_exists",
]
