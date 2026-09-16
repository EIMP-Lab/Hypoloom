from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import (
    EmbeddedInput,
    request_digest,
    validate_postflight,
    with_output_digest,
)
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_GIT_TOOL_PERMISSION_PROFILE,
    CLAUDE_AGENT_HOST_VERSION,
    ClaudeAgentHost,
    _capture_tool_workspace,
)
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.git_authoring_workspace import GitAuthoringWorkspace
from hypoloom.execution_workspace import GitTargetBinding


def _git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout


def _target(tmp_path: Path) -> tuple[Path, GitTargetBinding]:
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "-q")
    (target / "README.md").write_text("pinned target\n", encoding="utf-8")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Tests",
        "-c",
        "user.email=tests@example.invalid",
        "commit",
        "-q",
        "-m",
        "base",
    )
    commit = _git(target, "rev-parse", "HEAD").decode().strip()
    return target, GitTargetBinding(checkout=target.resolve(), commit=commit)


def _request():
    base = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    config = EmbeddedInput.from_bytes(
        f"{base.component_id}--expert-config",
        "application/json",
        json.dumps({
            "execution": {"runtime_prefix": None},
            "required_experiment_spec": {
                "metric": {"name": "score", "direction": "MAXIMIZE", "target": "1"},
                "evaluator_version": "component-result/7",
                "artifact_requirements": ["result.json"],
            },
        }, separators=(",", ":")).encode(),
    )
    return replace(
        base,
        required_model_identity="vendor-model-20260829",
        required_adapter_version=CLAUDE_AGENT_HOST_VERSION,
        tool_permission_profile=CLAUDE_AGENT_GIT_TOOL_PERMISSION_PROFILE,
        budget=replace(base.budget, max_output_bytes=256 * 1024),
        input_artifacts=(config,),
        immutable_context_refs=(config.ref,),
    )


def test_git_profile_mounts_only_the_writable_snapshot_and_hides_git_admin(
    tmp_path: Path,
) -> None:
    _, binding = _target(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    prepared = GitAuthoringWorkspace(state).prepare(binding, "attempt-1")
    host = ClaudeAgentHost(
        claude_executable="/bin/true",
        bwrap_path="/bin/true",
        extra_runtime_paths=(),
    )

    argv = host._sandbox_argv(_request(), "vendor-model-20260829", prepared)
    rendered = "\n".join(argv)

    assert str(prepared.worktree_root) in argv
    assert str(prepared.worktree_root / ".git") in argv
    assert str(prepared.repository_root) not in argv
    assert "Read(//workspace/**)" in argv
    assert "Write(//workspace/**)" in argv
    assert "Read(//workspace/.git)" in argv
    assert "Bash" not in rendered
    assert '{"mcpServers":{}}' in argv
    assert "--no-session-persistence" in argv


def test_git_workspace_capture_returns_one_source_and_one_replayable_cumulative_patch(
    tmp_path: Path,
) -> None:
    target, binding = _target(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    workspace = GitAuthoringWorkspace(state)
    prepared = workspace.prepare(binding, "attempt-1")
    source = b"from support import value\nprint(value)\n"
    (prepared.worktree_root / "experiment.py").write_bytes(source)
    (prepared.worktree_root / "support.py").write_text("value = 7\n", encoding="utf-8")

    artifacts = _capture_tool_workspace(prepared, max_bytes=256 * 1024)

    assert tuple(item.path for item in artifacts) == (
        "experiment.py",
        "implementation.patch",
    )
    assert artifacts[0].content_bytes() == source
    patch = artifacts[1].content_bytes()
    assert b"experiment.py" in patch and b"support.py" in patch
    assert _git(target, "status", "--porcelain=v1", "-z") == b""

    replay = workspace.prepare(binding, "attempt-2")
    _git(replay.worktree_root, "apply", "--check", "-", input_bytes=patch)
    _git(replay.worktree_root, "apply", "-", input_bytes=patch)
    assert (replay.worktree_root / "experiment.py").read_bytes() == source
    assert (replay.worktree_root / "support.py").read_text() == "value = 7\n"

    request = _request()
    generated = FakeAgent().invoke(
        default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    )
    response = with_output_digest(
        replace(
            generated,
            proposed_workspace_artifacts=artifacts,
            model_identity=request.required_model_identity,
            adapter_version=request.required_adapter_version,
            input_digest=request_digest(request),
            output_digest="",
        )
    )
    validated = validate_postflight(request, response)
    assert tuple(validated.workspace_files) == (
        "experiment.py",
        "implementation.patch",
    )
