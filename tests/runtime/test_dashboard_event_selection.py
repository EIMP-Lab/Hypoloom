"""Dashboard-only selectors over frozen GoalDefined; real loopback readers."""

from __future__ import annotations

import copy
import json
import socket
import sqlite3
from pathlib import Path

import pytest
import yaml

from hypoloom.control_protocol import ControlRequest
from hypoloom.notification_tailer import load_notification_config
from hypoloom.observation_api import build_product_observation
from hypoloom.web_observer import WebObserverServer
from hypoloom.workflow_host import WorkflowHost

from test_notification_tailer import _create_profile_run
from test_web_observer import _TOKEN, _request, _start, _stop


def _source(tmp_path: Path, selection):
    events = {"telegram": ["run_started"], "webhook": ["participant_resumed"]}
    if selection != "omitted":
        events["dashboard"] = selection
    config = {"sinks": {"dashboard": {"enabled": True, "host": "127.0.0.1", "port": 7860}},
              "events": events}
    root = _create_profile_run(tmp_path, config)
    # These controls occur only during fresh fixture construction. No Agent or
    # experiment is dispatched; the observer below never acquires a writer.
    host = WorkflowHost(root)
    try:
        host.initialize()
        for command in ("PAUSE", "RESUME"):
            result = host.handle(ControlRequest(schema_version=1,
                request_id="fixture-" + command.lower(), expected_revision=None,
                command=command, payload={"run_id": "notifications", "reason": "operator",
                    "scope": {"role": None, "component_id": None, "chain": None}}))
            assert result.status == "accepted", result
    finally:
        host.close()
    return root, config


def _source_contents(root):
    # Read-only WAL readers may coordinate via exactly this shm file. Database,
    # WAL, every artifact and their mtimes remain part of the strict comparison.
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file() and path != root / "state.sqlite-shm"}


def _rows(root):
    with sqlite3.connect(f"file:{root / 'state.sqlite'}?mode=ro", uri=True) as db:
        return tuple(db.iterdump())


@pytest.mark.parametrize("selection", ["omitted", [], ["participant_pause_requested"],
    ["run_started", "participant_resumed", "unknown_future_event"]],
    ids=["omitted-all", "empty-none", "selected-one", "selected-list"])
def test_http_events_and_activity_use_only_admitted_dashboard_selector(tmp_path, selection):
    root, config = _source(tmp_path, selection)
    canonical = json.loads(json.dumps(build_product_observation(root, "notifications")))
    assert {event["type"] for event in canonical["events"]} == {
        "run_started", "participant_pause_requested", "participant_resumed"}
    expected = canonical["events"] if selection == "omitted" else [
        event for event in canonical["events"] if event["type"] in selection]
    # Ordinary operator edits to the original profile cannot rebind this run.
    profile_file = tmp_path / "profile-v3" / "notify.yaml"
    profile_file.write_text(yaml.safe_dump({"events": {"dashboard": [] if expected else ["run_started"]}}))
    before_rows, before_files = _rows(root), _source_contents(root)
    server = WebObserverServer(root, "notifications", port=0, token=_TOKEN)
    server.log_requests = False
    thread = _start(server)
    try:
        for name in ("events", "activity"):
            assert _request(server, "GET", "/api/" + name)[0] == 401
            status, headers, body = _request(server, "GET", "/api/" + name, token=_TOKEN)
            assert status == 200 and headers["cache-control"] == "no-store"
            view = json.loads(body)
            assert view["events"] == expected
            if name == "activity":
                for field in ("graph", "experts", "experiments", "participant_controls", "final_report"):
                    assert view[field] == canonical[field]
        for name in ("graph", "experts", "experiments", "final_report"):
            status, _, body = _request(server, "GET", "/api/" + name, token=_TOKEN)
            assert status == 200 and json.loads(body)[name] == canonical[name]
        status, _, body = _request(server, "GET", "/api/snapshot", token=_TOKEN)
        assert status == 200
        assert json.loads(body)["goal"]["research_contract"]["methodology"]["controls"]["product"]["notify"] == config
        assert load_notification_config(root, "notifications") == config
        assert build_product_observation(root, "notifications")["events"] == canonical["events"]
    finally:
        _stop(server, thread)
    assert _rows(root) == before_rows
    assert _source_contents(root) == before_files


