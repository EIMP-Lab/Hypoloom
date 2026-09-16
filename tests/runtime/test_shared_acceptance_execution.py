"""Raw/local/common parser mechanics, without a Host or scientific verdict."""

import json
import sys
from types import SimpleNamespace

import pytest

from hypoloom.domain import AttemptTerminalReason
from hypoloom.measurement_driver import run_sequence, sequence_request
from hypoloom.runner import LocalRunner, ProcessRequest
from hypoloom.shared_acceptance_execution import evaluation_phase_success


def request(tmp_path, name):
    return ProcessRequest(
        attempt_id=name,
        attempt_ordinal=1,
        execution_id="execution-1",
        argv=("/bin/true",),
        cwd=str(tmp_path / name),
        env={},
        timeout_seconds=3,
    )


@pytest.mark.parametrize(
    "local,goal,expected", [(0, 0, 0), (1, 0, 71), (0, 1, 72), (1, 1, 73)]
)
def test_goal_sequence_records_independent_stage_exits(
    tmp_path, monkeypatch, local, goal, expected
):
    calls = []

    def run(self, command):
        calls.append(command.attempt_id)
        code = {"raw": 0, "local": local, "goal": goal}[command.attempt_id]
        return SimpleNamespace(
            returncode=code,
            terminal_reason=AttemptTerminalReason.FAILED
            if code
            else AttemptTerminalReason.SUCCEEDED,
        )

    monkeypatch.setattr(LocalRunner, "run", run)
    plan = {
        "raw_request": request(tmp_path, "raw").to_dict(),
        "parser_request": request(tmp_path, "local").to_dict(),
        "goal_parser_request": request(tmp_path, "goal").to_dict(),
    }
    assert run_sequence(plan) == expected
    assert calls == ["raw", "local", "goal"]


def test_goal_only_has_no_invented_local_parser(tmp_path, monkeypatch):
    calls = []

    def run(self, command):
        calls.append(command.attempt_id)
        return SimpleNamespace(
            returncode=0, terminal_reason=AttemptTerminalReason.SUCCEEDED
        )

    monkeypatch.setattr(LocalRunner, "run", run)
    outer = sequence_request(
        request(tmp_path, "raw"),
        None,
        goal_parser_request=request(tmp_path, "goal"),
        attempt_id="outer",
        attempt_ordinal=1,
        execution_id="execution-1",
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert run_sequence(json.loads(outer.argv[3])) == 0
    assert calls == ["raw", "goal"]


def test_legacy_sequence_plan_bytes_are_unchanged(tmp_path):
    raw, local = request(tmp_path, "raw"), request(tmp_path, "local")
    outer = sequence_request(
        raw,
        local,
        attempt_id="outer",
        attempt_ordinal=1,
        execution_id="execution-1",
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert outer.argv[3] == json.dumps(
        {"raw_request": raw.to_dict(), "parser_request": local.to_dict()},
        sort_keys=True,
        separators=(",", ":"),
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        (0, (True, True, True)),
        (70, (False, False, False)),
        (71, (True, False, True)),
        (72, (True, True, False)),
        (73, (True, False, False)),
        (-9, (False, False, False)),
    ],
)
def test_phase_status_uses_actual_outer_observation(code, expected):
    observed = {
        "terminal_reason": "SUCCEEDED" if code == 0 else "FAILED",
        "returncode": code,
    }
    assert evaluation_phase_success(observed, has_local=True, has_goal=True) == expected
    observed["terminal_reason"] = "TIMED_OUT"
    assert evaluation_phase_success(observed, has_local=True, has_goal=True) == (
        False,
        False,
        False,
    )


def test_new_sequence_raw_failure_never_runs_either_parser(tmp_path, monkeypatch):
    calls = []

    def run(self, command):
        calls.append(command.attempt_id)
        return SimpleNamespace(
            returncode=3, terminal_reason=AttemptTerminalReason.FAILED
        )

    monkeypatch.setattr(LocalRunner, "run", run)
    plan = {
        "raw_request": request(tmp_path, "raw").to_dict(),
        "parser_request": request(tmp_path, "local").to_dict(),
        "goal_parser_request": request(tmp_path, "goal").to_dict(),
    }
    assert run_sequence(plan) == 70
    assert calls == ["raw"]


def test_shared_parser_actual_nonzero_cannot_be_promoted_by_written_result(tmp_path):
    from hypoloom.measurement_execution import prepare_parser

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "evidence.txt").write_text("actual raw input")
    declaration = SimpleNamespace(
        parser_files=("evaluate.py",),
        parser_argv=("python", "evaluate.py"),
        raw_artifacts=("evidence.txt",),
    )
    parser = prepare_parser(
        declaration,
        {
            "evaluate.py": b'from pathlib import Path\nPath(\'/parsed/result.json\').write_text(\'{"measurements":{},"checks":{"artifact":true}}\')\nraise SystemExit(2)\n'
        },
        raw_dir=raw,
        work_dir=tmp_path / "goal",
        attempt_id="goal",
        execution_id="execution-1",
        timeout_seconds=5,
        python_executable=sys.executable,
        runtime_prefix=sys.prefix,
    )
    outer = sequence_request(
        request(tmp_path, "raw"),
        None,
        goal_parser_request=parser.process_request,
        attempt_id="outer",
        attempt_ordinal=1,
        execution_id="execution-1",
        cwd=tmp_path / "logs",
        timeout_seconds=10,
    )
    (tmp_path / "logs").mkdir()
    observed = LocalRunner().run(outer)
    assert observed.returncode == 72
    assert (parser.output_dir / "result.json").is_file()
    assert evaluation_phase_success(
        observed.to_dict(), has_local=False, has_goal=True
    ) == (True, None, False)


