from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError, request_digest, required_output_schema, validate_postflight, with_output_digest
from hypoloom.agent_sessions import AgentSessions
from tests.runtime.test_grounding_aci import grounding_exchange


QUESTION = {"question": "Which evaluation split is intended?", "rationale": "The project has two incompatible protocols.",
            "options": ["Official split", "Project cross-validation"]}


def exchange(*, ordinal=1, question=True):
    request, response = grounding_exchange(14)
    task = replace(request.task_context, ordinal=ordinal)
    request = replace(request, schema_version=15, task_context=task, command_id=task.command_id,
                      required_output_schema="GroundingCandidate/v2")
    raw = {**response.typed_candidate, "clarification": QUESTION if question else None}
    if question:
        raw["background"] = None
    response = with_output_digest(replace(response, schema_version=15, command_id=task.command_id,
                                          input_digest=request_digest(request), typed_candidate=raw))
    return request, response


@pytest.mark.parametrize("question", [True, False])
def test_aci15_grounding_question_or_final_background(question):
    request, response = exchange(question=question)
    candidate = validate_postflight(request, response).candidate
    assert (candidate.background is None) is question
    assert candidate.clarification == (QUESTION if question else None)
    assert required_output_schema(request.command, request.task_context, schema_version=15) == "GroundingCandidate/v2"


@pytest.mark.parametrize("change", [
    {"clarification": None},
    {"clarification": {**QUESTION, "question": " "}},
    {"clarification": {**QUESTION, "rationale": ""}},
    {"clarification": {**QUESTION, "options": ["a", "a"]}},
    {"clarification": {**QUESTION, "approved": True}},
])
def test_aci15_rejects_missing_or_malformed_clarification(change):
    request, response = exchange()
    response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, **change}))
    with pytest.raises(AgentACIError):
        validate_postflight(request, response)


def test_question_must_not_also_claim_completed_background():
    request, response = exchange(question=False)
    response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "clarification": QUESTION}))
    with pytest.raises(AgentACIError):
        validate_postflight(request, response)


def test_grounding_rounds_have_distinct_commands_but_one_session():
    first, _ = exchange(ordinal=1)
    second, _ = exchange(ordinal=2)
    assert first.command_id != second.command_id
    assert AgentSessions.binding(first) == AgentSessions.binding(second)
    assert AgentSessions.binding(first)["scope"] == "grounding-preparation"


def test_retained_aci14_grounding_keeps_background_only_schema():
    request, response = grounding_exchange(14)
    validate_postflight(request, response)
    response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "clarification": None}))
    with pytest.raises(AgentACIError):
        validate_postflight(request, response)
