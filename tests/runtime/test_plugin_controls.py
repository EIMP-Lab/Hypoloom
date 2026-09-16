from __future__ import annotations

import pytest

from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.git_authoring_workspace import GitAuthoringWorkspaceError
from test_expert_plugin import _LIMIT, _SKILL


def candidate(store, base, name, *, advance=True):
    prepared = store.prepare_update("run", "critic", commit=base.commit, attempt_id=name, command_id=name)
    path = prepared.root / _SKILL
    path.write_bytes(path.read_bytes() + ("\n" + name + "\n").encode())
    return prepared, store.finish_update(prepared, max_bytes=_LIMIT, advance=advance)


def test_pin_old_commit_retains_real_evolution_candidates_without_moving_main(tmp_path):
    store = ExpertPluginStore(tmp_path)
    seed = store.ensure("run", "critic", "focus")
    _, current = candidate(store, seed, "before-pin")
    prepared, proposal = candidate(store, seed, "pinned-candidate", advance=False)
    assert proposal.commit not in {seed.commit, current.commit}
    assert store.ensure("run", "critic", "ignored") == current
    assert store.binding_at("run", "critic", seed.commit, max_bytes=_LIMIT) == seed
    assert store.finish_update(prepared, max_bytes=_LIMIT, advance=False) == proposal
    refs = store._git(prepared.repository_root, "for-each-ref", "--format=%(objectname)", "refs/heads/proposals").decode().splitlines()
    assert proposal.commit in refs
    later = store.prepare_update("run", "critic", commit=seed.commit, attempt_id="retry", command_id="pinned-candidate")
    assert store.finish_update(later, max_bytes=_LIMIT, advance=False) == proposal
    view = store.read("run", "critic", commit=proposal.commit, attempt_id="inspect", max_bytes=_LIMIT)
    try:
        assert b"pinned-candidate" in (view.root / _SKILL).read_bytes()
        assert b"before-pin" not in (view.root / _SKILL).read_bytes()
    finally:
        view.close()


def test_revert_creates_head_child_with_exact_target_tree_and_keeps_all_history(tmp_path):
    store = ExpertPluginStore(tmp_path)
    seed = store.ensure("run", "critic", "focus")
    _, current = candidate(store, seed, "accepted-update")
    reverted = store.revert_to("run", "critic", target_commit=seed.commit, command_id="operator-revert", max_bytes=_LIMIT)
    repository = store._repository("run", "critic")
    assert reverted.commit not in {seed.commit, current.commit}
    assert store._git(repository, "rev-list", "--parents", "-n", "1", reverted.commit).decode().split() == [reverted.commit, current.commit]
    assert store._git(repository, "rev-parse", reverted.commit + "^{tree}") == store._git(repository, "rev-parse", seed.commit + "^{tree}")
    assert store.ensure("run", "critic", "ignored") == current
    assert store.revert_to("run", "critic", target_commit=seed.commit, command_id="operator-revert", max_bytes=_LIMIT) == reverted
    with pytest.raises(ValueError, match="bound|target|different"):
        store.revert_to("run", "critic", target_commit=current.commit, command_id="operator-revert", max_bytes=_LIMIT)


def test_explicit_commit_validation_is_readonly_and_checks_native_skill_bytes(tmp_path):
    store = ExpertPluginStore(tmp_path)
    seed = store.ensure("run", "critic", "focus")
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert store.binding_at("run", "critic", seed.commit, max_bytes=_LIMIT) == seed
    assert {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    with pytest.raises(ValueError, match="explicit full"):
        store.binding_at("run", "critic", "HEAD", max_bytes=_LIMIT)
    with pytest.raises((ValueError, GitAuthoringWorkspaceError), match="budget|bound"):
        store.binding_at("run", "critic", seed.commit, max_bytes=1)


def test_selection_respects_next_cycle_pin_and_unpin_without_discarding_candidates():
    from hypoloom.expert_plugin import select_plugin_commit
    import json
    def attempt(identity, cycle, commit, *, state="CLOSED", reason="SUCCEEDED", role="critic", ended=1):
        return {"attempt_id": identity, "run_id": "run", "state": state, "terminal_reason": reason,
            "started_at": ended - .1, "ended_at": ended, "observation_json": json.dumps({
                "request": {"run_id": "run", "role": role, "component_id": "component", "command": "SELF_EVOLVE",
                    "task_context": {"key": {"cycle": cycle, "component_id": "component"}}},
                "plugin_update": {"base_commit": "a" * 40, "commit": commit}})}
    def control(action, commit, revision, cycle):
        return {"run_id": "run", "revision": revision, "command_id": action + str(revision), "created_at": 1,
            "payload_json": json.dumps({"plugin_selection": {"action": action, "role": "critic", "owner_kind": "expert",
                "commit": commit, "requested_commit": commit, "effective_cycles": {"component": cycle}}})}
    attempts = [attempt("candidate-1", 1, "b" * 40), attempt("pinned-candidate", 2, "c" * 40, ended=2)]
    pin = control("pin", "a" * 40, 10, 2)
    kwargs = dict(run_id="run", role="critic", component_id="component", default_commit="a" * 40)
    assert select_plugin_commit([pin], attempts, cycle=1, **kwargs)["commit"] == "c" * 40
    assert select_plugin_commit([pin], attempts, cycle=2, **kwargs) == {"commit": "a" * 40, "pinned": True, "source_command_id": "pin10"}
    unpin = control("unpin", "b" * 40, 11, 3)
    assert select_plugin_commit([pin, unpin], attempts, cycle=3, **kwargs)["commit"] == "b" * 40
    attempts.extend([attempt("post-unpin", 3, "d" * 40, ended=3), attempt("failed", 4, "e" * 40, reason="FAILED", ended=4),
        attempt("foreign", 4, "f" * 40, role="other", ended=5)])
    selected = select_plugin_commit([pin, unpin], attempts, cycle=4, **kwargs)
    assert selected["commit"] == "d" * 40 and selected["pinned"] is False
    reverted = control("revert", "b" * 40, 12, 4)
    assert select_plugin_commit([pin, unpin, reverted], attempts, cycle=4, **kwargs)["pinned"] is True


@pytest.mark.parametrize("action", ["pin", "revert", "unpin"])
def test_cli_forwards_explicit_plugin_control_without_local_git_writes(tmp_path, monkeypatch, action):
    from hypoloom import cli
    submitted = []
    monkeypatch.setattr(cli, "_submit", lambda args, command, payload: submitted.append((command, payload)) or 0)
    arguments = ["plugin-" + action, "--state-root", str(tmp_path / "absent"), "--run-id", "run", "--expert", "critic"]
    if action != "unpin":
        arguments.extend(["--commit", "a" * 40])
    assert cli.main(arguments) == 0
    assert submitted == [(action.upper() + "_PLUGIN", {"run_id": "run", "role": "critic", "owner_kind": "expert",
        "commit": None if action == "unpin" else "a" * 40})]
    assert not (tmp_path / "absent").exists()
