"""Leaving the observer and explicitly cancelling a run are separate actions."""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace

import pytest
from textual.widgets import Button, Static

from hypoloom.control_protocol import ControlResponse
from hypoloom.monitor import build_monitor_snapshot
from hypoloom.tui import HypoloomVNextMonitor
from test_web_observer import _create_fresh_run


def run_snapshot(tmp_path):
    root = tmp_path / "state"
    _create_fresh_run(root, "end-ui")
    return root, build_monitor_snapshot(root, "end-ui")


def app_for(root, snapshot):
    return HypoloomVNextMonitor(root, "end-ui", initial_snapshot=snapshot,
        interval=3600, web_enabled=False)


def test_fatal_monitor_exception_is_recorded_and_nonzero_exit_is_preserved(tmp_path, monkeypatch):
    import json
    from textual.app import App
    from hypoloom import tui
    root, snapshot = run_snapshot(tmp_path)
    app = app_for(root, snapshot)
    seen = []
    monkeypatch.setattr(App, "_handle_exception", lambda self, exc: seen.append(exc))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-test-key")
    error = ValueError("render failed private-test-key")
    app._handle_exception(error)
    record = json.loads((root / "diagnostics/host.jsonl").read_text().splitlines()[-1])
    assert record["phase"] == "tui" and record["run_id"] == "end-ui"
    assert record["exception_type"] == "ValueError" and seen == [error]
    assert "private-test-key" not in json.dumps(record)

    class FailedApp:
        return_code = 1

        def __init__(self, *args, **kwargs): pass
        def run(self): pass
        def close_web_observer(self): seen.append("closed")

    monkeypatch.setattr(tui, "HypoloomVNextMonitor", FailedApp)
    with pytest.raises(SystemExit) as failure:
        tui.run_tui(root, "end-ui")
    assert failure.value.code == 1 and seen[-1] == "closed"


def test_leaving_monitor_preserves_run_and_explains_background_work(tmp_path):
    root, snapshot = run_snapshot(tmp_path)

    async def exercise():
        app = app_for(root, snapshot)
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            hint = str(app.query_one("#monitor-exit-help", Static).render())
            assert "Host and tasks continue" in hint
            assert app.query_one("#end-run-button", Button).region.right <= 80
            await pilot.press("q")
            assert app.return_code == 0

    asyncio.run(exercise())
    after = build_monitor_snapshot(root, "end-ui")
    assert after.status["run_state"] == snapshot.status["run_state"]
    assert after.status["run_revision"] == snapshot.status["run_revision"]


