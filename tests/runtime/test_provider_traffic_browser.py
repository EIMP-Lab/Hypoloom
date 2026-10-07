"""Real browser RPM ownership, live animation, stale state and accessibility."""
import json

import pytest

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
      fixture.snapshot.attempts.push({attempt_id:'rpm-process',kind:'PROCESS',state:'OPEN',
        role:'expert-a',task_scope:'expert',owner_role:'expert-a',command:'EXECUTE',started_at:fixture.snapshot.observed_at-540});
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
    assert period == '1.89s'
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=live-rpm] .agent-tile-light')).animationDuration") == '1.4s'
    assert page.text('#grounding-state') == 'RUN'
    assert page.evaluate("document.querySelector('[data-attempt=live-rpm] .agent-tile-metric svg') !== null")
    assert 'Request rate: 4 RPM' in page.evaluate("document.querySelector('[data-attempt=live-rpm] .agent-tile-metric').title")
    assert 'Request rate: 4 RPM' in page.evaluate("document.querySelector('[data-attempt=live-rpm]').getAttribute('aria-label')")
    assert 'RPM' not in page.text('[data-attempt=live-rpm]')
    assert page.evaluate("document.querySelector('[data-attempt=rpm-process] .agent-tile-metric svg') !== null")
    assert page.text('[data-attempt=rpm-process] .agent-tile-value') == '9m'
    assert 'elapsed 9m' in page.evaluate("document.querySelector('[data-attempt=rpm-process] .agent-tile-metric').title")
    page.wait("document.querySelectorAll('.agent-tile.tile-enter').length === 0")
    sizes = page.evaluate("['live-rpm','rpm-process','ground-rpm'].map(id=>{const r=document.querySelector(`[data-attempt='${id}']`).getBoundingClientRect();return [r.width,r.height]})")
    assert all(size == pytest.approx(sizes[0]) for size in sizes[1:])
    page.evaluate("document.querySelector('#grounding-agent-panel').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / 'grounding-card.png')
    page.evaluate("fixture.snapshot.status.provider_traffic.by_expert['expert-a'].rpm=16; fixture.snapshot.status.provider_traffic.rpm=21")
    page.wait("document.querySelector('.expert-rpm-value').textContent === '16'")
    assert page.evaluate("getComputedStyle(document.querySelector('.expert-preview .breath-dot')).animationDuration") == '1.485s'
    page.evaluate("document.querySelector('#filter-expert').value='expert-a'; document.querySelector('#filter-expert').dispatchEvent(new Event('change'))")
    assert page.text('#run-rpm') == '21' and page.text('#grounding-rpm') == '3'
    page.click('#language-toggle')
    assert page.text('#run-rpm') == '21' and page.text('.expert-rpm-value') == '16'
    assert page.text('#grounding-state') == 'RUN'
    assert '仅本运行' in page.evaluate("document.querySelector('#run-rpm').title")
    assert '请求速率：4 RPM' in page.evaluate("document.querySelector('[data-attempt=live-rpm] .agent-tile-metric').title")
    assert '耗时 9m' in page.evaluate("document.querySelector('[data-attempt=rpm-process] .agent-tile-metric').title")
    assert '最终交付' in page.evaluate("document.querySelector('#grounding-agent-card [data-preview-metric=node_delivery]').title")
    assert '2/2' in page.text('#grounding-agent-card [data-preview-metric=node_delivery]')
    page.call('Emulation.setEmulatedMedia', {'features':[{'name':'prefers-reduced-motion','value':'reduce'}]})
    assert page.evaluate("getComputedStyle(document.querySelector('.expert-preview .breath-dot')).animationName") == 'none'
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=live-rpm] .agent-tile-light')).animationName") == 'none'
    page.call('Emulation.setEmulatedMedia', {'features':[]})
    page.call('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    mobile_sizes = page.evaluate("['live-rpm','rpm-process'].map(id=>{const r=document.querySelector(`[data-attempt='${id}']`).getBoundingClientRect();return [r.width,r.height]})")
    assert mobile_sizes[0] == pytest.approx(mobile_sizes[1])
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
    assert page.evaluate("document.querySelector('[data-attempt=stale-rpm] .agent-tile-metric svg') !== null")
    assert 'Request rate: — RPM' in page.evaluate("document.querySelector('[data-attempt=stale-rpm] .agent-tile-metric').title")
    assert 'no fresh Host traffic observation' in page.evaluate("document.querySelector('[data-attempt=stale-rpm]').getAttribute('aria-label')")
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=stale-rpm] .agent-tile-light')).animationName") == 'none'


