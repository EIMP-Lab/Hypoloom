"""Run-local, disposable outcomes and follow-up over existing evidence.

These are associations, not skill attribution, a selection policy or a ledger.
The reliability reducer owns logical-node semantics and accounting.
"""
from __future__ import annotations

from collections import defaultdict

from .diagnostics import diagnostic_summary
from .observer_reliability import (
    _object,
    _time,
    build_reliability,
    reliability_context,
    summarize_reliability,
)
from .runtime_diagnostics import sanitize_payload


def _observation(row):
    return _object(row.get("observation", row.get("observation_json")))


def _request(row):
    return _object(_observation(row).get("request"))


def _key(row):
    return _object(_object(_request(row).get("task_context")).get("key"))


def _payload(row):
    return _object(row.get("payload", row.get("payload_json")))


def _source_proposals(rows, transitions):
    """Join exact accepted relations to their successful authoring Attempt."""
    result = defaultdict(list)
    for transition in transitions:
        payload = _payload(transition)
        source = rows.get(payload.get("agent_attempt_id"))
        if source is None or not payload.get("hypothesis_id"):
            continue
        request, key = _request(source), _key(source)
        if (source.get("state") != "CLOSED" or source.get("terminal_reason") != "SUCCEEDED"
                or request.get("command") not in {"PROPOSE_HYPOTHESIS", "HYPOTHESIS_PROPOSAL"}
                or payload.get("node_command_id") != request.get("command_id")
                or payload.get("role") != request.get("role")
                or payload.get("component_id") != request.get("component_id")):
            continue
        identity = (request.get("role"), request.get("component_id"), key.get("cycle"), payload.get("chain"))
        value = {"attempt_id": source["attempt_id"], "hypothesis_id": payload["hypothesis_id"]}
        if value not in result[identity]:
            result[identity].append(value)
    return result


def _signature(failure):
    # Exact retained diagnostic, not an invented root-cause fingerprint. Dynamic
    # error text can split one problem; missing text cannot prove its resolution.
    return (failure["failure_code"], failure["failure_stage"], failure["diagnostic"])


def _context(node):
    return (node["role"], node["node"], node["component_id"], node["model"])


def outcome_nodes(root, run_id, attempts, bindings, *, transitions=(), facts=(), evidence=None):
    """Preserve whole retry chains before filtering by version or observed call."""
    evidence = evidence or {}
    rows = {row["attempt_id"]: row for row in attempts
            if row.get("run_id") == run_id and row.get("kind") == "AGENT"}
    transitions = [row for row in transitions if row.get("run_id") == run_id]
    facts = [row for row in facts if row.get("run_id") == run_id]
    nodes = build_reliability(root, run_id, list(rows.values()), **reliability_context(transitions, facts),
        evidence_reader=lambda _root, _run, row: evidence.get(row["attempt_id"], {"coverage": "unavailable"}))["nodes"]
    sources = _source_proposals(rows, transitions)
    for node in nodes:
        node_bindings = [bindings.get(identity) for identity in node["attempt_ids"]]
        identities = {(item["role"], item["commit"]) for item in node_bindings if item}
        node["binding"] = (next(item for item in node_bindings if item)
            if len(identities) == 1 and all(node_bindings) else None)
        node["failures"] = []
        node["started_at"] = min((v["started_at"] for v in node["attempts"] if v["started_at"] is not None), default=None)
        node["ended_at"] = max((v["ended_at"] for v in node["attempts"] if v["ended_at"] is not None), default=None)
        for view in node["attempts"]:
            if view["terminal_reason"] not in {"FAILED", "LOST", "TIMED_OUT"}:
                continue
            row = rows[view["attempt_id"]]
            request, key = _request(row), _key(row)
            source_key = (request.get("role"), request.get("component_id"), key.get("cycle"), key.get("chain"))
            node["failures"].append({"attempt_id": view["attempt_id"],
                "failure_code": view["failure_code"] or view["terminal_reason"],
                "failure_stage": view["failure_stage"], "diagnostic": diagnostic_summary(_observation(row)),
                "attribution": view["attribution"], "cycle": key.get("cycle"), "chain": key.get("chain"),
                "source_proposals": sources.get(source_key, [])[:4]})
    return nodes


