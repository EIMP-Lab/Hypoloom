from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from hypoloom.agent_aci import (
    EXPERT_LOOP_ACI_SCHEMA_VERSION,
    MULTICHAIN_ACI_SCHEMA_VERSION,
    AgentACIError,
    AgentRequest,
    DeclaredEffect,
    EmbeddedInput,
    HypothesisCandidate,
    HypothesisSetCandidate,
    IterationDisposition,
    canonical_json,
    request_digest,
    required_effect,
    required_output_schema,
    validate_postflight,
    with_output_digest,
)
from hypoloom.domain import AgentCommand, fact_to_dict
from hypoloom.expert_loop import (
    AdjudicationCandidate,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.fake_agent import FakeAgent, default_request
from test_agent_aci import (
    _pincer_request,
    _update_request,
    _hypothesis_fact,
    _assessment_fact,
)
from test_domain import all_facts
from test_measurement_contract import declaration


def test_self_evolve_is_an_aci14_research_node_without_scientific_or_workspace_effects():
    command = AgentCommand("SELF_EVOLVE")
    request = default_request(command)
    assert request.schema_version == 14
    assert request.task_context.key.node_kind.value == "SELF_EVOLVE"
    accepted = validate_postflight(request, FakeAgent().invoke(request))
    assert accepted.candidate.task_kind == "SELF_EVOLVE"
    assert not accepted.workspace_files
    with pytest.raises(AgentACIError):
        replace(request, schema_version=13)


@pytest.mark.parametrize("selected", ["hyp-a", "hyp-b", None])
def test_aci14_update_reads_exact_selected_batch_tip_not_single_root_walk(selected):
    from hypoloom.agent_aci import hypothesis_update_current_assessment
    from hypoloom.domain import HypothesisAssessment

    request = _update_request(
        _hypothesis_fact(
            "hyp-a", parent_hypothesis_id=None, method_family_id="family-current"
        ),
        _hypothesis_fact(
            "hyp-b", parent_hypothesis_id=None, method_family_id="family-current"
        ),
        _assessment_fact(
            "hyp-a",
            method_family_id="family-current",
            assessment=HypothesisAssessment.SUPPORTED,
        ),
        _assessment_fact(
            "hyp-b",
            method_family_id="family-current",
            assessment=HypothesisAssessment.REFUTED,
        ),
        cycle=1,
        method_family_id="family-current",
    )
    tip = (
        None
        if selected is None
        else {
            "node_id": selected,
            "chain_id": "chain-a" if selected == "hyp-a" else "chain-b",
            "run_id": request.run_id,
            "component_id": request.component_id,
            "expert_slot": request.role,
            "method_family_id": request.method_family_id,
            "outcome": "SUCCESS" if selected == "hyp-a" else "CLEAN_REFUTATION",
        }
    )
    context = EmbeddedInput.from_bytes(
        request.command_id + "--batch-context",
        "application/json",
        canonical_json(
            {
                "batch_command_id": "synthesis-command",
                "cycle": 1,
                "chains": {"chain-a": "hyp-a", "chain-b": "hyp-b"},
                "terminal_tips": {},
                "selected_tip": tip,
            }
        ),
    )
    request = replace(
        request,
        schema_version=14,
        input_artifacts=(*request.input_artifacts, context),
        immutable_context_refs=(*request.immutable_context_refs, context.ref),
    )
    expected = {
        "hyp-a": HypothesisAssessment.SUPPORTED,
        "hyp-b": HypothesisAssessment.REFUTED,
        None: None,
    }[selected]
    assert hypothesis_update_current_assessment(request) is expected
    raw = {
        "task_kind": "HYPOTHESIS_UPDATE",
        "summary": "Close current batch.",
        "supporting_refs": [],
        "update_disposition": "REVISE_NEXT_CYCLE",
    }
    if selected == "hyp-a":
        with pytest.raises(AgentACIError):
            validate_postflight(request, _response(request, raw))
    else:
        validate_postflight(request, _response(request, raw))


def _request(command=AgentCommand.HYPOTHESIS_PROPOSAL, *, cycle=1):
    if command is AgentCommand.HYPOTHESIS_PROPOSAL:
        base = _pincer_request(
            ResearchNodeSubkind.SYNTHESIS,
            cycle=cycle,
            parent_hypothesis_id=None if cycle == 1 else "parent-1",
        )
    elif command is AgentCommand.ADJUDICATE:
        base = default_request(AgentCommand.CYCLE_REPORT)
        task = ResearchNodeTask(
            key=replace(base.task_context.key, node_kind=ResearchNodeKind.ADJUDICATE)
        )
        base = replace(
            base,
            command=command,
            task_context=task,
            command_id=task.command_id,
            schema_version=14,
            required_output_schema="Adjudication/v1",
            effect_requirements=(required_effect(command),),
        )
    else:
        base = default_request(command)
    return replace(
        base,
        schema_version=14,
        required_output_schema=required_output_schema(
            command, base.task_context, schema_version=14
        ),
    )


def _response(request, candidate):
    base = FakeAgent().invoke(default_request(AgentCommand.LITERATURE_REVIEW))
    return with_output_digest(
        replace(
            base,
            schema_version=request.schema_version,
            command=request.command,
            command_id=request.command_id,
            attempt_id=request.attempt_id,
            attempt_ordinal=request.attempt_ordinal,
            input_digest=request_digest(request),
            typed_candidate=candidate,
            declared_effects=(
                DeclaredEffect(
                    effect=required_effect(request.command),
                    command_id=request.command_id,
                    attempt_id=request.attempt_id,
                ),
            ),
        )
    )


def _batch(*, cycle=1):
    request = _pincer_request(
        ResearchNodeSubkind.SYNTHESIS,
        cycle=cycle,
        parent_hypothesis_id=None if cycle == 1 else "parent-1",
    )
    candidate = dict(FakeAgent().invoke(request).typed_candidate)
    return {
        "schema_version": 1,
        "hypotheses": [
            {"chain_id": chain, "candidate": copy.deepcopy(candidate)}
            for chain in ("chain-a", "chain-b")
        ],
    }


def _analysis_request(outcome="SUCCESS"):
    request = _request(AgentCommand.RESULT_ANALYSIS)
    hypothesis, spec, result = [fact_to_dict(item) for item in all_facts()[1:4]]
    for item in (hypothesis, spec, result):
        item.update(run_id=request.run_id, component_id=request.component_id)
    result["outcome"] = outcome
    inputs = tuple(
        EmbeddedInput.from_bytes(
            f"{request.command_id}--current-{name}",
            "application/json",
            canonical_json(
                {
                    "fact_id": payload["fact_id"],
                    "fact_type": payload["fact_type"],
                    "run_id": request.run_id,
                    "payload": payload,
                    **{
                        field: payload.get(field)
                        for field in (
                            "hypothesis_id",
                            "experiment_id",
                            "execution_id",
                            "outcome_id",
                        )
                    },
                }
            ),
        )
        for name, payload in (("spec", spec), ("outcome", result))
    )
    return replace(
        request,
        input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
    )


def test_aci14_batch_wire_does_not_rewrite_retained_aci13_candidates():
    assert EXPERT_LOOP_ACI_SCHEMA_VERSION == 13
    assert MULTICHAIN_ACI_SCHEMA_VERSION == 14
    request = _request()
    assert request.required_output_schema == "HypothesisSet/v1"
    assert AgentRequest.from_json(request.to_json()) == request
    assert (
        _pincer_request(ResearchNodeSubkind.SYNTHESIS).required_output_schema
        == "HypothesisCandidate/v2"
    )


@pytest.mark.parametrize("cycle", [1, 2])
def test_batch_members_receive_existing_typed_hypothesis_postflight(cycle):
    request, raw = _request(cycle=cycle), _batch(cycle=cycle)
    raw["hypotheses"][1]["candidate"]["measurement"] = declaration().to_dict()
    validated = validate_postflight(request, _response(request, raw)).candidate
    assert isinstance(validated, HypothesisSetCandidate)
    assert tuple(item.chain_id for item in validated.hypotheses) == (
        "chain-a",
        "chain-b",
    )
    assert all(
        isinstance(item.candidate, HypothesisCandidate) for item in validated.hypotheses
    )
    assert validated.hypotheses[1].candidate.measurement == declaration()
    assert validated.to_dict() == raw


@pytest.mark.parametrize(
    "tamper",
    [
        "owner",
        "component",
        "parent",
        "missing_lineage",
        "measurement",
        "duplicate",
        "unsafe",
        "empty",
    ],
)
def test_no_batch_member_bypasses_existing_hypothesis_validation(tamper):
    request, raw = _request(), _batch()
    member = raw["hypotheses"][1]
    if tamper == "owner":
        member["candidate"]["proposer_role"] = "foreign"
    elif tamper == "component":
        member["candidate"]["component_id"] = "other.component"
    elif tamper == "parent":
        member["candidate"].update(parent_hypothesis_id="foreign", relation="refines")
    elif tamper == "missing_lineage":
        member["candidate"].pop("parent_hypothesis_id")
    elif tamper == "measurement":
        member["candidate"]["measurement"] = {"metric": "invented"}
    elif tamper == "duplicate":
        member["chain_id"] = "chain-a"
    elif tamper == "unsafe":
        member["chain_id"] = "../foreign"
    else:
        raw["hypotheses"] = []
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, raw))


