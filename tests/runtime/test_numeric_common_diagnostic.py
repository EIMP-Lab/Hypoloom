"""Reproduce valid numeric GOAL output plus an actually invalid LOCAL shape."""
import json
import sys
from types import SimpleNamespace

import pytest

from hypoloom.shared_acceptance_host import SharedAcceptanceHost
from hypoloom.shared_acceptance_oracle import interpret_shared_acceptance
from hypoloom.measurement_oracle import interpret_measurement


def _mixed_phase_case(tmp_path, monkeypatch, numeric):
    import hypoloom.measurement_oracle as local_oracle
    import hypoloom.shared_acceptance_oracle as shared_oracle
    import hypoloom.oracle as oracle

    declaration = {'summary': 'Common accuracy', 'rule': 'ALL',
        'criteria': [{'name': 'accuracy', 'operator': 'GE', 'target': '0.875'}],
        'validity_checks': ['complete_pairs', 'computed_accuracy_matches'],
        'parser_argv': ['python', '/input/evaluate.py'], 'parser_files': ['evaluate.py'],
        'raw_artifacts': ['predictions.json']}
    measurement = {'comparison': 'VALUE',
        'metric': {'name': 'accuracy', 'direction': 'MAXIMIZE', 'target': '0.875'},
        'parse_rule': 'Read predictions and return a local scalar',
        'parser_argv': ['python', '/input/evaluate.py'], 'parser_files': ['evaluate.py'],
        'raw_artifacts': ['predictions.json']}
    raw = {'measurements': {'accuracy': numeric},
           'checks': {'complete_pairs': True, 'computed_accuracy_matches': True}}
    publication = tmp_path / 'publication'
    (publication / 'goal/parser').mkdir(parents=True)
    source = {'evaluate.py': b'accepted common parser source'}
    (publication / 'goal/parser/evaluate.py').write_bytes(source['evaluate.py'])
    (publication / 'goal/result.json').write_text(json.dumps(raw))
    (publication / 'result.json').write_text(json.dumps(raw))
    parser_dirs = [tmp_path / name for name in ('local-parser', 'goal-parser')]
    for directory in parser_dirs:
        directory.mkdir()
        (directory / 'result.json').write_text(json.dumps(raw))
    prepared = SimpleNamespace(**{name: SimpleNamespace(output_dir=directory, max_output_bytes=4096)
        for name, directory in zip(('local_parser', 'goal_parser'), parser_dirs)})
    spec = {'measurement': measurement, 'goal_evaluation': {'scope': 'GOAL',
        'source_attempt_id': 'grounding', 'acceptance': declaration},
        'execution_contract': {'source_submit_command_id': 'submit', 'timeout_seconds': 5},
        'evaluator_version': 'component-result/9'}
    process = {'terminal_reason': 'SUCCEEDED', 'returncode': 0, 'process_request':
        {'argv': ['python', '-m', 'driver', json.dumps({'raw_request':
            {'argv': ['bwrap', '--ro-bind', sys.prefix, '/runtime']}})]}}
    local, common, diagnostic = SharedAcceptanceHost(None).evaluate(
        {'payload': spec}, publication, process, prepared)
    assert local is None
    assert diagnostic.startswith('local: invalid_parsed_measurement:')
    assert 'goal' not in diagnostic
    assert common == {'source_attempt_id': 'grounding', **raw}
    with pytest.raises(ValueError, match='fields or metric identity differ'):
        interpret_measurement(measurement, raw)
    independent = interpret_shared_acceptance(declaration, raw)
    assert independent.goal_met is independent.validity_met is True
    canonical = {'source_attempt_id': 'grounding', **independent.observations}
    candidate = {'outcome': 'EXECUTION_FAILURE', 'evaluator_version': 'component-result/9',
        'metric_value': None, 'criterion_met': None, 'execution_integrity_error': None,
        'validation_error': diagnostic, 'parser_validation_error': diagnostic,
        'goal_evaluation': common}
    payload = {'outcome': 'EXECUTION_FAILURE', 'evaluator_version': 'component-result/9',
        'metric_value': None, 'logical_artifacts': [], 'goal_evaluation': canonical}
    def invalid_local(*_args, **_kwargs):
        return interpret_measurement(measurement, raw)
    monkeypatch.setattr(local_oracle, 'audit_measurement_result', invalid_local)
    monkeypatch.setattr(shared_oracle, 'grounding_source', lambda *_a: (declaration, source, {}))
    monkeypatch.setattr(shared_oracle, 'audit_shared_result', lambda *_a, **_k: independent)
    monkeypatch.setattr(oracle, '_raw_submit_source', lambda *_a: ({}, {}))
    monkeypatch.setattr(oracle, '_decode_workspace', lambda *_a: {})
    return oracle, spec, payload, publication, candidate, process


def _audit_case(case, tmp_path):
    oracle, spec, payload, publication, candidate, process = case
    issues = []
    result = oracle._audit_shared_component_result(None, {'run_id': 'run'}, spec,
        {'fact_id': 'outcome'}, payload, {'attempt_id': 'process'}, publication, candidate,
        [{'command_id': 'submit'}], [], {'process': process}, issues, tmp_path)
    assert result.outcome == 'EXECUTION_FAILURE'
    assert result.criterion_met is result.metric_value is None
    assert result.goal_met is result.goal_valid is True
    return issues


@pytest.mark.parametrize('numeric', [0.875, '0.875', '0.8750'])
def test_actual_local_parse_failure_keeps_valid_common_numeric_observation(tmp_path, monkeypatch, numeric):
    assert _audit_case(_mixed_phase_case(tmp_path, monkeypatch, numeric), tmp_path) == []


@pytest.mark.parametrize('mutation', [
    'wrong-source', 'wrong-value', 'extra-top-field', 'missing-source',
    'missing-checks', 'missing-required-check', 'extra-check', 'extra-measurement',
    'bool-numeric', 'numeric-check', 'nonfinite', 'non-object', 'null',
])
def test_common_numeric_transition_still_rejects_changed_source_values_and_shape(tmp_path, monkeypatch, mutation):
    case = _mixed_phase_case(tmp_path, monkeypatch, 0.875)
    candidate = case[4]
    common = candidate['goal_evaluation']
    if mutation == 'wrong-source':
        common['source_attempt_id'] = 'different-grounding'
    elif mutation == 'wrong-value':
        common['measurements']['accuracy'] = 0.874
    elif mutation == 'extra-top-field':
        common['extra'] = 'not an observation'
    elif mutation == 'missing-source':
        common.pop('source_attempt_id')
    elif mutation == 'missing-checks':
        common.pop('checks')
    elif mutation == 'missing-required-check':
        common['checks'].pop('complete_pairs')
    elif mutation == 'extra-check':
        common['checks']['extra'] = True
    elif mutation == 'extra-measurement':
        common['measurements']['extra'] = 1
    elif mutation == 'bool-numeric':
        common['measurements']['accuracy'] = True
    elif mutation == 'numeric-check':
        common['checks']['complete_pairs'] = 1
    elif mutation == 'nonfinite':
        common['measurements']['accuracy'] = 'NaN'
    elif mutation == 'non-object':
        candidate['goal_evaluation'] = []
    else:
        candidate['goal_evaluation'] = None
    issues = _audit_case(case, tmp_path)
    assert [issue.code for issue in issues] == ['accept_outcome_common_diagnostic_mismatch']
