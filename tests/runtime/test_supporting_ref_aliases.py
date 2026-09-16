"""Short display references never change durable scientific identity."""
from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import AgentACIError, AgentRequest
from hypoloom.claude_agent_host import CLAUDE_AGENT_HOST_VERSION, _output_schema, _prompt, _provider_response
from hypoloom.fake_agent import FakeAgent
from hypoloom.native_output import decode_output
from hypoloom.reference_aliases import canonical_output
from test_final_report import report_request


def test_old_request_keeps_full_refs_and_rejects_unbounded_cost():
    request = report_request()
    assert "ref_aliases" not in json.loads(_prompt(request))
    with pytest.raises(AgentACIError, match="ACI v19"):
        replace(request, budget=replace(request.budget, max_cost=None))


def test_native_alias_roundtrip_restores_ids_before_agent_response():
    request = replace(report_request(), schema_version=19)
    assert AgentRequest.from_dict(request.to_dict()) == request
    prompt = json.loads(_prompt(request, tool_workspace=True))
    aliases = prompt["ref_aliases"]
    assert list(aliases) == [f"R{i}" for i in range(1, len(aliases) + 1)]
    assert list(aliases.values()) == list(request.immutable_context_refs)
    candidate = FakeAgent().invoke(request).to_dict()["typed_candidate"]
    canonical_refs = candidate["supporting_refs"]
    inverse = {ref: alias for alias, ref in aliases.items()}
    candidate["supporting_refs"] = [inverse[ref] for ref in canonical_refs]
    result = {"typed_candidate": candidate, "declared_effects": [{"effect": request.effect_requirements[0]}]}
    before = json.dumps(result)
    decode_output(before, _output_schema(request, tool_workspace=True))
    restored = canonical_output(result, aliases)
    assert restored["typed_candidate"]["supporting_refs"] == canonical_refs
    assert json.dumps(result) == before
    outer = {"type": "result", "subtype": "success", "is_error": False, "result": before,
        "num_turns": 1, "total_cost_usd": 0, "usage": {"input_tokens": 1, "output_tokens": 1},
        "modelUsage": {request.required_model_identity: {"inputTokens": 1, "outputTokens": 1}}}
    response, failure, _ = _provider_response(replace(request, required_adapter_version=CLAUDE_AGENT_HOST_VERSION), request.required_model_identity,
        json.dumps(outer).encode(), final_text_output=True,
        workspace_artifacts=FakeAgent().invoke(request).proposed_workspace_artifacts)
    assert failure is None
    assert response.typed_candidate["supporting_refs"] == canonical_refs


def test_unknown_alias_and_canonical_id_are_not_silently_repaired():
    request = replace(report_request(), schema_version=19)
    prompt = json.loads(_prompt(request))
    for invalid in ("R99999", next(iter(prompt["ref_aliases"].values()))):
        with pytest.raises(ValueError, match="exact reference aliases"):
            canonical_output({"typed_candidate": {"supporting_refs": [invalid]}}, prompt["ref_aliases"])


def test_native_enum_hint_names_allowed_refs_without_echoing_private_candidate():
    schema = {"type": "object", "properties": {"typed_candidate": {"type": "object",
        "properties": {"supporting_refs": {"type": "array", "items": {"enum": ["R1", "R2"]}}}}}}
    with pytest.raises(ValueError) as caught:
        decode_output('{"typed_candidate":{"supporting_refs":["private-invalid-value"]}}', schema)
    assert "/typed_candidate/supporting_refs/0" in str(caught.value)
    assert '"R1", "R2"' in caught.value.correction_hint
    assert "private-invalid-value" not in str(caught.value) + caught.value.correction_hint
