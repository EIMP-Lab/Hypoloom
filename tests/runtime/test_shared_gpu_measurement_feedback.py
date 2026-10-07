"""Host-owned measurement feedback through real submit/store/pool boundaries.

GPU inventory, NVML byte evidence and workload execution are explicitly
synthetic. No CUDA or paid Agent work is performed by this module.
"""

import ast
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.domain import AttemptTerminalReason
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.expert_loop import ResearchNodeKind
from hypoloom.fake_agent import FakeAgent
from hypoloom.gpu_attribution import GpuAttributionSampler
from hypoloom.oracle import Oracle
from hypoloom.runner import ProcessObservation
from test_gpu_host_recovery import _DeterministicRunner, _output_binding
from test_host_experiment_tools import _terminal, _until
from test_multigpu_resources import GPU_A, GPU_B, GPU_C
from test_shared_gpu_learning import gpu_arguments
from test_shared_gpu_learning import configured_gpu_host as configured_gpu_host
from test_shared_gpu_learning import current_aci as current_aci
from test_shared_gpu_learning import execution as execution
from test_shared_gpu_learning import shared_pool as shared_pool

MIB = 1024**2


class _MeasuredRunner(_DeterministicRunner):
    def wait(self, handle):
        reason = (
            AttemptTerminalReason.CANCELLED
            if handle.cancelled
            else (
                AttemptTerminalReason.SUCCEEDED
                if handle.returncode == 0
                else AttemptTerminalReason.FAILED
            )
        )
        return ProcessObservation(
            attempt_id=handle.request.attempt_id,
            attempt_ordinal=handle.request.attempt_ordinal,
            execution_id=handle.request.execution_id,
            terminal_reason=reason,
            returncode=handle.returncode,
            pid=handle.pid,
            process_group_id=handle.process_group_id,
            started_at=handle.started_at,
            ended_at=time.monotonic(),
            stdout_path=handle.request.stdout_path,
            stderr_path=handle.request.stderr_path,
        )


def _configured(execution, shared_pool, monkeypatch, *, complete=True):
    host, request, arguments = execution
    free, observe = shared_pool
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    monkeypatch.setattr(host.compute_resources, "_observation_provider", observe)
    host.runner = _MeasuredRunner(complete=False)
    measurement = {"mib": 16000, "complete": complete}

    def sample(self, uuids, roots):
        result = {}
        for key, pgid in roots.items():
            own = measurement["complete"]
            process = {
                "pid": pgid + 100,
                "resident_bytes": measurement["mib"] * MIB,
                "ancestry": [
                    {"pid": pgid + 100, "ppid": pgid, "start_time_ticks": 101},
                    {"pid": pgid, "ppid": 1, "start_time_ticks": 100},
                ],
            }
            result[key] = {
                uuid: {
                    "gpu_uuid": uuid,
                    "root_process_group_id": pgid,
                    "root_start_time_ticks": 100,
                    "complete": own,
                    "reason": None if own else "synthetic_nvml_unavailable",
                    "observed_at": time.time(),
                    "duration_seconds": 0.001,
                    "resident_bytes": measurement["mib"] * MIB if own else None,
                    "processes": [process] if own else [],
                }
                for uuid in uuids
            }
        return result

    monkeypatch.setattr(GpuAttributionSampler, "sample", sample)
    return host, request, gpu_arguments(arguments), free, measurement


def _running(host, submitted):
    experiment = submitted["exp_id"]
    _until(
        host,
        lambda: any(
            row["workflow_id"] == experiment
            and row["state"] == "OPEN"
            and row["kind"] == "PROCESS"
            for row in host.store.list_attempts()
        ),
    )
    spec = host.store.find_fact("host-tools", "ExperimentPreregistered", experiment)
    process = host.store.list_attempts(execution_id=spec["execution_id"])[-1]
    return spec, host.running[process["attempt_id"]]


