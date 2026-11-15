"""Real navigation and activity presentation; no research controls or provider calls."""
import asyncio
from dataclasses import replace
import time

import pytest
from textual.widgets import Button, Static

from hypoloom.tui import ExpertsPanel, VerificationScreen, _activity_light, _agent_models
from test_end_run_ui import app_for, run_snapshot
from test_monitor import _static_text
from test_observer_browser_ui import browser as browser


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_verifier_history_opens_pages_and_returns_to_end_run_dialog(tmp_path, monkeypatch, language):
    root, snapshot = run_snapshot(tmp_path)
    offsets = []

    def detail(root, run, kind, *, offset, limit):
        assert kind == 'verification' and limit == 20
        offsets.append(offset)
        return {'state': 'idle', 'model': 'review-model', 'experts': [{
            'expert': 'author', 'deliveries': 21, 'blockers': 0,
            'recent': [{'revisions': []}] * (20 if offset == 0 else 1)}]}

    monkeypatch.setattr('hypoloom.observer_details.read_observer_detail', detail)

    async def exercise():
        app = app_for(root, snapshot)
        app.ui_language = language
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.press('v')
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, VerificationScreen)
            assert dialog.page_offset == 0 and dialog.offset.x == 0
            assert dialog.query_one('#verification-previous', Button).disabled
            await pilot.click('#verification-next')
            await pilot.pause()
            assert dialog.page_offset == 20
            assert dialog.query_one('#verification-next', Button).disabled
            await pilot.click('#verification-previous')
            await pilot.pause()
            assert dialog.page_offset == 0
            await pilot.press('escape')
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press('x')
            await pilot.pause()
            assert app.screen.query_one('#end-run-confirm', Button)
            await pilot.press('escape')
            assert not app._end_run_draft.get('pending')

    asyncio.run(exercise())
    assert offsets == [0, 20, 0]


def test_activity_light_animates_only_confirmed_requests_and_keeps_unknown_static(monkeypatch):
    import hypoloom.tui as tui
    outputs = []
    for now in (0, .8):
        monkeypatch.setattr(tui.time, 'monotonic', lambda: now)
        outputs.append([_activity_light(counts, active=active, confirmed=confirmed, color='#b695da', animate=animate)
            for counts, active, confirmed, animate in [({'rpm': 4}, True, True, True),
                ({}, True, True, True), ({'rpm': 4}, True, False, True),
                ({'rpm': 4}, False, True, True), ({'rpm': 4}, True, True, False)]])
    assert outputs[0][0] != outputs[1][0]
    assert outputs[0][1:] == outputs[1][1:]
    assert '#ffb86b' in outputs[0][2]
    assert _agent_models({'reliability': {'groups': [{'model': 'b'}, {'model': 'a'}, {'model': 'b'}, {'model': 'unknown'}]}}) == 'a / b'
    assert _agent_models({}) == '—'


def test_tui_card_animation_preserves_scroll_and_recorded_model_text(tmp_path, monkeypatch):
    root, initial = run_snapshot(tmp_path)
    model = 'mimo-v2.6-flash[1m]'
    group = {'reliability': {'groups': [{'model': model}]}, 'state': 'active', 'activity': [], 'accounting': {}}
    overview = {**initial.overview, 'host_observation': {'availability': 'responded'},
        'grounding': group, 'verifier': {**group, 'model': 'mimo-v2.6-pro',
            'reliability': {'groups': [{'model': 'mimo-v2.6-pro'}]}, 'experts': []}}
    traffic = {'scope': 'run', 'observed_at': time.time(), 'grounding': {'rpm': 4}, 'verifier': {'rpm': 4}}
    snapshot = replace(initial, overview=overview, status={**initial.status, 'provider_traffic': traffic})
    before = snapshot.to_json()

    async def exercise():
        app = app_for(root, snapshot)
        monkeypatch.setattr(app, '_refresh_snapshot', lambda: pytest.fail('animation must not poll'))
        async with app.run_test(size=(100, 30)) as pilot:
            panel = app.query_one(ExpertsPanel)
            await pilot.pause()
            panel.scroll_end(animate=False)
            await pilot.pause()
            y, content = panel.scroll_y, panel._rendered_content
            await pilot.pause(.4)
            assert panel.scroll_y == y and panel._rendered_content != content
            text = _static_text(panel.query_one(Static))
            assert model in text and 'mimo-v2.6-pro' in text and 'Verifier' in text
            app.save_screenshot('verifier-tui.svg', path=tmp_path)
            expired = replace(snapshot, status={**snapshot.status, 'provider_traffic': {**traffic, 'observed_at': time.time() - 60}})
            app._render(expired)
            assert not panel._pulse_active
            assert snapshot.to_json() == before

    asyncio.run(exercise())


