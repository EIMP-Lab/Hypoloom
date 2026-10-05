"""One explicit init/run identity owns client discovery; legacy scans cannot attach."""
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace

import pytest

from hypoloom.control_protocol import ControlRequest, ControlResponse, request_over_socket
from hypoloom import institutional_client as client
from hypoloom.institutional_runtime import RunAccess
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_loop_host import _pilot_payload


def target(root, run_id="alpha", request_id="create-alpha"):
    return {"state_root": str(root), "run_id": run_id, "request_id": request_id}


def query(payload):
    return ControlRequest(1, "scoped-discovery", None, "INSTITUTIONAL_CONNECTIONS", payload)


class Access:
    def __init__(self, rows):
        self.rows = rows

    def connections(self):
        return self.rows


@pytest.fixture
def host(tmp_path):
    access = RunAccess({"mode": "client", "port": 0, "ready_timeout": 3})
    selected = WorkflowHost(tmp_path / "state", institutional_access=access)
    selected.initialize()
    with tempfile.TemporaryDirectory(prefix="scope-target-", dir="/var/tmp") as directory:
        for name in ("alpha", "beta"):
            inputs = tmp_path / name
            inputs.mkdir()
            mount = Path(directory) / name
            mount.mkdir()
            response = selected.handle(ControlRequest(1, "create-" + name, None, "CREATE_RUN",
                _pilot_payload(inputs, mount, name)))
            assert response.status == "accepted", response
        access.connections = lambda run_id=None: [
            {"run_id": name, "mode": "client", "socket_path": "/tmp/" + name + "/browser.sock"}
            for name in ("alpha", "beta") if run_id is None or name == run_id]
        try:
            yield selected
        finally:
            selected.close()


def test_old_empty_control_query_cannot_discover_client_sockets(host):
    response = host.handle(query({}))
    assert response.status == "rejected"
    assert "socket_path" not in json.dumps(response.to_mapping())


def test_host_returns_only_requested_created_run_without_command_write(host):
    before = host.store.inspect("SELECT COUNT(*) AS n FROM workflow_commands")[0]["n"]
    response = host.handle(query({"run_id": "alpha", "request_id": "create-alpha"}))
    assert response.status == "accepted", response
    assert response.result["scope"] == target(host.state_root)
    assert response.result["lifecycle"] == "active"
    assert [entry["run_id"] for entry in response.result["connections"]] == ["alpha"]
    assert host.store.inspect("SELECT COUNT(*) AS n FROM workflow_commands")[0]["n"] == before


@pytest.mark.parametrize("payload", [
    {"run_id": "alpha", "request_id": "create-beta"},
    {"run_id": "alpha", "request_id": "wrong-create"},
    {"run_id": "wrong-run", "request_id": "create-alpha"},
    {"run_id": "alpha"},
    {"run_id": "alpha", "request_id": "create-alpha", "another": "field"},
])
def test_host_rejects_wrong_or_incomplete_create_identity(host, payload):
    response = host.handle(query(payload))
    assert response.status == "rejected"
    assert "socket_path" not in json.dumps(response.to_mapping())


def test_host_stopping_returns_terminal_even_if_browser_allocation_remains(host):
    stopped = host.handle(ControlRequest(1, "stop-alpha", None, "CANCEL",
        {"run_id": "alpha", "cause": "scoped client test"}))
    assert stopped.status == "accepted"
    response = host.handle(query({"run_id": "alpha", "request_id": "create-alpha"}))
    assert response.status == "accepted"
    assert response.result["lifecycle"] == "terminal"
    assert response.result["connections"] == []


def test_durable_stop_is_visible_after_host_control_disappears(host):
    stopped = host.handle(ControlRequest(1, "stop-alpha", None, "CANCEL",
        {"run_id": "alpha", "cause": "scoped client test"}))
    assert stopped.status == "accepted"
    before = host.store.total_changes
    result = client.connections(host.state_root, "alpha", "create-alpha")
    assert result == {"scope": target(host.state_root), "lifecycle": "terminal", "connections": []}
    assert host.store.total_changes == before


