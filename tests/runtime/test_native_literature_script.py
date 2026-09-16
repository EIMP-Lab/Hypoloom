"""Native-script input selection follows actual fact identity, not a suffix."""
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.expert_loop import ResearchNodeSubkind
from test_agent_aci import _pincer_request
from test_claude_literature_host_g3 import LiteratureScenario


@pytest.mark.parametrize("reference", ["actual-run--fact-goal", "actual-goal-primary-key"])
def test_backward_reads_actual_goal_fact_without_requiring_a_generated_ref_suffix(tmp_path, reference):
    source = EmbeddedInput.from_bytes(reference, "application/json",
        b'{"fact_id":"actual-goal-primary-key","fact_type":"GoalDefined","payload":{}}')
    request = replace(_pincer_request(ResearchNodeSubkind.BACKWARD), input_artifacts=(source,),
        immutable_context_refs=(reference,))
    scenario = LiteratureScenario(tmp_path)
    scenario.current = request
    scenario.steps[request.attempt_id] = 1
    assert scenario._unscoped_tool_calls(1) == (("hypothesis_source", "Read", {"file_path": "/inputs/0000.txt"}),)
