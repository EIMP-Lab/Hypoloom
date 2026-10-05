"""Language is local presentation state, never a research control or data rewrite."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from string import Formatter

import pytest
from textual.widgets import Button, Select, Static, TextArea

from hypoloom.monitor import build_monitor_snapshot
from hypoloom.tui import HypoloomVNextMonitor, SnapshotRefreshCompleted
from hypoloom.ui_i18n import messages, tr, translate
from tests.runtime.test_literature_access_tui import observation
from tests.runtime.test_observer_browser_ui import (
    browser as browser,  # noqa: PLC0414 -- pytest fixture
)
from tests.runtime.test_web_observer import _create_fresh_run


def test_translation_preserves_fields_and_never_translates_values():
    formatter = Formatter()
    for source, translated in messages().items():
        fields = lambda text: sorted(key for _, key, _, _ in formatter.parse(text) if key is not None)
        assert fields(source) == fields(translated), source
    value = 'Help {0} [red] <script>研究原文</script>'
    assert translate('Goal: {0}', value, language='zh-CN') == '目标：' + value
    assert translate('Goal: {0}', value, language='fr') == 'Goal: ' + value
    assert tr('Help') == 'Help'


@pytest.mark.parametrize('size', [(50, 16), (80, 24), (120, 40), (237, 52)])
def test_tui_switch_preserves_observation_filter_staleness_and_modal_draft(tmp_path, monkeypatch, size):
    root = tmp_path / 'run'
    _create_fresh_run(root, 'language-test')
    snapshot = build_monitor_snapshot(root, 'language-test')
    status = {**snapshot.status, **observation(), 'run_state': 'WAITING_HUMAN',
        'grounding_dialogue': [{'decision_id': 'decision-language', 'ordinal': 1, 'state': 'OPEN',
          'question': 'Help is a research term; 不翻译研究内容', 'rationale': 'Original rationale',
          'options': ['Keep original'], 'answer': None, 'resolution': None}]}
    snapshot = replace(snapshot, status=status)
    original = snapshot.to_json()
    def forbidden(*args, **kwargs):
        pytest.fail('Language selection must not send controls or request a fresh snapshot')
    app = HypoloomVNextMonitor(root, 'language-test', initial_snapshot=snapshot,
                              interval=3600, web_enabled=False)
    monkeypatch.setattr(app, '_refresh_snapshot', forbidden)
    monkeypatch.setattr('hypoloom.control_protocol.request_over_socket', forbidden)

    async def exercise():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert app.ui_language == 'en'
            selector = app.query_one('#expert-filter', Select)
            if app._expert_roles:
                selector.value = app._expert_roles[0]
            selected = selector.value
            for _ in range(2):
                await pilot.click('#language-button')
                await pilot.pause()
                assert app.ui_language == 'zh-CN'
                assert str(app.query_one('#help-button', Button).label) == '帮助'
                assert '运行概览' in str(app.query_one('#summary .panel-content', Static).render())
                assert selector.value == selected
                for name in ('language-button', 'help-button', 'support-help-button', 'end-run-button'):
                    button = app.query_one('#' + name, Button)
                    assert button.region.right <= size[0]
                    assert app.query_one('#observer-bar').content_region.contains_region(button.region)
                    assert any(str(button.label) in button.render_line(y).text for y in range(button.size.height))
                assert '退出监视器' in app.screen.active_bindings['q'].binding.description
                assert '所有专家' in str(selector._options[0][0])
                await pilot.click('#language-button')
                await pilot.pause()
                assert 'RUN AT A GLANCE' in str(app.query_one('#summary .panel-content', Static).render())
            await pilot.click('#language-button')
            await pilot.press('i')
            await pilot.pause()
            assert str(app.screen.query_one('#literature-close', Button).label) == '关闭'
            assert '出版社：' in str(app.screen.query_one('#literature-summary', Static).render())
            await pilot.press('escape')
            await pilot.press('h')
            await pilot.pause()
            app.screen.query_one('#human-answer', TextArea).load_text('Keep original draft 原文')
            await pilot.press('escape')
            await pilot.click('#language-button')
            await pilot.press('h')
            await pilot.pause()
            assert app.screen.query_one('#human-answer', TextArea).text == 'Keep original draft 原文'
            assert str(app.screen.query_one('#human-submit', Button).label) == 'Review answer'
            await pilot.press('escape')
            app.on_snapshot_refresh_completed(SnapshotRefreshCompleted(None, OSError('offline')))
            await pilot.click('#language-button')
            await pilot.pause()
            banner = app.query_one('#freshness', Static)
            assert banner.has_class('stale') and '已过期' in str(banner.render())
            assert snapshot.to_json() == original
    asyncio.run(exercise())
    assert tr('Help') == 'Help'  # A closed Chinese app cannot change another viewer.


def test_browser_switch_preserves_filters_research_and_no_operator_submission(browser):
    page = browser
    page.wait("!document.querySelector('#language-toggle').disabled")
    assert page.evaluate('document.documentElement.lang') == 'en'
    original = page.evaluate('JSON.stringify(fixture.snapshot)')
    page.evaluate("document.querySelector('#live-log-text').textContent = 'Help <script>研究日志原文</script>'")
    page.evaluate("document.querySelector('#filter-search').value='expert-a'; document.querySelector('#filter-search').dispatchEvent(new Event('input'))")
    page.click('#language-toggle')
    page.wait("document.documentElement.lang === 'zh-CN'")
    assert page.text('h1') == '研究观察器'
    assert page.text('#tab-map') == '研究图谱'
    assert page.text('#open-support') == '报告问题'
    assert page.evaluate("document.querySelector('#filter-search').value") == 'expert-a'
    assert page.evaluate('JSON.stringify(fixture.snapshot)') == original
    assert page.text('#live-log-text') == 'Help <script>研究日志原文</script>'
    page.click('#open-help')
    assert '术语与权限' in page.text('#help-dialog')
    page.click('#close-help')
    page.evaluate("window.fixtureError = true")
    page.wait("document.querySelector('#connection').dataset.state === 'stale'")
    page.click('#language-toggle')
    page.wait("document.documentElement.lang === 'en'")
    assert 'unconfirmed' in page.text('#provider-title')
    assert page.text('#tab-map') == 'Research Map'
    assert page.evaluate("window.fixtureRequests.every(request => request[0] === 'GET')")
    assert not page.errors


def test_web_language_storage_reload_failure_and_small_viewport(tmp_path):
    from pathlib import Path
    from urllib.parse import urlparse

    from tests.runtime.test_observer_browser_ui import (
        _ASSETS,
        _CHROME,
        _activity,
        _snapshot,
    )
    if not _CHROME:
        pytest.skip('Needs an installed Chromium browser')
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    requests = []
    snapshot, activity = _snapshot(), _activity()
    snapshot['overview'].update(activity['summary'])
    snapshot['status']['literature_access'] = observation()['literature_access']

    def serve(route):
        path = urlparse(route.request.url).path
        requests.append((route.request.method, path))
        if path == '/api/refresh':
            route.fulfill(json={'snapshot': snapshot, 'activity': activity})
        elif path == '/api/details':
            route.fulfill(json={'available': True, 'skills': [], 'total': 0})
        elif path == '/' or path.startswith('/assets/'):
            file = _ASSETS / ('index.html' if path == '/' else Path(path).name)
            mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json'}
            route.fulfill(body=file.read_bytes(), content_type=mime[file.suffix])
        else:
            route.fulfill(status=404)

    with sync_playwright() as engine:
        browser_instance = engine.chromium.launch(executable_path=_CHROME, args=['--no-sandbox'])
        try:
            context = browser_instance.new_context(locale='zh-CN', viewport={'width': 1280, 'height': 900})
            context.route('http://observer.test/**', serve)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://observer.test/#token=test-only')
            page.wait_for_function("!document.querySelector('#language-toggle').disabled")
            assert page.locator('html').get_attribute('lang') == 'en'
            page.locator('#language-toggle').click()
            assert page.locator('html').get_attribute('lang') == 'zh-CN'
            page.screenshot(path=str(tmp_path / 'web-chinese-wide.png'))
            page.reload()
            page.wait_for_function("document.documentElement.lang === 'zh-CN'")
            page.locator('#tab-workbench').click()
            page.locator('[data-workbench="institutional"]').click()
            assert page.locator('#workbench-title').inner_text() == '机构接入'
            assert 'IEEE' in page.locator('#workbench-list').inner_text()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#language-toggle').scroll_into_view_if_needed()
            for language in ('en', 'zh-CN'):
                page.locator('#language-toggle').click()
                assert page.locator('html').get_attribute('lang') == language
                contents = page.locator('#workbench-list').inner_text()
                assert ('Read-only list.' if language == 'en' else '只读列表。') in contents
                assert page.locator('#total-experiments .eyebrow').inner_text() == ('Experiments' if language == 'en' else '实验')
                box = page.locator('#language-toggle').bounding_box()
                assert 0 <= box['x'] and box['x'] + box['width'] <= 390
            page.screenshot(path=str(tmp_path / 'web-chinese-narrow.png'))
            assert not errors
            context.close()

            denied = browser_instance.new_context()
            denied.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('storage denied')}})")
            denied.route('http://observer.test/**', serve)
            page = denied.new_page()
            page.goto('http://observer.test/#token=test-only')
            page.wait_for_function("!document.querySelector('#language-toggle').disabled")
            page.locator('#language-toggle').click()
            assert page.locator('html').get_attribute('lang') == 'zh-CN'
            denied.close()

            missing = browser_instance.new_context()
            missing.route('http://observer.test/**', serve)
            missing.route('**/assets/messages.json', lambda route: route.fulfill(status=404))
            page = missing.new_page()
            page.goto('http://observer.test/#token=test-only')
            page.wait_for_function("document.querySelector('#connection').dataset.state === 'live'")
            assert page.locator('#language-toggle').is_disabled()
            assert 'English remains available' in page.locator('#language-toggle').get_attribute('title')
            assert page.locator('html').get_attribute('lang') == 'en'
            missing.close()
            assert all(method == 'GET' for method, _ in requests)
        finally:
            browser_instance.close()


def test_chinese_cancel_still_requires_explicit_confirmation(tmp_path, monkeypatch):
    from hypoloom import end_run_tui
    from hypoloom.control_protocol import ControlResponse
    root = tmp_path / 'state'
    _create_fresh_run(root, 'language-cancel')
    snapshot = build_monitor_snapshot(root, 'language-cancel')
    calls = []
    def send(path, request, *, timeout):
        calls.append(request)
        return ControlResponse.accepted(request.request_id, request.expected_revision + 1,
                                        {'run_id': 'language-cancel', 'run_state': 'STOPPING'})
    monkeypatch.setattr(end_run_tui, 'request_over_socket', send)
    app = HypoloomVNextMonitor(root, 'language-cancel', initial_snapshot=snapshot,
                              interval=3600, web_enabled=False)
    monkeypatch.setattr(app, 'action_refresh', lambda: None)
    async def exercise():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click('#language-button')
            await pilot.press('x')
            await pilot.pause()
            assert not calls
            assert app.focused.id == 'end-run-close'
            assert str(app.screen.query_one('#end-run-confirm', Button).label) == '结束本次运行'
            await pilot.press('enter')
            assert not calls
            await pilot.press('x')
            await pilot.click('#end-run-confirm')
            await pilot.pause()
            assert len(calls) == 1 and calls[0].command == 'CANCEL'
            assert calls[0].payload == {'run_id': 'language-cancel', 'cause': 'operator_request_from_tui'}
    asyncio.run(exercise())
