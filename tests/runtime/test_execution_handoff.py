"""A handoff closes an invocation, never an experiment or its research node."""
from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import AgentACIError, with_output_digest
from hypoloom.agent_budget import agent_attempts_exhausted
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost
from test_host_experiment_tools import execution, _until as _wait


def _until(host, predicate):
    try:
        _wait(host, predicate)
    except AssertionError:
        raise AssertionError(str(host.dispatch_errors)) from None


def handoff(request, experiment_id):
    response = FakeAgent().invoke(request)
    return with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
        'waiting_for_experiment_id': experiment_id}))


def test_handoff_releases_agent_waits_then_collects_without_duplicate_work(execution):
    host, request, arguments = execution
    assert request.schema_version == 27
    submitted = host.handle_agent_tool(request.attempt_id, 'submit_experiment', arguments)
    host._finish_fake_agent(request, handoff(request, submitted['exp_id']))
    attempt = host.store.get_attempt(request.attempt_id)
    assert attempt['terminal_reason'] == 'SUCCEEDED'
    assert host._expert_success(request.task_context) is None
    assert not agent_attempts_exhausted({'budget': {'max_retries': 0}}, [attempt],
        model_fallback=1, infrastructure_fallback=1)
    host._resume_tool_execution_agents(request.run_id)
    assert not host.local_execution_agents
    assert host.store.latest_transition(submitted['exp_id'])['state'] != 'TERMINAL'
    _until(host, lambda: bool(host.local_execution_agents))
    collection = next(iter(host.local_execution_agents.values()))
    assert host.store.latest_transition(submitted['exp_id'])['state'] == 'TERMINAL'
    assert collection.command_id == request.command_id
    assert collection.input_artifacts == request.input_artifacts
    assert collection.budget == request.budget
    view = host.handle_agent_tool(collection.attempt_id, 'list_my_experiments', {})
    assert len(view['experiments']) == 1 and not view['experiments'][0]['can_handoff']
    with pytest.raises(AgentACIError, match='prior accepted handoff'):
        host._accept_expert_packet(collection, handoff(collection, submitted['exp_id']))
    _until(host, lambda: host._expert_success(request.task_context) is not None)
    assert len(host._expert_attempts(request.task_context)) == 2
    assert len(host.store.list_facts(request.run_id, 'CanonicalOutcomeAccepted')) == 1
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()


def test_handoff_rejects_missing_or_foreign_experiment(execution):
    host, request, arguments = execution
    with pytest.raises(AgentACIError, match='latest owned experiment'):
        host._accept_expert_packet(request, handoff(request, 'foreign'))
    submitted = host.handle_agent_tool(request.attempt_id, 'submit_experiment', arguments)
    with pytest.raises(AgentACIError, match='latest owned experiment'):
        host._accept_expert_packet(request, handoff(request, submitted['exp_id'] + '-foreign'))
    assert host.store.get_attempt(request.attempt_id)['state'] == 'OPEN'


def test_handoff_survives_host_reconstruction_before_process_spawn(execution):
    host, request, arguments = execution
    submitted = host.handle_agent_tool(request.attempt_id, 'submit_experiment', arguments)
    host._finish_fake_agent(request, handoff(request, submitted['exp_id']))
    root = host.state_root
    host.close()
    reopened = WorkflowHost(root)
    reopened.initialize()
    try:
        assert reopened._expert_success(request.task_context) is None
        reopened._resume_tool_execution_agents(request.run_id)
        assert not reopened.local_execution_agents
        _until(reopened, lambda: reopened._expert_success(request.task_context) is not None)
        attempts = reopened._expert_attempts(request.task_context)
        assert len(attempts) == 2 and {r['terminal_reason'] for r in attempts} == {'SUCCEEDED'}
        assert len(reopened.store.list_workflow_heads(request.run_id, 'EXPERIMENT')) == 1
        report = Oracle(reopened.store.database_path, reopened.publisher.root).audit(request.run_id)
        assert report.ok, report.to_dict()
    finally:
        reopened.close()


