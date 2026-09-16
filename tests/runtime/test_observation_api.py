from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from hypoloom.observation_api import build_product_observation, product_events, read_experiment_log
from hypoloom.web_observer import WebObserverServer
from test_web_observer import _TOKEN, _create_fresh_run, _request, _row_census, _start, _stop


def test_events_cite_existing_facts_and_report_both_attempt_edges():
    facts = [{"fact_id": "outcome", "fact_type": "CanonicalOutcomeAccepted", "created_at": "2026-09-06", "payload_json": json.dumps({"experiment_id": "e", "outcome": "EXECUTION_FAILURE"})}]
    attempts = [{"attempt_id": "a", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED", "started_at": 1, "ended_at": 2,
                 "observation_json": json.dumps({"request": {"role": "scientist", "command": "CYCLE_REPORT"}})}]
    events = product_events("r", facts, [], attempts)
    assert {item["type"] for item in events} == {"stage_started", "stage_finished", "experiment_failed", "expert_round_done"}
    assert len({item["event_id"] for item in events}) == 4
    assert next(item for item in events if item["type"] == "experiment_failed")["payload"]["fact_id"] == "outcome"


def test_iso_fact_and_epoch_attempt_timestamps_have_one_chronology():
    facts = [{"fact_id": "goal", "fact_type": "GoalDefined", "created_at": "1970-01-01T00:00:02Z", "payload_json": "{}"}]
    attempts = [{"attempt_id": "a", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED", "started_at": 1, "ended_at": 3,
                 "observation_json": json.dumps({"request": {"command": "GROUNDING"}})}]
    assert [item["type"] for item in product_events("r", facts, [], attempts)] == ["stage_started", "run_started", "stage_finished"]


def test_round_completion_and_error_events_have_distinct_delivery_sources():
    attempts = [{"attempt_id": "report", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
                 "started_at": 1, "ended_at": 2, "observation_json": json.dumps({"request": {"role": "a", "command": "CYCLE_REPORT"}})},
                {"attempt_id": "budget", "kind": "AGENT", "state": "CLOSED", "terminal_reason": "FAILED",
                 "started_at": 3, "ended_at": 4, "observation_json": json.dumps({"request": {"role": "b", "command": "HYPOTHESIS_PROPOSAL"},
                     "failure_code": "profile_budget_exhausted"})}]
    events = product_events("r", [], [], attempts)
    assert {item["type"] for item in events} >= {"expert_round_done", "error", "budget_warning"}
    assert len(events) == len({item["event_id"] for item in events})
    assert len(events) == len({(item["source"], item["source_id"], item["revision"]) for item in events})


def test_control_events_report_scope_without_exporting_operator_note_text():
    transitions = [{"workflow_id": "r", "workflow_kind": "RUN", "revision": 3, "command_id": "pause-1", "created_at": 1,
                    "payload": {"participant_control": {"action": "pause", "reason": "operator", "scope": {"role": "a", "component_id": None, "chain": None}}}},
                   {"workflow_id": "r", "workflow_kind": "RUN", "revision": 4, "command_id": "note-1", "created_at": 2,
                    "payload": {"participant_control": {"action": "inject", "text": "private instruction", "reason": "operator", "scope": {"role": "a", "component_id": None, "chain": None}}}}]
    events = product_events("r", [], transitions, [])
    assert [event["type"] for event in events] == ["participant_pause_requested", "operator_note_injected"]
    assert events[1]["payload"]["command_id"] == "note-1"
    assert "private instruction" not in json.dumps(events)


def test_evolution_event_requires_success_and_changed_accepted_git_revision():
    attempts = []
    for number, (reason, before, after) in enumerate([
            ("SUCCEEDED", "a", "b"), ("SUCCEEDED", "a", "a"), ("FAILED", "a", "b")]):
        attempts.append({"attempt_id": str(number), "kind": "AGENT", "state": "CLOSED",
            "terminal_reason": reason, "started_at": number, "ended_at": number + 1,
            "observation_json": json.dumps({"request": {"role": "scientist", "command": "SELF_EVOLVE"},
                "plugin_update": {"base_commit": before, "commit": after}})})
    events = [item for item in product_events("r", [], [], attempts) if item["type"] == "expert_evolved"]
    assert len(events) == 1 and events[0]["payload"]["attempt_id"] == "0"
    assert events[0]["payload"]["plugin_update"] == {"base_commit": "a", "commit": "b"}


