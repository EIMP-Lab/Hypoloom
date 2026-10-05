"""Literature availability remains a read-only view of the Host recovery state."""
import asyncio
import json
from dataclasses import replace

import pytest
from textual.widgets import Static

from hypoloom.monitor import build_monitor_snapshot
from hypoloom.tui import HypoloomVNextMonitor, SnapshotRefreshCompleted
from tests.runtime.test_observer_browser_ui import browser as browser
from tests.runtime.test_web_observer import _create_fresh_run


def recovery(**changes):
    return {"service": "openalex", "state": "cooldown", "pending": True,
            "requires_operator": False, "reason": "openalex_timeout", "http_status": 503,
            "next_retry_at": 1791201600, "recovery_attempts": 9, "attempt_limit": 6,
            "affected_roles": ["expert-a", "expert-b"], **changes}


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_tui_literature_recovery_is_automatic_after_initial_rounds_and_translated(tmp_path, monkeypatch, language):
    root = tmp_path / "state"
    _create_fresh_run(root, "literature-ui")
    base = build_monitor_snapshot(root, "literature-ui")
    snapshot = replace(base, status={**base.status, "run_state": "ACTIVE", "literature_retry": recovery()})
    original = snapshot.to_json()
    app = HypoloomVNextMonitor(root, "literature-ui", initial_snapshot=snapshot, interval=3600, web_enabled=False)
    notices = []
    monkeypatch.setattr(app, "notify", lambda *args, **kwargs: notices.append(args))
    monkeypatch.setattr(app, "_refresh_snapshot", lambda: pytest.fail("UI must reuse its snapshot"))
    monkeypatch.setattr("hypoloom.control_protocol.request_over_socket", lambda *a, **k: pytest.fail("No control expected"))

    async def exercise():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            if language == "zh-CN":
                await pilot.click("#language-button")
                await pilot.pause()
            text = lambda: str(app.query_one("#summary .panel-content", Static).render())
            for expected in (("Automatic retry scheduled", "Last failure: Request timed out", "Next automatic retry:", "Affected roles:")
                             if language == "en" else ("已安排自动重试", "最近失败：请求超时", "下次自动重试：", "受影响角色：")):
                assert expected in text()
            assert "HTTP 503" in text() and "expert-a, expert-b" in text()
            assert "waiting_operator" not in text() and "openalex_timeout" not in text()
            app.on_snapshot_refresh_completed(SnapshotRefreshCompleted(snapshot, None))
            await pilot.pause()
            assert not notices and snapshot.to_json() == original
            auth = replace(snapshot, status={**snapshot.status, "literature_retry": recovery(
                state="waiting_operator", requires_operator=True, reason="openalex_auth", http_status=401, next_retry_at=None)})
            app.on_snapshot_refresh_completed(SnapshotRefreshCompleted(auth, None))
            await pilot.pause()
            assert ("Authentication rejected" if language == "en" else "认证被拒绝") in text()
            assert ("automatic retry is paused" if language == "en" else "自动重试已暂停") in text().lower()
            assert ("Next automatic retry:" if language == "en" else "下次自动重试：") not in text()
            pending = replace(snapshot, status={**snapshot.status, "literature_retry": recovery(state="recovering")})
            app.on_snapshot_refresh_completed(SnapshotRefreshCompleted(pending, None))
            await pilot.pause()
            assert ("An eligible task will retry automatically" if language == "en" else "符合执行条件的任务将自动重试") in text()
            assert ("Next automatic retry:" if language == "en" else "下次自动重试：") not in text()
            ready = replace(snapshot, status={**snapshot.status, "literature_retry": recovery(
                state="ready", pending=False, reason=None, http_status=None, next_retry_at=None,
                recovered_at=1791201601, affected_roles=[])})
            app.on_snapshot_refresh_completed(SnapshotRefreshCompleted(ready, None))
            await pilot.pause()
            assert ("Retrieval recovered" if language == "en" else "检索服务已恢复") in text()
            assert ("Last failure:" if language == "en" else "最近失败：") not in text()
            assert ("Affected roles:" if language == "en" else "受影响角色：") not in text()
            backup = replace(ready, status={**ready.status, "literature_retry": {
                **ready.status['literature_retry'], "active_service": "crossref", "primary_failure": "openalex_timeout"}})
            app.on_snapshot_refresh_completed(SnapshotRefreshCompleted(backup, None))
            await pilot.pause()
            assert "Crossref" in text()
            assert ("OpenAlex recovery unconfirmed" if language == "en" else "OpenAlex 尚未确认恢复") in text()
            assert ("Retrieval recovered" if language == "en" else "检索服务已恢复") not in text()
            ended = replace(snapshot, status={**snapshot.status, "run_state": "COMPLETED"})
            app.on_snapshot_refresh_completed(SnapshotRefreshCompleted(ended, None))
            await pilot.pause()
            assert ("no further automatic retry" if language == "en" else "不再自动重试") in text()
            assert ("Next automatic retry:" if language == "en" else "下次自动重试：") not in text()
            assert not notices
    asyncio.run(exercise())


