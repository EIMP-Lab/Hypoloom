from __future__ import annotations

import copy
import http.client
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from hypoloom.control_protocol import (
    ControlRequest,
    ControlResponse,
    receive_message,
    request_over_socket,
    send_message,
)
from hypoloom.monitor import HostStatusBackoff, build_monitor_snapshot, build_observer_snapshot
from hypoloom.profile_admission import admit_profile
from hypoloom.web_observer import create_server
from hypoloom.workflow_host import WorkflowHost


_REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture
def short_status_parent() -> Iterator[Path]:
    # These one-response test listeners bind an actual AF_UNIX pathname.
    # Descriptive pytest leaves must not consume its 107-byte Linux limit.
    with tempfile.TemporaryDirectory(prefix="pms-", dir="/tmp") as directory:
        yield Path(directory)


def _create_run(state_root: Path, run_id: str) -> None:
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        delay_seconds=0.0,
    )
    assert admission.admitted and admission.create_run_payload is not None
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        response = host.handle(ControlRequest(
            1,
            f"create-{run_id}",
            0,
            "CREATE_RUN",
            copy.deepcopy(admission.create_run_payload),
        ))
        assert response.status == "accepted"
    finally:
        host.close()


def _row_census(database: Path) -> dict[str, int]:
    with sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True,
    ) as connection:
        tables = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return {
            table: int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in tables
        }


def _one_status_response(
    socket_path: Path,
    result: dict[str, object],
    *,
    response_delay_seconds: float = 0.0,
) -> tuple[threading.Thread, list[ControlRequest], list[BaseException]]:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    requests: list[ControlRequest] = []
    errors: list[BaseException] = []

    def respond() -> None:
        try:
            with listener:
                peer, _ = listener.accept()
                with peer:
                    request = ControlRequest.from_mapping(receive_message(peer))
                    requests.append(request)
                    if response_delay_seconds:
                        time.sleep(response_delay_seconds)
                    send_message(
                        peer,
                        ControlResponse.accepted(
                            request.request_id,
                            int(result["run_revision"]),
                            result,
                        ).to_mapping(),
                    )
        except BaseException as exc:  # pragma: no cover - asserted by caller
            errors.append(exc)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    return thread, requests, errors


def test_observer_snapshot_default_timeout_tolerates_busy_host_tick(
    short_status_parent: Path, monkeypatch,
) -> None:
    state_root = short_status_parent / "busy-host-observer"
    run_id = "busy-host-observer"
    _create_run(state_root, run_id)
    offline = build_monitor_snapshot(state_root, run_id)
    live = dict(offline.status)
    log = state_root / "existing-events.jsonl"
    log.write_text(json.dumps({"run_id": run_id, "type": "literature_access",
        "payload": {"provider": "IEEE", "to": "available", "reason_code": "document_verified"}}) + "\n")
    live.update(literature_access={"enabled": True, "groups": []}, notifications={"local_log_path": str(log)})
    live.update({
        "quiescent_for_backup": False,
        "agent_usage": {"observed_lower_bound": {"tokens": 7, "turns": 1}},
        "resource_waits": [],
    })
    monkeypatch.setattr(
        "hypoloom.monitor.build_monitor_snapshot",
        lambda _state_root, _run_id: offline,
    )
    socket_path = state_root / "control.sock"
    thread, requests, errors = _one_status_response(
        socket_path,
        live,
        response_delay_seconds=0.4,
    )
    try:
        snapshot = build_observer_snapshot(state_root, run_id)
    finally:
        thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert not thread.is_alive()
    assert errors == []
    assert len(requests) == 1
    assert snapshot.status_source == "offline-state-store+resident-host-status"
    assert snapshot.overview["host_observation"]["availability"] == "responded"
    assert snapshot.status["agent_usage"] == live["agent_usage"]
    assert snapshot.status["literature_access"]["history"]["document_verified"] == {"IEEE": 1}


