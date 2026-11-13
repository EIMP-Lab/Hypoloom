"""Run actual candidate dispatch before training, inside the existing sandbox."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
import time

import pytest
import yaml

from hypoloom.agent_aci import EmbeddedInput, WorkspaceArtifact, canonical_json, required_output_schema, with_output_digest
from hypoloom.claude_agent_host import _prompt
from hypoloom.domain import AgentCommand
from hypoloom.execution_checks import CHECKS_FAILED, read_checks, validate_declaration
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.profile_v4 import project_profile_v4, validate_product_settings
from hypoloom.runner import LocalRunner
from hypoloom.shared_acceptance_execution import (collect_evaluation, evaluation_phase_success,
    materialize_evaluation, plan_evaluation)

def documents():
    root = Path(__file__).resolve().parents[1] / 'profiles/templates/multi-expert-en'
    return {path.stem: yaml.safe_load(path.read_text()) for path in root.glob('*.yaml')}


def candidate_pair(source, extra_files=None):
    request = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    response = with_output_digest(replace(FakeAgent().invoke(request),
        proposed_workspace_artifacts=(WorkspaceArtifact.from_bytes('experiment.py', source),
            *(WorkspaceArtifact.from_bytes(name, data) for name, data in (extra_files or {}).items()))))
    return request.to_dict(), response.to_dict()


def make_workspace(tmp_path):
    root = tmp_path / 'state'
    artifacts = root / 'artifacts'
    artifacts.mkdir(parents=True)
    return ExecutionWorkspace(artifacts, state_root=root)


CANDIDATE = b'''import json, sys
from pathlib import Path
import numpy as np
def per_anchor(x):
    value = 0.0
    for row in x:
        value += float(np.sum(row * row))
    return value, 2 * x
def vectorized(x):
    return float(np.sum(x * x)), 2 * x
class Model:
    loss = staticmethod(vectorized)
class Override(Model):
    loss = staticmethod(per_anchor)
def factory(mode):
    model = Override() if mode == 'override' else Model()
    if mode in {'old', 'unused'}: model.loss = per_anchor
    if mode == 'instance': model.loss = lambda x: per_anchor(x)
    if mode == 'wrong': model.loss = lambda x: (float(np.sum(x)), np.ones_like(x))
    return model
if __name__ == '__main__':
    factory(sys.argv[1]).loss(np.ones((3, 4)))
    Path('training-started').write_text('yes')
    Path('result.json').write_text(json.dumps({'trained': True}))
'''

CHECKER = '''import importlib.util, json, sys
from pathlib import Path
import numpy as np
context = json.loads(Path('/hypoloom-checks/context.json').read_text())
spec = importlib.util.spec_from_file_location('candidate', Path('/input') / context['argv'][1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
model = module.factory(context['argv'][2])
seen = []
def profile(frame, event, arg):
    if event == 'call' and frame.f_code is module.per_anchor.__code__: seen.append('anchor')
sys.setprofile(profile)
x = np.arange(12, dtype=float).reshape(3, 4) / 7
loss, gradient = model.loss(x)
sys.setprofile(None)
checks = {'actual_dispatch': not seen,
          'equivalence': bool(np.allclose(loss, np.sum(x*x)) and np.allclose(gradient, 2*x))}
print('actual dispatch:', type(model).__name__, checks)
Path('result.json').write_text(json.dumps({'checks': checks}))
'''


def declaration(source=CHECKER):
    return {'requirements': {'actual_dispatch': 'Use the vectorized loss in the actual model.',
                             'equivalence': 'Preserve the loss and full parameter gradients.'},
            'argv': ['python', 'check.py'], 'files': {'check.py': source}, 'timeout_seconds': 5}


def prepare(tmp_path, mode='good', check=None, extra_files=None):
    pytest.importorskip('numpy')
    workspace = make_workspace(tmp_path)
    request, response = candidate_pair(CANDIDATE, extra_files)
    argv = ('python', 'experiment.py', mode)
    raw = workspace.plan(agent_request=request, agent_response=response, attempt_id='process-1',
        attempt_ordinal=1, execution_id='execution-1', target_env={}, timeout_seconds=15,
        execution_argv=argv, allow_generic_argv=True)
    evaluation = plan_evaluation(raw, local_measurement=None, local_files={}, goal_acceptance=None,
        goal_files={}, execution_id='execution-1', timeout_seconds=15, execution_checks=check or declaration(),
        workload_argv=argv, python_executable=sys.executable, runtime_prefix=sys.prefix)
    workspace.materialize(evaluation.execution, agent_request=request, agent_response=response)
    materialize_evaluation(evaluation)
    return workspace, request, response, evaluation


@pytest.mark.parametrize('mode,success', [('good', True), ('old', False), ('unused', False),
    ('override', False), ('instance', False), ('wrong', False)])
def test_actual_factory_dispatch_before_workload_and_publication(tmp_path, mode, success):
    workspace, request, response, evaluation = prepare(tmp_path, mode)
    observed = LocalRunner().run(evaluation.execution.process_request)
    assert observed.returncode == (0 if success else CHECKS_FAILED), (evaluation.execution.log_dir / 'stderr.log').read_text()
    assert (evaluation.execution.output_dir / 'training-started').exists() is success
    observation = observed.to_dict()
    assert evaluation_phase_success(observation, has_local=False, has_goal=False) == (success, None, None)
    collected = workspace.collect(evaluation.execution, observation, agent_request=request,
        agent_response=response, preserve_partial_outputs=True)
    published = collect_evaluation(evaluation, collected, observation)
    assert 'raw/execution-checks/source/check.py' in published.relative_paths
    assert (published.staging_dir / 'raw/execution-checks/source/check.py').read_text() == CHECKER
    assert 'actual dispatch:' in (published.staging_dir / 'raw/execution-checks/stdout.log').read_text()


@pytest.mark.parametrize('source', [
    "pass", "raise RuntimeError('checker broke')",
    "from pathlib import Path; Path('result.json').write_text('{\"checks\":{}}')",
    "from pathlib import Path; Path('result.json').write_text('x' * 65537)",
    "import time; time.sleep(20)",
])
def test_checker_failure_and_timeout_never_launch_workload(tmp_path, source):
    check = declaration(source)
    check['timeout_seconds'] = 0.3
    _, _, _, evaluation = prepare(tmp_path, check=check)
    observed = LocalRunner().run(evaluation.execution.process_request)
    assert observed.returncode == CHECKS_FAILED
    assert not (evaluation.execution.output_dir / 'training-started').exists()


@pytest.mark.parametrize('value', [
    '{}', '{"checks":{"actual_dispatch":true}}',
    '{"checks":{"actual_dispatch":1,"equivalence":true}}',
    '{"checks":{"actual_dispatch":true,"equivalence":false}}',
    '{"checks":{"actual_dispatch":true,"equivalence":true,"extra":true}}',
    '{"checks":{"actual_dispatch":true,"actual_dispatch":false,"equivalence":true}}',
    '{"checks":{"actual_dispatch":true,"equivalence":NaN}}',
])
def test_missing_nonboolean_extra_duplicate_and_false_results_fail(tmp_path, value):
    (tmp_path / 'result.json').write_text(value)
    with pytest.raises(ValueError): read_checks(tmp_path, declaration()['requirements'])


@pytest.mark.parametrize('field,value', [('requirements', {}), ('requirements', {'A': 'bad id'}),
    ('files', {'../check.py': 'pass'}), ('files', {'x': '', 'x/a.py': ''}),
    ('files', {'context.json': '{}'}), ('argv', ['python', '-c', 'pass']),
    ('timeout_seconds', True), ('timeout_seconds', float('inf')), ('timeout_seconds', 0)])
def test_profile_and_direct_controls_reject_invalid_checks(field, value):
    docs = documents()
    valid = project_profile_v4(**docs)
    check = {**declaration(), field: value}
    docs['goal']['execution_checks'] = check
    projected = project_profile_v4(**docs)
    assert any(issue.location == 'goal.yaml.execution_checks' for issue in projected.issues)
    settings = valid.settings
    settings['goal']['execution_checks'] = check
    with pytest.raises(ValueError): validate_product_settings(settings)


def test_optional_controls_preserve_legacy_and_freeze_source():
    docs = documents()
    old = project_profile_v4(**docs)
    assert 'execution_checks' not in old.settings['goal']
    docs['goal']['execution_checks'] = declaration()
    new = project_profile_v4(**docs)
    assert not new.issues
    frozen = validate_product_settings(new.settings)
    docs['goal']['execution_checks']['files']['check.py'] = 'pass'
    assert frozen['goal']['execution_checks']['files']['check.py'] == CHECKER
    assert validate_declaration(declaration()) == declaration()


@pytest.mark.parametrize('command', [AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION, AgentCommand.RESULT_ANALYSIS])
def test_required_checks_are_projected_without_inline_source_bloat(command):
    base = default_request(command)
    contract = {'methodology': {'controls': {'product': {'goal': {'execution_checks': declaration()}}}}}
    item = EmbeddedInput.from_bytes('run--fact-goal', 'application/json',
        canonical_json({'payload': {'research_contract': contract}}))
    config = EmbeddedInput.from_bytes('run--expert-config', 'application/json', canonical_json({
        'required_experiment_spec': {'metric': None, 'evaluator_version': 'fixture-v1', 'artifact_requirements': ['result.json']}}))
    request = replace(base, schema_version=28, input_artifacts=(*base.input_artifacts, item, config),
        required_output_schema=required_output_schema(command, base.task_context, schema_version=28))
    prompt = json.loads(_prompt(request, tool_workspace=True))
    assert prompt['execution_checks']['requirements'] == declaration()['requirements']
    assert 'files' not in prompt['execution_checks']
    assert request.input_artifacts[-2].content_bytes() == item.content_bytes()


def test_candidate_cannot_write_admitted_sources_or_reuse_other_output(tmp_path):
    source = '''from pathlib import Path
try:
    Path('/hypoloom-checks/check.py').write_text('pass')
except OSError:
    pass
else:
    raise AssertionError('source writable')
Path('result.json').write_text('{"checks":{"actual_dispatch":true,"equivalence":true}}')
'''
    _, _, _, evaluation = prepare(tmp_path, check=declaration(source))
    observed = LocalRunner().run(evaluation.execution.process_request)
    assert observed.returncode == 0
    assert evaluation.execution_checks.parser.source_dir.joinpath('check.py').read_text() == source
    assert json.loads(evaluation.execution.output_dir.joinpath('result.json').read_text()) == {'trained': True}


@pytest.mark.parametrize('finish', ['cancel', 'timeout'])
def test_outer_stop_drains_check_children_before_training(tmp_path, finish):
    from hypoloom.runner import process_group_exists
    source = '''import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path('started').write_text('ready')
time.sleep(60)
'''
    check = declaration(source)
    check['timeout_seconds'] = 30
    _, _, _, evaluation = prepare(tmp_path, check=check)
    request = replace(evaluation.execution.process_request, timeout_seconds=2 if finish == 'timeout' else 10)
    runner = LocalRunner()
    handle = runner.start(request)
    deadline = time.monotonic() + 5
    while not (evaluation.execution_checks.parser.output_dir / 'started').exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    # Independently sample the actual host PID tree before interrupting it.
    owned = {handle.pid}
    for _ in range(10):
        before = set(owned)
        for process in Path('/proc').iterdir():
            if not process.name.isdigit(): continue
            try:
                fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[1]) in owned: owned.add(int(process.name))
            except (OSError, ValueError): pass
        if before == owned: break
    assert len(owned) >= 4  # driver, internal runner, sandbox and probe child
    cancel = threading.Event()
    if finish == 'cancel': cancel.set()
    observed = runner.wait(handle, cancel_event=cancel)
    assert observed.terminal_reason.value == ('CANCELLED' if finish == 'cancel' else 'TIMED_OUT')
    assert not (evaluation.execution.output_dir / 'training-started').exists()
    deadline = time.monotonic() + 5
    def live(pid):
        try: return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except OSError: return False
    while any(live(pid) or process_group_exists(pid) for pid in owned):
        assert time.monotonic() < deadline, owned
        time.sleep(0.02)


def test_changed_materialized_source_and_stale_result_cannot_validate(tmp_path):
    from hypoloom.execution_checks import verify_check_sources
    _, _, _, evaluation = prepare(tmp_path)
    check = evaluation.execution_checks
    (check.parser.source_dir / 'check.py').write_text('pass')
    with pytest.raises(ValueError, match='admitted source'): verify_check_sources(check)
    (check.parser.output_dir / 'result.json').write_text('{"checks":{"different_configuration":true}}')
    with pytest.raises(ValueError, match='exactly'): read_checks(check.parser.output_dir, check.requirements)


def test_candidate_supplied_passing_result_cannot_replace_fresh_check(tmp_path):
    stale = json.dumps({'checks': dict.fromkeys(declaration()['requirements'], True)}).encode()
    _, _, _, evaluation = prepare(tmp_path, check=declaration('pass'), extra_files={'result.json': stale})
    observed = LocalRunner().run(evaluation.execution.process_request)
    assert observed.returncode == CHECKS_FAILED
    assert not (evaluation.execution.output_dir / 'training-started').exists()


def test_scientific_inventory_exempts_only_exact_host_evidence_paths(tmp_path):
    from hypoloom.outcome_adapter import ComponentResultValidationError, validate_component_result
    component = {'id': 'must.report', 'result': {'kind': 'artifact'}, 'required_artifacts': []}
    protocol = {'result_kind': 'artifact', 'requirements': {}}
    (tmp_path / 'result.json').write_text('{"artifacts":{}}')
    sidecar = tmp_path / 'raw/execution-checks/result.json'
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text('{"checks":{"bound":true}}')
    auxiliary = ('raw/execution-checks/result.json',)
    with pytest.raises(ComponentResultValidationError, match='artifact_inventory_mismatch'):
        validate_component_result(component, protocol, tmp_path)
    assert validate_component_result(component, protocol, tmp_path, auxiliary_paths=auxiliary)['criterion_met']
    sidecar.with_name('undeclared.json').write_text('{}')
    with pytest.raises(ComponentResultValidationError, match='artifact_inventory_mismatch'):
        validate_component_result(component, protocol, tmp_path, auxiliary_paths=auxiliary)
    sidecar.with_name('undeclared.json').unlink()
    (tmp_path / 'result.json').write_text(json.dumps({'artifacts': {'report': auxiliary[0]}}))
    with pytest.raises(ComponentResultValidationError, match='Host execution evidence'):
        validate_component_result({**component, 'required_artifacts': ['report']}, protocol, tmp_path, auxiliary_paths=auxiliary)
