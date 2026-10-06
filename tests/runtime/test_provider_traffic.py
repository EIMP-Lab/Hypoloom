"""Shared outbound scheduling: real relays, bounded waits and honest ownership."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from hypoloom.provider_traffic import ProviderDeferred, ProviderTraffic, for_request
from test_provider_relay import upstream, _relay, _request


def until(predicate):
    end = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < end
        time.sleep(.005)


def acquire(traffic, owner=('expert', 'a'), **kw):
    return traffic.acquire(model='model', owner=owner, connection=None,
        deadline=traffic.clock() + 120, cancelled=threading.Event(), **kw)


def success():
    return dict(request_path='/v1/messages', upstream_status=200,
        message_complete=True, response_body_complete=True, ended_at=time.time())


def test_sliding_window_paces_independent_experts_and_never_counts_queue():
    clock = [0.0]
    traffic = ProviderTraffic(rpm_limit=2, clock=lambda: clock[0])
    first = acquire(traffic)
    traffic.finish(first, success())
    with ThreadPoolExecutor() as pool:
        second = pool.submit(acquire, traffic, ('run', 'grounding'))
        until(lambda: traffic.snapshot()['queued'] == 1)
        assert traffic.snapshot()['rpm'] == 1
        with traffic.condition:
            clock[0] = 29
            traffic.condition.notify_all()
        assert not second.done()
        with traffic.condition:
            clock[0] = 30
            traffic.condition.notify_all()
        second_ticket = second.result(timeout=3)
        traffic.finish(second_ticket, success())
    counts = traffic.snapshot()
    assert counts['rpm'] == 2 and counts['queued'] == counts['in_flight'] == 0
    assert counts['by_expert']['a']['rpm'] == counts['grounding']['rpm'] == 1
    clock[0] = 60
    assert traffic.snapshot()['rpm'] == 1
    clock[0] = 90
    assert traffic.snapshot()['rpm'] == 0


def test_cancellation_releases_queue_and_does_not_spend_a_request():
    traffic = ProviderTraffic(max_in_flight=1)
    held = acquire(traffic)
    cancel = threading.Event()
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(traffic.acquire, model='model', owner=('expert', 'b'),
            connection=None, deadline=time.monotonic() + 10, cancelled=cancel)
        until(lambda: traffic.snapshot()['queued'] == 1)
        cancel.set()
        with pytest.raises(TimeoutError):
            pending.result(timeout=2)
    assert traffic.snapshot()['rpm'] == traffic.snapshot()['in_flight'] == 1
    assert traffic.snapshot()['queued'] == 0
    traffic.finish(held, success())
    assert traffic.snapshot()['in_flight'] == 0


def test_streams_hold_shared_slot_across_two_real_relays(tmp_path, upstream):
    traffic = ProviderTraffic(max_in_flight=1)
    release = threading.Event()
    body = json.dumps({'type': 'message', 'stop_reason': 'end_turn',
        'usage': {'input_tokens': 1, 'output_tokens': 1}, 'content': []}).encode()
    def stream(handler):
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        assert release.wait(3)
        handler.wfile.write(body)
    upstream.script = stream
    relays = [_relay(tmp_path / str(i), upstream,
        traffic_binding=(traffic, ('expert', str(i)), None)) for i in range(2)]
    try:
        with ThreadPoolExecutor() as pool:
            one = pool.submit(_request, relays[0])
            until(lambda: len(upstream.calls) == 1)
            two = pool.submit(_request, relays[1])
            until(lambda: traffic.snapshot()['queued'] == 1)
            assert traffic.snapshot()['rpm'] == traffic.snapshot()['in_flight'] == 1
            assert len(upstream.calls) == 1
            release.set()
            assert one.result(timeout=3)[0] == two.result(timeout=3)[0] == 200
            until(lambda: traffic.snapshot()['in_flight'] == 0)
            assert traffic.recovered_after(0)
            assert traffic.snapshot()['rpm'] == len(upstream.calls) == 2
    finally:
        release.set()
        for relay in relays:
            relay.close()


def test_recovery_needs_complete_current_connection_response():
    traffic = ProviderTraffic()
    old = acquire(traffic)
    traffic.select(10)
    traffic.finish(old, success())
    assert not traffic.recovered_after(0)
    def ticket():
        return traffic.acquire(model='model', owner=('expert', 'a'), connection=10,
            deadline=time.monotonic() + 5, cancelled=threading.Event())
    partial = ticket()
    traffic.finish(partial, {**success(), 'response_body_complete': False})
    assert not traffic.recovered_after(0)
    counted = ticket()
    traffic.finish(counted, {**success(), 'request_path': '/v1/messages/count_tokens'})
    assert not traffic.recovered_after(0)
    actual = ticket()
    traffic.finish(actual, success())
    assert traffic.recovered_after(0)


def test_restart_waits_out_unknown_previous_window_and_can_cancel():
    traffic = ProviderTraffic(resumed=True)
    assert traffic.snapshot()['warming_up']
    with pytest.raises(TimeoutError):
        traffic.acquire(model='model', owner=('run', 'grounding'), connection=None,
            deadline=time.monotonic() + .02, cancelled=threading.Event())
    assert traffic.snapshot()['rpm'] == traffic.snapshot()['queued'] == 0


def test_retry_after_defers_queued_calls_without_spending_rpm_and_switch_keeps_window():
    clock = [100.0]
    traffic = ProviderTraffic(clock=lambda: clock[0], wall_clock=lambda: clock[0])
    ticket = acquire(traffic)
    clock[0] = 101
    traffic.finish(ticket, {'request_path': '/v1/messages', 'upstream_status': 429,
        'ended_at': 101, 'response_headers': {'retry-after': '3600'}})
    with pytest.raises(ProviderDeferred) as caught:
        acquire(traffic)
    assert caught.value.code == 'rate_limited' and caught.value.retry_after == 3600
    assert traffic.snapshot()['rpm'] == 1 and traffic.snapshot()['queued'] == 0
    traffic.select(1)
    assert traffic.snapshot()['rpm'] == 1  # A clear cannot reset consumed requests.
    next_ticket = traffic.acquire(model='model', owner=('expert', 'a'), connection=1,
        deadline=500, cancelled=threading.Event())
    clock[0] = 102
    traffic.finish(next_ticket, success())
    assert traffic.recovered_after(101)


def test_explicit_quota_keeps_operator_control_and_old_connection_cannot_reblock():
    traffic = ProviderTraffic()
    old = acquire(traffic)
    traffic.finish(old, {'request_path': '/v1/messages', 'upstream_status': 429,
        'provider_error_code': 'quota_exhausted', 'ended_at': time.time()})
    with pytest.raises(ProviderDeferred) as caught:
        acquire(traffic)
    assert caught.value.code == 'quota_exhausted' and caught.value.retry_after is None
    traffic.select(1)
    late = traffic.acquire(model='model', owner=('expert', 'a'), connection=None,
        deadline=time.monotonic() + 10, cancelled=threading.Event())
    traffic.finish(late, {'request_path': '/v1/messages', 'upstream_status': 401, 'ended_at': time.time()})
    current = traffic.acquire(model='model', owner=('expert', 'a'), connection=1,
        deadline=time.monotonic() + 10, cancelled=threading.Event())
    traffic.finish(current, success())
    assert traffic.recovered_after(0)


def test_long_queue_returns_to_host_before_bridge_timeout():
    clock = [0.0]
    traffic = ProviderTraffic(max_in_flight=1, clock=lambda: clock[0])
    held = acquire(traffic)
    with ThreadPoolExecutor() as pool:
        queued = pool.submit(traffic.acquire, model='model', owner=('expert', 'b'),
            connection=None, deadline=1000, cancelled=threading.Event())
        until(lambda: traffic.snapshot()['queued'] == 1)
        with traffic.condition:
            clock[0] = 301
            traffic.condition.notify_all()
        with pytest.raises(ProviderDeferred) as caught:
            queued.result(timeout=3)
        assert caught.value.code == 'local_request_queue'
    assert traffic.snapshot()['queued'] == 0
    traffic.finish(held, success())


def test_reader_inherits_grounding_instead_of_transport_expert_role():
    parent = {'attempt_id': 'grounding', 'run_id': 'run', 'observation': {'request': {
        'role': 'expert-a', 'command': 'GROUNDING', 'task_context': {'request_kind': 'GROUNDING'}}}}
    import base64
    request = {'attempt_id': 'reader', 'run_id': 'run', 'role': 'expert-a', 'command': 'PAPER_READ',
        'input_artifacts': [{'ref': 'reader--paper-question', 'content_base64':
            base64.b64encode(json.dumps({'parent_attempt_id': 'grounding'}).encode()).decode()}]}
    host = SimpleNamespace(_provider_traffic={'run': ProviderTraffic()}, store=SimpleNamespace(
        get_attempt=lambda _: parent, inspect=lambda *_: [{'v': None}]))
    _, owner, _, attempt = for_request(host, SimpleNamespace(attempt_id='reader', run_id='run', to_dict=lambda: request))
    assert owner == ('run', 'grounding')
    assert attempt == 'reader'


def test_attempt_rpm_is_observed_not_divided_from_expert_total():
    traffic = ProviderTraffic()
    for attempt, owner in [('parent', ('expert', 'a')), ('reader', ('expert', 'a')),
            ('reader', ('expert', 'a')), ('grounding', ('run', 'grounding'))]:
        ticket = acquire(traffic, owner, attempt_id=attempt)
        traffic.finish(ticket, success())
    view = traffic.snapshot()
    assert view['rpm'] == sum(v['rpm'] for v in view['by_attempt'].values()) == 4
    assert view['by_expert']['a']['rpm'] == 3
    assert view['by_attempt']['parent']['rpm'] == 1
    assert view['by_attempt']['reader']['rpm'] == 2
    assert view['grounding']['rpm'] == 1


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '60'])
def test_invalid_rate_configuration_is_rejected(value):
    with pytest.raises(ValueError):
        ProviderTraffic(rpm_limit=value)
