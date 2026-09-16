"""Explain the existing shared-goal measurement choice at its actual stage."""

from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.claude_agent_host import _output_schema, _prompt
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_instruction
from tests.runtime.test_claude_agent_host import _claude_pincer_request
from tests.runtime.test_claude_measurement_capture import _deferred
from tests.runtime.test_claude_shared_acceptance_g3 import _local_measurement


def _request(command, *, local=True):
    base = (_claude_pincer_request(ResearchNodeSubkind.SYNTHESIS)
            if command is AgentCommand.HYPOTHESIS_PROPOSAL else default_request(command))
    request = _deferred(base)
    inputs = []
    for item in request.input_artifacts:
        if item.ref.endswith("--expert-config"):
            value = json.loads(item.content_bytes())
            value["goal"] = {"evaluation": {"mode": "grounding"}}
            if "required_experiment_spec" in value:
                measure = _local_measurement().to_dict() if local else None
                value["required_experiment_spec"].update(measurement=measure,
                    metric=None if measure is None else measure["metric"])
            item = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(value).encode())
        inputs.append(item)
    return replace(request, schema_version=16, input_artifacts=tuple(inputs),
        immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(command, request.task_context, schema_version=16))


@pytest.mark.parametrize("command", [AgentCommand.HYPOTHESIS_PROPOSAL,
    AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION])
def test_shared_prompt_locates_optional_c5_choice_before_design_and_submission(command):
    request = _request(command)
    instruction = full_cc_instruction(request)
    assert "SYNTHESIS may declare a local C5 measurement or measurement=null" in instruction
    assert "Once SYNTHESIS is accepted" in instruction
    assert "DESIGN and submit must preserve its exact measurement, including null" in instruction
    assert "GOAL scope does not remove an already selected local C5 measurement" in instruction
    if command is AgentCommand.HYPOTHESIS_PROPOSAL:
        assert "metrics exactly [measurement.metric]" not in instruction
    else:
        assert "metrics exactly [measurement.metric]" in instruction
    assert "a revised hypothesis/new cycle" in instruction
    assert instruction in json.loads(_prompt(request, tool_workspace=True))["instruction"]


def test_shared_prompt_distinguishes_independent_local_and_common_parser_contracts():
    instruction = full_cc_instruction(_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT))
    assert "local C5 parser" in instruction and "author its declared files under /workspace" in instruction
    assert "execute them under /input" in instruction
    assert "only metric, value and optional baseline" in instruction
    assert "common parser retains the accepted read-only /acceptance source" in instruction
    assert "measurements and checks" in instruction
    assert "These are separate parser outputs" in instruction


@pytest.mark.parametrize("local", [False, True])
def test_shared_design_prompt_uses_existing_schema_without_changing_selected_measurement(local):
    request = _request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, local=local)
    instruction = full_cc_instruction(request)
    schema = _output_schema(request, tool_workspace=True)
    candidate = schema["properties"]["typed_candidate"]
    assert "required method" in instruction and "omit supporting_refs" in instruction
    assert "declared_effects is a top-level sibling of typed_candidate" in instruction
    assert "method" in candidate["required"] and "supporting_refs" not in candidate["properties"]
    assert "declared_effects" in schema["required"] and "declared_effects" not in candidate["properties"]
    if local:
        assert candidate["properties"]["measurement"] == {"const": _local_measurement().to_dict()}
        assert candidate["properties"]["metrics"] == {"const": [_local_measurement().to_dict()["metric"]]}
    else:
        assert {"type": "null"} in candidate["properties"]["measurement"]["anyOf"]
        assert candidate["properties"]["metrics"]["minItems"] == 0


def test_nonshared_measurement_prompt_keeps_its_existing_scientific_contract():
    request = _deferred(default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT))
    instruction = full_cc_instruction(request)
    assert "not a preset metric" in instruction
    assert "GOAL scope does not remove" not in instruction
    assert "measurement=null for a GOAL-only" not in instruction


def test_execution_final_reply_keeps_its_own_expert_loop_schema():
    request = _request(AgentCommand.EXPERIMENT_EXECUTION)
    instruction = full_cc_instruction(request)
    candidate = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]
    assert "supporting_refs" in candidate["required"]
    assert "method" not in candidate["properties"]
    assert "omit supporting_refs" not in instruction
    assert "ExperimentSpecCandidate: include its required method" not in instruction
    assert "common parser output is measurements/checks" in instruction
