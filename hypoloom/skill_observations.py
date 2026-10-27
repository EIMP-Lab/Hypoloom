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
        node["output_rejections"] = []
        node["started_at"] = min((v["started_at"] for v in node["attempts"] if v["started_at"] is not None), default=None)
        node["ended_at"] = max((v["ended_at"] for v in node["attempts"] if v["ended_at"] is not None), default=None)
        for view in node["attempts"]:
            row = rows[view["attempt_id"]]
            request, key = _request(row), _key(row)
            source_key = (request.get("role"), request.get("component_id"), key.get("cycle"), key.get("chain"))
            correction = _object(view.get("corrections"))
            for round_view in correction.get("rounds", []):
                if round_view.get("status") != "rejected":
                    continue
                node["output_rejections"].append({"attempt_id": view["attempt_id"],
                    "ordinal": round_view.get("ordinal", round_view.get("round")),
                    "failure_code": "output_rejected", "failure_stage": "structured_output",
                    "validation_stage": round_view.get("validation_stage"),
                    "diagnostic": round_view.get("validation_error"), "attribution": view["attribution"],
                    "cycle": key.get("cycle"), "chain": key.get("chain"),
                    "source_proposals": sources.get(source_key, [])[:4]})
            if view["terminal_reason"] not in {"FAILED", "LOST", "TIMED_OUT"}:
                continue
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
        rejections = [rejection for node in members for rejection in node["output_rejections"]]
        items.append({"role": role, "commit": commit, "stage": stage, "component_id": component,
            "model": model, "name": skill, "basis": "observed_call" if skill else "bound_version",
            "metrics": summary["metrics"], "counts": summary["counts"], "costs": summary["costs"],
            "failed_attempts": len(failures), "failures": failures[-12:], "failures_truncated": len(failures) > 12,
            "output_rejection_count": len(rejections), "output_rejections": rejections[-12:],
            "output_rejections_truncated": len(rejections) > 12,
            "attempt_ids": [identity for node in members for identity in node["attempt_ids"]][-12:],
            "last_observed_at": max((node["ended_at"] or node["started_at"] or 0 for node in members), default=0)})
    items.sort(key=lambda item: (-item["last_observed_at"], item["role"], item["stage"], item["name"] or ""))
    return {"groups": items[:limit], "groups_truncated": len(items) > limit,
        "unassigned_nodes": unassigned, "partial_nodes_excluded": partial}


def process_outcomes(root, run_id, attempts, bindings, *, transitions=(), facts=()):
    rows = {row["attempt_id"]: row for row in attempts if row.get("run_id") == run_id and row.get("kind") == "AGENT"}
    creations = {}
    for transition in transitions:
        if transition.get("run_id") != run_id or transition.get("workflow_kind") != "EXPERIMENT" or transition.get("revision") != 1:
            continue
        payload = _payload(transition)
        creations[payload.get("experiment_id") or transition.get("workflow_id")] = payload
    outcomes, experiments, outcome_experiments = {}, {}, {}
    for fact in facts:
        if fact.get("run_id") != run_id:
            continue
        payload = _payload(fact)
        if fact.get("fact_type") == "ExperimentPreregistered":
            experiments[payload.get("execution_id")] = payload.get("experiment_id")
        elif fact.get("fact_type") == "CanonicalOutcomeAccepted":
            outcomes[payload.get("experiment_id")] = payload
            if payload.get("outcome_id"):
                outcome_experiments[payload["outcome_id"]] = payload.get("experiment_id")
    result = []
    for row in attempts:
        if row.get("run_id") != run_id or row.get("kind") != "PROCESS":
            continue
        observation = _observation(row)
        execution_id = row.get("execution_id") or observation.get("execution_id")
        experiment_id = row.get("experiment_id") or experiments.get(execution_id) or row.get("workflow_id") or observation.get("experiment_id")
        creation = creations.get(experiment_id, {})
        source_id = creation.get("source_agent_attempt_id")
        source = rows.get(source_id)
        binding = bindings.get(source_id)
        request = _request(source) if source is not None else {}
        response = _object(_observation(source).get("response")) if source is not None else {}
        outcome = outcomes.get(experiment_id, {})
        revision = creation.get("revision_of_outcome_id")
        preflight = creation.get("preflight_repair_of_experiment_id")
        predecessor = outcome_experiments.get(revision) if revision else preflight
        prior = creations.get(predecessor, {})
        prior_source = rows.get(prior.get("source_agent_attempt_id"))
        # These are joins over accepted identities, not guesses from timing or
        # return codes. Missing/truncated source evidence leaves the link unknown.
        scope = ("role", "component_id", "hypothesis_id", "expert_cycle", "chain",
                 "chain_iteration", "execution_node_command_id")
        if (not predecessor or predecessor == experiment_id or revision and preflight
                or source is None or prior_source is None
                or not creation.get("source_submit_command_id") or not prior.get("source_submit_command_id")
                or any(creation.get(key) != prior.get(key) for key in scope)
                or any(not creation.get(key) for key in ("role", "component_id", "hypothesis_id"))
                or any(_request(item).get(key) != identity.get(key)
                       for item, identity in ((source, creation), (prior_source, prior))
                       for key in ("role", "component_id"))):
            predecessor = None
        result.append({"attempt_id": row["attempt_id"], "source_attempt_id": source_id,
            "source_submit_command_id": creation.get("source_submit_command_id"),
            "revision_of_outcome_id": revision, "preflight_repair_of_experiment_id": preflight,
            "predecessor_experiment_id": predecessor,
            "experiment_id": experiment_id, "execution_id": row.get("execution_id") or observation.get("execution_id"),
            "role": binding["role"] if binding else creation.get("role") or request.get("role") or "unknown",
            "commit": binding["commit"] if binding else None,
            "stage": "EXPERIMENT_EXECUTION",
            "component_id": creation.get("component_id") or request.get("component_id") or "unknown",
            "model": response.get("model_identity") or request.get("required_model_identity") or "unknown",
            "cycle": creation.get("expert_cycle"), "chain": creation.get("chain"),
            "state": row.get("state"), "terminal_reason": row.get("terminal_reason"),
            "returncode": observation.get("returncode"),
            "started_at": _time(row.get("started_at")), "ended_at": _time(row.get("ended_at")),
            "outcome": outcome.get("outcome"), "validation_code": outcome.get("validation_code"),
            "metric_value": outcome.get("metric_value")})
    return result


