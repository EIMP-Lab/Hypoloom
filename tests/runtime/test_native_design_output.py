"""Real source/patch capture must give correctable feedback before CLI exit."""

import json
import time
from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError, validate_postflight, with_output_digest
from hypoloom.claude_agent_host import (
    _capture_tool_workspace, _native_candidate_check, _output_schema, _provider_response,
)
from hypoloom.fake_agent import FakeAgent
from hypoloom.native_output import NativeOutputValidator, call_validator
from test_agent_sessions import _manager, _seed
from test_claude_git_authoring import _git, _request, _target
from test_claude_measurement_capture import _deferred
from test_deferred_measurement_host import DeclaredMeasurementAgent


def _case(tmp_path, *, declared=False):
    _, binding = _target(tmp_path)
    manager = _manager(tmp_path)
    _seed(manager, binding)
    request = _request()
    if declared:
        request = _deferred(replace(request, input_artifacts=(), immutable_context_refs=()))
    request = replace(request, schema_version=29,
        budget=replace(request.budget, max_output_bytes=131072))
    workspace = manager.prepare(request, binding)
    response = (DeclaredMeasurementAgent() if declared else FakeAgent()).invoke(request)
    candidate = {**response.typed_candidate, "evaluator_version": "component-result/7"}
    output = {"typed_candidate": candidate,
        "declared_effects": [{"effect": x.effect} for x in response.declared_effects]}
    source = workspace.worktree_root / ("experiment.sh" if declared else "experiment.py")
    if declared:
        (workspace.worktree_root / "parse_metrics.py").write_text("print('parser')\n")
    return request, workspace, output, source


def _server(tmp_path, request, workspace, *, cap=3):
    return NativeOutputValidator(tmp_path / "output.sock",
        output_schema=_output_schema(request, tool_workspace=True),
        session_id="design-session", max_failed_validations=cap,
        max_request_bytes=4 * 1024 * 1024, deadline_at=time.monotonic() + 30,
        candidate_check=_native_candidate_check(request, workspace))


def _call(server, output):
    return call_validator(str(server.socket_path), session_id="design-session",
        last_assistant_message=json.dumps(output), max_request_bytes=4 * 1024 * 1024,
        timeout_seconds=5)


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("size", [52000, 70000])
@pytest.mark.parametrize("committed", [False, True])
def test_design_repairs_encoded_or_git_size_in_the_same_native_stop(tmp_path, declared, size, committed):
    request, workspace, output, source = _case(tmp_path, declared=declared)
    source.write_bytes(b"#" + b"x" * (size - 2) + b"\n")
    if committed:
        _git(workspace.worktree_root, "add", ".")
        _git(workspace.worktree_root, "-c", "user.name=Hypoloom Tests", "-c",
            "user.email=tests@example.invalid", "commit", "-qm", "implementation")
    server = _server(tmp_path, request, workspace)
    try:
        rejected = _call(server, output)
        assert rejected.get("decision") == "block", rejected
        assert "131072" in rejected["reason"]
        assert "patch" in rejected["reason"] and "untracked" in rejected["reason"]
        assert not server.snapshot()["accepted"]
        source.write_text("# reduced implementation\n")
        assert _call(server, output) == {}
        assert server.snapshot()["status"] == "recovered"
        assert server.snapshot()["rejected_count"] == 1
        artifacts = _capture_tool_workspace(workspace, max_bytes=131072,
            candidate=output["typed_candidate"])
        assert sum(len(x.content_bytes()) for x in artifacts) < 131072
        if not declared:
            outer = {"type": "result", "subtype": "success", "is_error": False,
                "num_turns": 1, "total_cost_usd": 0,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "modelUsage": {request.required_model_identity: {}}, "result": json.dumps(output)}
            response, failure, _ = _provider_response(request, request.required_model_identity,
                json.dumps(outer).encode(), workspace_artifacts=artifacts, final_text_output=True)
            assert response is not None and failure is None
    finally:
        server.close()


def test_design_stop_retains_explicit_correction_cap(tmp_path):
    request, workspace, output, source = _case(tmp_path)
    source.write_bytes(b"#" + b"x" * 51998 + b"\n")
    server = _server(tmp_path, request, workspace, cap=1)
    try:
        assert _call(server, output) == {}
        assert server.snapshot()["status"] == "unresolved"
        assert server.snapshot()["accepted"] is False
        assert server.snapshot()["call_count"] == 1
    finally:
        server.close()


def test_design_stop_rechecks_source_after_previous_acceptance(tmp_path):
    request, workspace, output, source = _case(tmp_path)
    source.write_text("# small\n")
    server = _server(tmp_path, request, workspace)
    try:
        assert _call(server, output) == {}
        source.write_bytes(b"#" + b"x" * 51998 + b"\n")
        with pytest.raises(ValueError, match="byte"):
            _call(server, output)
        assert not server.snapshot()["accepted"]
    finally:
        server.close()


def test_design_stop_reports_missing_declared_source_without_following_symlink(tmp_path):
    request, workspace, output, source = _case(tmp_path, declared=True)
    server = _server(tmp_path, request, workspace)
    try:
        missing = _call(server, output)
        assert missing.get("decision") == "block"
        assert "experiment.sh" in missing["reason"]
        source.symlink_to(tmp_path / "not-an-authorized-source")
        linked = _call(server, output)
        assert linked.get("decision") == "block"
        assert "symlink" in linked["reason"]
        assert not server.snapshot()["accepted"]
    finally:
        server.close()


def test_stop_preview_does_not_replace_full_envelope_or_accounting_validation(tmp_path):
    request, workspace, output, source = _case(tmp_path)
    source.write_text("# small\n")
    _native_candidate_check(request, workspace)(output)
    artifacts = _capture_tool_workspace(workspace, max_bytes=131072)
    response = replace(FakeAgent().invoke(request), typed_candidate=output["typed_candidate"],
        proposed_workspace_artifacts=artifacts, model_identity=request.required_model_identity,
        adapter_version=request.required_adapter_version)
    oversized = with_output_digest(replace(response, diagnostics={"late_metadata": "x" * 131072}))
    with pytest.raises(AgentACIError, match="output_too_large"):
        validate_postflight(request, oversized)
    over_budget = with_output_digest(replace(response, cost="1"))
    with pytest.raises(AgentACIError, match="cost_budget_exceeded"):
        validate_postflight(request, over_budget)
