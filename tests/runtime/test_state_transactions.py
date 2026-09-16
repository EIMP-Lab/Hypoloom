from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import hypoloom.state_store as state_store_module
from hypoloom.domain import RunVersion
from hypoloom import recovery
from hypoloom.state_store import (
    HostWriterCapability,
    IdentityConflict,
    RevisionConflict,
    SQLiteRuntimeError,
    StateStore,
    WriterCapabilityError,
)


def run_version() -> RunVersion:
    return RunVersion(
        workflow_definition_version="workflow-1",
        research_contract_version="research-1",
        evaluator_version="evaluator-1",
        agent_aci_version="aci-1",
        runner_version="runner-1",
        artifact_result_schema_version="result-1",
        runtime_environment_id="runtime-1",
        configuration_id="config-1",
        sqlite_runtime_version=sqlite3.sqlite_version,
    )


def open_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state", HostWriterCapability.issue()).initialize()


def command(
    tx: object,
    command_id: str,
    *,
    run_id: str = "run-1",
    workflow_id: str = "run-1",
    command_type: str = "TEST",
    expected_revision: int | None = None,
    revision: int | None = None,
) -> None:
    tx.record_command(  # type: ignore[attr-defined]
        {
            "command_id": command_id,
            "run_id": run_id,
            "workflow_id": workflow_id,
            "expected_revision": expected_revision,
            "command_type": command_type,
            "request": {"command": command_type},
            "status": "accepted",
            "result": {},
            "revision": revision,
        }
    )


def transition(
    command_id: str,
    revision: int,
    state: str,
    *,
    workflow_id: str = "run-1",
    kind: str = "RUN",
    previous_state: str | None = None,
    fact_id: str | None = None,
) -> dict[str, object]:
    return {
        "workflow_id": workflow_id,
        "revision": revision,
        "run_id": "run-1",
        "workflow_kind": kind,
        "previous_state": previous_state,
        "state": state,
        "command_id": command_id,
        "fact_id": fact_id,
    }


def goal_fact(*, objective: str = "answer") -> dict[str, object]:
    return {
        "fact_id": "fact-goal",
        "run_id": "run-1",
        "fact_type": "GoalDefined",
        "schema_version": 1,
        "subject_id": "goal-1",
        "goal_id": "goal-1",
        "payload": {"objective": objective},
        "refs": [],
    }


def seed_run(store: StateStore) -> None:
    assert store.create_run("run-1", run_version())


def test_production_connection_factory_enforces_runtime_pragmas_and_permissions(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path)
    try:
        assert store.inspect("PRAGMA journal_mode") == ({"journal_mode": "wal"},)
        assert store.inspect("PRAGMA synchronous") == ({"synchronous": 2},)
        assert store.inspect("PRAGMA foreign_keys") == ({"foreign_keys": 1},)
        assert store.inspect("PRAGMA busy_timeout") == ({"timeout": 5000},)
        assert store.inspect("PRAGMA wal_autocheckpoint") == ({"wal_autocheckpoint": 1000},)
        assert os.stat(store.state_root).st_mode & 0o777 == 0o700
        assert os.stat(store.database_path).st_mode & 0o777 == 0o600
        assert store.database_path.name == "state.sqlite"
    finally:
        store.close()


def test_production_migration_failure_rolls_back_and_clean_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = state_store_module._MIGRATIONS
    broken = tmp_path / "0001-broken.sql"
    script = original[0].read_text(encoding="utf-8")
    broken.write_text(script.replace("COMMIT;", "SELECT no_such_function();\nCOMMIT;"), encoding="utf-8")
    root = tmp_path / "state"
    monkeypatch.setattr(state_store_module, "_MIGRATIONS", (broken, original[1]))
    with pytest.raises(sqlite3.OperationalError, match="no such function"):
        StateStore(root, HostWriterCapability.issue()).initialize()

    with sqlite3.connect(root / "state.sqlite") as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
        assert raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall() == []

    monkeypatch.setattr(state_store_module, "_MIGRATIONS", original)
    store = StateStore(root, HostWriterCapability.issue()).initialize()
    try:
        assert store.inspect("PRAGMA user_version") == ({"user_version": 3},)
        assert store.inspect("SELECT version FROM schema_migrations") == ({"version": 1}, {"version": 2}, {"version": 3})
    finally:
        store.close()


