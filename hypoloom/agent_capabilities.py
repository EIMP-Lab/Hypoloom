"""Pure capability projection for the current closed Agent command set.

The matrix describes what one Agent invocation may use.  It does not grant a
capability, launch work, own execution, or persist workflow state.  Provider
transport connectivity is deliberately outside the ``network`` flag: network
here means an Agent-visible retrieval capability such as Web or literature I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from .domain import AgentCommand
from .execution_workspace import EXECUTION_WORKSPACE_VERSION
from .git_authoring_workspace import GIT_AUTHORING_WORKSPACE_VERSION
from .full_cc_runtime import FULL_CC_PROFILE, FULL_CC_TOOLS


_TOOLLESS_PROFILE = "agent-tool-less/1"
_LITERATURE_PROFILE = "agent-host-mediated-literature/1"
_DESIGN_FILE_TOOLS_PROFILE = "agent-git-authoring-file-tools/1"
_FILE_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write")


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentCapabilities:
    """One immutable, non-authoritative Agent capability projection."""

    profile: str
    workspace: str | None
    built_in_tools: tuple[str, ...]
    network: bool
    literature: bool
    test_broker: bool
    bash: bool
    web: bool
    nested_agents: bool
    mcp_servers: tuple[str, ...]
    plugins: tuple[str, ...]
    session_persistence: bool
    execution_owner: str


_TOOLLESS = AgentCapabilities(
    profile=_TOOLLESS_PROFILE,
    workspace=None,
    built_in_tools=(),
    network=False,
    literature=False,
    test_broker=False,
    bash=False,
    web=False,
    nested_agents=False,
    mcp_servers=(),
    plugins=(),
    session_persistence=False,
    execution_owner="HOST_RUNNER",
)

_HOST_MEDIATED_LITERATURE = AgentCapabilities(
    profile=_LITERATURE_PROFILE,
    workspace=None,
    built_in_tools=(),
    network=True,
    literature=True,
    test_broker=False,
    bash=False,
    web=False,
    nested_agents=False,
    mcp_servers=(),
    plugins=(),
    session_persistence=False,
    execution_owner="HOST_RUNNER",
)

_DESIGN_FILE_TOOLS = AgentCapabilities(
    profile=_DESIGN_FILE_TOOLS_PROFILE,
    workspace=GIT_AUTHORING_WORKSPACE_VERSION,
    built_in_tools=_FILE_TOOLS,
    network=False,
    literature=False,
    test_broker=False,
    bash=False,
    web=False,
    nested_agents=False,
    mcp_servers=(),
    plugins=(),
    session_persistence=False,
    execution_owner="HOST_RUNNER",
)

_LEGACY_DRAFT_FILE_TOOLS = AgentCapabilities(
    profile="agent-progressive-single-file-tools/2",
    workspace=EXECUTION_WORKSPACE_VERSION,
    built_in_tools=_FILE_TOOLS,
    network=False,
    literature=False,
    test_broker=False,
    bash=False,
    web=False,
    nested_agents=False,
    mcp_servers=(),
    plugins=(),
    session_persistence=False,
    execution_owner="HOST_RUNNER",
)


_RETAINED_CAPABILITY_MATRIX: Mapping[AgentCommand, AgentCapabilities] = (
    MappingProxyType(
        {
            AgentCommand.PROPOSE_HYPOTHESIS: _TOOLLESS,
            AgentCommand.DRAFT_EXPERIMENT_SPEC: _LEGACY_DRAFT_FILE_TOOLS,
            AgentCommand.ANSWER_RENDEZVOUS: _TOOLLESS,
            AgentCommand.GROUNDING: _HOST_MEDIATED_LITERATURE,
            AgentCommand.LITERATURE_REVIEW: _HOST_MEDIATED_LITERATURE,
            AgentCommand.HYPOTHESIS_PROPOSAL: _TOOLLESS,
            AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT: _DESIGN_FILE_TOOLS,
            AgentCommand.EXPERIMENT_EXECUTION: _TOOLLESS,
            AgentCommand.RESULT_ANALYSIS: _TOOLLESS,
            AgentCommand.HYPOTHESIS_UPDATE: _TOOLLESS,
            AgentCommand.CYCLE_REPORT: _TOOLLESS,
            AgentCommand.ADJUDICATE: _TOOLLESS,
            AgentCommand.SELF_EVOLVE: _TOOLLESS,
            AgentCommand.GRAPH_EVOLVE: _TOOLLESS,
            AgentCommand.FINAL_REPORT: _TOOLLESS,
            AgentCommand.DEBATE_JUDGE: _TOOLLESS,
            AgentCommand.OBSERVER: _TOOLLESS,
            AgentCommand.DEBATE: _TOOLLESS,
            AgentCommand.LEADER: _TOOLLESS,
            AgentCommand.COMPACT_CONTEXT: _TOOLLESS,
            AgentCommand.PAPER_READ: _TOOLLESS,
        }
    )
)

# ACI 11's default product capability is the same complete CC tool set for
# every node. Mounts and Host service exposure specialize ownership, not tools.
AGENT_CAPABILITY_MATRIX: Mapping[AgentCommand, AgentCapabilities] = MappingProxyType({
    command: replace(old, profile=FULL_CC_PROFILE, workspace="chain-session/1",
        built_in_tools=("Read", "Glob", "Grep") if command is AgentCommand.PAPER_READ else FULL_CC_TOOLS,
        bash=command is not AgentCommand.PAPER_READ, session_persistence=True,
        mcp_servers=("host-tools",) if command is AgentCommand.EXPERIMENT_EXECUTION else ())
    for command, old in _RETAINED_CAPABILITY_MATRIX.items()
})

if set(AGENT_CAPABILITY_MATRIX) != set(AgentCommand):
    raise RuntimeError("Agent capability matrix does not cover the closed command set")


def capabilities_for(command: AgentCommand, *, schema_version: int = 11) -> AgentCapabilities:
    """Derive the exact capability profile for one closed Agent command."""

    if not isinstance(command, AgentCommand):
        raise TypeError("command must be an AgentCommand")
    from .agent_aci import supports_grounding_web, supports_literature_web
    value = (AGENT_CAPABILITY_MATRIX if schema_version >= 11 else _RETAINED_CAPABILITY_MATRIX)[command]
    if schema_version >= 12 and command in {
        AgentCommand.LITERATURE_REVIEW, AgentCommand.HYPOTHESIS_PROPOSAL,
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION,
        AgentCommand.RESULT_ANALYSIS, AgentCommand.HYPOTHESIS_UPDATE, AgentCommand.CYCLE_REPORT, AgentCommand.ADJUDICATE,
        AgentCommand.SELF_EVOLVE,
    }:
        # The actual per-role plugin name/commit is request metadata, not this
        # generic capability category. No marketplace plugin is implied.
        value = replace(value, plugins=("host-owned-expert",))
    if supports_grounding_web(schema_version) and command is AgentCommand.GROUNDING:
        value = replace(value, web=True, network=True, mcp_servers=("host-tools",))
    if schema_version >= 14 and command in {AgentCommand.GRAPH_EVOLVE, AgentCommand.OBSERVER, AgentCommand.FINAL_REPORT, AgentCommand.DEBATE_JUDGE}:
        value = replace(value, plugins=("host-owned-grounding",))
    if supports_literature_web(schema_version) and command is AgentCommand.LITERATURE_REVIEW:
        value = replace(value, web=True, network=True, mcp_servers=("host-tools",))
    if schema_version >= 14 and command is AgentCommand.DEBATE:
        value = replace(value, plugins=("host-owned-expert",), web=True, network=True,
            literature=True, mcp_servers=("host-tools",))
    return value


__all__ = [
    "AGENT_CAPABILITY_MATRIX",
    "AgentCapabilities",
    "capabilities_for",
]