def scorecards(nodes, calls=(), *, selected_ids=None, name=None, limit=200):
    """Bound-context cohorts and observed-call cohorts must never be summed."""
    called = defaultdict(set)
    for call in calls:
        if name is None or call["name"] == name:
            called[call["attempt_id"]].add(call["name"])
    groups = defaultdict(list)
    unassigned = partial = 0
    for node in nodes:
        ids = set(node["attempt_ids"])
        if selected_ids is not None:
            if not ids & selected_ids:
                continue
            if not ids <= selected_ids:
                partial += 1
                continue
        binding = node["binding"]
        if binding is None:
            unassigned += 1
            continue
        role, stage, component, model = _context(node)
        # Plugin owner may be Grounding while the initial executor has a role.
        role = binding["role"]
        identity = (role, binding["commit"], stage, component, model)
        groups[(*identity, None)].append(node)
        for skill in sorted(set().union(*(called[i] for i in ids))):
            groups[(*identity, skill)].append(node)
    items = []
    for (role, commit, stage, component, model, skill), members in groups.items():
        summary = summarize_reliability(members, include_nodes=False)
        failures = [failure for node in members for failure in node["failures"]]
        items.append({"role": role, "commit": commit, "stage": stage, "component_id": component,
            "model": model, "name": skill, "basis": "observed_call" if skill else "bound_version",
            "metrics": summary["metrics"], "counts": summary["counts"], "costs": summary["costs"],
            "failed_attempts": len(failures), "failures": failures[-12:], "failures_truncated": len(failures) > 12,
            "attempt_ids": [identity for node in members for identity in node["attempt_ids"]][-12:],
            "last_observed_at": max((node["ended_at"] or node["started_at"] or 0 for node in members), default=0)})
    items.sort(key=lambda item: (-item["last_observed_at"], item["role"], item["stage"], item["name"] or ""))
    return {"groups": items[:limit], "groups_truncated": len(items) > limit,
        "unassigned_nodes": unassigned, "partial_nodes_excluded": partial}


def followups(nodes, evolutions, *, name=None, limit=100):
    """Compare later contexts to baseline failures; never declare a fix proven."""
    result = []
    for event in evolutions:
        if event["status"] != "accepted_change":
            continue
        changed = [skill["name"] for skill in event["skills"] if name is None or skill["name"] == name]
        if not changed:
            continue
        ended = _time(event.get("ended_at"))
        before, after = defaultdict(list), defaultdict(list)
        for node in nodes:
            binding = node["binding"]
            if binding is None or binding["role"] != event["role"]:
                continue
            if binding["commit"] == event["base_commit"] and ended is not None and (node["ended_at"] or float("inf")) <= ended:
                before[_context(node)].append(node)
            if (ended is not None and node["started_at"] is not None and node["started_at"] >= ended
                    and binding["commit"] != event["base_commit"] and event["attempt_id"] not in node["attempt_ids"]):
                after[_context(node)].append(node)
        comparisons = []
        for context, baseline in before.items():
            problems = defaultdict(list)
            for node in baseline:
                for failure in node["failures"]:
                    problems[_signature(failure)].append(failure["attempt_id"])
            for signature, original_ids in problems.items():
                # Unknown/mixed model identities are not comparable evidence.
                comparable = context[-1] != "unknown" and not context[-1].startswith("mixed:")
                later = after.get(context, []) if comparable else []
                matches = [f["attempt_id"] for node in later for f in node["failures"] if signature[2] and _signature(f) == signature]
                finished = sum(node["status"] in {"delivered", "undelivered"} for node in later)
                comparisons.append({"stage": context[1], "component_id": context[2], "model": context[3],
                    "failure_code": signature[0], "failure_stage": signature[1], "diagnostic": signature[2],
                    "baseline_failed_attempts": len(original_ids), "baseline_attempt_ids": original_ids[-6:],
                    "later_nodes": len(later), "later_finished_nodes": finished,
                    "later_failed_attempts": len(matches), "later_attempt_ids": matches[-6:],
                    "later_commits": sorted({node["binding"]["commit"] for node in later}),
                    "status": "recurrence_observed" if matches else "not_observed" if finished and signature[2]
                        else "awaiting_samples"})
        later_count = sum(len(items) for items in after.values())
        comparable_samples = any(context in before and context[-1] != "unknown" and not context[-1].startswith("mixed:")
            and any(node["status"] in {"delivered", "undelivered"} for node in members) for context, members in after.items())
        for skill in changed:
            result.append({"role": event["role"], "name": skill, "commit": event["commit"],
                "base_commit": event["base_commit"], "attempt_id": event["attempt_id"],
                "status": "recurrence_observed" if any(c["status"] == "recurrence_observed" for c in comparisons)
                    else "observations_available" if any(c["status"] == "not_observed" for c in comparisons)
                        or not comparisons and comparable_samples
                    else "awaiting_samples",
                "later_nodes": later_count, "comparisons": comparisons[:24],
                "comparisons_truncated": len(comparisons) > 24})
    return {"followups": result[:limit], "followups_truncated": len(result) > limit}


