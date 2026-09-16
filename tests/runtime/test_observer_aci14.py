import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema, validate_postflight
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import AuxiliaryTask, AuxiliaryRequestKind
from hypoloom.fake_agent import default_request
from test_agent_aci14 import _response
from test_expert_interaction import _facts, _observer


def observer_request(actions=("none", "leader", "debate")):
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.OBSERVER, run_id="run-1", cycle=1, ordinal=12)
    inputs = EmbeddedInput.from_bytes("judge--interaction-context", "application/json", json.dumps({
        "facts": _facts(accepted_rows=True), "source_report_ref": "actual-report--response",
        "allowed_actions": list(actions)}).encode())
    return replace(default_request(AgentCommand.OBSERVER), schema_version=14,
        run_id=task.run_id, role="grounding", component_id="grounding", task_context=task,
        command_id=task.command_id, input_artifacts=(inputs,), immutable_context_refs=(inputs.ref,),
        required_output_schema=required_output_schema(AgentCommand.OBSERVER, task, schema_version=14))


@pytest.mark.parametrize("action", ["none", "leader", "debate"])
def test_aci14_observer_cites_actual_facts_instead_of_input_aliases(action):
    request = observer_request()
    candidate = _observer(action)
    parsed = validate_postflight(request, _response(request, candidate))
    assert parsed.candidate.to_dict() == candidate
    assert request.required_output_schema == "ObserverProposal/v1"


@pytest.mark.parametrize("failure", ["disabled", "foreign_ref", "unqualified", "missing_frozen_context"])
def test_observer_postflight_rejects_unavailable_actions_and_unaccepted_science(failure):
    request = observer_request(("none",) if failure == "disabled" else ("none", "leader"))
    candidate = _observer("leader")
    if failure == "foreign_ref": candidate["supporting_refs"] = ["not-a-fact"]
    elif failure == "unqualified": candidate["leader"] = "not-an-expert"
    elif failure == "missing_frozen_context": request = replace(request, input_artifacts=(), immutable_context_refs=())
    with pytest.raises(ValueError): validate_postflight(request, _response(request, candidate))


def test_retained_observer_still_accepts_legacy_summary_candidate():
    from hypoloom.fake_agent import FakeAgent
    request = default_request(AgentCommand.OBSERVER)
    assert request.schema_version == 13
    assert validate_postflight(request, FakeAgent().invoke(request)).candidate.task_kind == "OBSERVER"


def test_observer_cannot_select_a_component_before_its_minimum_completed_rounds():
    request = observer_request(("none", "leader"))
    context = json.loads(request.input_artifacts[0].content_bytes())
    context["eligible_leader_components"] = []
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(request.input_artifacts[0].ref,
        "application/json", json.dumps(context).encode()),))
    with pytest.raises(ValueError, match="eligible"):
        validate_postflight(request, _response(request, _observer("leader")))


def test_observer_cannot_debate_an_invented_chain_without_an_actual_fork_source():
    request = observer_request(("none", "debate")); candidate = _observer("debate")
    context = json.loads(request.input_artifacts[0].content_bytes())
    context["debate_participants"] = candidate["debate"]["participants"]
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(request.input_artifacts[0].ref,
        "application/json", json.dumps(context).encode()),))
    assert validate_postflight(request, _response(request, candidate)).candidate.debate is not None
    candidate["debate"]["participants"][0]["scope"]["chain"] = "invented-chain"
    with pytest.raises(ValueError, match="fork"):
        validate_postflight(request, _response(request, candidate))
