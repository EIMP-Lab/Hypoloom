"""Present ADJUDICATE's existing reference domain at its actual output fields."""

from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import AgentACIError, EmbeddedInput, canonical_json, validate_postflight
from hypoloom.claude_agent_host import _output_schema, _prompt
from hypoloom.domain import AgentCommand, fact_to_dict

from test_agent_aci import _hypothesis_fact
from test_agent_aci14 import _adjudication, _request, _response
from test_domain import all_facts


def _fact_input(payload):
    return EmbeddedInput.from_bytes(payload['fact_id'], 'application/json', canonical_json({
        'fact_id': payload['fact_id'], 'fact_type': payload['fact_type'], 'payload': payload}))


def _reference_request(version=17):
    request = replace(_request(AgentCommand.ADJUDICATE), schema_version=version)
    hypotheses = [_hypothesis_fact(identity, parent_hypothesis_id=None, method_family_id='family-1')
                  for identity in ('hyp-a', 'hyp-b')]
    for field, identity in (('component_id', 'hyp-other-component'), ('proposer_role', 'hyp-other-role')):
        source = _hypothesis_fact(identity, parent_hypothesis_id=None, method_family_id='family-1')
        payload = json.loads(source.content_bytes())['payload']
        payload[field] = 'other'
        hypotheses.append(_fact_input(payload))
    outcome = fact_to_dict(all_facts()[3])
    outcome.update(fact_id='fact-out-a', outcome_id='out-a', experiment_id='exp-a', artifact_id='artifact-a',
                   run_id=request.run_id, component_id=request.component_id)
    foreign = {**outcome, 'fact_id': 'fact-out-other', 'component_id': 'other',
               'outcome_id': 'out-other', 'experiment_id': 'exp-other', 'artifact_id': 'artifact-other'}
    inputs = (EmbeddedInput.from_bytes('context-note', 'text/plain', b'Existing immutable context.'),
              *hypotheses, _fact_input(outcome), _fact_input(foreign))
    request = replace(request, input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs))
    expected = {*request.immutable_context_refs, 'hyp-a', 'hyp-b', 'out-a', 'exp-a', 'artifact-a'}
    return request, expected


def _candidate(reference, field):
    value = _adjudication()
    value['verdicts'] = value['verdicts'][:1]
    value['prune'] = []
    value['verdicts'][0][field] = [reference] if field == 'evidence' else reference
    return value


@pytest.mark.parametrize('tool_workspace', [False, True])
def test_adjudication_prompt_names_real_reference_fields_and_summary(tool_workspace):
    request, _ = _reference_request()
    before = request.to_dict()
    assembled = json.loads(_prompt(request, tool_workspace=tool_workspace))
    instruction = assembled['instruction']
    assert 'AdjudicationCandidate' in instruction
    assert 'verdicts[].evidence' in instruction
    assert 'node_id' in instruction
    assert 'summary' in instruction and 'explanations' in instruction.lower()
    assert 'Complete only the typed task obligation.' not in instruction
    assert 'Complete only the typed ADJUDICATE obligation' not in instruction
    assert 'cite only exact supplied immutable context refs in supporting_refs' not in instruction
    for source, rendered in zip(request.input_artifacts, assembled['rendered_inputs'], strict=True):
        assert rendered['ref'] == source.ref
        assert rendered['content_utf8'].encode() == source.content_bytes()
    assert request.to_dict() == before


@pytest.mark.parametrize('version', [14, 16, 17])
def test_adjudication_schema_projects_the_complete_existing_postflight_domain(version):
    request, expected = _reference_request(version)
    schema = _output_schema(request)['properties']['typed_candidate']
    fields = schema['properties']['verdicts']['items']['properties']
    assert set(fields['node_id']['enum']) == expected
    assert set(fields['evidence']['items']['enum']) == expected
    assert 'supporting_refs' not in schema['properties']
    assert schema['additionalProperties'] is False
    assert fields['status']['enum'] == ['verified', 'refuted', 'inconclusive']


@pytest.mark.parametrize('field', ['node_id', 'evidence'])
@pytest.mark.parametrize('reference', ['context-note', 'out-a', 'fact-out-other'])
def test_existing_domain_does_not_narrow_node_ids_or_explicit_immutable_refs(field, reference):
    request, expected = _reference_request()
    assert reference in expected
    # Even an immutable foreign fact envelope is already admissible as a ref;
    # its unadmitted canonical payload identifiers must not be derived below.
    validate_postflight(request, _response(request, _candidate(reference, field)))


@pytest.mark.parametrize('reference', [
    'The repaired experiment now works.', 'hyp-other-component', 'hyp-other-role',
    'out-other', 'exp-other', 'artifact-other',
])
def test_existing_validator_rejects_prose_and_foreign_derived_identifiers(reference):
    request, expected = _reference_request()
    assert reference not in expected
    with pytest.raises(AgentACIError, match='outside the admitted input domain'):
        validate_postflight(request, _response(request, _candidate(reference, 'evidence')))
