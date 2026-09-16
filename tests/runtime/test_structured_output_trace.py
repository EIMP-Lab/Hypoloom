"""Pure, non-authoritative StructuredOutput correction observations."""
from __future__ import annotations

import copy
import json

import pytest

from hypoloom import diagnostics
from hypoloom.debug_mode import _attempt_projection, _attempt_relationships


def _call(identity, candidate, *, message_id=None, tool="StructuredOutput"):
    return {"type": "assistant", "message": {
        "id": message_id or "message-" + identity, "role": "assistant",
        "content": [{"type": "thinking", "thinking": "PRIVATE THINKING"},
                    {"type": "tool_use", "id": identity, "name": tool,
                     "input": candidate}]}}


def _result(identity, *, error=False, content="Structured output provided successfully"):
    return {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": identity, "is_error": error,
        "content": content}]}}


def _trace(events):
    return diagnostics.structured_output_trace(events)


def test_rejected_repeated_object_then_correction_is_retained_without_payloads():
    rejected = {"typed_candidate": {"summary": "PRIVATE VALUE", "extra": True}}
    corrected = {"typed_candidate": {"summary": "CORRECTED PRIVATE VALUE"}}
    events = [_call("one", rejected), _result("one", error=True, content="/typed_candidate: extra field"),
              _call("two", copy.deepcopy(rejected)), _result("two", error=True, content="/typed_candidate: extra field"),
              _call("three", corrected), _result("three"),
              {"type": "result", "subtype": "success", "structured_output": corrected}]
    before = copy.deepcopy(events)
    trace = _trace(events)
    assert trace["scope"] == "cc_tool_observation"
    assert trace["status"] == "recovered"
    assert (trace["call_count"], trace["rejected_count"], trace["tool_succeeded_count"], trace["unknown_count"]) == (3, 2, 1, 0)
    assert trace["truncated"] is False
    assert [r["status"] for r in trace["rounds"]] == ["rejected", "rejected", "tool_succeeded"]
    assert [r["repeats_rejected_input"] for r in trace["rounds"]] == [False, True, False]
    assert [r["call_record_index"] for r in trace["rounds"]] == [0, 2, 4]
    assert [r["result_record_index"] for r in trace["rounds"]] == [1, 3, 5]
    assert [r["round"] for r in trace["rounds"]] == [1, 2, 3]
    assert trace["rounds"][0]["candidate_fields"] == ["/typed_candidate", "/typed_candidate/extra", "/typed_candidate/summary"]
    assert trace["rounds"][0]["validation_error"] == "/typed_candidate: extra field"
    assert trace["rounds"][2]["validation_error"] is None
    assert trace["rounds"][0]["message_id"] == "message-one"
    assert events == before
    assert "PRIVATE" not in json.dumps(trace)


@pytest.mark.parametrize("feedback,expected", [
    ([_result("one")], "first_pass"),
    ([_result("one", error=True, content="bad schema")], "unresolved"),
    ([], "unresolved"),
])
def test_final_cli_success_never_invents_tool_validation_feedback(feedback, expected):
    trace = _trace([_call("one", {"typed_candidate": {}}), *feedback,
                    {"type": "result", "subtype": "success"}])
    assert trace["status"] == expected
    assert trace["unknown_count"] == (not feedback)


def test_nonstructured_or_unmatched_errors_do_not_become_rejections():
    trace = _trace([_call("bash", {"command": "PRIVATE"}, tool="Bash"),
                    _result("bash", error=True, content="shell failure"),
                    _call("one", {"typed_candidate": {}}),
                    _result("different", error=True, content="unmatched")])
    assert (trace["call_count"], trace["rejected_count"], trace["unknown_count"]) == (1, 0, 1)
    assert trace["rounds"][0]["validation_error"] is None
    assert _trace([])["status"] == "not_observed"


def test_missing_feedback_before_later_success_remains_explicitly_unresolved():
    trace = _trace([_call("one", {}), _call("two", {}), _result("two")])
    assert trace["status"] == "unresolved"
    assert trace["unknown_count"] == 1
    assert trace["rounds"][1]["repeats_rejected_input"] is False


def test_feedback_before_call_or_malformed_error_flag_is_not_validation():
    malformed = _result("one")
    malformed["message"]["content"][0]["is_error"] = "false"
    trace = _trace([_result("one"), _call("one", {}), malformed])
    assert trace["rounds"][0]["status"] == "unknown"


