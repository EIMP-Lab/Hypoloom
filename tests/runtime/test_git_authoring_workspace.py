from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

import hypoloom.git_authoring_workspace as authoring_workspace
from hypoloom.execution_workspace import GitTargetBinding
from hypoloom.git_authoring_workspace import (
    GitAuthoringIntegrityError,
    GitAuthoringPreparationError,
    GitAuthoringWorkspace,
)


def git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout


def make_target(tmp_path: Path) -> tuple[Path, GitTargetBinding]:
    target = tmp_path / "target"
    target.mkdir()
    git(target, "init", "-q")
    (target / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
    (target / "src").mkdir()
    (target / "src" / "value.txt").write_text("base\n", encoding="utf-8")
    (target / "README.md").write_text("target\n", encoding="utf-8")
    git(target, "add", ".")
    git(
        target,
        "-c",
        "user.name=Hypoloom test",
        "-c",
        "user.email=hypoloom@example.invalid",
        "commit",
        "-q",
        "-m",
        "base",
    )
    commit = git(target, "rev-parse", "HEAD").decode().strip()
    return target, GitTargetBinding(checkout=target.resolve(), commit=commit)


def make_workspace(tmp_path: Path) -> GitAuthoringWorkspace:
    state_root = tmp_path / "state"
    state_root.mkdir()
    return GitAuthoringWorkspace(state_root)


def predecessor_patch() -> bytes:
    return (
        "diff --git a/src/value.txt b/src/value.txt\n"
        "index df967b96a579e45a18b8251732d16804b2e56a55..7a445693709d240a4bbf0681f3cb19bf09f45c6f 100644\n"
        "--- a/src/value.txt\n"
        "+++ b/src/value.txt\n"
        "@@ -1 +1 @@\n"
        "-base\n"
        "+predecessor\n"
    ).encode()


def test_capture_accepts_git_deletion_of_entire_directory(tmp_path):
    _, binding = make_target(tmp_path)
    prepared = make_workspace(tmp_path).prepare(binding, "delete-directory")
    (prepared.worktree_root / "src/value.txt").unlink()
    (prepared.worktree_root / "src").rmdir()
    patch = prepared.capture_patch(max_bytes=4096, max_files=1)
    assert b"deleted file mode" in patch and b"-base" in patch


def test_deleted_path_still_rejects_a_replaced_symlink_parent(tmp_path):
    _, binding = make_target(tmp_path)
    prepared = make_workspace(tmp_path).prepare(binding, "symlink-directory")
    (prepared.worktree_root / "src/value.txt").unlink()
    (prepared.worktree_root / "src").rmdir()
    (prepared.worktree_root / "src").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(GitAuthoringIntegrityError):
        prepared.capture_patch(max_bytes=4096, max_files=2)


def test_prepare_uses_private_clone_exact_commit_and_leaves_target_unchanged(
    tmp_path: Path,
) -> None:
    target, binding = make_target(tmp_path)
    source_status = git(
        target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    source_worktrees = git(target, "worktree", "list", "--porcelain")
    source_head = git(target, "rev-parse", "HEAD")
    source_object = (
        target
        / ".git"
        / "objects"
        / git(target, "rev-parse", "HEAD^{tree}").decode().strip()[:2]
        / git(target, "rev-parse", "HEAD^{tree}").decode().strip()[2:]
    )

    prepared = make_workspace(tmp_path).prepare(binding, "attempt-1")

    assert prepared.worktree_root.is_dir()
    assert (
        git(prepared.worktree_root, "rev-parse", "HEAD").decode().strip()
        == binding.commit
    )
    assert git(prepared.worktree_root, "rev-parse", "--abbrev-ref", "HEAD") == b"HEAD\n"
    assert git(prepared.repository_root, "remote") == b""
    assert (
        git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        == source_status
    )
    assert git(target, "worktree", "list", "--porcelain") == source_worktrees
    assert git(target, "rev-parse", "HEAD") == source_head
    assert not (target / ".git" / "worktrees").exists()
    private_object = (
        prepared.repository_root
        / "objects"
        / source_object.parent.name
        / source_object.name
    )
    assert source_object.is_file() and private_object.is_file()
    assert source_object.stat().st_ino != private_object.stat().st_ino
    assert private_object.stat().st_nlink == 1
    dot_git = (prepared.worktree_root / ".git").read_text(encoding="utf-8")
    assert dot_git == (
        f"gitdir: {prepared.repository_root / 'worktrees' / 'worktree'}\n"
    )


def test_source_checkout_may_become_dirty_without_changing_pinned_clone_or_patch(
    tmp_path: Path,
) -> None:
    target, binding = make_target(tmp_path)
    (target / "src" / "value.txt").write_text("operator edit\n", encoding="utf-8")
    (target / "untracked.txt").write_text("operator only\n", encoding="utf-8")
    dirty_before = git(
        target, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )

    prepared = make_workspace(tmp_path).prepare(binding, "dirty-source")
    assert (prepared.worktree_root / "src" / "value.txt").read_text() == "base\n"
    (prepared.worktree_root / "src" / "value.txt").write_text(
        "candidate\n", encoding="utf-8"
    )
    patch = prepared.capture_patch(max_bytes=4096, max_files=1)

    assert b"-base\n+candidate\n" in patch
    assert b"operator edit" not in patch
    assert (
        git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        == dirty_before
    )


def test_predecessor_patch_is_the_authoring_base_and_capture_is_cumulative(
    tmp_path: Path,
) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    prepared = workspace.prepare(binding, "successor", predecessor_patch())
    assert (prepared.worktree_root / "src" / "value.txt").read_text() == "predecessor\n"

    (prepared.worktree_root / "src" / "value.txt").write_text(
        "successor\n", encoding="utf-8"
    )
    patch = prepared.capture_patch(max_bytes=8192, max_files=1)
    assert b"-base\n+successor\n" in patch
    assert b"predecessor" not in patch

    replay = workspace.prepare(binding, "replay")
    git(replay.worktree_root, "apply", "--check", "-", input_bytes=patch)
    git(replay.worktree_root, "apply", "-", input_bytes=patch)
    assert (replay.worktree_root / "src" / "value.txt").read_text() == "successor\n"


def test_capture_includes_untracked_binary_content(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    prepared = make_workspace(tmp_path).prepare(binding, "binary")
    payload = bytes(range(256)) * 4
    (prepared.worktree_root / "payload.bin").write_bytes(payload)

    patch = prepared.capture_patch(max_bytes=8192, max_files=1)

    assert b"diff --git a/payload.bin b/payload.bin" in patch
    assert b"GIT binary patch" in patch
    assert b"literal 1024" in patch
    assert git(prepared.worktree_root, "diff", "--cached", "--name-only") == b""


def test_combined_tracked_deletion_and_untracked_file_patch_replays(
    tmp_path: Path,
) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    prepared = workspace.prepare(binding, "combined")
    (prepared.worktree_root / "README.md").unlink()
    (prepared.worktree_root / "src" / "new.txt").write_text(
        "new candidate\n", encoding="utf-8"
    )

    patch = prepared.capture_patch(max_bytes=8192, max_files=2)
    replay = workspace.prepare(binding, "combined-replay")
    git(replay.worktree_root, "apply", "--check", "-", input_bytes=patch)
    git(replay.worktree_root, "apply", "-", input_bytes=patch)

    assert not (replay.worktree_root / "README.md").exists()
    assert (replay.worktree_root / "src" / "new.txt").read_text() == "new candidate\n"


def test_two_attempts_are_isolated_and_cleanup_is_attempt_local(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    first = workspace.prepare(binding, "first")
    second = workspace.prepare(binding, "second")
    (first.worktree_root / "one.txt").write_text("one\n", encoding="utf-8")
    (second.worktree_root / "two.txt").write_text("two\n", encoding="utf-8")

    assert b"one.txt" in first.capture_patch(max_bytes=4096, max_files=1)
    assert b"two.txt" in second.capture_patch(max_bytes=4096, max_files=1)
    assert not (first.worktree_root / "two.txt").exists()
    assert not (second.worktree_root / "one.txt").exists()

    first.cleanup()
    first.cleanup()
    assert not first.attempt_root.exists()
    assert second.attempt_root.is_dir()


def test_workspace_cleanup_attempt_is_exact_idempotent_and_does_not_create_root(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    assert not workspace.root.exists()

    workspace.cleanup_attempt("absent")
    assert not workspace.root.exists()

    _, binding = make_target(tmp_path)
    first = workspace.prepare(binding, "first")
    second = workspace.prepare(binding, "second")
    workspace.cleanup_attempt("first")
    workspace.cleanup_attempt("first")

    assert not first.attempt_root.exists()
    assert second.attempt_root.is_dir()
    with pytest.raises(ValueError, match="safe path component"):
        workspace.cleanup_attempt("../second")
    assert second.attempt_root.is_dir()


def test_duplicate_attempt_and_missing_or_nonexact_commit_are_rejected(
    tmp_path: Path,
) -> None:
    target, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    prepared = workspace.prepare(binding, "same")
    with pytest.raises(GitAuthoringPreparationError, match="already exists"):
        workspace.prepare(binding, "same")
    assert prepared.worktree_root.is_dir()

    missing = GitTargetBinding(checkout=target.resolve(), commit="0" * 40)
    with pytest.raises(GitAuthoringPreparationError):
        workspace.prepare(missing, "missing")
    assert not (workspace.root / "missing").exists()

    abbreviated = GitTargetBinding(
        checkout=target.resolve(), commit=binding.commit[:12]
    )
    with pytest.raises(GitAuthoringPreparationError, match="exact object ID"):
        workspace.prepare(abbreviated, "abbreviated")
    assert not (workspace.root / "abbreviated").exists()


def test_capture_rejects_unchanged_worktree_and_changed_symlink_or_hardlinked_files(
    tmp_path: Path,
) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    empty = workspace.prepare(binding, "empty")
    with pytest.raises(GitAuthoringIntegrityError, match="no changes"):
        empty.capture_patch(max_bytes=4096, max_files=1)

    symlinked = workspace.prepare(binding, "symlink")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (symlinked.worktree_root / "linked.txt").symlink_to(outside)
    with pytest.raises(GitAuthoringIntegrityError, match="regular single-link"):
        symlinked.capture_patch(max_bytes=4096, max_files=1)

    hardlinked = workspace.prepare(binding, "hardlink")
    first = hardlinked.worktree_root / "first.txt"
    first.write_text("linked\n", encoding="utf-8")
    os.link(first, hardlinked.worktree_root / "second.txt")
    with pytest.raises(GitAuthoringIntegrityError, match="regular single-link"):
        hardlinked.capture_patch(max_bytes=4096, max_files=2)


def test_capture_and_replay_empty_untracked_file(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    prepared = workspace.prepare(binding, "empty-untracked")
    (prepared.worktree_root / "empty.txt").touch()

    patch = prepared.capture_patch(max_bytes=4096, max_files=1)
    replay = workspace.prepare(binding, "empty-untracked-replay")
    git(replay.worktree_root, "apply", "--check", "-", input_bytes=patch)
    git(replay.worktree_root, "apply", "-", input_bytes=patch)

    assert (replay.worktree_root / "empty.txt").is_file()
    assert (replay.worktree_root / "empty.txt").stat().st_size == 0


def test_capture_enforces_file_and_byte_bounds(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    too_many = workspace.prepare(binding, "too-many")
    (too_many.worktree_root / "one.txt").write_text("one\n", encoding="utf-8")
    (too_many.worktree_root / "two.txt").write_text("two\n", encoding="utf-8")
    with pytest.raises(GitAuthoringIntegrityError, match="changed-file bound"):
        too_many.capture_patch(max_bytes=4096, max_files=1)

    too_large = workspace.prepare(binding, "too-large")
    (too_large.worktree_root / "large.bin").write_bytes(os.urandom(2 * 1024 * 1024))
    with pytest.raises(GitAuthoringIntegrityError, match="byte bound"):
        too_large.capture_patch(max_bytes=128, max_files=1)


def test_capture_rejects_changed_or_per_worktree_git_configuration(
    tmp_path: Path,
) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    changed = workspace.prepare(binding, "changed-config")
    config = changed.repository_root / "config"
    config.write_bytes(config.read_bytes() + b"\n[core]\n\tworktree = /tmp\n")
    (changed.worktree_root / "change.txt").write_text("change\n", encoding="utf-8")
    with pytest.raises(GitAuthoringIntegrityError, match="configuration changed"):
        changed.capture_patch(max_bytes=4096, max_files=1)

    per_worktree = workspace.prepare(binding, "worktree-config")
    admin = per_worktree.repository_root / "worktrees" / "worktree"
    (admin / "config.worktree").write_text(
        "[core]\n\tworktree = /tmp\n", encoding="utf-8"
    )
    (per_worktree.worktree_root / "change.txt").write_text("change\n", encoding="utf-8")
    with pytest.raises(GitAuthoringIntegrityError, match="configuration is forbidden"):
        per_worktree.capture_patch(max_bytes=4096, max_files=1)


def test_capture_rejects_git_metadata_escape(tmp_path: Path) -> None:
    _, binding = make_target(tmp_path)
    workspace = make_workspace(tmp_path)
    prepared = workspace.prepare(binding, "metadata-escape")
    outside = tmp_path / "outside-admin"
    outside.mkdir()
    (prepared.worktree_root / ".git").write_text(
        f"gitdir: {outside}\n", encoding="utf-8"
    )
    (prepared.worktree_root / "change.txt").write_text("change\n", encoding="utf-8")

    with pytest.raises(GitAuthoringIntegrityError, match="private repository"):
        prepared.capture_patch(max_bytes=4096, max_files=1)

    common_escape = workspace.prepare(binding, "common-escape")
    outside_common = tmp_path / "outside-common"
    outside_common.mkdir()
    admin = common_escape.repository_root / "worktrees" / "worktree"
    (admin / "commondir").write_text(f"{outside_common}\n", encoding="utf-8")
    (common_escape.worktree_root / "change.txt").write_text(
        "change\n", encoding="utf-8"
    )
    with pytest.raises(GitAuthoringIntegrityError, match="common directory escaped"):
        common_escape.capture_patch(max_bytes=4096, max_files=1)


def test_bounded_git_cleanup_kills_child_after_leader_exit(tmp_path: Path) -> None:
    fake_git = tmp_path / "fake-git"
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    fake_git.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "root = sys.argv[-1]\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    with open(os.path.join(root, 'pid'), 'w') as stream:\n"
        "        stream.write(str(os.getpid()))\n"
        "    os.close(1)\n"
        "    os.close(2)\n"
        "    with open(os.path.join(root, 'heartbeat'), 'ab', buffering=0) as stream:\n"
        "        while True:\n"
        "            stream.write(b'x')\n"
        "            time.sleep(0.02)\n"
        "while not os.path.exists(os.path.join(root, 'heartbeat')):\n"
        "    time.sleep(0.005)\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)

    output = authoring_workspace._run_git(
        str(fake_git),
        (str(child_dir),),
        timeout_seconds=1,
        max_output_bytes=128,
    )
    assert output == b""

    child_pid = int((child_dir / "pid").read_text())
    heartbeat = child_dir / "heartbeat"
    size_after_cleanup = heartbeat.stat().st_size
    time.sleep(0.15)
    try:
        assert heartbeat.stat().st_size == size_after_cleanup
        stat_path = Path("/proc") / str(child_pid) / "stat"
        if stat_path.exists():
            assert stat_path.read_text().split()[2] == "Z"
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_prepare_rejects_checkout_inside_state_root(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    target, binding = make_target(state_root)
    workspace = GitAuthoringWorkspace(state_root)

    with pytest.raises(GitAuthoringPreparationError, match="must be disjoint"):
        workspace.prepare(binding, "recursive")

    assert not (workspace.root / "recursive").exists()


def test_cleanup_refuses_any_path_other_than_the_exact_attempt_root(
    tmp_path: Path,
) -> None:
    _, binding = make_target(tmp_path)
    prepared = make_workspace(tmp_path).prepare(binding, "confined")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep\n", encoding="utf-8")

    forged = replace(prepared, attempt_root=outside)
    with pytest.raises(GitAuthoringIntegrityError, match="exact Attempt root"):
        forged.cleanup()

    assert sentinel.read_text() == "keep\n"
    assert prepared.attempt_root.is_dir()
    prepared.cleanup()
    assert not prepared.attempt_root.exists()
    assert sentinel.read_text() == "keep\n"
