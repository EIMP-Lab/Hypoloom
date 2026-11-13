"""Portable checks for bounded history reads, service opportunities and workers."""
from dataclasses import replace
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentRequest, required_output_schema
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.state_store import HostWriterCapability, StateStore, _ReadCache, canonical_json
from hypoloom.workflow_host import WorkflowHost


@pytest.fixture
def history(tmp_path):
    host = WorkflowHost(tmp_path / "state")
    host.store.initialize()
    with host.store.transaction() as tx:
        tx.create_run("run-fake", host.version)
    dispatched = []
    host._expert_agent_step = lambda run, owner, task, *args: dispatched.append(task.command_id)
    host._expert_task_exhausted = lambda *args: False
    try:
        yield host, dispatched
    finally:
        host.store.close()
        host._projection_executor.shutdown(wait=True)


def attempt(host, cycle, *, ordinal=1, reason="FAILED", schema=22, handoff=None, padding=0):
    base = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    task = replace(base.task_context, key=replace(base.task_context.key, cycle=cycle))
    request = replace(base, task_context=task, command_id=task.command_id,
        attempt_id=f"attempt-{cycle}-{ordinal}", attempt_ordinal=ordinal, schema_version=schema,
        required_output_schema=required_output_schema(base.command, task, schema_version=schema))
    observation = {"request": request.to_dict(), "response": {"typed_candidate": {
        "waiting_for_experiment_id": handoff}}, "padding": "x" * padding}
    with host.store.transaction() as tx:
        if tx.get_command(request.command_id) is None:
            tx.record_command({"command_id": request.command_id, "run_id": request.run_id,
                "workflow_id": request.owner_workflow_id, "command_type": "REQUEST_RESEARCH_NODE",
                "request": {"task_context": task.to_dict()}})
        tx.execute("""INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,
            kind,attempt_ordinal,host_generation,state,started_at,ended_at,terminal_reason,observation_json)
            VALUES(?,?,?,?,'AGENT',?,'test',?,?,?,?,?)""",
            (request.attempt_id, request.run_id, request.owner_workflow_id, request.command_id,
             ordinal, "OPEN" if reason is None else "CLOSED", cycle * 10 + ordinal,
             None if reason is None else cycle * 10 + ordinal + 1, reason, canonical_json(observation)))
    return request


@pytest.mark.parametrize("count", [1, 100, 1000])
def test_completed_history_does_not_rebuild_requests(history, monkeypatch, count):
    host, dispatched = history
    for cycle in range(1, count + 1):
        attempt(host, cycle, reason="SUCCEEDED", padding=4096)
    pending = attempt(host, count + 1)
    original = AgentRequest.from_dict
    decoded = []
    def parse(value):
        decoded.append(value["attempt_id"])
        return original(value)
    monkeypatch.setattr(AgentRequest, "from_dict", parse)
    with host.store.cache_reads():
        host._resume_tool_execution_agents("run-fake")
    assert dispatched == [pending.command_id]
    assert decoded == [pending.attempt_id]


def test_resume_keeps_handoffs_and_retries_but_skips_open_success_and_final_failure(history):
    host, dispatched = history
    attempt(host, 1, reason=None)
    attempt(host, 2, reason="SUCCEEDED")
    failed = attempt(host, 3)
    latest = attempt(host, 3, ordinal=2, reason="LOST")
    handoff = attempt(host, 4, reason="SUCCEEDED", handoff="waiting-experiment")
    attempt(host, 5, reason="SUCCEEDED", schema=10, handoff="legacy-unused-field")
    attempt(host, 6, reason="SUCCEEDED")
    attempt(host, 6, ordinal=2, reason="FAILED")
    final = attempt(host, 7)
    with host.store.transaction() as tx:
        tx.append_transition({"run_id": "run-fake", "workflow_id": "run-fake",
            "workflow_kind": "RUN", "revision": 1, "previous_state": None, "state": "ACTIVE",
            "command_id": final.command_id, "payload": {"agent_final_failure": True,
                "failed_task_command_id": final.command_id, "attempt_id": final.attempt_id}})
    host._resume_tool_execution_agents("run-fake")
    assert set(dispatched) == {failed.command_id, handoff.command_id}
    assert latest.command_id == failed.command_id


def test_resume_cache_observes_open_to_closed_commit(history):
    host, dispatched = history
    opened = attempt(host, 1, reason=None)
    with host.store.cache_reads():
        host._resume_tool_execution_agents("run-fake")
        assert dispatched == []
        with host.store.transaction() as tx:
            tx.close_attempt(opened.attempt_id, ended_at=99, terminal_reason="FAILED", payload={})
        host._resume_tool_execution_agents("run-fake")
        assert dispatched == [opened.command_id]


