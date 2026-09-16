"""Host/store/socket/pool closure with synthetic GPU observations and jobs.

These exercise no CUDA, GPU device access, or paid Agent calls. Process-shaped
jobs deliberately stay open until the test completes their synthetic result.
"""

import json
import sqlite3

import pytest

from hypoloom.gpu_pool import PoolError
from hypoloom.oracle import Oracle, audit_gpu_pool
from test_gpu_host_recovery import _output_binding
from test_shared_gpu_dispatch import GPU_A, GPU_B, create, specs, until
from test_shared_gpu_dispatch import environment as environment


def _experiment_running(host):
    return tuple(
        handle
        for handle in host.running.values()
        if not handle.request.execution_id.endswith("--candidate-validation")
    )


def _pool_rows(pool):
    with sqlite3.connect(pool.database_path) as connection:
        connection.row_factory = sqlite3.Row
        return tuple(dict(row) for row in connection.execute("SELECT * FROM requests"))


def _finish(host):
    host.runner.complete = True
    for handle in tuple(host.running.values()):
        (_output_binding(handle.request) / "result.json").write_text(
            '{"score":1}', encoding="utf-8"
        )
        handle.returncode = 0
    until(host, lambda: host.store.latest_transition("shared")["state"] == "COMPLETED")


def _clean(host):
    report = Oracle(
        host.state_root / "state.sqlite", host.state_root / "artifacts"
    ).audit("shared")
    assert report.ok, report.issues
    assert report.canonical_outcome_count > 0
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.duplicate_effect_count == 0
    assert report.owned_process_residue == ()
    assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
    assert not host.running
    return report


def test_shared_gpu_run_closes_collection_publication_and_independent_oracles(
    environment,
):
    make, free, pool, runtime = environment
    host = make("alpha")
    host.runner.complete = True
    create(host, runtime)
    _finish(host)

    _clean(host)
    assert specs(host)
    assert all(
        "resource_intent" in fact["payload"]["execution_contract"]
        for fact in specs(host)
    )
    rows = _pool_rows(pool)
    assert rows and all(row["state"] == "RELEASED" for row in rows)
    assert audit_gpu_pool(pool.root)["ok"]


def test_independent_roots_overlap_one_gpu_without_sharing_private_run_state(
    environment,
):
    make, free, pool, runtime = environment
    free.update({GPU_A: 18000, GPU_B: 1000})
    first, second = make("alpha"), make("beta")
    for host in (first, second):
        create(host, runtime)
        until(host, lambda host=host: bool(_experiment_running(host)))

    assert first.state_root != second.state_root
    rows = _pool_rows(pool)
    bound = [row for row in rows if row["state"] == "BOUND"]
    assert len(bound) == 2
    assert {row["namespace"] for row in bound} == {"alpha", "beta"}
    assert (
        len({(row["run_id"], row["execution_id"], row["attempt_id"]) for row in bound})
        == 1
    )
    assert all(json.loads(row["grant_json"]) == [GPU_A] for row in bound)
    assert audit_gpu_pool(pool.root)["ok"]

    _finish(first)
    _clean(first)
    assert _experiment_running(second)
    assert all(
        row["state"] == "RELEASED"
        for row in _pool_rows(pool)
        if row["namespace"] == "alpha"
    )
    assert any(
        row["state"] == "BOUND"
        for row in _pool_rows(pool)
        if row["namespace"] == "beta"
    )
    _finish(second)
    _clean(second)
    assert all(row["state"] == "RELEASED" for row in _pool_rows(pool))
    assert audit_gpu_pool(pool.root)["ok"]


def test_completing_owner_releases_capacity_for_waiting_independent_root(environment):
    make, free, pool, runtime = environment
    free.update({GPU_A: 12000, GPU_B: 1000})
    first, second = make("alpha"), make("beta")
    create(first, runtime)
    until(first, lambda: bool(_experiment_running(first)))
    create(second, runtime)
    until(second, lambda: bool(specs(second)) and bool(second.resource_waits))
    waiting_execution = specs(second)[0]["execution_id"]
    assert not second.store.list_attempts(execution_id=waiting_execution)
    assert not second.store.list_facts("shared", "CanonicalOutcomeAccepted")
    assert any(
        row["state"] == "QUEUED" and row["namespace"] == "beta"
        for row in _pool_rows(pool)
    )

    _finish(first)
    _clean(first)
    until(second, lambda: bool(_experiment_running(second)))
    attempts = second.store.list_attempts(execution_id=waiting_execution)
    assert len(attempts) == 1 and attempts[0]["attempt_ordinal"] == 1
    bound = [
        row
        for row in _pool_rows(pool)
        if row["namespace"] == "beta" and row["state"] == "BOUND"
    ]
    assert len(bound) == 1 and json.loads(bound[0]["grant_json"]) == [GPU_A]
    _finish(second)
    _clean(second)
    assert all(row["state"] == "RELEASED" for row in _pool_rows(pool))
    assert audit_gpu_pool(pool.root)["ok"]


def test_queued_work_can_use_a_different_gpu_when_only_that_device_recovers(environment):
    make, free, pool, runtime = environment
    host = make("capacity-recovery")
    # The request snapshot favors A, but neither GPU has dispatch capacity.
    # This reproduces the retained nonpooled run's unavailable pinned device.
    free.update({GPU_A: 1448, GPU_B: 1000})
    create(host, runtime)
    until(host, lambda: bool(specs(host)) and bool(host.resource_waits))
    frozen = specs(host)[0]
    execution = frozen["execution_id"]
    assert not host.store.list_attempts(execution_id=execution)
    queued = _pool_rows(pool)
    assert len(queued) == 1 and queued[0]["state"] == "QUEUED"
    assert json.loads(queued[0]["grant_json"]) == []

    free[GPU_B] = 16000
    until(host, lambda: bool(_experiment_running(host)))
    attempts = host.store.list_attempts(execution_id=execution)
    assert len(attempts) == 1 and attempts[0]["attempt_ordinal"] == 1
    assert free[GPU_A] == 1448
    assert specs(host)[0] == frozen
    bound = _pool_rows(pool)
    assert len(bound) == 1 and json.loads(bound[0]["grant_json"]) == [GPU_B]
    _finish(host)
    _clean(host)
    assert all(row["state"] == "RELEASED" for row in _pool_rows(pool))
    assert audit_gpu_pool(pool.root)["ok"]


