"""Persistent CC context and cumulative chain worktrees use existing Git/artifacts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentRequest, EmbeddedInput, WorkspaceArtifact
from hypoloom.agent_sessions import AgentSessions, PreparedSessionWorkspace, node_memory_limit
from hypoloom.artifacts import AgentArtifactKey, ArtifactPublisher
from hypoloom.domain import AgentCommand
from hypoloom.expert_batch import AcceptedBatch, BatchHistory
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask, chain_session_scope
from hypoloom.fake_agent import default_request
from hypoloom.git_authoring_workspace import (
    GitAuthoringIntegrityError,
    GitAuthoringWorkspace,
)

from test_agent_aci import _pincer_request
from test_git_authoring_workspace import git, make_target


def _manager(tmp_path: Path) -> AgentSessions:
    state = tmp_path / "state"
    state.mkdir()
    return AgentSessions(state, ArtifactPublisher(state / "artifacts"))


def _request(command: AgentCommand, attempt: str) -> AgentRequest:
    return replace(default_request(command), attempt_id=attempt)


def _transcript(
    workspace: PreparedSessionWorkspace, text: str, *, append: bool = False
) -> bytes:
    path = workspace.session_root / f"{workspace.binding['session_id']}.jsonl"
    content = (
        json.dumps(
            {
                "sessionId": workspace.binding["session_id"],
                "message": {"role": "user", "content": text},
            }
        ).encode()
        + b"\n"
    )
    if append:
        content = path.read_bytes() + content
    path.write_bytes(content)
    return content


def _seed(manager: AgentSessions, binding: object) -> PreparedSessionWorkspace:
    request = replace(
        _pincer_request(ResearchNodeSubkind.SYNTHESIS), attempt_id="attempt-seed"
    )
    workspace = manager.prepare(request, binding)
    assert workspace.session_arguments == (
        "--session-id",
        workspace.binding["session_id"],
    )
    _transcript(workspace, "parent proposal context")
    workspace.finish_node()
    return workspace


@pytest.mark.parametrize("invalid", (0, -1, True, "100", None))
def test_node_memory_byte_limit_rejects_untyped_or_nonpositive_values(invalid: object) -> None:
    request = replace(default_request(), input_artifacts=(EmbeddedInput.from_bytes(
        "node--expert-config", "application/json", json.dumps({"node_runtime": {
            "memory": {"max_transcript_bytes": invalid}}}).encode()),))
    with pytest.raises(ValueError, match="positive integer"):
        node_memory_limit(request, "max_transcript_bytes", 4096)
    assert node_memory_limit(default_request(), "max_transcript_bytes", 4096) == 4096


def test_frozen_transcript_limit_refuses_resume_without_truncating_context(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    manager = _manager(tmp_path)
    seed = _seed(manager, binding)
    source = seed.session_root / f"{seed.binding['session_id']}.jsonl"
    original = source.read_bytes()
    request = replace(_pincer_request(ResearchNodeSubkind.SYNTHESIS),
        attempt_id="retry-with-smaller-byte-limit", input_artifacts=(EmbeddedInput.from_bytes(
            "node--expert-config", "application/json", json.dumps({"node_runtime": {
                "memory": {"max_transcript_bytes": len(original) - 1}}}).encode()),))
    with pytest.raises(GitAuthoringIntegrityError, match="size"):
        manager.prepare(request, binding)
    assert source.read_bytes() == original
    assert node_memory_limit(request, "max_plugin_bytes", 4096) == 4096


def _chain_request(command: AgentCommand, attempt: str, chain: str | None, *, experiment: int = 1):
    request = _request(command, attempt)
    task = replace(request.task_context, key=replace(request.task_context.key,
        chain=chain, experiment_ordinal=experiment))
    return replace(request, task_context=task, command_id=task.command_id)


def test_explicit_chain_forks_are_distinct_and_repair_ordinals_resume_only_own_context(tmp_path: Path):
    target, binding = make_target(tmp_path)
    manager = _manager(tmp_path)
    seed = _seed(manager, binding)
    chains = (None, "1", "chain-a")
    prepared = {}
    for index, chain in enumerate(chains):
        workspace = manager.prepare(_chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
            f"design-{index}", chain), binding)
        assert workspace.binding["scope"] == chain_session_scope(chain)
        assert workspace.binding["chain"] == (chain if chain is not None else "1")
        assert workspace.binding["parent_session_id"] == seed.binding["session_id"]
        assert workspace.session_arguments == ("--resume", seed.binding["session_id"],
            "--fork-session", "--session-id", workspace.binding["session_id"])
        (workspace.worktree_root / "experiment.py").write_text(f"print('private-chain-{index}')\n")
        (workspace.worktree_root / "src/value.txt").write_text(f"private-chain-{index}\n")
        _transcript(workspace, f"private-chain-{index}")
        workspace.finish_node()
        prepared[chain] = workspace
    assert len({item.binding["session_id"] for item in prepared.values()}) == len(chains)
    assert len({item.binding["branch"] for item in prepared.values()}) == len(chains)
    assert len({item.worktree_root for item in prepared.values()}) == len(chains)
    restarted = AgentSessions(manager.state_root, manager.publisher)
    for index, chain in enumerate(chains):
        design = prepared[chain]
        # The next experiment is a repair iteration, not another chain/context.
        request = _chain_request(AgentCommand.EXPERIMENT_EXECUTION, f"repair-{index}", chain, experiment=2)
        execution = restarted.prepare(request, binding)
        assert execution.binding == design.binding
        assert execution.session_arguments == ("--resume", design.binding["session_id"])
        assert execution.worktree_root == design.worktree_root
        own = execution.session_root / f"{execution.binding['session_id']}.jsonl"
        assert f"private-chain-{index}".encode() in own.read_bytes()
        for other in range(len(chains)):
            if other != index:
                assert f"private-chain-{other}".encode() not in own.read_bytes()
        sources = {item["path"]: WorkspaceArtifact.from_dict(item).content_bytes()
            for item in execution.capture_workspace()}
        assert f"private-chain-{index}".encode() in sources["implementation.patch"]
        assert all(f"private-chain-{other}".encode() not in sources["implementation.patch"]
            for other in range(len(chains)) if other != index)
        analysis = restarted.prepare(_chain_request(AgentCommand.RESULT_ANALYSIS,
            f"analysis-{index}", chain, experiment=2), binding)
        assert analysis.binding == design.binding and not analysis.writable
        assert analysis.worktree_root == design.worktree_root
    assert git(target, "status", "--porcelain") == b""


def test_sessions_seed_fork_resume_archive_and_cumulative_host_commits(
    tmp_path: Path,
) -> None:
    target, binding = make_target(tmp_path)
    original = git(target, "rev-parse", "HEAD")
    manager = _manager(tmp_path)
    seed = _seed(manager, binding)
    design = manager.prepare(
        _request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "attempt-design"), binding
    )
    child_id, parent_id = design.binding["session_id"], seed.binding["session_id"]
    assert design.session_arguments == (
        "--resume",
        parent_id,
        "--fork-session",
        "--session-id",
        child_id,
    )
    assert (design.session_root / f"{parent_id}.jsonl").read_bytes() == (
        seed.session_root / f"{parent_id}.jsonl"
    ).read_bytes()
    assert design.worktree_root != seed.worktree_root
    assert (
        git(design.worktree_root, "branch", "--show-current").decode().strip()
        == design.binding["branch"]
    )
    (design.worktree_root / "experiment.py").write_text("print('design')\n")
    (design.worktree_root / "src/value.txt").write_text("design\n")
    design_bytes = _transcript(design, "private chain repair context")
    first = design.finish_node()
    assert first["transcript_complete"] is True
    archive = manager.publisher.path_for(
        AgentArtifactKey(run_id=design.request.run_id, attempt_id="attempt-design")
    )
    assert (archive / "transcript.jsonl").read_bytes() == design_bytes
    replay = design.finish_node()
    assert replay["commit"] == first["commit"]
    assert replay["transcript"]["reused"] is True
    assert git(design.worktree_root, "status", "--porcelain") == b""

    execution = manager.prepare(
        _request(AgentCommand.EXPERIMENT_EXECUTION, "attempt-execution"), binding
    )
    assert execution.session_arguments == ("--resume", child_id)
    assert execution.worktree_root == design.worktree_root
    assert execution.writable
    (execution.worktree_root / "src/value.txt").write_text("execution repaired\n")
    _transcript(execution, "execution result", append=True)
    execution.finish_node()
    artifacts = {
        item["path"]: WorkspaceArtifact.from_dict(item).content_bytes()
        for item in execution.capture_workspace()
    }
    assert artifacts["experiment.py"] == b"print('design')\n"
    patch = artifacts["implementation.patch"]
    assert b"-base\n+execution repaired\n" in patch
    assert b"+print('design')" in patch
    replay_root = tmp_path / "replay"
    replay_root.mkdir()
    replay_workspace = GitAuthoringWorkspace(replay_root).prepare(binding, "apply")
    git(replay_workspace.worktree_root, "apply", "--check", "-", input_bytes=patch)
    git(replay_workspace.worktree_root, "apply", "-", input_bytes=patch)
    assert (
        replay_workspace.worktree_root / "src/value.txt"
    ).read_text() == "execution repaired\n"
    analysis = manager.prepare(
        _request(AgentCommand.RESULT_ANALYSIS, "attempt-analysis"), binding
    )
    assert analysis.session_arguments == ("--resume", child_id)
    assert analysis.worktree_root == design.worktree_root
    assert not analysis.writable
    assert (
        b"private chain repair context"
        in (analysis.session_root / f"{child_id}.jsonl").read_bytes()
    )
    assert git(target, "rev-parse", "HEAD") == original
    assert git(target, "status", "--porcelain") == b""


def test_session_retry_preserves_uncommitted_work_and_exact_attempt_inputs(
    tmp_path: Path,
) -> None:
    _, binding = make_target(tmp_path)
    manager = _manager(tmp_path)
    _seed(manager, binding)
    input_file = EmbeddedInput.from_bytes(
        "input-1", "text/plain", b"exact frozen input"
    )
    request = replace(
        _request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "attempt-design"),
        input_artifacts=(input_file,),
    )
    workspace = manager.prepare(request, binding)
    assert (
        workspace.input_root / "0000.txt"
    ).read_bytes() == input_file.content_bytes()
    (workspace.worktree_root / "experiment.py").write_text(
        "print('in-flight repair')\n"
    )
    transcript = _transcript(workspace, "before process interruption")
    closed = []

    class Resource:
        def close(self) -> None:
            closed.append(True)

    workspace.resources.append(Resource())
    workspace.cleanup()
    assert closed == [True]
    assert workspace.scope_root.exists()
    retry_request = replace(request, attempt_id="attempt-retry", attempt_ordinal=2)
    retry = manager.prepare(retry_request, binding)
    assert retry.binding == workspace.binding
    assert retry.session_arguments == ("--resume", workspace.binding["session_id"])
    assert (
        retry.worktree_root / "experiment.py"
    ).read_text() == "print('in-flight repair')\n"
    assert (
        retry.session_root / f"{retry.binding['session_id']}.jsonl"
    ).read_bytes() == transcript
    assert retry.input_root != workspace.input_root
    changed = replace(
        request,
        input_artifacts=(
            EmbeddedInput.from_bytes("input-1", "text/plain", b"changed"),
        ),
    )
    with pytest.raises(GitAuthoringIntegrityError, match="input bytes changed"):
        manager.prepare(changed, binding)
    removed = replace(request, input_artifacts=())
    with pytest.raises(GitAuthoringIntegrityError, match="input inventory"):
        manager.prepare(removed, binding)


def test_sessions_without_target_and_isolated_auxiliary_contexts(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    first = manager.prepare(_request(AgentCommand.GROUNDING, "attempt-grounding"), None)
    _transcript(first, "grounding-private")
    result = first.finish_node()
    resumed = manager.prepare(first.request, None)
    assert resumed.git_workspace.base_commit == first.git_workspace.base_commit
    assert resumed.binding == first.binding
    assert resumed.session_arguments == ("--resume", first.binding["session_id"])
    assert result["transcript_complete"]
    other = manager.prepare(_request(AgentCommand.OBSERVER, "attempt-observer"), None)
    assert other.session_root != first.session_root
    assert list(other.session_root.iterdir()) == []


@pytest.mark.parametrize("command", [AgentCommand.OBSERVER, AgentCommand.DEBATE_JUDGE])
def test_stateless_judge_retry_starts_fresh_and_preserves_failed_transcript(tmp_path, command):
    manager = _manager(tmp_path)
    request = _request(command, "attempt-first")
    first = manager.prepare(request, None)
    prior = _transcript(first, "FAILED_JUDGE_PRIVATE_CONTEXT")
    closed = first.finish_node()
    archive = manager.publisher.root / closed["transcript"]["path"] / "transcript.jsonl"
    first.cleanup()

    retry_request = replace(request, attempt_id="attempt-retry", attempt_ordinal=2)
    retry = manager.prepare(retry_request, None)
    try:
        assert retry.binding["session_id"] != first.binding["session_id"]
        assert retry.binding["parent_session_id"] is None
        assert retry.session_arguments == ("--session-id", retry.binding["session_id"])
        assert retry.scope_root != first.scope_root
        assert list(retry.session_root.iterdir()) == []
        assert archive.read_bytes() == prior
        assert AgentSessions.binding(retry_request) == retry.binding
        _transcript(retry, "FRESH_JUDGE_CONTEXT")
        completed = retry.finish_node()
        assert completed["transcript_complete"]
        assert archive.read_bytes() == prior
    finally:
        retry.cleanup()


def test_session_archive_rejects_foreign_identity_and_preserves_partial_tail(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    workspace = manager.prepare(
        _request(AgentCommand.GROUNDING, "attempt-grounding"), None
    )
    path = workspace.session_root / f"{workspace.binding['session_id']}.jsonl"
    path.write_text(
        json.dumps({"sessionId": "foreign-chain", "message": "foreign"}) + "\n"
    )
    with pytest.raises(GitAuthoringIntegrityError, match="another chain"):
        workspace.finish_node()
    complete = _transcript(workspace, "honest interrupted context")
    partial = complete + b'{"sessionId":'
    path.write_bytes(partial)
    result = workspace.finish_node()
    assert result["transcript_complete"] is False
    assert (
        manager.publisher.root / result["transcript"]["path"] / "transcript.jsonl"
    ).read_bytes() == partial


def test_resume_rejects_corrupted_complete_transcript_before_launch(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    request = _request(AgentCommand.GROUNDING, "attempt-grounding")
    workspace = manager.prepare(request, None)
    path = workspace.session_root / f"{workspace.binding['session_id']}.jsonl"
    for content in (b"not-json\n", b'{"sessionId":"another-session"}\n'):
        path.write_bytes(content)
        with pytest.raises(GitAuthoringIntegrityError, match="transcript"):
            manager.prepare(request, None)


def test_expert_forks_receive_only_their_own_parent_transcript(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    manager = _manager(tmp_path)
    children = []

    def owned(request: AgentRequest, role: str) -> AgentRequest:
        assert isinstance(request.task_context, ResearchNodeTask)
        task = replace(
            request.task_context,
            key=replace(request.task_context.key, expert_slot=role),
        )
        return replace(
            request, role=role, task_context=task, command_id=task.command_id
        )

    for role in ("alice", "bob"):
        seed_request = owned(
            replace(
                _pincer_request(ResearchNodeSubkind.SYNTHESIS),
                attempt_id="seed-" + role,
            ),
            role,
        )
        seed = manager.prepare(seed_request, binding)
        _transcript(seed, "private-context-" + role)
        seed.finish_node()
        child = manager.prepare(
            owned(
                _request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design-" + role),
                role,
            ),
            binding,
        )
        children.append(child)
        files = list(child.session_root.iterdir())
        assert len(files) == 1
        assert files[0].name == seed.binding["session_id"] + ".jsonl"
        assert ("private-context-" + role).encode() in files[0].read_bytes()
    assert children[0].scope_root != children[1].scope_root
    assert children[0].worktree_root != children[1].worktree_root
    assert b"private-context-bob" not in b"".join(
        path.read_bytes() for path in children[0].session_root.iterdir()
    )
    assert b"private-context-alice" not in b"".join(
        path.read_bytes() for path in children[1].session_root.iterdir()
    )


def _adjudicated_chain(root: Path):
    root.mkdir()
    _, binding = make_target(root)
    manager = _manager(root)
    _seed(manager, binding)
    request = _chain_request(
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "original-design", "chain-a"
    )
    original = manager.prepare(request, binding)
    (original.worktree_root / "experiment.py").write_text("print('source')\n")
    metadata = original.finish_node()
    row = {
        "run_id": request.run_id,
        "attempt_id": request.attempt_id,
        "kind": "AGENT",
        "state": "CLOSED",
        "terminal_reason": "SUCCEEDED",
        "command_id": request.command_id,
        "attempt_ordinal": request.attempt_ordinal,
        "observation_json": json.dumps(
            {
                "request": request.to_dict(),
                "session_binding": original.binding,
                "session": metadata,
            }
        ),
    }
    batch = AcceptedBatch(
        1,
        "accepted-synthesis",
        "actual-synthesis",
        {"chain-a": "source-hypothesis"},
        {
            "cycle": 1,
            "batch_command_id": "accepted-synthesis",
            "candidate": {
                "verdicts": [
                    {
                        "chain_id": "chain-a",
                        "node_id": "source-hypothesis",
                        "status": "refuted",
                    }
                ],
                "prune": [],
            },
        },
    )
    selected = BatchHistory(
        request.run_id, request.component_id, request.role, (batch,), {}
    )
    return manager, original, row, selected


def test_retire_adjudicated_worktrees_defers_open_chain_identically_for_open_only_listing(
    tmp_path: Path,
) -> None:
    manager, original, row, selected = _adjudicated_chain(tmp_path / "a")
    opened = {**row, "state": "OPEN", "attempt_id": "still-open"}
    assert manager.retire_adjudicated_worktrees(selected, [row, opened]) == {}
    assert manager.retire_adjudicated_worktrees(selected, [opened]) == {}
    assert original.worktree_root.exists()


def test_retire_adjudicated_worktrees_retires_identically_for_open_only_listing(
    tmp_path: Path,
) -> None:
    full_manager, full_original, full_row, full_selected = _adjudicated_chain(
        tmp_path / "a"
    )
    open_manager, open_original, _, open_selected = _adjudicated_chain(
        tmp_path / "b"
    )
    assert full_manager.retire_adjudicated_worktrees(full_selected, [full_row]) == {}
    assert open_manager.retire_adjudicated_worktrees(open_selected, []) == {}
    assert not full_original.worktree_root.exists()
    assert not open_original.worktree_root.exists()


def _cost_state_record(session_id: str, total: float, tokens: int) -> dict:
    return {"type": "cost-state", "sessionId": session_id, "totalCostUSD": total,
        "modelUsage": {"stub-model": {"inputTokens": tokens, "outputTokens": tokens,
            "costUSD": total}}}


def test_resume_and_fork_record_the_newest_saved_cost_state_as_usage_baseline(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    manager = _manager(tmp_path)
    first = manager.prepare(_request(AgentCommand.GROUNDING, "attempt-grounding"), None)
    assert first.session_usage_baseline is None
    _transcript(first, "grounding-private")
    resumed = manager.prepare(first.request, None)
    assert resumed.session_arguments == ("--resume", first.binding["session_id"])
    # Older CC transcripts carry no saved totals and start at zero.
    assert resumed.session_usage_baseline is None
    path = first.session_root / f"{first.binding['session_id']}.jsonl"
    cost_state = _cost_state_record(first.binding["session_id"], 0.001, 7)
    path.write_bytes(path.read_bytes() + json.dumps(cost_state).encode() + b"\n")
    carried = manager.prepare(first.request, None)
    assert carried.session_usage_baseline == cost_state
    # A later exit record supersedes it; an unterminated tail is a partial write.
    newer = _cost_state_record(first.binding["session_id"], 0.002, 11)
    path.write_bytes(path.read_bytes() + json.dumps(newer).encode() + b"\n" + b'{"type": "cost')
    assert manager.prepare(first.request, None).session_usage_baseline == newer

    seed = _seed(manager, binding)
    forked = manager.prepare(_chain_request(
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design-fork", "chain-a"), binding)
    assert forked.session_arguments == ("--resume", seed.binding["session_id"],
        "--fork-session", "--session-id", forked.binding["session_id"])
    assert forked.session_usage_baseline is None
    seed_path = seed.session_root / f"{seed.binding['session_id']}.jsonl"
    seed_state = _cost_state_record(seed.binding["session_id"], 0.003, 13)
    seed_path.write_bytes(seed_path.read_bytes() + json.dumps(seed_state).encode() + b"\n")
    forked = manager.prepare(_chain_request(
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design-fork-2", "1"), binding)
    assert forked.session_usage_baseline == seed_state