def test_durable_active_is_not_proof_of_a_live_host(host):
    with pytest.raises(OSError):
        client.connections(host.state_root, "alpha", "create-alpha")
    with pytest.raises(ValueError):
        client.connections(host.state_root, "alpha", "create-beta")


def test_not_yet_created_run_never_claims_active_or_returns_a_browser(host):
    response = host.handle(query({"run_id": "future", "request_id": "future-create"}))
    assert response.status == "accepted"
    assert response.result["lifecycle"] == "pending"
    assert response.result["connections"] == []


@contextmanager
def preparation(tmp_path, *, supplied_target=True, state="waiting_for_user", endpoint_out=None):
    root = tmp_path / "preparation"
    root.mkdir(mode=0o700)
    final = tmp_path / "final"
    scope = target(final)
    def handler(request):
        assert request.command == "PREPARATION_STATUS"
        return ControlResponse.accepted(request.request_id, None,
            {"state": state, "remaining_seconds": 27})
    access = Access([{"run_id": "preparation", "mode": "client",
                     "socket_path": str(tmp_path / "browser.sock")}])
    kwargs = {"target": scope} if supplied_target else {}
    endpoint = client.PreparationControl(root, access, handler, **kwargs)
    if endpoint_out is not None:
        endpoint_out.append(endpoint)
    try:
        yield root, final, scope
    finally:
        endpoint.close()


def test_old_unscoped_preparation_query_is_rejected(tmp_path):
    with preparation(tmp_path, supplied_target=False) as (root, _, _):
        response = request_over_socket(root / "control.sock", query({}))
        assert response.status == "rejected"
        assert "socket_path" not in json.dumps(response.to_mapping())


@pytest.mark.parametrize("state,lifecycle", [
    ("waiting_for_user", "preparing"), ("awaiting_launch", "awaiting_launch"),
    ("finishing", "terminal"), ("finished", "terminal"),
])
def test_scoped_preparation_lifecycle_and_final_identity(tmp_path, state, lifecycle):
    with preparation(tmp_path, state=state) as (root, final, scope):
        result = client.connections(final, "alpha", "create-alpha", preparation_root=root)
        assert result["scope"] == scope
        assert result["lifecycle"] == lifecycle
        if lifecycle == "terminal":
            assert result["connections"] == []
        else:
            assert result["connections"][0]["state_root"] == str(root)
            assert result["connections"][0]["run_id"] == "preparation"
            assert result["remaining_seconds"] == 27


def test_preparation_cannot_be_reused_for_another_run_or_root(tmp_path):
    with preparation(tmp_path) as (root, final, _):
        with pytest.raises(ValueError):
            client.connections(final, "beta", "create-beta", preparation_root=root)
        with pytest.raises(ValueError):
            client.connections(tmp_path / "wrong-final", "alpha", "create-alpha", preparation_root=root)


def test_missing_target_is_unavailable_without_scanning_neighbor_preparations(tmp_path):
    with preparation(tmp_path):
        with pytest.raises(OSError):
            client.connections(tmp_path / "absent", "alpha", "create-alpha")


def test_legacy_runs_root_scan_is_rejected_without_querying(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "request_over_socket", lambda *a, **kw: pytest.fail("legacy request sent"))
    args = SimpleNamespace(institutional_action="connections", runs_root=str(tmp_path),
                           state_root=None, run_id=None, request_id=None)
    with pytest.raises(ValueError):
        client.run(args)


def test_host_response_must_echo_exact_scope_and_nonlegacy_lifecycle(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(root / "control.sock"))
        for document in ({"connections": []},
                         {"scope": target(root, "beta", "create-beta"), "lifecycle": "active", "connections": []}):
            monkeypatch.setattr(client, "request_over_socket", lambda *a, **kw: ControlResponse.accepted(
                "scoped-discovery", None, document))
            with pytest.raises(ValueError):
                client.connections(root, "alpha", "create-alpha")


