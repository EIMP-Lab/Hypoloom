"""Real Host controls, restart projection, time interpretation and TUI delivery."""
import asyncio
import json
import time
from dataclasses import replace

import pytest

from hypoloom.control_protocol import ControlRequest, ControlResponse
from hypoloom.provider_recovery import parse_retry_time, scheduled_retry
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_batch_host import batch_host
from tests.runtime.test_host_experiment_tools import _until
from tests.runtime.test_provider_auth_cooldown import _auth_failure, _resume_snapshot


def control(host, identity, **extra):
    return host.handle(ControlRequest(1, identity, None, "PROVIDER_RETRY_CLEAR",
        {"run_id": "batch-host", "reason": "operator", **extra}))


def block(host, monkeypatch):
    accept = host._accept_agent_response
    monkeypatch.setattr(host, "_accept_agent_response", lambda request, response, **kw: _auth_failure(host, request))
    _until(host, lambda: host._provider_retry_pending("batch-host"))
    return accept


def clears(host):
    return host.store.inspect("SELECT command_id FROM workflow_transitions WHERE run_id='batch-host' "
        "AND json_type(payload_json,'$.provider_retry_clear')='object'")


def test_appointment_survives_reopen_and_fires_once_through_host_tick(batch_host, monkeypatch):
    host = batch_host
    accept = block(host, monkeypatch)
    now = [time.time()]
    monkeypatch.setattr("hypoloom.provider_recovery.time.time", lambda: now[0])
    first = control(host, "appointment", retry_at=now[0] + 120)
    assert first.status == "accepted", first
    assert first.result["provider_retry"]["scheduled_retry"]["retry_at"] == now[0] + 120
    assert not clears(host)
    before = len(host.store.list_attempts())
    for _ in range(3): host.tick()
    assert len(host.store.list_attempts()) == before
    reopened = WorkflowHost(host.state_root)
    try:
        assert reopened._provider_retry_status("batch-host")["scheduled_retry"] == first.result["provider_retry"]["scheduled_retry"]
    finally: reopened.store.close()
    # Lost-response replay is the same appointment, including after its deadline.
    now[0] += 121
    replay = control(host, "appointment", retry_at=first.result["provider_retry"]["next_retry_at"])
    assert replay.status == "accepted" and replay.revision == first.revision
    monkeypatch.setattr(host, "_accept_agent_response", accept)
    host.tick()
    assert scheduled_retry(host, "batch-host") is None
    assert len(clears(host)) == 1
    _until(host, lambda: len(host.store.list_attempts()) > before)
    for _ in range(4): host.tick()
    assert len(clears(host)) == 1


@pytest.mark.parametrize("action", ["cancel", "immediate", "replace", "stop"])
def test_appointment_replacement_cancel_immediate_and_stop(batch_host, monkeypatch, action):
    host = batch_host
    block(host, monkeypatch)
    now = [time.time()]
    monkeypatch.setattr("hypoloom.provider_recovery.time.time", lambda: now[0])
    assert control(host, "first", retry_at=now[0] + 60).status == "accepted"
    if action == "stop":
        response = host.handle(ControlRequest(1, "stop", None, "CANCEL", {"run_id": "batch-host", "cause": "operator"}))
    else:
        response = control(host, action, **({"retry_at": None} if action == "cancel" else
            {"retry_at": now[0] + 180} if action == "replace" else {}))
    assert response.status == "accepted", response
    now[0] += 61
    host.tick()
    if action == "replace":
        assert scheduled_retry(host, "batch-host")["retry_at"] == now[0] + 119
        assert not clears(host)
    else:
        assert scheduled_retry(host, "batch-host") is None
        assert len(clears(host)) == (1 if action == "immediate" else 0)


@pytest.mark.parametrize("value", [True, "tomorrow", 0, -1, 253402300800])
def test_invalid_appointment_has_no_effect(batch_host, value):
    result = control(batch_host, "invalid", retry_at=value)
    assert result.status == "rejected"
    assert scheduled_retry(batch_host, "batch-host") is None
    assert not clears(batch_host)


def test_recovering_api_can_be_scheduled_and_conflicting_replay_cannot_replace_it(batch_host, monkeypatch):
    host = batch_host
    block(host, monkeypatch)
    assert control(host, "try").result["provider_retry"]["state"] == "recovering"
    at = time.time() + 60
    assert control(host, "schedule", retry_at=at).status == "accepted"
    assert control(host, "schedule", retry_at=at + 30).status == "conflict"
    assert scheduled_retry(host, "batch-host")["retry_at"] == at


def test_ready_api_cannot_be_scheduled_and_expired_run_does_not_retry(batch_host, monkeypatch):
    host = batch_host
    assert control(host, "ready", retry_at=time.time() + 60).status == "rejected"
    block(host, monkeypatch)
    assert control(host, "valid", retry_at=time.time() + 60).status == "accepted"
    monkeypatch.setattr(host, "_run_wallclock_expired", lambda _: True)
    now = time.time() + 61
    monkeypatch.setattr("hypoloom.provider_recovery.time.time", lambda: now)
    from hypoloom.provider_recovery import apply_scheduled_retry
    assert not apply_scheduled_retry(host, "batch-host")
    assert not clears(host)


