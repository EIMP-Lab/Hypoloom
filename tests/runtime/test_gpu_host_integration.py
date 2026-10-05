from __future__ import annotations

import base64
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hypoloom.agent_aci import (
    AgentCommand,
    AgentRequest,
    AgentResponse,
    EmbeddedInput,
    request_digest,
    with_output_digest,
)
from hypoloom.compute_resources import (
    ComputeCapabilities,
    GpuCapability,
    GpuRuntimeObservation,
    LocalComputeResources,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AttemptTerminalReason
from hypoloom.fake_agent import FakeAgent
from hypoloom.monitor import build_monitor_snapshot
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import ProcessObservation
from hypoloom.workflow_host import WorkflowHost


GPU_UUID = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GPU_UUID_B = "GPU-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
REPOSITORY = Path(__file__).resolve().parents[2]


def _capabilities() -> ComputeCapabilities:
    return ComputeCapabilities(gpus=(GpuCapability(
        index=2,
        uuid=GPU_UUID,
        name="qualified synthetic GPU",
        total_memory_mib=24576,
        free_memory_mib=20_480,
        driver_version="570.133.07",
    ),))


def _capabilities_with_free(free_memory_mib: int) -> ComputeCapabilities:
    return ComputeCapabilities(
        gpus=(replace(_capabilities().gpus[0], free_memory_mib=free_memory_mib),)
    )


def _resources(tmp_path: Path, provider) -> LocalComputeResources:
    runtime = tmp_path / "gpu-runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_bytes(b"qualified-python")
    (runtime / "bin" / "python").chmod(0o755)
    return LocalComputeResources(
        allowed_gpu_uuids=(GPU_UUID,),
        gpu_runtime_prefix=runtime,
        inventory_provider=provider,
    )


def _rewrite_gpu_runtime(resources: LocalComputeResources) -> None:
    assert resources.gpu_runtime_prefix is not None
    (resources.gpu_runtime_prefix / "bin/python").write_bytes(b"drifted-python")


def _assert_gpu_process_request(request: dict[str, object]) -> None:
    argv = request["argv"]
    assert isinstance(argv, list)
    visible_devices = argv.index("CUDA_VISIBLE_DEVICES")
    assert argv[visible_devices + 1] == GPU_UUID
    assert "/dev/nvidia2" in argv
    assert argv[-2] == "/runtime/bin/python"


def _payload(run_id: str, *, allow_gpu: bool = True,
             runtime: Path | None = None) -> dict[str, object]:
    admission = admit_profile(REPOSITORY / "tests/fixtures/profiles/component-demo", run_id=run_id,
                              allow_gpu=allow_gpu)
    assert admission.admitted and admission.create_run_payload is not None
    payload = admission.create_run_payload
    if runtime is not None:
        resolved = str(runtime.resolve())
        payload["research_contract"]["runtime"]["conda_env"] = resolved
        payload["execution"]["runtime_prefix"] = resolved
    return payload


def _nonproduction_artifact_payload(
    run_id: str,
    *,
    runtime: Path | None = None,
) -> dict[str, object]:
    payload = _payload(run_id, runtime=runtime)
    contract = payload["research_contract"]
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": "1",
        "display_only": True,
    }
    component = contract["components"][0]
    component["result"] = {
        "kind": "artifact",
        "evaluation_kind": "artifact_check",
    }
    component["required_artifacts"] = ["report"]
    return payload


def _create(host: WorkflowHost, run_id: str, *, allow_gpu: bool = True) -> None:
    response = host.handle(ControlRequest(
        schema_version=1,
        request_id=f"create-{run_id}",
        expected_revision=0,
        command="CREATE_RUN",
        payload=_payload(run_id, allow_gpu=allow_gpu,
                         runtime=host.compute_resources.gpu_runtime_prefix),
    ))
    assert response.status == "accepted", response


def _drive_until(host: WorkflowHost, predicate, *, limit: int = 100) -> None:
    for _ in range(limit):
        host.tick()
        if predicate():
            return
    raise AssertionError("Host did not reach the expected bounded condition")


def _process_attempts(
    host: WorkflowHost,
    run_id: str,
    *,
    execution_id: str | None = None,
) -> tuple[dict[str, object], ...]:
    return tuple(
        attempt
        for attempt in host.store.list_attempts(execution_id=execution_id)
        if attempt["run_id"] == run_id
        and attempt["kind"] == "PROCESS"
        and (
            attempt["execution_id"] == execution_id
            if execution_id is not None
            else not str(attempt["execution_id"]).endswith("--candidate-validation")
        )
    )


def _running_processes(
    host: WorkflowHost, run_id: str, execution_id: str
) -> dict[str, object]:
    attempt_ids = {
        str(attempt["attempt_id"])
        for attempt in _process_attempts(
            host, run_id, execution_id=execution_id
        )
    }
    return {
        attempt_id: handle
        for attempt_id, handle in host.running.items()
        if attempt_id in attempt_ids
    }


