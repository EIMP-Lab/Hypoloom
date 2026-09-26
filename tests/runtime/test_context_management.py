"""Context boundaries preserve facts and effects while replacing model history."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.agent_context import agent_retry_class
from hypoloom.agent_sessions import AgentSessions
from hypoloom.claude_agent_host import _prompt, _classify_cli_oversized_input, _native_candidate_check
from hypoloom.context_management import context_policy, request_capacity, latest_context, input_usage
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.provider_observation import provider_failure, provider_retry_gate
from hypoloom.workflow_host import WorkflowHost
from test_agent_sessions import _manager, _chain_request, _transcript
from test_git_authoring_workspace import make_target
from test_provider_relay import upstream, _relay, _request


def request24(command=AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, **memory):
    request = replace(default_request(command), schema_version=24,
        **({"required_output_schema": "GroundingCandidate/v3"} if command is AgentCommand.GROUNDING else {}))
    if memory:
        request = replace(request, input_artifacts=(*request.input_artifacts,
            EmbeddedInput.from_bytes("context--expert-config", "application/json",
                json.dumps({"node_runtime": {"memory": memory}}).encode())))
    return request


def test_context_usage_is_last_request_not_cumulative_and_cache_semantics_are_explicit():
    exchanges = [{"request_path": "/v1/messages", "usage": {"input_tokens": 3250503}},
        {"request_path": "/v1/messages", "usage": {"input_tokens": 72337, "cache_read_input_tokens": 400000}},
        {"request_path": "/v1/messages/count_tokens", "upstream_status": 404}]
    assert latest_context(exchanges)["input_tokens"] == 472337
    assert input_usage(exchanges[1]["usage"], "includes_cache") == 72337
    assert input_usage(exchanges[1]["usage"], "unknown") is None
    assert latest_context([{"request_path": "/v1/messages"}])["source"] == "unknown"


def test_recovery_notes_keep_bounded_own_tool_error_and_latest_result():
    from hypoloom.context_management import recovery_tool_notes
    rows = [{"sessionId": "own", "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "Bash"},
        {"type": "tool_use", "id": "b", "name": "Read"}]}}]
    rows.extend({"sessionId": "own", "message": {"content": [
        {"type": "tool_result", "tool_use_id": str(i), "content": "old output"}]}}
        for i in range(20))
    rows.extend([
        {"sessionId": "own", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": True, "content": "未解决错误" * 400}]}},
        {"sessionId": "own", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "latest code inspection"}]}},
        {"sessionId": "other", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "private other session"}]}},
    ])
    notes = recovery_tool_notes(b"\n".join(json.dumps(row).encode() for row in rows), "own")
    assert len(notes) <= 3
    assert any(item["is_error"] and item["truncated"] and item["tool"] == "Bash" for item in notes)
    assert notes[-1]["excerpt"] == "latest code inspection"
    assert "private other session" not in json.dumps(notes)
    assert len(json.dumps(notes, ensure_ascii=False).encode()) < 4096
    assert recovery_tool_notes(b"{partial", "own") == []


def test_capacity_never_uses_model_name_or_invocation_budget_as_window():
    request = request24(context_window_tokens=100000, runtime_context_tokens=80000, working_context_tokens=60000)
    policy = context_policy(request)
    assert policy["effective_context_tokens"] == 60000
    assert context_policy(replace(request, required_model_identity="anything[1m]")) == policy
    assert context_policy(replace(request, budget=replace(request.budget, max_tokens=9999999))) == policy
    capacity = request_capacity({"messages": [{"content": "中" * 20000}], "max_tokens": 1000}, policy)
    assert not capacity["fits"] and capacity["estimate_source"] == "utf8_bytes_conservative_estimate"


def test_total_inline_budget_offloads_repeated_subthreshold_sources_without_changing_facts():
    request = request24(AgentCommand.LITERATURE_REVIEW, max_inline_input_bytes=1000)
    inputs = tuple(EmbeddedInput.from_bytes(f"source-{i}", "text/plain", (str(i) * 600).encode()) for i in range(10))
    request = replace(request, input_artifacts=(*request.input_artifacts, *inputs))
    before = request.to_dict()
    manifest = {}
    prompt = json.loads(_prompt(request, tool_workspace=True, manifest=manifest))
    assert manifest["inline_input_bytes"] <= 1000
    assert len(manifest["offloaded_refs"]) >= 9
    for index, item in enumerate(prompt["rendered_inputs"]):
        if "path" in item:
            assert item["path"] == f"/inputs/{index:04d}.txt"
            assert item["size_bytes"] == len(request.input_artifacts[index].content_bytes())
    assert request.to_dict() == before
    assert context_policy(replace(request, attempt_ordinal=2))["max_inline_input_bytes"] == 500


def test_fresh_sessions_keep_chain_code_and_archive_without_synthesis_transcript(tmp_path):
    _, binding = make_target(tmp_path)
    manager = _manager(tmp_path)
    first = replace(_chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "design", "a"), schema_version=24)
    workspace = manager.prepare(first, binding)
    (workspace.worktree_root / "experiment.py").write_text("print('retained code')\n")
    original = _transcript(workspace, "old tool history")
    closed = workspace.finish_node()
    second = replace(first, attempt_id="design-retry", attempt_ordinal=2)
    resumed = AgentSessions(manager.state_root, manager.publisher).prepare(second, binding)
    assert resumed.worktree_root == workspace.worktree_root
    assert resumed.binding["branch"] == workspace.binding["branch"]
    assert resumed.binding["session_id"] != workspace.binding["session_id"]
    assert resumed.binding["parent_session_id"] is None
    assert resumed.session_arguments == ("--session-id", resumed.binding["session_id"])
    assert (resumed.worktree_root / "experiment.py").read_text() == "print('retained code')\n"
    assert (manager.publisher.root / closed["transcript"]["path"] / "transcript.jsonl").read_bytes() == original
    _transcript(resumed, "reconstructed task")
    resumed.finish_node()


@pytest.mark.parametrize("code", ["input_too_long", "context_capacity", "compaction_failed"])
def test_context_recovery_is_bounded_infrastructure_and_never_global_api_wait(code):
    details = {"diagnostics": {"provider_failure": {"code": code}}}
    assert agent_retry_class("provider_not_success", details, retryable=True, bounded_context=True) == "INFRASTRUCTURE"
    assert agent_retry_class("session_archive_failed", details, retryable=False, bounded_context=True) is None
    gate = provider_retry_gate([{"code": code, "attempt_id": "one", "ended_at": 5}], cooldown_seconds=60, cleared_at=None, now=10)
    assert not gate["pending"] and gate["state"] == "ready"


def test_native_semantic_rejection_spends_model_retry_but_provider_failure_keeps_precedence():
    diagnostic = {"structured_output_correction": {"status": "unresolved", "rejected_count": 2}}
    classify = lambda d: agent_retry_class("invalid_provider_envelope", {"diagnostics": d}, retryable=True, bounded_context=True)
    assert classify(diagnostic) == "MODEL"
    assert classify({**diagnostic, "provider_failure": {"code": "rate_limited"}}) == "INFRASTRUCTURE"
    assert classify({**diagnostic, "structured_output_correction": {
        "status": "unresolved", "rejected_count": 2, "error": "unavailable"}}) == "INFRASTRUCTURE"


def test_empty_output_limit_is_not_automatically_context_exhaustion_or_normal_truncation():
    exchange = {"request_path": "/v1/messages", "upstream_status": 200, "stop_reason": "max_tokens",
        "usage": {"input_tokens": 99, "output_tokens": 0}}
    assert provider_failure([exchange])["code"] == "empty_output_limit"
    assert provider_failure([{**exchange, "usage": {"input_tokens": 99, "output_tokens": 12}}]) is None
    assert provider_failure([{**exchange, "context_capacity": {"declared_context_tokens": 100}}])["code"] == "input_too_long"
    assert provider_failure([{**exchange, "upstream_status": 429}])["code"] == "rate_limited"
    diagnostics = {"provider_diagnostic": "Compaction failed: empty summary"}
    _classify_cli_oversized_input(request24(), diagnostics)
    assert diagnostics["provider_failure"]["code"] == "compaction_failed"


def test_relay_blocks_growing_tool_history_without_forwarding_and_count_404_is_independent(tmp_path, upstream):
    policy = context_policy(request24(working_context_tokens=1000))
    relay = _relay(tmp_path, upstream, context_policy=policy)
    try:
        upstream.status = 404
        assert _request(relay, path="/v1/messages/count_tokens")[0] == 404
        upstream.status = 200
        assert _request(relay)[0] == 200
        large = json.dumps({"model": relay.model, "messages": [{"role": "user", "content": "x" * 1500}], "max_tokens": 10}).encode()
        assert _request(relay, body=large)[0] == 400
        assert _request(relay)[0] == 400  # No CLI retry storm after local exhaustion.
        assert len(upstream.calls) == 2
        assert relay.context_limit_reached and not relay.token_limit_reached
    finally:
        relay.close()


@pytest.mark.parametrize("version,command,code,blocks", [
    (23, AgentCommand.RESULT_ANALYSIS, "provider_not_success", True),
    (24, AgentCommand.RESULT_ANALYSIS, "provider_not_success", False),
    (24, AgentCommand.RESULT_ANALYSIS, "invalid_candidate", True),
    (24, AgentCommand.ADJUDICATE, "provider_not_success", True),
    (24, AgentCommand.HYPOTHESIS_UPDATE, "provider_not_success", True),
])
def test_only_operational_analysis_failure_can_defer_to_valid_cycle_controls(version, command, code, blocks):
    request = replace(default_request(command), schema_version=version)
    host = object.__new__(WorkflowHost)
    host._expert_final_failures = lambda _: [{"attempt_id": request.attempt_id}]
    host.store = SimpleNamespace(get_attempt=lambda _: {"observation_json": json.dumps({"request": request.to_dict(), "failure_code": code})})
    key = request.task_context.key
    assert host._read_batch_science_failed(request.run_id, request.component_id, request.role, key.cycle) is blocks


def test_native_source_feedback_rejects_unretrieved_urls_and_accepts_prior_same_node_evidence(tmp_path):
    request = request24(AgentCommand.GROUNDING)
    workspace = SimpleNamespace(session_root=tmp_path, binding={"session_id": "session"},
        prior_web_urls=frozenset({"https://example.org/actual"}))
    (tmp_path / "session.jsonl").write_bytes(b"\n")
    check = _native_candidate_check(request, workspace)
    with pytest.raises(ValueError, match="retrieved"):
        check({"typed_candidate": {"web_sources": ["https://example.org/invented"]}})
    check({"typed_candidate": {"web_sources": ["https://example.org/actual"]}})


def test_technical_analysis_exhaustion_preserves_a_budgeted_revision_through_real_cpu(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path
    from hypoloom.control_protocol import ControlRequest
    from hypoloom.domain import AttemptTerminalReason
    from hypoloom.oracle import Oracle
    from hypoloom.profile_v4 import project_profile_v4
    from test_expert_batch_host import TwoCycleBatchAgent, _complete
    from test_expert_loop_host import _pilot_payload
    from test_profile_v4 import documents

    with tempfile.TemporaryDirectory(prefix="context-revision-", dir="/var/tmp") as directory:
        payload = _pilot_payload(tmp_path, Path(directory), "batch-host")
        contract = payload["research_contract"]
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = contract["roles"][0]["id"]
        settings["experts"]["defaults"].update(max_hypotheses_per_round=2, max_forks=2)
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 2
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 0}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        contract["methodology"]["controls"]["product"] = settings
        contract["budget"]["max_experiments"] = contract["roles"][0]["budget"]["max_experiments"] = 4
        host = WorkflowHost(tmp_path / "state", local_agent=TwoCycleBatchAgent())
        finish = host._finish_fake_agent
        def analysis_failure(request, raw):
            if request.command is AgentCommand.RESULT_ANALYSIS and request.task_context.key.cycle == 1:
                host._agent_failure(request, AttemptTerminalReason.FAILED, "provider_not_success", retryable=True,
                    details={"diagnostics": {"provider_failure": {"code": "context_capacity"}}})
            else: finish(request, raw)
        monkeypatch.setattr(host, "_finish_fake_agent", analysis_failure)
        try:
            host.initialize()
            assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload)).status == "accepted"
            _complete(host, timeout=120)
            batches = host._batch_history("batch-host", "primary.validation_accuracy").batches
            assert len(batches) == 2
            outcomes = host.store.list_facts("batch-host", "CanonicalOutcomeAccepted")
            assert len(outcomes) == 4 and [item["payload"]["outcome"] for item in outcomes[:2]] == ["CLEAN_REFUTATION"] * 2
            failed = [item for item in host._expert_final_failures("batch-host")
                if json.loads(host.store.get_attempt(item["attempt_id"])["observation_json"])["request"]["command"] == "RESULT_ANALYSIS"]
            assert len(failed) == 2
            report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
            assert report.ok, report.to_dict()
            from hypoloom.claude_agent_host import process_group_exists
            assert all(row["state"] == "CLOSED" and (row["process_group_id"] is None or not process_group_exists(row["process_group_id"]))
                for row in host.store.list_attempts())
        finally:
            host.close()


@pytest.mark.parametrize("code", ["session_archive_failed", "profile_wallclock_exhausted", "run_stopping"])
def test_context_diagnostic_cannot_override_archive_or_stop_failure(code):
    from hypoloom.agent_context import operational_agent_failure
    observed = {"failure_code": code, "diagnostics": {"provider_failure": {"code": "context_capacity"}}}
    assert not operational_agent_failure(observed)
