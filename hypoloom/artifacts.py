"""Immutable result-artifact publication with one canonical set digest."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


DIGEST_ALGORITHM = "sha256"
ARTIFACT_KIND = "result"
_RENAME_NOREPLACE = 1
_AT_FDCWD = -100
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


class ArtifactError(RuntimeError):
    pass


class ArtifactConflict(ArtifactError):
    pass


class ArtifactIntegrityError(ArtifactError):
    pass


class ArtifactDurabilityError(ArtifactError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactKey:
    run_id: str
    experiment_id: str
    execution_id: str
    attempt_id: str
    artifact_kind: str = ARTIFACT_KIND

    def __post_init__(self) -> None:
        for name in ("run_id", "experiment_id", "execution_id", "attempt_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
                raise ValueError(f"{name} is not a safe stable path component")
        if self.artifact_kind != ARTIFACT_KIND:
            raise ValueError(f"artifact_kind must be {ARTIFACT_KIND!r}")

    def as_tuple(self) -> tuple[str, str, str, str, str]:
        return (
            self.run_id,
            self.experiment_id,
            self.execution_id,
            self.attempt_id,
            self.artifact_kind,
        )

    @property
    def artifact_id(self) -> str:
        return ":".join(self.as_tuple())

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "artifact_kind": self.artifact_kind,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentArtifactKey:
    """Host-published node transcript, not a fabricated scientific execution."""

    run_id: str
    attempt_id: str
    artifact_kind: str = "transcript"

    def __post_init__(self) -> None:
        for value in (self.run_id, self.attempt_id):
            if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
                raise ValueError("Agent artifact identity is not a safe path component")
        if self.artifact_kind != "transcript":
            raise ValueError("unknown Agent artifact kind")

    def as_tuple(self) -> tuple[str, ...]:
        return self.run_id, "agent", self.attempt_id, self.artifact_kind

    @property
    def artifact_id(self) -> str:
        return ":".join(self.as_tuple())

    def to_dict(self) -> dict[str, str]:
        return {"run_id": self.run_id, "attempt_id": self.attempt_id, "artifact_kind": self.artifact_kind}


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactPublication:
    key: ArtifactKey | AgentArtifactKey
    schema_version: int
    digest_algorithm: str
    digest: str
    path: str
    relative_paths: tuple[str, ...]
    reused: bool

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ValueError("schema_version must be an integer")
        if self.schema_version <= 0:
            raise ValueError("schema_version must be positive")
        if self.digest_algorithm != DIGEST_ALGORITHM:
            raise ValueError(f"digest_algorithm must be {DIGEST_ALGORITHM}")
        _validate_digest(self.digest)
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("path must be non-empty")
        normalized = normalize_relative_paths(self.relative_paths)
        if normalized != self.relative_paths:
            raise ValueError("relative_paths must be canonical sorted paths")

    @property
    def artifact_id(self) -> str:
        return self.key.artifact_id

    @property
    def artifact_set_digest(self) -> str:
        return self.digest

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "key": self.key.to_dict(),
            "schema_version": self.schema_version,
            "digest_algorithm": self.digest_algorithm,
            "digest": self.digest,
            "path": self.path,
            "relative_paths": list(self.relative_paths),
            "reused": self.reused,
        }


class ArtifactPublisher:
    """The sole no-overwrite publisher for canonical process result sets."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        fsync: Callable[[int], None] = os.fsync,
        rename_noreplace: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ArtifactIntegrityError("artifact root must be a real directory")
        self.root = self.root.resolve()
        self._fsync = fsync
        self._rename_noreplace = rename_noreplace or renameat2_noreplace

    def path_for(self, key: ArtifactKey | AgentArtifactKey) -> Path:
        return self.root.joinpath(*key.as_tuple())

    def publish(
        self,
        key: ArtifactKey | AgentArtifactKey,
        staging_dir: str | os.PathLike[str],
        relative_paths: Iterable[str] | None = None,
        *,
        schema_version: int = 1,
        expected_digest: str | None = None,
        known_publication: ArtifactPublication | None = None,
    ) -> ArtifactPublication:
        """Publish once or exactly reuse a matching immutable target.

        ``known_publication`` is the existing database record when one exists;
        it makes schema equality explicit.  Recovery of an unreferenced rename
        instead supplies the run-pinned schema and an expected digest or a fresh
        matching staging directory.
        """

        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version <= 0:
            raise ValueError("schema_version must be a positive integer")
        target = self.path_for(key)
        self._ensure_parent(target.parent)
        staging = Path(staging_dir)
        target_exists = _lexists(target)
        if not staging.exists():
            if not target_exists or expected_digest is None or relative_paths is None:
                raise ArtifactIntegrityError("missing staging requires an existing target, paths and expected digest")
            paths = normalize_relative_paths(relative_paths)
            _validate_digest(expected_digest)
            source_digest = expected_digest
        else:
            self._validate_tree_root(staging)
            if staging.resolve() == target:
                raise ArtifactIntegrityError("staging directory cannot already be the target")
            paths = normalize_relative_paths(relative_paths or inventory_relative_paths(staging))
            _require_exact_inventory(staging, paths)
            source_digest = canonical_artifact_digest(staging, paths)
            if expected_digest is not None and source_digest != expected_digest:
                raise ArtifactConflict("staging bytes do not match expected digest")
            self._fsync_tree(staging, paths)
            if canonical_artifact_digest(staging, paths) != source_digest:
                raise ArtifactIntegrityError("staging bytes changed during durability preparation")

        if known_publication is not None:
            if known_publication.key != key:
                raise ArtifactConflict("known publication key differs")
            if known_publication.schema_version != schema_version:
                raise ArtifactConflict("known publication schema differs")
            if known_publication.digest != source_digest:
                raise ArtifactConflict("known publication digest differs")
            if Path(known_publication.path) != target:
                raise ArtifactConflict("known publication path differs")

        reused = target_exists
        if not target_exists:
            if not staging.exists():
                raise ArtifactIntegrityError("staging disappeared before publish")
            if staging.stat().st_dev != target.parent.stat().st_dev:
                raise ArtifactDurabilityError("staging and target must be on the same filesystem")
            try:
                self._rename_noreplace(staging, target)
                reused = False
            except FileExistsError:
                reused = True
            except OSError as exc:
                raise ArtifactDurabilityError(f"atomic no-replace rename failed: {exc}") from exc

        publication = ArtifactPublication(
            key=key,
            schema_version=schema_version,
            digest_algorithm=DIGEST_ALGORITHM,
            digest=source_digest,
            path=str(target),
            relative_paths=paths,
            reused=reused,
        )
        return self.durability_barrier(publication)

    def durability_barrier(self, publication: ArtifactPublication) -> ArtifactPublication:
        """Reopen, fsync and revalidate an existing target on every path."""

        expected_path = self.path_for(publication.key)
        if Path(publication.path) != expected_path:
            raise ArtifactConflict("publication path does not match stable key")
        self._validate_tree_root(expected_path)
        _require_exact_inventory(expected_path, publication.relative_paths)
        before = canonical_artifact_digest(expected_path, publication.relative_paths)
        if before != publication.digest:
            raise ArtifactConflict("stable target digest conflicts with expected bytes")
        self._fsync_tree(expected_path, publication.relative_paths)
        self._fsync_directory(expected_path.parent)
        after = canonical_artifact_digest(expected_path, publication.relative_paths)
        if after != publication.digest:
            raise ArtifactIntegrityError("stable target changed during durability barrier")
        return publication

    def verify(self, publication: ArtifactPublication) -> bool:
        expected_path = self.path_for(publication.key)
        if Path(publication.path) != expected_path:
            raise ArtifactConflict("publication path does not match stable key")
        self._validate_tree_root(expected_path)
        _require_exact_inventory(expected_path, publication.relative_paths)
        if canonical_artifact_digest(expected_path, publication.relative_paths) != publication.digest:
            raise ArtifactIntegrityError("published artifact digest mismatch")
        return True

    def _ensure_parent(self, directory: Path) -> None:
        relative = directory.relative_to(self.root)
        current = self.root
        self._validate_tree_root(current)
        for component in relative.parts:
            child = current / component
            try:
                child.mkdir(mode=0o700)
            except FileExistsError:
                pass
            if child.is_symlink() or not child.is_dir():
                raise ArtifactIntegrityError(f"artifact path component is not a real directory: {child}")
            self._fsync_directory(current)
            current = child

    def _fsync_tree(self, root: Path, paths: tuple[str, ...]) -> None:
        directories = {root}
        for relative in paths:
            path = root.joinpath(*PurePosixPath(relative).parts)
            fd = _open_regular_nofollow(path)
            try:
                self._fsync(fd)
            except OSError as exc:
                raise ArtifactDurabilityError(f"file fsync failed for {path}: {exc}") from exc
            finally:
                os.close(fd)
            parent = path.parent
            while parent != root:
                directories.add(parent)
                parent = parent.parent
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            self._fsync_directory(directory)

    def _fsync_directory(self, directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(directory, flags)
        except OSError as exc:
            raise ArtifactIntegrityError(f"cannot open directory without following links: {directory}") from exc
        try:
            self._fsync(fd)
        except OSError as exc:
            raise ArtifactDurabilityError(f"directory fsync failed for {directory}: {exc}") from exc
        finally:
            os.close(fd)

    @staticmethod
    def _validate_tree_root(root: Path) -> None:
        try:
            mode = root.lstat().st_mode
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError(f"artifact directory is missing: {root}") from exc
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise ArtifactIntegrityError(f"artifact root is not a real directory: {root}")


def normalize_relative_paths(relative_paths: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in relative_paths:
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise ArtifactIntegrityError(f"invalid artifact relative path: {raw!r}")
        path = PurePosixPath(raw)
        if path.is_absolute() or path.as_posix() != raw or any(part in {"", ".", ".."} for part in path.parts):
            raise ArtifactIntegrityError(f"unsafe artifact relative path: {raw!r}")
        normalized.append(raw)
    if not normalized:
        raise ArtifactIntegrityError("canonical artifact set cannot be empty")
    if len(set(normalized)) != len(normalized):
        raise ArtifactIntegrityError("canonical artifact paths must be unique")
    return tuple(sorted(normalized, key=lambda value: value.encode("utf-8")))


def inventory_relative_paths(root: str | os.PathLike[str]) -> tuple[str, ...]:
    base = Path(root)
    paths: list[str] = []
    for directory, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in tuple(dirnames):
            path = directory_path / name
            if path.is_symlink():
                raise ArtifactIntegrityError(f"symlink directory forbidden: {path}")
        for name in filenames:
            path = directory_path / name
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                raise ArtifactIntegrityError(f"only regular files are canonical artifacts: {path}")
            paths.append(path.relative_to(base).as_posix())
    return normalize_relative_paths(paths)


def canonical_artifact_digest(
    root: str | os.PathLike[str],
    relative_paths: Iterable[str] | None = None,
) -> str:
    """SHA-256 of sorted ``path-length,path,file-length,file-bytes`` frames."""

    base = Path(root)
    paths = normalize_relative_paths(relative_paths or inventory_relative_paths(base))
    digest = hashlib.sha256()
    for relative in paths:
        encoded = relative.encode("utf-8")
        path = base.joinpath(*PurePosixPath(relative).parts)
        fd = _open_regular_nofollow(path)
        try:
            metadata = os.fstat(fd)
            length = metadata.st_size
            digest.update(struct.pack(">Q", len(encoded)))
            digest.update(encoded)
            digest.update(struct.pack(">Q", length))
            remaining = length
            while remaining:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise ArtifactIntegrityError(f"file shortened during hashing: {relative}")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1):
                raise ArtifactIntegrityError(f"file grew during hashing: {relative}")
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            ):
                raise ArtifactIntegrityError(f"file changed during hashing: {relative}")
        finally:
            os.close(fd)
    return digest.hexdigest()