def test_web_literature_status_keeps_failure_roles_and_retry_without_controls(browser):
    page = browser
    page.evaluate("fixture.snapshot.status.run_state='ACTIVE'; fixture.snapshot.status.literature_retry=" + json.dumps(recovery()))
    page.wait("document.querySelector('#literature-status').textContent.includes('Next automatic retry:')")
    assert "Automatic retry scheduled" in page.text("#literature-status")
    assert "Last failure: Request timed out · HTTP 503." in page.text("#literature-status")
    assert "Affected roles: expert-a, expert-b." in page.text("#literature-status")
    assert "exhaust" not in page.text("#literature-status")
    page.click("#language-toggle")
    page.wait("document.documentElement.lang === 'zh-CN'")
    assert "已安排自动重试" in page.text("#literature-status")
    assert "最近失败：请求超时" in page.text("#literature-status")
    assert "受影响角色：expert-a, expert-b" in page.text("#literature-status")
    page.evaluate("Object.assign(fixture.snapshot.status.literature_retry,{state:'waiting_operator',requires_operator:true,reason:'openalex_auth',http_status:401,next_retry_at:null})")
    page.wait("document.querySelector('#literature-status').textContent.includes('自动重试已暂停')")
    assert "认证被拒绝" in page.text("#literature-status")
    assert "下次自动重试" not in page.text("#literature-status")
    page.evaluate("Object.assign(fixture.snapshot.status.literature_retry,{state:'recovering',requires_operator:false})")
    page.wait("document.querySelector('#literature-status').textContent.includes('符合执行条件的任务将自动重试')")
    assert "下次自动重试" not in page.text("#literature-status")
    page.evaluate("Object.assign(fixture.snapshot.status.literature_retry,{state:'ready',reason:null,http_status:null,recovered_at:1791201601,affected_roles:[]})")
    page.wait("document.querySelector('#literature-status').textContent.includes('检索服务已恢复')")
    assert "最近失败" not in page.text("#literature-status") and "受影响角色" not in page.text("#literature-status")
    page.evaluate("Object.assign(fixture.snapshot.status.literature_retry,{active_service:'crossref',primary_failure:'openalex_timeout'})")
    page.wait("document.querySelector('#literature-status').textContent.includes('Crossref')")
    assert "OpenAlex 尚未确认恢复" in page.text("#literature-status")
    assert "检索服务已恢复" not in page.text("#literature-status")
    page.evaluate("fixture.snapshot.status.run_state='COMPLETED'")
    page.wait("document.querySelector('#literature-status').textContent.includes('不再自动重试')")
    assert "需要操作员处理" not in page.text("#literature-status")
    assert page.evaluate("window.fixtureRequests.every(request => request[0] === 'GET')")
    assert not page.errors
