"""External-process G3 coverage for the expert-loop production topology."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
from hypoloom.control_protocol import ControlRequest, request_over_socket
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import PROFILE_FILES, PROFILE_V3_FILES


_REPOSITORY = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
_TEMPLATE_V3 = _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot-v3"
_REAL_BWRAP = pytest.mark.skipif(
    not Path("/usr/bin/bwrap").is_file(),
    reason="external G3 requires the production /usr/bin/bwrap executable",
)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-expert-external-g3-",
        dir="/var/tmp",
    ) as raw:
        yield Path(raw)


def _materialize_profile(
    root: Path,
    mount_root: Path,
    *,
    profile_version: int = 2,
) -> tuple[Path, str]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text(
        "Bounded external-process expert-loop G3 target.\n",
        encoding="utf-8",
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom G3",
        "-c",
        "user.email=hypoloom@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    data = mount_root / "data"
    data.mkdir()
    (data / "validation.json").write_text(
        json.dumps({"labels": [0, 1, 1, 0], "predictions": [0, 1, 1, 0]}),
        encoding="utf-8",
    )
    profiles_root = root / "profiles"
    profile_name = f"expert-loop-g3-v{profile_version}"
    profile = profiles_root / profile_name
    profile.mkdir(parents=True)
    template, files = (
        (_TEMPLATE_V3, PROFILE_V3_FILES)
        if profile_version == 3
        else (_TEMPLATE, PROFILE_FILES)
    )
    for name in files:
        text = (template / name).read_text(encoding="utf-8")
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-target", str(target.resolve())
        )
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-data", str(data.resolve())
        )
        if name == "experts.yaml":
            text = text.replace("per_expert_tokens: 20000", "per_expert_tokens: 40000")
        (profile / name).write_text(text, encoding="utf-8")
    return profiles_root, profile_name


def _child_environment() -> dict[str, str]:
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("HYPOLOOM_REAL_GPU", None)
    environment.pop("HYPOLOOM_REAL_GPU_UUID", None)
    environment.pop("HYPOLOOM_REAL_GPU_RUNTIME", None)
    return environment


def _cli(
    *arguments: str,
    timeout: float = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        (sys.executable, "-m", "hypoloom.cli", *arguments),
        cwd=_REPOSITORY,
        env=_child_environment(),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"CLI failed: {arguments=!r} returncode={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result


def _profile_check(profiles_root: Path, profile: str, run_id: str) -> None:
    result = _cli(
        "profile-check",
        profile,
        "--cpu-only",
        "--profiles-root",
        str(profiles_root),
        "--run-id",
        run_id,
        "--literature-adapter",
        "offline",
    )
    payload = json.loads(result.stdout)
    assert payload["admitted"] is True, payload
    assert payload["create_run_payload"]["execution"]["allow_gpu"] is False
    assert payload["literature_preflight"] == {
        "adapter": "offline",
        "network_probe_performed": False,
        "qualified": True,
    }


def _group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_host(
    process: subprocess.Popen[str],
    *,
    require_clean: bool,
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
    assert not _group_exists(process.pid), f"Host process group survived: {process.pid}"
    return stdout, stderr


def _start_host(
    state_root: Path,
    *,
    fault_point: str | None = None,
) -> subprocess.Popen[str]:
    arguments = [
        sys.executable,
        "-m",
        "hypoloom.cli",
        "serve",
        "--state-root",
        str(state_root),
        "--agent-adapter",
        "fake",
        "--literature-adapter",
        "offline",
    ]
    if fault_point is not None:
        arguments.extend(("--fault-point", fault_point))
    process = subprocess.Popen(
        arguments,
        cwd=_REPOSITORY,
        env=_child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    socket_path = state_root / "control.sock"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if socket_path.is_socket():
            try:
                response = request_over_socket(
                    socket_path,
                    ControlRequest(
                        schema_version=1,
                        request_id=str(uuid.uuid4()),
                        expected_revision=None,
                        command="STATUS",
                        payload={"run_id": "external-g3-readiness-probe"},
                    ),
                    timeout=0.2,
                )
            except (ConnectionError, TimeoutError, OSError):
                pass
            else:
                assert response.status == "rejected"
                return process
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"Host exited before serving: {stdout=!r} {stderr=!r}")
        time.sleep(0.02)
    _stop_host(process, require_clean=False)
    raise AssertionError("Host did not create a responsive control socket")


def _start_run(
    state_root: Path,
    profiles_root: Path,
    profile: str,
    run_id: str,
) -> None:
    result = _cli(
        "start",
        profile,
        "--cpu-only",
        "--profiles-root",
        str(profiles_root),
        "--state-root",
        str(state_root),
        "--run-id",
        run_id,
        "--request-id",
        f"{run_id}-create-1",
        "--process-delay",
        "0",
        "--literature-adapter",
        "offline",
    )
    response = json.loads(result.stdout)
    assert response["status"] == "accepted", response
    assert response["request_id"] == f"{run_id}-create-1"


def _await_completed(state_root: Path, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            response = request_over_socket(
                state_root / "control.sock",
                ControlRequest(
                    schema_version=1,
                    request_id=str(uuid.uuid4()),
                    expected_revision=None,
                    command="STATUS",
                    payload={"run_id": run_id},
                ),
                timeout=2,
            )
        except (ConnectionError, TimeoutError, OSError):
            time.sleep(0.02)
            continue
        assert response.status == "accepted", response
        latest = dict(response.result or {})
        if latest.get("run_state") == "COMPLETED":
            return latest
        if latest.get("run_state") in {"FAILED", "CANCELLED"}:
            raise AssertionError(f"expert-loop terminated unsuccessfully: {latest!r}")
        time.sleep(0.02)
    raise AssertionError(f"external expert-loop did not complete: {latest!r}")


def _rows(database: Path, statement: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(statement, parameters))
    finally:
        connection.close()


def _assert_clean_scientific_closure(state_root: Path, run_id: str) -> None:
    database = state_root / "state.sqlite"
    facts = _rows(
        database,
        "SELECT fact_type,COUNT(*) AS count FROM research_facts "
        "WHERE run_id=? GROUP BY fact_type",
        (run_id,),
    )
    counts = {str(row["fact_type"]): int(row["count"]) for row in facts}
    assert counts == {
        "CanonicalOutcomeAccepted": 1,
        "EvidenceLinked": 1,
        "ExperimentPreregistered": 1,
        "GoalDefined": 1,
        "GoalVerdictRecorded": 1,
        "HypothesisAssessed": 1,
        "HypothesisProposed": 1,
    }
    attempts = _rows(
        database,
        "SELECT * FROM execution_attempts WHERE run_id=? ORDER BY started_at,attempt_id",
        (run_id,),
    )
    all_process_attempts = [row for row in attempts if row["kind"] == "PROCESS"]
    # Fresh admitted-submit execution has one real Process. Parse-only source
    # admission does not fabricate the retained candidate-validation Process.
    assert len(all_process_attempts) == 1
    assert all(row["terminal_reason"] == "SUCCEEDED" for row in all_process_attempts)
    assert all(
        json.loads(row["observation_json"])["process_request"]["argv"][0]
        == "/usr/bin/bwrap"
        for row in all_process_attempts
    )
    process_attempts = _rows(
        database,
        "SELECT a.* FROM execution_attempts a JOIN research_facts f ON "
        "f.run_id=a.run_id AND f.fact_type='ExperimentPreregistered' "
        "AND f.experiment_id=a.experiment_id AND f.execution_id=a.execution_id "
        "WHERE a.run_id=? AND a.kind='PROCESS'",
        (run_id,),
    )
    assert len(process_attempts) == 1
    assert process_attempts[0]["terminal_reason"] == "SUCCEEDED"
    observation = json.loads(process_attempts[0]["observation_json"])
    argv = observation["process_request"]["argv"]
    assert argv[0] == "/usr/bin/bwrap"
    assert argv[argv.index("--unsetenv") + 1] == "CUDA_VISIBLE_DEVICES"
    assert "--dev-bind" not in argv
    publications = _rows(
        database,
        "SELECT * FROM execution_artifacts WHERE run_id=?",
        (run_id,),
    )
    assert len(publications) == 1

    commands = [
        json.loads(row["observation_json"])["request"]["command"]
        for row in attempts
        if row["kind"] == "AGENT" and row["terminal_reason"] == "SUCCEEDED"
    ]
    for required in (
        "GROUNDING",
        "LITERATURE_REVIEW",
        "HYPOTHESIS_PROPOSAL",
        "EXPERIMENT_DESIGN_IMPLEMENT",
        "EXPERIMENT_EXECUTION",
        "RESULT_ANALYSIS",
        "ADJUDICATE",
        "HYPOTHESIS_UPDATE",
        "CYCLE_REPORT",
        "SELF_EVOLVE",
        "FINAL_REPORT",
    ):
        assert required in commands
    assert commands.count("HYPOTHESIS_PROPOSAL") == 3
    assert all(json.loads(row["observation_json"])["request"]["schema_version"] == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
               for row in attempts if row["kind"] == "AGENT")

    report = Oracle(database, state_root / "artifacts").audit(run_id)
    assert report.ok, report.to_json()
    assert report.canonical_outcome_count == 1
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.owned_process_residue == ()
    work = state_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())
    assert not (state_root / "git-authoring-worktrees").exists()
    assert not (state_root / "control.sock").exists()


@_REAL_BWRAP
def test_external_fake_expert_loop_closes_the_production_topology(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-v3-external-g3-normal"
    profiles_root, profile = _materialize_profile(
        tmp_path,
        mount_root,
        profile_version=3,
    )
    _profile_check(profiles_root, profile, run_id)
    state_root = tmp_path / "state"
    host = _start_host(state_root)
    try:
        _start_run(state_root, profiles_root, profile, run_id)
        status = _await_completed(state_root, run_id)
        assert status["canonical_outcome_count"] == 1
    finally:
        _stop_host(host, require_clean=True)
    _assert_clean_scientific_closure(state_root, run_id)


@_REAL_BWRAP
def test_external_fake_expert_loop_recovers_one_lost_agent_attempt(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-external-g3-lost-agent"
    profiles_root, profile = _materialize_profile(tmp_path, mount_root)
    _profile_check(profiles_root, profile, run_id)
    state_root = tmp_path / "state"
    crashed = _start_host(
        state_root,
        fault_point="after_agent_intent_before_start",
    )
    try:
        _start_run(state_root, profiles_root, profile, run_id)
        assert crashed.wait(timeout=20) == 86
        crashed.communicate(timeout=2)
        assert not _group_exists(crashed.pid)
    finally:
        _stop_host(crashed, require_clean=False)

    resumed = _start_host(state_root)
    try:
        status = _await_completed(state_root, run_id)
        assert status["canonical_outcome_count"] == 1
    finally:
        _stop_host(resumed, require_clean=True)

    agent_attempts = _rows(
        state_root / "state.sqlite",
        "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT' "
        "ORDER BY started_at,attempt_id",
        (run_id,),
    )
    lost = [row for row in agent_attempts if row["terminal_reason"] == "LOST"]
    assert len(lost) == 1
    retries = [
        row for row in agent_attempts
        if row["command_id"] == lost[0]["command_id"]
    ]
    assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in retries] == [
        (1, "LOST"),
        (2, "SUCCEEDED"),
    ]
    lost_request = json.loads(lost[0]["observation_json"])["request"]
    retry_request = json.loads(retries[1]["observation_json"])["request"]
    assert retry_request["command_id"] == lost_request["command_id"]
    assert retry_request["task_context"] == lost_request["task_context"]
    _assert_clean_scientific_closure(state_root, run_id)
