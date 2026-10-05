"""Publisher selection is preserved from init to Host routing and observation."""
import json
import pytest

from hypoloom.bootstrap import build_parser as bootstrap_parser
from hypoloom.cli import build_parser as cli_parser
from hypoloom.institutional_access import InstitutionalAccess, PROVIDERS
from hypoloom.institutional_options import build, launch_arguments, options
from tests.runtime.test_institutional_access import Backend, SCOPE, URL, pump


def test_unselected_publishers_are_disabled_without_browser_calls_or_public_blocking():
    backend = Backend()
    access = InstitutionalAccess(backend)
    try:
        assert {g['mode'] for g in access.status('run')['groups']} == {'disabled'}
        assert access.automatic_reason(SCOPE, URL) is None, 'public retrieval remains independent'
        assert access.request(SCOPE, URL)['reason'] == 'disabled'
        for _ in range(3): access.tick({'run'})
        assert not backend.calls and access.status('run')['pending'] == 0
        with pytest.raises(ValueError, match='enable institutional access'):
            access.control('run', 'Nature', 'open')
    finally:
        access.close()


@pytest.mark.parametrize('selection,expected', [
    (['--institutional-prepare-provider', 'IEEE', '--institutional-provider-mode', 'Springer=public_only'],
     {'IEEE': 'auto', 'Springer': 'public_only'}),
    (['--institutional-prepare-provider', 'none'], {}),
    (['--institutional-prepare-provider', 'IEEE', '--institutional-provider-mode', 'IEEE=disabled'],
     {'IEEE': 'disabled'}),
    ([], {'IEEE': 'auto', 'ScienceDirect': 'auto', 'Springer': 'auto'}),
])
def test_init_selection_survives_host_argv_without_enabling_the_catalog(selection, expected):
    args = bootstrap_parser().parse_args(['launch', 'fixture', '--run-id', 'selection-fixture', '--auth-transport', 'none', '--cpu-only', '--institutional-browser-url',
        'http://127.0.0.1:9222', *selection])
    generated = launch_arguments(args)
    host_args = cli_parser().parse_args(['serve', '--state-root', '/tmp/unused-selection-fixture', *generated])
    assert options(host_args)['provider_modes'] == expected
    access = build(host_args)
    try:
        modes = {g['provider']: g['mode'] for g in access.status('fixture')['groups']}
        assert modes == {p: expected.get(p, 'disabled') for p in PROVIDERS.values()}
    finally:
        access.close()


def test_selected_mode_controls_actual_fetch_and_can_be_disabled_before_dispatch():
    backend = Backend(); backend.state = 'available'
    access = InstitutionalAccess(backend, provider_modes={'Nature': 'auto', 'ACM': 'public_only'})
    modes = {}
    access.read_choices = lambda run_id: (modes, {})
    try:
        assert access.request(SCOPE, URL)['status'] == 'pending'
        modes['Nature'] = 'disabled'
        access.tick({'run'})
        assert access.peek(SCOPE, URL)['reason'] == 'disabled' and not backend.calls
        modes['Nature'] = 'auto'
        access.tick({'run'})
        assert not backend.calls, 'enabling alone must not replay discarded work'
        access.request(SCOPE, URL)
        pump(access, lambda: access.status('run')['available'] == 1)
        assert len(backend.calls) == 1
        assert access.automatic_reason(SCOPE, 'https://dl.acm.org/doi/10.1145/1', institution=True) == 'public_only'
    finally:
        access.close()


def test_disabled_preparation_is_not_opened_or_reenabled_by_failure_policy(monkeypatch):
    from hypoloom.institutional_prepare import run
    monkeypatch.setattr('hypoloom.institutional_prepare.build',
                        lambda args: pytest.fail('disabled platform opened a browser'))
    args = cli_parser().parse_args(['institutional', 'prepare', '--provider', 'IEEE',
        '--institutional-provider-mode', 'IEEE=disabled',
        '--institutional-prepare-failure-mode', 'public_only'])
    output = []
    assert run(args, output=output.append) == 0
    summary = json.loads(output[-1])
    assert summary['provider_modes'] == {'IEEE': 'disabled'}
    assert summary['preparation_summary'][0]['state'] == 'disabled'
    assert summary['remaining_preparation_argv'] == []
