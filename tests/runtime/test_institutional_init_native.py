"""Real process/socket/browser closure, with disposable state and synthetic pages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from hypoloom.institutional_client import connections
from tests.runtime.test_institutional_init_dialogue import arguments, eventually, request
from tests.runtime.test_institutional_runtime import assert_free


from tests.runtime.test_institutional_access import ALL_PROVIDERS_AUTO


@pytest.fixture(autouse=True)
def synthetic_browser_sources_only(monkeypatch):
    # These scenarios qualify CDP/tab recovery using routed synthetic PDFs.
    # Public acquisition has its own native closure tests; a real 25s public
    # request here can expire the 6s browser observation before fault injection.
    monkeypatch.setattr("hypoloom.document_fallback.acquire_public_pdf", lambda *a, **kw: (None, None))


def route_fixed_samples(context, downloads, manual_clearance=None):
    from hypoloom.institutional_access import PROVIDERS, provider_catalog
    from tests.runtime.test_institutional_browser import _pdf
    catalog = [p for p in provider_catalog() if p['default_sample_url']]
    def respond(route):
        url = route.request.url
        for item in catalog:
            doi_url = item['default_sample_url']
            # Authentication entries may live outside the article origin.
            domain = next(domain for domain, provider in PROVIDERS.items() if provider == item['provider'])
            landing = 'https://' + domain + '/sample'
            doi = doi_url.removeprefix('https://doi.org/')
            if url == doi_url:
                # Browser-initiated navigation stays intercepted; an HTTP redirect
                # from route.fulfill can fetch its destination outside the route.
                route.fulfill(status=200, content_type='text/html', body=
                    f'<script>location.href = {json.dumps(landing)}</script>'); return
            if url == landing:
                if item['provider'] == 'ScienceDirect' and manual_clearance is not None and not manual_clearance.is_set():
                    route.fulfill(status=200, content_type='text/html', body=
                        '<p>Verify you are human</p><button onclick="document.body.innerText = \'Fixture cleared\'">Complete fixture verification</button>')
                    return
                route.fulfill(status=200, content_type='text/html', body=
                    f'<meta name="citation_doi" content="{doi}">'
                    f'<meta name="citation_pdf_url" content="{landing}.pdf">Controlled article'); return
            if url == landing + '.pdf':
                downloads.append(item['provider'])
                route.fulfill(status=200, content_type='application/pdf', body=_pdf(doi)); return
        route.fulfill(status=200, content_type='text/html', body='Publisher preparation fixture')
    context.route('**/*', respond)


@pytest.mark.skipif(os.environ.get('HYPOLOOM_INSTITUTIONAL_BROWSER_TEST') != '1', reason='opt-in real Chromium preparation')
@pytest.mark.parametrize('delayed_connection,sample_challenge', [(False, False), (True, False), (False, True)])
def test_real_relay_opens_next_platforms_after_idle_user_answer(tmp_path, monkeypatch, delayed_connection, sample_challenge):
    from hypoloom.institutional_access import InstitutionalAccess
    from hypoloom.institutional_runtime import RunBrowser
    from hypoloom.institutional_prepare import run
    from tests.runtime.test_institutional_browser import browser as browser_fixture
    fixture = browser_fixture.__wrapped__(tmp_path)
    adapter, context = next(fixture)
    user_page = context.pages[0]
    downloads = []
    manual_clearance = threading.Event() if sample_challenge else None
    class Browser(RunBrowser):
        def _connect(self):
            current = super()._connect()
            route_fixed_samples(current, downloads, manual_clearance)
            return current
    backend = Browser({'mode': 'existing', 'endpoint': adapter.endpoint}, 'preparation')
    from hypoloom import institutional_dialogue
    monkeypatch.setattr(institutional_dialogue, 'CONNECTION_RETRY_SECONDS', .1)
    reachable = threading.Event()
    if not delayed_connection: reachable.set()
    dial = backend.relay.dial
    def delayed_dial():
        if not reachable.is_set(): raise ConnectionRefusedError('disposable transport not ready')
        return dial()
    backend.relay.dial = delayed_dial
    relay_endpoint = backend.endpoint
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    if sample_challenge: access.poll_seconds = .05
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output, codes, errors = [], [], []
    def worker():
        try: codes.append(run(arguments('--agent-session', '--repository', str(tmp_path),
            '--institutional-connect-timeout', '3', '--provider', 'IEEE', '--provider', 'ScienceDirect',
            '--provider', 'Springer Nature', '--provider', 'ACM'), output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = None; observer = None
    try:
        first = eventually(lambda: next((json.loads(v) for v in output
            if v.startswith('{') and 'state_root' in json.loads(v)), None))
        root = Path(first['state_root'])
        def observe():
            return subprocess.Popen(first['watch_argv'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        observer = observe()
        if delayed_connection:
            eventually(lambda: request(root).result.get('state') == 'waiting_for_connection')
            assert request(root).result['connection']['reason_code'] == 'cdp_endpoint_unavailable'
            assert observer.poll() is None
            reachable.set()  # No retry reply: the program must discover recovery itself.
        for provider in ('IEEE', 'ScienceDirect', 'Springer Nature', 'ACM'):
            text, error = observer.communicate(timeout=15)
            assert observer.returncode == 0, error
            assert provider + ' 页面已打开' in text
            assert '固定回复：完成 / 验证样本' in text
            view = request(root).result
            assert view['state'] == 'waiting_for_user' and view['provider'] == provider
            if provider == 'IEEE': time.sleep(6)  # Beyond relay's per-I/O timeout.
            assert request(root, 'PREPARATION_ANSWER', {'provider': provider, 'reply': '完成'}).status == 'accepted'
            if sample_challenge and provider == 'ScienceDirect':
                observer = observe()
                text, error = observer.communicate(timeout=15)
                assert observer.returncode == 0, error
                assert '样本页面' in text and '原获取期限' in text
                view = request(root).result
                assert view['provider'] == 'ScienceDirect' and view['sample_url']
                assert downloads == ['IEEE'], 'no sample should be claimed before human verification'
                # Pump this independent CDP client's queued page-created events.
                user_page.wait_for_timeout(100)
                target = next(p for p in context.pages if p.url == 'https://sciencedirect.com/sample')
                manual_clearance.set()
                target.get_by_text('Complete fixture verification').click()
                assert request(root, 'PREPARATION_ANSWER', {'provider': provider, 'reply': '完成'}).status == 'accepted'
            if provider != 'ACM': observer = observe()
        thread.join(10)
        assert not thread.is_alive() and not errors and codes == [0]
        summary = json.loads(output[-1])
        assert [r['state'] for r in summary['preparation_summary']] == ['sample_verified'] * 4
        assert downloads == ['IEEE', 'ScienceDirect', 'Springer Nature', 'ACM']
        assert all(r['samples'][0]['sample_scope'] == 'public_full_text' for r in summary['preparation_summary'])
        assert summary['remaining_preparation_argv'] == []
        assert not user_page.is_closed() and not backend._tabs and not root.exists()
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        if thread.is_alive() and root is not None:
            request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); thread.join(10)
        if observer is not None and observer.poll() is None:
            observer.terminate(); observer.communicate(timeout=5)
        backend.release()
        fixture.close()
    assert_free(relay_endpoint)


def test_real_background_client_failure_is_single_and_finish_releases_resources(tmp_path):
    final_root = tmp_path / 'state' / 'research'
    process = subprocess.Popen([sys.executable, '-B', '-m', 'hypoloom', 'institutional', 'prepare',
        '--repository', str(tmp_path), '--agent-session', '--institutional-browser-mode', 'client',
        '--state-root', str(final_root), '--run-id', 'research', '--request-id', 'create-research',
        '--institutional-connect-timeout', '3'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    root = None
    started = time.monotonic()
    try:
        first = json.loads(process.stdout.readline())
        assert time.monotonic() - started < 5, 'initial status was buffered until process exit'
        root = Path(first['state_root'])
        allocated = connections(final_root, 'research', 'create-research', preparation_root=root)['connections'][0]
        assert allocated['state'] == 'waiting_for_client'
        def blocked():
            status = request(root).result
            return status if status.get('state') == 'waiting_for_connection' else None
        status = eventually(blocked)
        assert status['connection']['state'] == 'waiting_for_client'
        assert time.monotonic() - started < 8, 'connection failure waited per platform'
        assert request(root, 'PREPARATION_ANSWER', {'action': 'finish'}).status == 'accepted'
        output, errors = process.communicate(timeout=8)
        assert process.returncode == 0, errors
        rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        assert not any('provider' in row for row in rows)
        summaries = [row for row in rows if 'preparation_summary' in row]
        assert len(summaries) == 1
        assert {p['state'] for p in summaries[0]['preparation_summary']} == {'not_checked'}
        assert rows[-1] == {'preparation_lifecycle': {'state': 'finished', 'reason': 'operator_finished'}}
        assert not root.exists() and not Path(allocated['socket_path']).parent.exists()
        assert_free(allocated['endpoint'])
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        if process.poll() is None:
            process.terminate(); process.communicate(timeout=8)
        process.stdout.close(); process.stderr.close()


@pytest.mark.skipif(os.environ.get('HYPOLOOM_INSTITUTIONAL_BROWSER_TEST') != '1', reason='opt-in real Chromium preparation')
@pytest.mark.parametrize('challenge', [False, True])
def test_real_browser_connection_and_dialogue_share_worker_and_preserve_user_tab(tmp_path, monkeypatch, challenge):
    from hypoloom.institutional_access import InstitutionalAccess
    from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser
    from hypoloom.institutional_prepare import run
    from tests.runtime.test_institutional_browser import browser as browser_fixture, _pdf, URL, DOI
    # Reuse the existing disposable native browser fixture without changing its ownership.
    fixture = browser_fixture.__wrapped__(tmp_path)
    adapter, context = next(fixture)
    user_page = context.pages[0]
    workers = []
    class Browser(PlaywrightInstitutionalBrowser):
        def _connect(self):
            workers.append(threading.get_ident())
            current = super()._connect()
            def route(request):
                if challenge and request.request.url.rstrip('/') == 'https://nature.com':
                    request.fulfill(status=403, content_type='text/html', body='<h1>Are you a robot?</h1>')
                elif request.request.url.endswith('.pdf'):
                    request.fulfill(status=200, content_type='application/pdf', body=_pdf())
                else:
                    request.fulfill(status=200, content_type='text/html', body=
                        f'<meta name="citation_doi" content="{DOI}"><meta name="citation_pdf_url" content="{URL}.pdf">Institutional fixture')
            current.route('**/*', route)
            return current
    backend = Browser(adapter.endpoint)
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output, results, errors = [], [], []
    def worker():
        try: results.append(run(arguments('--agent-session', '--repository', str(tmp_path), '--provider', 'Springer Nature'), output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = None
    try:
        root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output if v.startswith('{') and 'state_root' in json.loads(v)), None))
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        from hypoloom.institutional_instructions import render
        view = request(root).result
        assert (view.get('reason_code') == 'verification_required') == challenge
        assert ('打开时检测到人机验证' in render(view)) == challenge
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'Springer Nature', 'action': 'verify', 'sample': URL}).status == 'accepted'
        thread.join(15)
        assert not thread.is_alive() and not errors and results == [0]
        assert json.loads(output[-1])['preparation_summary'][0]['state'] == 'sample_verified'
        assert len(workers) >= 3 and len(set(workers)) == 1
        assert not user_page.is_closed() and not backend._tabs and not root.exists()
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        if thread.is_alive() and root is not None:
            request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); thread.join(15)
        fixture.close()


@pytest.mark.skipif(os.environ.get('HYPOLOOM_INSTITUTIONAL_BROWSER_TEST') != '1', reason='opt-in real Chromium preparation')
def test_real_control_disconnect_between_platforms_recovers_with_browser_left_open(tmp_path, monkeypatch):
    import socket
    from hypoloom.institutional_access import InstitutionalAccess
    from hypoloom.institutional_runtime import RunBrowser
    from hypoloom.institutional_prepare import run
    from tests.runtime.test_institutional_browser import browser as browser_fixture
    fixture = browser_fixture.__wrapped__(tmp_path)
    adapter, context = next(fixture)
    user_page = context.pages[0]
    downloads = []
    class Browser(RunBrowser):
        disconnected = False
        def _connect(self):
            current = super()._connect()
            route_fixed_samples(current, downloads)
            return current
        def prepare(self, provider, **kwargs):
            if provider == 'ScienceDirect' and not self.disconnected:
                self.disconnected = True
                reachable.clear()
                with self.relay.lock:
                    for peer in list(self.relay.peers):
                        try: peer.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
            return super().prepare(provider, **kwargs)
    backend = Browser({'mode': 'existing', 'endpoint': adapter.endpoint}, 'preparation')
    reachable = threading.Event(); reachable.set()
    dial = backend.relay.dial
    def network():
        if not reachable.is_set(): raise ConnectionRefusedError('disposable transport unavailable')
        return dial()
    backend.relay.dial = network
    access = InstitutionalAccess(backend, poll_seconds=.05, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output, codes = [], []
    thread = threading.Thread(target=lambda: codes.append(run(arguments('--agent-session',
        '--repository', str(tmp_path), '--provider', 'IEEE', '--provider', 'ScienceDirect',
        '--provider', 'Springer Nature'), output=output.append)))
    thread.start(); root = None
    try:
        root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
            if v.startswith('{') and 'state_root' in json.loads(v)), None))
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'IEEE', 'reply': '完成'}).status == 'accepted'
        failure = eventually(lambda: next((json.loads(v) for v in output if v.startswith('{')
            and json.loads(v).get('provider') == 'ScienceDirect'
            and json.loads(v).get('state') == 'browser_unavailable'), None))
        assert failure['reason_code'] in {'browser_target_closed', 'cdp_connection_closed', 'cdp_endpoint_unavailable'}
        reachable.set()
        for provider in ('ScienceDirect', 'Springer Nature'):
            eventually(lambda: request(root).result.get('state') == 'waiting_for_user'
                and request(root).result.get('provider') == provider)
            assert request(root, 'PREPARATION_ANSWER', {'provider': provider, 'reply': '完成'}).status == 'accepted'
        thread.join(10)
        assert not thread.is_alive() and codes == [0]
        assert json.loads(output[-1])['remaining_preparation_argv'] == []
        assert downloads == ['IEEE', 'ScienceDirect', 'Springer Nature']
        assert not user_page.is_closed() and not backend._tabs and not root.exists()
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        reachable.set()
        if thread.is_alive() and root is not None:
            request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); thread.join(10)
        backend.release(); fixture.close()
    assert_free(backend.endpoint)
