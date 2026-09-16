from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from hypoloom.artifacts import (
    ArtifactConflict,
    ArtifactDurabilityError,
    ArtifactIntegrityError,
    ArtifactKey,
    ArtifactPublisher,
    canonical_artifact_digest,
    renameat2_noreplace,
)


def key() -> ArtifactKey:
    return ArtifactKey(
        run_id="run-1",
        experiment_id="experiment-1",
        execution_id="execution-1",
        attempt_id="attempt-1",
    )


def staging(
    tmp_path: Path,
    name: str,
    measurements: tuple[float, ...] = (1.0, 1.25, 1.5),
) -> Path:
    root = tmp_path / name
    (root / "nested").mkdir(parents=True)
    (root / "result.json").write_text(
        json.dumps({"measurements": measurements}, sort_keys=True),
        encoding="utf-8",
    )
    (root / "nested" / "telemetry.txt").write_bytes(b"bounded telemetry\n")
    return root


def test_digest_is_sorted_uint64_length_framed_sha256(tmp_path: Path) -> None:
    root = tmp_path / "set"
    (root / "nested").mkdir(parents=True)
    (root / "z.txt").write_bytes(b"z")
    (root / "nested" / "a.bin").write_bytes(b"abc")
    expected = hashlib.sha256()
    for name, content in (("nested/a.bin", b"abc"), ("z.txt", b"z")):
        encoded = name.encode("utf-8")
        expected.update(struct.pack(">Q", len(encoded)))
        expected.update(encoded)
        expected.update(struct.pack(">Q", len(content)))
        expected.update(content)
    assert canonical_artifact_digest(root) == expected.hexdigest()


def test_new_publish_and_exact_replay_use_same_durability_barrier(tmp_path: Path) -> None:
    events: list[tuple[str, str]] = []

    def observed_fsync(fd: int) -> None:
        events.append(("fsync", os.readlink(f"/proc/self/fd/{fd}")))
        os.fsync(fd)

    def observed_rename(source: Path, target: Path) -> None:
        events.append(("rename", str(target)))
        renameat2_noreplace(source, target)

    publisher = ArtifactPublisher(
        tmp_path / "artifacts",
        fsync=observed_fsync,
        rename_noreplace=observed_rename,
    )
    first = publisher.publish(key(), staging(tmp_path, "stage-first"), schema_version=1)
    assert not first.reused
    assert publisher.verify(first)
    rename_index = next(index for index, event in enumerate(events) if event[0] == "rename")
    after_rename = [Path(value) for kind, value in events[rename_index + 1 :] if kind == "fsync"]
    target = Path(first.path)
    assert target / "result.json" in after_rename
    assert target / "nested" in after_rename
    assert target in after_rename
    assert target.parent in after_rename

    inode = target.stat().st_ino
    events.clear()
    replay = publisher.publish(
        key(),
        staging(tmp_path, "stage-replay"),
        schema_version=1,
        known_publication=first,
    )
    assert replay.reused
    assert replay.digest == first.digest
    assert target.stat().st_ino == inode
    replay_fsyncs = [Path(value) for kind, value in events if kind == "fsync"]
    assert target / "result.json" in replay_fsyncs
    assert target.parent in replay_fsyncs


def test_conflicting_replay_never_overwrites_stable_target(tmp_path: Path) -> None:
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    first = publisher.publish(key(), staging(tmp_path, "stage-first", (1.0, 1.25, 1.5)))
    original = (Path(first.path) / "result.json").read_bytes()
    conflict_stage = staging(tmp_path, "stage-conflict", (-10.0,))
    with pytest.raises(ArtifactConflict):
        publisher.publish(key(), conflict_stage, known_publication=first)
    assert (Path(first.path) / "result.json").read_bytes() == original
    assert conflict_stage.is_dir()


def test_schema_conflict_is_explicit_when_database_record_exists(tmp_path: Path) -> None:
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    first = publisher.publish(key(), staging(tmp_path, "stage-first"), schema_version=1)
    with pytest.raises(ArtifactConflict, match="schema"):
        publisher.publish(
            key(),
            staging(tmp_path, "stage-second"),
            schema_version=2,
            known_publication=first,
        )


def test_recovery_reuses_rename_visible_before_parent_fsync(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    target = artifact_root.joinpath(*key().as_tuple())
    injected = False

    def crash_after_rename(fd: int) -> None:
        nonlocal injected
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if target.exists() and path == target.parent and not injected:
            injected = True
            raise OSError("injected parent fsync failure")
        os.fsync(fd)

    publisher = ArtifactPublisher(artifact_root, fsync=crash_after_rename)
    original_stage = staging(tmp_path, "stage-crash")
    expected = canonical_artifact_digest(original_stage)
    with pytest.raises(ArtifactDurabilityError):
        publisher.publish(key(), original_stage)
    assert target.is_dir()
    assert injected

    recovery = ArtifactPublisher(artifact_root).publish(
        key(),
        staging(tmp_path, "stage-recovery"),
        expected_digest=expected,
    )
    assert recovery.reused
    assert recovery.digest == expected


def test_partial_or_linked_staging_is_never_published(tmp_path: Path) -> None:
    publisher = ArtifactPublisher(tmp_path / "artifacts")
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "result.json").symlink_to(tmp_path / "outside.json")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="regular"):
        publisher.publish(key(), unsafe)
    assert not publisher.path_for(key()).exists()
