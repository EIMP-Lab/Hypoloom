"""One explicit init/run identity owns client discovery; legacy scans cannot attach."""
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
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


@contextmanager
def cdp_response(tmp_path, raw, *, status=200, protocol=None):
    path = tmp_path / "browser.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path)); listener.listen(1); listener.settimeout(5)
        failures = []
        def serve():
            try:
                peer, _ = listener.accept()
                with peer:
                    peer.settimeout(3)
                    assert peer.recv(65536).startswith(b"GET /json/version HTTP/1.1")
                    peer.sendall(protocol if protocol is not None else
                        f"HTTP/1.1 {status} Fixture\r\nConnection: close\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A rejected response may close before the fixture body finishes.
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=serve, daemon=True); thread.start()
        try:
            yield
        finally:
            thread.join(timeout=6)
            path.unlink(missing_ok=True)
            assert not thread.is_alive() and not failures


def watch_args(scope, root):
    return SimpleNamespace(institutional_action="watch", **scope, preparation_root=str(root),
        socket_path=str(root.parent / "browser.sock"), browser_path="/devtools/browser/expected")


@pytest.mark.parametrize("error,reason", [
    (OSError("sensitive-control-location"), "connection_target_unavailable"),
    (ValueError("sensitive-control-scope"), "connection_scope_rejected"),
])
def test_watch_control_failure_uses_static_stderr_only(tmp_path, monkeypatch, capsys, error, reason):
    def unavailable(*a, **kw):
        raise error
    monkeypatch.setattr(client, "connections", unavailable)
    output = []
    assert client.run(watch_args(target(tmp_path / "final"), tmp_path / "prep"), output=output.append) == 2
    captured = capsys.readouterr()
    assert not output and not captured.out
    assert captured.err == json.dumps({"event": "connection_watch_failed", "reason": reason}) + "\n"
    assert "sensitive" not in captured.err


def test_watch_missing_cdp_transport_is_distinct_from_control_failure(tmp_path, capsys):
    with preparation(tmp_path) as (root, _, scope):
        assert client.run(watch_args(scope, root)) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {"event": "connection_watch_failed", "reason": "cdp_connection_failed"}


@pytest.mark.parametrize("raw,status,protocol,reason", [
    (b"sensitive-response-body", 403, None, "cdp_endpoint_unavailable"),
    (b"", 200, b"sensitive-invalid-status-line\r\n\r\n", "cdp_endpoint_unavailable"),
    (b"sensitive-non-json", 200, None, "cdp_response_invalid"),
    (b"[]", 200, None, "cdp_response_invalid"),
    (b'{"webSocketDebuggerUrl":3}', 200, None, "cdp_response_invalid"),
    (b'{"sensitive-missing-field":true}', 200, None, "cdp_response_invalid"),
    (b"x" * ((1 << 20) + 1), 200, None, "cdp_response_invalid"),
    (b'{"webSocketDebuggerUrl":"ws://sensitive-host/devtools/browser/other"}', 200, None, "cdp_identity_mismatch"),
], ids=["http-status", "http-protocol", "invalid-json", "array", "field-type", "missing-field", "byte-limit", "wrong-browser"])
def test_watch_cdp_failure_never_copies_response_into_diagnostic(tmp_path, capsys, raw, status, protocol, reason):
    with preparation(tmp_path) as (root, _, scope), cdp_response(tmp_path, raw, status=status, protocol=protocol):
        assert client.run(watch_args(scope, root)) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == json.dumps({"event": "connection_watch_failed", "reason": reason}) + "\n"
    assert "sensitive" not in captured.err


def test_real_cli_watch_keeps_safe_failure_on_stderr(tmp_path):
    raw = b'{"webSocketDebuggerUrl":"ws://private-canary/devtools/browser/wrong"}'
    with preparation(tmp_path) as (root, _, scope), cdp_response(tmp_path, raw):
        arguments = watch_args(scope, root)
        argv = [sys.executable, "-m", "hypoloom", "institutional", "watch"]
        for name in ("state_root", "run_id", "request_id", "preparation_root", "socket_path", "browser_path"):
            argv.extend(["--" + name.replace("_", "-"), getattr(arguments, name)])
        result = subprocess.run(argv, capture_output=True, text=True, timeout=7)
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr == json.dumps({"event": "connection_watch_failed", "reason": "cdp_identity_mismatch"}) + "\n"
    assert "private-canary" not in result.stderr