def test_successor_batch_rejects_one_member_with_another_bound_parent():
    request, raw = _request(cycle=2), _batch(cycle=2)
    raw["hypotheses"][1]["candidate"]["parent_hypothesis_id"] = "other-parent"
    with pytest.raises(AgentACIError, match="lineage"):
        validate_postflight(request, _response(request, raw))


def _adjudication():
    return {
        "schema_version": 1,
        "verdicts": [
            {
                "chain_id": "chain-a",
                "node_id": "hyp-a",
                "status": "verified",
                "evidence": ["out-a"],
            },
            {
                "chain_id": "chain-b",
                "node_id": "hyp-b",
                "status": "inconclusive",
                "evidence": [],
            },
        ],
        "keep": ["chain-a"],
        "prune": ["chain-b"],
        "best_chain": "chain-a",
        "summary": "Prefer the accepted tested tip.",
    }


def test_adjudicate_is_a_typed_shape_not_an_accepted_science_verdict():
    request = _request(AgentCommand.ADJUDICATE)
    request = replace(request, immutable_context_refs=("out-a", "hyp-a", "hyp-b"))
    candidate = validate_postflight(
        request, _response(request, _adjudication())
    ).candidate
    assert isinstance(candidate, AdjudicationCandidate)
    assert candidate.to_dict() == _adjudication()
    assert not hasattr(candidate, "selected_tip")
    with pytest.raises(AgentACIError):
        replace(request, schema_version=13)


