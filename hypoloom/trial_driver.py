"""Host-owned sequential trial runner and component-result normalizer.

The driver consumes one canonical JSON plan assembled from GoalDefined.  It
does not accept Agent-authored commands, invoke a shell, or persist authority.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHAPES = {"paired_control_treatment", "seeded_trials", "checkpoint_audit"}
_PLAN_REQUIRED = {
    "schema_version", "shape", "identity", "dataset", "metric_paths",
    "candidate_manifest_resource", "data_checks", "trials",
}
_PLAN_OPTIONAL = {
    "per_class_path", "class_labels", "audit_path", "audit_max",
    "audit_required_arms", "checkpoint_resource", "checkpoint_torch_load",
}
_TRIAL_REQUIRED = {"module", "pythonpath", "seed", "target_argv", "raw_result"}
_TRIAL_OPTIONAL = {"arm", "model", "checkpoint"}


class TrialDriverError(RuntimeError):
    """The frozen batch cannot produce one complete canonical result."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _load_json(raw: str, label: str) -> Any:
    try:
        value = json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TrialDriverError(f"{label} is invalid JSON: {exc}") from exc
    _finite_tree(value, label)
    return value


def _finite_tree(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise TrialDriverError(f"{label} contains a non-finite number")
    if isinstance(value, list):
        for item in value:
            _finite_tree(item, label)
    elif isinstance(value, Mapping):
        for item in value.values():
            _finite_tree(item, label)


def _text(value: Any, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise TrialDriverError(f"{label} is invalid text")
    return value


def _relative(value: Any, label: str) -> str:
    text = _text(value, label)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or "\\" in text
        or text != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] in {"artifacts", "result.json"}
    ):
        raise TrialDriverError(f"{label} must be a safe output-relative path")
    return text


def _number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrialDriverError(f"{label} must be numeric")
    if not math.isfinite(float(value)):
        raise TrialDriverError(f"{label} must be finite")
    return value


def _string_list(value: Any, label: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not empty and not value):
        raise TrialDriverError(f"{label} must be a{' possibly empty' if empty else ' non-empty'} array")
    result = [_text(item, f"{label}[]") for item in value]
    if len(result) != len(set(result)):
        raise TrialDriverError(f"{label} contains duplicates")
    return result


