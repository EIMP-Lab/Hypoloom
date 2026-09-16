"""Recovery observations never replace candidate validation or actual accounting."""

import copy
import json
from dataclasses import replace

import pytest

from hypoloom.claude_agent_host import _instruction, _provider_response
from tests.runtime.test_claude_agent_host import (
    _claude_request, _provider_ndjson, _provider_output,
)
from tests.runtime.test_partial_cli_accounting import _charge


def _request():
    request = _claude_request()
    return replace(request, budget=replace(request.budget, max_turns=6))


def _events(*, subtype="success", complete_usage=True, invalid_final=False):
    outer = json.loads(_provider_output(subtype=subtype, is_error=subtype != "success",
        total_cost_usd=0, num_turns=3,
        usage={"input_tokens": 3 if complete_usage else 2, "output_tokens": 3}))
    valid = copy.deepcopy(outer["structured_output"])
    rejected = {"typed_candidate": {"claim": "private rejected claim"}}
    partial = {"typed_candidate": {"owner_role": "scientist"}}
    if invalid_final:
        outer["structured_output"] = copy.deepcopy(partial)
    events = []
    for index, candidate in enumerate((rejected, partial, partial if invalid_final else valid), 1):
        failed = index < 3 or subtype != "success" or invalid_final
        events.extend([
            {"type": "assistant", "message": {"id": f"message-{index}",
                "role": "assistant", "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": [{"type": "tool_use", "id": f"schema-{index}",
                    "name": "StructuredOutput", "input": candidate}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"schema-{index}",
                    "is_error": failed, "content": f"missing required fields round {index}" if failed
                    else "Structured output provided successfully"}]}},
        ])
    return [*events, outer]


def test_success_retains_earlier_errors_and_only_returns_the_complete_final_candidate():
    events = _events()
    before = copy.deepcopy(events)
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*events))
    assert failure is None and response is not None
    assert response.typed_candidate == events[-1]["structured_output"]["typed_candidate"]
    trace = diagnostics["structured_output_correction"]
    assert (trace["status"], trace["call_count"], trace["rejected_count"]) == ("recovered", 3, 2)
    assert [item["tool_use_id"] for item in trace["rounds"]] == ["schema-1", "schema-2", "schema-3"]
    assert [item["validation_error"] for item in trace["rounds"][:2]] == [
        "missing required fields round 1", "missing required fields round 2"]
    assert "provider_structured_candidate" not in diagnostics
    assert "provider_diagnostic" not in diagnostics
    assert "private rejected claim" not in json.dumps(trace)
    assert diagnostics["provider_accounting"]["token_usage"]["total_tokens"] == 6
    assert events == before


@pytest.mark.parametrize("subtype", ["success", "error_max_structured_output_retries"])
def test_correction_trace_does_not_repair_an_incomplete_cli_bill(subtype):
    request = _request()
    response, failure, diagnostics = _provider_response(request, "stub-model",
        _provider_ndjson(*_events(subtype=subtype, complete_usage=False)))
    assert response is None
    assert failure == ("invalid_provider_metadata" if subtype == "success" else "provider_not_success")
    assert diagnostics["structured_output_correction"]["call_count"] == 3
    assert "provider_accounting" not in diagnostics
    assert diagnostics["provider_reported_lower_bound"]["token_usage"]["input_tokens"] == 2
    assert _charge(request, response, diagnostics).source == "declared"


def test_exhausted_correction_preserves_last_rejection_without_making_a_response():
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_events(subtype="error_max_structured_output_retries")))
    assert response is None and failure == "provider_not_success"
    assert diagnostics["structured_output_correction"]["status"] == "unresolved"
    assert diagnostics["structured_output_correction"]["rejected_count"] == 3
    assert diagnostics["provider_diagnostic"] == "missing required fields round 3"
    assert diagnostics["provider_structured_candidate"] is not None


@pytest.mark.parametrize("cc_tool_succeeded", [False, True])
def test_partial_corrected_object_is_not_merged_with_prior_candidates(cc_tool_succeeded):
    events = _events(invalid_final=True)
    if cc_tool_succeeded:
        events[-2]["message"]["content"][0]["is_error"] = False
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*events))
    assert response is None and failure == "invalid_structured_output"
    assert diagnostics["structured_output_correction"]["status"] == (
        "recovered" if cc_tool_succeeded else "unresolved")


def test_correction_prompt_requests_a_complete_object_not_a_delta():
    instruction = _instruction(_claude_request())
    assert "complete corrected object, not just changed fields" in instruction
    assert "preserve unaffected required fields" in instruction
