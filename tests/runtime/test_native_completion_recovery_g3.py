"""Real CLI/Host corrects a changed final message after an accepted probe."""

import json
import socket
import sqlite3

from hypoloom.domain import AgentCommand
from hypoloom.native_output import call_validator
from hypoloom.profile_v4 import project_profile_v4

import test_claude_file_tool_cli as provider_fixture
import test_full_cc_host_g3 as native
from test_profile_v4 import documents


def test_native_completion_recovers_after_disconnected_probe_and_changed_text(tmp_path, monkeypatch):
    scenario = native._current_host_scenario(tmp_path)
    original_record, original_tools = scenario.record, scenario.tool_calls
    original_host, original_payload = native._new_host, native._pilot_payload
    original_event = provider_fixture._send_event
    hosts, affected, feedback = [], [], []
    prefix_next_text = False

    def new_host(*args, **kwargs):
        host = original_host(*args, **kwargs)
        hosts.append(host)
        return host

    def record(document):
        number = original_record(document)
        if affected and scenario.current.attempt_id == affected[0]:
            message = json.dumps(document["messages"][-1])
            assert "invalid JSON at line 1, column 1" in message
            feedback.append(scenario.current.attempt_id)
        return number

    def tool_calls(number):
        nonlocal prefix_next_text
        calls = original_tools(number)
        request = scenario.current
        if request.command is AgentCommand.ADJUDICATE and not affected:
            assert len(calls) == 1 and calls[0][1] == "StructuredOutput"
            validator = hosts[0].running_agents[request.attempt_id]._output_validator
            # Reproduce the abandoned connection and earlier direct validation
            # through the actual invocation socket. Only the provider is scripted.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(2)
                peer.connect(str(validator.socket_path))
                peer.sendall(b'{"session_id":')
                peer.shutdown(socket.SHUT_WR)
                assert json.loads(peer.makefile("rb").readline())["ok"] is False
            assert validator.snapshot()["call_count"] == 0
            candidate = provider_fixture.displayed_candidate(scenario.requests[-1], calls[0][2])
            assert call_validator(str(validator.socket_path), session_id=validator.session_id,
                last_assistant_message=json.dumps(candidate), max_request_bytes=validator.max_request_bytes,
                timeout_seconds=5) == {}
            affected.append(request.attempt_id)
            prefix_next_text = True
        return calls

    def send_event(handler, event, data):
        nonlocal prefix_next_text
        if prefix_next_text and event == "content_block_delta" and data["delta"]["type"] == "text_delta":
            data = {**data, "delta": {**data["delta"],
                "text": "The validated object follows. " + data["delta"]["text"]}}
            prefix_next_text = False
        original_event(handler, event, data)

    def payload(*args):
        value = original_payload(*args)
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
    monkeypatch.setattr(native, "_new_host", new_host)
    monkeypatch.setattr(native, "_pilot_payload", payload)
    monkeypatch.setattr(provider_fixture, "_send_event", send_event)
    native._exercise_full_cc_host(tmp_path)
    assert feedback == affected and len(affected) == 1
    with sqlite3.connect(f"file:{tmp_path / 'state/state.sqlite'}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT terminal_reason, observation_json FROM execution_attempts WHERE attempt_id=?",
                                  (affected[0],)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "SUCCEEDED"
    observation = json.loads(rows[0][1])
    correction = observation["diagnostics"]["structured_output_correction"]
    assert correction["accepted"] and correction["status"] == "recovered"
    assert correction["rejected_count"] == 1 and correction["call_count"] == 3
    assert "error" not in correction
    assert [item["status"] for item in correction["rounds"]] == ["accepted", "rejected", "accepted"]
    assert correction["rounds"][1]["validation_stage"] == "json_decode"
    (tmp_path / "completion-recovery-evidence.json").write_text(json.dumps({
        "attempt_id": affected[0], "native_stop": correction}, indent=2))
