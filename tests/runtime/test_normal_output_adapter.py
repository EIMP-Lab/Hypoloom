"""Normal native completion preserves exact shape validation and reported fees."""

import copy
import json

import pytest

from hypoloom.claude_agent_host import _output_schema, _prompt, _provider_response
from hypoloom.full_cc_runtime import full_cc_environment
from tests.runtime.test_claude_agent_host import _claude_request, _provider_ndjson, _provider_output
from tests.runtime.test_partial_cli_accounting import _charge, _messages, _request, _result


def _normal_result(text=None):
    outer = json.loads(_provider_output())
    candidate = outer.pop("structured_output")
    candidate["typed_candidate"].update(parent_hypothesis_id=None, relation=None)
    outer["result"] = json.dumps(candidate) if text is None else text
    return outer, candidate


def test_complete_normal_json_uses_exact_candidate_and_original_native_bill():
    outer, candidate = _normal_result()
    before = copy.deepcopy(outer)
    response, failure, diagnostics = _provider_response(_claude_request(), "stub-model",
        _provider_ndjson(outer), final_text_output=True)
    assert failure is None and response is not None
    assert response.typed_candidate == candidate["typed_candidate"]
    assert _charge(_claude_request(), response, diagnostics).source == "response"
    assert outer == before


def test_legacy_parser_does_not_implicitly_accept_normal_text():
    outer, _ = _normal_result()
    response, failure, _ = _provider_response(_claude_request(), "stub-model", _provider_ndjson(outer))
    assert response is None and failure == "missing_structured_output"


@pytest.mark.parametrize("text", ["not json", "```json\n{}\n```", "{} trailing", "[]",
    '{"typed_candidate":NaN}', '{"typed_candidate":"not an object"}',
    '{"typed_candidate":{},"declared_effects":[],"unexpected":true}'])
def test_bad_final_text_is_rejected_without_discarding_complete_native_billing(text):
    outer, _ = _normal_result(text)
    response, failure, diagnostics = _provider_response(_claude_request(), "stub-model",
        _provider_ndjson(outer), final_text_output=True)
    assert response is None and failure == "invalid_structured_output"
    assert diagnostics["provider_diagnostic"]
    assert _charge(_claude_request(), response, diagnostics).source == "provider"


def test_native_schema_still_rejects_wrong_fixed_id_at_normal_completion():
    outer, candidate = _normal_result()
    candidate["typed_candidate"]["component_id"] = "foreign-component"
    outer["result"] = json.dumps(candidate)
    response, failure, diagnostics = _provider_response(_claude_request(), "stub-model",
        _provider_ndjson(outer), final_text_output=True)
    assert response is None and failure == "invalid_structured_output"
    assert "provider_accounting" in diagnostics


def test_rejected_normal_object_retains_only_the_existing_bounded_correction_input():
    outer, candidate = _normal_result()
    candidate["typed_candidate"]["component_id"] = "foreign-component"
    outer["result"] = json.dumps(candidate)
    response, failure, diagnostics = _provider_response(_claude_request(), "stub-model",
        _provider_ndjson(outer), final_text_output=True)
    assert response is None and failure == "invalid_structured_output"
    assert diagnostics["provider_structured_candidate"] == candidate
    assert "provider_accounting" in diagnostics


@pytest.mark.parametrize("value", [
    {"api_key": "secret-example", "typed_candidate": {}},
    {"typed_candidate": {"claim": "x" * 65536}},
    '{"typed_candidate":{},"typed_candidate":{"claim":"duplicate"}}',
    '{"typed_candidate":NaN}', '{"typed_candidate":'])
def test_unsafe_oversize_or_ambiguous_normal_candidates_do_not_enter_retry_input(value):
    outer, _ = _normal_result(value if isinstance(value, str) else json.dumps(value))
    response, _, diagnostics = _provider_response(_claude_request(), "stub-model",
        _provider_ndjson(outer), final_text_output=True)
    assert response is None and "provider_structured_candidate" not in diagnostics
    assert "provider_accounting" in diagnostics


def test_normal_text_does_not_repair_an_incomplete_native_aggregate():
    outer = _result("success")
    outer["result"] = json.dumps(outer.pop("structured_output"))
    response, failure, diagnostics = _provider_response(_request(), "stub-model",
        _provider_ndjson(*_messages(), outer), final_text_output=True)
    assert response is None and failure == "invalid_provider_metadata"
    assert "provider_accounting" not in diagnostics
    assert _charge(_request(), response, diagnostics).source == "declared"


@pytest.mark.parametrize("cap", [1, 3, 12])
def test_native_stop_cap_follows_the_same_admitted_correction_budget(cap):
    env = full_cc_environment(structured_output_failed_validation_cap=cap)
    assert int(env["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"]) >= cap


def test_full_cc_prompt_supplies_unchanged_schema_for_normal_json_completion():
    from dataclasses import replace
    request = replace(_claude_request(), schema_version=11)
    prompt = json.loads(_prompt(request, tool_workspace=True, git_authoring=True))["instruction"]
    section = prompt.split("HYPOLOOM_OUTPUT_SCHEMA_JSON\n", 1)[1].split(
        "\nEND_HYPOLOOM_OUTPUT_SCHEMA_JSON", 1)[0]
    assert json.loads(section) == _output_schema(request, tool_workspace=True)
    assert "ordinary final response" in prompt
