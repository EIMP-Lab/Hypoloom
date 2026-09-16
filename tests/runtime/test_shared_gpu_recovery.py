"""Actual Host recovery/cancellation with deterministic jobs and synthetic GPUs."""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.gpu_pool import PoolError
from hypoloom.oracle import Oracle, audit_gpu_pool

from test_gpu_host_recovery import _output_binding
from test_phase8_parallel_host import _payload as _parallel_payload
from test_shared_gpu_dispatch import GPU_A, GPU_B, create, specs, until
from test_shared_gpu_dispatch import environment as environment


def _attempts(host, spec):
    return host.store.list_attempts(execution_id=spec["execution_id"])


def _dispatch(host, attempt):
    values = [json.loads(row["payload_json"]) for row in host.store.inspect(
        "SELECT payload_json FROM workflow_transitions WHERE workflow_id=? "
        "AND json_extract(payload_json,'$.attempt_id')=? AND json_extract(payload_json,'$.gpu_pool_key') IS NOT NULL",
        (attempt["workflow_id"], attempt["attempt_id"]))]
    assert len(values) == 1
    return values[0]


def _pool_rows(pool):
    with sqlite3.connect(f"file:{pool.database_path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY request_seq")]


def _successful_jobs(host, monkeypatch):
    """The older adapter's {score: 1} predates the current component protocol."""
    start = host.runner.start

    def complete(request, **kwargs):
        handle = start(request, **kwargs)
        if not request.execution_id.endswith("--candidate-validation"):
            (_output_binding(request) / "result.json").write_text(json.dumps({
                "artifacts": {}, "identity": {"method_family_id": "fake-family-v1"},
                "trials": [{"seed": 0, "clean": True, "metrics": {"score": 3}}],
            }))
        return handle

    host.runner.complete = True
    monkeypatch.setattr(host.runner, "start", complete)


def _assert_closed(host, pool):
    report = Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit("shared")
    assert report.ok, report.issues
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.owned_process_residue == ()
    assert not host.running
    assert host.shared_gpu.reconcile()
    audit = audit_gpu_pool(pool.root)
    assert audit["ok"], audit
    assert {row["state"] for row in _pool_rows(pool)} == {"RELEASED"}


def test_committed_gpu_a_intent_recovers_lost_then_collects_terminal_gpu_b(environment, monkeypatch):
    make, free, pool, runtime = environment
    first = make("alpha")
    create(first, runtime)
    until(first, lambda: bool(specs(first)) and first.store.latest_transition(specs(first)[0]["experiment_id"])["state"] == "READY")
    original = specs(first)[0]

    class SimulatedCrash(BaseException):
        pass

    def crash(point):
        if point == "after_process_intent_before_spawn":
            raise SimulatedCrash(point)

    with monkeypatch.context() as patch:
        patch.setattr(first, "_crash", crash)
        with pytest.raises(SimulatedCrash, match="after_process_intent_before_spawn"):
            first.tick()
    abandoned = _attempts(first, original)
    assert len(abandoned) == 1 and abandoned[0]["state"] == "OPEN"
    first_dispatch = _dispatch(first, abandoned[0])
    assert first_dispatch["resource_grant"] == {"kind": "GPU", "gpu_uuid": GPU_A}
    assert _pool_rows(pool)[0]["state"] == "RESERVED"
    assert not any(request.execution_id == original["execution_id"] for request in first.runner.started)
    first.close()

    free.update({GPU_A: 1000, GPU_B: 18000})
    recovered = make("alpha")
    assert recovered.store.get_attempt(abandoned[0]["attempt_id"])["terminal_reason"] == "LOST"
    _successful_jobs(recovered, monkeypatch)
    until(recovered, lambda: recovered.store.latest_transition("shared")["state"] == "COMPLETED")
    attempts = _attempts(recovered, original)
    assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in attempts] == [(1, "LOST"), (2, "SUCCEEDED")]
    final_dispatch = _dispatch(recovered, attempts[-1])
    assert final_dispatch["resource_grant"] == {"kind": "GPU", "gpu_uuid": GPU_B}
    assert GPU_B in final_dispatch["process_request"]["argv"]
    assert GPU_A not in final_dispatch["process_request"]["argv"]
    assert specs(recovered)[0] == original
    outcome = recovered.store.list_facts("shared", "CanonicalOutcomeAccepted")
    assert len(outcome) == 1 and outcome[0]["payload"]["outcome"] == "SUCCESS"
    artifact = recovered.store.inspect("SELECT attempt_id FROM execution_artifacts WHERE execution_id=?", (original["execution_id"],))
    assert artifact == ({"attempt_id": attempts[-1]["attempt_id"]},)
    assert [json.loads(row["grant_json"]) for row in _pool_rows(pool)] == [[GPU_A], [GPU_B]]
    _assert_closed(recovered, pool)


