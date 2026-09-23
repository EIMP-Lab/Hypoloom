from __future__ import annotations

import copy
import http.client
import inspect
import io
import json
import socket
import sqlite3
import struct
import threading
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

import pytest

from hypoloom.agent_aci import required_output_schema
from hypoloom.control_protocol import ControlRequest
from hypoloom.cli import main as cli_main
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.fake_agent import default_request
from hypoloom.monitor import (
    MonitorSnapshot,
    _attempt_phase,
    build_monitor_snapshot,
)
from hypoloom.profile_admission import admit_profile
from hypoloom.web_observer import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    WebObserverServer,
    create_server,
    run_web_observer,
)
from hypoloom.workflow_host import WorkflowHost


_REPOSITORY = Path(__file__).resolve().parents[2]
_TOKEN = "test-only-ephemeral-observer-token"
_REQUIRES_PRODUCT_SQLITE = pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 51, 3),
    reason="Hypoloom durable-state integration requires SQLite >= 3.51.3",
)
_SECURITY_HEADERS = {
    "cache-control": "no-store",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
}


class _IdAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self,
        _tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        rendered = dict(attrs)
        if rendered.get("id"):
            self.ids.append(str(rendered["id"]))


class _SyntheticSnapshot:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_json(self) -> str:
        return json.dumps(self._payload, sort_keys=True)


def _create_fresh_run(state_root: Path, run_id: str) -> None:
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        delay_seconds=0.0,
    )
    assert admission.admitted and admission.create_run_payload is not None
    payload = copy.deepcopy(admission.create_run_payload)
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        response = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=payload,
            )
        )
        assert response.status == "accepted"
    finally:
        host.close()


def _row_census(database: Path) -> dict[str, int]:
    with sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro",
        uri=True,
    ) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in tables
        }


def _artifact_inventory(state_root: Path) -> tuple[str, ...]:
    artifacts = state_root / "artifacts"
    if not artifacts.exists():
        return ()
    return tuple(
        sorted(str(path.relative_to(artifacts)) for path in artifacts.rglob("*"))
    )


def _start(server: WebObserverServer) -> threading.Thread:
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    return thread