def test_due_failure_reengages_policy_without_repeating_appointment(batch_host, monkeypatch):
    host = batch_host
    block(host, monkeypatch)
    now = [time.time()]
    monkeypatch.setattr("hypoloom.provider_recovery.time.time", lambda: now[0])
    assert control(host, "once", retry_at=now[0] + 30).status == "accepted"
    old_count = len(host.store.list_attempts())
    now[0] += 31
    _until(host, lambda: len(host.store.list_attempts()) > old_count and host._provider_retry_status("batch-host")["state"] == "cooldown")
    gate = host._provider_retry_status("batch-host")
    assert gate["reason"] == "auth_failed" and gate["scheduled_retry"] is None
    count = len(host.store.list_attempts())
    for _ in range(3): host.tick()
    assert len(host.store.list_attempts()) == count and len(clears(host)) == 1


def test_missing_connection_at_due_time_does_not_repeat_or_bypass_validation(batch_host, monkeypatch):
    host = batch_host
    block(host, monkeypatch)
    now = [time.time()]
    monkeypatch.setattr("hypoloom.provider_recovery.time.time", lambda: now[0])
    assert control(host, "once", retry_at=now[0] + 30).status == "accepted"
    monkeypatch.setattr(host, "_provider_connection", lambda _: ({}, "connection file unavailable"))
    before = len(host.store.list_attempts())
    now[0] += 31
    for _ in range(3): host.tick()
    gate = host._provider_retry_status("batch-host")
    assert gate["reason"] == "connection_configuration" and gate["requires_operator"]
    assert gate["scheduled_retry"] is None and len(clears(host)) == 1
    assert len(host.store.list_attempts()) == before


@pytest.mark.parametrize("tz,text,expected", [
    ("Asia/Shanghai", "2026-10-10 22:30", "2026-10-10T14:30:00+00:00"),
    ("UTC", "2026-10-10 22:30+08:00", "2026-10-10T14:30:00+00:00"),
    ("America/New_York", "2026-11-01 01:30-04:00", "2026-11-01T05:30:00+00:00"),
    ("America/New_York", "2026-07-01 12:00", "2026-07-01T16:00:00+00:00"),
])
def test_time_parsing_uses_the_requested_dates_local_offset(monkeypatch, tz, text, expected):
    from datetime import datetime
    with monkeypatch.context() as env:
        env.setenv("TZ", tz); time.tzset()
        try: assert parse_retry_time(text, now=0) == datetime.fromisoformat(expected).timestamp()
        finally: env.undo(); time.tzset()


@pytest.mark.parametrize("text", ["2026-03-08 02:30", "2026-11-01 01:30"])
def test_dst_gap_and_fold_require_explicit_offset(monkeypatch, text):
    with monkeypatch.context() as env:
        env.setenv("TZ", "America/New_York"); time.tzset()
        try:
            with pytest.raises(ValueError, match="ambiguous"): parse_retry_time(text, now=0)
        finally: env.undo(); time.tzset()


@pytest.mark.parametrize("text,delay", [("30m", 1800), ("30 分钟后", 1800), ("2h", 7200), ("2 小时后", 7200)])
def test_relative_time(text, delay):
    assert parse_retry_time(text, now=100) == 100 + delay


@pytest.mark.parametrize("text", ["yesterday", "0m", "-2h", "2020-01-01 00:00", "2026-13-30 12:00"])
def test_invalid_or_past_times_are_not_silently_moved_to_tomorrow(text):
    with pytest.raises(ValueError): parse_retry_time(text, now=1791554720)


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("size", [(80, 24), (100, 48)])
def test_tui_schedule_lost_reply_replay_and_cancel(tmp_path, monkeypatch, language, size):
    from hypoloom import provider_resume_tui as form
    from hypoloom.tui import HypoloomVNextMonitor
    from textual.widgets import Input, Static
    root, snapshot = _resume_snapshot(tmp_path)
    gate = {**snapshot.status["provider_retry"], "scheduling_supported": True, "state": "cooldown"}
    snapshot = replace(snapshot, status={**snapshot.status, "provider_retry": gate})
    calls = []
    def send(path, request, **kw):
        calls.append(request)
        if len(calls) == 1: raise ConnectionError("lost reply")
        at = request.payload["retry_at"]
        return ControlResponse.accepted(request.request_id, len(calls), {"provider_retry": {
            **gate, "scheduled_retry": None if at is None else {"retry_at": at}, "next_retry_at": at}})
    monkeypatch.setattr(form, "request_over_socket", send)
    async def exercise():
        app = HypoloomVNextMonitor(root, "resume-ui", initial_snapshot=snapshot, interval=3600, web_enabled=False)
        app.ui_language = language
        monkeypatch.setattr(app, "action_refresh", lambda: None)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); await pilot.press("p")
            app.screen.query_one("#provider-retry-time", Input).value = "30 分钟后"
            app.screen.query_one("#provider-schedule-confirm").scroll_visible()
            await pilot.pause()
            await pilot.click("#provider-schedule-confirm"); await pilot.pause()
            assert len(calls) == 1 and "retry_at" in calls[0].payload
            await pilot.click("#provider-resume-confirm"); await pilot.pause()
            assert calls[0] == calls[1]
            assert app.screen.status["provider_retry"]["scheduled_retry"]
            from hypoloom.ui_i18n import format_local_time
            rendered = str(app.screen.query_one("#provider-current-status", Static).render())
            assert format_local_time(calls[1].payload["retry_at"]) in rendered
            assert ("已预约" if language == "zh-CN" else "scheduled") in rendered
            message = app.screen.query_one("#provider-resume-status", Static)
            assert message.region.bottom <= app.screen.query_one("#provider-resume-actions").region.y
            app.screen.query_one("#provider-schedule-cancel").scroll_visible()
            await pilot.pause()
            await pilot.click("#provider-schedule-cancel"); await pilot.pause()
            assert calls[-1].payload["retry_at"] is None
            assert not app.screen.status["provider_retry"]["scheduled_retry"]
    asyncio.run(exercise())
