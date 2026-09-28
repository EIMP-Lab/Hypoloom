from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.git_authoring_workspace import GitAuthoringWorkspaceError


_LIMIT = 131072
_SKILL = "skills/experiment-report/SKILL.md"


@pytest.mark.parametrize("kind", ("unknown", None, {}, []))
def test_plugin_owner_namespace_is_explicit_and_typed(tmp_path: Path, kind):
    with pytest.raises(ValueError, match="owner_kind"):
        ExpertPluginStore(tmp_path, owner_kind=kind)


def test_grounding_namespace_has_its_own_seed_and_never_aliases_same_named_expert(tmp_path: Path):
    experts = ExpertPluginStore(tmp_path)
    expert = experts.ensure("run", "grounding", "User expert focus")
    grounding = ExpertPluginStore(tmp_path, owner_kind="grounding")
    manager = grounding.ensure("run", "grounding", "Global management focus")
    assert set(expert.to_dict()) == {"run_id", "role", "name", "commit"}
    assert manager.to_dict()["owner_kind"] == "grounding"
    assert (tmp_path / "run/grounding/plugin/.git").is_dir()
    assert (tmp_path / "run/experts/grounding/plugin/.git").is_dir()
    view = grounding.read("run", "grounding", commit=manager.commit, attempt_id="manager", max_bytes=_LIMIT)
    try:
        assert {path.parent.name for path in view.root.glob("skills/*/SKILL.md")} == {
            "leader-trigger", "debate-trigger", "debate-judge", "graph-evolve", "final-report", "self-evolve", "research-workflow"}
        assert "Global management focus" in (view.root / "skills/leader-trigger/SKILL.md").read_text()
        assert not (view.root / _SKILL).exists()
        update = grounding.prepare_update("run", "grounding", commit=manager.commit,
            attempt_id="update-1", command_id="same-logical-update")
        candidate = update.root / "skills/leader-trigger/SKILL.md"
        candidate.write_text(candidate.read_text() + "\nOne observed trigger lesson.\n")
        accepted = grounding.finish_update(update, max_bytes=_LIMIT)
        assert accepted.owner_kind == "grounding" and accepted.commit != manager.commit
        assert experts.ensure("run", "grounding", "ignored") == expert
        assert (view.root / "skills/leader-trigger/SKILL.md").read_bytes() != candidate.read_bytes()
        with pytest.raises(ValueError, match="namespace"):
            experts.finish_update(update, max_bytes=_LIMIT)
        broken = grounding.prepare_update("run", "grounding", commit=accepted.commit,
            attempt_id="update-2", command_id="second-logical-update")
        (broken.root / "skills/graph-evolve/SKILL.md").unlink()
        with pytest.raises(ValueError, match="entrypoints"):
            grounding.finish_update(broken, max_bytes=_LIMIT)
        assert grounding.ensure("run", "grounding", "ignored") == accepted
    finally:
        view.close()
    with pytest.raises(ValueError, match="owner_kind"):
        ExpertPluginStore(tmp_path, owner_kind="unknown")


def test_seed_and_existing_plugin_obey_admitted_byte_budget(tmp_path: Path) -> None:
    store = ExpertPluginStore(tmp_path)
    with pytest.raises(ValueError, match="budget"):
        store.ensure("run", "alpha", "focus", max_bytes=1)
    assert not (tmp_path / "run/experts/alpha/plugin").exists()
    with pytest.raises(ValueError, match="expanded plugin seed"):
        store.ensure("run", "alpha", "x" * _LIMIT, max_bytes=_LIMIT)
    assert not (tmp_path / "run/experts/alpha/plugin").exists()
    accepted = store.ensure("run", "alpha", "focus", max_bytes=_LIMIT)
    with pytest.raises(ValueError, match="budget"):
        store.ensure("run", "alpha", "ignored", max_bytes=1)
    assert store.ensure("run", "alpha", "ignored", max_bytes=_LIMIT) == accepted


