from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError, request_digest, required_output_schema, validate_postflight, with_output_digest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request


def background():
    return {"project_summary": "A repository with an executable CPU benchmark.",
            "codebase_map": [{"path": "src/main.py", "purpose": "benchmark entrypoint"}],
            "entrypoints": ["python src/main.py"], "how_to_run_experiment": "Run the benchmark and inspect timings.csv.",
            "metrics_available": ["latency"], "risks": ["Hardware affects timings."], "open_questions": []}


def grounding_exchange(version=13):
    old = replace(default_request(AgentCommand.GROUNDING), schema_version=12, required_output_schema="ExpertLoopCandidate/v1")
    original = FakeAgent().invoke(old)
    request = replace(old, schema_version=version, required_output_schema="GroundingCandidate/v1" if version >= 13 else "ExpertLoopCandidate/v1")
    response = with_output_digest(replace(original, schema_version=version,
        input_digest=request_digest(request),
        typed_candidate={"task_kind": "GROUNDING", "supporting_refs": [],
                         "background": background(), "web_sources": ["https://example.org/benchmark"]}))
    return request, response


def test_new_grounding_candidate_has_seven_fields_and_derived_summary():
    request, response = grounding_exchange()
    validated = validate_postflight(request, response)
    assert validated.candidate.background.to_dict() == background()
    assert validated.candidate.summary == background()["project_summary"]
    assert validated.candidate.web_sources == ("https://example.org/benchmark",)
    assert required_output_schema(request.command, request.task_context, schema_version=13) == "GroundingCandidate/v1"


@pytest.mark.parametrize("version", [11, 12])
def test_retained_grounding_keeps_old_output_contract(version):
    request = replace(default_request(AgentCommand.GROUNDING), schema_version=version, required_output_schema="ExpertLoopCandidate/v1")
    validate_postflight(request, FakeAgent().invoke(request))
    _, new = grounding_exchange(version)
    with pytest.raises(AgentACIError): validate_postflight(request, new)


@pytest.mark.parametrize("change", [{"summary": "duplicate background"}, {"web_sources": ["http://example.org/plain"]},
                                    {"background": {"project_summary": "not all fields"}}])
def test_grounding_candidate_does_not_accept_partial_or_legacy_shapes(change):
    request, response = grounding_exchange()
    response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate, **change}))
    with pytest.raises(AgentACIError): validate_postflight(request, response)


def test_retained_aci12_still_supports_cycle_report():
    from hypoloom.agent_aci import AgentRequest
    request = default_request(AgentCommand.CYCLE_REPORT)
    assert AgentRequest.from_dict({**request.to_dict(), "schema_version": 12}).command is AgentCommand.CYCLE_REPORT
