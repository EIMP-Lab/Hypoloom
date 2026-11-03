"""Execution allowances projected from accepted experiments, never new counters."""
from __future__ import annotations

import json

from .agent_aci import AgentACIError, AgentRequest
from .expert_batch import batch_limits
from .gpu_diagnostics import confirmed_gpu_fault


def device_fault_experiments(attempts):
    latest = {}
    for row in attempts:
        if row["kind"] == "PROCESS":
            key = row.get("experiment_id", row.get("workflow_id"))
            if key not in latest or row.get("attempt_ordinal", 0) > latest[key].get("attempt_ordinal", 0):
                latest[key] = row
    return {key for key, row in latest.items() if row["state"] == "CLOSED"
        and row.get("terminal_reason") == "FAILED"
        and confirmed_gpu_fault(json.loads(row.get("observation_json") or "{}"))}


def execution_budget(host, request, owned=None):
    contract = host._research_contract(request.run_id)
    limit = batch_limits(contract.document, request.role)["repairs"]
    members = [row for row in (host._owned_tool_experiments(request) if owned is None else owned)
        if row.get("execution_node_command_id") == request.command_id]
    device_limit = host._agent_attempt_limits(contract)[1] - 1 if request.schema_version >= 30 else 0
    faults = device_fault_experiments(host.store.list_attempts(run_id=request.run_id)) if request.schema_version >= 30 else set()
    recoveries = sum(row["exp_id"] in faults for row in members[:-1])
    repairs = max(0, len(members) - 1) - recoveries
    kind = "device_recovery" if members and members[-1]["exp_id"] in faults else "repair"
    blocked = bool(host.resource_recovery.for_request(request))
    return {"initial_submissions": int(bool(members)), "repair_limit": limit,
        "repairs_used": repairs, "repairs_remaining": max(0, limit - repairs),
        "device_recovery_limit": device_limit, "device_recoveries_used": recoveries,
        "device_recoveries_remaining": max(0, device_limit - recoveries),
        "next_replacement_kind": kind, "resource_recheck_required": blocked,
        "replacement_budget_available": not blocked and (recoveries < device_limit if kind == "device_recovery" else repairs < limit)}


def check_replacement_budget(host, request, owned, predecessor):
    if predecessor is None:
        return
    budget = execution_budget(host, request, owned)
    kind = budget["next_replacement_kind"]
    remaining = budget["device_recoveries_remaining"] if kind == "device_recovery" else budget["repairs_remaining"]
    if not remaining:
        used, limit = ((budget["device_recoveries_used"], budget["device_recovery_limit"])
            if kind == "device_recovery" else (budget["repairs_used"], budget["repair_limit"]))
        raise AgentACIError("device_recovery_budget_exhausted" if kind == "device_recovery" else "repair_budget_exhausted",
            f"execution node exhausted its {'device recovery' if kind == 'device_recovery' else 'repair'} attempts "
            f"(used={used}, limit={limit}); the initial submission is not a repair. "
            "Finish with the recorded technical result; another research round requires remaining round budget.")


def experiment_execution_feedback(host, spec, observation):
    result = {}
    if observation.get("gpu_diagnostic") is not None:
        result["gpu_diagnostic"] = observation["gpu_diagnostic"]
        if confirmed_gpu_fault(observation):
            result.update(failure_category="gpu_device_fault", repair_instruction=
                "An owned GPU process has a correlated Xid 38 driver/firmware event. "
                "Inspect the device and driver, then use operator GPU resource recheck. "
                "Do not treat empty_cache or a code retry as a device repair.")
    if spec is None:
        return result
    source = host._tool_command(spec["payload"]["execution_contract"].get("source_submit_command_id"))
    if source is not None:
        request = AgentRequest.from_dict(json.loads(source["request_json"])["source_request"])
        if request.schema_version >= 30:
            result["execution_budget"] = execution_budget(host, request)
    return result


def research_round_budget(task, limits):
    return {"current_round": task.key.cycle, "max_rounds_per_expert": limits["rounds"],
        "remaining_future_rounds": max(0, limits["rounds"] - task.key.cycle),
        "current_scientific_iteration": task.key.experiment_ordinal,
        "max_iterations_per_hypothesis": limits["iterations"],
        "repair_attempts_per_execution_node": limits["repairs"],
        "round_definition": "One research cycle: literature, hypotheses, design/execution/analysis, adjudication, update, report and evolution. Repairs do not start new rounds."}
