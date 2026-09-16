"""Real native Bash Git diagnostics, not a WorkflowHost qualification claim."""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentBudget, EmbeddedInput
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import default_request

from test_agent_sessions import _chain_request, _manager
from test_agent_aci import _pincer_request
from test_claude_chain_cli import _Chains, _invoke
from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results
from test_git_authoring_workspace import git, make_target


def _seed_native(tmp_path, sessions, target, executable):
    scenario = _Chains()
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        seed = sessions.prepare(replace(_pincer_request(ResearchNodeSubkind.SYNTHESIS),
            attempt_id="git-native-seed", budget=AgentBudget(max_turns=8, max_tokens=20000,
                max_output_bytes=131072, wall_seconds=40, max_cost="1")), target)
        _invoke(tmp_path, executable, seed, scenario, f"http://127.0.0.1:{provider.server_port}",
            "host-only-git-seed-key-" + secrets.token_hex(12))
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


class _GitDiagnostics:
    stage, chain = "diagnostics", None

    def __init__(self, workspace, sibling, checkout: Path, writable: bool):
        self.workspace, self.sibling, self.checkout, self.writable = workspace, sibling, checkout, writable
        self.before_head = git(workspace.worktree_root, "rev-parse", "HEAD").decode().strip()
        self.step = 0
        self.requests, self.errors, self.evidence = [], [], {}

    def record(self, document):
        self.requests.append(document)
        self.step += 1
        try:
            previous = {2: "read_git", 3: "write_candidate", 4: "check_boundary"}.get(self.step)
            if previous:
                result = _tool_results(document)[previous]
                assert not result.get("is_error"), result
                self.evidence[previous] = _result_text(result)
                if previous == "read_git":
                    assert "GIT_HISTORY_OK" in self.evidence[previous]
                elif previous == "check_boundary":
                    assert "GIT_READONLY_AND_PRIVATE" in self.evidence[previous]
        except (AssertionError, KeyError) as exc:
            self.errors.append(str(exc))
        return len(self.requests)

    def tool_calls(self, _number):
        if self.errors:
            return ()
        if self.step == 1:
            script = """python3 - <<'PY'
import os, subprocess
assert os.environ.get('GIT_DIR') == '/git/worktrees/worktree'
assert os.environ.get('GIT_WORK_TREE') == '/workspace'
history = subprocess.check_output(['git', 'log', '--format=%s'], text=True)
assert 'base' in history
assert 'sibling-only-history' not in history
assert subprocess.check_output(['git', 'status', '--porcelain']) == b''
assert subprocess.check_output(['git', 'diff']) == b''
print('GIT_HISTORY_OK', history)
PY"""
            return (("read_git", "Bash", {"command": script, "timeout": 10000}),)
        if self.step == 2:
            script = ("python3 - <<'PY'\nimport errno\nfrom pathlib import Path\n"
                "path = Path('/workspace/src/value.txt')\n"
                + ("path.write_text('local-agent-edit\\n')\n" if self.writable else
                   "try:\n    path.write_text('forbidden')\nexcept OSError as error:\n    assert error.errno == errno.EROFS\nelse:\n    raise AssertionError('read-only node wrote code')\n")
                + "print('CANDIDATE_WRITE_CHECKED')\nPY")
            return (("write_candidate", "Bash", {"command": script, "timeout": 10000}),)
        if self.step == 3:
            script = """python3 - <<'PY'
import errno, os, subprocess
from pathlib import Path
def readonly(path):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError as error:
        assert error.errno == errno.EROFS, (path, error)
    else:
        os.close(fd)
        raise AssertionError('writable protected path: ' + path)
for path in ('/git/config', '/git/worktrees/worktree/index', '/workspace/.git',
             '/workspace/data/locked.txt', '/workspace/protected.txt'):
    readonly(path)
for argv in (['add', 'src/value.txt'], ['config', '--local', 'user.name', 'Agent'],
             ['-c', 'user.name=Agent', '-c', 'user.email=agent@localhost', 'commit', '--allow-empty', '-m', 'forbidden']):
    result = subprocess.run(['git', *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert result.returncode != 0, argv
    assert 'Read-only file system' in result.stderr, result.stderr
status = subprocess.check_output(['git', 'status', '--porcelain'], text=True)
diff = subprocess.check_output(['git', 'diff'], text=True)
"""
            script += ("assert ' M src/value.txt' in status and '+local-agent-edit' in diff\n"
                if self.writable else "assert not status and not diff\n")
            script += f"assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == {self.before_head!r}\n"
            for path in (self.checkout, self.workspace.state_root, self.sibling.worktree_root,
                         self.sibling.git_workspace.repository_root):
                script += f"assert not Path({str(path)!r}).exists()\n"
            script += ("assert not Path('/git/objects/info/alternates').exists()\n"
                "assert 'sibling-only-history' not in subprocess.check_output(['git', 'log', '--all', '--format=%s'], text=True)\n"
                "print('GIT_READONLY_AND_PRIVATE', status, diff)\nPY")
            return (("check_boundary", "Bash", {"command": script, "timeout": 10000}),)
        return (("finish", "StructuredOutput", {"stage": self.stage, "ok": True}),)


