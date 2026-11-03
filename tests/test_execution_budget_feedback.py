"""Recovery does not consume code repairs or silently raise finite limits."""
import json
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentACIError
from hypoloom.execution_budget import execution_budget, check_replacement_budget, research_round_budget
from test_gpu_failure_diagnostics import failed_process, event, GPU


def context(faults=(), count=3, version=30):
    document = {"budget": {"repair_attempts": 2}}
    request = SimpleNamespace(run_id="r", role="expert", command_id="node", schema_version=version)
    members = [{"exp_id": f"e{i}", "execution_node_command_id": "node"} for i in range(count)]
    attempts = []
    for index in range(count):
        observed = failed_process()
        if index in faults:
            observed["gpu_diagnostic"] = {"source": "linux_kmsg", "status": "complete", "reason": None,
                "wall_monotonic_offset_seconds": 1000.0, "clock_ticks_per_second": 100,
                "events": [{**event(), "gpu_uuid": GPU}]}
        attempts.append({"kind": "PROCESS", "state": "CLOSED", "terminal_reason": "FAILED",
            "experiment_id": f"e{index}", "attempt_ordinal": 1, "observation_json": json.dumps(observed)})
    host = SimpleNamespace(_research_contract=lambda _: SimpleNamespace(document=document),
        _agent_attempt_limits=lambda _: (3, 3), _owned_tool_experiments=lambda _: members,
        store=SimpleNamespace(list_attempts=lambda **kw: attempts),
        resource_recovery=SimpleNamespace(for_request=lambda _: None))
    return host, request, members


def test_ordinary_repairs_and_device_recoveries_are_counted_from_predecessors():
    host, request, members = context(faults=(1,))
    budget = execution_budget(host, request)
    assert budget["initial_submissions"] == 1
    assert budget["repairs_used"] == budget["device_recoveries_used"] == 1
    assert budget["repairs_remaining"] == budget["device_recoveries_remaining"] == 1
    check_replacement_budget(host, request, members, "e2")


def test_device_failure_still_has_an_independent_finite_ceiling():
    host, request, members = context(faults=(0, 1, 2))
    budget = execution_budget(host, request)
    assert budget["repairs_used"] == 0 and budget["repairs_remaining"] == 2
    assert budget["device_recoveries_used"] == 2 and budget["device_recoveries_remaining"] == 0
    with pytest.raises(AgentACIError) as error:
        check_replacement_budget(host, request, members, "e2")
    assert error.value.code == "device_recovery_budget_exhausted"


def test_ordinary_error_and_legacy_evidence_cannot_earn_extra_repairs():
    for faults, version in [((), 30), ((0, 1, 2), 29)]:
        host, request, members = context(faults=faults, version=version)
        with pytest.raises(AgentACIError) as error:
            check_replacement_budget(host, request, members, "e2")
        assert error.value.code == "repair_budget_exhausted"
        assert "used=2, limit=2" in str(error.value)


def test_zero_remaining_rounds_is_visible_independently_of_repair_budget():
    task = SimpleNamespace(key=SimpleNamespace(cycle=3, experiment_ordinal=1))
    result = research_round_budget(task, {"rounds": 3, "iterations": 4, "repairs": 2})
    assert result["current_round"] == 3 and result["remaining_future_rounds"] == 0
    assert result["current_scientific_iteration"] == 1
    assert result["repair_attempts_per_execution_node"] == 2
