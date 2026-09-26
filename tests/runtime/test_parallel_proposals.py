"""Exercise real Host ownership and publication, not a second readiness reducer."""
import json
from dataclasses import replace

import pytest

from hypoloom.domain import AttemptTerminalReason
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_batch_host import BatchAgent
from tests.runtime.test_expert_batch_host import batch_host, _complete
from tests.runtime.test_host_experiment_tools import _until


def hold_wings(host, monkeypatch):
    finish, held = host._finish_fake_agent, {}

    def capture(request, response):
        task = request.task_context
        if isinstance(task, ResearchNodeTask) and task.key.subkind in {
                ResearchNodeSubkind.FORWARD, ResearchNodeSubkind.BACKWARD}:
            held.setdefault(task.key.subkind.value, []).append((request, response))
        else:
            finish(request, response)

    monkeypatch.setattr(host, "_finish_fake_agent", capture)
    return held, finish


@pytest.mark.parametrize("batch_host", ["proposal-parallel"], indirect=True)
@pytest.mark.parametrize("first", ["FORWARD", "BACKWARD"])
def test_parallel_wings_join_once_after_both_publications(batch_host, monkeypatch, first):
    host = batch_host
    held, finish = hold_wings(host, monkeypatch)
    _until(host, lambda: len(held) == 2)
    forward, backward = (held[name][0][0] for name in ("FORWARD", "BACKWARD"))
    assert forward.command_id != backward.command_id
    assert all(host.store.get_attempt(req.attempt_id)["state"] == "OPEN" for req in (forward, backward))
    # The exception cannot admit a duplicate wing, another cycle, or SYNTHESIS.
    for task in (forward.task_context,
                 ResearchNodeTask(key=replace(backward.task_context.key, cycle=2)),
                 ResearchNodeTask(key=replace(backward.task_context.key, subkind=ResearchNodeSubkind.SYNTHESIS))):
        assert host._participant_busy(forward.run_id, forward.role, proposal_task=task)
    finish(*held[first][0])
    for _ in range(3): host.tick()
    assert not host.store.list_facts(forward.run_id, "HypothesisProposed")
    finish(*held["BACKWARD" if first == "FORWARD" else "FORWARD"][0])
    _complete(host)
    attempts = host.store.list_attempts()
    synthesis = [row for row in attempts if row["kind"] == "AGENT" and
        json.loads(row["observation_json"]).get("request", {}).get("task_context", {}).get("key", {}).get("subkind") == "SYNTHESIS"]
    assert len(synthesis) == 1
    assert len(host.store.list_facts(forward.run_id, "HypothesisProposed")) == 2
    assert len(host.store.list_facts(forward.run_id, "CanonicalOutcomeAccepted")) == 2
    assert not [row for row in attempts if row["state"] == "OPEN"]
    assert Oracle(host.store.database_path, host.publisher.root).audit("batch-host").ok


@pytest.mark.parametrize("batch_host", ["proposal-one-slot", "serial25"], indirect=True)
def test_one_slot_and_retained_version_remain_serial(batch_host, monkeypatch):
    host = batch_host
    held, finish = hold_wings(host, monkeypatch)
    _until(host, lambda: "FORWARD" in held)
    for _ in range(10): host.tick()
    assert "BACKWARD" not in held
    finish(*held["FORWARD"][0])
    _until(host, lambda: "BACKWARD" in held)
    finish(*held["BACKWARD"][0])
    _complete(host)
    assert Oracle(host.store.database_path, host.publisher.root).audit("batch-host").ok


@pytest.mark.parametrize("batch_host", ["proposal-parallel"], indirect=True)
def test_retry_keeps_successful_sibling(batch_host, monkeypatch):
    host = batch_host
    held, finish = hold_wings(host, monkeypatch)
    _until(host, lambda: len(held) == 2)
    finish(*held["BACKWARD"][0])
    failed = held["FORWARD"][0][0]
    host._agent_failure(failed, AttemptTerminalReason.FAILED, "test_transient", retryable=True)
    _until(host, lambda: len(held["FORWARD"]) == 2)
    retry = held["FORWARD"][1][0]
    assert retry.command_id == failed.command_id
    assert retry.attempt_id != failed.attempt_id
    assert len(held["BACKWARD"]) == 1
    finish(*held["FORWARD"][1])
    _complete(host)
    assert Oracle(host.store.database_path, host.publisher.root).audit("batch-host").ok


@pytest.mark.parametrize("batch_host", ["proposal-parallel"], indirect=True)
def test_final_failure_discards_sibling_late_output(batch_host, monkeypatch):
    host = batch_host
    held, finish = hold_wings(host, monkeypatch)
    _until(host, lambda: len(held) == 2)
    failed = held["FORWARD"][0][0]
    host._agent_failure(failed, AttemptTerminalReason.FAILED, "test_permanent", retryable=False)
    finish(*held["BACKWARD"][0])
    sibling = host.store.get_attempt(held["BACKWARD"][0][0].attempt_id)
    assert sibling["terminal_reason"] == "CANCELLED"
    assert json.loads(sibling["observation_json"])["failure_code"] == "proposal_sibling_failed"
    assert not host.store.list_facts(failed.run_id, "HypothesisProposed")
    _until(host, lambda: host.store.latest_transition(failed.run_id)["state"] in {"FAILED", "COMPLETED"})
    assert not [row for row in host.store.list_attempts() if row["state"] == "OPEN"]
    assert Oracle(host.store.database_path, host.publisher.root).audit("batch-host").ok


@pytest.mark.parametrize("batch_host", ["proposal-parallel"], indirect=True)
def test_restart_keeps_already_published_wing(batch_host, monkeypatch):
    host = batch_host
    held, finish = hold_wings(host, monkeypatch)
    _until(host, lambda: len(held) == 2)
    finish(*held["BACKWARD"][0])
    root = host.state_root
    host.close()
    recovered = WorkflowHost(root, local_agent=BatchAgent())
    try:
        recovered.initialize()
        _complete(recovered)
        rows = recovered.store.list_attempts(command_id=held["BACKWARD"][0][0].command_id)
        assert len(rows) == 1 and rows[0]["terminal_reason"] == "SUCCEEDED"
        assert Oracle(recovered.store.database_path, recovered.publisher.root).audit("batch-host").ok
    finally:
        recovered.close()


@pytest.mark.parametrize("batch_host", ["proposal-two-cycles", "proposal-peers"], indirect=True)
def test_parallel_proposals_reach_other_experts_and_next_cpu_cycles(batch_host, monkeypatch, request):
    host = batch_host
    held, finish = hold_wings(host, monkeypatch)
    _until(host, lambda: len(held) == 2)
    if request.node.callspec.params["batch_host"] == "proposal-peers":
        roles = {value[0].role for items in held.values() for value in items}
        _until(host, lambda: len(roles) > 1 or any(json.loads(row["observation_json"]).get("request", {}).get("role") not in roles
            for row in host.store.list_attempts() if row["state"] == "OPEN" and row["kind"] == "AGENT"))
    monkeypatch.setattr(host, "_finish_fake_agent", finish)
    for items in held.values():
        for value in items: finish(*value)
    _complete(host, timeout=75)
    outcomes = host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
    assert len(outcomes) == (4 if request.node.callspec.params["batch_host"] == "proposal-two-cycles" else 3)
    assert Oracle(host.store.database_path, host.publisher.root).audit("batch-host").ok
