"""Bounded diagnostic text shared by persistence and read-only views."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


MAX_DIAGNOSTIC_TEXT_CHARS = 1_024
_REDACTED_DIAGNOSTIC = "[credential-shaped diagnostic redacted]"
_CREDENTIAL_TEXT = re.compile(
    r"(?ix)(?:"
    r"\bauthorization\s*[:=]\s*\S|"
    r"\bbearer\s+\S|"
    r"\b(?:api[\s_-]*key|(?:auth|access|refresh)[\s_-]*token|"
    r"password|passwd|private[\s_-]*key|secret)\s*[:=]\s*\S|"
    r"\b[a-z][a-z0-9_]*(?:_token|_secret|_password|_private_key|_api_key)"
    r"[a-z0-9_]*\s*[:=]\s*\S"
    r")"
)
_CREDENTIAL_JSON_KEY = re.compile(
    r"(?ix)^(?:authorization|api[\s_-]*key|"
    r"(?:auth|access|refresh)[\s_-]*token|password|passwd|"
    r"private[\s_-]*key|secret|[a-z][a-z0-9_]*"
    r"(?:_token|_secret|_password|_private_key|_api_key))$"
)


def credential_shaped_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if _CREDENTIAL_TEXT.search(value) is not None:
        return True
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return False
    return _credential_shaped_json(decoded)


def _credential_shaped_json(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                isinstance(key, str)
                and _CREDENTIAL_JSON_KEY.fullmatch(key)
                and item not in (None, "", False)
            ):
                return True
            if _credential_shaped_json(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_credential_shaped_json(item) for item in value)
    return isinstance(value, str) and _CREDENTIAL_TEXT.search(value) is not None


def bounded_diagnostic_text(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if credential_shaped_text(value):
        return _REDACTED_DIAGNOSTIC
    return value[:MAX_DIAGNOSTIC_TEXT_CHARS]


def _trace_ref(value: object) -> str | None:
    text = bounded_diagnostic_text(value)
    return None if text is None else text[:256]


def _structured_fields(value: object) -> tuple[list[str], bool]:
    if not isinstance(value, Mapping):
        return [], False
    paths = []
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        segment = _trace_ref(key)
        if segment is None:
            continue
        path = "/" + segment.replace("~", "~0").replace("/", "~1")
        paths.append(path)
        if key == "typed_candidate" and isinstance(item, Mapping):
            paths.extend(path + "/" + name.replace("~", "~0").replace("/", "~1")
                         for field in item if (name := _trace_ref(field)) is not None)
    return sorted(paths)[:64], len(paths) > 64


def _structured_input(value: object) -> str | None:
    # Ephemeral exact JSON comparison only; never emit candidate values or a digest.
    if not isinstance(value, Mapping):
        return None
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return None


def _tool_error(value: object) -> str | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        value = "\n".join(item["text"] for item in value
                          if isinstance(item, Mapping) and item.get("type") == "text"
                          and isinstance(item.get("text"), str))
    return bounded_diagnostic_text(value)


def structured_output_trace(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Observe CC tool feedback without validating or accepting a candidate.

    Record indices are zero-based; rounds are one-based. Only matching feedback
    after a call establishes its status. A terminal CLI result is not feedback.
    """
    rounds: list[dict[str, Any]] = []
    by_id: dict[str, int | None] = {}
    inputs: list[str | None] = []
    rejected_inputs: list[str] = []
    truncated = False
    for record_index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        message = event.get("message")
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
                identity = block.get("id")
                fields, fields_truncated = _structured_fields(block.get("input"))
                truncated |= fields_truncated
                candidate = _structured_input(block.get("input"))
                rounds.append({
                    "round": len(rounds) + 1,
                    "tool_use_id": _trace_ref(identity),
                    "message_id": _trace_ref(message.get("id")),
                    "call_record_index": record_index, "result_record_index": None,
                    "status": "unknown", "validation_error": None,
                    "candidate_fields": fields,
                    "repeats_rejected_input": None if candidate is None else candidate in rejected_inputs,
                })
                inputs.append(candidate)
                if isinstance(identity, str) and identity:
                    by_id[identity] = len(rounds) - 1 if identity not in by_id else None
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                identity = block.get("tool_use_id")
                index = by_id.get(identity) if isinstance(identity, str) else None
                error = block.get("is_error", False)
                if index is None or not isinstance(error, bool) or rounds[index]["result_record_index"] is not None:
                    continue
                rounds[index].update(result_record_index=record_index,
                    status="rejected" if error else "tool_succeeded",
                    validation_error=_tool_error(block.get("content")) if error else None)
                if error and inputs[index] is not None:
                    rejected_inputs.append(inputs[index])
    rejected = sum(item["status"] == "rejected" for item in rounds)
    succeeded = sum(item["status"] == "tool_succeeded" for item in rounds)
    unknown = len(rounds) - rejected - succeeded
    status = "not_observed" if not rounds else (
        "unresolved" if unknown or rounds[-1]["status"] != "tool_succeeded" else
        "recovered" if rejected else "first_pass")
    return {"scope": "cc_tool_observation", "status": status,
            "call_count": len(rounds), "rejected_count": rejected,
            "tool_succeeded_count": succeeded, "unknown_count": unknown,
            "truncated": truncated or len(rounds) > 64, "rounds": rounds[:64]}


def diagnostic_summary(observation: Mapping[str, Any]) -> str | None:
    """Project only explicitly admitted text; never expose arbitrary stderr."""

    for key in (
        "literature_diagnostic",
        "resource_diagnostic",
        "workspace_error",
        "agent_host_diagnostic",
        "provider_diagnostic",
        "integrity_error",
        "validation_error",
    ):
        if (value := bounded_diagnostic_text(observation.get(key))) is not None:
            return value
    diagnostics = observation.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    validation = diagnostics.get("validation_error")
    if isinstance(validation, Mapping):
        code = bounded_diagnostic_text(validation.get("code"))
        message = bounded_diagnostic_text(validation.get("message"))
        if code and message:
            return f"{code}: {message}"[:MAX_DIAGNOSTIC_TEXT_CHARS]
        return code or message
    for key in (
        "literature_diagnostic",
        "resource_diagnostic",
        "workspace_error",
        "agent_host_diagnostic",
        "integrity_error",
        "failure",
        "provider_diagnostic",
    ):
        if (value := bounded_diagnostic_text(diagnostics.get(key))) is not None:
            return value
    correction = diagnostics.get("structured_output_correction")
    if isinstance(correction, Mapping):
        rejected = correction.get("rejected_count")
        if correction.get("status") == "recovered" and type(rejected) is int and rejected > 0:
            if correction.get("scope") == "cc_tool_observation":
                return f"CC StructuredOutput recovered after {rejected} rejected call(s); Host acceptance is separate."
            if correction.get("scope") == "native_stop_validation":
                return f"Native CC output recovered after {rejected} rejected completion(s); Host acceptance is separate."
    subtype = bounded_diagnostic_text(diagnostics.get("provider_subtype"))
    return None if subtype == "success" else subtype


__all__ = [
    "MAX_DIAGNOSTIC_TEXT_CHARS",
    "bounded_diagnostic_text",
    "credential_shaped_text",
    "diagnostic_summary",
    "structured_output_trace",
]
