from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hypoloom.agent_capabilities import (
    AGENT_CAPABILITY_MATRIX,
    AgentCapabilities,
    capabilities_for,
)
from hypoloom.domain import AgentCommand
from hypoloom.execution_workspace import EXECUTION_WORKSPACE_VERSION
from hypoloom.git_authoring_workspace import (
    GIT_AUTHORING_WORKSPACE_VERSION,
)


def test_matrix_exhaustively_covers_the_closed_agent_command_union() -> None:
    assert set(AGENT_CAPABILITY_MATRIX) == set(AgentCommand)
    assert {
        command: capabilities_for(command) for command in AgentCommand
    } == dict(AGENT_CAPABILITY_MATRIX)

    with pytest.raises(TypeError, match="AgentCommand"):
        capabilities_for("DRAFT_EXPERIMENT_SPEC")  # type: ignore[arg-type]


def test_retained_design_profile_preserves_its_original_projection() -> None:
    design = capabilities_for(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, schema_version=10)
    assert design.workspace == GIT_AUTHORING_WORKSPACE_VERSION
    assert design.built_in_tools == ("Read", "Glob", "Grep", "Edit", "Write")
    legacy = capabilities_for(AgentCommand.DRAFT_EXPERIMENT_SPEC, schema_version=10)
    assert legacy.workspace == EXECUTION_WORKSPACE_VERSION
    assert legacy.built_in_tools == design.built_in_tools

    for command in set(AgentCommand) - {
        AgentCommand.DRAFT_EXPERIMENT_SPEC,
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    }:
        capabilities = capabilities_for(command, schema_version=10)
        assert capabilities.workspace is None
        assert capabilities.built_in_tools == ()
        assert capabilities.profile not in {design.profile, legacy.profile}


def test_only_grounding_and_literature_receive_host_mediated_retrieval() -> None:
    literature_commands = {
        AgentCommand.GROUNDING,
        AgentCommand.LITERATURE_REVIEW,
    }
    for command in AgentCommand:
        capabilities = capabilities_for(command)
        expected = command in literature_commands
        assert capabilities.network is expected
        assert capabilities.literature is expected


def test_legacy_toolless_commands_remain_toolless() -> None:
    for command in (
        AgentCommand.PROPOSE_HYPOTHESIS,
        AgentCommand.ANSWER_RENDEZVOUS,
    ):
        capabilities = capabilities_for(command, schema_version=10)
        assert capabilities.workspace is None
        assert capabilities.built_in_tools == ()


def test_full_cc_every_node_has_tests_tools_and_persistent_context() -> None:
    for command, capabilities in AGENT_CAPABILITY_MATRIX.items():
        assert capabilities.profile == "agent-full-cc/1"
        assert capabilities.built_in_tools == (("Read", "Glob", "Grep") if command is AgentCommand.PAPER_READ
            else ("Read", "Write", "Edit", "Glob", "Grep", "Bash", "Skill"))
        assert capabilities.workspace == "chain-session/1"
        # Tests run through local Bash, not the obsolete two-call test broker.
        assert capabilities.test_broker is False
        assert capabilities.bash is (command is not AgentCommand.PAPER_READ)
        assert capabilities.web is False
        assert capabilities.nested_agents is False
        assert capabilities.mcp_servers == (("host-tools",) if command is AgentCommand.EXPERIMENT_EXECUTION else ())
        assert capabilities.plugins == ()
        assert capabilities.session_persistence is True
        assert capabilities.execution_owner == "HOST_RUNNER"


def test_capability_values_and_matrix_are_immutable() -> None:
    capabilities = capabilities_for(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    with pytest.raises(FrozenInstanceError):
        capabilities.network = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        AGENT_CAPABILITY_MATRIX[AgentCommand.DRAFT_EXPERIMENT_SPEC] = AgentCapabilities(
            profile="forged/1",
            workspace=None,
            built_in_tools=("Bash",),
            network=True,
            literature=True,
            test_broker=True,
            bash=True,
            web=True,
            nested_agents=True,
            mcp_servers=("ambient",),
            plugins=("ambient",),
            session_persistence=True,
            execution_owner="AGENT",
        )


def test_grounding_web_projection_matches_versioned_host_mcp():
    for version in (13, 14):
        current = capabilities_for(AgentCommand.GROUNDING, schema_version=version)
        assert current.web is True and current.network is True
        assert current.mcp_servers == ("host-tools",)
    for version in (10, 11, 12):
        assert capabilities_for(AgentCommand.GROUNDING, schema_version=version).web is False


def test_native_expert_plugin_projection_does_not_claim_grounding_plugin():
    assert capabilities_for(AgentCommand.CYCLE_REPORT, schema_version=12).plugins == ("host-owned-expert",)
    assert capabilities_for(AgentCommand.CYCLE_REPORT, schema_version=11).plugins == ()
    assert capabilities_for(AgentCommand.GROUNDING, schema_version=13).plugins == ()
