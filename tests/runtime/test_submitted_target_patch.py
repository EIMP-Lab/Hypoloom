"""Research-v9 executes accepted target edits; predecessor contracts stay readable."""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import hypoloom.workflow_host as host_module
from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, AgentCommand, WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle, _oracle_uses_submitted_target
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_component_host_g3 import (
    _drive_to_completion, _git, _production_design_payload, _production_target,
    mount_fixture_root as mount_fixture_root,
)
from tests.runtime.test_profile_v4 import documents


class TargetEditAgent(FakeAgent):
    def __init__(self, target: Path):
        super().__init__()
        before = (target / "src/frozen_target/runner.py").read_text()
        after = before.replace('"max_score": 3', '"max_score": 4')
        assert before != after
        self.patch = "".join(difflib.unified_diff(before.splitlines(keepends=True),
            after.splitlines(keepends=True), fromfile="a/src/frozen_target/runner.py",
            tofile="b/src/frozen_target/runner.py")).encode()

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        files = {item.path: item.content_bytes() for item in response.proposed_workspace_artifacts}
        files["implementation.patch"] += self.patch
        return with_output_digest(replace(response, proposed_workspace_artifacts=tuple(
            WorkspaceArtifact.from_bytes(path, value) for path, value in files.items())))


class ImportedTargetAgent(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        source = ("import json, os, runpy\nfrom pathlib import Path\n"
            "value = runpy.run_path(os.environ['HYPOLOOM_TARGET_ROOT'] + '/score.py')['score']()\n"
            "Path(os.environ['HYPOLOOM_RESULT_PATH']).write_text(json.dumps("
            "{'artifacts': {}, 'trials': [{'seed': 0, 'metrics': {'max_score': value}, 'clean': True}], "
            f"'identity': {{'method_family_id': {str(request.method_family_id)!r}}}}}))\n").encode()
        from hypoloom.fake_agent import _new_file_patch
        patch = (_new_file_patch("experiment.py", source)
            + b"--- a/score.py\n+++ b/score.py\n@@ -1,2 +1,2 @@\n def score():\n-    return 3\n+    return 4\n")
        return with_output_digest(replace(response, proposed_workspace_artifacts=(
            WorkspaceArtifact.from_bytes("experiment.py", source),
            WorkspaceArtifact.from_bytes("implementation.patch", patch))))


class SelfPublishingTargetEditAgent(FakeAgent):
    """Execute and publish the same cumulative patch captured at submission."""

    def __init__(self, target: Path, *, publish_different_patch: bool = False):
        super().__init__()
        before = (target / "src/frozen_target/runner.py").read_text()
        after = """import argparse, hashlib, json
from pathlib import Path

MARKER = "TARGET_EXECUTED"
parser = argparse.ArgumentParser()
parser.add_argument("--dataset", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--seed", required=True, type=int)
parser.add_argument("--out", required=True)
parser.add_argument("--fixture", required=True)
args = parser.parse_args()
assert (args.dataset, args.model, args.seed) == ("FIXTURE", "candidate-v1", 7)
assert Path(args.fixture).read_text(encoding="utf-8") == "frozen fixture\\n"
result_path = Path(args.out)
implementation = Path("/input/implementation.patch").read_bytes()
published = implementation
if __PUBLISH_DIFFERENT_PATCH__:
    published = implementation.replace(
        b"+published = implementation\\n",
        b"+published = bytes(implementation)\\n")
    assert published != implementation
(result_path.parent / "implementation.patch").write_bytes(published)
result_path.write_text(json.dumps({
    "artifacts": {"implementation_patch": "implementation.patch"},
    "identity": {
        "method_family_id": "fake-family-v1",
        "implementation_sha256": hashlib.sha256(published).hexdigest(),
        "core_config_sha256": hashlib.sha256(b"frozen-config").hexdigest(),
    },
    "trials": [{"seed": 7, "metrics": {"max_score": 4}, "clean": True}],
}, sort_keys=True), encoding="utf-8")
print(MARKER, flush=True)
""".replace("__PUBLISH_DIFFERENT_PATCH__", repr(publish_different_patch))
        self.patch = "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/src/frozen_target/runner.py",
            tofile="b/src/frozen_target/runner.py",
        )).encode()

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        files = {
            item.path: item.content_bytes()
            for item in response.proposed_workspace_artifacts
        }
        files["implementation.patch"] += self.patch
        return with_output_digest(replace(
            response,
            proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(path, value)
                for path, value in files.items()
            ),
        ))


