"""Real Host/CPU publication, technical repair and independent evidence audit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.control_protocol import ControlRequest
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_deferred_measurement_host import (DeclaredMeasurementAgent,
    _manual_submission, deferred_payload)
from tests.runtime.test_host_experiment_tools import _terminal, _until
from tests.runtime.test_profile_v4 import documents
from tests.runtime.test_shared_acceptance_host import SharedAcceptanceAgent, shared_payload
from tests.runtime.test_expert_loop_host import _pilot_payload


PROBE = '''import json, subprocess, sys, tempfile
from pathlib import Path
context = json.loads(Path('/hypoloom-checks/context.json').read_text())
command = [sys.executable if context['argv'][0] == 'python' else context['argv'][0],
           *[str(Path('/input') / item) if (Path('/input') / item).is_file() else item for item in context['argv'][1:]]]
with tempfile.TemporaryDirectory() as directory:
    result = subprocess.run(command, cwd=directory, timeout=2, capture_output=True)
print('bounded actual command exit:', result.returncode)
Path('result.json').write_text(json.dumps({'checks': {'bounded_probe': result.returncode == 0}}))
'''


def add_controls(payload):
    product = project_profile_v4(**documents()).settings
    product['goal'].update(stop_conditions={}, execution_checks={
        'requirements': {'bounded_probe': 'The submitted tiny CPU fixture must complete its bounded probe.'},
        'argv': ['python', 'probe.py'], 'files': {'probe.py': PROBE}, 'timeout_seconds': 5})
    if payload['research_contract']['goal'].get('evaluation'):
        product['goal']['evaluation'] = 'grounding'
    product['experts']['defaults'].update(max_forks=1, max_hypotheses_per_round=1)
    product['methodology']['inner_loop'] = {'max_iterations_per_hypothesis': 2, 'repair_attempts': 1}
    product['methodology']['graph_evolution']['enabled'] = False
    product['notify'] = {'sinks': {}, 'events': {}}
    payload['research_contract']['methodology']['controls']['product'] = product
    return payload


@pytest.fixture
def checked_host(tmp_path, request):
    mode = getattr(request, 'param', 'legacy')
    with tempfile.TemporaryDirectory(prefix='hypoloom-checks-target-', dir='/var/tmp') as target:
        agent = FakeAgent() if mode == 'static' else DeclaredMeasurementAgent() if mode == 'legacy' else SharedAcceptanceAgent(
            scope='LOCAL' if mode == 'LOCAL' else 'GOAL', goal_only=mode == 'goal-only')
        host = WorkflowHost(tmp_path / 'state', local_agent=agent)
        host.initialize()
        try:
            payload = (_pilot_payload(tmp_path, Path(target), 'c5-host') if mode == 'static' else
                (deferred_payload if mode == 'legacy' else shared_payload)(tmp_path, Path(target)))
            created = host.handle(ControlRequest(1, 'checks-create', 0, 'CREATE_RUN', add_controls(payload)))
            assert created.status == 'accepted', created
            yield host
        finally:
            host.close()


def audit(host):
    report = Oracle(host.store.database_path, host.publisher.root).audit('c5-host')
    assert report.ok, report.to_dict()
    assert not [row for row in host.store.list_attempts() if row['state'] == 'OPEN']


@pytest.mark.parametrize('checked_host', ['static', 'legacy', 'LOCAL', 'GOAL', 'goal-only'], indirect=True)
def test_checked_workload_closes_through_real_host_and_audit(checked_host):
    host = checked_host
    _until(host, lambda: host.store.latest_transition('c5-host')['state'] == 'COMPLETED')
    outcomes = host.store.list_facts('c5-host', 'CanonicalOutcomeAccepted')
    assert len(outcomes) == 1 and outcomes[0]['payload']['outcome'] == 'SUCCESS'
    processes = [row for row in host.store.list_attempts() if row['kind'] == 'PROCESS']
    assert len(processes) == 1 and processes[0]['terminal_reason'] == 'SUCCEEDED'
    plan = json.loads(json.loads(processes[0]['observation_json'])['process_request']['argv'][3])
    assert plan['execution_checks']['requirements'] == ['bounded_probe']
    artifacts = host.store.inspect('SELECT * FROM execution_artifacts WHERE attempt_id=?', (processes[0]['attempt_id'],))
    root = host.publisher.root / artifacts[0]['path']
    assert json.loads((root / 'raw/execution-checks/result.json').read_text()) == {'checks': {'bounded_probe': True}}
    audit(host)


@pytest.mark.parametrize('checked_host', ['legacy', 'GOAL'], indirect=True)
def test_failed_prerequisite_repairs_through_existing_submit_and_keeps_diagnostics(checked_host):
    host = checked_host
    request, arguments = _manual_submission(host)
    main = Path(arguments['candidate']['execution_argv'][1]).name
    failed = {**arguments, 'workspace_files': [WorkspaceArtifact.from_bytes(item['path'],
        (b'exit 7\n' if main.endswith('.sh') else b'raise SystemExit(7)\n') if item['path'] == main else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
        for item in arguments['workspace_files']]}
    first = host.handle_agent_tool(request.attempt_id, 'submit_experiment', failed)
    _until(host, lambda: _terminal(host, first['exp_id']) and bool(host.store.list_facts('c5-host', 'EvidenceLinked')))
    outcome = host.handle_agent_tool(request.attempt_id, 'wait_experiment', {'exp_id': first['exp_id']})['outcome']
    assert outcome['outcome'] == 'EXECUTION_FAILURE'
    assert outcome['metric_value'] is None
    process = next(row for row in host.store.list_attempts() if row['kind'] == 'PROCESS')
    observation = json.loads(process['observation_json'])
    assert observation['returncode'] == 74
    raw = json.loads(observation['process_request']['argv'][3])['raw_request']
    assert not Path(raw['cwd']).joinpath('raw-stdout.log').exists()
    second = host.handle_agent_tool(request.attempt_id, 'submit_experiment', {**arguments, 'replaces_experiment_id': first['exp_id']})
    _until(host, lambda: _terminal(host, second['exp_id']))
    assert host.handle_agent_tool(request.attempt_id, 'wait_experiment', {'exp_id': second['exp_id']})['outcome']['outcome'] == 'SUCCESS'
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition('c5-host')['state'] == 'COMPLETED')
    assert len([row for row in host.store.list_attempts() if row['kind'] == 'PROCESS']) == 2
    audit(host)


@pytest.mark.parametrize('point', ['after_observation_before_artifact_collection', 'after_artifact_publish_before_digest_commit'])
def test_checked_publication_recovers_without_reexecuting(tmp_path, point):
    with tempfile.TemporaryDirectory(prefix='hypoloom-check-recover-', dir='/var/tmp') as target:
        root = tmp_path / 'state'
        host = WorkflowHost(root, local_agent=DeclaredMeasurementAgent())
        host.initialize()
        try:
            result = host.handle(ControlRequest(1, 'create-check-recover', 0, 'CREATE_RUN',
                add_controls(deferred_payload(tmp_path, Path(target)))))
            assert result.status == 'accepted'
        finally: host.close()
        script = '''from pathlib import Path
import sys
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_deferred_measurement_host import DeclaredMeasurementAgent
from tests.runtime.test_host_experiment_tools import _until
host = WorkflowHost(Path(sys.argv[1]), local_agent=DeclaredMeasurementAgent(), fault_point=sys.argv[2])
host.initialize()
_until(host, lambda: host.store.latest_transition('c5-host')['state'] == 'COMPLETED')
raise AssertionError('publication crash hook did not fire')
'''
        child = subprocess.run([sys.executable, '-c', script, str(root), point],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True, timeout=35)
        assert child.returncode == 86, (child.stdout, child.stderr)
        recovered = WorkflowHost(root, local_agent=DeclaredMeasurementAgent())
        try:
            recovered.initialize()
            _until(recovered, lambda: recovered.store.latest_transition('c5-host')['state'] == 'COMPLETED')
            processes = [row for row in recovered.store.list_attempts() if row['kind'] == 'PROCESS']
            assert len(processes) == 1 and processes[0]['terminal_reason'] == 'SUCCEEDED'
            assert len(recovered.store.list_facts('c5-host', 'CanonicalOutcomeAccepted')) == 1
            assert len(recovered.store.list_facts('c5-host', 'EvidenceLinked')) == 1
            audit(recovered)
        finally: recovered.close()


def test_native_shared_goal_with_admitted_checks(tmp_path, monkeypatch):
    import tests.runtime.test_claude_shared_acceptance_g3 as native
    original = native._payload
    def payload(*args):
        value = original(*args)
        value['research_contract']['methodology']['controls']['product']['goal']['execution_checks'] = {
            'requirements': {'bounded_probe': 'The tiny candidate completes a bounded trial.'},
            'argv': ['python', 'probe.py'], 'files': {'probe.py': PROBE}, 'timeout_seconds': 5}
        return value
    monkeypatch.setattr(native, '_payload', payload)
    native.test_native_grounding_shared_source_local_failure_and_complete_goal_stop(tmp_path, monkeypatch)


def test_check_plan_rebuild_and_admitted_source_cannot_drift(checked_host):
    host = checked_host
    _until(host, lambda: host.store.latest_transition('c5-host')['state'] == 'COMPLETED')
    process = next(row for row in host.store.list_attempts() if row['kind'] == 'PROCESS')
    observation = json.loads(process['observation_json'])
    from hypoloom.execution_checks_oracle import audit_request
    from hypoloom.execution_checks import contract_checks
    checks = contract_checks(host._research_contract('c5-host').document)
    outer = observation['process_request']
    assert 'execution_checks' not in json.loads(audit_request(outer, checks)['argv'][3])
    changed = json.loads(json.dumps(outer))
    plan = json.loads(changed['argv'][3])
    plan['execution_checks']['requirements'] = []
    changed['argv'][3] = json.dumps(plan)
    with pytest.raises(ValueError, match='requirements'): audit_request(changed, checks)
    plan = json.loads(outer['argv'][3])
    plan.pop('execution_checks')
    changed['argv'][3] = json.dumps(plan)
    with pytest.raises((KeyError, ValueError)): audit_request(changed, checks)
    audit(host)
