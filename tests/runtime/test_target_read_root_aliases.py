"""Explicit ignored data stays RO without obscuring submitted target code."""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import hypoloom.execution_workspace as workspace_module
import hypoloom.workflow_host as host_module
from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.execution_workspace import bind_clean_git_target
from hypoloom.fake_agent import _new_file_patch
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_component_host_g3 import _git, mount_fixture_root as mount_fixture_root
from tests.runtime.test_deferred_measurement_host import DeclaredMeasurementAgent, deferred_payload
from tests.runtime.test_execution_workspace import candidate_pair, make_workspace
from tests.runtime.test_host_experiment_tools import _until
from tests.runtime.test_shared_acceptance_host import SharedAcceptanceAgent, shared_payload
from tests.runtime.test_trial_driver_host_g3 import _drive as drive_to_completion


def project(roots, target="/project", *, generic=True, plan=None):
    return workspace_module.execution_read_root_projection(
        {"read_roots": roots, "target": None if target is None else {"checkout": target}},
        {"production": False} if plan is None else plan, generic=generic)


@pytest.mark.parametrize("roots,expected,mapping", [
    (["/data", "/project"], ["/data", "/hypoloom-read-roots/2"],
        {"/project": "/hypoloom-read-roots/2"}),
    (["/project/cache", "/project", "/project/splits"], ["/hypoloom-read-roots/2"],
        {"/project/cache": "/hypoloom-read-roots/2/cache", "/project": "/hypoloom-read-roots/2",
         "/project/splits": "/hypoloom-read-roots/2/splits"}),
    (["/project/cache", "/project/splits"], ["/hypoloom-read-roots/1", "/hypoloom-read-roots/2"],
        {"/project/cache": "/hypoloom-read-roots/1", "/project/splits": "/hypoloom-read-roots/2"}),
    (["/project", "/"], ["/hypoloom-read-roots/2"],
        {"/project": "/hypoloom-read-roots/2/project", "/": "/hypoloom-read-roots/2"}),
])
def test_projection_uses_original_ordinals_and_only_outermost_explicit_roots(roots, expected, mapping):
    bindings, environment = project(roots)
    assert [destination for _, destination in bindings] == expected
    assert json.loads(environment["HYPOLOOM_READ_ROOTS"]) == expected
    assert environment["HYPOLOOM_READ_ROOT_MAP"] == json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    assert all(str(source) in roots for source, _ in bindings)


@pytest.mark.parametrize("generic,plan,target,roots", [
    (False, {}, "/project", ["/project"]),
    (True, {"production": True}, "/project", ["/project"]),
    (True, {"data_bindings": [{"source": "/project/cache", "sandbox": "/data"}]}, "/project", ["/project"]),
    (True, {}, None, ["/data"]), (True, {}, "/project", ["/data"]),
])
def test_non_generic_driver_and_disjoint_cases_keep_old_environment_bytes(generic, plan, target, roots):
    bindings, environment = project(roots, target, generic=generic, plan=plan)
    assert bindings == tuple((Path(root), root) for root in roots)
    assert environment == {"HYPOLOOM_READ_ROOTS": json.dumps(roots, separators=(",", ":"))}


def test_aliased_parent_also_covers_a_disjoint_explicit_child():
    bindings, environment = project(["/project/data", "/project"], "/project/repository")
    assert bindings == ((Path("/project"), "/hypoloom-read-roots/2"),)
    assert json.loads(environment["HYPOLOOM_READ_ROOT_MAP"]) == {
        "/project": "/hypoloom-read-roots/2", "/project/data": "/hypoloom-read-roots/2/data"}


def test_projection_does_not_relax_nested_mount_validator(tmp_path, mount_fixture_root):
    workspace = make_workspace(tmp_path)
    root = mount_fixture_root / "original"
    request, response = candidate_pair()
    with pytest.raises(ValueError, match="targets must not overlap"):
        workspace.plan(agent_request=request, agent_response=response, attempt_id="nested",
            attempt_ordinal=1, execution_id="execution", target_env={}, timeout_seconds=5,
            read_only_bindings=((root, "/code"), (mount_fixture_root / "other", "/code/cache")))