def test_observer_snapshot_default_timeout_preserves_offline_view_when_host_is_silent(
    short_status_parent: Path, monkeypatch,
) -> None:
    state_root = short_status_parent / "silent-host-observer"
    run_id = "silent-host-observer"
    _create_run(state_root, run_id)
    before = _row_census(state_root / "state.sqlite")
    offline = build_monitor_snapshot(state_root, run_id)
    monkeypatch.setattr(
        "hypoloom.monitor.build_monitor_snapshot",
        lambda _state_root, _run_id: offline,
    )
    socket_path = state_root / "control.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    listener.settimeout(5)
    release = threading.Event()
    requests: list[ControlRequest] = []
    errors: list[BaseException] = []

    def accept_without_reply() -> None:
        try:
            with listener:
                peer, _ = listener.accept()
                with peer:
                    peer.settimeout(5)
                    requests.append(ControlRequest.from_mapping(receive_message(peer)))
                    # Outlast the probe window but still clean up if it regresses.
                    release.wait(6)
        except BaseException as exc:  # pragma: no cover - asserted by caller
            errors.append(exc)

    thread = threading.Thread(target=accept_without_reply, daemon=True)
    thread.start()
    try:
        started_at = time.monotonic()
        snapshot = build_observer_snapshot(state_root, run_id)
        elapsed = time.monotonic() - started_at
    finally:
        release.set()
        thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert not thread.is_alive()
    assert errors == []
    assert elapsed < 4.5
    assert len(requests) == 1
    assert requests[0].command == "STATUS"
    assert requests[0].expected_revision is None
    assert requests[0].payload == {"run_id": run_id}
    assert snapshot is offline
    assert _row_census(state_root / "state.sqlite") == before


def test_observer_snapshot_uses_exact_read_only_host_status_when_available(
    short_status_parent: Path, monkeypatch,
) -> None:
    state_root = short_status_parent / "live-observer"
    run_id = "live-observer"
    _create_run(state_root, run_id)
    before = _row_census(state_root / "state.sqlite")

    offline = build_observer_snapshot(state_root, run_id)
    assert offline.status_source == "offline-state-store"
    assert offline.status["agent_usage"] is None
    assert offline.status.get("agent_scheduling") is None
    live = dict(offline.status)
    live.update({
        "state_sqlite_bytes": int(offline.status["state_sqlite_bytes"]) + 1,
        "quiescent_for_backup": True,
        "agent_scheduling": {"observed_at": offline.observed_at, "tasks": [
            {"command_id": "waiting-task", "role": "expert", "reason": "chain_capacity"}]},
        "agent_usage": {
            "observed_lower_bound": {
                "responses": 0,
                "provider_error_reports": 0,
                "tokens": 0,
                "turns": 0,
                "cost": "0",
                "currency": "USD",
            },
        },
        "resource_waits": [
            {"experiment_id": "component-workflow", "reason": "GPU busy"},
        ],
        "dispatch_error": {"exception_type": "ValueError", "phase": "dispatch", "host_generation": "fixture"},
    })
    monkeypatch.setattr(
        "hypoloom.monitor.build_monitor_snapshot",
        lambda _state_root, _run_id: offline,
    )
    socket_path = state_root / "control.sock"
    thread, requests, errors = _one_status_response(socket_path, live)
    try:
        snapshot = build_observer_snapshot(state_root, run_id, host_timeout=1.0)
    finally:
        thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert not thread.is_alive()
    assert errors == []
    assert len(requests) == 1
    assert requests[0].command == "STATUS"
    assert requests[0].expected_revision is None
    assert requests[0].payload == {"run_id": run_id}
    assert snapshot.status_source == "offline-state-store+resident-host-status"
    assert snapshot.overview["host_observation"]["availability"] == "responded"
    assert offline.overview["host_observation"]["availability"] == "absent"
    assert snapshot.status["dispatch_error"] == live["dispatch_error"]
    assert snapshot.status["quiescent_for_backup"] is True
    assert snapshot.status["agent_usage"] == live["agent_usage"]
    assert snapshot.status["agent_scheduling"] == live["agent_scheduling"]
    assert snapshot.status["resource_waits"] == live["resource_waits"]
    assert snapshot.status["state_sqlite_bytes"] == offline.status["state_sqlite_bytes"]
    assert snapshot.observed_at == offline.observed_at
    assert _row_census(state_root / "state.sqlite") == before


