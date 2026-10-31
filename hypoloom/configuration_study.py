"""Read-only comparison of a finite configuration study; no execution or verdict.

This module also runs as a standalone script in a version-bound expert plugin.
Keep it standard-library-only so a task can include it in its admitted parser
source without importing the Host or acquiring any lifecycle authority.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


class StudyError(ValueError):
    """The supplied evidence cannot represent the declared comparison."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StudyError(message)


def _object(value: Any, required: set[str], optional: set[str], where: str) -> Mapping:
    _require(isinstance(value, dict), f"{where}: expected an object")
    _require(required <= value.keys() <= required | optional,
             f"{where}: missing {sorted(required - value.keys())}, "
             f"unknown {sorted(value.keys() - required - optional)}")
    return value


def _text(value: Any, where: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()) and value == value.strip(),
             f"{where}: expected nonempty trimmed text")
    return value


def _integer(value: Any, where: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, f"{where}: expected integer >= {minimum}")
    return value


def _number(value: Any, where: str) -> Decimal:
    _require(type(value) in (int, float), f"{where}: expected a finite JSON number")
    number = Decimal(str(value))
    _require(number.is_finite(), f"{where}: expected a finite JSON number")
    return number


def _json_value(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _text(key, where)
            _json_value(child, where + "." + key)
    elif isinstance(value, list):
        for child in value:
            _json_value(child, where)
    else:
        _require(value is None or type(value) in (str, bool, int)
                 or type(value) is float and math.isfinite(value),
                 f"{where}: expected finite JSON values")


def _equal(left: Any, right: Any) -> bool:
    # Python considers True == 1, including inside nested dictionaries/lists.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _seeds(value: Any, where: str) -> list[int]:
    _require(isinstance(value, list) and bool(value), f"{where}: expected a nonempty array")
    for seed in value:
        _integer(seed, where)
    _require(len(value) == len(set(value)), f"{where}: duplicate seed")
    return value


def _plan(value: Any) -> Mapping:
    plan = _object(value, {"schema_version", "datasets", "seeds", "metric", "direction",
        "dataset_weights", "max_trials", "shared_architecture", "shared_training",
        "context", "candidates"}, {"evaluation"}, "plan")
    _require(type(plan["schema_version"]) is int and plan["schema_version"] == 1,
             "plan.schema_version: expected 1")
    datasets = plan["datasets"]
    _require(isinstance(datasets, list) and bool(datasets), "plan.datasets: expected a nonempty array")
    for dataset in datasets:
        _text(dataset, "plan.datasets")
    _require(len(set(datasets)) == len(datasets), "plan.datasets: duplicate dataset")
    _seeds(plan["seeds"], "plan.seeds")
    _text(plan["metric"], "plan.metric")
    _require(plan["direction"] in ("maximize", "minimize"), "plan.direction: expected maximize or minimize")
    weights = _object(plan["dataset_weights"], set(datasets), set(), "plan.dataset_weights")
    for dataset, weight in weights.items():
        _require(_number(weight, f"weight.{dataset}") > 0, "dataset weights must be positive")
    context = _object(plan["context"], {"implementation_ref", "data_refs"}, set(), "plan.context")
    _text(context["implementation_ref"], "plan.context.implementation_ref")
    for reference in _object(context["data_refs"], set(datasets), set(), "plan.context.data_refs").values():
        _text(reference, "plan.context.data_refs")
    for key in ("shared_architecture", "shared_training"):
        _require(type(plan[key]) is bool, f"plan.{key}: expected boolean")
    candidates = plan["candidates"]
    _require(isinstance(candidates, list) and bool(candidates), "plan.candidates: expected a nonempty array")
    identities = set()
    for candidate in candidates:
        _object(candidate, {"id", "configs", "rationale"}, set(), "candidate")
        identity = _text(candidate["id"], "candidate.id")
        _require(identity not in identities, f"duplicate candidate {identity}")
        identities.add(identity)
        _text(candidate["rationale"], f"candidate.{identity}.rationale")
        configs = _object(candidate["configs"], set(datasets), set(), f"candidate.{identity}.configs")
        for dataset, config in configs.items():
            _object(config, {"architecture", "training", "adaptation", "runtime"}, set(), f"config.{dataset}")
            for group, settings in config.items():
                _require(isinstance(settings, dict), f"config.{dataset}.{group}: expected an object")
                _json_value(settings, f"config.{dataset}.{group}")
        for group in ("architecture", "training"):
            if plan["shared_" + group]:
                _require(all(_equal(configs[datasets[0]][group], configs[dataset][group]) for dataset in datasets),
                         f"candidate {identity}: {group} differs across datasets")
    count = len(candidates) * len(datasets) * len(plan["seeds"])
    _require(_integer(plan["max_trials"], "plan.max_trials", 1) >= count,
             "plan.max_trials: smaller than the declared candidate matrix")
    if "evaluation" in plan:
        evaluation = _object(plan["evaluation"], {"seeds", "split"}, set(), "plan.evaluation")
        _seeds(evaluation["seeds"], "plan.evaluation.seeds")
        _require(evaluation["split"] in ("validation", "test"), "plan.evaluation.split: expected validation or test")
    return plan


def _records(plan: Mapping, records: Any, *, candidate_ids: Sequence[str], seeds: Sequence[int],
             split: str, where: str) -> tuple[dict, dict]:
    _require(isinstance(records, list), f"{where}: expected an array")
    candidates = {candidate["id"]: candidate for candidate in plan["candidates"]}
    histories: dict[tuple, list] = {}
    completed = {}
    counts = {"completed": 0, "failed": 0, "interrupted": 0}
    base = {"candidate_id", "dataset", "seed", "attempt", "status", "evidence_ref"}
    measured = {"resolved_config", "implementation_ref", "data_ref", "metric", "split", "value"}
    for index, record in enumerate(records):
        location = f"{where}[{index}]"
        _require(isinstance(record, dict), f"{location}: expected an object")
        success = record.get("status") == "completed"
        _object(record, base | (measured if success else {"reason"}),
                {"duration_seconds"} | (set() if success else measured - {"value"}), location)
        identity = _text(record["candidate_id"], location + ".candidate_id")
        dataset = _text(record["dataset"], location + ".dataset")
        seed = _integer(record["seed"], location + ".seed")
        attempt = _integer(record["attempt"], location + ".attempt", 1)
        status = _text(record["status"], location + ".status")
        _require(identity in candidate_ids and dataset in plan["datasets"] and seed in seeds,
                 f"{location}: unplanned candidate, dataset or seed")
        _require(status in counts, f"{location}: unknown status {status}")
        _text(record["evidence_ref"], location + ".evidence_ref")
        if not success:
            _text(record["reason"], location + ".reason")
        if "duration_seconds" in record:
            _require(_number(record["duration_seconds"], location + ".duration_seconds") >= 0,
                     f"{location}: duration must be nonnegative")
        expected = {"resolved_config": candidates[identity]["configs"][dataset],
                    "implementation_ref": plan["context"]["implementation_ref"],
                    "data_ref": plan["context"]["data_refs"][dataset],
                    "metric": plan["metric"], "split": split}
        for key, value in expected.items():
            if key in record:
                _require(_equal(record[key], value), f"{location}: {key} differs from plan")
        key = (identity, dataset, seed)
        histories.setdefault(key, []).append((attempt, status))
        counts[status] += 1
        if success:
            _require(key not in completed, f"{location}: duplicate completed trial")
            completed[key] = _number(record["value"], location + ".value")
    for key, history in histories.items():
        history.sort()
        _require([item[0] for item in history] == list(range(1, len(history) + 1)),
                 f"{where}: duplicate or missing attempt for {key}")
        _require(all(status != "completed" for _, status in history[:-1]),
                 f"{where}: retry after a completed trial for {key}")
    return completed, counts


def _cell(identity: str, dataset: str, seed: int) -> dict:
    return {"candidate_id": identity, "dataset": dataset, "seed": seed}


def audit_study(plan: Mapping, trials: list, *, selection: Mapping | None = None,
                evaluation: list | None = None) -> dict:
    """Derive coverage, a validation ranking and configuration correspondence.

    This does not mutate inputs, certify scientific quality, schedule missing
    trials or prove the provenance of the supplied training observations.
    """
    plan = _plan(plan)
    identities = [candidate["id"] for candidate in plan["candidates"]]
    completed, counts = _records(plan, trials, candidate_ids=identities,
                                seeds=plan["seeds"], split="validation", where="trials")
    _require(len(trials) <= plan["max_trials"], "trials: attempted trials exceed plan.max_trials (retries count)")
    missing = [_cell(identity, dataset, seed)
               for identity in identities for dataset in plan["datasets"] for seed in plan["seeds"]
               if (identity, dataset, seed) not in completed]
    scores = {}
    ranking_values = {}
    for identity in identities:
        per_dataset = {}
        for dataset in plan["datasets"]:
            values = [completed[(identity, dataset, seed)] for seed in plan["seeds"]
                      if (identity, dataset, seed) in completed]
            per_dataset[dataset] = {"completed_seeds": len(values), "required_seeds": len(plan["seeds"]),
                                    "mean": str(sum(values) / len(values)) if values else None}
        eligible = all(item["completed_seeds"] == item["required_seeds"] for item in per_dataset.values())
        score = None
        if eligible:
            weights = {dataset: Decimal(str(value)) for dataset, value in plan["dataset_weights"].items()}
            score = sum(Decimal(per_dataset[dataset]["mean"]) * weight for dataset, weight in weights.items()) / sum(weights.values())
            ranking_values[identity] = score
        scores[identity] = {"eligible": eligible, "per_dataset": per_dataset,
                            "score": str(score) if score is not None else None}
    ranking = sorted(ranking_values, key=ranking_values.__getitem__, reverse=plan["direction"] == "maximize")
    complete = not missing
    scope = "complete_plan" if complete else "completed_candidates_only" if ranking else "no_complete_candidate"
    report = {"schema_version": 1, "search_complete": complete, "recommendation_scope": scope,
              "planned_trials": len(identities) * len(plan["datasets"]) * len(plan["seeds"]),
              "attempted_trials": len(trials), "max_trials": plan["max_trials"],
              "counts": counts, "missing": missing, "candidates": scores, "ranking": ranking,
              "recommended_candidate": ranking[0] if ranking else None, "selection": None, "evaluation": None}
    if selection is not None:
        _object(selection, {"candidate_id", "configs"}, set(), "selection")
        identity = _text(selection["candidate_id"], "selection.candidate_id")
        _require(bool(ranking) and identity == ranking[0], "selection: must match the validation ranking (ties use plan order)")
        selected = next(candidate for candidate in plan["candidates"] if candidate["id"] == identity)
        _require(_equal(selection["configs"], selected["configs"]), "selection: configurations differ from the selected candidate")
        report["selection"] = {"candidate_id": identity, "status": "frozen" if complete else "provisional",
                               "configuration_verified": True}
    if evaluation is not None:
        _require(selection is not None and "evaluation" in plan, "evaluation requires a selection and plan.evaluation")
        identity = selection["candidate_id"]
        final, final_counts = _records(plan, evaluation, candidate_ids=[identity], seeds=plan["evaluation"]["seeds"],
                                      split=plan["evaluation"]["split"], where="evaluation")
        final_missing = [_cell(identity, dataset, seed) for dataset in plan["datasets"] for seed in plan["evaluation"]["seeds"]
                         if (identity, dataset, seed) not in final]
        report["evaluation"] = {"complete": not final_missing, "counts": final_counts, "missing": final_missing,
                                "verified_completed_trials": len(final), "used_for_selection": False}
    return report


def _unique_object(pairs: list[tuple]) -> dict:
    value = {}
    for key, item in pairs:
        _require(key not in value, f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise StudyError(f"nonfinite JSON constant {value}")


def read_document(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="the admitted finite comparison plan")
    parser.add_argument("--trials", required=True, help="training-emitted validation observations, including failed attempts")
    parser.add_argument("--selection", help="the selected candidate and exact resolved configurations")
    parser.add_argument("--evaluation", help="separate final observations; never used to rank candidates")
    args = parser.parse_args(argv)
    try:
        report = audit_study(read_document(args.plan), read_document(args.trials),
                             selection=read_document(args.selection) if args.selection else None,
                             evaluation=read_document(args.evaluation) if args.evaluation else None)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"configuration_study: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
