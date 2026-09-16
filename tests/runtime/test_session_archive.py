from __future__ import annotations

import io
import tarfile

import pytest
import yaml

from hypoloom import session_archive as archive
from hypoloom.artifacts import AgentArtifactKey, ArtifactPublisher
from hypoloom.control_protocol import ControlRequest
from hypoloom.git_authoring_workspace import GitAuthoringWorkspace
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost
from test_git_authoring_workspace import make_target, git
from test_observer_services import _cancel_fresh_fixture
from test_profile_admission import _materialize_expert_loop_pilot_v3
from test_profile_v4 import documents


def fixture(tmp_path, *, retention="archive_on_close", terminal=True):
    profile, target, data = _materialize_expert_loop_pilot_v3(tmp_path)
    source = documents()
    source["methodology"]["runtime"]["target_repo"] = str(target)
    source["methodology"]["memory"] = {"retention": retention}
    source["safety"]["allow_read_paths"] = [str(data)]
    for name, value in source.items():
        (profile / (name + ".yaml")).write_text(yaml.safe_dump(value))
    report = admit_profile(profile, run_id="archive-run")
    assert report.admitted, report.to_dict()
    root = tmp_path / "state"
    host = WorkflowHost(root)
    host.initialize()
    response = host.handle(ControlRequest(schema_version=1, request_id="create-archive-run", expected_revision=0,
        command="CREATE_RUN", payload=report.create_run_payload))
    assert response.status == "accepted", response
    if terminal:
        _cancel_fresh_fixture(host, "archive-run")
    host.close()
    return root


def source_tree(tmp_path, root):
    _, binding = make_target(tmp_path)
    scope = root / "archive-run/sessions/critic/c1/component/chain-a"
    source = scope / "source"
    source.mkdir(parents=True)
    prepared = GitAuthoringWorkspace(source).prepare(binding, "chain")
    (prepared.worktree_root / "src/value.txt").write_text("actual authoring history\n")
    git(prepared.worktree_root, "add", "--all")
    git(prepared.worktree_root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "closed node")
    commit = git(prepared.worktree_root, "rev-parse", "HEAD")
    projects = scope / "projects"
    projects.mkdir()
    (projects / "session.jsonl").write_bytes(b'{"sessionId":"fixture","message":"closed bytes"}\n')
    ground = root / "archive-run/grounding/sessions/manager"
    ground.mkdir(parents=True)
    (ground / "context.txt").write_bytes(b"manager history")
    publisher = ArtifactPublisher(root / "artifacts")
    staging = tmp_path / "canonical-stage"
    staging.mkdir()
    (staging / "transcript.jsonl").write_bytes((projects / "session.jsonl").read_bytes())
    publication = publisher.publish(AgentArtifactKey(run_id="archive-run", attempt_id="a1"), staging, ("transcript.jsonl",))
    return prepared, commit, publisher, publication


def test_terminal_archive_is_recoverable_before_hot_trees_are_removed(tmp_path):
    from hypoloom.expert_plugin import ExpertPluginStore
    from hypoloom.expert_memory import ExpertMemory
    root = fixture(tmp_path)
    prepared, commit, publisher, publication = source_tree(tmp_path, root)
    plugins = ExpertPluginStore(root)
    plugin = plugins.ensure("archive-run", "critic", "retained plugin")
    memory = ExpertMemory(root)
    memory_commit = memory.ensure("archive-run", "critic")
    original = {str(path.relative_to(root / "archive-run")): path.read_bytes()
        for parent in (root / "archive-run/sessions", root / "archive-run/grounding/sessions")
        for path in parent.rglob("*") if path.is_file()}
    path = archive.archive_run_sessions(root, "archive-run")
    assert path == root / "archive-run/archives/sessions.tar.gz"
    assert not (root / "archive-run/sessions").exists()
    assert not (root / "archive-run/grounding/sessions").exists()
    assert publisher.verify(publication)
    assert plugins.ensure("archive-run", "critic", "unchanged") == plugin
    assert memory.ensure("archive-run", "critic") == memory_commit
    listed = archive.inspect_session_archive(root, "archive-run")
    assert {item["name"] for item in listed if item["type"] == "file"} == set(original)
    name = next(name for name in original if name.endswith("session.jsonl"))
    assert archive.read_session_archive_file(root, "archive-run", name) == original[name]
    assert not (root / "archive-run/sessions").exists(), "browsing is read-only"
    assert archive.archive_run_sessions(root, "archive-run") == path
    restored = archive.restore_run_sessions(root, "archive-run")
    assert set(restored) == {root / "archive-run/sessions", root / "archive-run/grounding/sessions"}
    assert git(prepared.worktree_root, "rev-parse", "HEAD") == commit
    assert {name: (root / "archive-run" / name).read_bytes() for name in original} == original
    assert archive.restore_run_sessions(root, "archive-run") == restored
    assert path.is_file() and publisher.verify(publication)


