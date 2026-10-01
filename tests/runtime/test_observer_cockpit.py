"""Actual Textual interactions and read-only HTTP summary/detail integration."""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import selectors
import subprocess
import sys
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from hypoloom.monitor import HypothesisView, build_monitor_snapshot
from hypoloom.observation_api import PRODUCT_EVENT_TYPES, product_events
from hypoloom.web_observer import create_server
from test_web_observer import _TOKEN, _create_fresh_run, _request, _row_census, _start, _stop
from test_monitor import _static_text


def _overview():
    counts = {"experiments": {"total": 4, "states": {"EXECUTING": 1}, "outcomes": {"SUCCESS": 1}},
        "hypotheses": 3, "chains": 2, "active_chains": 1, "chain_coverage": "complete",
        "literature": {"retrieved": 9, "pdf_available": 6, "coverage": "complete"},
        "skills": {"count": 3, "coverage": "complete"},
        "evolutions": {"changed": 1, "completed": 2, "failed": 0},
        "leader": {"started": 2, "active": 0, "completed": 1, "aborted": 1, "failed": 0},
        "debate": {"started": 1, "active": 1, "completed": 0, "aborted": 0, "failed": 0}}
    stage = {"node": "RESULT_ANALYSIS", "chain": "chain-1", "component_id": "c", "cycle": 1,
        "state": "OPEN", "subkind": "MAIN", "attempt_id": "retry-2", "attempts": 2, "elapsed_seconds": 21}
    return {"elapsed_seconds": 3661, "waiting_human_seconds": 120, "remaining_seconds": 7200,
        "totals": counts, "notices": ["OPEN is not a heartbeat"], "experts": [
            {"role": role, "cycle": 1, **counts, "stages": [stage], "chains": [
                {"chain": "chain-1", "component_id": "c", "cycle": 1,
                 "nodes": [{"hypothesis_id": role + "-h1", "assessment": "SUPPORTED", "pruned": False}],
                 "stages": [stage]}]} for role in ("expert-a", "expert-b")]}