def test_three_role_seeds_and_fixed_git_snapshots_survive_updates(
    tmp_path: Path,
) -> None:
    store = ExpertPluginStore(tmp_path)
    bindings = {
        role: store.ensure("run", role, f"Distinct {role} scientific focus")
        for role in ("alpha", "beta", "gamma")
    }
    views = {
        role: store.read(
            "run",
            role,
            commit=binding.commit,
            attempt_id="read-" + role,
            max_bytes=_LIMIT,
        )
        for role, binding in bindings.items()
    }
    try:
        old = (views["alpha"].root / _SKILL).read_bytes()
        for role, view in views.items():
            content = (view.root / _SKILL).read_text()
            assert f"Distinct {role} scientific focus" in content
            assert not (view.root / ".git").exists()
            assert set(view.binding.to_dict()) == {"run_id", "role", "name", "commit"}
            assert (
                store.ensure("run", role, "do not overwrite an existing plugin").commit
                == bindings[role].commit
            )
        update = store.prepare_update(
            "run", "alpha", commit=bindings["alpha"].commit, attempt_id="evolve-1"
        )
        (update.root / _SKILL).write_bytes(old + b"\nNew repair lesson.\n")
        accepted = store.finish_update(update, max_bytes=_LIMIT)
        assert accepted.commit != bindings["alpha"].commit
        assert (views["alpha"].root / _SKILL).read_bytes() == old
        same = store.finish_update(update, max_bytes=_LIMIT)
        assert same == accepted
        for commit, expected in (
            (bindings["alpha"].commit, old),
            (accepted.commit, old + b"\nNew repair lesson.\n"),
        ):
            view = store.read(
                "run", "alpha", commit=commit, attempt_id="later", max_bytes=_LIMIT
            )
            try:
                assert (view.root / _SKILL).read_bytes() == expected
            finally:
                view.close()
        assert store.ensure("run", "beta", "ignored").commit == bindings["beta"].commit
        assert update.root.is_dir(), "update context is retained for recovery"
    finally:
        for view in views.values():
            view.close()
            view.close()
            assert not view.root.exists()
    assert (tmp_path / "run/experts/alpha/plugin/.git").is_dir()


def test_interrupted_update_keeps_bytes_and_rejects_stale_base(tmp_path: Path) -> None:
    store = ExpertPluginStore(tmp_path)
    base = store.ensure("run", "alpha", "focus")
    first = store.prepare_update("run", "alpha", commit=base.commit, attempt_id="first")
    pending = store.prepare_update(
        "run", "alpha", commit=base.commit, attempt_id="pending"
    )
    text = (first.root / _SKILL).read_text() + "\nObserved improvement.\n"
    (first.root / _SKILL).write_text(text)
    recovered = ExpertPluginStore(tmp_path).prepare_update(
        "run", "alpha", commit=base.commit, attempt_id="first"
    )
    assert recovered.root == first.root
    assert (recovered.root / _SKILL).read_text() == text
    accepted = store.finish_update(recovered, max_bytes=_LIMIT)
    (pending.root / _SKILL).write_text(text + "Another pending change.\n")
    with pytest.raises(ValueError, match="superseded"):
        store.finish_update(pending, max_bytes=_LIMIT)
    assert store.ensure("run", "alpha", "ignored").commit == accepted.commit


def test_logical_command_update_recovers_between_git_commit_and_acceptance(tmp_path: Path, monkeypatch) -> None:
    store = ExpertPluginStore(tmp_path)
    base = store.ensure("run", "alpha", "focus")
    command_id = "self-evolve-logical-" + "x" * 300
    update = store.prepare_update("run", "alpha", commit=base.commit,
        attempt_id="attempt-1", command_id=command_id)
    changed = (update.root / _SKILL).read_bytes() + b"\nObserved repair lesson.\n"
    (update.root / _SKILL).write_bytes(changed)
    original_git = store._git
    def interrupted(root, *args, **kwargs):
        if args[:1] == ("merge",):
            raise OSError("simulated interruption after commit before acceptance")
        return original_git(root, *args, **kwargs)
    monkeypatch.setattr(store, "_git", interrupted)
    with pytest.raises(OSError, match="after commit"):
        store.finish_update(update, max_bytes=_LIMIT)
    committed = original_git(update.root, "rev-parse", "HEAD").decode().strip()
    assert committed != base.commit
    assert original_git(update.repository_root, "rev-parse", "HEAD").decode().strip() == base.commit
    recovered_store = ExpertPluginStore(tmp_path)
    recovered = recovered_store.prepare_update("run", "alpha", commit=base.commit,
        attempt_id="attempt-2", command_id=command_id)
    assert recovered.root == update.root
    assert (recovered.root / _SKILL).read_bytes() == changed
    accepted = recovered_store.finish_update(recovered, max_bytes=_LIMIT)
    assert accepted.commit == committed
    assert recovered_store.finish_update(recovered, max_bytes=_LIMIT) == accepted
    assert recovered_store._git(update.repository_root, "rev-list", "--count", "HEAD").strip() == b"2"
    message = recovered_store._git(update.root, "log", "-1", "--format=%B").decode()
    assert command_id in message and "attempt-2" not in message
    with pytest.raises(ValueError, match="logical command.*base"):
        recovered_store.prepare_update("run", "alpha", commit=accepted.commit,
            attempt_id="attempt-3", command_id=command_id)