def _preset_v4_payload(root, target, run_id, *, read_roots=()):
    source = documents()
    source["goal"]["metric"] = {"name": "max_score", "direction": "maximize", "target": 4}
    source["goal"]["stop_conditions"] = {"max_rounds_per_expert": 1, "max_wall_clock_hours": 1}
    source["experts"]["defaults"].update(max_forks=1, max_hypotheses_per_round=1)
    source["methodology"] = {"schema_version": 4,
        "runtime": {"target_repo": str(target), "conda_env": sys.prefix},
        "budget": {"max_experiments": 1}, "graph_evolution": {"enabled": False},
        "interaction": {"leader": {"enabled": False}, "debate": {"enabled": False}}}
    source["notify"] = {"sinks": {}, "events": {}}
    source["safety"] = {"gpus": [], "allow_read_paths": list(read_roots), "experiment_timeout_minutes": 1}
    profile = root / "profile"
    profile.mkdir()
    for name, value in source.items():
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(value))
    admitted = admit_profile(profile, run_id=run_id, allow_gpu=False)
    assert admitted.admitted, admitted.to_dict()
    return admitted.create_run_payload


@pytest.mark.parametrize(
    ("publish_different_patch", "expected"),
    [
        (False, ("SUCCESS", "4")),
        (True, ("INTEGRITY_REJECTED", None)),
    ],
)
def test_submitted_patch_artifact_is_bound_to_executed_patch(
    tmp_path, mount_fixture_root, publish_different_patch, expected,
):
    target, commit = _production_target(mount_fixture_root)
    run_id = "submitted-patch-artifact-clean-base"
    payload = _production_design_payload(run_id, target, commit)
    contract = payload["research_contract"]
    component = contract["components"][0]
    component["result"]["target"] = "4"
    component["required_artifacts"] = ["implementation_patch"]
    contract["goal"]["summary_metric"]["target"] = "4"
    requirements = contract["protocols"]["frozen-single"]["requirements"]
    requirements["required_artifacts"] = ["implementation_patch"]
    requirements["artifact_validators"] = [{
        "artifact": "implementation_patch",
        "type": "unified_diff",
        "apply_check": True,
    }]

    state = tmp_path / "state"
    host = WorkflowHost(state)
    host.agent = SelfPublishingTargetEditAgent(
        target, publish_different_patch=publish_different_patch)
    try:
        host.initialize()
        accepted = host.handle(ControlRequest(
            schema_version=1,
            request_id="create-" + run_id,
            expected_revision=0,
            command="CREATE_RUN",
            payload=payload,
        ))
        assert accepted.status == "accepted", accepted
        _drive_to_completion(host, run_id)
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert [
            (item["payload"]["outcome"], item["payload"]["metric_value"])
            for item in outcomes
        ] == [expected]
    finally:
        host.close()

    assert _git(target, "rev-parse", "HEAD") == commit
    assert _git(target, "status", "--porcelain=v1") == ""
    report = Oracle(state / "state.sqlite", state / "artifacts").audit(run_id)
    assert report.ok, report.issues