def test_observer_snapshot_rejects_mismatched_host_status_and_falls_back(
    short_status_parent: Path,
) -> None:
    state_root = short_status_parent / "mismatched-observer"
    run_id = "mismatched-observer"
    _create_run(state_root, run_id)
    offline = build_observer_snapshot(state_root, run_id)
    mismatched = dict(offline.status)
    mismatched.update({
        "run_id": "different-run",
        "quiescent_for_backup": True,
        "agent_usage": {},
        "resource_waits": [],
    })
    socket_path = state_root / "control.sock"
    thread, _, errors = _one_status_response(socket_path, mismatched)
    try:
        snapshot = build_observer_snapshot(state_root, run_id, host_timeout=1.0)
    finally:
        thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert errors == []
    assert snapshot.status_source == "offline-state-store"
    assert snapshot.status["agent_usage"] is None


def test_observer_snapshot_rejects_a_later_durable_host_projection(
    short_status_parent: Path, monkeypatch,
) -> None:
    state_root = short_status_parent / "later-host-projection"
    run_id = "later-host-projection"
    _create_run(state_root, run_id)
    offline = build_monitor_snapshot(state_root, run_id)
    live = dict(offline.status)
    live.update({
        "run_state": "COMPLETED",
        "run_revision": int(offline.status["run_revision"]) + 6,
        "experiment_states": ["TERMINAL"],
        "canonical_outcome_count": 1,
        "goal_verdict": "GOAL_MET",
        "required_assessment_count": 1,
        "quiescent_for_backup": True,
        "agent_usage": {"observed_lower_bound": {"tokens": 7, "turns": 1}},
        "resource_waits": [],
    })
    monkeypatch.setattr(
        "hypoloom.monitor.build_monitor_snapshot",
        lambda _state_root, _run_id: offline,
    )
    socket_path = state_root / "control.sock"
    thread, requests, errors = _one_status_response(socket_path, live)
    try:
        snapshot = build_observer_snapshot(state_root, run_id, host_timeout=1.0)
    finally:
        thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert not thread.is_alive()
    assert errors == []
    assert len(requests) == 1
    assert snapshot.status_source == "offline-state-store"
    assert snapshot.status == offline.status
    assert snapshot.observed_at == offline.observed_at


def test_web_observer_default_snapshot_builder_uses_same_host_status_enrichment(
    short_status_parent: Path,
) -> None:
    state_root = short_status_parent / "web-live-observer"
    run_id = "web-live-observer"
    _create_run(state_root, run_id)
    before = _row_census(state_root / "state.sqlite")
    offline = build_monitor_snapshot(state_root, run_id)
    live = dict(offline.status)
    live.update({
        "quiescent_for_backup": True,
        "agent_usage": {"observed_lower_bound": {"tokens": 7, "turns": 1}},
        "resource_waits": [],
    })
    socket_path = state_root / "control.sock"
    status_thread, requests, errors = _one_status_response(socket_path, live)
    server = create_server(
        state_root,
        run_id,
        port=0,
        token="test-only-live-status-token",
    )
    web_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    web_thread.start()
    host, port = server.server_address[:2]
    connection = http.client.HTTPConnection(str(host), int(port), timeout=5)
    try:
        connection.request(
            "GET",
            "/api/snapshot",
            headers={"Authorization": "Bearer test-only-live-status-token"},
        )
        response = connection.getresponse()
        assert response.status == 200
        payload = json.loads(response.read())
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        web_thread.join(timeout=5)
        status_thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert not web_thread.is_alive()
    assert not status_thread.is_alive()
    assert errors == []
    assert len(requests) == 1 and requests[0].command == "STATUS"
    assert payload["status_source"] == "offline-state-store+resident-host-status"
    assert payload["status"]["agent_usage"] == live["agent_usage"]
    assert _row_census(state_root / "state.sqlite") == before