def runtime_feedback(store, root, run_id, role, component_id, *, max_attempts=256, max_bytes=24576):
    """Bounded metadata only: no Git, transcript reads, new scheduling or writes."""
    import json

    from .observer_details import _request_plugin

    # The existing SQLite rows are the source. Scope before LIMIT, so another
    # busy expert cannot crowd this owner's failures out of the input.
    attempts = [dict(row) for row in store.inspect(
        "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT' "
        "AND json_extract(observation_json,'$.request.role')=? "
        "AND json_extract(observation_json,'$.request.component_id')=? "
        "ORDER BY started_at DESC,attempt_id DESC LIMIT ?", (run_id, role, component_id, max_attempts + 1))]
    truncated = len(attempts) > max_attempts
    attempts = list(reversed(attempts[:max_attempts]))
    transitions = store.inspect("SELECT run_id,workflow_kind,state,created_at,payload_json FROM workflow_transitions "
        "WHERE run_id=? AND (json_extract(payload_json,'$.agent_final_failure')=1 "
        "OR json_extract(payload_json,'$.agent_attempt_id') IS NOT NULL)", (run_id,))
    bindings = {row["attempt_id"]: _request_plugin(row, run_id) for row in attempts}
    nodes = outcome_nodes(root, run_id, attempts, bindings, transitions=transitions)
    # A bounded read can start halfway through a retry chain. Exclude that node,
    # rather than falsely credit its last retry as an initial success.
    complete_nodes = [node for node in nodes if not node["costs"]["retry_unknown_nodes"]]
    cards = scorecards(complete_nodes)
    # Keep failure cohorts ahead of ordinary successes when the byte budget is
    # tight. Reports retain older unresolved lessons beyond this bounded window.
    cards["groups"].sort(key=lambda item: (not bool(item["failed_attempts"]), -item["last_observed_at"]))
    groups = cards["groups"][:24]
    for group in groups:
        group["failures_truncated"] |= len(group["failures"]) > 3
        group["failures"] = group["failures"][-3:]
        group["attempt_ids"] = group["attempt_ids"][-3:]
    result = {"scope": {"run_id": run_id, "role": role, "component_id": component_id},
        "basis": "bound_version", "attempts_observed": len(attempts), "history_truncated": truncated,
        "incomplete_nodes_excluded": len(nodes) - len(complete_nodes),
        "groups": groups,
        "instruction": "Use original Attempt evidence to follow recurring failures across versions. Binding is not a Skill call or cause. "
            "Record target problem, source refs, changed skill/stage, expected observation and unresolved follow-up in the existing report/evolution summary. "
            "Use only this request's immutable_context_refs in supporting_refs; cite this feedback input and place original Attempt IDs in the prose. "
            "Trace frozen upstream declarations to their proposal; fix at the owning stage. Scientific negative results are not execution failures. "
            "No samples or absent diagnostics do not prove a fix. Existing budgets, cycle limits and version selection still apply."}
    updates = []
    for row in attempts:
        observation = _observation(row)
        if (row.get("state") == "CLOSED" and row.get("terminal_reason") == "SUCCEEDED"
                and _request(row).get("command") == "SELF_EVOLVE" and observation.get("plugin_update")):
            update = _object(observation["plugin_update"])
            candidate = _object(_object(observation.get("response")).get("typed_candidate"))
            summary = candidate.get("summary") if isinstance(candidate.get("summary"), str) else ""
            updates.append({"attempt_id": row["attempt_id"], "base_commit": update.get("base_commit"),
                "commit": update.get("commit"), "ended_at": row.get("ended_at"),
                "summary": summary[:1000], "summary_truncated": len(summary) > 1000,
                "status": "unchanged" if update.get("base_commit") == update.get("commit") else "accepted_change"})
    result["updates"] = updates[-8:]
    result["updates_truncated"] = len(updates) > 8
    result = sanitize_payload(result)
    # All retained counts describe their selected cohorts. Never truncate JSON
    # bytes or silently turn dropped cohorts into zero observations.
    result["groups_truncated"] = cards["groups_truncated"] or len(cards["groups"]) > len(result["groups"])
    while len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes and result["groups"]:
        result["groups"].pop()
        result["groups_truncated"] = True
    while len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes and result["updates"]:
        result["updates"].pop(0)
        result["updates_truncated"] = True
    return result