@pytest.mark.parametrize("restart_after_submit", [False, True])
def test_actual_six_yaml_v4_preset_executes_its_imported_target_edit(tmp_path, mount_fixture_root, restart_after_submit):
    target = mount_fixture_root / "target"
    target.mkdir()
    (target / "score.py").write_text("def score():\n    return 3\n")
    _git(target, "init", "-q")
    _git(target, "add", "score.py")
    _git(target, "-c", "user.name=Hypoloom Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "base metric")
    commit = _git(target, "rev-parse", "HEAD")
    run_id = "v4-imported-target"
    payload = _preset_v4_payload(tmp_path, target, run_id)
    assert not payload["research_contract"]["components"][0]["result"].get("deferred")
    state = tmp_path / "state"
    host = WorkflowHost(state)
    host.agent = ImportedTargetAgent()
    try:
        host.initialize()
        accepted = host.handle(ControlRequest(schema_version=1, request_id="create-" + run_id,
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert accepted.status == "accepted", accepted
        if restart_after_submit:
            class SubmittedCrash(BaseException):
                pass

            def crash(point):
                if point == "after_submit_commit_before_reply":
                    raise SubmittedCrash()

            host._crash = crash
            with pytest.raises(SubmittedCrash):
                _drive_to_completion(host, run_id)
            submissions = host.store.inspect("SELECT command_id,request_json FROM workflow_commands WHERE run_id=? AND command_type='CREATE_EXPERIMENT'", (run_id,))
            assert len(submissions) == 1
            accepted_source = (submissions[0]["command_id"], submissions[0]["request_json"])
            host.close()
            host = WorkflowHost(state)
            host.agent = ImportedTargetAgent()
            host.initialize()
        _drive_to_completion(host, run_id)
        if restart_after_submit:
            assert [(row["command_id"], row["request_json"]) for row in host.store.inspect("SELECT command_id,request_json FROM workflow_commands WHERE run_id=? AND command_type='CREATE_EXPERIMENT'", (run_id,))] == [accepted_source]
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert [(row["payload"]["outcome"], row["payload"]["metric_value"]) for row in outcomes] == [("SUCCESS", "4")]
        assert host.store.get_run(run_id)["research_contract_version"] == "hypoloom-research-v10"
        assert [row["terminal_reason"] for row in host.store.list_attempts() if row["kind"] == "PROCESS"] == ["SUCCEEDED"]
    finally:
        host.close()
    assert (target / "score.py").read_text() == "def score():\n    return 3\n"
    assert _git(target, "rev-parse", "HEAD") == commit
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    report = Oracle(state / "state.sqlite", state / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.open_attempt_count == 0 and report.owned_process_residue == ()
    _assert_missing_submitted_patch_detected(state, run_id)


def _assert_missing_submitted_patch_detected(state: Path, run_id: str):
    """Change only in-memory raw rows; leave the real CPU evidence untouched."""
    from hypoloom.oracle import _audit_execution_integrity, _raw_submit_source, _row_identifier
    with sqlite3.connect(f"file:{state / 'state.sqlite'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        facts = [dict(row) for row in db.execute("SELECT rowid AS raw_order,* FROM research_facts WHERE run_id=? ORDER BY rowid", (run_id,))]
        commands = [dict(row) for row in db.execute("SELECT * FROM workflow_commands WHERE run_id=?", (run_id,))]
        transitions = [dict(row) for row in db.execute("SELECT * FROM workflow_transitions WHERE run_id=?", (run_id,))]
        attempts = [dict(row) for row in db.execute("SELECT * FROM execution_attempts WHERE run_id=?", (run_id,))]
        artifacts = [dict(row) for row in db.execute("SELECT * FROM execution_artifacts WHERE run_id=?", (run_id,))]
    payloads = {row["fact_id"]: json.loads(row["payload_json"]) for row in facts}
    transition_payloads = {_row_identifier(row): json.loads(row["payload_json"]) for row in transitions}
    observations = {row["attempt_id"]: json.loads(row["observation_json"]) for row in attempts}
    command = next(row for row in commands if row["command_type"] == "CREATE_EXPERIMENT")
    source = json.loads(command["request_json"])
    snapshot = source["source_snapshot"]
    old_digest = snapshot["output_digest"]
    for arguments in (source["arguments"], snapshot["arguments"]):
        arguments["workspace_files"] = [item for item in arguments["workspace_files"] if item["path"] != "implementation.patch"]
    encoded = json.dumps({key: value for key, value in snapshot.items() if key != "output_digest"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    new_digest = hashlib.sha256(encoded).hexdigest()
    snapshot["output_digest"] = new_digest
    command["request_json"] = json.dumps(source)

    def rebound(value):
        if isinstance(value, dict): return {key: rebound(item) for key, item in value.items()}
        if isinstance(value, list): return [rebound(item) for item in value]
        return new_digest if value == old_digest else value

    payloads, transition_payloads, observations = map(rebound, (payloads, transition_payloads, observations))
    # The exact raw submit remains resolvable: a stale digest is not the tested rejection.
    assert _raw_submit_source(command, attempts, observations) is not None
    issues = []
    _audit_execution_integrity(state / "artifacts", facts, payloads, transitions,
        transition_payloads, commands, attempts, observations, artifacts, issues,
        research_contract_version="hypoloom-research-v9")
    assert "source_agent_workspace_invalid" in {issue.code for issue in issues}, issues
    predecessor_issues = []
    _audit_execution_integrity(state / "artifacts", facts, payloads, transitions,
        transition_payloads, commands, attempts, observations, artifacts, predecessor_issues,
        research_contract_version="hypoloom-research-v8")
    assert "source_agent_workspace_invalid" not in {issue.code for issue in predecessor_issues}


@pytest.mark.parametrize("version,aci,kind,overlay,target,declared,expected", [
    (9, 14, "metric", None, True, False, b"own"),
    (10, 14, "metric", None, True, False, b"own"),
    (8, 14, "metric", None, True, False, b"ancestor"),
    (9, 13, "metric", None, True, False, b"ancestor"),
    (9, 14, "metric", "latest", True, False, b"ancestor"),
    (9, 14, "artifact", None, True, False, b"ancestor"),
    (9, 14, "metric", None, False, False, b"ancestor"),
    (8, 13, "metric", None, True, True, b"own"),
])
def test_target_source_and_independent_oracle_keep_version_and_upstream_boundaries(
    version, aci, kind, overlay, target, declared, expected,
):
    result = {"kind": kind, **({"target_overlay_policy": overlay} if overlay else {}),
              **({"deferred": True} if declared else {})}
    document = {"components": [{"id": "metric", "result": result}],
                "runtime": {"target_repo": "/target"} if target else {}}
    request = SimpleNamespace(run_id="run", component_id="metric", schema_version=aci)
    candidate = SimpleNamespace(method_family_id="family", measurement=object() if declared else None, evaluation_scope=None)
    host = SimpleNamespace(_research_contract=lambda _: SimpleNamespace(document=document),
        store=SimpleNamespace(get_run=lambda _: {"research_contract_version": f"hypoloom-research-v{version}"}),
        _run_config=lambda _: {"execution": {"target": {} if target else None}},
        _implementation_patch=lambda *args: b"ancestor")
    assert WorkflowHost._submitted_execution_patch(host, request, candidate, {"implementation.patch": b"own"}) == expected
    own = _oracle_uses_submitted_target(document, "metric", {"schema_version": aci}, f"hypoloom-research-v{version}")
    assert own is (expected == b"own" and not declared)


@pytest.mark.parametrize("research_version,expected", [(8, "3"), (9, "4")])
def test_submitted_target_patch_changes_actual_cpu_metric_only_for_successor(
    tmp_path, mount_fixture_root, monkeypatch, research_version, expected,
):
    installed = host_module.installed_run_version
    monkeypatch.setattr(host_module, "installed_run_version", lambda *args, **kwargs:
        replace(installed(*args, **kwargs), research_contract_version=f"hypoloom-research-v{research_version}"))
    target, commit = _production_target(mount_fixture_root)
    before = {name: (target / name).read_bytes() for name in _git(target, "ls-files").splitlines()}
    run_id = f"submitted-target-v{research_version}"
    payload = _production_design_payload(run_id, target, commit)
    contract = payload["research_contract"]
    contract["components"][0]["result"]["target"] = "4"
    contract["goal"]["summary_metric"]["target"] = "4"
    state = tmp_path / "state"
    host = WorkflowHost(state)
    host.agent = TargetEditAgent(target)
    try:
        host.initialize()
        response = host.handle(ControlRequest(schema_version=1, request_id=f"create-{run_id}",
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert response.status == "accepted", response
        _drive_to_completion(host, run_id)
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        actual = [(row["payload"]["outcome"], row["payload"]["metric_value"]) for row in outcomes]
        assert actual == [("SUCCESS" if research_version >= 9 else "CLEAN_REFUTATION", expected)]
        processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
        assert [row["terminal_reason"] for row in processes] == ["SUCCEEDED"]
        argv = json.loads(processes[0]["observation_json"])["process_request"]["argv"]
        assert Path(argv[0]).name == "bwrap"
        assert argv[argv.index("--") + 2:][:2] == ["-m", "frozen_target.runner"]
        assert host.store.get_run(run_id)["agent_aci_version"] == str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION)
    finally:
        host.close()
    assert {name: (target / name).read_bytes() for name in before} == before
    assert _git(target, "rev-parse", "HEAD") == commit
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    report = Oracle(state / "state.sqlite", state / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.open_attempt_count == 0 and report.owned_process_residue == ()
