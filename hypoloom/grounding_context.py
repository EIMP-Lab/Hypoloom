"""Bounded read-only filesystem context for the role-neutral Grounding node.

This outward helper consumes only the operational mapping already produced by
profile admission.  It has no persistence, workflow authority, network access,
subprocess access, or write path.  Symlinks and Git metadata are inventory
entries only and are never traversed or sampled.
"""

from __future__ import annotations

import json
import os
import stat
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_SAMPLE_SUFFIXES = frozenset(
    {".csv", ".json", ".markdown", ".md", ".py", ".txt", ".yaml", ".yml"}
)
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".conda",
        ".eggs",
        ".git",
        ".hg",
        ".idea",
        ".ipynb_checkpoints",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "__pypackages__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
        "virtualenv",
    }
)
_EXCLUDED_DIRECTORY_SUFFIXES = (".dist-info", ".egg-info")
_EXCLUDED_DIRECTORY_PREFIXES = (".venv-",)
_HARD_MAX_ENTRIES = 4_096
_HARD_MAX_SAMPLE_FILES = 256
_HARD_MAX_FILE_BYTES = 1024 * 1024
_HARD_MAX_TOTAL_SAMPLE_BYTES = 8 * 1024 * 1024


class GroundingContextError(ValueError):
    """The admitted execution binding is absent, stale, or malformed."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GroundingLimits:
    """Small caller-selectable limits capped by non-overridable hard bounds."""

    max_entries: int = 256
    max_sample_files: int = 16
    max_file_bytes: int = 32 * 1024
    max_total_sample_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        for name, upper in (
            ("max_entries", _HARD_MAX_ENTRIES),
            ("max_sample_files", _HARD_MAX_SAMPLE_FILES),
            ("max_file_bytes", _HARD_MAX_FILE_BYTES),
            ("max_total_sample_bytes", _HARD_MAX_TOTAL_SAMPLE_BYTES),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= upper
            ):
                raise ValueError(f"{name} must be a positive bounded integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_entries": self.max_entries,
            "max_sample_files": self.max_sample_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_sample_bytes": self.max_total_sample_bytes,
        }


@dataclass(slots=True)
class _ScanState:
    limits: GroundingLimits
    entry_count: int = 0
    sampled_file_count: int = 0
    sample_bytes_read: int = 0
    unreadable_count: int = 0
    truncated: bool = False

    @property
    def entries_remaining(self) -> int:
        return self.limits.max_entries - self.entry_count

    @property
    def sample_bytes_remaining(self) -> int:
        return self.limits.max_total_sample_bytes - self.sample_bytes_read


@dataclass(slots=True)
class _DirectoryScan:
    path: Path
    relative: PurePosixPath | None
    record: dict[str, object] | None
    expected_device: int
    expected_inode: int
    child_names: list[str] | None = None
    child_index: int = 0


def _execution_root(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GroundingContextError(f"{name} must be an admitted absolute directory")
    raw = Path(value)
    if not raw.is_absolute():
        raise GroundingContextError(f"{name} must be an admitted absolute directory")
    if raw.is_symlink():
        raise GroundingContextError(f"{name} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise GroundingContextError(f"{name} is no longer available") from exc
    if not resolved.is_dir():
        raise GroundingContextError(f"{name} is no longer a directory")
    if raw != resolved:
        raise GroundingContextError(f"{name} differs from its admitted resolved path")
    return resolved


def _error_status(error: BaseException) -> str:
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, FileNotFoundError):
        return "disappeared"
    return "io_error"


def _read_regular_file(
    directory_fd: int,
    name: str,
    expected: os.stat_result,
    limit: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_dev != expected.st_dev
            or observed.st_ino != expected.st_ino
        ):
            raise OSError("filesystem entry changed during bounded read")
        chunks: list[bytes] = []
        remaining = limit
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _file_entry(
    directory_fd: int,
    name: str,
    relative: str,
    metadata: os.stat_result,
    state: _ScanState,
) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": "file",
        "path": relative,
        "size_bytes": int(metadata.st_size),
    }
    if Path(name).suffix.casefold() not in _SAMPLE_SUFFIXES:
        record["sample_status"] = "unsupported_type"
        return record
    if state.sampled_file_count >= state.limits.max_sample_files:
        record["sample_status"] = "sample_file_limit"
        state.truncated = True
        return record
    if state.sample_bytes_remaining <= 0:
        record["sample_status"] = "total_byte_limit"
        state.truncated = True
        return record
    read_limit = min(
        int(metadata.st_size),
        state.limits.max_file_bytes,
        state.sample_bytes_remaining,
    )
    try:
        content = _read_regular_file(directory_fd, name, metadata, read_limit)
    except OSError:
        record["sample_status"] = "unreadable"
        state.unreadable_count += 1
        state.truncated = True
        return record
    state.sampled_file_count += 1
    state.sample_bytes_read += len(content)
    truncated = int(metadata.st_size) > len(content)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        if truncated and exc.end == len(content) and exc.reason == "unexpected end of data":
            text = content[: exc.start].decode("utf-8")
        else:
            record["sample_status"] = "non_utf8"
            return record
    record["sample_status"] = "truncated" if truncated else "complete"
    record["sample_text"] = text
    if truncated:
        state.truncated = True
    return record


def _excluded_directory(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in _EXCLUDED_DIRECTORY_NAMES
        or folded.startswith(_EXCLUDED_DIRECTORY_PREFIXES)
        or folded.endswith(_EXCLUDED_DIRECTORY_SUFFIXES)
    )


def _open_directory(directory: _DirectoryScan) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(directory.path, flags)
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_dev != directory.expected_device
            or observed.st_ino != directory.expected_inode
        ):
            raise OSError("filesystem directory changed during bounded scan")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _load_directory(
    directory: _DirectoryScan,
    state: _ScanState,
    result: dict[str, object],
) -> bool:
    if directory.child_names is None:
        try:
            descriptor = _open_directory(directory)
            try:
                with os.scandir(descriptor) as iterator:
                    directory.child_names = sorted(item.name for item in iterator)
            finally:
                os.close(descriptor)
        except OSError:
            state.unreadable_count += 1
            state.truncated = True
            result["truncated"] = True
            if directory.record is not None:
                directory.record["status"] = "unreadable"
            return False
    return directory.child_index < len(directory.child_names)


def _scan_root(
    root: Path,
    state: _ScanState,
    result: dict[str, object],
) -> Iterator[tuple[dict[str, object], bool]]:
    try:
        root_metadata = root.lstat()
    except OSError:
        state.unreadable_count += 1
        state.truncated = True
        result["truncated"] = True
        return
    if not stat.S_ISDIR(root_metadata.st_mode):
        state.unreadable_count += 1
        state.truncated = True
        result["truncated"] = True
        return
    frontier = deque([
        _DirectoryScan(
            root,
            None,
            None,
            int(root_metadata.st_dev),
            int(root_metadata.st_ino),
        )
    ])
    next_frontier: deque[_DirectoryScan] = deque()
    while frontier or next_frontier:
        if not frontier:
            frontier, next_frontier = next_frontier, deque()
        directory = frontier.popleft()
        if not _load_directory(directory, state, result):
            continue
        child_names = directory.child_names
        assert child_names is not None
        child_name = child_names[directory.child_index]
        relative_path = (
            PurePosixPath(child_name)
            if directory.relative is None
            else directory.relative / child_name
        )
        relative = relative_path.as_posix()
        child_path = directory.path / child_name
        try:
            child_path.relative_to(root)
        except ValueError as exc:
            raise GroundingContextError(
                "enumerated path escaped its admitted root"
            ) from exc
        try:
            descriptor = _open_directory(directory)
        except OSError:
            state.unreadable_count += 1
            state.truncated = True
            result["truncated"] = True
            if directory.record is not None:
                directory.record["status"] = "unreadable"
            continue
        directory.child_index += 1
        if directory.child_index < len(child_names):
            frontier.append(directory)
        try:
            if child_name == ".git":
                record: dict[str, object] = {
                    "kind": "directory",
                    "path": relative,
                    "status": "excluded",
                }
            else:
                try:
                    metadata = os.stat(
                        child_name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    state.unreadable_count += 1
                    state.truncated = True
                    result["truncated"] = True
                    record = {
                        "kind": "unreadable",
                        "path": relative,
                        "status": _error_status(exc),
                    }
                else:
                    if stat.S_ISLNK(metadata.st_mode):
                        record = {
                            "kind": "symlink",
                            "path": relative,
                            "status": "not_followed",
                        }
                    elif stat.S_ISDIR(metadata.st_mode):
                        record = {"kind": "directory", "path": relative}
                        if _excluded_directory(child_name):
                            record["status"] = "excluded"
                        else:
                            next_frontier.append(
                                _DirectoryScan(
                                    child_path,
                                    relative_path,
                                    record,
                                    int(metadata.st_dev),
                                    int(metadata.st_ino),
                                )
                            )
                    elif stat.S_ISREG(metadata.st_mode):
                        record = _file_entry(
                            descriptor,
                            child_name,
                            relative,
                            metadata,
                            state,
                        )
                    else:
                        record = {
                            "kind": "other",
                            "path": relative,
                            "status": "not_read",
                        }
        finally:
            os.close(descriptor)
        if not frontier:
            while next_frontier and not _load_directory(
                next_frontier[0], state, result
            ):
                next_frontier.popleft()
        yield record, not frontier and not next_frontier


def _fill_inventories(
    scans: list[
        tuple[dict[str, object], Iterator[tuple[dict[str, object], bool]]]
    ],
    state: _ScanState,
) -> None:
    active = scans
    while active and state.entries_remaining > 0:
        next_active: list[
            tuple[dict[str, object], Iterator[tuple[dict[str, object], bool]]]
        ] = []
        for index, (result, iterator) in enumerate(active):
            if state.entries_remaining <= 0:
                next_active.extend(active[index:])
                break
            try:
                entry, is_last = next(iterator)
            except StopIteration:
                continue
            entries = result["entries"]
            assert isinstance(entries, list)
            entries.append(entry)
            state.entry_count += 1
            if not is_last:
                next_active.append((result, iterator))
        active = next_active
    if active:
        state.truncated = True
        for result, _iterator in active:
            result["truncated"] = True
    for result, _iterator in scans:
        entries = result["entries"]
        assert isinstance(entries, list)
        entries.sort(key=lambda item: str(item["path"]))


def build_grounding_context(
    execution: Mapping[str, object],
    *,
    limits: GroundingLimits | None = None,
) -> dict[str, object]:
    """Project one admitted execution mapping into bounded JSON-safe context."""

    if not isinstance(execution, Mapping):
        raise GroundingContextError("execution must be the admitted mapping")
    selected_limits = GroundingLimits() if limits is None else limits
    if not isinstance(selected_limits, GroundingLimits):
        raise TypeError("limits must be GroundingLimits")
    raw_target = execution.get("target")
    if raw_target is not None and (
        not isinstance(raw_target, Mapping)
        or set(raw_target) != {"checkout", "commit"}
    ):
        raise GroundingContextError("execution.target must be the admitted binding")
    raw_read_roots = execution.get("read_roots")
    if (
        isinstance(raw_read_roots, (str, bytes))
        or not isinstance(raw_read_roots, Sequence)
    ):
        raise GroundingContextError("execution.read_roots must be an admitted array")
    state = _ScanState(selected_limits)
    scans: list[
        tuple[dict[str, object], Iterator[tuple[dict[str, object], bool]]]
    ] = []

    target: dict[str, object] | None = None
    checkout: Path | None = None
    if raw_target is not None:
        checkout = _execution_root(raw_target["checkout"], "execution.target.checkout")
        commit = raw_target["commit"]
        if (
            not isinstance(commit, str)
            or not commit
            or commit != commit.strip()
            or len(commit) > 512
        ):
            raise GroundingContextError("execution.target.commit is invalid")
        target = {
            "checkout": str(checkout),
            "commit": commit,
            "entries": [],
            "truncated": False,
        }
        scans.append((target, _scan_root(checkout, state, target)))

    read_roots: list[dict[str, object]] = []
    seen: set[Path] = set()
    for ordinal, raw_root in enumerate(raw_read_roots):
        root = _execution_root(raw_root, f"execution.read_roots[{ordinal}]")
        if checkout is not None and root == checkout:
            read_roots.append({
                "root": str(root),
                "entries": [],
                "truncated": False,
                "status": "covered_by_target",
            })
            continue
        if root in seen:
            read_roots.append({
                "root": str(root),
                "entries": [],
                "truncated": False,
                "status": "duplicate_root",
            })
            continue
        seen.add(root)
        result: dict[str, object] = {
            "root": str(root),
            "entries": [],
            "truncated": False,
        }
        read_roots.append(result)
        scans.append((result, _scan_root(root, state, result)))

    _fill_inventories(scans, state)

    return {
        "limits": selected_limits.to_dict(),
        "target": target,
        "read_roots": read_roots,
        "summary": {
            "entry_count": state.entry_count,
            "sample_bytes_read": state.sample_bytes_read,
            "sampled_file_count": state.sampled_file_count,
            "truncated": state.truncated,
            "unreadable_count": state.unreadable_count,
        },
    }


def build_grounding_context_bytes(
    execution: Mapping[str, object],
    *,
    limits: GroundingLimits | None = None,
) -> bytes:
    """Return the canonical UTF-8 JSON encoding of ``build_grounding_context``."""

    return json.dumps(
        build_grounding_context(execution, limits=limits),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