def _parse_plan(raw: str) -> dict[str, Any]:
    value = _load_json(raw, "plan")
    if not isinstance(value, dict) or set(value) - (_PLAN_REQUIRED | _PLAN_OPTIONAL) or _PLAN_REQUIRED - set(value):
        raise TrialDriverError("plan has unknown or missing fields")
    canonical = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if raw != canonical:
        raise TrialDriverError("plan must use canonical JSON encoding")
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise TrialDriverError("plan.schema_version must be 1")
    if value["shape"] not in _SHAPES:
        raise TrialDriverError("plan.shape is unsupported")
    identity = value["identity"]
    if not isinstance(identity, dict) or set(identity) != {"method_family_id", "implementation_sha256"}:
        raise TrialDriverError("plan.identity has invalid fields")
    _text(identity["method_family_id"], "plan.identity.method_family_id", _ID)
    _text(identity["implementation_sha256"], "plan.identity.implementation_sha256", _SHA256)
    _text(value["dataset"], "plan.dataset")
    metrics = value["metric_paths"]
    if not isinstance(metrics, dict) or not metrics:
        raise TrialDriverError("plan.metric_paths must be a non-empty object")
    for path, name in metrics.items():
        _dotted_path(path, "plan.metric_paths key")
        _text(name, "plan.metric_paths value", _ID)
    if len(set(metrics.values())) != len(metrics):
        raise TrialDriverError("plan.metric_paths canonical names must be unique")
    _text(value["candidate_manifest_resource"], "plan.candidate_manifest_resource", _ID)
    if not isinstance(value["data_checks"], list):
        raise TrialDriverError("plan.data_checks must be an array")
    per_class = value.get("per_class_path")
    labels = value.get("class_labels")
    if (per_class is None) != (labels is None):
        raise TrialDriverError("per_class_path and class_labels must be supplied together")
    if per_class is not None:
        _dotted_path(per_class, "plan.per_class_path")
        _string_list(labels, "plan.class_labels")
    audit_fields = (value.get("audit_path"), value.get("audit_max"), value.get("audit_required_arms"))
    if any(item is not None for item in audit_fields) and any(item is None for item in audit_fields):
        raise TrialDriverError("audit_path, audit_max and audit_required_arms are one contract")
    if audit_fields[0] is not None:
        _dotted_path(audit_fields[0], "plan.audit_path")
        _number(audit_fields[1], "plan.audit_max")
        _string_list(audit_fields[2], "plan.audit_required_arms")
    if value["shape"] == "checkpoint_audit" and audit_fields[0] is None:
        raise TrialDriverError("checkpoint_audit requires its audit contract")
    checkpoint_resource = value.get("checkpoint_resource")
    if value["shape"] == "checkpoint_audit":
        _text(checkpoint_resource, "plan.checkpoint_resource", _ID)
    elif checkpoint_resource is not None:
        raise TrialDriverError("checkpoint_resource is only valid for checkpoint_audit")
    if not isinstance(value.get("checkpoint_torch_load", False), bool):
        raise TrialDriverError("plan.checkpoint_torch_load must be boolean")
    trials = value["trials"]
    if not isinstance(trials, list) or not trials:
        raise TrialDriverError("plan.trials must be a non-empty array")
    parsed = [_parse_trial(item, index, value) for index, item in enumerate(trials)]
    _validate_batch_shape(value["shape"], parsed)
    value["trials"] = parsed
    return value


def _dotted_path(value: Any, label: str) -> tuple[str, ...]:
    text = _text(value, label)
    parts = tuple(text.split("."))
    if any(_ID.fullmatch(part) is None for part in parts):
        raise TrialDriverError(f"{label} is not a safe dotted JSON path")
    return parts


