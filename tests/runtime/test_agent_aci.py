from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import (
    ACI_SCHEMA_VERSION,
    EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AGENT_COMMANDS,
    AgentACIError,
    AgentBudget,
    AgentCommand,
    AgentRequest,
    AgentResponse,
    EmbeddedInput,
    ExpertLoopCandidate,
    HypothesisCandidate,
    canonical_json,
    hypothesis_update_current_assessment,
    request_digest,
    required_output_schema,
    validate_postflight,
    with_output_digest,
)
from hypoloom.domain import HypothesisAssessment, ResourcePreference
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.literature import (
    LITERATURE_SCHEMA_VERSION,
    LITERATURE_STRATA,
    LiteratureFilters,
    LiteratureQuery,
    PreferredVenue,
    ScholarlySourceClass,
)
from hypoloom.fake_agent import (
    FAKE_ADAPTER_VERSION,
    FAKE_MODEL_IDENTITY,
    FakeAgent,
    FakeAgentMode,
    default_request,
)


def _pincer_request(
    subkind: ResearchNodeSubkind,
    *,
    cycle: int = 1,
    parent_hypothesis_id: str | None = None,
) -> AgentRequest:
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    assert isinstance(request.task_context, ResearchNodeTask)
    task = ResearchNodeTask(
        key=replace(request.task_context.key, subkind=subkind, cycle=cycle)
    )
    inputs = request.input_artifacts
    if parent_hypothesis_id is not None:
        lineage = EmbeddedInput.from_bytes(
            f"{request.run_id}--{request.component_id}--lineage-context",
            "application/json",
            canonical_json(
                {
                    "cycle": cycle,
                    "parent_hypothesis_id": parent_hypothesis_id,
                }
            ),
        )
        inputs += (lineage,)
    return replace(
        request,
        task_context=task,
        command_id=task.command_id,
        input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(request.command, task),
    )


def _query_plan_request(
    *, schema_version: int = EXPERT_LOOP_ACI_SCHEMA_VERSION,
) -> AgentRequest:
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    assert isinstance(request.task_context, ResearchNodeTask)
    task = ResearchNodeTask(
        key=replace(
            request.task_context.key,
            subkind=ResearchNodeSubkind.QUERY_PLAN,
        )
    )
    template = EmbeddedInput.from_bytes(
        f"{task.command_id}--literature-query-template",
        "application/json",
        canonical_json(_literature_query(
            request.immutable_context_refs,
            schema_version=1 if schema_version == 8 else 2,
        ).to_dict()),
    )
    inputs = (*request.input_artifacts, template)
    return replace(
        request,
        schema_version=schema_version,
        task_context=task,
        command_id=task.command_id,
        input_artifacts=inputs,
        immutable_context_refs=(
            *request.immutable_context_refs,
            *(item.ref for item in inputs),
        ),
        required_output_schema=required_output_schema(
            request.command, task, schema_version=schema_version,
        ),
    )


def _literature_query(
    refs: tuple[str, ...],
    *,
    schema_version: int = LITERATURE_SCHEMA_VERSION,
) -> LiteratureQuery:
    return LiteratureQuery(
        schema_version=schema_version,
        query='"multimodal affect" AND (causal OR robust)',
        filters=LiteratureFilters(
            year_from=1997,
            year_to=2026,
            languages=("en",),
        ),
        max_items=6,
        allowed_source_classes=(ScholarlySourceClass.JOURNAL,),
        strata=LITERATURE_STRATA,
        latest_as_of="2026-08-29",
        immutable_context_refs=refs,
        preferred_venues=(
            (PreferredVenue(
                name="Transactions on Affective Computing",
                source_class=ScholarlySourceClass.JOURNAL,
            ),)
            if schema_version == 2 else ()
        ),
    )


def _literature_main_request() -> AgentRequest:
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    packet = EmbeddedInput.from_bytes(
        f"{request.command_id}--literature-packet", "application/json", b"{}",
    )
    query = EmbeddedInput.from_bytes(
        f"{request.command_id}--literature-query", "application/json", b"{}",
    )
    return replace(request, input_artifacts=(query, packet),
        immutable_context_refs=(query.ref, packet.ref))


def _hypothesis_fact(
    hypothesis_id: str,
    *,
    parent_hypothesis_id: str | None,
    method_family_id: str,
) -> EmbeddedInput:
    fact_id = f"fact-{hypothesis_id}"
    payload = {
        "fact_type": "HypothesisProposed",
        "fact_id": fact_id,
        "run_id": "run-fake",
        "schema_version": 5,
        "hypothesis_id": hypothesis_id,
        "goal_id": "goal-fake",
        "component_id": "primary.score",
        "proposer_role": "scientist",
        "method_family_id": method_family_id,
        "claim": f"claim for {hypothesis_id}",
        "assumptions": [],
        "parent_hypothesis_id": parent_hypothesis_id,
        "relation": None if parent_hypothesis_id is None else "revises",
    }
    return EmbeddedInput.from_bytes(
        fact_id,
        "application/json",
        canonical_json(
            {
                "fact_id": fact_id,
                "fact_type": "HypothesisProposed",
                "payload": payload,
            }
        ),
    )