def process_repairs(records, *, role=None, component_id=None, selected_ids=None, limit=8):
    """Existing explicit repair predecessors, never a skill-effect judgment."""
    by_experiment = defaultdict(list)
    for row in records:
        by_experiment[row["experiment_id"]].append(row)
    repairs, unavailable = [], 0
    for row in sorted(records, key=lambda item: item["started_at"] or 0, reverse=True):
        if (role is not None and row["role"] != role
                or component_id is not None and row["component_id"] != component_id
                or selected_ids is not None and row["source_attempt_id"] not in selected_ids):
            continue
        if not (row.get("revision_of_outcome_id") or row.get("preflight_repair_of_experiment_id")):
            continue
        predecessors = by_experiment.get(row.get("predecessor_experiment_id"), [])
        if len(predecessors) != 1:
            unavailable += 1
            continue
        prior = predecessors[0]
        repairs.append({"experiment_id": row["experiment_id"], "process_attempt_id": row["attempt_id"],
            "source_attempt_id": row["source_attempt_id"], "source_submit_command_id": row["source_submit_command_id"],
            "predecessor_experiment_id": prior["experiment_id"], "predecessor_process_attempt_id": prior["attempt_id"],
            "predecessor_source_attempt_id": prior["source_attempt_id"],
            "predecessor_submit_command_id": prior["source_submit_command_id"],
            "role": row["role"], "component_id": row["component_id"],
            "commit": row["commit"], "predecessor_commit": prior["commit"],
            "state": row["state"], "terminal_reason": row["terminal_reason"], "returncode": row["returncode"],
            "predecessor_terminal_reason": prior["terminal_reason"], "predecessor_returncode": prior["returncode"],
            "outcome": row["outcome"], "validation_code": row["validation_code"]})
    return {"repairs": repairs[:limit], "repairs_truncated": len(repairs) > limit,
        "unavailable_repair_links": unavailable}