def _parse_trial(raw: Any, index: int, plan: Mapping[str, Any]) -> dict[str, Any]:
    label = f"plan.trials[{index}]"
    if not isinstance(raw, dict) or set(raw) - (_TRIAL_REQUIRED | _TRIAL_OPTIONAL) or _TRIAL_REQUIRED - set(raw):
        raise TrialDriverError(f"{label} has unknown or missing fields")
    _text(raw["module"], f"{label}.module", _MODULE)
    pythonpath = Path(_text(raw["pythonpath"], f"{label}.pythonpath"))
    if not pythonpath.is_absolute() or pythonpath.is_symlink() or not pythonpath.is_dir():
        raise TrialDriverError(f"{label}.pythonpath must be an absolute real directory")
    seed = raw["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise TrialDriverError(f"{label}.seed must be a non-negative integer")
    argv = raw["target_argv"]
    if not isinstance(argv, list) or any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise TrialDriverError(f"{label}.target_argv must be a non-empty string array")
    raw["raw_result"] = _relative(raw["raw_result"], f"{label}.raw_result")
    arm = raw.get("arm")
    if arm is not None:
        _text(arm, f"{label}.arm", _ID)
    model = raw.get("model")
    if model is not None:
        _text(model, f"{label}.model", _ID)
    checkpoint = raw.get("checkpoint")
    if checkpoint is not None:
        if plan["shape"] == "checkpoint_audit" and Path(str(checkpoint)).is_absolute():
            raw["checkpoint"] = _text(checkpoint, f"{label}.checkpoint")
        else:
            raw["checkpoint"] = _relative(checkpoint, f"{label}.checkpoint")
    if _flag(argv, "--dataset", required=True) != plan["dataset"]:
        raise TrialDriverError(f"{label} dataset argv differs from the plan")
    if _flag(argv, "--seed", required=True) != str(seed):
        raise TrialDriverError(f"{label} seed argv differs from the plan")
    _text(_flag(argv, "--causal-mode", required=True), f"{label} causal mode")
    _text(_flag(argv, "--out", required=True), f"{label} output binding")
    argv_model = _flag(argv, "--model", required=False)
    if (model is None and argv_model is not None) or (model is not None and argv_model != model):
        raise TrialDriverError(f"{label} model argv differs from the record")
    allowed_tokens = {"{raw_result}", "{checkpoint}"}
    for item in argv:
        if "{" in item or "}" in item:
            if item not in allowed_tokens:
                raise TrialDriverError(f"{label} contains an unsupported argv token")
    return raw


def _flag(argv: Sequence[str], flag: str, *, required: bool) -> str | None:
    matches = [index for index, value in enumerate(argv[:-1]) if value == flag]
    if len(matches) > 1 or (required and len(matches) != 1):
        raise TrialDriverError(f"target argv has an absent or ambiguous {flag}")
    return argv[matches[0] + 1] if matches else None


def _validate_batch_shape(shape: str, trials: Sequence[Mapping[str, Any]]) -> None:
    raw_paths = [item["raw_result"] for item in trials]
    if len(raw_paths) != len(set(raw_paths)):
        raise TrialDriverError("trial raw_result paths must be unique")
    identities = [(item["seed"], item.get("arm")) for item in trials]
    if len(identities) != len(set(identities)):
        raise TrialDriverError("trial seed/arm identities must be unique")
    if shape == "paired_control_treatment":
        seeds = sorted({item["seed"] for item in trials})
        if len(seeds) != 3 or len(trials) != 6 or any(
            {item.get("arm") for item in trials if item["seed"] == seed} != {"control", "treatment"}
            for seed in seeds
        ):
            raise TrialDriverError("paired_control_treatment requires exact control/treatment pairs for three seeds")
        if any(item.get("model") is None or item.get("checkpoint") is not None for item in trials):
            raise TrialDriverError("paired trials require explicit models and no checkpoints")
        models = {
            arm: {item["model"] for item in trials if item["arm"] == arm}
            for arm in ("control", "treatment")
        }
        if any(len(values) != 1 for values in models.values()) or models["control"] == models["treatment"]:
            raise TrialDriverError("paired control and treatment require distinct frozen model identities")
    elif shape == "seeded_trials":
        if len(trials) != 5 or any(item.get("arm") is not None or item.get("checkpoint") is None for item in trials):
            raise TrialDriverError("seeded_trials requires five unpaired checkpoint-producing trials")
    elif any(item.get("arm") is not None for item in trials):
        raise TrialDriverError("checkpoint_audit trials must be unpaired")


def _required_inputs() -> dict[str, list[str]]:
    raw = os.environ.get("HYPOLOOM_REQUIRED_INPUTS")
    value = _load_json(raw if raw is not None else "", "HYPOLOOM_REQUIRED_INPUTS")
    if not isinstance(value, dict):
        raise TrialDriverError("HYPOLOOM_REQUIRED_INPUTS must be an object")
    result: dict[str, list[str]] = {}
    for key, paths in value.items():
        _text(key, "required input resource", _ID)
        if not isinstance(paths, list) or not paths:
            raise TrialDriverError(f"required input {key!r} must contain paths")
        normalized = []
        for raw_path in paths:
            path = Path(_text(raw_path, f"required input {key!r} path"))
            if not path.is_absolute():
                raise TrialDriverError(f"required input {key!r} path must be absolute")
            normalized.append(str(path))
        if len(normalized) != len(set(normalized)):
            raise TrialDriverError(f"required input {key!r} paths are duplicated")
        result[key] = normalized
    return result


def _run_data_checks(plan: Mapping[str, Any]) -> None:
    checks = plan["data_checks"]
    if not checks:
        return
    try:
        from .data_contracts import validate_hdf5_split_manifest
    except (ImportError, ModuleNotFoundError):
        path = Path(__file__).with_name("data_contracts.py")
        spec = importlib.util.spec_from_file_location("_hypoloom_data_contracts", path)
        if spec is None or spec.loader is None:
            raise TrialDriverError("sibling data_contracts.py is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        validate_hdf5_split_manifest = module.validate_hdf5_split_manifest
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or check.get("type") != "hdf5_split_manifest":
            raise TrialDriverError(f"plan.data_checks[{index}] is unsupported")
        allowed = {"type", "dataset", "cache", "manifest", "val_cache", "declared_val_cache"}
        required = {"type", "dataset", "cache", "manifest"}
        if set(check) - allowed or required - set(check) or check["dataset"] != plan["dataset"]:
            raise TrialDriverError(f"plan.data_checks[{index}] has invalid fields")
        paths: dict[str, Path] = {}
        for field in ("cache", "manifest", "val_cache"):
            if field not in check:
                continue
            path = Path(_text(check[field], f"plan.data_checks[{index}].{field}"))
            if not path.is_absolute():
                raise TrialDriverError(f"plan.data_checks[{index}].{field} must be absolute")
            paths[field] = path
        try:
            validate_hdf5_split_manifest(
                paths["cache"], paths["manifest"], expected_dataset=plan["dataset"],
                val_cache_path=paths.get("val_cache"),
                declared_val_cache=str(check.get("declared_val_cache", "")),
            )
        except Exception as exc:
            raise TrialDriverError(f"data check {index} failed: {exc}") from exc


def _resource_file(resources: Mapping[str, list[str]], name: str) -> Path:
    paths = resources.get(name)
    if paths is None or len(paths) != 1:
        raise TrialDriverError(f"resource {name!r} must resolve to exactly one file")
    path = Path(paths[0])
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise TrialDriverError(f"resource {name!r} is not a non-empty regular file")
    return path


def _candidate_manifest(plan: Mapping[str, Any], resources: Mapping[str, list[str]]) -> tuple[bytes, Mapping[str, Any]]:
    path = _resource_file(resources, plan["candidate_manifest_resource"])
    try:
        content = path.read_bytes()
        value = _load_json(content.decode("utf-8"), "candidate_manifest")
    except (OSError, UnicodeError) as exc:
        raise TrialDriverError(f"candidate_manifest cannot be read: {exc}") from exc
    identity = plan["identity"]
    if (
        not isinstance(value, Mapping)
        or value.get("method_family_id") != identity["method_family_id"]
        or value.get("implementation_sha256") != identity["implementation_sha256"]
    ):
        raise TrialDriverError("candidate_manifest identity differs from the plan")
    config = value.get("core_config_sha256")
    if config is not None and (not isinstance(config, str) or _SHA256.fullmatch(config) is None):
        raise TrialDriverError("candidate_manifest core_config_sha256 is invalid")
    return content, value


def _expand_audit_checkpoints(
    plan: Mapping[str, Any], resources: Mapping[str, list[str]]
) -> tuple[list[dict[str, Any]], list[Path]]:
    trials = [dict(item) for item in plan["trials"]]
    if plan["shape"] != "checkpoint_audit":
        return trials, []
    raw_paths = resources.get(plan["checkpoint_resource"])
    if raw_paths is None or len(raw_paths) != len(trials):
        raise TrialDriverError("checkpoint resource count must equal audit trial count")
    checkpoints = [Path(value) for value in raw_paths]
    for index, (trial, checkpoint) in enumerate(zip(trials, checkpoints, strict=True)):
        _regular_checkpoint(checkpoint, f"checkpoint resource[{index}]")
        declared = trial.get("checkpoint")
        if declared is not None and Path(str(declared)) != checkpoint:
            raise TrialDriverError("audit trial checkpoint differs from its resource")
        trial["checkpoint"] = str(checkpoint)
        if "{checkpoint}" not in trial["target_argv"] and str(checkpoint) not in trial["target_argv"]:
            raise TrialDriverError("audit trial argv does not consume its accepted checkpoint")
    return trials, checkpoints


def _regular_checkpoint(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise TrialDriverError(f"{label} is not a non-empty regular checkpoint")


def _output_path(root: Path, relative: str) -> Path:
    return root.joinpath(*PurePosixPath(relative).parts)


def _command(trial: Mapping[str, Any], root: Path) -> list[str]:
    raw_path = _output_path(root, trial["raw_result"])
    checkpoint = trial.get("checkpoint")
    checkpoint_path = (
        Path(str(checkpoint)) if checkpoint is not None and Path(str(checkpoint)).is_absolute()
        else _output_path(root, str(checkpoint)) if checkpoint is not None else None
    )
    replacements = {"{raw_result}": str(raw_path)}
    if checkpoint_path is not None:
        replacements["{checkpoint}"] = str(checkpoint_path)
    argv = [replacements.get(item, item) for item in trial["target_argv"]]
    if any(item in {"{raw_result}", "{checkpoint}"} for item in argv):
        raise TrialDriverError("trial argv needs a checkpoint that was not resolved")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if checkpoint_path is not None and checkpoint_path.is_relative_to(root):
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    return [sys.executable, "-m", trial["module"], *argv]


def _raw_result(path: Path, trial: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise TrialDriverError(f"child did not produce {trial['raw_result']}")
    try:
        raw = _load_json(path.read_text(encoding="utf-8"), f"raw result {trial['raw_result']}")
    except (OSError, UnicodeError) as exc:
        raise TrialDriverError(f"raw result cannot be read: {exc}") from exc
    if not isinstance(raw, dict):
        raise TrialDriverError("raw result must contain an object")
    expected_model = trial.get("model")
    expected_mode = _flag(trial["target_argv"], "--causal-mode", required=True)
    if (
        raw.get("dataset") != plan["dataset"]
        or raw.get("seed") != trial["seed"]
        or isinstance(raw.get("seed"), bool)
        or raw.get("causal_mode") != expected_mode
        or (expected_model is None and raw.get("model") is not None)
        or (expected_model is not None and raw.get("model") != expected_model)
    ):
        raise TrialDriverError("raw result dataset/model/seed/causal_mode differs from the trial")
    metrics = {
        canonical: _number(_json_path(raw, dotted), f"raw metric {dotted}")
        for dotted, canonical in plan["metric_paths"].items()
    }
    per_class = None
    if plan.get("per_class_path") is not None:
        supplied = _json_path(raw, plan["per_class_path"])
        labels = plan["class_labels"]
        if not isinstance(supplied, Mapping) or set(supplied) != set(labels):
            raise TrialDriverError("raw per-class metric keys differ from class_labels")
        per_class = {label: _number(supplied[label], f"per-class metric {label}") for label in labels}
    audit = None
    if plan.get("audit_path") is not None:
        supplied = _json_path(raw, plan["audit_path"])
        arms = plan["audit_required_arms"]
        if isinstance(supplied, Mapping):
            if any(arm not in supplied for arm in arms):
                raise TrialDriverError("raw audit lacks a required arm")
            audit = {arm: _number(supplied[arm], f"audit arm {arm}") for arm in arms}
        elif plan["shape"] == "paired_control_treatment" and trial.get("arm") in arms:
            arm = str(trial["arm"])
            audit = {arm: _number(supplied, f"audit arm {arm}")}
        elif plan["shape"] == "paired_control_treatment":
            audit = {}
        else:
            raise TrialDriverError("raw audit must map every required arm")
        if any(value > plan["audit_max"] for value in audit.values()):
            raise TrialDriverError("raw audit exceeds audit_max")
        if audit:
            metrics["max_future_perturbation_logit_diff"] = max(audit.values())
    return {"raw": raw, "metrics": metrics, "per_class": per_class, "audit": audit}


def _json_path(value: Any, dotted: str) -> Any:
    current = value
    for part in _dotted_path(dotted, "JSON path"):
        if not isinstance(current, Mapping) or part not in current:
            raise TrialDriverError(f"raw result lacks JSON path {dotted!r}")
        current = current[part]
    return current


def _inventory(root: Path) -> set[str]:
    result: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise TrialDriverError(f"child created a symlink: {path}")
        if path.is_file():
            result.add(path.relative_to(root).as_posix())
    return result


def _torch_check(checkpoints: Sequence[Path]) -> None:
    code = "import sys,torch;torch.load(sys.argv[1],map_location='cpu',weights_only=True)"
    for path in checkpoints:
        completed = subprocess.run(
            [sys.executable, "-c", code, str(path)], shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if completed.returncode != 0:
            raise TrialDriverError(f"torch checkpoint validation failed: {completed.stderr.strip()}")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _mean(values: Sequence[int | float]) -> float:
    result = math.fsum(float(value) for value in values) / len(values)
    if not math.isfinite(result):
        raise TrialDriverError("aggregate metric is non-finite")
    return result


def _build_result(
    stage: Path,
    plan: Mapping[str, Any],
    trials: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    candidate_bytes: bytes,
    result_identity: Mapping[str, Any],
    checkpoints: Sequence[Path],
) -> None:
    artifact_root = stage / "artifacts"
    artifact_root.mkdir()
    normalized_trials: list[dict[str, Any]] = []
    for trial, record in zip(trials, records, strict=True):
        metrics = dict(record["metrics"])
        if plan["shape"] == "seeded_trials":
            metrics["n_successful_seeds"] = len(records)
        item: dict[str, Any] = {"seed": trial["seed"], "metrics": metrics, "clean": True}
        if plan["shape"] == "paired_control_treatment":
            item["arm"] = trial["arm"]
        normalized_trials.append(item)
    common = {
        "schema_version": 1, "dataset": plan["dataset"],
        "method_family_id": plan["identity"]["method_family_id"],
    }
    per_trial = {**common, "trials": normalized_trials, "raw_outputs": [record["raw"] for record in records]}
    _write_json(artifact_root / "per_trial_metrics.json", per_trial)
    _write_json(artifact_root / "resolved_config.json", {**common, "shape": plan["shape"], "plan": plan})
    _write_json(artifact_root / "provenance.json", {
        **common, "identity": result_identity,
        "children": [{"module": item["module"], "seed": item["seed"], "arm": item.get("arm")} for item in trials],
    })
    (artifact_root / "candidate_manifest.json").write_bytes(candidate_bytes)
    artifacts: dict[str, str | list[str]] = {
        "per_trial_metrics": "artifacts/per_trial_metrics.json",
        "resolved_config": "artifacts/resolved_config.json",
        "provenance": "artifacts/provenance.json",
        "candidate_manifest": "artifacts/candidate_manifest.json",
    }
    if plan["shape"] == "paired_control_treatment":
        controls = [record["raw"] for trial, record in zip(trials, records, strict=True) if trial["arm"] == "control"]
        _write_json(artifact_root / "baseline_control.json", {**common, "controls": controls})
        artifacts["baseline_control"] = "artifacts/baseline_control.json"
    elif plan["shape"] == "seeded_trials":
        aggregate = {
            name: _mean([record["metrics"][name] for record in records])
            for name in plan["metric_paths"].values()
        }
        _write_json(artifact_root / "aggregate_results.json", {**common, "metrics": aggregate, "n_successful_seeds": len(records)})
        artifacts["aggregate_results"] = "artifacts/aggregate_results.json"
        if plan.get("per_class_path") is not None:
            values = {
                label: _mean([record["per_class"][label] for record in records])
                for label in plan["class_labels"]
            }
            _write_json(artifact_root / "per_class_metrics.json", {
                "schema_version": 1,
                "datasets": {plan["dataset"]: {"per_class_f1": values}},
                "per_trial": [record["per_class"] for record in records],
            })
            artifacts["per_class_metrics"] = "artifacts/per_class_metrics.json"
        checkpoint_refs: list[str] = []
        for trial, source in zip(trials, checkpoints, strict=True):
            relative = f"artifacts/checkpoints/seed-{trial['seed']}.pt"
            destination = stage.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            checkpoint_refs.append(relative)
        artifacts["checkpoints"] = checkpoint_refs
    else:
        audit_values = [record["metrics"]["max_future_perturbation_logit_diff"] for record in records]
        checks = {
            arm: all(record["audit"][arm] <= plan["audit_max"] for record in records)
            for arm in plan["audit_required_arms"]
        }
        _write_json(artifact_root / "causal_audit.json", {
            **common, "max_future_perturbation_logit_diff": max(audit_values),
            "checks": checks, "trials": [record["audit"] for record in records],
        })
        artifacts.pop("per_trial_metrics")
        artifacts["causal_audit"] = "artifacts/causal_audit.json"
        (artifact_root / "per_trial_metrics.json").unlink()
    result = {
        "artifacts": artifacts,
        "identity": result_identity,
        "trials": normalized_trials,
    }
    _write_json(stage / "result.json", result)


def _cleanup(root: Path, *, preserve: Path | None = None) -> None:
    for path in tuple(root.iterdir()):
        if preserve is not None and path == preserve:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def _execute(raw_plan: str) -> None:
    plan = _parse_plan(raw_plan)
    root = Path.cwd()
    if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
        raise TrialDriverError("driver output directory must start as an empty real directory")
    resources = _required_inputs()
    _run_data_checks(plan)
    candidate_bytes, candidate = _candidate_manifest(plan, resources)
    result_identity = dict(plan["identity"])
    if candidate.get("core_config_sha256") is not None:
        result_identity["core_config_sha256"] = candidate["core_config_sha256"]
    trials, input_checkpoints = _expand_audit_checkpoints(plan, resources)
    records: list[Mapping[str, Any]] = []
    generated_checkpoints: list[Path] = []
    expected_files: set[str] = set()
    stage: Path | None = None
    try:
        for index, trial in enumerate(trials):
            command = _command(trial, root)
            environment = os.environ.copy()
            environment.update({"PYTHONPATH": trial["pythonpath"], "PYTHONDONTWRITEBYTECODE": "1"})
            completed = subprocess.run(
                command, shell=False, cwd=root, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            if completed.returncode != 0:
                raise TrialDriverError(f"child {index} failed with {completed.returncode}: {completed.stderr.strip()}")
            raw_path = _output_path(root, trial["raw_result"])
            expected_files.add(trial["raw_result"])
            records.append(_raw_result(raw_path, trial, plan))
            checkpoint = trial.get("checkpoint")
            if plan["shape"] == "seeded_trials":
                checkpoint_path = _output_path(root, str(checkpoint))
                _regular_checkpoint(checkpoint_path, f"trial {index} checkpoint")
                generated_checkpoints.append(checkpoint_path)
                expected_files.add(str(checkpoint))
            if _inventory(root) != expected_files:
                raise TrialDriverError(f"child {index} left undeclared output files")
        checkpoints = generated_checkpoints or input_checkpoints
        if plan.get("checkpoint_torch_load"):
            _torch_check(checkpoints)
        # /output is a bind mount in the production sandbox, so stage inside it
        # to keep the final renames on the same filesystem.
        stage = Path(tempfile.mkdtemp(prefix=".hypoloom-trial-stage-", dir=root))
        _build_result(
            stage,
            plan,
            trials,
            records,
            candidate_bytes,
            result_identity,
            generated_checkpoints,
        )
        _cleanup(root, preserve=stage)
        os.replace(stage / "artifacts", root / "artifacts")
        os.replace(stage / "result.json", root / "result.json")
    except Exception:
        _cleanup(root)
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("trial_driver requires one canonical JSON plan argument", file=sys.stderr)
        return 2
    try:
        _execute(arguments[0])
    except (OSError, TrialDriverError, ValueError) as exc:
        print(f"trial_driver: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["TrialDriverError", "main"]
