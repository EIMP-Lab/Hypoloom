"""Unattended service recovery reuses real pending queries and Host ownership."""
import json
import threading
import time

import pytest

from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.control_protocol import ControlRequest
from hypoloom.expert_loop import AuxiliaryTask, AuxiliaryRequestKind
from hypoloom.literature import FixedLiteratureIO, LiteratureFailureCode, LiteratureFetchError
from hypoloom.literature_recovery import events, points, reduce_availability, status
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost
from test_literature_workflow_host import _LiteratureFetcher, _payload, _drive, _request, mount_root
from test_service_notifications import row


class OffsetClock:
    offset = 0.0

    def time(self):
        return time.time() + self.offset

    def advance_to(self, at):
        self.offset = at - time.time() + .01

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch):
    value = OffsetClock()
    # Advance only the recovery decision's clock. Attempt and SQLite lifecycle
    # timestamps keep the same real clock for independent closure auditing.
    monkeypatch.setattr("hypoloom.literature_recovery.time", value)
    return value


def until(host, condition):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        host.tick()
        assert not host.dispatch_errors, host.dispatch_errors
        if condition(): return
        time.sleep(.005)
    raise AssertionError("Host did not reach the expected bounded observation")


@pytest.mark.parametrize("reason", ["openalex_timeout", "openalex_http", "openalex_rate_limit"])
@pytest.mark.parametrize("retry_after", [None, 1800])
def test_transient_outage_keeps_low_frequency_recovery_after_six_rounds(reason, retry_after):
    records = [row(str(i), i * 2000, i * 2000 + 2, reason=reason) for i in range(8)]
    records[-1]["observation"]["literature_service"]["retry_after_seconds"] = retry_after
    ended = records[-1]["ended_at"]
    waiting = reduce_availability(points(records, []), now=ended + 1)
    assert waiting["state"] == "cooldown" and waiting["pending"]
    assert not waiting["requires_operator"] and waiting["recovery_attempts"] == 8
    assert waiting["next_retry_at"] == ended + (retry_after or 900)
    assert reduce_availability(points(records, []), now=waiting["next_retry_at"])["state"] == "recovering"
    assert [event["type"] for event in events("run", records, [])] == ["literature_waiting"]


def test_auth_failure_never_automatically_probes_after_the_slow_interval():
    records = [row("auth", 1, 2, reason="openalex_auth")]
    waiting = reduce_availability(points(records, []), now=100000)
    assert waiting["state"] == "waiting_operator" and waiting["requires_operator"]
    assert waiting["pending"] and waiting["next_retry_at"] is None


@pytest.mark.parametrize("restart", [False, True])
def test_host_recovers_after_six_failures_without_operator_or_new_plan(tmp_path, mount_root, clock, restart):
    fetcher = _LiteratureFetcher(fail=True, failure_code=LiteratureFailureCode.OPENALEX_TIMEOUT)
    adapter = FixedLiteratureIO(fetcher)
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    run = "automatic-literature-recovery"
    try:
        host.initialize()
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", _payload(tmp_path, mount_root, run))).status == "accepted"
        for wave in range(1, 7):
            until(host, lambda: status(host.store, run)["recovery_attempts"] == wave)
            waiting = status(host.store, run)
            assert waiting["state"] == "cooldown" and not waiting["requires_operator"]
            before = (len(fetcher.queries), len(host.store.list_attempts()))
            assert host._read_literature(run, "coverage", "waiting-coverage", fetcher.queries[-1]) is None
            assert ("coverage", "waiting-coverage") not in host._literature_jobs
            for _ in range(4): host.tick()
            assert (len(fetcher.queries), len(host.store.list_attempts())) == before
            if wave < 6: clock.advance_to(waiting["next_retry_at"])
        failures = [a for a in host.store.list_attempts() if json.loads(a["observation_json"]).get("failure_stage") == "literature_retrieval"]
        assert len(failures) == 6 and len({a["command_id"] for a in failures}) == 1
        requests = [json.loads(a["observation_json"])["request"] for a in failures]
        assert all(r["input_artifacts"] == requests[0]["input_artifacts"] for r in requests)
        assert all(c.source == "pre_model" and c.tokens == c.turns == c.cost == 0 for c in fold_agent_attempts(failures))
        assert not host._expert_final_failures(run)
        assert not host.store.list_facts(run, "HypothesisProposed")
        assert waiting["next_retry_at"] - failures[-1]["ended_at"] == pytest.approx(900, abs=.1)
        if restart:
            host.close()
            host = WorkflowHost(tmp_path / "state", literature_io=adapter)
            host.initialize()
            assert status(host.store, run) == waiting
            host.tick()
            assert (len(fetcher.queries), len(host.store.list_attempts())) == before
        fetcher.fail = False
        clock.advance_to(waiting["next_retry_at"])
        until(host, lambda: bool(host.store.list_facts(run, "HypothesisProposed")))
        assert status(host.store, run)["state"] == "ready"
        attempts = host.store.list_attempts()
        grounding = [a for a in attempts if a["command_id"] == failures[0]["command_id"]]
        assert len(grounding) == 7 and grounding[-1]["terminal_reason"] == "SUCCEEDED"
        plans = [a for a in attempts if isinstance(_request(a).task_context, AuxiliaryTask)
                 and _request(a).task_context.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN]
        assert len(plans) == 1
        assert not host.store.inspect("SELECT command_id FROM workflow_transitions WHERE json_type(payload_json,'$.literature_retry_clear')='object'")
        assert host.handle(ControlRequest(1, "cancel", None, "CANCEL", {"run_id": run, "cause": "operator"})).status == "accepted"
        _drive(host, run)
        assert all(a["state"] == "CLOSED" for a in host.store.list_attempts())
        assert Oracle(host.store.database_path, host.publisher.root).audit(run).ok
    finally:
        host.close()