def test_observer_snapshot_validates_live_status_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="host_timeout"):
        build_observer_snapshot(tmp_path / "unused", "unused", host_timeout=0)


def test_host_status_backoff_defers_attempts_exponential_with_cap_and_resets() -> None:
    now = [100.0]
    backoff = HostStatusBackoff(base_seconds=3.0, cap_seconds=10.0, clock=lambda: now[0])
    assert backoff.allows_attempt()
    assert backoff.consecutive_failures == 0
    backoff.record_failure()
    assert backoff.consecutive_failures == 1
    assert not backoff.allows_attempt()
    now[0] = 102.9
    assert not backoff.allows_attempt()
    now[0] = 103.0
    assert backoff.allows_attempt()
    backoff.record_failure()
    now[0] = 108.9
    assert not backoff.allows_attempt()
    now[0] = 109.0
    assert backoff.allows_attempt()
    backoff.record_failure()  # The 12-second step is capped at 10.
    now[0] = 118.9
    assert not backoff.allows_attempt()
    now[0] = 119.0
    assert backoff.allows_attempt()
    backoff.record_success()
    assert backoff.consecutive_failures == 0
    assert backoff.allows_attempt()
    with pytest.raises(ValueError, match="base_seconds"):
        HostStatusBackoff(base_seconds=0)
    with pytest.raises(ValueError, match="cap_seconds"):
        HostStatusBackoff(base_seconds=5.0, cap_seconds=4.0)


def test_observer_snapshot_status_backoff_defers_retry_and_recovers(
    short_status_parent: Path, monkeypatch,
) -> None:
    state_root = short_status_parent / "backoff-observer"
    run_id = "backoff-observer"
    _create_run(state_root, run_id)
    offline = build_monitor_snapshot(state_root, run_id)
    live = dict(offline.status)
    live.update({
        "quiescent_for_backup": False,
        "agent_usage": {"observed_lower_bound": {"tokens": 7, "turns": 1}},
        "resource_waits": [],
    })
    monkeypatch.setattr(
        "hypoloom.monitor.build_monitor_snapshot",
        lambda _state_root, _run_id: offline,
    )
    now = [1000.0]
    backoff = HostStatusBackoff(base_seconds=30.0, cap_seconds=120.0, clock=lambda: now[0])
    socket_path = state_root / "control.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(8)
    listener.settimeout(0.05)
    stop = threading.Event()
    accepted: list[socket.socket] = []
    errors: list[BaseException] = []

    def hold_connections() -> None:
        try:
            with listener:
                while not stop.is_set():
                    try:
                        peer, _ = listener.accept()
                    except TimeoutError:
                        continue
                    accepted.append(peer)  # Held open but never answered.
        except BaseException as exc:  # pragma: no cover - asserted by caller
            errors.append(exc)

    thread = threading.Thread(target=hold_connections, daemon=True)
    thread.start()
    try:
        first = build_observer_snapshot(
            state_root, run_id, host_timeout=0.2, _host_backoff=backoff,
        )
        deadline = time.monotonic() + 2
        while not accepted and time.monotonic() < deadline:
            time.sleep(0.01)
        deferred = build_observer_snapshot(
            state_root, run_id, host_timeout=0.2, _host_backoff=backoff,
        )
        time.sleep(0.3)
    finally:
        stop.set()
        thread.join(timeout=2)
        for peer in accepted:
            peer.close()
        socket_path.unlink(missing_ok=True)

    assert not thread.is_alive()
    assert errors == []
    assert first is offline and deferred is offline
    assert len(accepted) == 1  # The deferred poll never touched the socket.
    assert backoff.consecutive_failures == 1

    now[0] += 30.0
    status_thread, requests, status_errors = _one_status_response(socket_path, live)
    try:
        recovered = build_observer_snapshot(
            state_root, run_id, host_timeout=1.0, _host_backoff=backoff,
        )
    finally:
        status_thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert not status_thread.is_alive()
    assert status_errors == []
    assert len(requests) == 1
    assert backoff.consecutive_failures == 0
    assert recovered.status_source == "offline-state-store+resident-host-status"

    # A reset backoff allows an immediate follow-up attempt.
    follow_thread, follow_requests, follow_errors = _one_status_response(socket_path, live)
    try:
        followup = build_observer_snapshot(
            state_root, run_id, host_timeout=1.0, _host_backoff=backoff,
        )
    finally:
        follow_thread.join(timeout=2)
        socket_path.unlink(missing_ok=True)

    assert not follow_thread.is_alive()
    assert follow_errors == []
    assert len(follow_requests) == 1
    assert followup.status_source == "offline-state-store+resident-host-status"