@pytest.mark.parametrize(
    "tamper",
    ["overlap", "missing", "duplicate", "unknown_ref", "pruned_best", "bad_status"],
)
def test_adjudicate_shape_and_input_refs_are_checked_before_host_facts(tamper):
    request = replace(
        _request(AgentCommand.ADJUDICATE),
        immutable_context_refs=("out-a", "hyp-a", "hyp-b"),
    )
    raw = _adjudication()
    if tamper == "overlap":
        raw["keep"].append("chain-b")
    elif tamper == "missing":
        raw["verdicts"].pop()
    elif tamper == "duplicate":
        raw["verdicts"][1]["chain_id"] = "chain-a"
    elif tamper == "unknown_ref":
        raw["verdicts"][0]["evidence"] = ["fake-outcome"]
    elif tamper == "pruned_best":
        raw["best_chain"] = "chain-b"
    else:
        raw["verdicts"][0]["status"] = "EXECUTION_FAILURE"
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, raw))


@pytest.mark.parametrize(
    "outcome",
    [
        "SUCCESS",
        "CLEAN_REFUTATION",
        "EXECUTION_FAILURE",
        "INTEGRITY_REJECTED",
        "INCONCLUSIVE",
    ],
)
def test_iterate_requires_exact_accepted_comparable_outcome_input(outcome):
    request = _analysis_request(outcome)
    raw = {
        "task_kind": "RESULT_ANALYSIS",
        "summary": "Continue the same hypothesis with a new experiment.",
        "supporting_refs": [request.input_artifacts[1].ref],
        "iteration_disposition": "ITERATE",
    }
    if outcome in {"SUCCESS", "CLEAN_REFUTATION"}:
        assert (
            validate_postflight(
                request, _response(request, raw)
            ).candidate.iteration_disposition
            is IterationDisposition.ITERATE
        )
    else:
        with pytest.raises(AgentACIError, match="iteration"):
            validate_postflight(request, _response(request, raw))
    raw["iteration_disposition"] = "COMPLETE"
    assert (
        validate_postflight(
            request, _response(request, raw)
        ).candidate.iteration_disposition
        is IterationDisposition.COMPLETE
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "uncited",
        "foreign_run",
        "foreign_component",
        "wrong_experiment",
        "wrong_execution",
        "foreign_spec_hypothesis",
        "wrong_fact_type",
    ],
)
def test_iteration_cannot_reference_a_different_experiment_or_missing_fact(tamper):
    request = _analysis_request()
    raw = {
        "task_kind": "RESULT_ANALYSIS",
        "summary": "Iterate.",
        "supporting_refs": [request.input_artifacts[1].ref],
        "iteration_disposition": "ITERATE",
    }
    if tamper == "missing":
        request = replace(request, input_artifacts=())
    elif tamper == "uncited":
        raw["supporting_refs"] = []
    else:
        import json

        item = request.input_artifacts[1]
        payload = json.loads(item.content_bytes())
        field, value = {
            "foreign_run": ("run_id", "foreign"),
            "foreign_component": ("component_id", "foreign"),
            "wrong_experiment": ("experiment_id", "foreign"),
            "wrong_execution": ("execution_id", "foreign"),
            "foreign_spec_hypothesis": ("hypothesis_id", "foreign"),
            "wrong_fact_type": ("fact_type", "EvidenceLinked"),
        }[tamper]
        if tamper == "foreign_spec_hypothesis":
            item = request.input_artifacts[0]
            payload = json.loads(item.content_bytes())
        payload["payload"][field] = value
        changed = EmbeddedInput.from_bytes(
            item.ref, item.media_type, canonical_json(payload)
        )
        request = replace(
            request,
            input_artifacts=tuple(
                changed if old.ref == item.ref else old
                for old in request.input_artifacts
            ),
        )
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, raw))