@pytest.mark.parametrize(
    "local_failure,goal_failure",
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_real_dual_parser_collection_retains_each_stage(
    tmp_path, local_failure, goal_failure
):
    from hypoloom.shared_acceptance_execution import (
        plan_evaluation,
        materialize_evaluation,
        collect_evaluation,
        read_evaluation_results,
    )
    from tests.runtime.test_execution_workspace import candidate_pair, make_workspace
    from tests.runtime.test_measurement_contract import declaration
    from tests.runtime.test_shared_acceptance_oracle import acceptance
    from hypoloom.shared_acceptance import SharedAcceptance
    from hypoloom.shared_acceptance_oracle import (
        audit_parser_process_requests,
        audit_shared_result,
    )

    workspace = make_workspace(tmp_path)
    agent_request, agent_response = candidate_pair()
    raw = workspace.plan(
        agent_request=agent_request,
        agent_response=agent_response,
        attempt_id="dual",
        attempt_ordinal=1,
        execution_id="execution-1",
        target_env={},
        timeout_seconds=10,
        execution_argv=(
            "/bin/sh",
            "-c",
            "printf '10,8\n10,9\n10,100\n' > timings.csv; echo raw-stage",
        ),
        allow_generic_argv=True,
        read_only_bindings=(("/usr/bin", "/bin"),),
    )
    spec = declaration()
    common = acceptance()
    common["raw_artifacts"] = ["timings.csv"]
    local_source = b"""import json
from pathlib import Path
rows = [list(map(float, row.split(','))) for row in Path('/raw/timings.csv').read_text().splitlines()]
Path('/parsed/result.json').write_text(json.dumps({'metric':'median_latency_delta','value':str(sorted(row[1] for row in rows)[1]),'baseline':'10'}))
"""
    shared_source = b"""import json
from pathlib import Path
rows = [list(map(float, row.split(','))) for row in Path('/raw/timings.csv').read_text().splitlines()]
value = sorted(row[1] for row in rows)[1] / 10
Path('/parsed/result.json').write_text(json.dumps({'measurements':{'left':str(value),'right':str(value)},'checks':{'valid':True}}))
"""
    if local_failure:
        local_source += b"raise SystemExit(2)\n"
    if goal_failure:
        shared_source += b"raise SystemExit(3)\n"
    plan = plan_evaluation(
        raw,
        local_measurement=spec,
        local_files={"parse_metrics.py": local_source},
        goal_acceptance=SharedAcceptance.from_dict(common),
        goal_files={"evaluate.py": shared_source},
        execution_id="execution-1",
        timeout_seconds=10,
    )
    assert not plan.execution.attempt_root.exists(), (
        "planning must not write before its OPEN intent"
    )
    workspace.materialize(
        plan.execution, agent_request=agent_request, agent_response=agent_response
    )
    materialize_evaluation(plan)
    observation = LocalRunner().run(plan.execution.process_request).to_dict()
    assert (
        observation["returncode"]
        == {(False, False): 0, (True, False): 71, (False, True): 72, (True, True): 73}[
            local_failure, goal_failure
        ]
    )
    binding = {"scope": "GOAL", "source_attempt_id": "grounding", "acceptance": common}
    assert (
        audit_parser_process_requests(
            plan.execution.process_request.to_dict(),
            {"measurement": spec.to_dict(), "goal_evaluation": binding},
        )["attempt_id"]
        == "dual"
    )
    local, goal = read_evaluation_results(plan, observation)
    assert (local is None) is local_failure
    assert (goal is None) is goal_failure
    collected = workspace.collect(
        plan.execution,
        observation,
        agent_request=agent_request,
        agent_response=agent_response,
        preserve_partial_outputs=True,
    )
    published = collect_evaluation(plan, collected, observation)
    assert (
        published.staging_dir / "raw/timings.csv"
    ).read_text() == "10,8\n10,9\n10,100\n"
    assert (published.staging_dir / "result.json").is_file()
    assert (published.staging_dir / "goal/result.json").is_file()
    if not goal_failure:
        assert audit_shared_result(
            common,
            {"evaluate.py": shared_source},
            published.staging_dir,
            runtime_prefix=sys.prefix,
            python_executable=sys.executable,
        ).goal_met
    else:
        with pytest.raises(ValueError, match="replay"):
            audit_shared_result(
                common,
                {"evaluate.py": shared_source},
                published.staging_dir,
                runtime_prefix=sys.prefix,
                python_executable=sys.executable,
            )