class _GpuRequiredFake(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        assert isinstance(response, AgentResponse)
        candidate = dict(response.typed_candidate or {})
        candidate["resource_preference"] = "GPU_REQUIRED"
        return with_output_digest(replace(
            response, typed_candidate=candidate, output_digest=""
        ))


class _ArtifactPreferenceFake(FakeAgent):
    def __init__(self, preference: str) -> None:
        super().__init__()
        self.preference = preference

    def invoke(self, request):
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return super().invoke(request)
        cpu_request = replace(
            request,
            input_artifacts=tuple(
                EmbeddedInput.from_bytes(
                    item.ref,
                    item.media_type,
                    ComputeCapabilities(gpus=()).to_bytes(),
                )
                if item.ref.endswith("--compute-capabilities")
                else item
                for item in request.input_artifacts
            ),
        )
        response = super().invoke(cpu_request)
        assert isinstance(response, AgentResponse)
        candidate = dict(response.typed_candidate or {})
        candidate["resource_preference"] = self.preference
        return with_output_digest(replace(
            response,
            typed_candidate=candidate,
            input_digest=request_digest(request),
            output_digest="",
        ))


@pytest.mark.parametrize("preference", ("CPU", "AUTO"))
def test_nonproduction_artifact_freezes_cpu_even_with_available_gpu(
    tmp_path: Path,
    preference: str,
) -> None:
    run_id = f"artifact-cpu-{preference.lower()}"
    host = WorkflowHost(
        tmp_path / "state",
        compute_resources=_resources(tmp_path, _capabilities),
    )
    host.agent = _ArtifactPreferenceFake(preference)
    try:
        host.initialize()
        response = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_nonproduction_artifact_payload(
                run_id,
                runtime=host.compute_resources.gpu_runtime_prefix,
            ),
        ))
        assert response.status == "accepted", response
        _drive_until(
            host,
            lambda: bool(
                host.store.list_facts(run_id, "ExperimentPreregistered")
            ),
        )
        spec = host.store.list_facts(
            run_id, "ExperimentPreregistered"
        )[0]["payload"]
        assert spec["result_kind"] == "artifact"
        assert spec["resource_preference"] == preference
        assert spec["execution_contract"]["resource_grant"] == {
            "kind": "CPU",
            "gpu_uuid": None,
        }
        assert spec["execution_contract"]["sandbox_profile"] == (
            "bwrap-cpu-no-network/2"
        )
    finally:
        host.close()


def test_nonproduction_artifact_gpu_required_retries_without_fact_or_process(
    tmp_path: Path,
) -> None:
    run_id = "artifact-gpu-required"
    host = WorkflowHost(
        tmp_path / "state",
        compute_resources=_resources(tmp_path, _capabilities),
    )
    host.agent = _GpuRequiredFake()
    try:
        host.initialize()
        response = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_nonproduction_artifact_payload(
                run_id,
                runtime=host.compute_resources.gpu_runtime_prefix,
            ),
        ))
        assert response.status == "accepted", response
        _drive_until(
            host,
            lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED",
        )
        assert host.store.list_facts(run_id, "ExperimentPreregistered") == ()
        attempts = tuple(
            row
            for row in host.store.list_attempts(
                workflow_id=f"{run_id}--component-1--experiment"
            )
            if row["kind"] == "AGENT"
        )
        assert [row["attempt_ordinal"] for row in attempts] == [1, 2]
        observations = tuple(
            json.loads(row["observation_json"]) for row in attempts
        )
        assert [item["failure_code"] for item in observations] == [
            "resource_unsatisfied",
            "resource_unsatisfied",
        ]
        assert all(item["retry_class"] == "MODEL" for item in observations)
        assert any(
            artifact["ref"].endswith("--agent-retry-diagnostic")
            for artifact in observations[1]["request"]["input_artifacts"]
        )
        assert not tuple(
            row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "PROCESS"
        )
    finally:
        host.close()


