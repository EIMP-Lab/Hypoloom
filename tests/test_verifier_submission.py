"""Structured review transport cannot bypass Stop, evidence or correction limits."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from hypoloom import native_output
from hypoloom.agent_capabilities import capabilities_for
from hypoloom.claude_agent_host import _output_schema, _native_candidate_check
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.verification_contract import validate_review, review_schema, instructions


def _review(version=39):
    request = replace(default_request(AgentCommand.VERIFY_IMPLEMENTATION), schema_version=version)
    report = deepcopy(FakeAgent().invoke(request).typed_candidate)
    report["supporting_refs"] = ["R1"]
    output = {"typed_candidate": report, "declared_effects": [{"effect": request.effect_requirements[0]}]}
    return request, output


def _server(tmp_path, *, cap=3, structured=True, check=None, version=39):
    request, _ = _review(version)
    return native_output.NativeOutputValidator(tmp_path / "output.sock",
        output_schema=_output_schema(request, tool_workspace=True), session_id="review-session",
        max_failed_validations=cap, max_request_bytes=1024 * 1024,
        deadline_at=time.monotonic() + 15, structured_submission=structured, compact_review=version >= 40,
        candidate_check=check or _native_candidate_check(request, None))


def _prepare(server, output, session="review-session"):
    return native_output._call_endpoint(str(server.socket_path), {
        "operation": "prepare_output", "session_id": session, "output": output},
        max_request_bytes=1024 * 1024, timeout_seconds=2)


def _stop(server, text='{"review_submitted":true}'):
    return native_output.call_validator(str(server.socket_path), session_id="review-session",
        last_assistant_message=text, max_request_bytes=1024 * 1024, timeout_seconds=2)


def test_preparation_requires_stop_and_preserves_long_escaped_source(tmp_path):
    _, output = _review()
    source = '# "quoted" backslash \\ unicode 中文\n' * 900
    output["typed_candidate"]["probe"]["files"]["verification/probe.py"] += source
    server = _server(tmp_path)
    try:
        assert _prepare(server, output)["prepared"]
        assert server.snapshot()["status"] == "prepared" and not server.snapshot()["accepted"]
        with pytest.raises(ValueError, match="not accepted"):
            server.accepted_text()
        assert _stop(server) == _stop(server) == {}
        assert json.loads(server.accepted_text()) == output
        assert json.loads(server.accepted_text(final_message='{"review_submitted":true}')) == output
        with pytest.raises(ValueError, match="differs from validated native completion"):
            server.accepted_text(final_message=json.dumps(output))
        assert server.output_path.stat().st_mode & 0o777 == 0o600
        assert server.snapshot()["rejected_count"] == 0
    finally:
        server.close()


@pytest.mark.parametrize("replacement", ["prepare", "final", "numeric_selector"])
def test_failed_replacement_revokes_prepared_and_previously_accepted_output(tmp_path, replacement):
    _, output = _review()
    server = _server(tmp_path, cap=5)
    try:
        _prepare(server, output)
        _stop(server)
        if replacement == "prepare":
            assert not _prepare(server, {})["prepared"]
        else:
            assert _stop(server, '{"review_submitted":1}' if replacement == "numeric_selector" else "not JSON")["decision"] == "block"
        assert _stop(server)["decision"] == "block"
        with pytest.raises(ValueError, match="not accepted"):
            server.accepted_text()
        output["typed_candidate"]["summary"] = "Corrected candidate"
        assert _prepare(server, output)["prepared"]
        assert _stop(server) == {}
        assert json.loads(server.accepted_text()) == output
    finally:
        server.close()


def test_prepare_and_stop_share_one_failure_budget_and_cannot_use_stale_file(tmp_path):
    _, output = _review()
    server = _server(tmp_path, cap=2)
    try:
        assert _stop(server)["decision"] == "block"
        assert _prepare(server, output)["prepared"]
        assert _prepare(server, {})["remaining_corrections"] == 0
        with pytest.raises(ValueError, match="already completed"):
            _stop(server)
        assert not server.snapshot()["accepted"]
    finally:
        server.close()


@pytest.mark.parametrize("boundary", ["session", "deadline", "cancel", "legacy"])
def test_prepared_review_does_not_override_invocation_boundary(tmp_path, boundary):
    _, output = _review()
    server = _server(tmp_path, structured=boundary != "legacy")
    try:
        if boundary == "legacy":
            with pytest.raises(ValueError, match="unavailable"):
                _prepare(server, output)
        else:
            assert _prepare(server, output)["prepared"]
            if boundary == "session":
                with pytest.raises(ValueError, match="session mismatch"):
                    _prepare(server, output, "another-session")
            elif boundary == "deadline":
                server.deadline_at = time.monotonic() - 1
                with pytest.raises(ValueError, match="deadline"):
                    _stop(server)
            else:
                server.close()
        with pytest.raises(ValueError, match="not accepted"):
            server.accepted_text()
    finally:
        server.close()


def test_stop_revalidates_semantics_after_tool_preflight(tmp_path):
    _, output = _review()
    changed = []
    def check(value):
        if changed:
            raise ValueError("current source drift")
    server = _server(tmp_path, check=check)
    try:
        assert _prepare(server, output)["prepared"]
        changed.append(True)
        assert "current source drift" in _stop(server)["reason"]
        assert not server.snapshot()["accepted"]
    finally:
        server.close()


@pytest.mark.parametrize("version", [39, 40])
def test_stdlib_mcp_advertises_versioned_schema_and_returns_field_corrections(tmp_path, version):
    request, output = _review(version)
    schema = _output_schema(request, tool_workspace=True)
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema))
    server = _server(tmp_path, version=version)
    arguments = output if version == 39 else {k: v for k, v in output["typed_candidate"].items() if k != "task_kind"}
    try:
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "submit_review", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "submit_review", "arguments": arguments}}]
        completed = subprocess.run([sys.executable, str(Path(native_output.__file__)), "--review-tool",
            "--socket", str(server.socket_path), "--output-schema", str(path), "--session-id", "review-session",
            "--max-bytes", "1048576", "--timeout-seconds", "5", *(["--compact-review"] if version == 40 else [])],
            input="".join(json.dumps(row) + "\n" for row in requests), text=True, capture_output=True, timeout=10)
        assert completed.returncode == 0, completed.stderr
        rows = [json.loads(row) for row in completed.stdout.splitlines()]
        assert len(rows) == 4
        advertised = rows[1]["result"]["tools"][0]["inputSchema"]
        if version == 39:
            assert advertised == schema
        else:
            original = schema["properties"]["typed_candidate"]
            assert advertised["properties"] == {k: v for k, v in original["properties"].items() if k != "task_kind"}
            assert set(advertised["required"]) == set(arguments)
            assert advertised["additionalProperties"] is False
        assert rows[2]["result"]["isError"]
        assert ("typed_candidate" if version == 39 else "summary") in rows[2]["result"]["content"][0]["text"]
        assert not rows[3]["result"]["isError"] and not server.snapshot()["accepted"]
        assert _stop(server) == {}
        assert json.loads(server.accepted_text()) == output
    finally:
        server.close()


@pytest.mark.parametrize("extra", [{"task_kind": "VERIFY_IMPLEMENTATION"}, {"declared_effects": []}, {"typed_candidate": {}}])
def test_flat_tool_rejects_envelope_fields_and_revokes_stale_preparation(tmp_path, extra):
    _, output = _review(40)
    report = {k: v for k, v in output["typed_candidate"].items() if k != "task_kind"}
    server = _server(tmp_path, version=40)
    try:
        assert _prepare(server, report)["prepared"]
        assert not _prepare(server, {**report, **extra})["prepared"]
        assert _stop(server)["decision"] == "block"
        assert _prepare(server, report)["prepared"]
        assert _stop(server) == {}
        assert json.loads(server.accepted_text()) == output
    finally:
        server.close()


@pytest.mark.parametrize("version", [38, 39, 40])
def test_ordinary_json_completion_remains_supported(tmp_path, version):
    _, output = _review(version)
    server = _server(tmp_path, version=version, structured=version >= 39)
    try:
        assert _stop(server, json.dumps(output)) == {}
        assert json.loads(server.accepted_text()) == output
    finally:
        server.close()


@pytest.mark.parametrize("field", ["summary", "finding", "check", "total"])
def test_narrative_limits_apply_at_host_admission_and_preserve_aci38(field):
    request = replace(default_request(AgentCommand.VERIFY_IMPLEMENTATION), schema_version=39)
    report = deepcopy(FakeAgent().invoke(request).typed_candidate)
    if field == "summary":
        report["summary"] = "x" * 2049
    elif field == "check":
        report["probe"]["checks"][0]["purpose"] = "x" * 1025
    else:
        report["findings"] = [{"id": f"note_{i}", "severity": "advisory", "message": "中" * (2000 if field == "total" else 2049),
            "resolution_condition": "Measure the actual path", "evidence_refs": list(request.immutable_context_refs),
            "followup_of": None} for i in range(3 if field == "total" else 1)]
    with pytest.raises(ValueError, match="explanatory text exceeds"):
        validate_review(request, report)
    validate_review(replace(request, schema_version=38), report)
    if field != "total":
        with pytest.raises(ValueError, match="maxLength"):
            native_output.decode_output(json.dumps(report), review_schema(request))


def test_versioned_tools_and_adoption_guidance_keep_candidate_readonly():
    request, _ = _review()
    old = replace(request, schema_version=38)
    current = capabilities_for(request.command, schema_version=39)
    assert current.mcp_servers == ("verifier-output",)
    assert current.built_in_tools == ("Read", "Glob", "Grep") and not current.bash
    assert not capabilities_for(request.command, schema_version=38).mcp_servers
    assert "submit_review" not in instructions(old)
    prompt = instructions(request)
    assert "actual dataset, loader" in prompt and "disconnected reimplementation" in prompt
    assert "advisories, not" in prompt and "coverage" in prompt
