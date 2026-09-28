"""Actual browser status, scoped recovery and ambiguous-delivery behavior."""
import pytest
from test_observer_browser_ui import browser as browser


def unavailable(page):
    page.evaluate("Object.assign(fixture.snapshot.status, {run_state:'ACTIVE',provider_retry:{state:'waiting_operator',pending:true,reason:'quota_exhausted',requires_operator:true}})")
    page.wait("document.querySelector('#provider-title').textContent.includes('waiting for operator')")


def test_reader_sees_quota_wait_without_write_access(browser):
    unavailable(browser)
    assert "Quota exhausted" in browser.text("#provider-description")
    assert browser.evaluate("document.querySelector('#provider-controls').hidden")
    assert browser.evaluate("fixtureRequests.every(r => r[0] === 'GET')")
    browser.evaluate("delete fixture.snapshot.status.provider_retry")
    browser.wait("document.querySelector('#provider-title').textContent.includes('unknown')")
    assert "cannot be confirmed" in browser.text("#provider-description")


@pytest.mark.parametrize("browser", ["provider"], indirect=True)
def test_operator_switch_retries_same_request_and_does_not_claim_recovered(browser):
    page = browser
    unavailable(page)
    page.evaluate("""(() => {
      const previous = window.fetch; window.providerCalls = [];
      window.fetch = async (path, options={}) => {
        if (path !== '/api/provider-retry') return previous(path, options);
        providerCalls.push({body:JSON.parse(options.body),headers:options.headers});
        if (providerCalls.length === 1) throw new Error('lost reply');
        fixture.snapshot.status.provider_retry = {state:'recovering',pending:false};
        return new Response(JSON.stringify({status:'accepted',revision:6}), {status:200});
      };
      document.querySelector('#provider-settings').value = '/operator/connection.json';
    })()""")
    page.click("#provider-switch")
    page.wait("document.querySelector('#provider-retry').textContent === 'Retry same request'")
    assert page.evaluate("document.querySelector('#provider-settings').disabled")
    page.click("#provider-retry")
    page.wait("document.querySelector('#provider-operation').textContent.includes('Recovery requested')")
    assert page.evaluate("providerCalls.length === 2 && JSON.stringify(providerCalls[0].body) === JSON.stringify(providerCalls[1].body)")
    assert page.evaluate("providerCalls[0].headers['X-Hypoloom-Provider-Token'] === 'fixture-provider-token'")
    assert page.evaluate("providerCalls[0].body.settings_path === '/operator/connection.json'")
    page.wait("document.querySelector('#provider-title').textContent === 'Trying API recovery'")
    assert "not yet confirmed" in page.text("#provider-description")
    page.call("Emulation.setDeviceMetricsOverride", {"width":390,"height":844,"deviceScaleFactor":1,"mobile":False})
    assert page.evaluate("document.querySelector('#provider-panel').scrollWidth <= document.querySelector('#provider-panel').clientWidth")
    page.evaluate("fixture.snapshot.status.provider_retry = {state:'ready',pending:false}")
    page.wait("document.querySelector('#provider-title').textContent === 'API dispatch available'")


@pytest.mark.parametrize("browser", ["provider"], indirect=True)
def test_gpu_recheck_is_separate_and_retries_exact_request(browser):
    page = browser
    page.evaluate("Object.assign(fixture.snapshot.status, {run_state:'ACTIVE',provider_retry:{state:'ready',pending:false},resource_blocked:[{role:'expert-a',code:'incompatible_pool'}],process_activity:[{experiment_id:'cpu-active',process_alive:true,resource_grant:{kind:'CPU'},logs:[],progress:'unknown'}]})")
    page.wait("!document.querySelector('#resource-recheck').hidden")
    assert "incompatible_pool" in page.text("#resource-blocked")
    page.wait("document.querySelector('#resource-wait-list').textContent.includes('scientific progress') || document.querySelector('#resource-wait-list').textContent.includes('Scientific progress')")
    assert "CPU" in page.text("#resource-wait-list") and "no output observed" in page.text("#resource-wait-list")
    page.evaluate("""(() => {
      const original = window.fetch; window.resourceCalls = [];
      window.fetch = async (path, options={}) => {
        if (path !== '/api/resource-recheck') return original(path, options);
        resourceCalls.push({body:JSON.parse(options.body),headers:options.headers});
        if (resourceCalls.length === 1) throw new Error('reply lost');
        fixture.snapshot.status.resource_blocked = [];
        return new Response(JSON.stringify({status:'accepted',revision:9}), {status:200});
      };
    })()""")
    page.click("#resource-recheck")
    page.wait("document.querySelector('#resource-recheck').textContent.includes('Retry same')")
    page.click("#resource-recheck")
    page.wait("document.querySelector('#resource-operation').textContent.includes('rechecked')")
    assert page.evaluate("resourceCalls.length === 2 && JSON.stringify(resourceCalls[0].body) === JSON.stringify(resourceCalls[1].body)")
    assert page.evaluate("resourceCalls[0].headers['X-Hypoloom-Provider-Token'] === 'fixture-provider-token'")
    assert page.evaluate("Object.keys(resourceCalls[0].body).join() === 'request_id'")