def test_host_binds_request_snapshot_and_freezes_auto_gpu_grant_before_execution(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root, compute_resources=_resources(tmp_path, _capabilities))
    try:
        host.initialize(); _create(host, "gpu-preregister")
        _drive_until(host, lambda: bool(host.store.list_facts("gpu-preregister", "ExperimentPreregistered")))
        spec = host.store.list_facts("gpu-preregister", "ExperimentPreregistered")[0]["payload"]
        contract = spec["execution_contract"]
        assert spec["resource_preference"] == contract["resource_preference"] == "AUTO"
        assert contract["resource_grant"] == {"kind": "GPU", "gpu_uuid": GPU_UUID}
        assert contract["sandbox_profile"] == "bwrap-gpu-single-no-network/1"
        attempt = host.store.get_attempt(contract["source_agent_attempt_id"])
        observation = json.loads(attempt["observation_json"])
        request = observation["request"]
        response = observation["response"]
        capability_input = next(
            item for item in request["input_artifacts"]
            if item["ref"].endswith("--compute-capabilities")
        )
        assert ComputeCapabilities.from_bytes(
            base64.b64decode(capability_input["content_base64"])
        ) == _capabilities()
        skill_input = next(
            item
            for item in request["input_artifacts"]
            if item["ref"].endswith("--resource-planning-skill")
        )
        assert skill_input["media_type"] == "text/markdown"
        skill_bytes = base64.b64decode(skill_input["content_base64"])
        assert b"This is advice, not resource authority" in skill_bytes
        assert b"Pure documentation, report, checklist" in skill_bytes
        experience_input = next(
            item
            for item in request["input_artifacts"]
            if item["ref"].endswith("--resource-experience")
        )
        assert json.loads(base64.b64decode(experience_input["content_base64"])) == {
            "groups": [],
            "resource_experience_version": "resource-experience/1",
            "schema_version": 1,
            "source_attempt_count": 0,
            "truncated": False,
        }
        assert host.configuration["resource_planning_skill"] == (
            "experiment-resource-planning/6"
        )
        assert any(path.name == "SKILL.md" for path in host.runtime_files)
        assert response["input_digest"] == request_digest(AgentRequest.from_dict(request))
    finally:
        host.close()