@pytest.mark.parametrize(
    "bad_change",
    ("manifest", "frontmatter", "missing", "outside", "symlink", "hardlink", "budget"),
)
def test_invalid_update_never_moves_accepted_plugin(
    tmp_path: Path, bad_change: str
) -> None:
    store = ExpertPluginStore(tmp_path)
    binding = store.ensure("run", "alpha", "focus")
    update = store.prepare_update(
        "run", "alpha", commit=binding.commit, attempt_id="bad"
    )
    if bad_change == "manifest":
        manifest = update.root / ".claude-plugin/plugin.json"
        document = json.loads(manifest.read_text())
        document["hooks"] = {"Stop": "not an admitted component"}
        manifest.write_text(json.dumps(document))
    elif bad_change == "frontmatter":
        (update.root / _SKILL).write_text("not a native skill\n")
    elif bad_change == "missing":
        (update.root / _SKILL).unlink()
    elif bad_change == "outside":
        (update.root / "CLAUDE.md").write_text("not in the plugin skill scope\n")
    elif bad_change == "symlink":
        (update.root / "skills/experiment-report/linked").symlink_to(tmp_path)
    elif bad_change == "hardlink":
        os.link(update.root / _SKILL, update.root / "skills/experiment-report/linked")
    limit = 1 if bad_change == "budget" else _LIMIT
    with pytest.raises((ValueError, yaml.YAMLError)):
        store.finish_update(update, max_bytes=limit)
    assert store.ensure("run", "alpha", "ignored").commit == binding.commit


def test_new_native_skill_assets_and_explicit_commit_validation(tmp_path: Path) -> None:
    store = ExpertPluginStore(tmp_path)
    binding = store.ensure("run", "alpha", "focus")
    foreign = store.ensure("run", "beta", "other focus")
    for commit in ("HEAD", binding.commit[:8], foreign.commit):
        with pytest.raises((ValueError, GitAuthoringWorkspaceError)):
            store.read(
                "run", "alpha", commit=commit, attempt_id="read", max_bytes=_LIMIT
            )
    with pytest.raises((ValueError, GitAuthoringWorkspaceError)):
        store.read(
            "run", "alpha", commit=binding.commit, attempt_id="small", max_bytes=1
        )
    update = store.prepare_update(
        "run", "alpha", commit=binding.commit, attempt_id="new-skill"
    )
    new = update.root / "skills/check-statistics"
    new.mkdir()
    (new / "SKILL.md").write_text(
        "---\nname: check-statistics\ndescription: Check statistics.\n---\nUse check.py.\n"
    )
    script = new / "check.py"
    script.write_text("#!/usr/bin/python3\nassert 1 + 1 == 2\n")
    script.chmod(0o755)
    accepted = store.finish_update(update, max_bytes=_LIMIT)
    assert accepted.commit != binding.commit
    view = store.read(
        "run", "alpha", commit=accepted.commit, attempt_id="script", max_bytes=_LIMIT
    )
    try:
        assert (view.root / "skills/check-statistics/check.py").stat().st_mode & 0o111
    finally:
        view.close()
    with pytest.raises(ValueError, match="safe path"):
        store.ensure("../elsewhere", "alpha", "focus")