def _stop(server: WebObserverServer, thread: threading.Thread) -> None:
    server.begin_shutdown()
    if thread.is_alive():
        server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def _request(
    server: WebObserverServer,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    host, port = server.server_address[:2]
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection = http.client.HTTPConnection(str(host), int(port), timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        rendered_headers = {
            name.lower(): value for name, value in response.getheaders()
        }
        return response.status, rendered_headers, response.read()
    finally:
        connection.close()


def _assert_security_headers(headers: dict[str, str]) -> None:
    for name, value in _SECURITY_HEADERS.items():
        assert headers[name] == value
    policy = headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_server_defaults_to_loopback_accepts_port_zero_and_generates_token(
    tmp_path: Path,
) -> None:
    signature = inspect.signature(create_server)
    assert signature.parameters["host"].default == DEFAULT_HOST == "127.0.0.1"
    assert signature.parameters["port"].default == DEFAULT_PORT == 0

    server = create_server(tmp_path / "unused", "explicit-run")
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.server_address[1] != 0
        assert server.state_root == (tmp_path / "unused").resolve()
        assert server.run_id == "explicit-run"
        assert server.access_token
        assert server.startup_url().endswith(f"/#token={server.access_token}")
    finally:
        server.server_close()

    with pytest.raises(ValueError, match="SSH tunnel"):
        create_server(
            tmp_path / "unused",
            "explicit-run",
            host="0.0.0.0",
            port=0,
        )


def test_web_snapshot_preserves_monitor_expert_subphases(tmp_path: Path) -> None:
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    assert isinstance(request.task_context, ResearchNodeTask)
    plan = replace(
        request,
        task_context=(task := ResearchNodeTask(key=replace(
            request.task_context.key,
            subkind=ResearchNodeSubkind.QUERY_PLAN,
        ))),
        command_id=task.command_id,
        required_output_schema=required_output_schema(request.command, task),
    )
    phases = [
        _attempt_phase(
            "AGENT", item.command.value, {}, {}, request=item.to_dict(),
        )
        for item in (plan, request)
    ]
    snapshot = cast(MonitorSnapshot, _SyntheticSnapshot({
        "attempts": [{"phase": phase} for phase in phases],
    }))
    server = create_server(
        tmp_path / "unused",
        "expert-subphases",
        port=0,
        token=_TOKEN,
        snapshot_builder=lambda _root, _run_id: snapshot,
    )
    thread = _start(server)
    try:
        status, _, body = _request(server, "GET", "/api/snapshot", token=_TOKEN)
        assert status == 200
        assert [item["phase"] for item in json.loads(body)["attempts"]] == [
            "literature_review/query_plan",
            "literature_review/main",
        ]
    finally:
        _stop(server, thread)


def test_begin_shutdown_closes_idle_accepted_connection_and_releases_port(
    tmp_path: Path,
) -> None:
    calls = 0

    def snapshot_builder(
        _state_root: str | Path,
        _run_id: str,
    ) -> MonitorSnapshot:
        nonlocal calls
        calls += 1
        return cast(MonitorSnapshot, _SyntheticSnapshot({"call": calls}))

    server = create_server(
        tmp_path / "unused",
        "idle-accepted",
        port=0,
        token=_TOKEN,
        snapshot_builder=snapshot_builder,
    )
    server.log_requests = False
    accepted = threading.Event()
    original_get_request = server.get_request

    def tracked_get_request() -> tuple[socket.socket, tuple[str, int]]:
        request = original_get_request()
        accepted.set()
        return request

    server.get_request = tracked_get_request  # type: ignore[method-assign]
    thread = _start(server)
    host, port = server.server_address[:2]
    client = socket.create_connection((str(host), int(port)), timeout=5)
    try:
        assert accepted.wait(timeout=5)
        server.begin_shutdown()
        assert server.is_closing
        server.begin_shutdown()
        _stop(server, thread)

        client.settimeout(1)
        try:
            client.sendall(
                b"GET /api/snapshot HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Authorization: Bearer " + _TOKEN.encode() + b"\r\n\r\n"
            )
            closed = client.recv(1) == b""
        except TimeoutError:
            pytest.fail("accepted connection remained open after begin_shutdown")
        except OSError:
            closed = True
        assert closed
        assert calls == 0
    finally:
        client.close()
        if thread.is_alive():
            _stop(server, thread)
        else:
            server.server_close()

    replacement = create_server(
        tmp_path / "unused",
        "replacement",
        port=int(port),
    )
    replacement.server_close()


def test_begin_shutdown_terminates_authenticated_keep_alive_connection(
    tmp_path: Path,
) -> None:
    calls = 0

    def snapshot_builder(
        _state_root: str | Path,
        _run_id: str,
    ) -> MonitorSnapshot:
        nonlocal calls
        calls += 1
        return cast(MonitorSnapshot, _SyntheticSnapshot({"call": calls}))

    server = create_server(
        tmp_path / "unused",
        "keep-alive",
        port=0,
        token=_TOKEN,
        snapshot_builder=snapshot_builder,
    )
    server.log_requests = False
    thread = _start(server)
    host, port = server.server_address[:2]
    connection = http.client.HTTPConnection(str(host), int(port), timeout=5)
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    try:
        connection.request("GET", "/api/snapshot", headers=headers)
        first = connection.getresponse()
        assert first.status == 200
        assert json.loads(first.read()) == {"call": 1}

        _stop(server, thread)
        with pytest.raises((OSError, http.client.HTTPException)):
            connection.request("GET", "/api/snapshot", headers=headers)
            connection.getresponse()
        assert calls == 1
    finally:
        connection.close()
        if thread.is_alive():
            _stop(server, thread)
        else:
            server.server_close()


@pytest.mark.parametrize("log_requests", (False, True))
def test_reset_diagnostics_follow_request_logging_mode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    log_requests: bool,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def snapshot_builder(
        _state_root: str | Path,
        _run_id: str,
    ) -> MonitorSnapshot:
        started.set()
        release.wait(timeout=5)
        return cast(MonitorSnapshot, _SyntheticSnapshot({"small": True}))

    server = create_server(
        tmp_path / "unused",
        "reset-diagnostics",
        port=0,
        token=_TOKEN,
        snapshot_builder=snapshot_builder,
    )
    server.log_requests = log_requests
    handled = threading.Event()
    original_handle_error = server.handle_error

    def tracked_handle_error(
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        try:
            original_handle_error(request, client_address)
        finally:
            handled.set()

    server.handle_error = tracked_handle_error  # type: ignore[method-assign]
    thread = _start(server)
    client = socket.create_connection(server.server_address[:2], timeout=5)
    try:
        client.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_LINGER,
            struct.pack("ii", 1, 0),
        )
        client.sendall(
            b"GET /api/snapshot HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Authorization: Bearer " + _TOKEN.encode() + b"\r\n\r\n"
        )
        assert started.wait(timeout=5)
        client.close()
        release.set()
        assert handled.wait(timeout=5)
    finally:
        release.set()
        client.close()
        _stop(server, thread)

    errors = capsys.readouterr().err
    if log_requests:
        assert "Exception occurred during processing" in errors
        assert "ConnectionResetError" in errors or "BrokenPipeError" in errors
    else:
        assert errors == ""


def test_quiet_server_preserves_unexpected_handler_diagnostics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def snapshot_builder(
        _state_root: str | Path,
        _run_id: str,
    ) -> MonitorSnapshot:
        raise RuntimeError("unexpected snapshot failure")

    server = create_server(
        tmp_path / "unused",
        "unexpected-handler-failure",
        port=0,
        token=_TOKEN,
        snapshot_builder=snapshot_builder,
    )
    server.log_requests = False
    handled = threading.Event()
    original_handle_error = server.handle_error

    def tracked_handle_error(
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        try:
            original_handle_error(request, client_address)
        finally:
            handled.set()

    server.handle_error = tracked_handle_error  # type: ignore[method-assign]
    thread = _start(server)
    client = socket.create_connection(server.server_address[:2], timeout=5)
    try:
        client.sendall(
            b"GET /api/snapshot HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Authorization: Bearer " + _TOKEN.encode() + b"\r\n\r\n"
        )
        assert handled.wait(timeout=5)
    finally:
        client.close()
        _stop(server, thread)

    errors = capsys.readouterr().err
    assert "Exception occurred during processing" in errors
    assert "RuntimeError: unexpected snapshot failure" in errors


def test_static_get_routes_lifecycle_and_security_headers(tmp_path: Path) -> None:
    server = create_server(tmp_path / "unused", "static-run", port=0, token=_TOKEN)
    thread = _start(server)
    try:
        status, headers, index = _request(server, "GET", "/")
        assert status == 200
        assert headers["content-type"] == "text/html; charset=utf-8"
        _assert_security_headers(headers)
        assert b"READ-ONLY" in index
        assert b"/assets/style.css" in index
        assert b"/assets/app.js" in index
        assert _TOKEN.encode() not in index

        status, headers, stylesheet = _request(server, "GET", "/assets/style.css")
        assert status == 200
        assert headers["content-type"] == "text/css; charset=utf-8"
        _assert_security_headers(headers)
        assert b"--bg:" in stylesheet

        status, headers, script = _request(server, "GET", "/assets/app.js")
        assert status == 200
        assert headers["content-type"] == "text/javascript; charset=utf-8"
        _assert_security_headers(headers)
        assert b"snapshot.hypotheses" in script
        assert b"research_contract" in script
        assert b"required-assessed" in script
        assert b"COMPLETED means lifecycle settlement, not GOAL_MET" in script
        assert b"durably open attempt" in script
        assert b"sessionStorage.setItem" in script
        assert script.index(b"sessionStorage.setItem") < script.index(
            b"history.replaceState"
        )
        assert b"Authorization" in script
        assert b"innerHTML" not in script

        for path in ("/missing", "/assets/missing.js", "/assets/../web_observer.py"):
            status, headers, body = _request(server, "GET", path)
            assert status == 404
            assert json.loads(body) == {"error": "not found"}
            _assert_security_headers(headers)

        status, headers, body = _request(server, "HEAD", "/")
        assert status == 405
        assert headers["allow"] == "GET"
        assert body == b""
        _assert_security_headers(headers)

        status, headers, body = _request(
            server,
            "GET",
            "/api/snapshot",
            token="non-ascii-é",
        )
        assert status == 401
        assert json.loads(body) == {"error": "unauthorized"}
        _assert_security_headers(headers)
        assert thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_frontend_assets_expose_read_only_operations_map_and_evidence_ux() -> None:
    asset_root = _REPOSITORY / "hypoloom/web_observer_assets"
    index = (asset_root / "index.html").read_text(encoding="utf-8")
    script = (asset_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (asset_root / "style.css").read_text(encoding="utf-8")

    parser = _IdAuditParser()
    parser.feed(index)
    assert len(parser.ids) == len(set(parser.ids))
    for view in ("operations", "map", "evidence"):
        assert f'id="tab-{view}"' in index
        assert f'data-view-panel="{view}"' in index
    for control_id in (
        "filter-search",
        "filter-component",
        "filter-expert",
        "filter-kind",
        "filter-state",
        "show-components",
        "show-hypotheses",
        "open-help",
        "runtime-source",
        "backup-quiescence",
        "agent-observed",
        "agent-budget",
        "resource-wait-list",
    ):
        assert f'id="{control_id}"' in index
    assert "Expert OPEN activity" in index
    assert "Rendezvous" in index
    assert "Oracle CLEAN" in index
    assert "does not prove a process is alive" in index
    assert '!("limit" in tokens) ? "unknown"' in script
    assert '!("limit" in turns) ? "unknown"' in script

    for behavior in (
        "setTopologyMode",
        "attemptMatches",
        "renderActivity",
        "renderRendezvous",
        "renderRuntime",
        "showRelation",
        "highlightConnected",
        "minimum_free_memory_mib",
        "latest_free_memory_mib",
        "latest_utilization_percent",
        "resource_pressure_exceeded",
        "token_enforcement",
        "observed_overage_attempts",
        "overage_tokens",
        "open_declared_limits",
        "closed_unknown_declared_limits",
        "budget_rejected_attempts",
        "closed accounting unknown",
        "zero charge",
        "revision_warning",
    ):
        assert behavior in script
    assert "const openDeclared = object(usage.open_declared_limits);" in script
    assert "const closedUnknown = object(usage.closed_unknown_declared_limits);" in script
    assert 'openDeclared.attempts ?? "?"' in script
    assert 'closedUnknown.turns ?? "?"' in script
    assert 'method: "GET"' in script
    assert 'state.selectedKey = `component:${componentId}`' in script
    assert "!proposerRoles.includes(filters.expert)" in script
    assert 'role: "img"' in script
    assert "innerHTML" not in script
    # Human answers change state; support export carries the reviewed issue
    # description in a POST body but remains read-only.
    assert script.count('method: "POST"') == 2
    assert 'fetch("/api/human-answer"' in script
    assert 'window.fetch(`/api/support?${supportOptions(format)}`' in script
    assert '"X-Hypoloom-Answer-Token": answerToken' in script

    assert ".view-tabs" in stylesheet
    assert ".graph-edge.connected" in stylesheet
    assert ".attempt-card.pressure-exceeded" in stylesheet
    assert "@media (max-width: 700px)" in stylesheet


@_REQUIRES_PRODUCT_SQLITE
def test_snapshot_bearer_auth_rebuilds_only_the_explicit_run(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    run_id = "web-explicit"
    _create_fresh_run(state_root, run_id)
    calls: list[tuple[Path, str]] = []

    def counted_builder(root: str | Path, selected_run_id: str) -> MonitorSnapshot:
        calls.append((Path(root), selected_run_id))
        return build_monitor_snapshot(root, selected_run_id)

    server = create_server(
        state_root,
        run_id,
        port=0,
        token=_TOKEN,
        snapshot_builder=counted_builder,
    )
    thread = _start(server)
    try:
        status, headers, body = _request(server, "GET", "/api/snapshot")
        assert status == 401
        assert headers["www-authenticate"] == "Bearer"
        assert json.loads(body) == {"error": "unauthorized"}
        assert run_id.encode() not in body
        _assert_security_headers(headers)

        status, _, body = _request(server, "GET", "/api/snapshot", token="wrong")
        assert status == 401
        assert json.loads(body) == {"error": "unauthorized"}
        assert calls == []

        status, headers, body = _request(
            server,
            "GET",
            "/api/snapshot?run_id=some-other-run",
            token=_TOKEN,
        )
        assert status == 200
        _assert_security_headers(headers)
        first = json.loads(body)
        assert first["run_id"] == run_id
        assert first["state_root"] == str(state_root.resolve())
        assert first["status_source"] == "offline-state-store"

        status, _, body = _request(server, "GET", "/api/snapshot", token=_TOKEN)
        assert status == 200
        assert json.loads(body)["run_id"] == run_id
        assert calls == [(state_root.resolve(), run_id), (state_root.resolve(), run_id)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@_REQUIRES_PRODUCT_SQLITE
def test_get_only_observation_does_not_write_durable_run_state(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    run_id = "web-read-only"
    _create_fresh_run(state_root, run_id)
    before_rows = _row_census(state_root / "state.sqlite")
    before_artifacts = _artifact_inventory(state_root)

    server = create_server(state_root, run_id, port=0, token=_TOKEN)
    thread = _start(server)
    try:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, headers, body = _request(
                server,
                method,
                "/api/snapshot",
                token=_TOKEN,
                body=b"{}",
            )
            assert status == 405
            assert headers["allow"] == "GET"
            assert json.loads(body) == {"error": "method not allowed"}
            _assert_security_headers(headers)

        for _ in range(2):
            status, _, body = _request(server, "GET", "/api/snapshot", token=_TOKEN)
            assert status == 200
            assert json.loads(body)["run_id"] == run_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert _row_census(state_root / "state.sqlite") == before_rows
    assert _artifact_inventory(state_root) == before_artifacts


def test_foreground_entry_prints_the_tokenized_url_once(monkeypatch) -> None:
    class FakeServer:
        def __enter__(self) -> FakeServer:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def startup_url(self) -> str:
            return f"http://127.0.0.1:43210/#token={_TOKEN}"

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    created: list[dict[str, object]] = []
    preflight_calls: list[tuple[Path, str]] = []

    def fake_snapshot_builder(
        state_root: str | Path,
        run_id: str,
    ) -> MonitorSnapshot:
        preflight_calls.append((Path(state_root), run_id))
        return cast(MonitorSnapshot, object())

    def fake_create_server(
        state_root: str | Path,
        run_id: str,
        *,
        host: str,
        port: int,
        token: str | None,
        snapshot_builder,
    ) -> FakeServer:
        created.append(
            {
                "state_root": state_root,
                "run_id": run_id,
                "host": host,
                "port": port,
                "token": token,
                "snapshot_builder": snapshot_builder,
            }
        )
        return FakeServer()

    monkeypatch.setattr(
        "hypoloom.web_observer.create_server", fake_create_server
    )
    output = io.StringIO()
    errors = io.StringIO()

    assert (
        run_web_observer(
            "/explicit/state",
            "explicit-run",
            host="127.0.0.1",
            port=0,
            token=None,
            snapshot_builder=fake_snapshot_builder,
            stdout=output,
            stderr=errors,
        )
        == 0
    )

    assert output.getvalue().count(_TOKEN) == 1
    assert errors.getvalue() == ""
    assert created == [
        {
            "state_root": "/explicit/state",
            "run_id": "explicit-run",
            "host": "127.0.0.1",
            "port": 0,
            "token": None,
            "snapshot_builder": fake_snapshot_builder,
        }
    ]
    assert preflight_calls == [(Path("/explicit/state"), "explicit-run")]


@pytest.mark.parametrize("port_args, expected_port", [([], 0), (["--port", "0"], 0), (["--port", "43210"], 43210)])
def test_cli_web_routes_one_explicit_run_to_the_loopback_observer(
    tmp_path: Path,
    monkeypatch,
    port_args,
    expected_port,
) -> None:
    calls: list[tuple[Path, str, int]] = []

    def fake_run(state_root: Path, run_id: str, *, port: int) -> int:
        calls.append((state_root, run_id, port))
        return 0

    monkeypatch.setattr(
        "hypoloom.web_observer.run_web_observer",
        fake_run,
    )
    state_root = tmp_path / "explicit-state"
    assert cli_main([
        "web",
        "--state-root",
        str(state_root),
        "--run-id",
        "explicit-run",
        *port_args,
    ]) == 0
    assert calls == [(state_root.resolve(), "explicit-run", expected_port)]
