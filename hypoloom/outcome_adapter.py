"""The single trusted interpreter for component experiment results."""
from __future__ import annotations
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal, Mapping, TypedDict
from .artifacts import ArtifactIntegrityError, inventory_relative_paths, normalize_relative_paths
from .domain import normalize_metric_value
from .measurement_contract import ScientificMeasurement, evaluate_parsed_measurement
OUTCOME_ADAPTER_VERSION = "component-result/9"
_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"; _SHA256_PATTERN = r"[0-9a-f]{64}"; _ID = re.compile(rf"{_ID_PATTERN}\Z"); _SHA256 = re.compile(rf"{_SHA256_PATTERN}\Z"); _MISSING = object(); _RESULT_REQUIRED_FIELDS = {"artifact": ("artifacts",), "metric": ("artifacts", "trials")}
_RESULT_OPTIONAL_FIELDS = ("identity",); _TRIAL_REQUIRED_FIELDS = ("clean", "metrics", "seed"); _TRIAL_OPTIONAL_FIELDS = ("arm",); _IDENTITY_FIELDS = ("core_config_sha256", "implementation_sha256", "method_family_id"); _UNIFIED_DIFF_SYNTAX_ERROR = "must contain paired headers and a text hunk"
GitApplyCheck = Callable[[bytes], bool | str | None]; ImplementationDigestCheck = Callable[[str], bool | str | None]
class ComponentResultValidationError(ValueError):
    def __init__(self, code: str, location: str, message: str) -> None:
        self.code, self.location, self.message = code, location, message; super().__init__(f"{code}@{location}: {message}")
class ValidatedComponentResult(TypedDict):
    component_id: str; result_kind: Literal["metric", "artifact"]; metric_name: str | None; metric_value: str | None; criterion_met: bool | None; logical_artifacts: dict[str, tuple[str, ...]]; method_family_id: str | None; implementation_sha256: str | None; core_config_sha256: str | None
def _metric_mode(metric: str, required_metrics: tuple[str, ...]) -> str:
    return "paired" if metric.casefold().startswith("paired_") or "paired_delta" in required_metrics else "max" if metric.casefold().startswith("max_") else "mean"