def test_browser_verifier_identity_models_and_pulse_freshness(browser, tmp_path):
    page = browser
    page.evaluate("""fixture.snapshot.status.run_state='ACTIVE';
      fixture.snapshot.overview.host_observation={availability:'responded'};
      fixture.snapshot.status.provider_traffic={scope:'run',observed_at:Date.now()/1000,
        verifier:{rpm:4,in_flight:1,queued:0},grounding:{rpm:1,in_flight:1,queued:0}};
      fixture.snapshot.overview.grounding={state:'active',activity:[],reliability:{groups:[{model:'grounding-base'}]}};
      fixture.snapshot.overview.verifier={state:'active',model:'mimo-v2.6-pro',effort:'high',attempt_ids:[],experts:[]};
      fixture.snapshot.overview.experts=[{role:'expert-a',reliability:{groups:[{model:'mimo-v2.6-flash'}]}}];""")
    page.wait("document.querySelector('#verifier-pulse')?.classList.contains('rpm-active')")
    assert page.text('#verifier-agent-heading') == 'Verifier'
    assert 'mimo-v2.6-pro' in page.text('#verifier-agent-card .agent-model')
    assert 'mimo-v2.6-flash' in page.text('.expert-preview .agent-model')
    assert 'grounding-base' in page.text('#grounding-agent-card .agent-model')
    assert 'NaN' not in page.text('#verifier-agent-card')
    assert page.evaluate("document.querySelector('#verifier-agent-card .agent-identity-icon svg') !== null")
    assert page.evaluate("getComputedStyle(document.querySelector('#verifier-pulse')).animationName") == 'breath'
    page.evaluate("window.savedLight=document.querySelector('#verifier-pulse'); fixture.snapshot.overview.verifier.effort='max'")
    page.wait("document.querySelector('#verifier-agent-card').textContent.includes('max')")
    assert page.evaluate("window.savedLight===document.querySelector('#verifier-pulse')")
    page.evaluate("document.querySelector('#verifier-agent-panel').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / 'verifier-web.png')
    page.call('Emulation.setEmulatedMedia', {'features': [{'name': 'prefers-reduced-motion', 'value': 'reduce'}]})
    assert page.evaluate("getComputedStyle(document.querySelector('#verifier-pulse')).animationName") == 'none'
    page.call('Emulation.setEmulatedMedia', {'features': []})
    page.evaluate("fixture.snapshot.status.provider_traffic.observed_at-=90")
    page.wait("!document.querySelector('#verifier-pulse').classList.contains('rpm-active')")
    assert page.text('#verifier-rpm') == '—'
    page.evaluate("fixture.snapshot.overview.host_observation={availability:'absent'}")
    page.wait("document.querySelector('#verifier-state').textContent==='OPEN?'")
    assert page.evaluate("document.querySelector('#verifier-pulse').classList.contains('host-unconfirmed')")
    assert page.evaluate("getComputedStyle(document.querySelector('#verifier-pulse')).animationName") == 'none'
    page.evaluate("fixture.snapshot.overview.verifier.model='<script>window.modelInjected=1</script>'")
    page.wait("document.querySelector('#verifier-agent-card .agent-model').textContent.includes('<script>')")
    assert page.evaluate('window.modelInjected') is None
    page.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
    page.evaluate("document.querySelector('#verifier-agent-panel').scrollIntoView({block:'center'})")
    page.wait("document.querySelector('#verifier-agent-card').getBoundingClientRect().right <= innerWidth")
    page.screenshot(tmp_path / 'verifier-web-mobile.png')
