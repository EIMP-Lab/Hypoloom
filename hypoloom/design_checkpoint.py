"""Host-verified design progress and measured usage across attempts of one node."""
from __future__ import annotations

import hashlib
import json

from .agent_aci import EFFICIENT_AGENT_ACI_SCHEMA_VERSION, EmbeddedInput, canonical_json
from .provider_observation import measured_usage


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def checkpoint(tx, request, observation):
    if request.schema_version < EFFICIENT_AGENT_ACI_SCHEMA_VERSION or request.command.value != "EXPERIMENT_DESIGN_IMPLEMENT":
        return {}
    rows = tx.list_attempts(workflow_id=request.owner_workflow_id, command_id=request.command_id)
    history = [json.loads(row["observation_json"] or "{}") for row in sorted(rows, key=lambda r: r["attempt_ordinal"])
               if row["state"] == "CLOSED" and row["kind"] == "AGENT" and row["attempt_ordinal"] < request.attempt_ordinal]
    return {"design_checkpoint": build_checkpoint(request, observation, history)}


def build_checkpoint(request, observation, history):
    usage = measured_usage(observation)
    previous = None
    for prior in history:
        for k, n in measured_usage(prior).items():
            usage[k] = usage.get(k, 0) + n
        previous = prior.get("design_checkpoint")
    authoring = observation.get("diagnostics", {}).get("structured_output_correction", {}).get("authoring") or {}
    marker = authoring.get("deterministic_failure") if observation.get("retry_class") == "MODEL" else None
    checks = authoring.get("verified_checks", [])
    artifact = authoring.get("artifact_digest")
    output = observation.get("response", {}).get("output_digest")
    evidence = None
    if marker:
        input_hashes = [(item.ref, hashlib.sha256(item.content_bytes()).hexdigest())
                        for item in request.input_artifacts if item.ref in request.immutable_context_refs]
        evidence = digest([request.run_id, request.command_id, request.schema_version, request.required_model_identity,
                           request.tool_permission_profile, input_hashes, marker, artifact])
    repeated = bool(evidence and previous and previous.get("failure_fingerprint") == evidence
                    and set(checks) <= set(previous["verified_checks"]))
    record = {"attempt_id": request.attempt_id, "verified_checks": checks, "artifact_digest": artifact,
              "accepted_output_digest": output, "failure": marker, "failure_fingerprint": evidence,
              "would_pause": repeated, "measured_usage": usage}
    if error := authoring.get("validation_error"):
        record["repair"] = error
    return record


def handoff(attempts):
    if not attempts:
        return ()
    latest = json.loads(attempts[-1]["observation_json"] or "{}")
    saved = latest.get("design_checkpoint")
    if not saved:
        return ()
    return (EmbeddedInput.from_bytes(saved["attempt_id"] + "--design-checkpoint", "application/json", canonical_json(saved)),)