def test_archive_failure_keeps_closure_and_provider_accounting(execution):
    from types import SimpleNamespace
    from hypoloom.agent_sessions import SessionFinishError
    from hypoloom.agent_budget import fold_agent_attempts
    host, request, _ = execution
    response = FakeAgent().invoke(request)
    metadata = {'commit': None, 'commit_error': 'injected failure', 'transcript': None}
    def finish():
        raise SessionFinishError('injected failure', metadata)
    handle = SimpleNamespace(request=request, tool_workspace=SimpleNamespace(finish_node=finish))
    observation = SimpleNamespace(returncode=0, pid=None, process_group_id=None,
        failure_code=None, diagnostics={}, response=response)
    cleaned = []
    host.claude_agent_host = SimpleNamespace(poll=lambda _: observation,
        cleanup=lambda handle: cleaned.append(handle), cancel=lambda _: None)
    host.running_agents[request.attempt_id] = handle
    try:
        assert host._poll_agents()
        row = host.store.get_attempt(request.attempt_id)
        payload = json.loads(row['observation_json'])
        assert row['terminal_reason'] == 'FAILED'
        assert payload['failure_code'] == 'session_archive_failed'
        assert payload['session'] == metadata and 'response' not in payload
        assert payload['discarded_response'] == response.to_dict()
        assert fold_agent_attempts([row])[0].cost is not None
        assert cleaned == [handle] and not host.running_agents
    finally:
        host.claude_agent_host = None


def test_handoff_is_still_charged_against_profile_budget(execution):
    from hypoloom.agent_budget import fold_agent_attempts, remaining_agent_budget
    host, request, arguments = execution
    submitted = host.handle_agent_tool(request.attempt_id, 'submit_experiment', arguments)
    response = with_output_digest(replace(handoff(request, submitted['exp_id']),
        token_usage={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}, cost='0.1'))
    host._finish_fake_agent(request, response)
    charges = fold_agent_attempts([host.store.get_attempt(request.attempt_id)])
    assert charges[0].tokens > 0
    for maximum_cost in (None, '1'):
        budget = replace(request.budget, max_cost=maximum_cost, token_enforcement_mode="enforce")
        document = {'budget': {'agent_invocation': budget.to_dict(), 'run_tokens': charges[0].tokens},
            'roles': [{'id': request.role, 'budget': {}}]}
        assert remaining_agent_budget(document, request.role, charges, budget) is None


def test_cancel_after_handoff_never_restarts_collection(execution):
    from hypoloom.control_protocol import ControlRequest
    host, request, arguments = execution
    submitted = host.handle_agent_tool(request.attempt_id, 'submit_experiment', arguments)
    host._finish_fake_agent(request, handoff(request, submitted['exp_id']))
    result = host.handle(ControlRequest(schema_version=1, request_id='cancel-handoff',
        expected_revision=None, command='CANCEL', payload={'run_id': request.run_id, 'cause': 'test handoff cancellation'}))
    assert result.status == 'accepted'
    _until(host, lambda: host.store.latest_transition(request.run_id)['state'] == 'CANCELLED')
    assert len(host._expert_attempts(request.task_context)) == 1
    report = Oracle(host.store.database_path, host.publisher.root).audit(request.run_id)
    assert report.ok, report.to_dict()
    assert report.open_attempt_count == report.pending_effect_count == 0
    assert not host.running and not host.local_execution_agents


def test_retained_protocol_rejects_handoff_output(execution):
    from hypoloom.agent_aci import validate_postflight
    _, request, _ = execution
    prior = replace(request, schema_version=21)
    response = handoff(request, 'experiment')
    response = with_output_digest(replace(response, schema_version=21))
    with pytest.raises(AgentACIError):
        validate_postflight(prior, response)


@pytest.mark.parametrize('corruption', ['foreign', 'premature_completion'])
def test_independent_oracle_rejects_corrupted_handoff(execution, corruption):
    from hypoloom.oracle import _audit_execution_handoffs, _row_identifier
    host, request, arguments = execution
    submitted = host.handle_agent_tool(request.attempt_id, 'submit_experiment', arguments)
    host._finish_fake_agent(request, handoff(request, submitted['exp_id']))
    attempts = host.store.list_attempts(run_id=request.run_id)
    payloads = {row['attempt_id']: json.loads(row['observation_json']) for row in attempts}
    candidate = payloads[request.attempt_id]['response']['typed_candidate']
    if corruption == 'foreign':
        candidate['waiting_for_experiment_id'] = 'foreign-experiment'
    else:
        candidate.pop('waiting_for_experiment_id')
    transitions = host.store.inspect('SELECT * FROM workflow_transitions WHERE run_id=?', (request.run_id,))
    transition_payloads = {_row_identifier(row): json.loads(row['payload_json']) for row in transitions}
    issues = []
    _audit_execution_handoffs(attempts, payloads, transitions, transition_payloads, issues)
    assert [issue.code for issue in issues] == ['execution_handoff_invalid']
