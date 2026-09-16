from __future__ import annotations

import json
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from hypoloom.domain import FACT_SCHEMA_VERSION
from hypoloom.monitor import build_monitor_snapshot
from hypoloom.runner import process_group_exists
from hypoloom.state_store import StateStore


_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hypoloom.cli", *arguments],
        cwd=_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def test_public_profile_start_reaches_clean_three_role_component_closure(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id = "operator-cutover-demo"
    host = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypoloom.cli",
            "serve",
            "--state-root",
            str(state_root),
        ],
        cwd=_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if (state_root / "control.sock").is_socket():
                break
            if host.poll() is not None:
                stdout, stderr = host.communicate()
                raise AssertionError(f"Host exited before serving: {stdout=} {stderr=}")
            time.sleep(0.02)
        else:
            raise AssertionError("Host did not create its control socket")

        started = _run_cli(
            "start",
            "component-demo",
            "--cpu-only",
            "--profiles-root",
            str(_ROOT / "tests/fixtures/profiles"),
            "--state-root",
            str(state_root),
            "--run-id",
            run_id,
        )
        assert started.returncode == 0, started.stderr
        assert json.loads(started.stdout)["result"]["run_state"] == "CREATED"

        projection: dict[str, object] | None = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            observed = _run_cli(
                "monitor",
                "--state-root",
                str(state_root),
                "--run-id",
                run_id,
                "--json",
            )
            if observed.returncode == 0:
                projection = json.loads(observed.stdout)
                if projection["status"]["run_state"] == "COMPLETED":
                    break
            if host.poll() is not None:
                stdout, stderr = host.communicate()
                raise AssertionError(f"Host exited while driving: {stdout=} {stderr=}")
            time.sleep(0.03)
        else:
            raise AssertionError(f"run did not complete: {projection!r}")

        assert projection is not None
        assert projection["fact_counts"]["CanonicalOutcomeAccepted"] == 1
        assert projection["goal"]["schema_version"] == FACT_SCHEMA_VERSION
        contract = projection["goal"]["research_contract"]
        assert [role["id"] for role in contract["roles"]] == [
            "scientist-a",
            "scientist-b",
            "scientist-c",
        ]
        assert [component["id"] for component in contract["components"]] == [
            "primary.score"
        ]
        assert projection["oracle"]["ok"] is True
        assert projection["oracle"]["issues"] == []
        assert projection["oracle"]["owned_process_residue"] == []
        assert host.poll() is None
    finally:
        if host.poll() is None:
            os.killpg(host.pid, signal.SIGTERM)
        try:
            host.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(host.pid, signal.SIGKILL)
            host.communicate(timeout=5)


def test_managed_host_waits_for_a_run_then_exits_after_terminal_closure(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "managed-state"
    run_id = "managed-terminal-exit"
    host = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypoloom.cli",
            "serve",
            "--state-root",
            str(state_root),
            "--exit-when-terminal",
        ],
        cwd=_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if (state_root / "control.sock").is_socket():
                break
            if host.poll() is not None:
                stdout, stderr = host.communicate()
                raise AssertionError(
                    f"managed Host exited before serving: {stdout=} {stderr=}"
                )
            time.sleep(0.02)
        else:
            raise AssertionError("managed Host did not create its control socket")

        time.sleep(0.1)
        assert host.poll() is None

        started = _run_cli(
            "start",
            "component-demo",
            "--cpu-only",
            "--profiles-root",
            str(_ROOT / "tests/fixtures/profiles"),
            "--state-root",
            str(state_root),
            "--run-id",
            run_id,
        )
        assert started.returncode == 0, started.stderr

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and host.poll() is None:
            time.sleep(0.03)
        assert host.poll() == 0
        stdout, stderr = host.communicate(timeout=5)
        assert stdout == ""
        assert stderr == ""
        assert not (state_root / "control.sock").exists()

        observed = _run_cli(
            "monitor",
            "--state-root",
            str(state_root),
            "--run-id",
            run_id,
            "--json",
        )
        assert observed.returncode == 0, observed.stderr
        projection = json.loads(observed.stdout)
        assert projection["status"]["run_state"] == "COMPLETED"
        assert projection["durable_open_attempts_state_root"] == 0
        assert projection["oracle"]["ok"] is True
        assert projection["oracle"]["owned_process_residue"] == []
    finally:
        if host.poll() is None:
            os.killpg(host.pid, signal.SIGTERM)
        try:
            host.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(host.pid, signal.SIGKILL)
            host.communicate(timeout=5)


