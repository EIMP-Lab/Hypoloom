"""Navigation failures must never admit stale text or imply broken credentials."""
from types import SimpleNamespace

import pytest

from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser

URL = "https://www.nature.com/articles/s41586-026-00001-1"


@pytest.mark.parametrize("failure,reason", [
    (TimeoutError("private callback SECRET"), "cdp_timeout"),
    (RuntimeError("connect ECONNREFUSED 127.0.0.1:42 SECRET"), "cdp_connection_refused"),
    (RuntimeError("Unexpected status 401 when connecting to SECRET"), "cdp_authorization_failed"),
    (RuntimeError("Unexpected status 503 when connecting to SECRET"), "cdp_endpoint_unavailable"),
    (ValueError("authenticated browser context is unavailable"), "cdp_context_unavailable"),
    (RuntimeError("private SSO SECRET"), "cdp_connection_failed"),
])
def test_connection_failure_returns_fixed_reason_without_exception_text(failure, reason):
    browser = PlaywrightInstitutionalBrowser("http://127.0.0.1:9222")
    def fail(): raise failure
    browser._connect = fail
    result = browser.check_connection(timeout_seconds=.005)
    assert result == {"state": "browser_unavailable", "reason_code": reason}
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("local_deadline,reason", [(True, "cdp_endpoint_unavailable"), (False, "cdp_timeout")])
def test_local_check_deadline_does_not_erase_last_endpoint_failure(monkeypatch, local_deadline, reason):
    from hypoloom import institutional_browser
    import time
    clock, calls = [0.], []
    monkeypatch.setattr(institutional_browser, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    browser = PlaywrightInstitutionalBrowser("http://127.0.0.1:9222")
    def connect():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("Unexpected status 503 SECRET")
        clock[0] = 1.1
        if local_deadline: browser._remaining(5)
        raise TimeoutError("actual control request timed out SECRET")
    browser._connect = connect
    assert browser.check_connection(timeout_seconds=1) == {"state": "browser_unavailable", "reason_code": reason}
    assert len(calls) == 2 and browser._deadline is None


@pytest.mark.parametrize("failure,reason", [
    (TimeoutError("private callback SECRET"), "cdp_timeout"),
    (RuntimeError("connect ECONNREFUSED SECRET"), "cdp_connection_refused"),
    (RuntimeError("Unexpected status 503 SECRET"), "cdp_endpoint_unavailable"),
    (ValueError("authenticated browser context is unavailable"), "cdp_context_unavailable"),
])
@pytest.mark.parametrize('operation', ['prepare', 'fetch'])
def test_platform_connection_errors_keep_the_same_safe_diagnostics(failure, reason, operation):
    browser = PlaywrightInstitutionalBrowser("http://127.0.0.1:9222")
    def fail(): raise failure
    browser._connect = fail
    result = browser.prepare("Springer") if operation == 'prepare' else browser.fetch(URL)
    assert result == {"state": "browser_unavailable", "reason": reason}
    assert 'SECRET' not in str(result) and browser._deadline is None


@pytest.mark.parametrize('operation', ['new_page', 'bring_to_front'])
@pytest.mark.parametrize('failure,reason', [
    (type('TargetClosedError', (Exception,), {})('closed SECRET'), 'browser_target_closed'),
    (type('TimeoutError', (Exception,), {})('page timed out SECRET'), 'browser_operation_timeout'),
    (RuntimeError('page operation SECRET'), 'browser_operation_failed'),
])
def test_sample_page_failures_keep_safe_diagnostics(operation, failure, reason):
    browser = PlaywrightInstitutionalBrowser('http://127.0.0.1:9222')
    def fail(*args): raise failure
    page = SimpleNamespace(set_default_timeout=lambda *a: None, bring_to_front=fail)
    browser._connect = lambda: SimpleNamespace(new_page=fail if operation == 'new_page' else lambda: page)
    assert browser.fetch(URL, focus=True) == {'state': 'browser_unavailable', 'reason': reason}
    assert browser._deadline is None


@pytest.mark.parametrize("operation", ["new_page", "bring_to_front"])
@pytest.mark.parametrize("failure,reason", [
    (TimeoutError("page operation SECRET"), "browser_operation_timeout"),
    (type("TargetClosedError", (Exception,), {})("target closed SECRET"), "browser_target_closed"),
    (RuntimeError("page operation SECRET"), "browser_operation_failed"),
])
def test_platform_page_errors_do_not_claim_a_disconnected_browser(operation, failure, reason):
    browser = PlaywrightInstitutionalBrowser("http://127.0.0.1:9222")
    def fail(*a): raise failure
    page = SimpleNamespace(set_default_timeout=lambda *a: None, bring_to_front=fail)
    browser._connect = lambda: SimpleNamespace(new_page=fail if operation == "new_page" else lambda: page)
    browser._navigate = lambda *a: None
    assert browser.prepare("Springer") == {"state": "browser_unavailable", "reason": reason}
    assert "SECRET" not in str(browser.prepare("ScienceDirect"))


class Page:
    def __init__(self, *, current=URL, failure=None, **info):
        self.url, self.failure, self.visits = current, failure, []
        self.info = dict(title="Article", doi="10.1038/s41586-026-00001-1", pdf="",
                         login=False, verification=False, denied=False, paywall=False,
                         full=False, text="", network_error="") | info

    def is_closed(self): return False
    def set_default_timeout(self, value): pass
    def on(self, *args): pass
    def remove_listener(self, *args): pass
    def wait_for_timeout(self, *args): pass
    def goto(self, target, **kwargs):
        self.visits.append(target)
        if self.failure: raise self.failure
        return SimpleNamespace(status=200)
    def evaluate(self, *args): return dict(self.info)


def adapter(page):
    backend = PlaywrightInstitutionalBrowser("http://127.0.0.1:9222")
    backend._connect = lambda: SimpleNamespace(new_page=lambda: page)
    return backend


def test_missing_landing_identity_reports_pre_download_stage():
    page = Page(current='https://ieeexplore.ieee.org/document/8654603/', doi='',
                pdf='https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=8654603&secret=never-log')
    original = page.evaluate
    def evaluate(script, *args):
        assert not args, 'unconfirmed landing page must not fetch PDF bytes'
        return original(script)
    page.evaluate = evaluate
    result = adapter(page).fetch('https://doi.org/10.1109/ACCESS.2019.2902267')
    assert result == {'state': 'identity_unconfirmed', 'reason': 'article_identity_unconfirmed'}
    assert 'never-log' not in str(result)


@pytest.mark.parametrize("failure,reason", [
    (RuntimeError("net::ERR_NETWORK_CHANGED at https://school.example/?SAMLResponse=SECRET"), "network_changed"),
    (RuntimeError("net::ERR_NAME_NOT_RESOLVED"), "dns_failed"),
    (RuntimeError("net::ERR_CONNECTION_RESET"), "connection_failed"),
    (TimeoutError("Page.goto: Timeout 15000ms exceeded"), "navigation_timeout"),
    (RuntimeError("net::ERR_BLOCKED_BY_CLIENT"), "request_blocked"),
])
def test_failed_navigation_does_not_read_stale_article_and_retries(failure, reason):
    page = Page(failure=failure, full=True, text="Stale previous article " * 200)
    browser = adapter(page)
    result = browser.fetch(URL)
    assert result == {"state": "fetch_unavailable", "reason": reason}
    assert "SECRET" not in str(result)
    page.failure = None
    assert browser.fetch(URL)["state"] == "available"
    assert len(page.visits) == (2 if reason == "request_blocked" else 3)


def test_transient_navigation_is_retried_once_in_the_same_check():
    page = Page(full=True, text="Verified article " * 200)
    original = page.goto
    def goto(target, **kwargs):
        page.failure = RuntimeError("net::ERR_NETWORK_CHANGED") if not page.visits else None
        return original(target, **kwargs)
    page.goto = goto
    assert adapter(page).fetch(URL)["state"] == "available"
    assert page.visits == [URL, URL]


def test_navigation_timeout_does_not_restart_an_arrived_login_page():
    page = Page(current="https://school.example/login", login=True,
                failure=TimeoutError("navigation timed out"))
    browser = adapter(page)
    assert browser.fetch(URL)["state"] == "login_required"
    assert browser.fetch(URL)["state"] == "login_required"
    assert len(page.visits) == 1


def test_delayed_bridge_is_not_accepted_as_an_article_or_reloaded():
    from hypoloom.institutional_access import publisher
    doi = "https://doi.org/10.1016/j.fixture.2026.001"
    bridge = "https://linkinghub.elsevier.com/retrieve/pii/1234"
    final = "https://www.sciencedirect.com/science/article/pii/1234"
    page = Page(current=bridge, doi="10.1016/j.fixture.2026.001", full=True, text="Verified article " * 200)
    waits = []
    def advance(delay):
        waits.append(delay)
        if len(waits) == 3:
            page.url = final
    page.wait_for_timeout = advance
    assert publisher(bridge) is None, "bridges are transit, not an article admission rule"
    result = adapter(page).fetch(doi)
    assert result["state"] == "available" and result["document"]["url"] == final
    assert page.visits == [doi] and waits == [500] * 3


def test_stuck_doi_resolver_is_retryable_even_with_copied_article_metadata():
    doi = "https://doi.org/10.1038/s41586-026-00001-1"
    page = Page(current=doi, full=True, text="Do not admit resolver content " * 200)
    waits = []
    page.wait_for_timeout = waits.append
    assert adapter(page).fetch(doi) == {"state": "fetch_unavailable", "reason": "redirect_unresolved"}
    assert waits == [500] * 16 and page.visits == [doi]


def test_redirect_destination_waits_for_its_own_dom_before_identity_check():
    page = Page(loading=True, doi="")
    def finish_document(delay):
        page.info.update(loading=False, doi="10.1038/s41586-026-00001-1",
                         full=True, text="Verified article " * 200)
    page.wait_for_timeout = finish_document
    assert adapter(page).fetch("https://doi.org/10.1038/s41586-026-00001-1")["state"] == "available"
    assert len(page.visits) == 1


@pytest.mark.parametrize("timed_out", [False, True])
def test_stalled_elsevier_bridge_uses_pii_destination_without_restarting_doi(timed_out):
    doi = "https://doi.org/10.1016/j.chb.2014.04.002"
    bridge = "https://linkinghub.elsevier.com/retrieve/pii/S0747563214002118"
    final = "https://www.sciencedirect.com/science/article/pii/S0747563214002118"
    page = Page(current="about:blank", doi="10.1016/j.chb.2014.04.002", full=True, text="Verified article " * 200)
    budgets = []
    def goto(target, **kwargs):
        page.visits.append(target)
        budgets.append(kwargs["timeout"])
        page.url = bridge if target == doi else target
        if target == doi and timed_out:
            raise TimeoutError("bridge did not finish loading")
        return SimpleNamespace(status=200)
    page.goto = goto
    result = adapter(page).fetch(doi)
    assert result["state"] == "available" and result["document"]["url"] == final
    assert page.visits == [doi, final] and budgets == [25000, 25000]


def test_redirecting_shell_on_article_url_never_becomes_terminal_no_full_text():
    url = "https://www.sciencedirect.com/science/article/pii/S0747563214002118"
    page = Page(current=url, doi="10.1016/j.chb.2014.04.002", redirecting=True, title="Redirecting")
    assert adapter(page).fetch(url) == {"state": "fetch_unavailable", "reason": "redirect_unresolved"}


@pytest.mark.parametrize("bridge", [
    "https://linkinghub.elsevier.com/retrieve/pii/S0747563214002118",
    "https://linkinghub.elsevier.com/retrieve/pii/S0747563214002118?token=SECRET",
])
def test_bridge_login_is_never_bypassed(bridge):
    page = Page(current=bridge, login=True, doi="")
    browser = adapter(page)
    doi = "https://doi.org/10.1016/j.chb.2014.04.002"
    assert browser.fetch(doi)["state"] == "login_required"
    assert browser.fetch(doi)["state"] == "login_required"
    assert page.visits == [doi]


@pytest.mark.parametrize("url", [
    "http://linkinghub.elsevier.com/retrieve/pii/S0747563214002118",
    "https://linkinghub.elsevier.com.evil.example/retrieve/pii/S0747563214002118",
    "https://user:secret@linkinghub.elsevier.com/retrieve/pii/S0747563214002118",
    "https://linkinghub.elsevier.com:8443/retrieve/pii/S0747563214002118",
    "https://linkinghub.elsevier.com/login/S0747563214002118",
    "https://linkinghub.elsevier.com/retrieve/pii/invalid",
    "https://linkinghub.elsevier.com/retrieve/pii/S0747563214002118/extra",
])
def test_pii_rewrite_rejects_unrelated_or_malformed_bridge_urls(url):
    from hypoloom.institutional_browser import _elsevier_article_url
    assert _elsevier_article_url(url) is None


def test_pii_rewrite_drops_query_and_does_not_prove_article_identity():
    from hypoloom.institutional_browser import _elsevier_article_url
    bridge = "https://linkinghub.elsevier.com/retrieve/pii/S0747563214002118?token=SECRET#private"
    final = "https://www.sciencedirect.com/science/article/pii/S0747563214002118"
    assert _elsevier_article_url(bridge) == final
    page = Page(current=bridge, doi="10.1016/wrong-article", full=True, text="Wrong article " * 200)
    def goto(target, **kwargs):
        page.visits.append(target)
        if target == final:
            page.url = final
        return SimpleNamespace(status=200)
    page.goto = goto
    assert adapter(page).fetch("https://doi.org/10.1016/j.chb.2014.04.002") == {
        "state": "identity_unconfirmed", "reason": "article_identity_unconfirmed"}


def test_bridge_js_racing_direct_navigation_is_followed_without_reloading():
    doi = "https://doi.org/10.1016/j.chb.2014.04.002"
    bridge = "https://linkinghub.elsevier.com/retrieve/pii/S0747563214002118"
    final = "https://www.sciencedirect.com/science/article/pii/S0747563214002118"
    page = Page(current=bridge, doi="10.1016/j.chb.2014.04.002", full=True, text="Verified article " * 200)
    def goto(target, **kwargs):
        page.visits.append(target)
        if target == final:
            page.url = final
            raise RuntimeError("net::ERR_ABORTED: navigation replaced by the site's redirect")
        return SimpleNamespace(status=200)
    page.goto = goto
    result = adapter(page).fetch(doi)
    assert result["state"] == "available"
    assert page.visits == [doi, final]


@pytest.mark.parametrize("current,info,expected", [
    ("chrome-error://chromewebdata/", {}, "fetch_unavailable"),
    ("https://linkinghub.elsevier.com/retrieve/pii/1234", {}, "fetch_unavailable"),
    ("https://idp.school.example/idp/profile/SAML2/Redirect/SSO?secret=never-log", {}, "login_required"),
    ("https://school.example/session", {"login": True}, "login_required"),
    (URL, {"network_error": "ERR_NETWORK_CHANGED", "paywall": True}, "fetch_unavailable"),
    (URL, {"paywall": True}, "access_required"),
    (URL.replace("00001-1", "99999-9"), {}, "identity_unconfirmed"),
])
def test_non_publisher_or_paywall_does_not_by_itself_prove_login(current, info, expected):
    assert adapter(Page(current=current, **info)).fetch(URL)["state"] == expected