def test_monitor_projects_existing_typed_rendezvous_without_writes(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "rendezvous-observer"
    run_id = "rendezvous-observer"
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        delay_seconds=0.0,
    )
    assert admission.admitted and admission.create_run_payload is not None
    payload = copy.deepcopy(admission.create_run_payload)
    payload["research_contract"]["methodology"]["controls"][
        "experiment_campaigns"
    ] = {
        "enabled": True,
        "required_roles": ["preflight", "analyze"],
        "role_owners": {
            "*": {"preflight": "scientist-b", "analyze": "scientist-c"},
        },
    }
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            run_head = next(
                row
                for row in host.store.list_workflow_heads(run_id)
                if row["workflow_kind"] == "RUN"
            )
            if run_head["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.001)
        assert run_head["state"] == "COMPLETED"
        before = _row_census(state_root / "state.sqlite")

        snapshot = build_monitor_snapshot(state_root, run_id)

        assert snapshot.schema_version == 2
        assert [(item.phase, item.kind, item.status) for item in snapshot.rendezvous] == [
            ("preflight", "HELP", "RESOLVED"),
            ("analyze", "DEBATE", "RESOLVED"),
        ]
        assert [item.requester_role for item in snapshot.rendezvous] == [
            "scientist-a", "scientist-a",
        ]
        assert [item.target_role for item in snapshot.rendezvous] == [
            "scientist-b", "scientist-c",
        ]
        assert {item.disposition for item in snapshot.rendezvous} == {"ANSWERED"}
        assert all(item.question and item.summary for item in snapshot.rendezvous)
        assert _row_census(state_root / "state.sqlite") == before
    finally:
        host.close()


def test_observer_snapshot_reads_status_from_real_resident_host_process(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "resident-host-observer"
    run_id = "resident-host-observer"
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        delay_seconds=0.0,
    )
    assert admission.admitted and admission.create_run_payload is not None
    environment = os.environ.copy()
    environment.update({
        "HYPOLOOM_RUNTIME_FAMILY": "hypoloom",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypoloom.cli",
            "serve",
            "--state-root",
            str(state_root),
        ],
        cwd=_REPOSITORY,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    socket_path = state_root / "control.sock"
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if socket_path.is_socket():
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"Host exited before serving: {stdout=!r} {stderr=!r}"
                )
            time.sleep(0.02)
        else:
            raise AssertionError("Host did not create control.sock")

        created = request_over_socket(
            socket_path,
            ControlRequest(
                1,
                f"create-{run_id}",
                0,
                "CREATE_RUN",
                copy.deepcopy(admission.create_run_payload),
            ),
            timeout=5,
        )
        assert created.status == "accepted"

        observation_deadline = time.monotonic() + 8
        while time.monotonic() < observation_deadline:
            snapshot = build_observer_snapshot(state_root, run_id, host_timeout=2.0)
            if snapshot.status_source.endswith("+resident-host-status"):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Host projection did not stabilize for enrichment")

        assert snapshot.status_source == (
            "offline-state-store+resident-host-status"
        )
        assert snapshot.status["run_id"] == run_id
        assert isinstance(snapshot.status["quiescent_for_backup"], bool)
        assert isinstance(snapshot.status["agent_usage"], dict)
        assert isinstance(snapshot.status["resource_waits"], list)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=8)
            raise AssertionError(
                f"Host ignored SIGTERM: {stdout=!r} {stderr=!r}"
            )
    assert process.returncode == 0, (stdout, stderr)