def process_scorecards(records, *, role=None, component_id=None, limit=24):
    groups = defaultdict(list)
    unassigned = 0
    for record in records:
        if role is not None and record["role"] != role or component_id is not None and record["component_id"] != component_id:
            continue
        if record["commit"] is None:
            unassigned += 1
            continue
        key = (record["role"], record["commit"], record["stage"], record["component_id"], record["model"])
        groups[key].append(record)
    items = []
    for (owner, commit, stage, component, model), members in groups.items():
        failures = [row for row in members if row["terminal_reason"] in {"FAILED", "LOST", "TIMED_OUT"}]
        outcomes = defaultdict(int)
        returncodes = defaultdict(int)
        for row in members:
            outcomes[row["outcome"] or "UNKNOWN"] += 1
            returncodes[str(row["returncode"])] += 1
        items.append({"role": owner, "commit": commit, "stage": stage, "component_id": component, "model": model,
            "basis": "bound_version", "process_attempts": len(members),
            "closed_processes": sum(row["state"] == "CLOSED" for row in members),
            "open_processes": sum(row["state"] == "OPEN" for row in members),
            "succeeded_processes": sum(row["terminal_reason"] == "SUCCEEDED" for row in members),
            "failed_processes": len(failures),
            "cancelled_processes": sum(row["terminal_reason"] == "CANCELLED" for row in members),
            "outcomes": dict(outcomes), "returncodes": dict(returncodes),
            "failures": [{"attempt_id": row["attempt_id"], "source_attempt_id": row["source_attempt_id"],
                "terminal_reason": row["terminal_reason"], "returncode": row["returncode"],
                "outcome": row["outcome"], "validation_code": row["validation_code"],
                "cycle": row["cycle"], "chain": row["chain"]} for row in failures[-3:]],
            "failures_truncated": len(failures) > 3,
            "last_observed_at": max((row["ended_at"] or row["started_at"] or 0 for row in members), default=0)})
    items.sort(key=lambda item: (not bool(item["failed_processes"]), -item["last_observed_at"]))
    return {"groups": items[:limit], "groups_truncated": len(items) > limit, "unassigned_processes": unassigned}


def _agent_samples(later, field, signature):
    failures = [failure for node in later for failure in node[field]]
    matches = list(dict.fromkeys(f["attempt_id"] for f in failures
        if signature[2] and _signature(f) == tuple(signature)))
    finished = sum(node["status"] in {"delivered", "undelivered"} for node in later)
    unknown = not signature[2] or any(not failure["diagnostic"] for failure in failures)
    if field == "output_rejections":
        unknown |= any(node["metrics"]["output_first_pass"]["unknown"] for node in later)
    return {"later_nodes": len(later), "later_finished_nodes": finished,
        "later_failed_attempts": len(matches), "later_attempt_ids": matches[-6:],
        "status": "recurrence_observed" if matches else "insufficient_evidence" if unknown
            else "not_observed" if finished else "awaiting_samples"}


def _process_samples(later, signature):
    matches = [row["attempt_id"] for row in later if row["terminal_reason"] in {"FAILED", "LOST", "TIMED_OUT"}
        and (row["terminal_reason"], row["returncode"], row["validation_code"]) == signature]
    finished = sum(row["state"] == "CLOSED" and row["terminal_reason"] != "CANCELLED" for row in later)
    return {"later_nodes": len(later), "later_finished_nodes": finished,
        "later_cancelled": sum(row["terminal_reason"] == "CANCELLED" for row in later),
        "later_failed_attempts": len(matches), "later_attempt_ids": matches[-6:],
        "status": "recurrence_observed" if matches else "not_observed" if finished else "awaiting_samples"}


def _versions(later, sample, *, processes=False):
    grouped = defaultdict(list)
    for row in later:
        grouped[row["commit"] if processes else row["binding"]["commit"]].append(row)
    return {"versions": [{"commit": commit, **sample(rows)} for commit, rows in sorted(grouped.items())][:24],
        "versions_truncated": len(grouped) > 24}


