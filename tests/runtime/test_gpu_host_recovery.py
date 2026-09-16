from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

import hypoloom.workflow_host as workflow_host_module
from hypoloom.compute_resources import (
    ComputeCapabilities,
    GpuCapability,
    LocalComputeResources,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AttemptTerminalReason
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import ProcessObservation, ProcessRequest
from hypoloom.workflow_host import WorkflowHost


GPU_UUID = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
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


def _resources(tmp_path: Path) -> LocalComputeResources:
    runtime = tmp_path / "gpu-runtime"
    (runtime / "bin").mkdir(parents=True, exist_ok=True)
    (runtime / "bin" / "python").write_bytes(b"qualified-python")
    (runtime / "bin" / "python").chmod(0o755)
    return LocalComputeResources(
        allowed_gpu_uuids=(GPU_UUID,),
        gpu_runtime_prefix=runtime,
        inventory_provider=_capabilities,
    )


def _create(host: WorkflowHost, run_id: str, *, allow_gpu: bool) -> None:
    admission = admit_profile(REPOSITORY / "tests/fixtures/profiles/component-demo", run_id=run_id,
                              allow_gpu=allow_gpu)
    assert admission.admitted and admission.create_run_payload is not None
    payload = admission.create_run_payload
    if allow_gpu:
        assert host.compute_resources.gpu_runtime_prefix is not None
        runtime = str(host.compute_resources.gpu_runtime_prefix.resolve())
        payload["research_contract"]["runtime"]["conda_env"] = runtime
        payload["execution"]["runtime_prefix"] = runtime
    response = host.handle(ControlRequest(
        schema_version=1,
        request_id=f"create-{run_id}",
        expected_revision=0,
        command="CREATE_RUN",
        payload=payload,
    ))
    assert response.status == "accepted", response


def _drive_until(host: WorkflowHost, predicate, *, limit: int = 100) -> None:
    for _ in range(limit):
        host.tick()
        if predicate():
            return
    raise AssertionError("Host did not reach the expected bounded condition")


def _run_state(host: WorkflowHost, run_id: str) -> str:
    head = host.store.latest_transition(run_id)
    assert head is not None
    return str(head["state"])


def _process_attempts(
    host: WorkflowHost, run_id: str, execution_id: str
) -> tuple[dict[str, object], ...]:
    return tuple(
        attempt
        for attempt in host.store.list_attempts(execution_id=execution_id)
        if attempt["run_id"] == run_id
        and attempt["kind"] == "PROCESS"
        and attempt["execution_id"] == execution_id
    )


def _running_processes(
    host: WorkflowHost, run_id: str, execution_id: str
) -> dict[str, object]:
    attempt_ids = {
        str(attempt["attempt_id"])
        for attempt in _process_attempts(host, run_id, execution_id)
    }
    return {
        attempt_id: handle
        for attempt_id, handle in host.running.items()
        if attempt_id in attempt_ids
    }


def _started_process_requests(
    runner: _DeterministicRunner, execution_id: str
) -> tuple[ProcessRequest, ...]:
    return tuple(
        request
        for request in runner.started
        if request.execution_id == execution_id
    )


def _output_binding(request: ProcessRequest) -> Path:
    argv = request.argv
    for index, value in enumerate(argv[:-2]):
        if value == "--bind" and argv[index + 2] == "/output":
            return Path(argv[index + 1])
    raise AssertionError("ProcessRequest lacks the canonical /output binding")


def _gpu_resource_argv(request: ProcessRequest) -> tuple[tuple[str, ...], ...]:
    result: list[tuple[str, ...]] = []
    argv = request.argv
    for index, value in enumerate(argv):
        if value == "--dev-bind":
            result.append(tuple(argv[index:index + 3]))
        elif value == "--setenv" and argv[index + 1].startswith("CUDA_"):
            result.append(tuple(argv[index:index + 3]))
    return tuple(result)


@dataclass(slots=True)
class _Handle:
    request: ProcessRequest
    pid: int
    process_group_id: int
    started_at: float
    returncode: int | None
    cancelled: bool = False


class _DeterministicRunner:
    def __init__(self, *, complete: bool) -> None:
        self.complete = complete
        self.started: list[ProcessRequest] = []

    def start(
        self, request: ProcessRequest, *, before_exec: Callable[[int], None] | None = None,
    ) -> _Handle:
        ordinal = len(self.started) + 1
        if before_exec is not None:
            before_exec(2_000_000_000 + ordinal)
        self.started.append(request)
        log_root = Path(request.cwd)
        for relative in (request.stdout_path, request.stderr_path):
            assert relative is not None
            (log_root / relative).write_bytes(b"")
        if self.complete:
            (_output_binding(request) / "result.json").write_text(
                '{"score":1}', encoding="utf-8"
            )
        return _Handle(
            request=request,
            pid=2_000_000_000 + ordinal,
            process_group_id=2_000_000_000 + ordinal,
            started_at=time.monotonic(),
            returncode=0 if self.complete else None,
        )

    def wait(self, handle: _Handle) -> ProcessObservation:
        reason = (
            AttemptTerminalReason.CANCELLED
            if handle.cancelled
            else AttemptTerminalReason.SUCCEEDED
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

    def cancel(self, handle: _Handle) -> bool:
        if handle.returncode is not None:
            return False
        handle.cancelled = True
        handle.returncode = -15
        return True

    def cleanup(self, handle: _Handle) -> None:
        if handle.returncode is None:
            self.cancel(handle)


@pytest.fixture
def simulated_gpu_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    monkeypatch.setattr(workflow_host_module, "process_group_exists", lambda process_group_id: False)


def _assert_clean_report(state_root: Path, run_id: str) -> None:
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.duplicate_effect_count == 0
    assert report.owned_process_residue == ()


def test_gpu_enabled_host_keeps_non_opted_run_on_canonical_cpu_path(
    tmp_path: Path,
    simulated_gpu_workspace: None,
) -> None:
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root, compute_resources=_resources(tmp_path))
    run_id = "gpu-host-cpu-run"
    try:
        host.initialize()
        _create(host, run_id, allow_gpu=False)
        _drive_until(
            host,
            lambda: bool(
                facts := host.store.list_facts(run_id, "ExperimentPreregistered")
            )
            and host.store.latest_transition(facts[0]["experiment_id"])["state"]
            == "READY",
        )
        spec_fact = host.store.list_facts(run_id, "ExperimentPreregistered")[0]
        execution_id = str(spec_fact["execution_id"])
        runner = _DeterministicRunner(complete=True)
        host.runner = runner  # type: ignore[assignment]
        _drive_until(host, lambda: _run_state(host, run_id) == "COMPLETED")
        spec = spec_fact["payload"]
        assert spec["resource_preference"] == "CPU"
        assert spec["execution_contract"]["resource_grant"] == {
            "kind": "CPU",
            "gpu_uuid": None,
        }
        requests = _started_process_requests(runner, execution_id)
        assert len(requests) == 1
        argv = requests[0].argv
        assert "--dev-bind" not in argv
        assert any(
            left == "--unsetenv" and right == "CUDA_VISIBLE_DEVICES"
            for left, right in zip(argv, argv[1:])
        )
    finally:
        host.close()
    _assert_clean_report(state_root, run_id)


def test_gpu_grant_survives_lost_attempt_and_retry_with_one_canonical_outcome(
    tmp_path: Path,
    simulated_gpu_workspace: None,
) -> None:
    state_root = tmp_path / "state"
    run_id = "gpu-lost-retry"
    first = WorkflowHost(state_root, compute_resources=_resources(tmp_path))
    first.initialize()
    try:
        _create(first, run_id, allow_gpu=True)
        _drive_until(
            first,
            lambda: bool(
                facts := first.store.list_facts(run_id, "ExperimentPreregistered")
            )
            and first.store.latest_transition(facts[0]["experiment_id"])["state"]
            == "READY",
        )
        spec = first.store.list_facts(run_id, "ExperimentPreregistered")[0]
        execution_id = str(spec["execution_id"])
        blocked_runner = _DeterministicRunner(complete=False)
        first.runner = blocked_runner  # type: ignore[assignment]
        _drive_until(
            first,
            lambda: bool(_running_processes(first, run_id, execution_id)),
        )
        first_requests = _started_process_requests(blocked_runner, execution_id)
        assert len(first_requests) == 1
        first_request = first_requests[0]
    finally:
        first.close()

    resumed = WorkflowHost(state_root, compute_resources=_resources(tmp_path))
    try:
        resumed.initialize()
        successful_runner = _DeterministicRunner(complete=True)
        resumed.runner = successful_runner  # type: ignore[assignment]
        _drive_until(resumed, lambda: _run_state(resumed, run_id) == "COMPLETED")
        second_requests = _started_process_requests(
            successful_runner, execution_id
        )
        assert len(second_requests) == 1
        second_request = second_requests[0]
        attempts = _process_attempts(resumed, run_id, execution_id)
        assert [(attempt["attempt_ordinal"], attempt["terminal_reason"]) for attempt in attempts] == [
            (1, "LOST"),
            (2, "SUCCEEDED"),
        ]
        assert len(resumed.store.list_facts(run_id, "CanonicalOutcomeAccepted")) == 1
        for request in (first_request, second_request):
            assert request.argv.count(GPU_UUID) == 1
            assert request.argv.count("/dev/nvidia2") == 2
        assert _gpu_resource_argv(first_request) == _gpu_resource_argv(second_request)
    finally:
        resumed.close()
    _assert_clean_report(state_root, run_id)


def test_gpu_process_cancel_closes_without_process_or_workspace_residue(
    tmp_path: Path,
    simulated_gpu_workspace: None,
) -> None:
    state_root = tmp_path / "state"
    run_id = "gpu-cancel"
    host = WorkflowHost(state_root, compute_resources=_resources(tmp_path))
    try:
        host.initialize()
        _create(host, run_id, allow_gpu=True)
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
        runner = _DeterministicRunner(complete=False)
        host.runner = runner  # type: ignore[assignment]
        _drive_until(
            host,
            lambda: bool(_running_processes(host, run_id, execution_id)),
        )
        head = host.store.latest_transition(run_id)
        assert head is not None
        response = host.handle(ControlRequest(
            schema_version=1,
            request_id="cancel-gpu-run",
            expected_revision=int(head["revision"]),
            command="CANCEL",
            payload={"run_id": run_id, "cause": "bounded_gpu_cancel_test"},
        ))
        assert response.status == "accepted", response
        _drive_until(host, lambda: _run_state(host, run_id) == "CANCELLED")
        attempts = _process_attempts(host, run_id, execution_id)
        assert len(attempts) == 1
        assert attempts[0]["terminal_reason"] == "CANCELLED"
        assert not host.running
    finally:
        host.close()
    _assert_clean_report(state_root, run_id)
    work = state_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())
