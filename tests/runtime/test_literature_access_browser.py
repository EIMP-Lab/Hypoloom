"""Institutional full-text panel rendering and scoped action transport in a real browser."""
import pytest
from test_observer_browser_ui import browser as browser


def pending(page):
    assert page.evaluate("document.documentElement.lang === 'en'")
    page.evaluate("""Object.assign(fixture.snapshot.status, {run_state:'ACTIVE', literature_access:{
        enabled:true, pending:2, available:0, groups:[{provider:'Nature', mode:'auto', papers:[
            {url:'https://www.nature.com/articles/s41586-026-00001-1', title:'A verified scholarly method', state:'login_required'},
            {url:'https://www.nature.com/articles/s41586-026-00002-2', title:'Second paper', state:'queued'}]}]}})""")
    page.wait("document.querySelector('#run-state').textContent.includes('Institutional full text: 2 pending')")
    page.click("#tab-workbench")
    page.click('[data-workbench="institutional"]')
    page.wait("document.querySelector('#workbench-list').textContent.includes('A verified scholarly method')")


def test_reader_sees_pending_panel_without_action_grant(browser):
    page = browser
    pending(page)
    assert "Browser login required" in page.text("#workbench-list")
    assert "Read-only list" in page.text("#workbench-list")
    assert "including those not listed here" in page.text("#workbench-scope")
    assert "Excludes public downloads and history" in page.text("#workbench-status")
    assert "Institutional route: Allowed" in page.text("#workbench-list")
    assert page.evaluate("Array.from(document.querySelectorAll('#workbench-list button')).every(b => b.disabled)")
    page.evaluate("fixture.snapshot.status.literature_access = {enabled: false}")
    page.wait("document.querySelector('#workbench-list').textContent.includes('Institutional browser not enabled')")
    assert "Public/OA retrieval remains available" in page.text("#workbench-list")
    page.evaluate("fixture.snapshot.status.literature_access = {}")
    page.wait("document.querySelector('#workbench-list').textContent.includes('Institutional status unknown')")
    assert page.evaluate("fixtureRequests.every(r => r[0] === 'GET')")


@pytest.mark.parametrize("browser", ["literature"], indirect=True)
def test_disabled_publisher_needs_explicit_enable_before_browser_actions(browser):
    page = browser
    pending(page)
    page.evaluate("""(() => {
      const original = window.fetch; window.modeCalls = [];
      window.fetch = async (path, options={}) => {
        if (path !== '/api/literature-access') return original(path, options);
        modeCalls.push(JSON.parse(options.body));
        return new Response(JSON.stringify({status:'accepted',revision:6}), {status:200});
      };
    })()""")
    page.evaluate("fixture.snapshot.status.literature_access.groups[0].mode = 'disabled'")
    page.wait("document.querySelector('#workbench-list').textContent.includes('Institutional route: Not enabled')")
    page.click('#language-toggle')
    page.wait("document.querySelector('#workbench-list').textContent.includes('机构渠道：未启用')")
    assert '所有出版社（含未列出的出版社）' in page.text('#workbench-scope')
    page.click('#language-toggle')
    page.wait("document.querySelector('#workbench-list').textContent.includes('Institutional route: Not enabled')")
    assert page.evaluate("[...document.querySelectorAll('.institutional-heading button')].slice(0, 2).every(b => b.disabled)")
    assert page.evaluate("[...document.querySelectorAll('#workbench-list button')].find(b => b.textContent === 'Verify sample').disabled")
    page.evaluate("[...document.querySelectorAll('.institutional-heading button')].find(b => b.textContent === 'Enable institutional access').click()")
    page.wait("modeCalls.length === 1")
    assert page.evaluate("modeCalls[0].provider === 'Nature' && modeCalls[0].action === 'mode' && modeCalls[0].mode === 'auto'")