@pytest.mark.parametrize("cancel", [False, True])
def test_two_query_plan_responses_wait_for_one_coverage_read_without_relaunch(tmp_path, mount_root, cancel):
    from test_expert_parallel_host import _payload as parallel_payload
    from hypoloom.expert_loop import ResearchNodeTask, ResearchNodeSubkind
    from hypoloom.fake_agent import FakeAgent

    entered, release = threading.Event(), threading.Event()
    class HeldCoverage(FixedLiteratureIO):
        coverage_queries = []

        def admit_query_coverage(self, query):
            self.coverage_queries.append(query)
            if len(self.coverage_queries) == 2:
                entered.set()
                assert release.wait(15)
            return super().admit_query_coverage(query)

    class CountPlans(FakeAgent):
        plans = []

        def invoke(self, request):
            if isinstance(request.task_context, ResearchNodeTask) and request.task_context.key.subkind is ResearchNodeSubkind.QUERY_PLAN:
                self.plans.append(request.attempt_id)
            return super().invoke(request)

    adapter = HeldCoverage(_LiteratureFetcher())
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    host.agent = agent = CountPlans()
    run = "serialized-coverage"
    try:
        host.initialize()
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", parallel_payload(tmp_path, mount_root, run))).status == "accepted"
        until(host, lambda: entered.is_set() and len(agent.plans) == 2)
        assert len(host._literature_responses) == 2
        assert len(host._literature_jobs) == 1
        for _ in range(3): host.tick()
        assert len(agent.plans) == 2 and len(adapter.coverage_queries) == 2
        if cancel:
            assert host.handle(ControlRequest(1, "cancel", None, "CANCEL", {"run_id": run, "cause": "operator"})).status == "accepted"
            host.tick()
            assert not host._literature_responses
        release.set()
        if not cancel:
            until(host, lambda: all(host.store.get_attempt(a)["state"] == "CLOSED" for a in agent.plans))
            assert len(agent.plans) == 2 and len(adapter.coverage_queries) == 3
            assert all(host.store.get_attempt(a)["terminal_reason"] == "SUCCEEDED" for a in agent.plans)
            assert host.handle(ControlRequest(1, "cancel", None, "CANCEL", {"run_id": run, "cause": "operator"})).status == "accepted"
        _drive(host, run)
        assert all(a["state"] == "CLOSED" for a in host.store.list_attempts())
        assert not host._literature_responses and not host._literature_jobs
        assert Oracle(host.store.database_path, host.publisher.root).audit(run).ok
    finally:
        release.set()
        host.close()