def test_capacity_flips_between_reservation_and_activation_reselect_without_duplicate_execution(
    environment, monkeypatch,
):
    make, free, pool, runtime = environment
    host = make("changing-load")
    free.update({GPU_A: 18000, GPU_B: 12000})
    activate = host.shared_gpu.activate
    selected = []

    def change_capacity(key, pgid):
        row = next(row for row in _pool_rows(pool) if row["attempt_id"] == key["attempt_id"])
        selected.append(json.loads(row["grant_json"]))
        if len(selected) == 1:
            free.update({GPU_A: 1000, GPU_B: 16000})
        elif len(selected) == 2:
            free.update({GPU_A: 16000, GPU_B: 1000})
        activate(key, pgid)

    monkeypatch.setattr(host.shared_gpu, "activate", change_capacity)
    create(host, runtime)
    until(host, lambda: bool(specs(host)))
    frozen = specs(host)[0]
    until(host, lambda: bool(_experiment_running(host)))
    assert selected == [[GPU_A], [GPU_B], [GPU_A]]
    assert specs(host)[0] == frozen
    execution = frozen["execution_id"]
    attempts = host.store.list_attempts(execution_id=execution)
    assert [row["terminal_reason"] for row in attempts] == ["LOST", "LOST", None]
    assert all(json.loads(row["observation_json"])["resource_wait_before_exec"] for row in attempts[:2])
    assert len([request for request in host.runner.started if request.execution_id == execution]) == 1
    _finish(host)
    _clean(host)
    assert len(host.store.list_facts("shared", "CanonicalOutcomeAccepted")) == 1
    assert all(row["state"] == "RELEASED" for row in _pool_rows(pool))
    assert audit_gpu_pool(pool.root)["ok"]


def _activation_losses(host, monkeypatch):
    activate = host.shared_gpu.activate
    calls = []

    def transient_capacity_loss(key, pgid):
        calls.append(dict(key))
        if len(calls) <= 3:
            raise PoolError("resource_wait", "synthetic co-resident capacity loss")
        activate(key, pgid)

    monkeypatch.setattr(host.shared_gpu, "activate", transient_capacity_loss)
    return calls


def test_preexec_capacity_losses_do_not_exhaust_process_retries(
    environment, monkeypatch
):
    make, free, pool, runtime = environment
    host = make("alpha")
    calls = _activation_losses(host, monkeypatch)
    host.runner.complete = True
    create(host, runtime)
    _finish(host)
    _clean(host)

    execution = specs(host)[0]["execution_id"]
    attempts = host.store.list_attempts(execution_id=execution)
    assert len(attempts) == 4
    assert [row["attempt_ordinal"] for row in attempts] == [1, 2, 3, 4]
    assert [row["terminal_reason"] for row in attempts] == ["LOST"] * 3 + ["SUCCEEDED"]
    assert len({key["attempt_id"] for key in calls}) == 4
    started = [
        request for request in host.runner.started if request.execution_id == execution
    ]
    assert len(started) == 1 and started[0].attempt_ordinal == 4
    for row in attempts[:3]:
        observation = json.loads(row["observation_json"])
        assert observation["resource_wait_before_exec"] is True
        assert observation["absence_proven"] is True
        assert not host.store.inspect(
            "SELECT artifact_id FROM execution_artifacts WHERE attempt_id=?",
            (row["attempt_id"],),
        )
    assert all(row["state"] == "RELEASED" for row in _pool_rows(pool))
    assert audit_gpu_pool(pool.root)["ok"]


@pytest.mark.parametrize(
    "corruption", ["successful_process", "absence_missing", "synthetic"]
)
def test_oracle_requires_raw_preexec_loss_proof(environment, monkeypatch, corruption):
    make, free, pool, runtime = environment
    host = make("alpha")
    _activation_losses(host, monkeypatch)
    host.runner.complete = True
    create(host, runtime)
    _finish(host)
    _clean(host)

    attempts = host.store.list_attempts(execution_id=specs(host)[0]["execution_id"])
    attempt = attempts[-1] if corruption == "successful_process" else attempts[0]
    observation = json.loads(attempt["observation_json"])
    if corruption == "successful_process":
        observation.update(resource_wait_before_exec=True, absence_proven=True)
    elif corruption == "absence_missing":
        observation.pop("absence_proven")
    else:
        observation["synthetic_before_spawn"] = True
    # Corrupt a disposable copy of this test's database. The actual Host rows
    # and their append-only enforcement remain intact.
    copied = host.state_root / "corrupted.sqlite"
    with (
        sqlite3.connect(host.state_root / "state.sqlite") as source,
        sqlite3.connect(copied) as connection,
    ):
        source.backup(connection)
        connection.execute("DROP TRIGGER execution_attempt_update_rules")
        connection.execute(
            "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
            (json.dumps(observation), attempt["attempt_id"]),
        )
    report = Oracle(copied, host.state_root / "artifacts").audit("shared")
    assert "resource_wait_proof_invalid" in {issue.code for issue in report.issues}