def test_pulse_phase_survives_poll_card_redraw_and_rpm_change(browser, tmp_path):
    page = browser
    page.evaluate("""fixture.snapshot.overview.host_observation={availability:'responded'};
      fixture.snapshot.attempts.push(
        {attempt_id:'phase-agent',kind:'AGENT',state:'OPEN',role:'expert-a',task_scope:'expert',owner_role:'expert-a',command:'LITERATURE_REVIEW'},
        {attempt_id:'phase-ground',kind:'AGENT',state:'OPEN',task_scope:'run',command:'GROUNDING'},
        {attempt_id:'phase-process',kind:'PROCESS',state:'OPEN',role:'expert-a',task_scope:'expert',owner_role:'expert-a'});
      fixture.snapshot.overview.grounding={state:'active',attempt_ids:['phase-ground'],elapsed_seconds:10,accounting:{reported_tokens:21}};
      fixture.snapshot.status.provider_traffic={scope:'run',observed_at:fixture.snapshot.observed_at,
        rpm:6,in_flight:2,queued:0,by_expert:{'expert-a':{rpm:3}},grounding:{rpm:3},
        by_attempt:{'phase-agent':{rpm:3},'phase-ground':{rpm:3}}};
      window.pulseSelectors=['.expert-preview .breath-dot','[data-attempt=phase-agent] .agent-tile-light',
        '#grounding-pulse','[data-attempt=phase-ground] .agent-tile-light'];""")
    page.wait("pulseSelectors.every(s => document.querySelector(s)?.getAnimations()[0]?.currentTime > 350)")
    page.evaluate("""window.pulseNodes=pulseSelectors.map(s=>document.querySelector(s));
      window.pulseAnimations=pulseNodes.map(e=>e.getAnimations()[0]);
      pulseAnimations.forEach(a=>{a.pause(); a.currentTime=a.effect.getComputedTiming().duration*.375;});
      window.readPulses=()=>pulseSelectors.map((s,i)=>{const e=document.querySelector(s),a=e.getAnimations()[0],c=getComputedStyle(e);
        return {sameNode:e===pulseNodes[i],sameAnimation:a===pulseAnimations[i],phase:a.effect.getComputedTiming().progress,
          duration:a.effect.getComputedTiming().duration,opacity:+c.opacity,width:parseFloat(c.width),shadow:c.boxShadow,transform:c.transform};});
      window.pollCount=fixtureRequests.filter(r=>r[1]==='/api/refresh').length;""")
    before = page.evaluate('readPulses()')
    # A real unchanged poll must not seek a paused animation to a wall-clock phase.
    page.wait("fixtureRequests.filter(r=>r[1]==='/api/refresh').length > pollCount")
    after_poll = page.evaluate('readPulses()')
    assert [p['phase'] for p in after_poll] == pytest.approx([p['phase'] for p in before])
    assert all(p['sameNode'] and p['sameAnimation'] for p in after_poll)

    # Changing card content must preserve all four lights, including main lights.
    page.evaluate("""fixture.snapshot.attempts.find(a=>a.attempt_id==='phase-agent').context_tokens=1234;
      fixture.snapshot.overview.grounding.elapsed_seconds=17;""")
    page.wait("document.querySelector('[data-attempt=phase-agent] .agent-tile-value').textContent === '1.23K' && document.querySelector('#grounding-agent-card').textContent.includes('17s')")
    after_redraw = page.evaluate('readPulses()')
    assert all(p['sameNode'] for p in after_redraw)
    assert [p['phase'] for p in after_redraw] == pytest.approx([p['phase'] for p in before])
    assert [p['opacity'] for p in after_redraw] == pytest.approx([p['opacity'] for p in before])
    assert [p['transform'] for p in after_redraw] == [p['transform'] for p in before]
    assert [p['shadow'] for p in after_redraw] == [p['shadow'] for p in before]
    # Chromium replaces CSSAnimation objects on reparenting. Check the visible
    # phase above and exactly one animation per light, not old object identity.
    assert page.evaluate('pulseNodes.every(e=>e.getAnimations().length===1)')
    page.evaluate('window.pulseAnimations=pulseNodes.map(e=>e.getAnimations()[0])')

    for rpm, child_ms in [(1, 2000), (4, 1400), (25, 1040), (100, 920)]:
        page.evaluate(f"""fixture.snapshot.status.provider_traffic.by_expert['expert-a'].rpm={rpm};
          fixture.snapshot.status.provider_traffic.grounding.rpm={rpm};
          fixture.snapshot.status.provider_traffic.rpm={rpm * 2};
          Object.values(fixture.snapshot.status.provider_traffic.by_attempt).forEach(c=>c.rpm={rpm});""")
        page.wait(f"document.querySelector('.expert-rpm-value').textContent === '{rpm}'")
        changed = page.evaluate('readPulses()')
        assert all(p['sameNode'] and p['sameAnimation'] for p in changed)
        assert [p['phase'] for p in changed] == pytest.approx([p['phase'] for p in before])
        assert [p['duration'] for p in changed] == pytest.approx([child_ms * 1.35, child_ms] * 2)

    # The user prefers the original small light and soft halo, without scaling.
    page.evaluate('pulseAnimations.forEach(a=>a.currentTime=0)')
    bright = page.evaluate('readPulses()')
    page.evaluate("document.querySelector('.expert-preview').scrollIntoView({block:'center'})")
    page.screenshot(tmp_path / 'pulse-bright.png')
    page.evaluate('pulseAnimations.forEach(a=>a.currentTime=a.effect.getComputedTiming().duration/2)')
    dim = page.evaluate('readPulses()')
    page.screenshot(tmp_path / 'pulse-dim.png')
    for index, (high, low) in enumerate(zip(bright, dim)):
        assert high['width'] == pytest.approx(9.9 if index % 2 == 0 else 8.1, abs=.02)
        assert high['opacity'] == pytest.approx(1) and low['opacity'] == pytest.approx(.35)
        assert high['transform'] == low['transform'] == 'none'
        assert high['shadow'] != low['shadow']
        assert high['shadow'].endswith('0px 0px 0px 0px')
    assert page.evaluate("getComputedStyle(document.querySelector('[data-attempt=phase-process] .agent-tile-light')).animationName") == 'none'

    # The browser still advances naturally after the diagnostic pause is removed.
    page.evaluate('pulseAnimations.forEach(a=>a.play())')
    page.wait('readPulses().every(p=>Math.abs(p.phase-.5)>.1)')
    running = page.evaluate('pulseNodes.map(e=>({start:e.getAnimations()[0].startTime,time:e.getAnimations()[0].currentTime}))')
    page.evaluate("""fixture.snapshot.attempts.find(a=>a.attempt_id==='phase-agent').context_tokens=2456;
      fixture.snapshot.overview.grounding.elapsed_seconds=24;""")
    page.wait("document.querySelector('[data-attempt=phase-agent] .agent-tile-value').textContent === '2.46K' && document.querySelector('#grounding-agent-card').textContent.includes('24s')")
    redrawn = page.evaluate('pulseNodes.map(e=>({start:e.getAnimations()[0].startTime,time:e.getAnimations()[0].currentTime}))')
    assert [p['start'] for p in redrawn] == pytest.approx([p['start'] for p in running])
    assert all(after['time'] > prior['time'] for after, prior in zip(redrawn, running))
    page.call('Emulation.setEmulatedMedia', {'features':[{'name':'prefers-reduced-motion','value':'reduce'}]})
    assert page.evaluate("pulseNodes.every(e=>e.getAnimations().length===0 && getComputedStyle(e).opacity==='1' && getComputedStyle(e).transform==='none')")
