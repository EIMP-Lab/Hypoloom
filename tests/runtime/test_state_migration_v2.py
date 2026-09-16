from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import hypoloom.state_store as state_store_module
from hypoloom.state_store import (
    HostWriterCapability,
    IdentityConflict,
    MigrationError,
    StateStore,
)


MIGRATIONS = Path(state_store_module.__file__).with_name("migrations")


def _v1_database(root: Path, *, with_data: bool) -> Path:
    root.mkdir(mode=0o700)
    database = root / "state.sqlite"
    with sqlite3.connect(database, isolation_level=None) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((MIGRATIONS / "0001_state.sql").read_text(encoding="utf-8"))
        if with_data:
            versions = ("w1", "r1", "e1", "a1", "runner1", "result1", "runtime1", "config1", sqlite3.sqlite_version)
            db.execute(
                "INSERT INTO workflow_runs VALUES (?,?, ?,?,?,?,?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                ("run-v1", None, *versions),
            )
            for command_id in ("agent-open", "agent-closed", "process-open", "process-closed"):
                db.execute(
                    "INSERT INTO workflow_commands(command_id,run_id,workflow_id,command_type,request_json,response_status,result_json) VALUES (?,?,?,?,?,'accepted','{}')",
                    (command_id, "run-v1", "workflow-v1", "TEST", "{}"),
                )
            db.execute(
                "INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,kind,attempt_ordinal,host_generation,state,started_at) VALUES ('attempt-agent-open','run-v1','workflow-v1','agent-open','AGENT',1,'host-v1','OPEN',1.0)"
            )
            db.execute(
                "INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,kind,attempt_ordinal,host_generation,state,started_at,ended_at,terminal_reason,observation_json) VALUES ('attempt-agent-closed','run-v1','workflow-v1','agent-closed','AGENT',1,'host-v1','CLOSED',1.0,2.0,'FAILED','{\"legacy\":true}')"
            )
            db.execute(
                "INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,kind,experiment_id,execution_id,attempt_ordinal,host_generation,state,started_at,process_group_id) VALUES ('attempt-process-open','run-v1','workflow-v1','process-open','PROCESS','experiment-v1','execution-v1',1,'host-v1','OPEN',1.0,4321)"
            )
            db.execute(
                "INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,kind,experiment_id,execution_id,attempt_ordinal,host_generation,state,started_at,ended_at,terminal_reason,process_group_id,observation_json) VALUES ('attempt-process-closed','run-v1','workflow-v1','process-closed','PROCESS','experiment-v2','execution-v2',1,'host-v1','CLOSED',1.0,2.0,'SUCCEEDED',4322,'{}')"
            )
            db.execute(
                "INSERT INTO execution_artifacts(artifact_id,run_id,experiment_id,execution_id,attempt_id,artifact_kind,schema_version,digest_algorithm,digest,path) VALUES ('artifact-v1','run-v1','experiment-v2','execution-v2','attempt-process-closed','result','1','sha256',?,'run-v1/result')",
                ("a" * 64,),
            )
    database.chmod(0o600)
    return database


def test_fresh_and_empty_v1_databases_upgrade_and_reopen_at_current_schema(tmp_path: Path) -> None:
    fresh = StateStore(tmp_path / "fresh", HostWriterCapability.issue()).initialize()
    assert fresh.inspect("PRAGMA user_version") == ({"user_version": 3},)
    assert fresh.inspect("SELECT version FROM schema_migrations ORDER BY version") == (
        {"version": 1},
        {"version": 2},
        {"version": 3},
    )
    fresh.close()
    reopened = StateStore(tmp_path / "fresh", HostWriterCapability.issue()).initialize()
    reopened.close()

    empty_root = tmp_path / "empty-v1"
    _v1_database(empty_root, with_data=False)
    upgraded = StateStore(empty_root, HostWriterCapability.issue()).initialize()
    assert upgraded.inspect("PRAGMA user_version") == ({"user_version": 3},)
    upgraded.close()


def test_automatic_upgrade_refuses_nonempty_v1_run_without_rewriting_it(tmp_path: Path) -> None:
    root = tmp_path / "retained-v1-copy"
    database = _v1_database(root, with_data=True)
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="nonempty schema 1"):
        StateStore(root, HostWriterCapability.issue()).initialize()
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT attempt_id,state FROM execution_attempts ORDER BY attempt_id").fetchall() == [
            ("attempt-agent-closed", "CLOSED"),
            ("attempt-agent-open", "OPEN"),
            ("attempt-process-closed", "CLOSED"),
            ("attempt-process-open", "OPEN"),
        ]
    assert database.read_bytes() == before


