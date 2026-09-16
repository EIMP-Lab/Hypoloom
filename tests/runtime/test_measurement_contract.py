from dataclasses import replace

import pytest

from hypoloom.domain import MetricDirection, MetricSpec
from hypoloom.measurement_contract import (
    MeasurementComparison, MeasurementContractError, ScientificMeasurement,
    assert_frozen_measurement, evaluate_parsed_measurement,
)


def declaration():
    return ScientificMeasurement(
        metric=MetricSpec(name="median_latency_delta", direction=MetricDirection.MINIMIZE, target="-0.10"),
        comparison=MeasurementComparison.RELATIVE_DELTA_FROM_BASELINE,
        parse_rule="Read timings.csv and compare medians over all validated samples",
        parser_argv=("python", "/input/parse_metrics.py"),
        parser_files=("parse_metrics.py",), raw_artifacts=("timings.csv",),
    )


def test_declaration_round_trip_and_real_comparison():
    spec = declaration()
    assert ScientificMeasurement.from_dict(spec.to_dict()) == spec
    result = evaluate_parsed_measurement(spec, {"metric": spec.metric.name, "value": "8.5", "baseline": "10"})
    assert result.metric_value == "-0.15"
    assert result.criterion_met is True


@pytest.mark.parametrize("change", [
    {"parse_rule": "Read only the best sample"},
    {"comparison": MeasurementComparison.VALUE},
    {"parser_argv": ("python", "/input/parse_metrics.py", "--best")},
    {"metric": MetricSpec(name="other", direction=MetricDirection.MINIMIZE, target="-0.1")},
])
def test_first_preregistration_fields_are_frozen(change):
    spec = declaration()
    with pytest.raises(MeasurementContractError, match="measurement_frozen"):
        assert_frozen_measurement(spec, replace(spec, **change), {"parse_metrics.py": b"original"}, {"parse_metrics.py": b"original"})


def test_parser_bytes_frozen_but_experiment_code_may_be_repaired():
    spec = declaration()
    original = {"parse_metrics.py": b"original", "experiment.sh": b"bad"}
    assert_frozen_measurement(spec, spec, original, {**original, "experiment.sh": b"fixed"})
    with pytest.raises(MeasurementContractError, match="measurement_frozen"):
        assert_frozen_measurement(spec, spec, original, {**original, "parse_metrics.py": b"different"})


@pytest.mark.parametrize("path", ["../parse.py", "/parse.py", "x/../parse.py", "x//parse.py", "x/./parse.py", "x\\parse.py"])
def test_parser_paths_cannot_escape_or_alias(path):
    with pytest.raises(MeasurementContractError):
        replace(declaration(), parser_files=(path,))


@pytest.mark.parametrize("raw", [
    {"metric": "median_latency_delta", "value": "NaN", "baseline": "10"},
    {"metric": "median_latency_delta", "value": True, "baseline": "10"},
    {"metric": "median_latency_delta", "value": "8", "baseline": "0"},
    {"metric": "wrong", "value": "8", "baseline": "10"},
    {"metric": "median_latency_delta", "value": "8", "baseline": "10", "verified": True},
])
def test_parser_cannot_supply_nonfinite_values_or_a_verdict(raw):
    with pytest.raises(MeasurementContractError):
        evaluate_parsed_measurement(declaration(), raw)


@pytest.mark.parametrize("field,value", [("parser_files", (["not-a-path"],)),
                                       ("parser_argv", "python parser.py"),
                                       ("raw_artifacts", ("timings.csv", "timings.csv"))])
def test_invalid_collection_types_are_typed_errors(field, value):
    with pytest.raises(MeasurementContractError):
        replace(declaration(), **{field: value})


def test_parser_source_paths_cannot_collide_as_file_and_directory():
    with pytest.raises(MeasurementContractError):
        replace(declaration(), parser_files=("parser", "parser/main.py"))


@pytest.mark.parametrize("comparison,expected", [(MeasurementComparison.VALUE, "8.5"),
                                               (MeasurementComparison.DELTA_FROM_BASELINE, "-1.5")])
def test_comparison_is_explicit_and_never_inferred_from_name(comparison, expected):
    spec = replace(declaration(), comparison=comparison)
    result = evaluate_parsed_measurement(spec, {"metric": spec.metric.name, "value": "8.5", "baseline": "10"})
    assert result.metric_value == expected
