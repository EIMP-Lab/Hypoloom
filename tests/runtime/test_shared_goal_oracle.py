from dataclasses import replace

import pytest

from hypoloom.oracle import _ComponentResult, _audit_component_verdicts
from tests.runtime.test_component_oracle import _contract_for_verdicts


def _issues(statuses, *, verdict, cited, family, closed=("primary.a", "primary.b"), shared=True):
    contract = _contract_for_verdicts()
    contract = replace(contract, components=contract.components[:2])
    rows = {component: {"fact_id": "fact-" + component, "subject_id": "assessment-" + component,
                       "hypothesis_id": "hypothesis-" + component}
            for component in contract.required_ids}
    payloads = {row["fact_id"]: {"component_id": component, "hypothesis_id": row["hypothesis_id"],
                               "method_family_id": "family-" + component, "assessment": status}
                for (component, row), status in zip(rows.items(), statuses)}
    payloads["verdict"] = {"assessment_ids": ["assessment-" + item for item in cited],
                           "method_family_id": family, "verdict": verdict}
    results = {row["subject_id"]: _ComponentResult("SUCCESS" if status == "SUPPORTED" else "CLEAN_REFUTATION",
        status == "SUPPORTED", "1", component, None) for (component, row), status in zip(rows.items(), statuses)}
    issues = []
    _audit_component_verdicts(contract, [{"fact_type": "GoalVerdictRecorded", "fact_id": "verdict"}], payloads,
        {row["subject_id"]: row for row in rows.values()}, rows, results, issues,
        completion_mode="shared_goal" if shared else "required_conjunction", terminated_component_ids=frozenset(closed))
    return {issue.code for issue in issues}


def test_shared_goal_oracle_accepts_one_supported_alternative_with_distinct_implementations():
    assert not _issues(("SUPPORTED", "REFUTED"), verdict="GOAL_MET", cited=("primary.a",), family="family-primary.a")


def test_shared_goal_oracle_accepts_all_refuted_families_without_inventing_one_family():
    assert not _issues(("REFUTED", "REFUTED"), verdict="GOAL_NOT_MET", cited=("primary.a", "primary.b"), family=None)


@pytest.mark.parametrize("changes", [
    {"cited": ("primary.b",), "family": "family-primary.b"},
    {"closed": ("primary.b",)},
    {"cited": ("primary.a", "primary.b")},
])
def test_shared_goal_oracle_rejects_wrong_winner_unclosed_winner_or_surplus_refs(changes):
    args = {"verdict": "GOAL_MET", "cited": ("primary.a",), "family": "family-primary.a", **changes}
    assert _issues(("SUPPORTED", "REFUTED"), **args)


def test_shared_goal_tie_uses_stable_hypothesis_identity_not_other_score_or_completion():
    assert not _issues(("SUPPORTED", "SUPPORTED"), verdict="GOAL_MET", cited=("primary.a",), family="family-primary.a")
    assert _issues(("SUPPORTED", "SUPPORTED"), verdict="GOAL_MET", cited=("primary.b",), family="family-primary.b")


def test_legacy_oracle_does_not_acquire_shared_goal_coverage_or_family_semantics():
    assert _issues(("SUPPORTED", "REFUTED"), verdict="GOAL_MET", cited=("primary.a",), family="family-primary.a", shared=False)
    assert _issues(("REFUTED", "REFUTED"), verdict="GOAL_NOT_MET", cited=("primary.a", "primary.b"), family=None, shared=False)
