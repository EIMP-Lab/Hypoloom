"""Automatic archival is only a newly reached, configured terminal boundary."""
import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.workflow_host import WorkflowHost
from test_session_archive import fixture, source_tree


@pytest.mark.parametrize("old_terminal", [False, True])
def test_host_archives_new_terminal_only_and_never_replays_existing_history(tmp_path, old_terminal):
    root = fixture(tmp_path, terminal=old_terminal)
    source_tree(tmp_path, root)
    host = WorkflowHost(root)
    try:
        host.initialize()
        if not old_terminal:
            result = host.handle(ControlRequest(schema_version=1, request_id="new-cancel", expected_revision=None,
                command="CANCEL", payload={"run_id": "archive-run", "cause": "test terminal scope"}))
            assert result.status == "accepted"
        host.tick()
        assert host.store.latest_transition("archive-run")["state"] == "CANCELLED"
        assert (root / "archive-run/archives/sessions.tar.gz").exists() is not old_terminal
        assert (root / "archive-run/sessions").exists() is old_terminal
        host.tick()  # Already terminal is not archived again.
    finally:
        host.close()


def test_archive_failure_retains_original_and_reports_status_without_scientific_failure(tmp_path, monkeypatch):
    import hypoloom.session_archive as archive
    root = fixture(tmp_path, terminal=False)
    source_tree(tmp_path, root)
    def fail(*args):
        raise OSError("injected archive disk failure")
    monkeypatch.setattr(archive, "archive_run_sessions", fail)
    host = WorkflowHost(root)
    try:
        host.initialize()
        host.handle(ControlRequest(schema_version=1, request_id="new-cancel", expected_revision=None,
            command="CANCEL", payload={"run_id": "archive-run", "cause": "test archive failure"}))
        host.tick()
        status = host.handle(ControlRequest(schema_version=1, request_id="check", expected_revision=None,
            command="STATUS", payload={"run_id": "archive-run"}))
        assert status.result["run_state"] == "CANCELLED"
        assert "disk failure" in status.result["session_retention"]["error"]
        assert (root / "archive-run/sessions").is_dir()
        assert not host.store.list_facts("archive-run", "GoalVerdictRecorded")
    finally:
        host.close()
