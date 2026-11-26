import hashlib
import json
import subprocess
import sys
from dataclasses import replace

import pytest
import yaml

from hypoloom.agent_aci import AgentACIError, EmbeddedInput, WorkspaceArtifact, validate_experiment_submission
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.goal_preflight import check_submission, read_report
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import LocalRunner
from hypoloom.shared_acceptance import SharedAcceptance
from hypoloom.shared_acceptance_execution import collect_evaluation, evaluation_phase_success, materialize_evaluation, plan_evaluation
from hypoloom.shared_acceptance_oracle import audit_parser_process_requests, phase_success
from test_public_profiles import _documents
from test_submission_recovery import submission


SOURCE = b"""import json, os, sys, time
from pathlib import Path
sample = '--preflight' in sys.argv
root = Path('/hypoloom-evaluation/1')
assert root.is_dir() != sample
assert (str(root) in json.loads(os.environ['HYPOLOOM_READ_ROOTS'])) != sample
assert not Path('preflight.json').exists()
mode = sys.argv[1]
if sample:
    if mode == 'timeout':
        time.sleep(30)
    if mode != 'missing':
        Path('preflight.json').write_text(json.dumps({'passed': mode != 'fail'}))
if not sample or mode != 'no-evidence':
    value = 'sample' if sample else (root / 'value').read_text()
    Path('data.json').write_text(json.dumps(value))
if not sample and mode == 'raw-fail':
    raise RuntimeError('formal failed after preflight')
"""
SPEC = {"path": "experiment.py", "sha256": hashlib.sha256(SOURCE).hexdigest(), "timeout_seconds": 2}
PARSER = b"""import json
from pathlib import Path
assert json.loads(Path('/raw/data.json').read_bytes()) == 'formal'
Path('/parsed/result.json').write_text('{"measurements":{"score":1},"checks":{}}')
"""
GOAL = {"summary": "positive score", "rule": "ALL", "criteria": [{"name": "score", "operator": "GT", "target": "0"}],
        "validity_checks": [], "parser_argv": ["python", "/input/score.py"],
        "parser_files": ["score.py"], "raw_artifacts": ["data.json"]}


def test_profile_pins_a_regular_git_blob_and_projects_the_binding(tmp_path):
    target, profile = tmp_path / "repo", tmp_path / "profile"
    target.mkdir()
    profile.mkdir()
    (target / "experiment.py").write_bytes(SOURCE)
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=test", "-c", "user.email=test@local",
                                              "commit", "-qm", "template"]):
        subprocess.run(["git", *cmd], cwd=target, check=True)
    docs = _documents("multi-expert-en")
    docs["methodology"]["runtime"].update(target_repo=str(target), conda_env=sys.prefix)
    docs["safety"]["allow_read_paths"] = []
    docs["goal"].update(evaluation="grounding", preflight=SPEC)
    docs["goal"].pop("metric", None)
    for section, value in docs.items():
        (profile / (section + ".yaml")).write_text(yaml.safe_dump(value))
    report = admit_profile(profile, run_id="template")
    assert report.admitted, report.to_dict()
    goal = report.create_run_payload["research_contract"]["methodology"]["controls"]["product"]["goal"]
    assert goal["preflight"] == SPEC
    docs["goal"]["preflight"] = {**SPEC, "sha256": "0" * 64}
    (profile / "goal.yaml").write_text(yaml.safe_dump(docs["goal"]))
    assert "preflight_binding_failed" in {b.code for b in admit_profile(profile, run_id="template").blockers}
    check_submission(SPEC, ["python", "/input/experiment.py"], {"experiment.py": SOURCE})
    for argv, files in ((["python", "/input/alternate.py"], {"experiment.py": SOURCE}),
                        (["python", "/input/experiment.py", "--preflight"], {"experiment.py": SOURCE}),
                        (["python", "/input/experiment.py"], {"experiment.py": b"changed"})):
        with pytest.raises(ValueError):
            check_submission(SPEC, argv, files)


