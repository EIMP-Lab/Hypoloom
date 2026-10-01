from __future__ import annotations

import http.client
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hypoloom.observer_services import ObserverServices
from hypoloom.notification_tailer import HTTPResponse
from test_notification_tailer import _create_profile_run, _notify
from test_web_observer import _row_census


def configured(tmp_path, *, dashboard=True, webhook=False, telegram=False, port=0):
    config = _notify(webhook=webhook, telegram=telegram)
    config["sinks"]["dashboard"] = {"enabled": dashboard, "host": "127.0.0.1", "port": port}
    if port is None:
        del config["sinks"]["dashboard"]["port"]
    return _create_profile_run(tmp_path, config)


def test_live_literature_transitions_reuse_local_log_without_research_writes_or_remote_delivery(tmp_path):
    from hypoloom.institutional_access import InstitutionalAccess
    from test_institutional_access import Backend, URL, pump
    root = configured(tmp_path, dashboard=False)
    before = _row_census(root / "state.sqlite")
    service = ObserverServices(root, output=io.StringIO(), errors=io.StringIO())
    access = InstitutionalAccess(Backend(), event_sink=service.record_literature_event)
    try:
        access.request(("notifications", "expert", "component"), URL)
        pump(access, lambda: access.peek(("notifications", "expert", "component"), URL)["reason"] == "login_required",
             runs=("notifications",))
        service.poll_once()
        log = service.output_root / "notifications/events.jsonl"
        records = [json.loads(line) for line in log.read_text().splitlines()]
        events = [e for e in records if e["type"] == "literature_access"]
        assert [e["payload"]["to"] for e in events] == ["queued", "checking", "login_required"]
        assert all(e["payload"]["provider"] == "Nature" and e["payload"]["url"] == URL for e in events)
        service.poll_once()
        assert log.read_text().count('"type": "literature_access"') == 3
        assert _row_census(root / "state.sqlite") == before
    finally:
        access.close()
        service.close()


def test_notifier_reuses_background_audit_without_blocking_delivery(tmp_path, monkeypatch):
    from hypoloom.oracle import Oracle
    root = configured(tmp_path, dashboard=False)
    entered, release = threading.Event(), threading.Event()
    callers = []

    def audit(self, run_id):
        callers.append(threading.get_ident())
        entered.set()
        release.wait(2)
        return {"ok": True, "issues": [], "run_id": run_id}

    monkeypatch.setattr(Oracle, "audit", audit)
    services = ObserverServices(root, output=io.StringIO(), errors=io.StringIO())
    try:
        services.poll_once()
        assert entered.wait(1)
        services.poll_once()
        assert len(callers) == 1
        assert callers[0] != threading.get_ident()
        assert not release.is_set(), "event delivery must finish before the audit"
        log = services.output_root / "notifications/events.jsonl"
        events = [json.loads(line) for line in log.read_text().splitlines()]
        assert events and len({row["event_id"] for row in events}) == len(events)
    finally:
        release.set()
        services.close()


