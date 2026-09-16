"""Shared-goal parser recovery follows its existing preregistration boundary."""

import json

import pytest

from hypoloom.claude_agent_host import _output_schema, _prompt
from hypoloom.domain import AgentCommand
from tests.runtime.test_shared_acceptance_prompt import _request


def _instruction(command, *, local=True):
    return json.loads(_prompt(_request(command, local=local), tool_workspace=True,
        git_authoring=True))["instruction"]


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION])
def test_shared_selected_c5_tests_own_contract_before_preregistration(command):
    instruction = _instruction(command)
    assert "Before first preregistration, test each selected parser separately" in instruction
    assert "required_experiment_spec.result_contract" in instruction
    assert "A passing common evaluator does not validate the local C5 output." in instruction
    assert "only metric, value and optional baseline" in instruction
    assert "only measurements and checks" in instruction


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION])
def test_shared_coding_instructions_freeze_local_parser_bytes(command):
    instruction = _instruction(command)
    assert "First preregistration freezes the local measurement declaration and parser bytes" in instruction
    assert "Operational repairs preserve both" in instruction
    assert "changing either requires a revised hypothesis/new cycle" in instruction
    assert "DESIGN and submit must preserve its exact measurement, including null" in instruction


def test_shared_execution_closes_unrepairable_parser_failure_with_its_actual_response():
    request = _request(AgentCommand.EXPERIMENT_EXECUTION)
    instruction = _instruction(request.command)
    assert "If submit reports measurement_frozen" in instruction
    assert "finish this EXECUTION with the factual failure in summary and exact allowed supporting_refs" in instruction
    assert "Do not repeatedly edit the frozen parser or resubmit it under the same hypothesis" in instruction
    assert "The Host decides whether to advance to a revised hypothesis/new cycle within admitted budgets" in instruction
    assert "Raw-source repairs that preserve the declaration and parser bytes still use this session's normal replacement submission" in instruction
    assert "if repair preserves the frozen declaration and parser bytes" in instruction
    assert "On an operational failure, inspect its log, edit/test here, and resubmit" not in instruction
    candidate = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]
    assert set(candidate["properties"]) == set(candidate["required"]) == {
        "task_kind", "summary", "supporting_refs"}


def test_shared_goal_only_parser_test_does_not_require_inventing_a_local_measurement():
    instruction = _instruction(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, local=False)
    assert "For a non-null local measurement, check its JSON against required_experiment_spec.result_contract" in instruction
    assert "Without local measurement use measurement=null and metrics=[]" in instruction


def test_shared_design_does_not_receive_execution_close_instruction():
    instruction = _instruction(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    assert "finish this EXECUTION with the factual failure" not in instruction