def _assessment_fact(
    hypothesis_id: str,
    *,
    method_family_id: str,
    assessment: HypothesisAssessment,
    ordinal: int | None = None,
) -> EmbeddedInput:
    suffix = "" if ordinal is None else f"-{ordinal}"
    fact_id = f"fact-assessment-{hypothesis_id}{suffix}"
    payload = {
        "fact_type": "HypothesisAssessed",
        "fact_id": fact_id,
        "run_id": "run-fake",
        "schema_version": 5,
        "assessment_id": f"assessment-{hypothesis_id}{suffix}",
        "hypothesis_id": hypothesis_id,
        "component_id": "primary.score",
        "method_family_id": method_family_id,
        "evidence_ids": [f"evidence-{hypothesis_id}{suffix}"],
        "assessment": assessment.value,
    }
    return EmbeddedInput.from_bytes(
        fact_id,
        "application/json",
        canonical_json(
            {
                "fact_id": fact_id,
                "fact_type": "HypothesisAssessed",
                "payload": payload,
            }
        ),
    )


def _update_request(
    *inputs: EmbeddedInput,
    cycle: int,
    method_family_id: str,
) -> AgentRequest:
    request = default_request(AgentCommand.HYPOTHESIS_UPDATE)
    assert isinstance(request.task_context, ResearchNodeTask)
    task = ResearchNodeTask(key=replace(request.task_context.key, cycle=cycle))
    return replace(
        request,
        task_context=task,
        command_id=task.command_id,
        method_family_id=method_family_id,
        input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
    )


def _update_response(
    request: AgentRequest,
    disposition: str,
) -> AgentResponse:
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    assert response.typed_candidate is not None
    candidate = dict(response.typed_candidate)
    candidate["update_disposition"] = disposition
    return with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )


def test_supported_current_hypothesis_tip_requires_lineage_termination() -> None:
    request = _update_request(
        _hypothesis_fact(
            "hypothesis-current",
            parent_hypothesis_id=None,
            method_family_id="family-current",
        ),
        _assessment_fact(
            "hypothesis-current",
            method_family_id="family-current",
            assessment=HypothesisAssessment.SUPPORTED,
        ),
        cycle=1,
        method_family_id="family-current",
    )

    assert hypothesis_update_current_assessment(request) is HypothesisAssessment.SUPPORTED
    with pytest.raises(AgentACIError) as error:
        validate_postflight(
            request,
            _update_response(request, "REVISE_NEXT_CYCLE"),
        )

    assert error.value.code == "invalid_candidate"
    assert error.value.message == (
        "a SUPPORTED current component tip must TERMINATE_LINEAGE"
    )


def test_latest_same_tip_assessment_controls_hypothesis_update() -> None:
    request = _update_request(
        _hypothesis_fact(
            "hypothesis-current",
            parent_hypothesis_id=None,
            method_family_id="family-current",
        ),
        _assessment_fact(
            "hypothesis-current",
            method_family_id="family-current",
            assessment=HypothesisAssessment.INCONCLUSIVE,
            ordinal=1,
        ),
        _assessment_fact(
            "hypothesis-current",
            method_family_id="family-current",
            assessment=HypothesisAssessment.SUPPORTED,
            ordinal=2,
        ),
        cycle=1,
        method_family_id="family-current",
    )

    assert hypothesis_update_current_assessment(request) is HypothesisAssessment.SUPPORTED
    with pytest.raises(AgentACIError, match="SUPPORTED current component tip"):
        validate_postflight(
            request,
            _update_response(request, "REVISE_NEXT_CYCLE"),
        )


def test_hypothesis_update_uses_exact_current_tip_not_historical_support() -> None:
    request = _update_request(
        _hypothesis_fact(
            "hypothesis-prior",
            parent_hypothesis_id=None,
            method_family_id="family-prior",
        ),
        _assessment_fact(
            "hypothesis-prior",
            method_family_id="family-prior",
            assessment=HypothesisAssessment.SUPPORTED,
        ),
        _hypothesis_fact(
            "hypothesis-current",
            parent_hypothesis_id="hypothesis-prior",
            method_family_id="family-current",
        ),
        _assessment_fact(
            "hypothesis-current",
            method_family_id="family-current",
            assessment=HypothesisAssessment.REFUTED,
        ),
        cycle=2,
        method_family_id="family-current",
    )

    assert hypothesis_update_current_assessment(request) is HypothesisAssessment.REFUTED
    validated = validate_postflight(
        request,
        _update_response(request, "REVISE_NEXT_CYCLE"),
    )

    assert isinstance(validated.candidate, ExpertLoopCandidate)
    assert validated.candidate.update_disposition is not None
    assert validated.candidate.update_disposition.value == "REVISE_NEXT_CYCLE"


