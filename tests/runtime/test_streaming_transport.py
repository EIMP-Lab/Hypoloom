"""Count-only CLI progress must not consume meaningful output capacity."""
import json
import tempfile

import pytest

from hypoloom.agent_context import agent_failure_stage, agent_retry_class, operational_agent_failure
from hypoloom.claude_agent_host import _read_agent_stdout, _unpack_provider_stream
from hypoloom.provider_observation import provider_failure


def _line(value):
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


_PROGRESS = {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 1,
    "estimated_tokens_delta": 1, "session_id": "session", "uuid": "event"}
_RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "done",
    "usage": {"input_tokens": 7, "output_tokens": 11}, "num_turns": 1}


def _read(raw, content_limit=4096, raw_limit=8 * 1024 * 1024, filter_progress=True):
    with tempfile.TemporaryFile() as stream:
        stream.write(raw)
        output, diagnostic = _read_agent_stdout(stream, content_limit,
            raw_limit=raw_limit, filter_progress=filter_progress)
        stream.seek(0)
        assert stream.read() == raw  # The original evidence stays intact.
    return output, diagnostic


def test_complete_progress_heavy_stream_preserves_content_and_usage():
    progress = _line(_PROGRESS) * 33000
    assistant = _line({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "Actual reasoning stays here."},
        {"type": "text", "text": "done"}]}})
    raw = progress + assistant + _line(_RESULT)
    assert len(raw) > 4 * 1024 * 1024
    output, diagnostic = _read(raw)
    assert output == assistant + _line(_RESULT)
    assert diagnostic["progress_events"] == 33000
    assert diagnostic["progress_bytes"] == len(progress)
    assert diagnostic["content_bytes"] == len(output)
    before = _unpack_provider_stream(raw)
    after = _unpack_provider_stream(output)
    assert before == after
    assert after[1] is None and after[0]["usage"] == _RESULT["usage"]


@pytest.mark.parametrize("record", [
    {**_PROGRESS, "text": "content"}, {**_PROGRESS, "error": "failure"},
    {**_PROGRESS, "estimated_tokens": True}, {**_PROGRESS, "estimated_tokens_delta": -1},
    {**_PROGRESS, "subtype": "unknown"}, {**_PROGRESS, "uuid": ""},
    {**_PROGRESS, "session_id": "x" * 1200}, {"type": "system", "subtype": "thinking_tokens"},
])
def test_unknown_or_content_bearing_events_are_not_exempt(record):
    raw = _line(record)
    output, diagnostic = _read(raw, content_limit=len(raw) - 1)
    assert output is None and diagnostic["limit_scope"] == "stdout_content"
    assert diagnostic["progress_events"] == 0


@pytest.mark.parametrize("suffix", [b"{broken\n", b"\n", _line({**_RESULT, "result": "different"})])
def test_filter_preserves_malformed_tail_and_conflicting_completions(suffix):
    raw = _line(_PROGRESS) + _line(_RESULT) + suffix
    output, _ = _read(raw)
    assert _unpack_provider_stream(output)[1] == _unpack_provider_stream(raw)[1]
    assert _unpack_provider_stream(output)[1] is not None


def test_raw_and_content_limits_are_distinct_and_legacy_keeps_progress():
    raw = _line(_PROGRESS) * 10 + _line(_RESULT)
    output, diagnostic = _read(raw, raw_limit=len(raw) - 1)
    assert output is None and diagnostic["limit_scope"] == "stdout_raw"
    assert diagnostic["raw_bytes"] == len(raw)
    output, diagnostic = _read(raw, content_limit=len(raw) - 1, filter_progress=False)
    assert output is None and diagnostic["limit_scope"] == "stdout_content"
    assert diagnostic["progress_events"] == 0
    raw = _line(_PROGRESS).rstrip(b"\n")
    assert _read(raw)[0] == raw  # An incomplete line gets no exemption.


@pytest.mark.parametrize("code", ["provider_transport_limit", "transport_output_too_large"])
def test_local_transport_exhaustion_is_terminal_operational_failure(code):
    details = {"failure_code": code, "diagnostics": {"provider_subtype": "error_during_execution"}}
    assert agent_retry_class(code, details, retryable=True, bounded_context=True) is None
    assert agent_failure_stage(code, details) == "provider_transport"
    assert operational_agent_failure(details)
    assert provider_failure([{"failure_reason": "response_byte_limit", "upstream_status": 200}]) is None
