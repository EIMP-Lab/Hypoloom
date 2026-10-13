"""Read-only experiment activity over accepted creation rows and outcomes.

The existing batch identities and admitted budget own the meaning of a repair.
This projection neither schedules work nor diagnoses scientific convergence.
"""
from __future__ import annotations

import json
from collections import defaultdict

from .contract_normalization import EXPERT_LOOP_RESEARCH_LOOP
from .domain import REVISIONABLE_INTEGRITY_VALIDATION_CODES
from .expert_batch import batch_limits, supports_hypothesis_batches
from .policy import _REVISION_BREAKER_MAX, _REVISION_BREAKER_SEVERE, _REVISION_BREAKER_WARN


def warning_level(repairs):
    if repairs > _REVISION_BREAKER_MAX:
        return "tripped"
    if repairs > _REVISION_BREAKER_SEVERE:
        return "severe"
    if repairs > _REVISION_BREAKER_WARN:
        return "general"
    return None


def _payload(row):
    return row["payload"] if "payload" in row else json.loads(row["payload_json"])


def _positive(value):
    return type(value) is int and value > 0


def revision_activities(rows, outcomes, document, *, agent_aci_version, run_state):
    """Project latest-cycle totals and exact execution-node repair scopes.

    Missing batch identity is unknown, never a fabricated legacy single chain.
    Oracle remains responsible for validating accepted causal relationships.
    """
    batch = (supports_hypothesis_batches(agent_aci_version)
        and document.get("methodology", {}).get("controls", {}).get("research_loop") == EXPERT_LOOP_RESEARCH_LOOP)
    by_component = defaultdict(dict)
    accepted_cycles = {}
    for row in rows:
        if batch and row["workflow_kind"] == "RUN" and row.get("command_type", row.get("command")) == "ACCEPT_HYPOTHESIS":
            payload = _payload(row)
            accepted = payload.get("hypothesis_batch", {})
            component = payload.get("component_id")
            if isinstance(component, str) and isinstance(accepted, dict) and _positive(accepted.get("cycle")):
                accepted_cycles[component] = max(accepted["cycle"], accepted_cycles.get(component, 0))
                by_component[component]  # An accepted new cycle can have no experiments yet.
        if row["workflow_kind"] != "EXPERIMENT" or int(row["revision"]) != 1:
            continue
        payload = _payload(row)
        component = payload.get("component_id")
        if isinstance(component, str) and component:
            by_component[component][str(row["workflow_id"])] = payload
    outcomes_by_experiment = {str(row["experiment_id"]): _payload(row) for row in outcomes}
    result = {}
    for component, creations in by_component.items():
        cycles = [p.get("expert_cycle", None if batch else 1) for p in creations.values()]
        valid_cycles = all(_positive(cycle) for cycle in cycles)
        latest_cycle = max([*cycles, accepted_cycles.get(component, 1)]) if valid_cycles else None
        selected = {eid: p for eid, p in creations.items()
            if latest_cycle is not None and p.get("expert_cycle", 1) == latest_cycle}
        activity = {"mode": "batch" if batch else "legacy", "complete": valid_cycles,
            "cycle": latest_cycle, "initial_experiments": 0, "scientific_iterations": 0,
            "operational_repairs": 0, "scopes": []}
        groups = defaultdict(list)
        for eid, p in selected.items():
            if batch:
                strings = (p.get("role"), p.get("chain"), p.get("execution_node_command_id"))
                if (not all(isinstance(v, str) and v for v in strings)
                        or not _positive(p.get("chain_iteration")) or not _positive(p.get("experiment_ordinal"))):
                    activity["complete"] = False
                    continue
                key = (strings[0], strings[1], p["chain_iteration"], strings[2])
            else:
                key = (p.get("role"), None, None, None)
            groups[key].append((eid, p))
        latest_iterations = {}
        for role, chain, iteration, command in groups:
            if batch:
                latest_iterations[(role, chain)] = max(iteration, latest_iterations.get((role, chain), 0))
        for (role, chain, iteration, command), members in sorted(groups.items(), key=lambda item: str(item[0])):
            members.sort(key=lambda item: (item[1].get("experiment_ordinal", 1), item[0]))
            if batch:
                repairs = sum(bool(p.get("revision_of_outcome_id") or p.get("preflight_repair_of_experiment_id")) for _, p in members)
                # A repaired scientific iteration retains iteration_of_experiment_id.
                roots = [p for _, p in members if not (p.get("revision_of_outcome_id") or p.get("preflight_repair_of_experiment_id"))]
                if (len(roots) != 1 or any(bool(p.get("iteration_of_experiment_id")) != (iteration > 1) for p in roots)):
                    activity["complete"] = False
                activity["initial_experiments"] += sum(not p.get("iteration_of_experiment_id") for p in roots)
                activity["scientific_iterations"] += sum(bool(p.get("iteration_of_experiment_id")) for p in roots)
                try:
                    limit = batch_limits(document, role)["repairs"]
                except (TypeError, ValueError):
                    limit = None
            else:
                repairs, limit = len(members) - 1, None
                activity["initial_experiments"] += 1
            activity["operational_repairs"] += repairs
            tip = members[-1][0]
            outcome = outcomes_by_experiment.get(tip, {})
            recovered = outcome.get("outcome") in {"SUCCESS", "CLEAN_REFUTATION"}
            historical = run_state != "ACTIVE" or (batch and iteration != latest_iterations[(role, chain)])
            warning = None
            if not recovered:
                if not batch or repairs > _REVISION_BREAKER_MAX:
                    warning = warning_level(repairs)
                elif (limit is not None and repairs >= limit
                        and (outcome.get("outcome") == "EXECUTION_FAILURE"
                            or outcome.get("outcome") == "INTEGRITY_REJECTED"
                            and outcome.get("validation_code") in REVISIONABLE_INTEGRITY_VALIDATION_CODES)):
                    warning = "allowance_used"
            activity["scopes"].append({"chain": chain, "iteration": iteration,
                "execution_node_command_id": command, "repairs": repairs, "repair_limit": limit,
                "experiment_ids": [eid for eid, _ in members], "latest_experiment_id": tip,
                "latest_outcome": outcome.get("outcome"),
                "validation_code": outcome.get("validation_code"),
                "recovered": recovered, "historical": historical, "warning": warning})
        if not activity["complete"]:
            for key in ("initial_experiments", "scientific_iterations", "operational_repairs"):
                activity[key] = None
        result[component] = activity
    return result


def activity_warning(activity):
    levels = {"general": 1, "severe": 2, "allowance_used": 3, "tripped": 4}
    warnings = [s["warning"] for s in activity.get("scopes", ()) if s.get("warning")]
    return max(warnings, key=levels.__getitem__) if warnings else None
