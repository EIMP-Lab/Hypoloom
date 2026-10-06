import errno
import json
import socket

import pytest

from hypoloom import notification_tailer as notifier
from hypoloom.literature_recovery import events, points, reduce_availability, service_failure
from hypoloom.literature import LiteratureFetchError, LiteratureFailureCode
from test_notification_tailer import _mock_run, _notify, _event, _tailer, _cursor
from test_literature_workflow_host import mount_root as mount_root

ENV = {"TEST_BOT": "123:private-key", "TEST_CHAT": "private-recipient"}


def test_notification_outage_waits_then_recovers_without_cursor_loss(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(telegram=True), [_event()])
    now = [100.0]; calls = []
    def transport(*args, **kwargs):
        calls.append(now[0])
        if len(calls) == 1: raise socket.gaierror(-3, "private-key private-recipient")
        return notifier.HTTPResponse(200, b'{"ok":true}')
    tailer = _tailer(tmp_path, environ=ENV, transport=transport, clock=lambda: now[0])
    first = tailer.poll_once()
    assert first.health["telegram"]["failure"]["code"] == "dns_failed"
    assert first.health["telegram"]["failure"]["errno"] == -3
    assert not _cursor(tmp_path, "telegram").exists()
    assert "private-" not in str(first)
    now[0] = 129; tailer.poll_once(); assert calls == [100]
    now[0] = 130; recovered = tailer.poll_once()
    assert recovered.delivered["telegram"] == 1
    assert recovered.health["telegram"]["last_success_at"] == 130
    assert recovered.health["telegram"]["pending"] == 0
    assert tailer.poll_once().delivered["telegram"] == 0


def test_server_wait_and_manual_retry_are_separate(tmp_path, monkeypatch):
    _mock_run(monkeypatch, _notify(telegram=True), [_event()])
    now = [0.0]; calls = []
    responses = [notifier.HTTPResponse(429, b'{"ok":false,"parameters":{"retry_after":120}}'),
                 notifier.HTTPResponse(403, b'{"ok":false,"description":"Forbidden: bot was blocked by user"}'),
                 notifier.HTTPResponse(200, b'{"ok":true}')]
    def transport(*args, **kwargs): calls.append(1); return responses.pop(0)
    tailer = _tailer(tmp_path, environ=ENV, transport=transport, clock=lambda: now[0])
    assert tailer.poll_once().health['telegram']['next_retry_at'] == 120
    now[0] = 119; tailer.poll_once(); assert len(calls) == 1
    now[0] = 120
    failed = tailer.poll_once().health['telegram']
    assert failed['state'] == 'waiting_operator' and failed['failure']['code'] == 'bot_blocked'
    now[0] = 10000; tailer.poll_once(); assert len(calls) == 2
    tailer.retry(); assert tailer.health()['telegram']['state'] == 'recovering'
    assert tailer.poll_once().delivered['telegram'] == 1


def test_summary_keeps_human_events_and_advances_only_acknowledged_sources(tmp_path, monkeypatch):
    config = _notify(telegram=True)
    config['events']['telegram'].append('grounding_question')
    es = [_event(identity=f'p{i}') for i in range(8)] + [_event(identity='human', kind='grounding_question')]
    _mock_run(monkeypatch, config, es)
    sent = []
    def transport(url, doc, **kwargs): sent.append(doc['text']); return notifier.HTTPResponse(200, b'{"ok":true}')
    tailer = _tailer(tmp_path, environ=ENV, transport=transport)
    result = tailer.poll_once()
    assert len(sent) == 2 and result.delivered['telegram'] == 9
    assert sum('notification_backlog' in s for s in sent) == 1
    assert sum('grounding_question' in s for s in sent) == 1
    assert len(json.loads(_cursor(tmp_path,'telegram').read_text())) == 9
    assert _tailer(tmp_path, environ=ENV, transport=transport).poll_once().delivered['telegram'] == 0