def test_final_host_identity_failure_does_not_fall_back_to_preparation(tmp_path, monkeypatch):
    with preparation(tmp_path) as (prep, final, _):
        final.mkdir(mode=0o700)
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(final / "control.sock"))
            observed = []
            def request(path, *args, **kwargs):
                observed.append(path)
                return ControlResponse.failure("discovery", "rejected", "identity_conflict", "wrong create")
            monkeypatch.setattr(client, "request_over_socket", request)
            with pytest.raises(ValueError):
                client.connections(final, "alpha", "create-alpha", preparation_root=prep)
            assert observed == [final / "control.sock"]


def test_forward_requires_scope_owned_socket_and_preserves_live_listener(tmp_path):
    with preparation(tmp_path) as (root, final, _):
        path = tmp_path / "browser.sock"
        assert client.prepare_forward(final, "alpha", str(path), request_id="create-alpha", preparation_root=root)
        assert not client.prepare_forward(final, "alpha", str(tmp_path / "other.sock"), request_id="create-alpha", preparation_root=root)
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path)); listener.listen(1)
            before = path.stat().st_ino
            assert not client.prepare_forward(final, "alpha", str(path), request_id="create-alpha", preparation_root=root)
            assert path.stat().st_ino == before
        assert client.prepare_forward(final, "alpha", str(path), request_id="create-alpha", preparation_root=root)
        assert not path.exists()


def test_watch_releases_changed_socket_without_adopting_replacement(tmp_path, monkeypatch):
    scope = target(tmp_path / "final")
    monkeypatch.setattr(client, "connections", lambda *args, **kwargs: {
        "scope": scope, "lifecycle": "active", "connections": [
            {"run_id": "alpha", "state_root": scope["state_root"], "socket_path": "/new/browser.sock"}]})
    args = SimpleNamespace(institutional_action="watch", **scope, preparation_root=None,
        socket_path="/old/browser.sock", browser_path="/devtools/browser/test")
    assert client.run(args) == 0


@pytest.mark.parametrize("root,run_id,request_id", [
    ("relative", "alpha", "create-alpha"), ("/tmp/final", "../alpha", "create-alpha"),
    ("/tmp/final", "alpha", "create\nalpha"), ("/tmp/final", "alpha", ""),
])
def test_target_uses_existing_safe_id_rules(root, run_id, request_id):
    with pytest.raises(ValueError):
        client.validate_target(root, run_id, request_id)


@pytest.mark.parametrize("browser_identity,expected_code", [
    ("/devtools/browser/expected", 0), ("/devtools/browser/another", 2), (None, 2),
])
def test_watch_announces_only_after_actual_cdp_identity_match(tmp_path, browser_identity, expected_code):
    endpoints = []
    with preparation(tmp_path, endpoint_out=endpoints) as (root, final, scope):
        endpoint = endpoints[0]
        raw = (json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1:9222" + browser_identity}).encode()
               if browser_identity else b"invalid-json")
        failures = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(tmp_path / "browser.sock")); listener.listen(1); listener.settimeout(3)
            def serve():
                try:
                    peer, _ = listener.accept()
                    with peer:
                        peer.settimeout(2)
                        assert peer.recv(65536).startswith(b"GET /json/version HTTP/1.1")
                        peer.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                                     + str(len(raw)).encode() + b"\r\n\r\n" + raw)
                except Exception as exc:
                    failures.append(exc)
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            announced = []
            def output(value):
                announced.append(json.loads(value))
                # Actual operation ends immediately after the readiness observation.
                endpoint.access.rows.clear()
            args = SimpleNamespace(institutional_action="watch", **scope, preparation_root=str(root),
                socket_path=str(tmp_path / "browser.sock"), browser_path="/devtools/browser/expected")
            assert client.run(args, output=output) == expected_code
            thread.join(timeout=3)
            assert not thread.is_alive() and not failures
            assert announced == ([{"scope": scope, "lifecycle": "preparing", "connected": True}]
                                 if expected_code == 0 else [])
