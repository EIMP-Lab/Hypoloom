"""Operator UI transport to the existing run-bound Host ANSWER command."""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping

from .control_protocol import ControlRequest, MAX_FRAME_BYTES, request_over_socket


def answer_request(run_id: str, values: Mapping) -> ControlRequest:
    if not isinstance(values, Mapping) or set(values) != {"decision_id", "answer", "expected_revision", "request_id"}:
        raise ValueError("answer fields must be decision_id, answer, expected_revision and request_id")
    if not isinstance(values["answer"], str) or not values["answer"].strip():
        raise ValueError("enter an answer before submitting")
    request = ControlRequest.from_mapping({"schema_version": 1, "command": "ANSWER",
        "request_id": values["request_id"], "expected_revision": values["expected_revision"],
        "payload": {"run_id": run_id, "decision_id": values["decision_id"], "answer": values["answer"]}})
    if request.expected_revision is None:
        raise ValueError("answer requires the reviewed run revision")
    for field in ("run_id", "decision_id", "answer"):
        value = request.payload[field]
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError(f"{field} must be nonempty text")
    if len(request.normalized().encode()) > MAX_FRAME_BYTES:
        raise ValueError("answer exceeds the Host request size limit")
    return request


def submit_answer(state_root: str | Path, run_id: str, values: Mapping):
    # The Host remains the sole validator/writer of decision ownership, revision,
    # idempotency and continuation. Never open a DB or start a Host here.
    return request_over_socket(Path(state_root) / "control.sock", answer_request(run_id, values), timeout=5)
