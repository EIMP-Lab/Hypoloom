"""Bounded socket I/O; the resident thread still owns every Host operation."""
from __future__ import annotations

import json
import queue
import selectors
import socket
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field, replace

from .control_protocol import (
    MAX_FRAME_BYTES, ControlRequest, ControlResponse, ProtocolError,
    _HEADER, _decode_message, _encode_message,
)

STATUS_REFRESH_SECONDS = 1.0
STATUS_MAX_AGE_SECONDS = 5.0


class StatusSnapshots:
    """Serialized copies only: no writer, live Host objects or second reducer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._values: dict[str, tuple[str, float, float]] = {}
        self._last_refresh = float("-inf")

    def refresh(self, host, *, force=False) -> None:
        """Called only by the owner thread; never retain the Host reference."""
        if not force and time.monotonic() - self._last_refresh < STATUS_REFRESH_SECONDS:
            return
        with host.store.cache_reads():
            for row in host.store.inspect("SELECT run_id FROM workflow_runs ORDER BY run_id"):
                run_id = row["run_id"]
                started_at = (time.time(), time.monotonic())
                response = host.handle(ControlRequest(1, "host-status-snapshot", None, "STATUS", {"run_id": run_id}))
                self.publish(run_id, response, started_at=started_at)
        self._last_refresh = time.monotonic()

    def publish(self, run_id: str, response: ControlResponse, *, started_at: tuple[float, float]) -> None:
        value = (json.dumps(response.to_mapping(), ensure_ascii=False, allow_nan=False), *started_at)
        with self._lock:
            self._values[run_id] = value

    def respond(self, request: ControlRequest) -> ControlResponse:
        if set(request.payload) != {"run_id"}:
            return ControlResponse.failure(request.request_id, "rejected", "invalid_request", "STATUS requires only run_id")
        run_id = str(request.payload["run_id"])
        with self._lock:
            value = self._values.get(run_id)
        if value is None:
            return ControlResponse.failure(request.request_id, "rejected", "not_found", run_id)
        encoded, observed_at, monotonic_at = value
        response = ControlResponse.from_mapping(json.loads(encoded))
        if response.result is not None:
            age = max(0.0, time.monotonic() - monotonic_at)
            response.result["status_snapshot"] = {
                "source": "resident-host", "observed_at": observed_at,
                "age_seconds": age, "stale": age > STATUS_MAX_AGE_SECONDS,
            }
        return replace(response, request_id=request.request_id)


@dataclass(eq=False)
class _Client:
    peer: socket.socket
    deadline: float
    incoming: bytearray = field(default_factory=bytearray)
    outgoing: memoryview | None = None
    response: Future | None = None


class ControlServer:
    """One nonblocking I/O thread and a bounded queue to the sole writer.

    Slow readers and partial frames cannot hold up other clients. A fully
    queued command retains ordinary replay semantics even if its peer leaves.
    """

    MAX_CLIENTS = 64
    MAX_COMMANDS = 16
    IO_TIMEOUT = 1.0

    def __init__(self, listener: socket.socket, snapshots: StatusSnapshots):
        self.listener = listener
        self.snapshots = snapshots
        self.requests: queue.Queue[tuple[ControlRequest, Future]] = queue.Queue(self.MAX_COMMANDS)
        self._stop = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="host-control", daemon=True)

    def start(self) -> None:
        self.listener.setblocking(False)
        self._thread.start()

    def next_request(self, timeout: float) -> tuple[ControlRequest, Future] | None:
        if self._error is not None:
            raise RuntimeError("Host control transport failed") from self._error
        try:
            return self.requests.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                raise RuntimeError("Host control transport did not stop")

    def _run(self) -> None:
        clients: set[_Client] = set()
        with selectors.DefaultSelector() as selector:
            def close(client):
                try:
                    selector.unregister(client.peer)
                except KeyError:
                    pass
                client.peer.close()
                clients.discard(client)

            def respond(client, response):
                try:
                    client.outgoing = memoryview(_encode_message(response.to_mapping()))
                except ProtocolError as exc:
                    client.outgoing = memoryview(_encode_message(ControlResponse.failure(
                        response.request_id, "error", "protocol_error", str(exc)).to_mapping()))
                client.incoming.clear()
                client.deadline = time.monotonic() + self.IO_TIMEOUT
                try:
                    selector.modify(client.peer, selectors.EVENT_WRITE, client)
                except KeyError:
                    selector.register(client.peer, selectors.EVENT_WRITE, client)

            try:
                selector.register(self.listener, selectors.EVENT_READ)
                shutdown_deadline = None
                while True:
                    if self._stop.is_set() and shutdown_deadline is None:
                        shutdown_deadline = time.monotonic() + self.IO_TIMEOUT
                        selector.unregister(self.listener)
                    for client in tuple(clients):
                        if client.response is not None:
                            if client.response.done():
                                respond(client, client.response.result())
                                client.response = None
                            elif shutdown_deadline is not None:
                                close(client)
                        elif (time.monotonic() >= client.deadline
                              or shutdown_deadline is not None and client.outgoing is None):
                            close(client)
                    # Deliver completed commands before closing; a peer that
                    # does not read still cannot prolong shutdown indefinitely.
                    if shutdown_deadline is not None and (not clients or time.monotonic() >= shutdown_deadline):
                        break
                    for key, mask in selector.select(timeout=0.02):
                        if key.fileobj is self.listener:
                            for _ in range(16):
                                try:
                                    peer, _ = self.listener.accept()
                                except BlockingIOError:
                                    break
                                if len(clients) >= self.MAX_CLIENTS:
                                    peer.close()
                                    continue
                                peer.setblocking(False)
                                client = _Client(peer, time.monotonic() + self.IO_TIMEOUT)
                                clients.add(client)
                                selector.register(peer, selectors.EVENT_READ, client)
                            continue
                        client = key.data
                        try:
                            if mask & selectors.EVENT_WRITE:
                                sent = client.peer.send(client.outgoing)
                                client.outgoing = client.outgoing[sent:]
                                if not client.outgoing:
                                    close(client)
                                continue
                            chunk = client.peer.recv(65536)
                            if not chunk:
                                close(client)
                                continue
                            client.incoming.extend(chunk)
                            if len(client.incoming) < _HEADER.size:
                                continue
                            length = _HEADER.unpack_from(client.incoming)[0]
                            if not 0 < length <= MAX_FRAME_BYTES:
                                raise ProtocolError(f"invalid frame length: {length}")
                            if len(client.incoming) < _HEADER.size + length:
                                continue
                            request = ControlRequest.from_mapping(_decode_message(
                                client.incoming[_HEADER.size:_HEADER.size + length]))
                            if request.command == "STATUS":
                                respond(client, self.snapshots.respond(request))
                                continue
                            future: Future = Future()
                            try:
                                self.requests.put_nowait((request, future))
                            except queue.Full:
                                respond(client, ControlResponse.failure(request.request_id,
                                    "rejected", "host_busy", "Host control queue is full; retry with the same request identity"))
                            else:
                                client.incoming.clear()
                                client.response = future
                                selector.unregister(client.peer)
                        except BlockingIOError:
                            continue
                        except (ProtocolError, ValueError, TypeError) as exc:
                            respond(client, ControlResponse.failure(
                                "invalid-request", "rejected", "protocol_error", str(exc)))
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            close(client)
            except BaseException as exc:
                self._error = exc
            finally:
                for client in tuple(clients):
                    close(client)