def test_resume_work_rotates_when_the_slice_expires(history, monkeypatch):
    host, dispatched = history
    expected = {attempt(host, cycle).command_id for cycle in range(1, 4)}
    clock = [0.0]
    monkeypatch.setattr("hypoloom.workflow_host.time.monotonic", lambda: clock[0])
    def dispatch(run, owner, task, *args):
        dispatched.append(task.command_id)
        clock[0] += 1
    host._expert_agent_step = dispatch
    for _ in range(3):
        host._tick_deadline = clock[0] + 0.25
        host._resume_tool_execution_agents("run-fake")
    assert len(dispatched) == 3 and set(dispatched) == expected


def test_history_indexes_cover_resume_and_command_reads(history, monkeypatch):
    host, _ = history
    attempt(host, 1, reason="SUCCEEDED")
    original = host.store.inspect
    plans = []
    def inspect(sql, parameters=()):
        if "pending CROSS JOIN" in sql:
            plans.extend(row["detail"] for row in original("EXPLAIN QUERY PLAN " + sql, parameters))
        return original(sql, parameters)
    monkeypatch.setattr(host.store, "inspect", inspect)
    host._resume_tool_execution_agents("run-fake")
    assert any("execution_agent_resume_read" in detail for detail in plans)
    assert any("execution_attempt_command_read" in detail for detail in plans)
    assert any("workflow_final_failure_read" in detail for detail in plans)
    assert not any(detail == "SCAN execution_attempts" for detail in plans)


def test_cache_evicts_without_aliases_and_never_retains_oversized_reads(tmp_path):
    with StateStore(tmp_path / "state", HostWriterCapability.issue()) as store:
        store._read_cache = _ReadCache(max_bytes=3000, max_entries=3)
        source = {"nested": ["x" * 400]}
        reads = []
        def read():
            reads.append(1)
            return source
        with store.cache_reads():
            for index in range(50):
                result = store.cached_read(index, read)
                result["nested"].append("private")
                assert store._read_cache.bytes <= 3000 and len(store._read_cache) <= 3
            assert source == {"nested": ["x" * 400]}
            store.cached_read(49, read)
            assert len(reads) == 50
            store.cached_read(0, read)
            assert len(reads) == 51
            large = {"nested": ["z" * 4000]}
            store.cached_read("large", lambda: large)["nested"].append("private")
            assert "large" not in store._read_cache and len(large["nested"]) == 1
        with store.transaction() as tx:
            tx.execute("CREATE TABLE example(value INTEGER)")
        assert store._read_cache.bytes == 0 and len(store._read_cache) == 0


def test_slow_phases_return_to_control_and_resume_without_repeating(monkeypatch):
    host = object.__new__(WorkflowHost)
    host._tick_work = None
    clock, phases, serviced = [0.0], [], []
    host.claude_agent_host = SimpleNamespace(service_tools=lambda: serviced.append(len(phases)) or False)
    def steps():
        for phase in range(4):
            phases.append(phase)
            clock[0] += 0.3
            yield False
        return False
    host._tick_steps = steps
    monkeypatch.setattr("hypoloom.workflow_host.time.monotonic", lambda: clock[0])
    for count in range(1, 5):
        assert host._tick()
        assert len(phases) == count
    assert host._tick() is False
    assert phases == [0, 1, 2, 3] and serviced == [0, 1, 2, 3, 4]
    assert host._tick_work is None


def test_audit_worker_matches_direct_report_and_is_reaped(history, monkeypatch):
    from hypoloom import network_io
    from hypoloom.observer_worker import read_in_worker
    from hypoloom.oracle import Oracle
    host, _ = history
    expected = Oracle(host.store.database_path, host.state_root / "artifacts").audit("run-fake").to_dict()
    processes = []
    popen = network_io.subprocess.Popen
    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(network_io.subprocess, "Popen", launch)
    before = host.store.inspect("SELECT * FROM workflow_runs")
    assert read_in_worker("audit", host.state_root, "run-fake") == expected
    assert len(processes) == 1 and processes[0].poll() is not None
    assert host.store.inspect("SELECT * FROM workflow_runs") == before


def test_closed_observer_cancels_its_audit_worker(tmp_path, monkeypatch):
    from hypoloom.observer_refresh import ObserverRefresh
    from hypoloom.network_io import NetworkOperationError
    entered = threading.Event()
    def read(operation, root, run, *, cancel_event):
        entered.set()
        assert cancel_event.wait(2)
        raise NetworkOperationError("cancelled")
    monkeypatch.setattr("hypoloom.observer_refresh.read_in_worker", read)
    reader = ObserverRefresh(tmp_path, "run")
    reader._thread = threading.Thread(target=reader._run_audit)
    reader._thread.start()
    assert entered.wait(2)
    reader.close()
    reader.wait_for_audit(timeout=2)
    assert not reader.audit_running and reader._audit_result is None


