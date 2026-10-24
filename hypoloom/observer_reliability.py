"""Disposable execution-quality statistics, never a model score or a gate.

Attempts and the existing accounting fold remain the sources of truth. Missing
evidence is visible; this reader cannot settle a node or authorize a retry.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import math

from .agent_budget import fold_agent_attempts
from .diagnostics import bounded_diagnostic_text

_METRICS = ("output_first_pass", "correction_recovery", "node_retry", "node_delivery", "required_actions")
_NOTICES = [
    "Execution observations are not base-model intelligence scores or scientific goal verdicts.",
    "MODEL/INFRASTRUCTURE are retry routes, not established causes. Attribution is separate and tentative.",
    "Rates show numerator/denominator, unknown and excluded samples; zero denominators are unavailable.",
    "Active failed nodes can still retry: delivery remains pending until acceptance, recorded final failure or run termination.",
    "Tool counts are observed lower bounds when trace coverage is incomplete; tool errors can be expected tests.",
    "Required actions cover only the existing explicit resource Skill/load/query instruction, not all prose instructions.",
    "Retry tokens are reported additional-call usage. Reservations are not measured usage and unknown is not zero.",
    "Same-call correction tokens are unavailable; elapsed sums are invocation time, not CPU time or critical-path delay.",
]


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, dict) else {}


def _text(value, default=None):
    return bounded_diagnostic_text(value) or default


def _time(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result = float(value)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            result = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _metric(numerator=0, denominator=0, unknown=0, excluded=0):
    return {"numerator": numerator, "denominator": denominator,
        "rate": numerator / denominator if denominator else None, "unknown": unknown, "excluded": excluded}


def _sum_metrics(items):
    return {name: _metric(**{field: sum(item[name][field] for item in items)
        for field in ("numerator", "denominator", "unknown", "excluded")}) for name in _METRICS}


def _correction(value):
    """Read observed validator feedback, not revalidate private model output."""
    value = _object(value)
    rounds = value.get("rounds")
    rejected, calls = value.get("rejected_count"), value.get("call_count")
    if (value.get("scope") not in {"native_stop_validation", "cc_tool_observation"}
            or type(rejected) is not int or rejected < 0 or type(calls) is not int or calls <= 0
            or not isinstance(rounds, list) or len(rounds) != calls or value.get("truncated")
            or any(not isinstance(item, dict) for item in rounds)):
        return None
    statuses = [item.get("status") for item in rounds]
    if (any(status not in {"accepted", "tool_succeeded", "rejected"} for status in statuses)
            or statuses.count("rejected") != rejected):
        return None
    if value["scope"] == "native_stop_validation" and (
            type(value.get("accepted")) is not bool
            or value["accepted"] != (statuses[-1] == "accepted") or value.get("error")):
        return None
    return {"first_pass": statuses[0] in {"accepted", "tool_succeeded"},
        "rejected": rejected, "accepted": statuses[-1] in {"accepted", "tool_succeeded"}}


def _attribution(observation, terminal, correction, trace):
    code = observation.get("failure_code")
    if terminal in {"TIMED_OUT", "LOST"}:
        return {"category": "undetermined", "confidence": "unknown",
            "reason": "Timeout/lost execution alone cannot separate model latency, transport, budget and Host interruption."}
    if terminal == "CANCELLED":
        return {"category": "control", "confidence": "observed", "reason": "Cancelled, excluded from quality failure rates."}
    if code == "host_restart":
        return {"category": "framework", "confidence": "observed", "reason": "Recorded Host restart; not evidence of model failure."}
    if (correction and correction["rejected"]) or observation.get("failure_stage") in {
            "host_candidate_validation", "provider_output", "provider_structured_output"}:
        return {"category": "model_output", "confidence": "suspected",
            "reason": "Observed rejected generated output; prompt, validator and service contributions are not ruled out."}
    if trace.get("required_action", {}).get("status") == "missed":
        return {"category": "model_action", "confidence": "suspected",
            "reason": "Explicit available action was not completed in the observed invocation; not a general capability verdict."}
    if observation.get("failure_stage") in {"workspace_preparation", "host_recovery", "agent_workspace"}:
        return {"category": "framework_boundary", "confidence": "suspected",
            "reason": "Failure observed at workspace/Host boundary; cause requires the linked evidence."}
    if code in {"provider_error", "nonzero_exit", "invalid_provider_envelope"}:
        return {"category": "service_boundary", "confidence": "suspected",
            "reason": "Provider/CLI boundary failed; this does not establish an upstream service defect."}
    return {"category": "undetermined", "confidence": "unknown",
        "reason": "No causal conclusion; tool errors may be expected tests or useful negative findings."}


def _attempt(root, run_id, row, reader):
    observation = _object(row.get("observation", row.get("observation_json")))
    request = _object(observation.get("request"))
    diagnostics = _object(observation.get("diagnostics"))
    trace = reader(root, run_id, row)
    raw_correction = _object(diagnostics.get("structured_output_correction"))
    correction = _correction(raw_correction)
    terminal = row.get("terminal_reason") if row.get("state") == "CLOSED" else None
    metrics = {name: _metric() for name in _METRICS}
    if terminal == "CANCELLED":
        metrics["output_first_pass"] = metrics["correction_recovery"] = _metric(excluded=1)
    elif terminal is None or correction is None:
        metrics["output_first_pass"] = metrics["correction_recovery"] = _metric(unknown=1)
    else:
        metrics["output_first_pass"] = _metric(int(correction["first_pass"]), 1)
        metrics["correction_recovery"] = (_metric(int(terminal == "SUCCEEDED" and correction["accepted"]), 1)
            if correction["rejected"] else _metric(excluded=1))
    action = trace.get("required_action", {}).get("status", "unknown")
    metrics["required_actions"] = (_metric(excluded=1) if action == "not_applicable" or terminal == "CANCELLED"
        else _metric(unknown=1) if terminal is None or action not in {"met", "missed"}
        else _metric(int(action == "met"), 1))
    observations = []
    if observation.get("failure_code"):
        observations.append({"kind": "terminal_failure", "code": _text(observation["failure_code"]),
            "stage": _text(observation.get("failure_stage"))})
    if correction and correction["rejected"]:
        observations.append({"kind": "output_rejected", "count": correction["rejected"]})
    if trace.get("tool_errors"):
        observations.append({"kind": "tool_errors", "count": trace["tool_errors"], "cause": "undetermined"})
    if action == "missed":
        observations.append({"kind": "required_action_missed", "name": trace["required_action"].get("name")})
    # Only small, bounded error metadata belongs in the snapshot, never candidate bodies.
    correction_view = None if not raw_correction else {
        key: raw_correction.get(key) for key in ("scope", "status", "accepted", "call_count", "rejected_count", "truncated")}
    if correction_view is not None:
        correction_view["rounds"] = [{"ordinal": item.get("ordinal", item.get("round")),
            "status": _text(item.get("status")), "validation_error": _text(item.get("validation_error"))}
            for item in raw_correction.get("rounds", [])[:64] if isinstance(item, dict)] if isinstance(raw_correction.get("rounds"), list) else []
    return {"attempt_id": row["attempt_id"], "state": row.get("state"), "terminal_reason": terminal,
        "attempt_ordinal": row.get("attempt_ordinal"),
        "model": _text(_object(observation.get("response")).get("model_identity") or request.get("required_model_identity"), "unknown"),
        "failure_code": _text(observation.get("failure_code")), "failure_stage": _text(observation.get("failure_stage")),
        "retry_class": observation.get("retry_class") if observation.get("retry_class") in {"MODEL", "INFRASTRUCTURE"} else None,
        "attribution": _attribution(observation, terminal, correction, trace), "observations": observations,
        "corrections": correction_view, "trace": trace, "metrics": metrics,
        "started_at": _time(row.get("started_at")), "ended_at": _time(row.get("ended_at"))}


def _costs(rows, ordinals_known):
    extra = rows[1:] if ordinals_known else []
    charges = fold_agent_attempts(extra, _observations={row['attempt_id']:
        _object(row.get('observation', row.get('observation_json'))) for row in extra})
    durations, missing_duration = [], 0
    for row in extra:
        start, end = _time(row.get("started_at")), _time(row.get("ended_at"))
        if row.get("state") == "CLOSED" and start is not None and end is not None and end >= start:
            durations.append(end - start)
        else:
            missing_duration += 1
    return {"retry_attempts": len(extra), "retry_unknown_nodes": int(not ordinals_known),
        "retry_reported_tokens": sum(item.tokens for item in charges if item.source != "declared" and item.tokens is not None),
        "retry_reserved_tokens": sum(item.tokens for item in charges if item.source == "declared" and item.tokens is not None),
        "retry_unavailable_token_attempts": sum(item.tokens is None for item in charges),
        "retry_reported_usd": str(sum((item.cost for item in charges if item.source != "declared" and item.cost is not None), Decimal(0))),
        "retry_reserved_usd": str(sum((item.cost for item in charges if item.source == "declared" and item.cost is not None), Decimal(0))),
        "retry_unknown_closed_attempts": sum(item.source == "declared" and item.state == "CLOSED" for item in charges),
        "retry_unavailable_reservations": sum(item.cost is None for item in charges),
        "retry_elapsed_seconds": sum(durations), "retry_elapsed_unknown_attempts": missing_duration,
        "correction_cost_usd": None}


def summarize_reliability(nodes, *, include_nodes=True, include_groups=None):
    """Combine already-scoped node observations without re-reading files."""
    if include_groups is None:
        include_groups = include_nodes
    metrics = _sum_metrics([item["metrics"] for item in nodes])
    attempts = [attempt for node in nodes for attempt in node["attempts"]]
    counts = {"agent_attempts": len(attempts),
        "closed_attempts": sum(item["state"] == "CLOSED" for item in attempts),
        "open_attempts": sum(item["state"] == "OPEN" for item in attempts),
        "timed_out_attempts": sum(item["terminal_reason"] == "TIMED_OUT" for item in attempts),
        "cancelled_attempts": sum(item["terminal_reason"] == "CANCELLED" for item in attempts),
        "tool_calls": sum(item["trace"].get("tool_calls", 0) for item in attempts),
        "tool_errors": sum(item["trace"].get("tool_errors", 0) for item in attempts),
        "trace_unknown_attempts": sum(item["trace"].get("coverage") != "complete" for item in attempts)}
    costs = {key: sum(item["costs"][key] for item in nodes) for key in (
        "retry_attempts", "retry_unknown_nodes", "retry_unknown_closed_attempts", "retry_unavailable_reservations",
        "retry_elapsed_seconds", "retry_elapsed_unknown_attempts", "retry_reported_tokens",
        "retry_reserved_tokens", "retry_unavailable_token_attempts")}
    costs.update({key: str(sum((Decimal(item["costs"][key]) for item in nodes), Decimal(0)))
        for key in ("retry_reported_usd", "retry_reserved_usd")})
    costs["correction_cost_usd"] = None
    result = {"metrics": metrics, "counts": counts, "costs": costs, "notices": list(_NOTICES)}
    if include_nodes:
        result["nodes"] = nodes
    if include_groups:
        grouped = defaultdict(list)
        for node in nodes:
            grouped[(node["role"], node["node"], node["model"], node.get("task_scope"), node.get("owner_role"), node.get("run_group"))].append(node)
        result["groups"] = [{"role": role, "node": kind, "model": model,
            **({"task_scope": scope, "owner_role": owner} if scope is not None else {}),
            **({"run_group": group} if group is not None else {}),
            **summarize_reliability(values, include_nodes=False)} for (role, kind, model, scope, owner, group), values in grouped.items()]
    return result


def reliability_context(transitions, facts=()):
    """Existing durable closure evidence shared by all execution score views."""
    def payload(row):
        return _object(row.get("payload", row.get("payload_json")))

    run_rows = [row for row in transitions if row.get("workflow_kind") == "RUN"]
    terminal = next((row for row in run_rows if row.get("state") in {"COMPLETED", "FAILED", "CANCELLED"}), None)
    interruptions = [_time(row.get("created_at")) for row in facts
        if row.get("fact_type") == "GoalVerdictRecorded" and payload(row).get("verdict") == "GOAL_MET"]
    interruptions.extend(_time(row.get("created_at")) for row in run_rows
        if row.get("state") == "STOPPING" and payload(row).get("stop_disposition") == "CANCELLED")
    if terminal is not None and terminal.get("state") == "CANCELLED":
        interruptions.append(_time(terminal.get("created_at")))
    return {"run_terminal": terminal is not None,
        "interrupted_at": min((value for value in interruptions if value is not None), default=None),
        "final_failure_ids": {payload(row).get("attempt_id") for row in transitions if payload(row).get("agent_final_failure")}}


def build_reliability(state_root, run_id, attempts, *, run_terminal=False, interrupted_at=None,
                      final_failure_ids=(), evidence_reader=None):
    if evidence_reader is None:
        from .observer_evidence import evidence_read_budget, read_attempt_evidence
        with evidence_read_budget(16):
            return _build(state_root, run_id, attempts, run_terminal, interrupted_at, final_failure_ids, read_attempt_evidence)
    return _build(state_root, run_id, attempts, run_terminal, interrupted_at, final_failure_ids, evidence_reader)


def _build(state_root, run_id, attempts, run_terminal, interrupted_at, final_failure_ids, evidence_reader):
    final_failure_ids = set(final_failure_ids)
    grouped = defaultdict(list)
    for row in attempts:
        if row.get("kind") != "AGENT" or row.get("run_id", run_id) != run_id:
            continue
        observation = _object(row.get("observation", row.get("observation_json")))
        row = {**row, "observation": observation}
        request = _object(observation.get("request"))
        workflow = row.get("workflow_id") or request.get("role") or "unknown"
        command = row.get("command_id") or request.get("command_id") or row["attempt_id"]
        grouped[(workflow, command)].append(row)
    nodes = []
    for (workflow, command), rows in grouped.items():
        rows.sort(key=lambda item: (item.get("attempt_ordinal") if type(item.get("attempt_ordinal")) is int else 0,
            _time(item.get("started_at")) or 0, item["attempt_id"]))
        ordinal_values = [item.get("attempt_ordinal") for item in rows]
        ordinals_known = (all(type(item) is int for item in ordinal_values)
            and ordinal_values == list(range(1, len(rows) + 1)))
        views = [_attempt(state_root, run_id, row, evidence_reader) for row in rows]
        last = views[-1]
        state = ("pending" if any(item["state"] == "OPEN" for item in views) else
            "delivered" if last["terminal_reason"] == "SUCCEEDED" else
            "cancelled" if last["terminal_reason"] == "CANCELLED" else
            "undelivered" if last["attempt_id"] in final_failure_ids
                and last["terminal_reason"] in {"FAILED", "LOST", "TIMED_OUT"} else
            "cancelled" if (run_terminal and interrupted_at is not None
                and last["started_at"] is not None and last["started_at"] <= interrupted_at) else
            "undelivered" if run_terminal and last["terminal_reason"] in {"FAILED", "LOST", "TIMED_OUT"} else "pending")
        metrics = _sum_metrics([item["metrics"] for item in views])
        metrics["node_delivery"] = (_metric(excluded=1) if state == "cancelled" else
            _metric(unknown=1) if state == "pending" else _metric(int(state == "delivered"), 1))
        metrics["node_retry"] = (_metric(excluded=1) if state == "cancelled" else
            _metric(unknown=1) if state == "pending" or not ordinals_known else _metric(int(len(rows) > 1), 1))
        request = _object(rows[-1]["observation"].get("request"))
        task = _object(request.get("task_context"))
        models = sorted({item["model"] for item in views})
        nodes.append({"workflow_id": _text(workflow), "command_id": _text(command),
            "role": _text(request.get("role"), "unknown"),
            "node": _text(task.get("request_kind") or request.get("command"), "unknown"),
            "component_id": _text(request.get("component_id")), "chain": _text(_object(task.get("key")).get("chain")),
            "model": models[0] if len(models) == 1 else "mixed: " + " / ".join(models),
            "status": state, "attempt_ids": [item["attempt_id"] for item in views], "attempts": views,
            "metrics": metrics, "costs": _costs(rows, ordinals_known)})
    return summarize_reliability(nodes)
