"""Native Observer schema projects its admitted scientific reference domain."""

import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import AgentHostError, _output_schema
from hypoloom.domain import AgentCommand
from hypoloom.expert_interaction import InteractionFacts
from hypoloom.fake_agent import default_request
from test_observer_aci14 import observer_request


@pytest.mark.parametrize("actions", [("none",), ("none", "leader", "debate")])
def test_native_observer_exact_candidate_and_actual_scientific_refs(actions):
    request = observer_request(actions)
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert set(schema["properties"]) == set(schema["required"]) == {
        "action", "rationale", "supporting_refs", "leader", "debate"}
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    assert props["action"] == {"enum": list(actions)}
    context = json.loads(request.input_artifacts[0].content_bytes())
    refs = InteractionFacts.from_rows(request.run_id, context["facts"]).reference_ids
    assert set(props["supporting_refs"]["items"]["enum"]) == refs
    assert "out-2" in refs and not set(request.immutable_context_refs) & refs
    assert props["leader"]["type"] == ["string", "null"]
    assert props["debate"]["type"] == ["object", "null"]
    debate = props["debate"]
    assert set(debate["required"]) == {"participants", "topic"}
    participant = debate["properties"]["participants"]["items"]
    assert set(participant["required"]) == {"scope", "hypothesis_id"}
    assert set(participant["properties"]["scope"]["required"]) == {"role", "component_id", "chain"}
    assert participant["properties"]["scope"]["additionalProperties"] is False
    assert participant["additionalProperties"] is False


@pytest.mark.parametrize("fault", ["missing", "duplicate", "invalid-actions", "foreign-facts"])
def test_native_observer_schema_rejects_unusable_bound_context(fault):
    request = observer_request()
    item = request.input_artifacts[0]
    if fault == "missing":
        request = replace(request, input_artifacts=())
    elif fault == "duplicate":
        request = replace(request, input_artifacts=(item, EmbeddedInput.from_bytes(
            "other--interaction-context", item.media_type, item.content_bytes())))
    else:
        context = json.loads(item.content_bytes())
        if fault == "invalid-actions":
            context["allowed_actions"] = ["graph_evolve"]
        else:
            context["facts"][0]["run_id"] = "foreign-run"
        request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(
            item.ref, item.media_type, json.dumps(context).encode()),))
    with pytest.raises(AgentHostError):
        _output_schema(request)


def test_retained_observer_native_schema_remains_summary_and_input_refs():
    request = default_request(AgentCommand.OBSERVER)
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert set(schema["required"]) == {"task_kind", "summary", "supporting_refs"}