@pytest.mark.parametrize("browser", ["literature"], indirect=True)
def test_operator_actions_post_clean_payload_with_literature_token(browser):
    page = browser
    pending(page)
    page.evaluate("""(() => {
      const previous = window.fetch; window.literatureCalls = [];
      window.fetch = async (path, options={}) => {
        if (path !== '/api/literature-access') return previous(path, options);
        literatureCalls.push({body:JSON.parse(options.body),headers:options.headers});
        fixture.snapshot.status.literature_access.groups[0].papers[0].state = 'checking';
        return new Response(JSON.stringify({status:'accepted',revision:6}), {status:200});
      };
    })()""")
    assert not page.evaluate("document.querySelector('#workbench-list .institutional-heading button').disabled")
    page.click("#workbench-list .institutional-heading button")
    page.wait("document.querySelector('#workbench-status').textContent.includes('Requested a tab in the connected browser')")
    assert page.evaluate("JSON.stringify(literatureCalls[0].body) === JSON.stringify({provider:'Nature',action:'open'})")
    assert page.evaluate("literatureCalls[0].headers['X-Hypoloom-Literature-Token'] === 'fixture-literature-token'")
    assert page.evaluate("literatureCalls[0].headers.Authorization === 'Bearer browser-fixture-token'")
    # Import without an absolute path reports the requirement and posts nothing further.
    page.evaluate("""(() => {
      const input = document.querySelector('#workbench-list .institutional-import-path');
      input.value = 'relative.pdf'; input.dispatchEvent(new Event('input'));
    })()""")
    page.click("#workbench-list .institutional-import button")
    page.wait("document.querySelector('#workbench-status').textContent.includes('Import requires an absolute server-side PDF path')")
    assert page.evaluate("literatureCalls.length === 1")
    page.evaluate("""(() => {
      const input = document.querySelector('#workbench-list .institutional-import-path');
      input.value = '/home/user/paper.pdf'; input.dispatchEvent(new Event('input'));
    })()""")
    page.click("#workbench-list .institutional-import button")
    page.wait("document.querySelector('#workbench-status').textContent.includes('PDF import scheduled')")
    assert page.evaluate("literatureCalls.length === 2")
    assert page.evaluate("literatureCalls[1].body.action === 'import' && literatureCalls[1].body.path === '/home/user/paper.pdf'")
    assert page.evaluate("literatureCalls[1].body.url === 'https://www.nature.com/articles/s41586-026-00001-1'")
    assert page.evaluate("!('password' in literatureCalls[0].body) && !('run_id' in literatureCalls[0].body)")


def test_network_reason_check_time_and_budget_failure_are_visible(browser, tmp_path):
    page = browser
    pending(page)
    page.evaluate("""(() => {
      Object.assign(fixture.snapshot.status.literature_access.groups[0].papers[0], {
        state:'fetch_unavailable', reason_code:'network_changed', retry_in_seconds:120,
        last_checked_at:'2026-09-28T12:00:00+00:00'});
      Object.assign(fixture.snapshot.attempts[0], {
        operational_status:'recovery_exhausted', operational_reason:'error_max_budget_usd'});
      fixture.snapshot.status.run_state = 'COMPLETED';
    })()""")
    page.wait("document.querySelector('#workbench-list').textContent.includes('network_changed')")
    assert 'Load failed; awaiting automatic retry' in page.text('#workbench-list')
    assert 'Network changed（network_changed）' in page.text('#workbench-list')
    assert 'Next check in about 120 seconds' in page.text('#workbench-list')
    page.evaluate("fixture.snapshot.status.literature_access.groups[0].papers[0].reason_code = 'pdf_response_not_pdf'")
    page.wait("document.querySelector('#workbench-list').textContent.includes('PDF endpoint returned non-PDF content')")
    assert 'Last checked: —' not in page.text('#workbench-list')
    page.wait("document.querySelector('#node-failure-list').textContent.includes('BUDGET LIMIT')")
    page.click('#node-failure-summary')
    assert 'error_max_budget_usd' in page.text('#node-failure-list')
    assert 'COMPLETED' in page.text('#run-state') and 'BUDGET LIMIT' not in page.text('#run-state')
    assert not page.evaluate("document.querySelector('#run-state').classList.contains('blocked')")
    assert page.evaluate("(() => { const el = document.querySelector('#node-failure-list'); return el.scrollWidth <= el.clientWidth; })()"), 'failure details must remain readable'
    page.screenshot(tmp_path / 'literature-diagnostics.png')


