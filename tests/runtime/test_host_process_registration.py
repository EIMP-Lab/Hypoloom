"""Real Host/Runner/bwrap registration, with fresh runs and no GPU or model."""
from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import time
from pathlib import Path

import pytest

from hypoloom.oracle import Oracle
from hypoloom.runner import RUNNER_VERSION, LocalRunner, process_group_exists
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_phase6_host_operations import _create


def _finish(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        host.tick()
        if host.store.latest_transition(run_id)["state"] == "COMPLETED":
            return
        time.sleep(.01)
    raise AssertionError("bounded Host run did not complete")


def test_host_records_process_group_before_any_workload_exec(tmp_path: Path) -> None:
    host = WorkflowHost(tmp_path / "state")
    host.initialize()
    registrations = []
    actual = LocalRunner()

    class ObservedRunner(LocalRunner):
        def start(self, request, *, before_exec=None):
            assert callable(before_exec), "Host must provide pre-exec registration"
            def register(pgid):
                assert host.store.get_attempt(request.attempt_id)["process_group_id"] is None
                assert process_group_exists(pgid)
                before_exec(pgid)
                # Independent reader observes the committed binding before release.
                with sqlite3.connect(f"file:{host.store.database_path}?mode=ro", uri=True) as db:
                    stored = db.execute("SELECT process_group_id FROM execution_attempts WHERE attempt_id=?",
                                        (request.attempt_id,)).fetchone()[0]
                assert stored == pgid
                for relative in (request.stdout_path, request.stderr_path):
                    if relative is not None:
                        assert (Path(request.cwd) / relative).read_bytes() == b""
                registrations.append((request.attempt_id, pgid))
            return actual.start(request, before_exec=register)

        def wait(self, *args, **kwargs):
            return actual.wait(*args, **kwargs)

        def cancel(self, *args, **kwargs):
            return actual.cancel(*args, **kwargs)

        def cleanup(self, *args, **kwargs):
            return actual.cleanup(*args, **kwargs)

    host.runner = ObservedRunner()
    try:
        _create(host, "registered")
        _finish(host, "registered")
        assert registrations
        assert host.version.runner_version == RUNNER_VERSION == "local-process-group/3"
        process_rows = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS"]
        assert {row["attempt_id"] for row in process_rows} == {item[0] for item in registrations}
        assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
        assert all(not process_group_exists(pgid) for _, pgid in registrations)
        report = Oracle(host.store.database_path, host.publisher.root).audit("registered")
        assert report.ok, report.to_dict()
    finally:
        host.close()


@pytest.mark.parametrize("replacement", [None, 2_000_000_001])
def test_oracle_rejects_terminal_process_registration_mismatch(tmp_path: Path, replacement) -> None:
    host = WorkflowHost(tmp_path / "source")
    host.initialize()
    try:
        _create(host, "registration-audit")
        _finish(host, "registration-audit")
        source = host.store.database_path
        artifacts = host.publisher.root
        original = Oracle(source, artifacts).audit("registration-audit")
        assert original.ok, original.to_dict()
    finally:
        host.close()
    copied = tmp_path / "tampered.sqlite"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as reader, sqlite3.connect(copied) as writer:
        reader.backup(writer)
        writer.execute("DROP TRIGGER execution_attempt_update_rules")
        writer.execute("UPDATE execution_attempts SET process_group_id=? WHERE kind='PROCESS' AND terminal_reason='SUCCEEDED'",
                       (replacement,))
    report = Oracle(copied, artifacts).audit("registration-audit")
    assert "process_registration_mismatch" in {issue.code for issue in report.issues}


@pytest.mark.parametrize("phase", ["validation", "experiment"])
def test_callback_error_after_registration_keeps_the_binding_and_never_runs_payload(tmp_path: Path, phase: str) -> None:
    host = WorkflowHost(tmp_path / "state")
    host.initialize()
    selected = []
    def fail(point: str) -> None:
        if selected or point != "after_process_registration_before_exec":
            return
        attempt = next(row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["state"] == "OPEN")
        if attempt["execution_id"].endswith("--candidate-validation") == (phase == "validation"):
            selected.append(attempt)
            raise OSError("registration-completed-release-refused")
    host._crash = fail
    try:
        _create(host, "registration-error")
        deadline = time.monotonic() + 10
        while not selected and time.monotonic() < deadline:
            host.tick()
            time.sleep(.01)
        assert len(selected) == 1
        previous = selected[0]
        row = host.store.get_attempt(previous["attempt_id"])
        assert row["state"] == "CLOSED" and row["terminal_reason"] == "FAILED"
        assert row["process_group_id"] == previous["process_group_id"]
        assert not process_group_exists(row["process_group_id"])
        observed = json.loads(row["observation_json"])
        request = observed["process_request"]
        if phase == "validation":
            assert observed["stdout_tail"] == observed["stderr_tail"] == ""
            assert not Path(request["cwd"]).exists()  # Existing validation cleanup.
        else:
            for relative in (request["stdout_path"], request["stderr_path"]):
                if relative is not None:
                    assert (Path(request["cwd"]) / relative).read_bytes() == b""
        assert not host.running
    finally:
        host.close()


@pytest.mark.parametrize("version,required", [("local-process-group/1", False),
    ("local-process-group/2", True), ("local-process-group/3", True)])
def test_registration_audit_keeps_old_version_semantics_and_future_threshold(version, required):
    from hypoloom.oracle import _audit_process_registration
    attempts = [{"attempt_id": "a", "kind": "PROCESS", "state": "CLOSED",
                 "terminal_reason": "SUCCEEDED", "process_group_id": None}]
    issues = []
    _audit_process_registration(version, attempts, {"a": {"process_group_id": 123}}, issues)
    assert bool(issues) is required


@pytest.mark.parametrize("reason", ["LOST", "FAILED", "CANCELLED", "TIMED_OUT"])
def test_registration_audit_does_not_invent_unspawned_process_observations(reason):
    from hypoloom.oracle import _audit_process_registration
    attempts = [{"attempt_id": "a", "kind": "PROCESS", "state": "CLOSED",
                 "terminal_reason": reason, "process_group_id": None}]
    issues = []
    _audit_process_registration("local-process-group/2", attempts, {"a": {}}, issues)
    assert not issues


def _crashing_host(root: Path, point: str, phase: str) -> None:
    host = WorkflowHost(root)
    host.initialize()
    def crash(selected: str) -> None:
        if selected != point:
            return
        attempts = [row for row in host.store.list_attempts() if row["kind"] == "PROCESS" and row["state"] == "OPEN"]
        assert len(attempts) == 1
        validation = attempts[0]["execution_id"].endswith("--candidate-validation")
        if validation == (phase == "validation"):
            os._exit(86)
    host._crash = crash
    try:
        _create(host, "registration-crash")
        _finish(host, "registration-crash")
    finally:
        host.close()


@pytest.mark.parametrize("point,bound", [
    ("before_process_registration", False),
    ("after_process_registration_before_exec", True),
])
@pytest.mark.parametrize("phase", ["validation", "experiment"])
def test_host_registration_crash_recovers_without_executing_unregistered_job(
    tmp_path: Path, point: str, bound: bool, phase: str,
) -> None:
    root = tmp_path / "state"
    child = multiprocessing.get_context("fork").Process(target=_crashing_host, args=(root, point, phase))
    child.start()
    try:
        child.join(timeout=25)
        assert child.exitcode == 86, "expected pre-exec Host crash was not reached"
        with sqlite3.connect(f"file:{root / 'state.sqlite'}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute("SELECT * FROM execution_attempts WHERE kind='PROCESS' AND state='OPEN'")]
            assert len(rows) == 1 and rows[0]["state"] == "OPEN"
            row = rows[0]
            assert (row["process_group_id"] is not None) is bound
            intent = json.loads(db.execute("SELECT payload_json FROM workflow_transitions WHERE "
                "json_extract(payload_json,'$.attempt_id')=?", (row["attempt_id"],)).fetchone()[0])
        request = intent["process_request"]
        for relative in (request["stdout_path"], request["stderr_path"]):
            if relative is not None:
                assert (Path(request["cwd"]) / relative).read_bytes() == b""
        host = WorkflowHost(root)
        host.initialize()
        try:
            _finish(host, "registration-crash")
            previous = host.store.get_attempt(row["attempt_id"])
            assert previous["state"] == "CLOSED" and previous["terminal_reason"] == "LOST"
            attempts = host.store.list_attempts()
            assert all(item["state"] == "CLOSED" for item in attempts)
            assert all(item["process_group_id"] is None or not process_group_exists(item["process_group_id"])
                       for item in attempts)
            assert len(host.store.list_facts("registration-crash", "CanonicalOutcomeAccepted")) == 1
            report = Oracle(host.store.database_path, host.publisher.root).audit("registration-crash")
            assert report.ok, report.to_dict()
        finally:
            host.close()
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=3)
        assert not child.is_alive()
