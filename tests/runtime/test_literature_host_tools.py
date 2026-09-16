import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError, with_output_digest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.grounding_web import GroundingWeb
from hypoloom.oracle import Oracle
from tests.runtime.test_expert_batch_host import BatchAgent, _complete, batch_host  # noqa: F401
from tests.runtime.test_grounding_web import DOC, SEARCH, Transport, _response


class LiteratureAgent(BatchAgent):
    def __init__(self, host, repair):
        super().__init__()
        self.host, self.repair, self.main_id = host, repair, None

    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.LITERATURE_REVIEW: return response
        if not isinstance(request.task_context, ResearchNodeTask):
            with pytest.raises(AgentACIError, match="Grounding invocation"):
                self.host.handle_agent_tool(request.attempt_id, "web_fetch", {"url": DOC})
            return response
        if request.attempt_ordinal == 1:
            found = self.host.handle_agent_tool(request.attempt_id, "web_search", {"query": "project evaluation"})
            assert found["results"][0]["url"] == DOC
            fetched = self.host.handle_agent_tool(request.attempt_id, "web_fetch", {"url": DOC})
            assert "measured literature nonce" in fetched["text"]
        with pytest.raises(AgentACIError, match="execution node"):
            self.host.handle_agent_tool(request.attempt_id, "submit_experiment", {})
        if request.task_context.key.subkind.value == "MAIN":
            self.main_id = request.command_id
            source = "https://example.org/invented" if self.repair and request.attempt_ordinal == 1 else DOC
            return with_output_digest(replace(response, typed_candidate={**response.typed_candidate, "web_sources": [source]}))
        return response


@pytest.mark.parametrize("repair", [False, True])
def test_actual_host_literature_web_and_same_task_citation_repair_reach_cpu_oracle(batch_host, repair):  # noqa: F811
    host = batch_host
    host.grounding_web = GroundingWeb(Transport({
        SEARCH: _response(SEARCH, b'<a class="result__a" href="https://example.org/docs">Prior work</a>'),
        DOC: _response(DOC, b'<title>Prior work</title><p>measured literature nonce</p>')}))
    host.agent = agent = LiteratureAgent(host, repair)
    _complete(host)
    attempts = host.store.list_attempts(command_id=agent.main_id)
    assert [row["terminal_reason"] for row in attempts] == (["FAILED", "SUCCEEDED"] if repair else ["SUCCEEDED"])
    assert json.loads(attempts[-1]["observation_json"])["response"]["typed_candidate"]["web_sources"] == [DOC]
    if repair:
        assert "web_tool_results" not in json.loads(attempts[-1]["observation_json"])
        assert json.loads(attempts[0]["observation_json"])["failure_code"] == "invalid_candidate"
    report = Oracle(host.store.database_path, host.publisher.root).audit("batch-host")
    assert report.ok, report.to_dict()