def test_wire_has_exactly_the_accepted_agent_commands() -> None:
    assert ACI_SCHEMA_VERSION == 6
    assert EXPERT_LOOP_ACI_SCHEMA_VERSION == 13
    assert tuple(command.value for command in AgentCommand) == (
        "PROPOSE_HYPOTHESIS",
        "DRAFT_EXPERIMENT_SPEC",
        "ANSWER_RENDEZVOUS",
        "GROUNDING",
        "LITERATURE_REVIEW",
        "HYPOTHESIS_PROPOSAL",
        "EXPERIMENT_DESIGN_IMPLEMENT",
        "EXPERIMENT_EXECUTION",
        "RESULT_ANALYSIS",
        "HYPOTHESIS_UPDATE",
        "OBSERVER",
        "DEBATE",
        "LEADER",
        "COMPACT_CONTEXT",
        "CYCLE_REPORT",
        "ADJUDICATE",
        "SELF_EVOLVE",
        "GRAPH_EVOLVE",
        "FINAL_REPORT",
        "DEBATE_JUDGE",
        "PAPER_READ",
    )
    assert AGENT_COMMANDS == tuple(AgentCommand)


def test_legacy_v5_request_digest_and_postflight_remain_read_compatible() -> None:
    current = default_request()
    raw_request = current.to_dict()
    raw_request["schema_version"] = 5
    raw_request.pop("task_context", None)
    del raw_request["budget"]["token_enforcement_mode"]
    legacy_bytes = canonical_json(raw_request)

    request = AgentRequest.from_json(legacy_bytes)

    assert request.budget.token_enforcement_mode == "enforce"
    assert request.to_dict() == raw_request
    assert request_digest(request) == hashlib.sha256(legacy_bytes).hexdigest()
    current_response = FakeAgent().invoke(current)
    assert isinstance(current_response, AgentResponse)
    legacy_response = with_output_digest(
        replace(
            current_response,
            schema_version=5,
            input_digest=request_digest(request),
            output_digest="",
        )
    )
    assert AgentResponse.from_json(legacy_response.to_json()) == legacy_response
    assert validate_postflight(request, legacy_response).response == legacy_response


def test_v5_rejects_explicit_mode_and_v6_requires_it_on_the_wire() -> None:
    current = default_request().to_dict()
    current.pop("task_context", None)
    legacy = {**current, "schema_version": 5}
    with pytest.raises(AgentACIError) as error:
        AgentRequest.from_dict(legacy)
    assert error.value.code == "invalid_schema"

    del current["budget"]["token_enforcement_mode"]
    with pytest.raises(AgentACIError) as error:
        AgentRequest.from_dict(current)
    assert error.value.code == "invalid_schema"


def test_v6_round_trip_stays_exact_and_rejects_expert_loop_commands() -> None:
    raw = default_request().to_dict()
    raw["schema_version"] = 6
    raw.pop("task_context", None)
    request = AgentRequest.from_dict(raw)
    assert request.to_dict() == raw
    expert = default_request(AgentCommand.LITERATURE_REVIEW).to_dict()
    expert["schema_version"] = 6
    del expert["task_context"]
    with pytest.raises(AgentACIError) as error:
        AgentRequest.from_dict(expert)
    assert error.value.code == "unknown_command"


def test_v10_task_context_binds_stable_command_component_and_expert() -> None:
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    assert request.task_context is not None
    assert request.command_id == request.task_context.command_id
    assert AgentRequest.from_json(request.to_json()) == request
    raw = request.to_dict()
    raw["command_id"] = "completion-order-derived"
    with pytest.raises(AgentACIError) as error:
        AgentRequest.from_dict(raw)
    assert error.value.code == "invalid_task_context"


def test_v7_remains_readable_but_cannot_claim_query_plan_support() -> None:
    current = default_request(AgentCommand.LITERATURE_REVIEW)
    assert isinstance(current.task_context, ResearchNodeTask)
    main = ResearchNodeTask(
        key=replace(current.task_context.key, subkind=ResearchNodeSubkind.MAIN)
    )
    legacy = replace(
        current,
        schema_version=7,
        task_context=main,
        command_id=main.command_id,
        required_output_schema=required_output_schema(current.command, main),
    )
    assert AgentRequest.from_json(legacy.to_json()) == legacy

    with pytest.raises(AgentACIError) as error:
        replace(_query_plan_request(), schema_version=7)
    assert error.value.code == "invalid_schema"


def test_aci10_query_plan_uses_literature_query_v2_and_reuses_its_parser() -> None:
    request = _query_plan_request()
    assert request.required_output_schema == "LiteratureQuery/v2"
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    query = _literature_query(request.immutable_context_refs)
    response = with_output_digest(
        replace(
            response,
            typed_candidate=query.to_dict(),
            output_digest="",
        )
    )

    validated = validate_postflight(request, response)

    assert validated.candidate == query
    assert isinstance(validated.candidate, LiteratureQuery)
    assert validated.candidate.preferred_venues == query.preferred_venues

    malformed = dict(query.to_dict())
    malformed["parallel_authority"] = True
    response = with_output_digest(
        replace(response, typed_candidate=malformed, output_digest="")
    )
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, response)
    assert error.value.code == "invalid_candidate"


