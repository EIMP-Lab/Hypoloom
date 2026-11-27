from dataclasses import replace
import json
import time
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import AgentACIError, EmbeddedInput, WorkspaceArtifact
from hypoloom.agent_budget import agent_attempts_exhausted
from hypoloom.context_observation import transcript_metrics
from hypoloom.design_checkpoint import build_checkpoint, handoff
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import host_tool_configuration
from hypoloom.host_tools_server import _arguments, call_tool, scoped_tools
from hypoloom.input_views import verification_focus
from hypoloom.native_output import NativeOutputValidator
from hypoloom.observer_summary import research_efficiency
from hypoloom.submission_references import expand_submission, inherit_captured_scoring
from hypoloom.verification_history import history_view, report_page
from hypoloom.workflow_host import WorkflowHost
from test_engineering_checks import measurement_package


def test_old_blocker_stays_visible_until_its_own_chain_resolves_it(tmp_path):
    host = WorkflowHost(tmp_path / "state")
    host.store.initialize()
    base = {"subject_expert": "alice", "component_id": "primary", "delivery_id": "chain", "phase": "review"}
    finding = {"id": "old", "severity": "blocker", "message": "probe failed", "resolution_condition": "rerun"}
    def record(tx, i, values, report):
        command, ref = f"review-{i}", f"attempt-{i}"
        tx.record_command({"run_id": "run", "workflow_id": "run", "command_id": command, "command_type": "REQUEST_AUXILIARY",
                           "request": {"implementation_verification": {**base, "experiment_id": str(i), **values}}})
        tx.open_attempt({"attempt_id": ref, "run_id": "run", "workflow_id": "run", "command_id": command,
                         "kind": "AGENT", "attempt_ordinal": 1, "host_generation": "test", "started_at": i,
                         "observation": {"request": {"command": "VERIFY_IMPLEMENTATION"}}})
        tx.close_attempt(ref, terminal_reason="SUCCEEDED", ended_at=i + .5, payload={"response": {"typed_candidate":
            {"verdict": "inconclusive", "findings": [], "resolved_refs": [], **report}}})
    try:
        with host.store.transaction() as tx:
            tx.create_run("run", host.version)
            record(tx, 1, {}, {"findings": [finding], "probe": {"source": "λ" * 12000}})
            for i in range(2, 21):
                record(tx, i, {"delivery_id": "sibling"}, {"verdict": "passed", "resolved_refs": ["attempt-1#old"]})
            record(tx, 21, {"subject_expert": "peer"}, {"findings": [finding]})
        view = history_view(host.store, "run", base, "current")
        assert view["open_blockers"] == [{"ref": "attempt-1#old", "finding": finding}]
        assert len(view["prior_reports"]) == 17
        assert "λ" not in json.dumps(view, ensure_ascii=False)
        request = SimpleNamespace(run_id="run", input_artifacts=(EmbeddedInput.from_bytes(
            "x--verification-context", "application/json", json.dumps(view).encode()),))
        page = report_page(host.store, request, {"report_ref": "attempt-1"})
        parts = [page["text"]]
        while page["next_offset"] is not None:
            page = report_page(host.store, request, {"report_ref": "attempt-1", "offset": page["next_offset"]})
            parts.append(page["text"])
        assert len(parts) > 1 and json.loads("".join(parts))["probe"]["source"] == "λ" * 12000
        with pytest.raises(AgentACIError, match="supplied|listed"):
            report_page(host.store, request, {"report_ref": "attempt-21"})
        with host.store.transaction() as tx:
            record(tx, 22, {}, {"verdict": "passed", "resolved_refs": ["attempt-1#old"]})
        assert history_view(host.store, "run", base, "current")["open_blockers"] == []
    finally:
        host.close()


def test_review_displays_recorded_and_missing_results_with_their_basis():
    checks = [{"id": key, "required": True, "basis": "expected 3"} for key in ("a", "b")]
    row = {"id": "a", "status": "FAIL", "details": ["obtained 2"]}
    result = {"attempt_id": "probe", "report_ref": "inspection", "checks": [row]}
    ctx = {"phase": "review", "probe_plan": {"checks": checks}, "probe_report_ref": "inspection", "open_blockers": ["previous"],
           "probe_evidence": {"attempt_id": "probe", "usable": False, "terminal_reason": "FAILED",
                              "observation": {"check_results": result}}}
    focus = verification_focus(ctx)
    assert focus["checks"][0]["observed"] == row and focus["checks"][1]["observed"] is None
    assert focus["checks"][1]["basis"] == "expected 3" and focus["open_blockers"] == ["previous"]
    result["attempt_id"] = "another-probe"
    focus = verification_focus(ctx)
    assert not focus["result_matches_probe"] and all(c["observed"] is None for c in focus["checks"])


