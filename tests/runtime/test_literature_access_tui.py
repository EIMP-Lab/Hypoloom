"""The institutional monitor observes existing Host state without controls."""
import asyncio
from copy import deepcopy
from io import StringIO

import pytest
from rich.console import Console
from textual.app import App
from textual.widgets import Button, Input, Select

from hypoloom.literature_access_tui import (
    LiteratureAccessScreen, access_summary, connection_summary, publisher_table,
)
from tests.runtime.test_paper_reading import reading_host as reading_host


def observation():
    return {"run_state": "ACTIVE", "literature_access": {"enabled": True,
        "connection": {"mode": "client", "state": "client_socket_present"}, "groups": [
            {"provider": "IEEE", "mode": "auto", "papers": [
                {"state": "available", "remaining_seconds": None, "last_checked_at": "2026-10-04T08:00:00+00:00"},
                {"state": "checking", "remaining_seconds": 5, "last_checked_at": "2026-10-04T08:00:01+00:00"},
                {"state": "identity_unconfirmed", "remaining_seconds": None,
                 "last_checked_at": "2026-10-04T08:00:02+00:00", "reason_code": "document_validation_failed"}]},
            {"provider": "ScienceDirect", "mode": "public_only", "papers": [
                {"state": "verification_required", "remaining_seconds": None,
                 "last_checked_at": "2026-10-04T08:00:03+00:00"}]},
            {"provider": "Springer", "mode": "auto", "papers": [], "preparation": {
                "state": "sample_verified", "last_checked_at": "2026-10-04T08:00:04+00:00"}},
            {"provider": "ACM", "mode": "public_only", "papers": []},
        ]}}


def plain(renderable, width=110):
    output = StringIO()
    Console(file=output, width=width, color_system=None).print(renderable)
    return output.getvalue()


def test_summary_separates_configured_paths_current_work_and_sample_readability():
    status = observation(); original = deepcopy(status)
    text = plain(publisher_table(status))
    assert '2 institutional · 2 public only · 0 disabled' in access_summary(status)
    assert 'browser availability unconfirmed' in connection_summary(status)
    assert 'IEEE' in text and '1 / 1 / 1' in text and 'Document validation failed' in text
    assert 'Human verification required' in text and '0 / 0 / 1' in text
    assert 'Sample readable' in text and 'No institutional requests' in text
    assert 'Login succeeded' not in text and 'PDF' not in text
    assert status == original


def test_empty_cache_does_not_erase_logged_verification():
    status = observation()
    status['literature_access']['groups'][0]['papers'] = []
    status['literature_access']['history'] = {'state': 'observed', 'document_verified': {'IEEE': 4}}
    text = plain(publisher_table(status), width=130)
    assert '0 / 0 / 0' in text and '4 verified in log' in text
    assert 'no verification in log' in text and 'PDF' not in text


@pytest.mark.parametrize('access,expected', [(None, 'status unknown'), ({}, 'status unknown'),
    ({'enabled': False, 'groups': []}, 'Institutional browser disabled')])
def test_missing_host_observation_is_not_disabled_or_connected(access, expected):
    status = {'literature_access': access}
    assert expected in access_summary(status)
    assert connection_summary(status) == '' and not publisher_table(status).rows


@pytest.mark.parametrize('state,expected', [('waiting_for_client', 'Waiting for client'),
    ('allocated', 'unconfirmed'), ('unavailable', 'unavailable'), ('inactive', 'inactive')])
def test_connection_observation_does_not_assert_browser_or_subscription_health(state, expected):
    status = observation()
    status['literature_access']['connection']['state'] = state
    assert expected in connection_summary(status)
    status['run_state'] = 'COMPLETED'
    assert 'Run ended' in connection_summary(status)