def test_aci10_role_neutral_grounding_query_plan_reuses_literature_query_v2() -> None:
    base = _query_plan_request()
    task = AuxiliaryTask(
        kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN,
        run_id=base.run_id,
    )
    template = EmbeddedInput.from_bytes(
        f"{task.command_id}--literature-query-template",
        "application/json",
        canonical_json(_literature_query(base.immutable_context_refs).to_dict()),
    )
    request = replace(
        base,
        task_context=task,
        command_id=task.command_id,
        input_artifacts=(template,),
        immutable_context_refs=(*base.immutable_context_refs, template.ref),
        required_output_schema=required_output_schema(
            base.command, task, schema_version=EXPERT_LOOP_ACI_SCHEMA_VERSION,
        ),
    )

    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    validated = validate_postflight(request, response)
    assert isinstance(validated.candidate, LiteratureQuery)
    assert validated.candidate.schema_version == 2
    assert request.required_output_schema == "LiteratureQuery/v2"

    raw = request.to_dict()
    raw["schema_version"] = 9
    with pytest.raises(AgentACIError) as error:
        AgentRequest.from_dict(raw)
    assert error.value.code == "invalid_schema"


def test_aci8_query_plan_remains_literature_query_v1_read_compatible() -> None:
    request = _query_plan_request(schema_version=8)
    assert request.required_output_schema == "LiteratureQuery/v1"

    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    validated = validate_postflight(request, response)

    assert isinstance(validated.candidate, LiteratureQuery)
    assert validated.candidate.schema_version == 1
    assert "preferred_venues" not in validated.candidate.to_dict()


def test_query_plan_rejects_cross_aci_literature_query_version() -> None:
    request = _query_plan_request()
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = dict(response.typed_candidate or {})
    candidate["schema_version"] = 1
    candidate.pop("preferred_venues")
    response = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )

    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, response)
    assert error.value.code == "invalid_candidate"

    legacy_request = _query_plan_request(schema_version=8)
    legacy_response = FakeAgent().invoke(legacy_request)
    assert isinstance(legacy_response, AgentResponse)
    v2 = _literature_query(
        legacy_request.immutable_context_refs,
        schema_version=2,
    )
    legacy_response = with_output_digest(replace(
        legacy_response,
        typed_candidate=v2.to_dict(),
        output_digest="",
    ))
    with pytest.raises(AgentACIError) as error:
        validate_postflight(legacy_request, legacy_response)
    assert error.value.code == "invalid_candidate"


def test_fake_query_plan_fails_closed_without_the_host_template() -> None:
    request = _query_plan_request()
    inputs = tuple(item for item in request.input_artifacts
        if not item.ref.endswith("--literature-query-template"))
    request = replace(
        request,
        input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
    )

    with pytest.raises(AgentACIError) as error:
        FakeAgent().invoke(request)
    assert error.value.code == "invalid_embedded_input"


def test_literature_main_can_cite_only_its_validated_packet_input() -> None:
    request = _literature_main_request()
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    validated = validate_postflight(request, response)
    packet_ref = next(item.ref for item in request.input_artifacts
        if item.ref.endswith("--literature-packet"))
    assert validated.candidate.supporting_refs == (packet_ref,)

    query_ref = next(item.ref for item in request.input_artifacts
        if item.ref.endswith("--literature-query"))
    invalid = with_output_digest(replace(response, typed_candidate={
        **dict(response.typed_candidate or {}), "supporting_refs": [query_ref],
    }, output_digest=""))
    with pytest.raises(AgentACIError, match="outside its validated packet"):
        validate_postflight(request, invalid)


@pytest.mark.parametrize(
    "subkind",
    (ResearchNodeSubkind.FORWARD, ResearchNodeSubkind.BACKWARD),
)
def test_pincer_wings_return_only_bounded_expert_loop_arguments(
    subkind: ResearchNodeSubkind,
) -> None:
    request = _pincer_request(subkind)
    assert request.required_output_schema == "ExpertLoopCandidate/v1"

    validated = validate_postflight(request, FakeAgent().invoke(request))

    assert isinstance(validated.candidate, ExpertLoopCandidate)
    assert validated.candidate.task_kind == "HYPOTHESIS_PROPOSAL"
    assert set(validated.candidate.supporting_refs) <= set(
        request.immutable_context_refs
    )


def test_pincer_synthesis_returns_one_cycle_one_root_hypothesis() -> None:
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    assert request.required_output_schema == "HypothesisCandidate/v2"

    validated = validate_postflight(request, FakeAgent().invoke(request))

    assert isinstance(validated.candidate, HypothesisCandidate)
    assert validated.candidate.component_id == request.component_id
    assert validated.candidate.proposer_role == request.role
    assert validated.candidate.method_family_id
    assert validated.candidate.parent_hypothesis_id is None
    assert validated.candidate.relation is None


