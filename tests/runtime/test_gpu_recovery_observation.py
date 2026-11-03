"""Independent pressure evidence and observer counts for the current policy."""
import json

import pytest

from hypoloom.oracle import _audit_process_resource_observation, _audit_resource_recovery
from hypoloom.observer_revisions import revision_activities
from test_multigpu_oracle import renamed_usage, GPU_A, GPU_B
from test_shared_gpu_learning_oracle import proof
from test_observer_revisions import creation, document, outcome
from test_execution_budget_feedback import context


@pytest.mark.parametrize("change", [None, "legacy", "over_budget", "unknown", "missing"])
def test_within_budget_requires_current_policy_and_complete_bounded_use(change):
    sample = proof(resident_bytes=4000 * 1024**2)
    item = renamed_usage(GPU_A, pressure=True, attribution="within_budget")
    item["owned_memory"] = {"sample_count": 1, "unknown_sample_count": 0,
        "latest": sample, "peak": sample}
    second = renamed_usage(GPU_B)
    other_sample = {**proof(), "gpu_uuid": GPU_B}
    second["owned_memory"] = {"sample_count": 1, "unknown_sample_count": 0,
        "latest": other_sample, "peak": other_sample}
    resource = {"expected_kind": "GPU", "gpu_uuids": (GPU_A, GPU_B), "gpu_uuid": None,
        "reserve_memory_mib": 1000, "telemetry_required": True, "budget_pressure": True,
        "intent": {"workload_memory_mib": 18432}, "attributable": True,
        "attribution_attempt": {"process_group_id": 100, "started_at": 10,
            "ended_at": 20, "terminal_reason": "SUCCEEDED"}}
    if change == "legacy": resource["budget_pressure"] = False
    elif change == "over_budget": resource["intent"]["workload_memory_mib"] = 3999
    elif change == "unknown": sample["complete"] = False
    elif change == "missing": item.pop("owned_memory")
    issues = []
    _audit_process_resource_observation({"terminal_reason": "SUCCEEDED", "returncode": 0,
        "resource_usage": [item, second]}, resource, "p", issues)
    assert bool(issues) is (change is not None)


def test_observer_separates_device_recovery_from_exhausted_code_repair():
    host, _, _ = context(faults=(0, 2))
    rows = [creation("e0", 1), creation("e1", 2, repair="out-e0"), creation("e2", 3, repair="out-e1")]
    current = revision_activities(rows, [outcome("e2", "EXECUTION_FAILURE")], document(1),
        agent_aci_version=30, run_state="ACTIVE", process_attempts=host.store.list_attempts())["primary.a"]
    assert current["operational_repairs"] == current["device_recoveries"] == 1
    assert current["scopes"][0]["device_recovery_required"]
    assert current["scopes"][0]["warning"] is None
    legacy = revision_activities(rows, [outcome("e2", "EXECUTION_FAILURE")], document(1),
        agent_aci_version=29, run_state="ACTIVE", process_attempts=host.store.list_attempts())["primary.a"]
    assert legacy["operational_repairs"] == 2 and "device_recoveries" not in legacy


@pytest.mark.parametrize("change", [None, "other_pid", "other_node", "other_source", "xid43", "successful"])
def test_resource_block_requires_owned_failed_process_and_accepted_source(change):
    host, _, _ = context(faults=(0,), count=1)
    process = host.store.list_attempts()[0]
    process.update(attempt_id="p", run_id="r", experiment_id="e0")
    observed = json.loads(process["observation_json"])
    request = {"schema_version": 30, "command_id": "n", "command": "EXPERIMENT_EXECUTION",
        "role": "expert", "component_id": "c"}
    source = {"attempt_id": "a", "kind": "AGENT", "run_id": "r", "command_id": "n", "state": "CLOSED"}
    block = {"code": "gpu_device_fault", "tool": "process", "attempt_id": "a", "process_attempt_id": "p",
        "node_command_id": "n", "role": "expert", "component_id": "c"}
    if change == "other_pid": observed["gpu_diagnostic"]["events"][0]["pid"] += 1
    elif change == "other_node": block["node_command_id"] = "foreign"
    elif change == "xid43": observed["gpu_diagnostic"]["events"][0]["xid"] = 43
    elif change == "successful": process["terminal_reason"] = "SUCCEEDED"
    commands = [{"command_id": "b", "command_type": "RESOURCE_BLOCKED", "workflow_id": "r"},
        {"command_id": "s", "workflow_id": "foreign" if change == "other_source" else "e0",
         "command_type": "CREATE_EXPERIMENT", "request_json": json.dumps({"source_request": request, "tool": "submit_experiment"})}]
    transitions = [{"command_id": "b", "workflow_id": "r", "run_id": "r", "previous_state": "ACTIVE",
        "state": "ACTIVE", "payload_json": json.dumps({"resource_blocked": block})}]
    issues = []
    _audit_resource_recovery(commands, transitions, [source, process], {"a": {"request": request}, "p": observed}, issues)
    assert bool(issues) is (change is not None)