def test_local_cursor_failure_is_not_mislabeled_as_network(tmp_path, monkeypatch):
    config = _notify(telegram=True); config['sinks'].pop('log')
    _mock_run(monkeypatch, config, [_event()])
    def failed_write(*args): raise OSError(errno.ENOSPC, 'private-data')
    monkeypatch.setattr(notifier, '_write_cursor', failed_write)
    t = _tailer(tmp_path, environ=ENV, transport=lambda *a, **k: notifier.HTTPResponse(200,b'{"ok":true}'))
    failure = t.poll_once().health['telegram']['failure']
    assert failure['phase'] == 'cursor_write' and failure['code'] == 'local_write_failed'
    assert failure['errno'] == errno.ENOSPC


@pytest.mark.parametrize('send,expected', [(False,['getMe','getChat']), (True,['getMe','getChat','sendMessage'])])
def test_explicit_probe_never_replays_run_events(send, expected):
    calls=[]
    def transport(url,*a,**k): calls.append(url.rsplit('/',1)[1]); return notifier.HTTPResponse(200,b'{"ok":true}')
    report=notifier.test_notification(_notify(telegram=True),environ=ENV,send=send,transport=transport)
    assert calls==expected and report['message_sent']==send and report['ok']
    assert 'private-' not in str(report)


def row(identity, start, end, available=False, reason='openalex_http'):
    value={'service':'openalex','available':available,'observed_at':end,'reason':reason,'http_status':500}
    obs={'request':{'role':'expert'}, 'preflight_diagnostics':{'literature_service':value}} if available else {
        'request':{'role':'expert'},'literature_service':value,'failure_stage':'literature_retrieval'}
    return {'attempt_id':identity,'started_at':start,'ended_at':end,'state':'CLOSED','observation':obs}


def test_literature_waves_slow_down_and_late_failures_do_not_undo_recovery():
    records=[row('a',1,5),row('b',2,6)]
    state=reduce_availability(points(records,[]),now=7)
    assert state['recovery_attempts']==1 and state['next_retry_at']==36
    records += [row('recovery',40,41,True),row('late',3,45)]
    state=reduce_availability(points(records,[]),now=46)
    assert state['state']=='ready' and state['recovered_at']==41
    records=[row(str(i),i*1000,i*1000+2) for i in range(6)]
    state=reduce_availability(points(records,[]),now=5003)
    assert state['state']=='cooldown' and state['next_retry_at']==5902
    assert reduce_availability(points(records,[]),now=99999)['state']=='recovering'
    clear={'command_id':'clear','payload':{'literature_retry_clear':{'cleared_at':100000}}}
    assert reduce_availability(points(records,[clear]),now=100001)['state']=='recovering'
    recorded=events('run',records,[clear])
    assert [e['type'] for e in recorded]==['literature_waiting','literature_retry_requested']


def test_literature_auth_waits_for_operator_and_query_errors_keep_normal_validation():
    state=reduce_availability(points([row('auth',1,2,reason='openalex_auth')],[]),now=3)
    assert state['requires_operator'] and state['next_retry_at'] is None
    assert service_failure(LiteratureFetchError('query',code=LiteratureFailureCode.QUERY_INVALID,http_status=400)) is None
    assert service_failure(LiteratureFetchError('address safety')) is None