@pytest.mark.parametrize("goal", [False, True])
def test_formal_host_mount_plan_preserves_target_and_authorized_data(tmp_path, mount_fixture_root, monkeypatch, goal):
    workspace = make_workspace(tmp_path)
    target = mount_fixture_root / "original"
    target.mkdir()
    execution = {"target": {"checkout": str(target), "commit": "a" * 40},
        "read_roots": [str(target), str(target / "cache")]}
    monkeypatch.setattr(host_module, "_component_plan", lambda *args: {"production": False})
    host = SimpleNamespace(_run_config=lambda _: {"execution": execution},
        _implementation_patch=lambda *args: None,
        _research_contract=lambda _: SimpleNamespace(document={}, component=lambda _: object()))
    payload = {"component_id": "sample", "method_family_id": "candidate",
        "measurement": None if goal else {},
        "goal_evaluation": {"scope": "GOAL"} if goal else None,
        "execution_contract": {"resource_grant": {"kind": "CPU"}}}
    mounts, _, _ = WorkflowHost._execution_mounts(host, "sample", {"payload": payload}, workspace, "process")
    request, response = candidate_pair()
    prepared = workspace.plan(agent_request=request, agent_response=response,
        attempt_id="process", attempt_ordinal=1, execution_id="execution",
        target_env={}, timeout_seconds=5, read_only_bindings=mounts)
    assert prepared.read_only_bindings == (
        (workspace.paths("process").attempt_root / "target", str(target)),
        (target, "/hypoloom-read-roots/1"))


class DataReadAgent(SharedAcceptanceAgent):
    def __init__(self, *, common):
        super().__init__(goal_only=common)
        self.common = common

    def invoke(self, request):
        for item in request.input_artifacts:
            if (request.command in {AgentCommand.GROUNDING, AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
                    AgentCommand.EXPERIMENT_EXECUTION}
                    and item.ref.endswith(("--grounding-config", "--expert-config"))):
                config = json.loads(item.content_bytes())["execution"]
                target = config["target"]["checkout"]
                assert config["read_roots"][0] == target
                assert config["read_root_map"][target] == "/hypoloom-read-roots/1"
                assert config["sandbox_read_roots"] == ["/hypoloom-read-roots/1"]
        response = (super().invoke(request) if self.common
            else DeclaredMeasurementAgent.invoke(self, request))
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        source = b'''import errno, json, os, runpy
from pathlib import Path
target = os.environ['HYPOLOOM_TARGET_ROOT']
mapping = json.loads(os.environ.get('HYPOLOOM_READ_ROOT_MAP', '{}'))
data = Path(mapping.get(target, target))
cache = json.loads((data / 'cache/sample.json').read_text())
split = json.loads((data / 'splits/sample.json').read_text())
for path in (data / 'cache/sample.json', data / 'splits/sample.json'):
    assert os.statvfs(path).f_flag & os.ST_RDONLY
    try:
        path.write_text('must-not-write')
    except OSError as error:
        assert error.errno == errno.EROFS
    else:
        raise AssertionError('data mount was writable')
assert runpy.run_path(target + '/score.py')['score']() == 4
assert runpy.run_path(str(data / 'score.py'))['score']() == 3
assert cache == {'baseline': 10, 'values': [8, 9, 100]}
assert split == {'indices': [0, 1, 2]}
Path('timings.csv').write_text(''.join(f"{cache['baseline']},{cache['values'][i]}\\n" for i in split['indices']))
Path('bundle.txt').write_text('bounded-package\\n')
Path('validity.txt').write_text('valid\\n')
print(json.dumps({'read_root_map': mapping, 'patched_score': 4, 'readonly': True}), flush=True)
'''
        candidate = dict(response.typed_candidate)
        candidate["execution_argv"] = ["python", "/input/experiment.py"]
        files = {item.path: item.content_bytes() for item in response.proposed_workspace_artifacts}
        files.pop("experiment.sh", None)
        files["experiment.py"] = source
        files["implementation.patch"] = _new_file_patch("experiment.py", source) + (
            b"--- a/score.py\n+++ b/score.py\n@@ -1,2 +1,2 @@\n def score():\n-    return 3\n+    return 4\n")
        return with_output_digest(replace(response, typed_candidate=candidate,
            proposed_workspace_artifacts=tuple(WorkspaceArtifact.from_bytes(path, value)
                for path, value in sorted(files.items()))))


