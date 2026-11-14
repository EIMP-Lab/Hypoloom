from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hypoloom.engineering_budget import budget_decision
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_instruction


def test_deadline_guard_limits_tool_without_granting_permissions_or_changing_command():
    event = {"tool_name": "Bash", "tool_input": {"command": "python worker_check.py", "timeout": 300000,
        "run_in_background": True, "description": "Check workers"}}
    before = json.loads(json.dumps(event))
    output = budget_decision(event, deadline="2030-01-01T00:00:00Z", wall_seconds=120,
        now=datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp() - 20)["hookSpecificOutput"]
    assert output["updatedInput"]["timeout"] == 9000
    assert output["updatedInput"]["command"] == event["tool_input"]["command"]
    assert output["updatedInput"]["description"] == "Check workers"
    assert output["updatedInput"]["run_in_background"] is False
    assert "permissionDecision" not in output and event == before


def test_deadline_guard_leaves_handoff_time_and_respects_shorter_timeouts():
    deadline = "2030-01-01T00:00:00Z"
    now = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    event = {"tool_name": "Bash", "tool_input": {"command": "true", "timeout": 1000, "run_in_background": False}}
    assert budget_decision(event, deadline=deadline, wall_seconds=120, now=now - 90) == {}
    denied = budget_decision(event, deadline=deadline, wall_seconds=120, now=now - 5)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert budget_decision({"tool_name": "Read"}, deadline=deadline, wall_seconds=120, now=now) == {}
    for timeout in (True, -1, "1000"):
        malformed = {**event, "tool_input": {**event["tool_input"], "timeout": timeout}}
        assert budget_decision(malformed, deadline=deadline, wall_seconds=120, now=now - 90)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("version", [36, 37])
def test_guard_is_bound_into_native_argv_only_for_new_engineering_nodes(tmp_path, monkeypatch, version):
    from hypoloom import full_cc_runtime
    monkeypatch.setattr(full_cc_runtime, "runtime_mounts", lambda _: ())
    monkeypatch.setattr(full_cc_runtime, "_profile_mounts", lambda *_: ())
    monkeypatch.setattr(full_cc_runtime, "_protected_worktree_mounts", lambda *_: ())
    request = replace(default_request(AgentCommand.EXPERIMENT_EXECUTION), schema_version=version)
    workspace = SimpleNamespace(session_root=tmp_path / "session", worktree_root=tmp_path / "worktree",
        input_root=tmp_path / "inputs", writable=True, session_arguments=())
    argv = full_cc_argv(bwrap=Path("/bwrap"), executable=Path("/claude"), request=request,
        model="test-model", workspace=workspace, provider_socket=tmp_path / "provider", host_socket=None,
        output_schema={}, extra_paths=())
    settings = json.loads(argv[argv.index("--settings") + 1])
    guards = [h for group in settings.get("hooks", {}).get("PreToolUse", []) for h in group["hooks"]
        if "engineering_budget.py" in h["command"]]
    assert bool(guards) is (version >= 37)
    if guards:
        command = shlex.split(guards[0]["command"])
        assert command[command.index("--deadline") + 1] == request.deadline
        assert guards[0]["timeout"] == 3
        assert "--verified-engineering-memory" in full_cc_instruction(request)


def test_standalone_hook_runs_with_stdlib_and_returns_native_updated_input(tmp_path):
    from hypoloom import engineering_budget
    process = subprocess.run([sys.executable, engineering_budget.__file__, "--deadline", "2099-01-01T00:00:00Z",
        "--wall-seconds", "120"], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}}),
        text=True, capture_output=True, timeout=5, cwd=tmp_path)
    assert process.returncode == 0
    result = json.loads(process.stdout)["hookSpecificOutput"]
    assert result["updatedInput"]["timeout"] == 60000 and "permissionDecision" not in result
