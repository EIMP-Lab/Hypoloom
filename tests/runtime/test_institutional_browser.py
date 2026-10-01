"""Real Chromium/CDP, synthetic publisher responses, no subscription/network use."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time

import pytest

from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser

pytestmark = pytest.mark.skipif(os.environ.get("HYPOLOOM_INSTITUTIONAL_BROWSER_TEST") != "1",
    reason="opt-in real Chromium/Poppler institutional adapter test")

URL = "https://www.nature.com/articles/s41586-026-00001-1"
DOI = "10.1038/s41586-026-00001-1"


@pytest.fixture
def browser(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        executable = os.environ.get("HYPOLOOM_TEST_CHROME") or playwright.chromium.executable_path
    profile = tmp_path / "profile"
    # Synthetic throwaway profile only; avoid desktop keyring prompts in headless CI.
    process = subprocess.Popen([executable, "--headless", "--no-sandbox", "--disable-gpu", "--password-store=basic",
        "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
        f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    adapter = None
    try:
        deadline = time.monotonic() + 15
        while not (profile / "DevToolsActivePort").exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError("test Chromium did not start")
            time.sleep(.05)
        port = (profile / "DevToolsActivePort").read_text().splitlines()[0]
        adapter = PlaywrightInstitutionalBrowser("http://127.0.0.1:" + port)
        context = adapter._connect()
        yield adapter, context
        adapter.close()
        assert process.poll() is None, "adapter must not close the operator's browser"
    finally:
        if adapter is not None:
            adapter.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)


def article(text, *, pdf=""):
    return f'<meta name="citation_doi" content="{DOI}"><meta name="citation_title" content="Controlled article">' + (
        f'<meta name="citation_pdf_url" content="{pdf}">' if pdf else "") + text


def test_real_downloaded_pdf_doi_overrules_correct_landing_page_metadata(browser):
    adapter, context = browser
    wrong_doi = "10.1038/s41586-026-99999-9"
    def respond(route):
        if route.request.url.endswith(".pdf"):
            route.fulfill(status=200, content_type="application/pdf", body=_pdf(wrong_doi))
        else:
            route.fulfill(status=200, content_type="text/html", body=article("Article", pdf=URL + ".pdf"))
    context.route("**/*", respond)
    result = adapter.fetch(URL)
    assert result == {"state": "identity_unconfirmed"}


def test_login_is_not_fulltext_and_operator_completion_is_observed_without_navigation(browser):
    adapter, context = browser
    page_state = [article('<input type="password"><p>Institutional login</p><h2>Abstract</h2><p>Summary only.</p>')]
    requests = []
    def respond(route):
        requests.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body=page_state[0])
    context.route("**/*", respond)
    assert adapter.fetch(URL)["state"] == "login_required"
    before = len(requests)
    assert adapter.fetch(URL)["state"] == "login_required"
    assert len(requests) == before, "waiting must inspect the tab, not restart authentication"
    page_state[0] = article('<div class="c-article-body"><h2>Methods</h2><p>' +
        "Verified method and result. " * 130 + '</p><h2>References</h2><p>Known source</p></div>')
    adapter._tabs["Nature"][0].reload()
    result = adapter.fetch(URL)
    assert result["state"] == "available" and result["document"]["media_type"] == "text/plain"
    assert "Verified method" in result["document"]["pages"][0]


def _pdf(doi=DOI):
    stream = f"BT /F1 12 Tf 50 750 Td (Method coefficient 0.83. DOI {doi}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    content, offsets = b"%PDF-1.4\n", [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(content))
        content += f"{number} 0 obj\n".encode() + value + b"\nendobj\n"
    xref = len(content)
    content += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
    return content + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


@pytest.mark.parametrize("suffix", ["", ".pdf"])
def test_browser_transfers_exact_pdf_and_poppler_keeps_method_without_headings(browser, suffix):
    adapter, context = browser
    original = _pdf()
    def respond(route):
        if route.request.url.endswith(".pdf"):
            route.fulfill(status=200, content_type="application/pdf", body=original)
        else:
            route.fulfill(status=200, content_type="text/html", body=article('<p>Article</p>', pdf=URL + ".pdf"))
    context.route("**/*", respond)
    result = adapter.fetch(URL + suffix)
    assert result["state"] == "available"
    assert result["document"]["content"] == original
    assert "0.83" in result["document"]["pages"][0]


@pytest.mark.parametrize(("body", "expected"), [
    ('<input type="password"><p>Institutional login</p>', "login_required"),
    ('<p>Verify you are human</p>', "verification_required"),
    ('<p>Your institution does not have access</p>', "access_denied"),
    ('<p>Access through your institution</p>', "access_required"),
    ('<h2>Abstract</h2><p>Short summary</p>', "no_full_text"),
])
def test_browser_distinguishes_login_verification_denial_and_abstract(browser, body, expected):
    adapter, context = browser
    context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=article(body)))
    assert adapter.fetch(URL)["state"] == expected


def test_real_navigation_failure_retries_and_never_reuses_an_old_article(browser):
    adapter, context = browser
    failed = [False]
    full = article('<article><h2>Methods</h2><p>' + 'Verified result. ' * 200 + '</p><h2>References</h2></article>')
    def respond(route):
        if failed[0]:
            route.abort('internetdisconnected')
        else:
            route.fulfill(status=200, content_type='text/html', body=full)
    context.route('**/*', respond)
    assert adapter.fetch(URL)['state'] == 'available'
    failed[0] = True
    result = adapter.fetch(URL, force=True)
    assert result == {'state': 'fetch_unavailable', 'reason': 'network_error'}
    # Chromium commits its error document asynchronously after rejecting goto.
    # Let it settle, as it does before the Host's next 30-second poll.
    adapter._tabs['Nature'][0].wait_for_selector('#main-frame-error', timeout=5000)
    failed[0] = False
    assert adapter.fetch(URL)['state'] == 'available'


@pytest.mark.parametrize('target,expected', [
    ('https://linkinghub.elsevier.com/retrieve/pii/1234', 'fetch_unavailable'),
    ('https://school.example/idp/profile/SAML2/Redirect/SSO?token=SECRET', 'login_required')])
def test_real_intermediate_and_login_pages_require_positive_login_evidence(browser, target, expected):
    adapter, context = browser
    context.route('**/*', lambda route: route.fulfill(status=200,
        content_type='text/html', body='<p>Redirect destination</p>'))
    page = context.new_page()
    page.goto(target)
    # Inspect the destination directly: route.fulfill(302) does not intercept
    # the next hop, and this test must not resolve or contact a real publisher.
    adapter._tabs['Nature'] = (page, URL, False)
    result = adapter.fetch(URL)
    assert result['state'] == expected
    assert 'SECRET' not in str(result)


def test_real_pdf_transport_failure_is_not_a_login_request(browser):
    adapter, context = browser
    def respond(route):
        if route.request.url.endswith('.pdf'):
            route.abort('connectionreset')
        else:
            route.fulfill(status=200, content_type='text/html', body=article('<p>Check access</p>', pdf=URL + '.pdf'))
    context.route('**/*', respond)
    assert adapter.fetch(URL) == {'state': 'fetch_unavailable', 'reason': 'network_error'}


def test_delayed_elsevier_js_redirect_reaches_pdf_in_one_check(browser):
    import json
    adapter, context = browser
    doi = 'https://doi.org/10.1016/j.fixture.2026.001'
    bridge = 'https://linkinghub.elsevier.com/retrieve/pii/1234'
    final = 'https://www.sciencedirect.com/science/article/pii/1234'
    pdf_url = final + '/pdfft'
    original = _pdf('10.1016/j.fixture.2026.001')
    requests = []
    def respond(route):
        target = route.request.url
        requests.append(target)
        if target == doi:
            body = f'<script>setTimeout(() => location.href = {json.dumps(bridge)}, 100)</script>'
        elif target == bridge:
            body = f'<script>setTimeout(() => location.href = {json.dumps(final)}, 900)</script>'
        elif target == final:
            body = article('<p>Article PDF</p>', pdf=pdf_url).replace(DOI, doi.removeprefix('https://doi.org/'))
        elif target == pdf_url:
            route.fulfill(status=200, content_type='application/pdf', body=original)
            return
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type='text/html', body=body)
    context.route('**/*', respond)
    result = adapter.fetch(doi)
    assert result['state'] == 'available'
    assert result['document']['content'] == original
    assert result['document']['identity'] == '10.1016/j.fixture.2026.001'
    assert requests == [doi, bridge, final, pdf_url]


def test_real_stalled_linkinghub_without_js_downloads_from_pii_destination(browser):
    import json
    adapter, context = browser
    doi = '10.1016/j.chb.2014.04.002'
    entry = 'https://doi.org/' + doi
    bridge = 'https://linkinghub.elsevier.com/retrieve/pii/S0747563214002118'
    final = 'https://www.sciencedirect.com/science/article/pii/S0747563214002118'
    original, requests = _pdf(doi), []
    def respond(route):
        target = route.request.url
        requests.append(target)
        if target == final + '/pdfft':
            route.fulfill(status=200, content_type='application/pdf', body=original)
            return
        if target == entry:
            body = f'<script>setTimeout(() => location.href = {json.dumps(bridge)}, 100)</script>'
        elif target == bridge:
            body = '<title>Redirecting</title><p>Redirecting</p>'  # No JS ever completes this hop.
        elif target == final:
            body = article('<p>Article PDF</p>', pdf=final + '/pdfft').replace(DOI, doi)
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type='text/html', body=body)
    context.route('**/*', respond)
    result = adapter.fetch(entry)
    assert result['state'] == 'available' and result['document']['content'] == original
    assert result['document']['identity'] == doi
    assert requests == [entry, bridge, final, final + '/pdfft']


def test_real_redirecting_shell_with_doi_is_retryable_not_missing_fulltext(browser):
    adapter, context = browser
    context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html',
        body=article('<title>Redirecting</title><p>Redirecting</p>')))
    assert adapter.fetch(URL) == {'state': 'fetch_unavailable', 'reason': 'redirect_unresolved'}


def test_sciencedirect_pdf_appearing_after_dom_complete_is_downloaded(browser):
    adapter, context = browser
    doi = '10.1016/j.chb.2014.04.002'
    final = 'https://www.sciencedirect.com/science/article/pii/S0747563214002118'
    original, requests = _pdf(doi), []
    def respond(route):
        requests.append(route.request.url)
        if route.request.url == final + '/pdfft':
            route.fulfill(status=200, content_type='application/pdf', body=original)
            return
        body = article('''<h2>Abstract</h2><p>Summary</p><script>
          setTimeout(() => {
            const a = document.createElement('a'); a.href = location.href + '/pdfft';
            a.textContent = 'View PDF'; document.body.append(a);
          }, 800);
        </script>''').replace(DOI, doi)
        route.fulfill(status=200, content_type='text/html', body=body)
    context.route('**/*', respond)
    result = adapter.fetch(final)
    assert result['state'] == 'available' and result['document']['content'] == original
    assert requests == [final, final + '/pdfft']


def test_html_interstitial_from_pdf_endpoint_is_retryable_not_missing_fulltext(browser):
    adapter, context = browser
    def respond(route):
        body = ('<meta http-equiv="refresh" content="0;url=/articles/fixture"><p>Redirecting</p>'
                if route.request.url.endswith('.pdf') else article('<p>PDF available</p>', pdf=URL+'.pdf'))
        route.fulfill(status=200, content_type='text/html', body=body)
    context.route('**/*', respond)
    assert adapter.fetch(URL) == {'state': 'fetch_unavailable', 'reason': 'pdf_response_not_pdf'}


@pytest.mark.parametrize('mode', ['script', 'verification', 'wrong_pii'])
def test_sciencedirect_pdf_interstitial_runs_in_browser_and_preserves_identity(browser, mode):
    import json
    adapter, context = browser
    doi = '10.1016/j.chb.2014.04.002'
    final = 'https://www.sciencedirect.com/science/article/pii/S0747563214002118'
    pii = 'S0000000000000000' if mode == 'wrong_pii' else 'S0747563214002118'
    asset = f'https://pdf.sciencedirectassets.com/271802/1-s2.0-{pii}/main.pdf?token=SECRET'
    original, navigations = _pdf(doi), []
    def respond(route):
        target = route.request.url
        if route.request.is_navigation_request():
            navigations.append(target)
        if target == asset:
            route.fulfill(status=200, content_type='application/pdf', body=original)
            return
        if target == final:
            body = article('<p>Article PDF</p>', pdf=final + '/pdfft').replace(DOI, doi)
        elif target == final + '/pdfft':
            body = ('<p>Verify you are human</p>' if mode == 'verification' else
                '<title>PDF redirect</title><script>setTimeout(() => location.href = ' +
                json.dumps(asset) + ', 800)</script>')
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type='text/html', body=body)
    context.route('**/*', respond)
    result = adapter.fetch(final)
    if mode == 'verification':
        assert result == {'state': 'verification_required'}
        before = list(navigations)
        assert adapter.fetch(final) == {'state': 'verification_required'}
        assert navigations == before, 'do not replace an unfinished PDF verification page'
        adapter._tabs['ScienceDirect'][0].goto(asset)
        result = adapter.fetch(final)
    if mode == 'wrong_pii':
        assert result == {'state': 'identity_unconfirmed'}
    else:
        assert result['state'] == 'available' and result['document']['content'] == original
        assert result['document']['identity'] == doi
        assert result['document']['url'] == asset.split('?')[0]
        assert 'SECRET' not in str(result)
    assert navigations.count(final + '/pdfft') == 1


@pytest.mark.parametrize('status,reason,visits', [
    (503, 'http_server_error', 2), (429, 'http_rate_limited', 1), (403, 'http_error', 1)])
def test_http_navigation_classification_and_retry_bound(browser, status, reason, visits):
    adapter, context = browser
    requests = []
    def respond(route):
        requests.append(route.request.url)
        route.fulfill(status=status, content_type='text/html', body='<p>Unavailable</p>')
    context.route('**/*', respond)
    assert adapter.fetch(URL) == {'state': 'fetch_unavailable', 'reason': reason}
    assert requests == [URL] * visits


def test_real_transient_navigation_recovers_before_returning_to_queue(browser):
    adapter, context = browser
    requests = []
    full = article('<article><h2>Methods</h2>' + 'Verified result. ' * 200 + '<h2>References</h2></article>')
    def respond(route):
        requests.append(route.request.url)
        if len(requests) == 1:
            route.abort('connectionreset')
        else:
            route.fulfill(status=200, content_type='text/html', body=full)
    context.route('**/*', respond)
    assert adapter.fetch(URL)['state'] == 'available'
    assert requests == [URL, URL]


@pytest.mark.parametrize('status,reason', [(503, 'http_server_error'), (429, 'http_rate_limited')])
def test_pdf_http_failures_use_transient_backoff_codes(browser, status, reason):
    adapter, context = browser
    def respond(route):
        route.fulfill(status=status if route.request.url.endswith('.pdf') else 200,
            content_type='text/html', body=article('<p>PDF</p>', pdf=URL + '.pdf'))
    context.route('**/*', respond)
    assert adapter.fetch(URL) == {'state': 'fetch_unavailable', 'reason': reason}


def test_new_host_connection_reuses_same_browser_login_for_other_papers(browser):
    adapter, context = browser
    login = 'https://www.nature.com/login'
    logins = []
    def respond(route):
        if route.request.url == login:
            logins.append(login)
            route.fulfill(status=200, headers={'Content-Type': 'text/html',
                'Set-Cookie': 'fixture_auth=entitled; Path=/; Secure; HttpOnly; Max-Age=3600'}, body='Signed in')
        elif 'fixture_auth=entitled' in route.request.headers.get('cookie', ''):
            doi = '10.1038/' + route.request.url.rsplit('/', 1)[-1]
            route.fulfill(status=200, content_type='text/html', body=article(
                '<article><h2>Methods</h2>' + 'Entitled full text. ' * 180 + '<h2>References</h2></article>').replace(DOI, doi))
        else:
            route.fulfill(status=200, content_type='text/html', body='<input type="password">')
    context.route('**/*', respond)
    assert adapter.fetch(URL)['state'] == 'login_required'
    operator = context.new_page()
    operator.goto(login)
    operator.close()
    assert adapter.fetch(URL, force=True)['state'] == 'available'
    adapter.close()  # End one Host connection; the server browser keeps running.
    replacement = PlaywrightInstitutionalBrowser(adapter.endpoint, ui_url='http://127.0.0.1:5800')
    try:
        replacement._connect().route('**/*', respond)
        result = replacement.fetch(URL.replace('00001-1', '00002-2'))
        assert result['state'] == 'available'
        assert result['document']['identity'].endswith('00002-2')
        assert len(logins) == 1
    finally:
        replacement.close()


def test_server_profile_retains_persistent_login_after_browser_restart(tmp_path):
    from playwright.sync_api import sync_playwright
    profile = tmp_path / 'server-profile'
    observed = []
    with sync_playwright() as playwright:
        options = dict(headless=True, args=['--no-sandbox', '--password-store=basic'],
                       executable_path=os.environ.get('HYPOLOOM_TEST_CHROME') or playwright.chromium.executable_path)
        first = playwright.chromium.launch_persistent_context(str(profile), **options)
        try:
            first.route('**/*', lambda route: route.fulfill(status=200, headers={
                'Content-Type': 'text/html', 'Set-Cookie': 'fixture_auth=entitled; Path=/; Secure; HttpOnly; Max-Age=3600'}, body='Signed in'))
            first.pages[0].goto(URL)
        finally:
            first.close()
        second = playwright.chromium.launch_persistent_context(str(profile), **options)
        try:
            def respond(route):
                observed.append(route.request.headers.get('cookie', ''))
                route.fulfill(status=200, content_type='text/html', body='Full text')
            second.route('**/*', respond)
            second.pages[0].goto(URL)
            assert any('fixture_auth=entitled' in header for header in observed)
        finally:
            second.close()
