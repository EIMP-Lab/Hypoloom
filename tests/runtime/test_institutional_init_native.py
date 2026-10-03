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


@pytest.mark.skipif(os.environ.get('HYPOLOOM_INSTITUTIONAL_BROWSER_TEST') != '1', reason='opt-in real Chromium preparation')
@pytest.mark.parametrize('delayed_connection', [False, True])
def test_real_relay_opens_next_platforms_after_idle_user_answer(tmp_path, monkeypatch, delayed_connection):
    from hypoloom.institutional_access import InstitutionalAccess
    from hypoloom.institutional_runtime import RunBrowser
    from hypoloom.institutional_prepare import run
    from tests.runtime.test_institutional_browser import browser as browser_fixture
    fixture = browser_fixture.__wrapped__(tmp_path)
    adapter, context = next(fixture)
    user_page = context.pages[0]
    class Browser(RunBrowser):
        def _navigate(self, page, target):
            # Real CDP/page operations, synthetic content; never visit a publisher.
            page.set_content('<title>Preparation fixture</title>Login placeholder')
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
    access = InstitutionalAccess(backend)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output, codes, errors = [], [], []
    def worker():
        try: codes.append(run(arguments('--agent-session', '--repository', str(tmp_path),
            '--institutional-connect-timeout', '3'), output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = None
    try:
        root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
            if v.startswith('{') and 'state_root' in json.loads(v)), None))
        if delayed_connection:
            eventually(lambda: request(root).result.get('state') == 'waiting_for_connection')
            assert request(root).result['connection']['reason_code'] == 'cdp_endpoint_unavailable'
            reachable.set()  # No retry reply: the program must discover recovery itself.
        for provider in ('IEEE', 'ScienceDirect', 'Springer'):
            eventually(lambda: request(root).result.get('state') == 'waiting_for_user'
                and request(root).result.get('provider') == provider)
            if provider == 'IEEE': time.sleep(6)  # Beyond relay's per-I/O timeout.
            assert request(root, 'PREPARATION_ANSWER', {'provider': provider, 'reply': '完成'}).status == 'accepted'
        thread.join(10)
        assert not thread.is_alive() and not errors and codes == [0]
        summary = json.loads(output[-1])
        assert [r['state'] for r in summary['preparation_summary']] == ['not_checked'] * 3
        assert summary['remaining_preparation_argv'] == []
        assert not user_page.is_closed() and not backend._tabs and not root.exists()
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        if thread.is_alive() and root is not None:
            request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); thread.join(10)
        backend.release()
        fixture.close()
    assert_free(relay_endpoint)


def test_real_background_client_failure_is_single_and_finish_releases_resources(tmp_path):
    process = subprocess.Popen([sys.executable, '-B', '-m', 'hypoloom', 'institutional', 'prepare',
        '--repository', str(tmp_path), '--agent-session', '--institutional-browser-mode', 'client',
        '--institutional-connect-timeout', '3'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    root = None
    started = time.monotonic()
    try:
        first = json.loads(process.stdout.readline())
        assert time.monotonic() - started < 5, 'initial status was buffered until process exit'
        root = Path(first['state_root'])
        allocated = connections(root)[0]
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
        assert {p['state'] for p in rows[-1]['preparation_summary']} == {'not_checked'}
        assert not root.exists() and not Path(allocated['socket_path']).parent.exists()
        assert_free(allocated['endpoint'])
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        if process.poll() is None:
            process.terminate(); process.communicate(timeout=8)
        process.stdout.close(); process.stderr.close()


@pytest.mark.skipif(os.environ.get('HYPOLOOM_INSTITUTIONAL_BROWSER_TEST') != '1', reason='opt-in real Chromium preparation')
def test_real_browser_connection_and_dialogue_share_worker_and_preserve_user_tab(tmp_path, monkeypatch):
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
                if request.request.url.endswith('.pdf'):
                    request.fulfill(status=200, content_type='application/pdf', body=_pdf())
                else:
                    request.fulfill(status=200, content_type='text/html', body=
                        f'<meta name="citation_doi" content="{DOI}"><meta name="citation_pdf_url" content="{URL}.pdf">Institutional fixture')
            current.route('**/*', route)
            return current
    backend = Browser(adapter.endpoint)
    access = InstitutionalAccess(backend)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output, results, errors = [], [], []
    def worker():
        try: results.append(run(arguments('--agent-session', '--repository', str(tmp_path), '--provider', 'Nature'), output=output.append))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    root = None
    try:
        root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output if v.startswith('{') and 'state_root' in json.loads(v)), None))
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'Nature', 'action': 'verify', 'sample': URL}).status == 'accepted'
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
    class Browser(RunBrowser):
        def _navigate(self, page, target):
            page.set_content('<title>Fixture</title>Publisher preparation')
    backend = Browser({'mode': 'existing', 'endpoint': adapter.endpoint}, 'preparation')
    reachable = threading.Event(); reachable.set()
    dial = backend.relay.dial
    def network():
        if not reachable.is_set(): raise ConnectionRefusedError('disposable transport unavailable')
        return dial()
    backend.relay.dial = network
    access = InstitutionalAccess(backend, poll_seconds=.05)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output, codes = [], []
    thread = threading.Thread(target=lambda: codes.append(run(arguments('--agent-session',
        '--repository', str(tmp_path), '--provider', 'IEEE', '--provider', 'ScienceDirect',
        '--provider', 'Springer'), output=output.append)))
    thread.start(); root = None
    try:
        root = eventually(lambda: next((Path(json.loads(v)['state_root']) for v in output
            if v.startswith('{') and 'state_root' in json.loads(v)), None))
        eventually(lambda: request(root).result.get('state') == 'waiting_for_user')
        reachable.clear()
        with backend.relay.lock:
            for peer in list(backend.relay.peers):
                try: peer.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        assert request(root, 'PREPARATION_ANSWER', {'provider': 'IEEE', 'reply': '完成'}).status == 'accepted'
        failure = eventually(lambda: next((json.loads(v) for v in output if v.startswith('{')
            and json.loads(v).get('provider') == 'ScienceDirect'
            and json.loads(v).get('state') == 'browser_unavailable'), None))
        assert failure['reason_code'] in {'browser_target_closed', 'cdp_connection_closed', 'cdp_endpoint_unavailable'}
        reachable.set()
        for provider in ('ScienceDirect', 'Springer'):
            eventually(lambda: request(root).result.get('state') == 'waiting_for_user'
                and request(root).result.get('provider') == provider)
            assert request(root, 'PREPARATION_ANSWER', {'provider': provider, 'reply': '完成'}).status == 'accepted'
        thread.join(10)
        assert not thread.is_alive() and codes == [0]
        assert json.loads(output[-1])['remaining_preparation_argv'] == []
        assert not user_page.is_closed() and not backend._tabs and not root.exists()
        assert not list(tmp_path.rglob('state.sqlite'))
    finally:
        reachable.set()
        if thread.is_alive() and root is not None:
            request(root, 'PREPARATION_ANSWER', {'action': 'finish'}); thread.join(10)
        backend.release(); fixture.close()
    assert_free(backend.endpoint)
