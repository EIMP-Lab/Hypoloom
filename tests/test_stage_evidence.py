"""Stage decisions must follow the frozen units through production and independent replay."""
from dataclasses import replace

import jsonschema
import pytest

from hypoloom.agent_aci import AgentACIError, _validate_measurement_version
from hypoloom.claude_agent_host import _measurement_schema
from hypoloom.domain import MetricDirection, MetricSpec
from hypoloom.fake_agent import default_request
from hypoloom.measurement_contract import (
    MeasurementComparison, ScientificMeasurement, StageEvidence, assert_frozen_measurement, evaluate_parsed_measurement,
)
from hypoloom.measurement_oracle import interpret_measurement


def declaration(reduction="WORST", direction="MAXIMIZE", comparison="VALUE"):
    return ScientificMeasurement(metric=MetricSpec(name="effect", direction=MetricDirection(direction), target="0"),
        success_operator="GT" if direction == "MAXIMIZE" else "LT", comparison=MeasurementComparison(comparison),
        evidence=StageEvidence("CONFIRMATION", ("B", "C"), reduction), parse_rule="Measure each named fold",
        parser_argv=("python", "/input/p.py"), parser_files=("p.py",), raw_artifacts=("folds.json",))


def check(spec, rows, value, passed):
    raw = {"metric": "effect", "observations": rows}
    host = evaluate_parsed_measurement(spec, raw)
    replay = interpret_measurement(spec.to_dict(), raw)
    assert host.metric_value == replay.metric_value == value
    assert host.criterion_met is replay.criterion_met is passed
    assert replay.parsed_result == raw


def test_positive_screen_and_failed_fresh_confirmation_are_distinct():
    spec = declaration()
    screen = replace(spec, evidence=StageEvidence("SCREENING", ("A",), "WORST"))
    check(screen, {"A": {"value": ".03"}}, "0.03", True)
    check(spec, {"C": {"value": "-.01"}, "B": {"value": ".04"}}, "-0.01", False)
    check(declaration("MEAN"), {"C": {"value": "-.01"}, "B": {"value": ".04"}}, "0.015", True)
    check(declaration(direction="MINIMIZE"), {"B": {"value": "-.03"}, "C": {"value": 0}}, "0", False)
    with pytest.raises(ValueError, match="measurement_frozen"):
        assert_frozen_measurement(screen, spec, {"p.py": b"same"}, {"p.py": b"same"})


@pytest.mark.parametrize("comparison,expected", [("DELTA_FROM_BASELINE", "0.5"),
    ("RELATIVE_DELTA_FROM_BASELINE", "0.075")])
def test_pairs_transformed_before_equal_weight_mean(comparison, expected):
    check(declaration("MEAN", comparison=comparison),
          {"B": {"value": -8, "baseline": -10}, "C": {"value": 19, "baseline": 20}}, expected, True)


def test_repeating_ratio_compares_the_precision_published_in_the_canonical_fact():
    target = "0.3333333333333333333333333333"
    spec = replace(declaration(comparison="RELATIVE_DELTA_FROM_BASELINE"),
        metric=MetricSpec(name="effect", direction=MetricDirection.MAXIMIZE, target=target))
    check(spec, {u: {"value": 4, "baseline": 3} for u in ("B", "C")}, target, False)


@pytest.mark.parametrize("change", [{"value": ".3"}, {"observations": {"A": {"value": 1}}},
    {"observations": {"B": {"value": 1}}}, {"observations": {"B": {"value": 1}, "C": {"value": 2}, "A": {"value": 5}}},
    {"observations": {"B": 2, "C": 3}}, {"observations": {"B": {"value": 1}, "C": {"value": "NaN"}}},
    {"observations": {"B": {"value": 1}, "C": {"value": True}}}, {"criterion_met": True}])
def test_invalid_stage_evidence_cannot_supply_a_scientific_outcome(change):
    spec = declaration()
    raw = {"metric": "effect", "observations": {"B": {"value": 1}, "C": {"value": 2}}, **change}
    for evaluator, arg in ((evaluate_parsed_measurement, spec), (interpret_measurement, spec.to_dict())):
        with pytest.raises(ValueError):
            evaluator(arg, raw)


@pytest.mark.parametrize("pair", [{"value": 3}, {"value": 3, "baseline": 0}])
def test_relative_comparison_requires_each_units_nonzero_baseline(pair):
    spec = declaration(comparison="RELATIVE_DELTA_FROM_BASELINE")
    raw = {"metric": "effect", "observations": {"B": {"value": 2, "baseline": 1}, "C": pair}}
    for evaluator, arg in ((evaluate_parsed_measurement, spec), (interpret_measurement, spec.to_dict())):
        with pytest.raises(ValueError, match="baseline"):
            evaluator(arg, raw)


def test_stage_contract_is_versioned_and_optional():
    spec = declaration()
    raw = spec.to_dict()
    assert ScientificMeasurement.from_dict(raw) == spec
    jsonschema.validate(raw, _measurement_schema(40))
    _validate_measurement_version(replace(default_request(), schema_version=40), spec)
    with pytest.raises(AgentACIError, match="ACI40"):
        _validate_measurement_version(replace(default_request(), schema_version=39), spec)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, _measurement_schema(39))
    scalar = replace(spec, evidence=None)
    assert "evidence" not in scalar.to_dict()
    with pytest.raises(ValueError):
        evaluate_parsed_measurement(scalar, {"metric": "effect", "observations": {"A": {"value": 1}}})
    with pytest.raises(ValueError):
        evaluate_parsed_measurement(spec, {"metric": "effect", "value": 1})


@pytest.mark.parametrize("plan", [None, {}, {"stage": "FINAL"}, {"units": []}, {"units": "A"},
    {"units": ["A", "A"]}, {"units": [" A"]}, {"reduction": "BEST"}, {"other": 1}])
def test_invalid_plan_rejected(plan):
    raw = declaration().to_dict()
    raw["evidence"] = {**raw["evidence"], **plan} if plan else plan
    with pytest.raises(ValueError):
        ScientificMeasurement.from_dict(raw)
