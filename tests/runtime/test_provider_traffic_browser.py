"""Real browser RPM ownership, live animation, stale state and accessibility."""
import json

from test_observer_browser_ui import browser
from test_observer_scope import record
from test_observer_summary import _summary


def test_rpm_cards_grounding_and_run_refresh_without_double_counting(browser, tmp_path):
    page = browser
    assert page.text('#run-rpm') == '—'
    assert '?/?' in page.text('#grounding-agent-card .reliability-preview')
    rows = [record('g-failed', 'GROUNDING', kind='GROUNDING', command_id='g', reason='FAILED'),
        record('g-done', 'GROUNDING', kind='GROUNDING', command_id='g', start=30),
        record('g-reader', 'PAPER_READ', kind='PAPER_READ', parent='g-done'),
        record('observer', 'OBSERVER', kind='OBSERVER'),
        record('other-reader', 'PAPER_READ', kind='PAPER_READ', parent='observer'),
        record('expert', 'RESULT_ANALYSIS')]
    for row in rows:
        row['attempt_ordinal'] = 2 if row['attempt_id'] == 'g-done' else 1
    summary = _summary(tmp_path, attempts=rows)
    page.evaluate('fixture.snapshot.overview.reliability=' + json.dumps(summary['reliability']))
    page.evaluate("""fixture.snapshot.status.run_state = 'ACTIVE';
      fixture.snapshot.overview.host_observation = {availability:'responded'};
      fixture.snapshot.status.provider_traffic = {
        scope:'run', window_seconds:60, observed_at:fixture.snapshot.observed_at,
        rpm:9, in_flight:1, queued:2, warming_up:false,
        by_attempt:{'live-rpm':{rpm:4,in_flight:1,queued:2},'ground-rpm':{rpm:3,in_flight:0,queued:0}},
        by_expert:{'expert-a':{rpm:4,in_flight:1,queued:2}},
        grounding:{rpm:3,in_flight:0,queued:0}, other:{rpm:2,in_flight:0,queued:0}};
      fixture.snapshot.attempts.push({attempt_id:'live-rpm',kind:'AGENT',state:'OPEN',
        role:'expert-a',task_scope:'expert',owner_role:'expert-a',command:'LITERATURE_REVIEW',
        started_at:fixture.snapshot.observed_at-10});
      fixture.snapshot.attempts.push({attempt_id:'ground-rpm',kind:'AGENT',state:'OPEN',task_scope:'run',command:'GROUNDING'});
      fixture.snapshot.overview.grounding={state:'active',attempt_ids:['ground-rpm'],active_readers:0,elapsed_seconds:10,accounting:{reported_tokens:21},activity:[]};""")
    page.evaluate('fixture.snapshot.overview.grounding.reliability=' + json.dumps(summary['grounding']['reliability']))
    page.wait("document.querySelector('#run-rpm').textContent === '9'")
    page.wait("document.querySelector('#grounding-agent-card [data-preview-metric=node_delivery]').textContent.includes('2/2')")
    assert '1/2' in page.text('#grounding-agent-card [data-preview-metric=node_retry]')
    assert '0/0' in page.text('#grounding-agent-card [data-preview-metric=output_first_pass]')
    assert '?3' in page.text('#grounding-agent-card [data-preview-metric=output_first_pass]')
    assert page.text('#grounding-rpm') == '3'
    assert page.text('.expert-rpm-value') == '4'
    assert 'in flight 1 · queued 2' in page.evaluate("document.querySelector('.expert-rpm-value').title")
    period = page.evaluate("getComputedStyle(document.querySelector('.expert-preview .breath-dot')).animationDuration")
    assert period == '2.025s'
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=live-rpm] .agent-tile-light')).animationDuration") == '1.5s'
    assert page.text('#grounding-state') == 'RUN'
    assert page.text('[data-attempt=live-rpm] .agent-tile-rpm') == '4'
    page.evaluate("document.querySelector('#grounding-agent-panel').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / 'grounding-card.png')
    page.evaluate("fixture.snapshot.status.provider_traffic.by_expert['expert-a'].rpm=16; fixture.snapshot.status.provider_traffic.rpm=21")
    page.wait("document.querySelector('.expert-rpm-value').textContent === '16'")
    assert page.evaluate("getComputedStyle(document.querySelector('.expert-preview .breath-dot')).animationDuration") == '1.0125s'
    page.evaluate("document.querySelector('#filter-expert').value='expert-a'; document.querySelector('#filter-expert').dispatchEvent(new Event('change'))")
    assert page.text('#run-rpm') == '21' and page.text('#grounding-rpm') == '3'
    page.click('#language-toggle')
    assert page.text('#run-rpm') == '21' and page.text('.expert-rpm-value') == '16'
    assert page.text('#grounding-state') == 'RUN'
    assert '仅本运行' in page.evaluate("document.querySelector('#run-rpm').title")
    assert '最终交付' in page.evaluate("document.querySelector('#grounding-agent-card [data-preview-metric=node_delivery]').title")
    assert '2/2' in page.text('#grounding-agent-card [data-preview-metric=node_delivery]')
    page.call('Emulation.setEmulatedMedia', {'features':[{'name':'prefers-reduced-motion','value':'reduce'}]})
    assert page.evaluate("getComputedStyle(document.querySelector('.expert-preview .breath-dot')).animationName") == 'none'
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=live-rpm] .agent-tile-light')).animationName") == 'none'
    page.call('Emulation.setEmulatedMedia', {'features':[]})
    page.call('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(tmp_path / 'rpm-mobile.png')
    page.evaluate("fixture.snapshot.status.provider_traffic.by_expert['expert-a'].rpm=0; fixture.snapshot.status.provider_traffic.by_attempt['live-rpm'].rpm=0")
    page.wait("document.querySelector('.expert-rpm-value').textContent === '0'")
    assert page.evaluate("getComputedStyle(document.querySelector('.expert-preview .breath-dot')).animationName") == 'none'
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=live-rpm] .agent-tile-light')).animationName") == 'none'
    page.evaluate("fixture.snapshot.status.provider_traffic.warming_up=true")
    page.wait("document.querySelector('#run-rpm').textContent === '—'")
    page.evaluate("fixture.snapshot.status.provider_traffic.warming_up=false; fixture.snapshot.overview.host_observation.availability='absent'")
    page.wait("document.querySelector('.expert-preview .breath-dot').classList.contains('host-unconfirmed')")
    assert page.text('#grounding-rpm') == page.text('.expert-rpm-value') == '—'
    assert page.text('#grounding-state') == 'OPEN?'
    assert page.evaluate("document.querySelector('#grounding-pulse').classList.contains('host-unconfirmed')")
    page.evaluate("fixture.snapshot.overview.host_observation.availability='responded'")
    page.wait("document.querySelector('#grounding-pulse').classList.contains('host-confirmed')")
    assert page.evaluate("getComputedStyle(document.querySelector('#grounding-pulse')).animationName") == 'breath'
    page.evaluate("""fixture.snapshot.overview.grounding.state='completed';
      fixture.snapshot.overview.grounding_sharing={state:'accepted',source_attempt_id:'ground-rpm',experts:[]};
      fixture.snapshot.attempts.find(a=>a.attempt_id==='ground-rpm').state='CLOSED';
      document.querySelector('#grounding-agent-card details').open=false;""")
    page.wait("document.querySelector('#grounding-state').textContent === 'DONE'")
    assert not page.evaluate("document.querySelector('#grounding-agent-card details').open")
    assert page.evaluate("document.querySelector('#grounding-agent-card .grounding-sharing') !== null")
    assert not page.evaluate("document.querySelector('#grounding-pulse') !== null")
    assert '2/2' in page.text('#grounding-agent-card [data-preview-metric=node_delivery]')
    page.evaluate("delete fixture.snapshot.status.provider_traffic")
    page.wait("document.querySelector('#run-rpm').textContent === '—'")
    page.click('#language-toggle')
    page.click('#grounding-agent-card [data-preview-metric=node_delivery]')
    page.wait("document.querySelector('#reliability-run_group')?.value === 'grounding'")
    assert page.evaluate("document.querySelector('#reliability-task_scope').value") == 'run'
    assert page.evaluate("document.querySelectorAll('.reliability-node').length") == 2
    assert page.evaluate("document.querySelector('#reliability-groups').children.length") == 2
    assert 'other-reader' not in page.text('#reliability-nodes')
    assert 'g-reader' in page.text('#reliability-nodes')
    page.click('#run-reliability-preview [data-preview-metric=node_delivery]')
    page.wait("document.querySelector('#reliability-run_group')?.value === ''")
    assert page.evaluate("document.querySelectorAll('.reliability-node').length") == 5


