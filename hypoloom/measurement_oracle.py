"""Independent C5 result interpretation; only parser execution uses an adapter.

The original accepted submission and preregistration remain the fact sources.
Every parser replay gets a fresh disposable directory, never a retained run write.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
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


def interpret_measurement(declaration: Mapping[str, Any], result: Mapping[str, Any]) -> MeasurementOracleResult:
    """Independent comparison, not the producer's evaluator/MetricSpec.accepts."""
    try:
        metric = declaration["metric"]
        if (set(result) - {"metric", "value", "baseline"} or not {"metric", "value"} <= result.keys()
                or result["metric"] != metric["name"]):
            raise ValueError("parsed measurement fields or metric identity differ")
        value = _decimal(result["value"])
        if "baseline" in result and result["baseline"] is not None: _decimal(result["baseline"])
        with localcontext() as context:
            context.prec = 50
            comparison = declaration["comparison"]
            if comparison == "VALUE": measured = value
            elif comparison in {"DELTA_FROM_BASELINE", "RELATIVE_DELTA_FROM_BASELINE"}:
                baseline = _decimal(result["baseline"])
                measured = value - baseline
                if comparison == "RELATIVE_DELTA_FROM_BASELINE":
                    if not baseline: raise ValueError("relative measurement has zero baseline")
                    measured /= abs(baseline)
            else: raise ValueError("unknown measurement comparison")
            text = format(measured, "f").rstrip("0").rstrip(".") if "." in format(measured, "f") else format(measured, "f")
            if measured == 0: text = "0"
        target = _decimal(metric["target"])
        direction = metric["direction"]
        if direction not in {"MINIMIZE", "MAXIMIZE"}: raise ValueError("invalid metric direction")
        criterion = measured <= target if direction == "MINIMIZE" else measured >= target
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
    result = json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=nonfinite)
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