def test_pincer_output_schema_requires_the_exact_typed_subkind() -> None:
    with pytest.raises(AgentACIError) as missing_context:
        required_output_schema(AgentCommand.HYPOTHESIS_PROPOSAL)
    assert missing_context.value.code == "invalid_task_context"

    synthesis = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    with pytest.raises(AgentACIError) as wrong_schema:
        replace(synthesis, required_output_schema="ExpertLoopCandidate/v1")
    assert wrong_schema.value.code == "wrong_output_schema"


def test_successor_cycle_synthesis_binds_exact_parent_and_nonempty_relation() -> None:
    request = _pincer_request(
        ResearchNodeSubkind.SYNTHESIS,
        cycle=2,
        parent_hypothesis_id="hypothesis-cycle-1",
    )

    validated = validate_postflight(request, FakeAgent().invoke(request))

    assert isinstance(validated.candidate, HypothesisCandidate)
    assert validated.candidate.parent_hypothesis_id == "hypothesis-cycle-1"
    assert validated.candidate.relation == "revises"


def test_successor_cycle_synthesis_fails_closed_without_predecessor_binding() -> None:
    with pytest.raises(AgentACIError) as error:
        _pincer_request(ResearchNodeSubkind.SYNTHESIS, cycle=2)
    assert error.value.code == "invalid_lineage_context"


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("duplicate", "invalid_lineage_context"),
        ("wrong_media", "invalid_lineage_context"),
        ("wrong_cycle", "invalid_lineage_context"),
        ("missing_parent", "invalid_lineage_context"),
        ("extra_field", "invalid_lineage_context"),
    ),
)
def test_successor_cycle_synthesis_rejects_invalid_lineage_context(
    mutation: str,
    code: str,
) -> None:
    request = _pincer_request(
        ResearchNodeSubkind.SYNTHESIS,
        cycle=2,
        parent_hypothesis_id="hypothesis-cycle-1",
    )
    lineage = request.input_artifacts[-1]
    if mutation == "duplicate":
        changed = EmbeddedInput.from_bytes(
            "other--lineage-context",
            lineage.media_type,
            lineage.content_bytes(),
        )
        inputs = request.input_artifacts + (changed,)
    elif mutation == "wrong_media":
        inputs = request.input_artifacts[:-1] + (
            replace(lineage, media_type="text/plain"),
        )
    else:
        payload = {
            "cycle": 1 if mutation == "wrong_cycle" else 2,
            **(
                {}
                if mutation == "missing_parent"
                else {"parent_hypothesis_id": "hypothesis-cycle-1"}
            ),
            **({"unexpected": True} if mutation == "extra_field" else {}),
        }
        inputs = request.input_artifacts[:-1] + (
            EmbeddedInput.from_bytes(
                lineage.ref,
                lineage.media_type,
                canonical_json(payload),
            ),
        )
    with pytest.raises(AgentACIError) as error:
        replace(
            request,
            input_artifacts=inputs,
            immutable_context_refs=tuple(item.ref for item in inputs),
        )
    assert error.value.code == code


def test_cycle_one_synthesis_rejects_a_lineage_context_input() -> None:
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    lineage = EmbeddedInput.from_bytes(
        f"{request.run_id}--{request.component_id}--lineage-context",
        "application/json",
        canonical_json(
            {"cycle": 1, "parent_hypothesis_id": "hypothesis-impossible"}
        ),
    )
    with pytest.raises(AgentACIError) as error:
        replace(
            request,
            input_artifacts=request.input_artifacts + (lineage,),
        )
    assert error.value.code == "invalid_lineage_context"


@pytest.mark.parametrize(
    ("parent_hypothesis_id", "relation", "code"),
    (
        ("hypothesis-other", "revises", "hypothesis_lineage_mismatch"),
        (None, None, "hypothesis_lineage_mismatch"),
        ("hypothesis-cycle-1", "", "invalid_candidate"),
    ),
)
def test_successor_synthesis_rejects_candidate_not_bound_to_predecessor(
    parent_hypothesis_id: str | None,
    relation: str | None,
    code: str,
) -> None:
    request = _pincer_request(
        ResearchNodeSubkind.SYNTHESIS,
        cycle=2,
        parent_hypothesis_id="hypothesis-cycle-1",
    )
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = {
        **(response.typed_candidate or {}),
        "parent_hypothesis_id": parent_hypothesis_id,
        "relation": relation,
    }
    changed = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == code


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("component_id", "primary.other", "component_id_mismatch"),
        ("proposer_role", "other-expert", "proposer_role_mismatch"),
        ("method_family_id", "", "invalid_candidate"),
    ),
)
def test_pincer_synthesis_binds_component_role_and_nonempty_method(
    field: str,
    value: str,
    code: str,
) -> None:
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = {**(response.typed_candidate or {}), field: value}
    changed = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == code


def test_pincer_cycle_one_synthesis_rejects_parent_or_preselected_method() -> None:
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    with pytest.raises(AgentACIError) as preselected:
        replace(request, method_family_id="family-preselected")
    assert preselected.value.code == "invalid_method_family_context"

    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = {
        **(response.typed_candidate or {}),
        "parent_hypothesis_id": "hypothesis-parent",
        "relation": "revises",
    }
    changed = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )
    with pytest.raises(AgentACIError) as parent:
        validate_postflight(request, changed)
    assert parent.value.code == "hypothesis_lineage_mismatch"