def _close(host, submitted, handle, *, success=False, arguments=None, malformed_result=False):
    if success:
        # Decode literal fixture output; do not execute its GPU-shaped source.
        source = next(
            WorkspaceArtifact.from_dict(item).content_bytes()
            for item in arguments["workspace_files"]
            if item["path"] == "experiment.py"
        )
        literals = {}
        for node in ast.parse(source).body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                if node.targets[0].id in {"result", "logical_files"}:
                    literals[node.targets[0].id] = json.loads(
                        ast.literal_eval(node.value.args[0])
                    )
        output = _output_binding(handle.request)
        for relative, content in literals["logical_files"].items():
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (output / "result.json").write_text(
            json.dumps({"invalid_fixture_result": True} if malformed_result else literals["result"]), encoding="utf-8"
        )
    else:
        (Path(handle.request.cwd) / handle.request.stderr_path).write_text(
            "RuntimeError: synthetic workload failure\n", encoding="utf-8"
        )
    handle.returncode = 0 if success else 1
    _until(host, lambda: _terminal(host, submitted["exp_id"]))
    process = host.store.get_attempt(handle.request.attempt_id)
    assert process["state"] == "CLOSED"
    assert process["terminal_reason"] == ("SUCCEEDED" if success else "FAILED")
    return process, json.loads(process["observation_json"])


@pytest.fixture
def calibrating_execution(shared_pool, monkeypatch, request):
    import test_host_experiment_tools as fixture
    original = fixture._pilot_payload

    def payload(*args):
        result = original(*args)
        result["research_contract"]["budget"].update(min_vram_per_experiment=1024,
            initial_vram_per_experiment=8192, gpu_vram_reserve_mb=4096)
        return result

    monkeypatch.setattr(fixture, "_pilot_payload", payload)
    return request.getfixturevalue("execution")