def test_draft_refreshes_live_capacity_instead_of_reusing_startup_snapshot(
    tmp_path: Path,
) -> None:
    snapshots = [_capabilities_with_free(8_192), _capabilities_with_free(3_072)]

    def provider() -> ComputeCapabilities:
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    host = WorkflowHost(
        tmp_path / "state",
        compute_resources=_resources(tmp_path, provider),
    )
    run_id = "gpu-fresh-draft-capacity"
    try:
        host.initialize()
        payload = _payload(
            run_id,
            runtime=host.compute_resources.gpu_runtime_prefix,
        )
        payload["research_contract"]["budget"].update(
            {
                "min_vram_per_experiment": 4_096,
                "gpu_vram_reserve_mb": 2_048,
            }
        )
        response = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=payload,
        ))
        assert response.status == "accepted", response
        _drive_until(
            host,
            lambda: bool(host.store.list_facts(run_id, "ExperimentPreregistered")),
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        assert spec["payload"]["execution_contract"]["resource_grant"] == {
            "kind": "CPU",
            "gpu_uuid": None,
        }
        attempt = host.store.get_attempt(
            spec["payload"]["execution_contract"]["source_agent_attempt_id"]
        )
        request = json.loads(attempt["observation_json"])["request"]
        capability_input = next(
            item
            for item in request["input_artifacts"]
            if item["ref"].endswith("--compute-capabilities")
        )
        assert ComputeCapabilities.from_bytes(
            base64.b64decode(capability_input["content_base64"])
        ) == _capabilities_with_free(3_072)
    finally:
        host.close()


def test_draft_snapshot_records_same_root_busy_gpu_before_grant_selection(
    tmp_path: Path,
) -> None:
    second_gpu = replace(
        _capabilities().gpus[0],
        index=3,
        uuid=GPU_UUID_B,
        free_memory_mib=16_384,
    )
    capabilities = ComputeCapabilities(gpus=(_capabilities().gpus[0], second_gpu))
    runtime = tmp_path / "gpu-runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_bytes(b"qualified-python")
    (runtime / "bin" / "python").chmod(0o755)
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_UUID, GPU_UUID_B),
        gpu_runtime_prefix=runtime,
        inventory_provider=lambda: capabilities,
    )
    host = WorkflowHost(tmp_path / "state", compute_resources=resources)
    host._busy_gpu_uuids = lambda exclude_workflow=None: (GPU_UUID,)  # type: ignore[method-assign]
    run_id = "gpu-busy-draft-filter"
    try:
        host.initialize(); _create(host, run_id)
        _drive_until(
            host,
            lambda: bool(host.store.list_facts(run_id, "ExperimentPreregistered")),
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        assert spec["payload"]["execution_contract"]["resource_grant"] == {
            "kind": "GPU",
            "gpu_uuid": GPU_UUID_B,
        }
        attempt = host.store.get_attempt(
            spec["payload"]["execution_contract"]["source_agent_attempt_id"]
        )
        request = json.loads(attempt["observation_json"])["request"]
        capability_input = next(
            item
            for item in request["input_artifacts"]
            if item["ref"].endswith("--compute-capabilities")
        )
        frozen = ComputeCapabilities.from_bytes(
            base64.b64decode(capability_input["content_base64"])
        )
        assert tuple(gpu.uuid for gpu in frozen.gpus) == (GPU_UUID, GPU_UUID_B)
        assert frozen.busy_gpu_uuids == (GPU_UUID,)
    finally:
        host.close()


def test_gpu_required_without_authorized_capability_fails_before_fact_or_process(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    host.agent = _GpuRequiredFake()
    try:
        host.initialize(); _create(host, "gpu-required-empty")
        _drive_until(host, lambda: host.store.latest_transition("gpu-required-empty")["state"] == "COMPLETED")
        assert host.store.list_facts("gpu-required-empty", "ExperimentPreregistered") == ()
        assert not _process_attempts(host, "gpu-required-empty")
    finally:
        host.close()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit("gpu-required-empty")
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 0


def test_frozen_gpu_unavailability_waits_without_attempt_or_cpu_fallback(
    tmp_path: Path,
) -> None:
    snapshots = [_capabilities(), _capabilities(), ComputeCapabilities(gpus=())]

    def provider() -> ComputeCapabilities:
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    host = WorkflowHost(
        tmp_path / "state",
        compute_resources=_resources(tmp_path, provider),
    )
    run_id = "gpu-live-drift"
    try:
        host.initialize(); _create(host, run_id)
        _drive_until(
            host,
            lambda: bool(host.store.list_facts(run_id, "ExperimentPreregistered")),
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]["payload"]
        assert spec["execution_contract"]["resource_grant"] == {"kind": "GPU", "gpu_uuid": GPU_UUID}
        experiment_id = host.store.list_facts(
            run_id, "ExperimentPreregistered"
        )[0]["experiment_id"]
        _drive_until(
            host,
            lambda: host.store.latest_transition(experiment_id)["state"] == "READY",
        )
        for _ in range(3):
            host.tick()
        assert host.store.latest_transition(experiment_id)["state"] == "READY"
        assert not _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )
        status = host.handle(
            ControlRequest(
                1,
                f"status-{run_id}",
                None,
                "STATUS",
                {"run_id": run_id},
            )
        )
        assert status.result is not None
        assert status.result["resource_waits"] == [
            {
                "experiment_id": experiment_id,
                "reason": f"granted GPU {GPU_UUID} is unavailable",
                "message": f"granted GPU {GPU_UUID} is unavailable",
            }
        ]
        revision = host.store.latest_transition(run_id)["revision"]
        cancelled = host.handle(ControlRequest(
            1, f"cancel-{run_id}", revision, "CANCEL",
            {"run_id": run_id, "cause": "test_capacity_wait_cancel"},
        ))
        assert cancelled.status == "accepted"
        cancelled_status = host.handle(ControlRequest(
            1, f"cancelled-status-{run_id}", None, "STATUS", {"run_id": run_id},
        ))
        assert cancelled_status.result is not None
        assert cancelled_status.result["resource_waits"] == []
    finally:
        host.close()
    report = Oracle(tmp_path / "state/state.sqlite", tmp_path / "state/artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.owned_process_residue == ()


def test_frozen_gpu_runtime_drift_fails_locally_before_first_spawn(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    resources = _resources(tmp_path, _capabilities)
    host = WorkflowHost(state_root, compute_resources=resources)
    run_id = "gpu-runtime-drift-first"
    validate_live = Mock(side_effect=AssertionError("live GPU validation must not be called"))
    start = Mock(side_effect=AssertionError("runner.start must not be called"))
    try:
        host.initialize(); _create(host, run_id)
        _drive_until(
            host,
            lambda: bool(host.store.list_facts(run_id, "ExperimentPreregistered")),
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]["payload"]
        assert spec["resource_preference"] == "AUTO"
        assert spec["execution_contract"]["resource_grant"] == {
            "kind": "GPU",
            "gpu_uuid": GPU_UUID,
        }

        _rewrite_gpu_runtime(resources)
        host.compute_resources.validate_live = validate_live
        host.runner.start = start
        _drive_until(
            host,
            lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED",
        )

        validate_live.assert_not_called()
        start.assert_not_called()
        processes = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )
        assert len(processes) == 1
        assert processes[0]["terminal_reason"] == "FAILED"
        observation = json.loads(processes[0]["observation_json"])
        assert observation["synthetic_before_spawn"] is True
        assert observation["process_request"]["attempt_ordinal"] == 1
        _assert_gpu_process_request(observation["process_request"])
        assert host.operational_halt is None
        assert host.store.list_facts(
            run_id, "CanonicalOutcomeAccepted"
        )[0]["payload"]["outcome"] == "EXECUTION_FAILURE"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.owned_process_residue == ()


def test_frozen_gpu_runtime_drift_uses_same_guard_before_retry_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    resources = _resources(tmp_path, _capabilities)
    host = WorkflowHost(state_root, compute_resources=resources)
    run_id = "gpu-runtime-drift-retry"

    # Ordinary dispatch exceptions are quarantined; simulate process exit.
    class SimulatedCrash(SystemExit):
        pass

    try:
        host.initialize(); _create(host, run_id)
        _drive_until(
            host,
            lambda: bool(
                facts := host.store.list_facts(run_id, "ExperimentPreregistered")
            )
            and host.store.latest_transition(facts[0]["experiment_id"])["state"]
            == "READY",
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]

        original_crash = host._crash

        def crash_after_intent(point: str) -> None:
            if point == "after_process_intent_before_spawn":
                raise SimulatedCrash(point)
            original_crash(point)

        monkeypatch.setattr(host, "_crash", crash_after_intent)
        with pytest.raises(SimulatedCrash, match="after_process_intent_before_spawn"):
            host.tick()
        monkeypatch.setattr(host, "_crash", original_crash)

        first = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )
        assert len(first) == 1 and first[0]["state"] == "OPEN"
        host.generation = "simulated-restarted-host-generation"
        host._recover_abandoned_attempts()
        assert host.store.get_attempt(first[0]["attempt_id"])["terminal_reason"] == "LOST"

        _rewrite_gpu_runtime(resources)
        validate_live = Mock(side_effect=AssertionError("live GPU validation must not be called"))
        start = Mock(side_effect=AssertionError("runner.start must not be called"))
        host.compute_resources.validate_live = validate_live
        host.runner.start = start
        _drive_until(
            host,
            lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED",
        )

        validate_live.assert_not_called()
        start.assert_not_called()
        processes = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )
        assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in processes] == [
            (1, "LOST"),
            (2, "FAILED"),
        ]
        observation = json.loads(processes[1]["observation_json"])
        assert observation["synthetic_before_spawn"] is True
        _assert_gpu_process_request(observation["process_request"])
        assert host.operational_halt is None
        assert host.store.list_facts(
            run_id, "CanonicalOutcomeAccepted"
        )[0]["payload"]["outcome"] == "EXECUTION_FAILURE"
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.owned_process_residue == ()


