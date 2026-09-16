from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

from hypoloom import cli
from hypoloom.control_protocol import ControlRequest
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost


_ROOT = Path(__file__).resolve().parents[2]


def _create_payload(
    run_id: str,
    *,
    roles: tuple[str, ...] = ("scientist",),
    proposers: tuple[str, ...] = ("scientist",),
) -> dict[str, object]:
    admission = admit_profile(
        _ROOT / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        allow_gpu=False,
        delay_seconds=0,
    )
    assert admission.admitted, admission.to_dict()
    assert admission.create_run_payload is not None
    payload = copy.deepcopy(admission.create_run_payload)
    contract = payload["research_contract"]
    assert isinstance(contract, dict)
    role_template = contract["roles"][0]
    contract["roles"] = [{**copy.deepcopy(role_template), "id": role} for role in roles]
    component = contract["components"][0]
    component["owner"] = roles[0]
    component["proposers"] = list(proposers)
    component["result"]["target"] = "1"
    contract["goal"]["summary_metric"]["target"] = "1"
    contract["budget"]["max_experiments"] = 1
    return payload


def _create(host: WorkflowHost, run_id: str) -> None:
    response = host.handle(
        ControlRequest(
            1,
            f"create-{run_id}",
            0,
            "CREATE_RUN",
            _create_payload(run_id),
        )
    )
    assert response.status == "accepted", response


def _complete(host: WorkflowHost, run_id: str) -> None:
    for _ in range(100):
        host.tick()
        if host.store.latest_transition(run_id)["state"] == "COMPLETED":
            return
    raise AssertionError("run did not complete")


