"""Native completion validation cannot bypass Host rejection or erase billing."""

import copy
import io
from dataclasses import replace
from types import SimpleNamespace

import pytest

import hypoloom.claude_agent_host as adapter
from hypoloom.agent_context import agent_retry_class
from test_claude_agent_host import _claude_request, _provider_ndjson
from test_normal_output_adapter import _normal_result
from test_partial_cli_accounting import _charge


@pytest.mark.parametrize("invalid,snapshot,prevented_field,failure,retry,bill", [
    (True, {"accepted": False, "status": "unresolved"}, None, "invalid_provider_envelope", "INFRASTRUCTURE", "provider"),
    (True, {"accepted": True, "status": "first_pass"}, None, "invalid_provider_envelope", "INFRASTRUCTURE", "provider"),
    (False, {"accepted": False, "status": "not_observed"}, None, "invalid_provider_envelope", "INFRASTRUCTURE", "provider"),
    (False, {"accepted": False, "status": "validation_failed", "error": "endpoint unavailable"},
        None, "invalid_provider_envelope", "INFRASTRUCTURE", "provider"),
    (False, {"accepted": True, "status": "validation_failed", "error": "endpoint unavailable"},
        None, "invalid_provider_envelope", "INFRASTRUCTURE", "provider"),
    (False, {"accepted": True, "status": "first_pass"}, "stop_reason", "invalid_provider_envelope", "INFRASTRUCTURE", "provider"),
    (False, {"accepted": True, "status": "first_pass"}, "terminal_reason", "invalid_provider_envelope", "INFRASTRUCTURE", "provider"),
    (False, {"accepted": True, "status": "first_pass"}, None, None, None, "response"),
])
def test_normal_observation_keeps_validation_and_reported_billing_separate(
    monkeypatch, invalid, snapshot, prevented_field, failure, retry, bill,
):
    request = replace(_claude_request(), schema_version=11)
    outer, _ = _normal_result('{"typed_candidate":"invalid"}' if invalid else None)
    if prevented_field:
        outer[prevented_field] = "stop_hook_prevented"
    before = copy.deepcopy(outer)
    def accepted_text():
        # Mirror the validator's invocation-file interface. Unaccepted output
        # and an accepted file that fails decoding are delivery failures here;
        # native rejected-candidate traces are covered by the Host G3 cases.
        if snapshot.get("accepted") is not True or snapshot.get("error"):
            raise ValueError("native output was not accepted")
        return outer["result"]
    handle = SimpleNamespace(
        _observation=None, _process=SimpleNamespace(poll=lambda: 0), request=request,
        model="stub-model", _stdout_file=io.BytesIO(_provider_ndjson(outer)), _stderr_file=io.BytesIO(),
        _output_validator=SimpleNamespace(snapshot=lambda: snapshot, close=lambda: None,
            accepted_text=accepted_text), tool_workspace=None,
        _runtime_diagnostics=None, _provider_relay=None, pid=123, process_group_id=123, started_at=0,
        _prompt_manifest=None, _session_usage_baseline=None,
    )
    host = SimpleNamespace(kill_grace_seconds=0, _clock=lambda: 1, _finalize_resources=lambda _: None)
    monkeypatch.setattr(adapter, "_bounded_wait", lambda *_: None)
    monkeypatch.setattr(adapter, "process_group_exists", lambda _: False)
    monkeypatch.setattr(adapter, "_stream_size", lambda stream: len(stream.getbuffer()))
    result = adapter.ClaudeAgentHost._observe(host, handle, None)
    assert result.failure_code == failure
    assert (result.response is not None) is (failure is None)
    assert result.diagnostics["structured_output_correction"] == snapshot
    assert _charge(request, result.response, result.diagnostics).source == bill
    if failure:
        assert agent_retry_class(failure, {"diagnostics": result.diagnostics}, retryable=True) == retry
    assert outer == before
