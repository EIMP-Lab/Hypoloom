"""Quiesced fresh-root backup and restore helpers for Hypoloom state roots."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .artifacts import ArtifactDurabilityError, ArtifactIntegrityError, inventory_relative_paths, renameat2_noreplace


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_CHUNK_SIZE = 1024 * 1024

QuiescentAssertion = Callable[[Path, str], None]
BackupAudit = Callable[[Path], None]
FaultHook = Callable[[str, Path], None]


class BackupRestoreError(RuntimeError):
    pass


class BackupConflict(BackupRestoreError):
    pass


class BackupIntegrityError(BackupRestoreError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedBackup:
    path: Path
    reused: bool


def create_quiesced_backup(
    source_state_root: str | os.PathLike[str],
    backup_root: str | os.PathLike[str],
    backup_id: str,
    *,
    assert_quiescent: QuiescentAssertion,
    audit: BackupAudit,
    fault_hook: FaultHook | None = None,
) -> PublishedBackup:
    source_root = _real_directory(source_state_root, "source_state_root")
    _safe_id(backup_id, "backup_id")
    proposed_target = (Path(backup_root) / backup_id).resolve(strict=False)
    if (
        proposed_target == source_root
        or proposed_target.is_relative_to(source_root)
        or source_root.is_relative_to(proposed_target)
    ):
        raise BackupConflict("backup target and source_state_root must not overlap")
    backup_base = _ensure_directory(backup_root)
    target = backup_base / backup_id
    if os.path.lexists(target):
        if target.is_symlink() or not target.is_dir():
            raise BackupConflict(f"backup target already exists and is not a real directory: {target}")
        _verify_backup_root(target)
        _sqlite_integrity(target / "state.sqlite")
        audit(target)
        _prepare_standalone_database(target / "state.sqlite")
        _fsync_backup_root(target)
        _fsync_directory(target.parent)
        return PublishedBackup(path=target, reused=True)
    source_db = _real_regular_file(source_root / "state.sqlite", "state.sqlite")
    temp_root = _temp_sibling(backup_base, backup_id)
    assert_quiescent(source_root, "start")
    try:
        _create_root(temp_root)
        _online_backup(source_db, temp_root / "state.sqlite")
        os.chmod(temp_root / "state.sqlite", 0o600)
        assert_quiescent(source_root, "after_db_backup")
        _maybe_fault(fault_hook, "after_db_backup", temp_root)
        _copy_artifacts(source_root / "artifacts", temp_root / "artifacts")
        _copy_contexts(source_root, temp_root, standalone=False)
        assert_quiescent(source_root, "after_artifact_copy")
        _maybe_fault(fault_hook, "after_artifact_copy", temp_root)
        _verify_backup_root(temp_root)
        _sqlite_integrity(temp_root / "state.sqlite")
        audit(temp_root)
        _prepare_standalone_database(temp_root / "state.sqlite")
        _fsync_backup_root(temp_root)
        _assert_same_filesystem(temp_root, target.parent)
        _maybe_fault(fault_hook, "before_rename", temp_root)
        renameat2_noreplace(temp_root, target)
        _maybe_fault(fault_hook, "after_rename", target)
        _fsync_directory(target.parent)
        return PublishedBackup(path=target, reused=False)
    except BaseException:
        if os.path.lexists(temp_root):
            _remove_tree(temp_root)
        raise


def restore_backup(
    backup_path: str | os.PathLike[str],
    target_state_root: str | os.PathLike[str],
    *,
    audit: BackupAudit,
    fault_hook: FaultHook | None = None,
    claude_settings: str | os.PathLike[str] | None = None,
) -> Path:
    source_root = _real_directory(backup_path, "backup_path")
    _verify_backup_root(source_root)
    requested_target = Path(target_state_root)
    if os.path.lexists(requested_target):
        raise BackupConflict(f"target_state_root already exists: {requested_target}")
    target = requested_target.resolve(strict=False)
    if target == source_root or target.is_relative_to(source_root) or source_root.is_relative_to(target):
        raise BackupConflict("restore target and backup_path must not overlap")
    parent = _ensure_directory(target.parent)
    temp_root = _temp_sibling(parent, target.name or "restore")
    try:
        _create_root(temp_root)
        _copy_regular_file(source_root / "state.sqlite", temp_root / "state.sqlite")
        os.chmod(temp_root / "state.sqlite", 0o600)
        _maybe_fault(fault_hook, "after_db_backup", temp_root)
        _copy_artifacts(source_root / "artifacts", temp_root / "artifacts")
        _copy_contexts(source_root, temp_root)
        _maybe_fault(fault_hook, "after_artifact_copy", temp_root)
        os.chmod(temp_root, 0o700)
        _verify_backup_root(temp_root)
        _sqlite_integrity(temp_root / "state.sqlite")
        audit(temp_root)
        _prepare_standalone_database(temp_root / "state.sqlite")
        if claude_settings is not None:
            from .host_settings import restore_host_settings
            restore_host_settings(temp_root, claude_settings)
        _fsync_backup_root(temp_root)
        _assert_same_filesystem(temp_root, parent)
        _maybe_fault(fault_hook, "before_rename", temp_root)
        renameat2_noreplace(temp_root, target)
        _maybe_fault(fault_hook, "after_rename", target)
        _fsync_directory(target.parent)
        return target
    except BaseException:
        if os.path.lexists(temp_root):
            _remove_tree(temp_root)
        raise


def _safe_id(value: str, field: str) -> None:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{field} must be one safe path component")


def _maybe_fault(fault_hook: FaultHook | None, phase: str, root: Path) -> None:
    if fault_hook is not None:
        fault_hook(phase, root)


def _ensure_directory(path: str | os.PathLike[str]) -> Path:
    directory = Path(path)
    existed = os.path.lexists(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    real = _real_directory(directory, str(directory))
    if not existed:
        os.chmod(real, 0o700)
    return real


def _real_directory(path: str | os.PathLike[str], label: str) -> Path:
    directory = Path(path)
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError as exc:
        raise BackupIntegrityError(f"{label} does not exist: {directory}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise BackupIntegrityError(f"{label} must be a real directory: {directory}")
    return directory.resolve()


def _real_regular_file(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise BackupIntegrityError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise BackupIntegrityError(f"{label} must be a real regular file: {path}")
    return path.resolve()


def _temp_sibling(parent: Path, label: str) -> Path:
    _safe_id(label, "label")
    return Path(tempfile.mkdtemp(prefix=f".tmp-{label}-", dir=parent))


def _create_root(root: Path) -> None:
    os.chmod(root, 0o700)
    (root / "artifacts").mkdir(mode=0o700)


def _online_backup(source_db: Path, destination_db: Path) -> None:
    source = sqlite3.connect(f"{source_db.as_uri()}?mode=ro", uri=True, isolation_level=None, timeout=5.0)
    destination = sqlite3.connect(destination_db, isolation_level=None, timeout=5.0)
    try:
        source.execute("PRAGMA query_only=ON")
        source.backup(destination)
    except sqlite3.Error as exc:
        raise BackupIntegrityError(f"SQLite Online Backup failed: {exc}") from exc
    finally:
        destination.close()
        source.close()


def _context_roots(root: Path, *, standalone: bool = True) -> tuple[Path, ...]:
    """Existing run-owned Git context and frozen views; no settings or live work."""
    # Only copied, closed backup databases have no WAL to consult. Avoid making
    # coordinator sidecars inside their existing exact backup-file boundary.
    query = "?mode=ro&immutable=1" if standalone else "?mode=ro"
    db = sqlite3.connect((root / "state.sqlite").as_uri() + query, uri=True)
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_runs'").fetchone() is None:
            return ()
        runs = [row[0] for row in db.execute("SELECT run_id FROM workflow_runs ORDER BY run_id")]
    finally:
        db.close()
    result = []

    def directory(path):
        return _real_directory(path, "run context") if os.path.lexists(path) else None

    for run_id in runs:
        _safe_id(run_id, "run_id")
        run = directory(root / run_id)
        if run is None:
            continue
        grounding = directory(run / "grounding")
        if grounding is not None and (plugin := directory(grounding / "plugin")) is not None:
            result.append(plugin)
        experts = directory(run / "experts")
        if experts is not None:
            for owner_path in sorted(experts.iterdir()):
                _safe_id(owner_path.name, "expert role")
                owner = directory(owner_path)
                for name in ("plugin", "memory", "views"):
                    if (context := directory(owner / name)) is not None:
                        result.append(context)
    return tuple(result)


def _copy_contexts(source: Path, destination: Path, *, standalone: bool = True) -> None:
    for context in _context_roots(source, standalone=standalone):
        _copy_artifacts(context, destination / context.relative_to(source), preserve_executable=True)


def _copy_artifacts(source: Path, destination: Path, *, preserve_executable: bool = False) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not source.exists():
        _fsync_directory(destination)
        return
    source_root = _real_directory(source, "source artifacts")
    relative_paths = _artifact_inventory(source_root)
    for relative in relative_paths:
        relative_path = PurePosixPath(relative)
        parent = destination
        for component in relative_path.parts[:-1]:
            parent = parent / component
            parent.mkdir(mode=0o700, exist_ok=True)
        _copy_regular_file(source_root.joinpath(*relative_path.parts), destination.joinpath(*relative_path.parts),
                           preserve_executable=preserve_executable)
    _fsync_tree(destination, relative_paths)
    _fsync_directory(destination)


def _copy_regular_file(source: Path, destination: Path, *, preserve_executable: bool = False) -> None:
    source_fd = _open_regular_nofollow(source, os.O_RDONLY)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    destination_fd = os.open(destination, flags, 0o600)
    try:
        if preserve_executable and os.fstat(source_fd).st_mode & 0o111:
            os.fchmod(destination_fd, 0o700)
        while True:
            chunk = os.read(source_fd, _CHUNK_SIZE)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fsync(destination_fd)
    except OSError as exc:
        raise ArtifactDurabilityError(f"copy failed for {source} -> {destination}: {exc}") from exc
    finally:
        os.close(destination_fd)
        os.close(source_fd)


def _open_regular_nofollow(path: Path, flags: int) -> int:
    try:
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BackupIntegrityError(f"cannot open file without following links: {path}") from exc
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(fd)
        raise BackupIntegrityError(f"file is not regular: {path}")
    return fd


def _fsync_backup_root(root: Path) -> None:
    _fsync_file(root / "state.sqlite")
    relative_paths = _artifact_inventory(root / "artifacts")
    _fsync_tree(root / "artifacts", relative_paths)
    _fsync_directory(root / "artifacts")
    for context in _context_roots(root):
        _fsync_tree(context, _artifact_inventory(context))
        for parent in context.parents:
            if parent == root:
                break
            _fsync_directory(parent)
    _fsync_directory(root)


def _fsync_tree(root: Path, relative_paths: tuple[str, ...]) -> None:
    directories = {root}
    for relative in relative_paths:
        path = root.joinpath(*PurePosixPath(relative).parts)
        _fsync_file(path)
        parent = path.parent
        while parent != root:
            directories.add(parent)
            parent = parent.parent
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)


def _fsync_file(path: Path) -> None:
    fd = _open_regular_nofollow(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as exc:
        raise ArtifactDurabilityError(f"file fsync failed for {path}: {exc}") from exc
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BackupIntegrityError(f"cannot open directory without following links: {path}") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise ArtifactDurabilityError(f"directory fsync failed for {path}: {exc}") from exc
    finally:
        os.close(fd)


def _sqlite_integrity(database: Path) -> None:
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, isolation_level=None, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise BackupIntegrityError(f"integrity_check failed for {database}: {integrity!r}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise BackupIntegrityError(f"foreign_key_check failed for {database}: {foreign_keys!r}")
    except sqlite3.Error as exc:
        raise BackupIntegrityError(f"integrity query failed for {database}: {exc}") from exc
    finally:
        connection.close()


def _verify_backup_root(root: Path) -> None:
    real = _real_directory(root, "backup root")
    _real_regular_file(real / "state.sqlite", "state.sqlite")
    _real_directory(real / "artifacts", "artifacts")
    _artifact_inventory(real / "artifacts")
    allowed = {real: {"state.sqlite", "artifacts"}}
    for context in _context_roots(real):
        _artifact_inventory(context)
        child = context
        while child != real:
            allowed.setdefault(child.parent, set()).add(child.name)
            child = child.parent
    for parent, names in allowed.items():
        if {entry.name for entry in parent.iterdir()} != names:
            raise BackupIntegrityError(f"backup contains files outside database, artifacts and admitted run context: {parent}")


def _artifact_inventory(root: Path) -> tuple[str, ...]:
    try:
        return inventory_relative_paths(root)
    except ArtifactIntegrityError as exc:
        if "cannot be empty" not in str(exc):
            raise
        return ()


def _strip_sqlite_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database}{suffix}")
        if sidecar.exists():
            if suffix == "-wal" and sidecar.stat().st_size != 0:
                raise BackupIntegrityError(f"SQLite sidecar remained nonempty after preparation: {sidecar}")
            sidecar.unlink()


def _prepare_standalone_database(database: Path) -> None:
    connection = sqlite3.connect(database, isolation_level=None, timeout=5.0)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        raise BackupIntegrityError(f"standalone database preparation failed for {database}: {exc}") from exc
    finally:
        connection.close()
    _strip_sqlite_sidecars(database)


def _assert_same_filesystem(path: Path, other: Path) -> None:
    if path.stat().st_dev != other.stat().st_dev:
        raise ArtifactDurabilityError(f"paths must be on the same filesystem: {path} vs {other}")


def _remove_tree(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


__all__ = [
    "BackupConflict",
    "BackupIntegrityError",
    "BackupRestoreError",
    "PublishedBackup",
    "create_quiesced_backup",
    "restore_backup",
]
