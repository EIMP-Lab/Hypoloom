"""The sole Host's common-source and evaluation integration, counted as core."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .agent_aci import AgentACIError, AgentRequest, EmbeddedInput, WorkspaceArtifact, canonical_json, validate_postflight
from .measurement_contract import ScientificMeasurement, evaluate_parsed_measurement
from .shared_acceptance import SharedAcceptance, acceptance_source_files, evaluate_shared_acceptance


class SharedAcceptanceHost:
    def __init__(self, host: Any):
        self.host = host

    def enabled(self, run_id: str) -> bool:
        return self.host._research_contract(run_id).document["goal"].get("evaluation") == {"mode": "grounding"}

    def source(self, run_id: str, *, required: bool = False):
        result = self.host.store.cached_read(("shared-acceptance", run_id), lambda: self._source(run_id))
        if required and result is None:
            raise AgentACIError("shared_acceptance_source", "expected one accepted final Grounding evaluator")
        return result

    def _source(self, run_id: str):
        if not self.enabled(run_id): return None
        sources = []
        for row in self.host.store.inspect(
                "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT' "
                "AND state='CLOSED' AND terminal_reason='SUCCEEDED' "
                "AND json_extract(observation_json,'$.request.command')='GROUNDING'", (run_id,)):
            observed = json.loads(row["observation_json"])
            request = AgentRequest.from_dict(observed["request"])
            validated = validate_postflight(request, observed["response"])
            declaration = validated.candidate.acceptance
            if declaration is not None:
                sources.append((row["attempt_id"], declaration, acceptance_source_files(declaration, validated.workspace_files)))
        if len(sources) > 1:
            raise AgentACIError("shared_acceptance_source", "expected one accepted final Grounding evaluator")
        return sources[0] if sources else None

    def input(self, run_id: str) -> EmbeddedInput:
        attempt_id, acceptance, files = self.source(run_id, required=True)
        return EmbeddedInput.from_bytes(run_id + "--shared-acceptance", "application/json", canonical_json({
            "source_attempt_id": attempt_id, "acceptance": acceptance.to_dict(),
            "files": [WorkspaceArtifact.from_bytes(path, data).to_dict() for path, data in sorted(files.items())]}))

    def binding(self, run_id: str, scope: str | None):
        if not self.enabled(run_id):
            if scope is not None: raise AgentACIError("shared_acceptance_scope", "legacy run has no common evaluator")
            return None
        if scope not in {"LOCAL", "GOAL"}: raise AgentACIError("shared_acceptance_scope", "select LOCAL or GOAL")
        attempt_id, acceptance, _ = self.source(run_id, required=True)
        return {"scope": scope, "source_attempt_id": attempt_id, "acceptance": acceptance.to_dict()}

    def qualifies(self, run_id: str, value: Mapping[str, Any] | None) -> bool:
        source = self.source(run_id)
        if source is None or value is None: return False
        if value.get("source_attempt_id") != source[0]: raise AgentACIError("shared_acceptance_source", "outcome changed its common source")
        return evaluate_shared_acceptance(source[1], {name: value[name] for name in ("measurements", "checks")})

    def assessment_qualifies(self, run_id: str, assessment, evidence, outcomes) -> bool:
        if not self.enabled(run_id): return assessment["payload"].get("assessment") == "SUPPORTED"
        linked = {row["outcome_id"] for row in evidence.values() if row["subject_id"] in assessment["payload"]["evidence_ids"]}
        return any(self.qualifies(run_id, row["payload"].get("goal_evaluation")) for row in outcomes.values() if row["outcome_id"] in linked)

    def plan(self, spec, prepared, workspace, response):
        from .shared_acceptance_execution import plan_evaluation
        binding = spec["payload"]["goal_evaluation"]
        expected = self.binding(spec["run_id"], binding["scope"])
        if binding != expected: raise AgentACIError("shared_acceptance_source", "preregistered evaluator differs from accepted Grounding")
        _, acceptance, goal_files = self.source(spec["run_id"], required=True)
        local = spec["payload"].get("measurement")
        files = {item.path: item.content_bytes() for raw in response["arguments"]["workspace_files"] if (item := WorkspaceArtifact.from_dict(raw))}
        evaluation = plan_evaluation(prepared, local_measurement=None if local is None else ScientificMeasurement.from_dict(local),
            local_files=files, goal_acceptance=acceptance if binding["scope"] == "GOAL" else None, goal_files=goal_files,
            execution_id=spec["execution_id"], timeout_seconds=spec["payload"]["execution_contract"]["timeout_seconds"],
            python_executable=str(workspace.python_executable), runtime_prefix=str(workspace.runtime_prefix))
        return evaluation.execution, evaluation

    def evaluate(self, spec, publication: Path, observation, prepared):
        from .shared_acceptance_execution import evaluation_phase_success, raw_execution_failed
        from .measurement_execution import read_parser_result
        raw_ok, local_ok, goal_ok = evaluation_phase_success(observation,
            has_local=prepared.local_parser is not None, has_goal=prepared.goal_parser is not None)
        binding = spec["payload"]["goal_evaluation"]
        local_result = goal_result = None
        errors = []
        # Validate each stage's exact bounded output independently; publication
        # equality is checked against the existing canonical artifact bytes.
        for name, parser, ok, relative in (("local", prepared.local_parser, local_ok, "result.json"),
                ("goal", prepared.goal_parser, goal_ok, "goal/result.json")):
            if parser is None: continue
            if not ok:
                if raw_ok: detail = " parser did not complete successfully"
                elif raw_execution_failed(observation): detail = " parser not run because raw execution did not complete successfully"
                else: detail = " parser result unavailable because execution phase completion is unconfirmed"
                if not raw_ok:
                    detail += f" (terminal_reason={observation.get('terminal_reason')}, returncode={observation.get('returncode')})"
                errors.append(name + detail); continue
            try:
                raw = read_parser_result(parser)
                if json.loads((publication / relative).read_bytes()) != raw: raise ValueError("published parsed observations differ")
                if name == "local":
                    checked = evaluate_parsed_measurement(ScientificMeasurement.from_dict(spec["payload"]["measurement"]), raw)
                    local_result = {"metric_value": checked.metric_value, "criterion_met": checked.criterion_met,
                        "method_family_id": None, "logical_artifacts": {}}
                else:
                    accepted = SharedAcceptance.from_dict(binding["acceptance"])
                    met = evaluate_shared_acceptance(accepted, raw)
                    goal_result = {"source_attempt_id": binding["source_attempt_id"], **raw}
                    if prepared.local_parser is None:
                        invalid = [name for name in accepted.validity_checks if not raw["checks"][name]]
                        if invalid: errors.append("goal validity checks failed: " + ", ".join(invalid))
                        else: local_result = {"metric_value": None, "criterion_met": met, "method_family_id": None, "logical_artifacts": {}}
            except (OSError, ValueError) as exc: errors.append(f"{name}: {exc}")
        return local_result, goal_result, "; ".join(errors) or None
