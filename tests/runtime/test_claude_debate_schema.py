"""Native debate statements and judging retain the actual six-output domain."""

import json
import re
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput, debate_reference_ids
from hypoloom.claude_agent_host import AgentHostError, _output_schema
from hypoloom.domain import AgentCommand
from hypoloom.expert_interaction import InteractionFacts
from hypoloom.fake_agent import default_request
from test_debate_aci14 import debate_request, judge_request


def test_native_debate_statement_has_exact_claim_and_evidence_shape():
    request, _ = debate_request()
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"position", "arguments", "evidence", "concessions"}
    assert set(schema["properties"]) == set(schema["required"]) | {"web_sources"}
    props = schema["properties"]
    assert props["position"] == {"type": "string", "minLength": 1}
    assert props["arguments"]["items"] == {"type": "string", "minLength": 1}
    assert "Nonempty" in props["arguments"]["description"]
    assert set(props["evidence"]["items"]["enum"]) == debate_reference_ids(request)
    context = json.loads(next(item.content_bytes() for item in request.input_artifacts
        if item.ref.endswith("--interaction-context")))
    assert InteractionFacts.from_rows(request.run_id, context["facts"]).reference_ids <= set(
        props["evidence"]["items"]["enum"])
    assert set(request.immutable_context_refs) - debate_reference_ids(request)
    assert props["web_sources"]["items"]["pattern"] == "^https://"


def test_retained_debate_statement_native_schema_does_not_gain_new_shape():
    schema = _output_schema(default_request(AgentCommand.DEBATE))["properties"]["typed_candidate"]
    assert set(schema["required"]) == {"task_kind", "summary", "supporting_refs"}


def test_native_judge_binds_roles_scores_and_actual_scientific_and_round_refs():
    request, _ = judge_request()
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert set(schema["properties"]) == set(schema["required"]) == {
        "winner", "scores", "keep", "prune", "rationale", "supporting_refs"}
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    assert props["winner"] == {"type": ["string", "null"], "enum": [None, "critic", "researcher"]}
    for field in ("keep", "prune"):
        assert props[field]["uniqueItems"] is True
        assert set(props[field]["items"]["enum"]) == {"critic", "researcher"}
    context = json.loads(request.input_artifacts[0].content_bytes())
    facts = InteractionFacts.from_rows(request.run_id, context["facts"])
    round_ids = {row["attempt_id"] for row in context["round_attempts"]}
    assert set(props["supporting_refs"]["items"]["enum"]) == facts.reference_ids | round_ids
    assert "six" in props["supporting_refs"]["description"]
    score = props["scores"]["items"]
    assert score["additionalProperties"] is False
    assert set(score["required"]) == {"role", "score", "round_refs"}
    domains = {item["properties"]["role"]["const"]: set(item["properties"]["round_refs"]["items"]["enum"])
               for item in score["oneOf"]}
    assert domains == {role: {f"round-{number}-{role}" for number in range(1, 4)} for role in ("critic", "researcher")}
    pattern = score["properties"]["score"]["pattern"]
    assert all(re.fullmatch(pattern, value) for value in ("0", "-1.25", "+.5", "1e-2"))
    assert all(re.fullmatch(pattern, value) is None for value in ("NaN", "Infinity", "-Inf", "narrative", ""))


@pytest.mark.parametrize("fault", ["missing", "duplicate", "missing-round", "wrong-role-round"])
def test_native_judge_schema_rejects_incomplete_or_wrong_participant_context(fault):
    request, _ = judge_request()
    item = request.input_artifacts[0]
    if fault == "missing":
        request = replace(request, input_artifacts=())
    elif fault == "duplicate":
        request = replace(request, input_artifacts=(item, EmbeddedInput.from_bytes(
            "other--debate-judge-context", item.media_type, item.content_bytes())))
    else:
        context = json.loads(item.content_bytes())
        if fault == "missing-round":
            context["round_attempts"].pop()
        else:
            row = context["round_attempts"][0]
            observed = json.loads(row["observation_json"])
            observed["request"]["role"] = "foreign"
            row["observation_json"] = json.dumps(observed)
        request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(item.ref,
            item.media_type, json.dumps(context).encode()),))
    with pytest.raises(AgentHostError):
        _output_schema(request)
