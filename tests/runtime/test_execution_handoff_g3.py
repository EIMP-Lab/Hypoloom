"""Native CC hands off both the failing CPU experiment and its repaired replacement."""
import json
import sqlite3

from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent
import test_full_cc_host_g3 as native


def test_native_handoff_releases_sessions_collects_and_repairs(tmp_path, monkeypatch):
    scenario = native._current_host_scenario(tmp_path)
    original_record, original_tools = scenario.record, scenario.tool_calls
    original_payload = native._pilot_payload
    resume_step = None

    def payload(*args):
        result = original_payload(*args)
        result['execution']['delay_seconds'] = 12
        return result

    def record(document):
        nonlocal resume_step
        request = native._request_from_provider(document)
        if (request.command is AgentCommand.EXPERIMENT_EXECUTION
                and request.attempt_id not in scenario.execution_attempt_ids and resume_step is not None):
            scenario.requests.append(document)
            scenario.current = request
            scenario.agent_requests[request.attempt_id] = request
            scenario.steps[request.attempt_id] = resume_step
            scenario.responses[request.attempt_id] = FakeAgent().invoke(request)
            scenario.execution_attempt_ids.append(request.attempt_id)
            resume_step = None
            return len(scenario.requests)
        return original_record(document)

    def tool_calls(number):
        nonlocal resume_step
        request = scenario.current
        if request is not None and request.command is AgentCommand.EXPERIMENT_EXECUTION:
            step = scenario.steps[request.attempt_id]
            if (step, len(scenario.execution_attempt_ids)) in {(3, 1), (8, 2)}:
                response = scenario.responses[request.attempt_id]
                resume_step = step
                experiment = scenario.experiment_id if step == 3 else scenario.repaired_experiment_id
                return (('handoff', 'StructuredOutput', {
                    'typed_candidate': {**response.typed_candidate,
                        'summary': 'Submitted; Host owns waiting and result collection.',
                        'waiting_for_experiment_id': experiment},
                    'declared_effects': [{'effect': item.effect} for item in response.declared_effects]}),)
        return original_tools(number)

    scenario.record, scenario.tool_calls = record, tool_calls
    monkeypatch.setattr(native, '_current_host_scenario', lambda _: scenario)
    monkeypatch.setattr(native, '_pilot_payload', payload)
    native._exercise_full_cc_host(tmp_path, execution_handoffs=2)
    with sqlite3.connect(f'file:{tmp_path / "state/state.sqlite"}?mode=ro', uri=True) as connection:
        rows = connection.execute("SELECT terminal_reason, observation_json FROM execution_attempts "
            "WHERE kind='AGENT' AND json_extract(observation_json,'$.request.command')='EXPERIMENT_EXECUTION' "
            "ORDER BY started_at").fetchall()
        timing = connection.execute("SELECT started_at,ended_at FROM execution_attempts "
            "WHERE kind='AGENT' AND json_extract(observation_json,'$.request.command')='EXPERIMENT_EXECUTION' "
            "ORDER BY started_at").fetchall()
        process_end = connection.execute("SELECT ended_at FROM execution_attempts WHERE kind='PROCESS' "
            "AND workflow_id=?", (scenario.repaired_experiment_id,)).fetchone()[0]
    assert timing[1][1] < process_end <= timing[2][0]
    assert [reason for reason, _ in rows] == ['SUCCEEDED'] * 3
    observations = [json.loads(raw) for _, raw in rows]
    assert [bool(row['response']['typed_candidate'].get('waiting_for_experiment_id'))
            for row in observations] == [True, True, False]
    assert len({row['session']['session_id'] for row in observations}) == len(observations)
    assert len({row['session']['branch'] for row in observations}) == 1
    assert all(row['diagnostics']['structured_output_correction']['accepted'] for row in observations)