@contextmanager
def formal_control_response(final, scope, monkeypatch):
    final.mkdir(mode=0o700)
    result = {"scope": scope, "lifecycle": "pending", "connections": []}
    ordinary_request = client.request_over_socket
    def query_formal(path, request, **kwargs):
        if Path(path) == final / "control.sock":
            return ControlResponse.accepted(request.request_id, None, result)
        return ordinary_request(path, request, **kwargs)
    monkeypatch.setattr(client, "request_over_socket", query_formal)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(final / "control.sock"))
        try:
            yield result
        finally:
            (final / "control.sock").unlink(missing_ok=True)


@pytest.mark.parametrize("state,lifecycle", [("waiting_for_user", "preparing"), ("awaiting_launch", "awaiting_launch")])
def test_pending_host_keeps_exact_preparation_until_formal_identity_is_active(tmp_path, monkeypatch, state, lifecycle):
    with preparation(tmp_path, state=state) as (root, final, scope):
        with formal_control_response(final, scope, monkeypatch) as response:
            result = client.connections(final, "alpha", "create-alpha", preparation_root=root)
            assert result["scope"] == scope and result["lifecycle"] == lifecycle
            assert result["connections"][0]["state_root"] == str(root)
            assert result["remaining_seconds"] == 27
            response.update(lifecycle="active", connections=[{
                "run_id": "alpha", "socket_path": str(tmp_path / "formal-browser.sock")}])
            result = client.connections(final, "alpha", "create-alpha", preparation_root=root)
            assert result["lifecycle"] == "active"
            assert result["connections"][0]["state_root"] == str(final)
            response.update(lifecycle="terminal", connections=[])
            assert client.connections(final, "alpha", "create-alpha", preparation_root=root) == response


@pytest.mark.parametrize("preparation_kind", ["not_supplied", "missing", "socket_missing"])
def test_pending_host_with_no_reachable_preparation_stays_pending(tmp_path, monkeypatch, preparation_kind):
    final = tmp_path / "final"; scope = target(final)
    root = tmp_path / "absent-preparation"
    if preparation_kind == "socket_missing": root.mkdir(mode=0o700)
    with formal_control_response(final, scope, monkeypatch) as response:
        result = client.connections(final, "alpha", "create-alpha",
            preparation_root=None if preparation_kind == "not_supplied" else root)
        assert result == response and result["connections"] == []


@pytest.mark.parametrize("failure", ["wrong_scope", "public_root"])
def test_pending_host_never_suppresses_preparation_identity_or_privacy_rejection(tmp_path, monkeypatch, failure):
    with preparation(tmp_path) as (root, final, scope):
        if failure == "wrong_scope": scope = target(final, "wrong-run", "wrong-create")
        else: root.chmod(0o755)
        try:
            with formal_control_response(final, scope, monkeypatch):
                with pytest.raises(ValueError):
                    client.connections(final, scope["run_id"], scope["request_id"], preparation_root=root)
        finally:
            root.chmod(0o700)



def test_watch_keeps_prior_stdout_handshake_separate_from_later_control_error(tmp_path, capsys):
    endpoints = []
    raw = b'{"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/browser/expected"}'
    with preparation(tmp_path, endpoint_out=endpoints) as (root, _, scope), cdp_response(tmp_path, raw):
        output = []
        def accepted(value):
            output.append(json.loads(value))
            endpoints[0].close()
        assert client.run(watch_args(scope, root), output=accepted) == 2
    assert output == [{"scope": scope, "lifecycle": "preparing", "connected": True}]
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {"event": "connection_watch_failed", "reason": "connection_target_unavailable"}


def test_watch_invalid_scope_has_safe_diagnostic_without_query(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(client, "connections", lambda *a, **kw: pytest.fail("invalid scope was queried"))
    args = watch_args(target(tmp_path / "final", "sensitive/invalid-run"), tmp_path / "prep")
    assert client.run(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {"event": "connection_watch_failed", "reason": "connection_scope_rejected"}
    assert "sensitive" not in captured.err



@pytest.mark.parametrize("failed_operation", ["output", "flush"])
def test_watch_handshake_output_failure_remains_safe_exit_two(tmp_path, capsys, monkeypatch, failed_operation):
    raw = b'{"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/browser/expected"}'
    def broken(*args, **kwargs):
        raise BrokenPipeError("sensitive-output-consumer-detail")
    with preparation(tmp_path) as (root, _, scope), cdp_response(tmp_path, raw):
        output = []
        with monkeypatch.context() as patch:
            if failed_operation == "flush": patch.setattr(sys.stdout, "flush", broken)
            assert client.run(watch_args(scope, root), output=broken if failed_operation == "output" else output.append) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == json.dumps({"event": "connection_watch_failed", "reason": "connection_watch_output_unavailable"}) + "\n"
    assert "sensitive" not in captured.err
