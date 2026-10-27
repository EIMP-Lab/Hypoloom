"""Read-only module diagnostics over the existing Hypoloom authority records.

Debug reports are derived, non-authoritative views.  They never create or
change a run, and an exported fixture is input to developer tooling rather
than a workflow command, fact, Attempt, or artifact.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sqlite3
import stat
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agent_aci import AgentACIError, AgentRequest, AgentResponse, validate_postflight
from .agent_budget import AgentAttemptCharge, fold_agent_attempts, usage_projection
from .diagnostics import (
    bounded_diagnostic_text,
    credential_shaped_text,
    diagnostic_summary,
)
from .domain import AttemptTerminalReason
from .oracle import Oracle
from .runner import ProcessObservation, ProcessRequest
from .state_store import StateStore, StateStoreError, StateTransaction


class DebugModeError(RuntimeError):
    """The requested diagnostic view or validation cannot be produced."""


_PROCESS_OBSERVATION_FIELDS = frozenset(
    {
        "attempt_id",
        "attempt_ordinal",
        "execution_id",
        "terminal_reason",
        "returncode",
        "pid",
        "process_group_id",
        "started_at",
        "ended_at",
        "stdout_path",
        "stderr_path",
    }
)
_CREDENTIAL_KEYS = frozenset(
    {
        "authorization",
        "apikey",
        "anthropicapikey",
        "openalexapikey",
        "accesstoken",
        "refreshtoken",
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "webtoken",
    }
)
_MAX_PRIVATE_FIXTURE_BYTES = 64 * 1024 * 1024
_MAX_DERIVED_REFS = 64
_MAX_DERIVED_REF_CHARS = 256
_MAX_CHANGED_FIELD_PATHS = 64
_MAX_CANDIDATE_DIFF_NODES = 4_096


def _is_credential_key(value: str) -> bool:
    normalized = "".join(
        character for character in value.lower() if character.isalnum()
    )
    return normalized in _CREDENTIAL_KEYS or any(
        marker in normalized
        for marker in (
            "apikey",
            "authorization",
            "authtoken",
            "accesstoken",
            "refreshtoken",
            "bearertoken",
            "password",
            "passwd",
            "privatekey",
            "secret",
        )
    ) or normalized.endswith("token")


def _json_object(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        raise DebugModeError("durable diagnostic JSON is invalid") from exc
    if not isinstance(value, Mapping):
        raise DebugModeError("durable diagnostic JSON is not an object")
    return dict(value)


def _credential_paths(value: object, path: str = "$") -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            child = f"{path}.{name}"
            if _is_credential_key(name):
                found.append(child)
            else:
                found.extend(_credential_paths(item, child))
        media_type = value.get("media_type")
        if isinstance(value.get("content_base64"), str):
            try:
                decoded = base64.b64decode(value["content_base64"], validate=True)
            except (binascii.Error, ValueError):
                decoded = None
            if decoded is not None and media_type == "application/json":
                try:
                    embedded = json.loads(decoded)
                except (UnicodeDecodeError, ValueError):
                    pass
                else:
                    found.extend(
                        _credential_paths(embedded, f"{path}.content_base64<json>")
                    )
            if decoded is not None and (
                (isinstance(media_type, str) and (
                    media_type.startswith("text/")
                    or media_type in {"application/x-python", "application/javascript"}
                ))
                or isinstance(value.get("path"), str)
            ):
                try:
                    embedded_text = decoded.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    if credential_shaped_text(embedded_text):
                        found.append(f"{path}.content_base64<text>")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            found.extend(_credential_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        if credential_shaped_text(value):
            return (path,)
        try:
            decoded_json = json.loads(value)
        except ValueError:
            pass
        else:
            if isinstance(decoded_json, (Mapping, list, tuple)):
                found.extend(_credential_paths(decoded_json, f"{path}<json>"))
    elif credential_shaped_text(value):
        found.append(path)
    return tuple(found)


def _task_identity(request: Mapping[str, Any]) -> dict[str, Any]:
    context = request.get("task_context")
    if not isinstance(context, Mapping):
        return {}
    if context.get("kind") == "RESEARCH_NODE" and isinstance(context.get("key"), Mapping):
        key = context["key"]
        return {
            "scope": "EXPERT_LINEAGE",
            "component_id": key.get("component_id"),
            "expert_slot": key.get("expert_slot"),
            "cycle": key.get("cycle"),
            "node_kind": key.get("node_kind"),
            "subkind": key.get("subkind"),
            "experiment_ordinal": key.get("experiment_ordinal"),
        }
    if context.get("kind") == "AUXILIARY":
        return {
            "scope": "AUXILIARY",
            "request_kind": context.get("request_kind"),
            "component_id": context.get("component_id"),
            "expert_slot": context.get("expert_slot"),
            "cycle": context.get("cycle"),
            "ordinal": context.get("ordinal"),
        }
    return {"scope": "UNKNOWN_TASK_CONTEXT"}


def _module_name(
    attempt: Mapping[str, Any],
    command: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> str:
    if attempt.get("kind") == "AGENT":
        request = observation.get("request")
        request = request if isinstance(request, Mapping) else {}
        context = request.get("task_context")
        if isinstance(context, Mapping):
            if context.get("kind") == "RESEARCH_NODE" and isinstance(context.get("key"), Mapping):
                key = context["key"]
                return "expert/" + "/".join(
                    str(item).lower()
                    for item in (key.get("node_kind"), key.get("subkind"))
                    if isinstance(item, str) and item
                )
            if context.get("kind") == "AUXILIARY" and isinstance(context.get("request_kind"), str):
                return f"auxiliary/{str(context['request_kind']).lower()}"
        agent_command = request.get("command")
        if isinstance(agent_command, str) and agent_command:
            return f"agent/{agent_command.lower()}"
        return "agent/unknown"
    command_type = command.get("command_type")
    if command_type == "VALIDATE_SPEC_CANDIDATE":
        return "process/candidate-validation"
    if command_type == "EXECUTE":
        return "process/experiment-execution"
    return f"process/{str(command_type or 'unknown').lower()}"


def _reconstructability(
    attempt: Mapping[str, Any], observation: Mapping[str, Any],
) -> dict[str, Any]:
    kind = attempt.get("kind")
    request_available = (
        isinstance(observation.get("request"), Mapping)
        if kind == "AGENT"
        else isinstance(observation.get("process_request"), Mapping)
    )
    omissions: list[str] = []
    response_available = isinstance(observation.get("response"), Mapping)
    rejected_response_available = isinstance(
        observation.get("rejected_response"), Mapping,
    )
    if kind == "AGENT" and not response_available and not rejected_response_available:
        omissions.append("agent_response_not_recorded")
    failure_code = observation.get("failure_code")
    if isinstance(failure_code, str) and failure_code.startswith("literature_"):
        omissions.append("raw_literature_response_not_retained")
    if kind == "PROCESS" and not _PROCESS_OBSERVATION_FIELDS <= set(observation):
        omissions.append("complete_process_observation_not_recorded")
    return {
        "pure_contract_input": (
            "AVAILABLE" if request_available else "NOT_RECONSTRUCTABLE"
        ),
        "terminal_evidence": (
            "EXACT_RECORDED_OUTPUT"
            if kind == "AGENT" and response_available
            else "EXACT_RECORDED_REJECTED_OUTPUT"
            if kind == "AGENT" and rejected_response_available
            else "EXACT_RECORDED_OBSERVATION"
            if kind == "PROCESS" and _PROCESS_OBSERVATION_FIELDS <= set(observation)
            else "BOUNDED_FAILURE_ONLY"
            if isinstance(failure_code, str)
            else "NOT_RECONSTRUCTABLE"
        ),
        "effectful_rehearsal": "MODULE_HANDLER_REQUIRED",
        "omissions": omissions,
    }


def _duration_seconds(row: Mapping[str, Any]) -> float | None:
    started = row.get("started_at")
    ended = row.get("ended_at")
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return None
    if not isinstance(ended, (int, float)) or isinstance(ended, bool):
        return None
    duration = float(ended) - float(started)
    return duration if duration >= 0 else None


def attempt_time_key(row: Mapping[str, Any]) -> tuple[float, str]:
    """Failure order uses completion, or start when completion is unavailable."""
    value = row.get("ended_at")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = row.get("started_at", 0)
    return float(value or 0), str(row["attempt_id"])


def _accounting_projection(
    row: Mapping[str, Any],
    charge: AgentAttemptCharge | None,
) -> dict[str, Any]:
    if row.get("kind") != "AGENT":
        return {
            "source": "not_applicable",
            "tokens": None,
            "turns": None,
            "cost_usd": None,
            "currency": "USD",
        }
    if charge is None:
        return {
            "source": "unavailable",
            "tokens": None,
            "turns": None,
            "cost_usd": None,
            "currency": "USD",
        }
    return {
        "source": charge.source,
        "tokens": charge.tokens,
        "turns": charge.turns,
        "cost_usd": None if charge.cost is None else format(charge.cost, "f"),
        "currency": "USD",
    }


def _failure_stage(
    row: Mapping[str, Any],
    command: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> str | None:
    if row.get("state") != "CLOSED" or row.get("terminal_reason") == "SUCCEEDED":
        return None
    explicit = observation.get("failure_stage")
    if (
        isinstance(explicit, str)
        and explicit
        and len(explicit) <= 64
        and all(character.isalnum() or character in "_./-" for character in explicit)
    ):
        return explicit
    failure_code = observation.get("failure_code")
    diagnostics = observation.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    provider_subtype = diagnostics.get("provider_subtype")
    if row.get("kind") == "PROCESS":
        if command.get("command_type") == "VALIDATE_SPEC_CANDIDATE":
            return "candidate_validation_process"
        if observation.get("synthetic_before_spawn") is True:
            return "process_prestart"
        return "process_runtime"
    if failure_code == "invalid_candidate" or isinstance(
        diagnostics.get("validation_error"), Mapping,
    ):
        return "host_candidate_validation"
    if isinstance(failure_code, str) and failure_code.startswith("literature_"):
        return "literature_retrieval"
    if failure_code in {"agent_workspace_preparation", "workspace_cleanup_failed"}:
        return "agent_workspace"
    if provider_subtype == "error_max_structured_output_retries":
        return "provider_structured_output"
    if failure_code in {"nonzero_exit", "provider_error", "malformed_json"}:
        return "provider"
    if row.get("terminal_reason") == "LOST" or failure_code == "host_restart":
        return "host_recovery"
    if row.get("terminal_reason") in {"TIMED_OUT", "CANCELLED"}:
        return "agent_runtime"
    return "agent_host"


def _safe_ref(value: object) -> str | None:
    text = bounded_diagnostic_text(value)
    if text is None:
        return None
    return text[:_MAX_DERIVED_REF_CHARS]


def _input_refs(observation: Mapping[str, Any]) -> tuple[str, ...]:
    request = observation.get("request")
    if not isinstance(request, Mapping):
        return ()
    artifacts = request.get("input_artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        return ()
    refs: list[str] = []
    for item in artifacts:
        ref = _safe_ref(item.get("ref")) if isinstance(item, Mapping) else None
        if ref is not None:
            refs.append(ref)
    return tuple(refs)


def _literature_retry_trace(
    observation: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    raw = observation.get("literature_retry_trace")
    if raw is None:
        preflight = observation.get("preflight_diagnostics")
        raw = (
            preflight.get("literature_retry_trace")
            if isinstance(preflight, Mapping)
            else None
        )
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    result: list[dict[str, Any]] = []
    for item in raw[:64]:
        if not isinstance(item, Mapping):
            continue
        result.append({
            "retry_ordinal": item.get("retry_ordinal"),
            "endpoint": item.get("endpoint"),
            "request_attempt_ordinal": item.get("request_attempt_ordinal"),
            "failure_code": item.get("failure_code"),
            "http_status": item.get("http_status"),
            "retry_after_seconds": item.get("retry_after_seconds"),
        })
    return tuple(result)


def _adapter_retry_policy(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, int] = {}
    for field in (
        "structured_output_failed_validation_cap",
        "transport_max_retries",
    ):
        configured = value.get(field)
        if (
            isinstance(configured, int)
            and not isinstance(configured, bool)
            and configured >= 0
        ):
            result[field] = configured
    return result or None


def _structured_output_correction(value: object) -> dict[str, Any] | None:
    """Whitelist the stored CC observation; never read raw logs or candidates."""
    if isinstance(value, Mapping) and value.get("scope") == "native_stop_validation":
        if (value.get("validation_source") != "host_stop_hook"
                or value.get("status") not in {"not_observed", "first_pass", "recovered", "unresolved", "validation_failed"}
                or type(value.get("accepted")) is not bool):
            return None
        counts = ("call_count", "rejected_count")
        if any(type(value.get(key)) is not int or value[key] < 0 for key in counts):
            return None
        raw = value.get("rounds")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return None
        rounds = [{
            "ordinal": item.get("ordinal") if type(item.get("ordinal")) is int and item["ordinal"] > 0 else None,
            "status": item["status"], "validation_error": bounded_diagnostic_text(item.get("validation_error")),
            **({"validation_stage": bounded_diagnostic_text(item.get("validation_stage"))}
               if "validation_stage" in item else {}),
        } for item in raw[:64] if isinstance(item, Mapping) and item.get("status") in {"accepted", "rejected", "error"}]
        result = {"scope": "native_stop_validation", "validation_source": "host_stop_hook",
                  "status": value["status"], "accepted": value["accepted"],
                  **{key: value[key] for key in counts}, "rounds": rounds}
        if (error := bounded_diagnostic_text(value.get("error"))) is not None:
            result["error"] = error
        if len(raw) > len(rounds) or value.get("truncated") is True:
            result["truncated"] = True
        return result
    if not isinstance(value, Mapping) or value.get("scope") != "cc_tool_observation":
        return None
    if value.get("status") not in {"not_observed", "first_pass", "recovered", "unresolved"}:
        return None
    counts = ("call_count", "rejected_count", "tool_succeeded_count", "unknown_count")
    if any(type(value.get(key)) is not int or value[key] < 0 for key in counts):
        return None
    raw = value.get("rounds")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    rounds = []
    for item in raw[:64]:
        if not isinstance(item, Mapping) or item.get("status") not in {"rejected", "tool_succeeded", "unknown"}:
            continue
        fields = item.get("candidate_fields")
        rounds.append({
            **{key: item.get(key) if type(item.get(key)) is int and item[key] >= 0 else None
               for key in ("round", "call_record_index", "result_record_index")},
            **{key: _safe_ref(item.get(key)) for key in ("tool_use_id", "message_id")},
            "status": item["status"],
            "validation_error": bounded_diagnostic_text(item.get("validation_error")),
            "candidate_fields": [safe for field in fields[:64] if (safe := _safe_ref(field)) is not None]
                if isinstance(fields, list) else [],
            "repeats_rejected_input": item.get("repeats_rejected_input")
                if isinstance(item.get("repeats_rejected_input"), bool) else None,
        })
    return {"scope": "cc_tool_observation", "status": value["status"],
            **{key: value[key] for key in counts},
            "truncated": value.get("truncated") is True or len(raw) > len(rounds), "rounds": rounds}


def _correction_input_refs(
    previous_observation: Mapping[str, Any] | None,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    previous = set(() if previous_observation is None else _input_refs(previous_observation))
    added = sorted(set(_input_refs(observation)) - previous)
    return {
        "refs": added[:_MAX_DERIVED_REFS],
        "count": len(added),
        "truncated": len(added) > _MAX_DERIVED_REFS,
    }


def _candidate_from_observation(observation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for field in ("response", "rejected_response"):
        response = observation.get(field)
        candidate = response.get("typed_candidate") if isinstance(response, Mapping) else None
        if isinstance(candidate, Mapping):
            return candidate
    return None


def _path_segment(value: object) -> str:
    text = str(value)
    if _is_credential_key(text) or credential_shaped_text(text):
        return "[credential-field]"
    return text.replace("~", "~0").replace("/", "~1")[:_MAX_DERIVED_REF_CHARS]


def _changed_candidate_paths(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    changed: list[str] = []
    stack: list[tuple[str, object, object]] = [("", before, after)]
    visited = 0
    truncated = False
    while stack:
        path, left, right = stack.pop()
        visited += 1
        if visited > _MAX_CANDIDATE_DIFF_NODES:
            truncated = True
            break
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            keys = sorted(set(left) | set(right), key=str, reverse=True)
            for key in keys:
                child = f"{path}/{_path_segment(key)}"
                if key not in left or key not in right:
                    changed.append(child)
                else:
                    stack.append((child, left[key], right[key]))
        elif (
            isinstance(left, Sequence)
            and not isinstance(left, (str, bytes))
            and isinstance(right, Sequence)
            and not isinstance(right, (str, bytes))
        ):
            maximum = max(len(left), len(right))
            for index in range(maximum - 1, -1, -1):
                child = f"{path}/{index}"
                if index >= len(left) or index >= len(right):
                    changed.append(child)
                else:
                    stack.append((child, left[index], right[index]))
        elif left != right:
            changed.append(path or "/")
        if len(changed) > _MAX_CHANGED_FIELD_PATHS:
            truncated = True
            break
    changed = sorted(set(changed))
    return {
        "changed_field_paths": changed[:_MAX_CHANGED_FIELD_PATHS],
        "changed_field_count_lower_bound": len(changed),
        "truncated": truncated or len(changed) > _MAX_CHANGED_FIELD_PATHS,
    }


def _candidate_delta(
    previous_attempt_id: str | None,
    previous_observation: Mapping[str, Any] | None,
    observation: Mapping[str, Any],
) -> dict[str, Any] | None:
    if previous_attempt_id is None or previous_observation is None:
        return None
    rejected = previous_observation.get("rejected_response")
    before = rejected.get("typed_candidate") if isinstance(rejected, Mapping) else None
    after = _candidate_from_observation(observation)
    if not isinstance(before, Mapping) or after is None:
        return None
    return {
        "from_attempt_id": previous_attempt_id,
        **_changed_candidate_paths(before, after),
    }


def _attempt_lineage_key(row: Mapping[str, Any]) -> tuple[str, str]:
    if row.get("kind") == "AGENT":
        return "AGENT_COMMAND", str(row.get("command_id", ""))
    execution_id = row.get("execution_id")
    if isinstance(execution_id, str) and execution_id:
        return "PROCESS_EXECUTION", execution_id
    return "PROCESS_COMMAND", str(row.get("command_id", ""))


def _attempt_sort_key(
    row: Mapping[str, Any],
    transition_revision: int | None,
) -> tuple[int, float, float, str]:
    started = row.get("started_at")
    started_value = (
        float(started)
        if isinstance(started, (int, float)) and not isinstance(started, bool)
        else float("inf")
    )
    ordinal = row.get("attempt_ordinal")
    ordinal_value = (
        int(ordinal)
        if isinstance(ordinal, int) and not isinstance(ordinal, bool)
        else 0
    )
    if row.get("kind") == "AGENT":
        return 0, float(ordinal_value), started_value, str(row.get("attempt_id", ""))
    if transition_revision is not None:
        return 0, float(transition_revision), started_value, str(row.get("attempt_id", ""))
    return 1, started_value, float(ordinal_value), str(row.get("attempt_id", ""))


def _attempt_relationships(
    attempts: Sequence[Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], set[str], dict[str, str], dict[str, str]]:
    transition_payloads = [
        (transition, _json_object(transition.get("payload_json")))
        for transition in transitions
    ]
    transition_revision_by_attempt: dict[str, int] = {}
    for transition, payload in transition_payloads:
        attempt_id = payload.get("attempt_id")
        revision = transition.get("revision")
        if (
            isinstance(attempt_id, str)
            and isinstance(revision, int)
            and not isinstance(revision, bool)
        ):
            transition_revision_by_attempt[attempt_id] = min(
                revision,
                transition_revision_by_attempt.get(attempt_id, revision),
            )
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    lineage_scope: dict[str, str] = {}
    for row in attempts:
        key = _attempt_lineage_key(row)
        groups.setdefault(key, []).append(row)
        lineage_scope[str(row["attempt_id"])] = key[0]
    predecessor: dict[str, str] = {}
    recovered: set[str] = set()
    for rows in groups.values():
        ordered = sorted(
            rows,
            key=lambda row: _attempt_sort_key(
                row,
                transition_revision_by_attempt.get(str(row["attempt_id"])),
            ),
        )
        for index, row in enumerate(ordered):
            attempt_id = str(row["attempt_id"])
            if index:
                previous = ordered[index - 1]
                if (
                    previous.get("state") == "CLOSED"
                    and previous.get("terminal_reason")
                    not in {"SUCCEEDED", "CANCELLED", None}
                ):
                    predecessor[attempt_id] = str(previous["attempt_id"])
            if (
                row.get("state") == "CLOSED"
                and row.get("terminal_reason") not in {"SUCCEEDED", "CANCELLED", None}
                and any(
                    later.get("state") == "CLOSED"
                    and later.get("terminal_reason") == "SUCCEEDED"
                    for later in ordered[index + 1 :]
                )
            ):
                recovered.add(attempt_id)
    source_candidates: dict[str, set[str]] = {}
    for _, payload in transition_payloads:
        attempt_id = payload.get("attempt_id")
        source_id = payload.get("agent_attempt_id")
        if isinstance(attempt_id, str) and isinstance(source_id, str):
            source_candidates.setdefault(attempt_id, set()).add(source_id)
    source_attempts = {
        attempt_id: next(iter(values))
        for attempt_id, values in source_candidates.items()
        if len(values) == 1
    }
    return predecessor, recovered, source_attempts, lineage_scope


def _evaluator_retry_projection(
    transitions: Sequence[Mapping[str, Any]],
    command_by_id: Mapping[str, Mapping[str, Any]],
    *,
    source_attempt_id: str | None,
) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for row in transitions:
        command = command_by_id.get(str(row.get("command_id", "")), {})
        if command.get("command_type") != "RETRY_EVALUATOR":
            continue
        payload = _json_object(row.get("payload_json"))
        source_id = payload.get("source_attempt_id")
        if source_attempt_id is not None and source_id != source_attempt_id:
            continue
        events.append({
            "workflow_id": row.get("workflow_id"),
            "revision": row.get("revision"),
            "source_attempt_id": source_id,
            "retry_ordinal": payload.get("evaluator_retry_ordinal"),
            "failure_stage": payload.get("failure_stage"),
            "failure_class": payload.get("failure_class"),
            "retry_after_seconds": payload.get("retry_after_seconds"),
            "due_at": row.get("due_at"),
            "created_at": row.get("created_at"),
        })
    return tuple(events)


def _attempt_projection(
    row: Mapping[str, Any],
    command: Mapping[str, Any],
    *,
    recovered: bool,
    final_failure: bool,
    charge: AgentAttemptCharge | None,
    retry_of_attempt_id: str | None,
    previous_row: Mapping[str, Any] | None,
    previous_command: Mapping[str, Any] | None,
    previous_observation: Mapping[str, Any] | None,
    source_attempt_id: str | None,
    lineage_scope: str,
    include_payloads: bool,
) -> dict[str, Any]:
    observation = _json_object(row.get("observation_json"))
    request = observation.get("request")
    request = dict(request) if isinstance(request, Mapping) else {}
    response = observation.get("response")
    response = dict(response) if isinstance(response, Mapping) else None
    rejected_response = observation.get("rejected_response")
    rejected_response = (
        dict(rejected_response) if isinstance(rejected_response, Mapping) else None
    )
    process_request = observation.get("process_request")
    process_request = dict(process_request) if isinstance(process_request, Mapping) else None
    diagnostics = observation.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    provider_subtype = bounded_diagnostic_text(diagnostics.get("provider_subtype"))
    if provider_subtype == "success":
        provider_subtype = None
    adapter_retry_policy = _adapter_retry_policy(
        diagnostics.get("adapter_retry_policy")
    )
    literature_retry_trace = _literature_retry_trace(observation)
    session = observation.get("session")
    session_id = _safe_ref(session.get("session_id")) if isinstance(session, Mapping) else None
    previous_session = (previous_observation or {}).get("session")
    previous_session_id = _safe_ref(previous_session.get("session_id")) if isinstance(previous_session, Mapping) else None
    projected: dict[str, Any] = {
        "attempt_id": row["attempt_id"],
        "module": _module_name(row, command, observation),
        "kind": row["kind"],
        "state": row["state"],
        "terminal_reason": row.get("terminal_reason"),
        "attempt_ordinal": row["attempt_ordinal"],
        "run_id": row["run_id"],
        "workflow_id": row["workflow_id"],
        "command_id": row["command_id"],
        "session_id": session_id,
        "same_session_as_previous_attempt": (
            None if session_id is None or previous_session_id is None else session_id == previous_session_id),
        "command_type": command.get("command_type"),
        "experiment_id": row.get("experiment_id"),
        "execution_id": row.get("execution_id"),
        "host_generation": row["host_generation"],
        "process_group_id": row.get("process_group_id"),
        "started_at": row["started_at"],
        "ended_at": row.get("ended_at"),
        "duration_seconds": _duration_seconds(row),
        "failure_code": observation.get("failure_code"),
        "failure_stage": _failure_stage(row, command, observation),
        "retry_class": observation.get("retry_class"),
        "provider_subtype": provider_subtype,
        "adapter_version": (
            request.get("required_adapter_version")
            if isinstance(request.get("required_adapter_version"), str)
            else None
        ),
        "requested_model_identity": (
            request.get("required_model_identity")
            if isinstance(request.get("required_model_identity"), str)
            else None
        ),
        "adapter_retry_policy": adapter_retry_policy,
        "structured_output_correction": _structured_output_correction(
            diagnostics.get("structured_output_correction")),
        "literature_retry_count": len(literature_retry_trace),
        "literature_retry_trace": list(literature_retry_trace),
        "returncode": observation.get("returncode"),
        "diagnostic_summary": diagnostic_summary(observation),
        "stdout_path": observation.get("stdout_path"),
        "stderr_path": observation.get("stderr_path"),
        "recovered_by_later_attempt": recovered,
        "final_failure_transition": final_failure,
        "retry_of_attempt_id": retry_of_attempt_id,
        "retry_lineage_scope": lineage_scope,
        "source_attempt_id": source_attempt_id,
        "retry_trigger": (
            None
            if previous_row is None or previous_command is None
            else {
                "terminal_reason": previous_row.get("terminal_reason"),
                "failure_code": (
                    None
                    if previous_observation is None
                    else previous_observation.get("failure_code")
                ),
                "failure_stage": _failure_stage(
                    previous_row,
                    previous_command,
                    previous_observation or {},
                ),
            }
        ),
        "correction_inputs": _correction_input_refs(
            previous_observation, observation,
        ) if retry_of_attempt_id is not None and row.get("kind") == "AGENT" else {
            "refs": [], "count": 0, "truncated": False,
        },
        "candidate_delta": _candidate_delta(
            retry_of_attempt_id, previous_observation, observation,
        ),
        "accounting": _accounting_projection(row, charge),
        "task_identity": _task_identity(request),
        "reconstructability": _reconstructability(row, observation),
        "has_request": bool(request or process_request),
        "has_response": response is not None,
        "has_rejected_response": rejected_response is not None,
    }
    if include_payloads:
        projected["observation"] = observation
        projected["command"] = dict(command)
    return projected


def _read_debug_rows(
    state_root: Path,
    run_id: str,
) -> tuple[
    dict[str, Any],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    store = StateStore(state_root)
    try:
        with store.readonly_connection() as connection:
            connection.execute("BEGIN")
            reader = StateTransaction(connection)
            run = reader.get_run(run_id)
            if run is None:
                raise DebugModeError(f"run not found: {run_id!r}")
            commands = tuple(
                dict(row)
                for row in reader.execute(
                    "SELECT * FROM workflow_commands WHERE run_id=? ORDER BY created_at,command_id",
                    (run_id,),
                ).fetchall()
            )
            attempts = reader.list_attempts(run_id=run_id)
            transitions = tuple(
                dict(row)
                for row in reader.execute(
                    "SELECT * FROM workflow_transitions WHERE run_id=? "
                    "ORDER BY created_at,workflow_id,revision",
                    (run_id,),
                ).fetchall()
            )
            facts = tuple(reader.list_facts(run_id))
            artifacts = tuple(
                dict(row)
                for row in reader.execute(
                    "SELECT * FROM execution_artifacts WHERE run_id=? ORDER BY created_at,artifact_id",
                    (run_id,),
                ).fetchall()
            )
    except DebugModeError:
        raise
    except (OSError, sqlite3.Error, StateStoreError) as exc:
        raise DebugModeError(f"cannot read source state: {type(exc).__name__}") from exc
    return dict(run), commands, attempts, transitions, facts, artifacts


def build_debug_report(
    state_root: str | Path,
    run_id: str,
    *,
    attempt_id: str | None = None,
    include_payloads: bool = False,
) -> dict[str, Any]:
    """Derive one read-only diagnostic report from existing authority rows."""

    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be non-empty text")
    if attempt_id is not None and (not isinstance(attempt_id, str) or not attempt_id):
        raise ValueError("attempt_id must be non-empty text or None")
    root = Path(state_root).expanduser().resolve()
    run, commands, attempts, transitions, facts, artifacts = _read_debug_rows(root, run_id)
    command_by_id = {str(row["command_id"]): row for row in commands}
    observation_by_id = {
        str(row["attempt_id"]): _json_object(row.get("observation_json"))
        for row in attempts
    }
    row_by_id = {str(row["attempt_id"]): row for row in attempts}
    charge_by_id = {
        item.attempt_id: item for item in fold_agent_attempts(attempts)
    }
    predecessor_by_id, recovered_ids, source_by_id, lineage_by_id = (
        _attempt_relationships(attempts, transitions)
    )
    evaluator_retries = _evaluator_retry_projection(
        transitions, command_by_id, source_attempt_id=attempt_id,
    )
    final_failure_ids: set[str] = set()
    for row in transitions:
        payload = _json_object(row["payload_json"])
        if payload.get("agent_final_failure") is True and isinstance(payload.get("attempt_id"), str):
            final_failure_ids.add(str(payload["attempt_id"]))
    selected_rows = tuple(
        row for row in attempts if attempt_id is None or row["attempt_id"] == attempt_id
    )
    if attempt_id is not None and not selected_rows:
        raise DebugModeError(f"attempt not found in run {run_id!r}: {attempt_id!r}")
    projected_rows: list[dict[str, Any]] = []
    for row in selected_rows:
        current_id = str(row["attempt_id"])
        previous_id = predecessor_by_id.get(current_id)
        previous_row = None if previous_id is None else row_by_id.get(previous_id)
        previous_command = (
            None
            if previous_row is None
            else command_by_id.get(str(previous_row["command_id"]), {})
        )
        projected_rows.append(_attempt_projection(
            row,
            command_by_id.get(str(row["command_id"]), {}),
            recovered=current_id in recovered_ids,
            final_failure=current_id in final_failure_ids,
            charge=charge_by_id.get(current_id),
            retry_of_attempt_id=previous_id,
            previous_row=previous_row,
            previous_command=previous_command,
            previous_observation=(
                None if previous_id is None else observation_by_id.get(previous_id)
            ),
            source_attempt_id=source_by_id.get(current_id),
            lineage_scope=lineage_by_id[current_id],
            include_payloads=include_payloads,
        ))
    projected = tuple(sorted(projected_rows, key=attempt_time_key))
    unrecovered = tuple(
        item["attempt_id"]
        for item in projected
        if item["state"] == "CLOSED"
        and item["terminal_reason"] not in {"SUCCEEDED", "CANCELLED"}
        and not item["recovered_by_later_attempt"]
    )
    recovered = tuple(
        item["attempt_id"] for item in projected if item["recovered_by_later_attempt"]
    )
    run_head = max((row for row in transitions if row["workflow_id"] == run_id),
                   key=lambda row: row["revision"], default={})
    report: dict[str, Any] = {
        "non_authoritative": True,
        "mode": "READ_ONLY_DIAGNOSIS",
        "observed_at": time.time(),
        "state_root": str(root),
        "run_id": run_id,
        "selected_attempt_id": attempt_id,
        "run_versions": run,
        "run_state": {"state": run_head.get("state"), "revision": run_head.get("revision")},
        "summary": {
            "scope": "attempt" if attempt_id else "run",
            "order": "ended_at_or_started_at_then_attempt_id",
            "attempt_count": len(projected),
            "recovered_failure_attempt_ids": list(recovered),
            "unrecovered_failure_attempt_ids": list(unrecovered),
            "final_agent_failure_attempt_ids": sorted(final_failure_ids),
            "first_unrecovered_failure_attempt_id": unrecovered[0] if unrecovered else None,
            "retry_attempt_ids": [
                item["attempt_id"]
                for item in projected
                if item["retry_of_attempt_id"] is not None
            ],
            "evaluator_retry_count": len(evaluator_retries),
        },
        "attempts": list(projected),
        "evaluator_retries": list(evaluator_retries),
    }
    contract = next((row.get("payload", {}).get("research_contract") for row in facts
        if row.get("fact_type") == "GoalDefined"), None)
    report["budget_usage"] = {"scope": "run", **usage_projection((), future_attempts=0,
        future_budget=None, document=contract if isinstance(contract, Mapping) else None,
        charges=tuple(charge_by_id.values()))}
    if include_payloads:
        report["related_records"] = {
            "commands": list(commands),
            "transitions": list(transitions),
            "facts": list(facts),
            "artifacts": list(artifacts),
        }
    try:
        oracle = Oracle(root / "state.sqlite", root / "artifacts").audit(run_id)
    except (OSError, RuntimeError, ValueError) as exc:
        report["oracle"] = {"available": False, "error": type(exc).__name__}
    else:
        report["oracle"] = {
            "available": True,
            "ok": oracle.ok,
            "issue_count": len(oracle.issues),
            "issues": [issue.to_dict() for issue in oracle.issues],
        }
    return report


def validate_attempt_payload(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """Re-run only current pure parsers/validators over one exported Attempt."""

    observation = attempt.get("observation")
    if not isinstance(observation, Mapping):
        return {
            "valid": False,
            "checks": [],
            "errors": ["fixture does not include the exact observation payload"],
        }
    checks: list[str] = []
    errors: list[str] = []
    kind = attempt.get("kind")
    if kind == "AGENT":
        request_value = observation.get("request")
        try:
            if not isinstance(request_value, Mapping):
                raise AgentACIError("invalid_request", "Agent observation has no request")
            request = AgentRequest.from_dict(request_value)
            if any((
                request.attempt_id != attempt.get("attempt_id"),
                request.attempt_ordinal != attempt.get("attempt_ordinal"),
                request.run_id != attempt.get("run_id"),
                request.owner_workflow_id != attempt.get("workflow_id"),
                request.command_id != attempt.get("command_id"),
            )):
                raise AgentACIError(
                    "wrong_invocation_identity", "Attempt row and request differ",
                )
            checks.append("agent_request")
            response_value = observation.get("response")
            if response_value is not None:
                if attempt.get("terminal_reason") != AttemptTerminalReason.SUCCEEDED.value:
                    raise AgentACIError(
                        "invalid_response",
                        "a recorded Agent response requires a SUCCEEDED Attempt",
                    )
                if not isinstance(response_value, Mapping):
                    raise AgentACIError("invalid_response", "Agent response is not an object")
                response = AgentResponse.from_dict(response_value)
                validate_postflight(request, response)
                checks.extend(("agent_response", "agent_postflight"))
            elif attempt.get("terminal_reason") == "SUCCEEDED":
                raise AgentACIError("invalid_response", "successful Agent Attempt has no response")
        except (AgentACIError, ValueError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    elif kind == "PROCESS":
        request_value = observation.get("process_request")
        try:
            if not isinstance(request_value, Mapping):
                raise ValueError("Process observation has no ProcessRequest")
            request = ProcessRequest.from_dict(request_value)
            if any((
                request.attempt_id != attempt.get("attempt_id"),
                request.attempt_ordinal != attempt.get("attempt_ordinal"),
                request.execution_id != attempt.get("execution_id"),
            )):
                raise ValueError("Attempt row and ProcessRequest differ")
            checks.append("process_request")
            synthetic = observation.get("synthetic_before_spawn")
            if synthetic is not None and not isinstance(synthetic, bool):
                raise ValueError("synthetic_before_spawn must be boolean when present")
            if synthetic is True:
                terminal_reason = AttemptTerminalReason(observation["terminal_reason"])
                if any((
                    terminal_reason is not AttemptTerminalReason.FAILED,
                    observation.get("attempt_id") != request.attempt_id,
                    observation.get("attempt_ordinal") != request.attempt_ordinal,
                    observation.get("execution_id") != request.execution_id,
                    terminal_reason.value != attempt.get("terminal_reason"),
                )):
                    raise ValueError("synthetic Process observation identity differs")
                checks.append("synthetic_process_observation")
            else:
                if not _PROCESS_OBSERVATION_FIELDS <= set(observation):
                    raise ValueError("terminal Process observation is incomplete")
                process_value = {
                    key: observation[key] for key in _PROCESS_OBSERVATION_FIELDS
                }
                parsed = ProcessObservation.from_dict(process_value)
                if any((
                    parsed.attempt_id != request.attempt_id,
                    parsed.attempt_ordinal != request.attempt_ordinal,
                    parsed.execution_id != request.execution_id,
                    parsed.terminal_reason.value != attempt.get("terminal_reason"),
                )):
                    raise ValueError("Process observation and request differ")
                checks.append("process_observation")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    else:
        errors.append(f"unsupported Attempt kind: {kind!r}")
    return {"valid": not errors, "checks": checks, "errors": errors}


def validate_debug_report(
    payload: Mapping[str, Any],
    *,
    validation_mode: str,
    fixture: str | None = None,
) -> dict[str, Any]:
    """Validate one exact report as source-exact or explicit counterfactual."""

    if validation_mode not in {"source-exact", "candidate"}:
        raise ValueError("validation_mode must be source-exact or candidate")
    try:
        from .workflow_host import installed_run_version

        candidate_versions = asdict(installed_run_version())
    except (OSError, RuntimeError, ValueError) as exc:
        raise DebugModeError("cannot identify the current candidate runtime") from exc
    if payload.get("non_authoritative") is not True:
        raise DebugModeError("debug fixture is not a non-authoritative export")
    source_versions = payload.get("run_versions")
    if not isinstance(source_versions, Mapping):
        raise DebugModeError("debug fixture has no source run version tuple")
    source_tuple = {
        key: source_versions.get(key) for key in candidate_versions
    }
    exact = source_tuple == candidate_versions
    attempts = payload.get("attempts")
    if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)) or len(attempts) != 1:
        raise DebugModeError("debug fixture must select exactly one Attempt")
    attempt = attempts[0]
    if not isinstance(attempt, Mapping):
        raise DebugModeError("debug fixture Attempt is not an object")
    result = validate_attempt_payload(attempt)
    if validation_mode == "source-exact" and not exact:
        result = {
            **result,
            "valid": False,
            "errors": [
                *result["errors"],
                "source runtime tuple is not reconstructable by this validator: "
                "the current executable/default configuration tuple differs; "
                "use a complete exact launch environment or select explicit "
                "candidate validation"
            ],
        }
    return {
        "non_authoritative": True,
        "mode": "PURE_CONTRACT_VALIDATION",
        "validation_mode": validation_mode,
        "fixture": fixture,
        "run_id": payload.get("run_id"),
        "attempt_id": attempt.get("attempt_id"),
        "source_versions": source_tuple,
        "candidate_versions": candidate_versions,
        "source_exact": exact,
        "source_exact_status": "MATCHED" if exact else "NOT_RECONSTRUCTABLE",
        **result,
    }


def validate_debug_fixture(
    path: str | Path,
    *,
    validation_mode: str,
) -> dict[str, Any]:
    """Validate one diagnostic export without accessing its source state root."""

    selected = Path(path).expanduser().resolve()
    try:
        raw = selected.read_bytes()
    except OSError as exc:
        raise DebugModeError(f"cannot read debug fixture: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise DebugModeError("debug fixture is not JSON") from exc
    if not isinstance(payload, Mapping):
        raise DebugModeError("debug fixture is not an object")
    return validate_debug_report(
        payload, validation_mode=validation_mode, fixture=str(selected),
    )


def write_debug_fixture(path: str | Path, report: Mapping[str, Any]) -> Path:
    """Write one exact non-authoritative fixture, mode 0600, without overwrite."""

    requested = Path(path).expanduser()
    if not requested.is_absolute():
        raise DebugModeError("debug fixture path must be absolute")
    try:
        parent = requested.parent.resolve(strict=True)
    except OSError as exc:
        raise DebugModeError(f"debug fixture parent is unavailable: {exc}") from exc
    if not parent.is_dir():
        raise DebugModeError(f"debug fixture parent is not a directory: {parent}")
    selected = parent / requested.name
    try:
        selected_metadata = selected.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DebugModeError(f"cannot inspect debug fixture target: {exc}") from exc
    else:
        if stat.S_ISLNK(selected_metadata.st_mode):
            raise DebugModeError("debug fixture target must not be a symlink")
    source_root_value = report.get("state_root")
    if isinstance(source_root_value, str):
        source_root = Path(source_root_value).expanduser().resolve()
        if selected == source_root or source_root in selected.parents:
            raise DebugModeError("debug fixture must be outside the source state root")
    if credential_paths := _credential_paths(report):
        raise DebugModeError(
            "debug fixture contains credential-shaped fields: "
            + ", ".join(credential_paths[:8])
        )
    encoded = json.dumps(
        dict(report), ensure_ascii=False, sort_keys=True, indent=2,
    ).encode("utf-8") + b"\n"
    if len(encoded) > _MAX_PRIVATE_FIXTURE_BYTES:
        raise DebugModeError(
            "exact debug fixture exceeds the 64 MiB private export bound"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(selected, flags, 0o600)
    except OSError as exc:
        raise DebugModeError(f"cannot create debug fixture: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DebugModeError("debug fixture target is not a regular file")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o600)
    except BaseException:
        try:
            selected.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    return selected


__all__ = [
    "DebugModeError",
    "build_debug_report",
    "validate_attempt_payload",
    "validate_debug_fixture",
    "validate_debug_report",
    "write_debug_fixture",
]
