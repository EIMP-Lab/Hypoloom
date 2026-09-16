"""Actual MCP/SQLite/preregistration with synthetic GPU inventory and no GPU I/O."""
from functools import partial
from unittest.mock import Mock

import pytest

from hypoloom.profile_v4 import project_profile_v4
from hypoloom.agent_aci import AgentACIError
from hypoloom.workflow_host import WorkflowHost
from test_host_experiment_tools import execution as _execution_fixture, _until
from test_multigpu_resources import _resources
from test_profile_v4 import documents

execution = _execution_fixture


@pytest.fixture(autouse=True)
def configured_gpu_host(tmp_path, monkeypatch):
    import test_host_experiment_tools as fixture_module
    resources = _resources(tmp_path)
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    monkeypatch.setattr(fixture_module, "WorkflowHost", partial(WorkflowHost, compute_resources=resources))
    original = fixture_module._pilot_payload
    def payload(*args):
        result = original(*args)
        contract = result["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False, "rounds": 3}}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["safety"]["max_gpus_per_experiment"] = 2
        settings["goal"]["completion_mode"] = "explicit_signals"
        contract["methodology"]["controls"]["product"] = settings
        result["execution"]["allow_gpu"] = True
        result["execution"]["runtime_prefix"] = str(resources.gpu_runtime_prefix)
        contract["runtime"]["conda_env"] = str(resources.gpu_runtime_prefix)
        return result
    monkeypatch.setattr(fixture_module, "_pilot_payload", payload)


def test_mcp_plural_count_is_frozen_and_reconstructed_without_gpu_spawn(execution):
    host, request, arguments = execution
    arguments = {**arguments, "candidate": {**arguments["candidate"], "resource_preference": "GPU_REQUIRED"}, "gpus": 2}
    host.runner.start = Mock(side_effect=AssertionError("this test must never spawn a GPU process"))
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    experiment = submitted["exp_id"]
    _until(host, lambda: (head := host.store.latest_transition(experiment)) is not None and head["state"] == "READY")
    spec, contract, _, _, _, _, _, capabilities, grant = host._execution_inputs(request.run_id, experiment)
    assert len(grant.device_uuids) == 2
    assert contract["resource_grant"] == {"kind": "GPU", "gpu_uuids": list(grant.device_uuids)}
    assert contract["sandbox_profile"] == "bwrap-gpu-multi-no-network/1"
    assert dict(contract["environment"])["CUDA_VISIBLE_DEVICES"] == ",".join(grant.device_uuids)
    assert set(grant.device_uuids) <= {item.uuid for item in capabilities.gpus}
    assert host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments) == submitted
    assert len(host.store.list_facts(request.run_id, "ExperimentPreregistered")) == 1
    assert not host.store.list_attempts(execution_id=spec["execution_id"])
    host.runner.start.assert_not_called()


def test_mcp_count_above_profile_cap_creates_no_experiment(execution):
    host, request, arguments = execution
    with pytest.raises(AgentACIError, match="max_gpus_per_experiment"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments,
            "candidate": {**arguments["candidate"], "resource_preference": "GPU_REQUIRED"}, "gpus": 3})
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")


def test_host_restart_reconstructs_same_plural_grant_and_submit_identity(execution):
    host, request, arguments = execution
    arguments = {**arguments, "candidate": {**arguments["candidate"], "resource_preference": "GPU_REQUIRED"}, "gpus": 2}
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    experiment = submitted["exp_id"]
    _until(host, lambda: host.store.latest_transition(experiment)["state"] == "READY")
    previous = host._execution_inputs(request.run_id, experiment)[1]
    host.close()
    restarted = WorkflowHost(host.state_root, compute_resources=host.compute_resources)
    try:
        restarted.initialize()
        spec, contract, *rest = restarted._execution_inputs(request.run_id, experiment)
        assert contract == previous
        assert len(rest[-1].device_uuids) == 2
        assert len(restarted.store.list_workflow_heads(request.run_id, "EXPERIMENT")) == 1
        assert not restarted.store.list_attempts(execution_id=spec["execution_id"])
        assert restarted.store.get_attempt(request.attempt_id)["terminal_reason"] == "LOST"
    finally:
        restarted.close()
