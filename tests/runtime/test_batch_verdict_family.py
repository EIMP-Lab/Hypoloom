from dataclasses import replace

import pytest

from hypoloom.oracle import _audit_component_verdicts, _row_identifier
from tests.runtime.test_component_oracle import _contract_for_verdicts


@pytest.mark.parametrize("selected_specs,expected", [
    (None, "created-family"),  # Retained single-lineage read semantics.
    ({}, None),  # Accepted batch adjudication explicitly selected nothing.
    ({"primary.a": {"fact_id": "selected-spec"}}, "selected-family"),
])
@pytest.mark.parametrize("tampered", [False, True])
def test_verdict_family_obeys_batch_selection_without_changing_legacy(selected_specs, expected, tampered):
    contract = _contract_for_verdicts()
    contract = replace(contract, components=contract.components[:1])
    transition = {"workflow_kind": "EXPERIMENT", "workflow_id": "latest-experiment", "revision": 1}
    facts = [{"fact_type": "GoalVerdictRecorded", "fact_id": "verdict"},
             {"fact_type": "HypothesisProposed", "fact_id": "hypothesis", "hypothesis_id": "h1"}]
    payloads = {
        "hypothesis": {"component_id": "primary.a", "method_family_id": "created-family"},
        "selected-spec": {"component_id": "primary.a", "method_family_id": "selected-family"},
        "verdict": {"verdict": "INCONCLUSIVE", "assessment_ids": [],
                    "method_family_id": "wrong-family" if tampered else expected},
    }
    issues = []
    _audit_component_verdicts(contract, facts, payloads, {}, {}, {}, issues,
        transitions=[transition], transition_payloads={_row_identifier(transition): {
            "component_id": "primary.a", "hypothesis_id": "h1", "experiment_ordinal": 2}},
        selected_specs=selected_specs)
    assert {issue.code for issue in issues} == ({"verdict_component_conjunction_mismatch"} if tampered else set())
