from __future__ import annotations

import io
import json
import copy
import socket
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import yaml

import hypoloom.monitor as monitor_module
import hypoloom.research_export as export_module
from hypoloom.cli import main as cli_main
from hypoloom.control_protocol import ControlRequest
from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.monitor import build_monitor_snapshot, main as monitor_main, run_monitor
from hypoloom.research_export import ResearchExportError, export_research_view
from hypoloom.state_store import StateStore, StateTransaction
from hypoloom.workflow_host import WorkflowHost
from test_expert_loop_host import (
    _ReviseOnceAgent,
    _artifact_snapshot,
    _authority_snapshot,
    _drive,
    _pilot_payload,
)
from test_monitor import _create_fresh_run


@pytest.fixture(scope="module")
def export_mount_root():
    # /tmp is private scratch inside the production process sandbox.
    with tempfile.TemporaryDirectory(prefix="hypoloom-research-export-", dir="/var/tmp") as raw:
        yield Path(raw)


@pytest.fixture(scope="module")
def completed_source(
    tmp_path_factory: pytest.TempPathFactory, export_mount_root: Path,
) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("research-export-source")
    state_root = root / "state"
    run_id = "research-export-two-cycles"
    host = WorkflowHost(state_root)
    host.agent = _ReviseOnceAgent()
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_pilot_payload(root, export_mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()
    snapshot = build_monitor_snapshot(state_root, run_id)
    assert snapshot.oracle["ok"]
    assert len(snapshot.hypotheses) == 2
    assert any(item.parent_hypothesis_id for item in snapshot.hypotheses)
    return state_root, run_id


def test_export_completed_host_cycles_preserves_existing_observation_and_source(
    completed_source: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root, run_id = completed_source
    monkeypatch.setattr(monitor_module.time, "time", lambda: 12345.0)
    snapshot = build_monitor_snapshot(state_root, run_id)
    before_rows = _authority_snapshot(state_root)
    before_files = _artifact_snapshot(state_root)

    output, file_count = export_research_view(state_root, run_id, tmp_path / "export")

    assert file_count == 4 + len(snapshot.hypotheses)
    assert json.loads((output / "run.json").read_text()) == json.loads(snapshot.to_json())
    assert output.stat().st_mode & 0o077 == 0
    actual_files = {
        path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()
    }
    assert actual_files == {
        "run.json", "background.md", "graph/edges.jsonl", "FINAL_REPORT.md",
        *(f"graph/nodes/{item.hypothesis_id}.md" for item in snapshot.hypotheses),
    }
    for cycle, item in enumerate(snapshot.hypotheses, 1):
        content = (output / "graph/nodes" / f"{item.hypothesis_id}.md").read_text()
        metadata, body = content.removeprefix("---\n").split("\n---\n", 1)
        expected = json.loads(json.dumps(asdict(item)))
        claim = expected.pop("claim")
        expected["state"] = item.assessment or "PROPOSED"
        expected.update(chain="1", cycle=cycle)
        if cycle == 2: expected["selected_tip"] = True
        assert yaml.safe_load(metadata) == expected
        assert claim in body
    assert [
        json.loads(line) for line in (output / "graph/edges.jsonl").read_text().splitlines()
    ] == [
        {
            "parent_hypothesis_id": item.parent_hypothesis_id,
            "hypothesis_id": item.hypothesis_id,
            "relation": item.relation,
        }
        for item in snapshot.hypotheses if item.parent_hypothesis_id is not None
    ]
    background = (output / "background.md").read_text()
    grounding = next(
        row for row in StateStore(state_root).list_attempts()
        if row["kind"] == "AGENT" and row["state"] == "CLOSED"
        and row["terminal_reason"] == "SUCCEEDED"
        and json.loads(row["observation_json"])["request"]["command"] == "GROUNDING"
    )
    candidate = json.loads(grounding["observation_json"])["response"]["typed_candidate"]
    final = next(row for row in StateStore(state_root).list_attempts()
        if row["kind"] == "AGENT" and row["terminal_reason"] == "SUCCEEDED"
        and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT")
    final_packet = json.loads(final["observation_json"])["response"]
    final_files = {item.path: item.content_bytes() for item in
        map(WorkspaceArtifact.from_dict, final_packet["proposed_workspace_artifacts"])}
    assert (output / "FINAL_REPORT.md").read_bytes() == final_files["FINAL_REPORT.md"]
    assert b"GOAL_MET" in final_files["FINAL_REPORT.md"]
    assert (candidate["background"]["project_summary"] if "background" in candidate else candidate["summary"]) in background
    assert grounding["attempt_id"] in background
    assert grounding["command_id"] in background
    assert all(ref in background for ref in candidate["supporting_refs"])
    assert "Offline polling observation" in background
    assert "Oracle is a separate read-only observation" in background
    assert _authority_snapshot(state_root) == before_rows
    assert _artifact_snapshot(state_root) == before_files


def test_export_reads_grounding_and_monitor_from_the_same_transaction(
    completed_source: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root, run_id = completed_source
    original = StateTransaction.list_attempts
    connections = []

    def track_reads(transaction: StateTransaction, **filters):
        connections.append(transaction._db)
        assert transaction._db.in_transaction
        assert transaction.execute("PRAGMA query_only").fetchone()[0] == 1
        return original(transaction, **filters)

    monkeypatch.setattr(StateTransaction, "list_attempts", track_reads)
    export_research_view(state_root, run_id, tmp_path / "same-transaction")

    assert len(connections) >= 2
    assert all(connection is connections[0] for connection in connections)


def test_background_uses_successful_grounding_after_failed_attempt(
    completed_source: tuple[Path, str],
) -> None:
    state_root, run_id = completed_source
    snapshot = build_monitor_snapshot(state_root, run_id)
    successful = next(
        row for row in StateStore(state_root).list_attempts()
        if row["kind"] == "AGENT"
        and json.loads(row["observation_json"])["request"]["command"] == "GROUNDING"
    )
    failed = copy.deepcopy(successful)
    failed["attempt_id"] = "failed-grounding-attempt"
    failed["terminal_reason"] = "FAILED"
    observation = json.loads(failed["observation_json"])
    observation["response"]["typed_candidate"]["summary"] = "Unaccepted failed Grounding text"
    failed["observation_json"] = json.dumps(observation)

    background = export_module._background_markdown(snapshot, (failed, successful))

    assert successful["attempt_id"] in background
    candidate = json.loads(successful["observation_json"])["response"]["typed_candidate"]
    assert (candidate["background"]["project_summary"] if "background" in candidate else candidate["summary"]) in background
    assert "Unaccepted failed Grounding text" not in background
    assert "failed-grounding-attempt" not in background


def test_export_before_grounding_is_explicitly_incomplete(tmp_path: Path) -> None:
    state_root = tmp_path / "fresh-state"
    run_id = "export-before-grounding"
    _create_fresh_run(state_root, run_id)

    output, file_count = export_research_view(state_root, run_id, tmp_path / "export")

    assert file_count == 3
    assert "no successful GROUNDING Attempt is recorded" in (output / "background.md").read_text()
    assert (output / "graph/edges.jsonl").read_text() == ""
    assert list((output / "graph/nodes").iterdir()) == []


@pytest.mark.parametrize("relative", ["existing", "same", "inside", "ancestor", "symlink-inside"])
def test_export_rejects_existing_or_overlapping_destination_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    state_root = tmp_path / "source"
    state_root.mkdir()
    if relative == "existing":
        output = tmp_path / "existing"
        output.mkdir()
        (output / "run.json").write_text("keep me")
    elif relative == "same":
        output = state_root
    elif relative == "inside":
        output = state_root / "export"
    elif relative == "ancestor":
        output = tmp_path
    else:
        alias = tmp_path / "source-alias"
        alias.symlink_to(state_root, target_is_directory=True)
        output = alias / "export"

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("destination rejection must precede reading source")

    monkeypatch.setattr(StateStore, "readonly_connection", unexpected_read)
    with pytest.raises(ResearchExportError, match="must be (new|disjoint)"):
        export_research_view(state_root, "unused", output)
    if relative == "existing":
        assert (output / "run.json").read_text() == "keep me"
    assert list(state_root.iterdir()) == []


@pytest.mark.parametrize("hypothesis_id", ["../escape", "/absolute", "bad/name", "x" * 253])
def test_export_rejects_unsafe_hypothesis_filename_without_creating_output(
    completed_source: tuple[Path, str], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, hypothesis_id: str,
) -> None:
    state_root, run_id = completed_source
    snapshot = build_monitor_snapshot(state_root, run_id)
    unsafe = replace(snapshot.hypotheses[0], hypothesis_id=hypothesis_id)
    monkeypatch.setattr(export_module, "build_monitor_snapshot", lambda *_args, **_kwargs: replace(
        snapshot, hypotheses=(unsafe,),
    ))
    output = tmp_path / "export"

    with pytest.raises(ResearchExportError, match="safe export filename"):
        export_research_view(state_root, run_id, output)

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entrypoint", ["cli", "monitor"])
def test_export_cli_never_contacts_socket_or_starts_tui_or_web(
    completed_source: tuple[Path, str], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys, entrypoint: str,
) -> None:
    state_root, run_id = completed_source

    def unexpected_interactive(*_args, **_kwargs):
        raise AssertionError("offline export must not use a socket, TUI, or Web server")

    monkeypatch.setattr(socket, "socket", unexpected_interactive)
    monkeypatch.setattr(monitor_module, "build_observer_snapshot", unexpected_interactive)
    monkeypatch.setattr(monitor_module.importlib, "import_module", unexpected_interactive)
    output = tmp_path / "cli-export"
    arguments = ["--state-root", str(state_root), "--run-id", run_id, "--export", str(output)]
    result = cli_main(["monitor", *arguments]) if entrypoint == "cli" else monitor_main(arguments)

    assert result == 0
    assert str(output) in capsys.readouterr().out
    assert (output / "run.json").is_file()


def test_export_cli_reports_source_error_and_preserves_new_destination(tmp_path: Path) -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    output = tmp_path / "export"

    result = run_monitor(
        tmp_path / "missing-source", "missing-run", json_output=False,
        export_dir=output, stdout=stdout, stderr=stderr,
    )

    assert result == 2
    assert "cannot export research view" in stderr.getvalue()
    assert stdout.getvalue() == ""
    assert not output.exists()
    assert not (tmp_path / "missing-source").exists()


def test_export_reports_partial_directory_after_write_failure_and_never_reuses_it(
    completed_source: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root, run_id = completed_source
    output = tmp_path / "partial-export"
    original = Path.open

    def fail_background(path: Path, *args, **kwargs):
        if path == output / "background.md" and args == ("x",):
            raise OSError("fixture disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_background)
    stdout, stderr = io.StringIO(), io.StringIO()
    result = run_monitor(
        state_root, run_id, json_output=False, export_dir=output,
        stdout=stdout, stderr=stderr,
    )

    assert result == 2
    assert f"partial export remains at {output}" in stderr.getvalue()
    assert "fixture disk full" in stderr.getvalue()
    assert stdout.getvalue() == ""
    existing = (output / "run.json").read_bytes()
    with pytest.raises(ResearchExportError, match="must be new"):
        export_research_view(state_root, run_id, output)
    assert (output / "run.json").read_bytes() == existing


def test_export_and_json_modes_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        cli_main([
            "monitor", "--run-id", "unused", "--json", "--export", str(tmp_path / "export"),
        ])
    assert error.value.code == 2
    assert not (tmp_path / "export").exists()
