"""Authenticated, read-only support downloads and the TUI entry point."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile

from hypoloom.web_observer import create_server
from test_web_observer import _TOKEN, _create_fresh_run, _request, _row_census, _start, _stop


def test_support_http_preview_download_scope_and_source_immutability(tmp_path):
    root = tmp_path / "state"
    _create_fresh_run(root, "private-project-run")
    before = _row_census(root / "state.sqlite")
    server = create_server(root, "private-project-run", port=0, token=_TOKEN)
    thread = _start(server)
    try:
        assert _request(server, "GET", "/api/support")[0] == 401
        assert _request(server, "POST", "/api/support", token=_TOKEN)[0] == 400
        for query in ("run_id=other", "path=/etc/passwd", "private=2", "format=sql", "max_mib=9999",
                      "scope=run&scope=failures", "attempt_id=not-in-run", "attempt_id=", "attempt_id=../outside"):
            assert _request(server, "GET", "/api/support?" + query, token=_TOKEN)[0] == 400
        status, headers, body = _request(server, "GET", "/api/support?format=preview", token=_TOKEN)
        assert status == 200 and headers["cache-control"] == "no-store"
        preview = json.loads(body)
        assert preview["report"]["diagnosis"]["available"]
        assert preview["report"]["include_private_data"] is False
        assert b"private-project-run" not in body and str(root).encode() not in body
        status, headers, body = _request(server, "GET", "/api/support?format=zip", token=_TOKEN)
        assert status == 200 and headers["content-type"] == "application/zip"
        assert "hypoloom-support.zip" in headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            assert json.loads(archive.read("report.json"))["run_id"] == "run-1"
            assert "README.md" in archive.namelist()
        server._support_lock.acquire()
        try:
            assert _request(server, "GET", "/api/support", token=_TOKEN)[0] == 429
        finally:
            server._support_lock.release()
    finally:
        _stop(server, thread)
    assert _row_census(root / "state.sqlite") == before


def test_support_post_description_uses_authorized_bounded_body(tmp_path):
    import http.client
    server = create_server(tmp_path / "absent", "report", port=0, token=_TOKEN)
    thread = _start(server)
    try:
        for token, expected in ((None, 401), (_TOKEN, 200)):
            connection = http.client.HTTPConnection(*server.server_address[:2], timeout=5)
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            connection.request("POST", "/api/support?format=preview", headers=headers,
                body=json.dumps({"actual": "Error API_KEY=fixture-secret", "steps": "Start once"}))
            response = connection.getresponse()
            body = response.read()
            assert response.status == expected
            if expected == 200:
                assert json.loads(body)["report"]["issue_description"]["steps"] == "Start once"
                assert b"fixture-secret" not in body
            connection.close()
    finally:
        _stop(server, thread)
    assert not (tmp_path / "absent").exists()


def test_support_endpoint_remains_available_when_run_diagnosis_cannot_load(tmp_path):
    server = create_server(tmp_path / "unavailable-state", "unavailable-run", port=0, token=_TOKEN)
    thread = _start(server)
    try:
        status, _, body = _request(server, "GET", "/api/support", token=_TOKEN)
        assert status == 200
        preview = json.loads(body)
        assert not preview["report"]["diagnosis"]["available"]
        assert not preview["diagnostics"]["oracle"]["available"]
    finally:
        _stop(server, thread)
    assert not (tmp_path / "unavailable-state").exists()


def test_tui_support_entry_opens_correct_browser_flow_and_offline_command(tmp_path):
    from hypoloom.monitor import build_monitor_snapshot
    from hypoloom.tui import HypoloomVNextMonitor, WebAccessScreen
    from textual.widgets import Static
    root = tmp_path / "state"
    _create_fresh_run(root, "support-entry")
    before = _row_census(root / "state.sqlite")

    async def exercise():
        for web_enabled in (False, True):
            app = HypoloomVNextMonitor(root, "support-entry", initial_snapshot=build_monitor_snapshot(root, "support-entry"),
                interval=3600, web_enabled=web_enabled, web_port=0)
            async with app.run_test(size=(120, 44)) as pilot:
                await pilot.pause()
                visible = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
                assert visible.lower().count("report issue") == 1
                await pilot.press("b")
                await pilot.pause()
                assert isinstance(app.screen, WebAccessScreen)
                text = str(app.screen.query_one("#web-access-content", Static).render())
                assert "REPORT AN ISSUE" in text and "--preview" in text and "--support-bundle" in text
                if web_enabled:
                    assert app.screen._access_url.endswith("&support=1")
                else:
                    assert app.screen._access_url is None
                await pilot.press("escape")
                await pilot.click("#support-help-button")
                assert isinstance(app.screen, WebAccessScreen)
                await pilot.resize_terminal(80, 40)
                await pilot.pause()
                app.save_screenshot("support-entry.svg", path=tmp_path)
    asyncio.run(exercise())
    assert _row_census(root / "state.sqlite") == before
