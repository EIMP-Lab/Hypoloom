"""Bounded declared-source capture and CLI schema, independent of model choice."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput, WorkspaceArtifact
from hypoloom.claude_agent_host import (
    _capture_tool_workspace,
    _output_schema,
    _prompt,
)
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
from test_agent_aci import _pincer_request
from test_agent_sessions import _manager, _request, _seed, _transcript
from test_git_authoring_workspace import git, make_target
from test_measurement_contract import declaration


def _deferred(request, *, deferred=True):
    inputs = []
    supplied = tuple(request.input_artifacts)
    if not any(item.ref.endswith("--expert-config") for item in supplied):
        metric = {"name": "accuracy", "direction": "MAXIMIZE", "target": "0.55"}
        config = {
            "component": {"result": {"kind": "metric", "metric": metric}},
            "required_experiment_spec": {
                "metric": metric,
                "evaluator_version": "component-result/7",
                "artifact_requirements": ["result.json"],
            },
        }
        supplied += (
            EmbeddedInput.from_bytes(
                "fixture--expert-config",
                "application/json",
                json.dumps(config).encode(),
            ),
        )
    for item in supplied:
        if deferred and item.ref.endswith("--expert-config"):
            document = json.loads(item.content_bytes())
            document["component"]["result"] = {"kind": "metric", "deferred": True}
            if "required_experiment_spec" in document:
                document["required_experiment_spec"]["metric"] = None
            item = EmbeddedInput.from_bytes(
                item.ref, item.media_type, json.dumps(document).encode()
            )
        inputs.append(item)
    return replace(
        request,
        schema_version=12,
        input_artifacts=tuple(inputs),
        immutable_context_refs=tuple(item.ref for item in inputs),
    )


def _candidate():
    return {
        "measurement": declaration().to_dict(),
        "execution_argv": ["/bin/sh", "/input/experiment.sh"],
    }


def _workspace(tmp_path):
    _, binding = make_target(tmp_path)
    manager = _manager(tmp_path)
    _seed(manager, binding)
    workspace = manager.prepare(
        _deferred(_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design")), binding
    )
    (workspace.worktree_root / "experiment.sh").write_text("echo raw\n")
    (workspace.worktree_root / "parse_metrics.py").write_text("print('parser')\n")
    return workspace


def test_generic_source_and_parser_capture_preserves_cumulative_patch_not_whole_repo(
    tmp_path: Path,
):
    workspace = _workspace(tmp_path)
    _transcript(workspace, "design shell and parser")
    workspace.finish_node()
    (workspace.worktree_root / "src/value.txt").write_text("later supporting change\n")
    captured = _capture_tool_workspace(
        workspace,
        max_bytes=workspace.request.budget.max_output_bytes,
        candidate=_candidate(),
    )
    files = {item.path: item.content_bytes() for item in captured}
    assert set(files) == {"experiment.sh", "parse_metrics.py", "implementation.patch"}
    assert b"+echo raw" in files["implementation.patch"]
    assert b"+later supporting change" in files["implementation.patch"]
    assert "README.md" not in files and "src/value.txt" not in files
    assert not (workspace.worktree_root / "experiment.py").exists()
    assert {item["path"] for item in workspace.capture_workspace(_candidate())} == set(
        files
    )
    with pytest.raises(GitAuthoringIntegrityError):
        _capture_tool_workspace(workspace, max_bytes=1, candidate=_candidate())


def test_existing_frozen_generic_program_and_parser_need_no_synthetic_patch(
    tmp_path: Path,
):
    target, binding = make_target(tmp_path)
    (target / "experiment.sh").write_text("echo raw\n")
    (target / "parse_metrics.py").write_text("print('parser')\n")
    git(target, "add", "experiment.sh", "parse_metrics.py")
    git(
        target,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "existing program",
    )
    binding = replace(binding, commit=git(target, "rev-parse", "HEAD").decode().strip())
    manager = _manager(tmp_path)
    _seed(manager, binding)
    workspace = manager.prepare(
        _deferred(_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design")), binding
    )
    files = {
        item["path"]: WorkspaceArtifact.from_dict(item).content_bytes()
        for item in workspace.capture_workspace(_candidate())
    }
    assert files == {
        "experiment.sh": b"echo raw\n",
        "parse_metrics.py": b"print('parser')\n",
        "implementation.patch": b"",
    }


@pytest.mark.parametrize(
    "failure", ("escape", "git", "symlink", "missing", "budget", "unadmitted")
)
def test_measurement_capture_rejects_invalid_sources(tmp_path: Path, failure: str):
    workspace = _workspace(tmp_path)
    candidate = _candidate()
    if failure == "escape":
        candidate["execution_argv"] = ["/bin/sh", "/input/../outside"]
    elif failure == "git":
        candidate["execution_argv"] = ["/bin/sh", "/input/.git"]
    elif failure == "symlink":
        (workspace.worktree_root / "experiment.sh").unlink()
        (workspace.worktree_root / "experiment.sh").symlink_to(
            tmp_path / "target/README.md"
        )
    elif failure == "missing":
        (workspace.worktree_root / "parse_metrics.py").unlink()
    elif failure == "budget":
        workspace.request = replace(
            workspace.request,
            budget=replace(workspace.request.budget, max_output_bytes=1),
        )
    else:
        workspace.request = _request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design")
    with pytest.raises((ValueError, OSError, GitAuthoringIntegrityError)):
        workspace.capture_workspace(candidate)


@pytest.mark.parametrize("synthesis", (False, True))
def test_deferred_cli_schema_accepts_declared_metric_but_static_schema_is_unchanged(
    synthesis: bool,
):
    original = (
        _pincer_request(ResearchNodeSubkind.SYNTHESIS)
        if synthesis
        else _request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design")
    )
    original = _deferred(original, deferred=False)
    request = _deferred(original)
    candidate = _output_schema(request, tool_workspace=True)["properties"][
        "typed_candidate"
    ]
    assert "measurement" in candidate["required"]
    assert set(candidate["properties"]["measurement"]["required"]) == set(
        declaration().to_dict()
    )
    if not synthesis:
        assert "execution_argv" in candidate["required"]
        assert "const" not in candidate["properties"]["metrics"]
        prompt = json.loads(_prompt(request, tool_workspace=True))["instruction"]
        assert "/input/<relative-path>" in prompt and "/parsed/result.json" in prompt
        assert (
            "experiment.py must" not in prompt and "Leave non-empty UTF-8" not in prompt
        )
    static = _output_schema(original, tool_workspace=True)["properties"][
        "typed_candidate"
    ]
    assert "measurement" not in static["properties"]