def test_cancel_requires_explicit_confirmation_and_does_not_claim_host_stopped(tmp_path, monkeypatch):
    from hypoloom import end_run_tui as form
    root, snapshot = run_snapshot(tmp_path)
    calls = []

    def send(path, request, *, timeout):
        calls.append(request)
        assert path == root / "control.sock" and timeout == 30
        assert request.command == "CANCEL"
        assert request.expected_revision == snapshot.status["run_revision"]
        assert request.payload == {"run_id": "end-ui", "cause": "operator_request_from_tui"}
        return ControlResponse.accepted(request.request_id, request.expected_revision + 1,
            {"run_id": "end-ui", "run_state": "STOPPING"})

    monkeypatch.setattr(form, "request_over_socket", send)

    async def exercise():
        app = app_for(root, snapshot)
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=(80, 32)) as pilot:
            await pilot.pause()
            await pilot.click("#end-run-button")
            screen = app.screen
            assert isinstance(screen, form.EndRunScreen)
            assert app.focused.id == "end-run-close"
            assert "end-ui" in str(screen.query_one("#end-run-target", Static).render())
            assert all(button.region.right <= screen.query_one("#end-run-form").region.right
                for button in screen.query(Button))
            app._render(snapshot)
            assert not calls
            await pilot.press("enter")  # Default action leaves the run alone.
            assert not isinstance(app.screen, form.EndRunScreen) and not calls
            await pilot.press("x")
            await pilot.click("#end-run-confirm")
            await pilot.pause()
            assert len(calls) == 1
            assert app.screen.query_one("#end-run-confirm", Button).disabled
            message = str(app.screen.query_one("#end-run-status", Static).render())
            assert "STOPPING" in message and "Host has not been confirmed stopped" in message
            await pilot.press("escape")
            await pilot.press("x")
            assert app.screen.query_one("#end-run-confirm", Button).disabled
            await pilot.click("#end-run-confirm")
            assert len(calls) == 1  # A stale snapshot cannot reopen an accepted cancel.
            app._render(replace(snapshot, status={**snapshot.status,
                "run_state": "CANCELLED", "run_revision": calls[0].expected_revision + 2}))
            message = str(app.screen.query_one("#end-run-status", Static).render())
            assert "CANCELLED" in message and "Host has not been confirmed stopped" in message

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError])
def test_unconfirmed_cancel_retries_exact_request_after_reopen_and_revision_change(tmp_path, monkeypatch, failure):
    from hypoloom import end_run_tui as form
    root, snapshot = run_snapshot(tmp_path)
    calls = []

    def send(path, request, *, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise failure("Host reply lost")
        return ControlResponse.accepted(request.request_id, request.expected_revision + 1,
            {"run_id": "end-ui", "run_state": "STOPPING"})

    monkeypatch.setattr(form, "request_over_socket", send)

    async def exercise():
        app = app_for(root, snapshot)
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=(90, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.click("#end-run-confirm")
            await pilot.pause()
            assert len(calls) == 1
            assert "not confirmed" in str(app.screen.query_one("#end-run-status", Static).render())
            app._render(replace(snapshot, status={**snapshot.status,
                "run_revision": snapshot.status["run_revision"] + 1}))
            await pilot.press("escape")
            await pilot.press("x")
            assert str(app.screen.query_one("#end-run-confirm", Button).label) == "Retry same request"
            await pilot.click("#end-run-confirm")
            await pilot.pause()
            assert calls == [calls[0], calls[0]]
            assert app.screen.query_one("#end-run-confirm", Button).disabled

    asyncio.run(exercise())


def test_unconfirmed_cancel_renders_durably_accepted_command_without_retry(tmp_path, monkeypatch):
    from hypoloom import end_run_tui as form
    from hypoloom.workflow_host import WorkflowHost
    root, snapshot = run_snapshot(tmp_path)
    calls = []

    def send(path, request, *, timeout):
        # The Host durably accepts the exact request, but its reply is lost.
        calls.append(request)
        assert path == root / "control.sock" and timeout == 30
        host = WorkflowHost(root)
        try:
            host.initialize()
            assert host.handle(request).status == "accepted"
        finally:
            host.close()
        raise TimeoutError("Host reply arrived after the timeout")

    monkeypatch.setattr(form, "request_over_socket", send)

    async def exercise():
        app = app_for(root, snapshot)
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=(90, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.click("#end-run-confirm")
            await pilot.pause()
            assert len(calls) == 1
            message = str(app.screen.query_one("#end-run-status", Static).render())
            assert "durably accepted" in message and "after the timeout" in message
            assert str(app.screen.query_one("#end-run-confirm", Button).label) != "Retry same request"
            assert app.screen.query_one("#end-run-confirm", Button).disabled
            assert app._end_run_draft.get("accepted_revision") is not None
            assert "pending" not in app._end_run_draft
            await pilot.click("#end-run-confirm")
            assert len(calls) == 1  # A durably accepted cancel is never retried.
            await pilot.press("escape")
            await pilot.press("x")
            assert str(app.screen.query_one("#end-run-confirm", Button).label) != "Retry same request"
            assert app.screen.query_one("#end-run-confirm", Button).disabled

    asyncio.run(exercise())


def test_unconfirmed_cancel_keeps_retry_path_when_reconciliation_read_fails(tmp_path, monkeypatch):
    from hypoloom import end_run_tui as form
    root, snapshot = run_snapshot(tmp_path)
    calls = []

    def send(path, request, *, timeout):
        calls.append(request)
        raise TimeoutError("Host reply lost")

    class BusyStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def readonly_connection(self, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(form, "request_over_socket", send)
    monkeypatch.setattr(form, "StateStore", BusyStore)

    async def exercise():
        app = app_for(root, snapshot)
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=(90, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.click("#end-run-confirm")
            await pilot.pause()
            assert len(calls) == 1
            assert "not confirmed" in str(app.screen.query_one("#end-run-status", Static).render())
            assert str(app.screen.query_one("#end-run-confirm", Button).label) == "Retry same request"
            assert not app.screen.query_one("#end-run-confirm", Button).disabled
            assert app._end_run_draft.get("pending") is not None

    asyncio.run(exercise())


@pytest.mark.parametrize("state", ["STOPPING", "COMPLETED", "FAILED", "CANCELLED"])
def test_stale_or_finishing_run_cannot_submit_cancel(tmp_path, monkeypatch, state):
    from hypoloom import end_run_tui as form
    root, snapshot = run_snapshot(tmp_path)
    monkeypatch.setattr(form, "request_over_socket", lambda *a, **kw: pytest.fail("unexpected cancellation"))

    async def exercise():
        app = app_for(root, snapshot)
        async with app.run_test(size=(80, 32)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            changed = replace(snapshot, status={**snapshot.status,
                "run_revision": snapshot.status["run_revision"] + 1})
            app._render(changed)
            assert app.screen.query_one("#end-run-confirm", Button).disabled
            assert "Close and reopen" in str(app.screen.query_one("#end-run-status", Static).render())
            await pilot.click("#end-run-confirm")
            await pilot.press("escape")
            await pilot.press("x")
            assert not app.screen.query_one("#end-run-confirm", Button).disabled
            app._render(replace(changed, status={**changed.status, "run_state": state}))
            assert app.screen.query_one("#end-run-confirm", Button).disabled
            await pilot.press("escape")
            await pilot.press("x")
            assert app.screen.query_one("#end-run-confirm", Button).disabled

    asyncio.run(exercise())


def test_revision_conflict_requires_review_before_new_cancel(tmp_path, monkeypatch):
    from hypoloom import end_run_tui as form
    root, snapshot = run_snapshot(tmp_path)
    calls = []

    def send(path, request, *, timeout):
        calls.append(request)
        return ControlResponse.failure(request.request_id, "conflict", "revision_conflict",
            "Run revision changed", revision=request.expected_revision + 1)

    monkeypatch.setattr(form, "request_over_socket", send)

    async def exercise():
        app = app_for(root, snapshot)
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=(90, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.click("#end-run-confirm")
            await pilot.pause()
            assert len(calls) == 1 and app.screen.query_one("#end-run-confirm", Button).disabled
            app.screen.update_status({**snapshot.status, "run_revision": snapshot.status["run_revision"] + 1})
            message = str(app.screen.query_one("#end-run-status", Static).render())
            assert "Cancellation not accepted: Run revision changed" in message
            assert "The run changed" not in message
            await pilot.click("#end-run-confirm")
            assert len(calls) == 1
            await pilot.press("escape")
            app._render(replace(snapshot, status={**snapshot.status,
                "run_revision": snapshot.status["run_revision"] + 1}))
            await pilot.press("x")
            await pilot.click("#end-run-confirm")
            await pilot.pause()
            assert len(calls) == 2
            assert calls[0].request_id != calls[1].request_id
            assert calls[1].expected_revision == calls[0].expected_revision + 1

    asyncio.run(exercise())
