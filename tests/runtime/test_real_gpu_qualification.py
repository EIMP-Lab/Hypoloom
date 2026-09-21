"""Opt-in component-v4 qualification against one authorized real GPU.

The normal suite never opens a CUDA context.  This lane requires an exact GPU
UUID and frozen Python runtime, rechecks that the selected device is idle before
each destructive-recovery scenario, and uses only disposable pytest state
roots.  A separate opt-in lane proves bounded coexistence with a pre-existing
compute process without signaling it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from hypoloom.agent_aci import AgentCommand, WorkspaceArtifact, with_output_digest
from hypoloom.compute_resources import LocalComputeResources
from hypoloom.control_protocol import ControlRequest, request_over_socket
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost


_ENABLE_ENV = "HYPOLOOM_REAL_GPU"
_SHARED_ENABLE_ENV = "HYPOLOOM_REAL_SHARED_GPU"
_UUID_ENV = "HYPOLOOM_REAL_GPU_UUID"
_RUNTIME_ENV = "HYPOLOOM_REAL_GPU_RUNTIME"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PROFILES_ROOT = _PROJECT_ROOT / "tests/fixtures/profiles"


def _run(
    arguments: list[str], *, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=_PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"command failed: {arguments!r} returncode={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result


def _cli(state_root: Path, *arguments: str, timeout: float = 30.0) -> dict[str, Any]:
    result = _run(
        [
            sys.executable,
            "-m",
            "hypoloom.cli",
            *arguments,
            "--state-root",
            str(state_root),
        ],
        timeout=timeout,
    )
    return json.loads(result.stdout)


def _assert_selected_gpu_idle(gpu_uuid: str) -> None:
    deadline = time.monotonic() + 20
    latest: tuple[list[str], list[str]] | None = None
    while time.monotonic() < deadline:
        inventory = _run(
            [
                "nvidia-smi",
                "--query-gpu=uuid,utilization.gpu,memory.used,temperature.gpu",
                "--format=csv,noheader,nounits",
            ]
        ).stdout.splitlines()
        matches = [
            line for line in inventory if line.split(",", 1)[0].strip() == gpu_uuid
        ]
        assert len(matches) == 1, (gpu_uuid, inventory)
        _, utilization, memory_used, temperature = [
            item.strip() for item in matches[0].split(",")
        ]
        applications = _run(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid",
                "--format=csv,noheader,nounits",
            ]
        ).stdout.splitlines()
        selected = [
            line for line in applications if line.split(",", 1)[0].strip() == gpu_uuid
        ]
        latest = (matches, selected)
        if (
            int(utilization) <= 10
            and int(memory_used) <= 512
            and int(temperature) <= 60
            and not selected
        ):
            return
        time.sleep(0.2)
    raise AssertionError(f"selected GPU did not become safely idle: {latest!r}")


def _start_host(
    state_root: Path,
    gpu_uuid: str,
    runtime: Path,
    *,
    fault_point: str | None = None,
    pool: tuple[Path, str] | None = None,
) -> subprocess.Popen[str]:
    arguments = [
        sys.executable,
        "-m",
        "hypoloom.cli",
        "serve",
        "--state-root",
        str(state_root),
        "--gpu-runtime-prefix",
        str(runtime),
        "--gpu-device-uuid",
        gpu_uuid,
    ]
    if pool is not None:
        pool_socket, pool_namespace = pool
        arguments.extend(
            (
                "--gpu-pool-socket",
                str(pool_socket),
                "--gpu-pool-namespace",
                pool_namespace,
                "--gpu-pool-server-uid",
                str(os.getuid()),
            )
        )
    if fault_point is not None:
        arguments.extend(("--fault-point", fault_point))
    process = subprocess.Popen(
        arguments,
        cwd=_PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    socket_path = state_root / "control.sock"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if socket_path.is_socket():
            try:
                request_over_socket(
                    socket_path,
                    ControlRequest(
                        schema_version=1,
                        request_id=str(uuid.uuid4()),
                        expected_revision=None,
                        command="STATUS",
                        payload={"run_id": "qualification-readiness-probe"},
                    ),
                    timeout=0.2,
                )
            except (ConnectionError, TimeoutError, OSError):
                pass
            else:
                return process
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"Host exited before serving: {stdout=!r} {stderr=!r}")
        time.sleep(0.02)
    _stop_host(process, require_clean=False)
    raise AssertionError("Host did not create control.sock")


def _stop_host(
    process: subprocess.Popen[str], *, require_clean: bool
) -> tuple[str, str]:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=10)
        raise AssertionError(f"Host ignored SIGTERM: {stdout=!r} {stderr=!r}")
    if require_clean:
        assert process.returncode == 0, (stdout, stderr)
    return stdout, stderr


def _create(
    state_root: Path,
    run_id: str,
    *,
    delay: float,
    profile: str = "component-demo",
    profiles_root: Path = _PROFILES_ROOT,
) -> None:
    response = _cli(
        state_root,
        "start",
        profile,
        "--profiles-root",
        str(profiles_root),
        "--run-id",
        run_id,
        "--process-delay",
        str(delay),
        "--allow-gpu",
    )
    assert response["status"] == "accepted", response


def _status(state_root: Path, run_id: str) -> dict[str, Any]:
    response = _cli(state_root, "status", "--run-id", run_id, "--timeout", "5")
    assert response["status"] == "accepted", response
    return response["result"]


def _await_state(
    state_root: Path,
    run_id: str,
    expected: str,
    *,
    timeout: float = 90.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest = _status(state_root, run_id)
        if latest["run_state"] == expected:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"run did not reach {expected}: {latest!r}")


def _process_attempts(state_root: Path, run_id: str) -> tuple[sqlite3.Row, ...]:
    connection = sqlite3.connect(
        f"file:{(state_root / 'state.sqlite').resolve()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        return tuple(
            connection.execute(
                "SELECT a.* FROM execution_attempts a JOIN research_facts f ON "
                "f.run_id=a.run_id AND f.fact_type='ExperimentPreregistered' "
                "AND f.experiment_id=a.experiment_id AND f.execution_id=a.execution_id "
                "WHERE a.run_id=? AND a.kind='PROCESS' ORDER BY a.experiment_id,a.attempt_ordinal",
                (run_id,),
            )
        )
    finally:
        connection.close()


def _compute_pids(gpu_uuid: str) -> set[int]:
    lines = _run(
        [
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid",
            "--format=csv,noheader,nounits",
        ]
    ).stdout.splitlines()
    return {
        int(pid.strip())
        for line in lines
        if len(parts := line.split(",", 1)) == 2
        and parts[0].strip() == gpu_uuid
        and (pid := parts[1]).strip().isdigit()
    }


def _shared_profile(
    root: Path,
    *,
    minimum_memory_mib: int = 512,
    reserve_memory_mib: int = 512,
) -> tuple[Path, str]:
    profiles_root = root / "profiles"
    profile_name = "shared-gpu-demo"
    target = profiles_root / profile_name
    shutil.copytree(_PROFILES_ROOT / "component-demo", target)
    methodology = target / "methodology.yaml"
    document = yaml.safe_load(methodology.read_text(encoding="utf-8"))
    document["budget"].update(
        {
            "max_experiments": 1,
            "experiment_timeout": 60,
            "min_vram_per_experiment": minimum_memory_mib,
            "gpu_vram_reserve_mb": reserve_memory_mib,
        }
    )
    methodology.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    return profiles_root, profile_name


def _gpu_free_memory_mib(gpu_uuid: str) -> int:
    lines = _run(
        [
            "nvidia-smi",
            "--query-gpu=uuid,memory.free",
            "--format=csv,noheader,nounits",
        ]
    ).stdout.splitlines()
    matches = [line.split(",", 1) for line in lines if "," in line]
    values = [
        int(free.strip())
        for observed_uuid, free in matches
        if observed_uuid.strip() == gpu_uuid
    ]
    assert len(values) == 1, (gpu_uuid, lines)
    return values[0]


def _resource_argv(row: sqlite3.Row) -> tuple[tuple[str, ...], ...]:
    request = json.loads(row["observation_json"])["process_request"]
    argv = request["argv"]
    selected: list[tuple[str, ...]] = []
    for index, value in enumerate(argv):
        if value == "--dev-bind":
            selected.append(tuple(argv[index : index + 3]))
        elif value == "--setenv" and argv[index + 1].startswith("CUDA_"):
            selected.append(tuple(argv[index : index + 3]))
    return tuple(selected)


def _assert_resource_binding(row: sqlite3.Row, gpu_uuid: str) -> None:
    arguments = _resource_argv(row)
    assert ("--setenv", "CUDA_VISIBLE_DEVICES", gpu_uuid) in arguments
    assert ("--setenv", "CUDA_DEVICE_ORDER", "PCI_BUS_ID") in arguments
    assert len([item for item in arguments if item[0] == "--dev-bind"]) == 3


def _cuda_evidence(state_root: Path, attempt_id: str) -> dict[str, Any]:
    log = (
        state_root / "artifacts" / "result-staging" / attempt_id / "logs" / "stdout.log"
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and "cuda_device_count" in value:
                    return value
        time.sleep(0.05)
    raise AssertionError(f"CUDA evidence was not observed for {attempt_id}")


def _live_cuda_evidence(state_root: Path, attempt_id: str) -> dict[str, Any]:
    log = state_root / "artifacts" / "work" / attempt_id / "logs" / "stdout.log"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and "cuda_device_count" in value:
                    return value
        time.sleep(0.05)
    raise AssertionError(f"live CUDA evidence was not observed for {attempt_id}")


def _assert_evidence(value: dict[str, Any]) -> None:
    assert value["cuda_available"] is True
    assert value["cuda_device_count"] == 1
    assert value["cuda_runtime"] == "12.8"
    assert "RTX 3090" in value["cuda_device_name"]


def _assert_oracle_clean(state_root: Path, run_id: str, *, outcomes: int) -> None:
    report = _cli(state_root, "oracle", "--run-id", run_id)
    assert report["ok"] is True, report
    assert report["canonical_outcome_count"] == outcomes, report
    assert report["open_attempt_count"] == 0, report
    assert report["pending_effect_count"] == 0, report
    assert report["owned_process_residue"] == [], report
    work = state_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())


def _normal_component_cohort(root: Path, gpu_uuid: str, runtime: Path) -> None:
    _assert_selected_gpu_idle(gpu_uuid)
    run_id = "component-v4-real-gpu-normal"
    host = _start_host(root, gpu_uuid, runtime)
    try:
        _create(root, run_id, delay=0)
        status = _await_state(root, run_id, "COMPLETED")
        assert status["canonical_outcome_count"] == 1
    finally:
        _stop_host(host, require_clean=True)
    attempts = _process_attempts(root, run_id)
    assert len(attempts) == 1
    assert all(row["terminal_reason"] == "SUCCEEDED" for row in attempts)
    for row in attempts:
        _assert_resource_binding(row, gpu_uuid)
        _assert_evidence(_cuda_evidence(root, row["attempt_id"]))
    _assert_oracle_clean(root, run_id, outcomes=1)


def _lost_attempt_retry(root: Path, gpu_uuid: str, runtime: Path) -> None:
    _assert_selected_gpu_idle(gpu_uuid)
    run_id = "component-v4-real-gpu-lost-retry"
    crashed = _start_host(
        root,
        gpu_uuid,
        runtime,
        fault_point="after_spawn_before_observation",
    )
    _create(root, run_id, delay=0)
    stdout, stderr = crashed.communicate(timeout=30)
    assert crashed.returncode == 86, (stdout, stderr)
    _assert_selected_gpu_idle(gpu_uuid)
    resumed = _start_host(root, gpu_uuid, runtime)
    try:
        status = _await_state(root, run_id, "COMPLETED")
        assert status["canonical_outcome_count"] == 1
    finally:
        _stop_host(resumed, require_clean=True)
    attempts = _process_attempts(root, run_id)
    assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in attempts] == [
        (1, "LOST"),
        (2, "SUCCEEDED"),
    ]
    assert _resource_argv(attempts[0]) == _resource_argv(attempts[1])
    _assert_resource_binding(attempts[0], gpu_uuid)
    _assert_resource_binding(attempts[1], gpu_uuid)
    _assert_evidence(_cuda_evidence(root, attempts[1]["attempt_id"]))
    _assert_oracle_clean(root, run_id, outcomes=1)


def _cancel_after_cuda_context(root: Path, gpu_uuid: str, runtime: Path) -> None:
    _assert_selected_gpu_idle(gpu_uuid)
    run_id = "component-v4-real-gpu-cancel"
    host = _start_host(root, gpu_uuid, runtime)
    try:
        _create(root, run_id, delay=20)
        deadline = time.monotonic() + 30
        attempt: sqlite3.Row | None = None
        while time.monotonic() < deadline:
            attempts = _process_attempts(root, run_id)
            if attempts and attempts[0]["state"] == "OPEN":
                attempt = attempts[0]
                break
            time.sleep(0.05)
        assert attempt is not None
        _assert_evidence(_live_cuda_evidence(root, attempt["attempt_id"]))
        status = _status(root, run_id)
        response = _cli(
            root,
            "cancel",
            "--run-id",
            run_id,
            "--expected-revision",
            str(status["run_revision"]),
            "--cause",
            "component_v4_real_gpu_cancel",
        )
        assert response["status"] == "accepted", response
        _await_state(root, run_id, "CANCELLED")
    finally:
        _stop_host(host, require_clean=True)
    attempts = _process_attempts(root, run_id)
    assert len(attempts) == 1
    assert attempts[0]["terminal_reason"] == "CANCELLED"
    _assert_resource_binding(attempts[0], gpu_uuid)
    _assert_evidence(_cuda_evidence(root, attempts[0]["attempt_id"]))
    _assert_oracle_clean(root, run_id, outcomes=1)


class _GpuRevisionAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__(); self.draft_count = 0

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC: return response
        self.draft_count += 1
        if self.draft_count > 2: return response
        source = response.proposed_workspace_artifacts[0].content_bytes()
        marker = b"time.sleep(float(os.environ.get('HYPOLOOM_DELAY', '0')))\n"
        assert marker in source
        source = source.replace(marker, f"raise RuntimeError('gpu revision {self.draft_count}')\n".encode(), 1)
        artifact = WorkspaceArtifact.from_bytes("experiment.py", source)
        return with_output_digest(replace(response, proposed_workspace_artifacts=(artifact,), output_digest=""))


def _gpu_revision_loop(root: Path, gpu_uuid: str, runtime: Path) -> None:
    _assert_selected_gpu_idle(gpu_uuid)
    run_id = "component-v5-real-gpu-revision-loop"; admission = admit_profile(_PROFILES_ROOT / "component-demo", run_id=run_id)
    assert admission.create_run_payload is not None; payload = admission.create_run_payload
    payload["research_contract"]["budget"].update({"max_experiments": 3, "max_retries": 0, "experiment_timeout": 60})
    payload["execution"]["allow_gpu"] = True
    resources = LocalComputeResources(allowed_gpu_uuids=(gpu_uuid,), gpu_runtime_prefix=runtime)
    agent = _GpuRevisionAgent(); host = WorkflowHost(root, compute_resources=resources); host.agent = agent
    try:
        host.initialize(); created = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}", expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED": break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        specs = host.store.list_facts(run_id, "ExperimentPreregistered"); outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert agent.draft_count == len(specs) == len(outcomes) == 3
        assert [row["payload"]["outcome"] for row in outcomes] == ["EXECUTION_FAILURE", "EXECUTION_FAILURE", "SUCCESS"]
        assert all(row["payload"]["execution_contract"]["resource_grant"] == {"kind": "GPU", "gpu_uuid": gpu_uuid} for row in specs)
        executions = {str(row["execution_id"]) for row in specs}; attempts = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["execution_id"] in executions]
        assert len(attempts) == 3
        for row in attempts: _assert_evidence(_cuda_evidence(root, row["attempt_id"]))
    finally:
        host.close()
    report = Oracle(root / "state.sqlite", root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    _assert_selected_gpu_idle(gpu_uuid)


def test_component_v4_real_gpu_production_topology_cohort(tmp_path: Path) -> None:
    if os.environ.get(_ENABLE_ENV) != "1":
        pytest.skip(
            f"set {_ENABLE_ENV}=1 with exact UUID/runtime to run real GPU qualification"
        )
    gpu_uuid = os.environ.get(_UUID_ENV)
    runtime_raw = os.environ.get(_RUNTIME_ENV)
    assert gpu_uuid and runtime_raw, f"{_UUID_ENV} and {_RUNTIME_ENV} are required"
    assert gpu_uuid == gpu_uuid.strip(), f"{_UUID_ENV} must be exact trimmed text"
    assert runtime_raw == runtime_raw.strip(), (
        f"{_RUNTIME_ENV} must be exact trimmed text"
    )
    configured_runtime = Path(runtime_raw)
    assert configured_runtime.is_absolute(), f"{_RUNTIME_ENV} must be absolute"
    assert not configured_runtime.is_symlink(), f"{_RUNTIME_ENV} must not be a symlink"
    runtime = configured_runtime.resolve()
    assert runtime.is_dir() and (runtime / "bin" / "python").is_file()

    _normal_component_cohort(tmp_path / "normal", gpu_uuid, runtime)
    _lost_attempt_retry(tmp_path / "recovery", gpu_uuid, runtime)
    _cancel_after_cuda_context(tmp_path / "cancel", gpu_uuid, runtime)
    _gpu_revision_loop(tmp_path / "revision", gpu_uuid, runtime)
    _assert_selected_gpu_idle(gpu_uuid)


def test_component_v4_real_shared_gpu_three_action_cycles(tmp_path: Path) -> None:
    if os.environ.get(_SHARED_ENABLE_ENV) != "1":
        pytest.skip(
            f"set {_SHARED_ENABLE_ENV}=1 with an authorized occupied UUID/runtime"
        )
    gpu_uuid = os.environ.get(_UUID_ENV)
    runtime_raw = os.environ.get(_RUNTIME_ENV)
    assert gpu_uuid and runtime_raw, f"{_UUID_ENV} and {_RUNTIME_ENV} are required"
    runtime = Path(runtime_raw)
    assert runtime.is_absolute() and not runtime.is_symlink()
    runtime = runtime.resolve()
    assert runtime.is_dir() and (runtime / "bin/python").is_file()
    baseline_pids = _compute_pids(gpu_uuid)
    assert baseline_pids, "shared qualification requires a pre-existing compute process"
    profiles_root, profile = _shared_profile(tmp_path)
    state_root = tmp_path / "state"
    host = _start_host(state_root, gpu_uuid, runtime)
    try:
        for ordinal in range(1, 4):
            run_id = f"component-v4-real-shared-gpu-{ordinal}"
            _create(
                state_root,
                run_id,
                delay=0.15,
                profile=profile,
                profiles_root=profiles_root,
            )
            status = _await_state(state_root, run_id, "COMPLETED")
            assert status["canonical_outcome_count"] == 1
            attempts = _process_attempts(state_root, run_id)
            assert len(attempts) == 1 and attempts[0]["terminal_reason"] == "SUCCEEDED"
            observation = json.loads(attempts[0]["observation_json"])
            usage = observation["resource_usage"]
            assert usage["gpu_uuid"] == gpu_uuid
            assert usage["sample_count"] >= 2
            assert usage["pressure_exceeded"] is False
            _assert_resource_binding(attempts[0], gpu_uuid)
            _assert_evidence(_cuda_evidence(state_root, attempts[0]["attempt_id"]))
            _assert_oracle_clean(state_root, run_id, outcomes=1)
            assert baseline_pids & _compute_pids(gpu_uuid), (
                "all pre-existing compute processes disappeared during qualification"
            )
    finally:
        _stop_host(host, require_clean=True)
    assert baseline_pids & _compute_pids(gpu_uuid)


def test_component_v4_real_shared_gpu_pressure_guard(tmp_path: Path) -> None:
    if os.environ.get(_SHARED_ENABLE_ENV) != "1":
        pytest.skip(
            f"set {_SHARED_ENABLE_ENV}=1 with an authorized occupied UUID/runtime"
        )
    gpu_uuid = os.environ.get(_UUID_ENV)
    runtime_raw = os.environ.get(_RUNTIME_ENV)
    assert gpu_uuid and runtime_raw, f"{_UUID_ENV} and {_RUNTIME_ENV} are required"
    runtime = Path(runtime_raw)
    assert runtime.is_absolute() and not runtime.is_symlink()
    runtime = runtime.resolve()
    assert runtime.is_dir() and (runtime / "bin/python").is_file()
    baseline_pids = _compute_pids(gpu_uuid)
    assert baseline_pids, "pressure qualification requires a pre-existing process"

    initial_free = _gpu_free_memory_mib(gpu_uuid)
    minimum = 64
    reserve = initial_free - 192
    assert reserve > 0 and initial_free >= minimum + reserve
    profiles_root, profile = _shared_profile(
        tmp_path,
        minimum_memory_mib=minimum,
        reserve_memory_mib=reserve,
    )
    state_root = tmp_path / "state"
    host = _start_host(state_root, gpu_uuid, runtime)
    try:
        transient_run = "component-v4-real-shared-gpu-transient-pressure"
        _create(
            state_root,
            transient_run,
            delay=0.15,
            profile=profile,
            profiles_root=profiles_root,
        )
        _await_state(state_root, transient_run, "COMPLETED")
        transient_attempts = _process_attempts(state_root, transient_run)
        assert len(transient_attempts) == 1
        assert transient_attempts[0]["terminal_reason"] == "SUCCEEDED"
        transient_observation = json.loads(
            transient_attempts[0]["observation_json"]
        )
        transient_usage = transient_observation["resource_usage"]
        assert transient_usage["gpu_minimum_free_memory_mib"] < reserve
        assert transient_usage["pressure_exceeded"] is False
        assert "resource_pressure" not in transient_observation
        _assert_evidence(
            _cuda_evidence(state_root, transient_attempts[0]["attempt_id"])
        )
        _assert_oracle_clean(state_root, transient_run, outcomes=1)
        assert baseline_pids & _compute_pids(gpu_uuid)

        sustained_run = "component-v4-real-shared-gpu-sustained-pressure"
        _create(
            state_root,
            sustained_run,
            delay=8,
            profile=profile,
            profiles_root=profiles_root,
        )
        _await_state(state_root, sustained_run, "COMPLETED")
        sustained_attempts = _process_attempts(state_root, sustained_run)
        assert len(sustained_attempts) == 1
        assert sustained_attempts[0]["terminal_reason"] == "FAILED"
        sustained_observation = json.loads(
            sustained_attempts[0]["observation_json"]
        )
        sustained_usage = sustained_observation["resource_usage"]
        assert sustained_usage["gpu_minimum_free_memory_mib"] < reserve
        assert sustained_usage["pressure_exceeded"] is True
        assert sustained_usage["pressure_attribution"] == "unknown"
        assert sustained_observation["resource_pressure"] == {
            "reason": "sustained_free_gpu_memory_below_reserve",
            "gpu_reserve_memory_mib": reserve,
            "owned_memory_mib_at_decision": None,
            "attribution": "unknown",
            "terminate_grace_seconds": 30.0,
        }
        _assert_evidence(
            _cuda_evidence(state_root, sustained_attempts[0]["attempt_id"])
        )
        _assert_oracle_clean(state_root, sustained_run, outcomes=1)
        assert baseline_pids & _compute_pids(gpu_uuid)
    finally:
        _stop_host(host, require_clean=True)
    assert baseline_pids & _compute_pids(gpu_uuid)


_FOREIGN_TENANT = '''
import os, sys, time
import torch
baseline_mib, grow_mib = (int(arg) for arg in sys.argv[1:3])
ready_path, grow_path, stop_path = sys.argv[3:6]
held = []
def allocate(mib):
    tensor = torch.empty(mib * 1024 * 1024, dtype=torch.uint8, device="cuda")
    tensor.fill_(1)
    torch.cuda.synchronize()
    held.append(tensor)
allocate(baseline_mib)
with open(ready_path, "w", encoding="utf-8") as stream:
    stream.write("ready")
grown = False
deadline = time.monotonic() + 900
while time.monotonic() < deadline and not os.path.exists(stop_path):
    if not grown and grow_mib and os.path.exists(grow_path):
        allocate(grow_mib)
        grown = True
    time.sleep(0.05)
'''


def _start_foreign_tenant(
    runtime: Path,
    gpu_uuid: str,
    directory: Path,
    *,
    baseline_mib: int,
    grow_mib: int,
) -> subprocess.Popen[str]:
    script = directory / "foreign_tenant.py"
    script.write_text(_FOREIGN_TENANT, encoding="utf-8")
    process = subprocess.Popen(
        [
            str(runtime / "bin" / "python"),
            str(script),
            str(baseline_mib),
            str(grow_mib),
            str(directory / "ready"),
            str(directory / "grow"),
            str(directory / "stop"),
        ],
        env={
            **os.environ,
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "CUDA_VISIBLE_DEVICES": gpu_uuid,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if (directory / "ready").exists():
            return process
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"foreign tenant exited early: {stdout=!r} {stderr=!r}"
            )
        time.sleep(0.1)
    os.killpg(process.pid, signal.SIGKILL)
    raise AssertionError("foreign tenant did not establish its CUDA context")


def _stop_foreign_tenant(directory: Path, process: subprocess.Popen[str]) -> None:
    (directory / "stop").write_text("stop", encoding="utf-8")
    try:
        process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=15)


def _start_pool_server(
    pool_root: Path, socket_path: Path, gpu_uuid: str, *, reserve_mib: int
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypoloom.cli",
            "gpu-pool-serve",
            "--pool-root",
            str(pool_root),
            "--socket-path",
            str(socket_path),
            "--member",
            f"{os.getuid()}={gpu_uuid}",
            "--reserve-mib",
            str(reserve_mib),
        ],
        cwd=_PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if socket_path.is_socket():
            return process
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"pool server exited early: {stdout=!r} {stderr=!r}")
        time.sleep(0.05)
    os.killpg(process.pid, signal.SIGKILL)
    raise AssertionError("pool server did not create its socket")


def test_component_v4_real_shared_gpu_pressure_attribution(tmp_path: Path) -> None:
    if os.environ.get(_SHARED_ENABLE_ENV) != "1":
        pytest.skip(
            f"set {_SHARED_ENABLE_ENV}=1 with an authorized occupied UUID/runtime"
        )
    gpu_uuid = os.environ.get(_UUID_ENV)
    runtime_raw = os.environ.get(_RUNTIME_ENV)
    assert gpu_uuid and runtime_raw, f"{_UUID_ENV} and {_RUNTIME_ENV} are required"
    runtime = Path(runtime_raw)
    assert runtime.is_absolute() and not runtime.is_symlink()
    runtime = runtime.resolve()
    assert runtime.is_dir() and (runtime / "bin/python").is_file()

    tenant_dir = tmp_path / "foreign-tenant"
    tenant_dir.mkdir()
    tenant = _start_foreign_tenant(
        runtime, gpu_uuid, tenant_dir, baseline_mib=512, grow_mib=2048
    )
    pool_process: subprocess.Popen[str] | None = None
    host: subprocess.Popen[str] | None = None
    try:
        baseline_pids = _compute_pids(gpu_uuid)
        assert tenant.pid in baseline_pids
        initial_free = _gpu_free_memory_mib(gpu_uuid)
        minimum = 64
        reserve = initial_free - 192
        assert reserve > 0 and initial_free >= minimum + reserve
        profiles_root, profile = _shared_profile(
            tmp_path,
            minimum_memory_mib=minimum,
            reserve_memory_mib=reserve,
        )
        pool_root = tmp_path / "pool"
        # AF_UNIX sun_path is bounded; the archived basetemp is deeper than 107 bytes.
        pool_socket = Path(tempfile.mkdtemp(prefix="hyp-pool-")) / "pool.sock"
        pool_process = _start_pool_server(pool_root, pool_socket, gpu_uuid, reserve_mib=512)
        namespace = f"g4-pressure-{os.getpid()}"
        state_root = tmp_path / "state"
        host = _start_host(state_root, gpu_uuid, runtime, pool=(pool_socket, namespace))

        # First, this workload alone pushes free memory below reserve;
        # releasing its owned context restores reserve and must stop it.
        self_run = "component-v4-real-shared-gpu-self-pressure"
        _create(
            state_root,
            self_run,
            delay=20,
            profile=profile,
            profiles_root=profiles_root,
        )
        _await_state(state_root, self_run, "COMPLETED", timeout=180.0)
        self_attempts = _process_attempts(state_root, self_run)
        assert len(self_attempts) == 1
        assert self_attempts[0]["terminal_reason"] == "FAILED"
        self_observation = json.loads(self_attempts[0]["observation_json"])
        self_usage = self_observation["resource_usage"]
        assert self_usage["gpu_minimum_free_memory_mib"] < reserve
        assert self_usage["pressure_exceeded"] is True
        assert self_usage["pressure_attribution"] == "self"
        self_pressure = self_observation["resource_pressure"]
        owned_at_decision = self_pressure["owned_memory_mib_at_decision"]
        assert type(owned_at_decision) is int and owned_at_decision > 0
        assert self_pressure == {
            "reason": "sustained_free_gpu_memory_below_reserve",
            "gpu_reserve_memory_mib": reserve,
            "owned_memory_mib_at_decision": owned_at_decision,
            "attribution": "self",
            "terminate_grace_seconds": 30.0,
        }
        _assert_oracle_clean(state_root, self_run, outcomes=1)
        external_run = "component-v4-real-shared-gpu-external-pressure"
        _create(
            state_root,
            external_run,
            delay=20,
            profile=profile,
            profiles_root=profiles_root,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and _gpu_free_memory_mib(gpu_uuid) >= initial_free - 128:
            time.sleep(0.2)
        assert _gpu_free_memory_mib(gpu_uuid) < initial_free - 128, (
            "workload CUDA context never materialized"
        )
        (tenant_dir / "grow").write_text("grow", encoding="utf-8")
        _await_state(state_root, external_run, "COMPLETED")
        external_attempts = _process_attempts(state_root, external_run)
        assert len(external_attempts) == 1
        assert external_attempts[0]["terminal_reason"] == "SUCCEEDED"
        external_observation = json.loads(external_attempts[0]["observation_json"])
        external_usage = external_observation["resource_usage"]
        assert external_usage["gpu_minimum_free_memory_mib"] < reserve
        assert external_usage["owned_memory"]["sample_count"] > 0
        # The foreign tenant grows after admission. Its allocation alone
        # breaches reserve; releasing our own context cannot restore it.
        if external_usage["pressure_exceeded"]:
            assert external_usage["pressure_attribution"] == "external"
        assert "resource_pressure" not in external_observation
        _assert_evidence(
            _cuda_evidence(state_root, external_attempts[0]["attempt_id"])
        )
        _assert_oracle_clean(state_root, external_run, outcomes=1)

        assert tenant.poll() is None
        assert baseline_pids & _compute_pids(gpu_uuid), (
            "the pressure kill signaled the foreign tenant"
        )
        assert audit_gpu_pool(pool_root)["ok"]
    finally:
        if host is not None:
            _stop_host(host, require_clean=True)
        if pool_process is not None:
            os.killpg(pool_process.pid, signal.SIGTERM)
            try:
                pool_process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(pool_process.pid, signal.SIGKILL)
                pool_process.communicate(timeout=10)
        _stop_foreign_tenant(tenant_dir, tenant)
