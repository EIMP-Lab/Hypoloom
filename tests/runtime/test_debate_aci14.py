import json
from dataclasses import asdict, replace

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema, validate_postflight
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask
from hypoloom.fake_agent import default_request
from test_agent_aci14 import _response
from test_expert_interaction import _debate, _judgment


def debate_request():
    facts, binding, originals, rows = _debate()
    observed = json.loads(rows[0]["observation_json"]); raw = observed["request"]
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE, run_id=raw["run_id"],
        component_id=raw["component_id"], expert_slot=raw["role"], cycle=1, ordinal=31)
    from hypoloom.domain import _thaw_json
    original = next(row for row in originals if row["attempt_id"] in raw["immutable_context_refs"])
    inputs = (*tuple(EmbeddedInput.from_dict(item) for item in raw["input_artifacts"]),
        EmbeddedInput.from_bytes(original["attempt_id"], "application/json", json.dumps(original).encode()),
        EmbeddedInput.from_bytes("debate--interaction-context", "application/json", json.dumps({"facts": _thaw_json(facts.rows)}).encode()))
    request = replace(default_request(AgentCommand.DEBATE), schema_version=14, run_id=task.run_id,
        role=task.expert_slot, component_id=task.component_id, task_context=task, command_id=task.command_id,
        input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(AgentCommand.DEBATE, task, schema_version=14))
    return request, observed["response"]["typed_candidate"]


def judge_request():
    facts, binding, originals, rows = _debate()
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE_JUDGE, run_id="run-1", ordinal=10)
    from hypoloom.domain import _thaw_json
    context = EmbeddedInput.from_bytes("judge--debate-judge-context", "application/json", json.dumps({
        "debate_id": binding.debate_id, "participants": [asdict(item) for item in binding.participants],
        "facts": _thaw_json(facts.rows), "round_attempts": rows}).encode())
    request = replace(default_request(AgentCommand.DEBATE_JUDGE), run_id="run-1", role="grounding", component_id="grounding",
        task_context=task, command_id=task.command_id, input_artifacts=(context,),
        immutable_context_refs=(context.ref, *(row["attempt_id"] for row in rows)),
        required_output_schema=required_output_schema(AgentCommand.DEBATE_JUDGE, task, schema_version=14))
    return request, _judgment(rows)


def test_debate_statement_is_typed_and_cites_only_the_frozen_input_domain():
    request, candidate = debate_request()
    result = validate_postflight(request, _response(request, candidate))
    assert result.candidate.position == candidate["position"]
    assert request.required_output_schema == "DebateStatement/v1"
    with pytest.raises(ValueError): validate_postflight(request, _response(request, {**candidate, "evidence": ["invented"]}))


def test_debate_judge_requires_six_actual_outputs_and_retains_control_only_pruning():
    request, candidate = judge_request()
    result = validate_postflight(request, _response(request, candidate))
    assert request.required_output_schema == "DebateJudgment/v1"
    assert result.candidate.winner == "critic"
    assert result.candidate.pruned_by[0]["debate_id"] == "debate-1"
    with pytest.raises(ValueError): validate_postflight(request, _response(request, {**candidate, "supporting_refs": [ref for ref in candidate["supporting_refs"] if ref != "round-1-critic"]}))


def test_selected_method_excerpt_reaches_judge_through_existing_statement():
    from hypoloom.agent_aci import debate_judge_inputs
    request, candidate = judge_request()
    context = json.loads(request.input_artifacts[0].content_bytes())
    excerpt = 'Source acquisition-1:paper-2, physical page 7: "uses only the causal prefix". Interpretation: future context is excluded; the equation remains unverified.'
    context["round_attempts"][0]["observation_json"] = json.dumps({
        **(observed := json.loads(context["round_attempts"][0]["observation_json"])),
        "response": {**observed["response"], "typed_candidate": {**observed["response"]["typed_candidate"], "position": excerpt}}})
    item = EmbeddedInput.from_bytes(request.input_artifacts[0].ref, "application/json", json.dumps(context).encode())
    request = replace(request, input_artifacts=(item,))
    _, _, rounds = debate_judge_inputs(request)
    selected = next(item for item in rounds if item.source_attempt_id == context["round_attempts"][0]["attempt_id"])
    assert selected.statement.position == excerpt
    assert validate_postflight(request, _response(request, candidate)).candidate.winner == "critic"


@pytest.mark.parametrize("tamper", [None, "missing", "duplicate", "participant", "session", "evidence", "ambiguous"])
def test_projected_judge_preserves_the_existing_six_statement_contract(tamper):
    from hypoloom.agent_aci import debate_judge_inputs
    request, candidate = judge_request()
    _, _, rounds = debate_judge_inputs(request)
    context = json.loads(request.input_artifacts[0].content_bytes())
    original = context.pop("round_attempts")
    context["rounds"] = [item.to_dict() for item in rounds]
    if tamper == "missing": context["rounds"].pop()
    elif tamper == "duplicate": context["rounds"][-1] = context["rounds"][0]
    elif tamper == "participant": context["rounds"][0]["participant"]["hypothesis_id"] = "invented"
    elif tamper == "session": context["rounds"][0]["session_id"] = "wrong-session"
    elif tamper == "evidence": context["rounds"][0]["statement"]["evidence"] = ["invented"]
    elif tamper == "ambiguous": context["round_attempts"] = original
    item = EmbeddedInput.from_bytes(request.input_artifacts[0].ref, "application/json", json.dumps(context).encode())
    request = replace(request, input_artifacts=(item,))
    if tamper:
        with pytest.raises(ValueError): validate_postflight(request, _response(request, candidate))
    else:
        assert validate_postflight(request, _response(request, candidate)).candidate.winner == "critic"