@pytest.mark.parametrize("field", ("parent_hypothesis_id", "relation"))
def test_pincer_cycle_one_synthesis_requires_explicit_null_lineage(
    field: str,
) -> None:
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = dict(response.typed_candidate or {})
    candidate.pop(field)
    changed = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == "invalid_schema"


def test_pincer_argument_outside_the_request_is_an_invalid_candidate() -> None:
    request = _pincer_request(ResearchNodeSubkind.FORWARD)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = {
        **(response.typed_candidate or {}),
        "supporting_refs": ["not-in-the-request"],
    }
    changed = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == "invalid_candidate"


def test_pincer_task_kind_mismatch_remains_fail_closed() -> None:
    request = _pincer_request(ResearchNodeSubkind.FORWARD)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = {
        **(response.typed_candidate or {}),
        "task_kind": "RESULT_ANALYSIS",
    }
    changed = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )

    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)

    assert error.value.code == "task_candidate_mismatch"


@pytest.mark.parametrize("command", tuple(command for command in AgentCommand if command not in {AgentCommand.ADJUDICATE, AgentCommand.DEBATE_JUDGE}))
def test_request_response_round_trip_and_generated_postflight(command: AgentCommand) -> None:
    request = default_request(command)
    assert AgentRequest.from_json(request.to_json()) == request
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    assert AgentResponse.from_json(response.to_json()) == response
    validated = validate_postflight(request, response)
    assert validated.response.command is command
    if command is AgentCommand.PROPOSE_HYPOTHESIS:
        assert validated.candidate.component_id == request.component_id
        assert validated.candidate.proposer_role == request.role
        assert validated.candidate.method_family_id
        assert validated.candidate.claim
        assert validated.workspace_files == {}
    elif command in {
        AgentCommand.DRAFT_EXPERIMENT_SPEC,
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    }:
        assert validated.candidate.component_id == request.component_id
        assert validated.candidate.owner_role == request.role
        assert validated.candidate.method_family_id == request.method_family_id
        assert validated.candidate.metrics[0].name == "score"
        assert validated.candidate.resource_preference is ResourcePreference.CPU
        expected = {"experiment.py"} if command is AgentCommand.DRAFT_EXPERIMENT_SPEC else {
            "experiment.py",
            "implementation.patch",
        }
        assert set(validated.workspace_files) == expected
    elif command is AgentCommand.ANSWER_RENDEZVOUS:
        assert validated.candidate.responder_role == "scientist"
        assert validated.workspace_files == {}
    elif isinstance(validated.candidate, LiteratureQuery):
        assert validated.candidate.query
        assert validated.workspace_files == {}
    elif command is AgentCommand.CYCLE_REPORT:
        assert set(validated.workspace_files) == {"MEMORY.md", "rounds/1.md"}
    elif command is AgentCommand.FINAL_REPORT:
        assert set(validated.workspace_files) == {"FINAL_REPORT.md"}
    else:
        assert validated.candidate.task_kind
        assert validated.candidate.summary
        assert validated.workspace_files == {}


def test_compute_capability_snapshot_drives_only_resource_preference_and_fake_payload() -> None:
    capabilities = EmbeddedInput.from_bytes(
        "run-1--compute-capabilities", "application/json",
        b'{"gpus":[{"uuid":"GPU-host-owned"}]}',
    )
    request = replace(
        default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC),
        input_artifacts=(capabilities,),
    )
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    validated = validate_postflight(request, response)
    assert validated.candidate.resource_preference is ResourcePreference.AUTO
    source = validated.workspace_files["experiment.py"]
    assert b"torch.cuda.is_available()" in source
    assert b"CUDA_VISIBLE_DEVICES" not in source
    assert "uuid" not in response.typed_candidate


@pytest.mark.parametrize("authority_field", ("resource_grant", "gpu_uuid", "gpu_index", "device_nodes", "environment", "runtime"))
def test_experiment_candidate_cannot_claim_host_resource_authority(authority_field: str) -> None:
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = dict(response.typed_candidate or {})
    candidate[authority_field] = "forbidden"
    changed = with_output_digest(replace(response, typed_candidate=candidate, output_digest=""))
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == "invalid_schema"


def test_unknown_request_and_response_fields_are_rejected() -> None:
    request = default_request()
    raw_request = json.loads(request.to_json())
    raw_request["surprise"] = True
    with pytest.raises(AgentACIError, match="unknown=.*surprise"):
        AgentRequest.from_dict(raw_request)

    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    raw_response = response.to_dict()
    raw_response["surprise"] = True
    with pytest.raises(AgentACIError, match="unknown=.*surprise"):
        AgentResponse.from_dict(raw_response)