def test_0002_direct_temp_copy_upgrade_preserves_v1_rows_indexes_and_rules(tmp_path: Path) -> None:
    database = _v1_database(tmp_path / "migration-copy", with_data=True)
    with sqlite3.connect(database, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        before = [dict(row) for row in db.execute("SELECT * FROM execution_attempts ORDER BY attempt_id")]
        db.executescript((MIGRATIONS / "0002_agent_process_groups.sql").read_text(encoding="utf-8"))
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert [dict(row) for row in db.execute("SELECT * FROM execution_attempts ORDER BY attempt_id")] == before
        assert [row[0] for row in db.execute("SELECT attempt_id FROM execution_artifacts")] == [
            "attempt-process-closed"
        ]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {
            "execution_agent_attempt_ordinal",
            "execution_process_attempt_ordinal",
            "execution_one_open_agent_attempt",
            "execution_one_open_process_attempt",
        } <= indexes
        db.execute(
            "UPDATE execution_attempts SET process_group_id=4444 WHERE attempt_id='attempt-agent-open'"
        )
        with pytest.raises(sqlite3.IntegrityError, match="one process bind"):
            db.execute(
                "UPDATE execution_attempts SET process_group_id=5555 WHERE attempt_id='attempt-agent-open'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,kind,attempt_ordinal,host_generation,state,started_at,process_group_id) VALUES ('bad','run-v1','workflow-v1','agent-open','AGENT',2,'host-v1','OPEN',3.0,0)"
            )


def test_interrupted_0002_rolls_back_then_clean_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "interrupted-v1"
    database = _v1_database(root, with_data=False)
    original = state_store_module._MIGRATIONS
    broken = tmp_path / "0002_broken.sql"
    script = original[1].read_text(encoding="utf-8")
    broken.write_text(
        script.replace("INSERT INTO schema_migrations(version) VALUES(2);", "SELECT no_such_function();"),
        encoding="utf-8",
    )
    monkeypatch.setattr(state_store_module, "_MIGRATIONS", (original[0], broken))
    with pytest.raises(sqlite3.OperationalError, match="no such function"):
        StateStore(root, HostWriterCapability.issue()).initialize()
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_attempts_v2'"
        ).fetchall() == []

    monkeypatch.setattr(state_store_module, "_MIGRATIONS", original)
    store = StateStore(root, HostWriterCapability.issue()).initialize()
    assert store.inspect("PRAGMA user_version") == ({"user_version": 3},)
    store.close()


def test_agent_open_intent_process_binding_and_lost_close_preserve_request(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state", HostWriterCapability.issue()).initialize()
    try:
        with store.transaction() as tx:
            tx.create_run(
                "run-1",
                {
                    "workflow_definition_version": "w1",
                    "research_contract_version": "r1",
                    "evaluator_version": "e1",
                    "agent_aci_version": "2",
                    "runner_version": "runner1",
                    "artifact_result_schema_version": "result1",
                    "runtime_environment_id": "runtime1",
                    "configuration_id": "config1",
                    "sqlite_runtime_version": sqlite3.sqlite_version,
                },
            )
            tx.record_command(
                {
                    "command_id": "agent-command",
                    "run_id": "run-1",
                    "workflow_id": "run-1",
                    "command_type": "REQUEST_AGENT",
                    "request": {},
                    "status": "accepted",
                    "result": {},
                }
            )
            attempt = {
                "attempt_id": "agent-attempt",
                "run_id": "run-1",
                "workflow_id": "run-1",
                "command_id": "agent-command",
                "kind": "AGENT",
                "attempt_ordinal": 1,
                "host_generation": "host-1",
                "started_at": 1.0,
            }
            with pytest.raises(ValueError, match="request intent"):
                tx.open_attempt(attempt)
            request = {"attempt_id": "agent-attempt", "attempt_ordinal": 1, "prompt": "bounded"}
            assert tx.open_attempt({**attempt, "payload": {"request": request}})
            assert tx.bind_attempt_process("agent-attempt", 7654)
        assert json.loads(store.get_attempt("agent-attempt")["observation_json"]) == {"request": request}  # type: ignore[index]
        with store.transaction() as tx:
            with pytest.raises(IdentityConflict, match="request intent"):
                tx.close_attempt(
                    "agent-attempt",
                    ended_at=2.0,
                    terminal_reason="LOST",
                    payload={"request": {**request, "attempt_ordinal": 2}, "reason": "parent_lost"},
                )
            assert tx.close_attempt(
                "agent-attempt",
                ended_at=2.0,
                terminal_reason="LOST",
                payload={"reason": "parent_lost"},
            )
        terminal = json.loads(store.get_attempt("agent-attempt")["observation_json"])  # type: ignore[index]
        assert terminal == {"request": request, "reason": "parent_lost"}
    finally:
        store.close()
