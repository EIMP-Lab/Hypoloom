from __future__ import annotations

import json
import multiprocessing
import shutil
import socket
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import hypoloom.research_projection as projection_module
from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.monitor import build_monitor_snapshot
from hypoloom.research_projection import (
    ResearchProjectionError,
    build_research_projection,
    freeze_role_view,
    read_role_view,
    role_view_files,
    snapshot_role_view,
    update_research_projection,
)
from hypoloom.state_store import StateStore
from test_expert_loop_host import _artifact_snapshot, _authority_snapshot
from test_research_export import completed_source, export_mount_root  # noqa: F401


def _serve_projection_host(root):
    from hypoloom import monitor, observer_summary, workflow_host
    from test_expert_loop_host import _ReviseOnceAgent

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Host replayed independent historical diagnostics")

    monitor.Oracle.audit = unexpected
    observer_summary.build_run_summary = unexpected
    initialize = workflow_host.WorkflowHost.__init__

    def with_two_cycles(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        self.agent = _ReviseOnceAgent()

    workflow_host.WorkflowHost.__init__ = with_two_cycles
    raise SystemExit(workflow_host.serve(root))


def test_resident_host_answers_status_through_two_cpu_cycles_and_projection(
    tmp_path, export_mount_root, record_testsuite_property,  # noqa: F811
):
    from hypoloom.control_protocol import ControlRequest, request_over_socket
    from hypoloom.oracle import Oracle
    from test_expert_loop_host import _pilot_payload

    root, run_id = tmp_path / "resident", "resident-projection-two-cycles"
    mount = export_mount_root / "resident"
    mount.mkdir()
    payload = _pilot_payload(tmp_path, mount, run_id)
    process = multiprocessing.get_context("spawn").Process(target=_serve_projection_host, args=(root,))
    process.start()
    latencies = []
    try:
        socket_path = root / "control.sock"
        deadline = time.monotonic() + 8
        while not socket_path.is_socket() and time.monotonic() < deadline:
            assert process.is_alive(), process.exitcode
            time.sleep(.02)
        assert socket_path.is_socket()
        created = request_over_socket(socket_path, ControlRequest(1, "create", 0, "CREATE_RUN", payload), timeout=3)
        assert created.status == "accepted", created
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            started = time.monotonic()
            status = request_over_socket(socket_path, ControlRequest(1, f"status-{len(latencies)}", None,
                "STATUS", {"run_id": run_id}), timeout=3)
            latencies.append(time.monotonic() - started)
            assert status.status == "accepted", status
            assert status.result.get("dispatch_error") is None, status.result
            assert status.result["research_projection"]["error"] is None, status.result
            if status.result["run_state"] == "COMPLETED":
                break
            assert status.result["run_state"] not in {"FAILED", "CANCELLED"}, status.result
            time.sleep(.02)
        else:
            pytest.fail("resident CPU research did not complete")
    finally:
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)
    assert process.exitcode == 0
    assert not (root / "control.sock").exists()
    assert len(latencies) > 3 and max(latencies) < 2
    record_testsuite_property("resident_projection_status_requests", len(latencies))
    record_testsuite_property("resident_projection_maximum_status_seconds", max(latencies))
    report = Oracle(root / "state.sqlite", root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 2
    assert report.open_attempt_count == 0 and report.owned_process_residue == ()
    projection = root / run_id / "projection"
    snapshot = json.loads((projection / "run.json").read_text())
    assert snapshot["status"]["run_state"] == "COMPLETED"
    assert snapshot["oracle"]["ok"] is None
    assert (projection / "FINAL_REPORT.md").is_file()


@pytest.fixture
def source(completed_source: tuple[Path, str], tmp_path: Path) -> tuple[Path, str]:  # noqa: F811
    original, run_id = completed_source
    root = tmp_path / "state"
    shutil.copytree(original, root)
    return root, run_id


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def _rows(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.splitlines()]


def test_real_cpu_cycles_project_exact_facts_results_logs_and_rounds_without_source_writes(
    source: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run_id = source
    before_rows = _authority_snapshot(root)
    before_artifacts = _artifact_snapshot(root / "artifacts")

    def no_socket(*_args, **_kwargs):
        raise AssertionError("projection must not contact the Host or start a server")

    monkeypatch.setattr(socket, "socket", no_socket)
    projection = build_research_projection(root, run_id)
    output, count = update_research_projection(root, run_id, projection=projection)

    assert output == root / run_id / "projection"
    assert count == len(projection.files)
    assert _files(output) == projection.files
    assert "Offline polling observation" in projection.files["background.md"].decode()
    assert "Successful GROUNDING Attempt" in projection.files["background.md"].decode()
    snapshot = build_monitor_snapshot(root, run_id)
    for hypothesis in snapshot.hypotheses:
        assert hypothesis.claim.encode() in projection.files[f"graph/nodes/{hypothesis.hypothesis_id}.md"]
    with StateStore(root).readonly_connection() as connection:
        raw_transitions = [dict(t) for t in connection.execute(
            "SELECT t.*,c.command_type FROM workflow_transitions t JOIN workflow_commands c USING(command_id) "
            "WHERE t.run_id=? ORDER BY t.rowid", (run_id,))]
        from hypoloom.observation_api import product_events
        raw_facts = [dict(row) for row in connection.execute("SELECT * FROM research_facts WHERE run_id=?", (run_id,))]
        raw_attempts = [dict(row) for row in connection.execute("SELECT * FROM execution_attempts WHERE run_id=?", (run_id,))]
        commands = {row["command_id"]: dict(row) for row in connection.execute(
            "SELECT * FROM workflow_commands WHERE run_id=?", (run_id,))}
        events = _rows(projection.files["events.jsonl"])
        assert events == product_events(run_id, raw_facts, raw_transitions, raw_attempts, commands=commands)
        assert len([item for item in events if item["type"] == "experiment_submitted"]) == 2
        assert len([item for item in events if item["type"] == "final_report_ready"]) == 1
        outcomes = StateStore(root).list_facts(run_id, "CanonicalOutcomeAccepted")
        assert len(outcomes) == 2
        for outcome in outcomes:
            artifact = dict(connection.execute(
                "SELECT * FROM execution_artifacts WHERE artifact_id=?", (outcome["artifact_id"],)).fetchone())
            prefix = f"experiments/{outcome['experiment_id']}"
            assert projection.files[f"{prefix}/result.json"] == (
                root / "artifacts" / artifact["path"] / "result.json").read_bytes()
            state = json.loads(projection.files[f"{prefix}/state.json"])
            assert state["canonical_process_attempt_id"] == artifact["attempt_id"]
            assert state["result_available"] is True
            process = StateStore(root).get_attempt(artifact["attempt_id"])
            observation = json.loads(process["observation_json"])
            assert not observation.get("synthetic_before_spawn"), observation.get("resource_diagnostic")
            assert json.loads(projection.files[f"{prefix}/request.json"])["preregistration"]
            for name in ("stdout.log", "stderr.log"):
                saved = root / "artifacts/result-staging" / artifact["attempt_id"] / "logs" / name
                assert (name in state["available_logs"][artifact["attempt_id"]]) == saved.is_file()
                if saved.is_file():
                    assert projection.files[f"{prefix}/{name}"] == saved.read_bytes()
                    assert projection.files[f"{prefix}/attempts/{artifact['attempt_id']}/{name}"] == saved.read_bytes()
                else:
                    assert f"{prefix}/{name}" not in projection.files
    role = snapshot.experts[0].role
    for cycle in (1, 2):
        for name in ("literature.md", "hypotheses.json", "design.md", "execution.md", "analysis.md", "hypothesis_update.md"):
            assert projection.files[f"experts/{role}/rounds/{cycle}/{name}"]
    assert _authority_snapshot(root) == before_rows
    assert _artifact_snapshot(root / "artifacts") == before_artifacts
    assert not list(output.parent.glob(".projection-*"))


def test_role_files_exclude_other_graphs_and_whole_run_inputs(
    source: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run_id = source
    original = build_monitor_snapshot(root, run_id)
    role = original.experts[0].role
    peer = "peer-expert"
    peer_hypothesis = replace(original.hypotheses[0], hypothesis_id="peer-only-hypothesis",
                              proposer_role=peer, claim="PEER PRIVATE CLAIM", parent_hypothesis_id=None)
    snapshot = replace(original, hypotheses=(*original.hypotheses, peer_hypothesis),
                       experts=(*original.experts, replace(original.experts[0], role=peer)))
    monkeypatch.setattr(projection_module, "build_monitor_snapshot", lambda *_a, **_k: snapshot)

    projection = build_research_projection(root, run_id)
    own = role_view_files(projection, role)
    other = role_view_files(projection, peer)

    assert b"PEER PRIVATE CLAIM" in b"\n".join(projection.files.values())
    assert b"PEER PRIVATE CLAIM" not in b"\n".join(own.values())
    assert f"graph/nodes/{peer_hypothesis.hypothesis_id}.md" not in own
    assert f"graph/nodes/{original.hypotheses[0].hypothesis_id}.md" not in other
    assert "run.json" not in own and "events.jsonl" not in own and "attempts.jsonl" not in own
    assert not any(name.startswith(("experiments/", f"experts/{peer}/")) for name in own)
    assert all(b'"input_artifacts"' not in content for content in own.values())
    own.clear()
    assert role_view_files(projection, role)
    with pytest.raises(ResearchProjectionError, match="unknown research role"):
        role_view_files(projection, "not-admitted")


@pytest.mark.parametrize("foreign_winner", [False, True])
def test_pruning_is_control_metadata_and_foreign_winner_is_not_exposed_in_role_view(
    source: tuple[Path, str], foreign_winner: bool,
) -> None:
    root, run_id = source
    original = build_monitor_snapshot(root, run_id)
    role = original.experts[0].role
    loser = replace(original.hypotheses[0], hypothesis_id="loser", parent_hypothesis_id=None,
                    relation=None, assessment="SUPPORTED")
    winner = replace(loser, hypothesis_id="winner", proposer_role="peer" if foreign_winner else role)
    experts = original.experts
    if foreign_winner: experts = (*experts, replace(experts[0], role="peer"))
    snapshot = replace(original, hypotheses=(loser, winner), experts=experts)
    transitions = [{"workflow_kind": "RUN", "workflow_id": run_id, "revision": 50,
        "command_id": "adjudicate", "command_type": "ACCEPT_ADJUDICATION", "created_at": 1,
        "payload": {"role": role, "component_id": loser.component_id,
            "adjudication": {"cycle": 1, "selected_tip": {"node_id": "winner"}, "pruned_by": [{
                "winner_hypothesis_id": "winner", "loser_hypothesis_id": "loser",
                "source_attempt_id": "judge-output", "score_refs": ["score-output"],
            }]}}}]
    projection = projection_module._render_projection(root, snapshot, (), transitions, (), {}, ())
    def metadata(files):
        return yaml.safe_load(files["graph/nodes/loser.md"].decode().split("---")[1])
    human, private = metadata(projection.files), metadata(projection.role_files[role])
    assert human["state"] == private["state"] == "PRUNED"
    assert human["assessment"] == private["assessment"] == "SUPPORTED"
    assert human["pruned_by"][0]["source"] == "winner"
    assert _rows(projection.files["graph/edges.jsonl"])[0]["relation"] == "pruned_by"
    if foreign_winner:
        assert private["pruned_by"] == [] and private["pruning_details_redacted"]
        assert b"winner" not in projection.role_files[role]["graph/nodes/loser.md"]
        assert projection.role_files[role]["graph/edges.jsonl"] == b""
    else:
        assert private["pruned_by"] == human["pruned_by"]
        assert _rows(projection.role_files[role]["graph/edges.jsonl"])[0]["relation"] == "pruned_by"


def test_complete_private_view_is_frozen_for_retry_without_live_reads(
    source: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run_id = source
    projection = build_research_projection(root, run_id)
    role = next(iter(projection.role_files))
    own = role_view_files(projection, role)
    large_path = f"experts/{role}/rounds/1/analysis.md"
    own[large_path] = b"complete-history\n" * 400000
    projection = replace(projection, role_files={role: own})

    path = freeze_role_view(root, run_id, role, "logical-node-one", projection=projection)
    assert path == root / run_id / "experts" / role / "views/logical-node-one"
    assert _files(path) == own
    assert read_role_view(root, run_id, role, "logical-node-one") == own
    assert len((path / large_path).read_bytes()) > 4 * 1024 * 1024

    def no_live_read(*_args, **_kwargs):
        raise AssertionError("retry must not rebuild or rebind live research history")

    monkeypatch.setattr(projection_module, "build_research_projection", no_live_read)
    assert freeze_role_view(root, run_id, role, "logical-node-one") == path
    changed = replace(projection, role_files={role: {**own, large_path: b"later-live-change"}})
    assert freeze_role_view(root, run_id, role, "logical-node-one", projection=changed) == path
    assert (path / large_path).read_bytes() == own[large_path]
    assert not list(path.parent.glob(".view-*"))


def test_inline_view_is_exact_or_explicitly_over_budget(source: tuple[Path, str]) -> None:
    projection = build_research_projection(*source)
    role = next(iter(projection.role_files))
    embedded = snapshot_role_view(projection, role, max_bytes=10000000)
    restored = {item.path: item.content_bytes() for item in
                (WorkspaceArtifact.from_dict(value) for value in json.loads(embedded.content_bytes()))}
    assert restored == role_view_files(projection, role)
    with pytest.raises(ResearchProjectionError, match="not truncated history"):
        snapshot_role_view(projection, role, max_bytes=len(embedded.content_bytes()) - 1)


@pytest.mark.parametrize("failure", ["write", "exchange"])
def test_failed_refresh_keeps_complete_previous_projection(
    source: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    root, run_id = source
    projection = build_research_projection(root, run_id)
    output, _ = update_research_projection(root, run_id, projection=projection)
    old = _files(output)
    changed = replace(projection, files={**projection.files, "background.md": b"NEW OBSERVATION"})

    def fail(*_args, **_kwargs):
        raise OSError("fixture disk full")

    monkeypatch.setattr(projection_module, "_write_files" if failure == "write" else "_exchange_directories", fail)
    with pytest.raises(ResearchProjectionError, match="fixture disk full"):
        update_research_projection(root, run_id, projection=changed)
    assert _files(output) == old
    assert not list(output.parent.glob(".projection-*"))


def test_refresh_exchanges_directory_then_removes_only_old_derived_tree(
    source: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run_id = source
    projection = build_research_projection(root, run_id)
    output, _ = update_research_projection(root, run_id, projection=projection)
    old_inode = output.stat().st_ino
    old = _files(output)
    history = root / run_id / "sessions"
    history.mkdir(exist_ok=True)
    (history / "retained.txt").write_text("permanent history")
    changed = replace(projection, files={**projection.files, "background.md": b"NEW OBSERVATION"})
    original_exchange = projection_module._exchange_directories

    def exchange(staging: Path, target: Path):
        assert _files(target) == old
        assert _files(staging) == changed.files
        original_exchange(staging, target)
        assert _files(target) == changed.files
        assert _files(staging) == old

    monkeypatch.setattr(projection_module, "_exchange_directories", exchange)
    update_research_projection(root, run_id, projection=changed)
    assert output.stat().st_ino != old_inode
    assert (history / "retained.txt").read_text() == "permanent history"
    assert not list(output.parent.glob(".projection-*"))


@pytest.mark.parametrize("entry", ["foreign-file", "foreign-directory", "symlink", "wrong-run"])
def test_refresh_refuses_unrecognized_existing_destination_without_cleanup(
    source: tuple[Path, str], tmp_path: Path, entry: str,
) -> None:
    root, run_id = source
    projection = build_research_projection(root, run_id)
    output, _ = update_research_projection(root, run_id, projection=projection)
    external = tmp_path / "external"
    external.mkdir()
    (external / "canary").write_text("external history")
    if entry == "foreign-file":
        (output / "user-notes.md").write_text("keep this")
    elif entry == "foreign-directory":
        (output / "user-directory").mkdir()
    elif entry == "symlink":
        (output / "external").symlink_to(external, target_is_directory=True)
    else:
        (output / "run.json").write_text('{"run_id":"someone-else"}')
    before = _artifact_snapshot(output)
    with pytest.raises(ResearchProjectionError):
        update_research_projection(root, run_id, projection=projection)
    assert _artifact_snapshot(output) == before
    assert (external / "canary").read_text() == "external history"
    assert not list(output.parent.glob(".projection-*"))


@pytest.mark.parametrize("field,value", [("run", "../escape"), ("role", "../peer"),
                                          ("view", "/absolute"), ("role", "unknown-role")])
def test_private_view_rejects_unsafe_or_unowned_identity(
    source: tuple[Path, str], field: str, value: str,
) -> None:
    root, run_id = source
    projection = build_research_projection(root, run_id)
    identities = {"run": run_id, "role": next(iter(projection.role_files)), "view": "node"}
    identities[field] = value
    before = _artifact_snapshot(root / run_id / "experts")
    with pytest.raises(ResearchProjectionError):
        freeze_role_view(root, identities["run"], identities["role"], identities["view"], projection=projection)
    assert _artifact_snapshot(root / run_id / "experts") == before


def test_private_view_rejects_cross_role_files_and_symlinked_existing_tree(
    source: tuple[Path, str], tmp_path: Path,
) -> None:
    root, run_id = source
    projection = build_research_projection(root, run_id)
    role = next(iter(projection.role_files))
    own = role_view_files(projection, role)
    foreign = replace(projection, role_files={role: {**own, "experts/peer/state.json": b"{}"}})
    with pytest.raises(ResearchProjectionError, match="foreign-role"):
        freeze_role_view(root, run_id, role, "foreign", projection=foreign)
    path = freeze_role_view(root, run_id, role, "safe", projection=projection)
    (path / "outside").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ResearchProjectionError, match="non-regular"):
        freeze_role_view(root, run_id, role, "safe")


def test_missing_canonical_artifact_is_explicit_and_never_uses_unbound_latest_result(
    source: tuple[Path, str],
) -> None:
    root, run_id = source
    outcome = StateStore(root).list_facts(run_id, "CanonicalOutcomeAccepted")[0]
    with StateStore(root).readonly_connection() as connection:
        artifact = dict(connection.execute(
            "SELECT * FROM execution_artifacts WHERE artifact_id=?", (outcome["artifact_id"],)).fetchone())
    canonical = root / "artifacts" / artifact["path"] / "result.json"
    canonical.rename(canonical.with_name("result.saved.json"))
    fake = root / "artifacts" / run_id / "unbound-latest"
    fake.mkdir()
    (fake / "result.json").write_text('{"wrong":"latest"}')

    projection = build_research_projection(root, run_id)

    prefix = f"experiments/{outcome['experiment_id']}"
    assert f"{prefix}/result.json" not in projection.files
    state = json.loads(projection.files[f"{prefix}/state.json"])
    assert state["canonical_process_attempt_id"] == artifact["attempt_id"]
    assert state["result_available"] is False
    assert b'"wrong":"latest"' not in b"\n".join(projection.files.values())
