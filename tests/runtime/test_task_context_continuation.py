"""ACI25 retry continuity uses closed task evidence and never whole-chain history."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.agent_sessions import AgentSessions, continuation_observation
from hypoloom.context_management import recovery_tool_notes
from hypoloom.domain import AgentCommand
from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
from hypoloom.oracle import _raw_artifact_digest
from hypoloom.paper_sources import PaperWorkspace, prepare_sources
from hypoloom.session_oracle import audit_sessions
from test_agent_sessions import _chain_request, _manager, _transcript
from test_git_authoring_workspace import make_target
from test_grounding_acceptance_aci import acceptance_exchange


def _request():
    return replace(_chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design-first", "a"),
        schema_version=25, required_adapter_version="claude-code-bwrap/63")


def _closed(workspace, *, failure=None, retry_class="MODEL"):
    request = workspace.request
    observed = {"request": request.to_dict(), "session_binding": workspace.binding,
        "session": workspace.finish_node(), "retry_class": retry_class,
        "failure_code": "invalid_candidate" if retry_class == "MODEL" else "provider_error",
        "diagnostics": {"context": {"current_request_fits": True, "last_observed_exchange": 7,
            "current_request_exchange": 7, "last_observed_input_tokens": 60000, "request_input_bytes": 180000,
            "available_input_tokens": 205568, "output_reserve_tokens": 32768}}}
    if failure is not None:
        observed["diagnostics"]["provider_failure"] = {"code": failure}
    return {"run_id": request.run_id, "workflow_id": request.owner_workflow_id,
        "command_id": request.command_id, "attempt_id": request.attempt_id,
        "attempt_ordinal": request.attempt_ordinal, "kind": "AGENT", "state": "CLOSED",
        "terminal_reason": "FAILED", "started_at": 1, "ended_at": 2,
        "process_group_id": 12345, "observation_json": json.dumps(observed)}


@pytest.mark.parametrize("retry_class", ["MODEL", "INFRASTRUCTURE"])
def test_same_task_correction_resumes_published_transcript_and_retains_code(tmp_path, retry_class):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    request = _request()
    first = manager.prepare(request, target)
    (first.worktree_root / "experiment.py").write_text("print('completed implementation')\n")
    original = _transcript(first, "inspected interfaces; next repair output field")
    prior = _closed(first, retry_class=retry_class)
    correction = EmbeddedInput.from_bytes("design-first--agent-retry-diagnostic", "application/json", b'{"failure_code":"invalid_candidate"}')
    retry = replace(request, attempt_id="design-second", attempt_ordinal=2,
        input_artifacts=(*request.input_artifacts, correction))
    second = manager.prepare(retry, target, prior=prior)
    assert second.session_arguments == ("--resume", first.binding["session_id"])
    assert second.worktree_root == first.worktree_root
    assert (second.worktree_root / "experiment.py").read_text() == "print('completed implementation')\n"
    assert (second.session_root / (second.binding["session_id"] + ".jsonl")).read_bytes() == original
    _transcript(second, "repaired output", append=True)
    closed = _closed(second)
    rows = [prior, {**closed, "started_at": 3, "ended_at": 4}]
    payloads = {row["attempt_id"]: json.loads(row["observation_json"]) for row in rows}
    issues, count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert not issues and count == 2
    # Same chain source, next stage: no reuse of the design conversation.
    execute = replace(_chain_request(AgentCommand.EXPERIMENT_EXECUTION, "execute-first", "a"), schema_version=25)
    execution = manager.prepare(execute, target, prior=closed)
    assert execution.session_arguments[0] == "--session-id"
    assert execution.binding["session_id"] != second.binding["session_id"]


@pytest.mark.parametrize("change", ["capacity", "compaction", "unknown_usage", "last_failed_exchange", "near_limit", "oversized_relay_input",
    "incomplete_archive", "source", "model", "permission", "adapter", "archive_failure", "aci24"])
def test_unsafe_or_changed_retry_reconstructs_without_erasing_draft(tmp_path, change):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    request = _request()
    if change == "aci24": request = replace(request, schema_version=24)
    first = manager.prepare(request, target)
    (first.worktree_root / "experiment.py").write_text("print('retained draft')\n")
    _transcript(first, "research progress")
    prior = _closed(first)
    observed = json.loads(prior["observation_json"])
    retry = replace(request, attempt_id="design-second", attempt_ordinal=2)
    if change == "capacity": observed["diagnostics"]["provider_failure"] = {"code": "context_capacity"}
    elif change == "compaction": observed["diagnostics"]["provider_failure"] = {"code": "compaction_failed"}
    elif change == "unknown_usage": observed["diagnostics"]["context"].pop("last_observed_input_tokens")
    elif change == "last_failed_exchange": observed["diagnostics"]["context"]["current_request_exchange"] = 8
    elif change == "near_limit": observed["diagnostics"]["context"]["last_observed_input_tokens"] = 200000
    elif change == "oversized_relay_input": observed["diagnostics"]["context"]["request_input_bytes"] = 300000
    elif change == "incomplete_archive": observed["session"]["transcript_complete"] = False
    elif change == "source": retry = replace(retry, input_artifacts=(EmbeddedInput.from_bytes("changed-source", "text/plain", b"new facts"),))
    elif change == "model": retry = replace(retry, required_model_identity="new-model")
    elif change == "permission": retry = replace(retry, tool_permission_profile="new-permission")
    elif change == "adapter": retry = replace(retry, required_adapter_version="claude-code-bwrap/64")
    elif change == "archive_failure": observed["failure_code"] = "session_archive_failed"
    prior["observation_json"] = json.dumps(observed)
    second = manager.prepare(retry, target, prior=prior)
    assert second.session_arguments[0] == "--session-id"
    assert second.binding["session_id"] != first.binding["session_id"]
    assert (second.worktree_root / "experiment.py").read_text() == "print('retained draft')\n"


def test_published_archive_blocks_drift_and_restores_missing_mutable_transcript(tmp_path):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    request = _request()
    first = manager.prepare(request, target)
    content = _transcript(first, "published progress")
    prior = _closed(first)
    retry = replace(request, attempt_id="design-second", attempt_ordinal=2)
    path = first.session_root / (first.binding["session_id"] + ".jsonl")
    _transcript(first, "accidental stale append", append=True)
    with pytest.raises(GitAuthoringIntegrityError, match="published archive"):
        manager.prepare(retry, target, prior=prior)
    path.unlink()
    resumed = manager.prepare(retry, target, prior=prior)
    assert path.read_bytes() == content
    assert resumed.session_arguments[0] == "--resume"
    progressed = _transcript(resumed, "second Attempt work before Host loss", append=True)
    recovered = manager.prepare(retry, target, prior=prior, recovering=True)
    assert recovered.binding == resumed.binding and path.read_bytes() == progressed
    assert recovered.finish_node()["transcript_complete"] is True


def test_transient_transport_can_resume_with_complete_usage_anchored_projection(tmp_path):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    first = manager.prepare(_request(), target)
    _transcript(first, "bounded tool work before transient transport loss")
    prior = _closed(first, retry_class="INFRASTRUCTURE")
    observed = json.loads(prior["observation_json"])
    observed["diagnostics"]["context"].update(current_request_exchange=8, anchor_exchange=7,
        current_request_estimate_source="provider_usage_plus_delta_estimate", current_request_estimated_input_tokens=62000)
    prior["observation_json"] = json.dumps(observed)
    retry = replace(first.request, attempt_id="design-second", attempt_ordinal=2)
    second = manager.prepare(retry, target, prior=prior)
    assert second.session_arguments == ("--resume", first.binding["session_id"])
    _transcript(second, "continued", append=True)
    following = {**_closed(second), "started_at": 3, "ended_at": 4}
    rows = [prior, following]
    payloads = {row["attempt_id"]: json.loads(row["observation_json"]) for row in rows}
    assert not audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)[0]


def test_oracle_rejects_cross_input_and_capacity_failed_session_reuse(tmp_path):
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    first = manager.prepare(_request(), target)
    _transcript(first, "first")
    prior = _closed(first)
    second = manager.prepare(replace(first.request, attempt_id="design-second", attempt_ordinal=2), target, prior=prior)
    _transcript(second, "corrected", append=True)
    following = {**_closed(second), "started_at": 3, "ended_at": 4}
    rows = [prior, following]
    payloads = {row["attempt_id"]: json.loads(row["observation_json"]) for row in rows}
    changed = copy.deepcopy(payloads)
    changed[first.attempt_id]["diagnostics"]["provider_failure"] = {"code": "context_capacity"}
    assert "session_restart_identity" in {issue[0] for issue in audit_sessions(manager.publisher.root, rows, changed, _raw_artifact_digest)[0]}
    changed = copy.deepcopy(payloads)
    changed[second.attempt_id]["request"]["tool_permission_profile"] = "changed"
    assert "session_restart_identity" in {issue[0] for issue in audit_sessions(manager.publisher.root, rows, changed, _raw_artifact_digest)[0]}


def test_state_transaction_validates_resume_against_the_actual_preceding_attempt(tmp_path):
    from hypoloom.domain import AttemptTerminalReason
    from test_state_transactions import open_store, run_version, command
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    first = manager.prepare(_request(), target)
    _transcript(first, "task work")
    prior = _closed(first)
    observation = json.loads(prior["observation_json"])
    # The transaction's run/command/ordinal rows remain the authority; the
    # prospective binding is not accepted solely because the caller supplies it.
    store_dir = tmp_path / "authority"
    store_dir.mkdir()
    store = open_store(store_dir)
    try:
        request = first.request
        store.create_run(request.run_id, run_version())
        with store.transaction() as tx:
            command(tx, request.command_id, run_id=request.run_id, workflow_id=request.owner_workflow_id)
            tx.open_attempt({**prior, "host_generation": "host-one", "payload": {
                "request": request.to_dict(), "session_binding": first.binding}})
            tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.FAILED, ended_at=2,
                payload={key: value for key, value in observation.items() if key not in {"request", "session_binding"}})
        retry = replace(request, attempt_id="design-second", attempt_ordinal=2)
        with store.transaction() as tx:
            actual = tx.get_attempt(request.attempt_id)
            assert AgentSessions.binding(retry, actual) == first.binding
            changed = replace(retry, required_model_identity="different-model")
            with pytest.raises(ValueError, match="session binding"):
                tx.open_attempt({**prior, "attempt_id": retry.attempt_id, "attempt_ordinal": 2,
                    "host_generation": "host-one", "started_at": 3,
                    "payload": {"request": changed.to_dict(), "session_binding": first.binding}})
            tx.open_attempt({**prior, "attempt_id": retry.attempt_id, "attempt_ordinal": 2,
                "host_generation": "host-one", "started_at": 3,
                "payload": {"request": retry.to_dict(), "session_binding": first.binding}})
        assert json.loads(store.get_attempt(retry.attempt_id)["observation_json"])["session_binding"] == first.binding
    finally:
        store.close()


def test_same_task_oracle_uses_closed_ordinal_order_despite_wall_clock_step(tmp_path):
    from hypoloom.domain import AttemptTerminalReason
    from hypoloom.state_store import IdentityConflict
    from test_state_transactions import open_store, run_version, command

    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    first = manager.prepare(_request(), target)
    _transcript(first, "completed source inspection")
    prior = _closed(first)
    observation = json.loads(prior["observation_json"])
    store_dir = tmp_path / "authority"
    store_dir.mkdir()
    store = open_store(store_dir)
    try:
        request = first.request
        retry = replace(request, attempt_id="design-second", attempt_ordinal=2)
        store.create_run(request.run_id, run_version())
        with store.transaction() as tx:
            command(tx, request.command_id, run_id=request.run_id, workflow_id=request.owner_workflow_id)
            tx.open_attempt({**prior, "host_generation": "host-one", "payload": {
                "request": request.to_dict(), "session_binding": first.binding}})
        attempt = {**prior, "attempt_id": retry.attempt_id, "attempt_ordinal": 2,
            "host_generation": "host-one", "started_at": 1}
        with store.transaction() as tx:
            with pytest.raises(IdentityConflict, match="prior attempt is OPEN"):
                tx.open_attempt({**attempt, "payload": {"request": retry.to_dict(),
                    "session_binding": AgentSessions.binding(retry)}})
            tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.FAILED, ended_at=2,
                payload={key: value for key, value in observation.items() if key not in {"request", "session_binding"}})
        with store.transaction() as tx:
            actual = tx.get_attempt(request.attempt_id)
            skipped = replace(retry, attempt_ordinal=3)
            with pytest.raises(IdentityConflict, match="ordinal must be contiguous"):
                tx.open_attempt({**attempt, "attempt_ordinal": 3, "payload": {
                    "request": skipped.to_dict(), "session_binding": AgentSessions.binding(skipped, actual)}})
            # Transactional CLOSED -> OPEN and the immediate ordinal remain
            # authoritative when the host's wall clock moves backward.
            admitted = AgentSessions.binding(retry, actual)
            assert admitted == first.binding
            tx.open_attempt({**attempt, "payload": {"request": retry.to_dict(), "session_binding": admitted}})
        second = manager.prepare(retry, target, prior=actual, admitted_binding=admitted)
        assert second.session_arguments == ("--resume", first.binding["session_id"])
        _transcript(second, "completed correction", append=True)
        with store.transaction() as tx:
            tx.close_attempt(retry.attempt_id, terminal_reason=AttemptTerminalReason.SUCCEEDED, ended_at=1.5,
                payload={"session": second.finish_node()})
        with store.readonly_connection() as db:
            rows = tuple(db.execute("SELECT * FROM execution_attempts ORDER BY attempt_ordinal"))
        assert rows[0]["ended_at"] > rows[1]["started_at"]
        payloads = {row["attempt_id"]: json.loads(row["observation_json"]) for row in rows}
        issues, count = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
        assert not issues and count == 2
        changed = [{**dict(rows[0]), "state": "OPEN"}, dict(rows[1])]
        issues, _ = audit_sessions(manager.publisher.root, changed, payloads, _raw_artifact_digest)
        assert "session_restart_identity" in {item[0] for item in issues}
    finally:
        store.close()


def test_rich_recovery_preserves_sources_ranges_checks_error_and_redacts_credentials():
    rows = []
    operations = [("Read", {"file_path": "/inputs/0001.txt", "offset": 120, "limit": 50}, "interface definition", False),
        ("Bash", {"command": "python /acceptance/evaluate.py /tmp/fixture"}, "validity fixture failed", True)]
    operations += [("Read", {"file_path": f"/workspace/source-{i}.py", "offset": i * 10}, "source text" * 300, False) for i in range(20)]
    operations += [("Bash", {"command": "API_KEY=do-not-expose python check.py"}, "Authorization: do-not-expose", False)]
    for index, (name, arguments, output, failed) in enumerate(operations):
        rows.extend([{"sessionId": "own", "message": {"content": [{"type": "tool_use", "id": str(index), "name": name, "input": arguments}]}},
            {"sessionId": "own", "message": {"content": [{"type": "tool_result", "tool_use_id": str(index), "content": output, "is_error": failed}]}}])
    rows.append({"sessionId": "other", "message": {"content": [{"type": "tool_result", "content": "unrelated secret"}]}})
    notes = recovery_tool_notes(b"\n".join(json.dumps(row).encode() for row in rows), "own", rich=True)
    encoded = json.dumps(notes)
    assert len(notes) <= 12 and len(encoded.encode()) < 18000
    assert any('/inputs/0001.txt' in item["input_excerpt"] and '120' in item["input_excerpt"] for item in notes)
    assert any(item["is_error"] and "evaluate.py" in item["input_excerpt"] for item in notes)
    assert "do-not-expose" not in encoded and "unrelated secret" not in encoded


def test_grounding_retry_keeps_acceptance_drafts_and_remounts_published_papers(tmp_path):
    manager = _manager(tmp_path)
    request = replace(acceptance_exchange()[0], schema_version=25, required_adapter_version="claude-code-bwrap/63")
    first = manager.prepare(request, None)
    (first.acceptance_root / "evaluate.py").write_text("print('unfinished evaluator')\n")
    (first.acceptance_root / "progress.md").write_text("Confirmed input interface; next check empty predictions.\n")
    first.papers = PaperWorkspace(manager.state_root / "paper-staging", request)
    source = first.papers.add({"content": b"exact-paper", "media_type": "text/plain", "pages": ("Methods: causal prefix",)},
        source_ref=request.attempt_id + ":web-document-1", acquisition=request.attempt_id)
    _transcript(first, "read methods; evaluator drafted")
    prior = _closed(first, failure="context_capacity", retry_class="INFRASTRUCTURE")
    first.papers.close()
    second = manager.prepare(replace(request, attempt_id="grounding-second", attempt_ordinal=2), None, prior=prior)
    host = SimpleNamespace(state_root=manager.state_root, publisher=manager.publisher,
        store=SimpleNamespace(list_attempts=lambda **_: [prior], get_attempt=lambda _: prior))
    prepare_sources(host, second)
    assert second.session_arguments[0] == "--session-id"
    assert second.papers.sources[0]["source_ref"] == source["source_ref"]
    assert second.papers.sources[0]["archive_attempt_id"] == request.attempt_id
    assert (second.papers.archive_mounts["source-0001"] / "pages.txt").read_text().find("causal prefix") >= 0
    notes = continuation_observation(manager.publisher, prior, second)
    assert {item["path"] for item in notes["available_drafts"]} == {"/acceptance/evaluate.py", "/acceptance/progress.md"}
    assert notes["session_action"] == "reconstruct_task"
    assert "accepted" in notes["authority"]
    second.papers.close()


@pytest.mark.parametrize("control_before_open", [True, False])
@pytest.mark.parametrize("switch_endpoint", [True, False])
def test_connection_control_forces_future_fresh_sessions_but_preserves_open_intent(tmp_path, control_before_open, switch_endpoint):
    from hypoloom.agent_sessions import session_resume_allowed
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.domain import AttemptTerminalReason
    from hypoloom.host_settings import prepare_host_settings
    from test_host_settings import _bound_host, _source
    from test_phase8_parallel_adversarial import _create_run
    from test_state_transactions import command

    _, target = make_target(tmp_path)
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    private = prepare_host_settings(root, _source(tmp_path))
    host = _bound_host(root, private)
    host.initialize()
    manager = AgentSessions(root, host.publisher)
    request = _request()
    try:
        _create_run(host, request.run_id)
        first = manager.prepare(request, target)
        _transcript(first, "completed source inspection")
        prior = _closed(first)
        observation = json.loads(prior["observation_json"])
        with host.store.transaction() as tx:
            command(tx, request.command_id, run_id=request.run_id, workflow_id=request.owner_workflow_id)
            tx.open_attempt({**prior, "host_generation": host.generation,
                "payload": {"request": request.to_dict(), "session_binding": first.binding}})
            tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.FAILED, ended_at=2,
                payload={key: value for key, value in observation.items() if key not in {"request", "session_binding"}})
        retry = replace(request, attempt_id="design-second", attempt_ordinal=2)
        payload = {"run_id": request.run_id, "reason": "operator"}
        if switch_endpoint:
            selected = tmp_path / "connection.json"
            selected.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "synthetic-new-key",
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:8999"}}))
            selected.chmod(0o600)
            payload["settings_path"] = str(selected)
        def change_connection():
            response = host.handle(ControlRequest(1, "operator-change", None, "PROVIDER_RETRY_CLEAR", payload))
            assert response.status == "accepted", response
        if control_before_open: change_connection()
        with host.store.transaction() as tx:
            actual = tx.get_attempt(request.attempt_id)
            permitted = session_resume_allowed(tx, retry)
            assert permitted is not control_before_open
            admitted = AgentSessions.binding(retry, actual, allow_resume=permitted)
            attempt = {**prior, "attempt_id": retry.attempt_id, "attempt_ordinal": 2,
                "host_generation": host.generation, "started_at": 3}
            if control_before_open:
                with pytest.raises(ValueError, match="session binding"):
                    tx.open_attempt({**attempt, "payload": {"request": retry.to_dict(), "session_binding": first.binding}})
            tx.open_attempt({**attempt, "payload": {"request": retry.to_dict(), "session_binding": admitted}})
        if not control_before_open: change_connection()
        with host.store.transaction() as tx:
            assert not session_resume_allowed(tx, retry)
        # The OPEN decision predates later controls, so it is recovered as
        # recorded rather than recomputed from now-selected connection settings.
        second = manager.prepare(retry, target, prior=actual, admitted_binding=admitted)
        assert second.session_arguments[0] == ("--session-id" if control_before_open else "--resume")
        progressed = _transcript(second, "retry work before Host loss", append=not control_before_open)
        recovered = manager.prepare(retry, target, prior=actual, admitted_binding=admitted, recovering=True)
        assert recovered.binding == admitted
        assert recovered.session_arguments[0] == "--resume"
        assert (recovered.session_root / (admitted["session_id"] + ".jsonl")).read_bytes() == progressed
        with host.store.transaction() as tx:
            tx.close_attempt(retry.attempt_id, terminal_reason=AttemptTerminalReason.LOST, ended_at=4,
                payload={"session": {**recovered.finish_node(), "launch_arguments": None, "archive_after_loss": True}})
        # Exercise the Oracle's real SQLite row interface, not only dict fixtures.
        with host.store.readonly_connection() as db:
            rows = tuple(db.execute("SELECT * FROM execution_attempts ORDER BY attempt_ordinal"))
        payloads = {row["attempt_id"]: json.loads(row["observation_json"]) for row in rows}
        issues, count = audit_sessions(host.publisher.root, rows, payloads, _raw_artifact_digest)
        assert not issues and count == 2
    finally:
        host.close()
