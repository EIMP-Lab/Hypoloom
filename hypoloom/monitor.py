"""Read-only JSON and TUI-facing observations for a Hypoloom state root.

The monitor deliberately has no writer capability.  Workflow state comes from
the canonical StateStore heads, integrity evidence comes from the independent
Oracle, and the resident Host uses the same status projection for ``STATUS``.
Each refresh reads its durable StateStore projection in one read transaction;
the independent Oracle and optional compatible Host enrichment remain separate
read-only observations rather than new sources of authority.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO

from .agent_aci import (
    AgentACIError,
    AgentRequest,
    AgentResponse,
    EXPERT_LOOP_ACI_SCHEMA_VERSION,
    validate_postflight,
)
from .collaboration import derive_rendezvous_records
from .contract_normalization import EXPERT_LOOP_RESEARCH_LOOP
from .control_protocol import ControlRequest, request_over_socket
from .domain import HypothesisAssessment, ResearchContract, ScientificOutcome
from .diagnostics import diagnostic_summary
from .expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    HypothesisUpdateDisposition,
    ResearchNodeKind,
    ResearchNodeTask,
)
from .oracle import Oracle
from .policy import (
    _REVISION_BREAKER_MAX,
    _REVISION_BREAKER_SEVERE,
    _REVISION_BREAKER_WARN,
)
from .state_store import StateStore, StateStoreError, StateTransaction


class MonitorError(RuntimeError):
    """The selected offline state cannot be rendered safely."""


@dataclass(frozen=True, slots=True)
class WorkflowView:
    workflow_id: str
    kind: str
    component_id: str | None
    state: str
    revision: int
    next_command: str | None
    due_at: float | None


@dataclass(frozen=True, slots=True)
class AttemptView:
    attempt_id: str
    kind: str
    state: str
    workflow_id: str
    command: str
    component_id: str | None
    phase: str
    role: str | None
    slot_ordinal: int | None
    attempt_ordinal: int
    terminal_reason: str | None
    process_group_id: int | None
    started_at: float
    ended_at: float | None
    gpu_uuid: str | None
    minimum_free_memory_mib: int | None
    latest_free_memory_mib: int | None
    latest_utilization_percent: int | None
    resource_pressure_exceeded: bool | None
    failure_code: str | None = None
    returncode: int | None = None
    diagnostic_summary: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    gpu_usage: tuple[dict[str, Any], ...] = ()
    session_id: str | None = None
    context_tokens: int | None = None
    wall_seconds: float | None = None
    context_source: str | None = None
    budget_max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ExpertView:
    role: str
    slot_ordinal: int
    agent_attempts: int
    process_attempts: int
    owned_component_ids: tuple[str, ...]
    proposing_component_ids: tuple[str, ...]
    open_attempt_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComponentView:
    component_id: str
    tier: str
    owner_role: str
    proposer_roles: tuple[str, ...]
    display_ordinal: int
    workflow_id: str | None
    state: str
    phase: str
    blocked: bool
    blocked_reasons: tuple[str, ...]
    open_attempt_ids: tuple[str, ...]
    cycle_revisions: int = 0
    current_cycle: int = 0
    revision_warning: str | None = None


@dataclass(frozen=True, slots=True)
class HypothesisView:
    hypothesis_id: str
    component_id: str
    proposer_role: str
    method_family_id: str
    claim: str
    assumptions: tuple[str, ...]
    parent_hypothesis_id: str | None
    relation: str | None
    assessment_id: str | None
    evidence_ids: tuple[str, ...]
    assessment: str | None


@dataclass(frozen=True, slots=True)
class RendezvousView:
    rendezvous_id: str
    component_id: str | None
    phase: str
    kind: str
    status: str
    requester_role: str
    requester_slot: int
    target_role: str | None
    responder_role: str | None
    source_attempt_id: str
    question: str
    context_refs: tuple[str, ...]
    disposition: str | None
    summary: str | None
    supporting_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    schema_version: int
    observed_at: float
    state_root: str
    run_id: str
    status_source: str
    status: Mapping[str, Any]
    durable_open_attempts_state_root: int
    goal: Mapping[str, Any]
    experts: tuple[ExpertView, ...]
    components: tuple[ComponentView, ...]
    hypotheses: tuple[HypothesisView, ...]
    rendezvous: tuple[RendezvousView, ...]
    workflows: tuple[WorkflowView, ...]
    attempts: tuple[AttemptView, ...]
    fact_counts: Mapping[str, int]
    oracle: Mapping[str, Any]
    overview: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent,
            separators=(",", ":") if indent is None else None,
        )


def build_status_projection(
    store: StateStore,
    run_id: str,
    *,
    quiescent_for_backup: bool | None,
    attempts: Sequence[Mapping[str, Any]] | None = None,
    facts: Sequence[Mapping[str, Any]] | None = None,
    heads: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the shared Host/offline status fields without reducing lifecycle.

    ``None`` for backup quiescence is intentional in an offline reader: only a
    resident Host can also account for its in-memory handles.  The Host replaces
    ``agent_usage`` with its persisted-budget projection before responding.
    """

    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be non-empty text")
    selected_heads = tuple(heads) if heads is not None else tuple(
        store.list_workflow_heads(run_id)
    )
    run_head = next(
        (row for row in selected_heads if row["workflow_kind"] == "RUN"), None,
    )
    if run_head is None:
        return None
    experiment_heads = tuple(
        row for row in selected_heads if row["workflow_kind"] == "EXPERIMENT"
    )
    selected_attempts = tuple(attempts) if attempts is not None else tuple(
        row for row in store.list_attempts() if row["run_id"] == run_id
    )
    selected_facts = tuple(facts) if facts is not None else tuple(
        store.list_facts(run_id)
    )
    outcome_count = sum(
        row["fact_type"] == "CanonicalOutcomeAccepted" for row in selected_facts
    )
    goal_verdict = None
    required_component_count = required_assessment_count = 0
    goals = tuple(row for row in selected_facts if row["fact_type"] == "GoalDefined")
    if len(goals) == 1:
        raw_contract = goals[0]["payload"].get("research_contract")
        if isinstance(raw_contract, Mapping):
            try:
                contract = ResearchContract.from_mapping(raw_contract)
            except ValueError:
                contract = None
            if contract is not None:
                required_ids = {
                    str(component.component_id) for component in contract.components
                    if component.tier.value != "should"
                }
                assessed_ids = {
                    str(row["payload"].get("component_id"))
                    for row in selected_facts
                    if row["fact_type"] == "HypothesisAssessed"
                    if row["payload"].get("component_id") in required_ids
                }
                required_component_count = len(required_ids)
                required_assessment_count = len(assessed_ids)
    verdicts = tuple(
        row for row in selected_facts if row["fact_type"] == "GoalVerdictRecorded"
    )
    if len(verdicts) == 1 and isinstance(verdicts[0]["payload"].get("verdict"), str):
        goal_verdict = verdicts[0]["payload"]["verdict"]
    database = store.database_path
    wal = Path(f"{database}-wal")
    dialogue = []
    for row in store.inspect("SELECT * FROM workflow_human_decisions WHERE run_id=? ORDER BY request_revision,decision_id", (run_id,)):
        question = json.loads(row["request_json"])
        if question.get("kind") != "grounding_clarification":
            continue
        resolution = json.loads(row["resolution_json"]) if row["resolution_json"] is not None else {}
        dialogue.append({"decision_id": row["decision_id"], "state": row["state"], "resolution": row["resolution"],
            **{name: question[name] for name in ("ordinal", "question", "rationale", "options")},
            "answer": resolution.get("answer")})
    creation = store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? "
                             "AND workflow_id=? AND workflow_kind='RUN' AND revision=1", (run_id, run_id))
    configuration = _json_object(creation[0]["payload_json"]) if creation else {}
    return {
        "run_id": run_id,
        "run_state": run_head["state"],
        "run_revision": run_head["revision"],
        "experiment_states": [row["state"] for row in experiment_heads],
        "open_attempts": sum(row["state"] == "OPEN" for row in selected_attempts),
        "canonical_outcome_count": outcome_count,
        "goal_verdict": goal_verdict,
        "required_component_count": required_component_count,
        "required_assessment_count": required_assessment_count,
        "state_sqlite_bytes": database.stat().st_size,
        "state_sqlite_wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "wal_autocheckpoint": 1000,
        "quiescent_for_backup": quiescent_for_backup,
        "agent_usage": None,
        "resource_waits": None,
        "gpu_pool": configuration.get("gpu_pool"),
        "grounding_dialogue": dialogue,
        "shared_acceptance": _shared_acceptance_projection(run_id, selected_attempts, selected_facts),
    }


