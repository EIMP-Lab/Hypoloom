"""Portable regressions for read-only status transport and history reuse."""
import socket
import time
from types import MappingProxyType

import pytest

from hypoloom.control_protocol import ControlRequest, ControlResponse, request_over_socket, send_message, receive_message
from hypoloom.control_server import ControlServer, StatusSnapshots
from hypoloom.expert_batch import AcceptedBatch, BatchHistory
from hypoloom.state_store import HostWriterCapability, StateStore


def request(command="STATUS", request_id="status", **payload):
    return ControlRequest(1, request_id, None, command, payload or {"run_id": "run"})


def publish(snapshots, *, age=0):
    result = {"run_id": "run", "run_revision": 7, "run_state": "ACTIVE", "nested": [1]}
    snapshots.publish("run", ControlResponse.accepted("internal", 7, result),
        started_at=(time.time() - age, time.monotonic() - age))
    return result


@pytest.fixture
def transport(tmp_path):
    # The protocol also supports long production paths; keep the test bind short.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="hyp-status-", dir="/tmp") as root:
        from pathlib import Path
        path = Path(root) / "control.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen(16)
            snapshots = StatusSnapshots()
            publish(snapshots)
            server = ControlServer(listener, snapshots)
            server.start()
            try:
                yield path, server, snapshots
            finally:
                server.close()
            assert not server._thread.is_alive()


def test_status_responds_while_writer_and_partial_client_are_blocked(transport):
    path, server, _ = transport
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as mutation, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as partial:
        mutation.connect(str(path))
        send_message(mutation, request("CANCEL", "cancel").to_mapping())
        pending = server.next_request(timeout=1)
        assert pending is not None
        command, future = pending
        partial.connect(str(path))
        partial.sendall(b"\x00")
        # The writer deliberately leaves the command unresolved. Ten real
        # requests must complete without ticking, receiving that partial frame,
        # or letting a status request enter the command queue.
        for index in range(10):
            observed = request_over_socket(path, request(request_id=f"s-{index}"), timeout=0.5)
            assert observed.request_id == f"s-{index}" and observed.revision == 7
            assert observed.result["status_snapshot"]["stale"] is False
        assert server.next_request(timeout=0) is None and not future.done()
        future.set_result(ControlResponse.accepted(command.request_id, 8, {"run_state": "STOPPING"}))
        mutation.settimeout(1)
        assert receive_message(mutation)["revision"] == 8


def test_snapshot_age_identity_and_nested_isolation(transport):
    path, _, snapshots = transport
    original = publish(snapshots, age=10)
    original["nested"].append(99)
    observed = request_over_socket(path, request(), timeout=0.5)
    assert observed.result["nested"] == [1]
    metadata = observed.result["status_snapshot"]
    assert metadata["age_seconds"] >= 10 and metadata["stale"] is True
    assert metadata["observed_at"] <= time.time() - 10
    observed.result["nested"].append(42)
    assert request_over_socket(path, request(), timeout=0.5).result["nested"] == [1]
    assert request_over_socket(path, request(run_id="missing"), timeout=0.5).error["code"] == "not_found"
    assert request_over_socket(path, request(extra=True), timeout=0.5).error["code"] == "invalid_request"


def test_full_control_queue_leaves_status_available_and_rejects_extra_command(transport):
    path, server, _ = transport
    peers = []
    try:
        for index in range(server.MAX_COMMANDS):
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peers.append(peer)
            peer.connect(str(path))
            send_message(peer, request("CANCEL", f"cancel-{index}").to_mapping())
            deadline = time.monotonic() + 2
            while server.requests.qsize() != index + 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            assert server.requests.qsize() == index + 1
        extra = request_over_socket(path, request("CANCEL", "extra"), timeout=0.5)
        assert extra.status == "rejected" and extra.error["code"] == "host_busy"
        assert request_over_socket(path, request(), timeout=0.5).revision == 7
    finally:
        for peer in peers:
            peer.close()


def test_bad_frame_and_unread_large_response_do_not_block_status(transport):
    path, _, snapshots = transport
    snapshots.publish("large", ControlResponse.accepted("large", 1, {"padding": "x" * 900000}),
        started_at=(time.time(), time.monotonic()))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as slow:
        slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        slow.connect(str(path))
        send_message(slow, request(run_id="large").to_mapping())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bad:
            bad.connect(str(path))
            bad.sendall(b"\xff\xff\xff\xff")
            bad.settimeout(0.5)
            assert receive_message(bad)["error"]["code"] == "protocol_error"
        assert request_over_socket(path, request(), timeout=0.5).revision == 7