def test_tool_bursts_leave_other_work_a_service_opportunity(tmp_path):
    from hypoloom.host_tools_server import AgentToolServer, _PendingCall
    import tempfile
    from pathlib import Path
    calls = []
    with tempfile.TemporaryDirectory(prefix="hyp-burst-", dir="/tmp") as root:
        endpoint = AgentToolServer(Path(root) / "tools.sock", "attempt", lambda attempt, tool, args: calls.append(tool) or {})
        try:
            queued = [_PendingCall("list_my_experiments", {}) for _ in range(25)]
            for call in queued:
                endpoint._calls.put(call)
            assert endpoint.service(max_calls=4)
            assert len(calls) == 4
            assert sum(call.ready.is_set() for call in queued) == 4
            while endpoint.service(max_calls=4):
                pass
            assert len(calls) == 25
        finally:
            endpoint.close()


def test_tool_endpoints_rotate_when_an_earlier_handler_spends_the_budget(monkeypatch):
    from hypoloom.claude_agent_host import ClaudeAgentHost
    host = object.__new__(ClaudeAgentHost)
    clock, served = [0.0], []
    class Endpoint:
        def __init__(self, name): self.name = name
        def service(self, **kwargs):
            served.append(self.name)
            clock[0] += 0.06
            return True
    host._tool_servers = {name: Endpoint(name) for name in ('a', 'b', 'c')}
    monkeypatch.setattr("hypoloom.claude_agent_host.time.monotonic", lambda: clock[0])
    for _ in range(6):
        assert host.service_tools()
    assert served == ['a', 'b', 'c', 'a', 'b', 'c']


def test_nonempty_old_schema_is_not_upgraded_by_new_host(tmp_path):
    from pathlib import Path
    import hypoloom.state_store as module
    from hypoloom.state_store import MigrationError
    root = tmp_path / "retained"
    root.mkdir(mode=0o700)
    path = root / "state.sqlite"
    with sqlite3.connect(path) as db:
        for migration in sorted(Path(module.__file__).with_name("migrations").glob("*.sql"))[:3]:
            db.executescript(migration.read_text())
        versions = ("w", "r", "e", "33", "runner", "2", "env", "config", sqlite3.sqlite_version)
        db.execute("INSERT INTO workflow_runs VALUES(?,NULL,?,?,?,?,?,?,?,?,?,?)", ("old", *versions, "now"))
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="nonempty schema 3"):
        StateStore(root, HostWriterCapability.issue()).initialize()
    assert path.read_bytes() == before


def test_historical_provider_compaction_matches_reducer_for_late_concurrent_results():
    import random
    from hypoloom.provider_observation import compact_retry_history, provider_retry_gate
    randomizer = random.Random(13)
    full, compact, cleared = [], [], None
    for index in range(500):
        if index % 37 == 0:
            cleared = float(index - 2)
        entry = {'attempt_id': str(index), 'state': 'CLOSED', 'ended_at': float(index),
            'started_at': float(index - randomizer.randrange(20)),
            'provider_success': randomizer.randrange(3) == 0,
            'code': randomizer.choice(['auth_failed', 'quota_exhausted', 'rate_limited', None])}
        full.append(entry)
        compact = compact_retry_history([*compact, entry], cleared_at=cleared)
        kwargs = {'cooldown_seconds': 100, 'cleared_at': cleared, 'now': float(index), 'attempt_limit': 3}
        assert provider_retry_gate(compact, **kwargs) == provider_retry_gate(full, **kwargs)


def test_successful_provider_timeline_does_not_rescan_completed_history(monkeypatch):
    from hypoloom.observation_api import _provider_auth_events
    import hypoloom.provider_observation as module
    rows = [{'attempt_id': str(index), 'kind': 'AGENT', 'state': 'CLOSED',
        'terminal_reason': 'SUCCEEDED', 'started_at': index * 2., 'ended_at': index * 2. + 1,
        'observation': {'request': {}, 'diagnostics': {}}} for index in range(2000)]
    original, sizes = module.provider_retry_gate, []
    def gate(entries, **kwargs):
        sizes.append(len(entries))
        return original(entries, **kwargs)
    monkeypatch.setattr(module, 'provider_retry_gate', gate)
    facts = [{'fact_type': 'GoalDefined', 'payload': {'research_contract': {'budget': {}}}}]
    assert _provider_auth_events('run', facts, [], rows) == []
    assert len(sizes) == 2000 and max(sizes) == 1
