from __future__ import annotations

import multiprocessing
import os
import sqlite3
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from hypoloom.backup_restore import BackupConflict, BackupIntegrityError, PublishedBackup, create_quiesced_backup, restore_backup
from hypoloom.oracle import Oracle, audit_all_runs


def _quiescent_calls():
    seen: list[str] = []

    def assert_quiescent(root: Path, phase: str) -> None:
        assert root.is_dir(); seen.append(phase)

    return seen, assert_quiescent


def _simple_state_root(tmp_path: Path, *, with_artifact_record: bool = False) -> Path:
    root = tmp_path / "state-root"
    root.mkdir(mode=0o700)
    artifacts_root = root / "artifacts"
    artifacts_root.mkdir(mode=0o700)
    with sqlite3.connect(root / "state.sqlite", isolation_level=None) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE item(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("INSERT INTO item VALUES (1, 'committed')")
        if with_artifact_record:
            artifact = artifacts_root / "run-1" / "experiment-1" / "execution-1" / "attempt-1" / "result"
            artifact.mkdir(parents=True, exist_ok=True)
            (artifact / "result.json").write_text('{"score":1}', encoding="utf-8")
            db.execute(
                "CREATE TABLE execution_artifacts("
                "artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, experiment_id TEXT NOT NULL, "
                "execution_id TEXT NOT NULL, attempt_id TEXT NOT NULL, artifact_kind TEXT NOT NULL, path TEXT NOT NULL)"
            )
            db.execute(
                "INSERT INTO execution_artifacts VALUES(?,?,?,?,?,?,?)",
                (
                    "artifact-1", "run-1", "experiment-1", "execution-1", "attempt-1", "result",
                    str(artifact),
                ),
            )
    return root


def _actual_hypoloom_state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state-root"
    root.mkdir(mode=0o700)
    (root / "artifacts").mkdir(mode=0o700)
    migration = Path("hypoloom/migrations/0001_state.sql").read_text(encoding="utf-8")
    with sqlite3.connect(root / "state.sqlite", isolation_level=None) as db:
        db.executescript(migration)
        db.execute("CREATE TABLE aux(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("INSERT INTO aux VALUES (1, 'seed')")
        db.execute(
            "INSERT INTO workflow_runs("
            "run_id,derived_from_run_id,workflow_definition_version,research_contract_version,"
            "evaluator_version,agent_aci_version,runner_version,artifact_result_schema_version,"
            "runtime_environment_id,configuration_id,sqlite_runtime_version"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("run-1", None, "wf/1", "research/1", "eval/1", "aci/1", "runner/1", "artifact/1", "runtime/1", "config/1", "3.53.4"),
        )
        db.execute(
            "INSERT INTO workflow_commands(command_id,run_id,workflow_id,expected_revision,command_type,request_json,response_status,result_json,error_json,accepted_revision) "
            "VALUES (?,?,?,?,?,'{}','accepted','{}',NULL,1)",
            ("cmd-1", "run-1", "experiment-1", 0, "EXECUTE"),
        )
        db.execute(
            "INSERT INTO execution_attempts(attempt_id,run_id,workflow_id,command_id,kind,experiment_id,execution_id,attempt_ordinal,host_generation,state,started_at,ended_at,terminal_reason,process_group_id,observation_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,'CLOSED',?,?,?,NULL,'{}')",
            ("attempt-1", "run-1", "experiment-1", "cmd-1", "PROCESS", "experiment-1", "execution-1", 1, "host-1", 1.0, 2.0, "SUCCEEDED"),
        )
        db.execute(
            "INSERT INTO execution_artifacts(artifact_id,run_id,experiment_id,execution_id,attempt_id,artifact_kind,schema_version,digest_algorithm,digest,path) "
            "VALUES (?,?,?,?,?,'result','1','sha256',?,?)",
            ("artifact-1", "run-1", "experiment-1", "execution-1", "attempt-1", "0" * 64, "run-1/experiment-1/execution-1/attempt-1/result"),
        )
    return root


def _backup_names(path: Path) -> set[str]:
    return {item.name for item in path.iterdir()} if path.exists() else set()


def test_backup_retains_context_git_history_views_and_executable_mode(tmp_path):
    source = _simple_state_root(tmp_path)
    with sqlite3.connect(source / "state.sqlite") as db:
        db.execute("CREATE TABLE workflow_runs(run_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO workflow_runs VALUES('run-1')")
    repositories = ("run-1/experts/scientist/plugin", "run-1/experts/scientist/memory",
                    "run-1/grounding/plugin")
    commits = {}
    for relative in repositories:
        repository = source / relative
        repository.mkdir(parents=True)
        script = repository / "source.sh"
        script.write_text("#!/bin/sh\nexit 0\n"); script.chmod(0o700)
        for args in (("init", "--quiet"), ("add", "."),
                     ("-c", "user.name=Test", "-c", "user.email=test@invalid.example",
                      "commit", "--quiet", "-m", "source")):
            subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
        commits[relative] = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"])
    view = "run-1/experts/scientist/views/request-1/experts/scientist/state.json"
    (source / view).parent.mkdir(parents=True)
    (source / view).write_text('{"role":"scientist"}\n')
    (source / "run-1/experts/scientist/native").mkdir()
    (source / "run-1/experts/scientist/native/credential").write_text("excluded synthetic credential")
    (source / "host-settings.json").write_text("excluded synthetic settings")
    phases, quiescent = _quiescent_calls()
    published = create_quiesced_backup(source, tmp_path / "backups", "context",
        assert_quiescent=quiescent, audit=lambda _: None)
    restored = restore_backup(published.path, tmp_path / "restored", audit=lambda _: None)
    assert phases == ["start", "after_db_backup", "after_artifact_copy"]
    for root in (published.path, restored):
        for relative in repositories:
            repository = root / relative
            assert subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"]) == commits[relative]
            assert subprocess.check_output(["git", "-C", str(repository), "show", "HEAD:source.sh"]) == b"#!/bin/sh\nexit 0\n"
            assert (repository / "source.sh").stat().st_mode & 0o100
        assert (root / view).read_bytes() == (source / view).read_bytes()
        assert not (root / "run-1/experts/scientist/native").exists()
        assert not (root / "host-settings.json").exists()


@pytest.mark.parametrize("relative", ["run-1", "run-1/experts", "run-1/experts/scientist",
                                      "run-1/experts/scientist/plugin"])
def test_backup_rejects_indirect_context_roots(tmp_path, relative):
    source = _simple_state_root(tmp_path)
    with sqlite3.connect(source / "state.sqlite") as db:
        db.execute("CREATE TABLE workflow_runs(run_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO workflow_runs VALUES('run-1')")
    outside = tmp_path / "outside"; outside.mkdir()
    path = source / relative; path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside, target_is_directory=True)
    _, quiescent = _quiescent_calls()
    with pytest.raises(BackupIntegrityError, match="real directory"):
        create_quiesced_backup(source, tmp_path / "backups", "context",
            assert_quiescent=quiescent, audit=lambda _: None)
    assert not (tmp_path / "backups/context").exists()


def test_create_quiesced_backup_excludes_uncommitted_writes_and_nonbackup_files(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    (source / "control.sock").write_text("ignored", encoding="utf-8")
    (source / "controller.lock").write_text("ignored", encoding="utf-8")
    pending = sqlite3.connect(source / "state.sqlite", isolation_level=None)
    pending.execute("BEGIN IMMEDIATE")
    pending.execute("INSERT INTO item VALUES (2, 'uncommitted')")
    phases, assert_quiescent = _quiescent_calls()
    observed: list[tuple[int, ...]] = []

    def audit(root: Path) -> None:
        with sqlite3.connect(root / "state.sqlite", isolation_level=None) as db:
            observed.append(tuple(row[0] for row in db.execute("SELECT id FROM item ORDER BY id")))

    backup_store = tmp_path / "backups"
    published = create_quiesced_backup(
        source,
        backup_store,
        "backup-a",
        assert_quiescent=assert_quiescent,
        audit=audit,
    )
    pending.execute("ROLLBACK")
    pending.close()
    assert published == PublishedBackup(path=backup_store / "backup-a", reused=False)
    assert phases == ["start", "after_db_backup", "after_artifact_copy"]
    assert observed == [(1,)]
    assert _backup_names(published.path) == {"state.sqlite", "artifacts"}
    assert not (published.path / "control.sock").exists()
    assert not (published.path / "controller.lock").exists()


def test_existing_backup_is_verified_and_reused_without_new_snapshot(tmp_path: Path) -> None:
    source = _actual_hypoloom_state_root(tmp_path)
    phases, assert_quiescent = _quiescent_calls()
    backup_store = tmp_path / "backups"
    first = create_quiesced_backup(
        source,
        backup_store,
        "oracle-backup",
        assert_quiescent=assert_quiescent,
        audit=lambda root: None,
    )
    second = create_quiesced_backup(
        source,
        backup_store,
        "oracle-backup",
        assert_quiescent=assert_quiescent,
        audit=lambda root: None,
    )
    assert not first.reused
    assert second.reused
    with sqlite3.connect(source / "state.sqlite", isolation_level=None) as db:
        db.execute("INSERT INTO aux VALUES (2, 'changed')")
    third = create_quiesced_backup(
        source,
        backup_store,
        "oracle-backup",
        assert_quiescent=assert_quiescent,
        audit=lambda root: None,
    )
    assert third.reused
    assert not any(name.startswith(".tmp-oracle-backup-") for name in _backup_names(backup_store))
    assert phases == ["start", "after_db_backup", "after_artifact_copy"]


def test_backup_rejects_symlinked_artifact_and_cleans_temp_on_ordinary_failure(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source / "artifacts" / "bad").symlink_to(outside)
    backup_store = tmp_path / "backups"
    _, assert_quiescent = _quiescent_calls()
    with pytest.raises(Exception):
        create_quiesced_backup(
            source,
            backup_store,
            "bad-symlink",
            assert_quiescent=assert_quiescent,
            audit=lambda root: None,
        )
    assert not any(name.startswith(".tmp-bad-symlink-") for name in _backup_names(backup_store))


def test_backup_audit_rejection_cleans_temp_and_restore_target_must_not_exist(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    backup_store = tmp_path / "backups"
    _, assert_quiescent = _quiescent_calls()
    with pytest.raises(RuntimeError, match="reject"):
        create_quiesced_backup(
            source,
            backup_store,
            "audit-reject",
            assert_quiescent=assert_quiescent,
            audit=lambda root: (_ for _ in ()).throw(RuntimeError("reject")),
        )
    assert not any(name.startswith(".tmp-audit-reject-") for name in _backup_names(backup_store))
    published = create_quiesced_backup(
        source,
        backup_store,
        "restore-src",
        assert_quiescent=assert_quiescent,
        audit=lambda root: None,
    )
    target = tmp_path / "restore-target"
    target.mkdir()
    with pytest.raises(BackupConflict, match="already exists"):
        restore_backup(published.path, target, audit=lambda root: None)


def test_backup_and_restore_do_not_chmod_existing_parent_directories(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    existing_parent = tmp_path / "operator-owned-parent"
    existing_parent.mkdir(mode=0o755)
    existing_parent.chmod(0o755)
    backup_store = existing_parent / "backups"
    backup_store.mkdir(mode=0o755)
    backup_store.chmod(0o755)
    _, assert_quiescent = _quiescent_calls()
    published = create_quiesced_backup(
        source,
        backup_store,
        "permission-safe",
        assert_quiescent=assert_quiescent,
        audit=lambda root: None,
    )
    assert stat.S_IMODE(existing_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(backup_store.stat().st_mode) == 0o755
    restored = restore_backup(
        published.path,
        existing_parent / "restored-state",
        audit=lambda root: None,
    )
    assert stat.S_IMODE(existing_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(restored.stat().st_mode) == 0o700


def test_backup_os_exit_before_rename_leaves_temp_and_final_invisible(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    backup_store = tmp_path / "backups"
    backup_store.mkdir()
    backup_id = "crash-before-rename"

    def worker() -> None:
        create_quiesced_backup(
            source,
            backup_store,
            backup_id,
            assert_quiescent=lambda root, phase: None,
            audit=lambda root: None,
            fault_hook=lambda phase, root: os._exit(91) if phase == "before_rename" else None,
        )

    process = multiprocessing.get_context("fork").Process(target=worker)
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 91
    assert not (backup_store / backup_id).exists()
    assert any(name.startswith(f".tmp-{backup_id}-") for name in _backup_names(backup_store))


def test_after_rename_failure_preserves_final_target(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    backup_store = tmp_path / "backups"
    _, assert_quiescent = _quiescent_calls()
    with pytest.raises(RuntimeError, match="after-rename"):
        create_quiesced_backup(
            source,
            backup_store,
            "backup-after-rename",
            assert_quiescent=assert_quiescent,
            audit=lambda root: None,
            fault_hook=lambda phase, root: (_ for _ in ()).throw(RuntimeError("after-rename")) if phase == "after_rename" else None,
        )
    assert (backup_store / "backup-after-rename").is_dir()


def test_restore_backup_runs_oracle_audit_and_cleans_temp_on_ordinary_failure(tmp_path: Path) -> None:
    source = _actual_hypoloom_state_root(tmp_path)
    backup_store = tmp_path / "backups"
    _, assert_quiescent = _quiescent_calls()
    published = create_quiesced_backup(
        source,
        backup_store,
        "oracle-backup",
        assert_quiescent=assert_quiescent,
        audit=lambda root: None,
    )
    seen: list[tuple[Path, Path]] = []

    @dataclass(frozen=True)
    class _Report:
        ok: bool = True
        issues: tuple[str, ...] = ()

    original_audit = Oracle.audit

    def fake_audit(self: Oracle, run_id: str):
        seen.append((self.state_db, self.artifact_root))
        return _Report()

    Oracle.audit = fake_audit
    try:
        def audit(root: Path) -> None:
            report = Oracle(root / "state.sqlite", root / "artifacts").audit("ignored")
            assert report.ok

        restored = restore_backup(
            published.path,
            tmp_path / "restored-root",
            audit=audit,
        )
        assert restored == tmp_path / "restored-root"
        audited_db, audited_artifacts = seen[-1]
        assert audited_db.name == "state.sqlite"
        assert audited_artifacts.name == "artifacts"
        assert audited_db.parent.name.startswith(".tmp-restored-root-")
        assert audited_artifacts.parent == audited_db.parent
        with pytest.raises(RuntimeError, match="restore-reject"):
            restore_backup(
                published.path,
                tmp_path / "restored-reject",
                audit=lambda root: (_ for _ in ()).throw(RuntimeError("restore-reject")),
            )
    finally:
        Oracle.audit = original_audit
    assert not any(name.startswith(".tmp-restored-reject-") for name in _backup_names(tmp_path))


def test_backup_rejects_source_target_overlap(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    nested = source / "nested-backups"
    with pytest.raises(BackupConflict, match="must not overlap"):
        create_quiesced_backup(
            source,
            nested,
            "backup-a",
            assert_quiescent=lambda *_: None,
            audit=lambda _: None,
        )
    assert not nested.exists()


def test_backup_leaf_symlink_and_restore_into_backup_are_rejected(tmp_path: Path) -> None:
    source = _simple_state_root(tmp_path)
    backup_store = tmp_path / "backups"
    _, assert_quiescent = _quiescent_calls()
    published = create_quiesced_backup(
        source,
        backup_store,
        "source-copy",
        assert_quiescent=assert_quiescent,
        audit=lambda _: None,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (backup_store / "redirected").symlink_to(outside, target_is_directory=True)
    with pytest.raises(BackupConflict, match="not a real directory"):
        create_quiesced_backup(
            source,
            backup_store,
            "redirected",
            assert_quiescent=assert_quiescent,
            audit=lambda _: None,
        )
    assert tuple(outside.iterdir()) == ()
    with pytest.raises(BackupConflict, match="must not overlap"):
        restore_backup(published.path, published.path / "nested-restore", audit=lambda _: None)
    assert {item.name for item in published.path.iterdir()} == {"state.sqlite", "artifacts"}


def test_all_runs_oracle_callback_checks_every_copied_workflow_run(tmp_path: Path) -> None:
    root = _actual_hypoloom_state_root(tmp_path)
    with pytest.raises(RuntimeError, match="run-1"):
        audit_all_runs(root)