def test_managed_notification_health_and_retry_are_visible_without_research_mutation(tmp_path):
    import io
    from hypoloom.observer_services import ObserverServices
    from hypoloom.workflow_host import WorkflowHost
    from hypoloom.control_protocol import ControlRequest
    from test_observer_services import configured
    from test_web_observer import _row_census
    root=configured(tmp_path,dashboard=False,telegram=True)
    calls=[]
    def transport(*args,**kwargs):
        calls.append(1)
        if len(calls)==1: raise OSError(errno.ENETUNREACH,'secret')
        return notifier.HTTPResponse(200,b'{"ok":true}')
    services=ObserverServices(root,output=io.StringIO(),errors=io.StringIO(),environ=ENV,transport=transport)
    host=WorkflowHost(root)
    try:
        host.initialize(); host.observer_services=services
        before=_row_census(root/'state.sqlite')
        services.poll_once()
        reply=host.handle(ControlRequest(1,'status',None,'STATUS',{'run_id':'notifications'}))
        health=reply.result['notifications']['sinks']['telegram']
        assert health['state']=='cooldown' and health['failure']['code']=='network_unreachable'
        assert health['pending']==1 and health['route']=='direct'
        assert host.handle(ControlRequest(1,'retry',None,'NOTIFICATION_RETRY',{'run_id':'notifications','sink':'telegram'})).status=='accepted'
        services.poll_once()
        assert services.notification_status('notifications')['sinks']['telegram']['state']=='healthy'
        assert _row_census(root/'state.sqlite')==before
    finally: services.close(); host.close()


@pytest.mark.parametrize('key,service,command,payload', [
    ('l','openalex','PROVIDER_RETRY_CLEAR',{'run_id':'resume-ui','reason':'operator','service':'openalex'}),
    ('n','telegram','NOTIFICATION_RETRY',{'run_id':'resume-ui','sink':'telegram'})])
def test_tui_dependency_retry_preserves_uncertain_request(tmp_path,monkeypatch,key,service,command,payload):
    import asyncio
    from dataclasses import replace
    from hypoloom.tui import HypoloomVNextMonitor
    import hypoloom.resource_recheck_tui as form
    from hypoloom.control_protocol import ControlResponse
    from test_provider_auth_cooldown import _resume_snapshot
    root,snapshot=_resume_snapshot(tmp_path)
    snapshot=replace(snapshot,status={**snapshot.status,'run_state':'ACTIVE',
        'literature_retry':{'state':'waiting_operator','reason':'openalex_auth'},
        'notifications':{'state':'observed','sinks':{'telegram':{'state':'waiting_operator','pending':1}}}})
    calls=[]
    def send(path,request,**kwargs):
        calls.append(request)
        if len(calls)==1: raise ConnectionError('uncertain')
        return ControlResponse.accepted(request.request_id,None,{})
    monkeypatch.setattr(form,'request_over_socket',send)
    async def exercise():
        app=HypoloomVNextMonitor(root,'resume-ui',initial_snapshot=snapshot,interval=3600,web_enabled=False)
        monkeypatch.setattr(app,'action_refresh',lambda:None)
        async with app.run_test(size=(100,40)) as pilot:
            await pilot.pause(); await pilot.press(key)
            assert isinstance(app.screen,form.ResourceRecheckScreen) and app.screen.service==service
            await pilot.click('#resource-confirm'); await pilot.pause(.3)
            await pilot.click('#resource-confirm'); await pilot.pause(.3)
            assert len(calls)==2 and calls[0]==calls[1]
            assert calls[0].command==command and calls[0].payload==payload
            await pilot.press('escape'); await pilot.pause()
    asyncio.run(exercise())