def test_live_host_backup_restore_and_restored_host_status(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    backup_root = tmp_path / "backups"
    restored = tmp_path / "restored"
    host = WorkflowHost(source)
    host.initialize()
    try:
        _create(host, "artifact-run")
        _complete(host, "artifact-run")
        _create(host, "second-artifact-run")
        _complete(host, "second-artifact-run")
        response = host.handle(
            ControlRequest(
                1,
                "backup-1",
                None,
                "BACKUP",
                {
                    "backup_root": str(backup_root),
                    "backup_id": "artifact-copy",
                },
            )
        )
        assert response.status == "accepted", response
        assert response.result is not None
        assert response.result["path"] == str(backup_root / "artifact-copy")
        assert response.result["reused"] is False
        assert response.result["checkpoint"]["status"] == "ok"
        assert response.result["checkpoint"]["busy"] is False
        reused = host.handle(
            ControlRequest(
                1,
                "backup-2",
                None,
                "BACKUP",
                {
                    "backup_root": str(backup_root),
                    "backup_id": "artifact-copy",
                },
            )
        )
        assert reused.status == "accepted" and reused.result is not None
        assert reused.result["reused"] is True
    finally:
        host.close()

    with sqlite3.connect(source / "state.sqlite") as db:
        artifact_path = db.execute("SELECT path FROM execution_artifacts").fetchone()[0]
    assert (
        cli.main(
            (
                "restore",
                "--backup-path",
                str(backup_root / "artifact-copy"),
                "--state-root",
                str(restored),
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["result"]["path"] == str(restored)
    assert not (restored / "control.sock").exists()
    with sqlite3.connect(restored / "state.sqlite") as db:
        assert (
            db.execute("SELECT path FROM execution_artifacts").fetchone()[0]
            == artifact_path
        )
    report = Oracle(restored / "state.sqlite", restored / "artifacts").audit(
        "artifact-run"
    )
    assert report.ok, report.issues
    second_report = Oracle(restored / "state.sqlite", restored / "artifacts").audit(
        "second-artifact-run"
    )
    assert second_report.ok, second_report.issues
    assert (
        cli.main(("oracle", "--state-root", str(restored), "--run-id", "artifact-run"))
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ok"] is True
    restored_host = WorkflowHost(restored)
    restored_host.initialize()
    try:
        status = restored_host.handle(
            ControlRequest(
                1, "status-restored", None, "STATUS", {"run_id": "artifact-run"}
            )
        )
        assert status.status == "accepted" and status.result is not None
        assert status.result["run_state"] == "COMPLETED"
        assert status.result["state_sqlite_bytes"] > 0
        assert status.result["wal_autocheckpoint"] == 1000
        assert status.result["quiescent_for_backup"] is True
        assert status.result["agent_usage"]["observed_lower_bound"]["tokens"] >= 0
        assert status.result["agent_usage"]["unobserved_closed_attempts"] == 0
        assert status.result["agent_usage"]["remaining_declared_limits"] == {
            "attempts": 0,
            "tokens": 0,
            "turns": 0,
            "cost": "0",
            "currency": "USD",
        }
    finally:
        restored_host.close()


def test_backup_rejects_memory_or_durable_open_attempt_without_publishing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    host = WorkflowHost(source)
    host.initialize()
    try:
        _create(host, "open-run")
        host.running["synthetic"] = object()  # type: ignore[assignment]
        response = host.handle(
            ControlRequest(
                1,
                "busy-memory",
                None,
                "BACKUP",
                {
                    "backup_root": str(tmp_path / "backups"),
                    "backup_id": "busy-memory",
                },
            )
        )
        assert response.status == "rejected"
        assert response.error and response.error["code"] == "non_quiescent"
        assert not (tmp_path / "backups" / "busy-memory").exists()
        host.running.clear()
        initial = host.handle(
            ControlRequest(
                1,
                "existing",
                None,
                "BACKUP",
                {
                    "backup_root": str(tmp_path / "backups"),
                    "backup_id": "existing",
                },
            )
        )
        assert initial.status == "accepted"
        host.running["synthetic"] = object()  # type: ignore[assignment]
        response = host.handle(
            ControlRequest(
                1,
                "busy-reuse",
                None,
                "BACKUP",
                {
                    "backup_root": str(tmp_path / "backups"),
                    "backup_id": "existing",
                },
            )
        )
        assert response.status == "rejected"
        assert response.error and response.error["code"] == "non_quiescent"
        host.running.clear()
        with host.store.transaction() as tx:
            tx.open_attempt(
                {
                    "attempt_id": "open-agent",
                    "run_id": "open-run",
                    "workflow_id": "open-run",
                    "command_id": "create-open-run",
                    "kind": "AGENT",
                    "attempt_ordinal": 1,
                    "host_generation": host.generation,
                    "started_at": 1.0,
                    "payload": {"request": {}},
                }
            )
        response = host.handle(
            ControlRequest(
                1,
                "busy-db",
                None,
                "BACKUP",
                {
                    "backup_root": str(tmp_path / "backups"),
                    "backup_id": "busy-db",
                },
            )
        )
        assert response.status == "rejected"
        assert response.error and response.error["code"] == "non_quiescent"
        assert not (tmp_path / "backups" / "busy-db").exists()
    finally:
        host.close()


def test_checkpoint_failure_does_not_hide_an_already_published_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    host = WorkflowHost(tmp_path / "source")
    host.initialize()
    try:
        monkeypatch.setattr(
            host.store,
            "checkpoint_truncate",
            lambda: (_ for _ in ()).throw(
                sqlite3.OperationalError("checkpoint failed")
            ),
        )
        response = host.handle(
            ControlRequest(
                1,
                "backup-checkpoint-error",
                None,
                "BACKUP",
                {
                    "backup_root": str(tmp_path / "backups"),
                    "backup_id": "published",
                },
            )
        )
        assert response.status == "accepted" and response.result is not None
        assert response.result["checkpoint"] == {
            "status": "error",
            "error": "OperationalError: checkpoint failed",
        }
        assert (tmp_path / "backups" / "published" / "state.sqlite").is_file()
    finally:
        host.close()


def test_status_uses_each_persisted_attempt_budget_and_separates_future_capacity(
    tmp_path: Path,
) -> None:
    host = WorkflowHost(tmp_path / "source")
    host.initialize()
    try:
        _create(host, "mixed-budgets")
        with host.store.transaction() as tx:
            for ordinal, maximum in ((1, 5), (2, 100)):
                attempt_id = f"mixed-agent-{ordinal}"
                tx.open_attempt(
                    {
                        "attempt_id": attempt_id,
                        "run_id": "mixed-budgets",
                        "workflow_id": "mixed-budgets",
                        "command_id": "create-mixed-budgets",
                        "kind": "AGENT",
                        "attempt_ordinal": ordinal,
                        "host_generation": host.generation,
                        "started_at": float(ordinal),
                        "payload": {
                            "request": {
                                "budget": {
                                    "max_tokens": maximum,
                                    "max_turns": maximum,
                                    "max_cost": str(maximum),
                                }
                            }
                        },
                    }
                )
                details = (
                    {
                        "diagnostics": {
                            "provider_accounting": {
                                "token_usage": {
                                    "input_tokens": 37,
                                    "output_tokens": 11,
                                    "total_tokens": 48,
                                },
                                "turn_count": 2,
                                "cost": "0.108265",
                                "cost_currency": "USD",
                            }
                        }
                    }
                    if ordinal == 1
                    else {}
                )
                tx.close_attempt(
                    attempt_id,
                    terminal_reason="FAILED",
                    ended_at=float(ordinal) + 0.1,
                    payload={"failure_code": "no_response", **details},
                )
        response = host.handle(
            ControlRequest(
                1, "mixed-status", None, "STATUS", {"run_id": "mixed-budgets"}
            )
        )
        assert response.status == "accepted" and response.result is not None
        usage = response.result["agent_usage"]
        assert usage["observed_lower_bound"] == {
            "responses": 0,
            "provider_error_reports": 1,
            "rejected_responses": 0,
            "discarded_responses": 0,
            "tokens": 48,
            "turns": 2,
            "cost": "0.108265",
            "currency": "USD",
        }
        assert usage["unobserved_closed_attempts"] == 1
        assert usage["unobserved_declared_limits"] == {
            "attempts": 1,
            "tokens": 100,
            "turns": 100,
            "cost": "100",
            "currency": "USD",
        }
        assert usage["remaining_declared_limits"] == {
            # Legacy profiles omit the new infrastructure field. The frozen
            # fallback permits a mixed lineage of two model Attempts and
            # three infrastructure Attempts, with one terminal slot shared.
            "attempts": 6,
            "tokens": 3072,
            "turns": 12,
            "cost": "0",
            "currency": "USD",
        }
        assert usage["declared_limits"] == {
            "attempts": 8,
            "tokens": 3177,
            "turns": 117,
            "cost": "105",
            "currency": "USD",
        }
    finally:
        host.close()


def test_status_declared_limits_include_two_role_rendezvous_capacity(
    tmp_path: Path,
) -> None:
    host = WorkflowHost(tmp_path / "source")
    host.initialize()
    try:
        response = host.handle(
            ControlRequest(
                1,
                "create-parallel-budget",
                0,
                "CREATE_RUN",
                _create_payload(
                    "parallel-budget",
                    roles=("scientist", "critic"),
                    proposers=("scientist", "critic"),
                ),
            )
        )
        assert response.status == "accepted", response
        status = host.handle(
            ControlRequest(
                1,
                "parallel-budget-status",
                None,
                "STATUS",
                {
                    "run_id": "parallel-budget",
                },
            )
        )
        assert status.status == "accepted" and status.result is not None
        usage = status.result["agent_usage"]
        assert usage["declared_limits"] == {
            "attempts": 24,
            "tokens": 12288,
            "turns": 48,
            "cost": "0",
            "currency": "USD",
        }
        assert usage["remaining_declared_limits"] == usage["declared_limits"]
    finally:
        host.close()
