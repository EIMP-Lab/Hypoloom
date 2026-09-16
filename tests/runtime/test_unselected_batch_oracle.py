"""Independent ancestry for explicit ACI17 revision without a selected winner."""
import base64
import copy
import json
from dataclasses import replace

import pytest

from hypoloom.batch_oracle import selected_fact_tips
from hypoloom.domain import fact_to_dict
from hypoloom.expert_loop import ResearchNodeKind, ResearchNodeSubkind
from test_batch_oracle import _fixture, _input
from test_domain import all_facts


def _inputs(request, values):
    request['input_artifacts'] = [value.to_dict() for value in values]
    request['immutable_context_refs'] = [value.ref for value in values]


def _prior_cycle(version=17, *, unselected=True):
    data = _fixture()
    judge = data.attempt_payloads['adjudicate']
    context = json.loads(base64.b64decode(judge['request']['input_artifacts'][0]['content_base64']))
    for index in (1, 2):
        data.fact_payloads[f'fact-outcome-{index}'].update(outcome='EXECUTION_FAILURE', metric_value=None)
        context['terminal_tips'][f'chain-{index}'].update(outcome='EXECUTION_FAILURE', metric_value=None)
    candidate = judge['response']['typed_candidate']
    candidate.update(keep=['chain-1', 'chain-2'], prune=[], best_chain=None)
    for verdict in candidate['verdicts']:
        verdict['status'] = 'inconclusive'
    _inputs(judge['request'], [_input(judge['request']['command_id'] + '--batch-context', context)])
    row = data.transitions[-1]
    payload = json.loads(row['payload_json'])
    payload['adjudication'].update(candidate=copy.deepcopy(candidate), selected_tip=None, pruned_by=[])
    row['payload_json'] = json.dumps(payload)
    if not unselected:
        data = _fixture()
        context = json.loads(base64.b64decode(data.attempt_payloads['adjudicate']['request']['input_artifacts'][0]['content_base64']))
    for name, kind, prior in (('update', ResearchNodeKind.HYPOTHESIS_UPDATE, 'adjudicate'),
                             ('report', ResearchNodeKind.CYCLE_REPORT, 'update'),
                             ('evolve', ResearchNodeKind.SELF_EVOLVE, 'report')):
        value = {'summary': 'Close the failed batch.', 'supporting_refs': [prior + '--response']}
        if name == 'update':
            value['update_disposition'] = 'REVISE_NEXT_CYCLE'
        data.agent(name, kind, value,
            inputs=(_input(prior + '--response', data.attempt_payloads[prior]['response']),))
    for observation in data.attempt_payloads.values():
        observation['request']['schema_version'] = version
    for command in data.commands:
        payload = json.loads(command['request_json'])
        payload['source_request']['schema_version'] = version
        command['request_json'] = json.dumps(payload)
    return data, context


def _successor(version=17, *, accept=True, parents=('hyp-1', 'hyp-2'), unselected=True):
    data, context = _prior_cycle(version, unselected=unselected)
    template = all_facts()[1]
    hypotheses = [replace(template, fact_id=f'fact-next-{index}', hypothesis_id=f'next-{index}',
        parent_hypothesis_id=parent, relation='repair local parser')
        for index, parent in enumerate(parents, 1)]
    members = [{'chain_id': f'next-chain-{index}', 'candidate': {key: fact_to_dict(fact).get(key)
        for key in ('component_id', 'proposer_role', 'method_family_id', 'claim', 'assumptions',
                    'parent_hypothesis_id', 'relation', 'measurement')}}
        for index, fact in enumerate(hypotheses, 1)]
    _, observed = data.agent('next-synthesis', ResearchNodeKind.HYPOTHESIS_PROPOSAL,
        {'schema_version': 1, 'hypotheses': members}, cycle=2, subkind=ResearchNodeSubkind.SYNTHESIS)
    request = observed['request']
    request['schema_version'] = version
    _inputs(request, [_input(request['command_id'] + '--lineage-context',
        {'cycle': 2, 'parent_hypothesis_id': None if unselected else 'hyp-1'}),
        _input(request['command_id'] + '--batch-context', context),
        *[_input(f'fact-hyp-{index}', {'fact_id': f'fact-hyp-{index}', 'fact_type': 'HypothesisProposed',
            'payload': data.fact_payloads[f'fact-hyp-{index}']}) for index in (1, 2)]])
    if accept:
        for index, fact in enumerate(hypotheses, 1):
            data.fact(fact)
            payload = {'node_command_id': request['command_id'], 'agent_attempt_id': 'next-synthesis',
                'agent_output_digest': observed['response']['output_digest'], 'role': 'researcher',
                'component_id': 'primary.score', 'chain': f'next-chain-{index}',
                'hypothesis_id': fact.hypothesis_id, 'method_family_id': fact.method_family_id}
            if index == len(hypotheses):
                payload['hypothesis_batch'] = {'cycle': 2,
                    'chains': {f'next-chain-{i}': item.hypothesis_id for i, item in enumerate(hypotheses, 1)}}
            data.transition('ACCEPT_HYPOTHESIS', payload, fact_id=fact.fact_id)
    return data


