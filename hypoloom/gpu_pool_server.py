"""OS-authenticated socket service for GpuPool; all resource rules stay in core."""

from __future__ import annotations

import fcntl
import os
import signal
import socket
import stat
import threading
from pathlib import Path
from typing import Any

from .gpu_pool import GpuPool, PoolError
from .gpu_pool_client import _decode, _encode, _peer_uid, _readline


class PoolServer:
    def __init__(self, pool: GpuPool, socket_path: str | Path, socket_mode: int = 0o600,
                 socket_gid: int | None = None) -> None:
        if type(socket_mode) is not int or not 0 <= socket_mode <= 0o777:
            raise ValueError("invalid pool socket mode")
        if socket_gid is not None and (type(socket_gid) is not int or socket_gid < 0):
            raise ValueError("invalid pool socket group")
        self.pool = pool
        self.socket_path = Path(socket_path).absolute()
        self.request_timeout_seconds = 5.0
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
        self._lock_fd: int | None = None
        self._listener: socket.socket | None = None
        self._connection: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        try:
            if not self.socket_path.parent.is_dir():
                raise PoolError("unavailable", "pool socket parent must be an existing configured directory")
            self._lock_fd = os.open(Path(pool.root) / "pool-service.lock",
                                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            lock_stat = os.fstat(self._lock_fd)
            if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.geteuid():
                raise PoolError("unavailable", "pool service lock must be an owned regular file")
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PoolError("service_busy", "another service owns this GPU pool directory") from error
            self._clear_stale_socket()
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(str(self.socket_path))
            bound = self.socket_path.lstat()
            self._socket_identity = (bound.st_dev, bound.st_ino)
            if socket_gid is not None:
                os.chown(self.socket_path, -1, socket_gid)
            os.chmod(self.socket_path, socket_mode)
            self._listener.listen(64)
            self._listener.settimeout(0.1)
        except BaseException as error:
            self._finish()
            if isinstance(error, OSError):
                raise PoolError("unavailable", "cannot establish the configured GPU pool endpoint") from error
            raise

    def _clear_stale_socket(self) -> None:
        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
            raise PoolError("unavailable", "pool socket path is not an owned socket")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            try:
                probe.connect(str(self.socket_path))
            except ConnectionRefusedError:
                pass
            else:
                raise PoolError("service_busy", "configured GPU pool socket is already serving")
        current = self.socket_path.lstat()
        if (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino):
            raise PoolError("unavailable", "pool socket changed during startup")
        self.socket_path.unlink()

    def _reply(self, connection: socket.socket) -> None:
        try:
            uid = _peer_uid(connection)
            try:
                request = _decode(_readline(connection, self.request_timeout_seconds))
                if (not isinstance(request, dict) or set(request) != {"operation", "arguments"}
                        or not isinstance(request["operation"], str) or not isinstance(request["arguments"], dict)):
                    raise ValueError("invalid request envelope")
            except (ValueError, TimeoutError, RecursionError) as error:
                raise PoolError("invalid_request", "invalid, incomplete or oversized GPU pool request") from error
            result = self.pool.handle(uid, request["operation"], request["arguments"])
            if not isinstance(result, dict):
                raise TypeError("pool core returned a non-object result")
            response = _encode({"ok": True, "result": result})
        except PoolError as error:
            response = _encode({"ok": False, "error": {"code": error.code, "message": str(error)}})
        except Exception:
            response = _encode({"ok": False, "error": {"code": "internal_error", "message": "GPU pool request failed"}})
        try:
            connection.settimeout(self.request_timeout_seconds)
            connection.sendall(response)
        except OSError:
            pass  # Disconnect never changes a persisted commitment.

    def serve_forever(self) -> None:
        with self._state_lock:
            if self.thread is not None and self.thread is not threading.current_thread():
                raise RuntimeError("GPU pool service is already running")
            self.thread = threading.current_thread()
        try:
            while not self._stop.is_set():
                try:
                    assert self._listener is not None
                    connection, _ = self._listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                with connection:
                    with self._state_lock:
                        self._connection = connection
                    try:
                        if not self._stop.is_set():
                            self._reply(connection)
                    finally:
                        with self._state_lock:
                            self._connection = None
        finally:
            self._stop.set()
            self._finish()

    def start(self) -> PoolServer:
        with self._state_lock:
            if self._stop.is_set() or self.thread is not None:
                raise RuntimeError("GPU pool service cannot be started again")
            self.thread = threading.Thread(target=self.serve_forever, name="gpu-pool", daemon=True)
            self.thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        with self._state_lock:
            for connection in (self._listener, self._connection):
                if connection is not None:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            serving = self.thread
        if serving is threading.current_thread():
            return  # serve_forever's finally retains the lock through in-flight effects.
        if serving is not None:
            serving.join()
        self._finish()

    def _finish(self) -> None:
        with self._state_lock:
            if self._listener is not None:
                self._listener.close()
                self._listener = None
            if self._socket_identity is not None:
                try:
                    current = self.socket_path.lstat()
                    if (stat.S_ISSOCK(current.st_mode) and current.st_uid == os.geteuid()
                            and (current.st_dev, current.st_ino) == self._socket_identity):
                        self.socket_path.unlink()
                except FileNotFoundError:
                    pass
                self._socket_identity = None
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None

    def __enter__(self) -> PoolServer:
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.close()


def serve_pool(pool: GpuPool, socket_path: str | Path, socket_gid: int | None = None) -> None:
    server = PoolServer(pool, socket_path, socket_mode=0o660 if socket_gid is not None else 0o600,
                        socket_gid=socket_gid)
    previous: dict[int, Any] = {}
    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, lambda _signum, _frame: server.close())
        server.serve_forever()
    finally:
        server.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