def test_request_embeds_and_bounds_the_actual_context_bytes() -> None:
    first = EmbeddedInput.from_bytes("goal", "application/json", b'{"goal":"alpha"}')
    second = EmbeddedInput.from_bytes("goal", "application/json", b'{"goal":"beta"}')
    request = replace(default_request(), input_artifacts=(first,), max_input_bytes=16)
    changed = replace(request, input_artifacts=(second,))

    assert AgentRequest.from_json(request.to_json()) == request
    assert request.input_artifacts[0].content_bytes() == b'{"goal":"alpha"}'
    assert request_digest(request) != request_digest(changed)
    with pytest.raises(AgentACIError) as error:
        replace(request, max_input_bytes=15)
    assert error.value.code == "input_too_large"


def test_request_schema_requires_component_limits_and_selected_adapter_identity() -> None:
    request = default_request()
    assert request.input_artifacts == ()
    assert request.required_model_identity == FAKE_MODEL_IDENTITY
    assert request.required_adapter_version == FAKE_ADAPTER_VERSION
    for field in (
        "component_id",
        "method_family_id",
        "max_input_bytes",
        "required_model_identity",
        "required_adapter_version",
    ):
        raw = request.to_dict()
        del raw[field]
        with pytest.raises(AgentACIError, match=f"missing=.*{field}"):
            AgentRequest.from_dict(raw)


def test_embedded_input_schema_and_base64_are_strict() -> None:
    with pytest.raises(AgentACIError, match="unknown=.*path"):
        EmbeddedInput.from_dict(
            {"ref": "goal", "media_type": "application/json", "content_base64": "e30=", "path": "x"}
        )
    with pytest.raises(AgentACIError) as error:
        EmbeddedInput(ref="goal", media_type="application/json", content_base64="not base64")
    assert error.value.code == "invalid_embedded_input"


@pytest.mark.parametrize("command", tuple(command for command in AgentCommand if command is not AgentCommand.ADJUDICATE))
def test_zero_mandatory_effect_is_generic_postflight_failure(command: AgentCommand) -> None:
    request = default_request(command)
    if command is AgentCommand.DEBATE_JUDGE:
        from test_debate_aci14 import judge_request
        request = judge_request()[0]
    response = FakeAgent(FakeAgentMode.MISSING_EFFECT).invoke(request)
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, response)
    assert error.value.code == "mandatory_effect_mismatch"


@pytest.mark.parametrize(
    ("mode", "code"),
    (
        (FakeAgentMode.MALFORMED_JSON, "malformed_json"),
        (FakeAgentMode.WRONG_SCHEMA, "wrong_schema_version"),
        (FakeAgentMode.WRONG_COMMAND_ID, "wrong_invocation_identity"),
        (FakeAgentMode.WRONG_ATTEMPT_ID, "wrong_invocation_identity"),
        (FakeAgentMode.WRONG_ATTEMPT_ORDINAL, "wrong_invocation_identity"),
        (FakeAgentMode.WRONG_EFFECT_IDENTITY, "wrong_effect_identity"),
        (FakeAgentMode.DUPLICATE_EFFECT, "duplicate_effect"),
        (FakeAgentMode.FREE_TEXT_ONLY, "missing_candidate"),
        (FakeAgentMode.TIMEOUT, "agent_attempt_failed"),
        (FakeAgentMode.NONZERO_EXIT, "agent_attempt_failed"),
        (FakeAgentMode.PATH_TRAVERSAL, "unsafe_path"),
        (FakeAgentMode.MISSING_METADATA, "invalid_schema"),
        (FakeAgentMode.WRONG_INPUT_DIGEST, "input_digest_mismatch"),
        (FakeAgentMode.WRONG_OUTPUT_DIGEST, "output_digest_mismatch"),
    ),
)
def test_adversarial_response_fails_locally(mode: FakeAgentMode, code: str) -> None:
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, FakeAgent(mode).invoke(request))
    assert error.value.code == code


@pytest.mark.parametrize(
    ("changes", "code"),
    (
        ({"cost": "0.01"}, "cost_budget_exceeded"),
        ({"token_usage": {"input_tokens": 256, "output_tokens": 257, "total_tokens": 513}},
         "token_budget_exceeded"),
    ),
)
def test_postflight_enforces_declared_cost_and_token_budgets(changes, code: str) -> None:
    request = default_request()
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    changed = with_output_digest(replace(response, output_digest="", **changes))
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == code


def test_observe_mode_records_and_accepts_per_invocation_token_overage() -> None:
    request = default_request()
    request = replace(
        request,
        budget=replace(request.budget, token_enforcement_mode="observe"),
    )
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    observed = with_output_digest(
        replace(
            response,
            token_usage={
                "input_tokens": 512,
                "output_tokens": 1,
                "total_tokens": 513,
            },
            output_digest="",
        )
    )

    validated = validate_postflight(request, observed)

    assert validated.response.token_usage["total_tokens"] == 513
    assert validated.response.token_usage["total_tokens"] > request.budget.max_tokens


def test_agent_budget_requires_text_cost_identity() -> None:
    with pytest.raises(AgentACIError) as error:
        AgentBudget(
            max_turns=1,
            max_tokens=1,
            max_output_bytes=1,
            wall_seconds=1,
            max_cost=0,  # type: ignore[arg-type]
        )
    assert error.value.code == "invalid_budget"


