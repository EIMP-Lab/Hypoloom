"""Short-lived pool maintenance/admission coordination over existing storage.

The pool database, CREATED bindings and controller/service locks remain the
authorities. No maintenance marker or second binding registry is written.
See docs/design/init-managed-gpu-pool-lifecycle.md for the race justification.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import quote

from .gpu_pool import PoolError
from .runner import process_group_exists


def managed_repository(socket_path: Path) -> Path | None:
    path = Path(socket_path).absolute()
    if path.parts[-4:] == (".local", "gpu-pools", "shared", "control.sock"):
        return path.parents[3]
    return None


@contextmanager
def directory_lock(root: Path, *, exclusive: bool, timeout: float = 2.0):
    """Never create storage during observation; process exit releases the lock."""
    descriptor = None
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise PoolError("invalid_storage", "Managed pool directory must be owned and private")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PoolError("pool_maintenance_busy", "Pool preparation is in progress; recheck in this init") from None
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        current = root.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise PoolError("invalid_storage", "Managed pool directory changed")
        yield
    except OSError as exc:
        raise PoolError("pool_state_unknown", "Cannot inspect or coordinate the managed pool directory") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def admission_lock(socket_path: Path | None, state_root: Path):
    repository = None if socket_path is None else managed_repository(socket_path)
    if repository is None:
        yield
        return
    # The canonical managed pool's durable bindings must be discoverable.
    # External roots can use an explicitly configured external pool instead.
    if state_root.resolve().parent != repository.resolve() / "state":
        raise ValueError("The project managed pool requires a state root under its repository/state; use an external pool for external roots")
    try:
        with directory_lock(Path(socket_path).parent, exclusive=False):
            yield
    except PoolError as exc:
        raise ValueError(f"GPU pool admission failed ({exc.code}): {exc}") from exc


def _read_database(path: Path):
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise ValueError("unreadable database")
    db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=0.25)
    db.row_factory = sqlite3.Row
    deadline = time.monotonic() + 2
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return db


def _hold_controller(root: Path, stack: ExitStack):
    path = root / "controller.lock"
    if not path.exists():
        raise ValueError("controller ownership unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    stack.callback(os.close, fd)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise ValueError("invalid controller ownership")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def inspect_dependencies(repository: Path, socket_path: Path, *, locks: ExitStack | None = None,
                         controllers_held: bool = False, timeout: float = 8.0) -> dict:
    """Inspect project bindings plus all pool request owners, without settling any.

    `locks` retains known controller locks through maintenance. An unreadable
    root, an open Attempt or unknown ownership is never interpreted as idle.
    The scope is explicitly project-local, not a machine-wide census.
    """
    blockers = []
    bound = []
    known = set()
    held_roots = set()
    temporary = ExitStack()
    stack = temporary if locks is None else locks
    deadline = time.monotonic() + timeout
    try:
        roots = sorted((repository / "state").iterdir()) if (repository / "state").exists() else []
        for root in roots:
            if time.monotonic() >= deadline:
                blockers.append({"reason": "dependency_check_timeout"})
                break
            database = root / "state.sqlite"
            if not root.is_dir() or not database.exists():
                if not controllers_held and root.is_dir() and (root / "controller.lock").exists():
                    try: _hold_controller(root, stack)
                    except (OSError, ValueError): blockers.append({"state_root": str(root), "reason": "host_present_or_unknown"})
                continue
            if root.is_symlink():
                blockers.append({"state_root": str(root), "reason": "state_root_alias"})
                continue
            try:
                with closing(_read_database(database)) as db:
                    # revision 1 avoids scanning all experiment transitions.
                    rows = db.execute("SELECT t.run_id, t.payload_json FROM workflow_runs r "
                        "JOIN workflow_transitions t ON t.workflow_id=r.run_id AND t.revision=1 "
                        "WHERE t.workflow_kind='RUN' AND t.state='CREATED'").fetchall()
                    if not rows and not controllers_held and (root / "controller.lock").exists():
                        try: _hold_controller(root, stack)
                        except (OSError, ValueError): blockers.append({"state_root": str(root), "reason": "host_present_or_unknown"})
                    for row in rows:
                        binding = json.loads(row["payload_json"]).get("gpu_pool")
                        if not binding or Path(binding["socket_path"]).absolute() != socket_path.absolute():
                            continue
                        key = (os.geteuid(), binding["namespace"], row["run_id"])
                        known.add(key)
                        head = db.execute("SELECT state FROM workflow_transitions WHERE workflow_id=? "
                            "ORDER BY revision DESC LIMIT 1", (row["run_id"],)).fetchone()
                        status = None if head is None else head["state"]
                        bound.append({"run_id": row["run_id"], "state_root": str(root), "state": status})
                        if status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                            blockers.append({"run_id": row["run_id"], "reason": "run_depends_on_pool"})
                        attempts = db.execute("SELECT state, process_group_id FROM execution_attempts WHERE run_id=?", (row["run_id"],)).fetchall()
                        if any(a["state"] == "OPEN" for a in attempts):
                            blockers.append({"run_id": row["run_id"], "reason": "open_attempt"})
                        if any(a["process_group_id"] and process_group_exists(a["process_group_id"]) for a in attempts):
                            blockers.append({"run_id": row["run_id"], "reason": "process_present"})
                        if not controllers_held and root not in held_roots:
                            held_roots.add(root)
                            try: _hold_controller(root, stack)
                            except (OSError, ValueError): blockers.append({"run_id": row["run_id"], "reason": "host_present_or_unknown"})
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                blockers.append({"state_root": str(root), "reason": "state_unreadable"})
        database = socket_path.parent / "pool.sqlite3"
        if database.exists():
            try:
                with closing(_read_database(database)) as db:
                    # Validate the fields used by the actual pool, without DDL.
                    db.execute("SELECT request_seq,uid,namespace,run_id,execution_id,attempt_id,state,intent_json,"
                        "grant_json,decision_json,process_group_id,reason FROM requests LIMIT 0")
                    owners = db.execute("SELECT uid,namespace,run_id,last_grant FROM runs").fetchall()
                    if any((r["uid"], r["namespace"], r["run_id"]) not in known for r in owners):
                        blockers.append({"reason": "pool_owner_outside_known_roots"})
                    if db.execute("SELECT 1 FROM requests WHERE state!='RELEASED' LIMIT 1").fetchone():
                        blockers.append({"reason": "unreleased_pool_request"})
                    for r in db.execute("SELECT DISTINCT process_group_id FROM requests WHERE process_group_id IS NOT NULL"):
                        if process_group_exists(r[0]):
                            blockers.append({"reason": "pool_process_present"})
            except (OSError, ValueError, TypeError, sqlite3.Error):
                blockers.append({"reason": "pool_database_unreadable"})
        return {"scope": "project_state_roots", "bindings": bound, "blockers": blockers,
                "quiescent": not blockers}
    except OSError:
        return {"scope": "project_state_roots", "bindings": bound,
                "blockers": [{"reason": "state_roots_unreadable"}], "quiescent": False}
    finally:
        temporary.close()