def wait_for(predicate, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("service condition did not become true")


def request(server, *, authorized=True, token=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/events", headers={"Authorization": "Bearer " + (server.access_token if token is None else token)} if authorized else {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_profile_starts_one_authenticated_reader_and_disjoint_delivery_without_source_writes(tmp_path):
    root = configured(tmp_path)
    before = _row_census(root / "state.sqlite")
    files = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    output, errors = io.StringIO(), io.StringIO()
    services = ObserverServices(root, output=output, errors=errors, poll_interval=0.02)
    with services:
        wait_for(lambda: "notifications" in services.dashboards)
        server = services.dashboards["notifications"]
        wait_for(lambda: (tmp_path / "state-notifications/notifications/cursors/log.json").is_file())
        assert request(server, authorized=False)[0] == 401
        status, body = request(server)
        assert status == 200 and json.loads(body)["run_id"] == "notifications"
        services.start()
        assert services.dashboards["notifications"] is server
        assert output.getvalue().count("#token=") == 1
        assert server.access_token not in errors.getvalue()
        assert _row_census(root / "state.sqlite") == before
        assert {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} == files
    services.close()
    assert not services.running
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", server.server_port), timeout=0.1)


@pytest.mark.parametrize("port", [0, None])
def test_concurrent_dashboards_use_distinct_ports_tokens_and_lifetimes(tmp_path, port):
    services = []
    before = []
    barrier = threading.Barrier(2)

    def start(service):
        barrier.wait(timeout=5)
        service.poll_once()

    try:
        for index in range(2):
            directory = tmp_path / str(index)
            directory.mkdir()
            root = configured(directory, port=port)
            before.append(_row_census(root / "state.sqlite"))
            services.append(ObserverServices(root, output=io.StringIO(), errors=io.StringIO()))
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(start, services))
        for service in services:
            assert "notifications" in service.dashboards, service.errors.getvalue()
        first, second = [service.dashboards["notifications"] for service in services]
        assert 0 < first.server_port != second.server_port > 0
        assert first.access_token != second.access_token
        for index, server in enumerate((first, second)):
            status, body = request(server)
            assert status == 200 and json.loads(body)["run_id"] == "notifications"
            assert request(server, authorized=False)[0] == 401
            assert server.startup_url() in services[index].output.getvalue()
            assert f":{server.server_port}/" in server.startup_url()
        assert request(first, token=second.access_token)[0] == 401
        assert request(second, token=first.access_token)[0] == 401
        services[0].close()
        assert request(second)[0] == 200
    finally:
        for service in services:
            service.close()
    for index, service in enumerate(services):
        assert _row_census(service.state_root / "state.sqlite") == before[index]
        server = service.dashboards["notifications"]
        assert not service._web_threads["notifications"].is_alive()
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", server.server_port), timeout=0.1)


def test_managed_telegram_uses_actual_dynamic_port_without_access_token(tmp_path):
    root = configured(tmp_path, telegram=True)
    messages = []

    def transport(_url, document, **_kwargs):
        messages.append(document["text"])
        return HTTPResponse(200, b'{"ok":true}')

    services = ObserverServices(root, output=io.StringIO(), errors=io.StringIO(),
        environ={"TEST_BOT": "123:test-bot", "TEST_CHAT": "test-chat"}, transport=transport)
    try:
        services.poll_once()
        server = services.dashboards["notifications"]
        assert messages
        assert all(f"http://127.0.0.1:{server.server_port}/" in message for message in messages)
        assert all(server.access_token not in message and "#token=" not in message for message in messages)
        assert not services.errors.getvalue()
    finally:
        services.close()


def test_disabled_sinks_create_neither_listener_nor_output(tmp_path):
    root = _create_profile_run(tmp_path, {"sinks": {}, "events": {}})
    with ObserverServices(root, poll_interval=0.02) as services:
        services.poll_once()
        assert not services.dashboards
        assert not (tmp_path / "state-notifications").exists()


def test_port_conflict_is_visible_and_retries_same_port_without_stealing_listener(tmp_path):
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    port = occupied.getsockname()[1]
    root = configured(tmp_path, port=port)
    errors = io.StringIO()
    try:
        with ObserverServices(root, poll_interval=0.02, errors=errors) as services:
            wait_for(lambda: "dashboard" in errors.getvalue())
            assert not services.dashboards
            occupied.close()
            wait_for(lambda: "notifications" in services.dashboards)
            assert services.dashboards["notifications"].server_port == port
    finally:
        occupied.close()


def test_shutdown_releases_port_restart_rotates_token_and_reuses_delivery_cursor(tmp_path):
    root = configured(tmp_path)
    with ObserverServices(root, poll_interval=0.02) as first:
        wait_for(lambda: "notifications" in first.dashboards)
        token = first.dashboards["notifications"].access_token
        wait_for(lambda: (tmp_path / "state-notifications/notifications/events.jsonl").is_file())
    path = tmp_path / "state-notifications/notifications/events.jsonl"
    before = path.read_bytes()
    with ObserverServices(root, poll_interval=0.02) as second:
        wait_for(lambda: "notifications" in second.dashboards)
        assert second.dashboards["notifications"].access_token != token
    assert path.read_bytes() == before


def test_missing_secrets_and_overlapping_delivery_path_do_not_remove_dashboard(tmp_path):
    root = configured(tmp_path, telegram=True)
    errors = io.StringIO()
    with ObserverServices(root, environ={}, errors=errors, poll_interval=0.02) as services:
        wait_for(lambda: "TEST_BOT" in errors.getvalue())
        assert "notifications" in services.dashboards
    with ObserverServices(root, output_root=root / "delivery", errors=errors, poll_interval=0.02) as services:
        wait_for(lambda: "disjoint" in errors.getvalue())
        assert "notifications" in services.dashboards
    assert not (root / "delivery").exists()


def test_missing_state_is_never_initialized_by_services(tmp_path):
    root = tmp_path / "absent"
    errors = io.StringIO()
    with ObserverServices(root, errors=errors, poll_interval=0.02):
        wait_for(lambda: "observation" in errors.getvalue())
    assert not root.exists()


def test_owner_exit_polls_once_more_even_before_the_next_periodic_tick(tmp_path, monkeypatch):
    from hypoloom.notification_tailer import DeliveryReport
    root = configured(tmp_path, dashboard=False)
    calls = []
    def poll(tailer):
        calls.append(tailer.run_id)
        return DeliveryReport()
    monkeypatch.setattr("hypoloom.observer_services.NotificationTailer.poll_once", poll)
    with ObserverServices(root, poll_interval=60):
        wait_for(lambda: len(calls) == 1)
    assert calls == ["notifications", "notifications"]


def _cancel_fresh_fixture(host, run_id):
    from hypoloom.control_protocol import ControlRequest
    response = host.handle(ControlRequest(schema_version=1, request_id="cancel-" + run_id, expected_revision=None,
        command="CANCEL", payload={"run_id": run_id, "cause": "fresh service fixture boundary"}))
    assert response.status == "accepted", response
    host._drive_stopping(run_id)
    assert host.store.latest_transition(run_id)["state"] == "CANCELLED"


def _create_second_fixture(host, tmp_path):
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.profile_admission import admit_profile
    report = admit_profile(tmp_path / "profile-v3", run_id="new-run")
    assert report.admitted, report.to_dict()
    response = host.handle(ControlRequest(schema_version=1, request_id="create-new-run", expected_revision=0,
        command="CREATE_RUN", payload=report.create_run_payload))
    assert response.status == "accepted", response


@pytest.mark.parametrize("created_after_start", [False, True])
def test_startup_excludes_existing_terminal_runs_but_keeps_new_fast_terminal_scope(tmp_path, created_after_start):
    from hypoloom.workflow_host import WorkflowHost
    root = configured(tmp_path)
    host = WorkflowHost(root)
    try:
        host.initialize()
        _cancel_fresh_fixture(host, "notifications")
        if not created_after_start:
            _create_second_fixture(host, tmp_path)
        with ObserverServices(root, poll_interval=60) as services:
            services.poll_once()
            assert "notifications" not in services.dashboards
            if created_after_start:
                _create_second_fixture(host, tmp_path)
                # This run is created and closes between the coordinator's polls.
                _cancel_fresh_fixture(host, "new-run")
            services.poll_once()
            assert set(services.dashboards) == {"new-run"}
            if not created_after_start:
                _cancel_fresh_fixture(host, "new-run")
            assert request(services.dashboards["new-run"])[0] == 200
        assert not (tmp_path / "state-notifications/notifications").exists()
        rows = [json.loads(line) for line in (tmp_path / "state-notifications/new-run/events.jsonl").read_text().splitlines()]
        assert rows and {row["run_id"] for row in rows} == {"new-run"}
    finally:
        host.close()


def test_cli_passes_lifetime_factory_without_starting_readers_before_host_lock(tmp_path, monkeypatch):
    from hypoloom import cli
    observed = {}
    def serve(state_root, **options):
        observed.update(root=state_root, **options)
        return 23
    monkeypatch.setattr("hypoloom.workflow_host.serve", serve)
    assert cli.main(["serve", "--state-root", str(tmp_path / "state"), "--notification-output-root", str(tmp_path / "delivery")]) == 23
    services = observed["services_factory"]()
    assert isinstance(services, ObserverServices)
    assert services.output_root == tmp_path / "delivery"
    assert not services.running
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("credentials_file", [False, True, "profile"])
def test_actual_cli_lifetime_loopback_retry_terminal_dashboard_and_final_delivery(tmp_path, credentials_file):
    from hypoloom.state_store import StateStore

    received = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.headers.get("Authorization") == "Bearer loopback-only-secret"
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(503 if len(received) == 1 else 204)
            self.end_headers()
        def log_message(self, *_):
            pass
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    reader = threading.Thread(target=receiver.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    reader.start()
    process = None
    try:
        config = _notify(webhook=True)
        config["sinks"]["dashboard"] = {"enabled": True, "host": "127.0.0.1", "port": 0}
        config["events"]["webhook"] = config["events"]["log"] = ["run_started", "run_finished"]
        if credentials_file == "profile":
            config["sinks"]["webhook"] = {"enabled": True,
                "url": f"http://127.0.0.1:{receiver.server_port}/events", "token": "loopback-only-secret"}
        root = _create_profile_run(tmp_path, config)
        stdout, stderr = tmp_path / "host.stdout", tmp_path / "host.stderr"
        notification_env = {"TEST_WEBHOOK_URL": f"http://127.0.0.1:{receiver.server_port}/events",
                            "TEST_WEBHOOK_TOKEN": "loopback-only-secret"}
        flags = []
        if credentials_file is True:
            private = tmp_path / "notifications.env"
            private.write_text("\n".join(key + "=" + value for key, value in notification_env.items()) + "\n")
            private.chmod(0o600)
            flags = ["--notification-credentials-file", str(private)]
            notification_env = {"TEST_WEBHOOK_URL": "http://127.0.0.1:1/wrong", "TEST_WEBHOOK_TOKEN": "wrong-ambient-value"}
        elif credentials_file == "profile":
            notification_env = {"TEST_WEBHOOK_URL": "http://127.0.0.1:1/wrong", "TEST_WEBHOOK_TOKEN": "wrong-ambient-value"}
        with stdout.open("w") as output, stderr.open("w") as errors:
            process = subprocess.Popen([sys.executable, "-m", "hypoloom.cli", "serve", "--state-root", str(root), *flags], stdout=output, stderr=errors,
                env={**os.environ, **notification_env})
            wait_for(lambda: "#token=" in stdout.read_text() or process.poll() is not None, timeout=15)
            assert process.poll() is None, stdout.read_text() + stderr.read_text()
            match = re.search(r"http://127\.0\.0\.1:(\d+)/#token=([^\s]+)", stdout.read_text())
            assert match is not None
            port, token = int(match.group(1)), match.group(2)
            assert port > 0
            duplicate = subprocess.run([sys.executable, "-m", "hypoloom.cli", "serve", "--state-root", str(root)],
                capture_output=True, text=True, timeout=10)
            assert duplicate.returncode == 2 and "another Workflow Host" in duplicate.stderr
            assert "#token=" not in duplicate.stdout + duplicate.stderr
            wait_for(lambda: StateStore(root).latest_transition("notifications")["state"] in {"FAILED", "COMPLETED", "CANCELLED"}, timeout=45)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("GET", "/api/events", headers={"Authorization": "Bearer " + token})
                response = connection.getresponse()
                assert response.status == 200
                assert any(event["type"] == "run_finished" for event in json.loads(response.read())["events"])
            finally:
                connection.close()
            # No --exit-when-terminal: the dashboard stays usable at the terminal
            # state. Its owner now exits normally and performs the final poll.
            process.terminate()
            assert process.wait(timeout=30) == 0
        assert len(received) >= 3 and received[0] == received[1]
        assert {event["type"] for event in received} == {"run_started", "run_finished"}
        assert "503" in stderr.read_text()
        assert "loopback-only-secret" not in stdout.read_text() + stderr.read_text()
        if credentials_file == "profile":
            # Check actual DB/artifacts/Agent inputs, then both support scopes.
            for path in root.rglob("*"):
                if path.is_file():
                    assert b"loopback-only-secret" not in path.read_bytes(), path
            from hypoloom.support_bundle import build_support_bundle
            for private in (False, True):
                report, files = build_support_bundle(root, "notifications", include_private_data=private)
                assert "loopback-only-secret" not in json.dumps(report)
                assert all(b"loopback-only-secret" not in content for content in files.values())
        assert (tmp_path / "state-notifications/notifications/cursors/webhook.json").is_file()
        with socket.socket() as reused:
            reused.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reused.bind(("127.0.0.1", port))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        receiver.shutdown()
        receiver.server_close()
        reader.join(timeout=5)
