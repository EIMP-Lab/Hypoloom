"""Real CC Stop rejects early completion, then waits, repairs and closes CPU work."""

import json
import sqlite3

from hypoloom.domain import AgentCommand
from hypoloom.profile_v4 import project_profile_v4

import test_full_cc_host_g3 as native
from test_profile_v4 import documents


def test_native_execution_corrects_early_completion_before_publication(tmp_path, monkeypatch):
    scenario = native._current_host_scenario(tmp_path)
    original_record, original_tools = scenario.record, scenario.tool_calls
    original_payload = native._pilot_payload
    rejected = []
    pending = False

    def record(document):
        nonlocal pending
        if pending:
            request = native._request_from_provider(document)
            assert request.command is AgentCommand.EXPERIMENT_EXECUTION
            messages = json.dumps(document["messages"])
            assert "execution completion requires its terminal submitted experiment" in messages
            assert "wait_experiment" in messages
            rejected.append(request.attempt_id)
            pending = False
            scenario.requests.append(document)
            return len(scenario.requests)
        return original_record(document)

    def tool_calls(number):
        nonlocal pending
        request = scenario.current
        if (request is not None and request.command is AgentCommand.EXPERIMENT_EXECUTION
                and scenario.steps[request.attempt_id] == 8 and not rejected):
            # The second real Process is still running after its accepted
            # replacement submission. Its predecessor is already terminal.
            pending = True
            response = scenario.responses[request.attempt_id]
            return (("premature_completion", "StructuredOutput", {
                "typed_candidate": {**response.typed_candidate,
                    "summary": "The replacement was submitted and is still running."},
                "declared_effects": [{"effect": item.effect} for item in response.declared_effects]}),)
        # Exercise the production default bounded wait. The base scenario's
        # ten-second probe can legitimately return before this longer Process.
        return tuple((identity, tool, {"exp_id": arguments["exp_id"]})
            if tool == "mcp__host-tools__wait_experiment" else (identity, tool, arguments)
            for identity, tool, arguments in original_tools(number))

    def payload(*args):
        value = original_payload(*args)
        value["execution"]["delay_seconds"] = 16
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = value["research_contract"]["roles"][0]["id"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 1
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 1}
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["output_correction"] = {"max_failed_validations": 3}
        value["research_contract"]["methodology"]["controls"]["product"] = settings
        return value

    scenario.record, scenario.tool_calls = record, tool_calls
    monkeypatch.setattr(native, "_current_host_scenario", lambda _: scenario)
    monkeypatch.setattr(native, "_pilot_payload", payload)
    native._exercise_full_cc_host(tmp_path)
    assert rejected == scenario.execution_attempt_ids
    with sqlite3.connect(f"file:{tmp_path / 'state' / 'state.sqlite'}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT terminal_reason, observation_json FROM execution_attempts WHERE attempt_id=?",
                                  (rejected[0],)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "SUCCEEDED"
    observed = json.loads(rows[0][1])
    correction = observed["diagnostics"]["structured_output_correction"]
    assert correction["accepted"] and correction["status"] == "recovered"
    assert correction["rejected_count"] == 1 and correction["call_count"] == 2
    assert "terminal submitted experiment" in correction["rounds"][0]["validation_error"]
    (tmp_path / "execution-completion-evidence.json").write_text(json.dumps({
        "attempt_id": rejected[0], "native_stop": correction}, indent=2))