def test_default_retain_and_nonterminal_safety_do_not_touch_session_history(tmp_path):
    root = fixture(tmp_path, retention="retain", terminal=False)
    prepared, commit, _, _ = source_tree(tmp_path, root)
    assert archive.archive_run_sessions(root, "archive-run") is None
    assert git(prepared.worktree_root, "rev-parse", "HEAD") == commit
    assert not (root / "archive-run/archives").exists()


def test_archive_selection_does_not_authorize_archiving_a_live_run(tmp_path):
    root = fixture(tmp_path, terminal=False)
    source_tree(tmp_path, root)
    with pytest.raises(ValueError, match="terminal"):
        archive.archive_run_sessions(root, "archive-run")
    assert (root / "archive-run/sessions").is_dir()


def test_failed_publication_preserves_original_history(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    prepared, commit, _, _ = source_tree(tmp_path, root)
    monkeypatch.setattr(archive.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("injected publish failure")))
    with pytest.raises(OSError, match="injected"):
        archive.archive_run_sessions(root, "archive-run")
    assert git(prepared.worktree_root, "rev-parse", "HEAD") == commit
    assert not (root / "archive-run/archives/sessions.tar.gz").exists()


def test_interrupted_hot_tree_removal_can_restore_from_already_published_archive(tmp_path, monkeypatch):
    root = fixture(tmp_path)
    prepared, commit, _, _ = source_tree(tmp_path, root)
    remove = archive.shutil.rmtree
    def interrupted(path, *args, **kwargs):
        if path == root / "archive-run/grounding/sessions":
            raise OSError("interrupted second removal")
        return remove(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(archive.shutil, "rmtree", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            archive.archive_run_sessions(root, "archive-run")
    assert (root / "archive-run/archives/sessions.tar.gz").is_file()
    assert not (root / "archive-run/sessions").exists()
    archive.restore_run_sessions(root, "archive-run")
    assert git(prepared.worktree_root, "rev-parse", "HEAD") == commit


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_restore_refuses_link_members_before_extracting_anything(tmp_path, kind):
    root = fixture(tmp_path)
    directory = root / "archive-run/archives"
    directory.mkdir(parents=True)
    with tarfile.open(directory / "sessions.tar.gz", "w:gz") as handle:
        item = tarfile.TarInfo("sessions/link")
        item.type, item.linkname = kind, "/tmp/outside"
        handle.addfile(item)
    with pytest.raises(ValueError, match="member"):
        archive.restore_run_sessions(root, "archive-run")
    assert not (root / "archive-run/sessions").exists()


@pytest.mark.parametrize("member", ["../escape", "/tmp/escape", "experts/critic/memory/MEMORY.md", "sessions/../../escape"])
def test_browser_and_restore_reject_unsafe_archive_members(tmp_path, member):
    root = fixture(tmp_path)
    directory = root / "archive-run/archives"
    directory.mkdir(parents=True)
    with tarfile.open(directory / "sessions.tar.gz", "w:gz") as handle:
        info = tarfile.TarInfo(member)
        info.size = 1
        handle.addfile(info, io.BytesIO(b"x"))
    for action in (archive.inspect_session_archive, archive.restore_run_sessions):
        with pytest.raises(ValueError, match="member"):
            action(root, "archive-run")
    assert not (root / "archive-run/sessions").exists()


def test_restore_refuses_to_overwrite_changed_existing_context(tmp_path):
    root = fixture(tmp_path)
    source_tree(tmp_path, root)
    path = archive.archive_run_sessions(root, "archive-run")
    archive.restore_run_sessions(root, "archive-run")
    changed = root / "archive-run/grounding/sessions/manager/context.txt"
    changed.write_text("new local work")
    with pytest.raises(ValueError, match="differ"):
        archive.restore_run_sessions(root, "archive-run")
    assert changed.read_text() == "new local work" and path.is_file()


def test_cli_archive_inventory_is_readonly_and_explicit_restore_does_not_resume_run(tmp_path, capsys):
    import json
    from hypoloom import cli
    from hypoloom.state_store import StateStore
    root = fixture(tmp_path)
    source_tree(tmp_path, root)
    archive.archive_run_sessions(root, "archive-run")
    before = StateStore(root).inspect("SELECT * FROM workflow_transitions")
    assert cli.main(["session-archive", "--state-root", str(root), "--run-id", "archive-run"]) == 0
    assert json.loads(capsys.readouterr().out)["members"]
    assert not (root / "archive-run/sessions").exists()
    assert cli.main(["session-restore", "--state-root", str(root), "--run-id", "archive-run"]) == 0
    assert json.loads(capsys.readouterr().out)["run_resumed"] is False
    assert (root / "archive-run/sessions").is_dir()
    assert StateStore(root).inspect("SELECT * FROM workflow_transitions") == before