def component_result_contract_parts(component: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the component/protocol fields shared by validation and display."""
    component = _map(component, "component"); protocol = _map(protocol, "protocol")
    spec = _map(component.get("result"), "component.result")
    kind = spec.get("kind")
    if kind not in _RESULT_REQUIRED_FIELDS: _bad("invalid_component", "component.result.kind", "must be metric or artifact")
    if protocol.get("result_kind") != kind: _bad("contract_mismatch", "protocol.result_kind", f"must equal {kind!r}")
    requirements = _map(protocol.get("requirements", {}), "protocol.requirements")
    required, counts = _requirements(component, requirements)
    mode = _metric_mode(_text(spec.get("metric"), "component.result.metric"), _texts(requirements.get("required_metrics", []), "protocol.requirements.required_metrics")) if kind == "metric" else None
    return {"component": component, "protocol": protocol, "spec": spec, "kind": kind, "requirements": requirements, "metric_mode": mode, "required_artifacts": required, "artifact_min_counts": counts, "required_top_level_keys": _RESULT_REQUIRED_FIELDS[str(kind)], "optional_top_level_keys": _RESULT_OPTIONAL_FIELDS, "trial_required_keys": _TRIAL_REQUIRED_FIELDS, "trial_optional_keys": _TRIAL_OPTIONAL_FIELDS, "identity_keys": _IDENTITY_FIELDS, "identity_fields": {"method_family_id": {"type": "string", "pattern": f"^{_ID_PATTERN}$", "must_equal": "required_experiment_spec.method_family_id"}, "implementation_sha256": {"type": "string", "pattern": f"^{_SHA256_PATTERN}$"}, "core_config_sha256": {"type": "string", "pattern": f"^{_SHA256_PATTERN}$"}}}
def repairable_component_result_error(component: Mapping[str, Any], protocol: Mapping[str, Any], artifact_dir: str | Path, error: ComponentResultValidationError, *, expected_method_family_id: str | None = None, git_apply_check: GitApplyCheck | None = None, implementation_bytes: bytes | bytearray | memoryview | None = None, implementation_digest_check: ImplementationDigestCheck | None = None) -> bool:
    if error.code in {"missing_result", "invalid_result_json", "invalid_result"}: return True
    if error.code in {"text_contract_failed", "invalid_unified_diff"}: return _repairable_artifact_contract_error(component, protocol, artifact_dir, error, expected_method_family_id=expected_method_family_id, git_apply_check=git_apply_check, implementation_bytes=implementation_bytes, implementation_digest_check=implementation_digest_check)
    if error.code in {"invalid_identity", "artifact_inventory_mismatch", "implementation_digest_mismatch"}: return _repairable_artifact_error(component, protocol, artifact_dir, error, expected_method_family_id=expected_method_family_id, git_apply_check=git_apply_check, implementation_bytes=implementation_bytes, implementation_digest_check=implementation_digest_check)
    if error.code not in {"invalid_result_shape", "invalid_artifact_map"}: return False
    path = Path(artifact_dir) / "result.json"
    if Path(artifact_dir).is_symlink() or path.is_symlink(): return False
    try: raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_component_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError): return False
    if not isinstance(raw, Mapping) or "identity" in raw: return False
    parts = component_result_contract_parts(component, protocol); required = set(parts["required_top_level_keys"])
    shape = not required <= set(raw) or not set(raw) <= required | set(parts["optional_top_level_keys"])
    artifacts = raw.get("artifacts", _MISSING)
    declarations = tuple(artifacts.values()) if isinstance(artifacts, Mapping) else (() if artifacts is _MISSING else (artifacts,))
    if any(isinstance(value, str) or isinstance(value, list) and bool(value) for value in declarations) or isinstance(artifacts, Mapping) and set(artifacts) != set(parts["required_artifacts"]): return False
    map_error = artifacts is not _MISSING and (not isinstance(artifacts, Mapping) or any(not isinstance(value, list) or not value for value in declarations))
    return error.code == "invalid_result_shape" and shape or error.code == "invalid_artifact_map" and not shape and map_error
def _repairable_artifact_contract_error(component: Mapping[str, Any], protocol: Mapping[str, Any], artifact_dir: str | Path, error: ComponentResultValidationError, *, expected_method_family_id: str | None, git_apply_check: GitApplyCheck | None, implementation_bytes: bytes | bytearray | memoryview | None, implementation_digest_check: ImplementationDigestCheck | None) -> bool:
    if error.code == "invalid_unified_diff" and (error.message != _UNIFIED_DIFF_SYNTAX_ERROR or implementation_bytes is not None or implementation_digest_check is not None): return False
    try:
        parts = component_result_contract_parts(component, protocol); root, result = _component_json(artifact_dir, "artifact")
        if parts["kind"] != "artifact": return False
        _, paths = _artifact_map(result["artifacts"], root, parts["required_artifacts"], parts["artifact_min_counts"]); family, _, _ = _validated_identity(result, paths, implementation_bytes, implementation_digest_check)
        if family is not None and (expected_method_family_id is None or family != expected_method_family_id): return False
        validators = parts["requirements"].get("artifact_validators", [])
        if not isinstance(validators, (list, tuple)): return False
        for index in range(len(validators)):
            try: _artifact_contracts(validators[:index + 1], paths, git_apply_check)
            except ComponentResultValidationError as actual: validator = validators[index]; artifact = validator.get("artifact") if isinstance(validator, Mapping) else None; selected = paths.get(artifact, ()); required = validator.get("apply_check", True) if isinstance(validator, Mapping) else None; exact = (actual.code, actual.location, actual.message) == (error.code, error.location, error.message); return exact and ((isinstance(validator, Mapping) and validator.get("type") == "unified_diff" and len(selected) == 1 and selected[0].name == error.location and isinstance(required, bool) and (not required or callable(git_apply_check)) and _repairable_collapsed_diff(selected[0]) and _artifact_contracts(validators[index + 1:], paths, git_apply_check)) if error.code == "invalid_unified_diff" else (not any(not isinstance(item, Mapping) or item.get("type") != "text_contract" for item in validators[index + 1:]) and _artifact_contracts(validators[index + 1:], paths, git_apply_check, tolerate_text_failures=True)))
        return False
    except (ArtifactIntegrityError, ComponentResultValidationError, OSError, StopIteration, TypeError, ValueError): return False
def _repairable_artifact_error(component: Mapping[str, Any], protocol: Mapping[str, Any], artifact_dir: str | Path, error: ComponentResultValidationError, *, expected_method_family_id: str | None, git_apply_check: GitApplyCheck | None, implementation_bytes: bytes | bytearray | memoryview | None, implementation_digest_check: ImplementationDigestCheck | None) -> bool:
    try:
        parts = component_result_contract_parts(component, protocol); root, result = _component_json(artifact_dir, "artifact"); extra = error.code == "artifact_inventory_mismatch"; logical, paths = _artifact_map(result["artifacts"], root, parts["required_artifacts"], parts["artifact_min_counts"], allow_extra=extra)
        if error.code == "invalid_identity":
            validators = parts["requirements"].get("artifact_validators", []); identity = result.get("identity"); expected = ("invalid_identity", "result.json.identity", "digests must be 64 lowercase hex")
            if (error.code, error.location, error.message) != expected or expected_method_family_id is None or implementation_bytes is not None or implementation_digest_check is not None or parts["kind"] != "artifact" or parts["component"].get("dependencies") not in (None, [], ()) or parts["component"].get("required_inputs") not in (None, [], ()) or "implementation_patch" in parts["required_artifacts"] or not isinstance(validators, (list, tuple)) or any(not isinstance(item, Mapping) or item.get("type") == "unified_diff" for item in validators): return False
            try: _artifact_contracts(validators, paths, None); _validated_identity(result, paths, None, None)
            except ComponentResultValidationError as actual: return isinstance(identity, Mapping) and identity.get("method_family_id") == expected_method_family_id and (actual.code, actual.location, actual.message) == expected
        if parts["kind"] != "artifact": return False
        if extra:
            actual = set(inventory_relative_paths(root)); declared = {path for values in logical.values() for path in values} | {"result.json"}; expected = ("artifact_inventory_mismatch", "artifact_dir", f"undeclared={sorted(actual - declared)!r}, missing=[]"); metadata = [root.joinpath(*PurePosixPath(path).parts).stat() for path in actual]
            if not actual > declared or (error.code, error.location, error.message) != expected or len({(item.st_dev, item.st_ino) for item in metadata}) != len(metadata): return False
            return implementation_bytes is None and implementation_digest_check is None and _repairable_identity(result, paths, expected_method_family_id, None, None)[0]
        validators = parts["requirements"].get("artifact_validators", [])
        if implementation_bytes is not None or implementation_digest_check is not None or any(isinstance(item, Mapping) and item.get("type") == "unified_diff" and item.get("apply_check", True) is True for item in validators if isinstance(validators, (list, tuple))): return False
        _artifact_contracts(validators, paths, None); valid, mismatch = _repairable_identity(result, paths, expected_method_family_id, None, None); return valid and mismatch and (error.code, error.location, error.message) == ("implementation_digest_mismatch", "implementation_sha256", "bytes do not match")
    except (ComponentResultValidationError, OSError, StopIteration, TypeError, ValueError): return False
def _repairable_identity(result: Mapping[str, Any], paths: Mapping[str, tuple[Path, ...]], expected_method_family_id: str | None, implementation_bytes: bytes | bytearray | memoryview | None, implementation_digest_check: ImplementationDigestCheck | None) -> tuple[bool, bool]:
    mismatch = False
    try: family, _, _ = _validated_identity(result, paths, implementation_bytes, implementation_digest_check)
    except ComponentResultValidationError as error:
        mismatch = implementation_bytes is None and implementation_digest_check is None and (error.code, error.location, error.message) == ("implementation_digest_mismatch", "implementation_sha256", "bytes do not match")
        if not mismatch: return False, False
        family, declared, _ = _identity(result.get("identity")); patch = paths.get("implementation_patch", ())
        if declared is None or len(patch) != 1 or hashlib.sha256(patch[0].read_bytes()).hexdigest() == declared: return False, False
    return family is None or expected_method_family_id is not None and family == expected_method_family_id, mismatch
def validate_component_result(component: Mapping[str, Any], protocol: Mapping[str, Any], artifact_dir: str | Path, *, git_apply_check: GitApplyCheck | None = None, implementation_bytes: bytes | bytearray | memoryview | None = None, implementation_digest_check: ImplementationDigestCheck | None = None) -> ValidatedComponentResult:
    """Validate raw component trials and logical artifacts without writing state."""
    parts = component_result_contract_parts(component, protocol); component = parts["component"]; protocol = parts["protocol"]
    component_id = _text(component.get("id", component.get("name")), "component.id")
    spec = parts["spec"]; kind = parts["kind"]; requirements = parts["requirements"]; required = parts["required_artifacts"]; counts = parts["artifact_min_counts"]
    root, result = _component_json(artifact_dir, kind)
    logical, paths = _artifact_map(result["artifacts"], root, required, counts)
    _artifact_contracts(requirements.get("artifact_validators", []), paths, git_apply_check)
    family, implementation, config = _validated_identity(result, paths, implementation_bytes, implementation_digest_check)
    metric_name = metric_value = None; criterion = True
    if kind == "metric": metric_name, value, target, direction = _component_metric(component, spec, requirements, result["trials"]); metric_value = normalize_metric_value(value); criterion = value >= target if direction == "MAXIMIZE" else value <= target
    condition = requirements.get("inconclusive_if")
    if condition is not None and _inconclusive_condition_matches(condition, paths):
        criterion = None
    return {"component_id": component_id, "result_kind": kind, "metric_name": metric_name, "metric_value": metric_value, "criterion_met": criterion, "logical_artifacts": logical, "method_family_id": family, "implementation_sha256": implementation, "core_config_sha256": config}


def _inconclusive_condition_matches(condition: Mapping[str, Any], paths: Mapping[str, tuple[Path, ...]]) -> bool:
    """Interpret a protocol-owned completeness marker without special-casing a metric sentinel."""
    artifact = condition.get("artifact")
    dotted = condition.get("path")
    selected = paths.get(artifact, ())
    if not isinstance(artifact, str) or not isinstance(dotted, str) or len(selected) != 1:
        raise ComponentResultValidationError(
            "invalid_inconclusive_condition", "protocol.requirements.inconclusive_if", "condition must select one declared artifact"
        )
    try:
        value: Any = json.loads(selected[0].read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_component_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ComponentResultValidationError("invalid_inconclusive_condition", dotted, str(exc)) from exc
    for part in dotted.split("."):
        if not part or not isinstance(value, Mapping) or part not in value:
            raise ComponentResultValidationError("invalid_inconclusive_condition", dotted, "declared JSON path is absent")
        value = value[part]
    expected = condition.get("equals")
    return type(value) is type(expected) and value == expected


def validate_measurement_result(component: Mapping[str, Any], measurement: ScientificMeasurement,
                                artifact_dir: str | Path) -> ValidatedComponentResult:
    """Accept only parsed numbers under the preregistered expert interpretation."""
    root = Path(artifact_dir)
    try:
        paths = set(inventory_relative_paths(root))
        required = {"result.json", *("raw/" + name for name in measurement.raw_artifacts),
                    *("parser/" + name for name in measurement.parser_files)}
        if not required <= paths or any(path != "result.json" and not path.startswith(("raw/", "parser/")) for path in paths):
            raise ValueError("measurement publication lacks its raw/parser/result inventory")
        raw = json.loads((root / "result.json").read_bytes(), object_pairs_hook=_unique_object,
                         parse_constant=_reject_component_constant)
        evaluation = evaluate_parsed_measurement(measurement, raw)
        if component.get("required_artifacts"):
            raise ValueError("expert measurement must name logical artifacts in a supported result protocol")
    except (OSError, ValueError, ArtifactIntegrityError) as exc:
        raise ComponentResultValidationError("invalid_measurement_result", "result.json", str(exc)) from exc
    return {"component_id": str(component["id"]), "result_kind": "metric", "metric_name": measurement.metric.name,
            "metric_value": evaluation.metric_value, "criterion_met": evaluation.criterion_met,
            "logical_artifacts": {}, "method_family_id": None, "implementation_sha256": None, "core_config_sha256": None}
def _component_json(directory: str | Path, kind: str) -> tuple[Path, dict[str, Any]]:
    supplied = Path(directory)
    if supplied.is_symlink(): _bad("unsafe_artifact_root", "artifact_dir", "must not be a symlink")
    try:
        root = supplied.resolve(strict=True); path = root / "result.json"
        if not root.is_dir(): raise OSError("not a directory")
        if path.is_symlink(): _bad("unsafe_artifact_path", "result.json", "must not be a symlink")
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_component_constant)
    except ComponentResultValidationError: raise
    except FileNotFoundError: _bad("missing_result", "result.json", "file is required")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc: _bad("invalid_result_json", "result.json", str(exc))
    if not isinstance(result, dict): _bad("invalid_result", "result.json", "must contain an object")
    required = set(_RESULT_REQUIRED_FIELDS[kind]); allowed = required | set(_RESULT_OPTIONAL_FIELDS)
    if not required <= set(result) or not set(result) <= allowed: _bad("invalid_result_shape", "result.json", f"missing={sorted(required - set(result))!r}, unsupported={sorted(set(result) - allowed)!r}")
    return root, result
def _requirements(component: Mapping[str, Any], requirements: Mapping[str, Any]) -> tuple[tuple[str, ...], dict[str, int]]:
    left, left_counts = _requirement_set(component.get("required_artifacts", []), "component.required_artifacts"); right, right_counts = _requirement_set(requirements.get("required_artifacts", []), "protocol.requirements.required_artifacts")
    if left and right and set(left) != set(right): _bad("contract_mismatch", "required_artifacts", "component and protocol inventories differ")
    required, counts = left or right, dict(left_counts)
    for key, count in right_counts.items(): counts[key] = max(counts.get(key, 1), count)
    seeds = component.get("seeds")
    if "checkpoints" in required and isinstance(seeds, (list, tuple)) and seeds: counts["checkpoints"] = max(counts.get("checkpoints", 1), len(seeds))
    return required, counts
def _requirement_set(raw: Any, location: str) -> tuple[tuple[str, ...], dict[str, int]]:
    if isinstance(raw, Mapping): items = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        items = []
        for index, item in enumerate(raw):
            if isinstance(item, str): items.append((item, 1))
            elif isinstance(item, Mapping):
                name = item.get("artifact", item.get("id"))
                allowed = {"artifact", "min_count"} if "artifact" in item else {"id", "min_count"}
                if not set(item) <= allowed: _bad("invalid_contract", f"{location}[{index}]", "invalid count declaration")
                items.append((name, item.get("min_count", 1)))
            else: _bad("invalid_contract", f"{location}[{index}]", "invalid artifact declaration")
    else: _bad("invalid_contract", location, "must be an array or object")
    ordered: list[str] = []
    counts: dict[str, int] = {}
    for raw_id, raw_count in items:
        artifact_id = _text(raw_id, location, pattern=_ID)
        if artifact_id in counts: _bad("invalid_contract", location, f"duplicate artifact {artifact_id!r}")
        if isinstance(raw_count, Mapping):
            if set(raw_count) != {"min_count"}: _bad("invalid_contract", location, "count object must contain min_count")
            raw_count = raw_count["min_count"]
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 1: _bad("invalid_contract", location, "min_count must be a positive integer")
        ordered.append(artifact_id)
        counts[artifact_id] = raw_count
    return tuple(ordered), counts
def _artifact_map(raw: Any, root: Path, required: tuple[str, ...], counts: Mapping[str, int], *, allow_extra: bool = False) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[Path, ...]]]:
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw): _bad("invalid_artifact_map", "result.json.artifacts", "must be an object")
    keys = {_text(key, "result.json.artifacts", pattern=_ID) for key in raw}
    if keys != set(required) or len(keys) != len(raw): _bad("artifact_inventory_mismatch", "result.json.artifacts", f"expected {sorted(required)!r}, got {sorted(keys)!r}")
    logical: dict[str, tuple[str, ...]] = {}; paths: dict[str, tuple[Path, ...]] = {}; seen_refs: set[str] = set()
    result_stat = (root / "result.json").stat(); seen_files: dict[tuple[int, int], str] = {(result_stat.st_dev, result_stat.st_ino): "result.json"}
    try:
        actual = set(inventory_relative_paths(root))
    except ArtifactIntegrityError as exc:
        _bad("unsafe_artifact_path", "artifact_dir", str(exc))
    for raw_id, declaration in raw.items():
        artifact_id = _text(raw_id, "result.json.artifacts", pattern=_ID)
        refs = [declaration] if isinstance(declaration, str) else declaration
        if not isinstance(refs, list) or not refs:
            _bad("invalid_artifact_map", artifact_id, "must be a path or non-empty path list")
        normalized: list[str] = []; resolved: list[Path] = []
        for index, reference in enumerate(refs):
            where = f"result.json.artifacts.{artifact_id}[{index}]"
            try:
                relative = normalize_relative_paths([reference])[0]
            except ArtifactIntegrityError as exc:
                _bad("unsafe_artifact_path", where, str(exc))
            if relative == "result.json" or relative in seen_refs:
                _bad("artifact_path_alias", where, f"duplicate/reserved path {relative!r}")
            if relative not in actual:
                _bad("unsafe_artifact_path", where, "path is missing or not a regular file")
            path = root.joinpath(*PurePosixPath(relative).parts)
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen_files:
                _bad("artifact_path_alias", where, f"aliases {seen_files[identity]!r}")
            seen_refs.add(relative)
            seen_files[identity] = relative
            normalized.append(relative)
            resolved.append(path)
        if len(normalized) < counts.get(artifact_id, 1):
            _bad("artifact_count_mismatch", artifact_id, f"requires at least {counts.get(artifact_id, 1)} paths")
        logical[artifact_id], paths[artifact_id] = tuple(normalized), tuple(resolved)
    declared = {relative for group in logical.values() for relative in group} | {"result.json"}
    if actual != declared and not allow_extra:
        _bad("artifact_inventory_mismatch", "artifact_dir", f"undeclared={sorted(actual - declared)!r}, missing={sorted(declared - actual)!r}")
    return logical, paths
def _component_metric(component: Mapping[str, Any], spec: Mapping[str, Any], requirements: Mapping[str, Any], raw: Any) -> tuple[str, Decimal, Decimal, str]:
    metric = _text(spec.get("metric"), "component.result.metric")
    direction = str(spec.get("direction", "")).upper()
    if direction not in {"MAXIMIZE", "MINIMIZE"}:
        _bad("invalid_component", "component.result.direction", "must be maximize or minimize")
    target = _decimal(spec.get("target"), "component.result.target", numeric_text=True)
    seeds = _seeds(component.get("seeds", []), "component.seeds")
    trials = _trials(raw)
    required_metrics = _texts(
        requirements.get("required_metrics", []),
        "protocol.requirements.required_metrics",
    )
    mode = _metric_mode(metric, required_metrics)
    field = _metric_field(metric, mode, trials)
    if mode == "paired":
        if len(seeds) != 3:
            _bad("invalid_component", "component.seeds", "paired batches require three seeds")
        expected = {((type(seed).__name__, seed), arm) for seed in seeds for arm in ("control", "treatment")}
        by_id = {((type(t["seed"]).__name__, t["seed"]), t["arm"]): t for t in trials}
        if set(by_id) != expected or len(trials) != len(expected):
            _bad("trial_completeness", "result.json.trials", "requires exact control/treatment pairs")
        values = [by_id[((type(seed).__name__, seed), "treatment")]["metrics"][field] - by_id[((type(seed).__name__, seed), "control")]["metrics"][field] for seed in seeds]
        value = _aggregate(values, "mean")
    else:
        if any(trial["arm"] is not None for trial in trials):
            _bad("trial_completeness", "result.json.trials", "unpaired trials must omit arm")
        if seeds:
            if mode == "mean" and len(seeds) != 5:
                _bad("invalid_component", "component.seeds", "confirmation batches require five seeds")
            trials = _exact_seeds(trials, seeds)
        elif mode == "mean" and len(trials) != 1:
            _bad("trial_completeness", "result.json.trials", "requires exactly one undeclared-seed trial")
        values = [trial["metrics"][field] for trial in trials]
        value = _aggregate(values, mode)
    _required_metric_check(required_metrics, metric, mode, trials)
    return metric, value, target, direction
def _trials(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        _bad("invalid_trials", "result.json.trials", "must be a non-empty array")
    result: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, object], str | None]] = set()
    for index, item in enumerate(raw):
        where = f"result.json.trials[{index}]"
        if not isinstance(item, Mapping) or not set(_TRIAL_REQUIRED_FIELDS) <= set(item) \
                or not set(item) <= set(_TRIAL_REQUIRED_FIELDS + _TRIAL_OPTIONAL_FIELDS):
            _bad("invalid_trial", where, "exact fields are seed, optional arm, metrics, clean")
        seed = _seeds([item["seed"]], f"{where}.seed")[0]
        arm = None if item.get("arm") is None else _text(item["arm"], f"{where}.arm")
        if item["clean"] is not True:
            _bad("unclean_trial", f"{where}.clean", "must be true")
        if not isinstance(item["metrics"], Mapping) or not item["metrics"]:
            _bad("invalid_trial", f"{where}.metrics", "must be a non-empty object")
        metrics = {_text(name, f"{where}.metrics"): _decimal(value, f"{where}.metrics.{name}") for name, value in item["metrics"].items()}
        identity = ((type(seed).__name__, seed), arm)
        if identity in seen:
            _bad("duplicate_trial", where, "seed/arm identity is duplicated")
        seen.add(identity)
        result.append({"seed": seed, "arm": arm, "metrics": metrics})
    return result
def _metric_field(metric: str, mode: str, trials: list[dict[str, Any]]) -> str:
    common = set(trials[0]["metrics"])
    for trial in trials[1:]:
        common &= set(trial["metrics"])
    candidates = [metric]
    match = re.fullmatch(r"paired_(.+?)_gain_vs_.+", metric) if mode == "paired" else None
    if match:
        candidates.append(match.group(1))
    candidate = next((name for name in candidates if name in common), None)
    if candidate is None:
        _bad("missing_trial_metric", "result.json.trials[*].metrics", f"cannot identify {metric!r}")
    return candidate
def _required_metric_check(required: tuple[str, ...], metric: str, mode: str, trials: list[dict[str, Any]]) -> None:
    common = set(trials[0]["metrics"])
    for trial in trials[1:]:
        common &= set(trial["metrics"])
    derived = {metric}
    if mode == "paired":
        derived.add("paired_delta")
    for required_metric in required:
        if required_metric == "n_successful_seeds":
            successful = Decimal(len(trials))
            if required_metric not in common or any(
                trial["metrics"][required_metric] != successful for trial in trials
            ):
                _bad("required_metric_mismatch", "result.json.trials[*].metrics", "n_successful_seeds must equal the clean trial count on every trial")
        elif required_metric not in derived and required_metric not in common:
            _bad("required_metric_mismatch", "result.json.trials[*].metrics", f"required metric {required_metric!r} must be present on every trial")
def _exact_seeds(trials: list[dict[str, Any]], seeds: tuple[object, ...]) -> list[dict[str, Any]]:
    by_seed = {(type(trial["seed"]).__name__, trial["seed"]): trial for trial in trials}
    expected = {(type(seed).__name__, seed) for seed in seeds}
    if set(by_seed) != expected or len(trials) != len(expected):
        _bad("trial_completeness", "result.json.trials", "must exactly match component seeds")
    return [by_seed[(type(seed).__name__, seed)] for seed in seeds]
def _artifact_contracts(raw: Any, artifacts: Mapping[str, tuple[Path, ...]], apply_check: GitApplyCheck | None, *, tolerate_text_failures: bool = False) -> bool:
    if not isinstance(raw, (list, tuple)): _bad("invalid_contract", "artifact_validators", "must be an array")
    for index, item in enumerate(raw):
        where = f"artifact_validators[{index}]"; validator = _map(item, where)
        artifact = _text(validator.get("artifact"), f"{where}.artifact", pattern=_ID)
        if artifact not in artifacts: _bad("invalid_contract", where, f"undeclared artifact {artifact!r}")
        for path in artifacts[artifact]:
            try:
                kind = validator.get("type")
                if kind == "json_contract": _json_contract(path, validator, where)
                elif kind == "text_contract": _text_contract(path, validator, where)
                elif kind == "unified_diff": _diff_contract(path, validator, where, apply_check)
                else: _bad("invalid_contract", f"{where}.type", f"unsupported {kind!r}")
            except ComponentResultValidationError as error:
                if not tolerate_text_failures: raise
                if error.code != "text_contract_failed": return False
    return True
def _json_contract(path: Path, validator: Mapping[str, Any], where: str) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_component_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _bad("invalid_json_artifact", path.name, str(exc))
    for field in _texts(validator.get("required_paths", []), f"{where}.required_paths"):
        found = _json_path(value, field)
        if found is _MISSING or found in (None, "", [], {}):
            _bad("json_contract_failed", path.name, f"missing JSON path {field!r}")
    groups = validator.get("equal_groups", [])
    if not isinstance(groups, (list, tuple)):
        _bad("invalid_contract", f"{where}.equal_groups", "must be an array")
    for index, group in enumerate(groups):
        fields = _texts(group, f"{where}.equal_groups[{index}]")
        values = [_json_path(value, field) for field in fields]
        if len(fields) < 2 or any(item is _MISSING for item in values) or any(type(item) is not type(values[0]) or item != values[0] for item in values[1:]):
            _bad("json_contract_failed", path.name, f"paths are not equal: {fields!r}")
    exact = validator.get("exact_key_sets", {})
    allowed = validator.get("allowed_values", {})
    if not isinstance(exact, Mapping) or not isinstance(allowed, Mapping):
        _bad("invalid_contract", where, "exact_key_sets/allowed_values must be objects")
    for field, keys in exact.items():
        field = _text(field, f"{where}.exact_key_sets")
        keys = _texts(keys, f"{where}.exact_key_sets.{field}")
        found = _json_path(value, field)
        if not isinstance(found, Mapping) or set(found) != set(keys) or len(keys) != len(set(keys)):
            _bad("json_contract_failed", path.name, f"{field!r} must have exact keys {sorted(keys)!r}")
    for field, choices in allowed.items():
        field = _text(field, f"{where}.allowed_values")
        if not isinstance(choices, (list, tuple)) or not choices:
            _bad("invalid_contract", where, "allowed values must be a non-empty array")
        found = _json_path(value, field)
        if found is _MISSING or not any(type(found) is type(choice) and found == choice for choice in choices):
            _bad("json_contract_failed", path.name, f"{field!r} has a forbidden value")
def _text_contract(path: Path, validator: Mapping[str, Any], where: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _bad("invalid_text_artifact", path.name, str(exc))
    minimum = validator.get("min_chars", 1)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        _bad("invalid_contract", f"{where}.min_chars", "must be a positive integer")
    if len(text.strip()) < minimum:
        _bad("text_contract_failed", path.name, f"requires at least {minimum} characters")
    folded = text.casefold()
    for key in ("required_sections", "required_terms"):
        for token in _texts(validator.get(key, []), f"{where}.{key}"):
            if token.casefold() not in folded:
                _bad("text_contract_failed", path.name, f"missing required text {token!r}")
    for token in _texts(validator.get("forbidden_terms", []), f"{where}.forbidden_terms"):
        if token.casefold() in folded:
            _bad("text_contract_failed", path.name, f"contains forbidden text {token!r}")
def _repairable_collapsed_diff(path: Path) -> bool: text = path.read_text(encoding="utf-8"); old = re.findall(r"(?m)^--- ([^\t\r\n]*?)(?=\+\+\+ )", text); new = re.findall(r"\+\+\+ ([^\t\r\n]*?)(?=@@ [^\r\n]+ @@)", text); headers = old + new; return "\x00" not in text and bool(old) and len(old) == len(new) == len(re.findall(r"(?m)^--- ", text)) == len(re.findall(r"\+\+\+ ", text)) and all(header == "/dev/null" or header.startswith(("a/", "b/")) and bool(normalize_relative_paths([header[2:]])) for header in headers)
def _diff_contract(path: Path, validator: Mapping[str, Any], where: str, apply_check: GitApplyCheck | None) -> None:
    try: payload = path.read_bytes(); text = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        _bad("invalid_unified_diff", path.name, str(exc))
    old = re.findall(r"(?m)^--- ([^\t\r\n]+)", text); new = re.findall(r"(?m)^\+\+\+ ([^\t\r\n]+)", text)
    if "\x00" in text or re.search(r"(?m)^@@ [^\r\n]+ @@", text) is None or not old or len(old) != len(new):
        _bad("invalid_unified_diff", path.name, _UNIFIED_DIFF_SYNTAX_ERROR)
    for header in old + new:
        if header != "/dev/null":
            if not header.startswith(("a/", "b/")):
                _bad("invalid_unified_diff", path.name, f"unsafe patch path {header!r}")
            try:
                normalize_relative_paths([header[2:]])
            except ArtifactIntegrityError as exc:
                _bad("invalid_unified_diff", path.name, str(exc))
    required = validator.get("apply_check", True)
    if not isinstance(required, bool):
        _bad("invalid_contract", f"{where}.apply_check", "must be boolean")
    if not required:
        return
    if apply_check is None:
        _bad("apply_check_unavailable", path.name, "requires an injected git-apply-check callback")
    try:
        verdict = apply_check(payload)
    except Exception as exc:
        _bad("apply_check_failed", path.name, str(exc))
    _callback(verdict, "apply_check_failed", path.name)
def _identity(raw: Any) -> tuple[str | None, str | None, str | None]:
    if raw is None:
        return None, None, None
    value = _map(raw, "result.json.identity")
    allowed = set(_IDENTITY_FIELDS)
    if not value or not set(value) <= allowed:
        _bad("invalid_identity", "result.json.identity", f"allowed fields are {sorted(allowed)!r}")
    family = value.get("method_family_id")
    if family is not None: family = _text(family, "result.json.identity.method_family_id", pattern=_ID)
    digests = [value.get(field) for field in ("implementation_sha256", "core_config_sha256")]
    if any(digest is not None and (not isinstance(digest, str) or _SHA256.fullmatch(digest) is None) for digest in digests): _bad("invalid_identity", "result.json.identity", "digests must be 64 lowercase hex")
    return family, digests[0], digests[1]
def _validated_identity(result: Mapping[str, Any], paths: Mapping[str, tuple[Path, ...]], implementation_bytes: bytes | bytearray | memoryview | None, implementation_digest_check: ImplementationDigestCheck | None) -> tuple[str | None, str | None, str | None]:
    family, implementation, config = _identity(result.get("identity"))
    if implementation_bytes is None and "implementation_patch" in paths:
        if len(paths["implementation_patch"]) != 1: _bad("invalid_implementation_artifact", "implementation_patch", "must resolve to one file")
        try: implementation_bytes = paths["implementation_patch"][0].read_bytes()
        except OSError as exc: _bad("invalid_implementation_artifact", "implementation_patch", str(exc))
    _implementation_check(implementation, implementation_bytes, implementation_digest_check)
    return family, implementation, config
def _implementation_check(declared: str | None, content: bytes | bytearray | memoryview | None, check: ImplementationDigestCheck | None) -> None:
    if (content is not None or check is not None) and declared is None:
        _bad("missing_implementation_digest", "implementation_sha256", "is required")
    if content is not None:
        if not isinstance(content, (bytes, bytearray, memoryview)):
            _bad("invalid_verifier_input", "implementation_bytes", "must be bytes-like")
        if hashlib.sha256(bytes(content)).hexdigest() != declared:
            _bad("implementation_digest_mismatch", "implementation_sha256", "bytes do not match")
    if check is not None:
        try:
            verdict = check(declared)
        except Exception as exc:
            _bad("implementation_digest_mismatch", "implementation_digest_check", str(exc))
        _callback(verdict, "implementation_digest_mismatch", "implementation_digest_check")
def _decimal(value: Any, location: str, *, numeric_text: bool = False) -> Decimal:
    def fail(message: str) -> None:
        _bad("nonfinite_metric", location, message)
    types = (int, float, Decimal, str) if numeric_text else (int, float, Decimal)
    if isinstance(value, bool) or not isinstance(value, types):
        fail(f"{location} cannot be bool" if isinstance(value, bool) else f"{location} is not numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail(f"{location} is not numeric")
    if not number.is_finite():
        fail(f"{location} must be finite")
    return number
def _aggregate(values: list[Decimal], mode: str) -> Decimal:
    return max(values) if mode == "max" else sum(values, Decimal()) / Decimal(len(values))
def _json_path(value: Any, path: str) -> Any:
    current = value
    for token in path.split("."):
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return _MISSING
    return current
def _seeds(raw: Any, location: str) -> tuple[object, ...]:
    if not isinstance(raw, (list, tuple)) or any(isinstance(value, bool) or not isinstance(value, (int, str)) or isinstance(value, str) and (not value or value != value.strip()) for value in raw):
        _bad("invalid_trial", location, "must contain only integers or non-empty trimmed strings")
    result = tuple(raw)
    if len({(type(value).__name__, value) for value in result}) != len(result):
        _bad("invalid_component", location, "must not contain duplicates")
    return result
def _map(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _bad("invalid_contract", location, "must be an object with text keys")
    return value
def _text(value: Any, location: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _bad("invalid_contract", location, "must be non-empty trimmed text")
    if pattern is not None and pattern.fullmatch(value) is None:
        _bad("invalid_contract", location, "must be a stable identifier")
    return value
def _texts(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _bad("invalid_contract", location, "must be an array")
    result = tuple(_text(item, f"{location}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        _bad("invalid_contract", location, "must not contain duplicates")
    return result
def _callback(verdict: Any, code: str, location: str) -> None:
    if verdict is None or verdict is True:
        return
    message = verdict or "empty callback diagnostic" if isinstance(verdict, str) else f"callback rejected {verdict!r}"
    _bad(code, location, message)
def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result
def _reject_component_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")
def _bad(code: str, location: str, message: str) -> None:
    raise ComponentResultValidationError(code, location, message)
__all__ = ["ComponentResultValidationError", "GitApplyCheck", "ImplementationDigestCheck",
           "OUTCOME_ADAPTER_VERSION", "ValidatedComponentResult", "component_result_contract_parts",
           "repairable_component_result_error", "validate_component_result"]