def test_trace_bounds_and_redacts_without_emitting_candidates_or_thinking():
    events = []
    for index in range(70):
        identity = str(index)
        events.extend([_call(identity, {"typed_candidate": {"api_key": "PRIVATE TOKEN", "summary": "PRIVATE"}}),
                       _result(identity, error=True, content="Authorization: Bearer PRIVATE TOKEN")])
    trace = _trace(events)
    assert trace["call_count"] == trace["rejected_count"] == 70
    assert len(trace["rounds"]) <= 64
    assert trace["truncated"] is True
    assert "PRIVATE" not in json.dumps(trace)
    assert trace["rounds"][0]["validation_error"] == "[credential-shaped diagnostic redacted]"


def test_absent_message_id_and_text_block_errors_remain_observable():
    call = _call("one", {"typed_candidate": {}})
    call["message"].pop("id")
    result = _result("one", error=True, content=[
        {"type": "text", "text": "schema field missing"},
        {"type": "thinking", "thinking": "PRIVATE THINKING"}])
    trace = _trace([call, result])
    assert trace["rounds"][0]["message_id"] is None
    assert trace["rounds"][0]["validation_error"] == "schema field missing"


def _row(identity, ordinal, observation, *, terminal="FAILED", command="command-one"):
    return {"attempt_id": identity, "attempt_ordinal": ordinal, "kind": "AGENT",
            "state": "CLOSED", "terminal_reason": terminal, "run_id": "run-one",
            "workflow_id": "workflow-one", "command_id": command,
            "host_generation": "host-one", "started_at": ordinal,
            "ended_at": ordinal + .5, "observation_json": json.dumps(observation)}


def _project(row, previous=None):
    return _attempt_projection(row, {}, recovered=False, final_failure=False, charge=None,
        retry_of_attempt_id=None if previous is None else previous["attempt_id"],
        previous_row=previous, previous_command=None if previous is None else {},
        previous_observation=None if previous is None else json.loads(previous["observation_json"]),
        source_attempt_id=None, lineage_scope="AGENT_COMMAND", include_payloads=False)


def test_debug_projects_correction_and_durable_session_without_host_success_claim():
    trace = _trace([_call("one", {}), _result("one", error=True, content="schema"),
                    _call("two", {"summary": "PRIVATE"}), _result("two")])
    first = _row("first", 1, {"session": {"session_id": "session-one"}})
    second = _row("second", 2, {"session": {"session_id": "session-one"},
        "failure_code": "invalid_candidate", "diagnostics": {"structured_output_correction": trace}})
    projected = _project(second, first)
    assert projected["structured_output_correction"] == trace
    assert projected["session_id"] == "session-one"
    assert projected["same_session_as_previous_attempt"] is True
    assert projected["retry_of_attempt_id"] == "first"
    assert projected["terminal_reason"] == "FAILED"
    assert projected["failure_code"] == "invalid_candidate"
    assert projected["recovered_by_later_attempt"] is False
    assert "observation" not in projected
    third = _row("third", 1, {"session": {"session_id": "session-one"}}, command="another-command")
    predecessors, _, _, _ = _attempt_relationships([first, second, third], [])
    assert predecessors == {"second": "first"}


def test_debug_old_or_malformed_trace_is_not_reconstructed_or_leaked():
    row = _row("old", 1, {"diagnostics": {"structured_output_correction": {
        "status": "recovered", "input": {"secret": "PRIVATE"}}}})
    projected = _project(row)
    assert projected["structured_output_correction"] is None
    assert projected["session_id"] is None
    assert projected["same_session_as_previous_attempt"] is None
    assert "PRIVATE" not in json.dumps(projected)


def test_recovered_cc_summary_is_explicit_and_never_masks_primary_diagnostic():
    trace = _trace([_call("one", {}), _result("one", error=True, content="schema"),
                    _call("two", {"summary": "corrected"}), _result("two")])
    observation = {"diagnostics": {"provider_subtype": "success", "structured_output_correction": trace}}
    summary = diagnostics.diagnostic_summary(observation)
    assert "CC" in summary and "recovered" in summary and "Host" in summary
    observation["diagnostics"]["provider_diagnostic"] = "primary error"
    assert diagnostics.diagnostic_summary(observation) == "primary error"
