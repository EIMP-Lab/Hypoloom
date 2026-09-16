from __future__ import annotations

import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import (
    ACI_SCHEMA_VERSION,
    AgentACIError,
    AgentCommand,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    DeclaredEffect,
    EmbeddedInput,
    WorkspaceArtifact,
    canonical_json,
    request_digest,
    required_effect,
    validate_postflight,
    with_output_digest,
)
from hypoloom.collaboration import (
    ExpertSignal,
    ExpertSignalKind,
    RendezvousDisposition,
    RendezvousOrigin,
    RendezvousReply,
    RendezvousRequest,
    derive_rendezvous_records,
)
from hypoloom.fake_agent import (
    FAKE_ADAPTER_VERSION,
    FAKE_MODEL_IDENTITY,
    FakeAgent,
    default_request,
)


def signal(*, context_refs: tuple[str, ...] = ("goal-1",)) -> ExpertSignal:
    return ExpertSignal(
        schema_version=1,
        kind=ExpertSignalKind.HELP,
        target_role_hint="critic",
        question="Which assumption most threatens this proposal?",
        context_refs=context_refs,
    )


def rendezvous() -> RendezvousRequest:
    return RendezvousRequest(
        schema_version=1,
        rendezvous_id="rendezvous-attempt-1",
        run_id="run-fake",
        requester_role="scientist",
        requester_slot=1,
        target_role="critic",
        source_attempt_id="attempt-1",
        kind=ExpertSignalKind.HELP,
        question="Which assumption most threatens this proposal?",
        context_refs=("goal-1",),
        round=1,
        max_rounds=1,
    )


def reply(request: RendezvousRequest | None = None) -> RendezvousReply:
    request = request or rendezvous()
    return RendezvousReply(
        schema_version=1,
        rendezvous_id=request.rendezvous_id,
        kind=request.kind,
        disposition=RendezvousDisposition.ANSWERED,
        responder_role=request.target_role,
        summary="Add an explicit sensitivity check.",
        supporting_refs=("goal-1",),
    )


def answer_request(*, attempt_ordinal: int = 1) -> AgentRequest:
    request = rendezvous()
    embedded = EmbeddedInput.from_bytes(
        request.rendezvous_id,
        "application/json",
        canonical_json(request.to_dict()),
    )
    return replace(
        default_request(),
        command=AgentCommand.ANSWER_RENDEZVOUS,
        command_id=f"{request.rendezvous_id}--answer",
        attempt_id=f"{request.rendezvous_id}--agent-{attempt_ordinal}",
        attempt_ordinal=attempt_ordinal,
        role=request.target_role,
        immutable_context_refs=request.context_refs,
        input_artifacts=(embedded,),
        required_output_schema="RendezvousReply/v1",
        effect_requirements=(required_effect(AgentCommand.ANSWER_RENDEZVOUS),),
    )


def answer_response(request: AgentRequest) -> AgentResponse:
    return with_output_digest(
        AgentResponse(
            schema_version=ACI_SCHEMA_VERSION,
            command=AgentCommand.ANSWER_RENDEZVOUS,
            command_id=request.command_id,
            attempt_id=request.attempt_id,
            attempt_ordinal=request.attempt_ordinal,
            status=AgentStatus.SUCCEEDED,
            typed_candidate=reply().to_dict(),
            proposed_workspace_artifacts=(),
            declared_effects=(DeclaredEffect(
                effect=required_effect(AgentCommand.ANSWER_RENDEZVOUS),
                command_id=request.command_id,
                attempt_id=request.attempt_id,
            ),),
            diagnostics={"fixture": "typed-rendezvous"},
            model_identity=FAKE_MODEL_IDENTITY,
            adapter_version=FAKE_ADAPTER_VERSION,
            token_usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            turn_count=1,
            cost="0",
            cost_currency="USD",
            input_digest=request_digest(request),
            output_digest="",
        )
    )


