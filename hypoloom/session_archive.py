"""Opt-in terminal-run session archival using ordinary recoverable tar files.

Only the two fixed temporary session trees move. Canonical transcript artifacts
and expert/Grounding plugin and memory Git repositories never move or disappear.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .artifacts import _SAFE_ID
from .state_store import StateStore

_ROOTS = ("sessions", "grounding/sessions")
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _safe_path(path):
    if path.absolute() != path.resolve():
        raise ValueError("session archive path must not follow symlinks")


def _run_root(state_root, run_id):
    if not isinstance(run_id, str) or _SAFE_ID.fullmatch(run_id) is None:
        raise ValueError("invalid archive run identity")
    state = Path(state_root).absolute()
    root = state / run_id
    _safe_path(root)
    if not state.is_dir() or root.exists() and not root.is_dir():
        raise ValueError("session archive requires an existing state root and safe run directory")
    return root


def _terminal(state_root, run_id, *, configured=False):
    store = StateStore(state_root)
    with store.readonly_connection() as db:
        db.execute("BEGIN")
        if configured:
            rows = db.execute("SELECT payload_json FROM research_facts WHERE run_id=? AND fact_type='GoalDefined'", (run_id,)).fetchall()
            if len(rows) != 1:
                raise ValueError("archive requires its admitted GoalDefined settings")
            product = json.loads(rows[0][0])["research_contract"].get("methodology", {}).get("controls", {}).get("product", {})
            mode = product.get("methodology", {}).get("memory", {}).get("retention", "retain")
            if mode == "retain":
                return False
            if mode != "archive_on_close":
                raise ValueError("unknown session retention setting")
        head = db.execute("SELECT state FROM workflow_transitions WHERE workflow_id=? AND workflow_kind='RUN' ORDER BY revision DESC LIMIT 1", (run_id,)).fetchone()
        if head is None or head[0] not in _TERMINAL:
            raise ValueError("session archival/restoration requires an actual terminal Run")
        if db.execute("SELECT 1 FROM execution_attempts WHERE run_id=? AND state='OPEN' LIMIT 1", (run_id,)).fetchone():
            raise ValueError("session archival/restoration refuses OPEN effects")
    return True


def _member_name(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name or "\\" in name or "\x00" in name
            or path.is_absolute() or ".." in path.parts or path.as_posix() != name
            or not any(name == root or name.startswith(root + "/") for root in _ROOTS)):
        raise ValueError("unsafe session archive member")
    return path


def _members(handle):
    members = {}
    for member in handle.getmembers():
        path = _member_name(member.name)
        if member.name in members or not (member.isdir() or member.isfile()):
            raise ValueError("session archive members must be unique regular files/directories")
        if member.name in _ROOTS and not member.isdir():
            raise ValueError("session archive root member must be a directory")
        if any(str(parent) in members and not members[str(parent)].isdir() for parent in path.parents):
            raise ValueError("session archive member conflicts with its parent")
        members[member.name] = member
    # Consume the standard gzip trailer too: an incomplete compressed archive
    # must never justify removing an original tree.
    handle.fileobj.read()
    return members


def _archive(root):
    path = root / "archives/sessions.tar.gz"
    _safe_path(path)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("session archive must be an existing regular file")
    return path


def _tree(root, name):
    parent = root / name
    _safe_path(parent)
    if not parent.exists():
        return {}
    if not parent.is_dir():
        raise ValueError("session tree must be a directory")
    result = {}
    for path in (parent, *sorted(parent.rglob("*"))):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) and not (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1):
            raise ValueError("session tree contains a non-regular or linked entry")
        result[path.relative_to(root).as_posix()] = path
    return result


def _compare(handle, members, tree, *, complete=False):
    for name, path in tree.items():
        member = members.get(name)
        if member is None or member.isdir() != path.is_dir():
            raise ValueError("existing session context differs from its archive")
        if member.isfile():
            if member.size != path.stat().st_size:
                raise ValueError("existing session bytes differ from their archive")
            with handle.extractfile(member) as source, path.open("rb") as original:
                while chunk := source.read(1024 * 1024):
                    if original.read(len(chunk)) != chunk:
                        raise ValueError("existing session bytes differ from their archive")
        if (path.stat().st_mode & 0o777) != (member.mode & 0o777):
            raise ValueError("existing session modes differ from their archive")
    if complete and set(tree) != set(members):
        raise ValueError("existing session inventory differs from its archive")


def _sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def archive_run_sessions(state_root, run_id):
    """Host-only terminal callback; return archive path or None for retain/empty."""
    root = _run_root(state_root, run_id)
    if not _terminal(state_root, run_id, configured=True):
        return None
    trees = {name: _tree(root, name) for name in _ROOTS}
    destination = root / "archives/sessions.tar.gz"
    _safe_path(destination)
    if not destination.exists() and not any(trees.values()):
        return None
    if not destination.exists():
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        _sync(root)
        _sync(root.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".sessions-", suffix=".tar.gz", dir=destination.parent)
        temporary = Path(temporary_name)
        os.close(descriptor)
        try:
            with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as handle:
                for name, tree in trees.items():
                    if tree:
                        handle.add(root / name, arcname=name)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            with tarfile.open(temporary, "r:gz") as handle:
                members = _members(handle)
                _compare(handle, members, {key: value for tree in trees.values() for key, value in tree.items()}, complete=True)
            os.replace(temporary, destination)
            _sync(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)
    with tarfile.open(_archive(root), "r:gz") as handle:
        members = _members(handle)
        # A crash during deletion may leave only part of a tree. Every remaining
        # byte must still match the complete published archive before proceeding.
        for name in _ROOTS:
            _compare(handle, members, _tree(root, name))
        for name in _ROOTS:
            path = root / name
            if path.exists():
                shutil.rmtree(path)
                _sync(path.parent)
    return destination


def inspect_session_archive(state_root, run_id):
    """Read-only standard-tar inventory; never restore or start a Host."""
    with tarfile.open(_archive(_run_root(state_root, run_id)), "r:gz") as handle:
        return tuple({"name": member.name, "type": "directory" if member.isdir() else "file", "size": member.size}
            for member in _members(handle).values())


def read_session_archive_file(state_root, run_id, member_name, *, max_bytes=1024 * 1024):
    _member_name(member_name)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("archive read limit must be a positive integer")
    with tarfile.open(_archive(_run_root(state_root, run_id)), "r:gz") as handle:
        member = _members(handle).get(member_name)
        if member is None or not member.isfile() or member.size > max_bytes:
            raise ValueError("archive member is absent, not a file, or exceeds the read limit")
        return handle.extractfile(member).read()


def restore_run_sessions(state_root, run_id):
    """Explicit restoration only; preserve the archive and never overwrite work."""
    root = _run_root(state_root, run_id)
    _terminal(state_root, run_id)
    path = _archive(root)
    with tarfile.open(path, "r:gz") as handle:
        members = _members(handle)
        roots = tuple(name for name in _ROOTS if any(key == name or key.startswith(name + "/") for key in members))
        for name in roots:
            if (root / name).exists():
                selected = {key: value for key, value in members.items() if key == name or key.startswith(name + "/")}
                _compare(handle, selected, _tree(root, name), complete=True)
        temporary = Path(tempfile.mkdtemp(prefix=".restore-sessions-", dir=path.parent))
        try:
            for name, member in members.items():
                destination = temporary / name
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with handle.extractfile(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                destination.chmod(member.mode & 0o777)
            for directory in sorted((item for item in temporary.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
                _sync(directory)
            _sync(temporary)
            for name in roots:
                if not (root / name).exists():
                    (root / name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    os.rename(temporary / name, root / name)
                    _sync((root / name).parent)
                    _sync(root)
        finally:
            shutil.rmtree(temporary)
    return tuple(root / name for name in roots)


__all__ = ["archive_run_sessions", "inspect_session_archive", "read_session_archive_file", "restore_run_sessions"]
