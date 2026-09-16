from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from hypoloom.control_protocol import ControlRequest, request_over_socket
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile


REPOSITORY = Path(__file__).resolve().parents[2]
CRASH_POINTS = (
    "before_process_intent_commit",
    "after_process_intent_before_spawn",
    "after_spawn_before_observation",
    "after_observation_before_artifact_collection",
    "after_artifact_publish_before_digest_commit",
    "after_evaluator_before_outcome_acceptance",
    "inside_outcome_after_artifact_before_fact",
    "inside_outcome_after_fact_before_terminal",
    "after_outcome_commit_before_cleanup",
)
AGENT_CRASH_POINTS = (
    "before_agent_intent_commit",
    "after_agent_intent_before_start",
    "after_agent_observation_before_close",
    "inside_agent_after_close_before_fact",
    "inside_agent_after_fact_before_transition",
    "after_agent_terminal_commit",
)


def start_host(state_root: Path, fault_point: str | None = None) -> subprocess.Popen[str]:
    arguments = [sys.executable, "-m", "hypoloom.cli", "serve", "--state-root", str(state_root)]
    if fault_point:
        arguments.extend(("--fault-point", fault_point))
    process = subprocess.Popen(
        arguments,
        cwd=REPOSITORY,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if (state_root / "control.sock").is_socket():
            try:
                request_over_socket(
                    state_root / "control.sock",
                    ControlRequest(1, str(uuid.uuid4()), None, "STATUS", {"run_id": "probe"}),
                    timeout=0.1,
                )
            except (ConnectionError, TimeoutError, OSError):
                pass
            else:
                return process
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"Host exited before serving: {stdout=} {stderr=}")
        time.sleep(0.02)
    stop_host(process)
    raise AssertionError("Host did not create control socket")


def stop_host(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)


def submit(state_root: Path, command: str, payload: dict[str, object], revision: int | None):
    return request_over_socket(
        state_root / "control.sock",
        ControlRequest(1, str(uuid.uuid4()), revision, command, payload),
        timeout=5,
    )


def create_payload(run_id: str, *, delay: float = 0.25) -> dict[str, object]:
    admission = admit_profile(
        REPOSITORY / "tests/fixtures/profiles/component-demo", run_id=run_id, delay_seconds=delay
    )
    assert admission.admitted and admission.create_run_payload is not None
    return admission.create_run_payload


def wait_for_completed(state_root: Path, run_id: str) -> None:
    deadline = time.monotonic() + 20
    latest = None
    while time.monotonic() < deadline:
        latest = submit(state_root, "STATUS", {"run_id": run_id}, None)
        if latest.result and latest.result["run_state"] == "COMPLETED":
            return
        time.sleep(0.04)
    raise AssertionError(f"restart did not converge: {latest}")


@pytest.mark.parametrize("fault_point", CRASH_POINTS)
def test_each_component_process_crash_boundary_recovers_without_duplicate_science(
    tmp_path: Path, fault_point: str
) -> None:
    state_root = tmp_path / fault_point
    run_id = f"run-{fault_point}"
    crashed = start_host(state_root, fault_point)
    try:
        created = submit(state_root, "CREATE_RUN", create_payload(run_id), 0)
        assert created.status == "accepted", created
        assert crashed.wait(timeout=15) == 86
        crashed.communicate(timeout=2)
    finally:
        stop_host(crashed)

    resumed = start_host(state_root)
    try:
        wait_for_completed(state_root, run_id)
    finally:
        stop_host(resumed)

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 1
    assert report.open_attempt_count == 0
    assert report.open_decision_count == 0
    assert report.nonterminal_experiment_count == 0
    assert report.owned_process_residue == ()


@pytest.mark.parametrize("fault_point", AGENT_CRASH_POINTS)
def test_each_component_agent_crash_boundary_recovers_without_duplicate_science(
    tmp_path: Path, fault_point: str
) -> None:
    state_root = tmp_path / fault_point
    run_id = f"run-{fault_point}"
    crashed = start_host(state_root, fault_point)
    try:
        created = submit(state_root, "CREATE_RUN", create_payload(run_id), 0)
        assert created.status == "accepted", created
        assert crashed.wait(timeout=15) == 86
        crashed.communicate(timeout=2)
    finally:
        stop_host(crashed)

    resumed = start_host(state_root)
    try:
        wait_for_completed(state_root, run_id)
    finally:
        stop_host(resumed)

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 1
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.duplicate_effect_count == 0
    assert report.owned_process_residue == ()


def test_component_cancel_is_durable_and_terminal_clean(tmp_path: Path) -> None:
    state_root = tmp_path / "cancel"
    run_id = "cancel-run"
    process = start_host(state_root)
    try:
        created = submit(state_root, "CREATE_RUN", create_payload(run_id, delay=2), 0)
        assert created.status == "accepted", created
        deadline = time.monotonic() + 10
        status = None
        while time.monotonic() < deadline:
            status = submit(state_root, "STATUS", {"run_id": run_id}, None)
            if status.result and status.result["open_attempts"]:
                break
            time.sleep(0.02)
        assert status is not None and status.result is not None
        cancelled = submit(state_root, "CANCEL", {"run_id": run_id, "cause": "test_cancel"},
                           int(status.result["run_revision"]))
        assert cancelled.status == "accepted", cancelled
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            terminal = submit(state_root, "STATUS", {"run_id": run_id}, None)
            if terminal.result and terminal.result["run_state"] == "CANCELLED":
                break
            time.sleep(0.03)
        else:
            raise AssertionError(f"cancel did not close: {terminal}")
    finally:
        stop_host(process)

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.open_attempt_count == 0
