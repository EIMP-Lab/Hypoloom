"""Institutional full-text panel rendering and scoped action transport in a real browser."""
import pytest
from test_observer_browser_ui import browser as browser


def pending(page):
    page.evaluate("""Object.assign(fixture.snapshot.status, {run_state:'ACTIVE', literature_access:{
        enabled:true, pending:2, available:0, groups:[{provider:'Nature', papers:[
            {url:'https://www.nature.com/articles/s41586-026-00001-1', title:'A verified scholarly method', state:'login_required'},
            {url:'https://www.nature.com/articles/s41586-026-00002-2', title:'Second paper', state:'queued'}]}]}})""")
    page.wait("document.querySelector('#run-state').textContent.includes('机构全文 2 篇待处理')")
    page.click("#tab-workbench")
    page.click('[data-workbench="institutional"]')
    page.wait("document.querySelector('#workbench-list').textContent.includes('A verified scholarly method')")


def test_reader_sees_pending_panel_without_action_grant(browser):
    page = browser
    pending(page)
    assert "需要浏览器登录" in page.text("#workbench-list")
    assert "只读列表" in page.text("#workbench-list")
    assert page.evaluate("Array.from(document.querySelectorAll('#workbench-list button')).every(b => b.disabled)")
    assert page.evaluate("fixtureRequests.every(r => r[0] === 'GET')")


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
    page.wait("document.querySelector('#workbench-status').textContent.includes('已请求打开连接的浏览器标签页')")
    assert page.evaluate("JSON.stringify(literatureCalls[0].body) === JSON.stringify({provider:'Nature',action:'open'})")
    assert page.evaluate("literatureCalls[0].headers['X-Hypoloom-Literature-Token'] === 'fixture-literature-token'")
    assert page.evaluate("literatureCalls[0].headers.Authorization === 'Bearer browser-fixture-token'")
    # Import without an absolute path reports the requirement and posts nothing further.
    page.evaluate("""(() => {
      const input = document.querySelector('#workbench-list .institutional-import-path');
      input.value = 'relative.pdf'; input.dispatchEvent(new Event('input'));
    })()""")
    page.click("#workbench-list .institutional-import button")
    page.wait("document.querySelector('#workbench-status').textContent.includes('导入需要服务器上的 PDF 绝对路径')")
    assert page.evaluate("literatureCalls.length === 1")
    page.evaluate("""(() => {
      const input = document.querySelector('#workbench-list .institutional-import-path');
      input.value = '/home/user/paper.pdf'; input.dispatchEvent(new Event('input'));
    })()""")
    page.click("#workbench-list .institutional-import button")
    page.wait("document.querySelector('#workbench-status').textContent.includes('已安排导入PDF')")
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
    assert '加载失败，等待自动重试' in page.text('#workbench-list')
    assert '网络发生变化（network_changed）' in page.text('#workbench-list')
    assert '下次检查约120秒后' in page.text('#workbench-list')
    page.evaluate("fixture.snapshot.status.literature_access.groups[0].papers[0].reason_code = 'pdf_response_not_pdf'")
    page.wait("document.querySelector('#workbench-list').textContent.includes('PDF入口返回非PDF内容')")
    assert '上次检查：—' not in page.text('#workbench-list')
    page.wait("document.querySelector('#run-state').textContent.includes('BUDGET LIMIT')")
    assert 'error_max_budget_usd' in page.text('#run-state')
    assert page.evaluate("document.querySelector('#run-state').classList.contains('blocked')")
    assert page.evaluate("(() => { const el = document.querySelector('#run-state'); return el.scrollWidth <= el.clientWidth; })()"), 'failure code must not be clipped by the HUD'
    page.screenshot(tmp_path / 'literature-diagnostics.png')


def test_server_browser_guide_uses_temporary_local_forwarding(browser):
    page = browser
    pending(page)
    page.evaluate("Object.assign(fixture.snapshot.status.literature_access, {browser_endpoint:'http://127.0.0.1:9222', browser_ui_url:'http://127.0.0.1:5800'})")
    page.wait("document.querySelector('.institutional-guide').textContent.includes('服务器浏览器')")
    guide = page.text('.institutional-guide')
    assert '关闭笔记本' in guide and '-L 127.0.0.1:5800:127.0.0.1:5800' in guide
    assert 'chrome.exe' not in guide and ' -R ' not in guide
    assert page.evaluate("document.querySelector('.institutional-guide a').href === 'http://127.0.0.1:5800/'")
