"""Bounded Unix transport for the node-local GPU pool; no fallback allocator."""

from __future__ import annotations

import json
import math
import socket
import struct
import time
from pathlib import Path
from typing import Any

from .gpu_pool import PoolError

MAX_MESSAGE_BYTES = 128 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _reject_constant(raw: str) -> None:
    raise ValueError("non-finite JSON number")


def _decode(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_float=_finite_float, parse_constant=_reject_constant)


def _encode(value: Any) -> bytes:
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("pool message exceeds byte limit")
    return raw


def _readline(connection: socket.socket, timeout_seconds: float) -> bytes:
    deadline = time.monotonic() + timeout_seconds
    raw = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("pool message timed out")
        connection.settimeout(remaining)
        part = connection.recv(min(4096, MAX_MESSAGE_BYTES + 1 - len(raw)))
        if not part:
            raise ValueError("incomplete pool message")
        raw.extend(part)
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("pool message exceeds byte limit")
        if b"\n" in part:
            line, trailing = bytes(raw).split(b"\n", 1)
            if trailing:
                raise ValueError("one pool message per connection required")
            return line


def _peer_uid(connection: socket.socket) -> int:
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", credentials)[1]


def validate_pool_snapshot(snapshot: dict[str, Any], *, aci_version: int,
                           allowed_gpu_uuids=()) -> tuple[str, ...]:
    """Share running-service compatibility and membership checks across callers.

    Free memory is not an authorization or readiness condition. Detailed
    capacity validation remains with the existing capacity calculator.
    """
    from .compute_resources import validate_gpu_uuid
    devices = snapshot.get("devices")
    if not isinstance(devices, list):
        raise PoolError("invalid_response", "GPU pool snapshot lacks devices")
    uuids = []
    for device in devices:
        try:
            if not isinstance(device, dict): raise ValueError("invalid device")
            uuid = device.get("gpu_uuid")
            validate_gpu_uuid(uuid)
        except (ValueError, TypeError):
            raise PoolError("invalid_response", "GPU pool snapshot has an invalid device identity") from None
        uuids.append(uuid)
    if len(set(uuids)) != len(uuids):
        raise PoolError("invalid_response", "GPU pool snapshot repeats a device")
    if aci_version >= 27:
        capabilities = snapshot.get("capabilities")
        if not isinstance(capabilities, list) or "resource_replanning/1" not in capabilities:
            raise PoolError("incompatible_pool", "GPU pool lacks resource_replanning/1; update the pool before starting GPU research")
    if not set(allowed_gpu_uuids).issubset(uuids):
        raise PoolError("unauthorized", "Selected GPUs are outside this pool membership; review the GPU selection or pool configuration")
    return tuple(sorted(uuids))


class PoolClient:
    def __init__(self, socket_path: str | Path, timeout_seconds: float = 5,
                 expected_server_uid: int | None = None) -> None:
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("pool timeout must be finite and positive")
        if expected_server_uid is not None and (type(expected_server_uid) is not int or expected_server_uid < 0):
            raise ValueError("expected pool service UID must be a nonnegative integer")
        self.socket_path = Path(socket_path)
        self.timeout_seconds = float(timeout_seconds)
        self.expected_server_uid = expected_server_uid

    def request(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(operation, str) or not isinstance(arguments, dict):
            raise PoolError("invalid_request", "pool request requires an operation and arguments object")
        try:
            request = _encode({"operation": operation, "arguments": arguments})
        except (TypeError, ValueError, RecursionError) as error:
            raise PoolError("invalid_request", "invalid or oversized pool request") from error
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                if self.expected_server_uid is not None and _peer_uid(connection) != self.expected_server_uid:
                    raise PoolError("unauthorized_server", "pool service UID does not match configured owner")
                connection.sendall(request)
                raw = _readline(connection, self.timeout_seconds)
        except OSError as error:
            raise PoolError("unavailable", "GPU pool is unavailable; no reservation was inferred") from error
        except ValueError as error:
            if isinstance(error, PoolError):
                raise
            raise PoolError("invalid_response", "GPU pool returned an incomplete or oversized response") from error
        try:
            response = _decode(raw)
            if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                raise ValueError("invalid response envelope")
            if response["ok"]:
                if set(response) != {"ok", "result"} or not isinstance(response["result"], dict):
                    raise ValueError("invalid success envelope")
                return response["result"]
            if set(response) != {"ok", "error"} or not isinstance(response["error"], dict):
                raise ValueError("invalid error envelope")
            failure = response["error"]
            if set(failure) != {"code", "message"} or any(not isinstance(failure[key], str) or not failure[key] for key in failure):
                raise ValueError("invalid error details")
        except (ValueError, RecursionError) as error:
            raise PoolError("invalid_response", "GPU pool returned invalid JSON or response fields") from error
        raise PoolError(failure["code"], failure["message"])