def followups(nodes, evolutions, *, name=None, limit=100, processes=()):
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
                for kind, field in (("agent_terminal", "failures"), ("output_rejection", "output_rejections")):
                    for failure in node[field]:
                        problems[(kind, *_signature(failure))].append(failure["attempt_id"])
            for (kind, *signature), original_ids in problems.items():
                comparable = context[-1] != "unknown" and not context[-1].startswith("mixed:")
                later = after.get(context, []) if comparable else []
                field = "failures" if kind == "agent_terminal" else "output_rejections"
                comparisons.append({"evidence_kind": kind, "stage": context[1], "component_id": context[2], "model": context[3],
                    "failure_code": signature[0], "failure_stage": signature[1], "diagnostic": signature[2],
                    "baseline_failed_attempts": len(set(original_ids)), "baseline_attempt_ids": list(dict.fromkeys(original_ids))[-6:],
                    "later_commits": sorted({node["binding"]["commit"] for node in later}),
                    **_agent_samples(later, field, signature),
                    **_versions(later, lambda rows, field=field, signature=signature: _agent_samples(rows, field, signature))})
        later_count = sum(len(items) for items in after.values())
        comparable_samples = any(context in before and context[-1] != "unknown" and not context[-1].startswith("mixed:")
            and any(node["status"] in {"delivered", "undelivered"} for node in members) for context, members in after.items())
        process_before, process_after = defaultdict(list), defaultdict(list)
        for row in processes:
            if row["role"] != event["role"] or row["commit"] is None:
                continue
            context = (row["role"], row["stage"], row["component_id"], row["model"])
            if row["commit"] == event["base_commit"] and ended is not None and (row["ended_at"] or float("inf")) <= ended:
                process_before[context].append(row)
            if (ended is not None and row["started_at"] is not None and row["started_at"] >= ended
                    and row["commit"] != event["base_commit"] and row["source_attempt_id"] != event["attempt_id"]):
                process_after[context].append(row)
        process_comparisons = []
        for context, baseline in process_before.items():
            problems = defaultdict(list)
            for row in baseline:
                if row["terminal_reason"] in {"FAILED", "LOST", "TIMED_OUT"}:
                    problems[(row["terminal_reason"], row["returncode"], row["validation_code"])].append(row["attempt_id"])
            for signature, original_ids in problems.items():
                comparable = context[-1] != "unknown" and not context[-1].startswith("mixed:")
                later = process_after.get(context, []) if comparable else []
                process_comparisons.append({"evidence_kind": "process_terminal", "stage": context[1],
                    "component_id": context[2], "model": context[3], "failure_code": signature[0],
                    "failure_stage": "process_terminal",
                    "diagnostic": f"returncode={signature[1]}; validation_code={signature[2]}",
                    "baseline_failed_attempts": len(original_ids), "baseline_attempt_ids": original_ids[-6:],
                    "later_commits": sorted({row["commit"] for row in later}),
                    **_process_samples(later, signature),
                    **_versions(later, lambda rows, signature=signature: _process_samples(rows, signature), processes=True)})
        process_count = sum(len(items) for items in process_after.values())
        comparable_processes = any(context in process_before and context[-1] != "unknown"
            and not context[-1].startswith("mixed:") and any(row["state"] == "CLOSED"
                and row["terminal_reason"] != "CANCELLED" for row in members)
            for context, members in process_after.items())
        for skill in changed:
            all_comparisons = [*comparisons, *process_comparisons]
            result.append({"role": event["role"], "name": skill, "commit": event["commit"],
                "base_commit": event["base_commit"], "attempt_id": event["attempt_id"],
                "scope": "owner_background",
                "status": "recurrence_observed" if any(c["status"] == "recurrence_observed" for c in all_comparisons)
                    else "observations_available" if any(c["status"] == "not_observed" for c in all_comparisons)
                        or not all_comparisons and (comparable_samples or comparable_processes)
                    else "insufficient_evidence" if any(c["status"] == "insufficient_evidence" for c in all_comparisons)
                    else "awaiting_samples",
                "later_nodes": later_count, "later_processes": process_count,
                "comparisons": comparisons[:24], "comparisons_truncated": len(comparisons) > 24,
                "process_comparisons": process_comparisons[:24],
                "process_comparisons_truncated": len(process_comparisons) > 24})
    return {"followups": result[:limit], "followups_truncated": len(result) > limit}


