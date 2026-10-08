"""Publisher selection is preserved from init to Host routing and observation."""
import json
import pytest

from hypoloom.bootstrap import build_parser as bootstrap_parser
from hypoloom.cli import build_parser as cli_parser
from hypoloom.institutional_access import (InstitutionalAccess, PROVIDERS, provider_catalog,
    provider_entry_url, publisher, identity_from_url)
from hypoloom.institutional_options import build, launch_arguments, options
from tests.runtime.test_institutional_access import Backend, SCOPE, URL, pump


def test_springer_nature_is_one_institutional_entry_with_unchanged_login():
    catalog = provider_catalog()
    names = [item['provider'] for item in catalog]
    assert names == ['IEEE', 'ACM', 'ScienceDirect', 'Springer Nature', 'Wiley', 'SAGE', 'Oxford', 'Taylor & Francis']
    login = 'https://wayf.springernature.com/?redirect_uri=https%3A%2F%2Flink.springer.com%2F'
    for name in ('Springer Nature', 'Springer', 'Nature'):
        assert provider_entry_url(name) == login
    row = next(item for item in catalog if item['provider'] == 'Springer Nature')
    assert row['display_name'] == 'Springer Nature' and row['entry_url'] == login
    assert row['default_sample_url'] == 'https://doi.org/10.1007/s10664-020-09851-6'
    for url in (URL, 'https://link.springer.com/article/10.1007/s10664-020-09851-6',
                'https://doi.org/10.1038/s41586-021-03819-2', 'https://doi.org/10.1007/s10664-020-09851-6'):
        assert publisher(url) == 'Springer Nature'
    assert identity_from_url(URL) == 'doi:10.1038/' + URL.rsplit('/', 1)[1]
    assert identity_from_url(URL.replace('https://', 'http://')) == ''
    assert identity_from_url(URL.replace('www.nature.com', 'link.springer.com')) == ''


def test_legacy_names_select_one_group_and_enable_both_article_routes(monkeypatch):
    args = cli_parser().parse_args(['institutional', 'prepare', '--provider', 'Springer',
        '--provider', 'Nature', '--provider', 'Springer Nature',
        '--institutional-browser-url', 'http://127.0.0.1:9222'])
    from hypoloom.institutional_options import preparation_selection
    assert preparation_selection(args.provider, {}) == ['Springer Nature']
    assert options(args)['provider_modes'] == {'Springer Nature': 'auto'}
    access = build(args)
    try:
        for url in (URL, 'https://link.springer.com/article/10.1007/s10664-020-09851-6'):
            assert access.request(SCOPE, url)['status'] == 'pending'
        groups = access.status('run')['groups']
        assert len(groups) == 8
        assert sum(g['provider'] == 'Springer Nature' for g in groups) == 1
    finally:
        access.close()
    from hypoloom.institutional_prepare import run
    from tests.runtime.test_institutional_preparation import PreparationBackend
    backend = PreparationBackend()
    access = InstitutionalAccess(backend, provider_modes=options(args)['provider_modes'])
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output = []
    assert run(args, input_fn=lambda _: 'p', output=output.append) == 0
    assert [call[0] for call in backend.calls] == ['Springer Nature']
    assert json.loads(output[-1])['provider_modes'] == {'Springer Nature': 'public_only'}


def test_conflicting_legacy_modes_require_an_explicit_unified_choice():
    args = cli_parser().parse_args(['institutional', 'prepare', '--provider', 'Springer Nature',
        '--institutional-provider-mode', 'Springer=auto', '--institutional-provider-mode', 'Nature=disabled'])
    with pytest.raises(ValueError, match='conflicting'):
        options(args)
    with pytest.raises(ValueError, match='conflicting'):
        InstitutionalAccess(Backend(), provider_modes={'Springer': 'auto', 'Nature': 'disabled'})


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
            access.control('run', 'Springer Nature', 'open')
    finally:
        access.close()


@pytest.mark.parametrize('selection,expected', [
    (['--institutional-prepare-provider', 'IEEE', '--institutional-provider-mode', 'Springer Nature=public_only'],
     {'IEEE': 'auto', 'Springer Nature': 'public_only'}),
    (['--institutional-prepare-provider', 'none'], {}),
    (['--institutional-prepare-provider', 'IEEE', '--institutional-provider-mode', 'IEEE=disabled'],
     {'IEEE': 'disabled'}),
    ([], {'IEEE': 'auto', 'ScienceDirect': 'auto', 'Springer Nature': 'auto'}),
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
    access = InstitutionalAccess(backend, provider_modes={'Springer Nature': 'auto', 'ACM': 'public_only'})
    modes = {}
    access.read_choices = lambda run_id: (modes, {})
    try:
        assert access.request(SCOPE, URL)['status'] == 'pending'
        modes['Springer Nature'] = 'disabled'
        access.tick({'run'})
        assert access.peek(SCOPE, URL)['reason'] == 'disabled' and not backend.calls
        modes['Springer Nature'] = 'auto'
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