def renameat2_noreplace(source: Path, destination: Path) -> None:
    """Linux ``renameat2(RENAME_NOREPLACE)`` with no overwrite fallback."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ArtifactDurabilityError("libc does not expose Linux renameat2")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), destination)
    raise OSError(error, os.strerror(error), destination)


def _require_exact_inventory(root: Path, expected: tuple[str, ...]) -> None:
    actual = inventory_relative_paths(root)
    if actual != expected:
        raise ArtifactIntegrityError(f"artifact inventory differs: expected={expected!r} actual={actual!r}")
    expected_directories: set[str] = set()
    for relative in expected:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() != ".":
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    actual_directories: set[str] = set()
    for directory, dirnames, _ in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        for name in dirnames:
            path = base / name
            if path.is_symlink():
                raise ArtifactIntegrityError(f"symlink directory forbidden: {path}")
            actual_directories.add(path.relative_to(root).as_posix())
    if actual_directories != expected_directories:
        raise ArtifactIntegrityError(
            "artifact directory inventory differs: "
            f"expected={sorted(expected_directories)!r} actual={sorted(actual_directories)!r}"
        )


def _open_regular_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ArtifactIntegrityError(f"cannot open artifact without following links: {path}") from exc
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(fd)
        raise ArtifactIntegrityError(f"artifact is not a regular file: {path}")
    return fd


def _validate_digest(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("digest must be lowercase SHA-256")


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


__all__ = [
    "ARTIFACT_KIND",
    "DIGEST_ALGORITHM",
    "ArtifactConflict",
    "ArtifactDurabilityError",
    "ArtifactError",
    "ArtifactIntegrityError",
    "ArtifactKey",
    "ArtifactPublication",
    "ArtifactPublisher",
    "canonical_artifact_digest",
    "inventory_relative_paths",
    "normalize_relative_paths",
    "renameat2_noreplace",
]