@pytest.mark.parametrize("common,restart", [(False, False), (True, True)])
def test_actual_host_cpu_reads_ignored_data_not_original_code(tmp_path, common, restart):
    with tempfile.TemporaryDirectory(prefix="hypoloom-root-alias-", dir="/var/tmp") as directory:
        mount = Path(directory)
        payload = (shared_payload if common else deferred_payload)(tmp_path, mount)
        target = mount / "target"
        (target / "score.py").write_text("def score():\n    return 3\n")
        (target / ".gitignore").write_text("/cache/\n/splits/\n")
        _git(target, "add", "score.py", ".gitignore")
        _git(target, "-c", "user.name=Hypoloom Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "tracked code")
        for name, value in (("cache", {"baseline": 10, "values": [8, 9, 100]}), ("splits", {"indices": [0, 1, 2]})):
            (target / name).mkdir()
            (target / name / "sample.json").write_text(json.dumps(value))
        binding = bind_clean_git_target(target)
        roots = [str(target), str(target / "cache"), str(target / "splits")]
        payload["execution"].update(target={"checkout": str(target), "commit": binding.commit},
            runtime_prefix=sys.prefix, read_roots=roots)
        payload["research_contract"]["runtime"]["conda_env"] = sys.prefix
        payload["research_contract"]["safety"]["read_roots"] = roots
        before = {str(path.relative_to(target)): path.read_bytes()
            for path in (target / "score.py", target / "cache/sample.json", target / "splits/sample.json")}
        state = tmp_path / "state"
        host = WorkflowHost(state, local_agent=DataReadAgent(common=common))
        try:
            host.initialize()
            assert host.handle(ControlRequest(1, "create-alias", 0, "CREATE_RUN", payload)).status == "accepted"
            if restart:
                _until(host, lambda: bool(host.store.list_facts("c5-host", "ExperimentPreregistered")))
                prior = host.store.list_facts("c5-host", "ExperimentPreregistered")[0]["payload"]
                host.close()
                host = WorkflowHost(state, local_agent=DataReadAgent(common=common))
                host.initialize()
            # Full CPU closure includes analysis and reporting under parallel G1 load.
            drive_to_completion(host, "c5-host", timeout=60)
            specs = host.store.list_facts("c5-host", "ExperimentPreregistered")
            assert len(specs) == 1
            if restart:
                assert specs[0]["payload"] == prior
            processes = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
            assert [row["terminal_reason"] for row in processes] == ["SUCCEEDED"]
            environment = dict(specs[0]["payload"]["execution_contract"]["environment"])
            assert json.loads(environment["HYPOLOOM_READ_ROOT_MAP"])[str(target)] == "/hypoloom-read-roots/1"
            outcome = host.store.list_facts("c5-host", "CanonicalOutcomeAccepted")[0]["payload"]
            assert outcome["outcome"] == "SUCCESS"
            if common:
                assert outcome["goal_evaluation"]["checks"] == {"package_valid": True, "evidence_valid": True}
            else:
                assert outcome["metric_value"] == "-0.1"
            report = Oracle(host.store.database_path, host.publisher.root).audit("c5-host")
            assert report.ok, report.to_dict()
            assert report.open_attempt_count == 0 and report.owned_process_residue == ()
            (tmp_path / "alias-evidence.json").write_text(json.dumps({"spec": specs[0]["payload"],
                "processes": processes, "outcome": outcome, "oracle": report.to_dict()}, default=str, indent=2))
        finally:
            host.close()
        assert {name: (target / name).read_bytes() for name in before} == before
        assert bind_clean_git_target(target) == binding
