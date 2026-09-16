"""Native requests freeze owned GPU occupancy; no physical GPU is inspected."""

import json
import sqlite3
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hypoloom.agent_aci import AgentRequest
from hypoloom.compute_resources import ComputeCapabilities
from hypoloom.domain import ResourceKind
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from test_compute_resources import GPU_A, GPU_B, capabilities
from test_host_experiment_tools import _until, execution as execution
from test_multigpu_resources import _resources
from test_profile_v4 import documents


@pytest.fixture
def occupied():
    return (GPU_A.uuid,)


@pytest.fixture(autouse=True)
def native_gpu_host(tmp_path, monkeypatch, occupied):
    import test_host_experiment_tools as fixture_module

    inventory = capabilities(replace(GPU_A, free_memory_mib=23_000),
                             replace(GPU_B, free_memory_mib=13_000))
    resources = _resources(tmp_path, lambda: inventory, allowed=(GPU_A.uuid, GPU_B.uuid))
    # Reuse the real ownership query over synthetic OPEN Process/preregistration
    # rows. The fresh MCP Host remains real; no GPU process needs to be spawned.
    ownership = sqlite3.connect(":memory:")
    ownership.row_factory = sqlite3.Row
    ownership.executescript(
        "CREATE TABLE execution_attempts(workflow_id,run_id,execution_id,kind,state,attempt_id);"
        "CREATE TABLE research_facts(run_id,execution_id,fact_type,payload_json);"
        "CREATE TABLE workflow_transitions(run_id,workflow_id,payload_json);"
    )
    for uuid in (GPU_A.uuid, GPU_B.uuid):
        identity = "owned-" + uuid
        ownership.execute("INSERT INTO execution_attempts VALUES(?,?,?,?,?,?)",
                          (identity, "peer-run", identity, "PROCESS", "OPEN" if uuid in occupied else "CLOSED", identity + "-attempt"))
        ownership.execute("INSERT INTO research_facts VALUES(?,?,?,?)", (
            "peer-run", identity, "ExperimentPreregistered",
            json.dumps({"execution_contract": {"resource_grant": {"kind": "GPU", "gpu_uuid": uuid}}}),
        ))
    source = SimpleNamespace(store=SimpleNamespace(
        inspect=lambda query: [dict(row) for row in ownership.execute(query)]))
    busy = WorkflowHost._busy_gpu_uuids
    monkeypatch.setattr(WorkflowHost, "_busy_gpu_uuids", lambda _host, exclude_workflow=None: busy(source, exclude_workflow))
    monkeypatch.setattr(fixture_module, "WorkflowHost", partial(WorkflowHost, compute_resources=resources))
    original = fixture_module._pilot_payload

    def payload(*args):
        result = original(*args)
        contract = result["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["safety"]["max_gpus_per_experiment"] = 1
        contract["methodology"]["controls"]["product"] = settings
        result["execution"]["allow_gpu"] = True
        result["execution"]["runtime_prefix"] = str(resources.gpu_runtime_prefix)
        contract["runtime"]["conda_env"] = str(resources.gpu_runtime_prefix)
        return result

    monkeypatch.setattr(fixture_module, "_pilot_payload", payload)
    try:
        yield ownership
    finally:
        ownership.close()


def _capabilities(request):
    item = next(item for item in request.input_artifacts if item.ref.endswith("--compute-capabilities"))
    assert item.ref in request.immutable_context_refs
    return ComputeCapabilities.from_bytes(item.content_bytes())


@pytest.mark.parametrize("command", ["EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"])
def test_native_request_capabilities_include_host_owned_open_process_gpu(execution, command):
    host, request, _ = execution
    if command != request.command.value:
        row = next(row for row in host.store.list_attempts() if row["kind"] == "AGENT"
                   and json.loads(row["observation_json"])["request"]["command"] == command)
        request = AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
    snapshot = _capabilities(request)
    assert host._busy_gpu_uuids() == (GPU_A.uuid,)
    assert snapshot.busy_gpu_uuids == (GPU_A.uuid,)
    assert {item.uuid: item.free_memory_mib for item in snapshot.gpus} == {
        GPU_A.uuid: 23_000, GPU_B.uuid: 13_000,
    }


def _submit_gpu(execution):
    host, request, arguments = execution
    arguments = {**arguments, "candidate": {**arguments["candidate"], "resource_preference": "GPU_REQUIRED"}, "gpus": 1}
    host.runner.start = Mock(side_effect=AssertionError("this test must never spawn a GPU process"))
    submitted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: host.store.latest_transition(submitted["exp_id"])["state"] == "READY")
    return submitted, arguments


def test_mcp_peer_chooses_less_free_unoccupied_gpu_and_replay_keeps_frozen_grant(execution, native_gpu_host):
    host, request, _ = execution
    submitted, arguments = _submit_gpu(execution)
    experiment = submitted["exp_id"]
    spec, contract, *rest = host._execution_inputs(request.run_id, experiment)
    grant = rest[-1]
    assert grant.kind is ResourceKind.GPU and grant.device_uuids == (GPU_B.uuid,)
    assert contract["resource_grant"] == {"kind": "GPU", "gpu_uuid": GPU_B.uuid}
    assert dict(contract["environment"])["CUDA_VISIBLE_DEVICES"] == GPU_B.uuid
    frozen_request = request.to_dict()
    # Current occupancy reverses after admission. Replay/preregistration still
    # use the exact captured request, not the new live availability.
    native_gpu_host.execute("UPDATE execution_attempts SET state=CASE WHEN workflow_id=? THEN 'OPEN' ELSE 'CLOSED' END",
                            ("owned-" + GPU_B.uuid,))
    assert host._busy_gpu_uuids() == (GPU_B.uuid,)
    assert host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments) == submitted
    assert host._execution_inputs(request.run_id, experiment)[1] == contract
    assert host._execution_inputs(request.run_id, experiment)[-1] == grant
    assert request.to_dict() == frozen_request
    assert len(host.store.list_facts(request.run_id, "ExperimentPreregistered")) == 1
    assert not host.store.list_attempts(execution_id=spec["execution_id"])
    host.runner.start.assert_not_called()


@pytest.mark.parametrize("occupied", [(GPU_A.uuid, GPU_B.uuid)])
def test_both_owned_busy_required_gpu_keeps_a_nonempty_frozen_queue_grant(execution):
    host, request, _ = execution
    submitted, arguments = _submit_gpu(execution)
    experiment = submitted["exp_id"]
    spec, contract, *rest = host._execution_inputs(request.run_id, experiment)
    grant = rest[-1]
    assert grant.kind is ResourceKind.GPU and grant.device_uuids == (GPU_A.uuid,)
    assert _capabilities(request).busy_gpu_uuids == (GPU_A.uuid, GPU_B.uuid)
    host._start_process(request.run_id, experiment, retry=False)
    assert host.resource_waits[experiment] == (request.run_id, "frozen GPU is busy in this state root")
    assert host.store.latest_transition(experiment)["state"] == "READY"
    assert not host.store.list_attempts(execution_id=spec["execution_id"])
    assert host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments) == submitted
    assert host._execution_inputs(request.run_id, experiment)[1] == contract
    host.runner.start.assert_not_called()