def test_process_lost_retry_budget_exhaustion_keeps_synthetic_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state"
    resources = _resources(tmp_path, _capabilities)
    host = WorkflowHost(state_root, compute_resources=resources)
    run_id = "gpu-lost-retry-budget-exhausted"

    # Ordinary dispatch exceptions are quarantined; simulate process exit.
    class SimulatedCrash(SystemExit):
        pass

    try:
        host.initialize()
        payload = _payload(
            run_id, runtime=host.compute_resources.gpu_runtime_prefix,
        )
        payload["research_contract"]["budget"]["max_process_lost_retries"] = 0
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created
        _drive_until(
            host,
            lambda: bool(
                facts := host.store.list_facts(run_id, "ExperimentPreregistered")
            )
            and host.store.latest_transition(facts[0]["experiment_id"])["state"]
            == "READY",
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        original_crash = host._crash

        def crash_after_intent(point: str) -> None:
            if point == "after_process_intent_before_spawn":
                raise SimulatedCrash(point)
            original_crash(point)

        monkeypatch.setattr(host, "_crash", crash_after_intent)
        with pytest.raises(SimulatedCrash, match="after_process_intent_before_spawn"):
            host.tick()
        monkeypatch.setattr(host, "_crash", original_crash)
        first = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"]),
        )
        assert len(first) == 1 and first[0]["state"] == "OPEN"
        host.generation = "simulated-restarted-host-generation"
        host._recover_abandoned_attempts()

        _drive_until(
            host,
            lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED",
        )
        processes = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"]),
        )
        assert [row["terminal_reason"] for row in processes] == ["LOST", "FAILED"]
        retries = host.store.inspect(
            "SELECT t.payload_json FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id "
            "WHERE t.run_id=? AND c.command_type='OPEN_RETRY_ATTEMPT'",
            (run_id,),
        )
        assert len(retries) == 1
        intent = json.loads(retries[0]["payload_json"])
        assert intent["attempt_id"] == processes[1]["attempt_id"]
        assert intent["process_request"]["attempt_id"] == processes[1]["attempt_id"]
        assert intent["retry_of_attempt_id"] == processes[0]["attempt_id"]
        assert intent["retry_reason"] == "process_retry_budget_exhausted"
        assert intent["synthetic_before_spawn"] is True
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_gpu_runtime_drift_after_final_capacity_check_never_reaches_runner(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    resources = _resources(tmp_path, _capabilities)
    host = WorkflowHost(state_root, compute_resources=resources)
    run_id = "gpu-runtime-drift-after-final-check"
    original_validate = resources.validate_live
    validate_calls = 0

    def drift_after_final_check(*args, **kwargs) -> None:
        nonlocal validate_calls
        original_validate(*args, **kwargs)
        validate_calls += 1
        if validate_calls == 2:
            _rewrite_gpu_runtime(resources)

    start = Mock(side_effect=AssertionError("drifted runtime must not reach runner.start"))
    try:
        host.initialize(); _create(host, run_id)
        _drive_until(host, lambda: bool(host.store.list_facts(run_id, "ExperimentPreregistered")))
        resources.validate_live = drift_after_final_check
        host.runner.start = start
        _drive_until(host, lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED")
        assert validate_calls == 2
        start.assert_not_called()
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        process = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )[0]
        assert process["terminal_reason"] == "FAILED"
        assert json.loads(process["observation_json"])["synthetic_before_spawn"] is True
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_gpu_required_waits_in_ready_without_consuming_attempt_until_capacity_recovers(
    tmp_path: Path,
) -> None:
    current = [_capabilities_with_free(3_072)]
    host = WorkflowHost(
        tmp_path / "state",
        compute_resources=_resources(tmp_path, lambda: current[0]),
    )
    host.agent = _GpuRequiredFake()
    run_id = "gpu-capacity-wait"
    try:
        host.initialize()
        payload = _payload(
            run_id,
            runtime=host.compute_resources.gpu_runtime_prefix,
        )
        payload["research_contract"]["budget"].update(
            {
                "min_vram_per_experiment": 4_096,
                "gpu_vram_reserve_mb": 2_048,
            }
        )
        response = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=payload,
            )
        )
        assert response.status == "accepted", response
        _drive_until(
            host,
            lambda: bool(
                host.store.list_facts(run_id, "ExperimentPreregistered")
            ),
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        assert spec["payload"]["execution_contract"]["resource_grant"] == {
            "kind": "GPU",
            "gpu_uuid": GPU_UUID,
        }

        _drive_until(
            host,
            lambda: host.store.latest_transition(spec["experiment_id"])["state"]
            == "READY",
        )
        for _ in range(5):
            host.tick()
        assert host.store.latest_transition(spec["experiment_id"])["state"] == "READY"
        assert not _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )

        host.runner.start = Mock(side_effect=OSError("bounded post-wait probe"))
        current[0] = _capabilities_with_free(8_192)
        _drive_until(
            host,
            lambda: bool(
                _process_attempts(
                    host, run_id, execution_id=str(spec["execution_id"])
                )
            ),
        )
        attempts = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )
        assert len(attempts) == 1
        assert attempts[0]["attempt_ordinal"] == 1
        assert attempts[0]["terminal_reason"] == "FAILED"
    finally:
        host.close()


