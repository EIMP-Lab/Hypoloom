"""Coding-node instructions must describe the actual raw and parser sandboxes."""

from dataclasses import replace
import json

import pytest

from hypoloom.claude_agent_host import _prompt
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from tests.runtime.test_claude_measurement_capture import _deferred
from tests.runtime.test_shared_acceptance_prompt import _request


_TARGET_AUTHORING_INSTRUCTIONS = (
    "Edit the actual target source files under /workspace and test those edited files.",
    "The Host generates implementation.patch from cumulative changes against the pinned base; do not create, write, copy, or regenerate /workspace/implementation.patch yourself.",
    "A passing scratch-only test does not validate the submitted target.",
)


@pytest.fixture(params=["shared-local", "shared-goal-only", "c5", "preset"])
def coding_request(request):
    def make(command):
        if request.param.startswith("shared-"):
            return _request(command, local=request.param == "shared-local")
        # The prompt embeds the full output schema, so even wording fixtures
        # need the frozen experiment config; only C5 leaves its metric deferred.
        base = _deferred(default_request(command), deferred=request.param == "c5")
        return replace(base, schema_version=14)

    return make


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION])
def test_complete_coding_prompt_preserves_read_root_json_contract(coding_request, command):
    instruction = json.loads(_prompt(coding_request(command), tool_workspace=True,
        git_authoring=True))["instruction"]
    assert "HYPOLOOM_READ_ROOTS is a JSON array of absolute path strings" in instruction
    assert "json.loads(os.environ['HYPOLOOM_READ_ROOTS'])" in instruction
    assert "HYPOLOOM_READ_ROOT_MAP is an optional JSON object" in instruction
    assert "json.loads(os.environ.get('HYPOLOOM_READ_ROOT_MAP', '{}'))" in instruction
    assert "Keep the JSON encoding of HYPOLOOM_READ_ROOTS in local tests" in instruction
    assert "a plain path or a colon-separated string" in instruction


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION])
def test_complete_coding_prompt_distinguishes_raw_output_from_parser_input(coding_request, command):
    instruction = json.loads(_prompt(coding_request(command), tool_workspace=True,
        git_authoring=True))["instruction"]
    assert "The raw experiment Process starts in /output" in instruction
    assert "Write generated outputs relative to the current working directory" in instruction
    assert "raw_artifacts are paths relative to that output directory" in instruction
    assert "/raw is only the subsequent parser's read-only view" in instruction
    assert "Use a fresh /tmp output directory as the cwd for local tests" in instruction
    assert "HYPOLOOM_MEASUREMENT_RAW_ROOT and HYPOLOOM_MEASUREMENT_RESULT_PATH are parser-only" in instruction


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION])
def test_complete_coding_prompt_captures_actual_target_edits_not_manual_patch(coding_request, command):
    request = coding_request(command)
    assert request.schema_version >= 14
    instruction = json.loads(_prompt(request, tool_workspace=True, git_authoring=True))["instruction"]
    assert "invoke /workspace source explicitly" in instruction
    assert "only the Host may write Git index/config/commits" in instruction
    assert "Profile protected_paths within /workspace remain protected" in instruction
    assert "The runtime and data directories are mounted read-only" in instruction
    missing = [phrase for phrase in _TARGET_AUTHORING_INSTRUCTIONS if phrase not in instruction]
    assert missing == []


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION])
def test_local_test_keeps_formal_entrypoint_imports_and_checkpoint_writer(coding_request, command):
    instruction = json.loads(_prompt(coding_request(command), tool_workspace=True,
        git_authoring=True))["instruction"]
    assert "Test the exact execution_argv entrypoint" in instruction
    assert "Do not derive target imports from the standalone script's __file__" in instruction
    assert "model registration, a tiny optimizer step, checkpoint save/reload" in instruction
    assert "A PYTHONPATH override that bypasses the wrapper" in instruction
    assert "Check each child CLI's required arguments" in instruction
    assert "real metric serialization and downstream loading" in instruction
    assert "including seed and extension placement" in instruction
    assert "leave device-dependent checks to the Host-granted Process" in instruction


@pytest.mark.parametrize("command", [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION])
def test_retained_aci13_coding_prompt_does_not_claim_current_target_authoring(command):
    request = replace(_deferred(default_request(command), deferred=False), schema_version=13)
    instruction = json.loads(_prompt(request, tool_workspace=True, git_authoring=True))["instruction"]
    assert all(phrase not in instruction for phrase in _TARGET_AUTHORING_INSTRUCTIONS)
    if command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
        assert "Your implementation.patch is NOT applied to your own experiment's sandbox" in instruction
