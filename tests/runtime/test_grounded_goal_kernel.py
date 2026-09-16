import base64
import json
from dataclasses import replace

import pytest

from hypoloom.domain import ComponentResultKind, GoalVerdict, HypothesisAssessment, ScientificOutcome, fact_to_dict
from hypoloom.research_kernel import ResearchKernel, ResearchValidationError
from tests.runtime.test_deferred_measurement import deferred_contract
from tests.runtime.test_measurement_contract import declaration
from tests.runtime.test_research_kernel import MemoryResearchView, candidates
from tests.runtime.test_shared_acceptance import acceptance_dict


def goal_contract():
    raw = deferred_contract()
    raw["schema_version"] = 3
    raw["goal"]["evaluation"] = {"mode": "grounding"}
    return raw


def binding(scope="GOAL"):
    return {"scope": scope, "source_attempt_id": "grounding-1", "acceptance": acceptance_dict()}


def shared_result(*, alpha=".7", causal=True):
    return {"source_attempt_id": "grounding-1", "measurements": {"alpha": alpha, "beta": ".8"}, "checks": {"causal": causal}}


class GroundedView(MemoryResearchView):
    def __init__(self):
        super().__init__()
        self.attempts = [{"attempt_id": "grounding-1", "run_id": "run-1", "workflow_id": "run-1", "kind": "AGENT",
            "state": "CLOSED", "terminal_reason": "SUCCEEDED", "command_id": "grounding-command", "observation_json": json.dumps({
                "request": {"schema_version": 16, "run_id": "run-1", "owner_workflow_id": "run-1", "attempt_id": "grounding-1",
                    "command": "GROUNDING", "command_id": "grounding-command"},
                "response": {"typed_candidate": {"acceptance": acceptance_dict(), "background": {"project_summary": "Project"}, "clarification": None},
                    "proposed_workspace_artifacts": [{"path": "parse_goal.py", "content_base64": base64.b64encode(b"print('{}')").decode()}]}})}]

    def get_attempt(self, attempt_id):
        return next((row for row in self.attempts if row["attempt_id"] == attempt_id), None)

    def list_attempts(self, *, workflow_id=None, **kwargs):
        return tuple(row for row in self.attempts if workflow_id is None or row["workflow_id"] == workflow_id)


def setup(*, local=True, scope="GOAL"):
    facts = list(candidates())
    raw = goal_contract()
    # Use this test lineage's original stable component identity.
    raw["components"][0]["id"] = "primary.score"
    raw["components"][0]["name"] = "score"
    raw["components"][0]["owner"] = "researcher"
    raw["components"][0]["proposers"] = ["researcher"]
    raw["roles"][0]["id"] = "researcher"
    facts[0] = replace(facts[0], research_contract=raw)
    measure = declaration() if local else None
    facts[1] = replace(facts[1], measurement=measure)
    facts[2] = replace(facts[2], result_kind=ComponentResultKind.METRIC if local else ComponentResultKind.ARTIFACT,
        metric=None if measure is None else measure.metric, measurement=measure, goal_evaluation=binding(scope))
    facts[3] = replace(facts[3], result_kind=facts[2].result_kind, metric_value="1" if local else None,
        goal_evaluation=shared_result() if scope == "GOAL" else None)
    kernel, view = ResearchKernel(), GroundedView()
    return kernel, view, facts


def until(kernel, view, facts, count):
    for fact in facts[:count]:
        view.add(kernel.construct_fact(fact, view))


def test_every_preregistration_binds_the_exact_accepted_grounding():
    kernel, view, facts = setup(scope="LOCAL")
    until(kernel, view, facts, 3)
    assert view.rows[-1]["payload"]["goal_evaluation"] == binding("LOCAL")
    assert "goal_evaluation" not in fact_to_dict(candidates()[2])
    assert "goal_evaluation" not in fact_to_dict(candidates()[3])