def test_batch_derived_cache_reuses_reducer_and_isolates_nested_state(tmp_path):
    with StateStore(tmp_path / "state", HostWriterCapability.issue()) as store:
        with store.transaction() as tx:
            tx.execute("CREATE TABLE sample(value INTEGER)")
            tx.execute("INSERT INTO sample VALUES(1)")
        reads = []
        def read():
            value = store.inspect("SELECT value FROM sample")[0]["value"]
            reads.append(value)
            return BatchHistory("run", "component", "role",
                (AcceptedBatch(1, "command", "attempt", MappingProxyType({"chain": "hypothesis"}),
                    {"selected_tip": {"node_id": "hypothesis"}}),),
                MappingProxyType({"hypothesis": {"payload": {"values": [value]}}}))
        with store.cache_reads():
            first = store.cached_read("batch", read)
            first.batches[0].selection["selected_tip"]["node_id"] = "tampered"
            first.hypotheses["hypothesis"]["payload"]["values"].append(999)
            second = store.cached_read("batch", read)
            assert second.batches[0].selection["selected_tip"]["node_id"] == "hypothesis"
            assert second.hypotheses["hypothesis"]["payload"]["values"] == [1]
            assert reads == [1]
            with pytest.raises(RuntimeError):
                with store.transaction() as tx:
                    tx.execute("UPDATE sample SET value=2")
                    raise RuntimeError("rollback")
            assert store.cached_read("batch", read).hypotheses["hypothesis"]["payload"]["values"] == [1]
            with store.transaction() as tx:
                tx.execute("UPDATE sample SET value=3")
            assert store.cached_read("batch", read).hypotheses["hypothesis"]["payload"]["values"] == [3]
            assert reads == [1, 1, 3]


def test_display_phase_reads_task_identity_without_embedded_input_validation(monkeypatch):
    from hypoloom.agent_aci import AgentRequest, EXPERT_LOOP_ACI_SCHEMA_VERSION
    from hypoloom.expert_loop import ResearchNodeKey, ResearchNodeKind, ResearchNodeTask
    from hypoloom.monitor import _attempt_phase
    task = ResearchNodeTask(key=ResearchNodeKey(run_id="run", component_id="must.component",
        expert_slot="expert", cycle=1, node_kind=ResearchNodeKind.LITERATURE_REVIEW))
    def forbidden(*args, **kwargs):
        raise AssertionError("display decoded an entire Agent request")
    monkeypatch.setattr(AgentRequest, "from_dict", forbidden)
    raw = {"schema_version": EXPERT_LOOP_ACI_SCHEMA_VERSION, "task_context": task.to_dict(),
        "input_artifacts": [{"content_base64": "large opaque display-unused input"}]}
    assert _attempt_phase("AGENT", "LITERATURE_REVIEW", {}, {}, request=raw) == "literature_review/main"
    raw["schema_version"] = []
    assert _attempt_phase("AGENT", "LITERATURE_REVIEW", {}, {}, request=raw) == "literature_review"
    raw["schema_version"] = EXPERT_LOOP_ACI_SCHEMA_VERSION
    raw["task_context"] = {"kind": "RESEARCH_NODE", "key": []}
    assert _attempt_phase("AGENT", "LITERATURE_REVIEW", {}, {}, request=raw) == "literature_review"


def test_shutdown_delivers_completed_command_response(transport):
    path, server, _ = transport
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.connect(str(path))
        peer.settimeout(2)
        send_message(peer, request("CANCEL", "cancel").to_mapping())
        command, future = server.next_request(timeout=1)
        future.set_result(ControlResponse.accepted(command.request_id, 8, {"run_state": "STOPPING"}))
        server.close()
        response = receive_message(peer)
        assert response["request_id"] == "cancel" and response["revision"] == 8


def test_unrelated_transport_error_is_reported_to_owner(transport, monkeypatch):
    from hypoloom.control_protocol import ProtocolError
    path, server, _ = transport
    def failed(*args):
        raise OSError("unrelated transport failure")
    monkeypatch.setattr("hypoloom.control_server._encode_message", failed)
    with pytest.raises((ProtocolError, ConnectionError)):
        request_over_socket(path, request(), timeout=1)
    with pytest.raises(RuntimeError, match="transport failed"):
        server.next_request(timeout=0)


def test_idle_terminal_host_limits_snapshot_publication(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import hypoloom.workflow_host as module
    now = [100.0]
    published = []
    class Host:
        stop_requested = False
        operational_halt = None
        institutional_access = None
        terminal_exit_ready = True
        def __init__(self, *args, **kwargs):
            self.store = SimpleNamespace(cache_reads=nullcontext, inspect=lambda sql: [{"run_id": "run"}])
        def initialize(self): pass
        def close(self): pass
        def handle(self, req):
            published.append(now[0])
            return ControlResponse.accepted(req.request_id, 2, {"run_state": "COMPLETED"})
        def tick(self):
            now[0] += 0.1
            self.stop_requested = now[0] >= 102.2
    monkeypatch.setattr(module, "WorkflowHost", Host)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    assert module.serve(tmp_path) == 0
    assert 2 <= len(published) <= 3
    assert not (tmp_path / "control.sock").exists()