def test_final_report_event_is_distinct_from_scientific_verdict_and_requires_success():
    attempts = [{"attempt_id": reason, "kind": "AGENT", "state": "CLOSED", "terminal_reason": reason,
        "started_at": 1, "ended_at": 2, "observation_json": json.dumps({"request": {"command": "FINAL_REPORT"}})}
        for reason in ("SUCCEEDED", "FAILED")]
    events = [item for item in product_events("r", [], [], attempts) if item["type"] == "final_report_ready"]
    assert len(events) == 1 and events[0]["payload"]["attempt_id"] == "SUCCEEDED"


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED"])
def test_run_finished_requires_terminal_workflow_even_without_a_scientific_verdict(state):
    row = {"workflow_id": "r", "workflow_kind": "RUN", "revision": 5, "command_id": "finish",
        "command_type": "FINISH_STOP", "previous_state": "STOPPING", "state": state, "created_at": 3, "payload": {}}
    facts = [{"fact_id": "verdict", "fact_type": "GoalVerdictRecorded", "created_at": 1, "payload": {"verdict": "GOAL_MET"}}]
    before = product_events("r", facts, [], [])
    assert [event["type"] for event in before] == ["goal_verdict_recorded"]
    finished = product_events("r", facts if state == "COMPLETED" else [], [row], [])[-1]
    assert finished["type"] == "run_finished" and finished["payload"]["state"] == state
    assert finished["payload"]["verdict_ref"] == ("verdict" if state == "COMPLETED" else None)
    assert not product_events("r", [], [{**row, "state": "STOPPING"}], [])


def _batch_transitions():
    return [{"workflow_id": "r", "workflow_kind": "RUN", "revision": 1, "command_id": "syn", "created_at": 1,
        "command_type": "ACCEPT_HYPOTHESIS", "payload": {"hypothesis_batch": {"cycle": 1, "chains": {"chain-a": "h1", "chain-b": "h2"}}}},
        {"workflow_id": "r", "workflow_kind": "RUN", "revision": 2, "command_id": "judge", "created_at": 2,
         "command_type": "ACCEPT_ADJUDICATION", "payload": {"role": "scientist", "component_id": "goal",
            "agent_attempt_id": "judge-attempt", "adjudication": {"cycle": 1,
                "selected_tip": {"node_id": "h1", "experiment_id": "e1", "outcome_id": "o1"},
                "pruned_by": [{"loser_hypothesis_id": "h2", "winner_hypothesis_id": "h1",
                    "loser_chain": "chain-b", "winner_chain": "chain-a", "source_attempt_id": "judge-attempt",
                    "debate_id": None, "score_refs": ["o1", "o2"]}]}}}]


def test_batch_graph_renders_control_pruning_without_changing_scientific_assessment():
    from hypoloom.observation_api import product_graph
    hypotheses = [{"hypothesis_id": "h1", "assessment": "SUPPORTED"},
        {"hypothesis_id": "h2", "assessment": "SUPPORTED"}]
    graph = product_graph(hypotheses, _batch_transitions())
    winner, loser = graph["nodes"]
    assert winner["chain"] == "chain-a" and winner["cycle"] == 1 and winner["selected_tip"]
    assert loser["chain"] == "chain-b" and loser["state"] == "PRUNED"
    assert loser["assessment"] == "SUPPORTED" and hypotheses[1] == {"hypothesis_id": "h2", "assessment": "SUPPORTED"}
    assert graph["edges"][0]["relation"] == "pruned_by"
    assert graph["edges"][0]["source"] == "h1" and graph["edges"][0]["target"] == "h2"
    assert graph["edges"][0]["score_refs"] == ["o1", "o2"]
    events = product_events("r", [], _batch_transitions(), [])
    assert [item["type"] for item in events] == ["batch_adjudicated", "hypothesis_pruned"]
    assert events[1]["payload"]["hypothesis_id"] == "h2"


def test_product_observation_uses_readonly_sources(tmp_path: Path):
    root = tmp_path / "state"
    _create_fresh_run(root, "observed")
    before = _row_census(root / "state.sqlite")
    result = build_product_observation(root, "observed")
    assert result["run_id"] == "observed"
    assert result["graph"] == {"nodes": [], "edges": []}
    assert any(item["type"] == "run_started" for item in result["events"])
    assert _row_census(root / "state.sqlite") == before
    with pytest.raises(Exception, match="does not belong"):
        read_experiment_log(root, "observed", "../../secrets")


def _interaction_transition(action, revision=1, **values):
    return {"workflow_id": "r", "workflow_kind": "RUN", "revision": revision,
        "command_id": f"control-{revision}", "command_type": "ACCEPT_EXPERT_INTERACTION", "created_at": revision,
        "payload": {"expert_interaction": {"action": action, "source_attempt_id": "observer", **values}}}


def test_interaction_events_are_from_accepted_controls_not_agent_proposals():
    actions = ["leader_selected", "leader_round_started", "leader_round_finished", "leader_not_started",
        "debate_proposed", "debate_started", "debate_verdict", "debate_restarts_finished", "debate_failed"]
    rows = [_interaction_transition(action, index + 1, debate_id="d") for index, action in enumerate(actions)]
    rows[2]["command_type"] = "RESUME_PARTICIPANT"
    rows.append({**rows[1], "revision": 100, "command_type": "REQUEST_AUXILIARY"})
    events = product_events("r", [], rows, [])
    assert [event["type"] for event in events] == ["leader_selected", "leader_started", "leader_finished",
        "leader_not_started", "debate_selected", "debate_started", "debate_finished", "debate_restarts_finished", "debate_failed"]
    assert len({event["event_id"] for event in events}) == len(events)
    assert all(event["payload"]["source_attempt_id"] == "observer" for event in events)