@pytest.mark.parametrize('restart', [False, True])
@pytest.mark.parametrize('code,status_code', [(LiteratureFailureCode.OPENALEX_HTTP,500), (LiteratureFailureCode.OPENALEX_AUTH,401)])
def test_real_host_service_failure_manual_retry_and_actual_recovery(tmp_path, mount_root, code, status_code, restart):
    import time
    from hypoloom.workflow_host import WorkflowHost
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.literature import FixedLiteratureIO
    from hypoloom.literature_recovery import status
    from hypoloom.oracle import Oracle
    from test_literature_workflow_host import _LiteratureFetcher, _payload, _drive
    fetcher = _LiteratureFetcher(fail=True,failure_code=code,http_status=status_code)
    host = WorkflowHost(tmp_path/'state',literature_io=FixedLiteratureIO(fetcher))
    run='service-recovery'
    try:
        host.initialize()
        assert host.handle(ControlRequest(1,'create',0,'CREATE_RUN',_payload(tmp_path,mount_root,run))).status=='accepted'
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            host.tick()
            if status(host.store,run)['pending']: break
            time.sleep(.005)
        observed=status(host.store,run)
        assert observed['pending'] and observed['recovery_attempts']==1
        query_count=len(fetcher.queries)
        for _ in range(5): host.tick()
        assert len(fetcher.queries)==query_count
        assert not host._expert_final_failures(run)
        if restart:
            root=host.state_root
            host.close()
            host=WorkflowHost(root,literature_io=FixedLiteratureIO(fetcher))
            host.initialize()
            assert status(host.store,run)==observed
            host.tick()
            assert len(fetcher.queries)==query_count
        fetcher.fail=False
        request=ControlRequest(1,'retry-service',None,'PROVIDER_RETRY_CLEAR',{'run_id':run,'reason':'operator','service':'openalex'})
        first=host.handle(request);second=host.handle(request)
        assert first.status==second.status=='accepted' and first.revision==second.revision
        assert status(host.store,run)['state']=='recovering'
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            host.tick()
            if host.store.list_facts(run,'HypothesisProposed'): break
            time.sleep(.005)
        assert status(host.store,run)['state']=='ready'
        assert host.store.list_facts(run,'HypothesisProposed'), 'recovery must reach the next accepted research action'
        from hypoloom.observation_api import build_product_observation
        kinds=[e['type'] for e in build_product_observation(host.state_root,run)['events']]
        assert kinds.count('literature_waiting')==1 and kinds.count('literature_recovered')==1
        assert host.handle(ControlRequest(1,'stop-test',None,'CANCEL',{'run_id':run,'cause':'operator'})).status=='accepted'
        _drive(host,run)
        assert host.store.latest_transition(run)['state']=='CANCELLED'
        assert all(a['state']=='CLOSED' for a in host.store.list_attempts())
        assert Oracle(host.state_root/'state.sqlite',host.state_root/'artifacts').audit(run).ok
    finally: host.close()


def test_summary_cannot_skip_an_unacknowledged_source_revision(tmp_path,monkeypatch):
    config=_notify(telegram=True)
    config['events']['telegram'].append('grounding_question')
    es=[_event(revision=i) for i in range(1,7)]+[_event(revision=7,kind='grounding_question')]
    _mock_run(monkeypatch,config,es)
    calls=[]
    def transport(url,doc,**kwargs):
        calls.append(doc['text'])
        if len(calls)==1: raise TimeoutError('private')
        return notifier.HTTPResponse(200,b'{"ok":true}')
    t=_tailer(tmp_path,environ=ENV,transport=transport)
    assert t.poll_once().health['telegram']['pending']==7
    assert len(calls)==1 and 'notification_backlog' in calls[0]
    assert not _cursor(tmp_path,'telegram').exists()
    t.retry()
    assert t.poll_once().delivered['telegram']==7
    assert len(calls)==3 and 'grounding_question' in calls[-1]


def test_openalex_long_retry_after_returns_to_host_without_worker_sleep():
    from dataclasses import replace
    from hypoloom.literature import OpenAlexLiteratureFetcher
    from test_literature import RecordingOpenAlexTransport,_query
    class Limited(RecordingOpenAlexTransport):
        def get(self,**kwargs): return replace(super().get(**kwargs),retry_after='120')
    transport=Limited(status_code=429)
    sleeps=[]
    fetcher=OpenAlexLiteratureFetcher(api_key='test-only',transport=transport,sleeper=sleeps.append)
    with pytest.raises(LiteratureFetchError) as failed: tuple(fetcher.search(_query()))
    assert failed.value.retry_after_seconds==120 and len(transport.calls)==1 and not sleeps


