"""Real CLI corrects a contradictory verdict before closing its one Attempt."""
from copy import deepcopy
import json
import sqlite3

from hypoloom.domain import AgentCommand
import test_claude_measurement_g3 as measurement_g3


class AdjudicationRepairScenario(measurement_g3._DeclaredScenario):
    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.feedback = []

    def record(self, document):
        number = super().record(document)
        request = self.current
        if request.command is AgentCommand.ADJUDICATE and self.steps[request.attempt_id] == 2:
            message = json.dumps(document["messages"][-1])
            for expected in ("SUCCESS", "expected_status", "verified", "actual_status", "refuted"):
                if expected not in message:
                    self.errors.append("Missing actionable native adjudication feedback: " + message[:1500])
            self.feedback.append(request.attempt_id)
        return number

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        request = self.current
        if request.command is AgentCommand.ADJUDICATE and self.steps[request.attempt_id] == 1:
            identity, name, output = calls[0]
            assert name == "StructuredOutput"
            output = deepcopy(output)
            assert output["typed_candidate"]["verdicts"][0]["status"] == "verified"
            output["typed_candidate"]["verdicts"][0]["status"] = "refuted"
            return ((identity, name, output),)
        return calls


def test_native_adjudication_corrects_status_without_spending_host_retry(tmp_path, monkeypatch):
    from hypoloom.profile_v4 import project_profile_v4
    original = measurement_g3._pilot_payload

    def payload(*args):
        value = original(*args)
        settings = project_profile_v4(**measurement_g3.documents()).settings
        settings["experts"]["experts"][0]["name"] = value["research_contract"]["roles"][0]["id"]
        settings["experts"]["defaults"].update(max_hypotheses_per_round=1, model=measurement_g3._MODEL)
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 1}
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["output_correction"] = {"max_failed_validations": 2}
        value["research_contract"]["methodology"]["controls"]["product"] = settings
        return value

    monkeypatch.setattr(measurement_g3, "_pilot_payload", payload)
    scenario = measurement_g3._run_real_claude_measurement_g3(tmp_path, "direct",
        scenario_factory=AdjudicationRepairScenario)
    with sqlite3.connect((tmp_path / "state/state.sqlite").as_uri() + "?mode=ro", uri=True) as connection:
        observations = [json.loads(row[0]) for row in connection.execute(
            "SELECT observation_json FROM execution_attempts WHERE kind='AGENT'")]
    adjudications = [item for item in observations if item["request"]["command"] == "ADJUDICATE"]
    assert len(adjudications) == len(scenario.feedback) == 1
    observation = adjudications[0]
    assert observation["request"]["attempt_ordinal"] == 1
    assert observation["response"]["status"] == "SUCCEEDED"
    correction = observation["diagnostics"]["structured_output_correction"]
    assert correction["accepted"] and correction["status"] == "recovered"
    assert correction["rejected_count"] == 1 and correction["call_count"] == 2
    assert correction["rounds"][0]["validation_stage"] == "candidate_validation"
    (tmp_path / "adjudication-recovery.json").write_text(json.dumps(correction, indent=2))
