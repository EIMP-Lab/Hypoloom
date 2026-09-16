"""Clarify the existing QUERY_PLAN envelope while retaining strict rejection."""

import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import _output_schema, _prompt, _provider_response
from test_claude_agent_host import (
    _claude_grounding_query_plan_request,
    _claude_literature_query_plan_request,
)


def _request(scope, version):
    if scope == "grounding":
        request, _ = _claude_grounding_query_plan_request()
        return replace(request, schema_version=version)
    return _claude_literature_query_plan_request(schema_version=version)[0]


@pytest.mark.parametrize("scope,version", [("expert", 8), ("expert", 9), ("expert", 16), ("grounding", 16)])
@pytest.mark.parametrize("retry", [False, True])
def test_query_plan_instruction_locates_proposal_inside_strict_envelope(scope, version, retry):
    request = _request(scope, version)
    rejected = {"query": "causal learning", "typed_candidate": {"query": "causal learning", "type": []}}
    if retry:
        correction = EmbeddedInput.from_bytes("source--agent-retry-diagnostic", "application/json",
            json.dumps({"source_attempt_id": "rejected-query-attempt", "rejected_structured_output": rejected,
                "diagnostic_summary": "root and typed_candidate must NOT have additional properties"}).encode())
        request = replace(request, input_artifacts=(*request.input_artifacts, correction))
    schema = _output_schema(request)
    prompt = json.loads(_prompt(request))
    instruction = prompt["instruction"]
    assert "The mutable proposal is the value of typed_candidate." in instruction
    assert ("The final JSON object contains only typed_candidate." if version >= 11 else
            "The StructuredOutput root contains only typed_candidate.") in instruction
    assert "JSON-schema keywords type, properties and required describe the schema; do not emit them as output fields." in instruction
    if version == 8:
        assert "Put only query inside typed_candidate; omit preferred_venues." in instruction
    else:
        assert "Put query and optional preferred_venues inside typed_candidate; never duplicate them at the root." in instruction
    assert _output_schema(request) == schema
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(schema["required"]) == {"typed_candidate"}
    candidate = schema["properties"]["typed_candidate"]
    assert candidate["additionalProperties"] is False
    assert candidate["required"] == ["query"]
    assert set(candidate["properties"]) == ({"query"} if version == 8 else {"query", "preferred_venues"})
    if retry:
        rendered = next(item for item in prompt["rendered_inputs"] if item["ref"] == correction.ref)
        assert json.loads(rendered["content_utf8"])["rejected_structured_output"] == rejected
        assert "prior non-authoritative failed output" in instruction


def test_injected_query_skill_locates_mutable_fields_and_preserves_legacy_omission():
    prompt = json.loads(_prompt(_request("grounding", 16)))
    skill = next(item["content_utf8"] for item in prompt["rendered_inputs"]
        if item["ref"].endswith("--openalex-query-planning-skill"))
    assert "The mutable proposal is the value of `typed_candidate`." in skill
    assert "The StructuredOutput root contains only `typed_candidate`." in skill
    assert "If the supplied schema excludes `preferred_venues` (ACI8), omit it." in skill
    assert "`type`, `properties`, and `required` are schema keywords, not output fields." in skill


@pytest.mark.parametrize("structured,failure", [
    ({"query": "causal learning", "typed_candidate": {"query": "causal learning", "type": []}}, "invalid_structured_output"),
    ({"query": "causal learning", "typed_candidate": {"query": "causal learning"}}, "invalid_structured_output"),
    ({"typed_candidate": {"query": "causal learning", "type": []}}, "invalid_candidate"),
])
def test_observed_extra_query_and_schema_fields_still_fail_closed(structured, failure):
    request = _request("grounding", 16)
    envelope = {"type": "result", "subtype": "success", "is_error": False,
        "num_turns": 1, "total_cost_usd": 0, "usage": {"input_tokens": 7, "output_tokens": 11},
        "modelUsage": {"stub-model": {}}, "structured_output": structured}
    response, actual_failure, _ = _provider_response(request, "stub-model", json.dumps(envelope).encode())
    assert response is None
    assert actual_failure == failure
