"""Accepted ADJ removes only hot checkouts, not recoverable chain history."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.agent_sessions import AgentSessions
from hypoloom.domain import AgentCommand
from hypoloom.execution_workspace import GitTargetBinding
from hypoloom.expert_batch import AcceptedBatch, BatchHistory
from hypoloom.git_authoring_workspace import (
    GitAuthoringWorkspace,
    GitAuthoringWorkspaceError,
)
from hypoloom.lifecycle import Command, RunState
from hypoloom.state_store import HostWriterCapability, StateStore
from hypoloom.workflow_host import WorkflowHost
from test_agent_sessions import _chain_request, _transcript
from test_debate_sessions import _request, _setup
from test_git_authoring_workspace import git
from test_state_transactions import run_version


def history(original, *, status="refuted", prune=(), chain="chain-a"):
    request = original.request
    batch = AcceptedBatch(
        1,
        "accepted-synthesis",
        "actual-synthesis",
        {chain: "source-hypothesis"},
        {
            "cycle": 1,
            "batch_command_id": "accepted-synthesis",
            "candidate": {
                "verdicts": [
                    {
                        "chain_id": chain,
                        "node_id": "source-hypothesis",
                        "status": status,
                    }
                ],
                "prune": list(prune),
            },
        },
    )
    return BatchHistory(
        request.run_id, request.component_id, request.role, (batch,), {}
    )


def files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "status,prune,removed",
    [
        ("refuted", (), True),
        ("verified", ("chain-a",), True),
        ("verified", (), False),
        ("inconclusive", (), False),
    ],
)
def test_accepted_selection_removes_only_refuted_or_explicitly_pruned_hot_checkout(
    tmp_path, status, prune, removed
):
    manager, target, original, row, _ = _setup(tmp_path)
    peer = manager.prepare(
        _chain_request(AgentCommand.RESULT_ANALYSIS, "peer-analysis", "chain-b"), target
    )
    _transcript(peer, "Unrelated kept chain.")
    peer.finish_node()
    archive_before = files(manager.publisher.root)
    projects_before = files(original.session_root)
    inputs_before = files(original.scope_root / "inputs")
    peer_before = files(peer.scope_root)
    source = original.git_workspace
    branch = original.binding["branch"]
    commit = json.loads(row["observation_json"])["session"]["commit"]
    selected = history(original, status=status, prune=prune)
    assert manager.retire_adjudicated_worktrees(selected, [row]) == {}
    assert original.worktree_root.exists() is not removed
    assert git(source.repository_root, "rev-parse", branch).decode().strip() == commit
    assert files(manager.publisher.root) == archive_before
    assert files(original.session_root) == projects_before
    assert files(original.scope_root / "inputs") == inputs_before
    assert files(peer.scope_root) == peer_before
    restarted = AgentSessions(manager.state_root, manager.publisher)
    assert restarted.retire_adjudicated_worktrees(selected, [row]) == {}
    assert original.worktree_root.exists() is not removed


@pytest.mark.parametrize("kind", ["AGENT", "PROCESS"])
def test_open_same_chain_user_defers_cleanup_until_closed(tmp_path, kind):
    manager, _, original, row, _ = _setup(tmp_path)
    opened = {
        **row,
        "state": "OPEN",
        "kind": kind,
        "attempt_id": "still-open",
        "experiment_id": "exp-1",
    }
    creation = {
        "run_id": row["run_id"],
        "workflow_id": "exp-1",
        "payload_json": json.dumps({"hypothesis_id": "source-hypothesis"}),
    }
    selected = history(original)
    assert (
        manager.retire_adjudicated_worktrees(
            selected, [row, opened], creations=[creation]
        )
        == {}
    )
    assert original.worktree_root.exists()
    assert (
        manager.retire_adjudicated_worktrees(
            selected, [row, {**opened, "state": "CLOSED"}], creations=[creation]
        )
        == {}
    )
    assert not original.worktree_root.exists()


def test_dirty_checkout_is_preserved_with_visible_retryable_error(tmp_path, caplog):
    manager, target, original, row, _ = _setup(tmp_path)
    dirty = original.worktree_root / "uncommitted.txt"
    dirty.write_text("Keep this interrupted authoring work.\n")
    before = files(original.scope_root)
    errors = manager.retire_adjudicated_worktrees(history(original), [row])
    assert len(errors) == 1 and "dirty" in next(iter(errors.values()))
    assert "worktree cleanup" in caplog.text
    assert files(original.scope_root) == before
    retry = manager.prepare(
        replace(original.request, attempt_id="retry-after-dirty", attempt_ordinal=2),
        target,
    )
    _transcript(retry, "Host subsequently closed the retry.", append=True)
    retry.finish_node()
    assert manager.retire_adjudicated_worktrees(history(original), [row]) == {}
    assert not original.worktree_root.exists()


@pytest.mark.parametrize("owner", ["role", "component_id", "run_id", "chain", "cycle"])
def test_owner_selection_never_cleans_a_different_scope(tmp_path, owner):
    manager, _, original, row, _ = _setup(tmp_path)
    selected = history(original)
    if owner == "chain":
        selected = history(original, chain="another-chain")
    elif owner == "cycle":
        selected = replace(selected, batches=(replace(selected.batches[0], cycle=2),))
    else:
        selected = replace(selected, **{owner: "another-owner"})
    before = files(original.scope_root)
    manager.retire_adjudicated_worktrees(selected, [row])
    assert files(original.scope_root) == before


@pytest.mark.parametrize("bad", ["../outside", ".", "a/b"])
def test_unsafe_identity_is_rejected_before_any_cleanup(tmp_path, bad):
    manager, _, original, row, _ = _setup(tmp_path)
    before = files(original.scope_root)
    with pytest.raises(ValueError):
        manager.retire_adjudicated_worktrees(
            replace(history(original), role=bad), [row]
        )
    assert files(original.scope_root) == before


def test_fork_reads_retained_commit_without_recreating_retired_source_and_resumes_after_restart(
    tmp_path,
):
    manager, target, original, row, context = _setup(tmp_path)
    manager.retire_adjudicated_worktrees(history(original), [row])
    assert not original.worktree_root.exists()
    for number in (1, 2, 3):
        restarted = AgentSessions(manager.state_root, manager.publisher)
        fork = restarted.prepare(_request(original, row, context, number), target)
        assert (
            fork.worktree_root / "experiment.py"
        ).read_text() == "print('original-debate-source')\n"
        assert not original.worktree_root.exists()
        if number > 1:
            assert fork.session_arguments == ("--resume", context["session_id"])
        _transcript(fork, f"Retained-source round {number}", append=number > 1)
        fork.finish_node()
        fork.cleanup()


def test_authorized_same_scope_reconstruction_materializes_branch_and_keeps_cumulative_base(
    tmp_path,
):
    manager, target, original, row, _ = _setup(tmp_path)
    manager.retire_adjudicated_worktrees(history(original), [row])
    restored = AgentSessions(manager.state_root, manager.publisher).prepare(
        original.request, target
    )
    metadata = json.loads(row["observation_json"])["session"]
    assert restored.binding == original.binding
    assert restored.git_workspace.base_commit == metadata["base_commit"]
    assert (
        git(restored.worktree_root, "rev-parse", "HEAD").decode().strip()
        == metadata["commit"]
    )
    assert restored.session_arguments == ("--resume", metadata["session_id"])


def test_external_binding_does_not_accept_bare_repository_as_checkout(tmp_path):
    manager, _, original, _, _ = _setup(tmp_path)
    destination = tmp_path / "external-consumer"
    destination.mkdir()
    binding = GitTargetBinding(
        checkout=original.git_workspace.repository_root,
        commit=git(original.worktree_root, "rev-parse", "HEAD").decode().strip(),
    )
    with pytest.raises(GitAuthoringWorkspaceError):
        GitAuthoringWorkspace(destination).prepare(binding, "invalid-external-binding")


def test_absent_checkout_replay_does_not_spawn_git(tmp_path, monkeypatch):
    manager, _, original, row, _ = _setup(tmp_path)
    manager.retire_adjudicated_worktrees(history(original), [row])

    def unexpected(*args, **kwargs):
        raise AssertionError("an already retired checkout must not spawn Git")

    monkeypatch.setattr("hypoloom.git_authoring_workspace._run_git", unexpected)
    assert (
        AgentSessions(
            manager.state_root, manager.publisher
        ).retire_adjudicated_worktrees(history(original), [row])
        == {}
    )


def test_io_failure_preserves_history_and_retry_uses_same_accepted_selection(
    tmp_path, monkeypatch, caplog
):
    manager, _, original, row, _ = _setup(tmp_path)
    before = files(original.scope_root)
    real_git = GitAuthoringWorkspace._repository_git

    def interrupted(self, prepared, *arguments):
        if arguments[:2] == ("worktree", "remove"):
            raise OSError("test I/O interruption")
        return real_git(self, prepared, *arguments)

    with monkeypatch.context() as patch:
        patch.setattr(GitAuthoringWorkspace, "_repository_git", interrupted)
        errors = manager.retire_adjudicated_worktrees(history(original), [row])
    assert (
        "test I/O interruption" in next(iter(errors.values()))
        and "test I/O interruption" in caplog.text
    )
    assert files(original.scope_root) == before
    assert (
        AgentSessions(
            manager.state_root, manager.publisher
        ).retire_adjudicated_worktrees(history(original), [row])
        == {}
    )
    assert not original.worktree_root.exists()


def test_symlink_hot_checkout_is_preserved_without_touching_another_asset(tmp_path):
    manager, _, original, row, _ = _setup(tmp_path)
    manager.retire_adjudicated_worktrees(history(original), [row])
    external = tmp_path / "unrelated-directory"
    external.mkdir()
    (external / "user-file.txt").write_text("Do not remove this unrelated data.\n")
    original.worktree_root.symlink_to(external, target_is_directory=True)
    before = files(external)
    assert manager.retire_adjudicated_worktrees(history(original), [row])
    assert original.worktree_root.is_symlink() and files(external) == before


def test_host_restart_replays_sqlite_adjudication_before_next_dispatch_without_new_rows(
    tmp_path,
):
    """Crash window: ADJ committed, Host exits before its next cleanup tick."""
    manager, target, original, row, context = _setup(tmp_path)
    request = original.request
    selected = history(original)
    manager.state_root.chmod(0o700)
    store = StateStore(manager.state_root, HostWriterCapability.issue()).initialize()
    store.create_run(request.run_id, replace(run_version(), agent_aci_version="14"))
    with store.transaction() as tx:
        for command, state, payload in (
            (Command.CREATE_RUN, RunState.CREATED, {}),
            (Command.ACTIVATE, RunState.ACTIVE, {}),
            (
                Command.ACCEPT_HYPOTHESIS,
                RunState.ACTIVE,
                {
                    "role": request.role,
                    "component_id": request.component_id,
                    "node_command_id": "accepted-synthesis",
                    "agent_attempt_id": "actual-synthesis",
                    "hypothesis_batch": {
                        "cycle": 1,
                        "chains": {"chain-a": "source-hypothesis"},
                    },
                },
            ),
            (
                Command.ACCEPT_ADJUDICATION,
                RunState.ACTIVE,
                {
                    "role": request.role,
                    "component_id": request.component_id,
                    "adjudication": selected.batches[0].selection,
                },
            ),
        ):
            tx.append_transition(
                WorkflowHost._transition_record(
                    None,
                    tx,
                    request.run_id,
                    request.run_id,
                    "RUN",
                    command,
                    state,
                    payload=payload,
                )
            )
        # Ordinary accepted-row fixture, not a second lifecycle receipt.
        tx.append_fact(
            {
                "fact_id": "source-hypothesis-fact",
                "run_id": request.run_id,
                "fact_type": "HypothesisProposed",
                "schema_version": 1,
                "goal_id": "goal-1",
                "subject_id": "source-hypothesis",
                "hypothesis_id": "source-hypothesis",
                "refs": [],
                "payload": {
                    "component_id": request.component_id,
                    "proposer_role": request.role,
                    "parent_hypothesis_id": None,
                },
            }
        )
    before = store.inspect("SELECT * FROM workflow_transitions ORDER BY rowid")
    store.close()
    assert original.worktree_root.exists()

    class DispatchReached(Exception):
        pass

    class RestartedDriver:
        _batch_history = WorkflowHost._batch_history

        def _research_contract(self, run_id):
            component = SimpleNamespace(
                component_id=request.component_id,
                owner_role=request.role,
                tier=SimpleNamespace(value="primary"),
            )
            return SimpleNamespace(
                components=(component,), component=lambda _: component
            )

        def _policy_snapshot(self, run):
            # Stop at the next ordinary dispatch boundary, after reconciliation.
            raise DispatchReached

    driver = RestartedDriver()
    driver.state_root, driver.publisher = manager.state_root, manager.publisher
    driver.interaction = SimpleNamespace(priority_roles=lambda _: ())
    driver.store = StateStore(
        manager.state_root, HostWriterCapability.issue()
    ).initialize()
    try:
        with pytest.raises(DispatchReached):
            WorkflowHost._drive_batches(driver, request.run_id)
        assert not original.worktree_root.exists()
        assert (
            driver.store.inspect("SELECT * FROM workflow_transitions ORDER BY rowid")
            == before
        )
        fork = AgentSessions(manager.state_root, manager.publisher).prepare(
            _request(original, row, context, 1), target
        )
        assert fork.session_arguments[0] == "--resume"
        assert (
            fork.worktree_root / "experiment.py"
        ).read_bytes() == b"print('original-debate-source')\n"
        assert not original.worktree_root.exists()
        fork.cleanup()
    finally:
        driver.store.close()