def test_debate_pruning_is_visible_without_refuting_supported_hypotheses():
    from hypoloom.observation_api import product_graph
    relation = {"loser_hypothesis_id": "h2", "winner_hypothesis_id": "h1", "debate_id": "d",
        "source_attempt_id": "judge", "score_refs": ["round1", "round6"]}
    row = _interaction_transition("debate_verdict", pruned_by=[relation])
    graph = product_graph([{"hypothesis_id": "h1", "assessment": "SUPPORTED"},
        {"hypothesis_id": "h2", "assessment": "SUPPORTED"}], [row])
    assert graph["nodes"][1]["state"] == "PRUNED"
    assert graph["nodes"][1]["assessment"] == "SUPPORTED"
    assert graph["edges"] == [{"source": "h1", "target": "h2", "relation": "pruned_by", **relation}]
    events = product_events("r", [], [row], [])
    assert [event["type"] for event in events] == ["debate_finished", "hypothesis_pruned"]
    assert events[1]["payload"]["debate_id"] == "d"


def test_graph_evolution_event_is_an_accepted_manager_control_not_merely_a_stage():
    row = {"workflow_id": "r", "workflow_kind": "RUN", "revision": 4, "command_id": "g", "created_at": 1,
        "command_type": "ACCEPT_GRAPH_EVOLUTION", "payload": {"agent_attempt_id": "manager",
            "graph_evolution": [{"role": "scientist", "max_forks": 1}], "plugin_update": {"commit": "b"}}}
    events = product_events("r", [], [row, {**row, "command_type": "REQUEST_AUXILIARY"}], [])
    assert len(events) == 1 and events[0]["type"] == "graph_evolved"
    assert events[0]["payload"]["source_attempt_id"] == "manager"
    assert events[0]["payload"]["graph_evolution"] == row["payload"]["graph_evolution"]


@pytest.mark.parametrize("endpoint", ["graph", "experts", "experiments", "events", "activity", "final_report"])
def test_each_product_http_endpoint_requires_existing_bearer(tmp_path: Path, endpoint: str):
    root = tmp_path / "state"
    _create_fresh_run(root, "observed")
    server = WebObserverServer(root, "observed", port=0, token=_TOKEN)
    server.log_requests = False
    thread = _start(server)
    try:
        assert _request(server, "GET", "/api/" + endpoint)[0] == 401
        status, headers, body = _request(server, "GET", "/api/" + endpoint, token=_TOKEN)
        assert status == 200
        assert json.loads(body)["run_id"] == "observed"
        assert ("experiments" if endpoint == "activity" else endpoint) in json.loads(body)
        assert headers["cache-control"] == "no-store"
    finally: _stop(server, thread)


def test_real_websocket_subscription_authenticates_streams_and_closes(tmp_path: Path, monkeypatch):
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import AcceptConnection, CloseConnection, Request, TextMessage
    content = {"text": "first line", "available": True, "size": 10, "offset": 0}
    def read(_root, run_id, experiment_id, **_kwargs):
        assert run_id == "observed" and experiment_id == "e1"
        return dict(content)
    monkeypatch.setattr("hypoloom.web_observer.read_experiment_log", read)
    server = WebObserverServer(tmp_path, "observed", port=0, token=_TOKEN)
    server.log_requests = False
    thread = _start(server)
    try:
        target = "/api/experiments/e1/logs/ws"
        assert _request(server, "GET", target)[0] == 401
        ws = WSConnection(ConnectionType.CLIENT)
        with socket.create_connection(server.server_address, timeout=5) as connection:
            connection.sendall(ws.send(Request(host=f"127.0.0.1:{server.server_address[1]}", target=target,
                subprotocols=["hypoloom.logs", "bearer." + _TOKEN])))
            pending = []
            def receive(kind):
                while True:
                    for index, event in enumerate(pending):
                        if isinstance(event, kind): return pending.pop(index)
                    data = connection.recv(65536)
                    assert data
                    ws.receive_data(data)
                    pending.extend(ws.events())
            accepted = receive(AcceptConnection)
            assert accepted.subprotocol == "hypoloom.logs"
            first = receive(TextMessage)
            assert json.loads(first.data)["text"] == "first line"
            content.update(text="first line\nsecond line", size=22)
            assert "second line" in json.loads(receive(TextMessage).data)["text"]
            connection.sendall(ws.send(CloseConnection(code=1000)))
            assert receive(CloseConnection).code == 1000
    finally: _stop(server, thread)