@pytest.mark.parametrize("tamper", ["missing-binding", "wrong-source", "changed-target", "wrong-run", "open-source", "question", "missing-file", "duplicate-final"])
def test_preregistration_rejects_unresolved_or_substituted_common_source(tamper):
    kernel, view, facts = setup()
    until(kernel, view, facts, 2)
    candidate = facts[2]
    if tamper == "missing-binding": candidate = replace(candidate, goal_evaluation=None)
    elif tamper == "wrong-source": candidate = replace(candidate, goal_evaluation={**binding(), "source_attempt_id": "absent"})
    elif tamper == "changed-target":
        changed = binding(); changed["acceptance"]["criteria"][0]["target"] = ".1"
        candidate = replace(candidate, goal_evaluation=changed)
    elif tamper == "wrong-run": view.attempts[0]["run_id"] = "other"
    elif tamper == "open-source": view.attempts[0]["state"] = "OPEN"
    elif tamper == "duplicate-final": view.attempts.append({**view.attempts[0], "attempt_id": "grounding-2"})
    else:
        observation = json.loads(view.attempts[0]["observation_json"])
        if tamper == "question": observation["response"]["typed_candidate"]["clarification"] = {"question": "Unresolved"}
        else: observation["response"]["proposed_workspace_artifacts"] = []
        view.attempts[0]["observation_json"] = json.dumps(observation)
    with pytest.raises(ResearchValidationError):
        kernel.construct_fact(candidate, view)


def test_goal_only_artifact_experiment_needs_no_local_numeric_measurement():
    kernel, view, facts = setup(local=False)
    until(kernel, view, facts, 4)
    assert view.rows[2]["payload"]["result_kind"] == "artifact"
    assert view.rows[2]["payload"]["metric"] is None
    assert "measurement" not in view.rows[2]["payload"]


def test_local_experiment_cannot_skip_local_measurement_or_claim_goal_result():
    kernel, view, facts = setup(local=False, scope="LOCAL")
    until(kernel, view, facts, 2)
    with pytest.raises(ResearchValidationError, match="measurement"):
        kernel.construct_fact(facts[2], view)
    kernel, view, facts = setup(scope="LOCAL")
    until(kernel, view, facts, 3)
    with pytest.raises(ResearchValidationError, match="GOAL"):
        kernel.construct_fact(replace(facts[3], goal_evaluation=shared_result()), view)


def test_common_success_can_cite_a_locally_refuted_assessment():
    kernel, view, facts = setup()
    facts[3] = replace(facts[3], outcome=ScientificOutcome.CLEAN_REFUTATION)
    facts[5] = replace(facts[5], assessment=HypothesisAssessment.REFUTED)
    until(kernel, view, facts, 6)
    assert kernel.construct_fact(facts[6], view).payload["verdict"] == "GOAL_MET"


@pytest.mark.parametrize("observation", [None, shared_result(alpha=".6674"), shared_result(causal=False)])
def test_local_support_cannot_replace_shared_goal_success(observation):
    kernel, view, facts = setup()
    facts[3] = replace(facts[3], goal_evaluation=observation)
    until(kernel, view, facts, 6)
    with pytest.raises(ResearchValidationError, match="shared"):
        kernel.construct_fact(facts[6], view)


def test_missing_or_invalid_shared_result_is_not_goal_not_met():
    for value in (None, shared_result(causal=False)):
        kernel, view, facts = setup()
        facts[3] = replace(facts[3], goal_evaluation=value)
        facts[5] = replace(facts[5], assessment=HypothesisAssessment.REFUTED)
        until(kernel, view, facts, 6)
        with pytest.raises(ResearchValidationError, match="shared"):
            kernel.construct_fact(replace(facts[6], verdict=GoalVerdict.GOAL_NOT_MET), view)


def test_legacy_contract_cannot_adopt_new_grounding_fields():
    kernel, view, facts = ResearchKernel(), GroundedView(), candidates()
    until(kernel, view, facts, 2)
    with pytest.raises(ResearchValidationError):
        kernel.construct_fact(replace(facts[2], goal_evaluation=binding()), view)


@pytest.mark.parametrize("reason", [ScientificOutcome.INTEGRITY_REJECTED, ScientificOutcome.CANCELLED])
def test_rejected_or_cancelled_outcome_cannot_publish_qualifying_common_observations(reason):
    kernel, view, facts = setup()
    until(kernel, view, facts, 3)
    with pytest.raises(ResearchValidationError, match="shared"):
        kernel.construct_fact(replace(facts[3], outcome=reason, metric_value=None), view)
