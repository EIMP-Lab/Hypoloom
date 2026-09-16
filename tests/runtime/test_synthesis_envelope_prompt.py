"""SYNTHESIS instructions use the existing versioned candidate envelope."""

from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.claude_agent_host import _output_schema, _prompt
from hypoloom.expert_loop import ResearchNodeSubkind
from tests.runtime.test_claude_agent_host import _claude_pincer_request


@pytest.mark.parametrize("version", [13, 14, 16])
@pytest.mark.parametrize("cycle", [1, 2])
def test_synthesis_instruction_preserves_versioned_envelope_and_each_members_constraints(version, cycle):
    parent = "bound-parent" if cycle == 2 else None
    request = _claude_pincer_request(ResearchNodeSubkind.SYNTHESIS,
        cycle=cycle, parent_hypothesis_id=parent)
    config = EmbeddedInput.from_bytes("fixture--expert-config", "application/json",
        json.dumps({"required_method_family_id": "bound-family",
            "goal": {"evaluation": {"mode": "grounding"}}}).encode())
    inputs = (*request.input_artifacts, config)
    request = replace(request, schema_version=version, input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(request.command, request.task_context, schema_version=version))
    instruction = json.loads(_prompt(request, tool_workspace=True))["instruction"]
    schema = _output_schema(request, tool_workspace=True)
    candidate = schema["properties"]["typed_candidate"]
    assert set(schema["required"]) == {"typed_candidate", "declared_effects"}
    if version >= 14:
        assert "one HypothesisSet/v1 in typed_candidate, with schema_version=1 and a nonempty hypotheses array." in instruction
        assert "Each hypotheses item contains only chain_id and candidate; candidate is one complete falsifiable HypothesisCandidate." in instruction
        assert "Keep this wrapper even for a single hypothesis." in instruction
        assert "Use unique chain_id values and do not exceed the admitted effective_max_forks." in instruction
        assert "The following component, role, method-family and lineage constraints apply to every member candidate." in instruction
        assert "produce one falsifiable HypothesisCandidate." not in instruction
        assert set(candidate["properties"]) == set(candidate["required"]) == {"schema_version", "hypotheses"}
        assert candidate["properties"]["schema_version"] == {"const": 1}
        members = candidate["properties"]["hypotheses"]
        assert "maxItems" not in members
        assert set(members["items"]["properties"]) == set(members["items"]["required"]) == {"chain_id", "candidate"}
        candidate = members["items"]["properties"]["candidate"]
    else:
        assert "produce one falsifiable HypothesisCandidate." in instruction
        assert "HypothesisSet/v1" not in instruction
        assert "hypotheses" not in candidate["properties"]
    assert "copy method_family_id exactly as 'bound-family'" in instruction
    assert candidate["properties"]["method_family_id"] == {"const": "bound-family"}
    assert candidate["properties"]["parent_hypothesis_id"] == {"const": parent}
    if parent is None:
        assert "set both parent_hypothesis_id and relation to null" in instruction
    else:
        assert "copy parent_hypothesis_id exactly as 'bound-parent'" in instruction
        assert "provide a non-empty relation" in instruction
