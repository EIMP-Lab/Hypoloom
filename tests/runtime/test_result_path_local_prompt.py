"""Local coding checks must preserve the formal Process's result-path semantics."""

from dataclasses import replace
import json

import pytest

from hypoloom.claude_agent_host import _prompt
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from tests.runtime.test_claude_measurement_capture import _deferred
from tests.runtime.test_shared_acceptance_prompt import _request


@pytest.mark.parametrize("mode", ("preset", "c5", "shared-local", "shared-goal-only"))
@pytest.mark.parametrize("command", (AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION))
def test_local_checks_preserve_relative_result_path_and_real_writer(mode, command):
    if mode.startswith("shared-"):
        request = _request(command, local=mode == "shared-local")
    else:
        request = replace(_deferred(default_request(command), deferred=mode == "c5"),
            schema_version=14)
    instruction = json.loads(_prompt(request, tool_workspace=True,
        git_authoring=True))["instruction"]
    assert "Use a fresh /tmp output directory as the cwd for local tests" in instruction
    assert "When the rendered result contract uses HYPOLOOM_RESULT_PATH" in instruction
    assert "keep HYPOLOOM_RESULT_PATH=result.json in local tests" in instruction
    assert "same output-writing function" in instruction
    assert "including parent-directory handling" in instruction
    assert "Path(result_path).parent.mkdir(parents=True, exist_ok=True)" in instruction
    assert "check result.json in that cwd" in instruction
    assert "A separate scratch-only serializer does not test the runtime writer" in instruction
    assert "substitute only the computation with a bounded CPU fixture" in instruction
    # Local output advice must keep the distinct parser environment untouched.
    assert "HYPOLOOM_MEASUREMENT_RAW_ROOT and HYPOLOOM_MEASUREMENT_RESULT_PATH are parser-only" in instruction