def test_stale_poll_stops_rpm_animation_and_clears_live_values(browser):
    page = browser
    page.evaluate("""fixture.snapshot.overview.host_observation={availability:'responded'};
      fixture.snapshot.attempts.push({attempt_id:'stale-rpm',kind:'AGENT',state:'OPEN',
        role:'expert-a',task_scope:'expert',owner_role:'expert-a',command:'LITERATURE_REVIEW'});
      fixture.snapshot.status.provider_traffic={scope:'run',observed_at:fixture.snapshot.observed_at,
        rpm:2,in_flight:0,queued:0,by_expert:{'expert-a':{rpm:1}},grounding:{rpm:1},
        by_attempt:{'stale-rpm':{rpm:1,in_flight:0,queued:0}}};""")
    page.wait("document.querySelector('#run-rpm').textContent === '2'")
    page.evaluate("window.fetch = async () => { throw new Error('fixture offline'); }")
    page.wait("document.querySelector('#connection').dataset.state === 'stale'")
    assert page.text('#run-rpm') == page.text('#grounding-rpm') == '—'
    assert page.evaluate("document.documentElement.dataset.rpmFresh") == 'false'
    assert page.text('[data-attempt=stale-rpm] .agent-tile-rpm') == '—'
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=stale-rpm] .agent-tile-light')).animationName") == 'none'