def test_every_connection_rejects_old_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 45, 3))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.45.3")
    with pytest.raises(SQLiteRuntimeError, match="3.51.3"):
        StateStore(tmp_path / "state", HostWriterCapability.issue()).initialize()


def test_read_only_store_cannot_create_or_begin_a_transaction(tmp_path: Path) -> None:
    root = tmp_path / "state"
    with pytest.raises(WriterCapabilityError, match="cannot create"):
        StateStore(root).initialize()
    writer = StateStore(root, HostWriterCapability.issue()).initialize()
    seed_run(writer)
    writer.close()

    reader = StateStore(root).initialize()
    try:
        assert reader.get_run("run-1") is not None
        with pytest.raises(WriterCapabilityError, match="capability"):
            with reader.transaction():
                pass
        with reader.readonly_connection() as connection:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("DELETE FROM workflow_runs")
    finally:
        reader.close()
    with pytest.raises(TypeError, match="issue"):
        HostWriterCapability(object())


def test_state_transactions_take_the_immediate_single_writer_lock(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    contender = sqlite3.connect(store.database_path, isolation_level=None, timeout=0.001)
    contender.execute("PRAGMA busy_timeout=1")
    try:
        with store.transaction():
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
    finally:
        contender.close()
        store.close()


def test_host_owned_checkpoint_policy_is_explicit_and_read_only_callers_cannot_run_it(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    try:
        seed_run(store)
        busy, _, remaining = store.checkpoint_truncate()
        assert busy == 0
        assert remaining == 0
    finally:
        store.close()
    reader = StateStore(tmp_path / "state").initialize()
    try:
        with pytest.raises(WriterCapabilityError, match="Host writer"):
            reader.checkpoint_truncate()
    finally:
        reader.close()


def test_recovery_surface_contains_only_the_six_accepted_queries(tmp_path: Path) -> None:
    rules = (
        recovery.abandoned_attempt_to_lost,
        recovery.due_dispatch,
        recovery.expired_deadline,
        recovery.interrupted_evaluation,
        recovery.resume_waiting_decision,
        recovery.resume_terminalizing_run,
    )
    assert len(rules) == 6
    store = open_store(tmp_path)
    try:
        with store.transaction() as tx:
            assert recovery.abandoned_attempt_to_lost(tx, "host-1") == ()
            assert recovery.due_dispatch(tx, 0.0) == ()
            assert recovery.expired_deadline(tx, 0.0) == ()
            assert recovery.interrupted_evaluation(tx) == ()
            assert recovery.resume_waiting_decision(tx) == ()
            assert recovery.resume_terminalizing_run(tx) == ()
    finally:
        store.close()


def test_fact_and_transition_are_atomic_append_only_and_idempotent(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    try:
        seed_run(store)
        with pytest.raises(RuntimeError, match="injected"):
            with store.transaction() as tx:
                command(tx, "create")
                tx.append_fact(goal_fact())
                tx.append_transition(transition("create", 1, "CREATED", fact_id="fact-goal"))
                raise RuntimeError("injected")
        assert store.list_facts("run-1") == ()
        assert store.latest_transition("run-1") is None

        with store.transaction() as tx:
            command(tx, "create")
            assert tx.append_fact(goal_fact())
            assert tx.append_transition(transition("create", 1, "CREATED", fact_id="fact-goal"))
        with store.transaction() as tx:
            assert not tx.append_fact(goal_fact())
            assert not tx.append_transition(transition("create", 1, "CREATED", fact_id="fact-goal"))
            with pytest.raises(IdentityConflict, match="fact"):
                tx.append_fact(goal_fact(objective="changed"))
        assert len(store.list_facts("run-1")) == 1
        with store.transaction() as tx:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                tx.execute("UPDATE research_facts SET subject_id='other' WHERE fact_id='fact-goal'")
    finally:
        store.close()


def test_unknown_persisted_vocabularies_fail_at_the_write_boundary(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    try:
        seed_run(store)
        with pytest.raises(IdentityConflict, match="constraint conflict"):
            with store.transaction() as tx:
                command(tx, "unknown-state")
                tx.append_transition(transition("unknown-state", 1, "MYSTERY"))
        with pytest.raises(IdentityConflict, match="constraint conflict"):
            with store.transaction() as tx:
                tx.append_fact(
                    {
                        **goal_fact(),
                        "fact_id": "unknown-fact",
                        "subject_id": "unknown",
                        "goal_id": None,
                        "fact_type": "ProcessSpawned",
                    }
                )
        assert store.latest_transition("run-1") is None
        assert store.list_facts("run-1") == ()
    finally:
        store.close()


def test_revisions_and_command_request_ids_fail_closed(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    try:
        seed_run(store)
        calls = 0

        def apply(tx: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            tx.append_transition(transition("create", 1, "CREATED"))  # type: ignore[attr-defined]
            return {"status": "accepted", "revision": 1, "result": {"run_id": "run-1"}}

        request = {
            "command_id": "create",
            "run_id": "run-1",
            "workflow_id": "run-1",
            "expected_revision": None,
            "command_type": "CREATE_RUN",
            "request": {"run_id": "run-1"},
        }
        assert not store.execute_command(request, apply).replayed
        assert store.execute_command(request, apply).replayed
        assert calls == 1
        with pytest.raises(IdentityConflict, match="request conflict"):
            store.execute_command({**request, "request": {"run_id": "other"}}, apply)
        with pytest.raises(RevisionConflict, match="expected revision 1, got 0"):
            store.execute_command(
                {**request, "command_id": "stale", "expected_revision": 0},
                lambda tx: {},
            )
        with store.transaction() as tx:
            command(tx, "skip", expected_revision=1)
            with pytest.raises(RevisionConflict, match="expected revision 2"):
                tx.append_transition(transition("skip", 3, "ACTIVE", previous_state="CREATED"))
    finally:
        store.close()


def test_attempt_identity_process_binding_terminal_and_retry_rules(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    try:
        seed_run(store)
        with store.transaction() as tx:
            command(tx, "execute", workflow_id="experiment-1")
            tx.append_transition(
                transition(
                    "execute",
                    1,
                    "EXECUTING",
                    workflow_id="experiment-1",
                    kind="EXPERIMENT",
                )
            )
            attempt = {
                "attempt_id": "attempt-1",
                "run_id": "run-1",
                "workflow_id": "experiment-1",
                "command_id": "execute",
                "kind": "PROCESS",
                "experiment_id": "experiment-1",
                "execution_id": "execution-1",
                "attempt_ordinal": 1,
                "host_generation": "host-1",
                "started_at": 1.0,
            }
            assert tx.open_attempt(attempt)
            with pytest.raises(IdentityConflict, match="prior attempt"):
                tx.open_attempt({**attempt, "attempt_id": "attempt-2", "attempt_ordinal": 2})
        with store.transaction() as tx:
            assert tx.bind_attempt_process("attempt-1", 4321)
            assert not tx.bind_attempt_process("attempt-1", 4321)
            with pytest.raises(IdentityConflict, match="another process group"):
                tx.bind_attempt_process("attempt-1", 4322)
        terminal = {
            "terminal_reason": "FAILED",
            "ended_at": 2.0,
            "observation": {"exit_code": 1},
        }
        with store.transaction() as tx:
            assert tx.close_attempt("attempt-1", terminal)
            assert not tx.close_attempt("attempt-1", terminal)
            with pytest.raises(IdentityConflict, match="conflicting terminal"):
                tx.close_attempt("attempt-1", {**terminal, "terminal_reason": "SUCCEEDED"})
            assert tx.open_attempt(
                {
                    "attempt_id": "attempt-2",
                    "run_id": "run-1",
                    "workflow_id": "experiment-1",
                    "command_id": "execute",
                    "kind": "PROCESS",
                    "experiment_id": "experiment-1",
                    "execution_id": "execution-1",
                    "attempt_ordinal": 2,
                    "host_generation": "host-1",
                    "started_at": 3.0,
                }
            )
        attempts = store.list_attempts(execution_id="execution-1")
        assert [(row["attempt_ordinal"], row["state"]) for row in attempts] == [
            (1, "CLOSED"),
            (2, "OPEN"),
        ]
    finally:
        store.close()


def test_artifact_outcome_and_terminal_transition_share_one_transaction(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    try:
        seed_run(store)
        with store.transaction() as tx:
            command(tx, "observe", workflow_id="experiment-1")
            tx.append_transition(
                transition(
                    "observe",
                    1,
                    "EVALUATING",
                    workflow_id="experiment-1",
                    kind="EXPERIMENT",
                )
            )
            tx.open_attempt(
                {
                    "attempt_id": "attempt-1",
                    "run_id": "run-1",
                    "workflow_id": "experiment-1",
                    "command_id": "observe",
                    "kind": "PROCESS",
                    "experiment_id": "experiment-1",
                    "execution_id": "execution-1",
                    "attempt_ordinal": 1,
                    "host_generation": "host-1",
                    "started_at": 1.0,
                }
            )
            tx.close_attempt(
                "attempt-1",
                {
                    "terminal_reason": "SUCCEEDED",
                    "ended_at": 2.0,
                    "observation": {"exit_code": 0},
                },
            )
            command(tx, "accept", workflow_id="experiment-1", expected_revision=1, revision=2)
        digest = "a" * 64
        artifact = {
            "artifact_id": "artifact-1",
            "run_id": "run-1",
            "experiment_id": "experiment-1",
            "execution_id": "execution-1",
            "attempt_id": "attempt-1",
            "artifact_kind": "result",
            "schema_version": 1,
            "digest_algorithm": "sha256",
            "digest": digest,
            "path": "run-1/experiment-1/execution-1/attempt-1/result",
        }
        fact = {
            "fact_id": "fact-outcome",
            "run_id": "run-1",
            "fact_type": "CanonicalOutcomeAccepted",
            "schema_version": 1,
            "subject_id": "outcome-1",
            "experiment_id": "experiment-1",
            "execution_id": "execution-1",
            "outcome_id": "outcome-1",
            "artifact_id": "artifact-1",
            "payload": {"outcome": "SUCCESS", "artifact_set_digest": digest},
        }
        final = transition(
            "accept",
            2,
            "TERMINAL",
            workflow_id="experiment-1",
            kind="EXPERIMENT",
            previous_state="EVALUATING",
            fact_id="fact-outcome",
        )
        assert store.accept_outcome(artifact, fact, final) == (True, True, True)
        with store.transaction() as tx:
            assert not tx.record_artifact(artifact)
        assert store.latest_transition("experiment-1")["state"] == "TERMINAL"  # type: ignore[index]
        assert len(store.inspect("SELECT * FROM execution_artifacts")) == 1
        with store.transaction() as tx:
            with pytest.raises(IdentityConflict, match="constraint conflict"):
                tx.append_fact({**fact, "fact_id": "fact-outcome-2", "subject_id": "outcome-2", "outcome_id": "outcome-2"})
        assert len(store.list_facts("run-1", "CanonicalOutcomeAccepted")) == 1
    finally:
        store.close()
