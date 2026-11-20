"""Independent C5 result interpretation; only parser execution uses an adapter.

The original accepted submission and preregistration remain the fact sources.
Every parser replay gets a fresh disposable directory, never a retained run write.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, Inexact, localcontext
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

from .measurement_contract import ScientificMeasurement
from .measurement_execution import prepare_parser, run_parser


@dataclass(frozen=True, slots=True)
class MeasurementOracleResult:
    metric_value: str
    criterion_met: bool
    parsed_result: Mapping[str, Any]


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("parsed measurement is not a decimal")
    result = Decimal(str(value))
    if not result.is_finite(): raise ValueError("parsed measurement is not finite")
    return result


def _unit_score(row: Mapping[str, Any], comparison: str) -> Decimal:
    if not isinstance(row, Mapping) or "value" not in row or set(row) - {"value", "baseline"}:
        raise ValueError("expected value and optional baseline")
    value = _decimal(row["value"])
    baseline = _decimal(row["baseline"]) if row.get("baseline") is not None else None
    if comparison == "VALUE":
        return value
    if comparison not in ("DELTA_FROM_BASELINE", "RELATIVE_DELTA_FROM_BASELINE") or baseline is None:
        raise ValueError("unknown comparison or missing paired baseline")
    delta = value - baseline
    if comparison == "DELTA_FROM_BASELINE":
        return delta
    if not baseline:
        raise ValueError("relative measurement has zero baseline")
    return delta / abs(baseline)


def interpret_measurement(declaration: Mapping[str, Any], result: Mapping[str, Any]) -> MeasurementOracleResult:
    """Independent comparison, not the producer's evaluator/MetricSpec.accepts."""
    try:
        metric = declaration["metric"]
        if not isinstance(result, Mapping) or result.get("metric") != metric["name"]:
            raise ValueError("parsed measurement fields or metric identity differ")
        with localcontext(Context(prec=50)) as context:
            comparison = declaration["comparison"]
            if "evidence" in declaration:
                # Decode the frozen plan, then compute from the parser replay independently.
                from .measurement_contract import StageEvidence
                plan = StageEvidence.from_dict(declaration["evidence"])
                rows = result.get("observations")
                if set(result) != {"metric", "observations"} or not isinstance(rows, Mapping) or set(rows) != set(plan.units):
                    raise ValueError("observations must match exactly the declared evidence units")
                scores = [_unit_score(rows[unit], comparison) for unit in plan.units]
                if plan.reduction == "MEAN":
                    measured = sum(scores) / len(scores)
                elif metric["direction"] == "MINIMIZE":
                    measured = max(scores)
                else:
                    measured = min(scores)
                if context.flags[Inexact]:
                    measured = Context(prec=28).plus(measured)
            else:
                measured = _unit_score({k: v for k, v in result.items() if k != "metric"}, comparison)
        from .shared_acceptance_oracle import _normal
        text = _normal(measured)
        target = _decimal(metric["target"])
        direction = metric["direction"]
        if direction not in {"MINIMIZE", "MAXIMIZE"}: raise ValueError("invalid metric direction")
        operator = declaration.get("success_operator", "LE" if direction == "MINIMIZE" else "GE")
        allowed = ("LT", "LE") if direction == "MINIMIZE" else ("GT", "GE")
        if operator not in allowed: raise ValueError(f"invalid success_operator for {direction}")
        criterion = {"LT": measured < target, "LE": measured <= target,
                     "GT": measured > target, "GE": measured >= target}[operator]
        return MeasurementOracleResult(text, criterion, dict(result))
    except (KeyError, TypeError, DecimalException) as exc:
        raise ValueError(f"invalid parsed measurement: {exc}") from exc


def _read_result(path: Path) -> Mapping[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in pairs:
            if key in values: raise ValueError("duplicate parsed JSON key")
            values[key] = value
        return values
    def nonfinite(value: str) -> Any: raise ValueError(f"nonfinite JSON value {value}")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("parsed measurement publication is absent or unbounded")
    def numeric(token):
        value = float(token)
        return token if Decimal(token) != Decimal(str(value)) else value
    result = json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=nonfinite, parse_float=numeric)
    if not isinstance(result, Mapping): raise ValueError("parsed result must be an object")
    return result


def audit_measurement_result(declaration: Mapping[str, Any], workspace_files: Mapping[str, bytes],
        publication: Path, *, runtime_prefix: str = sys.prefix, python_executable: str = sys.executable,
        timeout_seconds: float = 30) -> MeasurementOracleResult:
    typed = ScientificMeasurement.from_dict(declaration)
    for relative in typed.parser_files:
        source = publication / "parser" / relative
        if source.is_symlink() or not source.is_file() or source.read_bytes() != workspace_files.get(relative):
            raise ValueError("published parser differs from accepted submission bytes")
    for relative in typed.raw_artifacts:
        source = publication / "raw" / relative
        if source.is_symlink() or not source.is_file() or source.resolve() != source:
            raise ValueError("declared raw measurement input is absent or indirect")
    recorded = _read_result(publication / "result.json")
    with tempfile.TemporaryDirectory(prefix="hypoloom-measurement-oracle-") as directory:
        prepared = prepare_parser(typed, workspace_files, raw_dir=publication / "raw",
            work_dir=Path(directory) / "parser", attempt_id="oracle-parser", execution_id="oracle-measurement",
            runtime_prefix=runtime_prefix, python_executable=python_executable, timeout_seconds=timeout_seconds)
        replay = run_parser(prepared)
        if replay.raw_result is None: raise ValueError(f"parser replay failed: {replay.error}")
        if dict(replay.raw_result) != dict(recorded):
            raise ValueError("published parsed result differs from independent parser replay")
    return interpret_measurement(declaration, recorded)