@pytest.mark.parametrize("writable", (False, True), ids=("grounding", "coding"))
def test_native_git_history_status_diff_are_chain_private_and_host_write_only(tmp_path: Path, writable: bool):
    executable = _installed_claude()
    checkout, target = make_target(tmp_path)
    (checkout / "data").mkdir()
    (checkout / "data/locked.txt").write_text("admitted protected data\n")
    (checkout / "protected.txt").write_text("admitted protected file\n")
    git(checkout, "add", ".")
    git(checkout, "-c", "user.name=Host", "-c", "user.email=host@localhost", "commit", "-qm", "protected inputs")
    target = replace(target, commit=git(checkout, "rev-parse", "HEAD").decode().strip())
    sessions = _manager(tmp_path)
    _seed_native(tmp_path, sessions, target, executable)
    sibling = sessions.prepare(_chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
        "sibling-draft", "sibling"), target)
    (sibling.worktree_root / "sibling-secret.txt").write_text(secrets.token_hex(16))
    git(sibling.worktree_root, "add", ".")
    git(sibling.worktree_root, "-c", "user.name=Host", "-c", "user.email=host@localhost", "commit", "-qm", "sibling-only-history")
    request = (_chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "git-draft", "owner")
        if writable else replace(default_request(AgentCommand.GROUNDING), attempt_id="git-grounding"))
    request = replace(request, budget=AgentBudget(max_turns=8, max_tokens=20000,
        max_output_bytes=131072, wall_seconds=40, max_cost="1"), input_artifacts=(
        EmbeddedInput.from_bytes("node--expert-config", "application/json", json.dumps({
            "node_runtime": {"safety": {"filesystem": {"protected_paths": [
                "data/", ".git/", "/workspace/protected.txt", str(checkout)]}}}}).encode()),))
    workspace = sessions.prepare(request, target)
    scenario = _GitDiagnostics(workspace, sibling, checkout, writable)
    before_head = git(workspace.worktree_root, "rev-parse", "HEAD")
    before_config = (workspace.git_workspace.repository_root / "config").read_bytes()
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        metadata = _invoke(tmp_path, executable, workspace, scenario,
            f"http://127.0.0.1:{provider.server_port}", "host-only-git-key-" + secrets.token_hex(12))
        assert set(scenario.evidence) == {"read_git", "write_candidate", "check_boundary"}
        assert (workspace.git_workspace.repository_root / "config").read_bytes() == before_config
        # Host node closure commits even read-only nodes; the actual Bash check
        # above proves the Agent did not mutate HEAD before that boundary.
        assert git(workspace.worktree_root, "rev-parse", "HEAD") != before_head
        assert metadata["transcript_complete"] is True
        assert (checkout / "src/value.txt").read_text() == "base\n"
        assert (sibling.worktree_root / "src/value.txt").read_text() == "base\n"
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        sibling.cleanup()
