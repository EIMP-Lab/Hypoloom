"""Typed C5 declarations; authority remains the existing preregistration/source.

This module has no persistence or eligibility decisions. Callers supply the
original hypothesis preregistration and its admitted source for comparisons.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from .domain import MetricDirection, MetricSpec, normalize_metric_value


class MeasurementContractError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class MeasurementComparison(str, Enum):
    VALUE = "VALUE"
    DELTA_FROM_BASELINE = "DELTA_FROM_BASELINE"
    RELATIVE_DELTA_FROM_BASELINE = "RELATIVE_DELTA_FROM_BASELINE"


def _paths(values: tuple[str, ...], label: str) -> None:
    if (not isinstance(values, tuple) or not values or any(not isinstance(value, str) for value in values)
            or len(set(values)) != len(values)):
        raise MeasurementContractError("invalid_measurement", f"{label} must contain unique paths")
    for value in values:
        if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
                or PurePosixPath(value).is_absolute() or PurePosixPath(value).as_posix() != value
                or any(part in {".", ".."} for part in value.split("/"))):
            raise MeasurementContractError("invalid_measurement", f"{label} contains an unsafe path")
    if any(PurePosixPath(left) in PurePosixPath(right).parents for left in values for right in values if left != right):
        raise MeasurementContractError("invalid_measurement", f"{label} contains a file/directory collision")


@dataclass(frozen=True, slots=True, kw_only=True)
class ScientificMeasurement:
    metric: MetricSpec
    comparison: MeasurementComparison
    parse_rule: str
    parser_argv: tuple[str, ...]
    parser_files: tuple[str, ...]
    raw_artifacts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.metric, MetricSpec) or not isinstance(self.comparison, MeasurementComparison):
            raise MeasurementContractError("invalid_measurement", "typed metric and comparison are required")
        if not isinstance(self.parse_rule, str) or not self.parse_rule.strip():
            raise MeasurementContractError("invalid_measurement", "parse_rule must explain scientific interpretation")
        if (not isinstance(self.parser_argv, tuple) or not self.parser_argv
                or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in self.parser_argv)):
            raise MeasurementContractError("invalid_measurement", "parser_argv must be an explicit argv")
        _paths(self.parser_files, "parser_files")
        _paths(self.raw_artifacts, "raw_artifacts")

    def to_dict(self) -> dict[str, Any]:
        return {"metric": {"name": self.metric.name, "direction": self.metric.direction.value,
                           "target": self.metric.target}, "comparison": self.comparison.value,
                "parse_rule": self.parse_rule, "parser_argv": list(self.parser_argv),
                "parser_files": list(self.parser_files), "raw_artifacts": list(self.raw_artifacts)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ScientificMeasurement:
        fields = {"metric", "comparison", "parse_rule", "parser_argv", "parser_files", "raw_artifacts"}
        try:
            if not isinstance(raw, Mapping) or set(raw) != fields:
                raise ValueError("declaration must have exact measurement fields")
            metric = raw["metric"]
            if not isinstance(metric, Mapping) or set(metric) != {"name", "direction", "target"}:
                raise ValueError("metric must have exact name/direction/target fields")
            for name in ("parser_argv", "parser_files", "raw_artifacts"):
                if not isinstance(raw[name], (list, tuple)):
                    raise ValueError(f"{name} must be an array")
            return cls(metric=MetricSpec(name=metric["name"], direction=MetricDirection(metric["direction"]),
                                        target=metric["target"]), comparison=MeasurementComparison(raw["comparison"]),
                       parse_rule=raw["parse_rule"], parser_argv=tuple(raw["parser_argv"]),
                       parser_files=tuple(raw["parser_files"]), raw_artifacts=tuple(raw["raw_artifacts"]))
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, MeasurementContractError):
                raise
            raise MeasurementContractError("invalid_measurement", str(exc)) from exc


def measurement_parser_files(declaration: ScientificMeasurement, files: Mapping[str, bytes]) -> dict[str, bytes]:
    selected = {}
    for path in declaration.parser_files:
        value = files.get(path)
        if not isinstance(value, bytes) or not value:
            raise MeasurementContractError("invalid_measurement_source", f"parser source {path!r} is absent or empty")
        selected[path] = value
    return selected


def assert_measurement_constraints(declaration: ScientificMeasurement, result: Mapping[str, Any]) -> None:
    """Preserve operator name/direction constraints even when no target was given."""
    if (result.get("metric", declaration.metric.name) != declaration.metric.name
            or str(result.get("direction", declaration.metric.direction.value)).upper() != declaration.metric.direction.value
            or ("target" in result and normalize_metric_value(result["target"]) != declaration.metric.target)):
        raise MeasurementContractError("measurement_constraint", "expert declaration differs from the operator's metric constraints")


def assert_frozen_measurement(original: ScientificMeasurement, candidate: ScientificMeasurement,
                              original_files: Mapping[str, bytes], candidate_files: Mapping[str, bytes]) -> None:
    if original != candidate or measurement_parser_files(original, original_files) != measurement_parser_files(candidate, candidate_files):
        raise MeasurementContractError("measurement_frozen", "same-hypothesis repair must preserve declaration and parser bytes; propose a revised hypothesis/new cycle")


@dataclass(frozen=True, slots=True, kw_only=True)
class MeasurementEvaluation:
    value: str
    baseline: str | None
    metric_value: str
    criterion_met: bool


def evaluate_parsed_measurement(declaration: ScientificMeasurement, raw: Mapping[str, Any]) -> MeasurementEvaluation:
    """VALUE uses value directly; delta modes subtract baseline exactly once.

    Example: value=.72, baseline=.68 yields .04 in DELTA_FROM_BASELINE.
    A parser that already returns .04 must declare VALUE to avoid subtraction
    a second time. Scientific intent cannot be inferred from the sign/name.
    """
    try:
        if (not isinstance(raw, Mapping) or not {"metric", "value"} <= set(raw)
                or set(raw) - {"metric", "value", "baseline"} or raw["metric"] != declaration.metric.name):
            raise ValueError("parsed result must identify the frozen metric and only value/optional baseline")
        def numeric_field(name: str) -> str:
            try:
                return normalize_metric_value(raw[name])
            except (ValueError, TypeError, DecimalException) as exc:
                raise ValueError(f"parsed result.{name}: {exc}; expected a finite scalar number, not an object or array") from exc
        value = numeric_field("value")
        baseline = None if raw.get("baseline") is None else numeric_field("baseline")
        with localcontext() as context:
            context.prec = 50
            measured = Decimal(value)
            if declaration.comparison is not MeasurementComparison.VALUE:
                if baseline is None:
                    raise ValueError("baseline comparison requires a finite baseline")
                measured -= Decimal(baseline)
                if declaration.comparison is MeasurementComparison.RELATIVE_DELTA_FROM_BASELINE:
                    if Decimal(baseline) == 0:
                        raise ValueError("relative comparison cannot divide by zero baseline")
                    measured /= abs(Decimal(baseline))
            metric_value = normalize_metric_value(measured)
        return MeasurementEvaluation(value=value, baseline=baseline, metric_value=metric_value,
                                     criterion_met=declaration.metric.accepts(metric_value))
    except (ValueError, TypeError, DecimalException) as exc:
        raise MeasurementContractError("invalid_parsed_measurement", str(exc)) from exc


__all__ = ["MeasurementComparison", "MeasurementContractError", "ScientificMeasurement",
           "MeasurementEvaluation", "measurement_parser_files", "assert_frozen_measurement", "assert_measurement_constraints",
           "evaluate_parsed_measurement"]
