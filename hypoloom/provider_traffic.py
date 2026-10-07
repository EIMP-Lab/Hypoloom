"""Run-owned, disposable request pacing and live model traffic observations."""
from __future__ import annotations

from collections import deque
import threading
import time

from .provider_observation import PROVIDER_UNAVAILABLE, empty_generation_response, provider_failure, provider_retry_delay


class ProviderDeferred(Exception):
    """Return a known wait to Host before the local bridge's read timeout."""
    def __init__(self, code, retry_after=None):
        self.code, self.retry_after = code, retry_after


class ProviderTraffic:
    """Share admission across relays; never infer a provider's account quota.

    Count outbound attempts, including retries and connection failures, at
    admission to the upstream transport. Queued/local-rejected calls do not
    count. A permit stays held until the response is closed, including streams.
    """

    def __init__(self, *, rpm_limit=None, max_in_flight=None, auth_cooldown=18000,
                 resumed=False, clock=time.monotonic, wall_clock=time.time,
                 window_seconds=60.0):
        for value in (rpm_limit, max_in_flight):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError("provider traffic limits must be positive integers")
        self.rpm_limit, self.max_in_flight = rpm_limit, max_in_flight
        self.auth_cooldown = auth_cooldown
        self.clock, self.wall_clock, self.window = clock, wall_clock, window_seconds
        self.warm_until = clock() + (window_seconds if resumed else 0)
        self.condition = threading.Condition()
        self.buckets = {}
        self.connection = None
        self.last_success = None
        self.last_failure = None

    def select(self, connection):
        with self.condition:
            if self.connection != connection:
                self.connection = connection
                self.last_success = self.last_failure = None
                for bucket in self.buckets.values():
                    bucket.update(until=0, halted=False, failures=0, failed_at=0, reason=None)
                self.condition.notify_all()

    def _bucket(self, model):
        return self.buckets.setdefault(model, dict(history=deque(), active={}, queue=deque(),
            until=0, halted=False, failures=0, failed_at=0, reason=None))

    def _prune(self, bucket, now):
        while bucket['history'] and bucket['history'][0]['at'] <= now - self.window:
            bucket['history'].popleft()

    def acquire(self, *, model, owner, connection, deadline, cancelled, attempt_id=None):
        with self.condition:
            bucket = self._bucket(model)
            waiting = object()
            queued_at = self.clock()
            bucket['queue'].append((waiting, owner, attempt_id))
            try:
                while True:
                    now = self.clock()
                    if cancelled.is_set() or now >= deadline:
                        raise TimeoutError("provider request wait cancelled or expired")
                    if bucket['halted'] or bucket['until'] > now:
                        raise ProviderDeferred(bucket['reason'], None if bucket['halted'] else bucket['until'] - now)
                    self._prune(bucket, now)
                    history = bucket['history']
                    due = max(self.warm_until, bucket['until'])
                    if self.rpm_limit and history:
                        due = max(due, history[-1]['at'] + self.window / self.rpm_limit)
                        if len(history) >= self.rpm_limit:
                            due = max(due, history[0]['at'] + self.window)
                    if now - queued_at >= 300:
                        raise ProviderDeferred('local_request_queue')
                    capacity = self.max_in_flight is None or len(bucket['active']) < self.max_in_flight
                    if bucket['queue'][0][0] is waiting and capacity and not bucket['halted'] and now >= due:
                        permit = dict(at=now, started_at=self.wall_clock(), owner=owner,
                            connection=connection, model=model, attempt_id=attempt_id)
                        bucket['active'][waiting] = permit
                        history.append(permit)
                        return waiting, permit
                    self.condition.wait(min(.1, deadline - now, max(.001, due - now) if due > now else .1))
            finally:
                bucket['queue'].remove((waiting, owner, attempt_id))
                self.condition.notify_all()

    def finish(self, ticket, exchange):
        identity, permit = ticket
        with self.condition:
            bucket = self._bucket(permit['model'])
            bucket['active'].pop(identity, None)
            if permit['connection'] == self.connection:
                failure = provider_failure([exchange])
                now = self.clock()
                if failure and failure['code'] in PROVIDER_UNAVAILABLE:
                    bucket['reason'] = failure['code']
                    self.last_failure = max(self.last_failure or 0, self.wall_clock())
                    if permit['at'] >= bucket['failed_at']:
                        bucket['failures'] += 1
                    bucket['failed_at'] = now
                    if failure['code'] == 'quota_exhausted' and 'retry_at' not in failure:
                        bucket['halted'] = True
                    else:
                        delay = (self.auth_cooldown if failure['code'] == 'auth_failed' else
                            provider_retry_delay(bucket['failures']))
                        delay = max(delay, (failure.get('retry_at') or 0) - self.wall_clock())
                        bucket['until'] = max(bucket['until'], now + delay)
                elif (exchange.get('request_path') == '/v1/messages'
                        and exchange.get('upstream_status') == 200
                        and exchange.get('message_complete') is True
                        and exchange.get('response_body_complete') is True
                        and exchange.get('failure_reason') is None
                        and not empty_generation_response(exchange)):
                    start = permit['started_at']
                    if self.last_failure is None or start >= self.last_failure:
                        self.last_success = max(self.last_success or (0, 0), (start, self.wall_clock()))
                        bucket.update(failures=0, until=0, halted=False)
            self.condition.notify_all()

    def recovered_after(self, boundary):
        with self.condition:
            return (self.last_success[1] if self.last_success is not None and self.last_success[0] >= boundary
                and (self.last_failure is None or self.last_success[0] >= self.last_failure) else None)

    def snapshot(self):
        with self.condition:
            now = self.clock()
            def empty(): return dict(rpm=0, in_flight=0, queued=0)
            total, grounding, other, experts, models, attempts = empty(), empty(), empty(), {}, {}, {}
            for model, bucket in self.buckets.items():
                self._prune(bucket, now)
                model_counts = models.setdefault(model, empty())
                for key, requests in (
                    ('rpm', [(x['owner'], x['attempt_id']) for x in bucket['history']]),
                    ('in_flight', [(x['owner'], x['attempt_id']) for x in bucket['active'].values()]),
                    ('queued', [(owner, attempt) for _, owner, attempt in bucket['queue']])):
                    for owner, attempt in requests:
                        group = (grounding if owner == ('run', 'grounding') else
                            experts.setdefault(owner[1], empty()) if owner[0] == 'expert' else other)
                        total[key] += 1
                        model_counts[key] += 1
                        group[key] += 1
                        if attempt is not None:
                            attempts.setdefault(attempt, empty())[key] += 1
            return {**total, 'scope': 'run', 'window_seconds': self.window,
                'observed_at': self.wall_clock(), 'warming_up': now < self.warm_until,
                'limits': {'rpm': self.rpm_limit, 'in_flight': self.max_in_flight},
                'by_model': models, 'by_expert': experts, 'by_attempt': attempts,
                'grounding': grounding, 'other': other}