def test_agent_budget_defaults_token_enforcement_and_rejects_unknown_mode() -> None:
    budget = AgentBudget.from_dict(
        {
            "max_turns": 1,
            "max_tokens": 1,
            "max_output_bytes": 1,
            "wall_seconds": 1,
            "max_cost": "0",
        }
    )
    assert budget.token_enforcement_mode == "enforce"
    assert budget.to_dict()["token_enforcement_mode"] == "enforce"
    with pytest.raises(AgentACIError) as error:
        replace(budget, token_enforcement_mode="unbounded")
    assert error.value.code == "invalid_budget"


def test_postflight_rejects_cost_currency_outside_the_fixed_usd_budget() -> None:
    request = default_request(AgentCommand.PROPOSE_HYPOTHESIS)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    response = with_output_digest(replace(response, cost_currency="EUR", output_digest=""))
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, response)
    assert error.value.code == "invalid_cost_currency"


@pytest.mark.parametrize(
    ("changes", "code"),
    (
        ({"turn_count": 3}, "turn_budget_exceeded"),
        ({"model_identity": "other:model"}, "model_identity_mismatch"),
        ({"adapter_version": "other-adapter/1"}, "adapter_version_mismatch"),
    ),
)
def test_postflight_binds_turns_model_and_adapter(changes, code: str) -> None:
    request = default_request()
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    changed = with_output_digest(replace(response, output_digest="", **changes))
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == code


def test_response_schema_rejects_nonpositive_attempt_and_unknown_token_counters() -> None:
    response = FakeAgent().invoke(default_request())
    assert isinstance(response, AgentResponse)
    with pytest.raises(AgentACIError) as error:
        replace(response, attempt_ordinal=0)
    assert error.value.code == "invalid_attempt_ordinal"
    with pytest.raises(AgentACIError) as error:
        replace(
            response,
            token_usage={**response.token_usage, "cache_tokens": 1},
        )
    assert error.value.code == "invalid_token_usage"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("component_id", "primary.other", "component_id_mismatch"),
        ("proposer_role", "another-expert", "proposer_role_mismatch"),
    ),
)
def test_hypothesis_component_and_proposer_are_bound_to_the_request(
    field: str, value: str, code: str,
) -> None:
    request = default_request(AgentCommand.PROPOSE_HYPOTHESIS)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = dict(response.typed_candidate or {})
    candidate[field] = value
    changed = with_output_digest(replace(response, typed_candidate=candidate, output_digest=""))
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == code


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("component_id", "must.other", "component_id_mismatch"),
        ("owner_role", "another-expert", "owner_role_mismatch"),
        ("method_family_id", "another-family", "method_family_id_mismatch"),
    ),
)
def test_experiment_component_owner_and_family_are_bound_to_the_request(
    field: str, value: str, code: str,
) -> None:
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    candidate = dict(response.typed_candidate or {})
    candidate[field] = value
    changed = with_output_digest(
        replace(response, typed_candidate=candidate, output_digest="")
    )
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, changed)
    assert error.value.code == code


def test_command_specific_method_family_request_rules_are_strict() -> None:
    with pytest.raises(AgentACIError) as proposal_error:
        replace(
            default_request(AgentCommand.PROPOSE_HYPOTHESIS),
            method_family_id="preselected-family",
        )
    assert proposal_error.value.code == "invalid_method_family_context"

    with pytest.raises(AgentACIError) as spec_error:
        replace(
            default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC),
            method_family_id=None,
        )
    assert spec_error.value.code == "invalid_method_family_context"


def test_artifact_component_spec_accepts_an_empty_metric_tuple() -> None:
    component = EmbeddedInput.from_bytes(
        "must.bundle--spec-contract",
        "application/json",
        json.dumps(
            {
                "component": {
                    "id": "must.bundle",
                    "result": {
                        "kind": "artifact",
                        "evaluation_kind": "artifact_check",
                    },
                    "seeds": [],
                    "required_artifacts": ["implementation_patch"],
                },
                "protocol": {
                    "result_kind": "artifact",
                    "requirements": {
                        "required_metrics": [],
                        "required_artifacts": ["implementation_patch"],
                        "artifact_validators": [],
                    },
                },
                "goal": {"statement": "publish the implementation patch"},
                "required_experiment_spec": {
                    "result_kind": "artifact",
                    "metric": None,
                    "trial_plan": {"seeds": [], "arms": [], "trial_ids": []},
                    "required_artifacts": ["implementation_patch"],
                    "artifact_requirements": ["result.json"],
                }
            },
            sort_keys=True,
        ).encode(),
    )
    request = replace(
        default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC),
        component_id="must.bundle",
        method_family_id="family-implementation",
        immutable_context_refs=(component.ref,),
        input_artifacts=(component,),
    )
    validated = validate_postflight(request, FakeAgent().invoke(request))
    assert validated.candidate.component_id == "must.bundle"
    assert validated.candidate.method_family_id == "family-implementation"
    assert validated.candidate.metrics == ()
    assert validated.candidate.artifact_requirements == ("result.json",)
