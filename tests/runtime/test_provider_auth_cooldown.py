"""Provider auth rejection: durable dispatch cooldown, operator clear, alerts."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

import pytest

from hypoloom.control_protocol import ControlRequest, ControlResponse
from hypoloom.domain import AttemptTerminalReason
from hypoloom.observation_api import product_events
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_batch_host import batch_host
from tests.runtime.test_host_experiment_tools import _until
from tests.runtime.test_web_observer import _create_fresh_run


def _auth_failure(host, request):
    host._agent_failure(request, AttemptTerminalReason.FAILED, "nonzero_exit", retryable=True,
        details={"diagnostics": {"provider_failure": {"code": "auth_failed", "http_status": 403}}})


def test_auth_cooldown_blocks_dispatch_until_operator_clear(batch_host, monkeypatch):
    host = batch_host
    accept = host._accept_agent_response
    requests = []

    def rejected(request, response, **kwargs):
        requests.append(request)
        _auth_failure(host, request)

    monkeypatch.setattr(host, "_accept_agent_response", rejected)
    _until(host, lambda: bool(requests))
    before = len(host.store.list_attempts())
    for _ in range(3):
        host.tick()
    assert len(host.store.list_attempts()) == before
    status = host.handle(ControlRequest(1, "status-1", None, "STATUS", {"run_id": "batch-host"}))
    gate = status.result["provider_retry"]
    assert gate["pending"] and gate["cleared_at"] is None
    assert gate["gates"][-1]["class"] == "auth_failed" and gate["gates"][-1]["http_status"] == 403
    # The gate is derived from durable rows; a reopened Host computes it identically.
    reopened = WorkflowHost(host.state_root)
    assert reopened._provider_retry_pending("batch-host")
    reopened.store.close()
    # Operator confirms credential recovery: dispatch resumes on the next tick.
    cleared = host.handle(ControlRequest(1, "clear-1", None, "PROVIDER_RETRY_CLEAR",
        {"run_id": "batch-host", "reason": "operator"}))
    assert cleared.status == "accepted"
    assert cleared.result["provider_retry"]["pending"] is False
    assert not host._provider_retry_pending("batch-host")
    monkeypatch.setattr(host, "_accept_agent_response", accept)
    _until(host, lambda: len(host.store.list_attempts()) > before)
    # A provider that keeps failing after the clear re-engages the gate.
    monkeypatch.setattr(host, "_accept_agent_response", rejected)
    _until(host, lambda: len(requests) > 1)
    assert host._provider_retry_pending("batch-host")
    # Unattended recovery: the configured cooldown elapses and dispatch resumes.
    expired = time.time() + 18001
    monkeypatch.setattr("hypoloom.workflow_host.time.time", lambda: expired)
    assert not host._provider_retry_pending("batch-host")


def test_clear_requires_operator_reason_and_a_live_run(batch_host):
    host = batch_host
    bad = host.handle(ControlRequest(1, "clear-bad", None, "PROVIDER_RETRY_CLEAR",
        {"run_id": "batch-host", "reason": "model"}))
    assert bad.status == "rejected" and bad.error["code"] == "invalid_request"
    missing = host.handle(ControlRequest(1, "clear-missing", None, "PROVIDER_RETRY_CLEAR",
        {"run_id": "missing", "reason": "operator"}))
    assert missing.status == "rejected" and "unknown run" in missing.error["message"]


def _auth_attempt(attempt_id, ended_at):
    return {"attempt_id": attempt_id, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "FAILED",
        "started_at": ended_at - 5.0, "ended_at": ended_at, "experiment_id": None,
        "observation_json": json.dumps({"diagnostics": {"provider_failure": {"code": "auth_failed", "http_status": 403}}})}


def _clear_transition(command_id, revision, cleared_at):
    return {"workflow_kind": "RUN", "workflow_id": "run", "command_id": command_id, "revision": revision,
        "command_type": "PROVIDER_RETRY_CLEAR", "state": "ACTIVE", "previous_state": "ACTIVE",
        "created_at": cleared_at,
        "payload": {"provider_retry_clear": {"reason": "operator", "cleared_at": cleared_at}}}


def test_auth_cooldown_events_dedup_episode_and_name_operator_clear():
    facts = [{"fact_id": "f-goal", "fact_type": "GoalDefined", "created_at": 1.0,
        "payload": {"research_contract": {"budget": {}}}}]
    attempts = [_auth_attempt("a1", 100.0), _auth_attempt("a2", 200.0),
        _auth_attempt("a3", 300.0), _auth_attempt("a4", 100.0 + 18000.0 + 50.0)]
    events = product_events("run", facts, [], attempts)
    waiting = [event for event in events if event["type"] == "provider_waiting"]
    assert [event["source_id"] for event in waiting] == ["a1", "a3"]
    assert waiting[0]["payload"]["next_retry_at"] == 18100.0
    assert waiting[-1]["payload"]["requires_operator"] is True
    transitions = [_clear_transition("clear-1", 9, 150.0)]
    events = product_events("run", facts, transitions, attempts[:2])
    assert [event["type"] for event in events if event["type"].startswith("provider_")] == [
        "provider_waiting", "provider_retry_requested", "provider_waiting"]
    # A clear is a retry request, never proof that access recovered.
    assert not any(event["type"] == "provider_recovered" for event in events)


def test_telegram_auth_cooldown_message_guides_operator_recovery(tmp_path, monkeypatch):
    from hypoloom import notification_tailer as notifier
    event = {"event_id": "provider-auth:a1:1", "source": "provider-auth", "source_id": "a1",
        "revision": 1, "type": "provider_auth_cooldown", "run_id": "notifications",
        "created_at": 100.0, "payload": {"attempt_id": "a1", "http_status": 403,
            "cooldown_seconds": 18000.0, "cooldown_until": 100.0 + 18000.0}}
    resumed = {"event_id": "provider-auth-clear:c1:9", "source": "provider-auth-clear", "source_id": "c1",
        "revision": 9, "type": "provider_auth_resumed", "run_id": "notifications",
        "created_at": 150.0, "payload": {"cleared_at": 150.0, "command_id": "c1"}}
    config = {"sinks": {"telegram": {"enabled": True, "bot_token_env": "TEST_BOT", "chat_id_env": "TEST_CHAT"}},
        "events": {"telegram": ["provider_auth_cooldown", "provider_auth_resumed"]}}
    monkeypatch.setattr(notifier, "load_notification_config", lambda *_: config)
    monkeypatch.setattr(notifier, "build_product_observation", lambda *_: {"run_id": "notifications", "events": [event, resumed]})
    sent = []

    def transport(url, document, *, headers, timeout, **kwargs):
        sent.append(document["text"])
        return notifier.HTTPResponse(200, b'{"ok": true}')

    tailer = notifier.NotificationTailer(tmp_path / "state", "notifications", tmp_path / "out",
        environ={"TEST_BOT": "123456:ABCdef", "TEST_CHAT": "42"}, transport=transport)
    result = tailer.poll_once()
    assert not result.errors and result.delivered == {"telegram": 2}
    assert "HTTP 403" in sent[0] and "provider-retry-clear" in sent[0] and "Next recovery attempt" in sent[0]
    assert "not yet confirmed" in sent[1]


def _resume_snapshot(tmp_path):
    root = tmp_path / "state"
    _create_fresh_run(root, "resume-ui")
    from hypoloom.monitor import build_monitor_snapshot
    snapshot = build_monitor_snapshot(root, "resume-ui")
    gate = {"pending": True, "cleared_at": None, "cooldown_seconds": 18000.0,
        "gates": [{"class": "auth_failed", "http_status": 403, "attempt_id": "a1",
            "attempt_ended_at": time.time() - 10, "until": time.time() + 18000, "pending": True}]}
    return root, replace(snapshot, status={**snapshot.status, "provider_retry": gate})


@pytest.mark.parametrize("switch", [False, True])
def test_tui_banner_and_confirm_send_exact_clear_request(tmp_path, monkeypatch, switch):
    from hypoloom import provider_resume_tui as form
    from hypoloom.tui import HypoloomVNextMonitor, SummaryPanel
    from textual.widgets import Button, Input, Static
    root, snapshot = _resume_snapshot(tmp_path)
    calls = []

    def send(path, request, *, timeout):
        calls.append(request)
        assert path == root / "control.sock" and timeout == 5
        assert request.command == "PROVIDER_RETRY_CLEAR"
        assert request.expected_revision is None
        assert request.payload == {"run_id": "resume-ui", "reason": "operator",
            **({"settings_path": "/operator/connection.json"} if switch else {})}
        return ControlResponse.accepted(request.request_id, 2,
            {"run_id": "resume-ui", "run_state": "CREATED", "provider_retry": {"pending": False}})

    monkeypatch.setattr(form, "request_over_socket", send)

    async def exercise():
        app = HypoloomVNextMonitor(root, "resume-ui", initial_snapshot=snapshot,
            interval=3600, web_enabled=False)
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            summary = app.query_one("#summary", SummaryPanel)._rendered_content or ""
            assert "API COOLDOWN" in summary and "dispatch paused" in summary
            await pilot.press("p")
            screen = app.screen
            assert isinstance(screen, form.ProviderResumeScreen)
            assert app.focused.id == "provider-resume-close"
            await pilot.press("enter")  # Default action leaves the cooldown alone.
            assert not isinstance(app.screen, form.ProviderResumeScreen) and not calls
            await pilot.press("p")
            if switch: app.screen.query_one("#provider-settings-path", Input).value = "/operator/connection.json"
            await pilot.click("#provider-switch" if switch else "#provider-resume-confirm")
            await pilot.pause()
            assert len(calls) == 1
            assert app.screen.query_one("#provider-resume-confirm", Button).disabled
            assert "Recovery requested" in str(app.screen.query_one("#provider-resume-status", Static).render())

    asyncio.run(exercise())


def test_tui_resume_requires_a_live_host_gate_view(tmp_path, monkeypatch):
    from hypoloom.tui import HypoloomVNextMonitor
    from hypoloom.monitor import build_monitor_snapshot
    root = tmp_path / "state"
    _create_fresh_run(root, "resume-ui")
    snapshot = build_monitor_snapshot(root, "resume-ui")  # Offline: no provider_retry key.
    notifications = []

    async def exercise():
        app = HypoloomVNextMonitor(root, "resume-ui", initial_snapshot=snapshot,
            interval=3600, web_enabled=False)
        monkeypatch.setattr(app, "notify", lambda *args, **kwargs: notifications.append(args))
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert "provider_resume_tui" not in str(type(app.screen))
            assert notifications and "live Host" in notifications[0][0]

    asyncio.run(exercise())


def test_tui_gpu_recheck_uses_distinct_command_and_keeps_uncertain_request(tmp_path, monkeypatch):
    from dataclasses import replace
    from hypoloom.tui import HypoloomVNextMonitor
    import hypoloom.resource_recheck_tui as form
    root, snapshot = _resume_snapshot(tmp_path)
    snapshot = replace(snapshot, status={**snapshot.status, "run_state": "ACTIVE", "resource_blocked": [{"role": "expert", "code": "incompatible_pool"}]})
    calls = []
    def send(path, request, **kwargs):
        calls.append(request)
        if len(calls) == 1: raise ConnectionError("lost reply")
        return ControlResponse.accepted(request.request_id, 2, {})
    monkeypatch.setattr(form, "request_over_socket", send)
    async def exercise():
        app = HypoloomVNextMonitor(root, "resume-ui", initial_snapshot=snapshot, interval=3600, web_enabled=False)
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(); await pilot.press("g")
            assert isinstance(app.screen, form.ResourceRecheckScreen)
            await pilot.click("#resource-confirm"); await pilot.pause(0.6)
            await pilot.click("#resource-confirm"); await pilot.pause(0.6)
            assert len(calls) == 2 and calls[0] == calls[1]
            assert calls[0].command == "RESOURCE_RECHECK"
            assert calls[0].payload == {"run_id": "resume-ui", "reason": "operator"}
    asyncio.run(exercise())