def for_run(host, run_id):
    if run_id not in host._provider_traffic:
        budget = host._research_contract(run_id).document.get('budget', {})
        resumed = bool(host.store.inspect('SELECT 1 FROM execution_attempts WHERE run_id=? '
            "AND kind='AGENT' AND host_generation != ? LIMIT 1", (run_id, host.generation)))
        host._provider_traffic[run_id] = ProviderTraffic(rpm_limit=budget.get('provider_rpm_limit'),
            max_in_flight=budget.get('provider_max_in_flight'),
            auth_cooldown=budget.get('provider_auth_cooldown_seconds', 18000), resumed=resumed)
    return host._provider_traffic[run_id]


def for_request(host, request):
    """Reuse task ownership, including reading assistants' existing parent refs."""
    from .observer_scope import attempt_scopes, reading_parent
    rows = [dict(attempt_id=request.attempt_id, run_id=request.run_id,
        observation={'request': request.to_dict()})]
    current, seen = request.to_dict(), {request.attempt_id}
    while (parent := reading_parent(current)) and parent not in seen:
        seen.add(parent)
        row = host.store.get_attempt(parent)
        if row is None or row['run_id'] != request.run_id:
            break
        rows.append(row)
        from .observer_scope import request_of
        current = request_of(row)
    scopes = attempt_scopes(rows, include_run_group=True)
    scope = scopes[request.attempt_id]
    owner = (('expert', scope['owner_role']) if scope['task_scope'] == 'expert' else
        ('run', 'grounding') if scope['run_group'] == 'grounding' else ('run', 'other'))
    cleared = host.store.inspect("SELECT MAX(json_extract(payload_json,'$.provider_retry_clear.cleared_at')) AS v "
        "FROM workflow_transitions WHERE run_id=? AND workflow_kind='RUN'", (request.run_id,))[0]['v']
    traffic = for_run(host, request.run_id)
    traffic.select(cleared)
    return traffic, owner, cleared, request.attempt_id