def test_openalex_reload_uses_existing_credential_source():
    from hypoloom.literature import OpenAlexLiteratureFetcher
    from test_literature import RecordingOpenAlexTransport,_query
    transport=RecordingOpenAlexTransport()
    fetcher=OpenAlexLiteratureFetcher(api_key='initial',transport=transport)
    selected=['replacement-one']
    fetcher.api_key_loader=lambda:selected[0]
    tuple(fetcher.search(_query()))
    assert all(c['headers']['Authorization']=='Bearer replacement-one' for c in transport.calls)
    transport.calls.clear(); selected[0]='replacement-two'
    tuple(fetcher.search(_query()))
    assert all(c['headers']['Authorization']=='Bearer replacement-two' for c in transport.calls)


@pytest.mark.parametrize('action',['--test','--send-test','--retry'])
def test_notify_cli_actions_do_not_require_delivery_directory(tmp_path,monkeypatch,capsys,action):
    from hypoloom.cli import main
    import hypoloom.control_protocol as control
    calls=[]
    monkeypatch.setattr(notifier,'load_notification_config',lambda *a:_notify(telegram=True))
    def probe(*a,**kw): calls.append(kw); return {'ok':True,'message_sent':kw['send']}
    monkeypatch.setattr(notifier,'test_notification',probe)
    def request(path,req,**kwargs): calls.append(req); return control.ControlResponse.accepted(req.request_id,None,{})
    monkeypatch.setattr(control,'request_over_socket',request)
    assert main(['notify','--state-root',str(tmp_path),'--run-id','selected',action])==0
    if action=='--retry': assert calls[0].command=='NOTIFICATION_RETRY'
    else: assert calls[0]['send']==(action=='--send-test')
    capsys.readouterr()


@pytest.mark.parametrize('route,command,extra',[
    ('literature-retry','PROVIDER_RETRY_CLEAR',{'reason':'operator','service':'openalex'}),
    ('notification-retry','NOTIFICATION_RETRY',{'sink':'telegram'})])
def test_web_dependency_controls_preserve_auth_origin_scope_and_exact_retry(tmp_path,monkeypatch,route,command,extra):
    from email.message import Message
    from types import SimpleNamespace
    from io import BytesIO
    from hypoloom.web_observer import WebObserverRequestHandler
    import hypoloom.control_protocol as transport
    calls=[]
    def send(path,request,**kwargs):
        calls.append(request)
        if len(calls)==1: raise ConnectionError('private')
        return transport.ControlResponse.accepted(request.request_id,None,{})
    monkeypatch.setattr(transport,'request_over_socket',send)
    def post(*,token='observer',grant='operator',origin='http://127.0.0.1:1234',**fields):
        handler=object.__new__(WebObserverRequestHandler)
        handler.server=SimpleNamespace(access_token='observer',_provider_token='operator',run_id='selected',state_root=tmp_path)
        handler.path='/api/'+route
        body=json.dumps({'request_id':'exact',**fields}).encode()
        handler.headers=Message()
        for key,value in {'Authorization':'Bearer '+token,'X-Hypoloom-Provider-Token':grant,'Origin':origin,
            'Host':'127.0.0.1:1234','Content-Type':'application/json','Content-Length':str(len(body))}.items():
            handler.headers[key]=value
        handler.rfile=BytesIO(body)
        handler.connection=SimpleNamespace(settimeout=lambda value:None)
        replies=[]
        handler._send=lambda status,*args,**kw: replies.append(int(status))
        handler.do_POST()
        return replies[0]
    assert post(token='wrong')==401 and post(grant='wrong')==403
    assert post(origin='http://example.invalid')==403
    assert post(run_id='foreign')==400 and post(settings_path='/private/model')==400
    assert not calls
    assert post()==503 and post()==200
    assert calls[0]==calls[1] and calls[0].command==command
    assert calls[0].payload=={'run_id':'selected',**extra}