def test_tui_execution_reliability_preserves_unknowns_and_per_expert_scope(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Static
    from hypoloom.tui import HypoloomVNextMonitor, ExpertsPanel, SummaryPanel
    from test_observer_browser_ui import _reliability
    root = tmp_path / "reliability"
    _create_fresh_run(root, "reliability")
    overview = _overview()
    overview["reliability"] = _reliability()
    overview["experts"][0]["reliability"] = _reliability()
    snapshot = replace(build_monitor_snapshot(root, "reliability"), overview=overview)

    async def exercise():
        app = HypoloomVNextMonitor(root, "reliability", initial_snapshot=snapshot,
                                    interval=3600, web_enabled=False)
        async with app.run_test(size=(150, 60)) as pilot:
            await pilot.pause()
            summary = str(app.query_one("#summary", SummaryPanel).query_one(Static).render())
            lanes = _static_text(app.query_one("#experts", ExpertsPanel).query_one(Static))
            for expected in ("FP 1/2", "50%", "?1", "CR 1/1", "RT 1/2", "DL 1/2", "RA 0/0"):
                assert expected in summary and expected in lanes
            assert "FP ?" in lanes  # expert-b has no evidence
            assert "$" not in summary and "$" not in lanes
            assert "Retry ≥250 tok" in summary
            assert "res 2K" in summary and "closed ?1" in summary
            app._render(replace(snapshot, overview=_overview()))
            assert "FP ?" in str(app.query_one("#summary", SummaryPanel).query_one(Static).render())
    asyncio.run(exercise())


def test_tui_expert_cards_follow_variable_rosters_and_reflow_without_losing_scroll(tmp_path, monkeypatch):
    from copy import deepcopy
    from rich.console import Console
    from textual.widgets import Select, Static
    from hypoloom.tui import ExpertsPanel, HypoloomVNextMonitor, SummaryPanel
    from test_observer_browser_ui import _reliability
    monkeypatch.delenv("NO_COLOR", raising=False)
    root = tmp_path / "variable-roster"
    _create_fresh_run(root, "variable-roster")
    initial = build_monitor_snapshot(root, "variable-roster")

    async def exercise():
        app = HypoloomVNextMonitor(root, "variable-roster", initial_snapshot=initial,
                                    interval=3600, web_enabled=False)
        async with app.run_test(size=(150, 64)) as pilot:
            lanes = app.query_one(ExpertsPanel)
            for count in (0, 1, 4, 9):
                overview = _overview()
                overview["reliability"] = _reliability()
                overview["accounting"] = {"reported_tokens": 12500000, "reserved_tokens": 800000,
                                          "unknown_closed_attempts": 1, "unavailable_token_attempts": 0}
                template = overview["experts"][0]
                template.update(reliability=_reliability(), accounting={"reported_tokens": 1250000,
                    "reserved_tokens": 80000, "unknown_closed_attempts": 0, "unavailable_token_attempts": 0})
                overview["experts"] = [{**deepcopy(template), "role": f"profile-expert-{index + 1}"}
                                       for index in range(count)]
                roster = tuple(replace(initial.experts[0], role=item["role"], slot_ordinal=index + 1)
                               for index, item in enumerate(overview["experts"]))
                snapshot = replace(initial, overview=overview, experts=roster)
                app._render(snapshot)
                await pilot.pause()
                content = lanes.query_one(Static)
                text = _static_text(content)
                assert text.count("╭") == count and text.count("╰") == count
                assert all(item.role in text for item in roster)
                if not count:
                    assert "No frozen roster" in text
                    continue
                if count == 9:
                    app.save_screenshot("expert-cards-desktop.svg", path=tmp_path)
                    lanes.focus()
                    await pilot.press("end")
                    await pilot.pause()
                    assert lanes.scroll_y > 0
                    position, visual = lanes.scroll_y, content.visual
                    app._render(snapshot)
                    await pilot.pause()
                    assert lanes.scroll_y == position and content.visual is visual
                    totals = str(app.query_one(SummaryPanel).query_one(Static).render())
                    app.query_one("#expert-filter", Select).value = roster[-1].role
                    await pilot.pause()
                    assert _static_text(content).count("╭") == 1 and roster[-1].role in _static_text(content)
                    assert str(app.query_one(SummaryPanel).query_one(Static).render()) == totals
                    app.query_one("#expert-filter", Select).value = ""
                    for width in (100, 60):
                        await pilot.resize_terminal(width, 64)
                        await pilot.pause()
                        # Render the actual card content at its assigned width;
                        # long stage/metric rows must wrap inside each border.
                        actual_width = content.content_size.width
                        # Textual captures stdout as a TTY. TERM=dumb would make
                        # a separate Rich console ignore this width and use 80.
                        console = Console(width=actual_width, force_terminal=False)
                        assert console.options.max_width == actual_width
                        rows = console.render_lines(content.content, console.options, pad=False)
                        from rich.segment import Segment
                        assert all(Segment.get_line_length(row) <= actual_width for row in rows)
                        assert _static_text(content).count("╭") == count
                    lanes.scroll_home(animate=False)
                    app.save_screenshot("expert-cards-narrow.svg", path=tmp_path)
                    app._render(replace(initial, overview={**_overview(), "experts": []}, experts=()))
                    await pilot.pause()
                    assert "profile-expert-" not in _static_text(content)
                    assert "No frozen roster" in _static_text(content)
    asyncio.run(exercise())


def test_tui_numerical_summary_expandable_chains_and_filter_survive_refresh(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Select, Static, Tree
    from hypoloom.tui import HypoloomVNextMonitor, SummaryPanel, ExpertsPanel
    root = tmp_path / "cockpit"
    _create_fresh_run(root, "cockpit")
    snapshot = replace(build_monitor_snapshot(root, "cockpit"), overview=_overview())

    async def exercise():
        app = HypoloomVNextMonitor(root, "cockpit", initial_snapshot=snapshot, interval=3600, web_enabled=False)
        async with app.run_test(size=(140, 54)) as pilot:
            await pilot.pause()
            text = str(app.query_one("#summary", SummaryPanel).query_one(Static).render())
            assert "1h 01m" in text and "Human wait" in text
            assert "Lit 9" in text and "PDF 6" in text
            assert "Skills 3" in text and "EVO 1" in text
            assert all(title in text for title in ("RESEARCH ACTIVITY", "BUDGET & TOKENS", "EXECUTION RELIABILITY", "RUN HEALTH"))
            tree = app.query_one("#hypothesis-tree", Tree)
            assert len(tree.root.children) == 2
            expert = tree.root.children[0]
            expert.expand()
            chain = expert.children[0]
            chain.expand()
            tree.move_cursor(chain)
            await pilot.pause()
            app._render(replace(snapshot, observed_at=snapshot.observed_at + 2))
            await pilot.pause()
            assert tree.root.children[0].children[0].is_expanded
            assert tree.cursor_node.data == chain.data
            app.query_one("#expert-filter", Select).value = "expert-b"
            await pilot.pause()
            assert len(tree.root.children) == 1 and tree.root.children[0].data == ("expert", "expert-b")
            experts = _static_text(app.query_one("#experts", ExpertsPanel).query_one(Static))
            assert "expert-b" in experts and "expert-a" not in experts and "ANA" in experts
            app._render(snapshot)
            assert app.query_one("#expert-filter", Select).value == "expert-b"
            assert len(tree.root.children) == 1
            legacy_node = HypothesisView(hypothesis_id="retained-node", component_id="c", proposer_role="expert-b",
                method_family_id="method", claim="retained claim", assumptions=(), parent_hypothesis_id=None,
                relation=None, assessment_id=None, evidence_ids=(), assessment=None)
            app._render(replace(snapshot, hypotheses=(legacy_node,)))
            await pilot.pause()
            unknown = tree.root.children[0].children[-1]
            assert unknown.data == ("unassigned", "expert-b")
            assert unknown.children[0].data == ("hypothesis", "retained-node")
            assert not app.query_one("#attempts").display
            selector = app.query_one("#expert-filter", Select)
            selector.blur()
            await pilot.press("d")
            await pilot.pause()
            assert app.query_one("#attempts").display
            await pilot.press("d")
            await pilot.resize_terminal(60, 24)
            await pilot.pause()
            assert app.screen.has_class("narrow")
            app.save_screenshot("cockpit.svg", path=str(tmp_path))
            await pilot.press("q")
    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["leader_aborted", "debate_aborted"])
def test_goal_stop_aborts_are_observable_terminal_events(kind):
    row = {"workflow_id": "run", "workflow_kind": "RUN", "revision": 5, "command_id": "stop",
        "command_type": "ACCEPT_EXPERT_INTERACTION", "created_at": 2,
        "payload": {"expert_interaction": {"action": kind, "reason": "goal_stop"}}}
    assert kind in PRODUCT_EVENT_TYPES
    result = product_events("run", [], [row], [])
    assert len(result) == 1 and result[0]["type"] == kind


def test_http_summary_and_details_are_authenticated_get_only_and_read_only(tmp_path):
    root = tmp_path / "http-ui"
    _create_fresh_run(root, "http-ui")
    before = _row_census(root / "state.sqlite")
    server = create_server(root, "http-ui", port=0, token=_TOKEN)
    thread = _start(server)
    try:
        status, _, _ = _request(server, "GET", "/api/details?kind=skills")
        assert status == 401
        status, _, body = _request(server, "GET", "/api/activity", token=_TOKEN)
        assert status == 200
        summary = json.loads(body)["summary"]
        assert summary["totals"]["experiments"]["total"] == 0
        for kind in ("skills", "literature", "sessions", "interactions", "resources"):
            status, _, body = _request(server, "GET", f"/api/details?kind={kind}", token=_TOKEN)
            assert status == 200, body
            assert json.loads(body)["run_id"] == "http-ui"
        for query in ("kind=skills&run_id=other", "kind=skills&path=/etc/passwd", "kind=skills&kind=attempt",
                      "kind=skills&limit=-1", "kind=", "kind=not-a-kind"):
            status, _, _ = _request(server, "GET", "/api/details?" + query, token=_TOKEN)
            assert status == 400
        status, _, _ = _request(server, "POST", "/api/details?kind=skills", token=_TOKEN)
        assert status == 405
    finally:
        _stop(server, thread)
    assert _row_census(root / "state.sqlite") == before


def test_pool_observation_requires_explicit_server_configuration(tmp_path, monkeypatch):
    from hypoloom import observer_details
    calls = []
    def read_pool(path, **kwargs):
        calls.append((path, kwargs))
        return {"available": True, "scope": "aggregate", "devices": [{"gpu_uuid": "GPU-1", "free_memory_mib": 1024}]}
    monkeypatch.setattr(observer_details, "read_observer_pool", read_pool)
    root = tmp_path / "pool-ui"
    _create_fresh_run(root, "pool-ui")
    for explicit in (False, True):
        server = create_server(root, "pool-ui", port=0, token=_TOKEN,
            **({"gpu_pool_socket": tmp_path / "pool.sock", "gpu_pool_server_uid": 1000} if explicit else {}))
        thread = _start(server)
        try:
            status, _, body = _request(server, "GET", "/api/details?kind=resources", token=_TOKEN)
            assert status == 200 and json.loads(body)["shared_pool"]["available"] is explicit
            status, _, _ = _request(server, "GET", "/api/details?kind=resources&socket_path=/tmp/private.sock", token=_TOKEN)
            assert status == 400
        finally:
            _stop(server, thread)
    assert calls == [(tmp_path / "pool.sock", {"expected_server_uid": 1000})]


@pytest.mark.parametrize("command", ["monitor", "web"])
def test_cli_passes_explicit_resource_view_options(tmp_path, monkeypatch, command):
    from hypoloom import monitor, web_observer
    from hypoloom.cli import main
    captured = {}
    def run(*args, **kwargs):
        captured.update(kwargs)
        return 0
    monkeypatch.setattr(monitor if command == "monitor" else web_observer,
        "run_monitor" if command == "monitor" else "run_web_observer", run)
    assert main([command, "--state-root", str(tmp_path), "--run-id", "r",
        "--gpu-pool-socket", str(tmp_path / "pool.sock"), "--gpu-pool-server-uid", "1000"]) == 0
    assert captured["gpu_pool_socket"] == str(tmp_path / "pool.sock")
    assert captured["gpu_pool_server_uid"] == 1000


def test_real_web_cli_process_serves_and_closes_without_run_writes(tmp_path):
    root = tmp_path / "web-process"
    _create_fresh_run(root, "web-process")
    before = _row_census(root / "state.sqlite")
    process = subprocess.Popen([sys.executable, "-B", "-m", "hypoloom.web_observer",
        "--state-root", str(root), "--run-id", "web-process", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=10), "observer child did not report readiness"
        line = process.stdout.readline()
        assert line.startswith("web observer: http://127.0.0.1:")
        url = urlsplit(line.removeprefix("web observer: ").strip())
        token = parse_qs(url.fragment)["token"][0]
        for path in ("/api/snapshot", "/api/activity", "/api/details?kind=skills", "/api/details?kind=resources"):
            connection = http.client.HTTPConnection("127.0.0.1", url.port, timeout=5)
            try:
                connection.request("GET", path, headers={"Authorization": f"Bearer {token}"})
                response = connection.getresponse()
                assert response.status == 200
                assert json.loads(response.read())["run_id"] == "web-process"
            finally:
                connection.close()
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
    assert process.poll() is not None
    assert _row_census(root / "state.sqlite") == before