def test_iteration_disposition_is_version_and_node_specific():
    request = _analysis_request()
    raw = {
        "task_kind": "RESULT_ANALYSIS",
        "summary": "Complete.",
        "supporting_refs": [],
    }
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, raw))
    legacy = replace(request, schema_version=13)
    assert (
        validate_postflight(
            legacy, _response(legacy, raw)
        ).candidate.iteration_disposition
        is None
    )
    raw["iteration_disposition"] = "COMPLETE"
    with pytest.raises(AgentACIError):
        validate_postflight(legacy, _response(legacy, raw))
    other = _request(AgentCommand.EXPERIMENT_EXECUTION)
    raw["task_kind"] = "EXPERIMENT_EXECUTION"
    with pytest.raises(AgentACIError):
        validate_postflight(other, _response(other, raw))


@pytest.mark.parametrize(
    "version,enabled",
    [
        (13, False),
        ("13", False),
        (14, True),
        ("14", True),
        (15, True),
        (True, False),
        (None, False),
        (14.0, False),
    ],
)
def test_literature_web_capability_is_explicit_aci14(version, enabled):
    from hypoloom.agent_aci import supports_literature_web
    from hypoloom.agent_capabilities import capabilities_for

    assert supports_literature_web(version) is enabled
    if type(version) is int:
        capabilities = capabilities_for(
            AgentCommand.LITERATURE_REVIEW, schema_version=version
        )
        assert capabilities.web is enabled
        assert ("host-tools" in capabilities.mcp_servers) is enabled


@pytest.mark.parametrize("sources", [None, [], ["https://example.org/actual-paper"]])
def test_literature_main_keeps_web_sources_in_existing_response_package(sources):
    from hypoloom.agent_aci import LiteratureSummaryCandidate

    request = _request(AgentCommand.LITERATURE_REVIEW)
    raw = {
        "task_kind": "LITERATURE_REVIEW",
        "summary": "A directly researched source informs synthesis.",
        "supporting_refs": [],
    }
    if sources is not None:
        raw["web_sources"] = sources
    response = _response(request, raw)
    candidate = validate_postflight(request, response).candidate
    assert isinstance(candidate, LiteratureSummaryCandidate)
    assert candidate.web_sources == tuple(sources or ())
    assert response.to_dict()["typed_candidate"] == raw
    if sources is not None:
        legacy = replace(request, schema_version=13)
        with pytest.raises(AgentACIError):
            validate_postflight(legacy, _response(legacy, raw))


@pytest.mark.parametrize(
    "sources",
    [
        ["file:///private"],
        ["https://example.org/paper"] * 2,
        [5],
        "https://example.org/paper",
    ],
)
def test_literature_web_sources_use_existing_grounding_url_validation(sources):
    request = _request(AgentCommand.LITERATURE_REVIEW)
    raw = {
        "task_kind": "LITERATURE_REVIEW",
        "summary": "Invalid sources.",
        "supporting_refs": [],
        "web_sources": sources,
    }
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, raw))


def test_adjudication_reads_canonical_ids_from_actual_fact_id_inputs_not_alias_files():
    request = _request(AgentCommand.ADJUDICATE)
    hypotheses = tuple(
        _hypothesis_fact(
            identity, parent_hypothesis_id=None, method_family_id="family-1"
        )
        for identity in ("hyp-a", "hyp-b")
    )
    payload = fact_to_dict(all_facts()[3])
    payload.update(
        fact_id="fact-out-a",
        outcome_id="out-a",
        run_id=request.run_id,
        component_id=request.component_id,
    )
    outcome = EmbeddedInput.from_bytes(
        payload["fact_id"],
        "application/json",
        canonical_json(
            {
                "fact_id": payload["fact_id"],
                "fact_type": payload["fact_type"],
                "payload": payload,
            }
        ),
    )
    request = replace(
        request,
        input_artifacts=(*hypotheses, outcome),
        immutable_context_refs=tuple(item.ref for item in (*hypotheses, outcome)),
    )
    assert not {"hyp-a", "hyp-b", "out-a"}.intersection(request.immutable_context_refs)
    assert (
        validate_postflight(
            request, _response(request, _adjudication())
        ).candidate.best_chain
        == "chain-a"
    )
