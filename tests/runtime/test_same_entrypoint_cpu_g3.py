"""Real Process/bwrap uses the authored target, not an adjacent scratch test."""
from __future__ import annotations

import difflib
from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentCommand, WorkspaceArtifact, with_output_digest
from hypoloom.control_protocol import ControlRequest
from hypoloom.fake_agent import FakeAgent, _new_file_patch
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_component_host_g3 import (
    _drive_to_completion, _git, mount_fixture_root as mount_fixture_root,
)
from tests.runtime.test_submitted_target_patch import _preset_v4_payload


class SameEntryAgent(FakeAgent):
    def __init__(self, mode):
        super().__init__()
        self.scenario = mode

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        root = ("Path(__file__).parent / 'src'" if self.scenario == "input-relative"
                else "Path(os.environ['HYPOLOOM_TARGET_ROOT']) / 'src'")
        source = f'''import json, os, subprocess, sys
from pathlib import Path
assert Path.cwd() == Path('/output')
assert Path(__file__).parent == Path('/input')
env = dict(os.environ, PYTHONPATH=str({root}))
subprocess.run([sys.executable, '-m', 'topic.train', '--model', 'candidate',
    '--data', json.loads(os.environ['HYPOLOOM_READ_ROOTS'])[0] + '/value.txt',
    '--checkpoint', 'checkpoint.json'], env=env, check=True)
checkpoint = Path('checkpoint.json')
value = json.loads(checkpoint.read_text())['weight']
assert value == 4
checkpoint.unlink()
Path(os.environ['HYPOLOOM_RESULT_PATH']).write_text(json.dumps({{
    'artifacts': {{}}, 'trials': [{{'seed': 0, 'metrics': {{'max_score': value}}, 'clean': True}}],
    'identity': {{'method_family_id': {str(request.method_family_id)!r}}}}}))
print('FORMAL_IMPORT_REGISTER_TRAIN_CHECKPOINT_RELOAD_PASS', flush=True)
'''.encode()
        before = "REGISTRY = {'baseline': 0}\n"
        after = "REGISTRY = {'baseline': 0, 'candidate': 1}\n"
        edit = "".join(difflib.unified_diff(before.splitlines(keepends=True),
            after.splitlines(keepends=True), fromfile="a/src/topic/models.py",
            tofile="b/src/topic/models.py")).encode()
        patch = _new_file_patch("experiment.py", source)
        # Reproduce a manually saved patch rather than edits to target source.
        patch += (_new_file_patch("candidate.patch", edit)
                  if self.scenario == "nested-patch" else edit)
        return with_output_digest(replace(response, proposed_workspace_artifacts=(
            WorkspaceArtifact.from_bytes("experiment.py", source),
            WorkspaceArtifact.from_bytes("implementation.patch", patch))))


@pytest.mark.parametrize("mode", ["input-relative", "nested-patch", "formal-entry"])
def test_formal_cpu_entry_imports_registered_patch_trains_and_reloads_checkpoint(
    tmp_path, mount_fixture_root, mode,
):
    target = mount_fixture_root / "target"
    package = target / "src/topic"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "models.py").write_text("REGISTRY = {'baseline': 0}\n")
    (package / "train.py").write_text('''import argparse, json
from pathlib import Path
from .models import REGISTRY
p = argparse.ArgumentParser()
p.add_argument('--model'); p.add_argument('--data'); p.add_argument('--checkpoint')
a = p.parse_args()
weight = REGISTRY[a.model]
expected = float(Path(a.data).read_text())
# A deterministic bounded optimizer step; no substitute import or checkpoint writer.
weight -= 0.5 * (2 * (weight - expected))
Path(a.checkpoint).write_text(json.dumps({'weight': weight}))
assert json.loads(Path(a.checkpoint).read_text())['weight'] == expected
''')
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "-c", "user.name=Hypoloom Test", "-c", "user.email=test@example.invalid",
         "commit", "-qm", "temporary src-layout topic")
    original = {name: (target / name).read_bytes() for name in _git(target, "ls-files").splitlines()}
    data = mount_fixture_root / "data"
    data.mkdir()
    (data / "value.txt").write_text("4\n")
    run_id = "same-entry-" + mode
    payload = _preset_v4_payload(tmp_path, target, run_id, read_roots=[str(data)])
    state = tmp_path / "state"
    host = WorkflowHost(state)
    host.agent = SameEntryAgent(mode)
    try:
        host.initialize()
        result = host.handle(ControlRequest(1, "create-" + run_id, 0, "CREATE_RUN", payload))
        assert result.status == "accepted", result
        _drive_to_completion(host, run_id)
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert len(outcomes) == 1
        assert outcomes[0]["payload"]["outcome"] == (
            "SUCCESS" if mode == "formal-entry" else "EXECUTION_FAILURE")
        if mode == "formal-entry":
            assert outcomes[0]["payload"]["metric_value"] == "4"
        assert len([row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]) == 1
    finally:
        host.close()
    assert {name: (target / name).read_bytes() for name in original} == original
    assert _git(target, "status", "--porcelain=v1") == ""
    logs = "\n".join(path.read_text() for path in (state / "artifacts/result-staging").glob("*/logs/*.log"))
    assert {"input-relative": "No module named 'topic'", "nested-patch": "KeyError: 'candidate'",
            "formal-entry": "FORMAL_IMPORT_REGISTER_TRAIN_CHECKPOINT_RELOAD_PASS"}[mode] in logs
    report = Oracle(state / "state.sqlite", state / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.open_attempt_count == 0 and report.owned_process_residue == ()