def test_capacity_loss_after_intent_is_one_real_prestart_failure(
    tmp_path: Path,
) -> None:
    snapshots = [
        _capabilities_with_free(20_000),
        _capabilities_with_free(20_000),
        _capabilities_with_free(20_000),
        _capabilities_with_free(1_000),
    ]

    def provider() -> ComputeCapabilities:
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    host = WorkflowHost(
        tmp_path / "state",
        compute_resources=_resources(tmp_path, provider),
    )
    run_id = "gpu-final-capacity-race"
    try:
        host.initialize()
        payload = _payload(
            run_id,
            runtime=host.compute_resources.gpu_runtime_prefix,
        )
        payload["research_contract"]["budget"].update(
            {
                "min_vram_per_experiment": 4_096,
                "gpu_vram_reserve_mb": 2_048,
            }
        )
        response = host.handle(
            ControlRequest(
                1,
                f"create-{run_id}",
                0,
                "CREATE_RUN",
                payload,
            )
        )
        assert response.status == "accepted", response
        _drive_until(
            host,
            lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED",
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        process = _process_attempts(
            host, run_id, execution_id=str(spec["execution_id"])
        )[0]
        assert process["attempt_ordinal"] == 1
        assert process["terminal_reason"] == "FAILED"
        observation = json.loads(process["observation_json"])
        assert observation["synthetic_before_spawn"] is True
        assert "integrity_error" not in observation
        assert "1000 MiB free; 6144 MiB is required" in observation[
            "resource_diagnostic"
        ]
    finally:
        host.close()

    report = Oracle(
        tmp_path / "state/state.sqlite", tmp_path / "state/artifacts"
    ).audit(run_id)
    assert report.ok, report.issues


@pytest.mark.parametrize("reserve,percent,low_free", [(4096, None, 1024), (512, 5, 511)])
def test_sustained_gpu_pressure_cancels_only_owned_handle_and_records_device_summary(
    tmp_path: Path, reserve, percent, low_free,
) -> None:
    initial = GpuRuntimeObservation(
        uuid=GPU_UUID,
        total_memory_mib=24_576,
        free_memory_mib=8_192,
        used_memory_mib=16_384,
        utilization_percent=50,
    )
    low = replace(
        initial,
        free_memory_mib=low_free,
        used_memory_mib=24_576-low_free,
        utilization_percent=99,
    )
    samples = iter((initial, low))
    runtime = tmp_path / "gpu-runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_bytes(b"qualified-python")
    (runtime / "bin" / "python").chmod(0o755)
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_UUID,),
        gpu_runtime_prefix=runtime,
        inventory_provider=_capabilities,
        observation_provider=lambda _uuid: next(samples),
    )
    host = WorkflowHost(
        tmp_path / "state",
        compute_resources=resources,
        gpu_pressure_grace_seconds=0,
    )
    host.agent = _GpuRequiredFake()
    started: list[SimpleNamespace] = []

    def start(request, *, before_exec=None):
        if before_exec is not None:
            before_exec(41_001)
        handle = SimpleNamespace(
            request=request,
            pid=41_001,
            process_group_id=41_001,
            started_at=time.monotonic(),
            returncode=None,
        )
        started.append(handle)
        return handle

    def cancel(handle, *, terminate_grace_seconds=None):
        assert handle is started[0]
        assert terminate_grace_seconds == 30.0
        handle.returncode = -15
        return True

    def wait(handle):
        return ProcessObservation(
            attempt_id=handle.request.attempt_id,
            attempt_ordinal=handle.request.attempt_ordinal,
            execution_id=handle.request.execution_id,
            terminal_reason=AttemptTerminalReason.CANCELLED,
            returncode=-15,
            pid=handle.pid,
            process_group_id=handle.process_group_id,
            started_at=handle.started_at,
            ended_at=time.monotonic(),
            stdout_path=handle.request.stdout_path,
            stderr_path=handle.request.stderr_path,
        )

    run_id = "gpu-pressure-owned-only"
    try:
        host.initialize()
        payload = _payload(run_id, runtime=runtime)
        payload["research_contract"]["budget"].update(
            {
                "min_vram_per_experiment": 4_096,
                "gpu_vram_reserve_mb": reserve,
                **({"gpu_vram_reserve_percent": percent} if percent else {}),
            }
        )
        response = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=payload,
            )
        )
        assert response.status == "accepted", response
        _drive_until(
            host,
            lambda: bool(
                facts := host.store.list_facts(run_id, "ExperimentPreregistered")
            )
            and host.store.latest_transition(facts[0]["experiment_id"])["state"]
            == "READY",
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        execution_id = str(spec["execution_id"])
        host.runner.start = Mock(side_effect=start)
        host.runner.cancel = Mock(side_effect=cancel)
        host.runner.wait = Mock(side_effect=wait)
        host.runner.cleanup = Mock()
        _drive_until(
            host,
            lambda: bool(_running_processes(host, run_id, execution_id)),
        )
        assert host._poll_processes() is True

        process = _process_attempts(
            host, run_id, execution_id=execution_id
        )[0]
        assert process["terminal_reason"] == "FAILED"
        observation = json.loads(process["observation_json"])
        assert observation["resource_pressure"] == {
            "reason": "sustained_free_gpu_memory_below_reserve",
            "gpu_reserve_memory_mib": reserve,
            "owned_memory_mib_at_decision": None,
            "attribution": "unknown",
            "terminate_grace_seconds": 30.0,
        }
        assert observation["resource_usage"]["gpu_uuid"] == GPU_UUID
        assert observation["resource_usage"]["gpu_minimum_free_memory_mib"] == low_free
        assert observation["resource_usage"]["pressure_exceeded"] is True
        assert observation["resource_usage"]["pressure_attribution"] == "unknown"
        host.runner.cancel.assert_called_once_with(started[0], terminate_grace_seconds=30.0)
        spec_payload = spec["payload"]
        experience = host._resource_experience(
            run_id,
            "scientist-a",
            str(spec_payload["component_id"]),
            str(spec_payload["method_family_id"]),
        )
        assert experience["source_attempt_count"] == 1
        assert experience["groups"][0]["pressure_termination_count"] == 1
        assert experience["groups"][0]["failed_attempt_count"] == 1
        view = next(
            item
            for item in build_monitor_snapshot(
                host.state_root, run_id
            ).attempts
            if item.attempt_id == process["attempt_id"]
        )
        assert view.gpu_uuid == GPU_UUID
        assert view.minimum_free_memory_mib == low_free
        assert view.latest_free_memory_mib == low_free
        assert view.latest_utilization_percent == 99
        assert view.resource_pressure_exceeded is True
    finally:
        host.close()


def _completed_cpu_resource_source(host: WorkflowHost, run_id: str) -> dict[str, object]:
    _create(host, run_id, allow_gpu=False)
    _drive_until(host, lambda: host.store.latest_transition(run_id)["state"] == "COMPLETED")
    return host.store.list_facts(run_id, "ExperimentPreregistered")[0]["payload"]


def test_resource_experience_accepts_exact_contract_and_run_version_peer(
    tmp_path: Path,
) -> None:
    host = WorkflowHost(tmp_path / "state")
    try:
        host.initialize()
        spec = _completed_cpu_resource_source(host, "resource-source-compatible")
        _create(host, "resource-target-compatible", allow_gpu=False)
        experience = host._resource_experience(
            "resource-target-compatible", "scientist-a",
            str(spec["component_id"]), str(spec["method_family_id"]),
        )
        assert experience["source_attempt_count"] == 1
        assert experience["groups"][0]["cpu_grant_count"] == 1
    finally:
        host.close()


def test_resource_experience_rejects_contract_or_run_version_mismatch(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    first = WorkflowHost(state_root)
    try:
        first.initialize()
        spec = _completed_cpu_resource_source(first, "resource-source-incompatible")
        payload = _payload("resource-contract-mismatch", allow_gpu=False)
        payload["research_contract"]["budget"]["gpu_vram_reserve_mb"] = 1
        created = first.handle(ControlRequest(
            1, "create-resource-contract-mismatch", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted"
        mismatched = first._resource_experience(
            "resource-contract-mismatch", "scientist-a",
            str(spec["component_id"]), str(spec["method_family_id"]),
        )
        assert mismatched["source_attempt_count"] == 0
        _drive_until(first, lambda: first.store.latest_transition(
            "resource-contract-mismatch"
        )["state"] == "COMPLETED")
    finally:
        first.close()

    second = WorkflowHost(state_root, gpu_pressure_grace_seconds=6)
    try:
        second.initialize(); _create(second, "resource-version-mismatch", allow_gpu=False)
        mismatched = second._resource_experience(
            "resource-version-mismatch", "scientist-a",
            str(spec["component_id"]), str(spec["method_family_id"]),
        )
        assert mismatched["source_attempt_count"] == 0
    finally:
        second.close()


def test_pressure_cancel_race_preserves_natural_terminal_and_consistent_usage(
    tmp_path: Path,
) -> None:
    initial = GpuRuntimeObservation(
        uuid=GPU_UUID, total_memory_mib=24_576, free_memory_mib=8_192,
        used_memory_mib=16_384, utilization_percent=50,
    )
    low = replace(initial, free_memory_mib=1_024, used_memory_mib=23_552,
                  utilization_percent=99)
    samples = iter((initial, low))
    runtime = tmp_path / "gpu-runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_bytes(b"qualified-python")
    (runtime / "bin" / "python").chmod(0o755)
    resources = LocalComputeResources(
        allowed_gpu_uuids=(GPU_UUID,), gpu_runtime_prefix=runtime,
        inventory_provider=_capabilities, observation_provider=lambda _uuid: next(samples),
    )
    host = WorkflowHost(tmp_path / "state", compute_resources=resources,
                        gpu_pressure_grace_seconds=0)
    host.agent = _GpuRequiredFake()

    def start(request, *, before_exec=None):
        if before_exec is not None:
            before_exec(41_002)
        return SimpleNamespace(request=request, pid=41_002, process_group_id=41_002,
                               started_at=time.monotonic(), returncode=None)

    def cancel(handle, *, terminate_grace_seconds=None):
        assert terminate_grace_seconds == 30.0
        handle.returncode = 0
        return False

    def wait(handle):
        return ProcessObservation(
            attempt_id=handle.request.attempt_id,
            attempt_ordinal=handle.request.attempt_ordinal,
            execution_id=handle.request.execution_id,
            terminal_reason=AttemptTerminalReason.SUCCEEDED, returncode=0,
            pid=handle.pid, process_group_id=handle.process_group_id,
            started_at=handle.started_at, ended_at=time.monotonic(),
            stdout_path=handle.request.stdout_path, stderr_path=handle.request.stderr_path,
        )

    run_id = "gpu-pressure-terminal-race"
    try:
        host.initialize()
        payload = _payload(run_id, runtime=runtime)
        payload["research_contract"]["budget"].update(
            {"min_vram_per_experiment": 4_096, "gpu_vram_reserve_mb": 4_096}
        )
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted"
        _drive_until(
            host,
            lambda: bool(
                facts := host.store.list_facts(run_id, "ExperimentPreregistered")
            )
            and host.store.latest_transition(facts[0]["experiment_id"])["state"]
            == "READY",
        )
        spec = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        execution_id = str(spec["execution_id"])
        host.runner.start = Mock(side_effect=start)
        host.runner.cancel = Mock(side_effect=cancel)
        host.runner.wait = Mock(side_effect=wait)
        host.runner.cleanup = Mock()
        _drive_until(
            host,
            lambda: bool(_running_processes(host, run_id, execution_id)),
        )
        assert host._poll_processes() is True
        process = _process_attempts(
            host, run_id, execution_id=execution_id
        )[0]
        assert process["terminal_reason"] == "SUCCEEDED"
        observation = json.loads(process["observation_json"])
        assert observation["resource_usage"]["pressure_exceeded"] is False
        assert "resource_pressure" not in observation
        host.runner.cancel.assert_called_once()
    finally:
        host.close()
