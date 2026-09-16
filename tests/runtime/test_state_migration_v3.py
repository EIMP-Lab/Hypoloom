from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import hypoloom.state_store as state_store_module
from hypoloom.state_store import HostWriterCapability, MigrationError, StateStore


MIGRATIONS = Path(state_store_module.__file__).with_name("migrations")


def _v2_artifact_database(root: Path, artifact_path: str) -> Path:
    root.mkdir(mode=0o700)
    database = root / "state.sqlite"
    with sqlite3.connect(database, isolation_level=None) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((MIGRATIONS / "0001_state.sql").read_text(encoding="utf-8"))
        db.executescript((MIGRATIONS / "0002_agent_process_groups.sql").read_text(encoding="utf-8"))
        versions = ("w", "r", "e", "2", "runner", "result", "runtime", "config", sqlite3.sqlite_version)
        db.execute(
            "INSERT INTO workflow_runs VALUES (?,?, ?,?,?,?,?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            ("run-1", None, *versions),
        )
        db.execute(
            "INSERT INTO workflow_commands(command_id,run_id,workflow_id,command_type,request_json,response_status,result_json) "
            "VALUES ('cmd','run-1','experiment-1','TEST','{}','accepted','{}')"
        )
        db.execute(
            "INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,kind,experiment_id,execution_id,"
            "attempt_ordinal,host_generation,state,started_at,ended_at,terminal_reason,observation_json) "
            "VALUES ('attempt-1','run-1','experiment-1','cmd','PROCESS','experiment-1','execution-1',1,'host',"
            "'CLOSED',1,2,'SUCCEEDED','{}')"
        )
        db.execute(
            "INSERT INTO execution_artifacts(artifact_id,run_id,experiment_id,execution_id,attempt_id,artifact_kind,"
            "schema_version,digest_algorithm,digest,path) VALUES "
            "('artifact-1','run-1','experiment-1','execution-1','attempt-1','result','2','sha256',?,?)",
            ("a" * 64, artifact_path),
        )
    database.chmod(0o600)
    return database


def test_0003_authorized_copy_converts_verified_absolute_path_to_stable_relative(tmp_path: Path) -> None:
    stable = "run-1/experiment-1/execution-1/attempt-1/result"
    database = _v2_artifact_database(tmp_path / "copy", f"/old/root/artifacts/{stable}")
    with sqlite3.connect(database, isolation_level=None) as db:
        db.executescript((MIGRATIONS / "0003_portable_artifact_paths.sql").read_text(encoding="utf-8"))
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT path FROM execution_artifacts").fetchone()[0] == stable
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE execution_artifacts SET path='other'")


def test_0003_rejects_unrelated_legacy_path_without_partial_rewrite(tmp_path: Path) -> None:
    database = _v2_artifact_database(tmp_path / "bad-copy", "/wrong/artifact")
    before = database.read_bytes()
    with sqlite3.connect(database, isolation_level=None) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.executescript((MIGRATIONS / "0003_portable_artifact_paths.sql").read_text(encoding="utf-8"))
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT path FROM execution_artifacts").fetchone()[0] == "/wrong/artifact"
    assert database.read_bytes() == before


def test_automatic_upgrade_refuses_nonempty_v2_database(tmp_path: Path) -> None:
    root = tmp_path / "retained-v2-copy"
    stable = "run-1/experiment-1/execution-1/attempt-1/result"
    database = _v2_artifact_database(root, f"/old/root/artifacts/{stable}")
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="nonempty schema 2"):
        StateStore(root, HostWriterCapability.issue()).initialize()
    assert database.read_bytes() == before
