"""Actionable capture diagnostics without changing source/session authority."""

from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import _prompt
from hypoloom.domain import AgentCommand
from hypoloom.expert_memory import ExpertMemory, MemoryError
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_argv
from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
from test_claude_measurement_capture import _candidate, _workspace
from test_expert_memory import _facts, _report
from test_full_cc_runtime import _argv, _workspace as _runtime_workspace


@pytest.mark.parametrize("shape", ["directory", "missing", "symlink", "parent_symlink", "unreadable"])
def test_measurement_source_errors_name_only_sandbox_path(tmp_path, monkeypatch, shape):
    workspace = _workspace(tmp_path)
    candidate = _candidate()
    relative = "configs/value.json" if shape == "parent_symlink" else "configs"
    candidate["execution_argv"] += ["--config-root", "/input/" + relative]
    path = workspace.worktree_root / "configs"
    if shape == "directory":
        path.mkdir()
    elif shape in {"symlink", "parent_symlink"}:
        path.symlink_to(tmp_path / "private-host-source")
    elif shape == "unreadable":
        path.write_text("{}")
        def unreadable(*_args):
            raise PermissionError(13, "permission denied", str(path))
        monkeypatch.setattr("hypoloom.agent_sessions._regular", unreadable)
    with pytest.raises(GitAuthoringIntegrityError) as caught:
        workspace.capture_workspace(candidate)
    message = str(caught.value)
    assert "/input/" + relative in message
    assert str(tmp_path) not in message and "Errno" not in message
    if shape == "directory":
        assert "directory, not a file" in message
        assert "HYPOLOOM_TARGET_ROOT" in message
    elif shape == "missing":
        assert "missing" in message
    elif "symlink" in shape:
        assert "symlink" in message
    else:
        assert "cannot be read" in message


@pytest.mark.parametrize("failure", ["missing", "extra", "history", "wrong_component"])
def test_report_capture_distinguishes_missing_extra_and_changed_history(tmp_path, failure):
    store = ExpertMemory(tmp_path)
    base = store.ensure("run-1", "expert-a")
    cycle = 1
    if failure == "history":
        base = store.commit_report("run-1", "expert-a", base, "first-report",
            {"MEMORY.md": b"# Index\n", "rounds/1.md": _report()},
            cycle=1, facts=_facts(), max_bytes=8192)
        cycle = 2
    directory = tmp_path / "run-1/sessions/expert-a/report/memory"
    store.prepare("run-1", "expert-a", base, directory, writable=True)
    relative = f"rounds/{cycle}.md"
    if failure != "missing":
        actual = "rounds/primary.expert-a_goal/1.md" if failure == "wrong_component" else relative
        (directory / actual).parent.mkdir(parents=True, exist_ok=True)
        (directory / actual).write_bytes(_report())
    if failure == "extra":
        (directory / "unrelated.md").write_text("extra")
    elif failure == "history":
        (directory / "rounds/1.md").write_text("changed accepted history")
    with pytest.raises(MemoryError) as caught:
        store.capture("run-1", "expert-a", base, directory, cycle=cycle, max_bytes=8192)
    message = str(caught.value)
    assert "/memory/" + relative in message
    assert str(tmp_path) not in message
    if failure in {"missing", "wrong_component"}:
        assert "missing" in message
    if failure in {"extra", "wrong_component"}:
        assert "outside the allowed set" in message
        assert "unrelated.md" in message if failure == "extra" else "primary.expert-a_goal" in message
    if failure == "history":
        assert "modified read-only memory files" in message and "rounds/1.md" in message


@pytest.mark.parametrize("hooked", [False, True])
def test_native_settings_disable_auto_memory_without_disabling_tools_or_sessions(tmp_path, hooked):
    argv = _argv(tmp_path)
    if hooked:
        item = EmbeddedInput.from_bytes("node--expert-config", "application/json",
            b'{"node_runtime":{"safety":{"bash":{"deny_patterns":["forbidden-command"]}}}}')
        request = replace(default_request(), input_artifacts=(item,))
        argv = full_cc_argv(bwrap=tmp_path / "bwrap", executable=tmp_path / "claude",
            request=request, model="claude-sonnet-4-6", workspace=_runtime_workspace(tmp_path),
            provider_socket=tmp_path / "provider.sock", host_socket=None,
            output_socket=tmp_path / "output.sock", output_schema={}, extra_paths=())
        assert set(json.loads(argv[argv.index("--settings") + 1])["hooks"]) == {"PreToolUse", "Stop"}
    assert json.loads(argv[argv.index("--settings") + 1])["autoMemoryEnabled"] is False
    assert "Read,Write,Edit,Glob,Grep,Bash,Skill" == argv[argv.index("--tools") + 1]
    assert "--session-id" in argv


def test_report_prompt_uses_bound_relative_path_under_canonical_memory():
    request = default_request(AgentCommand.CYCLE_REPORT)
    item = EmbeddedInput.from_bytes("cycle-1--cycle-assessments", "application/json",
        b'{"cycle":1,"assessments":[],"report_path":"rounds/1.md"}')
    request = replace(request, input_artifacts=(item,), immutable_context_refs=(item.ref,))
    instruction = json.loads(_prompt(request, tool_workspace=True))["instruction"]
    assert "/memory/" in instruction and "report_path" in instruction
    assert "Multi-component owners use" not in instruction
    assert "Claude Code" in instruction and "memory directory" in instruction
    assert "--cycle-assessments.assessments" in instruction
    assert "row['payload']['assessment']" in instruction
    assert "do not copy whole fact records" in instruction
    assert "If it is empty, write assessments: []" in instruction