def runtime_feedback(store, root, run_id, role, component_id, *, max_attempts=256, max_bytes=24576):
    """Bounded metadata only: no Git, transcript reads, new scheduling or writes."""
    import json

    from .observer_details import _request_plugin

    attempts = [dict(row) for row in store.inspect(
        "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT' "
        "AND json_extract(observation_json,'$.request.role')=? "
        "AND json_extract(observation_json,'$.request.component_id')=? "
        "ORDER BY started_at DESC,attempt_id DESC LIMIT ?", (run_id, role, component_id, max_attempts + 1))]
    truncated = len(attempts) > max_attempts
    attempts = list(reversed(attempts[:max_attempts]))
    process_rows = [dict(row) for row in store.inspect(
        "SELECT * FROM execution_attempts WHERE run_id=? AND kind='PROCESS' "
        "ORDER BY started_at DESC,attempt_id DESC LIMIT ?", (run_id, max_attempts + 1))]
    process_truncated = len(process_rows) > max_attempts
    process_rows = list(reversed(process_rows[:max_attempts]))
    transitions = store.inspect("SELECT run_id,workflow_id,workflow_kind,revision,state,created_at,payload_json FROM workflow_transitions "
        "WHERE run_id=? AND (json_extract(payload_json,'$.agent_final_failure')=1 "
        "OR json_extract(payload_json,'$.agent_attempt_id') IS NOT NULL "
        "OR (workflow_kind='EXPERIMENT' AND revision=1))", (run_id,))
    facts = store.inspect("SELECT run_id,fact_id,fact_type,payload_json FROM research_facts "
        "WHERE run_id=? AND fact_type IN ('CanonicalOutcomeAccepted','ExperimentPreregistered')", (run_id,))
    bindings = {row["attempt_id"]: _request_plugin(row, run_id) for row in attempts}
    nodes = outcome_nodes(root, run_id, attempts, bindings, transitions=transitions)
    complete_nodes = [node for node in nodes if not node["costs"]["retry_unknown_nodes"]]
    cards = scorecards(complete_nodes)
    cards["groups"].sort(key=lambda item: (
        not bool(item["failed_attempts"] + item["output_rejection_count"]), -item["last_observed_at"]))
    groups = cards["groups"][:24]
    for group in groups:
        group["failures_truncated"] |= len(group["failures"]) > 3
        group["failures"] = group["failures"][-3:]
        group["output_rejections_truncated"] |= len(group["output_rejections"]) > 3
        group["output_rejections"] = group["output_rejections"][-3:]
        group["attempt_ids"] = group["attempt_ids"][-3:]
    processes = process_outcomes(root, run_id, [*attempts, *process_rows], bindings,
        transitions=transitions, facts=facts)
    process_cards = process_scorecards(processes, role=role, component_id=component_id)
    result = {"scope": {"run_id": run_id, "role": role, "component_id": component_id},
        "basis": "bound_version", "attempts_observed": len(attempts), "history_truncated": truncated,
        "processes_observed": len(process_rows), "process_history_truncated": process_truncated,
        "incomplete_nodes_excluded": len(nodes) - len(complete_nodes),
        "groups": groups, "process_groups": process_cards["groups"],
        **process_repairs(processes, role=role, component_id=component_id),
        "unassigned_processes": process_cards["unassigned_processes"],
        "instruction": "Use original Attempt evidence to follow recurring failures across versions. Binding is not a Skill call or cause. "
            "Record target problem, source refs, changed skill/stage, expected observation and unresolved follow-up in the existing report/evolution summary. "
            "Use only this request's immutable_context_refs in supporting_refs; cite this feedback input and place original Attempt IDs in the prose. "
            "Trace frozen upstream declarations to their proposal; fix at the owning stage. Scientific negative results and cancellations are not skill failures. "
            "Output-rejection and Process cohorts are separate from terminal Agent failures and must not be summed. "
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
                "summary": summary, "summary_truncated": False,
                "status": "unchanged" if update.get("base_commit") == update.get("commit") else "accepted_change"})
    result["updates"] = updates[-8:]
    result["updates_truncated"] = len(updates) > 8
    result = sanitize_payload(result)
    result["groups_truncated"] = cards["groups_truncated"] or len(cards["groups"]) > len(result["groups"])
    result["process_groups_truncated"] = process_cards["groups_truncated"]
    while len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes and result["groups"]:
        result["groups"].pop()
        result["groups_truncated"] = True
    while len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes and result["process_groups"]:
        result["process_groups"].pop()
        result["process_groups_truncated"] = True
    while len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes and result["repairs"]:
        result["repairs"].pop()
        result["repairs_truncated"] = True
    while len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes and len(result["updates"]) > 1:
        result["updates"].pop(0)
        result["updates_truncated"] = True
    if len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes and result["updates"]:
        # Preserve the latest source identity even when its full summary alone
        # exceeds the budget. Measure encoded JSON, including escaped characters.
        update = result["updates"][0]
        summary = update["summary"]
        update["summary_truncated"] = True
        low, high = 0, len(summary)
        while low < high:
            middle = (low + high + 1) // 2
            update["summary"] = summary[:middle]
            if len(json.dumps(result, ensure_ascii=False).encode()) <= max_bytes:
                low = middle
            else:
                high = middle - 1
        update["summary"] = summary[:low]
        if len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes:
            result["updates"].clear()
            result["updates_truncated"] = True
    return result