def test_resource_reference_uses_scoped_exact_estimate_and_rejects_scientific_edits():
    req = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    measurement, files = measurement_package()
    capture = {"candidate": {"measurement": measurement.to_dict(), "method": "base"},
               "workspace_files": [WorkspaceArtifact.from_bytes(k, v).to_dict() for k, v in files.items()],
               "resource_estimate": {"workload": {"batch": "8"}, "peak_memory_mib": 1024, "basis": "measured"}}
    host = SimpleNamespace(_submission_source=lambda _: (None, req, {"arguments": capture}, None))
    owned = {"exp": {"source_submit_command_id": "submit"}}
    args = {"replaces_experiment_id": "exp", "candidate_ref": "exp", "resource_estimate_ref": "exp",
            "candidate": {"method": "new"}}
    actual, pinned = expand_submission(host, req, args, owned)
    assert actual["resource_estimate"] == capture["resource_estimate"] and pinned == files
    assert actual["candidate"] == {**capture["candidate"], "method": "new"}
    actual["workspace_files"] = []
    inherit_captured_scoring(actual, pinned)
    assert {a.path: a.content_bytes() for a in map(WorkspaceArtifact.from_dict, actual["workspace_files"])} == files
    actual["workspace_files"] = [WorkspaceArtifact.from_bytes("parse.py", b"edited").to_dict()]
    with pytest.raises(AgentACIError, match="Frozen parser differs"):
        inherit_captured_scoring(actual, pinned)
    for edit in ({"candidate": {"measurement": {}}}, {"resource_estimate_ref": "other"}, {"resource_estimate": {}}):
        with pytest.raises(AgentACIError):
            expand_submission(host, req, {**args, **edit}, owned)


def test_tool_scope_and_lost_response_reconcile_without_resubmission(monkeypatch):
    req = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    for version in (47, 48):
        scope, names = host_tool_configuration(replace(req, schema_version=version))
        tools = {t["name"]: t["inputSchema"] for t in scoped_tools(scope, names)}
        assert ("candidate_ref" in tools["submit_experiment"]["properties"]) == (version == 48)
        assert ("get_submission_receipt" in tools) == (version == 48)
    calls = []
    def lost(socket, tool, arguments):
        calls.append((tool, arguments))
        if tool == "submit_experiment":
            raise TimeoutError("lost reply")
        return {"receipt": {"exp_id": "same"}}
    monkeypatch.setattr("hypoloom.host_tools_server._call_host", lost)
    result = call_tool("socket", "submit_experiment", {"candidate_ref": "design"}, references=True)
    assert result["receipt"]["exp_id"] == "same" and result["content_match"] == "unconfirmed"
    assert calls == [("submit_experiment", {"candidate_ref": "design"}), ("get_submission_receipt", {})]
    with pytest.raises(ValueError, match="Example"):
        _arguments("get_resources", {"gpu_count": 1})
    with pytest.raises(ValueError, match="basis"):
        _arguments("get_resources", {"resource_estimate": {"gpu_count": 1}})


def test_workspace_inherits_missing_scoring_files_and_rejects_an_edited_parser(tmp_path, monkeypatch):
    from hypoloom.agent_sessions import PreparedSessionWorkspace
    from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
    spec, pinned = measurement_package()
    req = replace(default_request(AgentCommand.EXPERIMENT_EXECUTION), schema_version=48)
    workspace = SimpleNamespace(request=req, worktree_root=tmp_path, capture_patch=lambda **_: b"")
    monkeypatch.setattr("hypoloom.agent_sessions.declared_measurement_request", lambda _: True)
    (tmp_path / "train.py").write_bytes(b"print(1)")
    candidate = {"measurement": spec.to_dict(), "execution_argv": ["python", "/input/train.py"]}
    captured = PreparedSessionWorkspace.capture_workspace(workspace, candidate, frozen_measurement_files=pinned)
    files = {a.path: a.content_bytes() for a in map(WorkspaceArtifact.from_dict, captured)}
    assert all(files[path] == data for path, data in pinned.items()) and files["train.py"] == b"print(1)"
    (tmp_path / "parse.py").write_bytes(b"print('changed')")
    with pytest.raises(GitAuthoringIntegrityError, match="frozen measurement parser differs"):
        PreparedSessionWorkspace.capture_workspace(workspace, candidate, frozen_measurement_files=pinned)