def test_dashboard_selection_does_not_mutate_nonempty_graph_or_other_product_data(tmp_path, monkeypatch):
    root, _ = _source(tmp_path, [])
    projection = build_product_observation(root, "notifications")
    # Sentinel display data exercises nonempty sibling fields without starting
    # science merely to test a view transformation. Goal configuration is real.
    projection.update(graph={"nodes": [{"hypothesis_id": "visible-hypothesis"}], "edges": []},
        experiments=[{"experiment_id": "visible-experiment", "state": "TERMINAL"}])
    original = copy.deepcopy(projection)
    monkeypatch.setattr("hypoloom.observer_refresh.build_product_observation", lambda *_, **__: projection)
    server = WebObserverServer(root, "notifications", port=0, token=_TOKEN)
    server.log_requests = False
    thread = _start(server)
    try:
        status, _, body = _request(server, "GET", "/api/activity", token=_TOKEN)
        assert status == 200
        view = json.loads(body)
        assert view["events"] == []
        serialized = json.loads(json.dumps(original))
        assert {key: value for key, value in view.items() if key != "events"} == {
            key: value for key, value in serialized.items() if key != "events"}
        assert projection == original
    finally:
        _stop(server, thread)


def _seed_log_reader_rows(root):
    # Standard SQLite reader fixture, not a claim that a Process was executed.
    # The file API must resolve this exact run/experiment/Attempt, not a URL path.
    host = WorkflowHost(root)
    try:
        host.initialize()
        with host.store.transaction() as tx:
            tx.record_command({"command_id": "reader-fixture-create", "run_id": "notifications",
                "workflow_id": "reader-experiment", "command_type": "CREATE_EXPERIMENT",
                "request": {}, "status": "accepted", "result": {}, "revision": 1})
            tx.append_transition({"workflow_id": "reader-experiment", "revision": 1,
                "run_id": "notifications", "workflow_kind": "EXPERIMENT", "previous_state": None,
                "state": "DRAFTED", "command_id": "reader-fixture-create", "payload": {}})
            tx.open_attempt({"attempt_id": "reader-process", "run_id": "notifications",
                "workflow_id": "reader-experiment", "command_id": "reader-fixture-create",
                "kind": "PROCESS", "experiment_id": "reader-experiment", "execution_id": "reader-execution",
                "attempt_ordinal": 1, "host_generation": host.generation, "started_at": 1})
            tx.close_attempt("reader-process", terminal_reason="SUCCEEDED", ended_at=2, payload={})
    finally:
        host.close()
    path = root / "artifacts/result-staging/reader-process/logs/stdout.log"
    path.parent.mkdir(parents=True)
    path.write_text("full experiment log remains visible\n")
    return path.read_text()


def test_empty_dashboard_events_does_not_filter_real_http_or_websocket_logs(tmp_path):
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import AcceptConnection, CloseConnection, Request, TextMessage

    root, _ = _source(tmp_path, [])
    text = _seed_log_reader_rows(root)
    before_rows, before_files = _rows(root), _source_contents(root)
    server = WebObserverServer(root, "notifications", port=0, token=_TOKEN)
    server.log_requests = False
    thread = _start(server)
    try:
        path = "/api/experiments/reader-experiment/logs"
        status, _, body = _request(server, "GET", path, token=_TOKEN)
        assert status == 200 and json.loads(body)["text"] == text
        assert _request(server, "GET", path + "/ws")[0] == 401
        ws = WSConnection(ConnectionType.CLIENT)
        with socket.create_connection(server.server_address, timeout=5) as connection:
            connection.sendall(ws.send(Request(host=f"127.0.0.1:{server.server_address[1]}",
                target=path + "/ws", subprotocols=["hypoloom.logs", "bearer." + _TOKEN])))
            pending = []

            def receive(kind):
                while True:
                    for index, event in enumerate(pending):
                        if isinstance(event, kind):
                            return pending.pop(index)
                    data = connection.recv(65536)
                    assert data
                    ws.receive_data(data)
                    pending.extend(ws.events())

            assert receive(AcceptConnection).subprotocol == "hypoloom.logs"
            result = json.loads(receive(TextMessage).data)
            assert result["text"] == text and result["available"]
            assert result["attempt_id"] == "reader-process"
            connection.sendall(ws.send(CloseConnection(code=1000)))
            assert receive(CloseConnection).code == 1000
    finally:
        _stop(server, thread)
    assert _rows(root) == before_rows
    assert _source_contents(root) == before_files
