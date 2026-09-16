"""Debate reuses the frozen Profile literature capability, never Judge access."""
import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.agent_capabilities import capabilities_for
from hypoloom.domain import AgentCommand
from hypoloom.full_cc_runtime import host_tool_configuration
from hypoloom.profile_v4 import request_literature_tools
from test_debate_aci14 import debate_request, judge_request


def configured(request, setting):
    config = EmbeddedInput.from_bytes("debate--expert-config", "application/json",
        json.dumps({"node_runtime": {"literature": setting}}).encode())
    return replace(request, input_artifacts=(*request.input_artifacts, config))


@pytest.mark.parametrize("setting,expected", [({}, ("web_search", "web_fetch")),
    ({"enabled": True, "tools": ["web_fetch"]}, ("web_fetch",)),
    ({"enabled": False}, ()), ({"tools": []}, ())])
def test_debate_uses_exact_frozen_literature_subset(setting, expected):
    request, _ = debate_request()
    request = configured(request, setting)
    assert request_literature_tools(request) == expected
    assert host_tool_configuration(request) == (("literature", expected) if expected else (None, ()))


def test_judge_and_retained_debate_do_not_gain_literature_tools():
    judge, _ = judge_request()
    debate, _ = debate_request()
    assert request_literature_tools(configured(judge, {})) == ()
    assert request_literature_tools(configured(replace(debate, schema_version=13,
        required_output_schema="ExpertLoopCandidate/v1"), {})) == ()


def test_debate_fails_closed_on_invalid_frozen_web_tool():
    request, _ = debate_request()
    with pytest.raises(ValueError, match="literature tools"):
        request_literature_tools(configured(request, {"tools": ["submit_experiment"]}))


def test_debate_capability_projects_expert_plugin_and_brokered_web_only():
    current = capabilities_for(AgentCommand.DEBATE, schema_version=14)
    assert current.plugins == ("host-owned-expert",)
    assert current.web and current.network and current.literature
    assert current.mcp_servers == ("host-tools",)
    assert not capabilities_for(AgentCommand.DEBATE_JUDGE, schema_version=14).web
    assert not capabilities_for(AgentCommand.DEBATE, schema_version=13).web
