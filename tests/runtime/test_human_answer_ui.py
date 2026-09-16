"""Operator-only answer transport, interactive drafts, review and retry behavior."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import http.client
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from hypoloom.control_protocol import ControlResponse
from hypoloom.human_answers import answer_request
from hypoloom.web_observer import create_server
from test_web_observer import _TOKEN, _start, _stop, _create_fresh_run


def post_answer(server, values, *, token=_TOKEN, answer_token=None, origin=None, path="/api/human-answer", content_type="application/json"):
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    try:
        headers = {"Authorization": "Bearer " + token, "Content-Type": content_type,
            "Origin": origin or f"http://127.0.0.1:{server.server_port}"}
        if answer_token is not None:
            headers["X-Hypoloom-Answer-Token"] = answer_token
        connection.request("POST", path, json.dumps(values).encode(), headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_web_answer_requires_separate_operator_grant_and_preserves_exact_retry(tmp_path, monkeypatch):
    import hypoloom.human_answers as transport
    calls = []
    def send(path, request, *, timeout):
        calls.append((path, request))
        if len(calls) == 1:
            raise ConnectionError("reply lost after delivery")
        return ControlResponse.accepted(request.request_id, 6, {"run_id": "selected", "run_state": "ACTIVE"})
    monkeypatch.setattr(transport, "request_over_socket", send)
    root = tmp_path / "absent"
    server = create_server(root, "selected", port=0, token=_TOKEN)
    thread = _start(server)
    values = {"decision_id": "question-1", "answer": "  Preserve two splits.\n五个 seed。  ", "expected_revision": 5, "request_id": "same-answer"}
    try:
        assert server._answer_token is None
        assert post_answer(server, values)[0] == 403
        grant = parse_qs(urlsplit(server.human_answer_url()).fragment)["answer_token"][0]
        assert grant not in server.startup_url()
        assert post_answer(server, values)[0] == 403  # reader remains read-only after grant
        assert post_answer(server, values, answer_token=grant, token="wrong")[0] == 401
        assert post_answer(server, values, answer_token="wrong")[0] == 403
        for origin in ("https://example.invalid", "null", "http://example.invalid", "http://[broken"):
            assert post_answer(server, values, answer_token=grant, origin=origin)[0] in {400, 403}
        assert post_answer(server, values, answer_token=grant, content_type="text/plain")[0] == 415
        for extra in ({"run_id": "foreign"}, {"command": "CANCEL"}, {"path": "/tmp/foreign"}, {"expected_revision": None}, {"answer": "  "}):
            assert post_answer(server, {**values, **extra}, answer_token=grant)[0] == 400
        assert post_answer(server, values, answer_token=grant, path="/api/cancel")[0] == 405
        assert not calls and not root.exists()
        status, first = post_answer(server, values, answer_token=grant)
        assert status == 503 and first["error"] == "delivery_unknown"
        status, second = post_answer(server, values, answer_token=grant)
        assert status == 200 and second["status"] == "accepted"
        assert len(calls) == 2 and calls[0] == calls[1]
        assert calls[0][1].command == "ANSWER" and calls[0][1].payload["run_id"] == "selected"
        assert calls[0][1].payload["answer"] == values["answer"]
        assert not root.exists()
    finally:
        _stop(server, thread)


def test_answer_request_reuses_control_validation_and_cli_web_opt_in(tmp_path, monkeypatch):
    from hypoloom import cli, web_observer
    values = {"decision_id": "question", "answer": "yes", "expected_revision": 5, "request_id": "answer"}
    assert answer_request("run", values).expected_revision == 5
    for changes in ({"expected_revision": True}, {"decision_id": ""}, {"answer": "no\x00yes"}, {"request_id": ""}):
        with pytest.raises(ValueError):
            answer_request("run", {**values, **changes})
    calls = []
    monkeypatch.setattr(web_observer, "run_web_observer", lambda *a, **kw: calls.append((a, kw)) or 0)
    assert cli.main(["web", "--state-root", str(tmp_path), "--run-id", "run", "--allow-human-answers"]) == 0
    assert calls[0][1]["allow_human_answers"] is True


def question_snapshot(tmp_path):
    from hypoloom.monitor import build_monitor_snapshot
    root = tmp_path / "state"
    _create_fresh_run(root, "human-ui")
    snapshot = build_monitor_snapshot(root, "human-ui")
    decision = {"decision_id": "pending-question", "state": "OPEN", "ordinal": 1,
        "question": "应该怎样定义验收？" * 12, "rationale": "The interpretation changes the experiment.",
        "options": ["Option A: " + "保留当前规则。" * 25, "Option B: use a revised feature extractor"], "answer": None}
    return root, replace(snapshot, status={**snapshot.status, "run_state": "WAITING_HUMAN", "run_revision": 5, "grounding_dialogue": [decision]})


def test_tui_answer_preserves_draft_across_refresh_and_requires_review_then_submit(tmp_path, monkeypatch):
    from hypoloom import human_decision_tui as form
    from hypoloom.tui import HypoloomVNextMonitor
    from textual.widgets import Button, Static, TextArea
    root, snapshot = question_snapshot(tmp_path)
    calls = []
    def send(state_root, run_id, values):
        calls.append(dict(values))
        assert state_root == root and run_id == "human-ui"
        if len(calls) == 1:
            raise ConnectionError("uncertain delivery")
        return ControlResponse.accepted(values["request_id"], 6, {"run_state": "ACTIVE"})
    monkeypatch.setattr(form, "submit_answer", send)
    async def exercise():
        app = HypoloomVNextMonitor(root, "human-ui", initial_snapshot=snapshot, interval=3600, web_enabled=False)
        async with app.run_test(size=(100, 44)) as pilot:
            await pilot.pause()
            assert app.query_one("#human-decision-button").display
            await pilot.press("h")
            screen = app.screen
            assert isinstance(screen, form.HumanDecisionScreen)
            text = screen.query_one("#human-answer", TextArea)
            assert text.text == "" and screen.query_one("#human-submit", Button).disabled
            text.load_text("保留两套划分。\nUse five seeds.")
            await pilot.pause()
            app._render(snapshot)
            assert text.text == "保留两套划分。\nUse five seeds." and not calls
            await pilot.click("#human-submit")
            assert not calls and text.read_only
            await pilot.pause(.4)
            await pilot.click("#human-submit")
            await pilot.pause()
            assert len(calls) == 1 and "not confirmed" in str(screen.query_one("#human-form-status", Static).render())
            await pilot.press("escape")
            await pilot.press("h")
            assert app.screen.query_one("#human-answer", TextArea).text == calls[0]["answer"]
            await pilot.click("#human-submit")
            await pilot.pause()
            assert calls == [calls[0], calls[0]]
            assert app.screen.query_one("#human-submit", Button).disabled
    asyncio.run(exercise())


def test_tui_stale_and_resolved_question_does_not_submit(tmp_path, monkeypatch):
    from hypoloom import human_decision_tui as form
    from hypoloom.tui import HypoloomVNextMonitor
    from textual.widgets import Button, TextArea
    root, snapshot = question_snapshot(tmp_path)
    monkeypatch.setattr(form, "submit_answer", lambda *a: pytest.fail("stale decision submitted"))
    async def exercise():
        app = HypoloomVNextMonitor(root, "human-ui", initial_snapshot=snapshot, interval=3600, web_enabled=False)
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#human-decision-button")
            screen = app.screen
            screen.query_one("#human-answer", TextArea).load_text("Keep this draft")
            await pilot.pause()
            app._render(replace(snapshot, status={**snapshot.status, "run_revision": 6}))
            assert screen.query_one("#human-submit", Button).disabled
            await pilot.press("escape")
            await pilot.press("h")
            screen = app.screen
            assert screen.query_one("#human-answer", TextArea).text == "Keep this draft"
            assert not screen.query_one("#human-submit", Button).disabled
            current = {**snapshot.status["grounding_dialogue"][0], "state": "RESOLVED", "answer": "Other operator answer"}
            app._render(replace(snapshot, status={**snapshot.status, "run_state": "ACTIVE", "run_revision": 7, "grounding_dialogue": [current]}))
            assert screen.query_one("#human-submit", Button).disabled
            assert all(button.region.right <= screen.query_one("#human-form").region.right for button in screen.query(Button))
            app.save_screenshot("human-decision-narrow.svg", path=tmp_path)
    asyncio.run(exercise())