def _shared_acceptance_projection(
    run_id: str,
    attempts: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Display existing raw evidence; do not compare criteria or select a winner."""
    goals = [row["payload"].get("research_contract") for row in facts
             if row["fact_type"] == "GoalDefined"]
    if (len(goals) != 1 or not isinstance(goals[0], Mapping)
            or goals[0].get("schema_version") != 3
            or goals[0].get("goal", {}).get("evaluation") != {"mode": "grounding"}):
        return None
    completed = []
    for row in attempts:
        if (row.get("run_id") != run_id or row.get("kind") != "AGENT"
                or row.get("state") != "CLOSED" or row.get("terminal_reason") != "SUCCEEDED"):
            continue
        observation = _json_object(row.get("observation", row.get("observation_json")))
        request, response = observation.get("request"), observation.get("response")
        if not isinstance(request, Mapping) or not isinstance(response, Mapping):
            continue
        candidate = response.get("typed_candidate")
        if (request.get("command") == "GROUNDING" and isinstance(candidate, Mapping)
                and candidate.get("clarification") is None
                and isinstance(candidate.get("background"), Mapping)
                and isinstance(candidate.get("acceptance"), Mapping)):
            completed.append((row["attempt_id"], candidate["acceptance"]))
    if len(completed) != 1:
        return None
    source_attempt_id, declaration = completed[0]
    specs = {row["experiment_id"]: row["payload"] for row in facts
             if row["fact_type"] == "ExperimentPreregistered"}
    outcomes = []
    for row in facts:
        if row["fact_type"] != "CanonicalOutcomeAccepted":
            continue
        payload = row["payload"]
        binding = specs.get(row["experiment_id"], {}).get("goal_evaluation") or {}
        outcomes.append({
            "outcome_id": payload.get("outcome_id"),
            "experiment_id": row["experiment_id"],
            "component_id": payload.get("component_id"),
            "method_family_id": payload.get("method_family_id"),
            "hypothesis_id": specs.get(row["experiment_id"], {}).get("hypothesis_id"),
            "artifact_id": payload.get("artifact_id"),
            "scope": binding.get("scope"),
            "source_attempt_id": binding.get("source_attempt_id"),
            "local_outcome": payload.get("outcome"),
            "local_metric_value": payload.get("metric_value"),
            "goal_evaluation": payload.get("goal_evaluation"),
        })
    # These mappings are display copies, never aliases of the supplied fact view.
    return json.loads(json.dumps({"source_attempt_id": source_attempt_id,
        "acceptance": declaration,
        "outcomes": sorted(outcomes, key=lambda item: (str(item["experiment_id"]), str(item["outcome_id"])))}))


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _owned_gpu_memory(value: object, total_mib: int | None) -> dict[str, Any]:
    """Historical samples, rounded up to MiB; no live credit or raw process data."""
    raw = value if isinstance(value, Mapping) else {}
    count, unknown = _optional_int(raw.get("sample_count")), _optional_int(raw.get("unknown_sample_count"))
    valid_counts = count is not None and unknown is not None and 0 <= unknown <= count < 2**53
    def sample(name: str) -> tuple[int | None, float | None]:
        item = raw.get(name)
        if not valid_counts or count == 0 or not isinstance(item, Mapping): return None, None
        observed = item.get("observed_at")
        stamp = float(observed) if type(observed) in {int, float} and 0 <= observed < 2**53 else None
        size = _optional_int(item.get("resident_bytes"))
        if (unknown == count or item.get("complete") is not True or item.get("reason") is not None
                or size is None or not 0 <= size < 2**53 or total_mib is None or total_mib <= 0 or size > total_mib * 1024**2):
            return None, stamp
        return (size + 1024**2 - 1) // 1024**2, stamp
    latest, latest_at = sample("latest"); peak, peak_at = sample("peak")
    return {"sample_count": count if valid_counts else None, "unknown_sample_count": unknown if valid_counts else None,
        "complete_sample_count": count - unknown if valid_counts else None,
        "coverage": "complete" if valid_counts and count > 0 and unknown == 0 and latest is not None and peak is not None else "unknown",
        "latest_sample_memory_mib": latest, "sampled_peak_memory_mib": peak,
        "latest_observed_at": latest_at, "peak_observed_at": peak_at}


def _gpu_usage(value: object) -> tuple[dict[str, Any], ...]:
    """Allowlisted device observations, without process ownership or secrets."""
    items = (value,) if isinstance(value, Mapping) else value if isinstance(value, list) else ()
    result = []
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("gpu_uuid"), str) or not item["gpu_uuid"]:
            continue
        device = {"gpu_uuid": item["gpu_uuid"]}
        for name in ("total_memory_mib", "initial_free_memory_mib", "minimum_free_memory_mib",
                     "latest_free_memory_mib", "latest_used_memory_mib", "latest_utilization_percent", "sample_count"):
            number = _optional_int(item.get(name) if name == "sample_count" else item.get(f"gpu_{name}", item.get(name)))
            device[name] = number if number is not None and number >= 0 and (name != "latest_utilization_percent" or number <= 100) else None
        device["pressure_exceeded"] = item.get("pressure_exceeded") if isinstance(item.get("pressure_exceeded"), bool) else None
        if "owned_memory" in item:
            device["owned_memory"] = _owned_gpu_memory(item["owned_memory"], device["total_memory_mib"])
        result.append(device)
    return tuple(sorted(result, key=lambda item: item["gpu_uuid"]))


def _fact_payload(row: Mapping[str, Any], fact_type: str) -> Mapping[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        raise MonitorError(f"{fact_type} has no typed payload")
    return payload


def _payload_text(payload: Mapping[str, Any], field: str, fact_type: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise MonitorError(f"{fact_type} has invalid {field}")
    return value


def _payload_text_tuple(
    payload: Mapping[str, Any], field: str, fact_type: str,
) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or item != item.strip()
        for item in value
    ):
        raise MonitorError(f"{fact_type} has invalid {field}")
    return tuple(value)


def _indexed_payload_text(
    row: Mapping[str, Any], payload: Mapping[str, Any], field: str, fact_type: str,
) -> str:
    value = _payload_text(payload, field, fact_type)
    if row.get(field) != value:
        raise MonitorError(f"{fact_type} has inconsistent {field}")
    return value


def _research_contract(goal: Mapping[str, Any]) -> ResearchContract:
    raw = goal.get("research_contract")
    if not isinstance(raw, Mapping):
        raise MonitorError("GoalDefined lacks its component research_contract")
    try:
        return ResearchContract.from_mapping(raw)
    except ValueError as exc:
        raise MonitorError(f"GoalDefined has an invalid research_contract: {exc}") from exc


def _transition_rows(
    reader: StateTransaction, run_id: str,
) -> tuple[Mapping[str, Any], ...]:
    return tuple({**dict(row), "payload": _json_object(row["payload_json"])} for row in reader.execute(
        "SELECT t.*,c.command_type "
        "FROM workflow_transitions t JOIN workflow_commands c "
        "ON c.command_id=t.command_id WHERE t.run_id=? ORDER BY t.rowid",
        (run_id,),
    ).fetchall())


def _transition_context(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, str], dict[str, int], dict[str, str], dict[str, tuple[str, ...]],
]:
    workflow_components: dict[str, str] = {}
    experiment_ordinals: dict[str, int] = {}
    rendezvous_phases: dict[str, str] = {}
    pending: dict[str, tuple[str, str]] = {}
    for row in rows:
        payload = _json_object(row.get("payload", row["payload_json"]))
        component_id = payload.get("component_id")
        if isinstance(component_id, str) and component_id:
            workflow_components[str(row["workflow_id"])] = component_id
        experiment_ordinal = payload.get("experiment_ordinal", 1)
        if (
            row["workflow_kind"] == "EXPERIMENT"
            and int(row["revision"]) == 1
            and isinstance(experiment_ordinal, int)
            and not isinstance(experiment_ordinal, bool)
            and experiment_ordinal > 0
        ):
            experiment_ordinals[str(row["workflow_id"])] = experiment_ordinal
        origin = payload.get("rendezvous_origin")
        if isinstance(origin, Mapping):
            rendezvous_id = origin.get("rendezvous_id")
            phase = origin.get("phase")
            origin_component = origin.get("component_id")
            if all(isinstance(value, str) and value for value in (
                rendezvous_id, phase, origin_component,
            )):
                rendezvous_phases[rendezvous_id] = phase
                pending[rendezvous_id] = (origin_component, phase)
        if row["command_type"] == "RESOLVE_RENDEZVOUS":
            rendezvous_id = payload.get("rendezvous_id")
            if isinstance(rendezvous_id, str):
                pending.pop(rendezvous_id, None)
    pending_by_component: dict[str, list[str]] = {}
    for component_id, phase in pending.values():
        pending_by_component.setdefault(component_id, []).append(phase)
    return (
        workflow_components,
        experiment_ordinals,
        rendezvous_phases,
        {key: tuple(dict.fromkeys(value)) for key, value in pending_by_component.items()},
    )


def _rendezvous_views(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[RendezvousView, ...]:
    records = tuple(
        (str(row["command_type"]), _json_object(row.get("payload", row["payload_json"])))
        for row in rows
    )
    try:
        origins, requests, replies = derive_rendezvous_records(records)
    except ValueError as exc:
        raise MonitorError(f"invalid durable rendezvous projection: {exc}") from exc
    views: list[RendezvousView] = []
    for origin in origins:
        request = requests.get(origin.rendezvous_id)
        reply = replies.get(origin.rendezvous_id)
        status = (
            "RESOLVED"
            if reply is not None
            else "OPEN"
            if request is not None
            else "ORIGINATED"
        )
        views.append(RendezvousView(
            rendezvous_id=origin.rendezvous_id,
            component_id=origin.component_id,
            phase=origin.phase,
            kind=(request.kind if request is not None else origin.signal.kind).value,
            status=status,
            requester_role=origin.requester_role,
            requester_slot=origin.requester_slot,
            target_role=(request.target_role if request is not None else origin.target_role),
            responder_role=None if reply is None else reply.responder_role,
            source_attempt_id=origin.source_attempt_id,
            question=(request.question if request is not None else origin.signal.question),
            context_refs=(request.context_refs if request is not None else origin.signal.context_refs),
            disposition=None if reply is None else reply.disposition.value,
            summary=None if reply is None else reply.summary,
            supporting_refs=() if reply is None else reply.supporting_refs,
        ))
    return tuple(views)


def _attempt_phase(
    kind: str,
    command: str,
    transition: Mapping[str, Any],
    rendezvous_phases: Mapping[str, str],
    *,
    request: Mapping[str, Any] | None = None,
) -> str:
    if kind == "AGENT" and request:
        try:
            parsed_request = AgentRequest.from_dict(request)
        except AgentACIError:
            parsed_request = None
        if (
            parsed_request is not None
            and parsed_request.schema_version in {8, 9, EXPERT_LOOP_ACI_SCHEMA_VERSION}
            and isinstance(parsed_request.task_context, ResearchNodeTask)
        ):
            return (
                f"{parsed_request.command.value.lower()}/"
                f"{parsed_request.task_context.key.subkind.value.lower()}"
            )
        if (
            parsed_request is not None
            and parsed_request.schema_version == EXPERT_LOOP_ACI_SCHEMA_VERSION
            and isinstance(parsed_request.task_context, AuxiliaryTask)
            and parsed_request.task_context.kind
            is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
        ):
            return "grounding/query_plan"
    if kind == "PROCESS":
        if command == "VALIDATE_SPEC_CANDIDATE":
            return "validation"
        if command == "EXECUTE":
            return "execute"
        return command.lower()
    if command == "PROPOSE_HYPOTHESIS":
        return "proposal"
    if command == "DRAFT_EXPERIMENT_SPEC":
        return "specification"
    if command == "ANSWER_RENDEZVOUS":
        rendezvous_id = transition.get("rendezvous_id")
        if isinstance(rendezvous_id, str):
            return rendezvous_phases.get(rendezvous_id, "rendezvous")
        return "rendezvous"
    return command.lower()


def _attempt_reported_tokens(observation: Mapping[str, Any]) -> int | None:
    diagnostics = observation.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    for key in ("provider_accounting", "provider_reported_lower_bound"):
        report = diagnostics.get(key)
        if not isinstance(report, Mapping):
            continue
        usage = report.get("token_usage")
        if not isinstance(usage, Mapping):
            continue
        total = usage.get("total_tokens")
        if type(total) is int and total >= 0:
            return total
    return None


_TRANSCRIPT_TAIL_BYTES = 256 * 1024


def _session_transcript_path(
    root: Path,
    run_id: str,
    observation: Mapping[str, Any],
) -> Path | None:
    """Locate the live session transcript only through recorded identities."""
    request = observation.get("request")
    session = observation.get("session")
    if not isinstance(session, Mapping):
        session = observation.get("session_binding")
    if not isinstance(request, Mapping) or not isinstance(session, Mapping):
        return None
    fields = tuple(
        observation_value
        for observation_value in (
            request.get("role"), request.get("component_id"),
            session.get("scope"), session.get("session_id"),
        )
    )
    cycle = session.get("cycle")
    if not all(isinstance(value, str) and value for value in fields) or not isinstance(cycle, int):
        return None
    role, component_id, scope, session_id = fields
    candidate = (
        root / run_id / "sessions" / role / f"c{cycle}"
        / component_id / scope / "projects" / f"{session_id}.jsonl"
    )
    try:
        resolved = candidate.resolve()
        confined = resolved.is_relative_to(root.resolve())
    except OSError:
        return None
    if not confined or not resolved.is_file():
        return None
    return resolved


def _transcript_context_tokens(transcript: Path) -> int | None:
    """Last model-turn input total from the live transcript tail, if readable."""
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as handle:
            if size > _TRANSCRIPT_TAIL_BYTES:
                handle.seek(-_TRANSCRIPT_TAIL_BYTES, 2)
            content = handle.read()
    except OSError:
        return None
    lines = content.splitlines()
    if size > _TRANSCRIPT_TAIL_BYTES and lines:
        lines = lines[1:]
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        message = record.get("message") if isinstance(record, dict) else None
        if not isinstance(message, Mapping):
            continue
        usage = message.get("usage")
        if not isinstance(usage, Mapping):
            continue
        total, found = 0, False
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                total += value
                found = True
        if found:
            return total
    return None


def _with_live_attempt_context(
    root: Path,
    run_id: str,
    attempts: tuple[AttemptView, ...],
    observations: Mapping[str, Mapping[str, Any]],
) -> tuple[AttemptView, ...]:
    rendered = []
    for attempt in attempts:
        if attempt.kind != "AGENT" or attempt.state != "OPEN":
            rendered.append(attempt)
            continue
        observation = observations.get(attempt.attempt_id)
        transcript = None if observation is None else _session_transcript_path(root, run_id, observation)
        tokens = None if transcript is None else _transcript_context_tokens(transcript)
        rendered.append(
            attempt if tokens is None
            else replace(attempt, context_tokens=tokens, context_source="transcript")
        )
    return tuple(rendered)


def _attempt_views(
    reader: StateTransaction,
    run_id: str,
    roles: tuple[str, ...],
    workflow_components: Mapping[str, str],
    rendezvous_phases: Mapping[str, str],
    scientific_execution_identities: frozenset[tuple[str, str]],
    observations: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[tuple[AttemptView, ...], frozenset[str]]:
    rows = tuple(dict(row) for row in reader.execute(
        "SELECT a.*,c.command_type,t.payload_json AS transition_payload_json "
        "FROM execution_attempts a "
        "JOIN workflow_commands c ON c.command_id=a.command_id "
        "LEFT JOIN workflow_transitions t "
        "ON t.workflow_id=a.workflow_id AND t.command_id=a.command_id "
        "WHERE a.run_id=? ORDER BY a.started_at,a.attempt_id",
        (run_id,),
    ).fetchall())
    rendered: list[AttemptView] = []
    scientific_process_attempt_ids: set[str] = set()
    session_context: dict[str, int] = {}
    for row in rows:
        transition = _json_object(row["transition_payload_json"])
        observation = (observations[row["attempt_id"]] if observations is not None
            else _json_object(row["observation_json"]))
        resource_usage = observation.get("resource_usage")
        gpu_usage = _gpu_usage(resource_usage)
        device_pressures = [item["pressure_exceeded"] for item in gpu_usage if item["pressure_exceeded"] is not None]
        resource_usage = dict(resource_usage) if isinstance(resource_usage, Mapping) else {}
        request = observation.get("request")
        request = dict(request) if isinstance(request, Mapping) else {}
        role_value = request.get("role", transition.get("role"))
        role = role_value if isinstance(role_value, str) and role_value else None
        slot = roles.index(role) + 1 if role in roles else None
        workflow_id = str(row["workflow_id"])
        component_value = request.get(
            "component_id", transition.get("component_id", workflow_components.get(workflow_id)),
        )
        component_id = (
            component_value
            if isinstance(component_value, str) and component_value
            else None
        )
        workflow_command = str(row["command_type"])
        command = (
            workflow_command
            if row["kind"] == "PROCESS"
            else str(request.get("command", workflow_command))
        )
        attempt_id = str(row["attempt_id"])
        session = observation.get("session")
        session_value = session.get("session_id") if isinstance(session, Mapping) else None
        session_id = (session_value if isinstance(session_value, str) and session_value
                      and row["kind"] == "AGENT" else None)
        # Latest completed call in the same session bounds the context the next
        # call carries; an OPEN attempt's own usage is unknown until close.
        reported_tokens = _attempt_reported_tokens(observation) if session_id is not None else None
        if row["state"] == "CLOSED" and reported_tokens is not None and session_id is not None:
            context_tokens = reported_tokens
            session_context[session_id] = reported_tokens
        else:
            context_tokens = session_context.get(session_id) if session_id is not None else None
        budget = request.get("budget")
        wall_value = budget.get("wall_seconds") if isinstance(budget, Mapping) else None
        wall_seconds = (float(wall_value) if row["kind"] == "AGENT"
                        and isinstance(wall_value, (int, float)) and not isinstance(wall_value, bool)
                        and 0 < float(wall_value) < float("inf") else None)
        max_tokens_value = budget.get("max_tokens") if isinstance(budget, Mapping) else None
        budget_max_tokens = (max_tokens_value if row["kind"] == "AGENT"
                             and type(max_tokens_value) is int and max_tokens_value > 0 else None)
        if (
            row["kind"] == "PROCESS"
            and workflow_command == "EXECUTE"
            and (str(row["experiment_id"]), str(row["execution_id"]))
            in scientific_execution_identities
        ):
            scientific_process_attempt_ids.add(attempt_id)
        rendered.append(AttemptView(
            attempt_id=attempt_id,
            kind=str(row["kind"]),
            state=str(row["state"]),
            workflow_id=workflow_id,
            command=command,
            component_id=component_id,
            phase=_attempt_phase(
                str(row["kind"]), command, transition, rendezvous_phases,
                request=request,
            ),
            role=role,
            slot_ordinal=slot,
            attempt_ordinal=int(row["attempt_ordinal"]),
            terminal_reason=None if row["terminal_reason"] is None else str(row["terminal_reason"]),
            process_group_id=_optional_int(row["process_group_id"]),
            started_at=float(row["started_at"]),
            ended_at=None if row["ended_at"] is None else float(row["ended_at"]),
            gpu_uuid=(
                resource_usage.get("gpu_uuid")
                if isinstance(resource_usage.get("gpu_uuid"), str)
                else None
            ),
            minimum_free_memory_mib=_optional_int(
                resource_usage.get(
                    "gpu_minimum_free_memory_mib",
                    resource_usage.get("minimum_free_memory_mib"),
                )
            ),
            latest_free_memory_mib=_optional_int(
                resource_usage.get(
                    "gpu_latest_free_memory_mib",
                    resource_usage.get("latest_free_memory_mib"),
                )
            ),
            latest_utilization_percent=_optional_int(
                resource_usage.get(
                    "gpu_latest_utilization_percent",
                    resource_usage.get("latest_utilization_percent"),
                )
            ),
            resource_pressure_exceeded=(
                resource_usage.get("pressure_exceeded")
                if isinstance(resource_usage.get("pressure_exceeded"), bool)
                else any(device_pressures) if device_pressures else None
            ),
            gpu_usage=gpu_usage,
            failure_code=(
                observation.get("failure_code")
                if isinstance(observation.get("failure_code"), str)
                else None
            ),
            returncode=_optional_int(observation.get("returncode")),
            diagnostic_summary=diagnostic_summary(observation),
            stdout_path=(
                observation.get("stdout_path")
                if isinstance(observation.get("stdout_path"), str)
                else None
            ),
            stderr_path=(
                observation.get("stderr_path")
                if isinstance(observation.get("stderr_path"), str)
                else None
            ),
            session_id=session_id,
            context_tokens=context_tokens,
            wall_seconds=wall_seconds,
            budget_max_tokens=budget_max_tokens,
        ))
    return tuple(rendered), frozenset(scientific_process_attempt_ids)


def _expert_views(
    contract: ResearchContract,
    attempts: tuple[AttemptView, ...],
    scientific_process_attempt_ids: frozenset[str],
) -> tuple[ExpertView, ...]:
    result: list[ExpertView] = []
    for slot, role in enumerate(contract.role_ids, 1):
        owned = tuple(item for item in attempts if item.role == role)
        result.append(ExpertView(
            role=role,
            slot_ordinal=slot,
            agent_attempts=sum(item.kind == "AGENT" for item in owned),
            process_attempts=sum(
                item.attempt_id in scientific_process_attempt_ids for item in owned
            ),
            owned_component_ids=tuple(
                str(item.component_id)
                for item in contract.components if item.owner_role == role
            ),
            proposing_component_ids=tuple(
                str(item.component_id)
                for item in contract.components if role in item.proposer_roles
            ),
            open_attempt_ids=tuple(item.attempt_id for item in owned if item.state == "OPEN"),
        ))
    return tuple(result)


def _component_phase(state: str, open_attempts: Sequence[AttemptView]) -> str:
    if open_attempts:
        phases = tuple(dict.fromkeys(item.phase for item in open_attempts))
        return "+".join(phases)
    return {
        "PENDING": "proposal",
        "DRAFTED": "specification",
        "PREREGISTERED": "prepare",
        "READY": "execute",
        "EXECUTING": "execute",
        "EVALUATING": "analyze",
        "TERMINAL": "terminal",
    }.get(state, state.lower())


def _component_dependency_evidence(
    head_by_component: Mapping[str, Mapping[str, Any]],
    assessments: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
) -> tuple[frozenset[str], frozenset[str], Mapping[str, str]]:
    assessment_by_component: dict[str, object] = {}
    for row in assessments:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        component_id = payload.get("component_id")
        if isinstance(component_id, str):
            assessment_by_component[component_id] = payload.get("assessment")
    supported_components = frozenset(
        component_id
        for component_id, assessment in assessment_by_component.items()
        if assessment == HypothesisAssessment.SUPPORTED.value
    )

    outcome_by_experiment: dict[str, Mapping[str, Any]] = {}
    for row in outcomes:
        payload = row.get("payload")
        experiment_id = row.get("experiment_id")
        if isinstance(payload, Mapping) and isinstance(experiment_id, str):
            outcome_by_experiment[experiment_id] = payload
    comparable_outcomes = {
        ScientificOutcome.SUCCESS.value,
        ScientificOutcome.CLEAN_REFUTATION.value,
    }
    comparable_components = frozenset(
        component_id
        for component_id, head in head_by_component.items()
        if (
            (payload := outcome_by_experiment.get(str(head["workflow_id"])))
            is not None
            and payload.get("component_id") == component_id
            and payload.get("outcome") in comparable_outcomes
        )
    )
    family_by_component = {
        component_id: str(payload["method_family_id"])
        for component_id, head in head_by_component.items()
        if (
            (payload := outcome_by_experiment.get(str(head["workflow_id"])))
            is not None
            and isinstance(payload.get("method_family_id"), str)
            and payload["method_family_id"]
        )
    }
    return supported_components, comparable_components, family_by_component


def _cycle_revision_counts(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[int, int]]:
    """Map each component to (latest expert cycle, revisions inside that cycle).

    Revisions are the experiment creations of the latest cycle minus one;
    legacy creation payloads without ``expert_cycle`` count as one cycle.
    """

    cycles_by_component: dict[str, list[int]] = {}
    for row in rows:
        if row["workflow_kind"] != "EXPERIMENT" or int(row["revision"]) != 1:
            continue
        payload = _json_object(row.get("payload", row["payload_json"]))
        component_id = payload.get("component_id")
        if not isinstance(component_id, str) or not component_id:
            continue
        cycle = payload.get("expert_cycle", 1)
        if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 1:
            raise MonitorError("experiment creation has an invalid expert_cycle")
        cycles_by_component.setdefault(component_id, []).append(cycle)
    counts: dict[str, tuple[int, int]] = {}
    for component_id, cycles in cycles_by_component.items():
        latest = max(cycles)
        counts[component_id] = (latest, cycles.count(latest) - 1)
    return counts


def _revision_warning_level(cycle_revisions: int) -> str | None:
    if cycle_revisions > _REVISION_BREAKER_MAX:
        return "tripped"
    if cycle_revisions > _REVISION_BREAKER_SEVERE:
        return "severe"
    if cycle_revisions > _REVISION_BREAKER_WARN:
        return "general"
    return None


def _component_views(
    contract: ResearchContract,
    heads: Sequence[Mapping[str, Any]],
    workflow_components: Mapping[str, str],
    pending_rendezvous: Mapping[str, tuple[str, ...]],
    attempts: tuple[AttemptView, ...],
    assessments: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    expert_lineage_terminated: frozenset[str] | None = None,
    experiment_ordinals: Mapping[str, int] | None = None,
    cycle_revision_counts: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[ComponentView, ...]:
    ordinal_by_workflow = experiment_ordinals or {}
    revision_counts = cycle_revision_counts or {}
    ranked_heads: dict[str, tuple[int, str, Mapping[str, Any]]] = {}
    for row in heads:
        workflow_id = str(row["workflow_id"])
        if (
            row["workflow_kind"] != "EXPERIMENT"
            or workflow_id not in workflow_components
        ):
            continue
        component_id = workflow_components[workflow_id]
        candidate = (ordinal_by_workflow.get(workflow_id, 1), workflow_id, row)
        prior = ranked_heads.get(component_id)
        if prior is None or candidate[:2] > prior[:2]:
            ranked_heads[component_id] = candidate
    head_by_component = {
        component_id: candidate[2]
        for component_id, candidate in ranked_heads.items()
    }
    (
        supported_components,
        comparable_components,
        family_by_component,
    ) = _component_dependency_evidence(head_by_component, assessments, outcomes)
    result: list[ComponentView] = []
    for ordinal, component in enumerate(contract.components, 1):
        component_id = str(component.component_id)
        head = head_by_component.get(component_id)
        workflow_id = None if head is None else str(head["workflow_id"])
        state = "PENDING" if head is None else str(head["state"])
        open_attempts = tuple(
            item for item in attempts
            if item.component_id == component_id and item.state == "OPEN"
        )
        input_dependencies = {
            str(item.produced_by_component) for item in component.required_inputs
        }
        missing_dependencies = tuple(
            str(item) for item in component.dependencies
            if str(item) not in (
                comparable_components
                if str(item) in input_dependencies
                else supported_components
            )
        )
        unterminated_dependencies = (
            ()
            if expert_lineage_terminated is None
            else tuple(
                str(item) for item in component.dependencies
                if str(item) not in expert_lineage_terminated
            )
        )
        input_families = {
            family_by_component[item]
            for item in input_dependencies if item in family_by_component
        }
        input_family_conflict = (
            expert_lineage_terminated is not None and len(input_families) > 1
        )
        pending_phases = pending_rendezvous.get(component_id, ())
        reasons = tuple(
            ([f"dependencies:{','.join(missing_dependencies)}"] if missing_dependencies else [])
            + ([f"expert-lineage:{','.join(unterminated_dependencies)}"]
               if unterminated_dependencies else [])
            + (["required-input-family:conflict"] if input_family_conflict else [])
            + [f"rendezvous:{phase}" for phase in pending_phases]
        )
        phase = pending_phases[0] if pending_phases else _component_phase(state, open_attempts)
        current_cycle, cycle_revisions = revision_counts.get(component_id, (0, 0))
        result.append(ComponentView(
            component_id=component_id,
            tier=component.tier.value,
            owner_role=component.owner_role,
            proposer_roles=component.proposer_roles,
            display_ordinal=ordinal,
            workflow_id=workflow_id,
            state=state,
            phase=phase,
            blocked=bool(reasons),
            blocked_reasons=reasons,
            open_attempt_ids=tuple(item.attempt_id for item in open_attempts),
            cycle_revisions=cycle_revisions,
            current_cycle=current_cycle,
            revision_warning=_revision_warning_level(cycle_revisions),
        ))
    return tuple(result)


def _hypothesis_views(
    proposed: Sequence[Mapping[str, Any]],
    assessed: Sequence[Mapping[str, Any]],
) -> tuple[HypothesisView, ...]:
    assessment_by_hypothesis: dict[
        str, tuple[str, str, str, tuple[str, ...], str]
    ] = {}
    for row in assessed:
        fact_type = "HypothesisAssessed"
        payload = _fact_payload(row, fact_type)
        hypothesis_id = _indexed_payload_text(
            row, payload, "hypothesis_id", fact_type,
        )
        assessment_id = _payload_text(payload, "assessment_id", fact_type)
        if row.get("subject_id") != assessment_id:
            raise MonitorError(f"{fact_type} has inconsistent assessment_id")
        component_id = _payload_text(payload, "component_id", fact_type)
        method_family_id = _payload_text(payload, "method_family_id", fact_type)
        evidence_ids = _payload_text_tuple(payload, "evidence_ids", fact_type)
        raw_assessment = _payload_text(payload, "assessment", fact_type)
        try:
            assessment = HypothesisAssessment(raw_assessment).value
        except ValueError as exc:
            raise MonitorError(f"{fact_type} has invalid assessment") from exc
        assessment_by_hypothesis.setdefault(hypothesis_id, (
            assessment_id,
            component_id,
            method_family_id,
            evidence_ids,
            assessment,
        ))

    result: list[HypothesisView] = []
    proposed_ids: set[str] = set()
    for row in proposed:
        fact_type = "HypothesisProposed"
        payload = _fact_payload(row, fact_type)
        hypothesis_id = _indexed_payload_text(
            row, payload, "hypothesis_id", fact_type,
        )
        if row.get("subject_id") != hypothesis_id:
            raise MonitorError(f"{fact_type} has inconsistent hypothesis_id")
        if hypothesis_id in proposed_ids:
            raise MonitorError(f"duplicate HypothesisProposed {hypothesis_id!r}")
        proposed_ids.add(hypothesis_id)
        component_id = _payload_text(payload, "component_id", fact_type)
        method_family_id = _payload_text(payload, "method_family_id", fact_type)
        parent = payload.get("parent_hypothesis_id")
        relation = payload.get("relation")
        if parent is None and relation is None:
            parent_hypothesis_id = None
            relation_value = None
        elif isinstance(parent, str) and isinstance(relation, str):
            parent_hypothesis_id = _payload_text(
                payload, "parent_hypothesis_id", fact_type,
            )
            relation_value = _payload_text(payload, "relation", fact_type)
        else:
            raise MonitorError(
                f"{fact_type} has incomplete parent_hypothesis_id/relation"
            )

        assessment = assessment_by_hypothesis.pop(hypothesis_id, None)
        if assessment is None:
            assessment_id = None
            evidence_ids: tuple[str, ...] = ()
            assessment_value = None
        else:
            (
                assessment_id,
                assessed_component_id,
                assessed_method_family_id,
                evidence_ids,
                assessment_value,
            ) = assessment
            if (
                assessed_component_id != component_id
                or assessed_method_family_id != method_family_id
            ):
                raise MonitorError(
                    "HypothesisAssessed component/method-family lineage differs "
                    f"from HypothesisProposed {hypothesis_id!r}"
                )
        result.append(HypothesisView(
            hypothesis_id=hypothesis_id,
            component_id=component_id,
            proposer_role=_payload_text(payload, "proposer_role", fact_type),
            method_family_id=method_family_id,
            claim=_payload_text(payload, "claim", fact_type),
            assumptions=_payload_text_tuple(payload, "assumptions", fact_type),
            parent_hypothesis_id=parent_hypothesis_id,
            relation=relation_value,
            assessment_id=assessment_id,
            evidence_ids=evidence_ids,
            assessment=assessment_value,
        ))
    if assessment_by_hypothesis:
        missing = next(iter(assessment_by_hypothesis))
        raise MonitorError(
            f"HypothesisAssessed cites missing HypothesisProposed {missing!r}"
        )
    return tuple(result)


def _expert_lineage_termination_components(
    contract: ResearchContract,
    attempts: Sequence[Mapping[str, Any]],
    hypotheses: Sequence[Mapping[str, Any]],
    *,
    research_contract_version: str,
) -> frozenset[str] | None:
    if research_contract_version not in {"hypoloom-research-v7", "hypoloom-research-v8", "hypoloom-research-v9", "hypoloom-research-v10", "hypoloom-research-v11", "hypoloom-research-v12"}:
        return None
    controls = contract.document.get("methodology", {}).get("controls", {})
    if not (
        isinstance(controls, Mapping)
        and controls.get("research_loop") == EXPERT_LOOP_RESEARCH_LOOP
    ):
        return None
    current_cycles: dict[tuple[str, str], int] = {}
    for row in hypotheses:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        component_id = payload.get("component_id")
        role = payload.get("proposer_role")
        if isinstance(component_id, str) and isinstance(role, str):
            key = (component_id, role)
            current_cycles[key] = current_cycles.get(key, 0) + 1
    terminated: set[str] = set()
    for row in attempts:
        if row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED":
            continue
        observation = _json_object(row.get("observation", row["observation_json"]))
        request_value = observation.get("request")
        response_value = observation.get("response")
        if not isinstance(request_value, Mapping) or not isinstance(
            response_value, Mapping
        ):
            continue
        # Only a current hypothesis-update response can terminate this lineage.
        # Filter on its task identity before decoding unrelated embedded inputs;
        # relevant entries still pass the complete existing postflight validator.
        task_value = request_value.get("task_context")
        key = task_value.get("key") if isinstance(task_value, Mapping) else None
        component_id, role = request_value.get("component_id"), request_value.get("role")
        if (not isinstance(key, Mapping)
                or not isinstance(component_id, str) or not isinstance(role, str)
                or key.get("node_kind") != ResearchNodeKind.HYPOTHESIS_UPDATE.value
                or key.get("cycle") != current_cycles.get((component_id, role))):
            continue
        try:
            request = AgentRequest.from_dict(request_value)
            response = AgentResponse.from_dict(response_value)
            validated = validate_postflight(request, response)
        except (AgentACIError, TypeError, ValueError):
            continue
        task = request.task_context
        candidate = validated.candidate
        if (
            isinstance(task, ResearchNodeTask)
            and task.key.node_kind is ResearchNodeKind.HYPOTHESIS_UPDATE
            and task.key.cycle
            == current_cycles.get((request.component_id, request.role))
            and candidate.update_disposition
            is HypothesisUpdateDisposition.TERMINATE_LINEAGE
        ):
            terminated.add(request.component_id)
    return frozenset(terminated)


def build_monitor_snapshot(
    state_root: str | Path,
    run_id: str,
    *,
    _connection: sqlite3.Connection | None = None,
    _oracle: Mapping[str, Any] | None = None,
    _include_overview: bool = True,
    _attempts: Sequence[Mapping[str, Any]] | None = None,
) -> MonitorSnapshot:
    """Build an offline polling observation without a socket, Host, or write.

    Facts, heads, transitions and Attempts share one SQLite read transaction so
    the durable display projection cannot straddle a Host commit.  The Oracle is
    still an independent read-only audit rather than part of lifecycle authority.
    Offline exporters may supply their existing read-only transaction through
    ``_connection`` to read additional display fields from the same observation.
    Polling servers may supply their separate audit observation via ``_oracle``;
    omitting it always runs the independent Oracle synchronously.
    Host research files set ``_include_overview=False`` to avoid replaying historical
    call diagnostics that are not part of their research context.
    """

    root = Path(state_root).expanduser().resolve()
    store = StateStore(root)
    try:
        with (
            store.readonly_connection()
            if _connection is None else nullcontext(_connection)
        ) as connection:
            if _connection is None:
                connection.execute("BEGIN")
            elif not connection.in_transaction or not connection.execute(
                "PRAGMA query_only"
            ).fetchone()[0]:
                raise MonitorError("snapshot requires an active read-only transaction")
            reader = StateTransaction(connection)
            run = reader.get_run(run_id)
            if run is None:
                raise MonitorError(
                    f"run not found in {root / 'state.sqlite'}: {run_id!r}"
                )
            raw_attempts = tuple(_attempts) if _attempts is not None else tuple(
                {**row, "observation": _json_object(row["observation_json"])}
                for row in reader.list_attempts(run_id=run_id)
            )
            research_facts = reader.list_facts(run_id)
            heads = reader.list_workflow_heads(run_id)
            status = build_status_projection(
                store,
                run_id,
                quiescent_for_backup=None,
                attempts=raw_attempts,
                facts=research_facts,
                heads=heads,
            )
            if status is None:
                raise MonitorError(f"run has no RunWorkflow head: {run_id!r}")
            facts_by_type: dict[str, list[Mapping[str, Any]]] = {}
            for row in research_facts:
                facts_by_type.setdefault(str(row["fact_type"]), []).append(row)
            goal_rows = facts_by_type.get("GoalDefined", ())
            goal = dict(goal_rows[0]["payload"]) if goal_rows else {}
            contract = _research_contract(goal)
            transition_rows = _transition_rows(reader, run_id)
            (
                workflow_components,
                experiment_ordinals,
                rendezvous_phases,
                pending_rendezvous,
            ) = (
                _transition_context(transition_rows)
            )
            scientific_execution_identities = frozenset(
                (str(row["experiment_id"]), str(row["execution_id"]))
                for row in facts_by_type.get("ExperimentPreregistered", ())
            )
            attempt_observations = {row["attempt_id"]: row["observation"] for row in raw_attempts}
            attempts, scientific_process_attempt_ids = _attempt_views(
                reader,
                run_id,
                contract.role_ids,
                workflow_components,
                rendezvous_phases,
                scientific_execution_identities,
                attempt_observations,
            )
            attempts = _with_live_attempt_context(root, run_id, attempts, attempt_observations)
            workflows = tuple(WorkflowView(
                workflow_id=str(row["workflow_id"]),
                kind=str(row["workflow_kind"]),
                component_id=workflow_components.get(str(row["workflow_id"])),
                state=str(row["state"]),
                revision=int(row["revision"]),
                next_command=(
                    None if row["next_command"] is None else str(row["next_command"])
                ),
                due_at=None if row["due_at"] is None else float(row["due_at"]),
            ) for row in heads)
            fact_counts = {
                fact_type: len(rows)
                for fact_type, rows in sorted(facts_by_type.items())
            }
            components = _component_views(
                contract, heads, workflow_components, pending_rendezvous, attempts,
                facts_by_type.get("HypothesisAssessed", ()),
                facts_by_type.get("CanonicalOutcomeAccepted", ()),
                _expert_lineage_termination_components(
                    contract,
                    raw_attempts,
                    facts_by_type.get("HypothesisProposed", ()),
                    research_contract_version=str(
                        run["research_contract_version"]
                    ),
                ),
                experiment_ordinals=experiment_ordinals,
                cycle_revision_counts=_cycle_revision_counts(transition_rows),
            )
            hypotheses = _hypothesis_views(
                facts_by_type.get("HypothesisProposed", ()),
                facts_by_type.get("HypothesisAssessed", ()),
            )
            rendezvous = _rendezvous_views(transition_rows)
            open_root_row = reader.execute(
                "SELECT COUNT(*) AS count FROM execution_attempts WHERE state='OPEN'"
            ).fetchone()
            assert open_root_row is not None
            open_root = int(open_root_row["count"])
    except MonitorError:
        raise
    except (OSError, sqlite3.Error, StateStoreError, ValueError) as exc:
        raise MonitorError(f"cannot read Hypoloom state at {root}: {exc}") from exc

    # The numerical cockpit and Web use the same rows, not a bounded event tail
    # or a second counter store. Git detail is read only at recorded revisions.
    from .observation_api import product_graph
    from .observer_summary import build_run_summary
    observed_at = time.time()
    experts = _expert_views(contract, attempts, scientific_process_attempt_ids)
    overview = build_run_summary(root, run_id, facts=research_facts,
        transitions=transition_rows, attempts=raw_attempts,
        experts=[asdict(item) for item in experts],
        graph=product_graph([asdict(item) for item in hypotheses], transition_rows),
        observed_at=observed_at, created_at=run["created_at"]) if _include_overview else {}
    oracle = (Oracle(root / "state.sqlite", root / "artifacts").audit(run_id).to_dict()
        if _oracle is None else dict(_oracle))
    return MonitorSnapshot(
        schema_version=2,
        observed_at=observed_at,
        state_root=str(root),
        run_id=run_id,
        status_source="offline-state-store",
        status=status,
        durable_open_attempts_state_root=open_root,
        goal=goal,
        experts=experts,
        components=components,
        hypotheses=hypotheses,
        rendezvous=rendezvous,
        workflows=workflows,
        attempts=attempts,
        fact_counts=fact_counts,
        oracle=oracle,
        overview=overview,
    )


_STATUS_COHERENCE_FIELDS = (
    "run_id",
    "run_state",
    "run_revision",
    "experiment_states",
    "open_attempts",
    "canonical_outcome_count",
    "goal_verdict",
    "required_component_count",
    "required_assessment_count",
)
_HOST_STATUS_FIELDS = (
    "quiescent_for_backup",
    "agent_usage",
    "resource_waits",
)

# The Host serializes control requests between workflow ticks. A tick that
# refreshes the research projection can exceed the former 0.25-second probe
# window even while the Host is healthy. Keep observation bounded, but allow
# one ordinary busy tick to complete.
_HOST_STATUS_TIMEOUT_SECONDS = 3.0


class HostStatusBackoff:
    """Defer STATUS attempts while the control socket stays unresponsive.

    Every poll of a wedged socket waits out the full timeout and leaves one
    more pending connection in the Host's bounded listen backlog. Consecutive
    failures therefore delay the next attempt exponentially up to a cap, and
    any response resets the delay. The owner of a polling loop keeps one
    instance and shares it across refreshes; one-shot callers need none.
    """

    def __init__(
        self,
        *,
        base_seconds: float = _HOST_STATUS_TIMEOUT_SECONDS,
        cap_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(base_seconds) or base_seconds <= 0:
            raise ValueError("base_seconds must be a positive finite number")
        if not math.isfinite(cap_seconds) or cap_seconds < base_seconds:
            raise ValueError("cap_seconds must be a finite number not below base_seconds")
        self.base_seconds = base_seconds
        self.cap_seconds = cap_seconds
        self._clock = clock
        self._failures = 0
        self._next_attempt_at = 0.0

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def allows_attempt(self) -> bool:
        return self._clock() >= self._next_attempt_at

    def record_failure(self) -> None:
        self._failures += 1
        delay = min(self.cap_seconds, self.base_seconds * 2 ** (self._failures - 1))
        self._next_attempt_at = self._clock() + delay

    def record_success(self) -> None:
        self._failures = 0
        self._next_attempt_at = 0.0


def _compatible_host_status(
    offline: Mapping[str, Any], live: Mapping[str, Any], run_id: str,
) -> bool:
    """Reject a STATUS response that cannot enrich this exact observation."""

    if live.get("run_id") != run_id or not set(offline) <= set(live):
        return False
    offline_revision = offline.get("run_revision")
    live_revision = live.get("run_revision")
    if (
        isinstance(offline_revision, bool)
        or not isinstance(offline_revision, int)
        or isinstance(live_revision, bool)
        or not isinstance(live_revision, int)
        or live_revision != offline_revision
    ):
        return False
    if any(live.get(field) != offline.get(field) for field in _STATUS_COHERENCE_FIELDS):
        return False
    return (
        isinstance(live.get("run_state"), str)
        and isinstance(live.get("quiescent_for_backup"), bool)
        and isinstance(live.get("agent_usage"), Mapping)
        and isinstance(live.get("resource_waits"), list)
    )


def build_observer_snapshot(
    state_root: str | Path,
    run_id: str,
    *,
    host_timeout: float = _HOST_STATUS_TIMEOUT_SECONDS,
    _connection: sqlite3.Connection | None = None,
    _oracle: Mapping[str, Any] | None = None,
    _host_backoff: HostStatusBackoff | None = None,
) -> MonitorSnapshot:
    """Build the canonical offline view and optionally enrich it via STATUS.

    The Unix-socket request is read-only and uses the existing Host projection.
    Absence, timeout, rejection, or an incompatible response leaves the offline
    snapshot unchanged; an observer never requires or starts a resident Host.
    A polling caller may pass its own HostStatusBackoff so that consecutive
    unresponsive polls defer further attempts instead of piling connections
    into the Host's bounded listen backlog.
    """

    if not math.isfinite(host_timeout) or host_timeout <= 0:
        raise ValueError("host_timeout must be a positive finite number")
    options = {}
    if _connection is not None:
        options["_connection"] = _connection
    if _oracle is not None:
        options["_oracle"] = _oracle
    snapshot = build_monitor_snapshot(state_root, run_id, **options)
    socket_path = Path(state_root).expanduser().resolve() / "control.sock"
    if not socket_path.is_socket():
        return snapshot
    if _host_backoff is not None and not _host_backoff.allows_attempt():
        return snapshot
    request = ControlRequest(
        schema_version=1,
        request_id=f"observer-status-{uuid.uuid4()}",
        expected_revision=None,
        command="STATUS",
        payload={"run_id": run_id},
    )
    try:
        response = request_over_socket(socket_path, request, timeout=host_timeout)
    except (ConnectionError, OSError, TimeoutError, ValueError):
        if _host_backoff is not None:
            _host_backoff.record_failure()
        return snapshot
    if _host_backoff is not None:
        _host_backoff.record_success()
    if response.status != "accepted" or response.result is None:
        return snapshot
    if not _compatible_host_status(snapshot.status, response.result, run_id):
        return snapshot
    enriched_status = dict(snapshot.status)
    enriched_status.update(
        {field: response.result[field] for field in _HOST_STATUS_FIELDS}
    )
    enriched_status["dispatch_error"] = response.result.get("dispatch_error")
    return replace(
        snapshot,
        status_source="offline-state-store+resident-host-status",
        status=enriched_status,
        overview={**snapshot.overview, "host_observation": {
            **snapshot.overview.get("host_observation", {}), "availability": "responded", "orphaned": False,
            "dispatch_error": response.result.get("dispatch_error")}},
    )


def validate_monitor_source(state_root: str | Path, run_id: str) -> None:
    """Check exact readable run identity without building or auditing history."""
    root = Path(state_root).expanduser().resolve()
    try:
        with StateStore(root).readonly_connection() as connection:
            if StateTransaction(connection).get_run(run_id) is None:
                raise MonitorError(f"run not found in {root / 'state.sqlite'}: {run_id!r}")
    except MonitorError:
        raise
    except (OSError, sqlite3.Error, StateStoreError, ValueError) as exc:
        raise MonitorError(f"cannot read Hypoloom state at {root}: {exc}") from exc


def run_monitor(
    state_root: str | Path,
    run_id: str,
    *,
    json_output: bool,
    export_dir: str | Path | None = None,
    interval: float = 2.0,
    web_enabled: bool = True,
    web_port: int | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    gpu_pool_socket: str | Path | None = None,
    gpu_pool_server_uid: int | None = None,
) -> int:
    """Export an offline view, render JSON, or launch the optional Textual app."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    if gpu_pool_server_uid is not None and (type(gpu_pool_server_uid) is not int or gpu_pool_server_uid < 0 or gpu_pool_socket is None):
        print("monitor: pool server UID requires an explicit socket and nonnegative integer", file=errors)
        return 2
    if export_dir is not None:
        if json_output:
            print("monitor: --export and --json are mutually exclusive", file=errors)
            return 2
        from .research_export import ResearchExportError, export_research_view

        try:
            exported, file_count = export_research_view(state_root, run_id, export_dir)
        except (MonitorError, ResearchExportError) as exc:
            print(f"monitor: {exc}", file=errors)
            return 2
        print(f"Exported {file_count} files to {exported}", file=output)
        return 0
    if not math.isfinite(interval) or interval <= 0:
        print("monitor: --interval must be a positive finite number", file=errors)
        return 2
    if json_output:
        try:
            snapshot = build_observer_snapshot(state_root, run_id)
        except MonitorError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=errors)
            return 2
        print(snapshot.to_json(indent=2), file=output)
        return 0

    if web_port is None:
        from .web_observer import DEFAULT_PORT

        web_port = DEFAULT_PORT
    if (
        isinstance(web_port, bool)
        or not isinstance(web_port, int)
        or not 0 <= web_port <= 65535
    ):
        print("monitor: --web-port must be an integer from 0 through 65535", file=errors)
        return 2

    try:
        tui = importlib.import_module("hypoloom.tui")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".", 1)[0] in {"rich", "textual"}:
            print(
                "monitor: TUI requires Textual; install "
                "'hypoloom[monitor]' or use --json",
                file=errors,
            )
            return 1
        raise
    try:
        validate_monitor_source(state_root, run_id)
    except MonitorError as exc:
        print(f"monitor: {exc}", file=errors)
        return 2
    tui.run_tui(
        Path(state_root),
        run_id,
        interval=interval,
        web_enabled=web_enabled,
        web_port=web_port,
        **({"gpu_pool_socket": gpu_pool_socket, "gpu_pool_server_uid": gpu_pool_server_uid}
           if gpu_pool_socket is not None else {}),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    from .web_observer import DEFAULT_PORT

    parser = argparse.ArgumentParser(
        prog="python -m hypoloom.monitor",
        description="Read-only Hypoloom run monitor (offline export, JSON, or Textual TUI).",
    )
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", dest="json_output")
    mode.add_argument(
        "--export", dest="export_dir", metavar="DIR",
        help="export an offline research view to a new directory outside the state root",
    )
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--gpu-pool-socket", help="explicit authorized aggregate capacity socket for Web; no discovery")
    parser.add_argument("--gpu-pool-server-uid", type=int)
    parser.add_argument(
        "--web-port",
        type=int,
        default=DEFAULT_PORT,
        help="TUI Web port on 127.0.0.1 (0 lets the OS choose; default: 8770)",
    )
    parser.add_argument(
        "--no-web",
        action="store_false",
        dest="web_enabled",
        help="do not start the TUI-owned loopback Web observer",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_monitor(
        args.state_root,
        args.run_id,
        json_output=args.json_output,
        export_dir=args.export_dir,
        interval=args.interval,
        web_enabled=args.web_enabled,
        web_port=args.web_port,
        **({"gpu_pool_socket": args.gpu_pool_socket, "gpu_pool_server_uid": args.gpu_pool_server_uid}
           if args.gpu_pool_socket is not None or args.gpu_pool_server_uid is not None else {}),
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AttemptView",
    "ComponentView",
    "ExpertView",
    "HostStatusBackoff",
    "HypothesisView",
    "MonitorError",
    "MonitorSnapshot",
    "RendezvousView",
    "WorkflowView",
    "build_monitor_snapshot",
    "build_observer_snapshot",
    "build_status_projection",
    "main",
    "run_monitor",
]
