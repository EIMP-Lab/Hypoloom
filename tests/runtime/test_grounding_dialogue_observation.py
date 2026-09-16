import json
from types import SimpleNamespace

from hypoloom.observation_api import product_events
from hypoloom.research_export import _background_markdown
from tests.runtime.test_grounding_dialogue_aci import QUESTION, exchange


def test_clarification_is_not_exported_as_successful_research_background():
    request, response = exchange()
    snapshot = SimpleNamespace(run_id=request.run_id, observed_at="2026-09-07T00:00:00Z", status={"run_revision": 4})
    row = {"run_id": request.run_id, "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
           "attempt_id": request.attempt_id, "workflow_id": request.owner_workflow_id,
           "command_id": request.command_id, "attempt_ordinal": request.attempt_ordinal,
           "observation_json": json.dumps({"request": request.to_dict(), "response": response.to_dict()})}
    rendered = _background_markdown(snapshot, [row])
    assert "not completed research background" in rendered
    assert "## Successful GROUNDING" not in rendered
    assert QUESTION["question"] not in rendered


def test_dialogue_events_use_existing_transition_keys_without_sending_answer_text():
    rows = [{"workflow_id": "run", "run_id": "run", "workflow_kind": "RUN", "revision": index,
             "previous_state": before, "state": after, "created_at": "2026-09-07T00:00:00Z",
             "command_id": f"command-{index}", "command_type": command,
             "payload": {"kind": "grounding_clarification", "ordinal": 1,
                         "decision_id": "question-1", "human_decision_command": action}}
            for index, before, after, command, action in [
                (4, "ACTIVE", "WAITING_HUMAN", "REQUEST_HUMAN", "OPEN_DECISION"),
                (5, "WAITING_HUMAN", "ACTIVE", "RESOLVE_CONTINUE", "RESOLVE")]]
    events = product_events("run", [], rows, [])
    assert [item["type"] for item in events] == ["grounding_question", "grounding_answered"]
    assert [item["event_id"] for item in events] == ["grounding-dialogue:run:4", "grounding-dialogue:run:5"]
    assert all(item["payload"]["decision_id"] == "question-1" and "answer" not in item["payload"] for item in events)
