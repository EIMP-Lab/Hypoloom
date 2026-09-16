"""Opt-in qualification for a real tmpfs ``ENOSPC`` at a Host DB commit.

This is intentionally outside the normal suite.  The outer test creates a
rootless user+mount namespace; the inner test mounts a bounded tmpfs below the
pytest temporary directory.  No retained state root, host mount, device, or
privileged container is involved.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


_ENV = "HYPOLOOM_REAL_FS_ENOSPC"
_CHILD_ENV = "HYPOLOOM_REAL_FS_ENOSPC_NAMESPACE_CHILD"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TMPFS_BYTES = 16 * 1024 * 1024


def _cli(state_root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "hypoloom.cli", *arguments],
        cwd=_PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(
            f"CLI failed: argv={arguments!r} returncode={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result


def _start_host(state_root: Path) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "hypoloom.cli", "serve", "--state-root", str(state_root)],
        cwd=_PROJECT_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    socket_path = state_root / "control.sock"
    while time.monotonic() < deadline:
        if socket_path.is_socket():
            return process
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"Host exited before serving: {stdout=!r} {stderr=!r}")
        time.sleep(0.02)
    _stop_host(process, require_clean=False)
    raise AssertionError("Host did not create control.sock")


def _group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_host(process: subprocess.Popen[str], *, require_clean: bool) -> tuple[str, str]:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        stdout, stderr = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=8)
        raise AssertionError(f"Host ignored SIGTERM: {stdout=!r} {stderr=!r}")
    if require_clean:
        assert process.returncode == 0, (stdout, stderr)
    assert not _group_exists(process.pid), f"Host process group survived: {process.pid}"
    return stdout, stderr


def _create_arguments(state_root: Path, run_id: str) -> tuple[str, ...]:
    return (
        "start",
        "component-demo",
        "--cpu-only",
        "--profiles-root",
        str(_PROJECT_ROOT / "tests" / "fixtures" / "profiles"),
        "--state-root",
        str(state_root),
        "--run-id",
        run_id,
        "--request-id",
        f"{run_id}-create-1",
        "--literature-adapter",
        "offline",
    )


def _create(state_root: Path, run_id: str) -> None:
    result = _cli(state_root, *_create_arguments(state_root, run_id))
    response = json.loads(result.stdout)
    assert response["status"] == "accepted", response


def _await_completed(state_root: Path, run_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 20
    latest: dict[str, object] | None = None
    while time.monotonic() < deadline:
        result = _cli(
            state_root,
            "status",
            "--state-root",
            str(state_root),
            "--run-id",
            run_id,
            "--timeout",
            "2",
            check=False,
        )
        if result.returncode == 0:
            latest = json.loads(result.stdout)
            payload = latest.get("result")
            if isinstance(payload, dict) and payload.get("run_state") == "COMPLETED":
                return latest
        time.sleep(0.03)
    raise AssertionError(f"run did not complete: {latest!r}")


def _exhaust_filesystem(directory: Path) -> Path:
    """Fill the mounted filesystem until its kernel returns a real ENOSPC."""

    filler = directory / "qualification-enospc-filler"
    block = b"\0" * (128 * 1024)
    with filler.open("xb", buffering=0) as stream:
        while True:
            try:
                stream.write(block)
            except OSError as exc:
                assert exc.errno == errno.ENOSPC, exc
                break
        stream.flush()
        os.fsync(stream.fileno())
    return filler


def _assert_intact_without_faulted_run(state_root: Path, run_id: str) -> None:
    database = state_root / "state.sqlite"
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_commands WHERE run_id=?", (run_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_transitions WHERE run_id=?", (run_id,)
        ).fetchone() == (0,)
    finally:
        connection.close()


def _mount_tmpfs(path: Path) -> None:
    path.mkdir(mode=0o700)
    subprocess.run(
        ["mount", "-t", "tmpfs", "-o", f"size={_TMPFS_BYTES},mode=700", "tmpfs", str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stats = os.statvfs(path)
    assert stats.f_blocks * stats.f_frsize <= _TMPFS_BYTES


def _unmount_tmpfs(path: Path) -> None:
    result = subprocess.run(
        ["umount", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        return
    # The namespace is private and ends with this child pytest process.  A lazy
    # detach is therefore recoverable cleanup for an otherwise busy tmpfs; it
    # never touches a host mount or device.
    detached = subprocess.run(
        ["umount", "-l", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert detached.returncode == 0, f"normal={result.stderr!r} lazy={detached.stderr!r}"


def _qualification_in_namespace(tmp_path: Path) -> None:
    mountpoint = tmp_path / "bounded-tmpfs"
    _mount_tmpfs(mountpoint)
    state_root = mountpoint / "state"
    faulted_id = "enospc-recovered-id"
    first_host: subprocess.Popen[str] | None = None
    second_host: subprocess.Popen[str] | None = None
    filler: Path | None = None
    try:
        # Initialize the actual production Host before constraining its real
        # state filesystem.  The post-release recovery run below exercises the
        # full CLI -> Host -> FakeAgent -> Runner -> artifact path.
        first_host = _start_host(state_root)
        _stop_host(first_host, require_clean=True)
        first_host = None

        # Remove reusable WAL allocation before the real exhaustion.  This is a
        # normal SQLite maintenance operation on the disposable test database,
        # not a synthetic SQLite size limit or an injected failure.
        with sqlite3.connect(state_root / "state.sqlite") as connection:
            assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0

        first_host = _start_host(state_root)
        filler = _exhaust_filesystem(mountpoint)
        assert os.statvfs(mountpoint).f_bavail == 0

        # A new CREATE_RUN requires a production StateStore transaction.  The
        # Host receives actual ENOSPC from tmpfs.  It cannot acknowledge or
        # fabricate a completed run.
        fault = _cli(
            state_root,
            *_create_arguments(state_root, faulted_id),
            check=False,
        )
        assert fault.returncode == 2, (fault.stdout, fault.stderr)
        failure = json.loads(fault.stderr)
        assert failure["error"]["code"] == "operational_halt", failure
        assert "full" in failure["error"]["message"].lower(), failure
        deadline = time.monotonic() + 8
        while first_host.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert first_host.poll() == 2, first_host.communicate()

        filler.unlink()
        filler = None
        _assert_intact_without_faulted_run(state_root, faulted_id)
        _stop_host(first_host, require_clean=False)
        first_host = None

        # Restarting the exact Host over the released filesystem permits the
        # same uncommitted ID to close once through the full production path.
        second_host = _start_host(state_root)
        _create(state_root, faulted_id)
        recovered = _await_completed(state_root, faulted_id)
        assert recovered["result"]["canonical_outcome_count"] == 1  # type: ignore[index]
        _stop_host(second_host, require_clean=True)
        second_host = None

        oracle = _cli(
            state_root,
            "oracle",
            "--state-root",
            str(state_root),
            "--run-id",
            faulted_id,
        )
        report = json.loads(oracle.stdout)
        assert report["ok"] is True, report
        assert report["canonical_outcome_count"] == 1, report
        assert report["open_attempt_count"] == 0, report
        assert report["owned_process_residue"] == [], report
    finally:
        if filler is not None and filler.exists():
            filler.unlink()
        if first_host is not None:
            _stop_host(first_host, require_clean=False)
        if second_host is not None:
            _stop_host(second_host, require_clean=False)
        _unmount_tmpfs(mountpoint)


def test_enospc_fixture_uses_current_profile_start_parser(tmp_path: Path) -> None:
    from hypoloom.cli import build_parser

    state_root = tmp_path / "state"
    run_id = "enospc-recovered-id"
    args = build_parser().parse_args(_create_arguments(state_root, run_id))
    assert args.subcommand == "start"
    assert args.profile == "component-demo"
    assert args.profiles_root == str(_PROJECT_ROOT / "tests" / "fixtures" / "profiles")
    assert args.state_root == str(state_root)
    assert args.run_id == run_id
    assert args.request_id == f"{run_id}-create-1"
    assert args.expected_revision == 0
    assert args.literature_adapter == "offline"
    assert args.allow_gpu is False
    assert not state_root.exists()


def test_real_tmpfs_enospc_db_commit_preserves_transaction_boundary(tmp_path: Path) -> None:
    """Opt-in real-fs Q1 evidence; SIGKILL, IOERR and power loss stay out of scope."""

    if os.environ.get(_ENV) != "1":
        pytest.skip(f"set {_ENV}=1 to run the rootless disposable tmpfs qualification")
    if os.environ.get(_CHILD_ENV) == "1":
        _qualification_in_namespace(tmp_path)
        return

    missing = [name for name in ("unshare", "mount", "umount") if shutil.which(name) is None]
    assert not missing, f"qualification prerequisites are unavailable: {missing}"
    environment = os.environ.copy()
    environment[_CHILD_ENV] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    child_base = tmp_path / "namespace-pytest"
    result = subprocess.run(
        [
            "unshare",
            "-Urnm",
            "--",
            sys.executable,
            "-m",
            "pytest",
            "--noconftest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={child_base}",
            f"{Path(__file__).resolve()}::test_real_tmpfs_enospc_db_commit_preserves_transaction_boundary",
        ],
        cwd=_PROJECT_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, f"namespace qualification failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "1 passed" in result.stdout.lower(), result.stdout
