"""Best means a comparable accepted measurement, never a global expert score."""
import copy
import json

import pytest

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.observation_api import product_events


def sources(values, *, direction="MAXIMIZE", deferred=False):
    facts, commands = [], {}
    for number, value in enumerate(values, 1):
        experiment = f"e{number}"
        metric = {"name": "score", "direction": direction, "target": "5"}
        spec = {"experiment_id": experiment, "component_id": "component-a", "result_kind": "metric",
            "metric": metric, "trial_plan": {"seeds": [1]}, "upstream_outcomes": [], "evaluator_version": "component-result/8",
            "execution_contract": {"source_submit_command_id": f"s{number}"}}
        if deferred:
            spec["measurement"] = {"metric": metric, "comparison": "VALUE", "parse_rule": "read score",
                "parser_files": ["parse.py"], "parser_argv": ["python", "parse.py"], "raw_artifacts": ["raw.json"]}
            commands[f"s{number}"] = {"command_id": f"s{number}", "command_type": "CREATE_EXPERIMENT", "response_status": "accepted",
                "request": {"tool": "submit_experiment", "source_snapshot": {"arguments": {
                    "workspace_files": [WorkspaceArtifact.from_bytes("parse.py", b"print('score')\n").to_dict()]}}}}
        facts.append({"fact_id": "spec-" + experiment, "fact_type": "ExperimentPreregistered", "created_at": number * 2, "payload": spec})
        facts.append({"fact_id": "fact-" + experiment, "fact_type": "CanonicalOutcomeAccepted", "created_at": number * 2 + 1,
            "payload": {"experiment_id": experiment, "component_id": "component-a", "outcome_id": "o" + str(number),
                "result_kind": "metric", "outcome": "SUCCESS", "metric_value": str(value)}})
    return facts, commands


def best(facts, commands):
    return [event for event in product_events("run", facts, [], [], commands=commands) if event["type"] == "new_best_result"]


@pytest.mark.parametrize("direction,values,expected", [("MAXIMIZE", [1, 2, 2, 1, 3], ["o1", "o2", "o5"]),
    ("MINIMIZE", [3, 2, 2, 4, 1], ["o1", "o2", "o5"])])
def test_only_strict_improvements_emit_after_an_accepted_comparable_result(direction, values, expected):
    facts, commands = sources(values, direction=direction)
    events = best(facts, commands)
    assert [event["payload"]["outcome_id"] for event in events] == expected
    assert events[0]["payload"]["previous_outcome_id"] is None
    assert events[1]["payload"]["previous_outcome_id"] == "o1"
    assert [event["payload"]["previous_metric_value"] for event in events] == [None, str(values[0]), str(values[1])]
    assert [event["event_id"] for event in events] == [f"best-result:fact-e{i}:1" for i in (1, 2, 5)]
    assert all(event["payload"]["component_id"] == "component-a" for event in events)


def test_components_and_frozen_evaluation_conditions_have_independent_best_values():
    facts, commands = sources([100, 1, 2])
    facts[2]["payload"]["component_id"] = facts[3]["payload"]["component_id"] = "component-b"
    facts[4]["payload"]["trial_plan"]["seeds"] = [2]
    events = best(facts, commands)
    assert len(events) == 3
    assert all(event["payload"]["previous_outcome_id"] is None for event in events)


def test_same_metric_label_with_different_parser_bytes_is_not_comparable():
    facts, commands = sources([100, 1, 2], deferred=True)
    commands["s2"]["request"]["source_snapshot"]["arguments"]["workspace_files"] = [
        WorkspaceArtifact.from_bytes("parse.py", b"print('different calculation')\n").to_dict()]
    commands["s3"] = {**commands["s3"], "request": copy.deepcopy(commands["s2"]["request"])}
    events = best(facts, commands)
    assert [event["payload"]["previous_outcome_id"] for event in events] == [None, None, "o2"]
    assert "parser" not in json.dumps([event["payload"] for event in events])


def test_failed_and_artifact_results_never_become_best_and_missing_parser_is_explicit():
    facts, commands = sources([1, 100, 200])
    facts[3]["payload"].update(outcome="EXECUTION_FAILURE", metric_value=None)
    facts[5]["payload"].update(result_kind="artifact", metric_value=None)
    assert len(best(facts, commands)) == 1
    facts, commands = sources([1], deferred=True)
    with pytest.raises(ValueError, match="parser"):
        best(facts, {})


@pytest.mark.parametrize("direction,values", [("MAXIMIZE", [-1, 0, 5]), ("MINIMIZE", [7, 6, 5])])
@pytest.mark.parametrize("scope", [None, "LOCAL", "GOAL"])
def test_metric_threshold_and_declared_scope_do_not_infer_a_goal_verdict(direction, values, scope):
    facts, commands = sources(values, direction=direction, deferred=True)
    for spec, outcome in zip(facts[::2], facts[1::2]):
        if scope is not None:
            spec["payload"]["goal_evaluation"] = {"scope": scope}
        spec["payload"]["measurement"]["comparison"] = "DELTA_FROM_BASELINE"
        outcome["payload"]["outcome"] = "CLEAN_REFUTATION" if outcome["payload"]["metric_value"] != "5" else "SUCCESS"
    events = product_events("run", facts, [], [], commands=commands)
    measurements = [event["payload"] for event in events if event["type"] == "new_best_result"]
    assert len(measurements) == 3
    assert [item["metric_target_met"] for item in measurements] == [False, False, True]
    assert [item["previous_metric_value"] for item in measurements] == [None, str(values[0]), str(values[1])]
    assert all(item["evaluation_scope"] == scope and item["measurement_comparison"] == "DELTA_FROM_BASELINE" for item in measurements)
    assert not any(event["type"] == "goal_verdict_recorded" for event in events)