@pytest.mark.parametrize("cancel", [False, True])
def test_pending_coverage_waits_through_main_outage_without_fake_acceptance(tmp_path, mount_root, monkeypatch, clock, cancel):
    from test_expert_parallel_host import _payload as parallel_payload
    from hypoloom.expert_loop import ResearchNodeTask, ResearchNodeSubkind
    from hypoloom.fake_agent import FakeAgent

    entered, release = threading.Event(), threading.Event()
    class FailMainOnce(FixedLiteratureIO):
        retrievals = 0
        coverage_queries = []

        def retrieve(self, query):
            self.retrievals += 1
            if self.retrievals == 2:
                entered.set()
                assert release.wait(15)
                raise LiteratureFetchError("offline", code=LiteratureFailureCode.OPENALEX_TIMEOUT)
            return super().retrieve(query)

        def admit_query_coverage(self, query):
            self.coverage_queries.append(query)
            return super().admit_query_coverage(query)

    class CountPlans(FakeAgent):
        plans = []

        def invoke(self, request):
            if isinstance(request.task_context, ResearchNodeTask) and request.task_context.key.subkind is ResearchNodeSubkind.QUERY_PLAN:
                self.plans.append(request.attempt_id)
            return super().invoke(request)

    adapter = FailMainOnce(_LiteratureFetcher())
    host = WorkflowHost(tmp_path / "state", literature_io=adapter)
    host.agent = agent = CountPlans()
    run = "coverage-during-outage"
    # Exercise the legal schedule where the second expert resumes while the
    # first expert's MAIN retrieval is in flight.
    paused = host._literature_cycle_paused
    monkeypatch.setattr(host, "_literature_cycle_paused", lambda run_id, role, component, cycle:
        role == "robustness-expert" and not entered.is_set() or paused(run_id, role, component, cycle))
    try:
        host.initialize()
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", parallel_payload(tmp_path, mount_root, run))).status == "accepted"
        until(host, lambda: entered.is_set() and len(agent.plans) == 2)
        waiting_id = agent.plans[-1]
        assert waiting_id in host._literature_responses
        assert ("coverage", waiting_id) not in host._literature_jobs
        release.set()
        until(host, lambda: status(host.store, run)["pending"])
        assert host.store.get_attempt(waiting_id)["state"] == "OPEN"
        counts = (adapter.retrievals, len(adapter.coverage_queries), len(agent.plans))
        for _ in range(4): host.tick()
        assert (adapter.retrievals, len(adapter.coverage_queries), len(agent.plans)) == counts
        assert host.store.get_attempt(waiting_id)["state"] == "OPEN"
        if cancel:
            assert host.handle(ControlRequest(1, "cancel", None, "CANCEL", {"run_id": run, "cause": "operator"})).status == "accepted"
            _drive(host, run)
            assert host.store.get_attempt(waiting_id)["terminal_reason"] == "CANCELLED"
            assert len(adapter.coverage_queries) == counts[1]
        else:
            clock.advance_to(status(host.store, run)["next_retry_at"])
            until(host, lambda: host.store.get_attempt(waiting_id)["state"] == "CLOSED" and status(host.store, run)["state"] == "ready")
            assert host.store.get_attempt(waiting_id)["terminal_reason"] == "SUCCEEDED"
            assert len(agent.plans) == 2 and len(adapter.coverage_queries) == counts[1] + 1
            assert host.handle(ControlRequest(1, "cancel", None, "CANCEL", {"run_id": run, "cause": "operator"})).status == "accepted"
            _drive(host, run)
        assert not host._literature_responses and not host._literature_jobs
        assert all(a["state"] == "CLOSED" for a in host.store.list_attempts())
        assert Oracle(host.store.database_path, host.publisher.root).audit(run).ok
    finally:
        release.set()
        host.close()


@pytest.mark.parametrize("boundary", ["stopping", "terminal", "wallclock"])
def test_recovery_never_submits_after_run_stop_or_wallclock(tmp_path, mount_root, monkeypatch, boundary):
    from datetime import datetime, timedelta
    from test_literature import _query

    fetcher = _LiteratureFetcher()
    host = WorkflowHost(tmp_path / "state", literature_io=FixedLiteratureIO(fetcher))
    run = "stopped-literature-recovery"
    try:
        host.initialize()
        payload = _payload(tmp_path, mount_root, run)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload)).status == "accepted"
        if boundary == "wallclock":
            class ExpiredDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(tz) + timedelta(hours=2)
            monkeypatch.setattr("hypoloom.agent_budget.datetime", ExpiredDateTime)
            assert host._run_wallclock_expired(run)
        else:
            assert host.handle(ControlRequest(1, "cancel", None, "CANCEL", {"run_id": run, "cause": "operator"})).status == "accepted"
            if boundary == "terminal":
                _drive(host, run)
                assert host.store.latest_transition(run)["state"] == "CANCELLED"
        for kind in ("coverage", "retrieve"):
            assert host._read_literature(run, kind, "stale-query", _query()) is None
        assert not host._literature_jobs and not fetcher.queries
    finally:
        host.close()
