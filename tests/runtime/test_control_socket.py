from __future__ import annotations

import copy
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import hypoloom.workflow_host as workflow_host
from hypoloom.control_protocol import (
    ControlRequest,
    ControlResponse,
    ProtocolError,
    receive_message,
    request_over_socket,
    send_message,
)
from hypoloom.profile_admission import admit_profile


_REPOSITORY = Path(__file__).resolve().parents[2]


def request(**overrides: object) -> ControlRequest:
    raw: dict[str, object] = {
        "schema_version": 1,
        "request_id": "command-1",
        "expected_revision": 0,
        "command": "create_run",
        "payload": {"run_id": "run-1"},
    }
    raw.update(overrides)
    return ControlRequest.from_mapping(raw)


def test_request_is_strict_normalized_and_versioned() -> None:
    parsed = request()
    assert parsed.command == "CREATE_RUN"
    assert parsed.normalized() == (
        '{"command":"CREATE_RUN","expected_revision":0,'
        '"payload":{"run_id":"run-1"},"request_id":"command-1",'
        '"schema_version":1}'
    )

    with pytest.raises(ProtocolError, match="unsupported schema_version"):
        request(schema_version=2)
    with pytest.raises(ProtocolError, match="invalid fields"):
        request(extra="not allowed")
    with pytest.raises(ProtocolError, match="payload must be an object"):
        request(payload=[])


def test_length_prefixed_frame_survives_fragmented_socket_delivery() -> None:
    left, right = socket.socketpair()
    response = ControlResponse.accepted("command-1", 1, {"run_id": "run-1"})

    def server() -> None:
        try:
            incoming = ControlRequest.from_mapping(receive_message(right))
            assert incoming == request()
            send_message(right, response.to_mapping())
        finally:
            right.close()

    thread = threading.Thread(target=server)
    thread.start()
    try:
        send_message(left, request().to_mapping())
        observed = ControlResponse.from_mapping(receive_message(left))
    finally:
        left.close()
        thread.join(timeout=2)
    assert observed == response
    assert not thread.is_alive()


@pytest.mark.parametrize("error_type", (BrokenPipeError, ConnectionResetError))
def test_handled_response_ignores_only_peer_disconnects(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[ConnectionError],
) -> None:
    def disconnected(_peer: socket.socket, _value: object) -> None:
        raise error_type("peer disconnected")

    monkeypatch.setattr(workflow_host, "send_message", disconnected)
    with socket.socket() as peer:
        workflow_host._send_control_response(
            peer,
            ControlResponse.accepted("command-1", 1, {"run_id": "run-1"}),
        )


def test_handled_response_does_not_hide_other_write_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_write(_peer: socket.socket, _value: object) -> None:
        raise OSError("unrelated response write failure")

    monkeypatch.setattr(workflow_host, "send_message", failed_write)
    with socket.socket() as peer:
        with pytest.raises(OSError, match="unrelated response write failure"):
            workflow_host._send_control_response(
                peer,
                ControlResponse.accepted("command-1", 1, {"run_id": "run-1"}),
            )


def test_cli_transport_does_not_fallback_when_host_is_absent(tmp_path: Path) -> None:
    socket_path = tmp_path / "missing.sock"
    with pytest.raises(ConnectionError, match="Workflow Host unavailable"):
        request_over_socket(socket_path, request(), timeout=0.1)
    assert list(tmp_path.iterdir()) == []


def test_resident_host_survives_response_peer_disconnect_and_replays_effect(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "response-peer-disconnect"
    run_id = "response-peer-disconnect"
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        delay_seconds=0.0,
    )
    assert admission.admitted and admission.create_run_payload is not None
    request_id = f"create-{run_id}"
    delayed_host = """
import sys
import time
from pathlib import Path
import hypoloom.workflow_host as workflow_host

original_handle = workflow_host.WorkflowHost.handle

def delayed_handle(self, request):
    if request.request_id == sys.argv[2]:
        time.sleep(0.25)
    return original_handle(self, request)

workflow_host.WorkflowHost.handle = delayed_handle
raise SystemExit(workflow_host.serve(Path(sys.argv[1])))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", delayed_host, str(state_root), request_id],
        cwd=_REPOSITORY,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
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

        create = ControlRequest(
            schema_version=1,
            request_id=request_id,
            expected_revision=0,
            command="CREATE_RUN",
            payload=copy.deepcopy(admission.create_run_payload),
        )
        with pytest.raises(TimeoutError):
            request_over_socket(socket_path, create, timeout=0.03)

        replay = request_over_socket(socket_path, create, timeout=5)
        assert process.poll() is None
        assert replay == ControlResponse.accepted(
            request_id,
            1,
            {"run_id": run_id, "run_state": "CREATED"},
        )
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