def test_cancellation_while_queued_uses_no_gpu_dispatch_and_releases_future_key(environment):
    make, free, pool, runtime = environment
    free.update({GPU_A: 1000, GPU_B: 1000})
    host = make("alpha")
    create(host, runtime)
    until(host, lambda: bool(specs(host)) and bool(host.resource_waits))
    original = specs(host)[0]
    assert _attempts(host, original) == ()
    queued = _pool_rows(pool)
    assert len(queued) == 1 and queued[0]["state"] == "QUEUED"
    assert json.loads(queued[0]["grant_json"]) == []
    response = host.handle(ControlRequest(1, "cancel-queued", host.store.latest_transition("shared")["revision"],
        "CANCEL", {"run_id": "shared", "cause": "bounded shared GPU queue cancellation"}))
    assert response.status == "accepted", response
    until(host, lambda: host.store.latest_transition("shared")["state"] == "CANCELLED")
    attempts = _attempts(host, original)
    assert len(attempts) == 1 and attempts[0]["terminal_reason"] == "CANCELLED"
    observed = json.loads(attempts[0]["observation_json"])
    assert observed["synthetic_before_spawn"] is True
    assert attempts[0]["process_group_id"] is None
    assert "--dev-bind" not in observed["process_request"]["argv"]
    assert not any(request.execution_id == original["execution_id"] for request in host.runner.started)
    assert specs(host)[0] == original
    rows = _pool_rows(pool)
    assert len(rows) == 1 and rows[0]["attempt_id"] == attempts[0]["attempt_id"]
    assert rows[0]["state"] == "RELEASED" and json.loads(rows[0]["grant_json"]) == []
    _assert_closed(host, pool)


def test_activation_waits_beyond_process_cap_retry_lost_without_scientific_failure(environment, monkeypatch):
    make, _, pool, runtime = environment
    host = make("alpha")
    create(host, runtime)
    until(host, lambda: bool(specs(host)) and host.store.latest_transition(specs(host)[0]["experiment_id"])["state"] == "READY")
    original = specs(host)[0]
    cap = host._effect_attempt_cap(host._research_contract("shared"), "process")
    failure_count = cap + 1
    original_activate = host.shared_gpu.activate
    activation_calls = []

    def activate(key, pgid):
        attempt = host.store.get_attempt(key["attempt_id"])
        assert attempt["state"] == "OPEN" and attempt["process_group_id"] == pgid
        activation_calls.append(key["attempt_id"])
        if len(activation_calls) <= failure_count:
            raise PoolError("resource_wait", "injected capacity loss while child is held")
        return original_activate(key, pgid)

    monkeypatch.setattr(host.shared_gpu, "activate", activate)
    _successful_jobs(host, monkeypatch)
    until(host, lambda: host.store.latest_transition("shared")["state"] == "COMPLETED")
    attempts = _attempts(host, original)
    assert len(attempts) == failure_count + 1
    assert [row["attempt_ordinal"] for row in attempts] == list(range(1, failure_count + 2))
    for row in attempts[:-1]:
        assert row["terminal_reason"] == "LOST"
        observed = json.loads(row["observation_json"])
        assert observed["resource_wait_before_exec"] is True
        assert observed["absence_proven"] is True
    assert attempts[-1]["terminal_reason"] == "SUCCEEDED"
    assert sum(request.execution_id == original["execution_id"] for request in host.runner.started) == 1
    assert specs(host)[0] == original
    outcomes = host.store.list_facts("shared", "CanonicalOutcomeAccepted")
    assert len(outcomes) == 1 and outcomes[0]["payload"]["outcome"] == "SUCCESS"
    assert {row["uid"] for row in _pool_rows(pool)} == {os.getuid()}
    assert len(_pool_rows(pool)) == failure_count + 1
    _assert_closed(host, pool)


