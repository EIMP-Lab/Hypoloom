from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import (AgentACIError, EmbeddedInput, WorkspaceArtifact,
    request_digest, required_output_schema, validate_postflight, with_output_digest)
from tests.runtime.test_grounding_dialogue_aci import exchange


ACCEPTANCE = {"summary": "The same candidate must pass both thresholds and the validity audit.",
    "rule": "ALL", "criteria": [{"name": "accuracy", "operator": "GT", "target": "0.8"},
        {"name": "quality", "operator": "GE", "target": "0.7"}], "validity_checks": ["valid"],
    "parser_argv": ["python", "evaluate.py"], "parser_files": ["evaluate.py"],
    "raw_artifacts": ["observations.json"]}
SOURCE = b"print('public parser fixture')\n"


def acceptance_exchange(*, question=False, shared=True):
    request, response = exchange(question=question)
    config = EmbeddedInput.from_bytes("run--grounding-config", "application/json",
        json.dumps({"goal": {"evaluation": {"mode": "grounding"}} if shared else {}}).encode())
    inputs = tuple(item for item in request.input_artifacts if not item.ref.endswith("--grounding-config")) + (config,)
    request = replace(request, schema_version=16, input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs), required_output_schema="GroundingCandidate/v3")
    response = with_output_digest(replace(response, schema_version=16,
        typed_candidate={**response.typed_candidate, "acceptance": None if question or not shared else ACCEPTANCE},
        proposed_workspace_artifacts=() if question or not shared else (WorkspaceArtifact.from_bytes("evaluate.py", SOURCE),),
        input_digest=request_digest(request)))
    return request, response


@pytest.mark.parametrize("question", [False, True])
def test_grounding_acceptance_binds_actual_files_only_on_final_response(question):
    request, response = acceptance_exchange(question=question)
    validated = validate_postflight(request, response)
    assert (validated.candidate.acceptance is None) is question
    assert validated.workspace_files == ({} if question else {"evaluate.py": SOURCE})
    assert required_output_schema(request.command, request.task_context, schema_version=16) == "GroundingCandidate/v3"


@pytest.mark.parametrize("damage", ["missing-declaration", "missing-source", "extra-source", "question-with-source", "legacy-with-declaration"])
def test_grounding_acceptance_rejects_incomplete_or_wrong_scope_output(damage):
    request, response = acceptance_exchange(question=damage == "question-with-source", shared=damage != "legacy-with-declaration")
    if damage == "missing-declaration": response = replace(response, typed_candidate={**response.typed_candidate, "acceptance": None})
    elif damage == "missing-source": response = replace(response, proposed_workspace_artifacts=())
    elif damage == "extra-source": response = replace(response, proposed_workspace_artifacts=(*response.proposed_workspace_artifacts, WorkspaceArtifact.from_bytes("extra.py", SOURCE)))
    elif damage == "question-with-source": response = replace(response, proposed_workspace_artifacts=(WorkspaceArtifact.from_bytes("evaluate.py", SOURCE),))
    else: response = replace(response, typed_candidate={**response.typed_candidate, "acceptance": ACCEPTANCE})
    with pytest.raises(AgentACIError): validate_postflight(request, with_output_digest(response))


def test_aci16_retains_ordinary_background_without_a_second_goal_protocol():
    request, response = acceptance_exchange(shared=False)
    assert validate_postflight(request, response).candidate.acceptance is None


def test_common_goal_local_support_can_continue_and_prompt_schema_agree():
    from tests.runtime.test_claude_agent_host import _claude_hypothesis_update_request
    from tests.runtime.test_agent_aci import _update_response
    from hypoloom.claude_agent_host import _output_schema, _prompt
    request = _claude_hypothesis_update_request()
    config = EmbeddedInput.from_bytes("run--expert-config", "application/json",
        json.dumps({"goal": {"evaluation": {"mode": "grounding"}}}).encode())
    inputs = (*(item for item in request.input_artifacts if not item.ref.endswith("--expert-config")), config)
    request = replace(request, schema_version=16, input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs), required_model_identity="fake:model-v1")
    response = with_output_digest(replace(_update_response(request, "REVISE_NEXT_CYCLE"),
        adapter_version=request.required_adapter_version, model_identity=request.required_model_identity))
    assert validate_postflight(request, response).candidate.update_disposition.value == "REVISE_NEXT_CYCLE"
    schema = _output_schema(request)["properties"]["typed_candidate"]["properties"]["update_disposition"]
    assert "REVISE_NEXT_CYCLE" in schema["enum"] and "const" not in schema
    instruction = json.loads(_prompt(request))["instruction"]
    assert "must choose TERMINATE_LINEAGE" not in instruction
    assert "Local SUPPORTED with an unresolved shared goal may request another scientific iteration or revised cycle." in instruction
    assert "No local C5 result alone proves the whole goal." in instruction
    assert "Only Policy accepts the complete goal and stops peers." in instruction
    assert "Write/test source under /workspace" not in instruction
