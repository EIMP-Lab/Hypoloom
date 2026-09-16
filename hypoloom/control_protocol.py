"""Versioned, bounded Unix-socket control protocol for the Hypoloom Host."""
from __future__ import annotations
import json
import os
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
SCHEMA_VERSION = 1
MAX_FRAME_BYTES = 1 << 20
MAX_UNIX_PATH_BYTES = 107
_HEADER = struct.Struct("!I")
_REQUEST_FIELDS = ("schema_version", "request_id", "expected_revision", "command", "payload")
_RESPONSE_FIELDS = ("schema_version", "request_id", "status", "revision", "result", "error")
_RESPONSE_STATUSES = {"accepted", "rejected", "conflict", "error"}
class ProtocolError(ValueError):
    """The peer supplied an invalid or unsupported protocol message."""
def socket_address(socket_path: Path) -> tuple[str, int | None]:
    """Return a Linux-safe address and an optional directory fd to close."""
    rendered = str(socket_path)
    if len(os.fsencode(rendered)) <= MAX_UNIX_PATH_BYTES:
        return rendered, None
    directory_fd = os.open(socket_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    address = f"/proc/self/fd/{directory_fd}/{socket_path.name}"
    if len(os.fsencode(address)) > MAX_UNIX_PATH_BYTES:
        os.close(directory_fd)
        raise ProtocolError("control socket name exceeds the Linux AF_UNIX limit")
    return address, directory_fd
def canonical_json(value: Mapping[str, Any]) -> str:
    """Return the one normalized representation used for command comparison."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
def _require_exact_fields(value: Mapping[str, Any], expected: tuple[str, ...]) -> None:
    actual = set(value)
    expected_set = set(expected)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        raise ProtocolError(f"invalid fields: missing={missing}, extra={extra}")
def _require_int(value: object, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{field} must be an integer")
    if value < 0:
        raise ProtocolError(f"{field} must be non-negative")
    return value
def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ProtocolError(f"{field} must be non-empty text")
    return value
@dataclass(frozen=True, slots=True)
class ControlRequest:
    schema_version: int
    request_id: str
    expected_revision: int | None
    command: str
    payload: dict[str, Any]
    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ControlRequest":
        _require_exact_fields(value, _REQUEST_FIELDS)
        version = _require_int(value["schema_version"], "schema_version")
        if version != SCHEMA_VERSION:
            raise ProtocolError(f"unsupported schema_version: {version}")
        request_id = _require_text(value["request_id"], "request_id")
        expected_revision = _require_int(value["expected_revision"], "expected_revision", nullable=True)
        command = _require_text(value["command"], "command").upper()
        payload = value["payload"]
        if not isinstance(payload, dict):
            raise ProtocolError("payload must be an object")
        canonical_json(payload)
        return cls(version, request_id, expected_revision, command, dict(payload))
    def to_mapping(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in _REQUEST_FIELDS}
    def normalized(self) -> str:
        return canonical_json(self.to_mapping())
@dataclass(frozen=True, slots=True)
class ControlResponse:
    schema_version: int
    request_id: str
    status: str
    revision: int | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ControlResponse":
        _require_exact_fields(value, _RESPONSE_FIELDS)
        version = _require_int(value["schema_version"], "schema_version")
        if version != SCHEMA_VERSION:
            raise ProtocolError(f"unsupported schema_version: {version}")
        request_id = _require_text(value["request_id"], "request_id")
        status = _require_text(value["status"], "status")
        if status not in _RESPONSE_STATUSES:
            raise ProtocolError(f"unknown response status: {status}")
        revision = _require_int(value["revision"], "revision", nullable=True)
        result = value["result"]
        error = value["error"]
        if result is not None and not isinstance(result, dict):
            raise ProtocolError("result must be an object or null")
        if error is not None and not isinstance(error, dict):
            raise ProtocolError("error must be an object or null")
        if status == "accepted" and error is not None:
            raise ProtocolError("accepted response cannot contain an error")
        canonical_json(result or {})
        canonical_json(error or {})
        return cls(version, request_id, status, revision, result, error)
    @classmethod
    def accepted(cls, request_id: str, revision: int | None, result: Mapping[str, Any]) -> "ControlResponse":
        return cls(SCHEMA_VERSION, request_id, "accepted", revision, dict(result), None)
    @classmethod
    def failure(cls, request_id: str, status: str, code: str, message: str,
        revision: int | None = None) -> "ControlResponse":
        if status not in _RESPONSE_STATUSES - {"accepted"}:
            raise ValueError(f"invalid failure status: {status}")
        return cls(SCHEMA_VERSION, request_id, status, revision, None,
            {"code": code, "message": message})
    def to_mapping(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in _RESPONSE_FIELDS}
def _encode_message(value: Mapping[str, Any]) -> bytes:
    payload = canonical_json(value).encode("utf-8")
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame size must be 1..{MAX_FRAME_BYTES} bytes")
    return _HEADER.pack(len(payload)) + payload
def send_message(peer: socket.socket, value: Mapping[str, Any]) -> None:
    peer.sendall(_encode_message(value))
def _recv_exact(peer: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = peer.recv(remaining)
        if not chunk:
            raise ProtocolError("peer closed an incomplete frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
def receive_message(peer: socket.socket) -> dict[str, Any]:
    length = _HEADER.unpack(_recv_exact(peer, _HEADER.size))[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise ProtocolError(f"invalid frame length: {length}")
    try:
        value = json.loads(_recv_exact(peer, length))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("message must be a JSON object")
    return value
def request_over_socket(socket_path: Path, request: ControlRequest, *, timeout: float = 5.0) -> ControlResponse:
    """Submit one request; this function never starts a Host or opens its DB."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(timeout)
        address, directory_fd = socket_address(socket_path)
        try:
            peer.connect(address)
        except OSError as exc:
            raise ConnectionError(f"Workflow Host unavailable at {socket_path}: {exc}") from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        send_message(peer, request.to_mapping())
        return ControlResponse.from_mapping(receive_message(peer))