def test_measured_success_releases_initial_budget_and_dispatches_in_previously_insufficient_capacity(
    calibrating_execution, shared_pool, monkeypatch,
):
    host, request, arguments, free, measurement = _configured(calibrating_execution, shared_pool, monkeypatch)
    arguments = gpu_arguments(arguments, peak=2200)
    measurement["mib"] = 2000
    view = host.handle_agent_tool(request.attempt_id, "get_resources", {})
    assert view["profile_workload_floor_mib"] == 1024
    assert view["profile_initial_workload_mib"] == 8192
    assert view["profile_reserve_memory_mib"] == 4096
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    original, handle = _running(host, first)
    assert original["payload"]["execution_contract"]["resource_intent"]["workload_memory_mib"] == 8192
    host.tick()
    # The synthetic workload exits successfully and has complete owned samples.
    # A malformed result permits a packaging repair of that same workload.
    process, observation = _close(host, first, handle, success=True, arguments=arguments, malformed_result=True)
    assert observation["resource_usage"]["owned_memory"]["unknown_sample_count"] == 0
    _until(host, lambda: bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"]})["outcome"]["outcome"] == "EXECUTION_FAILURE"

    free.update({GPU_A.uuid: 11271, GPU_B.uuid: 1000, GPU_C.uuid: 1000})
    raw_repair = {**arguments, "replaces_experiment_id": first["exp_id"]}
    repair = host.handle_agent_tool(request.attempt_id, "submit_experiment", raw_repair)
    frozen, handle = _running(host, repair)
    intent = frozen["payload"]["execution_contract"]["resource_intent"]
    assert intent["workload_memory_mib"] == 2560
    assert intent["reserve_memory_mib"] == 4096
    _, _, source, _ = host._submission_source(frozen["payload"]["execution_contract"]["source_submit_command_id"])
    assert source["arguments"]["resource_estimate"]["measurement_attempt_ids"] == [process["attempt_id"]]
    host.tick()
    _, completed = _close(host, repair, handle, success=True, arguments=arguments)
    assert completed["resource_usage"]["gpu_uuid"] == GPU_A.uuid
    with monkeypatch.context() as scoped:
        scoped.setattr(host.shared_gpu, "resolve_arguments", lambda *args: pytest.fail("replay consulted new history"))
        assert host.handle_agent_tool(request.attempt_id, "submit_experiment", raw_repair) == repair
    assert host.store.find_fact(request.run_id, "ExperimentPreregistered", first["exp_id"]) == original
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def _first_failure(execution, shared_pool, monkeypatch, *, complete=True):
    host, request, arguments, free, measurement = _configured(
        execution, shared_pool, monkeypatch, complete=complete
    )
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    spec, handle = _running(host, first)
    free[GPU_A.uuid] = 5000  # Own16GiB now fits within the sampled device-used amount.
    host.tick()
    assert handle.request.attempt_id in host.shared_gpu.owned_samples
    process, observation = _close(host, first, handle)
    assert observation["resource_usage"]["gpu_uuid"] == GPU_A.uuid
    _until(host, lambda: bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    assert (
        host.handle_agent_tool(
            request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"]}
        )["outcome"]["outcome"]
        == "EXECUTION_FAILURE"
    )
    return (
        host,
        request,
        arguments,
        free,
        measurement,
        first,
        spec,
        process,
        observation,
    )


def test_measured_failure_changes_repair_budget_and_device_but_not_exact_replay(
    execution, shared_pool, monkeypatch
):
    (
        host,
        request,
        arguments,
        free,
        measurement,
        first,
        original,
        process,
        observation,
    ) = _first_failure(execution, shared_pool, monkeypatch)
    owned = observation["resource_usage"]["owned_memory"]
    assert owned["sample_count"] > 0 and owned["unknown_sample_count"] == 0
    assert owned["peak"]["resident_bytes"] == 16000 * MIB
    raw_repair = {**arguments, "replaces_experiment_id": first["exp_id"]}
    assert raw_repair["resource_estimate"]["measurement_attempt_ids"] == []
    repair = host.handle_agent_tool(request.attempt_id, "submit_experiment", raw_repair)
    _until(
        host, lambda: host.store.latest_transition(repair["exp_id"])["state"] == "READY"
    )
    frozen = host.store.find_fact(
        request.run_id, "ExperimentPreregistered", repair["exp_id"]
    )
    contract = frozen["payload"]["execution_contract"]
    assert (
        original["payload"]["execution_contract"]["resource_intent"][
            "workload_memory_mib"
        ]
        == 12000
    )
    assert contract["resource_intent"]["workload_memory_mib"] == 20032
    command, source_request, source, validated = host._submission_source(
        contract["source_submit_command_id"]
    )
    captured = json.loads(command["request_json"])
    assert captured["arguments"] == raw_repair
    assert source["arguments"]["resource_estimate"]["measurement_attempt_ids"] == [
        process["attempt_id"]
    ]
    plan = host._submission_execution_plan(
        source_request, validated.candidate, source["arguments"]
    )
    free.update({GPU_A.uuid: 12000, GPU_B.uuid: 22000, GPU_C.uuid: 1000})
    measurement["mib"] = 17000
    _, handle = _running(host, repair)
    host.tick()
    repaired, repaired_observation = _close(
        host, repair, handle, success=True, arguments=arguments
    )
    assert repaired_observation["resource_usage"]["gpu_uuid"] == GPU_B.uuid
    assert (
        repaired_observation["resource_usage"]["owned_memory"]["peak"]["resident_bytes"]
        == 17000 * MIB
    )
    history = host.handle_agent_tool(request.attempt_id, "get_resources", {})[
        "measurements"
    ]
    assert {item["attempt_id"] for item in history} == {
        process["attempt_id"],
        repaired["attempt_id"],
    }
    with monkeypatch.context() as scoped:
        scoped.setattr(
            host.shared_gpu,
            "resolve_arguments",
            lambda *args: pytest.fail("exact replay consulted new history"),
        )
        scoped.setattr(
            host.shared_gpu.client,
            "request",
            lambda *args: pytest.fail("source replay consulted live pool"),
        )
        assert (
            host.handle_agent_tool(request.attempt_id, "submit_experiment", raw_repair)
            == repair
        )
        assert (
            host._submission_execution_plan(
                source_request, validated.candidate, source["arguments"]
            )
            == plan
        )
    assert (
        host.store.find_fact(
            request.run_id, "ExperimentPreregistered", repair["exp_id"]
        )
        == frozen
    )
    captured_expert_inputs = {}
    expert_inputs = host._expert_inputs

    def capture(run_id, task, component_id, role, method_family_id):
        inputs = expert_inputs(run_id, task, component_id, role, method_family_id)
        if getattr(task, "key", None) is not None and task.key.node_kind in {
            ResearchNodeKind.CYCLE_REPORT,
            ResearchNodeKind.SELF_EVOLVE,
        }:
            experience = next(
                item for item in inputs if item.ref.endswith("--resource-experience")
            )
            captured_expert_inputs[task.key.node_kind] = json.loads(
                experience.content_bytes()
            )
        return inputs

    monkeypatch.setattr(host, "_expert_inputs", capture)
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(
        host,
        lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED",
    )
    assert set(captured_expert_inputs) == {
        ResearchNodeKind.CYCLE_REPORT,
        ResearchNodeKind.SELF_EVOLVE,
    }
    for experience in captured_expert_inputs.values():
        assert experience["mode"] == "shared-pool"
        assert {item["attempt_id"] for item in experience["measurements"]} == {
            process["attempt_id"],
            repaired["attempt_id"],
        }
        assert "ancestry" not in json.dumps(experience)
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_unknown_owned_sample_does_not_raise_repair_budget(
    execution, shared_pool, monkeypatch
):
    host, request, arguments, _, _, first, _, process, observation = _first_failure(
        execution, shared_pool, monkeypatch, complete=False
    )
    owned = observation["resource_usage"]["owned_memory"]
    assert owned["unknown_sample_count"] > 0 and owned["peak"] is None
    repair = host.handle_agent_tool(
        request.attempt_id,
        "submit_experiment",
        {**arguments, "replaces_experiment_id": first["exp_id"]},
    )
    _until(
        host, lambda: host.store.latest_transition(repair["exp_id"])["state"] == "READY"
    )
    spec = host.store.find_fact(
        request.run_id, "ExperimentPreregistered", repair["exp_id"]
    )
    assert (
        spec["payload"]["execution_contract"]["resource_intent"]["workload_memory_mib"]
        == 12000
    )
    _, _, source, _ = host._submission_source(
        spec["payload"]["execution_contract"]["source_submit_command_id"]
    )
    assert source["arguments"]["resource_estimate"]["measurement_attempt_ids"] == []
    history = host.handle_agent_tool(request.attempt_id, "get_resources", {})[
        "measurements"
    ]
    assert (
        history[0]["attempt_id"] == process["attempt_id"]
        and history[0]["measurement_status"] == "unknown"
    )
    assert history[0]["sample_peak_bytes"] is None


@pytest.fixture
def planning_execution(shared_pool, monkeypatch, request):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 27)
    return request.getfixturevalue("execution")


def test_partial_process_peak_guides_next_estimate_without_becoming_calibration(
    planning_execution, shared_pool, monkeypatch,
):
    host, request, arguments, free, measurement = _configured(planning_execution, shared_pool, monkeypatch)
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    original, handle = _running(host, first)
    free[GPU_A.uuid] = 5000
    host.tick()
    measurement["complete"] = False
    host.shared_gpu.sampled_at.clear()
    host.tick()
    process, _ = _close(host, first, handle)
    _until(host, lambda: bool(host.store.list_facts(request.run_id, "EvidenceLinked")))
    before = host.store.inspect("SELECT * FROM workflow_commands")
    history = host.handle_agent_tool(request.attempt_id, "get_resources", {})["measurements"]
    assert host.store.inspect("SELECT * FROM workflow_commands") == before
    assert len(history) == 1
    observed = history[0]
    assert observed["attempt_id"] == process["attempt_id"]
    assert observed["workload"] == arguments["resource_estimate"]["workload"]
    assert observed["observed_peak_lower_bound_bytes"] == 16000 * MIB
    assert observed["sample_peak_bytes"] is None
    assert observed["measurement_status"] == "unknown"
    assert host._resource_experience(request.run_id, request.role, request.component_id,
        request.method_family_id)["measurements"] == history
    # Neither a different scope nor explicit IDs can manufacture comparability.
    assert host.shared_gpu.measurements(request.run_id, "other", request.component_id, request.method_family_id) == []
    from hypoloom.agent_aci import AgentACIError
    wrong = gpu_arguments(arguments, ids=(process["attempt_id"],))["resource_estimate"]
    wrong["workload"] = {"program": "different"}
    with pytest.raises(AgentACIError, match="matching workload"):
        host.handle_agent_tool(request.attempt_id, "get_resources", {"resource_estimate": wrong})
    proposal = gpu_arguments(arguments, peak=20032)
    preview = host.handle_agent_tool(request.attempt_id, "get_resources", {
        "resource_estimate": proposal["resource_estimate"]})["budget_preview"]
    assert preview["measurement_attempt_ids"] == [process["attempt_id"]]
    assert preview["calibrated_measurements"] == 0
    assert preview["sharing"] == "exclusive"
    assert preview["resolved_workload_memory_mib"] == 20032
    assert host.store.find_fact(request.run_id, "ExperimentPreregistered", first["exp_id"]) == original
    repair_args = {**proposal, "replaces_experiment_id": first["exp_id"]}
    repair = host.handle_agent_tool(request.attempt_id, "submit_experiment", repair_args)
    repaired_spec, repaired_handle = _running(host, repair)
    assert repaired_spec["payload"]["execution_contract"]["resource_intent"]["workload_memory_mib"] == 20032
    measurement["complete"] = True
    host.shared_gpu.sampled_at.clear()
    host.tick()
    _close(host, repair, repaired_handle, success=True, arguments=proposal)
    with monkeypatch.context() as replay:
        replay.setattr(host.shared_gpu, "resolve_arguments", lambda *a: pytest.fail("replay reselected memory history"))
        assert host.handle_agent_tool(request.attempt_id, "submit_experiment", repair_args) == repair
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_wrong_workload_and_owner_scope_cannot_reuse_measurement(
    execution, shared_pool, monkeypatch
):
    host, request, arguments, _, _, first, _, process, _ = _first_failure(
        execution, shared_pool, monkeypatch
    )
    for field, value in (
        ("role", "other-role"),
        ("component_id", "other-component"),
        ("method_family_id", "other-family"),
        ("run_id", "other-run"),
    ):
        key_field = "expert_slot" if field == "role" else field
        task = request.task_context
        if field != "method_family_id":
            task = replace(task, key=replace(task.key, **{key_field: value}))
        scope = replace(
            request, task_context=task, command_id=task.command_id, **{field: value}
        )
        resolved = host.shared_gpu.resolve_arguments(scope, arguments)
        assert resolved["resource_estimate"]["measurement_attempt_ids"] == []
    changed = {
        **arguments,
        "replaces_experiment_id": first["exp_id"],
        "resource_estimate": {
            **arguments["resource_estimate"],
            "workload": {"program": "different-workload"},
        },
    }
    repair = host.handle_agent_tool(request.attempt_id, "submit_experiment", changed)
    _until(
        host, lambda: host.store.latest_transition(repair["exp_id"])["state"] == "READY"
    )
    spec = host.store.find_fact(
        request.run_id, "ExperimentPreregistered", repair["exp_id"]
    )
    assert (
        spec["payload"]["execution_contract"]["resource_intent"]["workload_memory_mib"]
        == 12000
    )
    _, _, source, _ = host._submission_source(
        spec["payload"]["execution_contract"]["source_submit_command_id"]
    )
    assert source["arguments"]["resource_estimate"]["measurement_attempt_ids"] == []
    assert process["attempt_id"] not in json.dumps(
        source["arguments"]["resource_estimate"]
    )


def test_runner_exits_during_device_observation_without_poisoning_measured_history(
    execution, shared_pool, monkeypatch,
):
    host, request, arguments, free, _ = _configured(execution, shared_pool, monkeypatch)
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    _, handle = _running(host, first)
    free[GPU_A.uuid] = 5000
    host._poll_processes()
    attempt_id = handle.request.attempt_id
    before = host.shared_gpu.owned_samples[attempt_id][GPU_A.uuid].summary
    assert before["sample_count"] > 0 and before["unknown_sample_count"] == 0
    observe = host.compute_resources.observe_runtime
    (Path(handle.request.cwd) / handle.request.stderr_path).write_text(
        "RuntimeError: synthetic workload failure\n", encoding="utf-8"
    )

    def exit_during_device_observation(uuid):
        device = observe(uuid)
        handle.returncode = 1
        return device

    def forbidden(*args):
        pytest.fail("cached live status must not sample an already-terminal Runner")

    host.shared_gpu.sampled_at.clear()
    monkeypatch.setattr(host.compute_resources, "observe_runtime", exit_during_device_observation)
    monkeypatch.setattr(GpuAttributionSampler, "sample", forbidden)
    assert host._poll_processes()
    row = host.store.get_attempt(attempt_id)
    assert row["state"] == "CLOSED" and row["terminal_reason"] == "FAILED"
    owned = json.loads(row["observation_json"])["resource_usage"]["owned_memory"]
    assert owned == before
    _until(host, lambda: _terminal(host, first["exp_id"]))
    history = host.shared_gpu.measurements(
        request.run_id, request.role, request.component_id, request.method_family_id,
    )
    assert history[0]["measurement_status"] == "complete"
    assert history[0]["sample_peak_bytes"] == 16000 * MIB
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
