"""Accepted Profile values must have explicit, non-lossy product semantics."""

import pytest

from hypoloom.profile_v4 import project_profile_v4, resolve_node_runtime
from test_profile_v4 import documents


@pytest.mark.parametrize("rounds", [1, 2, 4, 0, 3.0, True])
def test_debate_requires_exactly_three_typed_rounds(rounds):
    source = documents()
    source["methodology"]["interaction"]["debate"]["rounds"] = rounds
    result = project_profile_v4(**source)
    assert any(issue.location == "methodology.yaml.interaction.debate.rounds"
               and "exactly three" in issue.message for issue in result.issues)


def test_three_rounds_and_omitted_round_count_keep_the_fixed_product():
    source = documents()
    assert not project_profile_v4(**source).issues
    source["methodology"]["interaction"]["debate"].pop("rounds")
    assert not project_profile_v4(**source).issues


def test_personal_grounding_override_warns_without_changing_neutral_runtime():
    source = documents()
    source["experts"]["experts"][0]["stage_overrides"]["grounding"] = {
        "model": "personal-grounding-model", "effort": "low"}
    result = project_profile_v4(**source)
    assert not result.issues
    warning = next(item for item in result.warnings if item.location ==
                   "experts.yaml.experts[0].stage_overrides.grounding")
    assert warning.code == "inactive_feature"
    assert "role-neutral" in warning.message and "not applied" in warning.message
    assert "experts.yaml.grounding" in warning.message
    assert result.settings["experts"] == source["experts"]
    runtime = resolve_node_runtime({"methodology": {"controls": {"product": result.settings}}},
                                   None, "grounding")
    assert runtime["model"] == "configured-model" and runtime["effort"] == "high"


@pytest.mark.parametrize("events", [[], ["run_finished"]])
def test_dashboard_event_selector_is_preserved_without_inactive_warning(events):
    source = documents()
    source["notify"]["events"]["dashboard"] = events
    result = project_profile_v4(**source)
    assert not result.issues
    assert not any(item.location == "notify.yaml.events.dashboard" for item in result.warnings)
    assert result.settings["notify"]["events"]["dashboard"] == events


@pytest.mark.parametrize("trigger", [None, "expert_round", "cycle_report"])
def test_whole_expert_round_trigger_and_legacy_alias_are_implemented(trigger):
    source = documents()
    leader = source["methodology"]["interaction"]["leader"]
    if trigger is None:
        leader.pop("check_after")
    else:
        leader["check_after"] = trigger
    result = project_profile_v4(**source)
    assert not result.issues
    assert not any(item.location.endswith("leader.check_after") for item in result.warnings)


@pytest.mark.parametrize("override,model,effort", [
    ({}, "configured-model", "high"),
    ({"model": "shared-grounding"}, "shared-grounding", "high"),
    ({"effort": "low"}, "configured-model", "low"),
    ({"model": "shared-grounding", "effort": "medium"}, "shared-grounding", "medium"),
])
def test_shared_grounding_configuration_inherits_only_missing_defaults(override, model, effort):
    source = documents()
    source["experts"]["grounding"] = override
    source["experts"]["experts"][0].update(model="private-expert", effort="max")
    result = project_profile_v4(**source)
    assert not result.issues and not result.warnings
    assert result.settings["experts"]["grounding"] == override
    contract = {"methodology": {"controls": {"product": result.settings}}}
    runtime = resolve_node_runtime(contract, None, "grounding")
    assert runtime["model"] == model and runtime["effort"] == effort
    assert resolve_node_runtime(contract, "scientist", "grounding") == runtime
    # Role-neutral auxiliary nodes are not all Step-1 Grounding.
    for route in ("observer", "graph_evolve", "debate_judge", "final_report"):
        neutral = resolve_node_runtime(contract, None, route)
        assert neutral["model"] == "configured-model" and neutral["effort"] == "high"
    expert = resolve_node_runtime(contract, "scientist", "literature_review.main")
    assert expert["model"] == "private-expert" and expert["effort"] == "max"


@pytest.mark.parametrize("override", [None, [], {"model": ""}, {"effort": "invalid"},
    {"max_forks": 2}, {"persona": "not an expert"}])
def test_shared_grounding_configuration_rejects_invalid_or_expert_only_fields(override):
    source = documents()
    source["experts"]["grounding"] = override
    result = project_profile_v4(**source)
    assert any(item.location.startswith("experts.yaml.grounding") for item in result.issues)


def test_null_stop_target_is_equivalent_to_an_unset_target():
    source = documents()
    expected = project_profile_v4(**source)
    source["goal"]["stop_conditions"]["target_metric"] = None
    result = project_profile_v4(**source)
    assert not result.issues
    assert result.projection == expected.projection
    assert result.settings == expected.settings


@pytest.mark.parametrize("metric", [None, {}, {"name": "accuracy"}, {"direction": "maximize"}])
def test_numeric_stop_target_requires_its_metric_name_and_direction(metric):
    source = documents()
    source["goal"]["metric"] = metric
    source["goal"]["stop_conditions"]["target_metric"] = 0.9
    result = project_profile_v4(**source)
    assert any(issue.location.startswith("goal.yaml.") for issue in result.issues)


@pytest.mark.parametrize("metric_target,stop_target", [(0.5, 0.9), (1, 2)])
def test_conflicting_numeric_targets_are_rejected(metric_target, stop_target):
    source = documents()
    source["goal"]["metric"] = {"name": "accuracy", "direction": "maximize", "target": metric_target}
    source["goal"]["stop_conditions"]["target_metric"] = stop_target
    result = project_profile_v4(**source)
    assert any(issue.location == "goal.yaml.stop_conditions.target_metric"
               and "conflict" in issue.message for issue in result.issues)


@pytest.mark.parametrize("metric_target,stop_target,expected", [
    (None, 0.9, 0.9), (0.9, None, 0.9), (0.9, 0.9, 0.9), (1, 1.0, 1), (-0.1, -0.1, -0.1)])
def test_nonconflicting_targets_bind_the_existing_scientific_criterion(metric_target, stop_target, expected):
    source = documents()
    source["goal"]["metric"] = {"name": "accuracy", "direction": "maximize", "target": metric_target}
    source["goal"]["stop_conditions"]["target_metric"] = stop_target
    result = project_profile_v4(**source)
    assert not result.issues
    criterion = result.projection.methodology["success_criteria"]["primary"][0]
    assert criterion["target"] == expected and "deferred" not in criterion


@pytest.mark.parametrize("target", [True, "0.9", float("nan"), float("inf")])
def test_target_validation_remains_finite_and_numeric(target):
    source = documents()
    source["goal"]["metric"] = {"name": "accuracy", "direction": "maximize", "target": target}
    result = project_profile_v4(**source)
    assert result.issues