def test_collaboration_wire_is_closed_and_round_trips() -> None:
    assert tuple(item.value for item in ExpertSignalKind) == ("HELP", "DEBATE")
    assert tuple(item.value for item in RendezvousDisposition) == (
        "ANSWERED", "DECLINED", "NO_TARGET", "TIMED_OUT", "CANCELLED", "INTEGRITY_REJECTED",
    )
    values = (signal(), rendezvous(), reply())
    for value in values:
        parsed = type(value).from_dict(json.loads(canonical_json(value.to_dict())))
        assert parsed == value
        raw = value.to_dict(); raw["surprise"] = True
        with pytest.raises(ValueError, match="unknown"):
            type(value).from_dict(raw)


def test_optional_signal_must_reference_only_immutable_agent_inputs() -> None:
    request = default_request(AgentCommand.PROPOSE_HYPOTHESIS)
    response = FakeAgent().invoke(request)
    assert isinstance(response, AgentResponse)
    valid = with_output_digest(replace(response, expert_signal=signal(), output_digest=""))
    assert validate_postflight(request, valid).response.expert_signal == signal()
    invalid = with_output_digest(replace(
        response,
        expert_signal=signal(context_refs=("goal-1", "private-workspace")),
        output_digest="",
    ))
    with pytest.raises(AgentACIError) as error:
        validate_postflight(request, invalid)
    assert error.value.code == "expert_signal_context_mismatch"


def test_answer_is_typed_no_workspace_nonrecursive_and_retry_bound() -> None:
    first_request = answer_request()
    first_response = answer_response(first_request)
    validated = validate_postflight(first_request, first_response)
    assert validated.candidate == reply()
    assert validated.workspace_files == {}
    assert validated.response.expert_signal is None

    forbidden = (
        (replace(first_response, proposed_workspace_artifacts=(
            WorkspaceArtifact.from_bytes("answer.txt", b"not allowed"),
        ), output_digest=""), "unexpected_workspace"),
        (replace(first_response, expert_signal=signal(), output_digest=""), "unexpected_expert_signal"),
    )
    for changed, code in forbidden:
        with pytest.raises(AgentACIError) as error:
            validate_postflight(first_request, with_output_digest(changed))
        assert error.value.code == code

    retry_request = answer_request(attempt_ordinal=2)
    assert retry_request.command_id == first_request.command_id
    with pytest.raises(AgentACIError) as error:
        validate_postflight(retry_request, first_response)
    assert error.value.code == "wrong_invocation_identity"
    assert validate_postflight(retry_request, answer_response(retry_request)).candidate == reply()


def test_component_phase_origin_uses_existing_request_and_resolution_stream() -> None:
    request = rendezvous()
    origin = RendezvousOrigin(
        rendezvous_id=request.rendezvous_id,
        requester_role=request.requester_role,
        requester_slot=request.requester_slot,
        source_attempt_id=request.source_attempt_id,
        target_role=request.target_role,
        signal=signal(),
        component_id="primary.metric",
        phase="analyze",
    )
    origin_record = {
        "rendezvous_id": origin.rendezvous_id,
        "requester_role": origin.requester_role,
        "requester_slot": origin.requester_slot,
        "source_attempt_id": origin.source_attempt_id,
        "target_role": origin.target_role,
        "expert_signal": origin.signal.to_dict(),
        "component_id": origin.component_id,
        "phase": origin.phase,
    }
    origins, requests, replies = derive_rendezvous_records((
        ("REQUEST_RENDEZVOUS", {
            "rendezvous_origin": origin_record,
            "rendezvous_request": request.to_dict(),
        }),
        ("RESOLVE_RENDEZVOUS", {"rendezvous_reply": reply(request).to_dict()}),
    ))
    assert origins == (origin,)
    assert requests == {request.rendezvous_id: request}
    assert replies == {request.rendezvous_id: reply(request)}