@pytest.mark.parametrize('size', [(80, 24), (100, 34), (237, 52)])
def test_readonly_panel_refresh_and_both_return_actions(tmp_path, monkeypatch, size):
    def forbidden(*args, **kwargs):
        pytest.fail('read-only panel sent a control request or probed a browser')
    monkeypatch.setattr('hypoloom.control_protocol.request_over_socket', forbidden)
    monkeypatch.setattr('hypoloom.institutional_browser.PlaywrightInstitutionalBrowser.check_connection', forbidden)
    status = observation()
    status['literature_access']['groups'].extend(
        {'provider': name, 'mode': 'disabled', 'papers': []}
        for name in ['Nature', 'Wiley', 'SAGE', 'Oxford', 'Taylor & Francis'])
    async def exercise():
        app = App()
        async with app.run_test(size=size) as pilot:
            app.push_screen(LiteratureAccessScreen(status))
            await pilot.pause()
            screen = app.screen
            assert not list(screen.query(Input)) and not list(screen.query(Select))
            assert [b.id for b in screen.query(Button)] == ['literature-close']
            await pilot.press('l', 'r')  # Former login/retry keys perform no operation.
            screen.update_status({'run_state': 'ACTIVE'})
            await pilot.pause()
            assert 'status unknown' in str(screen.query_one('#literature-summary').content)
            assert not screen.query_one('#literature-publishers').display
            screen.update_status(status)
            await pilot.pause()
            assert 'Human verification required' in plain(screen.query_one('#literature-publishers').content)
            app.save_screenshot(f'institutional-status-{size[0]}.svg', path=tmp_path)
            scroll = screen.query_one('#literature-access-scroll')
            scroll.scroll_end(animate=False)
            await pilot.pause()
            if size == (80, 24):
                assert scroll.scroll_y > 0
            app.save_screenshot(f'institutional-status-{size[0]}-bottom.svg', path=tmp_path)
            button = screen.query_one('#literature-close')
            assert 0 <= button.region.y and button.region.bottom <= size[1]
            assert await pilot.click('#literature-close')
            assert not isinstance(app.screen, LiteratureAccessScreen)
            app.push_screen(LiteratureAccessScreen(status))
            await pilot.pause()
            await pilot.press('escape')
            assert not isinstance(app.screen, LiteratureAccessScreen)
    asyncio.run(exercise())


def test_panel_uses_real_host_modes_and_results_without_writes_or_browser_actions(reading_host):
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.institutional_access import InstitutionalAccess, scope_for
    from hypoloom.institutional_controls import bind
    from tests.runtime.test_institutional_access import Backend, URL, pump
    from tests.runtime.test_monitor_live_status import _row_census
    host, parent, *_ = reading_host
    backend = Backend(); backend.state = 'available'
    access = InstitutionalAccess(backend, provider_modes={'Nature': 'auto', 'ScienceDirect': 'public_only', 'ACM': 'public_only'})
    host.institutional_access = access
    bind(host)
    access.request(scope_for(parent), URL)
    pump(access, lambda: access.status(parent.run_id)['available'] == 1, runs=(parent.run_id,))
    before = _row_census(host.store.database_path)
    calls = list(backend.calls)
    def status():
        response = host.handle(ControlRequest(1, 'readonly-panel', None, 'STATUS', {'run_id': parent.run_id}))
        assert response.status == 'accepted'
        return response.result
    async def exercise():
        app = App()
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(LiteratureAccessScreen(status()))
            await pilot.pause()
            screen = app.screen
            assert '2 public only' in str(screen.query_one('#literature-summary').content)
            rendered = plain(screen.query_one('#literature-publishers').content)
            assert 'Nature' in rendered and '0 / 1 / 0' in rendered and 'Full text ready' in rendered
            screen.update_status(status())
            await pilot.press('l', 'r')
            await pilot.click('#literature-close')
    asyncio.run(exercise())
    assert _row_census(host.store.database_path) == before
    assert backend.calls == calls
    assert not host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='LITERATURE_ACCESS'")