def test_tui_leave_reenter_and_cancel_drains_real_cpu_before_managed_host_exit(tmp_path: Path) -> None:
    from textual.widgets import Button
    from hypoloom.tui import HypoloomVNextMonitor

    state_root = tmp_path / "cancel-state"
    run_id = "tui-cpu-cancel"
    host = subprocess.Popen(
        [sys.executable, "-m", "hypoloom.cli", "serve", "--state-root",
            str(state_root), "--exit-when-terminal"],
        cwd=_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 8
        while not (state_root / "control.sock").is_socket():
            assert host.poll() is None, host.communicate()
            assert time.monotonic() < deadline, "Host did not create its control socket"
            time.sleep(.02)
        started = _run_cli("start", "component-demo", "--cpu-only", "--profiles-root",
            str(_ROOT / "tests/fixtures/profiles"), "--state-root", str(state_root),
            "--run-id", run_id, "--process-delay", "60")
        assert started.returncode == 0, started.stderr
        with StateStore(state_root) as store:
            deadline = time.monotonic() + 15
            while True:
                live = store.inspect("SELECT a.* FROM execution_attempts a "
                    "JOIN workflow_commands c USING(command_id) "
                    "WHERE c.command_type='EXECUTE' AND a.state='OPEN' AND a.process_group_id IS NOT NULL")
                if live:
                    break
                assert host.poll() is None, host.communicate()
                assert time.monotonic() < deadline, store.latest_transition(run_id)
                time.sleep(.02)
        groups = [row["process_group_id"] for row in live]
        assert all(process_group_exists(group) for group in groups)

        async def leave_monitor():
            app = HypoloomVNextMonitor(state_root, run_id,
                initial_snapshot=build_monitor_snapshot(state_root, run_id),
                interval=3600, web_enabled=False)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("q")

        asyncio.run(leave_monitor())
        assert host.poll() is None
        assert all(process_group_exists(group) for group in groups)
        with StateStore(state_root) as store:
            assert not store.inspect("SELECT command_id FROM workflow_commands WHERE command_type='CANCEL'")

        async def end_run():
            app = HypoloomVNextMonitor(state_root, run_id,
                initial_snapshot=build_monitor_snapshot(state_root, run_id),
                interval=.1, web_enabled=False)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("x")
                assert not app.screen.query_one("#end-run-confirm", Button).disabled
                await pilot.click("#end-run-confirm")
                await pilot.pause()
                assert app.screen.query_one("#end-run-confirm", Button).disabled
                assert app._end_run_draft.get("accepted_revision") is not None

        asyncio.run(end_run())
        stdout, stderr = host.communicate(timeout=20)
        assert host.returncode == 0, (stdout, stderr)
        assert not (state_root / "control.sock").exists()
        with StateStore(state_root) as store:
            cancels = store.inspect("SELECT * FROM workflow_commands WHERE command_type='CANCEL'")
            assert len(cancels) == 1 and cancels[0]["response_status"] == "accepted"
            attempts = store.list_attempts()
            assert all(row["state"] == "CLOSED" for row in attempts)
            processes = [row for row in attempts if row["attempt_id"] in {item["attempt_id"] for item in live}]
            assert processes and all(row["terminal_reason"] == "CANCELLED" for row in processes)
            assert all(not process_group_exists(row["process_group_id"])
                for row in attempts if row["process_group_id"])
        snapshot = build_monitor_snapshot(state_root, run_id)
        assert snapshot.status["run_state"] == "CANCELLED"
        assert snapshot.durable_open_attempts_state_root == 0
        assert snapshot.oracle["ok"] is True, snapshot.oracle
        assert snapshot.oracle["owned_process_residue"] == []
        (tmp_path / "cancel-monitor.json").write_text(snapshot.to_json() + "\n", encoding="utf-8")
    finally:
        if host.poll() is None:
            os.killpg(host.pid, signal.SIGTERM)
        try:
            host.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(host.pid, signal.SIGKILL)
            host.communicate(timeout=5)
