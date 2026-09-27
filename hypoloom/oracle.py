"""Independent, read-only closure oracle over raw SQLite rows and files.

This module intentionally imports no production state reducer, scheduler,
scientific adapter, or kernel helper.  Its SQL joins and artifact digest are an
independent outcome oracle rather than another runtime authority.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import stat
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .agent_aci import AgentACIError, AgentRequest, AgentResponse, supports_experiment_submission, supports_grounding_web, supports_literature_web, validate_postflight
from .measurement_contract import ScientificMeasurement


_VERSION_COLUMNS = (
    "workflow_definition_version",
    "research_contract_version",
    "evaluator_version",
    "agent_aci_version",
    "runner_version",
    "artifact_result_schema_version",
    "runtime_environment_id",
    "configuration_id",
    "sqlite_runtime_version",
)
_FACT_ORDER = (
    "GoalDefined",
    "HypothesisProposed",
    "ExperimentPreregistered",
    "CanonicalOutcomeAccepted",
    "EvidenceLinked",
    "HypothesisAssessed",
    "GoalVerdictRecorded",
)
_COMPONENT_FACT_SCHEMA_BY_RESEARCH_VERSION = {
    "hypoloom-research-v4": 4,
    "hypoloom-research-v5": 5,
    "hypoloom-research-v6": 5,
    "hypoloom-research-v7": 5,
    "hypoloom-research-v8": 5,
    "hypoloom-research-v9": 5,
    "hypoloom-research-v10": 5,
    "hypoloom-research-v11": 5,
    "hypoloom-research-v12": 5,
}
_COMPARABLE_OUTCOMES = {"SUCCESS", "CLEAN_REFUTATION"}
_OPERATIONAL_OUTCOMES = {"INCONCLUSIVE", "EXECUTION_FAILURE", "INTEGRITY_REJECTED", "CANCELLED"}
# Independent audit copy of the Host's same-hypothesis revision whitelist;
# drift between the two must fail the audit until deliberately reconciled.
_REVISIONABLE_INTEGRITY_CODES = {"apply_check_failed", "invalid_unified_diff", "missing_implementation_digest", "downstream_target_contract_failed"}
_MISSING_JSON_PATH = object()
_REQUIRED_TABLES = {
    "workflow_runs",
    "workflow_commands",
    "workflow_transitions",
    "workflow_human_decisions",
    "research_facts",
    "research_fact_refs",
    "execution_attempts",
    "execution_artifacts",
}
_TERMINAL_RUN_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
_PROCESS_REQUEST_KEYS = {
    "attempt_id",
    "attempt_ordinal",
    "execution_id",
    "argv",
    "cwd",
    "env",
    "timeout_seconds",
    "terminate_grace_seconds",
    "kill_grace_seconds",
    "stdout_path",
    "stderr_path",
}
_AGENT_COMMAND_TYPES = {
    "PROPOSE_HYPOTHESIS": "REQUEST_HYPOTHESIS_CANDIDATE",
    "DRAFT_EXPERIMENT_SPEC": "REQUEST_SPEC_CANDIDATE",
    "ANSWER_RENDEZVOUS": "REQUEST_RENDEZVOUS",
    "GROUNDING": "REQUEST_AUXILIARY",
    "LITERATURE_REVIEW": "REQUEST_RESEARCH_NODE",
    "HYPOTHESIS_PROPOSAL": "REQUEST_RESEARCH_NODE",
    "EXPERIMENT_DESIGN_IMPLEMENT": "REQUEST_RESEARCH_NODE",
    "EXPERIMENT_EXECUTION": "REQUEST_RESEARCH_NODE",
    "RESULT_ANALYSIS": "REQUEST_RESEARCH_NODE",
    "HYPOTHESIS_UPDATE": "REQUEST_RESEARCH_NODE",
    "CYCLE_REPORT": "REQUEST_RESEARCH_NODE",
    "ADJUDICATE": "REQUEST_RESEARCH_NODE",
    "SELF_EVOLVE": "REQUEST_RESEARCH_NODE",
    "GRAPH_EVOLVE": "REQUEST_AUXILIARY",
    "FINAL_REPORT": "REQUEST_AUXILIARY",
    "OBSERVER": "REQUEST_AUXILIARY",
    "DEBATE": "REQUEST_AUXILIARY",
    "DEBATE_JUDGE": "REQUEST_AUXILIARY",
    "LEADER": "REQUEST_AUXILIARY",
    "COMPACT_CONTEXT": "REQUEST_AUXILIARY",
    "PAPER_READ": "REQUEST_AUXILIARY",
}


def _agent_command_type(raw_request: Mapping[str, Any]) -> str | None:
    """Project the expected lifecycle command from the durable Agent task."""

    command = str(raw_request.get("command"))
    task = raw_request.get("task_context")
    if (
        command == "LITERATURE_REVIEW"
        and isinstance(task, Mapping)
        and task.get("kind") == "AUXILIARY"
        and task.get("request_kind") == "GROUNDING_QUERY_PLAN"
    ):
        return "REQUEST_AUXILIARY"
    return _AGENT_COMMAND_TYPES.get(command)
_AGENT_FACT_COMMANDS = {
    "HypothesisProposed": frozenset({"PROPOSE_HYPOTHESIS", "HYPOTHESIS_PROPOSAL"}),
    "ExperimentPreregistered": frozenset({"DRAFT_EXPERIMENT_SPEC", "EXPERIMENT_DESIGN_IMPLEMENT"}),
}
_PACKET_ONLY_AGENT_COMMANDS = {
    "GROUNDING",
    "LITERATURE_REVIEW",
    "RESULT_ANALYSIS",
    "HYPOTHESIS_UPDATE",
    "CYCLE_REPORT",
    "ADJUDICATE",
    "SELF_EVOLVE",
    "GRAPH_EVOLVE",
    "FINAL_REPORT",
    "OBSERVER",
    "DEBATE",
    "DEBATE_JUDGE",
    "LEADER",
    "COMPACT_CONTEXT",
    "PAPER_READ",
}


def _packet_only_agent_request(request: AgentRequest) -> bool:
    """Identify v7 Agent effects whose validated packet is their terminal effect."""

    if request.schema_version >= 11 and request.command.value in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"}:
        return True
    if request.command.value in _PACKET_ONLY_AGENT_COMMANDS:
        return True
    if request.command.value != "HYPOTHESIS_PROPOSAL":
        return False
    task = request.to_dict().get("task_context")
    key = task.get("key") if isinstance(task, Mapping) else None
    return (
        isinstance(key, Mapping)
        and key.get("node_kind") == "HYPOTHESIS_PROPOSAL"
        and key.get("subkind") in {"FORWARD", "BACKWARD"}
    )
_RENDEZVOUS_REQUEST_KEYS = {
    "schema_version",
    "rendezvous_id",
    "run_id",
    "requester_role",
    "requester_slot",
    "target_role",
    "source_attempt_id",
    "kind",
    "question",
    "context_refs",
    "round",
    "max_rounds",
}
_RENDEZVOUS_REPLY_KEYS = {
    "schema_version",
    "rendezvous_id",
    "kind",
    "disposition",
    "responder_role",
    "summary",
    "supporting_refs",
}
_RENDEZVOUS_KINDS = {"HELP", "DEBATE"}
_RENDEZVOUS_DISPOSITIONS = {
    "ANSWERED",
    "DECLINED",
    "NO_TARGET",
    "TIMED_OUT",
    "CANCELLED",
    "INTEGRITY_REJECTED",
}


@dataclass(frozen=True, slots=True)
class OracleIssue:
    code: str
    message: str
    identifiers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "identifiers": list(self.identifiers),
        }

    def __str__(self) -> str:
        suffix = f" [{', '.join(self.identifiers)}]" if self.identifiers else ""
        return f"{self.code}: {self.message}{suffix}"


@dataclass(frozen=True, slots=True, kw_only=True)
class OracleReport:
    run_id: str
    version_tuple: Mapping[str, str | None]
    fact_types: tuple[str, ...]
    causal_chain: Mapping[str, tuple[str, ...]]
    workflow_state_counts: Mapping[str, int]
    attempt_state_counts: Mapping[str, int]
    attempt_terminal_reason_counts: Mapping[str, int]
    artifact_counts: Mapping[str, int]
    canonical_outcome_count: int
    open_attempt_count: int
    open_decision_count: int
    pending_effect_count: int
    nonterminal_experiment_count: int
    duplicate_effect_count: int
    conflicting_identity_count: int
    owned_process_residue: tuple[int, ...]
    issues: tuple[OracleIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "run_id": self.run_id,
            "version_tuple": dict(self.version_tuple),
            "fact_types": list(self.fact_types),
            "causal_chain": {key: list(value) for key, value in self.causal_chain.items()},
            "workflow_state_counts": dict(self.workflow_state_counts),
            "attempt_state_counts": dict(self.attempt_state_counts),
            "attempt_terminal_reason_counts": dict(self.attempt_terminal_reason_counts),
            "artifact_counts": dict(self.artifact_counts),
            "canonical_outcome_count": self.canonical_outcome_count,
            "open_attempt_count": self.open_attempt_count,
            "open_decision_count": self.open_decision_count,
            "pending_effect_count": self.pending_effect_count,
            "nonterminal_experiment_count": self.nonterminal_experiment_count,
            "duplicate_effect_count": self.duplicate_effect_count,
            "conflicting_identity_count": self.conflicting_identity_count,
            "owned_process_residue": list(self.owned_process_residue),
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Oracle:
    def __init__(self, state_db: str | os.PathLike[str], artifact_root: str | os.PathLike[str]) -> None:
        self.state_db = Path(state_db)
        self.artifact_root = Path(artifact_root)

    def audit(self, run_id: str) -> OracleReport:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be non-empty text")
        issues: list[OracleIssue] = []
        if not self.state_db.is_file():
            return _empty_report(
                run_id,
                OracleIssue("missing_database", f"state database is missing: {self.state_db}"),
            )
        try:
            connection = sqlite3.connect(
                f"file:{self.state_db.resolve().as_posix()}?mode=ro",
                uri=True,
                timeout=5,
            )
        except sqlite3.Error as exc:
            return _empty_report(run_id, OracleIssue("database_open_failed", str(exc)))
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            tables = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            }
            missing_tables = sorted(_REQUIRED_TABLES - tables)
            if missing_tables:
                issues.append(
                    OracleIssue(
                        code="missing_tables",
                        message="required raw tables are absent",
                        identifiers=tuple(missing_tables),
                    )
                )
                return _empty_report(run_id, *issues)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                issues.append(OracleIssue("database_integrity", str(integrity)))
            run = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                issues.append(OracleIssue("missing_run", "workflow_runs has no matching row", (run_id,)))
                return _empty_report(run_id, *issues)
            version_tuple = {name: _row_get(run, name) for name in _VERSION_COLUMNS}
            missing_versions = tuple(name for name, value in version_tuple.items() if not value)
            if missing_versions:
                issues.append(
                    OracleIssue("incomplete_version_tuple", "run has blank version components", missing_versions)
                )

            facts = list(
                connection.execute(
                    "SELECT rowid AS raw_order,* FROM research_facts WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                )
            )
            parsed_payloads = _parse_json_rows(facts, "payload_json", "fact", issues)
            fact_types = tuple(str(row["fact_type"]) for row in facts)
            unknown_types = tuple(sorted(set(fact_types) - set(_FACT_ORDER)))
            if unknown_types:
                issues.append(OracleIssue("unknown_fact_type", "raw fact has unknown type", unknown_types))
            refs = list(
                connection.execute(
                    """SELECT r.* FROM research_fact_refs r
                       JOIN research_facts f ON f.fact_id=r.fact_id
                       WHERE f.run_id=? ORDER BY r.fact_id,r.role,r.ordinal""",
                    (run_id,),
                )
            )
            _audit_references(connection, run_id, refs, issues)
            causal_chain = _audit_fact_chain(facts, parsed_payloads, issues)

            transitions = list(
                connection.execute(
                    "SELECT t.rowid AS raw_order,t.*,c.command_type FROM workflow_transitions t LEFT JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id=? ORDER BY t.rowid",
                    (run_id,),
                )
            )
            transition_payloads = _parse_json_rows(
                transitions, "payload_json", "transition payload", issues
            )

            heads = list(
                connection.execute(
                    """SELECT t.* FROM workflow_transitions t
                       JOIN (
                         SELECT workflow_id,MAX(revision) AS revision
                         FROM workflow_transitions WHERE run_id=? GROUP BY workflow_id
                       ) h ON h.workflow_id=t.workflow_id AND h.revision=t.revision
                       WHERE t.run_id=? ORDER BY t.workflow_kind,t.workflow_id""",
                    (run_id, run_id),
                )
            )
            workflow_state_counts = _counts(str(row["state"]) for row in heads)
            run_heads = [row for row in heads if row["workflow_kind"] == "RUN"]
            if len(run_heads) != 1:
                issues.append(
                    OracleIssue(
                        "run_workflow_head_count",
                        f"expected one RunWorkflow head, found {len(run_heads)}",
                        tuple(str(row["workflow_id"]) for row in run_heads),
                    )
                )
            run_state = str(run_heads[0]["state"]) if len(run_heads) == 1 else None
            experiment_heads = [row for row in heads if row["workflow_kind"] == "EXPERIMENT"]
            nonterminal_experiment_count = sum(row["state"] != "TERMINAL" for row in experiment_heads)
            for row in heads:
                if row["state"] in _TERMINAL_RUN_STATES | {"TERMINAL"} and (
                    row["next_command"] is not None or row["due_at"] is not None
                ):
                    issues.append(
                        OracleIssue(
                            "terminal_workflow_pending_command",
                            "terminal workflow retains a next command or due time",
                            (str(row["workflow_id"]),),
                        )
                    )

            decisions = list(
                connection.execute(
                    "SELECT * FROM workflow_human_decisions WHERE run_id=? ORDER BY decision_id",
                    (run_id,),
                )
            )
            open_decision_count = sum(row["state"] == "OPEN" for row in decisions)
            attempts = list(
                connection.execute(
                    "SELECT * FROM execution_attempts WHERE run_id=? ORDER BY workflow_id,command_id,attempt_ordinal",
                    (run_id,),
                )
            )
            attempt_payloads = _parse_json_rows(
                attempts, "observation_json", "attempt observation", issues, allow_null=True
            )
            attempt_state_counts = _counts(str(row["state"]) for row in attempts)
            attempt_terminal_reason_counts = _counts(
                str(row["terminal_reason"]) for row in attempts if row["terminal_reason"] is not None
            )
            open_attempt_count = attempt_state_counts.get("OPEN", 0)
            duplicate_effect_count, conflicting_identity_count = _audit_attempt_identities(attempts, issues)
            _audit_process_registration(version_tuple.get("runner_version"), attempts, attempt_payloads, issues)
            _audit_gpu_attempt_overlaps(connection, run_id, issues)
            owned_process_residue = _owned_process_residue(attempts)

            commands = list(
                connection.execute(
                    "SELECT * FROM workflow_commands WHERE run_id=? ORDER BY created_at,command_id",
                    (run_id,),
                )
            )
            response_column = "response_status" if commands and "response_status" in commands[0].keys() else "status"
            pending_effect_count = sum(
                row[response_column] is None
                for row in commands
                if response_column in row.keys()
            )

            _audit_agent_attempts(
                version_tuple.get("research_contract_version"),
                version_tuple.get("agent_aci_version"),
                facts,
                parsed_payloads,
                transitions,
                transition_payloads,
                commands,
                attempts,
                attempt_payloads,
                issues,
            )
            expert_loop = any(payload.get("research_contract", {}).get("methodology", {}).get("controls", {}).get("research_loop") == "expert-six-node/1"
                for row in facts if row["fact_type"] == "GoalDefined" for payload in [parsed_payloads.get(str(row["fact_id"]), {})])
            if supports_experiment_submission(version_tuple.get("agent_aci_version")) and expert_loop:
                _audit_submitted_experiments(commands, transitions, transition_payloads, attempts, attempt_payloads, issues, facts=facts)
            if expert_loop and str(version_tuple.get("agent_aci_version", "")).isdigit() and int(version_tuple["agent_aci_version"]) >= 14:
                from .batch_oracle import audit_batches
                issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in
                    audit_batches(run_id, attempts, attempt_payloads, facts, parsed_payloads, transitions, commands,
                        workflow_version=version_tuple.get("workflow_definition_version")))
                from .manager_oracle import audit_manager
                issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in
                    audit_manager(self.artifact_root.parent, attempts, attempt_payloads, facts, parsed_payloads, transitions,
                        workflow_version=version_tuple.get("workflow_definition_version")))
                from .final_report_oracle import audit_final_reports
                issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in
                    audit_final_reports(self.artifact_root.parent, run_id, attempts, attempt_payloads, facts, parsed_payloads, transitions,
                        workflow_version=version_tuple.get("workflow_definition_version")))
                from .interaction_oracle import audit_interactions
                issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in
                    audit_interactions(self.artifact_root.parent, attempts, attempt_payloads, facts, parsed_payloads, transitions,
                        workflow_version=version_tuple.get("workflow_definition_version")))
            from .batch_oracle import shared_goal_stop
            try:
                success_stop = shared_goal_stop(facts, parsed_payloads, transitions, version_tuple.get("workflow_definition_version"))
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(OracleIssue("verdict_shared_goal_mismatch", str(exc)))
                success_stop = None
            _audit_participant_controls(version_tuple.get("workflow_definition_version"), commands, transitions,
                attempts, attempt_payloads, issues, roles=_goal_roles_by_run(facts, parsed_payloads).get(run_id, ()), success_stop=success_stop)

            outcomes = [row for row in facts if row["fact_type"] == "CanonicalOutcomeAccepted"]
            canonical_outcome_count = len(outcomes)
            _audit_unique_outcomes(outcomes, issues)
            artifacts = list(
                connection.execute(
                    "SELECT * FROM execution_artifacts WHERE run_id=? ORDER BY artifact_id",
                    (run_id,),
                )
            )
            _audit_execution_integrity(
                self.artifact_root,
                facts,
                parsed_payloads,
                transitions,
                transition_payloads,
                commands,
                attempts,
                attempt_payloads,
                artifacts,
                issues,
                research_contract_version=version_tuple.get("research_contract_version"),
            )
            _audit_owned_workspace_residue(self.artifact_root, attempts, issues)
            artifact_counts = _audit_artifacts(
                self.artifact_root,
                run_id,
                artifacts,
                outcomes,
                attempts,
                issues,
            )
            from .session_oracle import audit_sessions
            session_issues, transcript_count = audit_sessions(self.artifact_root, attempts, attempt_payloads, _raw_artifact_digest)
            issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in session_issues)
            _audit_grounding_sources(self.artifact_root, attempts, attempt_payloads, issues)
            from .shared_acceptance_oracle import audit_shared_bindings
            issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in
                audit_shared_bindings(self.artifact_root, run_id, facts, parsed_payloads, attempts, attempt_payloads))
            from .grounding_dialogue_oracle import audit_grounding_dialogue
            issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in
                audit_grounding_dialogue(run_id, attempts, attempt_payloads, decisions, transitions, commands,
                    run_state=run_state, aci_version=version_tuple.get("agent_aci_version")))
            from .expert_context_oracle import audit_expert_context
            issues.extend(OracleIssue(code, message, identifiers) for code, message, identifiers in
                audit_expert_context(self.artifact_root.parent, attempts, attempt_payloads, facts, parsed_payloads,
                    batch_transitions=transitions, commands=commands))
            if transcript_count: artifact_counts["TRANSCRIPT"] = transcript_count
            _audit_lowest_attempt(outcomes, artifacts, attempts, issues)
            _audit_component_scientific_closure(
                self.artifact_root,
                run_state,
                version_tuple.get("research_contract_version"),
                version_tuple.get("evaluator_version"),
                version_tuple.get("agent_aci_version"),
                facts,
                parsed_payloads,
                refs,
                transitions,
                transition_payloads,
                experiment_heads,
                artifacts,
                attempts,
                attempt_payloads,
                outcomes,
                issues,
                commands=commands,
                workflow_version=version_tuple.get("workflow_definition_version"),
            )

            if run_state == "COMPLETED":
                _audit_completed_chain(
                    version_tuple.get("research_contract_version"),
                    facts,
                    issues,
                )
            if run_state in _TERMINAL_RUN_STATES:
                if nonterminal_experiment_count:
                    issues.append(
                        OracleIssue(
                            "terminal_run_nonterminal_experiment",
                            f"terminal run owns {nonterminal_experiment_count} nonterminal experiments",
                        )
                    )
                if open_decision_count:
                    issues.append(OracleIssue("terminal_run_open_decision", "terminal run has open decisions"))
                if open_attempt_count:
                    issues.append(OracleIssue("terminal_run_open_attempt", "terminal run has OPEN attempts"))
                if pending_effect_count:
                    issues.append(OracleIssue("terminal_run_pending_effect", "terminal run has pending commands"))
                if owned_process_residue:
                    issues.append(
                        OracleIssue(
                            "terminal_run_process_residue",
                            "owned process groups are still observable",
                            tuple(str(value) for value in owned_process_residue),
                        )
                    )
            return OracleReport(
                run_id=run_id,
                version_tuple=version_tuple,
                fact_types=fact_types,
                causal_chain=causal_chain,
                workflow_state_counts=workflow_state_counts,
                attempt_state_counts=attempt_state_counts,
                attempt_terminal_reason_counts=attempt_terminal_reason_counts,
                artifact_counts=artifact_counts,
                canonical_outcome_count=canonical_outcome_count,
                open_attempt_count=open_attempt_count,
                open_decision_count=open_decision_count,
                pending_effect_count=pending_effect_count,
                nonterminal_experiment_count=nonterminal_experiment_count,
                duplicate_effect_count=duplicate_effect_count,
                conflicting_identity_count=conflicting_identity_count,
                owned_process_residue=owned_process_residue,
                issues=tuple(issues),
            )
        except sqlite3.Error as exc:
            issues.append(OracleIssue("database_query_failed", str(exc)))
            return _empty_report(run_id, *issues)
        finally:
            connection.close()


IndependentOracle = Oracle


def audit_all_runs(state_root: str | os.PathLike[str]) -> tuple[OracleReport, ...]:
    """Audit every durable run below one copied or offline state root."""
    root = Path(state_root)
    database = root / "state.sqlite"
    try:
        connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            connection.execute("PRAGMA query_only=ON")
            run_ids = tuple(str(row[0]) for row in connection.execute("SELECT run_id FROM workflow_runs ORDER BY run_id"))
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"cannot enumerate workflow runs for Oracle audit: {exc}") from exc
    reports = tuple(Oracle(database, root / "artifacts").audit(run_id) for run_id in run_ids)
    failed = [report.run_id for report in reports if not report.ok]
    if failed:
        raise RuntimeError(f"Oracle audit failed for workflow runs: {', '.join(failed)}")
    return reports


def audit_gpu_pool(pool_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Audit raw pool commitments, not scientific closure or live GPU ownership.

    Deliberately does not import the pool writer, sampler, or scheduling reducer.
    A consistent active commitment is legal; callers must inspect state counts
    rather than treating ``ok`` as an assertion that all work is closed.
    """
    issues: list[dict[str, Any]] = []
    counts: dict[str, int] = {state: 0 for state in (
        "QUEUED", "RESERVED", "LAUNCHING", "BOUND", "RELEASED")}

    def issue(code: str, seq: int | None = None) -> None:
        issues.append({"code": code, **({"request_seq": seq} if seq is not None else {})})

    def decode(value: Any) -> Any:
        return json.loads(value, object_pairs_hook=_unique_json_object,
                          parse_constant=_reject_json_constant)

    def integer(value: Any, minimum: int = 0) -> bool:
        return type(value) is int and minimum <= value <= 9_007_199_254_740_991

    def devices(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(uuid, str) and re.fullmatch(
            r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", uuid)
            for uuid in value) and value == sorted(set(value))

    database = Path(pool_root) / "pool.sqlite3"
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if connection.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
                issue("gpu_pool_sqlite_inconsistent")
            rows = [dict(row) for row in connection.execute("SELECT * FROM requests ORDER BY request_seq")]
            runs = {tuple(row) for row in connection.execute("SELECT uid,namespace,run_id FROM runs")}
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError):
        issue("gpu_pool_unreadable")
        return {"scope": "gpu_pool_capacity", "ok": False, "issues": issues, "counts": counts}
    counts["requests"] = len(rows)
    keyed: set[tuple[Any, ...]] = set()
    parsed: dict[int, tuple[dict[str, Any], dict[str, Any], list[str]]] = {}
    for row in rows:
        seq = row["request_seq"]
        identity = tuple(row[name] for name in ("uid", "namespace", "run_id", "execution_id", "attempt_id"))
        if identity in keyed:
            issue("gpu_pool_duplicate_identity", seq)
        keyed.add(identity)
        if not integer(identity[0]) or not all(_trimmed_text(value) for value in identity[1:]):
            issue("gpu_pool_identity_invalid", seq)
        if identity[:3] not in runs:
            issue("gpu_pool_run_missing", seq)
        state = row["state"]
        if state not in counts or state == "requests":
            issue("gpu_pool_state_invalid", seq)
        else:
            counts[state] += 1
        pgid = row["process_group_id"]
        if ((state == "BOUND" and not integer(pgid, 2))
                or (pgid is not None and not integer(pgid, 2))
                or (state in {"QUEUED", "RESERVED", "LAUNCHING"} and pgid is not None)):
            issue("gpu_pool_process_binding_invalid", seq)
        try:
            intent = decode(row["intent_json"])
            if (not isinstance(intent, dict) or set(intent) - {"sharing"} != {
                    "allowed_gpu_uuids", "gpu_count", "workload_memory_mib", "reserve_memory_mib"}
                    or ("sharing" in intent and intent["sharing"] not in ("exclusive", "shared"))
                    or not devices(intent["allowed_gpu_uuids"])
                    or not integer(intent["gpu_count"], 1)
                    or intent["gpu_count"] > len(intent["allowed_gpu_uuids"])
                    or not integer(intent["workload_memory_mib"], 1)
                    or not integer(intent["reserve_memory_mib"])):
                raise ValueError("invalid intent")
        except (TypeError, ValueError, KeyError):
            issue("gpu_pool_intent_invalid", seq)
            continue
        try:
            grant = decode(row["grant_json"])
            if (not devices(grant) or not set(grant) <= set(intent["allowed_gpu_uuids"])
                    or (state == "QUEUED" and grant)
                    or (state in {"RESERVED", "LAUNCHING", "BOUND"} and len(grant) != intent["gpu_count"])
                    or (grant and len(grant) != intent["gpu_count"])):
                raise ValueError("invalid grant")
        except (TypeError, ValueError):
            issue("gpu_pool_grant_invalid", seq)
            continue
        parsed[seq] = row, intent, grant
    for seq, (row, intent, grant) in parsed.items():
        if not grant:
            if row["decision_json"] is not None:
                issue("gpu_pool_decision_invalid", seq)
            continue
        try:
            decision = decode(row["decision_json"])
            if not isinstance(decision, dict) or decision.get("phase") not in {"reserve", "arm"}:
                raise ValueError("invalid decision")
            if row["state"] in {"LAUNCHING", "BOUND"} and decision["phase"] != "arm":
                raise ValueError("missing arm")
            samples = decision["devices"]
            if (not isinstance(samples, list) or len(samples) != len(grant)
                    or sorted(item["gpu_uuid"] for item in samples) != grant):
                raise ValueError("incomplete device decision")
            for sample in samples:
                for field in ("total_memory_mib", "free_memory_mib", "used_memory_mib",
                              "workload_memory_mib", "reserve_memory_mib", "outstanding_memory_mib"):
                    if not integer(sample[field]):
                        raise ValueError("invalid capacity scalar")
                if (sample["total_memory_mib"] < 1
                        or sample["total_memory_mib"] != sample["free_memory_mib"] + sample["used_memory_mib"]
                        or sample["workload_memory_mib"] != intent["workload_memory_mib"]
                        or sample["reserve_memory_mib"] < intent["reserve_memory_mib"]):
                    raise ValueError("inconsistent device decision")
                outstanding = 0
                resident_total = 0
                seen: set[int] = set()
                commitments = sample["commitments"]
                if not isinstance(commitments, list):
                    raise ValueError("invalid commitments")
                for commitment in commitments:
                    ref = commitment["request_seq"]
                    if not integer(ref, 1) or ref == seq or ref in seen or ref not in parsed:
                        raise ValueError("invalid commitment reference")
                    seen.add(ref)
                    _, referenced, other_grant = parsed[ref]
                    if intent.get("sharing") == "exclusive" or referenced.get("sharing") == "exclusive":
                        issue("gpu_pool_sharing_conflict", seq)
                    budget = commitment["workload_memory_mib"]
                    resident = commitment["resident_memory_mib"]
                    if (not integer(budget, 1) or budget != referenced["workload_memory_mib"]
                            or sample["gpu_uuid"] not in other_grant
                            or commitment["state"] not in {"RESERVED", "LAUNCHING", "BOUND"}
                            or (resident is not None and (not integer(resident)
                                or commitment["state"] != "BOUND"))
                            or sample["reserve_memory_mib"] < referenced["reserve_memory_mib"]):
                        raise ValueError("invalid commitment sample")
                    outstanding += max(0, budget - (0 if resident is None else resident))
                    resident_total += 0 if resident is None else resident
                if resident_total > sample["used_memory_mib"]:
                    raise ValueError("incoherent ownership sample")
                if outstanding != sample["outstanding_memory_mib"]:
                    issue("gpu_pool_accounting_mismatch", seq)
                if sample["free_memory_mib"] - outstanding < sample["workload_memory_mib"] + sample["reserve_memory_mib"]:
                    issue("gpu_pool_capacity_exceeded", seq)
        except (TypeError, ValueError, KeyError):
            issue("gpu_pool_decision_invalid", seq)
    return {"scope": "gpu_pool_capacity", "ok": not issues, "issues": issues, "counts": counts}


def _audit_references(
    connection: sqlite3.Connection,
    run_id: str,
    refs: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
) -> None:
    for ref in refs:
        target = connection.execute(
            "SELECT run_id FROM research_facts WHERE fact_id=?",
            (ref["ref_fact_id"],),
        ).fetchone()
        if target is None:
            issues.append(
                OracleIssue("missing_fact_ref", "fact reference target is absent", (str(ref["ref_fact_id"]),))
            )
        elif target["run_id"] != run_id:
            issues.append(
                OracleIssue(
                    "cross_run_fact_ref",
                    "fact reference crosses run boundary",
                    (str(ref["fact_id"]), str(ref["ref_fact_id"])),
                )
            )


def _audit_fact_chain(
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
) -> dict[str, tuple[str, ...]]:
    by_type = {kind: [row for row in facts if row["fact_type"] == kind] for kind in _FACT_ORDER}
    goals = {str(row["goal_id"]) for row in by_type["GoalDefined"]}
    hypotheses = {str(row["hypothesis_id"]) for row in by_type["HypothesisProposed"]}
    experiments = {
        (str(row["experiment_id"]), str(row["execution_id"]))
        for row in by_type["ExperimentPreregistered"]
    }
    outcomes = {str(row["outcome_id"]) for row in by_type["CanonicalOutcomeAccepted"]}
    artifacts = {str(row["artifact_id"]) for row in by_type["CanonicalOutcomeAccepted"]}
    evidences = {str(row["subject_id"]) for row in by_type["EvidenceLinked"]}
    assessments = {str(row["subject_id"]) for row in by_type["HypothesisAssessed"]}
    for row in by_type["HypothesisProposed"]:
        if str(row["goal_id"]) not in goals:
            issues.append(OracleIssue("missing_goal_edge", "hypothesis cites unknown goal", (str(row["fact_id"]),)))
    for row in by_type["ExperimentPreregistered"]:
        if str(row["hypothesis_id"]) not in hypotheses:
            issues.append(
                OracleIssue("missing_hypothesis_edge", "experiment cites unknown hypothesis", (str(row["fact_id"]),))
            )
    for row in by_type["CanonicalOutcomeAccepted"]:
        key = (str(row["experiment_id"]), str(row["execution_id"]))
        if key not in experiments:
            issues.append(
                OracleIssue("missing_experiment_edge", "outcome cites unknown experiment/execution", (str(row["fact_id"]),))
            )
    for row in by_type["EvidenceLinked"]:
        if str(row["hypothesis_id"]) not in hypotheses or str(row["outcome_id"]) not in outcomes:
            issues.append(OracleIssue("missing_evidence_edge", "evidence has an unresolved science edge", (str(row["fact_id"]),)))
        if str(row["artifact_id"]) not in artifacts:
            issues.append(OracleIssue("missing_evidence_artifact", "evidence cites an unaccepted artifact", (str(row["fact_id"]),)))
    for row in by_type["HypothesisAssessed"]:
        if str(row["hypothesis_id"]) not in hypotheses:
            issues.append(OracleIssue("missing_assessment_hypothesis", "assessment cites unknown hypothesis", (str(row["fact_id"]),)))
        payload = payloads.get(str(row["fact_id"]), {})
        for evidence_id in payload.get("evidence_ids", []):
            if str(evidence_id) not in evidences:
                issues.append(OracleIssue("missing_assessment_evidence", "assessment cites unknown evidence", (str(evidence_id),)))
    for row in by_type["GoalVerdictRecorded"]:
        if str(row["goal_id"]) not in goals:
            issues.append(OracleIssue("missing_verdict_goal", "verdict cites unknown goal", (str(row["fact_id"]),)))
        payload = payloads.get(str(row["fact_id"]), {})
        for assessment_id in payload.get("assessment_ids", []):
            if str(assessment_id) not in assessments:
                issues.append(OracleIssue("missing_verdict_assessment", "verdict cites unknown assessment", (str(assessment_id),)))
    return {
        kind: tuple(str(row["subject_id"]) for row in by_type[kind])
        for kind in _FACT_ORDER
    }


def _goal_roles_by_run(
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """Read participant roles from GoalDefined without validating the contract."""

    result: dict[str, tuple[str, ...]] = {}
    for fact in facts:
        if fact["fact_type"] != "GoalDefined":
            continue
        payload = payloads.get(str(fact["fact_id"]), {})
        contract = payload.get("research_contract")
        raw_roles = contract.get("roles") if isinstance(contract, Mapping) else None
        if not isinstance(raw_roles, list):
            continue
        roles = tuple(
            str(role["id"])
            for role in raw_roles
            if isinstance(role, Mapping)
            and isinstance(role.get("id"), str)
            and role["id"]
        )
        if len(roles) == len(raw_roles) and len(set(roles)) == len(roles):
            result[str(fact["run_id"])] = roles
    return result


def _goal_component_roles_by_run(
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, str], tuple[str, tuple[str, ...]]]:
    """Read component owner/proposer identities directly from GoalDefined."""

    result: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    for fact in facts:
        if fact["fact_type"] != "GoalDefined":
            continue
        payload = payloads.get(str(fact["fact_id"]), {})
        contract = payload.get("research_contract")
        raw_components = (
            contract.get("components") if isinstance(contract, Mapping) else None
        )
        if not isinstance(raw_components, list):
            continue
        for component in raw_components:
            if not isinstance(component, Mapping):
                continue
            component_id = component.get("id")
            owner = component.get("owner")
            raw_proposers = component.get("proposers")
            if (
                not _trimmed_text(component_id)
                or not _trimmed_text(owner)
                or not isinstance(raw_proposers, list)
                or any(not _trimmed_text(role) for role in raw_proposers)
            ):
                continue
            proposers = tuple(str(role) for role in raw_proposers)
            result[(str(fact["run_id"]), str(component_id))] = (
                str(owner),
                proposers,
            )
    return result


def _audit_completed_chain(
    research_contract_version: str | None,
    facts: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
) -> None:
    counts = _counts(str(row["fact_type"]) for row in facts)
    missing = tuple(kind for kind in _FACT_ORDER if counts.get(kind, 0) == 0)
    if research_contract_version in _COMPONENT_FACT_SCHEMA_BY_RESEARCH_VERSION:
        # A component run may honestly settle INCONCLUSIVE before every
        # component produces the middle five fact families.  Goal and verdict
        # are the only universally required completed-run facts; component
        # coverage and lineage are audited independently below.
        missing = tuple(
            kind
            for kind in missing
            if kind in {"GoalDefined", "GoalVerdictRecorded"}
        )
    if missing:
        issues.append(OracleIssue("completed_run_incomplete_chain", "completed run lacks fact families", missing))
    if counts.get("GoalDefined", 0) != 1:
        issues.append(OracleIssue("goal_count", "completed Phase 2 run must have exactly one GoalDefined"))
    if counts.get("GoalVerdictRecorded", 0) != 1:
        issues.append(OracleIssue("verdict_count", "completed run must have exactly one GoalVerdictRecorded"))


def _audit_unique_outcomes(outcomes: Sequence[sqlite3.Row], issues: list[OracleIssue]) -> None:
    groups: dict[tuple[str, str], list[str]] = {}
    for row in outcomes:
        key = (str(row["experiment_id"]), str(row["execution_id"]))
        groups.setdefault(key, []).append(str(row["fact_id"]))
    for key, fact_ids in groups.items():
        if len(fact_ids) > 1:
            issues.append(
                OracleIssue(
                    "duplicate_canonical_outcome",
                    f"experiment/execution {key!r} has {len(fact_ids)} accepted outcomes",
                    tuple(fact_ids),
                )
            )


def _audit_participant_controls(version: str | None, commands: Sequence[Mapping[str, Any]],
        transitions: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
        payloads: Mapping[str, Mapping[str, Any]], issues: list[OracleIssue], *, roles: Sequence[str] = (), success_stop=None) -> None:
    from .participant_controls import ParticipantScope, supports_participant_controls
    # Reconcile observed command/transition/input relations, not the Host's fold.
    kinds = {"pause": "PAUSE_PARTICIPANT", "resume": "RESUME_PARTICIPANT", "inject": "INJECT_NOTE"}
    by_id = {str(row["command_id"]): row for row in commands}
    controls = []; intents = {}
    for row in transitions:
        identity = str(row["command_id"])
        intents.setdefault(identity, row)
        data = json.loads(row["payload_json"]); control = data.get("participant_control")
        command = by_id.get(identity)
        if control is None and (command is None or command["command_type"] not in kinds.values()): continue
        try:
            scope = ParticipantScope.from_dict(control["scope"])
            action = control["action"]
            request = json.loads(command["request_json"])
            assert supports_participant_controls(version)
            assert row["workflow_kind"] == "RUN" and row["workflow_id"] == row["run_id"]
            assert command["workflow_id"] == command["run_id"] == row["run_id"]
            assert scope.role is None or scope.role in roles
            aborted = data.get("expert_interaction", {}).get("action") in {"leader_aborted", "debate_aborted"}
            assert row["state"] == row["previous_state"] and (row["state"] in {"CREATED", "ACTIVE"}
                or row["state"] == "STOPPING" and success_stop is not None and aborted and action == "resume")
            if aborted:
                assert success_stop is not None and row["revision"] > success_stop[0]["revision"]
                assert data["expert_interaction"]["goal_verdict_id"] == success_stop[1]["verdict_id"]
            assert command["command_type"] == kinds[action] and command["response_status"] == "accepted"
            if control["reason"] == "operator":
                assert request["command"] == action.upper() and request["payload"] == {"run_id": row["run_id"],
                    "scope": control["scope"], "reason": control["reason"], **({"text": control["text"]} if action == "inject" else {})}
            else:
                event = data.get("expert_interaction", {})
                source = data.get("interaction_source_attempt_id", event.get("source_attempt_id"))
                assert isinstance(source, str) and action in {"pause", "resume"}
                owner = next(item for item in attempts if item["attempt_id"] == source)
                original = payloads[source]["request"]
                assert owner["run_id"] == original["run_id"] == row["run_id"] and owner["state"] == "CLOSED" and owner["terminal_reason"] == "SUCCEEDED"
                assert original["schema_version"] >= 14 and original["attempt_id"] == source
                if control["reason"] == "leader:" + source:
                    assert original["command"] == "OBSERVER"
                    assert any((selected := json.loads(item["payload_json"]).get("expert_interaction", {})).get("action") == "leader_selected"
                        and selected.get("source_attempt_id") == source for item in transitions)
                    assert action == "pause" or event["action"] in {"leader_round_finished", "leader_not_started", "leader_aborted"}
                else:
                    debate_id = data.get("debate_id", event.get("debate_id"))
                    assert isinstance(debate_id, str) and control["reason"] == "debate:" + debate_id
                    proposed = next(json.loads(item["payload_json"])["expert_interaction"] for item in transitions
                        if (selected := json.loads(item["payload_json"]).get("expert_interaction", {})).get("action") == "debate_proposed"
                        and selected.get("debate_id") == debate_id)
                    if action == "pause" or event.get("action") in {"debate_failed", "debate_aborted"}:
                        assert source == proposed["source_attempt_id"] and original["command"] == "OBSERVER"
                    else:
                        assert event["action"] == "debate_verdict" and original["command"] == "DEBATE_JUDGE"
                        assert event["parent_observer_attempt_id"] == proposed["source_attempt_id"]
                assert (request == {"command": "PAUSE_PARTICIPANT", "workflow_id": row["run_id"]} if action == "pause" else
                    request == data)
            assert command["accepted_revision"] == row["revision"]
            assert sum(other["command_id"] == identity for other in transitions) == 1
            if action == "inject": assert isinstance(control["text"], str) and control["text"].strip()
            controls.append((row, control, scope))
        except (AssertionError, KeyError, TypeError, ValueError, StopIteration):
            issues.append(OracleIssue("participant_control_binding_mismatch", "control differs from its accepted owner command or lifecycle", (identity,)))
    notes = {str(row["command_id"]): (row, control, scope) for row, control, scope in controls if control["action"] == "inject"}
    quanta = []
    for row in attempts:
        if row["kind"] != "AGENT": continue
        request = payloads.get(str(row["attempt_id"]), {}).get("request", {})
        if not isinstance(request, Mapping): continue
        task = request.get("task_context", {})
        if not isinstance(task, Mapping): continue
        key = task.get("key", {})
        if not isinstance(key, Mapping): continue
        if task.get("kind") != "RESEARCH_NODE": continue
        chain = key.get("chain") or ("1" if request.get("command") in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"} else None)
        try: scope = ParticipantScope(request.get("role"), request.get("component_id"), chain)
        except ValueError: continue
        intent = intents.get(str(row["command_id"]))
        if intent is not None: quanta.append((row, request, key, scope, intent))
    bound = {}; originals = {}
    for row, request, key, scope, intent in quanta:
        current_notes = {}
        for item in request.get("input_artifacts", ()):
            if not isinstance(item, Mapping): continue
            try: decoded = json.loads(base64.b64decode(item["content_base64"], validate=True)) if item.get("media_type") == "application/json" else None
            except (KeyError, ValueError, TypeError): continue
            if item["ref"] not in notes and not (isinstance(decoded, Mapping) and "operator_instruction" in decoded): continue
            try:
                source, control, target = notes[item["ref"]]
                assert target.contains(scope) and source["raw_order"] < intent["raw_order"]
                assert item["ref"] in request["immutable_context_refs"]
                assert decoded == {"source_command_id": source["command_id"], "scope": control["scope"], "operator_instruction": control["text"]}
                owner = (request["role"], item["ref"])
                assert owner not in bound or bound[owner] == row["command_id"]
                bound[owner] = row["command_id"]; current_notes[item["ref"]] = item
            except (AssertionError, KeyError, TypeError):
                issues.append(OracleIssue("operator_note_binding_mismatch", "note source, scope, bytes, timing or one-task ownership differs", (str(row["attempt_id"]), str(item["ref"]))))
        identity = str(row["command_id"])
        if identity in originals and originals[identity] != current_notes:
            issues.append(OracleIssue("operator_note_retry_mismatch", "retry changed its logical node's frozen operator notes", (str(row["attempt_id"]),)))
        originals.setdefault(identity, current_notes)
    for identity, (source, _, target) in notes.items():
        first = {}
        for row, request, _, scope, intent in sorted(quanta, key=lambda item: (item[4]["raw_order"], item[0]["attempt_ordinal"])):
            if target.contains(scope) and source["raw_order"] < intent["raw_order"]: first.setdefault(request["role"], row["command_id"])
        for role, command_id in first.items():
            if bound.get((role, identity)) != command_id:
                issues.append(OracleIssue("operator_note_not_bound", "next eligible node did not freeze its operator note", (identity, str(command_id))))
    dispatches = []
    for row, request, key, scope, intent in quanta:
        if row["attempt_ordinal"] != 1 or request.get("command") not in {"EXPERIMENT_DESIGN_IMPLEMENT", "LITERATURE_REVIEW"}: continue
        if request["command"] == "LITERATURE_REVIEW":
            prior = [other for other in quanta if other[1].get("command") == "LITERATURE_REVIEW"
                and other[1].get("role") == request.get("role") and other[1].get("component_id") == request.get("component_id")
                and other[2].get("cycle") == key.get("cycle") and other[2].get("chain") == key.get("chain")]
            if intent["raw_order"] != min(other[4]["raw_order"] for other in prior): continue
        dispatches.append((intent, scope))
    for row in transitions:
        if row["workflow_kind"] == "EXPERIMENT" and row["revision"] == 1:
            data = json.loads(row["payload_json"])
            try: scope = ParticipantScope(data.get("role"), data.get("component_id"), data.get("chain") or "1")
            except ValueError: continue
            dispatches.append((row, scope))
    for intent, participant in dispatches:
        for paused, control, scope in controls:
            if control["action"] != "pause" or paused["raw_order"] >= intent["raw_order"] or not scope.contains(participant): continue
            target = ParticipantScope(participant.role) if scope.role is None else scope
            cleared = any(event["raw_order"] > paused["raw_order"] and event["raw_order"] < intent["raw_order"]
                and following["action"] == "resume" and following["reason"] == control["reason"] and resumed.contains(target)
                for event, following, resumed in controls)
            if not cleared:
                issues.append(OracleIssue("participant_paused_dispatch", "new exploration crossed an uncleared applicable pause", (str(paused["command_id"]), str(intent["command_id"])))); break


def _audit_grounding_sources(artifact_root: Path, attempts: Sequence[Mapping[str, Any]],
                             payloads: Mapping[str, Mapping[str, Any]], issues: list[OracleIssue]) -> None:
    """Inspect existing web observations/CC history without calling the Host."""
    from .grounding_background import GroundingBackground, retrieved_web_urls, transcript_web_results, validate_web_sources
    selected = []
    for row in attempts:
        identity = str(row["attempt_id"]); observation = payloads.get(identity, {}); request = observation.get("request", {})
        task = request.get("task_context", {}) if isinstance(request, Mapping) else {}
        literature = (isinstance(request, Mapping) and request.get("command") == "LITERATURE_REVIEW"
            and supports_literature_web(request.get("schema_version")) and isinstance(task, Mapping)
            and task.get("kind") == "RESEARCH_NODE" and task.get("key", {}).get("node_kind") == "LITERATURE_REVIEW")
        debate = (isinstance(request, Mapping) and request.get("command") == "DEBATE"
            and type(request.get("schema_version")) is int and request["schema_version"] >= 14
            and isinstance(task, Mapping) and task.get("kind") == "AUXILIARY" and task.get("request_kind") == "DEBATE")
        eligible = (row["kind"] == "AGENT" and isinstance(request, Mapping)
            and (request.get("command") == "GROUNDING" and supports_grounding_web(request.get("schema_version")) or literature or debate))
        allowed_tools = {"web_search", "web_fetch"}
        if literature or debate:
            try:
                parsed = AgentRequest.from_dict(request)
                configs = [json.loads(item.content_bytes()) for item in parsed.input_artifacts if item.ref.endswith(("--expert-config", "--grounding-config"))]
                settings = configs[0].get("node_runtime", {}).get("literature", {}) if len(configs) == 1 else {}
                allowed_tools = set(settings.get("tools", allowed_tools)) if settings.get("enabled", True) else set()
            except (AgentACIError, ValueError, TypeError): allowed_tools = set()
        records = observation.get("web_tool_results", [])
        if records and (not eligible or not isinstance(records, list) or any(not isinstance(record, Mapping)
                or record.get("tool") not in allowed_tools or record.get("status") not in {"success", "error"}
                or not isinstance(record.get("arguments"), Mapping) for record in records)):
            issues.append(OracleIssue("grounding_tool_scope_invalid", "web observation is outside its invocation or malformed", (identity,)))
            records = []
        if not eligible or row["state"] != "CLOSED": continue
        urls = retrieved_web_urls(records)
        session = observation.get("session", {}); publication = session.get("transcript") if isinstance(session, Mapping) else None
        if isinstance(publication, Mapping):
            try:
                relative = f"{request['run_id']}/agent/{identity}/transcript"
                if publication.get("path") != relative: raise ValueError("Grounding archive owner differs")
                directory = artifact_root / relative
                if _raw_artifact_digest(directory) != publication.get("digest"): raise ValueError("Grounding archive digest differs")
                transcript = directory / "transcript.jsonl"
                # The independent session audit checks the archive inventory and
                # its frozen profile byte limit before this context-only read.
                observed = retrieved_web_urls(transcript_web_results(transcript.read_bytes(), session_id=session.get("session_id")))
                if not urls <= observed: raise ValueError("CLOSED web metadata is absent from actual native tool results")
                urls.update(observed)
            except (OSError, ValueError, TypeError) as exc:
                issues.append(OracleIssue("grounding_transcript_source_invalid", str(exc), (identity,)))
        selected.append((row, request, observation, urls))
    for row, request, observation, _ in selected:
        if row["terminal_reason"] != "SUCCEEDED": continue
        try:
            raw = observation["response"]["typed_candidate"]
            if request.get("command") == "GROUNDING":
                question = request.get("schema_version", 0) >= 15 and raw.get("clarification") is not None
                if not question:
                    GroundingBackground.from_dict(raw["background"])
                elif raw.get("background") is not None:
                    raise ValueError("Grounding question cannot contain completed background")
            eligible_urls = set().union(*(urls for source, previous, _, urls in selected
                if source["workflow_id"] == row["workflow_id"] and source["command_id"] == row["command_id"]
                and source["attempt_ordinal"] <= row["attempt_ordinal"] and previous.get("run_id") == request.get("run_id")
                and previous.get("role") == request.get("role")))
            validate_web_sources(raw.get("web_sources", []), eligible_urls)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(OracleIssue("grounding_sources_unobserved", str(exc), (str(row["attempt_id"]),)))


def _audit_execution_handoffs(attempts, payloads, transitions, transition_payloads, issues):
    """Check existing submission identities and lifecycle timestamps independently."""
    def stamp(row):
        return datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")).timestamp()

    submitted = []
    terminals = {}
    for row in transitions:
        if row["workflow_kind"] != "EXPERIMENT":
            continue
        if row["revision"] == 1:
            submitted.append((row, transition_payloads.get(_row_identifier(row), {})))
        if row["state"] == "TERMINAL":
            terminals[str(row["workflow_id"])] = stamp(row)
    handed = {}
    for row in sorted(attempts, key=lambda item: (str(item["command_id"]), int(item["attempt_ordinal"]))):
        identity = str(row["attempt_id"])
        observed = payloads.get(identity, {})
        request = observed.get("request", {})
        if request.get("schema_version", 0) < 22 or request.get("command") != "EXPERIMENT_EXECUTION":
            continue
        try:
            prior = handed.get(str(row["command_id"]))
            if prior is not None and float(row["started_at"]) < terminals.get(prior, float("inf")):
                raise ValueError("execution resumed before its handed-off experiment became terminal")
            if row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED":
                continue
            own = [(created, payload) for created, payload in submitted
                   if created["run_id"] == request["run_id"]
                   and payload.get("execution_node_command_id") == request["command_id"]
                   and stamp(created) <= float(row["ended_at"])]
            if not own:
                raise ValueError("execution response lacks a prior owned submission")
            latest = max(own, key=lambda item: int(item[1].get("experiment_ordinal", 1)))[0]
            experiment = str(latest["workflow_id"])
            waiting = observed.get("response", {}).get("typed_candidate", {}).get("waiting_for_experiment_id")
            if waiting is not None:
                if waiting != experiment or prior == waiting:
                    raise ValueError("execution handoff is duplicate or differs from its latest owned submission")
                handed[str(row["command_id"])] = waiting
            elif float(row["ended_at"]) < terminals.get(experiment, float("inf")):
                raise ValueError("execution completed before its latest submitted experiment became terminal")
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(OracleIssue("execution_handoff_invalid", str(exc), (identity,)))


def _audit_agent_attempts(
    research_contract_version: str | None,
    agent_aci_version: str | None,
    facts: Sequence[sqlite3.Row],
    fact_payloads: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    commands: Sequence[sqlite3.Row],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
) -> None:
    """Validate every durable Agent quantum and its accepted scientific effect."""

    commands_by_id = {str(row["command_id"]): row for row in commands}
    run_roles = _goal_roles_by_run(facts, fact_payloads)
    component_roles = _goal_component_roles_by_run(facts, fact_payloads)
    expected_schema = (
        int(agent_aci_version)
        if isinstance(agent_aci_version, str) and agent_aci_version.isdigit() and int(agent_aci_version) >= 5
        else None
    )
    frozen_modes: dict[str, str | None] = {}
    for fact in facts:
        if fact["fact_type"] != "GoalDefined":
            continue
        run_id = str(fact["run_id"])
        payload = fact_payloads.get(str(fact["fact_id"]), {})
        contract = payload.get("research_contract")
        budget = contract.get("budget") if isinstance(contract, Mapping) else None
        invocation = (
            budget.get("agent_invocation") if isinstance(budget, Mapping) else None
        )
        mode = (
            "enforce"
            if agent_aci_version == "5"
            else invocation.get("token_enforcement_mode")
            if isinstance(invocation, Mapping)
            else None
        )
        frozen_modes[run_id] = mode if mode in {"enforce", "observe"} else None
    agent_attempts = [row for row in attempts if row["kind"] == "AGENT"]
    attempts_by_id = {str(row["attempt_id"]): row for row in agent_attempts}
    successful: dict[str, tuple[AgentRequest, AgentResponse]] = {}

    ordinal_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in agent_attempts:
        ordinal_groups.setdefault(
            (str(row["workflow_id"]), str(row["command_id"])), []
        ).append(row)
    for rows in ordinal_groups.values():
        ordered = sorted(rows, key=lambda row: int(row["attempt_ordinal"]))
        for expected, row in enumerate(ordered, 1):
            if int(row["attempt_ordinal"]) != expected:
                issues.append(
                    OracleIssue(
                        "agent_attempt_ordinal_mismatch",
                        "Agent attempt ordinals are not contiguous for their command owner",
                        (str(row["attempt_id"]), str(row["attempt_ordinal"]), str(expected)),
                    )
                )

    for attempt in agent_attempts:
        attempt_id = str(attempt["attempt_id"])
        observation = attempt_payloads.get(attempt_id)
        if not isinstance(observation, Mapping):
            issues.append(
                OracleIssue(
                    "agent_terminal_observation_invalid",
                    "Agent attempt lacks a readable observation object",
                    (attempt_id,),
                )
            )
            continue
        raw_request = observation.get("request")
        if not isinstance(raw_request, Mapping):
            issues.append(
                OracleIssue(
                    "agent_terminal_observation_invalid",
                    "Agent observation does not retain its request object",
                    (attempt_id,),
                )
            )
            continue
        try:
            request = AgentRequest.from_dict(raw_request)
        except (AgentACIError, TypeError, ValueError) as exc:
            issues.append(
                OracleIssue(
                    "agent_request_invalid",
                    f"Agent request fails the typed wire contract: {exc}",
                    (attempt_id,),
                )
            )
            request = None
        if raw_request.get("schema_version") != expected_schema:
            issues.append(
                OracleIssue(
                    "agent_request_schema_version_mismatch",
                    "Agent request schema differs from the run's frozen ACI version",
                    (
                        attempt_id,
                        str(raw_request.get("schema_version")),
                        str(agent_aci_version),
                    ),
                )
            )
        if (
            request is not None
            and request.budget.token_enforcement_mode
            != frozen_modes.get(str(attempt["run_id"]))
        ):
            issues.append(
                OracleIssue(
                    "agent_token_enforcement_mode_mismatch",
                    "Agent request token mode differs from GoalDefined",
                    (
                        attempt_id,
                        request.budget.token_enforcement_mode,
                        str(frozen_modes.get(str(attempt["run_id"]))),
                    ),
                )
            )
        expected_identity = {
            "run_id": attempt["run_id"],
            "owner_workflow_id": attempt["workflow_id"],
            "command_id": attempt["command_id"],
            "attempt_id": attempt["attempt_id"],
            "attempt_ordinal": attempt["attempt_ordinal"],
        }
        if any(raw_request.get(name) != value for name, value in expected_identity.items()):
            issues.append(
                OracleIssue(
                    "agent_request_identity_mismatch",
                    "Agent request identity differs from its durable attempt row",
                    (attempt_id,),
                )
            )

        command = commands_by_id.get(str(attempt["command_id"]))
        expected_command_type = _agent_command_type(raw_request)
        if (
            command is None
            or command["run_id"] != attempt["run_id"]
            or command["workflow_id"] != attempt["workflow_id"]
            or command["command_type"] != expected_command_type
        ):
            issues.append(
                OracleIssue(
                    "agent_command_owner_mismatch",
                    "Agent attempt, request command, and workflow command owner differ",
                    (attempt_id, str(attempt["command_id"])),
                )
            )

        if raw_request.get("command") == "PAPER_READ" and request is not None:
            try:
                from .profile_v4 import request_literature_tools
                assert command is not None and request.schema_version >= 20
                command_input = json.loads(command["request_json"])
                reading = command_input["paper_reading"]
                parent = attempts_by_id[reading["parent_attempt_id"]]
                parent_request = AgentRequest.from_dict(attempt_payloads[parent["attempt_id"]]["request"])
                assert "read_papers" in request_literature_tools(parent_request)
                assert request.owner_workflow_id == request.run_id == parent_request.run_id
                assert all(getattr(request, name) == getattr(parent_request, name)
                           for name in ("role", "component_id", "method_family_id", "required_model_identity"))
                assert request.task_context.to_dict() == command_input["task_context"]
                assert request.task_context.cycle == parent_request.task_context.key.cycle
                assert float(parent["started_at"]) <= float(attempt["started_at"])
                assert parent["ended_at"] is None or float(attempt["started_at"]) <= float(parent["ended_at"])
                assert request.deadline <= parent_request.deadline
                question, = (item for item in request.input_artifacts if item.ref.endswith("--paper-question"))
                context, = (item for item in request.input_artifacts if item.ref.endswith("--paper-context"))
                assert json.loads(question.content_bytes()) == reading
                source, = json.loads(context.content_bytes())["sources"]
                assert source == command_input["source"] and source["source_ref"] == reading["paper_ref"]
                assert source["parent_attempt_id"] == parent_request.attempt_id
            except (AssertionError, KeyError, TypeError, ValueError, AttributeError):
                issues.append(OracleIssue("paper_reading_owner_mismatch",
                    "reader request differs from its owning parent, source/question, model or deadline", (attempt_id,)))

        observed_pgid = observation.get("process_group_id")
        if "process_group_id" in observation and observed_pgid != attempt["process_group_id"]:
            issues.append(
                OracleIssue(
                    "agent_process_group_mismatch",
                    "Agent terminal observation process group differs from its attempt row",
                    (attempt_id, str(attempt["process_group_id"]), str(observed_pgid)),
                )
            )

        if attempt["state"] == "OPEN":
            if "response" in observation:
                issues.append(
                    OracleIssue(
                        "open_agent_response_present",
                        "OPEN Agent attempt carries a response before terminal observation",
                        (attempt_id,),
                    )
                )
            if (
                not {"request"} <= set(observation)
                or not set(observation) <= {"request", "preflight_diagnostics", "session_binding"}
                or "preflight_diagnostics" in observation
                and not isinstance(observation["preflight_diagnostics"], Mapping)
            ):
                issues.append(
                    OracleIssue(
                        "open_agent_observation_invalid",
                        "OPEN Agent observation must contain its committed request and optional diagnostics",
                        (attempt_id,),
                    )
                )
            continue

        if attempt["state"] != "CLOSED" or attempt["terminal_reason"] is None:
            issues.append(
                OracleIssue(
                    "agent_terminal_observation_invalid",
                    "Agent attempt is neither a valid OPEN nor CLOSED terminal row",
                    (attempt_id,),
                )
            )
            continue
        if attempt["terminal_reason"] != "SUCCEEDED":
            if "response" in observation:
                issues.append(
                    OracleIssue(
                        "failed_agent_response_present",
                        "failed, cancelled, timed-out, or LOST Agent attempt carries a response",
                        (attempt_id, str(attempt["terminal_reason"])),
                    )
                )
            rejected_response = observation.get("rejected_response")
            if rejected_response is not None:
                if attempt["terminal_reason"] != "FAILED" or request is None:
                    issues.append(
                        OracleIssue(
                            "rejected_agent_response_ineligible",
                            "only a FAILED Agent attempt may retain one non-authoritative rejected response",
                            (attempt_id, str(attempt["terminal_reason"])),
                        )
                    )
                elif not isinstance(rejected_response, Mapping):
                    issues.append(
                        OracleIssue(
                            "rejected_agent_response_invalid",
                            "rejected Agent response must be a typed response object",
                            (attempt_id,),
                        )
                    )
                else:
                    try:
                        validate_postflight(
                            request, AgentResponse.from_dict(rejected_response)
                        )
                    except (AgentACIError, TypeError, ValueError) as exc:
                        issues.append(
                            OracleIssue(
                                "rejected_agent_response_invalid",
                                f"rejected Agent response fails ACI postflight: {exc}",
                                (attempt_id,),
                            )
                        )
            continue

        raw_response = observation.get("response")
        if (
            isinstance(raw_response, Mapping)
            and raw_response.get("schema_version") != expected_schema
        ):
            issues.append(
                OracleIssue(
                    "agent_response_schema_version_mismatch",
                    "Agent response schema differs from the run's frozen ACI version",
                    (
                        attempt_id,
                        str(raw_response.get("schema_version")),
                        str(agent_aci_version),
                    ),
                )
            )
        if (
            not {"request", "response"} <= set(observation)
            or not set(observation) <= {
                "request", "response", "preflight_diagnostics", "diagnostics",
                "literature_retry_trace", "session_binding", "session", "memory", "web_tool_results", "plugin_update",
            }
            or not isinstance(raw_response, Mapping)
            or "preflight_diagnostics" in observation
            and not isinstance(observation["preflight_diagnostics"], Mapping)
            or "diagnostics" in observation
            and not isinstance(observation["diagnostics"], Mapping)
        ):
            issues.append(
                OracleIssue(
                    "successful_agent_observation_invalid",
                    "successful Agent observation must contain request and response objects plus only supported diagnostics",
                    (attempt_id,),
                )
            )
            continue
        if request is None:
            continue
        try:
            response = AgentResponse.from_dict(raw_response)
            validate_postflight(request, response)
        except (AgentACIError, TypeError, ValueError) as exc:
            issues.append(
                OracleIssue(
                    "agent_postflight_invalid",
                    f"successful Agent exchange fails ACI postflight: {exc}",
                    (attempt_id,),
                )
            )
            continue
        successful[attempt_id] = (request, response)

    _audit_execution_handoffs(agent_attempts, attempt_payloads, transitions, transition_payloads, issues)
    _audit_literature_query_plan_causation(
        agent_attempts, attempt_payloads, successful, issues,
    )
    fact_bindings: dict[str, str] = {}
    candidate_facts = [row for row in facts if row["fact_type"] in _AGENT_FACT_COMMANDS]
    for fact in candidate_facts:
        fact_id = str(fact["fact_id"])
        if (fact["fact_type"] == "ExperimentPreregistered"
                and fact_payloads.get(fact_id, {}).get("execution_contract", {}).get("source_submit_command_id") is not None):
            # Intermediate submit causation is checked against its immutable
            # command below; it is not an Agent terminal-response effect.
            continue
        accepted = [
            transition
            for transition in transitions
            if _row_get(transition, "fact_id") == fact["fact_id"]
        ]
        if len(accepted) != 1:
            issues.append(
                OracleIssue(
                    "accepted_agent_fact_binding_missing",
                    f"candidate fact has {len(accepted)} accepting transitions instead of one",
                    (fact_id,),
                )
            )
            continue
        transition = accepted[0]
        payload = transition_payloads.get(_row_identifier(transition), {})
        attempt_id = payload.get("agent_attempt_id")
        digest = payload.get("agent_output_digest")
        expected_owner = (
            fact["run_id"]
            if fact["fact_type"] == "HypothesisProposed"
            else fact["experiment_id"]
        )
        fact_bindings[fact_id] = str(attempt_id)
        exchange = successful.get(str(attempt_id))
        if exchange is None:
            issues.append(
                OracleIssue(
                    "accepted_agent_fact_attempt_ineligible",
                    "accepted candidate fact does not cite a successful postflight-valid Agent attempt",
                    (fact_id, str(attempt_id)),
                )
            )
            continue
        request, response = exchange
        expected_agent_command = _AGENT_FACT_COMMANDS[str(fact["fact_type"])]
        fact_payload = fact_payloads.get(fact_id, {})
        component_id = fact_payload.get("component_id")
        role_contract = component_roles.get(
            (str(fact["run_id"]), str(component_id))
        )
        expected_role = (
            fact_payload.get("proposer_role")
            if fact["fact_type"] == "HypothesisProposed"
            else None if role_contract is None else role_contract[0]
        )
        if (
            transition["run_id"] != fact["run_id"]
            or transition["workflow_id"] != expected_owner
            or request.owner_workflow_id != expected_owner
            or request.command.value not in expected_agent_command
            or request.component_id != component_id
            or request.role != expected_role
            or payload.get("component_id") != component_id
            or payload.get("role") != expected_role
            or payload.get("method_family_id")
            != fact_payload.get("method_family_id")
            or payload.get("hypothesis_id") != fact["hypothesis_id"]
            or (
                fact["fact_type"] == "HypothesisProposed"
                and (
                    role_contract is None
                    or expected_role not in role_contract[1]
                )
            )
            or (
                fact["fact_type"] == "ExperimentPreregistered"
                and request.method_family_id
                != fact_payload.get("method_family_id")
            )
        ):
            issues.append(
                OracleIssue(
                    "accepted_agent_fact_identity_mismatch",
                    "candidate fact, component/role, accepting transition, and Agent invocation identities differ",
                    (fact_id, str(attempt_id)),
                )
            )
        if digest != response.output_digest:
            issues.append(
                OracleIssue(
                    "accepted_agent_fact_digest_mismatch",
                    "candidate fact acceptance does not bind the exact Agent response digest",
                    (fact_id, str(attempt_id)),
                )
            )
        source_candidate = response.typed_candidate
        if fact["fact_type"] == "HypothesisProposed" and request.schema_version >= 14:
            source_candidate = next((member.get("candidate", {}) for member in response.typed_candidate.get("hypotheses", ())
                if member.get("chain_id") == payload.get("chain")), {})
        if (fact["fact_type"] == "HypothesisProposed"
                and not _measurement_declarations_match(fact_payload.get("measurement"), source_candidate.get("measurement"))):
            issues.append(OracleIssue("measurement_hypothesis_candidate_mismatch", "accepted hypothesis measurement differs from its SYNTHESIS candidate", (fact_id, str(attempt_id))))
        if fact["fact_type"] == "ExperimentPreregistered":
            contract = fact_payloads.get(fact_id, {}).get("execution_contract")
            if (
                not isinstance(contract, Mapping)
                or contract.get("source_agent_attempt_id") != attempt_id
                or contract.get("source_agent_output_digest") != digest
            ):
                issues.append(
                    OracleIssue(
                        "accepted_agent_fact_contract_mismatch",
                        "preregistration contract differs from its accepting Agent attempt and digest",
                        (fact_id, str(attempt_id)),
                    )
                )

    rendezvous_attempts = _audit_rendezvous(
        research_contract_version,
        facts,
        fact_payloads,
        transitions,
        transition_payloads,
        commands,
        attempts,
        attempt_payloads,
        successful,
        fact_bindings,
        run_roles,
        issues,
    )
    validation_source_attempts = {
        str(payload.get("agent_attempt_id"))
        for transition in transitions
        for payload in [transition_payloads.get(_row_identifier(transition), {})]
        for command in [commands_by_id.get(str(transition["command_id"]))]
        if command is not None
        and command["command_type"] == "VALIDATE_SPEC_CANDIDATE"
        and transition["state"] == "DRAFTED"
        and transition["run_id"] == command["run_id"]
        and transition["workflow_id"] == command["workflow_id"]
        and isinstance(payload, Mapping)
        and _trimmed_text(payload.get("agent_attempt_id"))
    }
    bound_attempts = (
        set(fact_bindings.values())
        | rendezvous_attempts
        | validation_source_attempts
        | {
            attempt_id
            for attempt_id, (request, _response) in successful.items()
            if _packet_only_agent_request(request)
        }
    )
    for attempt_id in successful:
        if attempt_id not in bound_attempts:
            issues.append(
                OracleIssue(
                    "successful_agent_fact_missing",
                    "successful Agent attempt has no accepted candidate fact",
                    (attempt_id,),
                )
            )

    for transition in transitions:
        payload = transition_payloads.get(_row_identifier(transition), {})
        if payload.get("agent_final_failure") is not True:
            continue
        attempt_id = str(payload.get("attempt_id"))
        attempt = attempts_by_id.get(attempt_id)
        if (
            attempt is None
            or attempt["state"] != "CLOSED"
            or attempt["terminal_reason"] == "SUCCEEDED"
            or attempt["run_id"] != transition["run_id"]
            or attempt["workflow_id"] != transition["workflow_id"]
        ):
            issues.append(
                OracleIssue(
                    "agent_final_failure_attempt_invalid",
                    "agent_final_failure does not bind its failed terminal Agent attempt",
                    (attempt_id,),
                )
            )
        observation = attempt_payloads.get(attempt_id, {})
        raw_request = observation.get("request") if isinstance(observation, Mapping) else None
        raw_command = raw_request.get("command") if isinstance(raw_request, Mapping) else None
        expected_fact_type = {
            "PROPOSE_HYPOTHESIS": "HypothesisProposed",
            "DRAFT_EXPERIMENT_SPEC": "ExperimentPreregistered",
        }.get(str(raw_command))
        conflicting: list[sqlite3.Row] = []
        if expected_fact_type == "HypothesisProposed":
            component_id = raw_request.get("component_id") if isinstance(raw_request, Mapping) else None
            expected_role = raw_request.get("role") if isinstance(raw_request, Mapping) else None
            if isinstance(component_id, str) and isinstance(expected_role, str):
                conflicting = [
                    fact
                    for fact in candidate_facts
                    if fact["fact_type"] == "HypothesisProposed"
                    and fact_payloads.get(str(fact["fact_id"]), {}).get("component_id") == component_id
                    and fact_payloads.get(str(fact["fact_id"]), {}).get("proposer_role") == expected_role
                ]
        elif expected_fact_type == "ExperimentPreregistered":
            conflicting = [
                fact
                for fact in candidate_facts
                if fact["fact_type"] == expected_fact_type
                and fact["experiment_id"] == transition["workflow_id"]
            ]
        if conflicting:
            issues.append(
                OracleIssue(
                    "agent_final_failure_has_candidate",
                    "final Agent failure owner already has an accepted candidate fact",
                    (attempt_id, *(str(fact["fact_id"]) for fact in conflicting)),
                )
            )


def _audit_literature_query_plan_causation(
    attempts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    successful: Mapping[str, tuple[AgentRequest, AgentResponse]],
    issues: list[OracleIssue],
) -> None:
    """Join each Grounding/MAIN retrieval to one exact successful query plan."""

    plans: dict[tuple[str, str, str, int], list[tuple[str, Mapping[str, Any], float]]] = {}
    grounding_plans: dict[str, list[tuple[str, Mapping[str, Any], float]]] = {}
    for attempt_id, (request, response) in successful.items():
        key = _research_task_key(request)
        task = request.to_dict().get("task_context")
        grounding_plan = (
            request.schema_version >= 10
            and request.command.value == "LITERATURE_REVIEW"
            and isinstance(task, Mapping)
            and task.get("kind") == "AUXILIARY"
            and task.get("request_kind") == "GROUNDING_QUERY_PLAN"
        )
        if (request.schema_version < 8 or request.command.value != "LITERATURE_REVIEW"
                or not grounding_plan and (not isinstance(key, Mapping)
                    or key.get("subkind") != "QUERY_PLAN")):
            continue
        candidate = response.typed_candidate
        templates = tuple(item for item in request.input_artifacts
            if item.ref.endswith("--literature-query-template"))
        template: object = None
        if len(templates) == 1 and templates[0].media_type == "application/json":
            try: template = json.loads(templates[0].content_bytes())
            except (UnicodeDecodeError, json.JSONDecodeError): pass
        mutable = {"query"} | ({"preferred_venues"} if request.schema_version >= 9 else set())
        template_matches = (isinstance(candidate, Mapping) and isinstance(template, Mapping)
            and set(template) == set(candidate) and mutable <= set(template)
            and {name: value for name, value in template.items() if name not in mutable}
            == {name: value for name, value in candidate.items() if name not in mutable})
        if not template_matches:
            issues.append(OracleIssue("literature_query_plan_causation_mismatch",
                "successful query plan changed or lacks its one frozen template", (attempt_id,)))
        elif isinstance(candidate, Mapping):
            ended = next((float(row["ended_at"]) for row in attempts
                if row["attempt_id"] == attempt_id and row["ended_at"] is not None), float("inf"))
            if grounding_plan:
                grounding_plans.setdefault(request.run_id, []).append(
                    (attempt_id, candidate, ended)
                )
            else:
                assert isinstance(key, Mapping)
                identity = (
                    request.run_id, request.component_id, request.role,
                    int(key["cycle"]),
                )
                plans.setdefault(identity, []).append(
                    (attempt_id, candidate, ended)
                )
        if any(item.ref.endswith("--literature-packet") for item in request.input_artifacts):
            issues.append(OracleIssue("literature_query_plan_causation_mismatch",
                "query-plan request must not contain a retrieved literature packet", (attempt_id,)))
    for attempt in attempts:
        attempt_id = str(attempt["attempt_id"]); observation = payloads.get(attempt_id)
        raw = observation.get("request") if isinstance(observation, Mapping) else None
        try: request = AgentRequest.from_dict(raw) if isinstance(raw, Mapping) else None
        except (AgentACIError, TypeError, ValueError): continue
        task = None if request is None else request.to_dict().get("task_context")
        grounding = (
            request is not None
            and request.schema_version >= 10
            and request.command.value == "GROUNDING"
            and isinstance(task, Mapping)
            and task.get("kind") == "AUXILIARY"
            and task.get("request_kind") == "GROUNDING"
        )
        if grounding:
            assert request is not None
            matches = grounding_plans.get(request.run_id, [])
            query_inputs = tuple(
                item for item in request.input_artifacts
                if item.ref.endswith("--literature-query")
            )
            if len(matches) != 1 or len(query_inputs) != 1:
                issues.append(OracleIssue(
                    "literature_query_plan_causation_mismatch",
                    "Grounding must bind one successful role-neutral plan and one exact query",
                    (attempt_id,),
                ))
                continue
            plan_id, candidate, ended = matches[0]
            query_input = query_inputs[0]
            try:
                query = json.loads(query_input.content_bytes())
            except (UnicodeDecodeError, json.JSONDecodeError):
                query = None
            if (
                query_input.ref != f"{plan_id}--literature-query"
                or query != candidate
                or ended > float(attempt["started_at"])
            ):
                issues.append(OracleIssue(
                    "literature_query_plan_causation_mismatch",
                    "Grounding query differs from its prior successful role-neutral plan",
                    (attempt_id, plan_id),
                ))
            packets = tuple(
                item for item in request.input_artifacts
                if item.ref.endswith("--literature-packet")
            )
            fake_offline = (
                request.required_adapter_version == "fake-agent/4"
                and request.tool_permission_profile
                in {"agent-host-mediated-literature/1", "agent-full-cc/1"}
            )
            if (
                attempt_id in successful
                and not fake_offline
                and len(packets) != 1
            ):
                issues.append(OracleIssue(
                    "literature_query_plan_causation_mismatch",
                    "successful Grounding must bind exactly one retrieved packet",
                    (attempt_id, plan_id),
                ))
            elif len(packets) > 1:
                issues.append(OracleIssue(
                    "literature_query_plan_causation_mismatch",
                    "Grounding must not bind duplicate retrieved packets",
                    (attempt_id, plan_id),
                ))
            elif packets:
                try:
                    packet_query = json.loads(
                        packets[0].content_bytes()
                    ).get("query")
                except (
                    AttributeError, UnicodeDecodeError, json.JSONDecodeError,
                ):
                    packet_query = None
                if packet_query != candidate:
                    issues.append(OracleIssue(
                        "literature_query_plan_causation_mismatch",
                        "Grounding packet differs from the admitted role-neutral plan",
                        (attempt_id, plan_id),
                    ))
            continue
        key = None if request is None else _research_task_key(request)
        if (request is None or request.schema_version < 8
                or request.command.value != "LITERATURE_REVIEW"
                or not isinstance(key, Mapping) or key.get("subkind") != "MAIN"):
            continue
        identity = (request.run_id, request.component_id, request.role, int(key["cycle"]))
        matches = plans.get(identity, []); detail = (attempt_id,)
        query_inputs = tuple(item for item in request.input_artifacts if item.ref.endswith("--literature-query"))
        if len(matches) != 1 or len(query_inputs) != 1:
            issues.append(OracleIssue("literature_query_plan_causation_mismatch",
                "literature MAIN must bind one successful plan and one exact query", detail)); continue
        plan_id, candidate, ended = matches[0]; query_input = query_inputs[0]
        try: query = json.loads(query_input.content_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError): query = None
        if (query_input.ref != f"{plan_id}--literature-query" or query != candidate
                or ended > float(attempt["started_at"])):
            issues.append(OracleIssue("literature_query_plan_causation_mismatch",
                "literature MAIN query differs from its prior successful plan", (attempt_id, plan_id)))
        packets = tuple(item for item in request.input_artifacts if item.ref.endswith("--literature-packet"))
        pair = successful.get(attempt_id)
        supporting_refs = (() if pair is None else
            tuple(pair[1].typed_candidate.get("supporting_refs", ())))
        if not set(supporting_refs) <= {item.ref for item in packets}:
            issues.append(OracleIssue("literature_query_plan_causation_mismatch",
                "literature MAIN cites context outside its retrieved packet",
                (attempt_id, plan_id)))
        if len(packets) != 1:
            fake_offline = (request.required_adapter_version == "fake-agent/4"
                and request.tool_permission_profile in {"agent-host-mediated-literature/1", "agent-full-cc/1"})
            real_success = attempt_id in successful and not fake_offline
            if real_success or packets:
                issues.append(OracleIssue("literature_query_plan_causation_mismatch",
                    "successful literature MAIN must bind exactly one retrieved packet",
                    (attempt_id, plan_id)))
        else:
            try: packet_query = json.loads(packets[0].content_bytes()).get("query")
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError): packet_query = None
            if packet_query != candidate:
                issues.append(OracleIssue("literature_query_plan_causation_mismatch",
                    "retrieved literature packet differs from the admitted plan", (attempt_id, plan_id)))


def _audit_rendezvous(
    research_contract_version: str | None,
    facts: Sequence[sqlite3.Row],
    fact_payloads: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    commands: Sequence[sqlite3.Row],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    successful: Mapping[str, tuple[AgentRequest, AgentResponse]],
    fact_bindings: Mapping[str, str],
    run_roles: Mapping[str, tuple[str, ...]],
    issues: list[OracleIssue],
) -> set[str]:
    """Join typed peer requests directly across raw command, Attempt, and transition rows."""

    commands_by_id = {str(row["command_id"]): row for row in commands}
    request_records: list[dict[str, Any]] = []
    resolution_records: list[dict[str, Any]] = []
    for transition in transitions:
        command = commands_by_id.get(str(transition["command_id"]))
        command_type = None if command is None else str(command["command_type"])
        if command_type not in {"REQUEST_RENDEZVOUS", "RESOLVE_RENDEZVOUS"}:
            continue
        identifier = _row_identifier(transition)
        component_owner = (
            research_contract_version in _COMPONENT_FACT_SCHEMA_BY_RESEARCH_VERSION
            and transition["workflow_kind"] == "EXPERIMENT"
            and transition["workflow_id"] != transition["run_id"]
        )
        if (
            not (
                transition["workflow_kind"] == "RUN"
                and transition["workflow_id"] == transition["run_id"]
                or component_owner
            )
            or command is None
            or command["run_id"] != transition["run_id"]
            or command["workflow_id"] != transition["workflow_id"]
        ):
            issues.append(
                OracleIssue(
                    "rendezvous_command_owner_mismatch",
                    "typed rendezvous command is not a self-edge on its owning workflow",
                    (identifier, str(transition["command_id"])),
                )
            )
        keys = (
            _RENDEZVOUS_REQUEST_KEYS
            if command_type == "REQUEST_RENDEZVOUS"
            else _RENDEZVOUS_REPLY_KEYS
        )
        values = _rendezvous_event_values(
            command,
            transition_payloads.get(identifier, {}),
            keys,
            identifier,
            issues,
        )
        validator = (
            _valid_rendezvous_request
            if command_type == "REQUEST_RENDEZVOUS"
            else _valid_rendezvous_reply
        )
        if len(values) != 1 or not validator(values[0]):
            issues.append(
                OracleIssue(
                    "rendezvous_request_invalid"
                    if command_type == "REQUEST_RENDEZVOUS"
                    else "rendezvous_resolution_invalid",
                    "rendezvous command must persist exactly one strict typed value",
                    (identifier,),
                )
            )
            continue
        record = {
            "command_id": str(transition["command_id"]),
            "identifier": identifier,
            "revision": int(transition["revision"]),
            "run_id": str(transition["run_id"]),
            "workflow_id": str(transition["workflow_id"]),
            "workflow_kind": str(transition["workflow_kind"]),
            "state": str(transition["state"]),
            "payload": transition_payloads.get(identifier, {}),
            "wire": values[0],
        }
        if command_type == "REQUEST_RENDEZVOUS":
            request_records.append(record)
        else:
            resolution_records.append(record)

    accepted_signals: list[dict[str, Any]] = []
    for fact in facts:
        if fact["fact_type"] != "HypothesisProposed":
            continue
        fact_id = str(fact["fact_id"])
        source_attempt_id = fact_bindings.get(fact_id)
        if source_attempt_id not in successful:
            continue
        observation = attempt_payloads.get(str(source_attempt_id), {})
        raw_response = observation.get("response") if isinstance(observation, Mapping) else None
        signal = raw_response.get("expert_signal") if isinstance(raw_response, Mapping) else None
        if signal is None:
            continue
        if not isinstance(signal, Mapping):
            continue  # validate_postflight already reports this malformed successful exchange.
        run_id = str(fact["run_id"])
        fact_payload = fact_payloads.get(fact_id, {})
        role = fact_payload.get("proposer_role")
        component_id = fact_payload.get("component_id")
        roles = run_roles.get(run_id, ())
        accepting = [
            transition
            for transition in transitions
            if _row_get(transition, "fact_id") == fact["fact_id"]
        ]
        acceptance_identifier = (
            _row_identifier(accepting[0]) if len(accepting) == 1 else None
        )
        acceptance_payload = (
            transition_payloads.get(acceptance_identifier, {})
            if acceptance_identifier is not None
            else {}
        )
        accepted_signals.append(
            {
                "accept_revision": (
                    int(accepting[0]["revision"])
                    if len(accepting) == 1
                    else None
                ),
                "accepted_signal": acceptance_payload.get("expert_signal"),
                "fact_id": fact_id,
                "component_id": component_id,
                "rendezvous_id": acceptance_payload.get("rendezvous_id"),
                "requester_role": role,
                "roles": roles,
                "run_id": run_id,
                "signal": dict(signal),
                "source_attempt_id": str(source_attempt_id),
                "source_role": successful[str(source_attempt_id)][0].role,
                "target_role": acceptance_payload.get("target_role"),
            }
        )

    linked_request_ids = _audit_component_rendezvous_origins(
        facts,
        fact_payloads,
        attempts,
        attempt_payloads,
        successful,
        request_records,
        run_roles,
        issues,
        commands=commands,
    ) if research_contract_version in _COMPONENT_FACT_SCHEMA_BY_RESEARCH_VERSION else set()
    direct_resolution_ids: set[str] = set()
    for accepted in accepted_signals:
        matching = [
            record
            for record in request_records
            if record["wire"].get("source_attempt_id") == accepted["source_attempt_id"]
        ]
        if len(matching) == 0:
            direct = [
                record
                for record in resolution_records
                if record["wire"].get("rendezvous_id")
                == accepted["rendezvous_id"]
            ]
            if len(direct) == 1 and _valid_direct_host_resolution(
                accepted, direct[0]
            ):
                direct_resolution_ids.add(direct[0]["identifier"])
                continue
            if len(direct) != 1:
                issues.append(
                    OracleIssue(
                        "rendezvous_resolution_count",
                        "accepted direct Host rendezvous must have exactly one RESOLVE_RENDEZVOUS",
                        (
                            accepted["fact_id"],
                            str(accepted["rendezvous_id"]),
                            str(len(direct)),
                        ),
                    )
                )
            else:
                issues.append(
                    OracleIssue(
                        "rendezvous_reply_mismatch",
                        "direct Host resolution is not a valid NO_TARGET, INTEGRITY_REJECTED, or pre-request CANCELLED terminal",
                        (accepted["fact_id"], direct[0]["identifier"]),
                    )
                )
            issues.append(
                OracleIssue(
                    "rendezvous_request_count",
                    "accepted expert_signal must have one typed request or a valid direct Host terminal resolution",
                    (accepted["fact_id"], accepted["source_attempt_id"], str(len(matching))),
                )
            )
            continue
        if len(matching) != 1:
            issues.append(
                OracleIssue(
                    "rendezvous_request_count",
                    "accepted expert_signal must have one typed request or a valid direct Host terminal resolution",
                    (accepted["fact_id"], accepted["source_attempt_id"], str(len(matching))),
                )
            )
            continue
        request_record = matching[0]
        linked_request_ids.add(request_record["identifier"])
        request = request_record["wire"]
        signal = accepted["signal"]
        target_hint = signal.get("target_role_hint")
        target_role = request.get("target_role")
        expected_target = _expected_rendezvous_target(accepted)
        valid_target = (
            isinstance(target_role, str)
            and target_role in accepted["roles"]
            and target_role != accepted["requester_role"]
            and (target_hint is None or target_role == target_hint)
            and target_role == expected_target
            and target_role == accepted["target_role"]
        )
        if not (
            request_record["run_id"] == accepted["run_id"]
            and request.get("rendezvous_id") == accepted["rendezvous_id"]
            and request.get("run_id") == accepted["run_id"]
            and request.get("requester_role") == accepted["requester_role"]
            and request.get("requester_role") == accepted["source_role"]
            and request.get("kind") == signal.get("kind")
            and request.get("question") == signal.get("question")
            and request.get("context_refs") == signal.get("context_refs")
            and valid_target
            and accepted["accept_revision"] is not None
            and request_record["revision"] > accepted["accept_revision"]
        ):
            issues.append(
                OracleIssue(
                    "rendezvous_request_mismatch",
                    "typed request differs from its accepted source signal, component role, or causation",
                    (accepted["fact_id"], request_record["identifier"]),
                )
            )

    for record in request_records:
        if record["identifier"] not in linked_request_ids:
            issues.append(
                OracleIssue(
                    "orphan_rendezvous_request",
                    "REQUEST_RENDEZVOUS does not bind exactly one accepted expert_signal",
                    (record["identifier"], str(record["wire"].get("rendezvous_id"))),
                )
            )

    successful_answer_ids: set[str] = set()
    answer_candidates: dict[str, list[Mapping[str, Any]]] = {}
    for attempt in attempts:
        attempt_id = str(attempt["attempt_id"])
        observation = attempt_payloads.get(attempt_id, {})
        raw_request = observation.get("request") if isinstance(observation, Mapping) else None
        if not isinstance(raw_request, Mapping) or raw_request.get("command") != "ANSWER_RENDEZVOUS":
            continue
        if attempt_id in successful:
            successful_answer_ids.add(attempt_id)
        embedded = _embedded_rendezvous_requests(raw_request)
        matching = [
            record
            for record in request_records
            if record["command_id"] == str(attempt["command_id"])
        ]
        if not (
            len(embedded) == 1
            and len(matching) == 1
            and embedded[0] == matching[0]["wire"]
            and raw_request.get("run_id") == matching[0]["run_id"]
            and raw_request.get("owner_workflow_id") == matching[0]["workflow_id"]
            and raw_request.get("role") == matching[0]["wire"].get("target_role")
        ):
            issues.append(
                OracleIssue(
                    "rendezvous_answer_request_mismatch",
                    "ANSWER_RENDEZVOUS Attempt does not bind the exact persisted Run request",
                    (attempt_id, str(attempt["command_id"])),
                )
            )
            continue
        if attempt_id not in successful:
            continue
        raw_response = observation.get("response")
        candidate = raw_response.get("typed_candidate") if isinstance(raw_response, Mapping) else None
        if isinstance(candidate, Mapping) and _valid_rendezvous_reply(candidate):
            answer_candidates.setdefault(str(attempt["command_id"]), []).append(candidate)

    resolutions_by_id: dict[str, list[dict[str, Any]]] = {}
    for record in resolution_records:
        rendezvous_id = str(record["wire"].get("rendezvous_id"))
        resolutions_by_id.setdefault(rendezvous_id, []).append(record)
        matching_requests = [
            request
            for request in request_records
            if request["wire"].get("rendezvous_id") == rendezvous_id
        ]
        if len(matching_requests) != 1:
            if record["identifier"] in direct_resolution_ids:
                continue
            issues.append(
                OracleIssue(
                    "orphan_rendezvous_resolution",
                    "RESOLVE_RENDEZVOUS does not identify exactly one typed request",
                    (record["identifier"], rendezvous_id),
                )
            )
            issues.append(
                OracleIssue(
                    "rendezvous_reply_mismatch",
                    "typed resolution request ID has no exact request peer",
                    (record["identifier"], rendezvous_id),
                )
            )

    for request_record in request_records:
        request = request_record["wire"]
        rendezvous_id = str(request.get("rendezvous_id"))
        resolutions = resolutions_by_id.get(rendezvous_id, [])
        if len(resolutions) != 1:
            issues.append(
                OracleIssue(
                    "rendezvous_resolution_count",
                    "accepted typed rendezvous must have exactly one RESOLVE_RENDEZVOUS",
                    (rendezvous_id, str(len(resolutions))),
                )
            )
            continue
        resolution = resolutions[0]
        reply = resolution["wire"]
        aligned = (
            resolution["run_id"] == request_record["run_id"]
            and resolution["workflow_id"] == request_record["workflow_id"]
            and resolution["revision"] > request_record["revision"]
            and reply.get("rendezvous_id") == request.get("rendezvous_id")
            and reply.get("kind") == request.get("kind")
            and set(reply.get("supporting_refs", ())) <= set(request.get("context_refs", ()))
        )
        candidates = answer_candidates.get(request_record["command_id"], [])
        disposition = reply.get("disposition")
        if disposition in {"ANSWERED", "DECLINED"}:
            aligned = (
                aligned
                and reply.get("responder_role") == request.get("target_role")
                and len(candidates) == 1
                and candidates[0] == reply
            )
        else:
            aligned = (
                aligned
                and reply.get("responder_role")
                in {request.get("target_role"), "WorkflowHost"}
                and not candidates
            )
        if not aligned:
            issues.append(
                OracleIssue(
                    "rendezvous_reply_mismatch",
                    "typed resolution differs from its request or accepted Agent reply",
                    (request_record["identifier"], resolution["identifier"]),
                )
            )
    return successful_answer_ids


def _audit_component_rendezvous_origins(
    facts: Sequence[sqlite3.Row],
    fact_payloads: Mapping[str, Mapping[str, Any]],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    successful: Mapping[str, tuple[AgentRequest, AgentResponse]],
    requests: Sequence[Mapping[str, Any]],
    run_roles: Mapping[str, tuple[str, ...]],
    issues: list[OracleIssue],
    *, commands: Sequence[Mapping[str, Any]] = (),
) -> set[str]:
    """Bind v4 campaign rendezvous to their component-local source attempt."""

    attempts_by_id = {str(row["attempt_id"]): row for row in attempts}
    component_roles = _goal_component_roles_by_run(facts, fact_payloads)
    campaign_roles = _goal_campaign_roles_by_run(facts, fact_payloads)
    specs = [
        row
        for row in facts
        if row["fact_type"] == "ExperimentPreregistered"
    ]
    outcomes = [
        row
        for row in facts
        if row["fact_type"] == "CanonicalOutcomeAccepted"
    ]
    linked: set[str] = set()
    seen: list[tuple[str, str, str, str, str]] = []
    origin_keys = {
        "rendezvous_id",
        "requester_role",
        "requester_slot",
        "source_attempt_id",
        "target_role",
        "expert_signal",
        "component_id",
        "phase",
    }
    for record in requests:
        payload = record.get("payload")
        origin = payload.get("rendezvous_origin") if isinstance(payload, Mapping) else None
        if origin is None:
            continue
        identifier = str(record["identifier"])
        linked.add(identifier)
        wire = record["wire"]
        run_id = str(record["run_id"])
        component_id = origin.get("component_id") if isinstance(origin, Mapping) else None
        phase = origin.get("phase") if isinstance(origin, Mapping) else None
        source_id = origin.get("source_attempt_id") if isinstance(origin, Mapping) else None
        source = attempts_by_id.get(str(source_id))
        roles = run_roles.get(run_id, ())
        role_contract = component_roles.get((run_id, str(component_id)))
        owner_role = None if role_contract is None else role_contract[0]
        expected_target = campaign_roles.get((run_id, str(component_id), str(phase)))
        signal = origin.get("expert_signal") if isinstance(origin, Mapping) else None
        expected_state = {"preflight": "DRAFTED", "analyze": "EVALUATING"}.get(str(phase))
        expected_kind = {"preflight": "AGENT", "analyze": "PROCESS"}.get(str(phase))
        expected_slot = roles.index(owner_role) + 1 if owner_role in roles else None
        source_valid = (
            source is not None
            and source["run_id"] == run_id
            and source["workflow_id"] == record["workflow_id"]
            and source["kind"] == expected_kind
            and source["state"] == "CLOSED"
        )
        submit_id = payload.get("source_submit_command_id") if isinstance(payload, Mapping) else None
        if phase == "preflight" and submit_id is not None:
            submit = next((row for row in commands if row["command_id"] == submit_id), None)
            decoded = None if submit is None else _raw_submit_source(submit, attempts, attempt_payloads)
            try:
                source_refs = set(_submitted_source_documents(decoded[1], str(submit["workflow_id"]) + "--")) if decoded is not None else set()
            except (KeyError, TypeError, ValueError): source_refs = set()
            source_valid = (decoded is not None and source is not None
                and decoded[0]["attempt_id"] == source_id
                and decoded[0]["role"] == owner_role and decoded[0]["component_id"] == component_id
                and submit["workflow_id"] == record["workflow_id"]
                and wire.get("rendezvous_id") == f"{submit['workflow_id']}--preflight-rendezvous"
                and bool(source_refs)
                and {f"{submit['workflow_id']}--submission", *source_refs} <= set(wire.get("context_refs", ())))
        elif phase == "preflight" and source_valid:
            exchange = successful.get(str(source_id))
            matching_validations = [
                attempt
                for attempt in attempts
                if attempt["run_id"] == run_id
                and attempt["workflow_id"] == record["workflow_id"]
                and attempt["kind"] == "PROCESS"
                and attempt["state"] == "CLOSED"
                and attempt["terminal_reason"] == "SUCCEEDED"
                and attempt["execution_id"]
                == f"{record['workflow_id']}--candidate-validation"
                and attempt_payloads.get(str(attempt["attempt_id"]), {}).get(
                    "source_agent_attempt_id"
                )
                == source_id
            ]
            source_valid = (
                source["terminal_reason"] == "SUCCEEDED"
                and exchange is not None
                and exchange[0].command.value in {
                    "DRAFT_EXPERIMENT_SPEC", "EXPERIMENT_DESIGN_IMPLEMENT",
                }
                and exchange[0].component_id == component_id
                and exchange[0].role == owner_role
                and len(matching_validations) == 1
            )
        elif phase == "analyze" and source_valid:
            matching_outcomes = [
                outcome
                for outcome in outcomes
                if outcome["run_id"] == run_id
                and fact_payloads.get(str(outcome["fact_id"]), {}).get(
                    "component_id"
                )
                == component_id
                and source["experiment_id"] == outcome["experiment_id"]
                and source["execution_id"] == outcome["execution_id"]
            ]
            source_valid = (
                source["terminal_reason"] is not None
                and len(matching_outcomes) == 1
            )
        wire_matches = (
            isinstance(origin, Mapping)
            and set(origin) == origin_keys
            and isinstance(signal, Mapping)
            and wire.get("rendezvous_id") == origin.get("rendezvous_id")
            and wire.get("run_id") == run_id
            and wire.get("requester_role") == origin.get("requester_role")
            and wire.get("requester_slot") == origin.get("requester_slot")
            and wire.get("target_role") == origin.get("target_role")
            and wire.get("source_attempt_id") == source_id
            and wire.get("kind") == signal.get("kind")
            and wire.get("question") == signal.get("question")
            and wire.get("context_refs") == signal.get("context_refs")
        )
        valid = (
            record["workflow_kind"] == "EXPERIMENT"
            and record["state"] == expected_state
            and phase in {"preflight", "analyze"}
            and _trimmed_text(component_id)
            and origin.get("rendezvous_id") == payload.get("rendezvous_id")
            and payload.get("component_id") == component_id
            and payload.get("role") == owner_role
            and origin.get("requester_role") == owner_role
            and origin.get("requester_slot") == expected_slot
            and origin.get("target_role") == expected_target
            and expected_target in roles
            and expected_target != owner_role
            and source_valid
            and wire_matches
        )
        if not valid:
            issues.append(
                OracleIssue(
                    "component_rendezvous_origin_mismatch",
                    "component campaign request differs from its frozen role owner, source attempt, or typed wire value",
                    (identifier, str(component_id), str(phase), str(source_id)),
                )
            )
        if _trimmed_text(component_id) and phase in {"preflight", "analyze"}:
            seen.append(
                (
                    run_id,
                    str(component_id),
                    str(phase),
                    str(submit_id or source_id),
                    identifier,
                )
            )

    for spec in specs:
        run_id = str(spec["run_id"])
        component_id = str(
            fact_payloads.get(str(spec["fact_id"]), {}).get("component_id")
        )
        if (run_id, component_id, "preflight") in campaign_roles:
            contract = fact_payloads.get(str(spec["fact_id"]), {}).get("execution_contract", {})
            source_id = str(contract.get("source_submit_command_id") or contract.get("source_agent_attempt_id"))
            matches = [
                item
                for item in seen
                if item[:4] == (run_id, component_id, "preflight", source_id)
            ]
            if len(matches) != 1:
                issues.append(
                    OracleIssue(
                        "component_rendezvous_count",
                        "preregistered component must have exactly one required preflight rendezvous",
                        (
                            run_id,
                            component_id,
                            "preflight",
                            str(spec["fact_id"]),
                            str(len(matches)),
                        ),
                    )
                )
    for outcome in outcomes:
        run_id = str(outcome["run_id"])
        component_id = str(
            fact_payloads.get(str(outcome["fact_id"]), {}).get("component_id")
        )
        if (run_id, component_id, "analyze") in campaign_roles:
            matches = []
            for item in seen:
                if item[:3] != (run_id, component_id, "analyze"):
                    continue
                source = attempts_by_id.get(item[3])
                if (
                    source is not None
                    and source["experiment_id"] == outcome["experiment_id"]
                    and source["execution_id"] == outcome["execution_id"]
                ):
                    matches.append(item)
            if len(matches) != 1:
                issues.append(
                    OracleIssue(
                        "component_rendezvous_count",
                        "accepted component outcome must have exactly one required analysis rendezvous",
                        (
                            run_id,
                            component_id,
                            "analyze",
                            str(outcome["fact_id"]),
                            str(len(matches)),
                        ),
                    )
                )
    return linked


def _goal_campaign_roles_by_run(
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    for fact in facts:
        if fact["fact_type"] != "GoalDefined":
            continue
        contract = payloads.get(str(fact["fact_id"]), {}).get("research_contract")
        if not isinstance(contract, Mapping):
            continue
        methodology = contract.get("methodology")
        controls = methodology.get("controls") if isinstance(methodology, Mapping) else None
        campaigns = controls.get("experiment_campaigns") if isinstance(controls, Mapping) else None
        if not isinstance(campaigns, Mapping) or campaigns.get("enabled") is not True:
            continue
        phases = campaigns.get("required_roles")
        owners = campaigns.get("role_owners")
        components = contract.get("components")
        if not isinstance(phases, list) or not isinstance(owners, Mapping) or not isinstance(components, list):
            continue
        for component in components:
            component_id = component.get("id") if isinstance(component, Mapping) else None
            selected = owners.get(component_id, owners.get("*"))
            if not _trimmed_text(component_id) or not isinstance(selected, Mapping):
                continue
            for phase in phases:
                role = selected.get(phase)
                if phase in {"preflight", "analyze"} and _trimmed_text(role):
                    result[(str(fact["run_id"]), str(component_id), str(phase))] = str(role)
    return result


def _expected_rendezvous_target(accepted: Mapping[str, Any]) -> str | None:
    requester = accepted.get("requester_role")
    roles = accepted.get("roles")
    signal = accepted.get("signal")
    if not isinstance(roles, tuple) or not isinstance(signal, Mapping):
        return None
    peers = tuple(role for role in roles if role != requester)
    hint = signal.get("target_role_hint")
    if hint is None:
        return peers[0] if peers else None
    return hint if isinstance(hint, str) and hint in peers else None


def _valid_direct_host_resolution(
    accepted: Mapping[str, Any],
    resolution: Mapping[str, Any],
) -> bool:
    reply = resolution.get("wire")
    accept_revision = accepted.get("accept_revision")
    if not isinstance(reply, Mapping) or not isinstance(accept_revision, int):
        return False
    expected_target = _expected_rendezvous_target(accepted)
    roles = accepted.get("roles")
    requester = accepted.get("requester_role")
    peers = (
        tuple(role for role in roles if role != requester)
        if isinstance(roles, tuple)
        else ()
    )
    expected_rendezvous_id = f"{accepted.get('source_attempt_id')}--rendezvous"
    common = (
        isinstance(accepted.get("rendezvous_id"), str)
        and accepted.get("rendezvous_id") == expected_rendezvous_id
        and accepted.get("accepted_signal") == accepted.get("signal")
        and accepted.get("target_role") == expected_target
        and resolution.get("run_id") == accepted.get("run_id")
        and isinstance(resolution.get("revision"), int)
        and resolution["revision"] > accept_revision
        and reply.get("rendezvous_id") == accepted.get("rendezvous_id")
        and reply.get("kind") == accepted.get("signal", {}).get("kind")
        and reply.get("responder_role") == "WorkflowHost"
        and reply.get("supporting_refs") == []
    )
    if not common:
        return False
    if reply.get("disposition") == "NO_TARGET":
        return not peers and expected_target is None and resolution.get("state") == "ACTIVE"
    if reply.get("disposition") == "INTEGRITY_REJECTED":
        return bool(peers) and expected_target is None and resolution.get("state") == "ACTIVE"
    if reply.get("disposition") == "CANCELLED":
        return resolution.get("state") == "STOPPING"
    return False


def _rendezvous_event_values(
    command: sqlite3.Row,
    transition_payload: Mapping[str, Any],
    keys: set[str],
    identifier: str,
    issues: list[OracleIssue],
) -> tuple[Mapping[str, Any], ...]:
    roots: list[Any] = [transition_payload]
    for column in ("request_json", "result_json", "error_json"):
        raw = _row_get(command, column)
        if raw is None:
            continue
        try:
            roots.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError) as exc:
            issues.append(
                OracleIssue(
                    "rendezvous_payload_invalid",
                    f"rendezvous command JSON is unreadable: {exc}",
                    (identifier, column),
                )
            )
    unique: dict[str, Mapping[str, Any]] = {}
    for root in roots:
        for value in _find_exact_wire_objects(root, keys):
            rendered = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            unique[rendered] = value
    return tuple(unique.values())


def _find_exact_wire_objects(value: Any, keys: set[str]) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        if set(value) == keys:
            return (value,)
        return tuple(
            match
            for nested in value.values()
            for match in _find_exact_wire_objects(nested, keys)
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            match
            for nested in value
            for match in _find_exact_wire_objects(nested, keys)
        )
    return ()


def _embedded_rendezvous_requests(request: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    matches: list[Mapping[str, Any]] = []
    artifacts = request.get("input_artifacts")
    if not isinstance(artifacts, list):
        return ()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        encoded = artifact.get("content_base64")
        if not isinstance(encoded, str):
            continue
        try:
            decoded = base64.b64decode(encoded, validate=True)
            value = json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        matches.extend(_find_exact_wire_objects(value, _RENDEZVOUS_REQUEST_KEYS))
    return tuple(matches)


def _valid_rendezvous_request(value: Mapping[str, Any]) -> bool:
    refs = value.get("context_refs")
    slot = value.get("requester_slot")
    return (
        set(value) == _RENDEZVOUS_REQUEST_KEYS
        and value.get("schema_version") == 1
        and all(
            _bounded_trimmed_text(value.get(name), 256)
            for name in (
                "rendezvous_id",
                "run_id",
                "requester_role",
                "target_role",
                "source_attempt_id",
            )
        )
        and value.get("requester_role") != value.get("target_role")
        and not isinstance(slot, bool)
        and isinstance(slot, int)
        and 1 <= slot <= 3
        and value.get("kind") in _RENDEZVOUS_KINDS
        and _bounded_trimmed_text(value.get("question"), 4096)
        and _valid_string_refs(refs, empty=False)
        and value.get("round") == 1
        and value.get("max_rounds") == 1
    )


def _valid_rendezvous_reply(value: Mapping[str, Any]) -> bool:
    return (
        set(value) == _RENDEZVOUS_REPLY_KEYS
        and value.get("schema_version") == 1
        and _bounded_trimmed_text(value.get("rendezvous_id"), 256)
        and value.get("kind") in _RENDEZVOUS_KINDS
        and value.get("disposition") in _RENDEZVOUS_DISPOSITIONS
        and _bounded_trimmed_text(value.get("responder_role"), 128)
        and _bounded_trimmed_text(value.get("summary"), 4096)
        and _valid_string_refs(value.get("supporting_refs"), empty=True)
    )


def _valid_string_refs(value: Any, *, empty: bool) -> bool:
    return (
        isinstance(value, list)
        and (empty or bool(value))
        and len(value) <= 16
        and all(_bounded_trimmed_text(item, 512) for item in value)
        and len(value) == len(set(value))
    )


def _bounded_trimmed_text(value: Any, maximum: int) -> bool:
    return _trimmed_text(value) and len(value) <= maximum


def _raw_resource_estimate(value: Any) -> dict[str, Any]:
    """Independent wire validation, not the production ResourceEstimate type."""
    def text(item: Any, limit: int) -> bool:
        return _bounded_trimmed_text(item, limit) and all(ord(c) >= 32 and ord(c) != 127 for c in item)
    if (not isinstance(value, Mapping) or not {"workload", "peak_memory_mib", "basis"} <= set(value)
            or set(value) - {"workload", "peak_memory_mib", "basis", "measurement_attempt_ids"}):
        raise ValueError("resource estimate fields differ from the typed submit protocol")
    workload, peak, ids = value["workload"], value["peak_memory_mib"], value.get("measurement_attempt_ids", [])
    if (not isinstance(workload, Mapping) or not 1 <= len(workload) <= 16
            or not all(text(key, 64) and text(item, 256) for key, item in workload.items())
            or type(peak) is not int or not 1 <= peak < 2**53 or not text(value["basis"], 1024)
            or not isinstance(ids, list) or len(ids) > 32 or not all(text(item, 256) for item in ids)
            or len(ids) != len(set(ids))):
        raise ValueError("resource estimate has invalid workload, memory, basis or source IDs")
    return {"workload": dict(sorted(workload.items())), "peak_memory_mib": peak,
            "basis": value["basis"], "measurement_attempt_ids": sorted(ids)}


def _raw_submit_arguments_match(original: Any, resolved: Any, aci_version: Any) -> bool:
    if not isinstance(original, Mapping) or not isinstance(resolved, Mapping): return False
    excluded = {"workspace_files"}
    if "resource_estimate" in original or "resource_estimate" in resolved:
        if type(aci_version) is not int or aci_version < 18: return False
        raw = _raw_resource_estimate(original.get("resource_estimate"))
        captured = _raw_resource_estimate(resolved.get("resource_estimate"))
        if (captured != resolved["resource_estimate"]
                or any(raw[name] != captured[name] for name in ("workload", "peak_memory_mib", "basis"))
                or not set(raw["measurement_attempt_ids"]) <= set(captured["measurement_attempt_ids"])):
            return False
        excluded.add("resource_estimate")
    return ({name: value for name, value in original.items() if name not in excluded}
            == {name: value for name, value in resolved.items() if name not in excluded})


def _raw_submit_source(command: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]],
                       observations: Mapping[str, Mapping[str, Any]]) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Decode source identity from raw command bytes, without the Host validator."""
    try:
        data = json.loads(command["request_json"])
        request, snapshot = data["source_request"], data["source_snapshot"]
        arguments = snapshot["arguments"]
        predecessor = data["predecessor_experiment_id"]
        source = next(row for row in attempts if row["attempt_id"] == request["attempt_id"])
        task = request["task_context"]; key = task["key"]
        valid = (command["command_type"] == "CREATE_EXPERIMENT" and command["response_status"] == "accepted"
            and data["tool"] == "submit_experiment" and supports_experiment_submission(request["schema_version"])
            and source["kind"] == "AGENT" and source["run_id"] == command["run_id"] == request["run_id"]
            and source["workflow_id"] == request["owner_workflow_id"] == request["run_id"]
            and source["command_id"] == request["command_id"] == data["execution_node_command_id"]
            and source["attempt_ordinal"] == request["attempt_ordinal"]
            and observations.get(str(source["attempt_id"]), {}).get("request") == request
            and request["command"] == "EXPERIMENT_EXECUTION" and task["kind"] == "RESEARCH_NODE"
            and key["node_kind"] == "EXPERIMENT_EXECUTION" and key["run_id"] == request["run_id"]
            and key["component_id"] == request["component_id"] and key["expert_slot"] == request["role"]
            and command["command_id"] == f"{request['command_id']}--submit--{predecessor or 'initial'}"
            and (predecessor is None or isinstance(predecessor, str) and bool(predecessor))
            and set(snapshot) == {"kind", "command_id", "attempt_id", "input_digest", "arguments", "output_digest"}
            and snapshot["kind"] == "admitted-submit/1" and snapshot["command_id"] == request["command_id"]
            and snapshot["attempt_id"] == request["attempt_id"]
            and snapshot["input_digest"] == _canonical_json_digest(request)
            and snapshot["output_digest"] == _canonical_json_digest({name: value for name, value in snapshot.items() if name != "output_digest"})
            and arguments.get("replaces_experiment_id") == predecessor
            and _raw_submit_arguments_match(data["arguments"], arguments, request["schema_version"])
            and _decode_workspace(snapshot) == _decode_workspace({"kind": "admitted-submit/1", "arguments": data["arguments"]}))
        stamp = datetime.fromisoformat(str(command["created_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
        accepted_at = stamp.timestamp()
        valid = valid and float(source["started_at"]) <= accepted_at + 0.002 and (
            source["ended_at"] is None or accepted_at <= float(source["ended_at"]) + 0.002)
        return (request, snapshot) if valid else None
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        return None


def _submitted_source_documents(snapshot: Mapping[str, Any], prefix: str) -> dict[str, tuple[str, bytes]]:
    workspace = _decode_workspace(snapshot)
    if workspace is None: raise ValueError("invalid submitted source bytes")
    candidate = snapshot["arguments"]["candidate"]
    argv = candidate.get("execution_argv")
    if argv is None:
        return {prefix + "experiment.py": ("text/x-python", workspace["experiment.py"])}
    measurement = candidate.get("measurement")
    paths = set(() if measurement is None else measurement["parser_files"])
    for argument in argv:
        if argument.startswith("/input/"): paths.add(argument.removeprefix("/input/"))
        elif not argument.startswith(("/", "-")) and argument.removeprefix("./") in workspace:
            paths.add(argument.removeprefix("./"))
    documents = {prefix + path: ("text/x-python" if path.endswith(".py") else "text/plain", workspace[path]) for path in sorted(paths)}
    documents[prefix + "source-command"] = ("application/json", json.dumps({"execution_argv": argv,
        "parser_argv": None if measurement is None else measurement["parser_argv"], "sources": [{"path": path, "ref": prefix + path} for path in sorted(paths)]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    return documents


def _audit_submitted_experiments(commands: Sequence[Mapping[str, Any]], transitions: Sequence[Mapping[str, Any]],
                                payloads: Mapping[str, Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
                                observations: Mapping[str, Mapping[str, Any]], issues: list[OracleIssue],
                                *, facts: Sequence[Mapping[str, Any]] = ()) -> None:
    by_id = {str(row["command_id"]): dict(row) for row in commands}
    creations = [row for row in transitions if row["workflow_kind"] == "EXPERIMENT" and row["revision"] == 1]
    declined: dict[str, Mapping[str, Any]] = {}
    for terminal in transitions:
        terminal_payload = payloads.get(_row_identifier(terminal), {})
        if terminal_payload.get("reason") != "preflight_declined": continue
        experiment_id = str(terminal["workflow_id"])
        creation = next((row for row in creations if row["workflow_id"] == experiment_id), None)
        rid = f"{experiment_id}--preflight-rendezvous"
        replies = [row for row in transitions if row["workflow_id"] == experiment_id
            and by_id.get(str(row["command_id"]), {}).get("command_type") == "RESOLVE_RENDEZVOUS"
            and payloads.get(_row_identifier(row), {}).get("rendezvous_reply", {}).get("rendezvous_id") == rid
            and payloads.get(_row_identifier(row), {}).get("rendezvous_reply", {}).get("disposition") == "DECLINED"]
        valid = (creation is not None and terminal["previous_state"] == "DRAFTED" and terminal["state"] == "TERMINAL"
            and by_id.get(str(terminal["command_id"]), {}).get("command_type") == "CANCEL"
            and terminal_payload.get("discarded_before_preregistration") is True
            and terminal_payload.get("source_submit_command_id") == creation["command_id"]
            and terminal_payload.get("rendezvous_id") == rid
            and len(replies) == 1 and replies[0]["revision"] < terminal["revision"]
            and not any(row["experiment_id"] == experiment_id for row in facts if row["fact_type"] in {"ExperimentPreregistered", "CanonicalOutcomeAccepted"})
            and not any(row["workflow_id"] == experiment_id and row["kind"] == "PROCESS" for row in attempts))
        if valid: declined[experiment_id] = terminal
        else: issues.append(OracleIssue("preflight_control_close_invalid", "unpreregistered rejection must follow its real DECLINED review and have no scientific or Process effect", (experiment_id,)))
    for creation in creations:
        identity = payloads.get(_row_identifier(creation), {})
        command = by_id.get(str(creation["command_id"]))
        decoded = None if command is None else _raw_submit_source(command, attempts, observations)
        if decoded is None:
            issues.append(OracleIssue("experiment_submit_source_invalid", "experiment creation lacks one accepted, exact-owner submit source", (str(creation["workflow_id"]),)))
            continue
        request, snapshot = decoded
        control_predecessor = identity.get("preflight_repair_of_experiment_id")
        predecessor = snapshot["arguments"].get("replaces_experiment_id")
        if control_predecessor is not None:
            previous = next((row for row in creations if row["workflow_id"] == control_predecessor), None)
            prior_identity = {} if previous is None else payloads.get(_row_identifier(previous), {})
            if (control_predecessor != predecessor or control_predecessor not in declined
                    or identity.get("revision_of_outcome_id") is not None
                    or any(identity.get(key) != prior_identity.get(key) for key in ("execution_node_command_id", "hypothesis_id", "role", "component_id", "expert_cycle"))
                    or identity.get("experiment_ordinal") != prior_identity.get("experiment_ordinal", -1) + 1):
                issues.append(OracleIssue("preflight_repair_source_invalid", "control repair must replace the exact immediate declined submission within one execution chain", (str(creation["workflow_id"]), str(control_predecessor))))
        elif predecessor is not None and identity.get("revision_of_outcome_id") is None:
            issues.append(OracleIssue("experiment_repair_source_invalid", "repair without a canonical predecessor must name its declined control predecessor", (str(creation["workflow_id"]),)))
        result = json.loads(command["result_json"])
        if (identity.get("source_submit_command_id") != command["command_id"]
                or identity.get("execution_node_command_id") != request["command_id"]
                or identity.get("source_agent_attempt_id") != request["attempt_id"]
                or identity.get("role") != request["role"] or identity.get("component_id") != request["component_id"]
                or identity.get("expert_cycle") != request["task_context"]["key"]["cycle"]
                or result.get("exp_id") != creation["workflow_id"] or command["workflow_id"] != creation["workflow_id"]
                or command["accepted_revision"] != 1):
            issues.append(OracleIssue("experiment_submit_creation_mismatch", "submit result and CREATE transition disagree on experiment or chain ownership", (str(creation["workflow_id"]), str(command["command_id"]))))
    for command in commands:
        try: data = json.loads(command["request_json"])
        except (TypeError, ValueError): continue
        if data.get("tool") != "submit_experiment": continue
        matches = [row for row in creations if row["command_id"] == command["command_id"]]
        if len(matches) != 1:
            issues.append(OracleIssue("experiment_submit_effect_count", "accepted submit must create exactly one Experiment", (str(command["command_id"]),)))


def _measurement_declarations_match(left: Any, right: Any) -> bool:
    if left is None or right is None: return left is right
    try: return ScientificMeasurement.from_dict(left) == ScientificMeasurement.from_dict(right)
    except (TypeError, ValueError): return False


def _audit_measurement_source(fact: Mapping[str, Any], payload: Mapping[str, Any], candidate: Mapping[str, Any],
        workspace: Mapping[str, bytes] | None, document: Any, facts: Sequence[Mapping[str, Any]],
        payloads: Mapping[str, Mapping[str, Any]], commands: Mapping[str, Mapping[str, Any]],
        attempts: Sequence[Mapping[str, Any]], observations: Mapping[str, Mapping[str, Any]], issues: list[OracleIssue]) -> None:
    try:
        declaration = ScientificMeasurement.from_dict(payload["measurement"])
        hypothesis = next(row for row in facts if row["fact_type"] == "HypothesisProposed" and row["hypothesis_id"] == fact["hypothesis_id"])
        component = next(item for item in document["components"] if item["id"] == payload["component_id"])
        constraint = component["result"]
        valid = (document["schema_version"] in {2, 3} and constraint.get("deferred") is True
            and payloads[str(hypothesis["fact_id"])].get("measurement") == payload["measurement"]
            and _measurement_declarations_match(payload["measurement"], candidate.get("measurement"))
            and payload["metric"] == declaration.to_dict()["metric"]
            and isinstance(candidate.get("metrics"), list) and len(candidate["metrics"]) == 1
            and candidate["metrics"][0].get("name") == declaration.metric.name
            and candidate["metrics"][0].get("direction") == declaration.metric.direction.value
            and _normalize_decimal_text(candidate["metrics"][0].get("target"), "candidate metric target") == declaration.metric.target
            and constraint.get("metric", declaration.metric.name) == declaration.metric.name
            and constraint.get("direction", declaration.metric.direction.value) == declaration.metric.direction.value
            and payload["evaluator_version"] in {"component-result/8", "component-result/9"}
            and workspace is not None and all(workspace.get(path) for path in declaration.parser_files))
        if not valid: raise ValueError("hypothesis, candidate, preregistration or operator constraints disagree")
        first = next(row for row in facts if row["fact_type"] == "ExperimentPreregistered" and row["hypothesis_id"] == fact["hypothesis_id"])
        first_payload = payloads[str(first["fact_id"])]
        first_command = commands[str(first_payload["execution_contract"]["source_submit_command_id"])]
        first_source = _raw_submit_source(first_command, attempts, observations)
        first_files = None if first_source is None else _decode_workspace(first_source[1])
        if (first_payload.get("measurement") != payload["measurement"] or first_files is None
                or any(first_files.get(path) != workspace.get(path) for path in declaration.parser_files)):
            issues.append(OracleIssue("measurement_frozen_source_mismatch", "same-hypothesis repair changed its first preregistered declaration or parser bytes", (str(fact["fact_id"]), str(first["fact_id"]))))
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        issues.append(OracleIssue("measurement_declaration_mismatch", str(exc), (str(fact["fact_id"]),)))


def _measurement_process_requests(request: Any, context: Mapping[str, Any], attempt_id: str,
                                  issues: list[OracleIssue]) -> Mapping[str, Any] | None:
    """Audit the one-attempt two-phase ABI independently of its driver producer."""
    raw = None
    try:
        if context["payload"].get("goal_evaluation") is not None:
            from .shared_acceptance_oracle import audit_parser_process_requests
            plan = json.loads(request["argv"][3])
            if not all(_valid_process_request(value) for value in plan.values() if value is not None):
                raise ValueError("shared driver contains a malformed internal ProcessRequest")
            return audit_parser_process_requests(request, context["payload"])
        outer = request["argv"]
        if len(outer) != 4 or outer[1:3] != ["-m", "hypoloom.measurement_driver"]:
            raise ValueError("measurement Process lacks the two-phase driver")
        plan = json.loads(outer[3]); raw, parser = plan["raw_request"], plan["parser_request"]
        if set(plan) != {"raw_request", "parser_request"} or not _valid_process_request(raw) or not _valid_process_request(parser):
            raise ValueError("measurement driver requests are malformed")
        source_root = request["env"].get("PYTHONPATH")
        if (not isinstance(source_root, str) or not PurePosixPath(source_root).is_absolute()
                or str(PurePosixPath(source_root)) != source_root or ".." in PurePosixPath(source_root).parts
                or ":" in source_root):
            raise ValueError("measurement Process lacks one recorded absolute runtime source")
        package = PurePosixPath(source_root) / "hypoloom"
        valid = (Path(outer[0]).is_absolute() and Path(outer[0]).name.startswith("python")
            and request["env"] == {"PYTHONPATH": str(package.parent), "PYTHONDONTWRITEBYTECODE": "1"}
            and raw["attempt_id"] == attempt_id and parser["attempt_id"] == attempt_id + "--parser"
            and raw["attempt_ordinal"] == request["attempt_ordinal"] and parser["attempt_ordinal"] == 1
            and raw["execution_id"] == parser["execution_id"] == request["execution_id"]
            and raw["cwd"] == request["cwd"] and raw["timeout_seconds"] == request["timeout_seconds"]
            and raw["stdout_path"] == "raw-stdout.log" and raw["stderr_path"] == "raw-stderr.log"
            and request["stdout_path"] == request["stderr_path"].replace("stderr", "stdout") == "stdout.log")
        declaration = ScientificMeasurement.from_dict(context["payload"]["measurement"])
        base = PurePosixPath(request["cwd"]).parent
        parser_root = base / "measurement-parser"
        argv = parser["argv"]; raw_argv = raw["argv"]
        # The experiment bwrap's runtime binding is the only admitted interpreter root.
        runtime_bindings = [(raw_argv[index + 1], raw_argv[index + 2]) for index, word in enumerate(raw_argv[:-2]) if word == "--ro-bind" and raw_argv[index + 2] == "/runtime"]
        if len(runtime_bindings) != 1: raise ValueError("raw runtime is ambiguous")
        prefix = runtime_bindings[0][0]
        timeout = min(30.0, float(request["timeout_seconds"]))
        if (parser["cwd"] != str(parser_root / "logs") or parser["env"] != {}
                or parser["timeout_seconds"] != timeout or parser["stdout_path"] != "stdout.log" or parser["stderr_path"] != "stderr.log"):
            valid = False
        interpreter = argv[-4]
        if not interpreter.startswith("/runtime/bin/python"): valid = False
        expected = [raw_argv[0], "--unshare-all", "--die-with-parent", "--new-session", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/runtime", "--ro-bind", prefix, "/runtime"]
        for path in ("/lib", "/lib64", "/usr/lib", "/usr/bin", "/etc/ld.so.cache"):
            if Path(path).exists(): expected.extend(("--ro-bind", path, path))
        expected.extend(("--ro-bind", "/usr/bin", "/bin", "--ro-bind", str(parser_root / "input"), "/input", "--bind", str(parser_root / "parsed"), "/parsed", "--dir", "/raw"))
        directories = {"/raw"}
        for relative in declaration.raw_artifacts:
            destination = PurePosixPath("/raw") / relative
            for parent in reversed(destination.parents):
                if str(parent) not in {"/", *directories}:
                    expected.extend(("--dir", str(parent))); directories.add(str(parent))
            source = PurePosixPath(request["cwd"]) / ("raw-" + relative) if relative in {"stdout.log", "stderr.log"} else base / "output" / relative
            expected.extend(("--ro-bind", str(source), str(destination)))
        expected.extend(("--ro-bind", str(package / "measurement_driver.py"), "/measurement-driver.py"))
        environment = {"HOME": "/nonexistent", "PATH": "/runtime/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "TMPDIR": "/tmp", "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "HYPOLOOM_MEASUREMENT_RAW_ROOT": "/raw", "HYPOLOOM_MEASUREMENT_RESULT_PATH": "/parsed/result.json"}
        for name, value in sorted(environment.items()): expected.extend(("--setenv", name, value) if value else ("--unsetenv", name))
        command = list(declaration.parser_argv)
        if command[0] == "python": command[0] = interpreter
        command = ["/input/" + word if word in declaration.parser_files else word for word in command]
        limits = {"argv": command, "timeout_seconds": timeout, "max_output_bytes": 1024 * 1024, "max_memory_bytes": 2 * 1024**3}
        expected.extend(("--chdir", "/parsed", "--", interpreter, "/measurement-driver.py", "--parser-limits", json.dumps(limits, sort_keys=True, separators=(",", ":"))))
        if not valid or argv != expected: raise ValueError("two-phase identity, mounts, CPU limits, or argv differ from the preregistration")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        issues.append(OracleIssue("measurement_process_binding_mismatch", str(exc), (attempt_id,)))
    return raw


def _audit_execution_integrity(
    artifact_root: Path,
    facts: Sequence[sqlite3.Row],
    fact_payloads: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    commands: Sequence[sqlite3.Row],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
    *, research_contract_version: str | None = None,
) -> None:
    attempts_by_id = {str(row["attempt_id"]): row for row in attempts}
    commands_by_id = {str(row["command_id"]): row for row in commands}
    goal_documents = {
        str(row["run_id"]): fact_payloads.get(str(row["fact_id"]), {}).get("research_contract")
        for row in facts if row["fact_type"] == "GoalDefined"
    }
    pool_bindings = {
        str(row["run_id"]): transition_payloads.get(_row_identifier(row), {}).get("gpu_pool")
        for row in transitions if research_contract_version in {"hypoloom-research-v11", "hypoloom-research-v12"}
        and row["workflow_kind"] == "RUN" and row["revision"] == 1
    }
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    learning_context = {"facts": facts, "fact_payloads": fact_payloads, "attempts": attempts,
        "observations": attempt_payloads, "commands": commands, "transitions": transitions}
    for fact in (row for row in facts if row["fact_type"] == "ExperimentPreregistered"):
        fact_id = str(fact["fact_id"])
        payload = fact_payloads.get(fact_id, {})
        contract = payload.get("execution_contract")
        if not isinstance(contract, Mapping):
            issues.append(
                OracleIssue(
                    "missing_execution_contract",
                    "ExperimentPreregistered lacks its execution_contract",
                    (fact_id,),
                )
            )
            continue
        source_id = contract.get("source_agent_attempt_id")
        source_digest = contract.get("source_agent_output_digest")
        submit_id = contract.get("source_submit_command_id")
        submit_command = commands_by_id.get(str(submit_id)) if submit_id is not None else None
        submitted = None if submit_command is None else _raw_submit_source(submit_command, attempts, attempt_payloads)
        argv = contract.get("argv")
        component_v4 = (
            int(fact["schema_version"]) in {4, 5}
            and payload.get("schema_version") == int(fact["schema_version"])
        )
        execution: _OracleComponentExecution | None = None
        component_mounts: _OracleComponentMounts | None = None
        document = goal_documents.get(str(fact["run_id"]))
        if component_v4:
            grant = contract.get("resource_intent", contract.get("resource_grant"))
            grant_kind = grant.get("kind") if isinstance(grant, Mapping) else None
            device = "cuda" if grant_kind == "GPU" else "cpu" if grant_kind == "CPU" else None
            try:
                patch_digest = _oracle_implementation_sha256(
                    document,
                    payload.get("component_id"),
                    payload.get("method_family_id"),
                    fact,
                    facts,
                    fact_payloads,
                    artifacts,
                    artifact_root,
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                patch_digest = None
            if patch_digest is not None and device is not None:
                execution = _oracle_component_execution(
                    document,
                    payload.get("component_id"),
                    method_family_id=payload.get("method_family_id"),
                    implementation_sha256=patch_digest,
                    device=device,
                )
            source_path = (
                execution.source_relative_path
                if execution is not None
                else _oracle_component_source_path(
                    document, payload.get("component_id")
                )
            )
            expected_argv = None if execution is None else execution.argv
            if (payload.get("measurement") is not None or payload.get("goal_evaluation") is not None) and submitted is not None:
                declaration = submitted[1]["arguments"]["candidate"].get("measurement") or {}
                generic = submitted[1]["arguments"]["candidate"].get("execution_argv")
                expected_argv = tuple(generic) if isinstance(generic, list) else None
                source_path = next((word.removeprefix("/input/") for word in generic or () if isinstance(word, str) and word.startswith("/input/")),
                    next(iter(declaration.get("parser_files", ())), None))
            goal_evaluation = payload.get("goal_evaluation")
            generic_target = execution is not None and execution.target_root is not None and (
                payload.get("measurement") is not None
                or isinstance(goal_evaluation, Mapping) and goal_evaluation.get("scope") == "GOAL"
            )
            if execution is not None and (
                execution.source_relative_path == ".hypoloom/trial_driver.py" or generic_target
            ):
                try:
                    component_mounts = _oracle_component_mounts(
                        document,
                        payload.get("component_id"),
                        execution,
                        fact,
                        payload,
                        facts,
                        fact_payloads,
                        artifacts,
                        artifact_root,
                        transition_payloads,
                    )
                except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                    component_mounts = None
        else:
            generic_target = False
            expected_argv = tuple(argv) if isinstance(argv, list) else None
            source_path = (
                _safe_relative_path(argv[1])
                if isinstance(argv, list) and len(argv) == 2
                else None
            )
        if not isinstance(source_id, str) or not isinstance(source_digest, str) or source_path is None:
            issues.append(
                OracleIssue(
                    "invalid_execution_contract",
                    "execution_contract lacks usable source identity, digest, or argv",
                    (fact_id,),
                )
            )
        environment_matches = (
            component_mounts is not None
            and _oracle_component_environment_matches(
                contract.get("environment"), component_mounts
            )
        )
        host_owned = source_path == ".hypoloom/trial_driver.py"
        if submitted is not None and not host_owned:
            supplied_command = submitted[1]["arguments"].get("command")
            if supplied_command is not None:
                import shlex
                expected_argv = tuple(shlex.split(supplied_command) if isinstance(supplied_command, str) else supplied_command)
        if component_v4 and (
            expected_argv is None
            or argv != list(expected_argv)
            or ((host_owned or generic_target) and not environment_matches)
        ):
            issues.append(OracleIssue(
                "component_execution_contract_mismatch",
                "execution_contract command, identity, or input environment is not independently derivable from GoalDefined and accepted publications",
                (fact_id,),
            ))
        context: dict[str, Any] = {
            "fact": fact,
            "payload": payload,
            "contract": contract,
            "component_v4": component_v4,
            "source_path": source_path,
            "execution_argv": expected_argv,
            "component_mounts": component_mounts,
            "host_owned_source": host_owned,
            "workspace": {},
        }
        contexts[(str(fact["experiment_id"]), str(fact["execution_id"]))] = context
        source = attempts_by_id.get(str(source_id))
        if source is None:
            issues.append(
                OracleIssue(
                    "source_agent_attempt_missing",
                    "execution_contract source Agent attempt is absent",
                    (fact_id, str(source_id)),
                )
            )
            continue
        context["source"] = source
        if submit_id is not None:
            if submitted is None or submit_command["workflow_id"] != fact["experiment_id"]:
                issues.append(OracleIssue("execution_submit_source_invalid", "preregistration does not bind its exact accepted submit", (fact_id, str(submit_id))))
                continue
            request, response = submitted
            workspace = _decode_workspace(response)
            candidate = response["arguments"]["candidate"]
            accepted = [row for row in transitions if row["fact_id"] == fact["fact_id"]]
            accepted_payload = {} if len(accepted) != 1 else transition_payloads.get(_row_identifier(accepted[0]), {})
            if (request["attempt_id"] != source_id or response["output_digest"] != source_digest
                    or candidate.get("component_id") != payload.get("component_id")
                    or candidate.get("method_family_id") != payload.get("method_family_id")
                    or candidate.get("evaluator_version") != payload.get("evaluator_version")
                    or candidate.get("method") != payload.get("method")
                    or candidate.get("acceptance_criteria") != payload.get("acceptance_criteria")
                    or not _measurement_declarations_match(candidate.get("measurement"), payload.get("measurement"))
                    or (payload.get("measurement") is not None or payload.get("goal_evaluation") is not None) and candidate.get("execution_argv") != argv
                    or payload.get("goal_evaluation") is not None and candidate.get("evaluation_scope") != payload["goal_evaluation"].get("scope")
                    or accepted_payload.get("source_submit_command_id") != submit_id
                    or accepted_payload.get("agent_attempt_id") != source_id
                    or accepted_payload.get("agent_output_digest") != source_digest):
                issues.append(OracleIssue("execution_submit_contract_mismatch", "submitted candidate, preregistration and accepting transition disagree", (fact_id, str(submit_id))))
            if (workspace is None or source_path is None or (not host_owned and source_path not in workspace)
                    or _oracle_uses_submitted_target(document, payload.get("component_id"), request, research_contract_version)
                    and "implementation.patch" not in workspace):
                issues.append(OracleIssue("source_agent_workspace_invalid", "submitted workspace lacks its declared program or target source", (fact_id, str(submit_id))))
            else: context["workspace"] = workspace
            context.update(request=request, response=response)
            if payload.get("measurement") is not None:
                _audit_measurement_source(fact, payload, candidate, workspace, document, facts, fact_payloads,
                    commands_by_id, attempts, attempt_payloads, issues)
            budget = document.get("budget", {}) if isinstance(document, Mapping) else {}
            context["resource"] = _audit_resource_contract(fact_id, payload, contract, request, response, budget, issues,
                cpu_only_artifact=_oracle_cpu_only_artifact(document, payload), document=document,
                research_contract_version=research_contract_version, pool_binding=pool_bindings.get(str(fact["run_id"])),
                learning_context=learning_context)
            continue
        if (
            source["kind"] != "AGENT"
            or source["state"] != "CLOSED"
            or source["terminal_reason"] != "SUCCEEDED"
        ):
            issues.append(
                OracleIssue(
                    "source_agent_attempt_ineligible",
                    "execution source is not a CLOSED SUCCEEDED AGENT attempt",
                    (fact_id, str(source["attempt_id"])),
                )
            )
        observation = attempt_payloads.get(str(source["attempt_id"]))
        request = observation.get("request") if isinstance(observation, Mapping) else None
        response = observation.get("response") if isinstance(observation, Mapping) else None
        if not isinstance(request, Mapping) or not isinstance(response, Mapping):
            issues.append(
                OracleIssue(
                    "source_agent_observation_invalid",
                    "source Agent observation lacks request and response objects",
                    (fact_id, str(source["attempt_id"])),
                )
            )
            continue
        context.update(request=request, response=response)
        budget = document.get("budget", {}) if isinstance(document, Mapping) else {}
        context["resource"] = _audit_resource_contract(
            fact_id, payload, contract, request, response, budget, issues,
            cpu_only_artifact=_oracle_cpu_only_artifact(document, payload),
            document=document,
            research_contract_version=research_contract_version,
            pool_binding=pool_bindings.get(str(fact["run_id"])),
            learning_context=learning_context,
        )
        expected = {
            "run_id": fact["run_id"],
            "owner_workflow_id": fact["experiment_id"],
            "command_id": source["command_id"],
            "attempt_id": source["attempt_id"],
            "attempt_ordinal": source["attempt_ordinal"],
        }
        response_identity = {
            "command": request.get("command"),
            "command_id": request.get("command_id"),
            "attempt_id": request.get("attempt_id"),
            "attempt_ordinal": request.get("attempt_ordinal"),
        }
        if (
            source["run_id"] != fact["run_id"]
            or source["workflow_id"] != fact["experiment_id"]
            or request.get("command") not in {
                "DRAFT_EXPERIMENT_SPEC", "EXPERIMENT_DESIGN_IMPLEMENT",
            }
            or any(request.get(name) != value for name, value in expected.items())
            or any(response.get(name) != value for name, value in response_identity.items())
            or response.get("status") != "SUCCEEDED"
        ):
            issues.append(
                OracleIssue(
                    "source_agent_identity_mismatch",
                    "Agent row, request, response, and preregistration identities differ",
                    (fact_id, str(source["attempt_id"])),
                )
            )
        request_digest = _canonical_json_digest(request)
        if request_digest is None or response.get("input_digest") != request_digest:
            issues.append(
                OracleIssue(
                    "source_agent_request_digest_mismatch",
                    "Agent response input_digest does not bind its request",
                    (fact_id, str(source["attempt_id"])),
                )
            )
        response_without_digest = {
            name: value for name, value in response.items() if name != "output_digest"
        }
        response_digest = _canonical_json_digest(response_without_digest)
        if response_digest is None or response.get("output_digest") != response_digest:
            issues.append(
                OracleIssue(
                    "source_agent_response_digest_mismatch",
                    "Agent output_digest does not bind its response",
                    (fact_id, str(source["attempt_id"])),
                )
            )
        if response.get("output_digest") != source_digest:
            issues.append(
                OracleIssue(
                    "source_agent_contract_digest_mismatch",
                    "execution_contract output digest differs from the committed response",
                    (fact_id, str(source["attempt_id"])),
                )
            )
        workspace = _decode_workspace(response)
        if (
            workspace is None
            or source_path is None
            or (host_owned and source_path in workspace)
            or (not host_owned and source_path not in workspace)
        ):
            issues.append(
                OracleIssue(
                    "source_agent_workspace_invalid",
                    "Agent response workspace is malformed or violates the Host/Agent source ownership boundary",
                    (fact_id, str(source["attempt_id"])),
                )
            )
        else:
            context["workspace"] = workspace

    commands_by_id = {str(row["command_id"]): row for row in commands}
    intent_rows: dict[str, list[tuple[sqlite3.Row, Mapping[str, Any]]]] = {}
    validation_observation_rows: dict[
        str, list[tuple[sqlite3.Row, Mapping[str, Any]]]
    ] = {}
    for transition in transitions:
        payload = transition_payloads.get(_row_identifier(transition))
        command = commands_by_id.get(str(_row_get(transition, "command_id")))
        command_type = None if command is None else _row_get(command, "command_type")
        if not isinstance(payload, Mapping):
            continue
        if command_type == "OBSERVE_SPEC_VALIDATION":
            attempt_id = payload.get("attempt_id")
            if isinstance(attempt_id, str):
                validation_observation_rows.setdefault(attempt_id, []).append(
                    (transition, payload)
                )
            continue
        if not (
            transition["state"] == "EXECUTING"
            or (
                command_type == "VALIDATE_SPEC_CANDIDATE"
                and transition["state"] == "DRAFTED"
            )
        ):
            continue
        process_request = payload.get("process_request")
        identities = {payload.get("attempt_id")}
        if isinstance(process_request, Mapping):
            identities.add(process_request.get("attempt_id"))
        for attempt_id in identities:
            if isinstance(attempt_id, str):
                intent_rows.setdefault(attempt_id, []).append((transition, payload))
    for attempt in (row for row in attempts if row["kind"] == "PROCESS"):
        attempt_id = str(attempt["attempt_id"])
        candidates = intent_rows.get(attempt_id, [])
        if not candidates:
            observation = attempt_payloads.get(attempt_id, {})
            if isinstance(observation, Mapping) and observation.get("synthetic_before_spawn") is True:
                _audit_pre_spawn_cancel(attempt, observation, commands_by_id, transitions,
                    transition_payloads, contexts.get((str(attempt["experiment_id"]), str(attempt["execution_id"]))), issues)
                continue
            issues.append(
                OracleIssue(
                    "process_intent_missing",
                    "PROCESS attempt has no persisted execution intent",
                    (attempt_id,),
                )
            )
            continue
        if len(candidates) > 1:
            issues.append(
                OracleIssue(
                    "process_intent_duplicate",
                    "PROCESS attempt has multiple persisted execution intents",
                    (attempt_id,),
                )
            )
        transition, intent = candidates[0]
        command = commands_by_id.get(str(_row_get(transition, "command_id")))
        command_type = None if command is None else _row_get(command, "command_type")
        validation = command_type == "VALIDATE_SPEC_CANDIDATE"
        if command_type not in {
            "EXECUTE",
            "OPEN_RETRY_ATTEMPT",
            "VALIDATE_SPEC_CANDIDATE",
        }:
            issues.append(
                OracleIssue(
                    "process_intent_command_mismatch",
                    "PROCESS execution intent is not owned by a declared process command",
                    (attempt_id, str(command_type)),
                )
            )
        request = intent.get("process_request")
        request_valid = _valid_process_request(request)
        if not request_valid:
            issues.append(
                OracleIssue(
                    "process_request_invalid",
                    "execution intent does not contain a complete ProcessRequest",
                    (attempt_id,),
                )
            )
        context = contexts.get((str(attempt["experiment_id"]), str(attempt["execution_id"])))
        if context is not None and isinstance(context.get("resource"), Mapping) and context["resource"].get("shared"):
            context = {**context, "resource": _audit_shared_process_resource(
                context["resource"], intent, attempt, attempt_payloads.get(attempt_id, {}), issues)}
        observation = attempt_payloads.get(attempt_id, {})
        if isinstance(observation, Mapping) and "resource_wait_before_exec" in observation:
            resource = None if context is None else context.get("resource")
            if not (research_contract_version in {"hypoloom-research-v11", "hypoloom-research-v12"}
                    and isinstance(resource, Mapping) and resource.get("shared") is True
                    and resource.get("source_valid") is True and resource.get("expected_kind") == "GPU"
                    and observation["resource_wait_before_exec"] is True
                    and attempt["state"] == "CLOSED" and attempt["terminal_reason"] == "LOST"
                    and observation.get("terminal_reason") == "LOST" and observation.get("absence_proven") is True
                    and observation.get("synthetic_before_spawn") is not True
                    and not any(str(row["attempt_id"]) == attempt_id for row in artifacts)):
                issues.append(OracleIssue("resource_wait_proof_invalid",
                    "pre-exec resource wait must be a shared GPU LOST attempt with proven absence and no publication",
                    (attempt_id,)))
        request_identity = {
            "attempt_id": attempt["attempt_id"],
            "attempt_ordinal": attempt["attempt_ordinal"],
            "execution_id": attempt["execution_id"],
        }
        if (
            not isinstance(request, Mapping)
            or intent.get("attempt_id") != attempt["attempt_id"]
            or transition["run_id"] != attempt["run_id"]
            or transition["workflow_id"] != attempt["workflow_id"]
            or any(request.get(name) != value for name, value in request_identity.items())
        ):
            issues.append(
                OracleIssue(
                    "process_intent_identity_mismatch",
                    "PROCESS row, transition, and ProcessRequest identities differ",
                    (attempt_id,),
                )
            )
        if not validation:
            if (
                context is None
                or intent.get("spec_fact_id") != context["fact"]["fact_id"]
            ):
                issues.append(
                    OracleIssue(
                        "process_intent_spec_mismatch",
                        "PROCESS intent does not bind its ExperimentPreregistered fact",
                        (attempt_id,),
                    )
                )
            elif _process_request_differs_from_contract(request, intent, context):
                issues.append(
                    OracleIssue(
                        "process_intent_spec_mismatch",
                        "ProcessRequest differs from its frozen execution contract",
                        (attempt_id,),
                    )
                )
        if validation:
            source_id = intent.get("agent_attempt_id")
            source = attempts_by_id.get(str(source_id))
            source_observation = attempt_payloads.get(str(source_id), {})
            source_request = (
                source_observation.get("request")
                if isinstance(source_observation, Mapping)
                else None
            )
            source_response = (
                source_observation.get("response")
                if isinstance(source_observation, Mapping)
                else None
            )
            if not (
                source is not None
                and source["kind"] == "AGENT"
                and source["state"] == "CLOSED"
                and source["terminal_reason"] == "SUCCEEDED"
                and source["run_id"] == attempt["run_id"]
                and source["workflow_id"] == attempt["workflow_id"]
                and isinstance(source_request, Mapping)
                and source_request.get("command") in {
                    "DRAFT_EXPERIMENT_SPEC", "EXPERIMENT_DESIGN_IMPLEMENT",
                }
                and source_request.get("component_id")
                == intent.get("component_id")
                and source_request.get("role") == intent.get("role")
                and isinstance(source_response, Mapping)
                and source_response.get("output_digest")
                == intent.get("agent_output_digest")
            ):
                issues.append(
                    OracleIssue(
                        "process_intent_source_mismatch",
                        "candidate validation intent differs from its successful DRAFT Agent source",
                        (attempt_id, str(source_id)),
                    )
                )
        elif context is not None:
            source = context.get("source")
            if (
                source is None
                or intent.get("agent_attempt_id") != source["attempt_id"]
                or intent.get("agent_output_digest")
                != context["contract"].get("source_agent_output_digest")
                or intent.get("source_relative_path") != context.get("source_path")
                or intent.get("source_submit_command_id") != context["contract"].get("source_submit_command_id")
            ):
                issues.append(
                    OracleIssue(
                        "process_intent_source_mismatch",
                        "PROCESS intent differs from its committed Agent source",
                        (attempt_id,),
                    )
                )
            resource_request = request
            if context["payload"].get("measurement") is not None or context["payload"].get("goal_evaluation") is not None:
                resource_request = _measurement_process_requests(request, context, attempt_id, issues)
            _audit_process_resource(
                resource_request,
                context.get("resource"),
                attempt_id,
                context.get("source_path"),
                context.get("execution_argv"),
                context.get("component_mounts"),
                issues,
                allow_generic=context["payload"].get("measurement") is not None or context["payload"].get("goal_evaluation") is not None,
            )
        if validation and attempt["state"] == "CLOSED":
            observations = validation_observation_rows.get(attempt_id, [])
            valid_observation = len(observations) == 1
            if valid_observation:
                observation_transition, observation_payload = observations[0]
                observed_source_id = observation_payload.get("agent_attempt_id")
                observed_component_id = observation_payload.get("component_id")
                valid_observation = (
                    observation_transition["run_id"] == attempt["run_id"]
                    and observation_transition["workflow_id"]
                    == attempt["workflow_id"]
                    and observation_transition["state"] == "DRAFTED"
                    and int(observation_transition["revision"])
                    > int(transition["revision"])
                    and observation_payload.get("validation_succeeded")
                    is (attempt["terminal_reason"] == "SUCCEEDED")
                    and (
                        observed_source_id is None
                        or observed_source_id == intent.get("agent_attempt_id")
                    )
                    and (
                        observed_component_id is None
                        or observed_component_id == intent.get("component_id")
                    )
                )
            if not valid_observation:
                issues.append(
                    OracleIssue(
                        "process_validation_observation_mismatch",
                        "terminal candidate validation lacks one matching OBSERVE_SPEC_VALIDATION transition",
                        (
                            attempt_id,
                            *(str(_row_identifier(row)) for row, _ in observations),
                        ),
                    )
                )
        if attempt["state"] != "CLOSED":
            continue
        observation = attempt_payloads.get(attempt_id)
        observed_request = (
            observation.get("process_request") if isinstance(observation, Mapping) else None
        )
        if not isinstance(observation, Mapping) or not isinstance(observed_request, Mapping):
            issues.append(
                OracleIssue(
                    "process_observation_missing",
                    "closed PROCESS attempt lacks a terminal observation and ProcessRequest",
                    (attempt_id,),
                )
            )
            continue
        if request_valid and not _valid_process_request(observed_request):
            issues.append(
                OracleIssue(
                    "process_request_invalid",
                    "terminal observation does not contain a complete ProcessRequest",
                    (attempt_id,),
                )
            )
        if (
            any(observation.get(name) != value for name, value in request_identity.items())
            or observation.get("terminal_reason") != attempt["terminal_reason"]
        ):
            issues.append(
                OracleIssue(
                    "process_observation_identity_mismatch",
                    "terminal observation differs from its PROCESS attempt row",
                    (attempt_id,),
                )
            )
        if not isinstance(request, Mapping) or dict(observed_request) != dict(request):
            issues.append(
                OracleIssue(
                    "process_request_mismatch",
                    "terminal observation ProcessRequest differs from execution intent",
                    (attempt_id,),
                )
            )
        if validation:
            source_id = intent.get("agent_attempt_id")
            source_digest = intent.get("agent_output_digest")
            source_fields_match = (
                observation.get("source_agent_attempt_id") == source_id
                and (
                    attempt["terminal_reason"] == "LOST"
                    or observation.get("source_agent_output_digest")
                    == source_digest
                )
            )
            if not source_fields_match:
                issues.append(
                    OracleIssue(
                        "process_observation_identity_mismatch",
                        "candidate validation observation differs from its DRAFT Agent source",
                        (attempt_id, str(source_id)),
                    )
                )
        if context is not None:
            _audit_process_resource_observation(
                observation, context.get("resource"), attempt_id, issues
            )

    for artifact in artifacts:
        attempt = attempts_by_id.get(str(artifact["attempt_id"]))
        if attempt is None or attempt["kind"] != "PROCESS":
            continue
        context = contexts.get((str(attempt["experiment_id"]), str(attempt["execution_id"])))
        if context is None or context.get("source_path") is None:
            continue
        if context.get("component_v4") is True:
            continue
        source_path = context["source_path"]
        expected_source = context["workspace"].get(source_path)
        stable_relative = _artifact_stable_relative_path(artifact)
        if stable_relative is None:
            continue
        artifact_path = artifact_root.resolve(strict=False).joinpath(*PurePosixPath(stable_relative).parts)
        if str(artifact["path"]) != stable_relative:
            continue
        source_file = artifact_path.joinpath(*PurePosixPath(source_path).parts)
        if (
            expected_source is None
            or artifact_path.is_symlink()
            or not source_file.is_file()
            or source_file.is_symlink()
        ):
            issues.append(
                OracleIssue(
                    "artifact_source_missing",
                    "canonical artifact lacks the committed Agent source file",
                    (str(artifact["artifact_id"]), source_path),
                )
            )
            continue
        try:
            matches = _regular_file_equals(source_file, expected_source)
        except OSError as exc:
            issues.append(
                OracleIssue(
                    "artifact_source_missing",
                    str(exc),
                    (str(artifact["artifact_id"]), source_path),
                )
            )
        else:
            if not matches:
                issues.append(
                    OracleIssue(
                        "artifact_source_bytes_mismatch",
                        "canonical source bytes differ from the committed Agent response",
                        (str(artifact["artifact_id"]), source_path),
                    )
                )


def _audit_owned_workspace_residue(
    artifact_root: Path,
    attempts: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
) -> None:
    for attempt in attempts:
        if attempt["state"] == "OPEN":
            continue  # The owner still needs its workspace for execution/collection.
        attempt_id = str(attempt["attempt_id"])
        if _safe_path_component(attempt_id) is None:
            issues.append(
                OracleIssue(
                    "owned_work_residue",
                    "PROCESS attempt_id is unsafe for owned-path inspection",
                    (attempt_id,),
                )
            )
            continue
        work = artifact_root / "work" / attempt_id
        if work.exists() or work.is_symlink():
            issues.append(
                OracleIssue(
                    "owned_work_residue",
                    "owned disposable attempt workspace remains",
                    (attempt_id, str(work)),
                )
            )
        if attempt["kind"] != "PROCESS":
            continue
        staging = artifact_root / "result-staging" / attempt_id
        if not staging.exists() and not staging.is_symlink():
            continue
        invalid = _unexpected_staging_residue(staging)
        if invalid:
            issues.append(
                OracleIssue(
                    "owned_staging_residue",
                    "owned result staging retains data other than bounded process logs",
                    (attempt_id, *invalid),
                )
            )


def _audit_pre_spawn_cancel(attempt, observation, commands, transitions, payloads, context, issues):
    """A queued cancellation has a real cancel cause, not an EXECUTE intent."""
    identity = str(attempt["attempt_id"])
    if context is not None and isinstance(context.get("resource"), Mapping) and context["resource"].get("shared"):
        context = {**context, "resource": _audit_shared_process_resource(context["resource"], {}, attempt, observation, issues)}
    commands = {key: dict(row) for key, row in commands.items()}
    try:
        revision = observation.get("resource_failure") == "capacity_requires_revision"
        assert attempt["state"] == "CLOSED" and attempt["terminal_reason"] == ("FAILED" if revision else "CANCELLED")
        assert attempt["attempt_ordinal"] == 1 and attempt["process_group_id"] is None
        assert observation.get("synthetic_before_spawn") is True and context is not None
        command = commands[str(attempt["command_id"])]
        assert command["command_type"] == "CANCEL" and command["response_status"] == "accepted"
        assert (command["run_id"], command["workflow_id"]) == (attempt["run_id"], attempt["workflow_id"])
        if revision:
            from .gpu_pool import assess_capacity
            diagnostic = {k: observation[k] for k in ("resource_failure", "capacity")}
            assert command["command_id"] == f"{attempt['workflow_id']}--resource-revision"
            assert json.loads(command["request_json"]) == diagnostic
            assert json.loads(command["result_json"]) == {"repair_required": True}
            capacity = diagnostic["capacity"]
            intent = context["contract"]["resource_intent"]
            assert intent["sharing"] in {"exclusive", "shared"}
            assert capacity["reason"] == "capacity_requires_revision"
            assert {k: v for k, v in capacity.items() if k != "observed_at"} == assess_capacity(capacity["devices"], intent)
        else:
            assert json.loads(command["request_json"]) == {"synthetic_cancel": True}
            assert json.loads(command["result_json"]) == {"attempt_id": identity}
        request = observation["process_request"]
        assert _valid_process_request(request)
        assert all(observation.get(key) == attempt[key] for key in ("attempt_id", "attempt_ordinal", "execution_id", "terminal_reason"))
        assert all(request.get(key) == attempt[key] for key in ("attempt_id", "attempt_ordinal", "execution_id"))
        assert request["timeout_seconds"] == context["contract"]["timeout_seconds"]
        cancelled = [(row, payloads.get(_row_identifier(row), {})) for row in transitions
            if (row["run_id"], row["workflow_id"], row["workflow_kind"], row["state"]) == (
                attempt["run_id"], attempt["workflow_id"], "EXPERIMENT", "EVALUATING")
            and commands.get(str(row["command_id"]), {}).get("command_type") == "CANCEL"]
        closed = [row for row, value in cancelled if value.get("attempt_id") == identity and value.get("synthetic") is True]
        assert len(closed) == 1
        if revision:
            assert closed[0]["previous_state"] in {"PREREGISTERED", "READY"}
        else:
            assert closed[0]["previous_state"] == "EVALUATING"
            assert any(row["revision"] < closed[0]["revision"] and row["previous_state"] in {"PREREGISTERED", "READY"}
                for row, _ in cancelled)
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        issues.append(OracleIssue("process_cancel_source_invalid", "Queued Process cancellation lacks its actual unspawned terminal/cancel source: " + str(exc), (identity,)))
        return
    resource_request = request
    if context["payload"].get("measurement") is not None or context["payload"].get("goal_evaluation") is not None:
        resource_request = _measurement_process_requests(request, context, identity, issues)
    _audit_process_resource(resource_request, context.get("resource"), identity, context.get("source_path"),
        context.get("execution_argv"), context.get("component_mounts"), issues,
        allow_generic=context["payload"].get("measurement") is not None or context["payload"].get("goal_evaluation") is not None)
    _audit_process_resource_observation(observation, context.get("resource"), identity, issues)


def _process_request_differs_from_contract(
    request: Any,
    intent: Mapping[str, Any],
    context: Mapping[str, Any],
) -> bool:
    if not isinstance(request, Mapping):
        return True
    contract = context["contract"]
    payload = context["payload"]
    if request.get("timeout_seconds") != contract.get("timeout_seconds"):
        return True
    if context.get("component_v4") is not True:
        requirements = payload.get("artifact_requirements")
        return (
            not isinstance(requirements, list)
            or intent.get("result_relative_path") not in requirements
        )
    raw_environment = contract.get("environment")
    environment = (
        dict(raw_environment)
        if isinstance(raw_environment, list)
        and all(
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(value, str) for value in item)
            for item in raw_environment
        )
        else {}
    )
    source_request = context.get("request")
    return (
        intent.get("component_id") != payload.get("component_id")
        or contract.get("argv") != list(context.get("execution_argv") or ())
        or not isinstance(source_request, Mapping)
        or intent.get("role") != source_request.get("role")
        or intent.get("result_relative_path")
        != environment.get("HYPOLOOM_RESULT_PATH")
        or environment.get("HYPOLOOM_RESULT_PATH") != "result.json"
    )


@dataclass(frozen=True, slots=True)
class _OracleComponentExecution:
    argv: tuple[str, ...]
    source_relative_path: str
    target_root: str | None
    data_bindings: tuple[tuple[str, str], ...] = ()
    control_target: bool = False


def _oracle_component_source_path(document: Any, component_id: Any) -> str | None:
    if not isinstance(document, Mapping) or not isinstance(component_id, str):
        return None
    components = document.get("components")
    delivery = document.get("delivery_contracts")
    protocols = delivery.get("protocols") if isinstance(delivery, Mapping) else None
    if not isinstance(components, list) or not isinstance(protocols, Mapping):
        return None
    component = next(
        (
            item
            for item in components
            if isinstance(item, Mapping) and item.get("id") == component_id
        ),
        None,
    )
    protocol_name = component.get("protocol") if isinstance(component, Mapping) else None
    delivery_protocol = protocols.get(protocol_name) if isinstance(protocol_name, str) else None
    if (
        isinstance(delivery_protocol, Mapping)
        and delivery_protocol.get("production") is True
        and delivery_protocol.get("execution_shape")
        in {
            "paired_control_treatment",
            "seeded_trials",
            "checkpoint_audit",
        }
    ):
        return ".hypoloom/trial_driver.py"
    return "experiment.py"


def _oracle_component_execution(
    document: Any,
    component_id: Any,
    *,
    method_family_id: Any,
    implementation_sha256: Any,
    device: Any,
) -> _OracleComponentExecution | None:
    """Independently project the Host command observable from GoalDefined."""

    if not isinstance(document, Mapping) or not isinstance(component_id, str):
        return None
    components = document.get("components")
    delivery = document.get("delivery_contracts")
    delivery_protocols = (
        delivery.get("protocols", {}) if isinstance(delivery, Mapping) else {}
    )
    if not isinstance(components, list) or not isinstance(delivery_protocols, Mapping):
        return None
    component = next(
        (
            item
            for item in components
            if isinstance(item, Mapping) and item.get("id") == component_id
        ),
        None,
    )
    protocol_name = component.get("protocol") if isinstance(component, Mapping) else None
    delivery_protocol = (
        delivery_protocols.get(protocol_name)
        if isinstance(protocol_name, str)
        else None
    )
    shape = (
        delivery_protocol.get("execution_shape")
        if isinstance(delivery_protocol, Mapping)
        and delivery_protocol.get("production") is True
        else None
    )
    if shape in {
        "paired_control_treatment",
        "seeded_trials",
        "checkpoint_audit",
    }:
        try:
            return _oracle_batch_component_execution(
                document,
                component_id,
                method_family_id=method_family_id,
                implementation_sha256=implementation_sha256,
                device=device,
            )
        except (TypeError, ValueError, OverflowError):
            return None
    argv = _oracle_component_execution_argv_legacy(document, component_id)
    if argv is None:
        return None
    target = document.get("runtime")
    target_root = target.get("target_repo") if isinstance(target, Mapping) else None
    return _OracleComponentExecution(
        argv=argv,
        source_relative_path="experiment.py",
        target_root=target_root if isinstance(target_root, str) else None,
    )


def _oracle_batch_component_execution(
    document: Mapping[str, Any],
    component_id: str,
    *,
    method_family_id: Any,
    implementation_sha256: Any,
    device: Any,
) -> _OracleComponentExecution:
    """Recompute the three production batch shapes without production helpers."""

    if not _trimmed_text(method_family_id):
        raise ValueError("invalid method family")
    if (
        not isinstance(implementation_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", implementation_sha256) is None
    ):
        raise ValueError("invalid implementation identity")
    if device not in {"cpu", "cuda"}:
        raise ValueError("invalid device")
    components, protocols = document.get("components"), document.get("protocols")
    delivery = document.get("delivery_contracts")
    delivery_protocols = (
        delivery.get("protocols", {}) if isinstance(delivery, Mapping) else {}
    )
    if (
        not isinstance(components, list)
        or not isinstance(protocols, Mapping)
        or not isinstance(delivery_protocols, Mapping)
    ):
        raise ValueError("invalid contract catalogs")
    component = next(
        (
            item
            for item in components
            if isinstance(item, Mapping) and item.get("id") == component_id
        ),
        None,
    )
    if not isinstance(component, Mapping):
        raise ValueError("unknown component")
    protocol_name = component.get("protocol")
    protocol = protocols.get(protocol_name) if isinstance(protocol_name, str) else None
    requirements = protocol.get("requirements") if isinstance(protocol, Mapping) else None
    delivery_protocol = (
        delivery_protocols.get(protocol_name)
        if isinstance(protocol_name, str)
        else None
    )
    capabilities = (
        delivery_protocol.get("capabilities")
        if isinstance(delivery_protocol, Mapping)
        else None
    )
    shape = (
        delivery_protocol.get("execution_shape")
        if isinstance(delivery_protocol, Mapping)
        else None
    )
    if (
        not isinstance(requirements, Mapping)
        or not isinstance(capabilities, Mapping)
        or delivery_protocol.get("production") is not True
        or shape
        not in {
            "paired_control_treatment",
            "seeded_trials",
            "checkpoint_audit",
        }
    ):
        raise ValueError("invalid batch protocol")
    runtime = document.get("runtime")
    target_root = runtime.get("target_repo") if isinstance(runtime, Mapping) else None
    if not isinstance(target_root, str) or not PurePosixPath(target_root).is_absolute():
        raise ValueError("invalid target root")
    entrypoints = [
        item
        for item in capabilities.values()
        if isinstance(item, Mapping) and item.get("role") == "entrypoint"
    ]
    if len(entrypoints) != 1:
        raise ValueError("invalid entrypoint")
    entrypoint_path, module = entrypoints[0].get("path"), entrypoints[0].get("module")
    if not _safe_contract_relative(entrypoint_path) or not _trimmed_text(module):
        raise ValueError("invalid entrypoint")

    selections = [
        item.get("selection")
        for item in capabilities.values()
        if isinstance(item, Mapping) and isinstance(item.get("selection"), Mapping)
    ]
    if len(selections) != 1:
        raise ValueError("invalid model selection")
    selection = selections[0]
    expected_models, model_flag = selection.get("expected_values"), selection.get("argv_flag")
    if (
        not isinstance(expected_models, list)
        or len(expected_models) != 1
        or not _trimmed_text(expected_models[0])
        or not isinstance(model_flag, str)
        or not model_flag.startswith("--")
    ):
        raise ValueError("invalid model selection")
    treatment = str(expected_models[0])
    control = selection.get("control_value")
    if shape == "paired_control_treatment" and not _trimmed_text(control):
        raise ValueError("invalid control")
    outputs = [
        item
        for item in capabilities.values()
        if isinstance(item, Mapping)
        and item.get("role") == "output"
        and isinstance(item.get("normalizer"), Mapping)
    ]
    if len(outputs) != 1:
        raise ValueError("invalid normalizer")
    normalizer = dict(outputs[0]["normalizer"])
    metric_paths = normalizer.get("metric_paths")
    candidate_resource = normalizer.get("candidate_manifest_resource")
    if (
        not isinstance(metric_paths, Mapping)
        or not metric_paths
        or any(
            not _trimmed_text(raw_path) or not _trimmed_text(canonical_name)
            for raw_path, canonical_name in metric_paths.items()
        )
        or not _trimmed_text(candidate_resource)
    ):
        raise ValueError("invalid normalizer")

    fixed: dict[str, str] = {}
    data_bindings: list[tuple[str, str]] = []
    raw_bindings = requirements.get("argv_bindings", [])
    if not isinstance(raw_bindings, list):
        raise ValueError("invalid argv bindings")
    binding_records: list[dict[str, str]] = []
    for raw in raw_bindings:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("flag"), str):
            raise ValueError("invalid argv binding")
        flag, literal = str(raw["flag"]), raw.get("value")
        if literal is not None:
            if (
                not isinstance(literal, (str, int, float))
                or isinstance(literal, bool)
                or raw.get("path") is not None
            ):
                raise ValueError("invalid literal binding")
            fixed[flag] = str(literal)
            continue
        role, scope, path = raw.get("role"), raw.get("scope"), raw.get("path")
        if (
            not isinstance(role, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", role) is None
            or raw.get("absolute") is not True
        ):
            raise ValueError("invalid path binding")
        if scope == "target" and _safe_contract_relative(path):
            source = str(PurePosixPath(target_root) / str(path))
        elif (
            scope == "absolute"
            and isinstance(path, str)
            and PurePosixPath(path).is_absolute()
        ):
            source = path
        else:
            raise ValueError("invalid path binding")
        sandbox = f"/hypoloom-data/{role}"
        fixed[flag] = sandbox
        data_bindings.append((source, sandbox))
        binding_records.append(
            {
                "flag": flag,
                "role": role,
                "scope": str(scope),
                "path": str(path),
                "source": source,
                "sandbox": sandbox,
                "kind": str(raw.get("kind", "")),
            }
        )

    result = component.get("result")
    dataset = result.get("dataset") if isinstance(result, Mapping) else None
    seeds = component.get("seeds")
    if not _trimmed_text(dataset) or not isinstance(seeds, list):
        raise ValueError("invalid trial identity")
    if shape == "paired_control_treatment" and len(seeds) != 3:
        raise ValueError("invalid paired seed count")
    if shape == "seeded_trials" and len(seeds) != 5:
        raise ValueError("invalid seeded trial count")
    config_specs = [
        item
        for item in capabilities.values()
        if isinstance(item, Mapping) and item.get("path_template")
    ]
    template: str | None
    config_flag: str | None
    if shape == "checkpoint_audit":
        if config_specs:
            raise ValueError("checkpoint audit has config")
        template = config_flag = None
    else:
        if len(config_specs) != 1:
            raise ValueError("invalid config binding")
        template, config_flag = (
            config_specs[0].get("path_template"),
            config_specs[0].get("path_flag"),
        )
        if not isinstance(template, str) or not isinstance(config_flag, str):
            raise ValueError("invalid config binding")

    required_flags: list[str] = []
    flag_sources: list[Any] = [requirements.get("required_cli_flags", [])]
    flag_sources.extend(
        item.get("required_cli_flags", [])
        for item in capabilities.values()
        if isinstance(item, Mapping)
    )
    for source in flag_sources:
        if not isinstance(source, list):
            raise ValueError("invalid required flags")
        for flag in source:
            if not isinstance(flag, str) or not flag.startswith("--"):
                raise ValueError("invalid required flag")
            if flag not in required_flags:
                required_flags.append(flag)
    for flag in fixed:
        if flag not in required_flags:
            required_flags.append(flag)
    additional_flags = (
        ((config_flag,) if isinstance(config_flag, str) else ()) + ("--device",)
    )
    for flag in additional_flags:
        if flag not in required_flags:
            required_flags.append(flag)

    input_specs = component.get("required_inputs", [])
    if not isinstance(input_specs, list):
        raise ValueError("invalid required inputs")
    input_positions: dict[Any, tuple[int, int]] = {}
    for position, item in enumerate(input_specs, 1):
        if not isinstance(item, Mapping):
            continue
        count = item.get("min_count", 1)
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("invalid required input count")
        input_positions[item.get("resource")] = (position, count)
    if candidate_resource not in input_positions or input_positions[candidate_resource][1] != 1:
        raise ValueError("invalid candidate manifest input")

    treatment_python = (
        str(PurePosixPath(target_root) / "src")
        if str(entrypoint_path).startswith("src/")
        else target_root
    )
    control_root = "/hypoloom-control-target"
    control_python = (
        str(PurePosixPath(control_root) / "src")
        if str(entrypoint_path).startswith("src/")
        else control_root
    )
    trials: list[dict[str, Any]] = []

    def config_path(model: str, root: str) -> str:
        if not isinstance(template, str):
            raise ValueError("missing config template")
        rendered = template.replace("{model}", model).replace(
            "{dataset_lower}", str(dataset).lower()
        )
        if not _safe_contract_relative(rendered):
            raise ValueError("invalid rendered config")
        return str(PurePosixPath(root) / rendered)

    def trial(
        seed: Any,
        arm: str | None,
        model: str,
        python_root: str,
        root: str,
        checkpoint_input: str | None = None,
    ) -> dict[str, Any]:
        tag = f"{arm}.seed{seed}" if arm else f"trial.seed{seed}"
        raw_result = f"raw/{tag}.json"
        checkpoint = (
            f"raw-checkpoints/{tag}.pt"
            if "--save-ckpt" in required_flags
            else checkpoint_input
        )
        dynamic: dict[str, str | None] = {
            "--dataset": str(dataset),
            str(model_flag): model,
            "--seed": str(seed),
            "--out": "{raw_result}",
            "--save-ckpt": (
                "{checkpoint}" if "--save-ckpt" in required_flags else None
            ),
            "--checkpoint": "{checkpoint}" if checkpoint_input is not None else None,
            "--device": str(device),
        }
        if isinstance(config_flag, str):
            dynamic[config_flag] = config_path(model, root)
        target_argv: list[str] = []
        for flag in required_flags:
            value = fixed.get(flag, dynamic.get(flag))
            if not isinstance(value, str) or not value:
                raise ValueError("underivable trial flag")
            target_argv.extend((flag, value))
        record: dict[str, Any] = {
            "seed": seed,
            "model": model,
            "module": str(module),
            "pythonpath": python_root,
            "target_argv": target_argv,
            "raw_result": raw_result,
        }
        if arm is not None:
            record["arm"] = arm
        if checkpoint is not None:
            record["checkpoint"] = checkpoint
        return record

    if shape == "paired_control_treatment":
        for seed in seeds:
            trials.extend(
                (
                    trial(seed, "control", str(control), control_python, control_root),
                    trial(seed, "treatment", treatment, treatment_python, target_root),
                )
            )
    elif shape == "seeded_trials":
        trials.extend(
            trial(seed, None, treatment, treatment_python, target_root)
            for seed in seeds
        )
    else:
        checkpoint_resource = normalizer.get("checkpoint_resource")
        if checkpoint_resource not in input_positions:
            raise ValueError("invalid checkpoint input")
        position, count = input_positions[checkpoint_resource]
        if count < 1:
            raise ValueError("invalid checkpoint count")
        trials.extend(
            trial(
                seed,
                None,
                treatment,
                treatment_python,
                target_root,
                f"/hypoloom-inputs/{position}/{seed + 1}",
            )
            for seed in range(count)
        )

    raw_checks = requirements.get("data_checks", [])
    if not isinstance(raw_checks, list):
        raise ValueError("invalid data checks")
    data_checks = [
        _oracle_sandbox_data_check(item, binding_records) for item in raw_checks
    ]
    driver_plan: dict[str, Any] = {
        "schema_version": 1,
        "shape": shape,
        "dataset": dataset,
        "identity": {
            "method_family_id": method_family_id,
            "implementation_sha256": implementation_sha256,
        },
        "metric_paths": dict(metric_paths),
        "candidate_manifest_resource": candidate_resource,
        "data_checks": data_checks,
        "trials": trials,
    }
    for name in (
        "per_class_path",
        "class_labels",
        "audit_path",
        "audit_max",
        "audit_required_arms",
        "checkpoint_resource",
        "checkpoint_torch_load",
    ):
        if name in normalizer:
            driver_plan[name] = normalizer[name]
    source = ".hypoloom/trial_driver.py"
    encoded = json.dumps(
        driver_plan,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return _OracleComponentExecution(
        argv=("python", source, encoded),
        source_relative_path=source,
        target_root=target_root,
        data_bindings=tuple(data_bindings),
        control_target=shape == "paired_control_treatment",
    )


def _oracle_sandbox_data_check(
    raw: Any, bindings: Sequence[Mapping[str, str]]
) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("type") != "hdf5_split_manifest":
        raise ValueError("invalid data check")
    result = dict(raw)
    for name in ("cache", "manifest", "val_cache"):
        value = raw.get(name)
        if value is None:
            continue
        if not _safe_contract_relative(value):
            raise ValueError("invalid data check path")
        matches: list[tuple[int, str]] = []
        for binding in bindings:
            try:
                suffix = PurePosixPath(str(value)).relative_to(
                    PurePosixPath(binding["path"])
                )
            except ValueError:
                continue
            matches.append(
                (
                    len(PurePosixPath(binding["path"]).parts),
                    str(PurePosixPath(binding["sandbox"]).joinpath(*suffix.parts)),
                )
            )
        if not matches:
            raise ValueError("unbound data check path")
        result[name] = max(matches)[1]
        if name == "val_cache":
            result["declared_val_cache"] = str(value).replace("\\", "/")
    return result


@dataclass(frozen=True, slots=True)
class _OracleMountBinding:
    destination: str
    source: str | None = None
    attempt_leaf: str | None = None


@dataclass(frozen=True, slots=True)
class _OracleComponentMounts:
    bindings: tuple[_OracleMountBinding, ...]
    read_roots: tuple[str, ...]
    required_inputs: tuple[tuple[str, tuple[str, ...]], ...]
    target_root: str
    read_root_map: tuple[tuple[str, str], ...] = ()


def _oracle_uses_submitted_target(document: Any, component_id: Any, request: Mapping[str, Any],
                                  research_version: str | None) -> bool:
    """Independently select the target-source rule, not the result artifact identity."""
    version = re.fullmatch(r"hypoloom-research-v([0-9]+)", str(research_version))
    if version is None or int(version[1]) < 9 or type(request.get("schema_version")) is not int or request["schema_version"] < 14:
        return False
    if not isinstance(document, Mapping): return False
    runtime = document.get("runtime", {})
    component = next((item for item in document.get("components", ()) if isinstance(item, Mapping) and item.get("id") == component_id), {})
    result = component.get("result", {})
    return (isinstance(runtime, Mapping) and isinstance(runtime.get("target_repo"), str)
        and isinstance(result, Mapping) and result.get("kind") == "metric" and not result.get("deferred")
        and result.get("target_overlay_policy") != "latest")


def _oracle_implementation_sha256(
    document: Any,
    component_id: Any,
    method_family_id: Any,
    spec_fact: sqlite3.Row,
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[sqlite3.Row],
    artifact_root: Path,
) -> str | None:
    """Resolve the accepted ancestor artifact identity, not a submitted target diff."""

    if (
        not isinstance(document, Mapping)
        or not isinstance(component_id, str)
        or not _trimmed_text(method_family_id)
    ):
        return None
    components = document.get("components")
    if not isinstance(components, list):
        return None
    by_id = {
        str(item["id"]): item
        for item in components
        if isinstance(item, Mapping) and _trimmed_text(item.get("id"))
    }
    component = by_id.get(component_id)
    result = component.get("result") if isinstance(component, Mapping) else None
    if not isinstance(result, Mapping) or result.get("target_overlay_policy") != "latest":
        return "0" * 64
    ancestors: set[str] = set()

    def visit(identity: str) -> bool:
        raw = by_id.get(identity)
        dependencies = raw.get("dependencies") if isinstance(raw, Mapping) else None
        if not isinstance(dependencies, list):
            return False
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency not in by_id:
                return False
            if dependency not in ancestors:
                ancestors.add(dependency)
                if not visit(dependency):
                    return False
        return True

    if not visit(component_id):
        return None
    candidates: dict[str, tuple[sqlite3.Row, tuple[str, ...]]] = {}
    for fact in facts:
        if (
            fact["fact_type"] != "CanonicalOutcomeAccepted"
            or int(fact["raw_order"]) >= int(spec_fact["raw_order"])
        ):
            continue
        payload = payloads.get(str(fact["fact_id"]), {})
        paths = _logical_artifact_paths(payload).get("implementation_patch", ())
        candidate_component = payload.get("component_id")
        if (
            candidate_component in ancestors
            and payload.get("method_family_id") == method_family_id
            and paths
        ):
            candidates[str(candidate_component)] = (fact, paths)
    if len(candidates) != 1:
        return None
    outcome, paths = next(iter(candidates.values()))
    if len(paths) != 1:
        return None
    relative = paths[0]
    matching_artifacts = [
        row
        for row in artifacts
        if str(row["artifact_id"]) == str(outcome["artifact_id"])
    ]
    if len(matching_artifacts) != 1:
        return None
    publication = _artifact_materialized_root(artifact_root, matching_artifacts[0])
    patch = None if publication is None else _publication_file(publication, relative)
    if patch is None:
        return None
    digest = hashlib.sha256()
    try:
        with patch.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _oracle_component_mounts(
    document: Any,
    component_id: Any,
    execution: _OracleComponentExecution,
    spec_fact: sqlite3.Row,
    spec_payload: Mapping[str, Any],
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[sqlite3.Row],
    artifact_root: Path,
    transition_payloads: Mapping[str, Mapping[str, Any]],
) -> _OracleComponentMounts | None:
    """Project the exact Host read-only sources from raw facts and publications."""

    if not isinstance(document, Mapping) or not isinstance(component_id, str):
        return None
    components = document.get("components")
    if not isinstance(components, list) or execution.target_root is None:
        return None
    component = next(
        (
            item
            for item in components
            if isinstance(item, Mapping) and item.get("id") == component_id
        ),
        None,
    )
    if not isinstance(component, Mapping):
        return None
    target_root = str(Path(execution.target_root).resolve(strict=False))
    bindings = [
        _OracleMountBinding(destination=target_root, attempt_leaf="target")
    ]
    if execution.control_target:
        bindings.append(
            _OracleMountBinding(
                destination="/hypoloom-control-target",
                attempt_leaf="control-target",
            )
        )
    raw_data_sources = [Path(source) for source, _ in execution.data_bindings]
    for source, destination in execution.data_bindings:
        bindings.append(
            _OracleMountBinding(
                source=str(Path(source).resolve(strict=False)),
                destination=destination,
            )
        )

    safety = document.get("safety")
    raw_read_roots = safety.get("read_roots", []) if isinstance(safety, Mapping) else []
    if not isinstance(raw_read_roots, list):
        return None
    read_roots: list[str] = []
    for raw_root in raw_read_roots:
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
            return None
        root = str(Path(raw_root).resolve(strict=False))
        read_roots.append(root)
    if len(read_roots) != len(set(read_roots)):
        return None
    delivery = document.get("delivery_contracts", {})
    protocols = delivery.get("protocols", {}) if isinstance(delivery, Mapping) else {}
    protocol = protocols.get(component.get("protocol"), {}) if isinstance(protocols, Mapping) else {}
    goal_evaluation = spec_payload.get("goal_evaluation")
    generic = spec_payload.get("measurement") is not None or (
        isinstance(goal_evaluation, Mapping) and goal_evaluation.get("scope") == "GOAL"
    )
    aliases: dict[str, str] = {}
    if generic and not execution.data_bindings and not (
        isinstance(protocol, Mapping) and protocol.get("production") is True
    ):
        # Derive from admitted roots, not the recorded Process environment or
        # the Host projection. Outer roots keep their original input ordinals.
        paths = tuple(Path(root) for root in read_roots)
        intersecting = tuple(root for root in paths if root == Path(target_root)
            or root in Path(target_root).parents or Path(target_root) in root.parents)
        outer = tuple(root for root in intersecting if not any(
            other in root.parents for other in intersecting
        ))
        for index, source in enumerate(paths, 1):
            if source not in outer:
                continue
            destination = f"/hypoloom-read-roots/{index}"
            for root in paths:
                if root == source or source in root.parents:
                    aliases[str(root)] = str(PurePosixPath(destination) / root.relative_to(source))
        if aliases:
            read_roots = []
            for source in paths:
                if str(source) in aliases and source not in outer:
                    continue
                destination = aliases.get(str(source), str(source))
                read_roots.append(destination)
                bindings.append(_OracleMountBinding(source=str(source), destination=destination))
    for root in () if aliases else read_roots:
        source = Path(root)
        if source == Path(target_root) or any(
            source == item or source in item.parents or item in source.parents
            for item in raw_data_sources
        ):
            continue
        bindings.append(_OracleMountBinding(source=root, destination=root))

    input_specs = component.get("required_inputs", [])
    raw_upstream = spec_payload.get("upstream_outcomes")
    if not isinstance(input_specs, list) or not isinstance(raw_upstream, list):
        return None
    if len(raw_upstream) != len(input_specs):
        return None
    family = spec_payload.get("method_family_id")
    required_inputs: list[tuple[str, tuple[str, ...]]] = []
    for position, requirement in enumerate(input_specs, 1):
        if not isinstance(requirement, Mapping):
            return None
        resource = requirement.get("resource")
        producer = requirement.get("produced_by")
        logical_name = requirement.get("artifact")
        minimum = requirement.get("min_count", 1)
        if (
            not _trimmed_text(resource)
            or not _trimmed_text(producer)
            or not _trimmed_text(logical_name)
            or isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or minimum < 1
        ):
            return None
        # Mirror the Host preregistration rule: only comparable accepted tips
        # count as producers, and the greatest producer experiment ordinal is
        # bound. A component whose same-family experiments accumulated
        # operational outcomes (execution failures, integrity revisions)
        # before its final success therefore resolves to exactly one producer.
        candidates: list[tuple[int, sqlite3.Row]] = []
        for fact in facts:
            if (
                fact["fact_type"] != "CanonicalOutcomeAccepted"
                or int(fact["raw_order"]) >= int(spec_fact["raw_order"])
            ):
                continue
            outcome_payload = payloads.get(str(fact["fact_id"]), {})
            if (
                outcome_payload.get("component_id") == producer
                and outcome_payload.get("method_family_id") == family
                and outcome_payload.get("outcome") in _COMPARABLE_OUTCOMES
            ):
                creation = transition_payloads.get(
                    f"{fact['experiment_id']}@1"
                )
                ordinal = (
                    creation.get("experiment_ordinal")
                    if isinstance(creation, Mapping)
                    else None
                )
                if (
                    isinstance(ordinal, bool)
                    or not isinstance(ordinal, int)
                    or ordinal < 1
                ):
                    return None
                candidates.append((ordinal, fact))
        if not candidates:
            return None
        outcome = max(candidates, key=lambda item: item[0])[1]
        outcome_payload = payloads.get(str(outcome["fact_id"]), {})
        logical_paths = _logical_artifact_paths(outcome_payload).get(
            str(logical_name), ()
        )
        if len(logical_paths) < minimum:
            return None
        raw_binding = raw_upstream[position - 1]
        expected_binding = {
            "resource": resource,
            "produced_by_component": producer,
            "logical_artifact": logical_name,
            "min_count": minimum,
            "outcome_id": str(outcome["outcome_id"]),
            "artifact_id": str(outcome["artifact_id"]),
        }
        if not isinstance(raw_binding, Mapping) or dict(raw_binding) != expected_binding:
            return None
        matching_artifacts = [
            row
            for row in artifacts
            if str(row["artifact_id"]) == str(outcome["artifact_id"])
        ]
        if len(matching_artifacts) != 1:
            return None
        publication = _artifact_materialized_root(artifact_root, matching_artifacts[0])
        if publication is None:
            return None
        input_targets: list[str] = []
        for index, relative in enumerate(logical_paths, 1):
            source = _publication_file(publication, relative)
            if source is None:
                return None
            destination = f"/hypoloom-inputs/{position}/{index}"
            bindings.append(
                _OracleMountBinding(
                    source=str(source.resolve(strict=True)),
                    destination=destination,
                )
            )
            input_targets.append(destination)
        required_inputs.append((str(resource), tuple(input_targets)))
    if len({name for name, _ in required_inputs}) != len(required_inputs):
        return None
    return _OracleComponentMounts(
        bindings=tuple(bindings),
        read_roots=tuple(read_roots),
        required_inputs=tuple(required_inputs),
        target_root=target_root,
        read_root_map=tuple(sorted(aliases.items())),
    )


def _oracle_component_environment_matches(
    raw_environment: Any, mounts: _OracleComponentMounts
) -> bool:
    if not isinstance(raw_environment, list) or any(
        not isinstance(item, list)
        or len(item) != 2
        or any(not isinstance(value, str) for value in item)
        for item in raw_environment
    ):
        return False
    names = [item[0] for item in raw_environment]
    if names != sorted(names) or len(names) != len(set(names)):
        return False
    environment = dict(raw_environment)
    required_inputs = {
        name: list(paths) for name, paths in mounts.required_inputs
    }
    return (
        environment.get("HYPOLOOM_RESULT_PATH") == "result.json"
        and environment.get("HYPOLOOM_TARGET_ROOT") == mounts.target_root
        and environment.get("HYPOLOOM_READ_ROOTS")
        == json.dumps(list(mounts.read_roots), separators=(",", ":"))
        and _oracle_read_root_map_matches(environment, mounts)
        and environment.get("HYPOLOOM_REQUIRED_INPUTS")
        == json.dumps(
            required_inputs,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _oracle_read_root_map_matches(environment: Mapping[str, Any], mounts: _OracleComponentMounts) -> bool:
    if not mounts.read_root_map:
        return "HYPOLOOM_READ_ROOT_MAP" not in environment
    return environment.get("HYPOLOOM_READ_ROOT_MAP") == json.dumps(
        dict(mounts.read_root_map), sort_keys=True, separators=(",", ":")
    )


def _oracle_component_execution_argv_legacy(
    document: Any, component_id: Any
) -> tuple[str, ...] | None:
    """Independently project the command observable from GoalDefined."""

    if not isinstance(document, Mapping) or not isinstance(component_id, str):
        return None
    components, protocols = document.get("components"), document.get("protocols")
    if not isinstance(components, list) or not isinstance(protocols, Mapping):
        return None
    component = next(
        (item for item in components if isinstance(item, Mapping) and item.get("id") == component_id), None
    )
    if component is None:
        return None
    protocol_name = component.get("protocol")
    protocol = protocols.get(protocol_name) if isinstance(protocol_name, str) else None
    requirements = protocol.get("requirements", {}) if isinstance(protocol, Mapping) else {}
    if not isinstance(requirements, Mapping):
        return None
    delivery = document.get("delivery_contracts")
    delivery_protocols = delivery.get("protocols", {}) if isinstance(delivery, Mapping) else {}
    delivery_protocol = delivery_protocols.get(protocol_name) if isinstance(delivery_protocols, Mapping) else None
    production = isinstance(delivery_protocol, Mapping) and delivery_protocol.get("production") is True
    named = ("required_paths", "data_checks", "required_cli_flags", "required_source_literals", "argv_bindings")
    if not production:
        return None if any(requirements.get(name) for name in named) else ("python", "experiment.py")
    seeds = component.get("seeds")
    if (
        delivery_protocol.get("execution_shape") == "paired_control_treatment"
        or not isinstance(seeds, list)
        or len(seeds) > 1
    ):
        return None
    runtime = document.get("runtime")
    target = runtime.get("target_repo") if isinstance(runtime, Mapping) else None
    capabilities = delivery_protocol.get("capabilities")
    if not isinstance(target, str) or not PurePosixPath(target).is_absolute() or not isinstance(capabilities, Mapping):
        return None
    entrypoints = [
        item for item in capabilities.values()
        if isinstance(item, Mapping) and item.get("role") == "entrypoint"
    ]
    if len(entrypoints) != 1:
        return None
    entrypoint = entrypoints[0]
    entrypoint_path, module = entrypoint.get("path"), entrypoint.get("module")
    if not _safe_contract_relative(entrypoint_path) or not _trimmed_text(module):
        return None
    values: dict[str, str] = {}
    bindings = requirements.get("argv_bindings", [])
    if not isinstance(bindings, list):
        return None
    for binding in bindings:
        if not isinstance(binding, Mapping) or not _trimmed_text(binding.get("flag")) or binding.get("absolute") is not True:
            return None
        path, scope = binding.get("path"), binding.get("scope")
        if scope == "target" and _safe_contract_relative(path):
            value = str(PurePosixPath(target) / str(path))
        elif scope == "absolute" and isinstance(path, str) and PurePosixPath(path).is_absolute():
            value = path
        else:
            return None
        values[str(binding["flag"])] = value
    model: str | None = None
    for capability in capabilities.values():
        if not isinstance(capability, Mapping):
            continue
        selection = capability.get("selection")
        if isinstance(selection, Mapping):
            expected, flag = selection.get("expected_values"), selection.get("argv_flag")
            if not isinstance(expected, list) or len(expected) != 1 or not _trimmed_text(flag):
                return None
            model = str(expected[0]); values[str(flag)] = model
    result = component.get("result")
    dataset = result.get("dataset") if isinstance(result, Mapping) else None
    for capability in capabilities.values():
        if not isinstance(capability, Mapping) or not capability.get("path_template"):
            continue
        template, flag = capability.get("path_template"), capability.get("path_flag")
        if not isinstance(template, str) or not _trimmed_text(flag) or model is None or not isinstance(dataset, str):
            return None
        rendered = template.replace("{model}", model).replace("{dataset_lower}", dataset.lower())
        if not _safe_contract_relative(rendered):
            return None
        values[str(flag)] = str(PurePosixPath(target) / rendered)
    builtin = {"--dataset": dataset, "--seed": str(seeds[0]) if seeds else None,
               "--out": "/output/result.json", "--save-ckpt": "/output/checkpoint.pt"}
    sources: list[Any] = [requirements.get("required_cli_flags", [])]
    sources.extend(
        capability.get("required_cli_flags", [])
        for capability in capabilities.values() if isinstance(capability, Mapping)
    )
    sources.append(list(values))
    flags: list[str] = []
    for source in sources:
        if not isinstance(source, list):
            return None
        for flag in source:
            if not isinstance(flag, str) or not flag.startswith("--"):
                return None
            if flag not in flags:
                flags.append(flag)
    argv = ["python", "-m", str(module)]
    for flag in flags:
        value = values.get(flag, builtin.get(flag))
        if not isinstance(value, str) or not value:
            return None
        argv.extend((flag, value))
    return tuple(argv)


def _safe_contract_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and value == path.as_posix() and all(part not in {"", ".", ".."} for part in path.parts)


def _oracle_cpu_only_artifact(document: Any, payload: Mapping[str, Any]) -> bool:
    """Independently derive the Host CPU-only override for non-production artifact work."""

    component_id = payload.get("component_id")
    components = document.get("components") if isinstance(document, Mapping) else None
    component = next(
        (item for item in components if isinstance(item, Mapping) and item.get("id") == component_id),
        None,
    ) if isinstance(components, list) else None
    # GOAL-only measurements may be stored as artifact results even when the
    # declared component is metric work. The Host override follows the latter.
    result = component.get("result") if isinstance(component, Mapping) else None
    if not isinstance(result, Mapping) or result.get("kind") != "artifact":
        return False
    protocol_name = component.get("protocol") if isinstance(component, Mapping) else None
    delivery = document.get("delivery_contracts") if isinstance(document, Mapping) else None
    protocols = delivery.get("protocols", {}) if isinstance(delivery, Mapping) else {}
    delivery_protocol = protocols.get(protocol_name) if isinstance(protocols, Mapping) and isinstance(protocol_name, str) else None
    return not (isinstance(delivery_protocol, Mapping) and delivery_protocol.get("production") is True)


def _raw_grant_devices(grant: Any, *, allow_multiple: bool = True) -> tuple[str, tuple[str, ...]]:
    if not isinstance(grant, Mapping): raise ValueError("resource grant is not an object")
    kind = grant.get("kind")
    if set(grant) == {"kind", "gpu_uuid"}:
        if kind == "CPU" and grant["gpu_uuid"] is None: return "CPU", ()
        if kind == "GPU" and _physical_gpu_uuid(grant["gpu_uuid"]): return "GPU", (grant["gpu_uuid"],)
    values = grant.get("gpu_uuids")
    if (allow_multiple and set(grant) == {"kind", "gpu_uuids"} and kind == "GPU"
            and isinstance(values, list) and len(values) >= 2 and all(_physical_gpu_uuid(item) for item in values)
            and values == sorted(set(values))): return "GPU", tuple(values)
    raise ValueError("resource grant has noncanonical or incompatible device identities")


def _raw_owned_sample(sample: Any, uuid: str, attempt: Mapping[str, Any], total_mib: int) -> int | None:
    """Check persisted numeric ownership evidence without inspecting a live PID."""
    def integer(value: Any, minimum: int = 0) -> bool: return type(value) is int and minimum <= value < 2**53
    def numeric(value: Any) -> bool: return type(value) in {int, float} and float("-inf") < value < float("inf")
    keys = {"gpu_uuid", "root_process_group_id", "root_start_time_ticks", "complete", "reason",
            "observed_at", "duration_seconds", "resident_bytes", "processes"}
    if (not isinstance(sample, Mapping) or set(sample) != keys or sample["gpu_uuid"] != uuid
            or not integer(attempt["process_group_id"], 2) or sample["root_process_group_id"] != attempt["process_group_id"]
            or type(sample["complete"]) is not bool or not numeric(sample["observed_at"])
            or not numeric(sample["duration_seconds"]) or sample["duration_seconds"] < 0
            or sample["observed_at"] < attempt["started_at"] - 0.002
            or attempt["ended_at"] is not None and sample["observed_at"] > attempt["ended_at"] + 0.002):
        raise ValueError("owned sample has invalid device, bound root, time or fields")
    if not sample["complete"]:
        if (sample["resident_bytes"] is not None or sample["processes"] != []
                or not _bounded_trimmed_text(sample["reason"], 128)
                or sample["root_start_time_ticks"] is not None and not integer(sample["root_start_time_ticks"])):
            raise ValueError("unknown sample carries memory credit or process data")
        return None
    rows = sample["processes"]
    if (sample["reason"] is not None or sample["duration_seconds"] > 1.0
            or not integer(sample["root_start_time_ticks"]) or not integer(sample["resident_bytes"])
            or not integer(total_mib, 1) or sample["resident_bytes"] > total_mib * 1024**2
            or not isinstance(rows, list) or len(rows) > 512):
        raise ValueError("complete owned sample has invalid memory, duration or process bounds")
    seen, metadata, total, proof_count = [], {}, 0, 0
    for row in rows:
        if (not isinstance(row, Mapping) or set(row) != {"pid", "resident_bytes", "ancestry"}
                or not integer(row["pid"], 1) or not integer(row["resident_bytes"])
                or not isinstance(row["ancestry"], list) or not 1 <= len(row["ancestry"]) <= 64):
            raise ValueError("owned GPU process lacks bounded numeric ancestry")
        ancestry = row["ancestry"]; chain = []
        for node in ancestry:
            if (not isinstance(node, Mapping) or set(node) != {"pid", "ppid", "start_time_ticks"}
                    or not integer(node["pid"], 1) or not integer(node["ppid"]) or not integer(node["start_time_ticks"])
                    or node["pid"] in chain or node["pid"] in metadata and metadata[node["pid"]] != node):
                raise ValueError("owned ancestry has conflicting PID incarnations")
            metadata[node["pid"]] = node; chain.append(node["pid"])
        if (chain[0] != row["pid"] or chain[-1] != attempt["process_group_id"]
                or ancestry[-1]["start_time_ticks"] != sample["root_start_time_ticks"]
                or any(child["ppid"] != parent["pid"] or child["start_time_ticks"] < parent["start_time_ticks"]
                       for child, parent in zip(ancestry, ancestry[1:]))):
            raise ValueError("GPU process ancestry does not reach the exact bound Runner root")
        seen.append(row["pid"]); total += row["resident_bytes"]; proof_count += len(ancestry)
    if seen != sorted(set(seen)) or proof_count > 512 or total != sample["resident_bytes"]:
        raise ValueError("owned GPU byte sum or unique bounded process set is invalid")
    return total


def _raw_owned_peak(observation: Mapping[str, Any], attempt: Mapping[str, Any], devices: Sequence[str], *, required: bool) -> int | None:
    usage = observation.get("resource_usage")
    rows = [usage] if isinstance(usage, Mapping) else usage
    if (not isinstance(rows, list) or not rows or not all(isinstance(row, Mapping) for row in rows)
            or [row.get("gpu_uuid") for row in rows] != list(devices)):
        if required: raise ValueError("owned memory telemetry lacks its exact granted GPU set")
        return None
    peaks, complete = [], True
    for row in rows:
        owned = row.get("owned_memory")
        if owned is None and not required: return None
        if not isinstance(owned, Mapping) or set(owned) != {"sample_count", "unknown_sample_count", "latest", "peak"}:
            raise ValueError("owned memory summary has invalid fields")
        count, unknown = owned["sample_count"], owned["unknown_sample_count"]
        if type(count) is not int or type(unknown) is not int or not 0 <= unknown <= count < 2**53:
            raise ValueError("owned memory sample counts are invalid")
        if count == 0:
            if owned["latest"] is not None or owned["peak"] is not None: raise ValueError("empty sampler has invented evidence")
            complete = False; continue
        latest = _raw_owned_sample(owned["latest"], row["gpu_uuid"], attempt, row.get("gpu_total_memory_mib", row.get("total_memory_mib")))
        peak = None if owned["peak"] is None else _raw_owned_sample(owned["peak"], row["gpu_uuid"], attempt, row.get("gpu_total_memory_mib", row.get("total_memory_mib")))
        if (unknown == 0 and latest is None or unknown == count and peak is not None
                or unknown < count and peak is None or owned["peak"] is not None and owned["peak"]["complete"] is not True
                or latest is not None and (peak is None or peak < latest)
                or peak is not None and owned["peak"]["observed_at"] > owned["latest"]["observed_at"]
                or latest is not None and peak is not None and owned["latest"]["root_start_time_ticks"] != owned["peak"]["root_start_time_ticks"]
                or count == 1 and latest is not None and owned["latest"] != owned["peak"]):
            raise ValueError("owned memory latest/peak and coverage counts disagree")
        if unknown or peak is None: complete = False
        else: peaks.append(peak)
    return max(peaks) if complete and peaks else None


def _raw_reported_gpu_oom(observation: Mapping[str, Any], attempt: Mapping[str, Any]) -> bool:
    if "resource_failure" not in observation and "resource_failure_excerpt" not in observation: return False
    excerpt = observation.get("resource_failure_excerpt")
    if (observation.get("resource_failure") != "gpu_out_of_memory" or attempt["terminal_reason"] != "FAILED"
            or observation.get("terminal_reason") != "FAILED" or type(observation.get("returncode")) is not int
            or observation["returncode"] == 0 or observation.get("synthetic_before_spawn") is True
            or observation.get("resource_pressure") is not None or not _bounded_trimmed_text(excerpt, 1024)
            or any(ord(c) < 32 or ord(c) == 127 for c in excerpt)
            or re.match(r"(?:torch(?:\.cuda)?\.OutOfMemoryError: CUDA out of memory|cupy\.cuda\.memory\.OutOfMemoryError: Out of memory)", excerpt) is None):
        raise ValueError("reported GPU OOM lacks its failed-workload exception excerpt")
    return True


def _raw_learned_workload_budget(contract: Mapping[str, Any], request: Mapping[str, Any], response: Mapping[str, Any],
                                floor: int, context: Mapping[str, Any] | None, pool: Mapping[str, Any],
                                *, initial_memory_mib: int | None = None,
                                visited: frozenset[str] = frozenset(), memo: dict[str, int] | None = None,
                                planning: dict[str, Any] | None = None) -> int:
    initial = floor if initial_memory_mib is None else initial_memory_mib
    if type(initial) is not int or not 0 < floor <= initial < 2**53:
        raise ValueError("initial memory must be JSON-safe and at least the positive Profile floor")
    if memo is None: memo = {}
    command_id = contract.get("source_submit_command_id")
    if planning is not None: planning.update(sharing="exclusive")
    if command_id is not None and command_id in memo and planning is None: return memo[command_id]
    arguments = response.get("arguments", {})
    if "resource_estimate" not in arguments: return initial
    if type(request.get("schema_version")) is not int or request["schema_version"] < 18:
        raise ValueError("learned resource estimate requires ACI18")
    estimate = _raw_resource_estimate(arguments["resource_estimate"])
    if estimate != arguments["resource_estimate"]: raise ValueError("resolved estimate is not canonical")
    result = max(floor, estimate["peak_memory_mib"])
    ids = estimate["measurement_attempt_ids"]
    if not ids:
        result = max(result, initial)
        if command_id is not None: memo[command_id] = result
        return result
    if context is None: raise ValueError("learned budget lacks raw measurement sources")
    command_id = contract["source_submit_command_id"]
    if command_id in visited: raise ValueError("resource measurement sources form a cycle")
    commands = {row["command_id"]: row for row in context["commands"]}
    attempts = {row["attempt_id"]: row for row in context["attempts"]}
    command = commands[command_id]
    current = _raw_submit_source(command, context["attempts"], context["observations"])
    if current is None or current[1] != response: raise ValueError("learned budget lacks its exact accepted submit")
    accepted = datetime.fromisoformat(command["created_at"].replace("Z", "+00:00"))
    accepted_at = (accepted if accepted.tzinfo else accepted.replace(tzinfo=timezone.utc)).timestamp()
    measured_success = False
    verified_measurements = []
    for identity in ids:
        source = attempts[identity]
        if (source["kind"] != "PROCESS" or source["state"] != "CLOSED" or source["run_id"] != request["run_id"]
                or type(source["ended_at"]) not in {int, float} or not source["started_at"] <= source["ended_at"] <= accepted_at + 0.002):
            raise ValueError("measurement source is not an earlier CLOSED Process in this run")
        specs = [row for row in context["facts"] if row["fact_type"] == "ExperimentPreregistered"
                 and row["run_id"] == request["run_id"] and row["experiment_id"] == source["experiment_id"]
                 and row["execution_id"] == source["execution_id"]]
        if len(specs) != 1: raise ValueError("measurement source lacks one exact preregistration")
        payload = context["fact_payloads"][specs[0]["fact_id"]]; previous = payload["execution_contract"]
        submit = commands[previous["source_submit_command_id"]]
        decoded = _raw_submit_source(submit, context["attempts"], context["observations"])
        if decoded is None: raise ValueError("measurement source lacks its raw accepted submit")
        prior_request, prior_response = decoded
        if (any(prior_request.get(name) != request.get(name) for name in ("run_id", "role", "component_id", "method_family_id"))
                or payload.get("component_id") != request["component_id"] or payload.get("method_family_id") != request["method_family_id"]
                or previous["source_agent_attempt_id"] != prior_request["attempt_id"]
                or previous["source_agent_output_digest"] != prior_response["output_digest"]
                or _raw_resource_estimate(prior_response["arguments"].get("resource_estimate"))["workload"] != estimate["workload"]):
            raise ValueError("measurement source differs from the exact private workload scope")
        prior_stamp = datetime.fromisoformat(submit["created_at"].replace("Z", "+00:00"))
        if (prior_stamp if prior_stamp.tzinfo else prior_stamp.replace(tzinfo=timezone.utc)).timestamp() > source["started_at"] + 0.002:
            raise ValueError("measurement Process precedes its admitted source")
        intent = previous["resource_intent"]
        admitted = _raw_learned_workload_budget(previous, prior_request, prior_response, floor, context, pool,
            initial_memory_mib=initial_memory_mib, visited=visited | {command_id}, memo=memo)
        if type(intent.get("workload_memory_mib")) is not int or intent["workload_memory_mib"] != admitted:
            raise ValueError("measurement source prior admitted budget differs from its raw sources")
        dispatches = [json.loads(row["payload_json"]) for row in context["transitions"]
                      if row["run_id"] == request["run_id"] and row["workflow_id"] == source["workflow_id"]
                      and json.loads(row["payload_json"]).get("attempt_id") == identity
                      and "resource_grant" in json.loads(row["payload_json"])]
        if len(dispatches) != 1: raise ValueError("measurement lacks one exact Attempt GPU allocation")
        dispatch = dispatches[0]; kind, devices = _raw_grant_devices(dispatch["resource_grant"])
        if (kind != "GPU" or intent.get("kind") != "GPU" or len(devices) != intent.get("gpu_count")
                or not set(devices) <= set(intent["allowed_gpu_uuids"])
                or dispatch.get("gpu_pool_key") != {"namespace": pool["namespace"], "run_id": request["run_id"],
                    "execution_id": source["execution_id"], "attempt_id": identity}):
            raise ValueError("measurement GPU grant or pool key differs from its source Attempt")
        observation = context["observations"][identity]
        if observation.get("terminal_reason") != source["terminal_reason"]:
            raise ValueError("measurement source terminal observation differs from its Attempt")
        peak = _raw_owned_peak(observation, source, devices, required=True)
        if peak is not None:
            mib = (peak + 1024**2 - 1) // 1024**2
            result = max(result, ((5 * mib + 255) // 256) * 64)
            measured_success |= source["terminal_reason"] == "SUCCEEDED"
        oom = _raw_reported_gpu_oom(observation, source)
        if oom: result = max(result, ((5 * admitted + 255) // 256) * 64)
        verified_measurements.append({"attempt_id": identity, "workload": estimate["workload"],
            "measurement_status": "complete" if peak is not None else "unknown",
            "sample_peak_bytes": peak, "terminal_status": source["terminal_reason"],
            "ended_at": source["ended_at"], "oom": oom, "admitted_workload_memory_mib": admitted})
    if not measured_success: result = max(result, initial)
    if request["schema_version"] >= 27:
        # Ownership, time, GPU allocation and raw measurement validation above
        # remain independent. Reuse the policy rather than a second reducer.
        from .resource_experience import explain_measured_memory
        explanation = explain_measured_memory(floor, estimate["peak_memory_mib"],
            verified_measurements, initial_memory_mib=initial, adaptive=True)
        result = explanation["resolved_workload_memory_mib"]
        if planning is not None: planning.update(explanation)
    if not 0 < result < 2**53: raise ValueError("learned workload budget is not JSON-safe")
    memo[command_id] = result
    return result


def _audit_resource_contract(
    fact_id: str,
    payload: Mapping[str, Any],
    contract: Mapping[str, Any],
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    budget: Mapping[str, Any],
    issues: list[OracleIssue],
    *,
    cpu_only_artifact: bool = False,
    document: Mapping[str, Any] | None = None,
    research_contract_version: str | None = None,
    pool_binding: Mapping[str, Any] | None = None,
    learning_context: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    initial_issue_count = len(issues)
    shared_version = research_contract_version in {"hypoloom-research-v11", "hypoloom-research-v12"}
    if research_contract_version != "hypoloom-research-v12" and "resource_estimate" in response.get("arguments", {}):
        issues.append(OracleIssue("resource_learning_source_invalid", "retained resource contracts cannot acquire learned estimate semantics", (fact_id,)))
    if shared_version and not _valid_gpu_pool_binding(pool_binding):
        issues.append(OracleIssue("gpu_pool_configuration_invalid", "shared-resource run lacks its configured pool endpoint and namespace", (fact_id,)))
    candidate = response.get("arguments", {}).get("candidate") if response.get("kind") == "admitted-submit/1" else response.get("typed_candidate")
    agent_preference = candidate.get("resource_preference") if isinstance(candidate, Mapping) else None
    fact_preference = payload.get("resource_preference")
    contract_preference = contract.get("resource_preference")
    if (
        agent_preference not in {"CPU", "AUTO", "GPU_REQUIRED"}
        or fact_preference != agent_preference
        or contract_preference != agent_preference
    ):
        issues.append(OracleIssue(
            "agent_resource_request_mismatch",
            "Agent resource preference and preregistered fact/contract differ",
            (fact_id,),
        ))
    requested_gpus = response["arguments"].get("gpus") if response.get("kind") == "admitted-submit/1" else None
    multiple_supported = type(request.get("schema_version")) is int and request["schema_version"] >= 14
    count_valid = requested_gpus is None or (type(requested_gpus) is int and requested_gpus >= 0
        and (multiple_supported or requested_gpus <= 1)
        and not (requested_gpus == 0 and agent_preference == "GPU_REQUIRED" or requested_gpus > 0 and agent_preference == "CPU"))
    if count_valid and requested_gpus is not None: contract_preference = "CPU" if requested_gpus == 0 else "GPU_REQUIRED"
    count = (0 if contract_preference == "CPU" else 1) if requested_gpus is None else requested_gpus
    cap = document.get("methodology", {}).get("controls", {}).get("product", {}).get("safety", {}).get("max_gpus_per_experiment", 1) if isinstance(document, Mapping) else 1
    count_valid = count_valid and type(cap) is int and cap >= 1 and type(count) is int and 0 <= count <= cap
    gpus, busy_gpu_uuids, capability_error = _decode_compute_capabilities(
        request,
        expected_schemas=(2, 3)
        if payload.get("schema_version") in {4, 5}
        else (1,),
    )
    if capability_error is not None:
        issues.append(OracleIssue(
            f"compute_capability_snapshot_{capability_error}",
            "source Agent request lacks one valid canonical compute capability snapshot",
            (fact_id,),
        ))
        gpus = ()
    if isinstance(document, Mapping):
        selected_devices = document.get("methodology", {}).get("controls", {}).get("product", {}).get("safety", {}).get("gpus", "auto")
        if selected_devices != "auto" and (not isinstance(selected_devices, list) or any(
                not any((type(item) is int and item == gpu["index"]) or (isinstance(item, str) and item == gpu["uuid"])
                    for item in selected_devices) for gpu in gpus)):
            issues.append(OracleIssue("compute_capability_profile_mismatch",
                "frozen authorized inventory exceeds the original Profile GPU selection", (fact_id,)))
    if "resource_intent" in contract:
        raw = contract["resource_intent"]
        expected_keys = {"kind", "allowed_gpu_uuids", "gpu_count", "workload_memory_mib", "reserve_memory_mib"}
        adaptive = request.get("schema_version", 0) >= 27
        if adaptive: expected_keys.add("sharing")
        environment = _raw_resource_environment(contract)
        minimum, reserve = budget.get("min_vram_per_experiment", 0), budget.get("gpu_vram_reserve_mb", 0)
        resolved = minimum
        planning = {}
        if research_contract_version == "hypoloom-research-v12":
            try: resolved = _raw_learned_workload_budget(contract, request, response, minimum, learning_context, pool_binding,
                initial_memory_mib=budget.get("initial_vram_per_experiment"), planning=planning)
            except (KeyError, TypeError, ValueError, RecursionError) as exc:
                issues.append(OracleIssue("resource_learning_source_invalid", str(exc), (fact_id,)))
        valid = (shared_version and _valid_gpu_pool_binding(pool_binding) and "resource_grant" not in contract
            and isinstance(raw, Mapping) and set(raw) == expected_keys and raw.get("kind") == "GPU"
            and (not adaptive or raw.get("sharing") == planning.get("sharing"))
            and count_valid and count > 0 and not cpu_only_artifact and contract_preference in {"AUTO", "GPU_REQUIRED"}
            and isinstance(raw.get("allowed_gpu_uuids"), list)
            and raw["allowed_gpu_uuids"] == sorted(gpu["uuid"] for gpu in gpus)
            and len(gpus) >= count and type(raw.get("gpu_count")) is int and raw["gpu_count"] == count
            and type(minimum) is int and 0 < minimum <= 2**53 - 1
            and ("initial_vram_per_experiment" not in budget or type(budget["initial_vram_per_experiment"]) is int)
            and type(reserve) is int and 0 <= reserve <= 2**53 - 1
            and type(raw.get("workload_memory_mib")) is int and raw["workload_memory_mib"] == resolved
            and type(raw.get("reserve_memory_mib")) is int and raw["reserve_memory_mib"] == reserve
            and contract.get("sandbox_profile") == ("bwrap-gpu-multi-no-network/1" if count > 1 else "bwrap-gpu-single-no-network/1")
            and environment is not None and environment.get("CUDA_VISIBLE_DEVICES") == ""
            and environment.get("CUDA_DEVICE_ORDER") == "PCI_BUS_ID")
        if not valid:
            issues.append(OracleIssue("resource_intent_mismatch", "shared GPU intent differs from its version, source count, authorized inventory or Profile floor", (fact_id,)))
        return {"shared": True, "intent": raw, "pool_binding": pool_binding,
                "attributable": research_contract_version == "hypoloom-research-v12",
                "source_valid": valid and len(issues) == initial_issue_count,
                "expected_kind": "GPU", "environment": environment,
                "gpu_by_uuid": {gpu["uuid"]: gpu for gpu in gpus}, "gpu_uuids": (),
                "reserve_memory_mib": reserve, "telemetry_required": True}
    grant = contract.get("resource_grant")
    grant_kind = grant.get("kind") if isinstance(grant, Mapping) else None
    try:
        grant_kind, grant_uuids = _raw_grant_devices(grant, allow_multiple=multiple_supported)
        valid_grant = True
    except ValueError:
        grant_uuids, valid_grant = (), False
    grant_uuid = grant_uuids[0] if len(grant_uuids) == 1 else None
    minimum = budget.get("min_vram_per_experiment", 0)
    reserve = budget.get("gpu_vram_reserve_mb", 0)
    thresholds_valid = all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (minimum, reserve)
    )
    ranked = tuple(
        sorted(
            gpus,
            key=lambda gpu: (-int(gpu["free_memory_mib"]), str(gpu["uuid"])),
        )
        if gpus and all(isinstance(gpu.get("free_memory_mib"), int) for gpu in gpus)
        else gpus
    )
    available = tuple(gpu for gpu in ranked if gpu["uuid"] not in busy_gpu_uuids)
    qualified = tuple(
        gpu
        for gpu in available
        if "free_memory_mib" not in gpu
        or thresholds_valid
        and gpu["free_memory_mib"] >= minimum + reserve
    )
    selected_count = count if count_valid else 0
    if not count_valid:
        expected = None
    elif cpu_only_artifact:
        expected = ("CPU", ()) if contract_preference in {"CPU", "AUTO"} else None
    elif contract_preference == "CPU":
        expected = ("CPU", ())
    elif contract_preference == "AUTO":
        candidates = ranked if shared_version else qualified
        expected = ("GPU", tuple(sorted(gpu["uuid"] for gpu in candidates[:selected_count]))) if len(candidates) >= selected_count else ("CPU", ())
    elif contract_preference == "GPU_REQUIRED" and len(ranked) >= selected_count:
        candidates = (*qualified, *(gpu for gpu in available if gpu not in qualified), *(gpu for gpu in ranked if gpu["uuid"] in busy_gpu_uuids))
        expected = ("GPU", tuple(sorted(gpu["uuid"] for gpu in candidates[:selected_count])))
    else:
        expected = None
    if shared_version and grant_kind == "GPU":
        issues.append(OracleIssue("resource_intent_mismatch", "shared-resource GPU preregistration requires an intent instead of an actual grant", (fact_id,)))
    if not valid_grant or expected is None or (grant_kind, grant_uuids) != expected:
        issues.append(OracleIssue(
            "resource_resolution_mismatch",
            "frozen resource grant is not the deterministic result of the Agent request and capability snapshot",
            (fact_id,),
        ))
    gpu_by_uuid = {gpu["uuid"]: gpu for gpu in gpus}
    for unknown_uuid in (uuid for uuid in grant_uuids if uuid not in gpu_by_uuid):
        issues.append(OracleIssue(
            "unauthorized_device_binding",
            "GPU grant is absent from the request-bound authorized capability snapshot",
            (fact_id, unknown_uuid),
        ))
    environment_map = _raw_resource_environment(contract)
    expected_profile = ("bwrap-gpu-multi-no-network/1" if len(grant_uuids) > 1 else "bwrap-gpu-single-no-network/1") if grant_kind == "GPU" else "bwrap-cpu-no-network/2"
    expected_visible = ",".join(grant_uuids)
    if (
        contract.get("sandbox_profile") != expected_profile
        or environment_map is None
        or environment_map.get("CUDA_VISIBLE_DEVICES") != expected_visible
        or environment_map.get("CUDA_DEVICE_ORDER") != "PCI_BUS_ID"
    ):
        issues.append(OracleIssue(
            "resource_resolution_mismatch",
            "execution profile/environment does not materialize the frozen resource grant",
            (fact_id,),
        ))
    selected = gpu_by_uuid.get(grant_uuid)
    artifacts = request.get("input_artifacts")
    resource_refs = (
        {
            str(item["ref"])
            for item in artifacts
            if isinstance(item, Mapping) and isinstance(item.get("ref"), str)
        }
        if isinstance(artifacts, list)
        else set()
    )
    # The current local-compute/4 bundle binds both inputs into the source
    # Agent request.  Older frozen bundles do not, so their GPU observations
    # remain readable without retroactively requiring the new telemetry.
    telemetry_required = all(
        any(ref.endswith(suffix) for ref in resource_refs)
        for suffix in ("--resource-planning-skill", "--resource-experience")
    )
    return {
        "expected_kind": None if expected is None else expected[0],
        "grant_kind": grant_kind,
        "gpu_uuid": grant_uuid,
        "gpu_index": None if selected is None else selected["index"],
        "gpu_uuids": grant_uuids,
        "gpu_indices": tuple(gpu_by_uuid[uuid]["index"] for uuid in grant_uuids if uuid in gpu_by_uuid),
        "environment": environment_map,
        "reserve_memory_mib": reserve if thresholds_valid else None,
        "telemetry_required": telemetry_required,
    }


def _valid_gpu_pool_binding(value: Any) -> bool:
    return (isinstance(value, Mapping) and set(value) == {"socket_path", "namespace", "server_uid"}
        and _trimmed_text(value.get("socket_path")) and PurePosixPath(value["socket_path"]).is_absolute()
        and isinstance(value.get("namespace"), str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", value["namespace"]) is not None
        and (value["server_uid"] is None or type(value["server_uid"]) is int and 0 <= value["server_uid"] <= 2**53 - 1))


def _raw_resource_environment(contract: Mapping[str, Any]) -> dict[str, str] | None:
    environment = contract.get("environment")
    if isinstance(environment, list) and all(
            isinstance(item, list) and len(item) == 2 and all(isinstance(value, str) for value in item)
            for item in environment):
        names = [item[0] for item in environment]
        if names == sorted(set(names)):
            return dict(environment)
    return None


def _audit_shared_process_resource(resource: Mapping[str, Any], intent: Mapping[str, Any],
                                   attempt: Mapping[str, Any], observation: Mapping[str, Any],
                                   issues: list[OracleIssue]) -> dict[str, Any]:
    """Bind one immutable dispatch grant without changing the frozen source context."""
    result = dict(resource)
    result["attribution_attempt"] = dict(attempt)
    result.update(expected_kind=None, gpu_uuids=(), gpu_indices=(), gpu_uuid=None, gpu_index=None)
    environment = dict(resource.get("environment") or {})
    result["environment"] = environment
    identity = str(attempt["attempt_id"])
    if ("resource_grant" not in intent and observation.get("synthetic_before_spawn") is True
            and attempt["state"] == "CLOSED" and attempt["terminal_reason"] in {"CANCELLED", "FAILED", "TIMED_OUT"}
            and attempt["process_group_id"] is None and "gpu_pool_key" not in intent
            and intent.get("gpu_uuid") is None and not intent.get("gpu_uuids")
            and resource.get("source_valid") is True):
        environment["CUDA_VISIBLE_DEVICES"] = ""
        result.update(expected_kind="CPU", telemetry_required=False)
        return result
    try:
        kind, devices = _raw_grant_devices(intent.get("resource_grant"))
        declared = resource["intent"]
        pool = resource["pool_binding"]
        expected_key = {"namespace": pool["namespace"], **{name: attempt[name] for name in ("run_id", "execution_id", "attempt_id")}}
        expected_identity = {name: value for name, value in intent["resource_grant"].items() if name != "kind"}
        actual_identity = {name: intent[name] for name in ("gpu_uuid", "gpu_uuids") if name in intent}
        valid = (resource.get("source_valid") is True and kind == "GPU"
            and len(devices) == declared["gpu_count"] and set(devices) <= set(declared["allowed_gpu_uuids"])
            and intent.get("gpu_pool_key") == expected_key and actual_identity == expected_identity)
        if not valid:
            raise ValueError("dispatch grant, device identity or pool key differs from its source and Attempt")
        indices = tuple(resource["gpu_by_uuid"][uuid]["index"] for uuid in devices)
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(OracleIssue("shared_gpu_grant_mismatch", str(exc), (identity,)))
        return result
    environment["CUDA_VISIBLE_DEVICES"] = ",".join(devices)
    result.update(expected_kind="GPU", grant_kind="GPU", gpu_uuids=devices, gpu_indices=indices,
                  gpu_uuid=devices[0] if len(devices) == 1 else None,
                  gpu_index=indices[0] if len(indices) == 1 else None)
    return result


def _decode_compute_capabilities(
    request: Mapping[str, Any],
    *,
    expected_schemas: Sequence[int],
) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...], str | None]:
    artifacts = request.get("input_artifacts")
    if not isinstance(artifacts, list):
        return (), (), "invalid"
    matches = [item for item in artifacts if isinstance(item, Mapping)
               and isinstance(item.get("ref"), str) and item["ref"].endswith("--compute-capabilities")]
    if not matches:
        return (), (), "missing"
    if len(matches) != 1 or matches[0].get("media_type") != "application/json":
        return (), (), "invalid"
    encoded = matches[0].get("content_base64")
    try:
        raw = base64.b64decode(encoded, validate=True)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return (), (), "invalid"
    schema_version = value.get("schema_version") if isinstance(value, Mapping) else None
    expected_keys = {"schema_version", "gpus"} | ({"busy_gpu_uuids"} if schema_version == 3 else set())
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_keys
        or isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in expected_schemas
    ):
        return (), (), "invalid"
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return (), (), "invalid"
    if canonical != raw:
        return (), (), "invalid"
    gpus = value.get("gpus")
    if not isinstance(gpus, list):
        return (), (), "invalid"
    expected_keys = {"index", "uuid", "name", "total_memory_mib", "driver_version"}
    if schema_version in {2, 3}:
        expected_keys.add("free_memory_mib")
    valid = all(
        isinstance(gpu, Mapping) and set(gpu) == expected_keys
        and isinstance(gpu.get("index"), int) and not isinstance(gpu.get("index"), bool) and gpu["index"] >= 0
        and _physical_gpu_uuid(gpu.get("uuid"))
        and _trimmed_text(gpu.get("name"))
        and isinstance(gpu.get("total_memory_mib"), int)
        and not isinstance(gpu.get("total_memory_mib"), bool)
        and gpu["total_memory_mib"] > 0
        and (
            schema_version == 1
            or isinstance(gpu.get("free_memory_mib"), int)
            and not isinstance(gpu.get("free_memory_mib"), bool)
            and 0 <= gpu["free_memory_mib"] <= gpu["total_memory_mib"]
        )
        and _trimmed_text(gpu.get("driver_version"))
        for gpu in gpus
    )
    uuids = [gpu["uuid"] for gpu in gpus] if valid else []
    indices = [gpu["index"] for gpu in gpus] if valid else []
    busy = value.get("busy_gpu_uuids", [])
    busy_valid = (isinstance(busy, list) and all(_physical_gpu_uuid(uuid) for uuid in busy)
                  and busy == sorted(set(busy)) and set(busy) <= set(uuids))
    if not valid or uuids != sorted(uuids) or len(uuids) != len(set(uuids)) or len(indices) != len(set(indices)):
        return (), (), "invalid"
    if not busy_valid:
        return (), (), "invalid"
    return tuple(gpus), tuple(busy), None


def _audit_process_resource(
    request: Any,
    resource: Any,
    attempt_id: str,
    source_relative_path: Any,
    execution_argv: Any,
    component_mounts: Any,
    issues: list[OracleIssue],
    *, allow_generic: bool = False,
) -> None:
    if not isinstance(request, Mapping) or not isinstance(resource, Mapping):
        return
    argv = request.get("argv")
    expected_kind = resource.get("expected_kind")
    expected_environment = resource.get("environment")
    process_environment = None
    if isinstance(expected_environment, Mapping):
        process_environment = dict(expected_environment)
        process_environment.setdefault("HOME", "/nonexistent")
        process_environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        process_environment.setdefault("PYTHONNOUSERSITE", "1")
        process_environment.setdefault("TMPDIR", "/tmp")
    expected_bindings: tuple[tuple[str, str], ...] = ()
    if expected_kind == "CPU":
        pass
    elif expected_kind == "GPU":
        gpu_indices = resource.get("gpu_indices", (resource.get("gpu_index"),))
        gpu_uuids = resource.get("gpu_uuids", (resource.get("gpu_uuid"),))
        expected_bindings = (
            *((f"/dev/nvidia{index}", f"/dev/nvidia{index}") for index in gpu_indices),
            ("/dev/nvidiactl", "/dev/nvidiactl"),
            ("/dev/nvidia-uvm", "/dev/nvidia-uvm"),
        )
        if not gpu_uuids or len(gpu_indices) != len(gpu_uuids) or any(not isinstance(uuid, str) for uuid in gpu_uuids):
            expected_bindings = ()
    valid = (
        expected_kind in {"CPU", "GPU"}
        and process_environment is not None
        and isinstance(source_relative_path, str)
        and _canonical_bwrap_argv(
            argv,
            attempt_id=attempt_id,
            source_relative_path=source_relative_path,
            execution_argv=execution_argv,
            expected_bindings=expected_bindings,
            expected_environment=process_environment,
            expected_component_mounts=(
                component_mounts
                if isinstance(component_mounts, _OracleComponentMounts)
                else None
            ),
            allow_generic=allow_generic,
        )
        and request.get("env") == {}
        and _canonical_attempt_cwd(request.get("cwd"), attempt_id)
    )
    if not valid:
        issues.append(OracleIssue(
            "process_resource_binding_mismatch",
            "ProcessRequest is not the canonical bwrap skeleton for its frozen resource grant",
            (attempt_id,),
        ))


def _audit_process_resource_observation(
    observation: Any,
    resource: Any,
    attempt_id: str,
    issues: list[OracleIssue],
) -> None:
    if not isinstance(observation, Mapping) or not isinstance(resource, Mapping):
        return
    usage = observation.get("resource_usage")
    pressure = observation.get("resource_pressure")
    if usage is None and pressure is None:
        if (
            resource.get("expected_kind") == "GPU"
            and resource.get("telemetry_required") is True
            and observation.get("synthetic_before_spawn") is not True
            and observation.get("terminal_reason") != "LOST"
        ):
            issues.append(
                OracleIssue(
                    "process_resource_observation_mismatch",
                    "current-bundle GPU terminal observation lacks device-level resource usage",
                    (attempt_id,),
                )
            )
        return
    devices = resource.get("gpu_uuids", ())
    if len(devices) > 1:
        valid = (isinstance(usage, list) and len(usage) == len(devices) and all(isinstance(item, Mapping) for item in usage)
            and tuple(item.get("gpu_uuid") for item in usage) == tuple(devices))
        pressured = [item["gpu_uuid"] for item in usage if item.get("pressure_exceeded") is True] if valid else []
        if pressure is not None:
            renamed = isinstance(pressure, Mapping) and set(pressure) == {"reason", "gpu_reserve_memory_mib", "owned_memory_mib_at_decision", "attribution", "terminate_grace_seconds", "gpu_uuids"}
            valid = valid and isinstance(pressure, Mapping) and (
                set(pressure) == {"reason", "reserve_memory_mib", "gpu_uuids"}
                or renamed and pressure.get("attribution") == ("self" if any(item.get("pressure_attribution") == "self" for item in usage if item.get("gpu_uuid") in pressured) else "unknown")
            ) and pressure.get("gpu_uuids") == pressured and bool(pressured)
        else:
            valid = valid and (not pressured or all(item.get("pressure_attribution") == "external" for item in usage if item["gpu_uuid"] in pressured))
        if not valid:
            issues.append(OracleIssue("process_resource_observation_mismatch", "multi-device usage or pressure differs from its exact frozen GPU set", (attempt_id,)))
            return
        for item in usage:
            per_pressure = ({key: value for key, value in pressure.items() if key != "gpu_uuids"}
                if pressure is not None and item["gpu_uuid"] in pressured and item.get("pressure_attribution") != "external" else None)
            _audit_process_resource_observation({**observation, "resource_usage": item, "resource_pressure": per_pressure},
                {**resource, "gpu_uuid": item["gpu_uuid"], "gpu_uuids": ()}, attempt_id, issues)
        return
    memory_keys = (
        "total_memory_mib",
        "initial_free_memory_mib",
        "minimum_free_memory_mib",
        "latest_free_memory_mib",
        "latest_used_memory_mib",
        "latest_utilization_percent",
    )
    renamed = isinstance(usage, Mapping) and any(f"gpu_{name}" in usage for name in memory_keys)
    prefix = "gpu_" if renamed else ""
    expected_keys = {f"{prefix}{name}" for name in memory_keys} | {
        "gpu_uuid",
        "sample_count",
        "pressure_exceeded",
    }
    if resource.get("attributable") is True: expected_keys.add("owned_memory")
    if renamed and usage.get("pressure_exceeded") is True: expected_keys.add("pressure_attribution")
    valid = isinstance(usage, Mapping) and set(usage) == expected_keys
    if valid and resource.get("attributable") is True:
        try:
            _raw_owned_peak(observation, resource["attribution_attempt"], (resource.get("gpu_uuid"),), required=True)
            _raw_reported_gpu_oom(observation, resource["attribution_attempt"])
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(OracleIssue("owned_gpu_measurement_invalid", str(exc), (attempt_id,)))
            valid = False
    if valid:
        total = usage[f"{prefix}total_memory_mib"]
        memory = tuple(
            usage[f"{prefix}{name}"]
            for name in (
                "initial_free_memory_mib",
                "minimum_free_memory_mib",
                "latest_free_memory_mib",
                "latest_used_memory_mib",
            )
        )
        utilization = usage[f"{prefix}latest_utilization_percent"]
        valid = (
            resource.get("expected_kind") == "GPU"
            and usage["gpu_uuid"] == resource.get("gpu_uuid")
            and isinstance(total, int)
            and not isinstance(total, bool)
            and total > 0
            and all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= total
                for value in memory
            )
            and usage[f"{prefix}minimum_free_memory_mib"]
            <= min(
                usage[f"{prefix}initial_free_memory_mib"],
                usage[f"{prefix}latest_free_memory_mib"],
            )
            and usage[f"{prefix}latest_free_memory_mib"]
            + usage[f"{prefix}latest_used_memory_mib"]
            == total
            and (
                utilization is None
                or isinstance(utilization, int)
                and not isinstance(utilization, bool)
                and 0 <= utilization <= 100
            )
            and isinstance(usage["sample_count"], int)
            and not isinstance(usage["sample_count"], bool)
            and usage["sample_count"] > 0
            and isinstance(usage["pressure_exceeded"], bool)
        )
    if valid and renamed and usage["pressure_exceeded"] is True:
        valid = usage.get("pressure_attribution") in {"external", "self", "unknown"}
    if renamed:
        reserve = pressure.get("gpu_reserve_memory_mib") if isinstance(pressure, Mapping) else None
        grace = pressure.get("terminate_grace_seconds") if isinstance(pressure, Mapping) else None
        pressure_valid = (
            isinstance(pressure, Mapping)
            and set(pressure) == {"reason", "gpu_reserve_memory_mib", "owned_memory_mib_at_decision", "attribution", "terminate_grace_seconds"}
            and pressure.get("reason") == "sustained_free_gpu_memory_below_reserve"
            and isinstance(reserve, int)
            and not isinstance(reserve, bool)
            and reserve == resource.get("reserve_memory_mib")
            and pressure.get("attribution") in {"self", "unknown"}
            and (
                pressure.get("owned_memory_mib_at_decision") is None
                or isinstance(pressure["owned_memory_mib_at_decision"], int)
                and not isinstance(pressure["owned_memory_mib_at_decision"], bool)
                and pressure["owned_memory_mib_at_decision"] >= 0
            )
            and isinstance(grace, (int, float))
            and not isinstance(grace, bool)
            and 0 < grace < float("inf")
            and observation.get("terminal_reason") == "FAILED" and valid
            and usage[f"{prefix}minimum_free_memory_mib"] < reserve
            and usage[f"{prefix}latest_free_memory_mib"] < reserve
        )
    else:
        pressure_valid = (
            isinstance(pressure, Mapping)
            and set(pressure) == {"reason", "reserve_memory_mib"}
            and pressure.get("reason") == "sustained_free_memory_below_reserve"
            and isinstance(pressure.get("reserve_memory_mib"), int)
            and not isinstance(pressure.get("reserve_memory_mib"), bool)
            and pressure["reserve_memory_mib"] == resource.get("reserve_memory_mib")
            and observation.get("terminal_reason") == "FAILED" and valid and usage["minimum_free_memory_mib"] < pressure["reserve_memory_mib"] and usage["latest_free_memory_mib"] < pressure["reserve_memory_mib"]
        )
    if valid:
        attribution = usage.get("pressure_attribution") if renamed else None
        valid = (
            usage["pressure_exceeded"] is True
            and (
                attribution == "external"
                and pressure is None
                or attribution != "external"
                and pressure_valid
            )
        ) or (usage["pressure_exceeded"] is False and pressure is None)
    if not valid:
        issues.append(
            OracleIssue(
                "process_resource_observation_mismatch",
                "device-level resource observation is malformed or differs from the frozen GPU grant",
                (attempt_id,),
            )
        )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _trimmed_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and "\x00" not in value
    )


def _physical_gpu_uuid(value: Any) -> bool:
    if not _trimmed_text(value) or not value.startswith("GPU-"):
        return False
    parts = value[4:].split("-")
    return [len(part) for part in parts] == [8, 4, 4, 4, 12] and all(
        character in "0123456789abcdefABCDEF"
        for part in parts
        for character in part
    )


def _canonical_attempt_cwd(value: Any, attempt_id: str) -> bool:
    if not isinstance(value, str):
        return False
    path = PurePosixPath(value)
    return (
        path.is_absolute()
        and len(path.parts) >= 4
        and path.parts[-3:] == ("work", attempt_id, "logs")
    )


def _canonical_bwrap_argv(
    argv: Any,
    *,
    attempt_id: str,
    source_relative_path: str,
    execution_argv: Any,
    expected_bindings: tuple[tuple[str, str], ...],
    expected_environment: Mapping[str, Any],
    expected_component_mounts: _OracleComponentMounts | None = None,
    allow_generic: bool = False,
) -> bool:
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return False
    if not argv or PurePosixPath(argv[0]).name != "bwrap" or not PurePosixPath(argv[0]).is_absolute():
        return False
    index = 1

    def consume(*values: str) -> bool:
        nonlocal index
        if argv[index:index + len(values)] != list(values):
            return False
        index += len(values)
        return True

    if not consume(
        "--unshare-all", "--die-with-parent", "--new-session",
        "--proc", "/proc", "--dev", "/dev",
    ):
        return False
    actual_bindings: list[tuple[str, str]] = []
    while argv[index:index + 1] == ["--dev-bind"] and index + 2 < len(argv):
        actual_bindings.append((argv[index + 1], argv[index + 2]))
        index += 3
    if tuple(actual_bindings) != expected_bindings:
        return False
    if not consume("--tmpfs", "/tmp", "--dir", "/runtime", "--ro-bind"):
        return False
    if index + 1 >= len(argv):
        return False
    runtime_source, runtime_target = argv[index:index + 2]
    index += 2
    if (
        runtime_target != "/runtime"
        or not _safe_runtime_source(runtime_source)
    ):
        return False

    while argv[index:index + 2] != ["--dir", "/input"]:
        if index + 1 >= len(argv):
            return False
        if argv[index] == "--dir":
            if not _safe_runtime_mount_target(argv[index + 1]):
                return False
            index += 2
            continue
        if argv[index] == "--ro-bind" and index + 2 < len(argv):
            source, target = argv[index + 1:index + 3]
            if source != target or not _safe_runtime_mount_target(target):
                return False
            index += 3
            continue
        return False
    if not consume("--dir", "/input", "--dir", "/output", "--ro-bind"):
        return False
    if index + 1 >= len(argv):
        return False
    input_source, input_target = argv[index:index + 2]
    index += 2
    if input_target != "/input" or not _attempt_mount(input_source, attempt_id, "input"):
        return False
    if not consume("--bind") or index + 1 >= len(argv):
        return False
    output_source, output_target = argv[index:index + 2]
    index += 2
    if (
        output_target != "/output"
        or not _attempt_mount(output_source, attempt_id, "output")
        or PurePosixPath(input_source).parent != PurePosixPath(output_source).parent
    ):
        return False

    extra_directories: list[str] = []
    extra_bindings: list[tuple[str, str]] = []
    while index < len(argv) and argv[index] in {"--dir", "--ro-bind"}:
        if argv[index] == "--dir" and index + 1 < len(argv):
            extra_directories.append(argv[index + 1])
            index += 2
        elif argv[index] == "--ro-bind" and index + 2 < len(argv):
            extra_bindings.append((argv[index + 1], argv[index + 2]))
            index += 3
        else:
            return False
    if not _valid_component_mounts(
        extra_directories,
        extra_bindings,
        attempt_id,
        expected_environment,
        expected_component_mounts,
    ):
        return False

    if not all(isinstance(name, str) and isinstance(value, str)
               for name, value in expected_environment.items()):
        return False
    for name, value in sorted(expected_environment.items()):
        if value:
            if not consume("--setenv", name, value):
                return False
        elif not consume("--unsetenv", name):
            return False
    if not consume("--chdir", "/output", "--") or index >= len(argv):
        return False
    executable = argv[index]
    command = argv[index + 1:]
    executable_path = PurePosixPath(executable)
    if not isinstance(execution_argv, tuple) or not execution_argv:
        return False
    expected_command = list(execution_argv[1:])
    if expected_command and expected_command[0] == source_relative_path:
        expected_command[0] = str(PurePosixPath("/input") / source_relative_path)
    if execution_argv[0] != "python":
        return allow_generic and executable == execution_argv[0] and command == expected_command
    return (
        executable_path.is_absolute()
        and executable_path.parts[:3] == ("/", "runtime", "bin")
        and executable_path.name.startswith("python")
        and command == expected_command
    )


def _valid_component_mounts(
    directories: Sequence[str],
    bindings: Sequence[tuple[str, str]],
    attempt_id: str,
    environment: Mapping[str, Any],
    expected: _OracleComponentMounts | None = None,
) -> bool:
    if (
        len(directories) != len(set(directories))
        or any(not _safe_runtime_mount_target(value) for value in directories)
        or len(bindings) != len({target for _, target in bindings})
    ):
        return False
    try:
        raw_reads = environment.get("HYPOLOOM_READ_ROOTS", "[]")
        raw_inputs = environment.get("HYPOLOOM_REQUIRED_INPUTS", "{}")
        reads = json.loads(raw_reads)
        inputs = json.loads(raw_inputs)
    except (TypeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(reads, list)
        or any(not isinstance(value, str) for value in reads)
        or len(reads) != len(set(reads))
        or not isinstance(inputs, Mapping)
        or any(
            not _trimmed_text(name)
            or not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) for value in values)
            for name, values in inputs.items()
        )
    ):
        return False
    input_targets = [value for values in inputs.values() for value in values]
    target = environment.get("HYPOLOOM_TARGET_ROOT")
    if expected is not None:
        expected_inputs = {
            name: list(values) for name, values in expected.required_inputs
        }
        if (
            reads != list(expected.read_roots)
            or dict(inputs) != expected_inputs
            or target != expected.target_root
            or not _oracle_read_root_map_matches(environment, expected)
            or len(bindings) != len(expected.bindings)
        ):
            return False
        for (source, destination), required in zip(bindings, expected.bindings):
            if (
                destination != required.destination
                or not _safe_host_mount(source)
                or not _safe_runtime_mount_target(destination)
            ):
                return False
            if required.attempt_leaf is not None:
                if required.source is not None or not _attempt_mount(
                    source, attempt_id, required.attempt_leaf
                ):
                    return False
            elif required.source is None or source != required.source:
                return False
        return True
    if "HYPOLOOM_READ_ROOT_MAP" in environment:
        # An alias needs an independently reconstructed authorized source.
        return False
    expected_targets = set(reads) | set(input_targets)
    if target is not None:
        if not isinstance(target, str):
            return False
        expected_targets.add(target)
    if {destination for _, destination in bindings} != expected_targets:
        return False
    for source, destination in bindings:
        if not _safe_host_mount(source) or not _safe_runtime_mount_target(destination):
            return False
        if destination == target:
            if not _attempt_mount(source, attempt_id, "target"):
                return False
        elif destination in reads:
            if source != destination:
                return False
        elif destination in input_targets:
            source_path = Path(source)
            if source_path.is_symlink() or not source_path.is_file():
                return False
        else:
            return False
    return True


def _safe_host_mount(value: str) -> bool:
    path = PurePosixPath(value)
    return path.is_absolute() and value != "/" and "\x00" not in value


def _safe_runtime_mount_target(value: str) -> bool:
    if not _safe_host_mount(value):
        return False
    forbidden = ("/dev", "/proc", "/sys", "/input", "/output", "/runtime", "/tmp")
    return not any(value == root or value.startswith(f"{root}/") for root in forbidden)


def _safe_runtime_source(value: str) -> bool:
    if not _safe_host_mount(value):
        return False
    forbidden = ("/dev", "/proc", "/sys")
    return not any(value == root or value.startswith(f"{root}/") for root in forbidden)


def _attempt_mount(value: str, attempt_id: str, leaf: str) -> bool:
    path = PurePosixPath(value)
    return (
        path.is_absolute()
        and len(path.parts) >= 4
        and path.parts[-3:] == ("work", attempt_id, leaf)
    )


def _decode_workspace(response: Mapping[str, Any]) -> dict[str, bytes] | None:
    artifacts = response.get("arguments", {}).get("workspace_files") if response.get("kind") == "admitted-submit/1" else response.get("proposed_workspace_artifacts")
    if not isinstance(artifacts, list):
        return None
    result: dict[str, bytes] = {}
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "content_base64"}:
            return None
        path = _safe_relative_path(artifact.get("path"))
        encoded = artifact.get("content_base64")
        if path is None or path in result or not isinstance(encoded, str):
            return None
        try:
            result[path] = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
    return result


def _valid_process_request(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != _PROCESS_REQUEST_KEYS:
        return False
    if any(
        not isinstance(value.get(name), str) or not value[name] or "\x00" in value[name]
        for name in ("attempt_id", "execution_id", "cwd")
    ):
        return False
    ordinal = value.get("attempt_ordinal")
    argv = value.get("argv")
    environment = value.get("env")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal <= 0:
        return False
    if not isinstance(argv, list) or not argv or any(
        not isinstance(item, str) or not item or "\x00" in item for item in argv
    ):
        return False
    if not isinstance(environment, Mapping) or any(
        not isinstance(name, str)
        or not name
        or "=" in name
        or "\x00" in name
        or not isinstance(item, str)
        or "\x00" in item
        for name, item in environment.items()
    ):
        return False
    for name in ("timeout_seconds", "terminate_grace_seconds", "kill_grace_seconds"):
        number = value.get(name)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 < float(number) < float("inf"):
            return False
    return all(
        value.get(name) is None or isinstance(value.get(name), str) and bool(value.get(name))
        for name in ("stdout_path", "stderr_path")
    )


def _canonical_json_digest(value: Any) -> str | None:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw).hexdigest()


def _safe_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return value


def _safe_path_component(value: str) -> str | None:
    return value if _safe_relative_path(value) == value and len(PurePosixPath(value).parts) == 1 else None


def _artifact_stable_relative_path(row: Mapping[str, Any] | sqlite3.Row) -> str | None:
    components = tuple(
        _safe_path_component(str(row[name]))
        for name in ("run_id", "experiment_id", "execution_id", "attempt_id", "artifact_kind")
    )
    if any(component is None for component in components):
        return None
    return "/".join(component for component in components if component is not None)


def _artifact_materialized_root(artifact_root: Path, row: Mapping[str, Any] | sqlite3.Row) -> Path | None:
    relative = _artifact_stable_relative_path(row)
    if relative is None or str(row["path"]) != relative:
        return None
    try:
        base = artifact_root.resolve(strict=True)
        candidate = base.joinpath(*PurePosixPath(relative).parts)
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir() or not resolved.is_relative_to(base):
            return None
    except OSError:
        return None
    return resolved


def _regular_file_equals(path: Path, expected: bytes) -> bool:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(expected):
            return False
        actual = bytearray()
        while len(actual) < metadata.st_size:
            chunk = os.read(fd, metadata.st_size - len(actual))
            if not chunk:
                break
            actual.extend(chunk)
        return bytes(actual) == expected and not os.read(fd, 1)
    finally:
        os.close(fd)


def _unexpected_staging_residue(root: Path) -> tuple[str, ...]:
    if root.is_symlink() or not root.is_dir():
        return (str(root),)
    invalid: list[str] = []
    try:
        entries = tuple(root.iterdir())
    except OSError:
        return (str(root),)
    for entry in entries:
        if entry.name != "logs" or entry.is_symlink() or not entry.is_dir():
            invalid.append(str(entry))
            continue
        try:
            log_entries = tuple(entry.iterdir())
        except OSError:
            invalid.append(str(entry))
            continue
        for log in log_entries:
            if log.name not in {"stdout.log", "stderr.log"} or log.is_symlink() or not log.is_file():
                invalid.append(str(log))
    return tuple(sorted(invalid))


def _audit_process_registration(runner_version, attempts, payloads, issues):
    """Check the existing binding against raw Runner observations, not a receipt."""
    version = re.fullmatch(r"local-process-group/([1-9][0-9]*)", str(runner_version))
    if version is None or int(version[1]) < 2:
        return
    for attempt in attempts:
        if attempt["kind"] != "PROCESS" or attempt["state"] != "CLOSED":
            continue
        observation = payloads.get(str(attempt["attempt_id"]), {})
        if not isinstance(observation, Mapping):
            continue  # The existing observation validator reports invalid payloads.
        observed = "process_group_id" in observation or "pid" in observation
        if attempt["terminal_reason"] != "SUCCEEDED" and not observed:
            continue  # LOST or pre-exec failure need not have a Runner observation.
        group = attempt["process_group_id"]
        if type(group) is not int or group <= 0 or observation.get("process_group_id") != group:
            issues.append(OracleIssue("process_registration_mismatch",
                "registered PROCESS group is absent or differs from its terminal Runner observation",
                (str(attempt["attempt_id"]),)))


def _audit_attempt_identities(
    attempts: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
) -> tuple[int, int]:
    owners: dict[tuple[str, str, str, int], list[str]] = {}
    attempt_ids: dict[str, tuple[str, str, str, int]] = {}
    conflict_count = 0
    for row in attempts:
        execution = str(row["execution_id"] or "")
        key = (str(row["workflow_id"]), str(row["command_id"]), execution, int(row["attempt_ordinal"]))
        owners.setdefault(key, []).append(str(row["attempt_id"]))
        attempt_id = str(row["attempt_id"])
        prior = attempt_ids.setdefault(attempt_id, key)
        if prior != key:
            conflict_count += 1
    duplicate_groups = [ids for ids in owners.values() if len(ids) > 1]
    for ids in duplicate_groups:
        issues.append(OracleIssue("duplicate_raw_effect", "effect owner/ordinal appears more than once", tuple(ids)))
    process_groups: dict[tuple[str, str, str, str], list[sqlite3.Row]] = {}
    for row in attempts:
        if row["kind"] != "PROCESS":
            continue
        process_groups.setdefault(
            (
                str(row["run_id"]),
                str(row["experiment_id"]),
                str(row["command_id"]),
                str(row["execution_id"]),
            ),
            [],
        ).append(row)
    for identity, rows in process_groups.items():
        ordered = sorted(rows, key=lambda row: int(row["attempt_ordinal"]))
        ordinals = tuple(int(row["attempt_ordinal"]) for row in ordered)
        expected = tuple(range(1, len(ordered) + 1))
        if ordinals != expected:
            issues.append(
                OracleIssue(
                    "process_attempt_ordinal_mismatch",
                    "Process Attempt ordinals are not the contiguous execution sequence",
                    (*identity, *(str(item) for item in ordinals)),
                )
            )
    if conflict_count:
        issues.append(OracleIssue("conflicting_attempt_identity", f"found {conflict_count} conflicting identities"))
    return sum(len(ids) - 1 for ids in duplicate_groups), conflict_count


def _audit_gpu_attempt_overlaps(
    connection: sqlite3.Connection,
    audited_run_id: str,
    issues: list[OracleIssue],
) -> None:
    rows = connection.execute(
        "SELECT a.*,f.payload_json spec_payload "
        "FROM execution_attempts a JOIN research_facts f ON f.run_id=a.run_id "
        "AND f.fact_type='ExperimentPreregistered' AND f.execution_id=a.execution_id "
        "WHERE a.kind='PROCESS' ORDER BY a.started_at,a.attempt_id"
    )
    by_gpu: dict[str, list[tuple[float, float, str, str, tuple[str, int | None] | None]]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        started_at, ended_at = row["started_at"], row["ended_at"]
        shared = None
        try:
            payload = json.loads(row["spec_payload"])
            contract = payload["execution_contract"]
            if "resource_intent" in contract and "resource_grant" not in contract:
                grant, shared = _shared_overlap_grant(connection, row, payload)
            else:
                grant = contract.get("resource_grant")
            if not isinstance(grant, Mapping): continue
            declared = grant.get("gpu_uuids", [])
            gpu_uuids = tuple(value for value in ([grant.get("gpu_uuid")] + declared if isinstance(declared, list) else []) if isinstance(value, str))
        except (KeyError, TypeError, ValueError): continue
        if (
            isinstance(started_at, bool)
            or not isinstance(started_at, (int, float))
            or (
                ended_at is not None
                and (isinstance(ended_at, bool) or not isinstance(ended_at, (int, float)))
            )
        ):
            continue
        for gpu_uuid in gpu_uuids:
            identity = (gpu_uuid, str(row["attempt_id"]))
            if identity in seen: continue
            seen.add(identity)
            by_gpu.setdefault(gpu_uuid, []).append((
                float(started_at), float("inf") if ended_at is None else float(ended_at),
                str(row["run_id"]), str(row["attempt_id"]), shared,
            ))
    for gpu_uuid, intervals in by_gpu.items():
        ordered = sorted(intervals)
        for index, (started_at, ended_at, run_id, attempt_id, shared) in enumerate(ordered):
            for other_start, other_end, other_run_id, other_id, other_shared in ordered[index + 1:]:
                if other_start >= ended_at:
                    break
                if started_at < other_end and audited_run_id in {run_id, other_run_id} and not (shared is not None and shared == other_shared):
                    issues.append(OracleIssue(
                        "same_gpu_attempt_overlap",
                        "PROCESS Attempt intervals overlap on one frozen GPU UUID",
                        (gpu_uuid, attempt_id, other_id),
                    ))


def _shared_overlap_grant(connection: sqlite3.Connection, attempt: sqlite3.Row,
                          payload: Mapping[str, Any]) -> tuple[Any, tuple[str, int | None] | None]:
    """Read each owner's existing sources; cross-root capacity needs the pool audit."""
    grant = None
    try:
        intents = list(connection.execute(
            "SELECT t.payload_json FROM workflow_transitions t JOIN workflow_commands c "
            "ON c.command_id=t.command_id WHERE t.run_id=? AND t.workflow_id=? "
            "AND c.command_type IN ('EXECUTE','OPEN_RETRY_ATTEMPT') "
            "AND json_extract(t.payload_json,'$.attempt_id')=?",
            (attempt["run_id"], attempt["experiment_id"], attempt["attempt_id"])))
        if len(intents) != 1:
            return None, None
        dispatch = json.loads(intents[0]["payload_json"])
        grant = dispatch.get("resource_grant")
        run = connection.execute("SELECT research_contract_version FROM workflow_runs WHERE run_id=?", (attempt["run_id"],)).fetchone()
        creation = connection.execute("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND workflow_kind='RUN' AND revision=1", (attempt["run_id"],)).fetchone()
        goal = connection.execute("SELECT payload_json FROM research_facts WHERE run_id=? AND fact_type='GoalDefined'", (attempt["run_id"],)).fetchone()
        if run is None or creation is None or goal is None or run["research_contract_version"] not in {"hypoloom-research-v11", "hypoloom-research-v12"}:
            return grant, None
        contract = payload["execution_contract"]
        source = connection.execute("SELECT * FROM execution_attempts WHERE attempt_id=? AND run_id=?", (contract["source_agent_attempt_id"], attempt["run_id"])).fetchone()
        if source is None:
            return grant, None
        source_observation = json.loads(source["observation_json"])
        if contract.get("source_submit_command_id") is not None:
            command = connection.execute("SELECT * FROM workflow_commands WHERE command_id=?", (contract["source_submit_command_id"],)).fetchone()
            submitted = None if command is None else _raw_submit_source(command, [source], {str(source["attempt_id"]): source_observation})
            if submitted is None:
                return grant, None
            request, response = submitted
        else:
            if source["kind"] != "AGENT" or source["state"] != "CLOSED" or source["terminal_reason"] != "SUCCEEDED":
                return grant, None
            request, response = source_observation["request"], source_observation["response"]
        document = json.loads(goal["payload_json"])["research_contract"]
        errors: list[OracleIssue] = []
        resource = _audit_resource_contract(str(attempt["attempt_id"]), payload, contract, request, response,
            document.get("budget", {}), errors, document=document,
            cpu_only_artifact=_oracle_cpu_only_artifact(document, payload),
            research_contract_version=run["research_contract_version"], pool_binding=json.loads(creation["payload_json"]).get("gpu_pool"),
            learning_context=_raw_learning_context(connection, attempt["run_id"]) if run["research_contract_version"] == "hypoloom-research-v12" else None)
        if not isinstance(resource, Mapping) or not resource.get("shared"):
            return grant, None
        _audit_shared_process_resource(resource, dispatch, attempt, json.loads(attempt["observation_json"] or "{}"), errors)
        pool = resource["pool_binding"]
        return grant, None if errors else (pool["socket_path"], pool["server_uid"])
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        return grant, None


def _raw_learning_context(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Ordinary read-only inputs for the same independent learning check."""
    result = {name: [dict(row) for row in connection.execute(f"SELECT * FROM {table} WHERE run_id=?", (run_id,))]
              for name, table in (("facts", "research_facts"), ("attempts", "execution_attempts"),
                                  ("commands", "workflow_commands"), ("transitions", "workflow_transitions"))}
    result["fact_payloads"] = {row["fact_id"]: json.loads(row["payload_json"]) for row in result["facts"]}
    result["observations"] = {row["attempt_id"]: json.loads(row["observation_json"] or "{}") for row in result["attempts"]}
    return result


def _audit_artifacts(
    artifact_root: Path,
    run_id: str,
    artifacts: Sequence[sqlite3.Row],
    outcomes: Sequence[sqlite3.Row],
    attempts: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
) -> dict[str, int]:
    counts = {"VALID": 0, "REJECTED": 0, "missing": 0}
    outcome_artifacts = {str(row["artifact_id"]) for row in outcomes}
    outcomes_by_artifact = {str(row["artifact_id"]): row for row in outcomes}
    attempt_by_id = {str(row["attempt_id"]): row for row in attempts}
    registered_paths: set[Path] = set()
    resolved_root = artifact_root.resolve(strict=False)
    for row in artifacts:
        artifact_id = str(row["artifact_id"])
        stable_relative = _artifact_stable_relative_path(row)
        if stable_relative is None:
            valid = False
            counts["REJECTED"] += 1
            continue
        path = resolved_root.joinpath(*PurePosixPath(stable_relative).parts)
        registered_paths.add(path.resolve(strict=False))
        valid = True
        if str(row["path"]) != stable_relative:
            issues.append(OracleIssue("unstable_artifact_path", "artifact path differs from stable tuple", (artifact_id,)))
            valid = False
        if row["digest_algorithm"] != "sha256":
            issues.append(OracleIssue("artifact_algorithm", "artifact digest algorithm is not sha256", (artifact_id,)))
            valid = False
        if not path.is_dir():
            counts["missing"] += 1
            issues.append(OracleIssue("missing_artifact", "registered artifact directory is absent", (artifact_id,)))
            continue
        try:
            actual = _raw_artifact_digest(path)
        except (OSError, ValueError) as exc:
            issues.append(OracleIssue("invalid_artifact_tree", str(exc), (artifact_id,)))
            valid = False
        else:
            if actual != row["digest"]:
                issues.append(OracleIssue("artifact_digest_mismatch", "recorded digest differs from bytes", (artifact_id,)))
                valid = False
        attempt = attempt_by_id.get(str(row["attempt_id"]))
        if attempt is None:
            issues.append(OracleIssue("artifact_missing_attempt", "artifact attempt does not exist", (artifact_id,)))
            valid = False
        elif (
            str(attempt["execution_id"]) != str(row["execution_id"])
            or str(attempt["experiment_id"]) != str(row["experiment_id"])
            or attempt["state"] != "CLOSED"
            or attempt["terminal_reason"] == "LOST"
        ):
            issues.append(OracleIssue("artifact_attempt_mismatch", "artifact does not bind an eligible closed attempt", (artifact_id,)))
            valid = False
        if artifact_id not in outcome_artifacts:
            issues.append(OracleIssue("unreferenced_artifact", "database artifact has no accepted outcome", (artifact_id,)))
            valid = False
        else:
            outcome = outcomes_by_artifact[artifact_id]
            if (
                str(outcome["run_id"]) != str(row["run_id"])
                or str(outcome["experiment_id"]) != str(row["experiment_id"])
                or str(outcome["execution_id"]) != str(row["execution_id"])
            ):
                issues.append(
                    OracleIssue("outcome_artifact_identity", "outcome and artifact stable identities differ", (artifact_id,))
                )
                valid = False
            try:
                outcome_payload = json.loads(outcome["payload_json"])
            except (TypeError, json.JSONDecodeError):
                outcome_payload = {}
            if not isinstance(outcome_payload, Mapping) or outcome_payload.get("artifact_set_digest") != row["digest"]:
                issues.append(
                    OracleIssue("outcome_artifact_digest", "outcome payload does not bind artifact digest", (artifact_id,))
                )
                valid = False
        counts["VALID" if valid else "REJECTED"] += 1
    for outcome in outcomes:
        if str(outcome["artifact_id"]) not in {str(row["artifact_id"]) for row in artifacts}:
            issues.append(OracleIssue("outcome_missing_artifact", "accepted outcome artifact row is absent", (str(outcome["fact_id"]),)))
    if artifact_root.exists():
        # Input snapshots, Agent candidate workspaces and operational logs also
        # live under artifacts/.  Only directories with the exact five-part
        # stable publication shape are canonical result publications.
        for candidate in artifact_root.rglob("result"):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            try:
                relative = candidate.relative_to(artifact_root)
            except ValueError:
                continue
            if len(relative.parts) != 5 or relative.parts[0] != run_id:
                continue
            resolved = candidate.resolve(strict=False)
            if resolved not in registered_paths:
                issues.append(
                    OracleIssue(
                        "unreferenced_artifact_publication",
                        "stable-key result publication has no database row",
                        (str(candidate),),
                    )
                )
    return counts


@dataclass(frozen=True, slots=True)
class _ComponentInput:
    resource: str
    producer_component: str
    logical_artifact: str
    min_count: int


@dataclass(frozen=True, slots=True)
class _ComponentContract:
    component_id: str
    tier: str
    owner_role: str
    proposer_roles: tuple[str, ...]
    dependencies: tuple[str, ...]
    required_inputs: tuple[_ComponentInput, ...]
    result_kind: str
    metric_name: str | None
    metric_direction: str | None
    metric_target: str | None
    seeds: tuple[int, ...]
    arms: tuple[str, ...]
    trial_ids: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    artifact_counts: Mapping[str, int]
    required_metrics: tuple[str, ...]
    artifact_validators: tuple[Mapping[str, Any], ...] = ()
    target_overlay_policy: str | None = None
    deferred_measurement: bool = False


@dataclass(frozen=True, slots=True)
class _GoalComponentContract:
    fact_id: str
    goal_id: str
    roles: tuple[str, ...]
    components: tuple[_ComponentContract, ...]

    @property
    def by_id(self) -> dict[str, _ComponentContract]:
        return {item.component_id: item for item in self.components}

    @property
    def required_ids(self) -> tuple[str, ...]:
        return tuple(
            item.component_id
            for item in self.components
            if item.tier in {"primary", "must"}
        )


@dataclass(frozen=True, slots=True)
class _ComponentResult:
    outcome: str
    criterion_met: bool | None
    metric_value: str | None
    implementation_digest: str | None
    core_config_digest: str | None
    goal_met: bool | None = None
    goal_valid: bool | None = None


def _uses_expert_scientific_lineage(
    agent_aci_version: str | None,
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Select the successor interpretation from the frozen run contract."""

    if not isinstance(agent_aci_version, str) or not agent_aci_version.isdigit() or int(agent_aci_version) < 7:
        return False
    goals = [row for row in facts if row["fact_type"] == "GoalDefined"]
    if len(goals) != 1:
        return False
    payload = payloads.get(str(goals[0]["fact_id"]), {})
    contract = payload.get("research_contract")
    methodology = contract.get("methodology") if isinstance(contract, Mapping) else None
    controls = methodology.get("controls") if isinstance(methodology, Mapping) else None
    return (
        isinstance(controls, Mapping)
        and controls.get("research_loop") == "expert-six-node/1"
    )


def _validated_agent_attempt(
    attempt: sqlite3.Row,
    observation: Mapping[str, Any] | None,
) -> tuple[AgentRequest, AgentResponse] | None:
    if attempt["state"] != "CLOSED" or attempt["terminal_reason"] != "SUCCEEDED":
        return None
    if not isinstance(observation, Mapping):
        return None
    request_value = observation.get("request")
    response_value = observation.get("response")
    if not isinstance(request_value, Mapping) or not isinstance(
        response_value, Mapping
    ):
        return None
    try:
        request = AgentRequest.from_dict(request_value)
        response = AgentResponse.from_dict(response_value)
        validated = validate_postflight(request, response)
    except (AgentACIError, TypeError, ValueError):
        return None
    return request, validated.response


def _research_task_key(request: AgentRequest) -> Mapping[str, Any] | None:
    task = request.to_dict().get("task_context")
    key = task.get("key") if isinstance(task, Mapping) else None
    return key if isinstance(key, Mapping) else None


def _expert_hypothesis_cycle(
    hypothesis: sqlite3.Row,
    hypotheses_by_id: Mapping[str, sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
) -> int | None:
    cycle = 1
    seen = {str(hypothesis["hypothesis_id"])}
    current = hypothesis
    while True:
        parent_id = payloads.get(str(current["fact_id"]), {}).get(
            "parent_hypothesis_id"
        )
        if parent_id is None:
            return cycle
        parent_text = str(parent_id)
        parent = hypotheses_by_id.get(parent_text)
        if parent is None or parent_text in seen:
            return None
        seen.add(parent_text)
        cycle += 1
        current = parent


def _audit_expert_successor_authorization(
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
) -> None:
    """Bind every scientific child to an accepted REVISE update and creation."""

    hypotheses = [row for row in facts if row["fact_type"] == "HypothesisProposed"]
    hypotheses_by_id = {str(row["hypothesis_id"]): row for row in hypotheses}
    assessments_by_hypothesis: dict[str, list[sqlite3.Row]] = {}
    for row in facts:
        if row["fact_type"] == "HypothesisAssessed":
            assessments_by_hypothesis.setdefault(
                str(row["hypothesis_id"]), []
            ).append(row)
    attempts_by_id = {str(row["attempt_id"]): row for row in attempts}
    exchanges = {
        attempt_id: exchange
        for attempt_id, attempt in attempts_by_id.items()
        if (
            exchange := _validated_agent_attempt(
                attempt, attempt_payloads.get(attempt_id)
            )
        )
        is not None
    }

    for child in hypotheses:
        child_fact_id = str(child["fact_id"])
        child_payload = payloads.get(child_fact_id, {})
        parent_id = child_payload.get("parent_hypothesis_id")
        if parent_id is None:
            continue
        parent = hypotheses_by_id.get(str(parent_id))
        cycle = _expert_hypothesis_cycle(child, hypotheses_by_id, payloads)
        parent_assessments = assessments_by_hypothesis.get(str(parent_id), [])
        accepting = [
            transition
            for transition in transitions
            if _row_get(transition, "fact_id") == child["fact_id"]
        ]
        accepting_payload = (
            transition_payloads.get(_row_identifier(accepting[0]), {})
            if len(accepting) == 1
            else {}
        )
        source_attempt_id = accepting_payload.get("agent_attempt_id")
        source_exchange = exchanges.get(str(source_attempt_id))
        source_request = None if source_exchange is None else source_exchange[0]
        source_key = (
            None if source_request is None else _research_task_key(source_request)
        )
        child_source_valid = (
            parent is not None
            and cycle is not None
            and cycle >= 2
            and len(parent_assessments) == 1
            and len(accepting) == 1
            and source_request is not None
            and source_request.command.value == "HYPOTHESIS_PROPOSAL"
            and source_request.component_id == child_payload.get("component_id")
            and source_request.role == child_payload.get("proposer_role")
            and isinstance(source_key, Mapping)
            and source_key.get("node_kind") == "HYPOTHESIS_PROPOSAL"
            and source_key.get("subkind") == "SYNTHESIS"
            and source_key.get("cycle") == cycle
        )

        matching_updates: list[str] = []
        if child_source_valid and source_request is not None and cycle is not None:
            for attempt_id, (request, response) in exchanges.items():
                if request.command.value != "HYPOTHESIS_UPDATE":
                    continue
                key = _research_task_key(request)
                candidate = response.typed_candidate
                if (
                    request.schema_version >= 7
                    and request.run_id == child["run_id"]
                    and request.owner_workflow_id == child["run_id"]
                    and request.component_id == child_payload.get("component_id")
                    and request.role == child_payload.get("proposer_role")
                    and isinstance(key, Mapping)
                    and key.get("node_kind") == "HYPOTHESIS_UPDATE"
                    and key.get("subkind") == "MAIN"
                    and key.get("cycle") == cycle - 1
                    and isinstance(candidate, Mapping)
                    and candidate.get("update_disposition")
                    == "REVISE_NEXT_CYCLE"
                    and str(parent["fact_id"]) in request.immutable_context_refs
                    and str(parent_assessments[0]["fact_id"])
                    in request.immutable_context_refs
                    and f"{attempt_id}--response"
                    in source_request.immutable_context_refs
                ):
                    matching_updates.append(attempt_id)

        creations = []
        if cycle is not None:
            for transition in transitions:
                transition_payload = transition_payloads.get(
                    _row_identifier(transition), {}
                )
                if (
                    transition["workflow_kind"] == "EXPERIMENT"
                    and int(transition["revision"]) == 1
                    and transition_payload.get("hypothesis_id")
                    == child["hypothesis_id"]
                    and transition_payload.get("component_id")
                    == child_payload.get("component_id")
                    and transition_payload.get("role")
                    == child_payload.get("proposer_role")
                    and transition_payload.get("method_family_id")
                    == child_payload.get("method_family_id")
                    and transition_payload.get("expert_cycle") == cycle
                    and transition_payload.get("revision_of_outcome_id") is None
                    and transition_payload.get("preflight_repair_of_experiment_id") is None
                ):
                    creations.append(transition)

        if (
            not child_source_valid
            or len(matching_updates) != 1
            or len(creations) != 1
        ):
            issues.append(
                OracleIssue(
                    "expert_successor_authorization_mismatch",
                    "scientific successor lacks one exact parent assessment, REVISE update response, synthesis causation, or experiment creation",
                    (
                        child_fact_id,
                        str(parent_id),
                        str(source_attempt_id),
                        *matching_updates,
                        *(str(row["workflow_id"]) for row in creations),
                    ),
                )
            )


def _revisionable_predecessor_outcome(payload: Mapping[str, Any]) -> bool:
    outcome = payload.get("outcome")
    return outcome == "EXECUTION_FAILURE" or (
        outcome == "INTEGRITY_REJECTED"
        and payload.get("validation_code") in _REVISIONABLE_INTEGRITY_CODES
    )


def _raw_precedes(parent: sqlite3.Row, child: sqlite3.Row) -> bool:
    parent_order = _row_get(parent, "raw_order")
    child_order = _row_get(child, "raw_order")
    return (
        parent_order is not None
        and child_order is not None
        and int(parent_order) < int(child_order)
    )


def _audit_expert_component_successor_chains(
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    specs: Sequence[sqlite3.Row],
    outcomes: Sequence[sqlite3.Row],
    evidences: Sequence[sqlite3.Row],
    assessments: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
    *,
    completed: bool,
) -> tuple[
    dict[str, sqlite3.Row],
    dict[str, sqlite3.Row],
    dict[str, sqlite3.Row],
]:
    """Project ACI-7 expert cycles through hypothesis-parent causation.

    ``revision_of_outcome_id`` remains the exact same-hypothesis operational
    repair edge.  A new scientific cycle instead starts a new root spec for a
    child hypothesis and inherits from the preceding hypothesis tip.
    """

    hypotheses = [row for row in facts if row["fact_type"] == "HypothesisProposed"]
    hypotheses_by_id = {str(row["hypothesis_id"]): row for row in hypotheses}
    specs_by_identity = {
        (
            str(row["run_id"]),
            str(row["experiment_id"]),
            str(row["execution_id"]),
        ): row
        for row in specs
    }
    outcomes_by_id = {str(row["outcome_id"]): row for row in outcomes}
    outcomes_by_spec: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in outcomes:
        outcomes_by_spec.setdefault(
            (
                str(row["run_id"]),
                str(row["experiment_id"]),
                str(row["execution_id"]),
            ),
            [],
        ).append(row)

    hypothesis_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in hypotheses:
        payload = payloads.get(str(row["fact_id"]), {})
        component_id = payload.get("component_id")
        proposer = payload.get("proposer_role")
        if _trimmed_text(component_id) and _trimmed_text(proposer):
            hypothesis_groups.setdefault(
                (str(component_id), str(proposer)), []
            ).append(row)

    ordered_hypotheses: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for (component_id, proposer), rows in hypothesis_groups.items():
        by_id = {str(row["hypothesis_id"]): row for row in rows}
        children: dict[str, list[sqlite3.Row]] = {}
        roots: list[sqlite3.Row] = []
        valid = True
        for row in rows:
            fact_id = str(row["fact_id"])
            payload = payloads.get(fact_id, {})
            parent_id = payload.get("parent_hypothesis_id")
            relation = payload.get("relation")
            if parent_id is None:
                roots.append(row)
                if relation is not None:
                    valid = False
            else:
                parent = by_id.get(str(parent_id))
                if (
                    parent is None
                    or not _trimmed_text(relation)
                    or not _raw_precedes(parent, row)
                ):
                    valid = False
                    issues.append(
                        OracleIssue(
                            "component_successor_lineage_mismatch",
                            "expert hypothesis parent is absent, later, or belongs to another component/proposer lineage",
                            (fact_id, component_id, proposer, str(parent_id)),
                        )
                    )
                    continue
                children.setdefault(str(parent_id), []).append(row)
        for parent_id, child_rows in children.items():
            if len(child_rows) > 1:
                valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_branch",
                        "one expert hypothesis has multiple scientific successors",
                        (
                            parent_id,
                            *(str(row["fact_id"]) for row in child_rows),
                        ),
                    )
                )
        if len(roots) != 1:
            valid = False
            issues.append(
                OracleIssue(
                    "component_successor_lineage_mismatch",
                    "one component/proposer expert lineage must have exactly one root hypothesis",
                    (
                        component_id,
                        proposer,
                        *(str(row["fact_id"]) for row in roots),
                    ),
                )
            )
        if not valid or len(roots) != 1:
            continue
        ordered = [roots[0]]
        while True:
            child_rows = children.get(str(ordered[-1]["hypothesis_id"]), [])
            if not child_rows:
                break
            ordered.append(child_rows[0])
        if len(ordered) != len(rows):
            issues.append(
                OracleIssue(
                    "component_successor_cycle",
                    "expert hypotheses do not form one connected acyclic chain",
                    (
                        component_id,
                        proposer,
                        *(str(row["fact_id"]) for row in rows),
                    ),
                )
            )
            continue
        ordered_hypotheses[(component_id, proposer)] = ordered

    specs_by_component: dict[str, list[sqlite3.Row]] = {}
    specs_by_hypothesis: dict[str, list[sqlite3.Row]] = {}
    for row in specs:
        payload = payloads.get(str(row["fact_id"]), {})
        component_id = payload.get("component_id")
        if _trimmed_text(component_id):
            specs_by_component.setdefault(str(component_id), []).append(row)
        specs_by_hypothesis.setdefault(str(row["hypothesis_id"]), []).append(row)

    assessments_by_hypothesis: dict[str, list[sqlite3.Row]] = {}
    for row in assessments:
        assessments_by_hypothesis.setdefault(
            str(row["hypothesis_id"]), []
        ).append(row)

    spec_tips: dict[str, sqlite3.Row] = {}
    outcome_tips: dict[str, sqlite3.Row] = {}
    evidence_tips: dict[str, sqlite3.Row] = {}
    for component_id, component_specs in specs_by_component.items():
        represented_groups = {
            (
                str(payloads.get(str(hypotheses_by_id[hypothesis_id]["fact_id"]), {}).get("component_id")),
                str(payloads.get(str(hypotheses_by_id[hypothesis_id]["fact_id"]), {}).get("proposer_role")),
            )
            for hypothesis_id in {str(row["hypothesis_id"]) for row in component_specs}
            if hypothesis_id in hypotheses_by_id
        }
        valid = len(represented_groups) == 1
        if not valid:
            issues.append(
                OracleIssue(
                    "component_successor_branch",
                    "component experiments span zero or multiple expert hypothesis lineages",
                    (
                        component_id,
                        *(str(row["fact_id"]) for row in component_specs),
                    ),
                )
            )
            continue
        group = next(iter(represented_groups))
        hypothesis_order = ordered_hypotheses.get(group)
        if hypothesis_order is None:
            continue
        if completed and any(
            not specs_by_hypothesis.get(str(row["hypothesis_id"]))
            for row in hypothesis_order
        ):
            issues.append(
                OracleIssue(
                    "component_successor_lineage_mismatch",
                    "each completed expert hypothesis cycle must own a preregistered experiment",
                    (
                        component_id,
                        *(str(row["fact_id"]) for row in hypothesis_order),
                    ),
                )
            )
            continue

        ordered_specs_by_hypothesis: dict[str, list[sqlite3.Row]] = {}
        for hypothesis in hypothesis_order:
            hypothesis_id = str(hypothesis["hypothesis_id"])
            hypothesis_specs = specs_by_hypothesis.get(hypothesis_id, [])
            if not hypothesis_specs:
                continue
            by_fact_id = {str(row["fact_id"]): row for row in hypothesis_specs}
            children_by_parent: dict[str, list[sqlite3.Row]] = {}
            local_roots: list[sqlite3.Row] = []
            local_valid = True
            for successor in hypothesis_specs:
                successor_id = str(successor["fact_id"])
                successor_payload = payloads.get(successor_id, {})
                if "revision_of_outcome_id" not in successor_payload:
                    local_valid = False
                    continue
                predecessor_outcome_id = successor_payload.get(
                    "revision_of_outcome_id"
                )
                if predecessor_outcome_id is None:
                    local_roots.append(successor)
                    continue
                predecessor_outcome = outcomes_by_id.get(str(predecessor_outcome_id))
                predecessor_spec = (
                    None
                    if predecessor_outcome is None
                    else specs_by_identity.get(
                        (
                            str(predecessor_outcome["run_id"]),
                            str(predecessor_outcome["experiment_id"]),
                            str(predecessor_outcome["execution_id"]),
                        )
                    )
                )
                predecessor_payload = (
                    {}
                    if predecessor_spec is None
                    else payloads.get(str(predecessor_spec["fact_id"]), {})
                )
                predecessor_outcome_payload = (
                    {}
                    if predecessor_outcome is None
                    else payloads.get(str(predecessor_outcome["fact_id"]), {})
                )
                predecessor_id = (
                    None
                    if predecessor_spec is None
                    else str(predecessor_spec["fact_id"])
                )
                revision_valid = (
                    _trimmed_text(predecessor_outcome_id)
                    and predecessor_outcome is not None
                    and predecessor_spec is not None
                    and predecessor_spec is not successor
                    and predecessor_spec["hypothesis_id"] == hypothesis_id
                    and predecessor_payload.get("component_id") == component_id
                    and predecessor_outcome_payload.get("component_id")
                    == component_id
                    and predecessor_payload.get("method_family_id")
                    == successor_payload.get("method_family_id")
                    and predecessor_outcome_payload.get("method_family_id")
                    == successor_payload.get("method_family_id")
                    and _revisionable_predecessor_outcome(
                        predecessor_outcome_payload
                    )
                    and _raw_precedes(predecessor_outcome, successor)
                )
                if not revision_valid or predecessor_id is None:
                    local_valid = False
                    issues.append(
                        OracleIssue(
                            "component_successor_lineage_mismatch",
                            "same-hypothesis repair predecessor is absent, non-operational, later, or belongs to another lineage",
                            (
                                successor_id,
                                component_id,
                                str(predecessor_outcome_id),
                            ),
                        )
                    )
                    continue
                children_by_parent.setdefault(predecessor_id, []).append(successor)
            for predecessor_id, child_rows in children_by_parent.items():
                if len(child_rows) > 1:
                    local_valid = False
                    issues.append(
                        OracleIssue(
                            "component_successor_branch",
                            "one repair outcome has multiple successor preregistrations",
                            (
                                predecessor_id,
                                *(str(row["fact_id"]) for row in child_rows),
                            ),
                        )
                    )
            if len(local_roots) != 1:
                local_valid = False
            if not local_valid or len(local_roots) != 1:
                issues.append(
                    OracleIssue(
                        "component_successor_lineage_mismatch",
                        "one scientific hypothesis must have exactly one root preregistration and a strict repair chain",
                        (
                            hypothesis_id,
                            *(str(row["fact_id"]) for row in local_roots),
                        ),
                    )
                )
                valid = False
                continue
            local_order = [local_roots[0]]
            while True:
                child_rows = children_by_parent.get(
                    str(local_order[-1]["fact_id"]), []
                )
                if not child_rows:
                    break
                local_order.append(child_rows[0])
            if len(local_order) != len(by_fact_id):
                valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_cycle",
                        "same-hypothesis repair preregistrations do not form one connected chain",
                        (hypothesis_id, *tuple(sorted(by_fact_id))),
                    )
                )
                continue
            ordered_specs_by_hypothesis[hypothesis_id] = local_order

        if not valid:
            continue
        ordered_specs = [
            spec
            for hypothesis in hypothesis_order
            for spec in ordered_specs_by_hypothesis.get(
                str(hypothesis["hypothesis_id"]), []
            )
        ]
        if len(ordered_specs) != len(component_specs) or not ordered_specs:
            issues.append(
                OracleIssue(
                    "component_successor_lineage_mismatch",
                    "component preregistrations do not cover one complete expert hypothesis chain",
                    (
                        component_id,
                        *(str(row["fact_id"]) for row in component_specs),
                    ),
                )
            )
            continue

        root_experiment_id = str(ordered_specs[0]["experiment_id"])
        root_execution_id = str(ordered_specs[0]["execution_id"])
        ordered_outcomes: list[sqlite3.Row | None] = []
        ordinal_valid = True
        for ordinal, spec in enumerate(ordered_specs, 1):
            suffix = "" if ordinal == 1 else f"-{ordinal}"
            if (
                str(spec["experiment_id"]) != f"{root_experiment_id}{suffix}"
                or str(spec["execution_id"]) != f"{root_execution_id}{suffix}"
            ):
                ordinal_valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_ordinal_mismatch",
                        "expert-cycle experiment and execution IDs do not use the lineage ordinal",
                        (str(spec["fact_id"]), str(ordinal)),
                    )
                )
            matches = outcomes_by_spec.get(
                (
                    str(spec["run_id"]),
                    str(spec["experiment_id"]),
                    str(spec["execution_id"]),
                ),
                [],
            )
            if len(matches) > 1 or (completed and len(matches) != 1):
                issues.append(
                    OracleIssue(
                        "component_successor_lineage_mismatch",
                        "each completed expert-cycle experiment must have one canonical outcome",
                        (
                            str(spec["fact_id"]),
                            *(str(row["fact_id"]) for row in matches),
                        ),
                    )
                )
            ordered_outcomes.append(matches[0] if len(matches) == 1 else None)

        present_outcomes = [row for row in ordered_outcomes if row is not None]
        if present_outcomes:
            root_outcome_id = str(present_outcomes[0]["outcome_id"])
            for ordinal, outcome in enumerate(ordered_outcomes, 1):
                if outcome is None:
                    continue
                suffix = "" if ordinal == 1 else f"-{ordinal}"
                if str(outcome["outcome_id"]) != f"{root_outcome_id}{suffix}":
                    ordinal_valid = False
                    issues.append(
                        OracleIssue(
                            "component_successor_ordinal_mismatch",
                            "expert-cycle outcome ID does not use the lineage ordinal",
                            (str(outcome["fact_id"]), str(ordinal)),
                        )
                    )

        ordered_evidence: list[sqlite3.Row | None] = []
        for outcome in ordered_outcomes:
            matches = (
                []
                if outcome is None
                else [
                    evidence
                    for evidence in evidences
                    if evidence["run_id"] == outcome["run_id"]
                    and evidence["outcome_id"] == outcome["outcome_id"]
                    and evidence["artifact_id"] == outcome["artifact_id"]
                ]
            )
            if outcome is not None and completed and len(matches) != 1:
                issues.append(
                    OracleIssue(
                        "component_outcome_evidence_closure_mismatch",
                        "accepted expert-cycle outcome lacks exactly one evidence link",
                        (
                            str(outcome["outcome_id"]),
                            *(str(row["fact_id"]) for row in matches),
                        ),
                    )
                )
            ordered_evidence.append(matches[0] if len(matches) == 1 else None)

        present_evidence = [row for row in ordered_evidence if row is not None]
        if present_evidence:
            root_evidence_id = str(present_evidence[0]["subject_id"])
            for ordinal, evidence in enumerate(ordered_evidence, 1):
                if evidence is None:
                    continue
                suffix = "" if ordinal == 1 else f"-{ordinal}"
                if str(evidence["subject_id"]) != f"{root_evidence_id}{suffix}":
                    ordinal_valid = False
                    issues.append(
                        OracleIssue(
                            "component_successor_ordinal_mismatch",
                            "expert-cycle evidence ID does not use the lineage ordinal",
                            (str(evidence["fact_id"]), str(ordinal)),
                        )
                    )

        offset = 0
        for hypothesis in hypothesis_order:
            hypothesis_id = str(hypothesis["hypothesis_id"])
            local_specs = ordered_specs_by_hypothesis.get(hypothesis_id, [])
            local_evidence = ordered_evidence[offset : offset + len(local_specs)]
            offset += len(local_specs)
            present_local_evidence = [row for row in local_evidence if row is not None]
            matching_assessments = assessments_by_hypothesis.get(hypothesis_id, [])
            if completed and present_local_evidence:
                expected_evidence_ids = [
                    str(row["subject_id"]) for row in present_local_evidence
                ]
                assessment_valid = len(matching_assessments) == 1
                if assessment_valid:
                    assessment = matching_assessments[0]
                    assessment_payload = payloads.get(str(assessment["fact_id"]), {})
                    assessment_valid = (
                        assessment["run_id"] == hypothesis["run_id"]
                        and assessment_payload.get("component_id") == component_id
                        and assessment_payload.get("method_family_id")
                        == payloads.get(str(local_specs[-1]["fact_id"]), {}).get(
                            "method_family_id"
                        )
                        and assessment_payload.get("evidence_ids")
                        == expected_evidence_ids
                    )
                if not assessment_valid:
                    issues.append(
                        OracleIssue(
                            "component_successor_tip_assessment_mismatch",
                            "each expert cycle must have one assessment over that hypothesis's ordered evidence",
                            (
                                component_id,
                                hypothesis_id,
                                *expected_evidence_ids,
                                *(
                                    str(row["fact_id"])
                                    for row in matching_assessments
                                ),
                            ),
                        )
                    )

        if ordinal_valid:
            spec_tips[component_id] = ordered_specs[-1]
            if ordered_outcomes[-1] is not None:
                outcome_tips[component_id] = ordered_outcomes[-1]
            if ordered_evidence[-1] is not None:
                evidence_tips[component_id] = ordered_evidence[-1]

    return spec_tips, outcome_tips, evidence_tips


def _audit_component_successor_chains(
    research_contract_version: str | None,
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
    *,
    completed: bool,
    agent_aci_version: str | None = None,
) -> tuple[
    dict[str, sqlite3.Row],
    dict[str, sqlite3.Row],
    dict[str, sqlite3.Row],
]:
    """Project component tips directly from immutable preregistration facts."""

    specs = [
        row for row in facts if row["fact_type"] == "ExperimentPreregistered"
    ]
    outcomes = [
        row
        for row in facts
        if row["fact_type"] == "CanonicalOutcomeAccepted"
    ]
    evidences = [row for row in facts if row["fact_type"] == "EvidenceLinked"]
    assessments = [
        row for row in facts if row["fact_type"] == "HypothesisAssessed"
    ]

    specs_by_component: dict[str, list[sqlite3.Row]] = {}
    outcomes_by_component: dict[str, list[sqlite3.Row]] = {}
    for row in specs:
        component_id = payloads.get(str(row["fact_id"]), {}).get("component_id")
        if _trimmed_text(component_id):
            specs_by_component.setdefault(str(component_id), []).append(row)
    for row in outcomes:
        component_id = payloads.get(str(row["fact_id"]), {}).get("component_id")
        if _trimmed_text(component_id):
            outcomes_by_component.setdefault(str(component_id), []).append(row)

    if research_contract_version == "hypoloom-research-v4":
        spec_tips: dict[str, sqlite3.Row] = {}
        outcome_tips: dict[str, sqlite3.Row] = {}
        evidence_tips: dict[str, sqlite3.Row] = {}
        for component_id, rows in specs_by_component.items():
            spec_tips[component_id] = rows[0]
            if len(rows) > 1:
                issues.append(
                    OracleIssue(
                        "duplicate_component_experiment",
                        "one component has multiple preregistered experiments",
                        (
                            component_id,
                            *(str(row["fact_id"]) for row in rows),
                        ),
                    )
                )
        for component_id, rows in outcomes_by_component.items():
            outcome_tips[component_id] = rows[0]
            if len(rows) > 1:
                issues.append(
                    OracleIssue(
                        "duplicate_component_outcome",
                        "one component has multiple accepted outcomes",
                        (
                            component_id,
                            *(str(row["fact_id"]) for row in rows),
                        ),
                    )
                )
            selected = rows[0]
            matches = [
                evidence
                for evidence in evidences
                if evidence["outcome_id"] == selected["outcome_id"]
                and evidence["artifact_id"] == selected["artifact_id"]
            ]
            if len(matches) == 1:
                evidence_tips[component_id] = matches[0]
        return spec_tips, outcome_tips, evidence_tips

    if _COMPONENT_FACT_SCHEMA_BY_RESEARCH_VERSION.get(research_contract_version) != 5:
        return {}, {}, {}

    if _uses_expert_scientific_lineage(agent_aci_version, facts, payloads):
        return _audit_expert_component_successor_chains(
            facts,
            payloads,
            specs,
            outcomes,
            evidences,
            assessments,
            issues,
            completed=completed,
        )

    specs_by_identity = {
        (
            str(row["run_id"]),
            str(row["experiment_id"]),
            str(row["execution_id"]),
        ): row
        for row in specs
    }
    outcomes_by_id = {str(row["outcome_id"]): row for row in outcomes}
    outcomes_by_spec: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in outcomes:
        outcomes_by_spec.setdefault(
            (
                str(row["run_id"]),
                str(row["experiment_id"]),
                str(row["execution_id"]),
            ),
            [],
        ).append(row)
    assessments_by_component: dict[str, list[sqlite3.Row]] = {}
    for row in assessments:
        component_id = payloads.get(str(row["fact_id"]), {}).get("component_id")
        if _trimmed_text(component_id):
            assessments_by_component.setdefault(str(component_id), []).append(row)

    spec_tips = {}
    outcome_tips = {}
    evidence_tips = {}
    for component_id, component_specs in specs_by_component.items():
        by_fact_id = {str(row["fact_id"]): row for row in component_specs}
        parent_by_child: dict[str, str] = {}
        children_by_parent: dict[str, list[sqlite3.Row]] = {}
        lineage_valid = True
        for successor in component_specs:
            successor_id = str(successor["fact_id"])
            successor_payload = payloads.get(successor_id, {})
            if "revision_of_outcome_id" not in successor_payload:
                lineage_valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_lineage_mismatch",
                        "successor preregistration omits its explicit predecessor outcome field",
                        (successor_id, component_id),
                    )
                )
                continue
            predecessor_outcome_id = successor_payload.get(
                "revision_of_outcome_id"
            )
            if predecessor_outcome_id is None:
                continue
            predecessor_outcome = outcomes_by_id.get(str(predecessor_outcome_id))
            predecessor_spec = (
                None
                if predecessor_outcome is None
                else specs_by_identity.get(
                    (
                        str(predecessor_outcome["run_id"]),
                        str(predecessor_outcome["experiment_id"]),
                        str(predecessor_outcome["execution_id"]),
                    )
                )
            )
            predecessor_payload = (
                {}
                if predecessor_spec is None
                else payloads.get(str(predecessor_spec["fact_id"]), {})
            )
            predecessor_outcome_payload = (
                {}
                if predecessor_outcome is None
                else payloads.get(str(predecessor_outcome["fact_id"]), {})
            )
            predecessor_id = (
                None if predecessor_spec is None else str(predecessor_spec["fact_id"])
            )
            if predecessor_id is not None:
                parent_by_child[successor_id] = predecessor_id
            predecessor_order = (
                None
                if predecessor_outcome is None
                else _row_get(predecessor_outcome, "raw_order")
            )
            successor_order = _row_get(successor, "raw_order")
            valid = (
                _trimmed_text(predecessor_outcome_id)
                and predecessor_outcome is not None
                and predecessor_spec is not None
                and predecessor_spec is not successor
                and predecessor_outcome["run_id"] == successor["run_id"]
                and predecessor_spec["run_id"] == successor["run_id"]
                and predecessor_spec["hypothesis_id"]
                == successor["hypothesis_id"]
                and predecessor_payload.get("component_id") == component_id
                and predecessor_outcome_payload.get("component_id")
                == component_id
                and predecessor_payload.get("method_family_id")
                == successor_payload.get("method_family_id")
                and predecessor_outcome_payload.get("method_family_id")
                == successor_payload.get("method_family_id")
                and _revisionable_predecessor_outcome(
                    predecessor_outcome_payload
                )
                and (
                    predecessor_order is None
                    or successor_order is None
                    or int(predecessor_order) < int(successor_order)
                )
            )
            if not valid:
                lineage_valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_lineage_mismatch",
                        "successor predecessor is absent, non-operational, later, or belongs to another lineage",
                        (
                            successor_id,
                            component_id,
                            str(predecessor_outcome_id),
                        ),
                    )
                )
                continue
            children_by_parent.setdefault(predecessor_id, []).append(successor)

        for predecessor_id, children in children_by_parent.items():
            if len(children) > 1:
                lineage_valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_branch",
                        "one canonical outcome has multiple successor preregistrations",
                        (
                            predecessor_id,
                            *(str(row["fact_id"]) for row in children),
                        ),
                    )
                )

        cycle = False
        for start in by_fact_id:
            path: set[str] = set()
            current = start
            while current in parent_by_child and current in by_fact_id:
                if current in path:
                    cycle = True
                    break
                path.add(current)
                current = parent_by_child[current]
            if cycle:
                break
        if cycle:
            lineage_valid = False
            issues.append(
                OracleIssue(
                    "component_successor_cycle",
                    "component successor preregistrations form a cycle",
                    (component_id, *tuple(sorted(by_fact_id))),
                )
            )

        roots = [
            row
            for row in component_specs
            if payloads.get(str(row["fact_id"]), {}).get(
                "revision_of_outcome_id"
            )
            is None
        ]
        if len(roots) != 1:
            lineage_valid = False
            issues.append(
                OracleIssue(
                    "component_successor_lineage_mismatch",
                    "component successor facts must have exactly one root preregistration",
                    (component_id, *(str(row["fact_id"]) for row in roots)),
                )
            )
        if not lineage_valid or len(roots) != 1:
            continue

        ordered = [roots[0]]
        while True:
            children = children_by_parent.get(str(ordered[-1]["fact_id"]), [])
            if not children:
                break
            ordered.append(children[0])
        if len(ordered) != len(component_specs):
            issues.append(
                OracleIssue(
                    "component_successor_lineage_mismatch",
                    "component successor facts do not form one connected chain",
                    (component_id, *(str(row["fact_id"]) for row in component_specs)),
                )
            )
            continue

        root_experiment_id = str(ordered[0]["experiment_id"])
        root_execution_id = str(ordered[0]["execution_id"])
        ordered_outcomes: list[sqlite3.Row | None] = []
        ordinal_valid = True
        for ordinal, spec in enumerate(ordered, 1):
            suffix = "" if ordinal == 1 else f"-{ordinal}"
            if (
                str(spec["experiment_id"]) != f"{root_experiment_id}{suffix}"
                or str(spec["execution_id"]) != f"{root_execution_id}{suffix}"
            ):
                ordinal_valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_ordinal_mismatch",
                        "successor experiment and execution IDs do not use the chain ordinal",
                        (str(spec["fact_id"]), str(ordinal)),
                    )
                )
            matches = outcomes_by_spec.get(
                (
                    str(spec["run_id"]),
                    str(spec["experiment_id"]),
                    str(spec["execution_id"]),
                ),
                [],
            )
            if len(matches) > 1 or (completed and len(matches) != 1):
                lineage_valid = False
                issues.append(
                    OracleIssue(
                        "component_successor_lineage_mismatch",
                        "each completed successor experiment must have one canonical outcome",
                        (
                            str(spec["fact_id"]),
                            *(str(row["fact_id"]) for row in matches),
                        ),
                    )
                )
            ordered_outcomes.append(matches[0] if len(matches) == 1 else None)

        present_outcomes = [row for row in ordered_outcomes if row is not None]
        if present_outcomes:
            root_outcome_id = str(present_outcomes[0]["outcome_id"])
            for ordinal, outcome in enumerate(ordered_outcomes, 1):
                if outcome is None:
                    continue
                suffix = "" if ordinal == 1 else f"-{ordinal}"
                if str(outcome["outcome_id"]) != f"{root_outcome_id}{suffix}":
                    ordinal_valid = False
                    issues.append(
                        OracleIssue(
                            "component_successor_ordinal_mismatch",
                            "successor outcome ID does not use the chain ordinal",
                            (str(outcome["fact_id"]), str(ordinal)),
                        )
                    )

        ordered_evidence: list[sqlite3.Row | None] = []
        for outcome in ordered_outcomes:
            matches = (
                []
                if outcome is None
                else [
                    evidence
                    for evidence in evidences
                    if evidence["run_id"] == outcome["run_id"]
                    and evidence["outcome_id"] == outcome["outcome_id"]
                    and evidence["artifact_id"] == outcome["artifact_id"]
                ]
            )
            if outcome is not None and completed and len(matches) != 1:
                issues.append(
                    OracleIssue(
                        "component_outcome_evidence_closure_mismatch",
                        "accepted component outcome lacks exactly one evidence link",
                        (
                            str(outcome["outcome_id"]),
                            *(str(row["fact_id"]) for row in matches),
                        ),
                    )
                )
            ordered_evidence.append(matches[0] if len(matches) == 1 else None)

        present_evidence = [row for row in ordered_evidence if row is not None]
        if present_evidence:
            root_evidence_id = str(present_evidence[0]["subject_id"])
            for ordinal, evidence in enumerate(ordered_evidence, 1):
                if evidence is None:
                    continue
                suffix = "" if ordinal == 1 else f"-{ordinal}"
                if str(evidence["subject_id"]) != f"{root_evidence_id}{suffix}":
                    ordinal_valid = False
                    issues.append(
                        OracleIssue(
                            "component_successor_ordinal_mismatch",
                            "successor evidence ID does not use the chain ordinal",
                            (str(evidence["fact_id"]), str(ordinal)),
                        )
                    )

        tip_spec = ordered[-1]
        tip_outcome = ordered_outcomes[-1]
        tip_evidence = ordered_evidence[-1]
        component_assessments = assessments_by_component.get(component_id, [])
        if completed and tip_evidence is not None:
            expected_evidence_ids = [
                str(row["subject_id"]) for row in ordered_evidence if row is not None
            ]
            valid_assessment = len(component_assessments) == 1
            if valid_assessment:
                assessment = component_assessments[0]
                assessment_payload = payloads.get(str(assessment["fact_id"]), {})
                valid_assessment = (
                    assessment["run_id"] == tip_spec["run_id"]
                    and assessment["hypothesis_id"] == tip_spec["hypothesis_id"]
                    and assessment_payload.get("component_id") == component_id
                    and assessment_payload.get("method_family_id")
                    == payloads.get(str(tip_spec["fact_id"]), {}).get(
                        "method_family_id"
                    )
                    and assessment_payload.get("evidence_ids") == expected_evidence_ids
                )
            if not valid_assessment:
                issues.append(
                    OracleIssue(
                        "component_successor_tip_assessment_mismatch",
                        "component assessment must cite the ordered successor evidence chain",
                        (
                            component_id,
                            *expected_evidence_ids,
                            *(str(row["fact_id"]) for row in component_assessments),
                        ),
                    )
                )

        if ordinal_valid:
            spec_tips[component_id] = tip_spec
            if tip_outcome is not None:
                outcome_tips[component_id] = tip_outcome
            if tip_evidence is not None:
                evidence_tips[component_id] = tip_evidence

    return spec_tips, outcome_tips, evidence_tips


def _audit_component_scientific_closure(
    artifact_root: Path,
    run_state: str | None,
    research_contract_version: str | None,
    run_evaluator_version: str | None,
    agent_aci_version: str | None,
    facts: Sequence[sqlite3.Row],
    fact_payloads: Mapping[str, Mapping[str, Any]],
    refs: Sequence[sqlite3.Row],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    experiment_heads: Sequence[sqlite3.Row],
    artifacts: Sequence[sqlite3.Row],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    outcomes: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
    *, commands: Sequence[sqlite3.Row] = (), workflow_version: str | None = None,
) -> None:
    """Reconstruct component-v4 scientific closure from raw rows and bytes."""

    if research_contract_version not in _COMPONENT_FACT_SCHEMA_BY_RESEARCH_VERSION:
        return
    contract = _read_goal_component_contract(
        facts, fact_payloads, run_evaluator_version, issues
    )
    if contract is None:
        return
    expert_scientific_lineage = _uses_expert_scientific_lineage(
        agent_aci_version, facts, fact_payloads
    )
    batch_mode = agent_aci_version is not None and agent_aci_version.isdigit() and int(agent_aci_version) >= 14 and expert_scientific_lineage
    goal_document = fact_payloads.get(contract.fact_id, {}).get("research_contract", {})
    completion_mode = ("shared_goal" if goal_document.get("schema_version") == 3 and goal_document.get("goal", {}).get("evaluation") == {"mode": "grounding"}
        else goal_document.get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {}).get("completion_mode", "required_conjunction") if batch_mode else "required_conjunction")
    from .batch_oracle import shared_goal_stop
    try:
        success_stop = shared_goal_stop(facts, fact_payloads, transitions, workflow_version)
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(OracleIssue("verdict_shared_goal_mismatch", str(exc)))
        success_stop = None
    _audit_component_fact_references(contract, facts, fact_payloads, refs, issues)
    if batch_mode:
        from .batch_oracle import selected_fact_tips
        projected_specs_by_component, projected_outcomes_by_component, _projected_evidence_by_component = selected_fact_tips(facts, fact_payloads, transitions, transition_payloads)
    else:
        projected_specs_by_component, projected_outcomes_by_component, _projected_evidence_by_component = _audit_component_successor_chains(
            research_contract_version, facts, fact_payloads, issues, completed=run_state == "COMPLETED", agent_aci_version=agent_aci_version)
    expected_fact_schema = _COMPONENT_FACT_SCHEMA_BY_RESEARCH_VERSION[
        research_contract_version
    ]

    artifacts_by_id = {str(row["artifact_id"]): row for row in artifacts}
    attempts_by_id = {str(row["attempt_id"]): row for row in attempts}
    component_by_id = contract.by_id
    hypotheses_by_id = {
        str(row["hypothesis_id"]): row
        for row in facts
        if row["fact_type"] == "HypothesisProposed"
    }
    specs_by_experiment: dict[str, sqlite3.Row] = {}
    specs_by_component = dict(projected_specs_by_component)
    outcomes_by_id = {
        str(row["outcome_id"]): row
        for row in facts
        if row["fact_type"] == "CanonicalOutcomeAccepted"
    }
    outcomes_by_component = dict(projected_outcomes_by_component)
    evidence_by_id = {
        str(row["subject_id"]): row
        for row in facts
        if row["fact_type"] == "EvidenceLinked"
    }
    assessments_by_id = {
        str(row["subject_id"]): row
        for row in facts
        if row["fact_type"] == "HypothesisAssessed"
    }
    if expert_scientific_lineage and not batch_mode:
        _audit_expert_successor_authorization(
            facts,
            fact_payloads,
            transitions,
            transition_payloads,
            attempts,
            attempt_payloads,
            issues,
        )

    proposals: dict[tuple[str, str], sqlite3.Row] = {}
    for fact in facts:
        _audit_v4_fact_payload_identity(
            fact, fact_payloads, issues, expected_schema=expected_fact_schema
        )
        fact_id = str(fact["fact_id"])
        payload = fact_payloads.get(fact_id, {})
        if fact["fact_type"] != "HypothesisProposed":
            continue
        component_id = payload.get("component_id")
        component = component_by_id.get(str(component_id))
        proposer = payload.get("proposer_role")
        family = payload.get("method_family_id")
        if (
            component is None
            or fact["goal_id"] != contract.goal_id
            or proposer not in component.proposer_roles
            or not _trimmed_text(family)
        ):
            issues.append(
                OracleIssue(
                    "hypothesis_component_identity_mismatch",
                    "hypothesis component, proposer, goal, or method family differs from GoalDefined",
                    (fact_id,),
                )
            )
        if component is not None and isinstance(proposer, str):
            proposal_key = (component.component_id, proposer)
            prior = proposals.setdefault(proposal_key, fact)
            if prior is not fact and not expert_scientific_lineage:
                issues.append(
                    OracleIssue(
                        "duplicate_component_proposal",
                        "one component/proposer identity has multiple accepted hypotheses",
                        (
                            component.component_id,
                            proposer,
                            str(prior["fact_id"]),
                            fact_id,
                        ),
                    )
                )
        parent_id = payload.get("parent_hypothesis_id")
        if parent_id is not None:
            parent = hypotheses_by_id.get(str(parent_id))
            parent_payload = (
                fact_payloads.get(str(parent["fact_id"]), {})
                if parent is not None
                else {}
            )
            if (
                parent is None
                or parent["goal_id"] != fact["goal_id"]
                or parent_payload.get("component_id") != component_id
            ):
                issues.append(
                    OracleIssue(
                        "hypothesis_parent_identity_mismatch",
                        "hypothesis parent is absent or belongs to another component",
                        (fact_id, str(parent_id)),
                    )
                )

    for fact in facts:
        if fact["fact_type"] != "ExperimentPreregistered":
            continue
        fact_id = str(fact["fact_id"])
        payload = fact_payloads.get(fact_id, {})
        component_id = str(payload.get("component_id"))
        component = component_by_id.get(component_id)
        hypothesis = hypotheses_by_id.get(str(fact["hypothesis_id"]))
        hypothesis_payload = (
            fact_payloads.get(str(hypothesis["fact_id"]), {})
            if hypothesis is not None
            else {}
        )
        if (
            component is None
            or hypothesis is None
            or hypothesis_payload.get("component_id") != component_id
            or hypothesis_payload.get("method_family_id")
            != payload.get("method_family_id")
        ):
            issues.append(
                OracleIssue(
                    "experiment_component_identity_mismatch",
                    "preregistration is not owned by the selected component hypothesis and method family",
                    (fact_id,),
                )
            )
        if component is not None:
            _audit_component_spec(
                fact,
                payload,
                component,
                run_evaluator_version,
                outcomes_by_id,
                fact_payloads,
                artifacts_by_id,
                artifact_root,
                issues,
                selected_outcomes_by_component=(
                    outcomes_by_component
                    if research_contract_version in {"hypoloom-research-v7", "hypoloom-research-v8", "hypoloom-research-v9", "hypoloom-research-v10", "hypoloom-research-v11", "hypoloom-research-v12"}
                    else None
                ),
                allow_clean_refutation_inputs=(
                    research_contract_version in {"hypoloom-research-v7", "hypoloom-research-v8", "hypoloom-research-v9", "hypoloom-research-v10", "hypoloom-research-v11", "hypoloom-research-v12"}
                ),
            )
        experiment_id = str(fact["experiment_id"])
        if experiment_id in specs_by_experiment:
            issues.append(
                OracleIssue(
                    "duplicate_component_experiment",
                    "component experiment identity has multiple preregistrations",
                    (experiment_id,),
                )
            )
        specs_by_experiment[experiment_id] = fact

    component_results: dict[str, _ComponentResult] = {}
    component_results_by_outcome: dict[str, _ComponentResult] = {}
    for outcome in outcomes:
        fact_id = str(outcome["fact_id"])
        payload = fact_payloads.get(fact_id, {})
        spec = specs_by_experiment.get(str(outcome["experiment_id"]))
        spec_payload = (
            fact_payloads.get(str(spec["fact_id"]), {}) if spec is not None else {}
        )
        component_id = str(payload.get("component_id"))
        component = component_by_id.get(component_id)
        artifact = artifacts_by_id.get(str(outcome["artifact_id"]))
        if (
            component is None
            or spec is None
            or str(spec["execution_id"]) != str(outcome["execution_id"])
            or spec_payload.get("component_id") != component_id
            or spec_payload.get("method_family_id")
            != payload.get("method_family_id")
            or spec_payload.get("result_kind") != payload.get("result_kind")
        ):
            issues.append(
                OracleIssue(
                    "outcome_component_identity_mismatch",
                    "accepted outcome differs from its component preregistration",
                    (fact_id,),
                )
            )
            continue
        if artifact is None:
            continue
        result = _audit_component_result(
            artifact_root,
            component,
            spec,
            spec_payload,
            outcome,
            payload,
            artifact,
            attempts_by_id,
            _bound_checkpoint_paths(
                component, spec_payload, outcomes_by_id, fact_payloads
            ),
            transitions,
            transition_payloads,
            issues,
            commands=commands, attempt_payloads=attempt_payloads,
        )
        component_results_by_outcome[str(outcome["outcome_id"])] = result
        selected = outcomes_by_component.get(component_id)
        if selected is not None and selected["fact_id"] == outcome["fact_id"]:
            component_results[component_id] = result

    assessment_by_component: dict[str, sqlite3.Row] = {}
    assessment_by_expert_hypothesis: dict[tuple[str, str], sqlite3.Row] = {}
    assessment_result: dict[str, _ComponentResult | None] = {}
    tip_hypothesis_by_component = {
        component_id: str(spec["hypothesis_id"])
        for component_id, spec in specs_by_component.items()
    }
    for evidence_id, evidence in evidence_by_id.items():
        payload = fact_payloads.get(str(evidence["fact_id"]), {})
        hypothesis = hypotheses_by_id.get(str(evidence["hypothesis_id"]))
        outcome = outcomes_by_id.get(str(evidence["outcome_id"]))
        hypothesis_payload = (
            fact_payloads.get(str(hypothesis["fact_id"]), {})
            if hypothesis is not None
            else {}
        )
        outcome_payload = (
            fact_payloads.get(str(outcome["fact_id"]), {})
            if outcome is not None
            else {}
        )
        selected_spec = (
            specs_by_experiment.get(str(outcome["experiment_id"]))
            if outcome is not None
            else None
        )
        if (
            hypothesis is None
            or outcome is None
            or selected_spec is None
            or selected_spec["hypothesis_id"] != evidence["hypothesis_id"]
            or evidence["artifact_id"] != outcome["artifact_id"]
            or payload.get("component_id") != hypothesis_payload.get("component_id")
            or payload.get("component_id") != outcome_payload.get("component_id")
            or payload.get("method_family_id")
            != hypothesis_payload.get("method_family_id")
            or payload.get("method_family_id")
            != outcome_payload.get("method_family_id")
        ):
            issues.append(
                OracleIssue(
                    "evidence_component_identity_mismatch",
                    "evidence does not close one hypothesis/outcome/artifact component lineage",
                    (evidence_id,),
                )
            )

    if run_state == "COMPLETED":
        if research_contract_version == "hypoloom-research-v4":
            for outcome_id, outcome in outcomes_by_id.items():
                matches = [
                    evidence
                    for evidence in evidence_by_id.values()
                    if evidence["outcome_id"] == outcome["outcome_id"]
                    and evidence["artifact_id"] == outcome["artifact_id"]
                ]
                if len(matches) != 1:
                    issues.append(
                        OracleIssue(
                            "component_outcome_evidence_closure_mismatch",
                            "accepted component outcome lacks exactly one evidence link",
                            (
                                outcome_id,
                                *(str(item["fact_id"]) for item in matches),
                            ),
                        )
                    )

        for evidence_id, evidence in evidence_by_id.items():
            matches = []
            for assessment in assessments_by_id.values():
                assessment_payload = fact_payloads.get(
                    str(assessment["fact_id"]), {}
                )
                evidence_ids = assessment_payload.get("evidence_ids")
                if (
                    assessment["hypothesis_id"] == evidence["hypothesis_id"]
                    and isinstance(evidence_ids, list)
                    and evidence_id in {str(item) for item in evidence_ids}
                ):
                    matches.append(assessment)
            if len(matches) != 1 and not (success_stop is not None and not matches):
                issues.append(
                    OracleIssue(
                        "component_evidence_assessment_closure_mismatch",
                        "component evidence assessment count differs from its chain-tip role",
                        (evidence_id, *(str(item["fact_id"]) for item in matches)),
                    )
                )

    for assessment_id, assessment in assessments_by_id.items():
        payload = fact_payloads.get(str(assessment["fact_id"]), {})
        component_id = str(payload.get("component_id"))
        hypothesis = hypotheses_by_id.get(str(assessment["hypothesis_id"]))
        hypothesis_payload = (
            fact_payloads.get(str(hypothesis["fact_id"]), {})
            if hypothesis is not None
            else {}
        )
        raw_evidence_ids = payload.get("evidence_ids")
        evidence_ids = (
            tuple(str(item) for item in raw_evidence_ids)
            if isinstance(raw_evidence_ids, list)
            else ()
        )
        linked = [evidence_by_id.get(item) for item in evidence_ids]
        identity_ok = (
            hypothesis is not None
            and bool(evidence_ids)
            and len(evidence_ids) == len(set(evidence_ids))
            and all(item is not None for item in linked)
            and hypothesis_payload.get("component_id") == component_id
            and hypothesis_payload.get("method_family_id")
            == payload.get("method_family_id")
        )
        for evidence in linked:
            if evidence is None:
                continue
            evidence_payload = fact_payloads.get(str(evidence["fact_id"]), {})
            identity_ok = identity_ok and (
                evidence["hypothesis_id"] == assessment["hypothesis_id"]
                and evidence_payload.get("component_id") == component_id
                and evidence_payload.get("method_family_id")
                == payload.get("method_family_id")
            )
        if not identity_ok:
            issues.append(
                OracleIssue(
                    "assessment_component_identity_mismatch",
                    "assessment evidence does not close one component hypothesis lineage",
                    (assessment_id,),
                )
            )
        if expert_scientific_lineage:
            hypothesis_key = (component_id, str(assessment["hypothesis_id"]))
            prior = assessment_by_expert_hypothesis.setdefault(
                hypothesis_key, assessment
            )
            if prior is not assessment:
                issues.append(
                    OracleIssue(
                        "duplicate_component_assessment",
                        "one expert hypothesis cycle has multiple assessments",
                        (
                            component_id,
                            str(assessment["hypothesis_id"]),
                            str(prior["fact_id"]),
                            str(assessment["fact_id"]),
                        ),
                    )
                )
            if (
                str(assessment["hypothesis_id"])
                == tip_hypothesis_by_component.get(component_id)
            ):
                assessment_by_component.setdefault(component_id, assessment)
        else:
            prior = assessment_by_component.setdefault(component_id, assessment)
            if prior is not assessment:
                issues.append(
                    OracleIssue(
                        "duplicate_component_assessment",
                        "one component has multiple hypothesis assessments",
                        (
                            component_id,
                            str(prior["fact_id"]),
                            str(assessment["fact_id"]),
                        ),
                    )
                )
        evidence_outcomes = [
            outcomes_by_id.get(str(item["outcome_id"]))
            for item in linked
            if item is not None
        ]
        result = next(
            (
                component_results_by_outcome.get(str(item["outcome_id"]))
                for item in reversed(evidence_outcomes)
                if item is not None
            ),
            None,
        )
        expected_assessment = _expected_component_assessment(result)
        if payload.get("assessment") != expected_assessment:
            issues.append(
                OracleIssue(
                    "component_assessment_semantics_mismatch",
                    "assessment differs from the independently recomputed component result",
                    (assessment_id,),
                )
            )
        assessment_result[assessment_id] = result

    _audit_component_dependency_closure(
        contract,
        specs_by_component,
        assessment_by_component,
        fact_payloads,
        issues,
        allow_ordinary_family_diversity=(
            research_contract_version in {"hypoloom-research-v7", "hypoloom-research-v8", "hypoloom-research-v9", "hypoloom-research-v10", "hypoloom-research-v11", "hypoloom-research-v12"}
        ),
    )
    terminal_updates: dict[str, tuple[str, AgentResponse]] = {}
    if expert_scientific_lineage and research_contract_version in {"hypoloom-research-v7", "hypoloom-research-v8", "hypoloom-research-v9", "hypoloom-research-v10", "hypoloom-research-v11", "hypoloom-research-v12"}:
        terminal_updates = _valid_expert_terminal_updates(
            contract,
            facts,
            fact_payloads,
            refs, run_evaluator_version,
            assessment_by_component,
            transitions,
            transition_payloads,
            attempts,
            attempt_payloads,
            artifacts,
            artifact_root,
            commands=commands,
        )
        if not batch_mode:
            _audit_expert_dependency_authorization(contract, attempts, attempt_payloads, terminal_updates, issues)
    _audit_component_verdicts(
        contract,
        facts,
        fact_payloads,
        assessments_by_id,
        assessment_by_component,
        assessment_result,
        issues,
        transitions=transitions,
        transition_payloads=transition_payloads,
        selected_family_dataflow=(
            research_contract_version in {"hypoloom-research-v7", "hypoloom-research-v8", "hypoloom-research-v9", "hypoloom-research-v10", "hypoloom-research-v11", "hypoloom-research-v12"}
        ),
        expert_lineages_terminated=(
            not expert_scientific_lineage
            or research_contract_version not in {"hypoloom-research-v7", "hypoloom-research-v8", "hypoloom-research-v9", "hypoloom-research-v10", "hypoloom-research-v11", "hypoloom-research-v12"}
            or all(
                component_id in terminal_updates
                for component_id in contract.required_ids
            )
        ),
        completion_mode=completion_mode,
        terminated_component_ids=frozenset(terminal_updates),
        selected_specs=projected_specs_by_component if batch_mode else None,
        workflow_version=workflow_version,
    )
    if completion_mode != "shared_goal":
        _audit_primary_implementation_lineage(
            contract, component_results, outcomes_by_component, fact_payloads, issues
        )

    if run_state == "COMPLETED":
        outcome_counts = _counts(
            f"{row['experiment_id']}::{row['execution_id']}" for row in outcomes
        )
        for head in experiment_heads:
            if head["state"] != "TERMINAL":
                continue
            spec = specs_by_experiment.get(str(head["workflow_id"]))
            if spec is None:
                continue
            key = f"{spec['experiment_id']}::{spec['execution_id']}"
            if outcome_counts.get(key, 0) != 1:
                issues.append(
                    OracleIssue(
                        "missing_terminal_experiment_outcome",
                        "terminal preregistered component lacks exactly one accepted outcome",
                        (str(spec["experiment_id"]), str(spec["execution_id"])),
                    )
                )


def _read_goal_component_contract(
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    run_evaluator_version: str | None,
    issues: list[OracleIssue],
) -> _GoalComponentContract | None:
    goals = [row for row in facts if row["fact_type"] == "GoalDefined"]
    if len(goals) != 1:
        issues.append(
            OracleIssue(
                "component_goal_count",
                f"component-v4 run must have one GoalDefined fact, found {len(goals)}",
            )
        )
        return None
    goal = goals[0]
    fact_id = str(goal["fact_id"])
    payload = payloads.get(fact_id, {})
    raw = payload.get("research_contract")
    valid = True

    def bad(code: str, message: str, *identifiers: str) -> None:
        nonlocal valid
        valid = False
        issues.append(OracleIssue(code, message, (fact_id, *identifiers)))

    if not isinstance(raw, Mapping) or raw.get("schema_version") not in {1, 2, 3}:
        bad(
            "component_contract_invalid",
            "GoalDefined lacks a component research_contract schema version 1",
        )
        return None
    goal_record = raw.get("goal")
    if raw.get("schema_version") == 3 and (not isinstance(goal_record, Mapping) or goal_record.get("evaluation") != {"mode": "grounding"}):
        bad("component_contract_invalid", "research schema 3 requires its explicit Grounding evaluation mode")
    if (
        not isinstance(goal_record, Mapping)
        or not _trimmed_text(goal_record.get("objective"))
        or goal_record.get("verdict_rule") != "required_conjunction"
    ):
        bad(
            "component_contract_invalid",
            "GoalDefined goal must carry an objective and required_conjunction verdict rule",
        )
    raw_roles = raw.get("roles")
    roles: tuple[str, ...] = ()
    if not isinstance(raw_roles, list):
        bad("component_contract_invalid", "research_contract.roles must be an array")
    else:
        parsed_roles = tuple(
            item.get("id") if isinstance(item, Mapping) else None
            for item in raw_roles
        )
        if (
            not 1 <= len(parsed_roles) <= 3
            or any(not _trimmed_text(item) for item in parsed_roles)
            or len(set(parsed_roles)) != len(parsed_roles)
        ):
            bad(
                "component_contract_invalid",
                "research_contract.roles must contain one to three unique role identities",
            )
        else:
            roles = tuple(str(item) for item in parsed_roles)
    if not _trimmed_text(run_evaluator_version):
        bad(
            "component_contract_invalid",
            "component run has no evaluator version in workflow_runs",
        )

    protocols = raw.get("protocols")
    if not isinstance(protocols, Mapping):
        bad("component_contract_invalid", "research_contract.protocols must be an object")
        protocols = {}
    delivery = raw.get("delivery_contracts")
    delivery_protocols = (
        delivery.get("protocols") if isinstance(delivery, Mapping) else {}
    )
    if not isinstance(delivery_protocols, Mapping):
        delivery_protocols = {}
    raw_components = raw.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        bad(
            "component_contract_invalid",
            "research_contract.components must be a non-empty ordered array",
        )
        return None

    components: list[_ComponentContract] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_components):
        location = f"components[{index}]"
        if not isinstance(item, Mapping):
            bad("component_contract_invalid", f"{location} must be an object")
            continue
        component_id = item.get("id")
        tier = item.get("tier")
        name = item.get("name")
        owner = item.get("owner")
        proposers = _text_tuple(item.get("proposers"))
        dependencies = _text_tuple(item.get("dependencies"), allow_empty=True)
        required_artifacts = _text_tuple(
            item.get("required_artifacts"), allow_empty=True
        )
        if (
            not _trimmed_text(component_id)
            or tier not in {"primary", "must", "should"}
            or not _trimmed_text(name)
            or component_id != f"{tier}.{name}"
            or component_id in seen
            or owner not in roles
            or proposers is None
            or not proposers
            or any(role not in roles for role in proposers)
            or dependencies is None
            or required_artifacts is None
        ):
            bad(
                "component_identity_invalid",
                f"{location} has an invalid stable ID, tier, owner, proposer, dependency, or artifact inventory",
                str(component_id),
            )
            continue
        seen.add(str(component_id))
        result = item.get("result")
        result_kind = result.get("kind") if isinstance(result, Mapping) else None
        metric_name = metric_direction = metric_target = None
        deferred = isinstance(result, Mapping) and result.get("deferred") is True
        if deferred:
            metric_name, metric_direction = result.get("metric"), result.get("direction")
            if (raw.get("schema_version") not in {2, 3} or result_kind != "metric"
                    or set(result) - {"kind", "deferred", "metric", "direction"}
                    or metric_name is not None and not _trimmed_text(metric_name)
                    or metric_direction is not None and metric_direction not in {"MINIMIZE", "MAXIMIZE"}):
                bad("component_metric_contract_invalid", f"{location} has an invalid deferred measurement", str(component_id))
        elif result_kind == "metric" and isinstance(result, Mapping):
            metric_name = result.get("metric")
            metric_direction = result.get("direction")
            try:
                metric_target = _normalize_decimal_text(
                    result.get("target"), f"{location}.result.target"
                )
            except ValueError:
                metric_target = None
            if (
                not _trimmed_text(metric_name)
                or metric_direction not in {"MAXIMIZE", "MINIMIZE"}
                or metric_target is None
            ):
                bad(
                    "component_metric_contract_invalid",
                    f"{location} has an invalid decision metric",
                    str(component_id),
                )
        elif result_kind == "artifact" and isinstance(result, Mapping):
            if result.get("evaluation_kind") != "artifact_check":
                bad(
                    "component_artifact_contract_invalid",
                    f"{location} has an invalid artifact criterion",
                    str(component_id),
                )
        else:
            bad(
                "component_result_kind_invalid",
                f"{location} result kind must be metric or artifact",
                str(component_id),
            )
            continue
        raw_seeds = item.get("seeds")
        if (
            not isinstance(raw_seeds, list)
            or any(
                isinstance(seed, bool) or not isinstance(seed, int) or seed < 0
                for seed in raw_seeds
            )
            or raw_seeds != sorted(raw_seeds)
            or len(set(raw_seeds)) != len(raw_seeds)
            or (result_kind == "artifact" and raw_seeds)
        ):
            bad(
                "component_trial_plan_invalid",
                f"{location} has an invalid seed plan",
                str(component_id),
            )
            raw_seeds = []

        protocol_name = item.get("protocol")
        requirements: Mapping[str, Any] = {}
        if protocol_name is not None:
            protocol = (
                protocols.get(protocol_name)
                if _trimmed_text(protocol_name)
                else None
            )
            if (
                not _trimmed_text(protocol_name)
                or not isinstance(protocol, Mapping)
                or protocol.get("result_kind") != result_kind
                or not isinstance(protocol.get("requirements"), Mapping)
            ):
                bad(
                    "component_protocol_mismatch",
                    f"{location} does not resolve to a same-kind protocol",
                    str(component_id),
                )
            else:
                requirements = protocol["requirements"]
        elif len(raw_components) != 1:
            bad(
                "component_protocol_mismatch",
                f"{location} must name a protocol in a multi-component contract",
                str(component_id),
            )
        protocol_artifacts, protocol_counts = _artifact_requirement_counts(
            requirements.get("required_artifacts", []),
            f"{location}.protocol.required_artifacts",
        )
        if protocol_artifacts is None:
            bad(
                "component_protocol_mismatch",
                f"{location} protocol has an invalid artifact inventory",
                str(component_id),
            )
            protocol_artifacts, protocol_counts = (), {}
        if protocol_artifacts and set(protocol_artifacts) != set(required_artifacts):
            bad(
                "component_protocol_mismatch",
                f"{location} component and protocol artifact inventories differ",
                str(component_id),
            )
        artifact_counts = {artifact_id: 1 for artifact_id in required_artifacts}
        for artifact_id, count in protocol_counts.items():
            artifact_counts[artifact_id] = max(
                artifact_counts.get(artifact_id, 1), count
            )
        if "checkpoints" in artifact_counts and raw_seeds:
            artifact_counts["checkpoints"] = max(
                artifact_counts["checkpoints"], len(raw_seeds)
            )
        required_metrics = _text_tuple(
            requirements.get("required_metrics", []), allow_empty=True
        )
        if required_metrics is None:
            bad(
                "component_protocol_mismatch",
                f"{location} protocol metrics must be unique text",
                str(component_id),
            )
            required_metrics = ()
        artifact_validators = _artifact_validator_contracts(
            requirements.get("artifact_validators", []), required_artifacts
        )
        if artifact_validators is None:
            bad(
                "component_protocol_mismatch",
                f"{location} protocol artifact validators are invalid",
                str(component_id),
            )
            artifact_validators = ()

        required_inputs: list[_ComponentInput] = []
        raw_inputs = item.get("required_inputs")
        if not isinstance(raw_inputs, list):
            bad(
                "component_input_contract_invalid",
                f"{location}.required_inputs must be an array",
                str(component_id),
            )
            raw_inputs = []
        input_resources: set[str] = set()
        for input_index, raw_input in enumerate(raw_inputs):
            input_location = f"{location}.required_inputs[{input_index}]"
            if not isinstance(raw_input, Mapping):
                bad(
                    "component_input_contract_invalid",
                    f"{input_location} must be an object",
                    str(component_id),
                )
                continue
            resource = raw_input.get("resource")
            producer = raw_input.get("produced_by")
            logical = raw_input.get("artifact")
            count = raw_input.get("min_count", 1)
            if (
                not _trimmed_text(resource)
                or resource in input_resources
                or not _trimmed_text(producer)
                or not _trimmed_text(logical)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 1
            ):
                bad(
                    "component_input_contract_invalid",
                    f"{input_location} has an invalid producer requirement",
                    str(component_id),
                )
                continue
            input_resources.add(str(resource))
            required_inputs.append(
                _ComponentInput(
                    resource=str(resource),
                    producer_component=str(producer),
                    logical_artifact=str(logical),
                    min_count=count,
                )
            )
        delivery_protocol = (
            delivery_protocols.get(protocol_name)
            if _trimmed_text(protocol_name)
            else None
        )
        arms = (
            ("control", "treatment")
            if isinstance(delivery_protocol, Mapping)
            and delivery_protocol.get("execution_shape")
            == "paired_control_treatment"
            else ()
        )
        seeds = tuple(raw_seeds)
        trial_ids = (
            tuple(f"{arm}:{seed}" for arm in arms for seed in seeds)
            if arms
            else tuple(str(seed) for seed in seeds)
        )
        components.append(
            _ComponentContract(
                component_id=str(component_id),
                tier=str(tier),
                owner_role=str(owner),
                proposer_roles=proposers,
                dependencies=dependencies,
                required_inputs=tuple(required_inputs),
                result_kind=str(result_kind),
                metric_name=(str(metric_name) if metric_name is not None else None),
                metric_direction=(
                    str(metric_direction) if metric_direction is not None else None
                ),
                metric_target=metric_target,
                seeds=seeds,
                arms=arms,
                trial_ids=trial_ids,
                required_artifacts=required_artifacts,
                artifact_counts=artifact_counts,
                required_metrics=required_metrics,
                artifact_validators=artifact_validators,
                deferred_measurement=deferred,
                target_overlay_policy=(
                    result.get("target_overlay_policy")
                    if isinstance(result, Mapping)
                    and isinstance(result.get("target_overlay_policy"), str)
                    else None
                ),
            )
        )

    by_id = {item.component_id: item for item in components}
    for component in components:
        input_producers = {item.producer_component for item in component.required_inputs}
        if any(
            dependency not in by_id or dependency == component.component_id
            for dependency in component.dependencies
        ):
            bad(
                "component_dependency_invalid",
                "component dependency is unknown or self-referential",
                component.component_id,
            )
        if not input_producers <= set(component.dependencies):
            bad(
                "component_dependency_invalid",
                "required producer inputs must also be declared dependencies",
                component.component_id,
            )
        for requirement in component.required_inputs:
            producer = by_id.get(requirement.producer_component)
            if (
                producer is None
                or requirement.logical_artifact not in producer.required_artifacts
            ):
                bad(
                    "component_dependency_invalid",
                    "required input names an unknown producer artifact",
                    component.component_id,
                    requirement.producer_component,
                    requirement.logical_artifact,
                )
    if _component_graph_has_cycle(by_id):
        bad("component_dependency_cycle", "component dependency graph contains a cycle")
    if not any(item.tier == "primary" for item in components):
        bad(
            "component_contract_invalid",
            "component contract must contain at least one primary leaf",
        )
    if not valid:
        return None
    return _GoalComponentContract(
        fact_id=fact_id,
        goal_id=str(goal["goal_id"]),
        roles=roles,
        components=tuple(components),
    )


def _text_tuple(value: Any, *, allow_empty: bool = False) -> tuple[str, ...] | None:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not _trimmed_text(item) for item in value)
        or len(set(value)) != len(value)
    ):
        return None
    return tuple(str(item) for item in value)


def _artifact_requirement_counts(
    value: Any,
    _location: str,
) -> tuple[tuple[str, ...] | None, dict[str, int]]:
    if isinstance(value, Mapping):
        declarations = list(value.items())
    elif isinstance(value, list):
        declarations: list[tuple[Any, Any]] = []
        for item in value:
            if isinstance(item, str):
                declarations.append((item, 1))
            elif isinstance(item, Mapping):
                declarations.append(
                    (
                        item.get("artifact", item.get("id")),
                        item.get("min_count", 1),
                    )
                )
            else:
                return None, {}
    else:
        return None, {}
    ordered: list[str] = []
    counts: dict[str, int] = {}
    for artifact_id, count in declarations:
        if isinstance(count, Mapping):
            count = count.get("min_count") if set(count) == {"min_count"} else None
        if (
            not _trimmed_text(artifact_id)
            or artifact_id in counts
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            return None, {}
        ordered.append(str(artifact_id))
        counts[str(artifact_id)] = count
    return tuple(ordered), counts


def _artifact_validator_contracts(
    value: Any,
    required_artifacts: Sequence[str],
) -> tuple[Mapping[str, Any], ...] | None:
    if not isinstance(value, list):
        return None
    common = {"artifact", "type"}
    fields_by_kind = {
        "json_contract": common
        | {"required_paths", "equal_groups", "exact_key_sets", "allowed_values"},
        "text_contract": common
        | {
            "min_chars",
            "required_sections",
            "required_terms",
            "forbidden_terms",
        },
        "unified_diff": common | {"base_scope", "apply_check"},
    }
    validators: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        artifact = item.get("artifact")
        kind = item.get("type")
        allowed = fields_by_kind.get(str(kind))
        if (
            not _trimmed_text(artifact)
            or artifact not in required_artifacts
            or allowed is None
            or not set(item) <= allowed
        ):
            return None
        if kind == "json_contract":
            if _text_tuple(
                item.get("required_paths", []), allow_empty=True
            ) is None:
                return None
            groups = item.get("equal_groups", [])
            if not isinstance(groups, list) or any(
                (fields := _text_tuple(group)) is None or len(fields) < 2
                for group in groups
            ):
                return None
            exact = item.get("exact_key_sets", {})
            choices = item.get("allowed_values", {})
            if (
                not isinstance(exact, Mapping)
                or not isinstance(choices, Mapping)
                or any(
                    not _trimmed_text(path)
                    or _text_tuple(keys, allow_empty=True) is None
                    for path, keys in exact.items()
                )
                or any(
                    not _trimmed_text(path)
                    or not isinstance(options, list)
                    or not options
                    for path, options in choices.items()
                )
            ):
                return None
            try:
                json.dumps(
                    choices,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                return None
        elif kind == "text_contract":
            minimum = item.get("min_chars", 1)
            if (
                isinstance(minimum, bool)
                or not isinstance(minimum, int)
                or minimum < 1
                or any(
                    _text_tuple(item.get(field, []), allow_empty=True) is None
                    for field in (
                        "required_sections",
                        "required_terms",
                        "forbidden_terms",
                    )
                )
            ):
                return None
        else:
            if not isinstance(item.get("apply_check", True), bool):
                return None
            if item.get("base_scope") is not None and not _trimmed_text(
                item.get("base_scope")
            ):
                return None
        validators.append(dict(item))
    return tuple(validators)


def _component_graph_has_cycle(
    components: Mapping[str, _ComponentContract],
) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> bool:
        if component_id in visiting:
            return True
        if component_id in visited or component_id not in components:
            return False
        visiting.add(component_id)
        if any(visit(item) for item in components[component_id].dependencies):
            return True
        visiting.remove(component_id)
        visited.add(component_id)
        return False

    return any(visit(component_id) for component_id in components)


def _audit_v4_fact_payload_identity(
    fact: sqlite3.Row,
    payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
    *,
    expected_schema: int = 4,
) -> None:
    fact_id = str(fact["fact_id"])
    payload = payloads.get(fact_id, {})
    subject_field = {
        "GoalDefined": "goal_id",
        "HypothesisProposed": "hypothesis_id",
        "ExperimentPreregistered": "experiment_id",
        "CanonicalOutcomeAccepted": "outcome_id",
        "EvidenceLinked": "evidence_id",
        "HypothesisAssessed": "assessment_id",
        "GoalVerdictRecorded": "verdict_id",
    }.get(str(fact["fact_type"]))
    valid = (
        int(fact["schema_version"]) == expected_schema
        and payload.get("schema_version") == expected_schema
        and payload.get("fact_id") == fact_id
        and payload.get("run_id") == fact["run_id"]
        and payload.get("fact_type") == fact["fact_type"]
        and subject_field is not None
        and payload.get(subject_field) == fact["subject_id"]
    )
    for field in (
        "goal_id",
        "hypothesis_id",
        "experiment_id",
        "execution_id",
        "outcome_id",
        "artifact_id",
    ):
        if field in payload and payload.get(field) != _row_get(fact, field):
            valid = False
    if not valid:
        issues.append(
            OracleIssue(
                "component_fact_payload_identity_mismatch",
                "component fact payload identity differs from its indexed raw row",
                (fact_id,),
            )
        )


def _audit_component_fact_references(
    contract: _GoalComponentContract,
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    refs: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
) -> None:
    by_fact_id = {str(row["fact_id"]): row for row in facts}
    by_subject = {
        (str(row["fact_type"]), str(row["subject_id"])): row for row in facts
    }
    actual: dict[str, tuple[tuple[str, int, str], ...]] = {}
    for fact_id in by_fact_id:
        actual[fact_id] = tuple(
            (str(row["role"]), int(row["ordinal"]), str(row["ref_fact_id"]))
            for row in refs
            if str(row["fact_id"]) == fact_id
        )

    for fact_id, fact in by_fact_id.items():
        payload = payloads.get(fact_id, {})
        expected: list[tuple[str, int, str]] = []
        kind = str(fact["fact_type"])
        if kind == "HypothesisProposed":
            expected.append(("goal", 0, contract.fact_id))
            parent_id = payload.get("parent_hypothesis_id")
            parent = by_subject.get(("HypothesisProposed", str(parent_id)))
            if parent_id is not None and parent is not None:
                expected.append(("parent_hypothesis", 0, str(parent["fact_id"])))
        elif kind == "ExperimentPreregistered":
            hypothesis = by_subject.get(
                ("HypothesisProposed", str(fact["hypothesis_id"]))
            )
            if hypothesis is not None:
                expected.append(("hypothesis", 0, str(hypothesis["fact_id"])))
            if (
                _row_get(fact, "schema_version") == 5
                and payload.get("schema_version") == 5
            ):
                predecessor_id = payload.get("revision_of_outcome_id")
                predecessor = by_subject.get(
                    ("CanonicalOutcomeAccepted", str(predecessor_id))
                )
                if predecessor_id is not None and predecessor is not None:
                    expected.append(
                        (
                            "revision_of_outcome",
                            0,
                            str(predecessor["fact_id"]),
                        )
                    )
            seen_outcomes: set[str] = set()
            upstream = payload.get("upstream_outcomes")
            if isinstance(upstream, list):
                for item in upstream:
                    if not isinstance(item, Mapping):
                        continue
                    outcome_id = str(item.get("outcome_id"))
                    if outcome_id in seen_outcomes:
                        continue
                    target = by_subject.get(("CanonicalOutcomeAccepted", outcome_id))
                    if target is not None:
                        expected.append(
                            (
                                "producer_outcome",
                                len(seen_outcomes),
                                str(target["fact_id"]),
                            )
                        )
                        seen_outcomes.add(outcome_id)
        elif kind == "CanonicalOutcomeAccepted":
            target = by_subject.get(
                ("ExperimentPreregistered", str(fact["experiment_id"]))
            )
            if target is not None:
                expected.append(("experiment", 0, str(target["fact_id"])))
        elif kind == "EvidenceLinked":
            hypothesis = by_subject.get(
                ("HypothesisProposed", str(fact["hypothesis_id"]))
            )
            outcome = by_subject.get(
                ("CanonicalOutcomeAccepted", str(fact["outcome_id"]))
            )
            if hypothesis is not None:
                expected.append(("hypothesis", 0, str(hypothesis["fact_id"])))
            if outcome is not None:
                expected.append(("outcome", 0, str(outcome["fact_id"])))
        elif kind == "HypothesisAssessed":
            hypothesis = by_subject.get(
                ("HypothesisProposed", str(fact["hypothesis_id"]))
            )
            if hypothesis is not None:
                expected.append(("hypothesis", 0, str(hypothesis["fact_id"])))
            evidence_ids = payload.get("evidence_ids")
            if isinstance(evidence_ids, list):
                for ordinal, evidence_id in enumerate(evidence_ids):
                    target = by_subject.get(("EvidenceLinked", str(evidence_id)))
                    if target is not None:
                        expected.append(
                            ("evidence", ordinal, str(target["fact_id"]))
                        )
        elif kind == "GoalVerdictRecorded":
            expected.append(("goal", 0, contract.fact_id))
            assessment_ids = payload.get("assessment_ids")
            if isinstance(assessment_ids, list):
                for ordinal, assessment_id in enumerate(assessment_ids):
                    target = by_subject.get(
                        ("HypothesisAssessed", str(assessment_id))
                    )
                    if target is not None:
                        expected.append(
                            ("assessment", ordinal, str(target["fact_id"]))
                        )
        if set(actual.get(fact_id, ())) != set(expected) or len(
            actual.get(fact_id, ())
        ) != len(expected):
            issues.append(
                OracleIssue(
                    "component_fact_ref_mismatch",
                    "raw research_fact_refs do not exactly close the component fact payload lineage",
                    (fact_id,),
                )
            )


def _audit_component_spec(
    fact: sqlite3.Row,
    payload: Mapping[str, Any],
    component: _ComponentContract,
    run_evaluator_version: str | None,
    outcomes_by_id: Mapping[str, sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    artifacts_by_id: Mapping[str, sqlite3.Row],
    artifact_root: Path,
    issues: list[OracleIssue],
    *,
    selected_outcomes_by_component: Mapping[str, sqlite3.Row] | None = None,
    allow_clean_refutation_inputs: bool = True,
) -> None:
    fact_id = str(fact["fact_id"])
    metric = payload.get("metric")
    metric_ok = metric is None
    if (payload.get("goal_evaluation", {}).get("scope") == "GOAL" and payload.get("measurement") is None):
        metric_ok = metric is None and payload.get("result_kind") == "artifact"
    elif component.deferred_measurement:
        try:
            declaration = ScientificMeasurement.from_dict(payload["measurement"])
            metric_ok = (metric == declaration.to_dict()["metric"]
                and (component.metric_name is None or component.metric_name == declaration.metric.name)
                and (component.metric_direction is None or component.metric_direction == declaration.metric.direction.value))
        except (KeyError, TypeError, ValueError): metric_ok = False
    elif component.result_kind == "metric" and isinstance(metric, Mapping):
        try:
            target = _normalize_decimal_text(
                metric.get("target"), "preregistered metric target"
            )
        except ValueError:
            target = None
        metric_ok = (
            metric.get("name") == component.metric_name
            and metric.get("direction") == component.metric_direction
            and target == component.metric_target
        )
    expected_trial_plan = {
        "seeds": list(component.seeds),
        "arms": list(component.arms),
        "trial_ids": list(component.trial_ids),
    }
    if (
        payload.get("result_kind") != ("artifact" if payload.get("goal_evaluation", {}).get("scope") == "GOAL"
            and payload.get("measurement") is None else component.result_kind)
        or not metric_ok
        or payload.get("trial_plan") != expected_trial_plan
        or payload.get("required_artifacts") != list(component.required_artifacts)
        or payload.get("evaluator_version") != run_evaluator_version
        or not isinstance(payload.get("execution_contract"), Mapping)
    ):
        issues.append(
            OracleIssue(
                "component_preregistration_contract_mismatch",
                "preregistration result, trial, artifact, evaluator, or execution contract differs from GoalDefined",
                (fact_id, component.component_id),
            )
        )

    raw_upstream = payload.get("upstream_outcomes")
    if not isinstance(raw_upstream, list):
        raw_upstream = []
    input_outcomes = (
        _COMPARABLE_OUTCOMES
        if allow_clean_refutation_inputs
        else {"SUCCESS"}
    )
    if len(raw_upstream) != len(component.required_inputs):
        issues.append(
            OracleIssue(
                "component_input_binding_mismatch",
                "preregistration does not bind every required producer input exactly once",
                (fact_id, component.component_id),
            )
        )
    for index, requirement in enumerate(component.required_inputs):
        binding = raw_upstream[index] if index < len(raw_upstream) else None
        if not isinstance(binding, Mapping):
            continue
        identity = (
            binding.get("resource"),
            binding.get("produced_by_component"),
            binding.get("logical_artifact"),
            binding.get("min_count"),
        )
        expected = (
            requirement.resource,
            requirement.producer_component,
            requirement.logical_artifact,
            requirement.min_count,
        )
        outcome = outcomes_by_id.get(str(binding.get("outcome_id")))
        selected_outcome = (None if selected_outcomes_by_component is None
            else selected_outcomes_by_component.get(requirement.producer_component))
        outcome_payload = (
            payloads.get(str(outcome["fact_id"]), {}) if outcome is not None else {}
        )
        artifact = (
            artifacts_by_id.get(str(binding.get("artifact_id")))
            if outcome is not None
            else None
        )
        logical = _logical_artifact_paths(outcome_payload)
        paths = logical.get(requirement.logical_artifact, ())
        publication_ok = False
        if artifact is not None:
            root = _artifact_materialized_root(artifact_root, artifact)
            publication_ok = (
                root is not None
                and len(paths) >= requirement.min_count
                and all(_publication_file(root, path) is not None for path in paths)
            )
        if (
            identity != expected
            or outcome is None
            or (selected_outcomes_by_component is not None and (
                selected_outcome is None
                or selected_outcome["outcome_id"] != binding.get("outcome_id")))
            or outcome_payload.get("component_id")
            != requirement.producer_component
            or outcome_payload.get("method_family_id")
            != payload.get("method_family_id")
            or outcome_payload.get("outcome") not in input_outcomes
            or outcome["artifact_id"] != binding.get("artifact_id")
            or artifact is None
            or not publication_ok
            or int(outcome["raw_order"]) >= int(fact["raw_order"])
        ):
            issues.append(
                OracleIssue(
                    "component_input_binding_mismatch",
                    "producer input does not resolve to an earlier comparable same-family logical artifact publication",
                    (
                        fact_id,
                        requirement.resource,
                        requirement.producer_component,
                    ),
                )
            )


def _logical_artifact_paths(payload: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    raw = payload.get("logical_artifacts")
    if not isinstance(raw, list):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            return {}
        logical_id = item.get("logical_artifact")
        paths = item.get("paths")
        if (
            not _trimmed_text(logical_id)
            or logical_id in result
            or not isinstance(paths, list)
            or not paths
            or any(_safe_relative_path(path) is None for path in paths)
            or len(set(paths)) != len(paths)
        ):
            return {}
        result[str(logical_id)] = tuple(str(path) for path in paths)
    return result


def _bound_checkpoint_paths(
    component: _ComponentContract,
    spec_payload: Mapping[str, Any],
    outcomes_by_id: Mapping[str, sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...] | None:
    requirements = tuple(
        (index, requirement)
        for index, requirement in enumerate(component.required_inputs)
        if requirement.logical_artifact == "checkpoints"
    )
    if not requirements:
        return ()
    raw_upstream = spec_payload.get("upstream_outcomes")
    if not isinstance(raw_upstream, list):
        return None
    paths: list[str] = []
    for index, requirement in requirements:
        binding = raw_upstream[index] if index < len(raw_upstream) else None
        if not isinstance(binding, Mapping):
            return None
        outcome = outcomes_by_id.get(str(binding.get("outcome_id")))
        outcome_payload = (
            payloads.get(str(outcome["fact_id"]), {}) if outcome is not None else {}
        )
        bound = _logical_artifact_paths(outcome_payload).get("checkpoints", ())
        if (
            outcome is None
            or binding.get("resource") != requirement.resource
            or binding.get("produced_by_component")
            != requirement.producer_component
            or binding.get("logical_artifact") != "checkpoints"
            or binding.get("min_count") != requirement.min_count
            or len(bound) < requirement.min_count
        ):
            return None
        paths.extend(bound)
    if len(paths) != len(set(paths)):
        return None
    return tuple(paths)


def _publication_file(root: Path, relative: str) -> Path | None:
    safe = _safe_relative_path(relative)
    if safe is None or safe == "result.json":
        return None
    path = root.joinpath(*PurePosixPath(safe).parts)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            return None
    except OSError:
        return None
    return path


def _component_json_path(value: Any, dotted: str) -> Any:
    current = value
    for token in dotted.split("."):
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif (
            isinstance(current, list)
            and token.isdigit()
            and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            return _MISSING_JSON_PATH
    return current


def _validate_component_artifact_bytes(
    path: Path,
    validator: Mapping[str, Any],
) -> None:
    kind = validator["type"]
    if kind == "json_contract":
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        for dotted in validator.get("required_paths", []):
            found = _component_json_path(value, str(dotted))
            if found is _MISSING_JSON_PATH or found is None or found in ("", [], {}):
                raise ValueError(f"missing JSON path {dotted!r}")
        for group in validator.get("equal_groups", []):
            values = [_component_json_path(value, str(dotted)) for dotted in group]
            if any(found is _MISSING_JSON_PATH for found in values) or any(
                type(found) is not type(values[0]) or found != values[0]
                for found in values[1:]
            ):
                raise ValueError(f"JSON paths are not equal: {group!r}")
        for dotted, expected_keys in validator.get("exact_key_sets", {}).items():
            found = _component_json_path(value, str(dotted))
            if not isinstance(found, Mapping) or set(found) != set(expected_keys):
                raise ValueError(
                    f"JSON path {dotted!r} does not have the exact required keys"
                )
        for dotted, choices in validator.get("allowed_values", {}).items():
            found = _component_json_path(value, str(dotted))
            if found is _MISSING_JSON_PATH or not any(
                type(found) is type(choice) and found == choice for choice in choices
            ):
                raise ValueError(f"JSON path {dotted!r} has a forbidden value")
        return
    payload = path.read_bytes()
    text = payload.decode("utf-8")
    if kind == "text_contract":
        minimum = int(validator.get("min_chars", 1))
        if len(text.strip()) < minimum:
            raise ValueError(f"text requires at least {minimum} characters")
        folded = text.casefold()
        for key in ("required_sections", "required_terms"):
            for token in validator.get(key, []):
                if str(token).casefold() not in folded:
                    raise ValueError(f"text is missing required token {token!r}")
        for token in validator.get("forbidden_terms", []):
            if str(token).casefold() in folded:
                raise ValueError(f"text contains forbidden token {token!r}")
        return
    old_paths = re.findall(r"(?m)^--- ([^\t\r\n]+)", text)
    new_paths = re.findall(r"(?m)^\+\+\+ ([^\t\r\n]+)", text)
    if (
        "\x00" in text
        or re.search(r"(?m)^@@ [^\r\n]+ @@", text) is None
        or not old_paths
        or len(old_paths) != len(new_paths)
    ):
        raise ValueError("unified diff lacks paired headers and a text hunk")
    for header in (*old_paths, *new_paths):
        if header == "/dev/null":
            continue
        if not header.startswith(("a/", "b/")) or _safe_relative_path(
            header[2:]
        ) is None:
            raise ValueError(f"unsafe unified diff path {header!r}")


def _audit_component_artifact_validators(
    root: Path,
    logical: Mapping[str, tuple[str, ...]],
    component: _ComponentContract,
    fact_id: str,
    issues: list[OracleIssue],
) -> bool:
    valid = True
    for validator in component.artifact_validators:
        artifact_id = str(validator["artifact"])
        paths = logical.get(artifact_id, ())
        if not paths:
            issues.append(
                OracleIssue(
                    "component_artifact_validator_mismatch",
                    "artifact validator has no materialized logical artifact",
                    (fact_id, artifact_id),
                )
            )
            valid = False
            continue
        for relative in paths:
            path = _publication_file(root, relative)
            try:
                if path is None:
                    raise ValueError("artifact path is not a publication file")
                _validate_component_artifact_bytes(path, validator)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                issues.append(
                    OracleIssue(
                        "component_artifact_validator_mismatch",
                        str(exc),
                        (fact_id, artifact_id, relative),
                    )
                )
                valid = False
    return valid


def _repairable_result_envelope_code(
    root: Path | None,
    component: _ComponentContract,
) -> str | None:
    """Independently recognize only candidate-authored envelope shape failures."""
    if root is None:
        return None
    result_path = root / "result.json"
    if result_path.is_symlink():
        return None
    try:
        raw = json.loads(
            result_path.read_bytes(),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except FileNotFoundError:
        return "missing_result"
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return "invalid_result_json"
    except OSError:
        return None
    if not isinstance(raw, Mapping):
        return "invalid_result"
    required = {"artifacts"} | (
        {"trials"} if component.result_kind == "metric" else set()
    )
    shape = not required <= set(raw) or not set(raw) <= required | {"identity"}
    if "identity" in raw:
        return None
    missing = object()
    artifacts = raw.get("artifacts", missing)
    declarations = (
        tuple(artifacts.values())
        if isinstance(artifacts, Mapping)
        else (() if artifacts is missing else (artifacts,))
    )
    if any(
        isinstance(value, str) or isinstance(value, list) and bool(value)
        for value in declarations
    ):
        return None
    if isinstance(artifacts, Mapping) and set(artifacts) != set(
        component.required_artifacts
    ):
        return None
    if shape:
        return "invalid_result_shape"
    if artifacts is not missing and (
        not isinstance(artifacts, Mapping)
        or any(not isinstance(value, list) or not value for value in declarations)
    ):
        return "invalid_artifact_map"
    return None


def _repairable_text_contract_diagnostic(
    root: Path | None,
    component: _ComponentContract,
    *,
    allow_identity: bool,
    expected_method_family: object,
) -> str | None:
    """Independently reproduce a candidate-authored text-contract failure."""
    if root is None or component.result_kind != "artifact":
        return None
    reconstruction_issues: list[OracleIssue] = []
    envelope = _read_component_envelope(
        root,
        component,
        "repairable-text-contract-reconstruction",
        reconstruction_issues,
    )
    if (
        envelope is None
        or reconstruction_issues
        or ("identity" in envelope and not allow_identity)
    ):
        return None
    logical, _family, _implementation, _config = _component_artifact_map(
        root,
        envelope,
        component,
        "repairable-text-contract-reconstruction",
        reconstruction_issues,
    )
    if (
        logical is None
        or reconstruction_issues
        or set(logical) != set(component.required_artifacts)
    ):
        return None
    if allow_identity:
        if not _repairable_result_identity_matches(
            root,
            envelope,
            logical,
            expected_method_family,
        ):
            return None
    matched: str | None = None
    for validator in component.artifact_validators:
        artifact_id = str(validator["artifact"])
        paths = logical.get(artifact_id, ())
        if not paths:
            return None
        kind = validator["type"]
        if matched is not None and kind != "text_contract":
            return None
        for relative in paths:
            path = _publication_file(root, relative)
            try:
                if path is None:
                    return None
                if kind == "text_contract":
                    text = path.read_text(encoding="utf-8")
                    minimum = validator.get("min_chars", 1)
                    if (
                        isinstance(minimum, bool)
                        or not isinstance(minimum, int)
                        or minimum < 1
                    ):
                        return None
                    failure: str | None = None
                    if len(text.strip()) < minimum:
                        failure = f"requires at least {minimum} characters"
                    folded = text.casefold()
                    for key in ("required_sections", "required_terms"):
                        tokens = validator.get(key, [])
                        if not isinstance(tokens, (list, tuple)):
                            return None
                        for token in tokens:
                            if (
                                not isinstance(token, str)
                                or not token
                                or token != token.strip()
                            ):
                                return None
                            if failure is None and token.casefold() not in folded:
                                failure = f"missing required text {token!r}"
                    forbidden = validator.get("forbidden_terms", [])
                    if not isinstance(forbidden, (list, tuple)):
                        return None
                    for token in forbidden:
                        if (
                            not isinstance(token, str)
                            or not token
                            or token != token.strip()
                        ):
                            return None
                        if failure is None and token.casefold() in folded:
                            failure = f"contains forbidden text {token!r}"
                    if failure is not None and matched is None:
                        matched = (
                            f"text_contract_failed@{path.name}: {failure}"
                        )
                    continue
                _validate_component_artifact_bytes(path, validator)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ):
                return None
    return matched


_IMPLEMENTATION_DIGEST_MISMATCH = (
    "implementation_digest_mismatch@implementation_sha256: bytes do not match"
)


def _repairable_result_identity_state(
    root: Path,
    envelope: Mapping[str, Any],
    logical: Mapping[str, tuple[str, ...]],
    expected_method_family: object,
) -> tuple[bool, str | None]:
    """Validate candidate identity and recompute any declared patch digest."""
    identity = envelope.get("identity")
    if identity is None:
        implementation = None
    elif (
        not isinstance(identity, Mapping)
        or not identity
        or not set(identity)
        <= {
            "method_family_id",
            "implementation_sha256",
            "core_config_sha256",
        }
    ):
        return False, None
    else:
        family = identity.get("method_family_id")
        if family is not None and (
            not isinstance(family, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", family) is None
            or family != expected_method_family
        ):
            return False, None
        implementation = identity.get("implementation_sha256")
        config = identity.get("core_config_sha256")
        if any(
            digest is not None
            and (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            )
            for digest in (implementation, config)
        ):
            return False, None

    patch_paths = logical.get("implementation_patch")
    if patch_paths is None:
        return True, None
    if len(patch_paths) != 1 or implementation is None:
        return False, None
    path = _publication_file(root, patch_paths[0])
    if path is None:
        return False, None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return False, None
    if digest.hexdigest() != implementation:
        return True, _IMPLEMENTATION_DIGEST_MISMATCH
    return True, None


def _repairable_result_identity_matches(
    root: Path,
    envelope: Mapping[str, Any],
    logical: Mapping[str, tuple[str, ...]],
    expected_method_family: object,
) -> bool:
    """Keep the frozen /4 rule: candidate identity must match completely."""
    valid, diagnostic = _repairable_result_identity_state(
        root,
        envelope,
        logical,
        expected_method_family,
    )
    return valid and diagnostic is None


def _repairable_extra_file_inventory_diagnostic(
    root: Path | None,
    component: _ComponentContract,
    expected_method_family: object,
) -> str | None:
    """Reconstruct an exact safe extra-file first failure."""
    if (
        root is None
        or component.result_kind != "artifact"
    ):
        return None
    reconstruction_issues: list[OracleIssue] = []
    envelope = _read_component_envelope(
        root,
        component,
        "repairable-extra-file-inventory-reconstruction",
        reconstruction_issues,
    )
    if envelope is None or reconstruction_issues:
        return None
    logical, _family, _implementation, _config = _component_artifact_map(
        root,
        envelope,
        component,
        "repairable-extra-file-inventory-reconstruction",
        reconstruction_issues,
    )
    if (
        logical is None
        or set(logical) != set(component.required_artifacts)
        or len(reconstruction_issues) != 1
        or reconstruction_issues[0].code != "component_artifact_inventory_mismatch"
        or reconstruction_issues[0].message
        != "publication contains missing or undeclared result files"
    ):
        return None
    identity_valid, identity_diagnostic = _repairable_result_identity_state(
        root,
        envelope,
        logical,
        expected_method_family,
    )
    if not identity_valid or identity_diagnostic not in {
        None,
        _IMPLEMENTATION_DIGEST_MISMATCH,
    }:
        return None
    try:
        files = _regular_files(root)
        actual: set[str] = set()
        identities: set[tuple[int, int]] = set()
        for path in files:
            relative = path.relative_to(root).as_posix()
            metadata = path.stat(follow_symlinks=False)
            identity = (metadata.st_dev, metadata.st_ino)
            if (
                _safe_relative_path(relative) != relative
                or not stat.S_ISREG(metadata.st_mode)
                or identity in identities
            ):
                return None
            actual.add(relative)
            identities.add(identity)
    except (OSError, ValueError):
        return None
    declared = {
        "result.json",
        *(relative for paths in logical.values() for relative in paths),
    }
    undeclared = sorted(actual - declared)
    missing = sorted(declared - actual)
    if (
        not undeclared
        or missing
        or any(_publication_file(root, relative) is None for relative in undeclared)
    ):
        return None
    return (
        "artifact_inventory_mismatch@artifact_dir: "
        f"undeclared={undeclared!r}, missing=[]"
    )


def _repairable_implementation_digest_diagnostic(
    root: Path | None,
    component: _ComponentContract,
    expected_method_family: object,
) -> str | None:
    """Reconstruct a candidate's valid-form digest mismatch over its patch."""
    if (
        root is None
        or component.result_kind != "artifact"
    ):
        return None
    if any(
        validator.get("type") == "unified_diff"
        and validator.get("apply_check", True) is True
        for validator in component.artifact_validators
    ):
        return None
    reconstruction_issues: list[OracleIssue] = []
    envelope = _read_component_envelope(
        root,
        component,
        "repairable-implementation-digest-reconstruction",
        reconstruction_issues,
    )
    if envelope is None or reconstruction_issues:
        return None
    logical, _family, _implementation, _config = _component_artifact_map(
        root,
        envelope,
        component,
        "repairable-implementation-digest-reconstruction",
        reconstruction_issues,
    )
    if (
        logical is None
        or set(logical) != set(component.required_artifacts)
        or reconstruction_issues
    ):
        return None
    identity_valid, identity_diagnostic = _repairable_result_identity_state(
        root,
        envelope,
        logical,
        expected_method_family,
    )
    if not identity_valid or identity_diagnostic != _IMPLEMENTATION_DIGEST_MISMATCH:
        return None
    validator_issues: list[OracleIssue] = []
    if not _audit_component_artifact_validators(
        root,
        logical,
        component,
        "repairable-implementation-digest-reconstruction",
        validator_issues,
    ) or validator_issues:
        return None
    return identity_diagnostic


_UNBOUND_IDENTITY_DIGEST_DIAGNOSTIC = (
    "invalid_identity@result.json.identity: digests must be 64 lowercase hex"
)


def _repairable_unbound_identity_diagnostic(
    root: Path | None,
    component: _ComponentContract,
    expected_method_family: object,
) -> str | None:
    """Reconstruct one unbound artifact candidate's malformed optional digests."""
    if (
        root is None
        or component.result_kind != "artifact"
        or component.dependencies
        or component.required_inputs
        or "implementation_patch" in component.required_artifacts
        or any(
            validator.get("type") == "unified_diff"
            for validator in component.artifact_validators
        )
        or not isinstance(expected_method_family, str)
    ):
        return None
    reconstruction_issues: list[OracleIssue] = []
    envelope = _read_component_envelope(
        root,
        component,
        "repairable-unbound-identity-reconstruction",
        reconstruction_issues,
    )
    if envelope is None or reconstruction_issues:
        return None
    identity = envelope.get("identity")
    if (
        not isinstance(identity, Mapping)
        or not identity
        or not set(identity)
        <= {
            "method_family_id",
            "implementation_sha256",
            "core_config_sha256",
        }
        or identity.get("method_family_id") != expected_method_family
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", expected_method_family
        )
        is None
    ):
        return None
    digests = tuple(
        identity.get(field)
        for field in ("implementation_sha256", "core_config_sha256")
    )
    if not any(
        value is not None
        and (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
        )
        for value in digests
    ):
        return None
    identity_free = dict(envelope)
    identity_free.pop("identity")
    logical, _, _, _ = _component_artifact_map(
        root,
        identity_free,
        component,
        "repairable-unbound-identity-reconstruction",
        reconstruction_issues,
    )
    if (
        logical is None
        or set(logical) != set(component.required_artifacts)
        or reconstruction_issues
        or not _audit_component_artifact_validators(
            root,
            logical,
            component,
            "repairable-unbound-identity-reconstruction",
            reconstruction_issues,
        )
        or reconstruction_issues
    ):
        return None
    return _UNBOUND_IDENTITY_DIGEST_DIAGNOSTIC


def _repairable_collapsed_diff_headers(text: str) -> bool:
    """Accept only newline-collapsed, paired, safe diff headers."""
    old = re.findall(r"(?m)^--- ([^\t\r\n]*?)(?=\+\+\+ )", text)
    new = re.findall(r"\+\+\+ ([^\t\r\n]*?)(?=@@ [^\r\n]+ @@)", text)
    headers = old + new
    return (
        "\x00" not in text
        and bool(old)
        and len(old)
        == len(new)
        == len(re.findall(r"(?m)^--- ", text))
        == len(re.findall(r"\+\+\+ ", text))
        and all(
            header == "/dev/null"
            or header.startswith(("a/", "b/"))
            and _safe_relative_path(header[2:]) is not None
            for header in headers
        )
    )


def _repairable_unified_diff_syntax_diagnostic(
    root: Path | None,
    component: _ComponentContract,
    expected_method_family: object,
    *,
    apply_check_available: bool,
) -> str | None:
    """Reconstruct only a candidate-authored unified-diff shape failure."""
    if (
        root is None
        or component.result_kind != "artifact"
        or component.target_overlay_policy == "latest"
    ):
        return None
    reconstruction_issues: list[OracleIssue] = []
    envelope = _read_component_envelope(
        root,
        component,
        "repairable-unified-diff-reconstruction",
        reconstruction_issues,
    )
    if envelope is None or reconstruction_issues:
        return None
    logical, _family, _implementation, _config = _component_artifact_map(
        root,
        envelope,
        component,
        "repairable-unified-diff-reconstruction",
        reconstruction_issues,
    )
    if (
        logical is None
        or set(logical) != set(component.required_artifacts)
        or reconstruction_issues
        or not _repairable_result_identity_matches(
            root,
            envelope,
            logical,
            expected_method_family,
        )
    ):
        return None

    matched: str | None = None
    for validator in component.artifact_validators:
        artifact_id = str(validator["artifact"])
        paths = logical.get(artifact_id, ())
        if not paths:
            return None
        if validator["type"] == "unified_diff" and (
            len(paths) != 1
            or not isinstance(validator.get("apply_check", True), bool)
            or validator.get("apply_check", True) and not apply_check_available
        ):
            return None
        for relative in paths:
            path = _publication_file(root, relative)
            if path is None:
                return None
            if validator["type"] != "unified_diff":
                try:
                    _validate_component_artifact_bytes(path, validator)
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                    ValueError,
                ):
                    return None
                continue
            try:
                text = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeError):
                return None
            old_paths = re.findall(r"(?m)^--- ([^\t\r\n]+)", text)
            new_paths = re.findall(r"(?m)^\+\+\+ ([^\t\r\n]+)", text)
            malformed = (
                "\x00" in text
                or re.search(r"(?m)^@@ [^\r\n]+ @@", text) is None
                or not old_paths
                or len(old_paths) != len(new_paths)
            )
            if malformed:
                if matched is not None or not _repairable_collapsed_diff_headers(text):
                    return None
                matched = (
                    f"invalid_unified_diff@{path.name}: "
                    "must contain paired headers and a text hunk"
                )
                continue
            try:
                _validate_component_artifact_bytes(path, validator)
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
            ):
                return None
    return matched


def _component_apply_check_available(spec_payload: Mapping[str, Any]) -> bool:
    """Read the target-backed apply-check capability frozen in this spec."""
    contract = spec_payload.get("execution_contract")
    environment = contract.get("environment") if isinstance(contract, Mapping) else None
    matches = [
        item
        for item in environment
        if isinstance(item, list)
        and len(item) == 2
        and item[0] == "HYPOLOOM_TARGET_ROOT"
    ] if isinstance(environment, list) else []
    return len(matches) == 1 and _trimmed_text(matches[0][1])


def _repairable_component_result_code(
    root: Path | None,
    component: _ComponentContract,
    evaluator_version: object,
    expected_method_family: object,
    *,
    apply_check_available: bool = False,
) -> tuple[str, str | None] | None:
    """Reconstruct repairability under the frozen evaluator semantics."""
    if evaluator_version not in {
        "component-result/2",
        "component-result/3",
        "component-result/4",
        "component-result/5",
        "component-result/6",
        "component-result/7",
        "component-result/8", "component-result/9",
    }:
        return None
    envelope_code = _repairable_result_envelope_code(root, component)
    if envelope_code is not None:
        return envelope_code, None
    if evaluator_version in {
        "component-result/3",
            "component-result/4",
            "component-result/5",
            "component-result/6",
            "component-result/7",
            "component-result/8", "component-result/9",
    }:
        diagnostic = _repairable_text_contract_diagnostic(
            root,
            component,
            allow_identity=evaluator_version in {
                "component-result/4",
                "component-result/5",
                "component-result/6",
                "component-result/7",
                "component-result/8", "component-result/9",
            },
            expected_method_family=expected_method_family,
        )
        if diagnostic is not None:
            return "text_contract_failed", diagnostic
    if evaluator_version in {
        "component-result/5",
        "component-result/6",
        "component-result/7",
        "component-result/8", "component-result/9",
    }:
        diagnostic = _repairable_extra_file_inventory_diagnostic(
            root,
            component,
            expected_method_family,
        )
        if diagnostic is not None:
            return "artifact_inventory_mismatch", diagnostic
        diagnostic = _repairable_implementation_digest_diagnostic(
            root,
            component,
            expected_method_family,
        )
        if diagnostic is not None:
            return "implementation_digest_mismatch", diagnostic
    if evaluator_version in {"component-result/6", "component-result/7", "component-result/8", "component-result/9"}:
        diagnostic = _repairable_unbound_identity_diagnostic(
            root,
            component,
            expected_method_family,
        )
        if diagnostic is not None:
            return "invalid_identity", diagnostic
    if evaluator_version in {"component-result/7", "component-result/8", "component-result/9"}:
        diagnostic = _repairable_unified_diff_syntax_diagnostic(
            root,
            component,
            expected_method_family,
            apply_check_available=apply_check_available,
        )
        if diagnostic is not None:
            return "invalid_unified_diff", diagnostic
    return None


def _audit_shared_component_result(component, spec, spec_payload, outcome, payload, attempt, root, candidate,
        commands, attempts, observations, issues, artifact_root):
    from .measurement_oracle import audit_measurement_result
    from .shared_acceptance_oracle import audit_shared_result, grounding_source, phase_success, interpret_shared_acceptance, normalized_declaration
    identity = str(outcome["fact_id"])
    binding = spec_payload["goal_evaluation"]
    actual = payload.get("outcome")
    disqualified = actual in {"INTEGRITY_REJECTED", "CANCELLED"}
    local = shared = None
    local_error = shared_error = None
    observation = {} if attempt is None else observations.get(str(attempt["attempt_id"]), {})
    prestart_rejected = (actual == "INTEGRITY_REJECTED"
        and observation.get("synthetic_before_spawn") is True
        and observation.get("terminal_reason") == "FAILED"
        and observation.get("returncode") is None
        and observation.get("process_group_id") is None
        and isinstance(observation.get("integrity_error"), str) and bool(observation["integrity_error"])
        and isinstance(candidate, Mapping)
        and candidate.get("execution_integrity_error") == observation["integrity_error"])
    _, local_ok, goal_ok = phase_success(observation, local=spec_payload.get("measurement") is not None,
                                        goal=binding["scope"] == "GOAL")
    try:
        if root is None or attempt is None: raise ValueError("common evaluation lacks publication or Process")
        outer = observation["process_request"]
        raw = json.loads(outer["argv"][3])["raw_request"]["argv"]
        prefixes = [raw[index + 1] for index, value in enumerate(raw[:-2]) if value == "--ro-bind" and raw[index + 2] == "/runtime"]
        if len(prefixes) != 1: raise ValueError("common evaluation lacks one admitted runtime")
        runtime = dict(runtime_prefix=prefixes[0], python_executable=str(Path(prefixes[0]) / "bin" / "python"),
                       timeout_seconds=min(30.0, float(spec_payload["execution_contract"]["timeout_seconds"])))
        if local_ok:
            try:
                command = next(row for row in commands if row["command_id"] == spec_payload["execution_contract"]["source_submit_command_id"])
                source = _raw_submit_source(command, attempts, observations)
                files = None if source is None else _decode_workspace(source[1])
                if files is None: raise ValueError("local measurement source is missing")
                local = audit_measurement_result(spec_payload["measurement"], files, root, **runtime)
            except (KeyError, StopIteration, OSError, RuntimeError, TypeError, ValueError) as exc:
                local_error = str(exc)
        if goal_ok:
            try:
                declaration, files, _ = grounding_source(binding["source_attempt_id"], spec["run_id"], attempts, observations, artifact_root)
                if normalized_declaration(binding["acceptance"]) != normalized_declaration(declaration): raise ValueError("shared outcome changed the accepted protocol")
                for name, data in files.items():
                    source = root / "goal" / "parser" / name
                    if not source.is_file() or source.is_symlink() or source.read_bytes() != data:
                        issues.append(OracleIssue("shared_acceptance_source_invalid", "executed public source differs from accepted Grounding bytes", (identity,)))
                from .measurement_oracle import _read_result
                recorded_shared = interpret_shared_acceptance(declaration, _read_result(root / "goal" / "result.json"))
                expected_recorded = {"source_attempt_id": binding["source_attempt_id"], **recorded_shared.observations}
                if not disqualified and payload.get("goal_evaluation") != expected_recorded:
                    issues.append(OracleIssue("shared_acceptance_result_mismatch", "successful shared phase observations were changed or omitted", (identity,)))
                shared = audit_shared_result(declaration, files, root, **runtime)
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                shared_error = str(exc)
    except (KeyError, IndexError, OSError, RuntimeError, TypeError, ValueError) as exc:
        local_error = shared_error = str(exc)
    expected_goal = None if disqualified or shared is None else {"source_attempt_id": binding["source_attempt_id"], **shared.observations}
    if payload.get("goal_evaluation") != expected_goal:
        issues.append(OracleIssue("shared_acceptance_result_mismatch", shared_error or "accepted common observations differ from successful parser replay", (identity,)))
    goal_only_invalid = spec_payload.get("measurement") is None and shared is not None and not shared.validity_met
    metric = None if disqualified or local is None else local.metric_value
    criterion = None if disqualified or goal_only_invalid else ((None if shared is None else shared.goal_met) if spec_payload.get("measurement") is None else (None if local is None else local.criterion_met))
    if payload.get("evaluator_version") != "component-result/9" or spec_payload.get("evaluator_version") != "component-result/9":
        issues.append(OracleIssue("outcome_component_evaluator_mismatch", "common evaluation requires its versioned evaluator", (identity,)))
    candidate_local = dict(candidate) if isinstance(candidate, Mapping) else candidate
    if isinstance(candidate, Mapping) and {"goal_evaluation", "parser_validation_error"} & set(candidate):
        diagnostic = candidate.get("parser_validation_error")
        candidate_goal = candidate.get("goal_evaluation")
        goal_matches = candidate_goal is None and expected_goal is None
        if (isinstance(candidate_goal, Mapping) and set(candidate_goal) == {"source_attempt_id", "measurements", "checks"}
                and candidate_goal["source_attempt_id"] == binding["source_attempt_id"]):
            try:
                interpreted = interpret_shared_acceptance(binding["acceptance"],
                    {name: candidate_goal[name] for name in ("measurements", "checks")})
                goal_matches = {"source_attempt_id": candidate_goal["source_attempt_id"], **interpreted.observations} == expected_goal
            except (KeyError, TypeError, ValueError):
                pass
        failed_phases = [] if prestart_rejected else [name for name, ok, error in (("local", local_ok, local_error), ("goal", goal_ok, shared_error))
            if ok is False or error is not None or name == "goal" and goal_only_invalid]
        if (not {"goal_evaluation", "parser_validation_error"} <= set(candidate)
                or not goal_matches
                or (bool(failed_phases) != (isinstance(diagnostic, str) and bool(diagnostic)))
                or (not failed_phases and diagnostic is not None)
                or (failed_phases and isinstance(diagnostic, str) and any(name not in diagnostic for name in failed_phases))):
            issues.append(OracleIssue("accept_outcome_common_diagnostic_mismatch", "common observations or parser diagnostic differ from independently observed phases", (identity,)))
        candidate_local.pop("goal_evaluation", None)
        candidate_local.pop("parser_validation_error", None)
    if criterion is not None:
        expected = "SUCCESS" if criterion else "CLEAN_REFUTATION"
        if actual != expected or payload.get("metric_value") != metric or payload.get("logical_artifacts") != []:
            issues.append(OracleIssue("shared_local_measurement_mismatch", "accepted local result differs from its independent parser phase", (identity,)))
        expected_candidate = {"outcome": expected, "evaluator_version": "component-result/9", "metric_value": metric,
            "criterion_met": criterion, "validation_error": None, "execution_integrity_error": None}
        if candidate_local != expected_candidate:
            issues.append(OracleIssue("accept_outcome_candidate_mismatch", "common experiment transition changed independently interpreted local result", (identity,)))
    else:
        expected = "EXECUTION_FAILURE" if goal_only_invalid and not disqualified else actual
        if disqualified and (not isinstance(candidate_local, Mapping)
                or candidate_local.get("outcome") != actual
                or candidate_local.get("metric_value") is not None
                or candidate_local.get("criterion_met") is not None):
            issues.append(OracleIssue("accept_outcome_candidate_mismatch", "disqualified candidate carries unsupported scientific result data", (identity,)))
        if goal_only_invalid and not disqualified and (actual != expected or not isinstance(candidate_local, Mapping)
                or candidate_local.get("outcome") != expected or candidate_local.get("criterion_met") is not None
                or not isinstance(candidate_local.get("validation_error"), str) or "validity" not in candidate_local["validation_error"]):
            issues.append(OracleIssue("shared_local_measurement_mismatch", "invalid GOAL-only evidence requires an operational result with a validity diagnostic, not a scientific refutation", (identity,)))
        if actual in _COMPARABLE_OUTCOMES:
            issues.append(OracleIssue("shared_local_measurement_mismatch", local_error or shared_error or "local phase did not complete", (identity,)))
        if payload.get("metric_value") is not None or payload.get("logical_artifacts") != []:
            issues.append(OracleIssue("outcome_metric_value_mismatch", "failed local phase carries unsupported scientific result data", (identity,)))
    return _ComponentResult(expected, criterion, metric, None, None,
        None if disqualified or shared is None else shared.goal_met, None if disqualified or shared is None else shared.validity_met)


def _audit_declared_measurement_result(component: _ComponentContract, spec: Mapping[str, Any],
        spec_payload: Mapping[str, Any], outcome: Mapping[str, Any], payload: Mapping[str, Any],
        attempt: Mapping[str, Any] | None, root: Path | None, candidate: Any,
        commands: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
        observations: Mapping[str, Mapping[str, Any]], issues: list[OracleIssue]) -> _ComponentResult:
    from .measurement_oracle import audit_measurement_result
    fact_id, actual = str(outcome["fact_id"]), str(payload.get("outcome"))
    result = None
    error = None
    try:
        contract = spec_payload["execution_contract"]
        command = next(row for row in commands if row["command_id"] == contract["source_submit_command_id"])
        source = _raw_submit_source(command, attempts, observations)
        workspace = None if source is None else _decode_workspace(source[1])
        if root is None or workspace is None or attempt is None: raise ValueError("measurement source/publication is absent")
        outer = observations[str(attempt["attempt_id"])]["process_request"]
        raw = json.loads(outer["argv"][3])["raw_request"]["argv"]
        prefixes = [raw[index + 1] for index, value in enumerate(raw[:-2]) if value == "--ro-bind" and raw[index + 2] == "/runtime"]
        if len(prefixes) != 1: raise ValueError("measurement runtime is not uniquely bound")
        prefix = prefixes[0]
        result = audit_measurement_result(spec_payload["measurement"], workspace, root,
            runtime_prefix=prefix, python_executable=str(Path(prefix) / "bin" / "python"),
            timeout_seconds=min(30.0, float(contract["timeout_seconds"])))
    except (KeyError, IndexError, StopIteration, OSError, RuntimeError, TypeError, ValueError) as exc:
        error = str(exc)
    if payload.get("evaluator_version") not in {"component-result/8", "component-result/9"} or spec_payload.get("evaluator_version") != payload.get("evaluator_version"):
        issues.append(OracleIssue("outcome_component_evaluator_mismatch", "declared measurements require their frozen evaluator version", (fact_id,)))
    if actual not in _COMPARABLE_OUTCOMES:
        terminal = None if attempt is None else attempt["terminal_reason"]
        causal = (actual == "CANCELLED" and terminal == "CANCELLED"
            or actual == "EXECUTION_FAILURE" and (terminal in {"FAILED", "TIMED_OUT"}
                or terminal == "SUCCEEDED" and result is None and isinstance(candidate, Mapping) and bool(candidate.get("validation_error")))
            or actual in {"INCONCLUSIVE", "INTEGRITY_REJECTED"})
        if not causal: issues.append(OracleIssue("operational_outcome_causation_mismatch", "measurement failure lacks a failed process or independently reproduced parse failure", (fact_id,)))
        if payload.get("metric_value") is not None or payload.get("logical_artifacts") != []:
            issues.append(OracleIssue("outcome_metric_value_mismatch", "operational measurement outcome carries scientific result data", (fact_id,)))
        return _ComponentResult(actual, None, None, None, None)
    if result is None:
        issues.append(OracleIssue("measurement_result_replay_mismatch", error or "parser replay is unavailable", (fact_id,)))
        return _ComponentResult(actual, None, None, None, None)
    expected = "SUCCESS" if result.criterion_met else "CLEAN_REFUTATION"
    if attempt is None or attempt["terminal_reason"] != "SUCCEEDED":
        issues.append(OracleIssue("component_result_attempt_mismatch", "comparable measurement requires one successful Process", (fact_id,)))
    if actual != expected or payload.get("metric_value") != result.metric_value:
        issues.append(OracleIssue("measurement_comparison_mismatch", "accepted measurement differs from independent parser and comparison", (fact_id,)))
    expected_candidate = {"outcome": expected, "evaluator_version": payload.get("evaluator_version"), "metric_value": result.metric_value,
        "criterion_met": result.criterion_met, "validation_error": None, "execution_integrity_error": None}
    if not isinstance(candidate, Mapping) or dict(candidate) != expected_candidate:
        issues.append(OracleIssue("accept_outcome_candidate_mismatch", "measurement transition differs from independently interpreted result", (fact_id,)))
    if payload.get("logical_artifacts") != [] or component.required_artifacts:
        issues.append(OracleIssue("outcome_logical_artifact_mismatch", "measurement publication has no declared logical artifact mapping", (fact_id,)))
    return _ComponentResult(expected, result.criterion_met, result.metric_value, None, None)


def _audit_component_result(
    artifact_root: Path,
    component: _ComponentContract,
    spec: sqlite3.Row,
    spec_payload: Mapping[str, Any],
    outcome: sqlite3.Row,
    payload: Mapping[str, Any],
    artifact: sqlite3.Row,
    attempts_by_id: Mapping[str, sqlite3.Row],
    checkpoint_paths: tuple[str, ...] | None,
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
    *, commands: Sequence[Mapping[str, Any]] = (),
    attempt_payloads: Mapping[str, Mapping[str, Any]] | None = None,
) -> _ComponentResult:
    fact_id = str(outcome["fact_id"])
    actual_outcome = str(payload.get("outcome"))
    attempt = attempts_by_id.get(str(artifact["attempt_id"]))
    accepted = [
        row for row in transitions if _row_get(row, "fact_id") == outcome["fact_id"]
    ]
    candidate = (
        transition_payloads.get(_row_identifier(accepted[0]), {}).get("outcome")
        if len(accepted) == 1
        else None
    )
    if actual_outcome == "CANCELLED" and (
        attempt is None or attempt["terminal_reason"] != "CANCELLED"
    ):
        issues.append(
            OracleIssue(
                "operational_outcome_causation_mismatch",
                "CANCELLED component outcome must bind a cancelled Process Attempt",
                (fact_id,),
            )
        )
    result_root = _artifact_materialized_root(artifact_root, artifact)
    if spec_payload.get("goal_evaluation") is not None:
        return _audit_shared_component_result(component, spec, spec_payload, outcome, payload, attempt,
            result_root, candidate, commands, tuple(attempts_by_id.values()), attempt_payloads or {}, issues, artifact_root)
    if component.deferred_measurement:
        return _audit_declared_measurement_result(component, spec, spec_payload, outcome, payload, attempt,
            result_root, candidate, commands, tuple(attempts_by_id.values()), attempt_payloads or {}, issues)
    evaluator_version = spec_payload.get("evaluator_version")
    repairable_result = _repairable_component_result_code(
        result_root,
        component,
        evaluator_version,
        spec_payload.get("method_family_id"),
        apply_check_available=_component_apply_check_available(spec_payload),
    )
    repairable_code, repairable_diagnostic = (
        repairable_result if repairable_result is not None else (None, None)
    )
    candidate_validation_error = (
        candidate.get("validation_error")
        if isinstance(candidate, Mapping)
        else None
    )
    repairable_evaluation = (
        attempt is not None
        and attempt["terminal_reason"] == "SUCCEEDED"
        and repairable_code is not None
        and isinstance(candidate, Mapping)
        and candidate.get("evaluator_version") == evaluator_version
        and candidate.get("criterion_met") is None
        and candidate.get("metric_value") is None
        and candidate.get("execution_integrity_error") is None
        and isinstance(candidate_validation_error, str)
        and (
            candidate_validation_error == repairable_diagnostic
            if repairable_diagnostic is not None
            else candidate_validation_error.split("@", 1)[0] == repairable_code
        )
    )
    repairable_failure = (
        repairable_evaluation
        and candidate.get("outcome") == "EXECUTION_FAILURE"
        and actual_outcome == "EXECUTION_FAILURE"
    )
    if repairable_evaluation and not repairable_failure:
        issues.append(
            OracleIssue(
                "operational_outcome_causation_mismatch",
                "an independently reproduced repairable component-result failure must be recorded as EXECUTION_FAILURE",
                (fact_id,),
            )
        )
    elif actual_outcome == "EXECUTION_FAILURE" and (
        attempt is None
        or attempt["terminal_reason"] not in {"FAILED", "TIMED_OUT"}
        and not repairable_failure
    ):
        issues.append(
            OracleIssue(
                "operational_outcome_causation_mismatch",
                "EXECUTION_FAILURE must bind a failed/timed-out Process Attempt or an independently reproduced repairable component-result failure",
                (fact_id,),
            )
        )
    if payload.get("evaluator_version") != evaluator_version:
        issues.append(
            OracleIssue(
                "outcome_component_evaluator_mismatch",
                "accepted outcome evaluator differs from preregistration",
                (fact_id,),
            )
        )
    if actual_outcome not in _COMPARABLE_OUTCOMES:
        if payload.get("metric_value") is not None:
            issues.append(
                OracleIssue(
                    "outcome_metric_value_mismatch",
                    "non-comparable component outcome must not carry a metric value",
                    (fact_id,),
                )
            )
        if payload.get("logical_artifacts") != []:
            issues.append(
                OracleIssue(
                    "outcome_logical_artifact_mismatch",
                    "non-comparable component outcome must carry an exact empty logical artifact inventory",
                    (fact_id,),
                )
            )
        return _ComponentResult(actual_outcome, None, None, None, None)

    if attempt is None or attempt["terminal_reason"] != "SUCCEEDED":
        issues.append(
            OracleIssue(
                "component_result_attempt_mismatch",
                "comparable component result must bind a successful Process Attempt",
                (fact_id,),
            )
        )
    root = result_root
    envelope = _read_component_envelope(root, component, fact_id, issues)
    if envelope is None:
        return _ComponentResult(actual_outcome, None, None, None, None)
    logical, family, implementation, config = _component_artifact_map(
        root, envelope, component, fact_id, issues
    )
    artifacts_valid = (
        logical is not None
        and set(logical) == set(component.required_artifacts)
        and _audit_component_artifact_validators(
            root, logical, component, fact_id, issues
        )
    )
    fact_logical = _logical_artifact_paths(payload)
    if logical is None or fact_logical != logical:
        issues.append(
            OracleIssue(
                "outcome_logical_artifact_mismatch",
                "outcome logical artifact inventory differs from result.json and publication bytes",
                (fact_id,),
            )
        )
    if family is not None and family != spec_payload.get("method_family_id"):
        issues.append(
            OracleIssue(
                "outcome_method_family_mismatch",
                "result.json identity differs from the preregistered method family",
                (fact_id,),
            )
        )
    metric_value: str | None = None
    criterion = True
    if component.result_kind == "metric":
        metric_value = _component_metric_from_trials(
            component,
            envelope.get("trials"),
            fact_id,
            issues,
            checkpoint_paths=checkpoint_paths,
        )
        if metric_value is None:
            return _ComponentResult(
                actual_outcome, None, None, implementation, config
            )
        criterion = _metric_accepts(
            str(component.metric_direction),
            str(component.metric_target),
            metric_value,
        )
    if not artifacts_valid:
        return _ComponentResult(actual_outcome, None, None, implementation, config)
    expected_outcome = "SUCCESS" if criterion else "CLEAN_REFUTATION"
    if payload.get("metric_value") != metric_value:
        issues.append(
            OracleIssue(
                "outcome_metric_value_mismatch",
                "accepted metric differs from independently recomputed raw trials",
                (fact_id,),
            )
        )
    if actual_outcome != expected_outcome:
        issues.append(
            OracleIssue(
                "outcome_scientific_verdict_mismatch",
                "accepted outcome differs from the component criterion over raw trials/artifacts",
                (fact_id,),
            )
        )
    expected_candidate = {
        "outcome": expected_outcome,
        "evaluator_version": spec_payload.get("evaluator_version"),
        "metric_value": metric_value,
        "criterion_met": criterion,
        "validation_error": None,
        "execution_integrity_error": None,
    }
    if (
        not isinstance(candidate, Mapping)
        or any(candidate.get(key) != value for key, value in expected_candidate.items())
    ):
        issues.append(
            OracleIssue(
                "accept_outcome_candidate_mismatch",
                "terminal transition candidate differs from independently recomputed component result",
                (fact_id,),
            )
        )
    return _ComponentResult(
        expected_outcome, criterion, metric_value, implementation, config
    )


def _read_component_envelope(
    root: Path | None,
    component: _ComponentContract,
    fact_id: str,
    issues: list[OracleIssue],
) -> Mapping[str, Any] | None:
    if root is None:
        issues.append(
            OracleIssue(
                "unstable_artifact_path",
                "component artifact path differs from its stable publication tuple",
                (fact_id,),
            )
        )
        return None
    result_path = root / "result.json"
    try:
        if result_path.is_symlink() or not result_path.is_file():
            raise ValueError("result.json is absent or not a regular file")
        value = json.loads(
            result_path.read_bytes(),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        issues.append(
            OracleIssue("component_result_json_invalid", str(exc), (fact_id,))
        )
        return None
    required = {"artifacts"} | ({"trials"} if component.result_kind == "metric" else set())
    allowed = required | {"identity"}
    if not isinstance(value, Mapping) or not required <= set(value) or not set(value) <= allowed:
        issues.append(
            OracleIssue(
                "component_result_json_invalid",
                "result.json has fields outside the component result envelope",
                (fact_id,),
            )
        )
        return None
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")


def _component_artifact_map(
    root: Path,
    envelope: Mapping[str, Any],
    component: _ComponentContract,
    fact_id: str,
    issues: list[OracleIssue],
) -> tuple[dict[str, tuple[str, ...]] | None, str | None, str | None, str | None]:
    raw = envelope.get("artifacts")
    if not isinstance(raw, Mapping) or set(raw) != set(component.required_artifacts):
        issues.append(
            OracleIssue(
                "component_artifact_inventory_mismatch",
                "result.json logical artifact IDs differ from GoalDefined",
                (fact_id,),
            )
        )
        raw = {}
    logical: dict[str, tuple[str, ...]] = {}
    seen_paths: set[str] = set()
    try:
        result_stat = (root / "result.json").stat(follow_symlinks=False)
        seen_files = {(result_stat.st_dev, result_stat.st_ino)}
    except OSError:
        seen_files = set()
    for artifact_id in component.required_artifacts:
        declaration = raw.get(artifact_id)
        refs = [declaration] if isinstance(declaration, str) else declaration
        if not isinstance(refs, list) or not refs:
            continue
        paths = tuple(str(item) for item in refs if isinstance(item, str))
        files = tuple(_publication_file(root, path) for path in paths)
        identities: tuple[tuple[int, int], ...] = ()
        if all(path is not None for path in files):
            try:
                identities = tuple(
                    (metadata.st_dev, metadata.st_ino)
                    for path in files
                    if path is not None
                    for metadata in (path.stat(follow_symlinks=False),)
                )
            except OSError:
                identities = ()
        if (
            len(paths) != len(refs)
            or len(paths) < component.artifact_counts.get(artifact_id, 1)
            or len(set(paths)) != len(paths)
            or any(path in seen_paths for path in paths)
            or any(path is None for path in files)
            or len(identities) != len(paths)
            or len(set(identities)) != len(identities)
            or any(identity in seen_files for identity in identities)
        ):
            issues.append(
                OracleIssue(
                    "component_artifact_path_invalid",
                    "logical artifact path is absent, aliased, under-counted, or outside publication",
                    (fact_id, artifact_id),
                )
            )
            continue
        seen_paths.update(paths)
        seen_files.update(identities)
        logical[artifact_id] = paths
    try:
        actual = {
            path.relative_to(root).as_posix() for path in _regular_files(root)
        }
    except (OSError, ValueError) as exc:
        issues.append(OracleIssue("invalid_artifact_tree", str(exc), (fact_id,)))
        actual = set()
    expected_files = {"result.json", *seen_paths}
    if actual != expected_files:
        issues.append(
            OracleIssue(
                "component_artifact_inventory_mismatch",
                "publication contains missing or undeclared result files",
                (fact_id,),
            )
        )
    identity = envelope.get("identity")
    family = implementation = config = None
    if identity is None:
        pass
    elif isinstance(identity, Mapping) and identity and set(identity) <= {
        "method_family_id",
        "implementation_sha256",
        "core_config_sha256",
    }:
        family = identity.get("method_family_id")
        implementation = identity.get("implementation_sha256")
        config = identity.get("core_config_sha256")
        if not _trimmed_text(family):
            family = None
        for value in (implementation, config):
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                issues.append(
                    OracleIssue(
                        "component_result_identity_invalid",
                        "implementation/config identities must be lowercase SHA-256 values",
                        (fact_id,),
                    )
                )
    else:
        issues.append(
            OracleIssue(
                "component_result_identity_invalid",
                "result identity must be absent or a non-empty bounded object",
                (fact_id,),
            )
        )
    return logical, family, implementation, config


def _component_metric_from_trials(
    component: _ComponentContract,
    raw: Any,
    fact_id: str,
    issues: list[OracleIssue],
    *,
    checkpoint_paths: Sequence[str] | None = None,
) -> str | None:
    if not isinstance(raw, list) or not raw:
        issues.append(
            OracleIssue(
                "component_trial_completeness_mismatch",
                "metric result must contain raw trial records",
                (fact_id, component.component_id),
            )
        )
        return None
    trials: list[tuple[Any, str | None, dict[str, Decimal]]] = []
    identities: set[tuple[str, Any, str | None]] = set()
    for index, item in enumerate(raw):
        if (
            not isinstance(item, Mapping)
            or not {"seed", "metrics", "clean"} <= set(item)
            or not set(item) <= {"seed", "arm", "metrics", "clean"}
            or item.get("clean") is not True
            or not isinstance(item.get("metrics"), Mapping)
            or not item["metrics"]
        ):
            issues.append(
                OracleIssue(
                    "component_trial_invalid",
                    "trial must have exact seed/optional-arm/finite-metrics/clean fields",
                    (fact_id, str(index)),
                )
            )
            return None
        seed = item.get("seed")
        arm = item.get("arm")
        if (
            isinstance(seed, bool)
            or not isinstance(seed, (int, str))
            or (isinstance(seed, str) and not _trimmed_text(seed))
            or (arm is not None and not _trimmed_text(arm))
        ):
            issues.append(
                OracleIssue(
                    "component_trial_invalid",
                    "trial seed or arm identity is invalid",
                    (fact_id, str(index)),
                )
            )
            return None
        metrics: dict[str, Decimal] = {}
        try:
            for name, value in item["metrics"].items():
                if not _trimmed_text(name):
                    raise ValueError("metric name is invalid")
                metrics[str(name)] = Decimal(
                    _normalize_decimal_text(value, f"trial[{index}].{name}")
                )
        except (InvalidOperation, ValueError) as exc:
            issues.append(OracleIssue("component_trial_invalid", str(exc), (fact_id,)))
            return None
        identity = (type(seed).__name__, seed, str(arm) if arm is not None else None)
        if identity in identities:
            issues.append(
                OracleIssue(
                    "component_trial_completeness_mismatch",
                    "trial seed/arm identity is duplicated",
                    (fact_id, str(seed), str(arm)),
                )
            )
            return None
        identities.add(identity)
        trials.append((seed, str(arm) if arm is not None else None, metrics))

    metric = str(component.metric_name)
    paired = bool(component.arms) or metric.casefold().startswith("paired_") or (
        "paired_delta" in component.required_metrics
    )
    maximum = metric.casefold().startswith("max_")
    common = set(trials[0][2])
    for _, _, metrics in trials[1:]:
        common &= set(metrics)
    aliases = [metric]
    folded = metric.casefold()
    if paired and folded.startswith("paired_") and "_gain_vs_" in folded:
        aliases.append(metric[len("paired_") : folded.index("_gain_vs_")])
    field = next((name for name in aliases if name in common), None)
    if field is None:
        issues.append(
            OracleIssue(
                "component_trial_metric_missing",
                "raw trials do not expose one unambiguous decision metric",
                (fact_id, metric),
            )
        )
        return None

    derived_metrics = {metric}
    if paired:
        derived_metrics.add("paired_delta")
    for required_metric in component.required_metrics:
        if required_metric == "n_successful_seeds":
            successful = Decimal(len(trials))
            if required_metric not in common or any(
                metrics[required_metric] != successful
                for _, _, metrics in trials
            ):
                issues.append(
                    OracleIssue(
                        "component_required_metric_mismatch",
                        "n_successful_seeds must be present on every clean trial and equal the independently counted trial total",
                        (fact_id, component.component_id, required_metric),
                    )
                )
                return None
        elif required_metric not in derived_metrics and required_metric not in common:
            issues.append(
                OracleIssue(
                    "component_required_metric_mismatch",
                    "a protocol-required metric is absent from the raw trials",
                    (fact_id, component.component_id, required_metric),
                )
            )
            return None

    checkpoint_counts = tuple(
        requirement.min_count
        for requirement in component.required_inputs
        if requirement.logical_artifact == "checkpoints"
    )
    if maximum and checkpoint_counts:
        expected_count = len(checkpoint_paths) if checkpoint_paths is not None else 0
        seeds_in_order = tuple(seed for seed, _, _ in trials)
        positional_ids = tuple(range(expected_count))
        if (
            checkpoint_paths is None
            or expected_count < sum(checkpoint_counts)
            or seeds_in_order not in (positional_ids, tuple(checkpoint_paths))
        ):
            issues.append(
                OracleIssue(
                    "component_trial_completeness_mismatch",
                    "causal maximum trials must exactly identify each required checkpoint in publication order",
                    (fact_id, component.component_id),
                )
            )
            return None

    values: list[Decimal]
    if paired:
        if component.seeds != tuple(range(3)) or component.arms != (
            "control",
            "treatment",
        ):
            issues.append(
                OracleIssue(
                    "component_trial_plan_mismatch",
                    "paired component must declare control/treatment seeds 0,1,2",
                    (fact_id, component.component_id),
                )
            )
            return None
        by_identity = {
            (type(seed).__name__, seed, arm): metrics
            for seed, arm, metrics in trials
        }
        expected = {
            (type(seed).__name__, seed, arm)
            for seed in component.seeds
            for arm in component.arms
        }
        if set(by_identity) != expected or len(trials) != len(expected):
            issues.append(
                OracleIssue(
                    "component_trial_completeness_mismatch",
                    "paired component requires exact control/treatment trials for seeds 0,1,2",
                    (fact_id, component.component_id),
                )
            )
            return None
        values = [
            by_identity[(type(seed).__name__, seed, "treatment")][field]
            - by_identity[(type(seed).__name__, seed, "control")][field]
            for seed in component.seeds
        ]
    else:
        if any(arm is not None for _, arm, _ in trials):
            issues.append(
                OracleIssue(
                    "component_trial_completeness_mismatch",
                    "unpaired component trials must omit arm",
                    (fact_id, component.component_id),
                )
            )
            return None
        if component.seeds:
            expected = {(type(seed).__name__, seed) for seed in component.seeds}
            actual = {(type(seed).__name__, seed) for seed, _, _ in trials}
            if actual != expected or len(trials) != len(expected):
                issues.append(
                    OracleIssue(
                        "component_trial_completeness_mismatch",
                        "trial seeds differ from the exact preregistered plan",
                        (fact_id, component.component_id),
                    )
                )
                return None
            if not maximum and component.seeds != tuple(range(5)):
                issues.append(
                    OracleIssue(
                        "component_trial_plan_mismatch",
                        "final mean component must declare seeds 0 through 4",
                        (fact_id, component.component_id),
                    )
                )
                return None
            ordered = {
                (type(seed).__name__, seed): metrics for seed, _, metrics in trials
            }
            values = [
                ordered[(type(seed).__name__, seed)][field]
                for seed in component.seeds
            ]
        else:
            if not maximum and len(trials) != 1:
                issues.append(
                    OracleIssue(
                        "component_trial_completeness_mismatch",
                        "singleton component must contain exactly one raw trial",
                        (fact_id, component.component_id),
                    )
                )
                return None
            values = [metrics[field] for _, _, metrics in trials]
    aggregate = max(values) if maximum else sum(values, Decimal()) / Decimal(len(values))
    return _normalize_decimal_text(aggregate, "component aggregate")


def _expected_component_assessment(result: _ComponentResult | None) -> str:
    if result is None:
        return "INCONCLUSIVE"
    if result.outcome == "SUCCESS" and result.criterion_met is True:
        return "SUPPORTED"
    if result.outcome == "CLEAN_REFUTATION" and result.criterion_met is False:
        return "REFUTED"
    return "INCONCLUSIVE"


def _audit_component_dependency_closure(
    contract: _GoalComponentContract,
    specs_by_component: Mapping[str, sqlite3.Row],
    assessments_by_component: Mapping[str, sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
    *,
    allow_ordinary_family_diversity: bool = True,
) -> None:
    by_id = contract.by_id
    for component_id, spec in specs_by_component.items():
        component = by_id.get(component_id)
        if component is None:
            continue
        input_producers = {item.producer_component for item in component.required_inputs}
        for dependency_id in component.dependencies:
            if dependency_id in input_producers:
                continue
            assessment = assessments_by_component.get(dependency_id)
            assessment_payload = (
                payloads.get(str(assessment["fact_id"]), {})
                if assessment is not None
                else {}
            )
            if (
                assessment is None
                or assessment_payload.get("assessment") != "SUPPORTED"
                or (
                    not allow_ordinary_family_diversity
                    and assessment_payload.get("method_family_id")
                    != payloads.get(str(spec["fact_id"]), {}).get("method_family_id")
                )
                or int(assessment["raw_order"]) >= int(spec["raw_order"])
            ):
                issues.append(
                    OracleIssue(
                        "component_dependency_closure_mismatch",
                        "experiment preregistered before an explicit dependency had an earlier "
                        + (
                            "supported assessment"
                            if allow_ordinary_family_diversity
                            else "supported same-family assessment"
                        ),
                        (str(spec["fact_id"]), component_id, dependency_id),
                    )
                )


def _canonical_json_input_matches(
    request: AgentRequest, ref: str, value: Mapping[str, Any],
) -> bool:
    expected = json.dumps(
        value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    inputs = [item for item in request.input_artifacts if item.ref == ref]
    return (
        ref in request.immutable_context_refs
        and len(inputs) == 1
        and inputs[0].media_type == "application/json"
        and inputs[0].content_bytes() == expected
    )


def _embedded_input_matches(
    request: AgentRequest,
    ref: str,
    media_type: str,
    expected: bytes,
) -> bool:
    inputs = [item for item in request.input_artifacts if item.ref == ref]
    return (
        ref in request.immutable_context_refs
        and len(inputs) == 1
        and inputs[0].media_type == media_type
        and inputs[0].content_bytes() == expected
    )


def _embedded_input_absent(request: AgentRequest, ref: str) -> bool:
    return ref not in request.immutable_context_refs and all(
        item.ref != ref for item in request.input_artifacts
    )


def _bounded_regular_file_tail(path: Path) -> tuple[bool, bytes | None]:
    """Read one optional regular file without loading more than the projected tail."""
    if not path.exists() and not path.is_symlink():
        return True, None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return False, None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            return False, None
        size = min(metadata.st_size, 65_536)
        offset = metadata.st_size - size
        result = bytearray()
        while len(result) < size:
            chunk = os.pread(fd, size - len(result), offset + len(result))
            if not chunk:
                break
            result.extend(chunk)
        return len(result) == size, bytes(result)
    except OSError:
        return False, None
    finally:
        os.close(fd)


def _agent_response_input_matches(
    request: AgentRequest, ref: str, response: AgentResponse,
) -> bool:
    return _canonical_json_input_matches(request, ref, response.to_dict())


def _fact_input_matches(
    request: AgentRequest,
    fact: sqlite3.Row,
    payloads: Mapping[str, Mapping[str, Any]],
) -> bool:
    fact_id = str(fact["fact_id"])
    return _canonical_json_input_matches(
        request,
        fact_id,
        {
            "fact_id": fact_id,
            "fact_type": str(fact["fact_type"]),
            "payload": payloads.get(fact_id, {}),
        },
    )


def _stored_fact_input_matches(
    request: AgentRequest,
    ref: str,
    fact: sqlite3.Row,
    payloads: Mapping[str, Mapping[str, Any]],
    refs: Sequence[sqlite3.Row],
) -> bool:
    """Match the exact StateStore fact projection used by current-node inputs."""
    fact_id = str(fact["fact_id"])
    value = {key: fact[key] for key in fact.keys() if key != "raw_order"}
    value["payload"] = payloads.get(fact_id, {})
    value["refs"] = [
        {
            "ref_fact_id": row["ref_fact_id"],
            "role": row["role"],
            "ordinal": row["ordinal"],
        }
        for row in refs
        if str(row["fact_id"]) == fact_id
    ]
    return _canonical_json_input_matches(request, ref, value)


def _current_evaluation_input_matches(
    request: AgentRequest,
    evaluation: Mapping[str, Any],
) -> bool:
    return _canonical_json_input_matches(
        request,
        f"{request.command_id}--current-evaluation",
        evaluation,
    )


def _result_analysis_execution_inputs_match(
    request: AgentRequest,
    spec: sqlite3.Row,
    outcome: sqlite3.Row,
    payloads: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[sqlite3.Row],
    artifact_root: Path,
    *, commands: Sequence[sqlite3.Row] = (),
) -> bool:
    """Rebuild current execution inputs from raw attempt and publication state."""
    terminal_rows = [
        row
        for row in transitions
        if row["workflow_id"] == outcome["experiment_id"]
        and row["fact_id"] == outcome["fact_id"]
    ]
    if len(terminal_rows) != 1:
        return False
    terminal = transition_payloads.get(_row_identifier(terminal_rows[0]), {})
    evaluation = terminal.get("outcome")
    process_attempt_id = terminal.get("attempt_id")
    outcome_payload = payloads.get(str(outcome["fact_id"]), {})
    if (
        not isinstance(evaluation, Mapping)
        or not isinstance(process_attempt_id, str)
        or evaluation.get("outcome") != outcome_payload.get("outcome")
    ):
        return False
    process_rows = [
        row for row in attempts if str(row["attempt_id"]) == process_attempt_id
    ]
    if len(process_rows) != 1:
        return False
    process = process_rows[0]
    if (
        process["run_id"] != request.run_id
        or process["kind"] != "PROCESS"
        or process["state"] != "CLOSED"
        or process["workflow_id"] != outcome["experiment_id"]
        or process["experiment_id"] != outcome["experiment_id"]
        or process["execution_id"] != outcome["execution_id"]
    ):
        return False
    observation = attempt_payloads.get(process_attempt_id)
    if not isinstance(observation, Mapping):
        return False

    spec_payload = payloads.get(str(spec["fact_id"]), {})
    execution_contract = spec_payload.get("execution_contract")
    source_attempt_id = (
        execution_contract.get("source_agent_attempt_id")
        if isinstance(execution_contract, Mapping)
        else None
    )
    source_rows = [
        row for row in attempts if str(row["attempt_id"]) == source_attempt_id
    ]
    if len(source_rows) != 1:
        return False
    source_attempt = source_rows[0]
    source_observation = attempt_payloads.get(str(source_attempt_id))
    response_value = (
        source_observation.get("response")
        if isinstance(source_observation, Mapping)
        else None
    )
    submit_id = execution_contract.get("source_submit_command_id") if isinstance(execution_contract, Mapping) else None
    submitted = None
    if submit_id is not None:
        command = next((row for row in commands if row["command_id"] == submit_id), None)
        submitted = None if command is None else _raw_submit_source(command, attempts, attempt_payloads)
        if submitted is None or command["workflow_id"] != spec["experiment_id"]:
            return False
        response_value = submitted[1]
    if (
        source_attempt["run_id"] != request.run_id
        or source_attempt["kind"] != "AGENT"
        or submit_id is None and source_attempt["state"] != "CLOSED"
        or submit_id is None and source_attempt["terminal_reason"] != "SUCCEEDED"
        or not isinstance(response_value, Mapping)
    ):
        return False
    try:
        if submitted is not None:
            workspace = _decode_workspace(submitted[1])
            if workspace is None: return False
            source_documents = _submitted_source_documents(submitted[1], request.command_id + "--current-")
        else:
            source_response = AgentResponse.from_dict(response_value)
            source_bytes = next(item.content_bytes() for item in source_response.proposed_workspace_artifacts if item.path == "experiment.py")
            source_documents = {request.command_id + "--current-experiment.py": ("text/x-python", source_bytes[-65_536:])}
    except (AgentACIError, KeyError, StopIteration, TypeError, ValueError):
        return False

    publications = [
        row for row in artifacts if str(row["attempt_id"]) == process_attempt_id
    ]
    if len(publications) != 1:
        return False
    publication = publications[0]
    if (
        publication["artifact_id"] != outcome["artifact_id"]
        or publication["run_id"] != request.run_id
        or publication["experiment_id"] != outcome["experiment_id"]
        or publication["execution_id"] != outcome["execution_id"]
        or publication["artifact_kind"] != "result"
    ):
        return False
    publication_root = _artifact_materialized_root(artifact_root, publication)
    if publication_root is None or _safe_path_component(process_attempt_id) is None:
        return False

    prefix = request.command_id
    if (
        not _current_evaluation_input_matches(request, evaluation)
        or not _canonical_json_input_matches(
            request, f"{prefix}--current-process", observation,
        )
        or not all(_embedded_input_matches(request, ref, media, content) for ref, (media, content) in source_documents.items())
    ):
        return False
    result_ok, result_bytes = _bounded_regular_file_tail(
        publication_root / "result.json"
    )
    result_ref = f"{prefix}--current-result.json"
    if not result_ok or (
        result_bytes is None
        and not _embedded_input_absent(request, result_ref)
    ) or (
        result_bytes is not None
        and not _embedded_input_matches(
            request, result_ref, "application/json", result_bytes,
        )
    ):
        return False
    log_root = artifact_root / "result-staging" / process_attempt_id / "logs"
    for name in ("stdout", "stderr"):
        log_ok, log_bytes = _bounded_regular_file_tail(log_root / f"{name}.log")
        if not log_ok or not _embedded_input_matches(
            request,
            f"{prefix}--current-{name}",
            "text/plain",
            b"" if log_bytes is None else log_bytes,
        ):
            return False
    return True


def _result_analysis_inputs_match(
    request: AgentRequest,
    spec: sqlite3.Row,
    outcome: sqlite3.Row,
    payloads: Mapping[str, Mapping[str, Any]],
    refs: Sequence[sqlite3.Row],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    evaluator_version: str | None,
    *,
    attempts: Sequence[sqlite3.Row] = (),
    attempt_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    artifacts: Sequence[sqlite3.Row] = (),
    artifact_root: Path | None = None,
    commands: Sequence[sqlite3.Row] = (),
) -> bool:
    """Interpret old fact refs and current exact projections by frozen version."""
    if evaluator_version not in {"component-result/7", "component-result/8", "component-result/9"}:
        return _fact_input_matches(request, outcome, payloads)
    return artifact_root is not None and (
        _stored_fact_input_matches(
            request, f"{request.command_id}--current-spec", spec, payloads, refs,
        )
        and _stored_fact_input_matches(
            request, f"{request.command_id}--current-outcome", outcome, payloads, refs,
        )
        and _result_analysis_execution_inputs_match(
            request,
            spec,
            outcome,
            payloads,
            transitions,
            transition_payloads,
            attempts,
            {} if attempt_payloads is None else attempt_payloads,
            artifacts,
            artifact_root,
            commands=commands,
        )
    )


def _audit_expert_dependency_authorization(
    contract: _GoalComponentContract,
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    terminal_updates: Mapping[str, tuple[str, AgentResponse]],
    issues: list[OracleIssue],
) -> None:
    expert_requests: dict[str, list[AgentRequest]] = {}
    by_id = contract.by_id
    for row in attempts:
        attempt_id = str(row["attempt_id"])
        observation = attempt_payloads.get(attempt_id)
        request_value = (
            observation.get("request")
            if isinstance(observation, Mapping) else None
        )
        if not isinstance(request_value, Mapping):
            continue
        try:
            request = AgentRequest.from_dict(request_value)
        except (AgentACIError, TypeError, ValueError):
            continue
        key = _research_task_key(request)
        if isinstance(key, Mapping):
            expert_requests.setdefault(request.component_id, []).append(request)
    for component_id, requests in expert_requests.items():
        component = by_id.get(component_id)
        if component is None:
            continue
        for request in requests:
            for dependency_id in component.dependencies:
                expected = terminal_updates.get(dependency_id)
                if expected is None or not _agent_response_input_matches(
                    request, expected[0], expected[1],
                ):
                    issues.append(
                        OracleIssue(
                            "expert_dependency_authorization_mismatch",
                            "dependent expert work lacks the unique terminal update response of an upstream lineage",
                            (
                                request.attempt_id,
                                component_id,
                                dependency_id,
                            ),
                        )
                    )


def _selected_experiment_families(
    contract: _GoalComponentContract,
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    family_components: set[str] | None = None,
) -> set[str]:
    required = (
        _verdict_family_component_ids(contract)
        if family_components is None
        else family_components
    )
    hypotheses = {
        str(row["hypothesis_id"]): payloads.get(str(row["fact_id"]), {})
        for row in facts
        if row["fact_type"] == "HypothesisProposed"
    }
    tips: dict[str, tuple[int, str, Mapping[str, Any]]] = {}
    for transition in transitions:
        if transition["workflow_kind"] != "EXPERIMENT" or transition["revision"] != 1:
            continue
        creation = transition_payloads.get(_row_identifier(transition), {})
        component_id = creation.get("component_id")
        ordinal = creation.get("experiment_ordinal", 1)
        if (
            component_id not in required
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 1
        ):
            continue
        candidate = (ordinal, str(transition["workflow_id"]), creation)
        prior = tips.get(str(component_id))
        if prior is None or candidate[:2] > prior[:2]:
            tips[str(component_id)] = candidate
    families: set[str] = set()
    for component_id, (_, _, creation) in tips.items():
        hypothesis = hypotheses.get(str(creation.get("hypothesis_id")))
        family = None if hypothesis is None else hypothesis.get("method_family_id")
        if (
            hypothesis is not None
            and hypothesis.get("component_id") == component_id
            and _trimmed_text(family)
        ):
            families.add(str(family))
    return families


def _verdict_family_component_ids(
    contract: _GoalComponentContract,
) -> set[str]:
    """Independently derive which required components share selected identity."""
    required = set(contract.required_ids)
    input_producers = {
        item.producer_component
        for component in contract.components
        if component.component_id in required
        for item in component.required_inputs
        if item.producer_component in required
    }
    if not input_producers:
        return required
    input_consumers = {
        component.component_id
        for component in contract.components
        if component.component_id in required and component.required_inputs
    }
    return input_producers | input_consumers


def _valid_expert_terminal_updates(
    contract: _GoalComponentContract,
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    refs: Sequence[sqlite3.Row], evaluator_version: str | None,
    assessments_by_component: Mapping[str, sqlite3.Row],
    transitions: Sequence[sqlite3.Row],
    transition_payloads: Mapping[str, Mapping[str, Any]],
    attempts: Sequence[sqlite3.Row],
    attempt_payloads: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[sqlite3.Row],
    artifact_root: Path,
    *, commands: Sequence[sqlite3.Row] = (),
) -> dict[str, tuple[str, AgentResponse]]:
    hypotheses = {
        str(row["hypothesis_id"]): row
        for row in facts if row["fact_type"] == "HypothesisProposed"
    }
    exchanges = {
        str(row["attempt_id"]): exchange
        for row in attempts
        if (exchange := _validated_agent_attempt(
            row, attempt_payloads.get(str(row["attempt_id"])),
        )) is not None
    }
    if any(request.schema_version >= 14 for request, _ in exchanges.values()):
        result = {}
        accepted = {}
        for transition in transitions:
            payload = transition_payloads.get(_row_identifier(transition), {})
            if isinstance(payload.get("adjudication"), Mapping): accepted[payload.get("component_id")] = payload
        for component_id, payload in accepted.items():
            adjudication = payload["adjudication"]; source_id = payload.get("agent_attempt_id")
            source = exchanges.get(source_id); assessment = assessments_by_component.get(component_id)
            tip = adjudication.get("selected_tip")
            if source is None or assessment is None or not isinstance(tip, Mapping) or tip.get("node_id") != assessment["hypothesis_id"]: continue
            cycle = adjudication["cycle"]
            # Control cleanup is not scientific completion. Every accepted
            # comparable tip still needs its actual successful analysis; an
            # exhausted successor proposal cannot borrow its parent's closure.
            complete_analyses = True
            for verdict in source[1].typed_candidate["verdicts"]:
                if verdict["status"] not in {"verified", "refuted"}: continue
                matches = []
                for analysis_request, analysis_response in exchanges.values():
                    analysis_key = _research_task_key(analysis_request)
                    if (analysis_request.command.value == "RESULT_ANALYSIS" and analysis_request.component_id == component_id
                            and analysis_request.role == source[0].role and isinstance(analysis_key, Mapping)
                            and analysis_key.get("cycle") == cycle and analysis_key.get("chain") == verdict["chain_id"]):
                        for item in analysis_request.input_artifacts:
                            if item.ref.endswith("--current-outcome"):
                                value = json.loads(item.content_bytes()).get("payload", {})
                                if value.get("outcome_id") in verdict["evidence"]: matches.append(analysis_response)
                if not matches: complete_analyses = False
            if not complete_analyses: continue
            interrupted = False
            for transition in transitions:
                failure = transition_payloads.get(_row_identifier(transition), {})
                if failure.get("agent_final_failure") is not True: continue
                raw = attempt_payloads.get(str(failure.get("attempt_id")), {}).get("request", {})
                key = (raw.get("task_context") or {}).get("key", {})
                if (raw.get("component_id"), raw.get("role")) != (component_id, source[0].role): continue
                if (key.get("cycle") == cycle and raw.get("command") in {"RESULT_ANALYSIS", "HYPOTHESIS_UPDATE", "CYCLE_REPORT"}
                        or key.get("cycle") == cycle + 1 and raw.get("command") in {"LITERATURE_REVIEW", "HYPOTHESIS_PROPOSAL"}): interrupted = True
            if interrupted: continue
            for attempt_id, (request, response) in exchanges.items():
                key = _research_task_key(request)
                if (request.command.value == "HYPOTHESIS_UPDATE" and request.component_id == component_id
                        and isinstance(key, Mapping) and key.get("cycle") == adjudication["cycle"]
                        and _fact_input_matches(request, assessment, payloads)
                        and _agent_response_input_matches(request, f"{source_id}--response", source[1])):
                    result[component_id] = (f"{attempt_id}--response", response)
        return result
    tips: dict[str, tuple[int, str, Mapping[str, Any]]] = {}
    for transition in transitions:
        creation = transition_payloads.get(_row_identifier(transition), {})
        component_id = creation.get("component_id")
        ordinal = creation.get("experiment_ordinal", 1)
        if (
            transition["workflow_kind"] != "EXPERIMENT"
            or transition["revision"] != 1
            or component_id not in contract.by_id
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 1
        ):
            continue
        candidate = (ordinal, str(transition["workflow_id"]), creation)
        prior = tips.get(str(component_id))
        if prior is None or candidate[:2] > prior[:2]:
            tips[str(component_id)] = candidate
    result: dict[str, tuple[str, AgentResponse]] = {}
    for component_id, component in contract.by_id.items():
        assessment = assessments_by_component.get(component_id)
        hypothesis = (
            None if assessment is None
            else hypotheses.get(str(assessment["hypothesis_id"]))
        )
        cycle = (
            None if hypothesis is None
            else _expert_hypothesis_cycle(hypothesis, hypotheses, payloads)
        )
        tip = tips.get(component_id)
        if (
            assessment is None or hypothesis is None or cycle is None or tip is None
            or tip[2].get("hypothesis_id") != assessment["hypothesis_id"]
            or tip[2].get("role") != component.owner_role
            or tip[2].get("expert_cycle", 1) != cycle
        ):
            continue
        current_outcomes = [
            row for row in facts
            if row["fact_type"] == "CanonicalOutcomeAccepted"
            and str(row["experiment_id"]) == tip[1]
        ]
        if len(current_outcomes) != 1:
            continue
        current_specs = [
            row for row in facts
            if row["fact_type"] == "ExperimentPreregistered"
            and str(row["experiment_id"]) == tip[1]
        ]
        if evaluator_version in {"component-result/7", "component-result/8", "component-result/9"} and len(current_specs) != 1:
            continue
        analyses = [
            (attempt_id, request, response)
            for attempt_id, (request, response) in exchanges.items()
            if request.command.value == "RESULT_ANALYSIS"
            and request.component_id == component_id
            and request.role == component.owner_role
            and isinstance(key := _research_task_key(request), Mapping)
            and key.get("node_kind") == "RESULT_ANALYSIS"
            and key.get("subkind") == "MAIN"
            and key.get("cycle") == cycle
            and key.get("experiment_ordinal") == tip[0]
            and _result_analysis_inputs_match(
                request,
                current_specs[0] if current_specs else current_outcomes[0],
                current_outcomes[0],
                payloads,
                refs,
                transitions,
                transition_payloads,
                evaluator_version,
                attempts=attempts,
                attempt_payloads=attempt_payloads,
                artifacts=artifacts,
                artifact_root=artifact_root,
                commands=commands,
            )
        ]
        if len(analyses) != 1:
            continue
        updates = []
        for attempt_id, (request, response) in exchanges.items():
            key = _research_task_key(request)
            candidate = response.typed_candidate
            if (
                request.command.value == "HYPOTHESIS_UPDATE"
                and request.component_id == component_id
                and request.role == component.owner_role
                and isinstance(key, Mapping)
                and key.get("node_kind") == "HYPOTHESIS_UPDATE"
                and key.get("subkind") == "MAIN"
                and key.get("cycle") == cycle
                and isinstance(candidate, Mapping)
                and candidate.get("update_disposition") == "TERMINATE_LINEAGE"
                and _fact_input_matches(request, assessment, payloads)
                and _agent_response_input_matches(
                    request, f"{analyses[0][0]}--response", analyses[0][2],
                )
            ):
                updates.append((f"{attempt_id}--response", response))
        if len(updates) == 1:
            result[component_id] = updates[0]
    return result


def _audit_component_verdicts(
    contract: _GoalComponentContract,
    facts: Sequence[sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    assessments_by_id: Mapping[str, sqlite3.Row],
    assessments_by_component: Mapping[str, sqlite3.Row],
    assessment_results: Mapping[str, _ComponentResult | None],
    issues: list[OracleIssue],
    *,
    transitions: Sequence[sqlite3.Row] = (),
    transition_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    selected_family_dataflow: bool = True,
    expert_lineages_terminated: bool = True,
    completion_mode: str = "required_conjunction",
    terminated_component_ids: frozenset[str] = frozenset(),
    selected_specs: Mapping[str, Mapping[str, Any]] | None = None,
    workflow_version: str | None = None,
) -> None:
    required = set(contract.required_ids)
    goal_document = payloads.get(contract.fact_id, {}).get("research_contract", {})
    grounded = goal_document.get("schema_version") == 3 and goal_document.get("goal", {}).get("evaluation") == {"mode": "grounding"}
    primary = {
        item.component_id for item in contract.components if item.tier == "primary"
    }
    must = {item.component_id for item in contract.components if item.tier == "must"}
    family_components = (
        _verdict_family_component_ids(contract)
        if selected_family_dataflow
        else required
    )
    selected_families = _selected_experiment_families(
        contract,
        facts,
        payloads,
        transitions,
        transition_payloads or {},
        family_components,
    )
    if selected_specs is not None:
        # Batch verdicts use the accepted adjudication, including no selection,
        # never a later-created experiment or a superseded batch's family.
        selected_families = {str(payloads[str(row["fact_id"])]["method_family_id"])
            for component_id, row in selected_specs.items() if component_id in family_components}
    verdicts = [row for row in facts if row["fact_type"] == "GoalVerdictRecorded"]
    for verdict in verdicts:
        fact_id = str(verdict["fact_id"])
        payload = payloads.get(fact_id, {})
        raw_ids = payload.get("assessment_ids")
        listed_ids = (
            tuple(str(item) for item in raw_ids) if isinstance(raw_ids, list) else ()
        )
        expected_ids = {
            str(row["subject_id"])
            for component_id, row in assessments_by_component.items()
            if component_id in required
        }
        if completion_mode == "shared_goal":
            from .batch_oracle import shared_goal_stop, supports_successful_stop
            if supports_successful_stop(workflow_version) and payload.get("verdict") == "GOAL_MET":
                try:
                    stop = shared_goal_stop(facts, payloads, transitions, workflow_version)
                    if stop is None or stop[1]["fact_id"] != fact_id:
                        raise ValueError("shared success did not atomically begin its successful stop")
                    ordered = sorted(enumerate(transitions), key=lambda item: dict(item[1]).get("raw_order", item[0]))
                    positions = {str(dict(row)["fact_id"]): index for index, (_, row) in enumerate(ordered) if dict(row).get("fact_id") is not None}
                    candidates = []
                    for identity, row in assessments_by_id.items():
                        value = payloads.get(str(row["fact_id"]), {})
                        result = assessment_results.get(identity)
                        if (value.get("component_id") in required and result is not None
                                and (result.goal_met is True if grounded else value.get("assessment") == "SUPPORTED"
                                     and result.outcome == "SUCCESS" and result.criterion_met is True)
                                and positions.get(str(row["fact_id"]), positions[fact_id]) < positions[fact_id]):
                            candidates.append((str(value["hypothesis_id"]), str(value["component_id"]), identity, value))
                    if not candidates: raise ValueError("shared success lacks independently recomputed prior qualified evidence")
                    _, _, winner, value = min(candidates, key=lambda item: item[:3])
                    if listed_ids != (winner,) or payload.get("method_family_id") != value.get("method_family_id"):
                        raise ValueError("shared success changed its stable qualified winner or scientific family")
                except (KeyError, TypeError, ValueError) as exc:
                    issues.append(OracleIssue("verdict_shared_goal_mismatch", str(exc), (fact_id,)))
                continue
            values = {key: payloads.get(str(row["fact_id"]), {}) for key, row in assessments_by_component.items() if key in required}
            comparable = {key for key in values if (result := _assessment_result_for_component(key, assessments_by_component, assessment_results)) is not None
                          and (result.goal_met is not None and result.goal_valid is True if grounded else result.outcome in _COMPARABLE_OUTCOMES)}
            winners = [key for key, value in values.items() if key in comparable and key in terminated_component_ids
                       and (_assessment_result_for_component(key, assessments_by_component, assessment_results).goal_met is True if grounded else value.get("assessment") == "SUPPORTED")]
            if winners:
                winner = min(winners, key=lambda key: (str(values[key].get("hypothesis_id", "")), key))
                expected_verdict = "GOAL_MET"; expected_family = values[winner].get("method_family_id")
                expected_ids = {str(assessments_by_component[winner]["subject_id"])}
            else:
                failed = all(_assessment_result_for_component(key, assessments_by_component, assessment_results).goal_met is False
                    for key in values if key in comparable) if grounded else all(value.get("assessment") == "REFUTED" for value in values.values())
                expected_verdict = "GOAL_NOT_MET" if required and set(values) == required and required <= comparable & terminated_component_ids and failed else "INCONCLUSIVE"
                families = {value.get("method_family_id") for value in values.values()}
                expected_family = next(iter(families)) if len(families) == 1 else None
            if len(listed_ids) != len(set(listed_ids)) or set(listed_ids) != expected_ids or any(item not in assessments_by_id for item in listed_ids):
                issues.append(OracleIssue("verdict_required_component_coverage_mismatch", "shared goal does not cite exactly its selected winner or available alternative assessments", (fact_id,)))
            if payload.get("verdict") != expected_verdict or payload.get("method_family_id") != expected_family:
                issues.append(OracleIssue("verdict_shared_goal_mismatch", "shared goal differs from accepted comparable alternative results and closure", (fact_id,)))
            continue
        if (
            len(listed_ids) != len(set(listed_ids))
            or set(listed_ids) != expected_ids
            or any(item not in assessments_by_id for item in listed_ids)
        ):
            issues.append(
                OracleIssue(
                    "verdict_required_component_coverage_mismatch",
                    "goal verdict does not cite every and only available required-component assessment exactly once",
                    (fact_id,),
                )
            )
        by_component_payload = {
            component_id: payloads.get(str(row["fact_id"]), {})
            for component_id, row in assessments_by_component.items()
            if component_id in required
        }
        families = {
            value.get("method_family_id")
            for component_id, value in by_component_payload.items()
            if component_id in family_components
            if _trimmed_text(value.get("method_family_id"))
        }
        assessed_families = {
            value.get("method_family_id")
            for value in by_component_payload.values()
            if _trimmed_text(value.get("method_family_id"))
        }
        one_family = (
            bool(family_components)
            and len(families) == 1
            and all(
                _trimmed_text(by_component_payload.get(component_id, {}).get("method_family_id"))
                for component_id in family_components
            )
        )
        complete = set(by_component_payload) == required
        primary_comparable = all(
            _assessment_result_for_component(
                component_id,
                assessments_by_component,
                assessment_results,
            )
            is not None
            and _assessment_result_for_component(
                component_id,
                assessments_by_component,
                assessment_results,
            ).outcome
            in _COMPARABLE_OUTCOMES
            for component_id in primary
        )
        implementation_ok = _primary_lineage_is_consistent(
            primary,
            assessments_by_component,
            assessment_results,
            require_identity=_contract_requires_implementation_identity(contract),
        )
        primary_values = {
            component_id: by_component_payload.get(component_id, {}).get("assessment")
            for component_id in primary
        }
        must_values = {
            component_id: by_component_payload.get(component_id, {}).get("assessment")
            for component_id in must
        }
        if (
            complete
            and expert_lineages_terminated
            and one_family
            and implementation_ok
            and primary
            and all(value == "SUPPORTED" for value in primary_values.values())
            and all(value == "SUPPORTED" for value in must_values.values())
        ):
            expected_verdict = "GOAL_MET"
        elif (
            complete
            and expert_lineages_terminated
            and one_family
            and implementation_ok
            and primary
            and primary_comparable
            and all(value in {"SUPPORTED", "REFUTED"} for value in primary_values.values())
            and any(value == "REFUTED" for value in primary_values.values())
            and all(value == "SUPPORTED" for value in must_values.values())
        ):
            expected_verdict = "GOAL_NOT_MET"
        else:
            expected_verdict = "INCONCLUSIVE"
        expected_family = (
            next(iter(selected_families))
            if len(selected_families) == 1
            else None
        )
        if expected_verdict == "INCONCLUSIVE" and len(assessed_families) > 1:
            expected_family = None
        if (
            payload.get("verdict") != expected_verdict
            or payload.get("method_family_id") != expected_family
        ):
            issues.append(
                OracleIssue(
                    "verdict_component_conjunction_mismatch",
                    "goal verdict disagrees with required component conjunction or shared lineage",
                    (fact_id,),
                )
            )


def _assessment_result_for_component(
    component_id: str,
    assessments_by_component: Mapping[str, sqlite3.Row],
    assessment_results: Mapping[str, _ComponentResult | None],
) -> _ComponentResult | None:
    assessment = assessments_by_component.get(component_id)
    return (
        None
        if assessment is None
        else assessment_results.get(str(assessment["subject_id"]))
    )


def _primary_lineage_is_consistent(
    primary_ids: set[str],
    assessments_by_component: Mapping[str, sqlite3.Row],
    assessment_results: Mapping[str, _ComponentResult | None],
    *,
    require_identity: bool,
) -> bool:
    results = [
        _assessment_result_for_component(
            component_id, assessments_by_component, assessment_results
        )
        for component_id in primary_ids
    ]
    comparable = [item for item in results if item is not None]
    if len(comparable) != len(primary_ids):
        return False
    return _lineage_values_are_consistent(comparable, require_identity=require_identity)


def _contract_requires_implementation_identity(contract: _GoalComponentContract) -> bool:
    return any(
        "implementation_patch" in component.required_artifacts
        or any(
            item.logical_artifact == "implementation_patch"
            for item in component.required_inputs
        )
        for component in contract.components
    )


def _lineage_values_are_consistent(
    results: Sequence[_ComponentResult],
    *,
    require_identity: bool,
) -> bool:
    pairs = [
        (item.implementation_digest, item.core_config_digest)
        for item in results
    ]
    if require_identity:
        return bool(pairs) and all(left is not None and right is not None for left, right in pairs) and len(set(pairs)) == 1
    implementations = {left for left, _ in pairs}
    configs = {right for _, right in pairs}
    return (
        implementations == {None}
        or None not in implementations and len(implementations) == 1
    ) and (configs == {None} or None not in configs and len(configs) == 1)


def _audit_primary_implementation_lineage(
    contract: _GoalComponentContract,
    component_results: Mapping[str, _ComponentResult],
    outcomes_by_component: Mapping[str, sqlite3.Row],
    payloads: Mapping[str, Mapping[str, Any]],
    issues: list[OracleIssue],
) -> None:
    primary_ids = {
        item.component_id for item in contract.components if item.tier == "primary"
    }
    available = [
        component_results[item]
        for item in primary_ids
        if item in component_results
        and payloads.get(str(outcomes_by_component[item]["fact_id"]), {}).get("outcome")
        in _COMPARABLE_OUTCOMES
    ]
    require_identity = _contract_requires_implementation_identity(contract)
    if not available or (not require_identity and len(available) < 2):
        return
    if not _lineage_values_are_consistent(
        available, require_identity=require_identity
    ):
        issues.append(
            OracleIssue(
                "primary_implementation_lineage_mismatch",
                "primary component results do not share one implementation/core configuration identity",
                tuple(sorted(primary_ids)),
            )
        )


def _audit_lowest_attempt(
    outcomes: Sequence[sqlite3.Row],
    artifacts: Sequence[sqlite3.Row],
    attempts: Sequence[sqlite3.Row],
    issues: list[OracleIssue],
) -> None:
    artifacts_by_id = {str(row["artifact_id"]): row for row in artifacts}
    attempts_by_id = {str(row["attempt_id"]): row for row in attempts}
    candidate_attempt_ids = {str(row["attempt_id"]) for row in artifacts}
    for outcome in outcomes:
        artifact = artifacts_by_id.get(str(outcome["artifact_id"]))
        if artifact is None:
            continue
        selected = attempts_by_id.get(str(artifact["attempt_id"]))
        if selected is None:
            continue
        earlier_successes = [
            row
            for row in attempts
            if row["kind"] == "PROCESS"
            and row["experiment_id"] == outcome["experiment_id"]
            and row["execution_id"] == outcome["execution_id"]
            and str(row["attempt_id"]) in candidate_attempt_ids
            and int(row["attempt_ordinal"]) < int(selected["attempt_ordinal"])
            and row["state"] == "CLOSED"
            and row["terminal_reason"] == "SUCCEEDED"
        ]
        if earlier_successes:
            issues.append(
                OracleIssue(
                    "nonlowest_candidate_attempt",
                    "accepted artifact bypasses an earlier successful attempt ordinal",
                    tuple(str(row["attempt_id"]) for row in earlier_successes) + (str(selected["attempt_id"]),),
                )
            )


def _normalize_decimal_text(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{label} cannot be bool")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{label} must be finite")
    if number == 0:
        return "0"
    normalized = number.normalize()
    return str(normalized) if abs(normalized.adjusted()) > 1000 else format(normalized, "f")


def _metric_accepts(direction: str, target: str, value: str) -> bool:
    actual = Decimal(value)
    threshold = Decimal(target)
    if direction == "MAXIMIZE":
        return actual >= threshold
    return actual <= threshold


def _owned_process_residue(attempts: Sequence[sqlite3.Row]) -> tuple[int, ...]:
    groups: set[int] = set()
    for row in attempts:
        value = row["process_group_id"]
        if value is None:
            continue
        process_group_id = int(value)
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        groups.add(process_group_id)
    return tuple(sorted(groups))


def _raw_artifact_digest(root: Path) -> str:
    files = _regular_files(root)
    if not files:
        raise ValueError("artifact set is empty")
    relative = sorted((path.relative_to(root).as_posix(), path) for path in files)
    relative.sort(key=lambda item: item[0].encode("utf-8"))
    digest = hashlib.sha256()
    for name, path in relative:
        encoded = name.encode("utf-8")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"not a regular artifact: {path}")
            digest.update(struct.pack(">Q", len(encoded)))
            digest.update(encoded)
            digest.update(struct.pack(">Q", metadata.st_size))
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError(f"artifact shortened while reading: {path}")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1):
                raise ValueError(f"artifact grew while reading: {path}")
        finally:
            os.close(fd)
    return digest.hexdigest()


def _regular_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in dirnames:
            path = base / name
            if path.is_symlink():
                raise ValueError(f"symlink directory in artifact set: {path}")
        for name in filenames:
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ValueError(f"non-regular file in artifact set: {path}")
            result.append(path)
    return result


def _parse_json_rows(
    rows: Sequence[sqlite3.Row],
    column: str,
    label: str,
    issues: list[OracleIssue],
    *,
    allow_null: bool = False,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        raw = row[column]
        identifier = _row_identifier(row)
        if raw is None and allow_null:
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            issues.append(OracleIssue("invalid_raw_json", f"{label}: {exc}", (identifier,)))
            continue
        if not isinstance(parsed, Mapping):
            issues.append(OracleIssue("invalid_raw_json", f"{label} must be an object", (identifier,)))
            continue
        result[identifier] = parsed
    return result


def _row_identifier(row: sqlite3.Row) -> str:
    if "revision" in row.keys() and "workflow_id" in row.keys():
        return f"{row['workflow_id']}@{row['revision']}"
    if "fact_id" in row.keys():
        return str(row["fact_id"])
    if "attempt_id" in row.keys():
        return str(row["attempt_id"])
    raise KeyError("raw JSON row has no stable identifier")


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _row_get(row: sqlite3.Row, name: str) -> Any:
    return row[name] if name in row.keys() else None


def _empty_report(run_id: str, *issues: OracleIssue) -> OracleReport:
    return OracleReport(
        run_id=run_id,
        version_tuple={name: None for name in _VERSION_COLUMNS},
        fact_types=(),
        causal_chain={kind: () for kind in _FACT_ORDER},
        workflow_state_counts={},
        attempt_state_counts={},
        attempt_terminal_reason_counts={},
        artifact_counts={"VALID": 0, "REJECTED": 0, "missing": 0},
        canonical_outcome_count=0,
        open_attempt_count=0,
        open_decision_count=0,
        pending_effect_count=0,
        nonterminal_experiment_count=0,
        duplicate_effect_count=0,
        conflicting_identity_count=0,
        owned_process_residue=(),
        issues=tuple(issues),
    )


__all__ = ["IndependentOracle", "Oracle", "OracleIssue", "OracleReport", "audit_all_runs"]
