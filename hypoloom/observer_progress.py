"""Read scientific progress and technical failures from their exact published sources."""
from __future__ import annotations

import json

from .artifacts import ArtifactKey


def result_producers(run_id, attempts):
    producers = {}
    for attempt in attempts:
        if attempt.get("kind") != "PROCESS" or attempt.get("run_id") != run_id:
            continue
        try:
            key = ArtifactKey(run_id=run_id, experiment_id=attempt.get("experiment_id"),
                execution_id=attempt.get("execution_id"), attempt_id=attempt.get("attempt_id"), artifact_kind="result")
        except (ValueError, TypeError):
            continue
        producers[key.artifact_id] = attempt
    return producers


def stage_coverage(measurement, outcome):
    evidence = (measurement or {}).get("evidence")
    if not evidence:
        return None
    verified = (outcome or {}).get("outcome") in {"SUCCESS", "CLEAN_REFUTATION"} and outcome.get("metric_value") is not None
    return {"stage": evidence["stage"], "units": evidence["units"], "replicates": evidence.get("replicates"),
        "validated": verified}


def failure_stage(process, expert):
    if process is None:
        return "unobserved", None
    observation = process.get("observation")
    if observation is None:
        observation = json.loads(process.get("observation_json") or "{}")
    if observation.get("synthetic_before_spawn"):
        if observation.get("failure_class") == "implementation_not_verified":
            verdict = (observation.get("implementation_verification") or {}).get("report", {}).get("verdict")
            return "implementation_verification", expert if verdict == "changes_required" else "verifier"
        return "before_process_start", "host"
    if observation.get("returncode") is not None or process.get("process_group_id") is not None:
        from .gpu_diagnostics import confirmed_gpu_fault
        return "formal_execution", "operator" if confirmed_gpu_fault(observation) else expert
    return "unobserved", None


def research_progress(facts, producers, owners):
    specs = {row["experiment_id"]: row["payload"] for row in facts if row["fact_type"] == "ExperimentPreregistered"}
    progress = {}
    outcomes = (row for row in facts if row["fact_type"] == "CanonicalOutcomeAccepted")
    for row in sorted(outcomes, key=lambda r: (r.get("created_at") or "", r.get("fact_id") or "")):
        outcome = row["payload"]
        experiment = row["experiment_id"]
        spec = specs.get(experiment, {})
        component = outcome.get("component_id")
        if not component:
            continue
        view = progress.setdefault(component, {"last_evidence": None, "last_failure": None, "failure_counts": {}})
        record = {"experiment_id": experiment, "outcome_id": outcome.get("outcome_id"), "at": row.get("created_at"),
            "scope": (spec.get("goal_evaluation") or {}).get("scope")}
        if outcome.get("outcome") in {"SUCCESS", "CLEAN_REFUTATION"}:
            view["last_evidence"] = {**record, "coverage": stage_coverage(spec.get("measurement"), outcome)}
        elif outcome.get("outcome") == "EXECUTION_FAILURE":
            stage, owner = failure_stage(producers.get(outcome.get("artifact_id")), owners.get(component))
            if outcome.get("validation_code") == "goal_preflight_failed":
                stage, owner = "goal_preflight", owners.get(component)
            counts = view["failure_counts"].setdefault(record["scope"] or "component", {})
            counts[stage] = counts.get(stage, 0) + 1
            view["last_failure"] = {**record, "stage": stage, "owner": owner}
    return progress