def test_native_checkpoint_repeated_evidence_and_new_progress(tmp_path):
    request = replace(default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT), schema_version=48)
    server = NativeOutputValidator(tmp_path / "check.sock", session_id="author",
        output_schema={"type": "object", "required": ["method"]}, max_failed_validations=3,
        max_request_bytes=8192, deadline_at=time.monotonic() + 30,
        draft_check=True, authoring_progress=True, design_checkpoints=True)
    try:
        server._dispatch({"session_id": "author", "operation": "check_draft", "text": "{}"})
        correction = server.snapshot()
        assert correction["authoring"]["deterministic_failure"]["constraint"] == "required"
    finally:
        server.close()
    wire = {"input_tokens": 16, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 0,
            "output_tokens": 1, "total_tokens": 17, "completed_requests": 1, "unmeasured_requests": 0}
    obs = {"retry_class": "MODEL", "diagnostics": {"structured_output_correction": correction, "provider_observed_usage": wire}}
    first = {**obs, "design_checkpoint": build_checkpoint(request, obs, [])}
    req = replace(request, attempt_ordinal=2, attempt_id="second")
    repeated = build_checkpoint(req, obs, [first])
    assert repeated["would_pause"] and repeated["measured_usage"]["uncached_input_tokens"] == 12
    infra = {**obs, "retry_class": "INFRASTRUCTURE"}
    assert not build_checkpoint(req, infra, [first])["would_pause"]
    new = EmbeddedInput.from_bytes("new-evidence", "application/json", b"{}")
    changed = replace(req, input_artifacts=(*req.input_artifacts, new),
                      immutable_context_refs=(*req.immutable_context_refs, new.ref))
    assert not build_checkpoint(changed, obs, [first])["would_pause"]
    rows = [{"state": "CLOSED", "terminal_reason": "FAILED", "attempt_ordinal": n,
             "observation_json": json.dumps({**obs, "design_checkpoint": ck})} for n, ck in enumerate((first["design_checkpoint"], repeated), 1)]
    assert json.loads(handoff(rows)[0].content_bytes()) == repeated
    for policy in ("shadow", "enforce"):
        assert agent_attempts_exhausted({"budget": {"design_retry_policy": policy}}, rows,
            model_fallback=4, infrastructure_fallback=4) == (policy == "enforce")


def test_efficiency_includes_failed_attempts_and_keeps_unknown_separate():
    wire = {"cache_read_input_tokens": 80, "cache_creation_input_tokens": 4, "output_tokens": 5,
            "total_tokens": 105, "completed_requests": 2, "unmeasured_requests": 1}
    observer = {"diagnostics": {"provider_observed_usage": wire}}
    empty = {"diagnostics": {"provider_observed_usage": dict.fromkeys(wire, 0)}}
    agents = [({}, o, {"command": "DESIGN", "role": "math"}) for o in (observer, observer, {}, empty)]
    experiments = {str(i): {"preregistered": True, "evaluation_scope": scope, "outcome": outcome} for i, (scope, outcome)
                   in enumerate((("LOCAL", "CLEAN_REFUTATION"), ("GOAL", "EXECUTION_FAILURE")))}
    result = research_efficiency(agents, experiments, 7200)
    assert result["measured_uncached_input_plus_output_per_comparable"] == 42
    assert result["measured_usage"]["unmetered_attempts"] == 1
    assert result["measured_usage"]["attempts_without_observed_requests"] == 1
    assert result["by_role"]["math"]["cache_read_input_tokens"] == 160
    assert result["outcomes"]["LOCAL"]["comparable_per_hour"] == .5
    assert result["outcomes"]["GOAL"] == {"preregistered": 1, "comparable": 0, "comparable_per_hour": 0}


def test_page_telemetry_separates_files_at_the_same_line_offset():
    rows = []
    for n, name in enumerate(("0000.view", "0000.page-0001", "0000.page-0002", "0000.page-0001")):
        for block in ({"type": "tool_use", "name": "Read", "id": str(n), "input": {"file_path": f"/inputs/{name}.txt"}},
                      {"type": "tool_result", "tool_use_id": str(n)}):
            rows.append({"sessionId": "s", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": [block]}})
    stats = transcript_metrics("\n".join(map(json.dumps, rows)), "s", 0, ["ctx"])
    assert stats["immutable_read_calls"] == 4 and stats["exact_reread_calls"] == 1