@pytest.mark.parametrize("same_run", [True, False], ids=["same-run", "distinct-runs"])
def test_one_host_runs_two_open_experiments_on_the_same_gpu(environment, same_run):
    make, free, pool, runtime = environment
    free.update({GPU_A: 23000, GPU_B: 1000})
    host = make("alpha")
    run_ids = ("shared",) if same_run else ("shared", "second")
    if same_run:
        payload = _parallel_payload("shared", roles=("analyst", "critic"),
                                    component_count=2, allow_gpu=True, runtime_prefix=runtime)
        payload["research_contract"]["budget"].update(
            min_vram_per_experiment=8000, gpu_vram_reserve_mb=1000, max_active_processes=2)
        response = host.handle(ControlRequest(1, "create-shared-parallel", 0, "CREATE_RUN", payload))
        assert response.status == "accepted", response
    else:
        for run_id in run_ids:
            create(host, runtime, name=run_id)
    assert all(host._effect_capacity(run_id, "PROCESS") >= 2 for run_id in run_ids)

    def experiments():
        return [handle for handle in host.running.values()
                if not handle.request.execution_id.endswith("--candidate-validation")]

    until(host, lambda: len(experiments()) == 2)
    handles = experiments()
    attempts = [host.store.get_attempt(handle.request.attempt_id) for handle in handles]
    assert {row["state"] for row in attempts} == {"OPEN"}
    assert len({row["workflow_id"] for row in attempts}) == 2
    assert {row["run_id"] for row in attempts} == set(run_ids)
    dispatches = [_dispatch(host, attempt) for attempt in attempts]
    assert all(value["resource_grant"] == {"kind": "GPU", "gpu_uuid": GPU_A} for value in dispatches)
    assert len({value["role"] for value in dispatches}) == (2 if same_run else 1)
    before = [fact for run_id in run_ids for fact in host.store.list_facts(run_id, "ExperimentPreregistered")]
    bound = _pool_rows(pool)
    assert len(bound) == 2 and {row["state"] for row in bound} == {"BOUND"}
    assert {row["namespace"] for row in bound} == {"alpha"}
    assert {row["run_id"] for row in bound} == set(run_ids)
    assert len({row["attempt_id"] for row in bound}) == 2
    assert len({row["process_group_id"] for row in bound}) == 2
    assert {row["process_group_id"] for row in bound} == {row["process_group_id"] for row in attempts}
    assert all(json.loads(row["grant_json"]) == [GPU_A] for row in bound)
    assert audit_gpu_pool(pool.root)["ok"]

    for handle, attempt in zip(handles, attempts, strict=True):
        spec = next(fact for fact in before if fact["execution_id"] == attempt["execution_id"])
        (_output_binding(handle.request) / "result.json").write_text(json.dumps({
            "artifacts": {}, "identity": {"method_family_id": spec["payload"]["method_family_id"]},
            "trials": [{"seed": 0, "clean": True, "metrics": {"score": 3}}],
        }))
        handle.returncode = 0
    until(host, lambda: all(host.store.latest_transition(run_id)["state"] == "COMPLETED" for run_id in run_ids))
    assert [fact for run_id in run_ids for fact in host.store.list_facts(run_id, "ExperimentPreregistered")] == before
    outcomes = [fact for run_id in run_ids for fact in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")]
    assert len(outcomes) == 2 and {fact["payload"]["outcome"] for fact in outcomes} == {"SUCCESS"}
    _assert_closed(host, pool)
    for run_id in run_ids:
        report = Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id)
        assert report.ok and report.open_attempt_count == 0, report.issues