def test_server_browser_guide_uses_temporary_local_forwarding(browser):
    page = browser
    pending(page)
    page.evaluate("Object.assign(fixture.snapshot.status.literature_access, {browser_endpoint:'http://127.0.0.1:9222', browser_ui_url:'http://127.0.0.1:5800'})")
    page.wait("document.querySelector('.institutional-guide').textContent.includes('Server browser')")
    guide = page.text('.institutional-guide')
    assert 'close your laptop' in guide and '-L 127.0.0.1:5800:127.0.0.1:5800' in guide
    assert 'chrome.exe' not in guide and ' -R ' not in guide
    assert page.evaluate("document.querySelector('.institutional-guide a').href === 'http://127.0.0.1:5800/'")


def test_run_owned_connection_guide_never_suggests_fixed_remote_port(browser):
    page = browser
    pending(page)
    page.evaluate("fixture.snapshot.status.literature_access.connection = {mode:'client',state:'waiting_for_client'}")
    page.wait("document.querySelector('.institutional-guide').textContent.includes('Keep the Windows connector running')")
    guide = page.text('.institutional-guide')
    assert 'released when it ends' in guide and ' -R ' not in guide and '9222' not in guide
    page.evaluate("fixture.snapshot.status.literature_access.connection = {mode:'local',state:'allocated'}")
    page.wait("document.querySelector('.institutional-guide').textContent.includes('Linux desktop')")
    page.evaluate("fixture.snapshot.status.literature_access.connection = {mode:'server',state:'allocated'}")
    page.wait("document.querySelector('.institutional-guide').textContent.includes('--include-ui-credentials')")
    assert page.evaluate("fixtureRequests.every(r => r[0] === 'GET')")


@pytest.mark.parametrize("browser", ["literature"], indirect=True)
def test_preference_retry_keeps_identity_after_lost_reply_and_empty_catalog_can_verify(browser):
    page = browser
    pending(page)
    page.evaluate("""(() => {
      fixture.snapshot.status.run_revision = 42;
      fixture.snapshot.status.literature_access.groups[0].papers = [];
      window.choiceCalls = [];
      const original = window.fetch;
      window.fetch = async (path, options={}) => {
        if (path !== '/api/literature-access') return original(path, options);
        choiceCalls.push(JSON.parse(options.body));
        if (choiceCalls.length === 1) throw new Error('reply lost');
        return new Response(JSON.stringify({status:'accepted',revision:43}), {status:200});
      };
    })()""")
    page.wait("document.querySelectorAll('.institutional-paper').length === 0")
    page.evaluate("[...document.querySelectorAll('.institutional-heading button')].find(b => b.textContent === 'Public/OA only').click()")
    page.wait("document.querySelector('#workbench-status').textContent.includes('Host reply unconfirmed')")
    page.evaluate("[...document.querySelectorAll('.institutional-heading button')].find(b => b.textContent === 'Public/OA only').click()")
    page.wait("choiceCalls.length === 2")
    assert page.evaluate("JSON.stringify(choiceCalls[0]) === JSON.stringify(choiceCalls[1])")
    assert page.evaluate("choiceCalls[0].expected_revision === 42 && !!choiceCalls[0].request_id")
    page.evaluate("""(() => {
      const input = document.querySelector('.institutional-sample');
      input.value = '10.1038/s41586-026-00001-1'; input.dispatchEvent(new Event('input'));
      [...document.querySelectorAll('#workbench-list button')].find(b => b.textContent === 'Verify sample').click();
    })()""")
    page.wait("choiceCalls.length === 3")
    assert page.evaluate("choiceCalls[2].action === 'verify' && choiceCalls[2].url.startsWith('https://doi.org/')")