def _replace_input(request, suffix, change):
    item = next(item for item in request['input_artifacts'] if item['ref'].endswith(suffix))
    value = json.loads(base64.b64decode(item['content_base64']))
    change(value)
    replacement = _input(item['ref'], value).to_dict()
    request['input_artifacts'][request['input_artifacts'].index(item)] = replacement


def test_aci17_unselected_batch_revises_each_member_without_inventing_a_selection():
    data = _successor()
    before = copy.deepcopy(vars(data))
    assert data.audit() == []
    assert selected_fact_tips(data.facts, data.fact_payloads, data.transitions, {}) == ({}, {}, {})
    assert vars(data) == before


@pytest.mark.parametrize('version', [14, 15, 16])
def test_older_aci_does_not_admit_unselected_successor(version):
    assert any(issue[0] == 'batch_source_invalid' for issue in _successor(version).audit())


@pytest.mark.parametrize('mutation', ['absent-parent', 'foreign-parent', 'older-parent', 'empty-relation',
    'wrong-fact-parent', 'missing-context', 'changed-batch', 'older-context', 'fake-selected-tip',
    'wrong-lineage-parent', 'no-revision', 'missing-update', 'missing-report', 'missing-evolve',
    'open-evolve', 'premature-start', 'cycle-budget', 'creation-budget'])
def test_unselected_successor_requires_own_prior_members_and_closed_budgeted_revision(mutation):
    data = _successor()
    request = data.attempt_payloads['next-synthesis']['request']
    candidate = data.attempt_payloads['next-synthesis']['response']['typed_candidate']['hypotheses'][0]['candidate']
    fact = data.fact_payloads['fact-next-1']
    if mutation in ('absent-parent', 'foreign-parent', 'older-parent'):
        parent = None if mutation == 'absent-parent' else 'foreign-hypothesis' if mutation == 'foreign-parent' else 'older-hypothesis'
        candidate['parent_hypothesis_id'] = fact['parent_hypothesis_id'] = parent
        if mutation == 'older-parent':
            old = copy.deepcopy(data.fact_payloads['fact-hyp-1'])
            old.update(fact_id='older-fact', hypothesis_id=parent)
            data.fact_payloads['older-fact'] = old
    elif mutation == 'empty-relation':
        candidate['relation'] = fact['relation'] = ''
    elif mutation == 'wrong-fact-parent':
        fact['parent_hypothesis_id'] = 'hyp-2'
    elif mutation == 'missing-context':
        _inputs(request, [])
    elif mutation in ('changed-batch', 'older-context', 'fake-selected-tip'):
        name, value = {'changed-batch': ('batch_command_id', 'different-batch'),
            'older-context': ('cycle', 0), 'fake-selected-tip': ('selected_tip', {'node_id': 'hyp-1'})}[mutation]
        _replace_input(request, '--batch-context', lambda context: context.update({name: value}))
    elif mutation == 'wrong-lineage-parent':
        _replace_input(request, '--lineage-context', lambda context: context.update(parent_hypothesis_id='hyp-1'))
    elif mutation == 'no-revision':
        data.attempt_payloads['update']['response']['typed_candidate']['update_disposition'] = 'TERMINATE_LINEAGE'
        _inputs(data.attempt_payloads['report']['request'],
            [_input('update--response', data.attempt_payloads['update']['response'])])
    elif mutation.startswith('missing-'):
        name = mutation.removeprefix('missing-')
        data.attempts = [row for row in data.attempts if row['attempt_id'] != name]
        data.attempt_payloads.pop(name)
    elif mutation == 'open-evolve':
        next(row for row in data.attempts if row['attempt_id'] == 'evolve')['state'] = 'OPEN'
    elif mutation == 'premature-start':
        next(row for row in data.attempts if row['attempt_id'] == 'next-synthesis')['started_at'] = 0
    else:
        contract = next(item for item in data.fact_payloads.values() if item['fact_type'] == 'GoalDefined')['research_contract']
        if mutation == 'cycle-budget':
            contract['methodology']['controls']['product']['goal']['stop_conditions']['max_rounds_per_expert'] = 1
        else:
            contract['budget']['max_experiments'] = 2
    assert data.audit()


def test_unselected_explicit_budgeted_revision_cannot_prematurely_complete_run():
    data, _ = _prior_cycle()
    data.transition('COMPLETE_RUN', {})['state'] = 'COMPLETED'
    assert any(issue[0] == 'batch_closure_invalid' for issue in data.audit())


def test_unselected_successor_dispatch_requires_prior_controls_even_before_acceptance():
    data = _successor(accept=False)
    next(row for row in data.attempts if row['attempt_id'] == 'next-synthesis')['started_at'] = 0
    assert any(issue[0] == 'batch_source_invalid' for issue in data.audit())