def test_profile_template_is_enforced_at_submission():
    request, args = submission()
    ref = f"{request.run_id}--{request.component_id}--expert-config"
    config = {"goal": {"evaluation": {"mode": "grounding"}}, "goal_preflight": SPEC}
    item = EmbeddedInput.from_bytes(ref, "application/json", json.dumps(config).encode())
    request = replace(request, schema_version=47, input_artifacts=(*request.input_artifacts, item),
                      immutable_context_refs=(*request.immutable_context_refs, ref))
    args["candidate"].update(evaluation_scope="GOAL", measurement=None, metrics=[],
                             execution_argv=["python", "/input/experiment.py"])
    with pytest.raises(AgentACIError, match="differs from the Profile-bound template"):
        validate_experiment_submission(request, args)
    args["workspace_files"][0] = WorkspaceArtifact.from_bytes("experiment.py", SOURCE).to_dict()
    validate_experiment_submission(request, args)
    args["entrypoint_smoke"] = {"args": ["--sample"], "outputs": ["data.json"]}
    with pytest.raises(AgentACIError, match="omit entrypoint_smoke"):
        validate_experiment_submission(request, args)


@pytest.mark.parametrize("mode", ["pass", "missing", "no-evidence", "fail", "timeout", "raw-fail"])
def test_goal_sequence_isolated_fail_closed_and_auditable(tmp_path, mode):
    request, args = submission()
    args["workspace_files"][0] = WorkspaceArtifact.from_bytes("experiment.py", SOURCE).to_dict()
    _, _, receipt = validate_experiment_submission(request, args)
    state = tmp_path / "state"
    (state / "artifacts").mkdir(parents=True)
    hidden = tmp_path / "held-out"
    hidden.mkdir()
    (hidden / "value").write_text("formal")
    workspace = ExecutionWorkspace(state / "artifacts", state_root=state)
    raw = workspace.plan(agent_request=request.to_dict(), agent_response=receipt, attempt_id="formal", attempt_ordinal=1,
        execution_id="execution", target_env={"HYPOLOOM_READ_ROOTS": '["/hypoloom-evaluation/1"]'},
        timeout_seconds=15, source_relative_path="experiment.py", execution_argv=["python", "/input/experiment.py", mode],
        read_only_bindings=[(hidden, "/hypoloom-evaluation/1")], allow_generic_argv=True)
    prepared = plan_evaluation(raw, local_measurement=None, local_files={}, goal_acceptance=SharedAcceptance.from_dict(GOAL),
        goal_files={"score.py": PARSER}, execution_id="execution", timeout_seconds=15, preflight=SPEC)
    workspace.materialize(raw, agent_request=request.to_dict(), agent_response=receipt)
    materialize_evaluation(prepared)
    outer = prepared.execution.process_request
    payload = {"measurement": None, "goal_evaluation": {"scope": "GOAL", "preflight": SPEC, "acceptance": GOAL}}
    audit_parser_process_requests(outer.to_dict(), payload)
    process = LocalRunner().run(outer)
    assert process.returncode == {"pass": 0, "raw-fail": 70}.get(mode, 74), (raw.log_dir / "stderr.log").read_text()
    obs = process.to_dict()
    expected = (mode == "pass", None, mode == "pass")
    assert evaluation_phase_success(obs, has_goal=True, has_local=False) == phase_success(obs, local=False, goal=True) == expected
    if mode == "pass":
        assert json.loads(read_report(raw.attempt_root / "goal-preflight"))["passed"]
        assert json.loads((raw.output_dir / "data.json").read_bytes()) == "formal"
        assert (prepared.goal_parser.output_dir / "result.json").is_file()
    elif process.returncode == 74:
        assert not (raw.output_dir / "data.json").exists()
        assert not (prepared.goal_parser.output_dir / "result.json").exists()
    collected = workspace.collect(prepared.execution, process, agent_request=request.to_dict(),
                                  agent_response=receipt, preserve_partial_outputs=True)
    published = collect_evaluation(prepared, collected, obs).staging_dir
    assert (published / "preflight/preflight.json").exists() == (process.returncode != 74)
    assert (published / "goal/result.json").exists() == (mode == "pass")
    if process.returncode == 74:
        assert "GOAL preflight failed" in (published / "preflight/stderr.log").read_text()
    doc = json.loads(outer.argv[3])
    doc["preflight_request"]["argv"] = doc["raw_request"]["argv"]
    changed = replace(outer, argv=(*outer.argv[:3], json.dumps(doc)))
    with pytest.raises(ValueError, match="preflight"):
        audit_parser_process_requests(changed.to_dict(), payload)
