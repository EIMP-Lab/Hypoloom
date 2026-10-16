"""Typed common criteria and measurements; existing Grounding/source remains authority."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import PurePosixPath
from typing import Any, Mapping

from .domain import normalize_metric_value


class SharedAcceptanceError(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise SharedAcceptanceError(f"{field} must be nonempty text")
    return value


def _names(value: Any, field: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise SharedAcceptanceError(f"{field} must be an array")
    items = tuple(_text(item, field) for item in value)
    if len(set(items)) != len(items) or (nonempty and not items):
        raise SharedAcceptanceError(f"{field} must contain unique names")
    return items


def _paths(value: Any, field: str) -> tuple[str, ...]:
    paths = _names(value, field, nonempty=True)
    for value in paths:
        path = PurePosixPath(value)
        if (path.is_absolute() or path.as_posix() != value or "\\" in value
            or any(part in {".", "..", ""} for part in value.split("/"))):
            raise SharedAcceptanceError(f"{field} contains an unsafe relative path")
    if any(PurePosixPath(left) in PurePosixPath(right).parents for left in paths for right in paths if left != right):
        raise SharedAcceptanceError(f"{field} has a file/directory collision")
    return paths


def _decimal(value: Any) -> str:
    try:
        return normalize_metric_value(value)
    except (ValueError, TypeError) as exc:
        raise SharedAcceptanceError("measurement must be a finite decimal") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class SharedCriterion:
    name: str
    operator: str
    target: str | None = None

    def __post_init__(self) -> None:
        _text(self.name, "criterion name")
        if not isinstance(self.operator, str):
            raise SharedAcceptanceError("shared criterion operator must be text")
        if self.operator == "IS_TRUE":
            if self.target is not None:
                raise SharedAcceptanceError("IS_TRUE cannot declare a numeric target")
        elif self.operator in {"GT", "GE", "LT", "LE", "EQ"}:
            object.__setattr__(self, "target", _decimal(self.target))
        else:
            raise SharedAcceptanceError("unknown shared criterion operator")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "operator": self.operator,
            **({} if self.operator == "IS_TRUE" else {"target": self.target})}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SharedCriterion:
        if not isinstance(raw, Mapping) or set(raw) != ({"name", "operator"} if raw.get("operator") == "IS_TRUE" else {"name", "operator", "target"}):
            raise SharedAcceptanceError("shared criterion fields are not exact")
        return cls(name=raw["name"], operator=raw["operator"], target=raw.get("target"))


@dataclass(frozen=True, slots=True, kw_only=True)
class SharedAcceptance:
    summary: str
    rule: str
    criteria: tuple[SharedCriterion, ...]
    validity_checks: tuple[str, ...]
    parser_argv: tuple[str, ...]
    parser_files: tuple[str, ...]
    raw_artifacts: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.summary, "summary")
        if not isinstance(self.rule, str) or self.rule not in {"ALL", "ANY"}:
            raise SharedAcceptanceError("shared rule must be ALL or ANY")
        if not isinstance(self.criteria, tuple) or not self.criteria or any(not isinstance(item, SharedCriterion) for item in self.criteria):
            raise SharedAcceptanceError("criteria must contain typed nonempty comparisons")
        names = tuple(item.name for item in self.criteria)
        if len(set(names)) != len(names):
            raise SharedAcceptanceError("criterion names must be unique")
        validity = _names(self.validity_checks, "validity_checks")
        if set(validity).intersection(names):
            raise SharedAcceptanceError("validity checks must be separate from success criteria")
        if not isinstance(self.parser_argv, tuple) or not self.parser_argv:
            raise SharedAcceptanceError("parser_argv must be an explicit nonempty argv")
        for item in self.parser_argv:
            _text(item, "parser argv")
        _paths(self.parser_files, "parser_files")
        _paths(self.raw_artifacts, "raw_artifacts")

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "rule": self.rule,
            "criteria": [item.to_dict() for item in self.criteria],
            "validity_checks": list(self.validity_checks), "parser_argv": list(self.parser_argv),
            "parser_files": list(self.parser_files), "raw_artifacts": list(self.raw_artifacts)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SharedAcceptance:
        fields = {"summary", "rule", "criteria", "validity_checks", "parser_argv", "parser_files", "raw_artifacts"}
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise SharedAcceptanceError("shared acceptance fields are not exact")
        if not isinstance(raw["criteria"], (list, tuple)) or not isinstance(raw["parser_argv"], (list, tuple)):
            raise SharedAcceptanceError("criteria/parser_argv must be arrays")
        return cls(summary=raw["summary"], rule=raw["rule"],
            criteria=tuple(SharedCriterion.from_dict(item) for item in raw["criteria"]),
            validity_checks=_names(raw["validity_checks"], "validity_checks"),
            parser_argv=tuple(raw["parser_argv"]), parser_files=_paths(raw["parser_files"], "parser_files"),
            raw_artifacts=_paths(raw["raw_artifacts"], "raw_artifacts"))


def normalize_shared_observation(raw: Mapping[str, Any], acceptance: SharedAcceptance | None = None) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {"measurements", "checks"}:
        raise SharedAcceptanceError("shared parser output must contain only measurements and checks")
    measurements, checks = raw["measurements"], raw["checks"]
    if not isinstance(measurements, Mapping) or not isinstance(checks, Mapping):
        raise SharedAcceptanceError("shared measurements/checks must be objects")
    measured = {_text(name, "measurement name"): _decimal(value) for name, value in measurements.items()}
    checked = {_text(name, "check name"): value for name, value in checks.items()}
    if any(type(value) is not bool for value in checked.values()):
        raise SharedAcceptanceError("shared checks must be boolean")
    if set(measured).intersection(checked):
        raise SharedAcceptanceError("measurement and check names cannot overlap")
    if acceptance is not None:
        numeric = {item.name for item in acceptance.criteria if item.operator != "IS_TRUE"}
        boolean = {item.name for item in acceptance.criteria if item.operator == "IS_TRUE"} | set(acceptance.validity_checks)
        if set(measured) != numeric or set(checked) != boolean:
            differences = [f"{kind} {field}=" + json.dumps(sorted(names), ensure_ascii=False)
                for field, expected, actual in (("measurements", numeric, set(measured)),
                    ("checks", boolean, set(checked)))
                for kind, names in (("missing", expected - actual), ("unexpected", actual - expected)) if names]
            raise SharedAcceptanceError("shared observations differ from the exact declared names; " + "; ".join(differences))
    return {"measurements": measured, "checks": checked}


def evaluate_shared_acceptance(acceptance: SharedAcceptance, raw: Mapping[str, Any]) -> bool:
    parsed = normalize_shared_observation(raw, acceptance)
    results = []
    for item in acceptance.criteria:
        if item.operator == "IS_TRUE":
            results.append(parsed["checks"][item.name])
        else:
            actual, target = Decimal(parsed["measurements"][item.name]), Decimal(item.target)
            results.append({"GT": actual > target, "GE": actual >= target, "LT": actual < target,
                "LE": actual <= target, "EQ": actual == target}[item.operator])
    return all(parsed["checks"][name] for name in acceptance.validity_checks) and (all(results) if acceptance.rule == "ALL" else any(results))


def acceptance_source_files(acceptance: SharedAcceptance, files: Mapping[str, bytes]) -> dict[str, bytes]:
    selected = {}
    for name in acceptance.parser_files:
        content = files.get(name)
        if not isinstance(content, bytes) or not content:
            raise SharedAcceptanceError(f"shared parser source {name!r} is absent or empty")
        selected[name] = content
    return selected


def uses_grounding_evaluation(contract: Mapping[str, Any]) -> bool:
    return contract.get("schema_version") == 3 and contract.get("goal", {}).get("evaluation") == {"mode": "grounding"}


def normalize_goal_evaluation_binding(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {"scope", "source_attempt_id", "acceptance"} or raw["scope"] not in {"LOCAL", "GOAL"}:
        raise SharedAcceptanceError("goal evaluation binding requires exact scope/source/acceptance")
    return {"scope": raw["scope"], "source_attempt_id": _text(raw["source_attempt_id"], "Grounding source Attempt"),
        "acceptance": SharedAcceptance.from_dict(raw["acceptance"]).to_dict()}


def normalize_goal_evaluation_observation(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {"source_attempt_id", "measurements", "checks"}:
        raise SharedAcceptanceError("goal evaluation observation requires exact source/measurements/checks")
    return {"source_attempt_id": _text(raw["source_attempt_id"], "Grounding source Attempt"),
        **normalize_shared_observation({name: raw[name] for name in ("measurements", "checks")})}