@pytest.mark.parametrize('version', [14, 15, 16, 17])
@pytest.mark.parametrize('parents', [('hyp-1', 'hyp-1'), ('hyp-1', 'hyp-2')])
def test_selected_successor_retains_exact_parent_for_every_aci(version, parents):
    issues = _successor(version, unselected=False, parents=parents).audit()
    assert bool(issues) is (parents[1] != 'hyp-1')


@pytest.mark.parametrize('parent', ['hyp-2', 'hyp-1', None])
def test_unselected_chain_node_uses_its_own_accepted_member_parent(parent):
    data = _successor()
    _, observation = data.agent('next-design', ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
        None, cycle=2, chain='next-chain-2', iteration=1, state='OPEN')
    request = observation['request']
    request['schema_version'] = 17
    _inputs(request, [_input(request['command_id'] + '--lineage-context',
        {'cycle': 2, 'parent_hypothesis_id': parent})])
    assert bool(data.audit()) is (parent != 'hyp-2')


@pytest.mark.parametrize('parent', [None, 'hyp-1'])
def test_unselected_current_shared_control_does_not_invent_one_member_parent(parent):
    data = _successor()
    _, observation = data.agent('next-adjudicate', ResearchNodeKind.ADJUDICATE,
        None, cycle=2, state='OPEN')
    request = observation['request']
    request['schema_version'] = 17
    context = {'batch_command_id': data.attempt_payloads['next-synthesis']['request']['command_id'],
        'cycle': 2, 'chains': {'next-chain-1': 'next-1', 'next-chain-2': 'next-2'},
        'terminal_tips': {'next-chain-1': None, 'next-chain-2': None}, 'selected_tip': None}
    _inputs(request, [_input(request['command_id'] + '--lineage-context',
        {'cycle': 2, 'parent_hypothesis_id': parent}),
        _input(request['command_id'] + '--batch-context', context)])
    assert bool(data.audit()) is (parent is not None)


def test_unselected_cycle_analysis_may_close_after_its_admitted_creation_budget_is_consumed():
    data = _successor()
    contract = next(item for item in data.fact_payloads.values() if item['fact_type'] == 'GoalDefined')['research_contract']
    contract['budget']['max_experiments'] = 4
    for index in (1, 2):
        _, observation = data.agent(f'next-execution-{index}', ResearchNodeKind.EXPERIMENT_EXECUTION,
            None, cycle=2, chain=f'next-chain-{index}', iteration=1, state='OPEN')
        request = observation['request']
        request['schema_version'] = 17
        _inputs(request, [_input(request['command_id'] + '--lineage-context',
            {'cycle': 2, 'parent_hypothesis_id': f'hyp-{index}'})])
        command_id, experiment = f'next-submit-{index}', f'next-experiment-{index}'
        data.commands.append({'run_id': data.run_id, 'command_id': command_id, 'workflow_id': experiment,
            'command_type': 'CREATE_EXPERIMENT', 'response_status': 'accepted',
            'request_json': json.dumps({'tool': 'submit_experiment', 'source_request': request,
                                       'execution_node_command_id': request['command_id']})})
        data.transition('CREATE_EXPERIMENT', {'hypothesis_id': f'next-{index}', 'component_id': 'primary.score',
            'role': 'researcher', 'method_family_id': 'family-1', 'expert_cycle': 2,
            'chain': f'next-chain-{index}', 'chain_iteration': 1, 'revision_of_outcome_id': None,
            'source_submit_command_id': command_id, 'source_agent_attempt_id': request['attempt_id'],
            'execution_node_command_id': request['command_id']}, workflow=experiment, kind='EXPERIMENT')
    _, observation = data.agent('next-analysis', ResearchNodeKind.RESULT_ANALYSIS, None,
        cycle=2, chain='next-chain-2', iteration=1, state='OPEN')
    request = observation['request']
    request['schema_version'] = 17
    _inputs(request, [_input(request['command_id'] + '--lineage-context',
        {'cycle': 2, 'parent_hypothesis_id': 'hyp-2'})])
    assert data.audit() == []


@pytest.mark.parametrize('mutation', ['missing', 'changed', 'duplicate', 'not-immutable'])
def test_unselected_proposal_parent_set_requires_exact_immutable_hypothesis_facts(mutation):
    data = _successor()
    request = data.attempt_payloads['next-synthesis']['request']
    item = next(value for value in request['input_artifacts'] if value['ref'] == 'fact-hyp-2')
    if mutation == 'missing':
        request['input_artifacts'].remove(item)
    elif mutation == 'changed':
        _replace_input(request, 'fact-hyp-2', lambda value: value['payload'].update(proposer_role='foreign-owner'))
    elif mutation == 'duplicate':
        value = json.loads(base64.b64decode(item['content_base64']))
        duplicate = _input('alias-hyp-2', value)
        request['input_artifacts'].append(duplicate.to_dict())
        request['immutable_context_refs'].append(duplicate.ref)
    else:
        request['immutable_context_refs'].remove('fact-hyp-2')
    assert any(issue[0] == 'batch_source_invalid' for issue in data.audit())
