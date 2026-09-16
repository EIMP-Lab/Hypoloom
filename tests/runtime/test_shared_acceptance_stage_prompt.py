"""Shared-goal context must respect each node's schema and authoring stage."""

from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.claude_agent_host import _output_schema, _prompt
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import FULL_CC_TOOLS, full_cc_instruction
from tests.runtime.test_shared_acceptance_prompt import _request


def _stage_request(stage, *, local=True):
    if stage in {"FORWARD", "BACKWARD", "SYNTHESIS"}:
        request = _request(AgentCommand.HYPOTHESIS_PROPOSAL, local=local)
        task = replace(request.task_context,
            key=replace(request.task_context.key, subkind=ResearchNodeSubkind(stage)))
        return replace(request, task_context=task, command_id=task.command_id,
            required_output_schema=required_output_schema(
            request.command, task, schema_version=request.schema_version))
    if stage == "FINAL_REPORT":
        request = default_request(AgentCommand.FINAL_REPORT)
        config = EmbeddedInput.from_bytes("fixture--expert-config", "application/json",
            json.dumps({"goal": {"evaluation": {"mode": "grounding"}}}).encode())
        inputs = (*request.input_artifacts, config)
        return replace(request, schema_version=16, input_artifacts=inputs,
            immutable_context_refs=tuple(item.ref for item in inputs),
            required_output_schema=required_output_schema(request.command, request.task_context, schema_version=16))
    return _request(AgentCommand(stage), local=local)


@pytest.mark.parametrize("stage", ["FORWARD", "BACKWARD", "SYNTHESIS", "RESULT_ANALYSIS",
    "HYPOTHESIS_UPDATE", "CYCLE_REPORT", "FINAL_REPORT"])
def test_shared_readonly_stage_keeps_tools_and_goal_context_without_design_commands(stage):
    request = _stage_request(stage)
    instruction = json.loads(_prompt(request, tool_workspace=True))["instruction"]
    assert "Write/test source under /workspace" not in instruction
    assert "author its declared files under /workspace" not in instruction
    assert "DESIGN and submit candidate must select" not in instruction
    assert "metrics=[]" not in instruction
    assert "metrics exactly [measurement.metric]" not in instruction
    assert "/workspace remains read-only at this stage; use /tmp for any useful local exploration." in instruction
    assert "Only Policy accepts the complete goal and stops peers." in instruction
    assert "No local C5 result alone proves the whole goal." in instruction
    assert "not a fixed number or duration of test calls" in instruction
    assert FULL_CC_TOOLS == ("Read", "Write", "Edit", "Glob", "Grep", "Bash", "Skill")


@pytest.mark.parametrize("stage", ["FORWARD", "BACKWARD"])
def test_shared_pincer_argument_returns_only_its_actual_proposal_fields(stage):
    request = _stage_request(stage)
    instruction = json.loads(_prompt(request, tool_workspace=True))["instruction"]
    assert "This proposal returns only task_kind, summary and supporting_refs inside typed_candidate; do not emit experiment-design fields." in instruction
    assert "measurement=null" not in instruction
    candidate = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]
    assert candidate["additionalProperties"] is False
    assert set(candidate["properties"]) == set(candidate["required"]) == {
        "task_kind", "summary", "supporting_refs"}


def test_shared_synthesis_declares_optional_measurement_without_authoring_it_now():
    request = _stage_request("SYNTHESIS")
    instruction = full_cc_instruction(request)
    assert "SYNTHESIS may declare a local C5 measurement or measurement=null" in instruction
    assert "DESIGN and submit must preserve its exact measurement, including null" in instruction
    assert "Declare the hypothesis now; implementation and parser authoring belong to later DESIGN/execution nodes." in instruction
    batch = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]
    assert set(batch["properties"]) == {"schema_version", "hypotheses"}
    candidate = batch["properties"]["hypotheses"]["items"]["properties"]["candidate"]
    assert "measurement" in candidate["properties"]
    assert "metrics" not in candidate["properties"]


@pytest.mark.parametrize("stage", ["EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"])
@pytest.mark.parametrize("local", [False, True])
def test_shared_coding_stages_keep_authoring_and_separate_submit_from_final_response(stage, local):
    request = _stage_request(stage, local=local)
    instruction = full_cc_instruction(request)
    assert "Write/test source under /workspace" in instruction
    assert "author its declared files under /workspace" in instruction
    assert "metrics exactly [measurement.metric]" in instruction and "metrics=[]" in instruction
    assert "DESIGN and submit must preserve its exact measurement, including null" in instruction
    assert "No local C5 result alone proves the whole goal." in instruction
    candidate = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]
    if stage == "EXPERIMENT_EXECUTION":
        assert "The MCP submit candidate is an ExperimentSpecCandidate; the final JSON object is the execution response, not that candidate." in instruction
        assert "Keep task_kind, summary and supporting_refs in the final execution typed_candidate." in instruction
        assert set(candidate["properties"]) == {"task_kind", "summary", "supporting_refs"}
        assert "submit_experiment" in instruction and "wait_experiment" in instruction
    else:
        assert "ExperimentSpecCandidate: include its required method and omit supporting_refs." in instruction
        assert "method" in candidate["required"] and "supporting_refs" not in candidate["properties"]
