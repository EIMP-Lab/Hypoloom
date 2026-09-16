import copy
from dataclasses import replace

import pytest

from hypoloom.domain import GoalVerdict
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.research_kernel import ResearchValidationError
from tests.runtime.test_profile_v4 import documents
from tests.runtime.test_research_kernel import build_until


def _accepted(*, shared, two=False, assessment="SUPPORTED"):
    kernel, view, facts = build_until(6)
    goal = view.rows[0]["payload"]["research_contract"]
    if two:
        goal["components"].append({**copy.deepcopy(goal["components"][0]), "id": "primary.other", "name": "other"})
        goal["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "2", "display_only": True}
        goal["protocols"] = {"score": {"result_kind": "metric", "requirements": {"required_metrics": ["score"]}}}
        for component in goal["components"]: component["protocol"] = "score"
    if shared:
        product = project_profile_v4(**documents()).settings
        product["goal"]["completion_mode"] = "shared_goal"
        goal["methodology"]["controls"]["product"] = product
    view.rows[-1]["payload"]["assessment"] = assessment
    return kernel, view, facts[-1]


def test_shared_goal_accepts_one_actual_supported_alternative_without_all_component_coverage():
    kernel, view, candidate = _accepted(shared=True, two=True)
    assert kernel.construct_fact(candidate, view).subject_id == "verdict-1"


def test_shared_goal_never_promotes_a_refuted_alternative_to_a_winner():
    kernel, view, candidate = _accepted(shared=True, assessment="REFUTED")
    with pytest.raises(ResearchValidationError, match="SUPPORTED"):
        kernel.construct_fact(candidate, view)


def test_legacy_goal_not_met_still_requires_the_existing_conclusive_family():
    kernel, view, candidate = _accepted(shared=False, assessment="REFUTED")
    with pytest.raises(ResearchValidationError, match="family"):
        kernel.construct_fact(replace(candidate, verdict=GoalVerdict.GOAL_NOT_MET, method_family_id=None), view)


def test_shared_goal_not_met_does_not_accept_supported_or_incomplete_alternatives():
    kernel, view, candidate = _accepted(shared=True, two=True)
    with pytest.raises(ResearchValidationError):
        kernel.construct_fact(replace(candidate, verdict=GoalVerdict.GOAL_NOT_MET, method_family_id=None), view)


def test_shared_goal_accepts_all_actual_refuted_alternatives_with_distinct_families():
    kernel, view, candidate = _accepted(shared=True, two=True, assessment="REFUTED")
    hypothesis = copy.deepcopy(view.rows[1])
    hypothesis.update(fact_id="other-hypothesis", subject_id="hypothesis-2", hypothesis_id="hypothesis-2")
    hypothesis["payload"].update(fact_id="other-hypothesis", hypothesis_id="hypothesis-2", component_id="primary.other", method_family_id="family-2")
    assessment = copy.deepcopy(view.rows[-1])
    assessment.update(fact_id="other-assessment", subject_id="assessment-2", hypothesis_id="hypothesis-2")
    assessment["payload"].update(fact_id="other-assessment", hypothesis_id="hypothesis-2", assessment_id="assessment-2", component_id="primary.other", method_family_id="family-2")
    view.rows.extend((hypothesis, assessment))
    mutation = kernel.construct_fact(replace(candidate, verdict=GoalVerdict.GOAL_NOT_MET, method_family_id=None,
        assessment_ids=("assessment-1", "assessment-2")), view)
    assert mutation.payload["method_family_id"] is None
