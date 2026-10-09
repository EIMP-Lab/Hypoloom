"""Read-only construction of bounded Agent task, repair and rendezvous context."""
from __future__ import annotations
from dataclasses import replace
import json
from typing import Any, Iterable, Mapping
from .agent_aci import AgentACIError, AgentRequest, AgentResponse, EmbeddedInput, ValidatedSubmission, canonical_json, validate_submission_snapshot
from .collaboration import ExpertSignal, ExpertSignalKind, RendezvousOrigin, RendezvousReply
from .diagnostics import (
    bounded_diagnostic_text,
    credential_shaped_text,
    diagnostic_summary,
)
from .domain import AttemptTerminalReason
from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask
from .literature import LiteratureQuery, admit_literature_query_plan
from .provider_observation import PROVIDER_UNAVAILABLE


RETRYABLE_MODEL_FAILURES = frozenset({
    "agent_attempt_failed", "invalid_structured_output", "invalid_candidate",
    "missing_structured_output",
    "component_id_mismatch", "duplicate_effect", "duplicate_workspace_artifact",
    "expert_signal_context_mismatch", "hypothesis_lineage_mismatch",
    "invalid_effect", "invalid_expert_signal", "invalid_iteration_candidate",
    "invalid_response", "invalid_schema",
    "invalid_tool_workspace", "invalid_workspace", "invalid_workspace_artifact",
    "mandatory_effect_mismatch", "method_family_id_mismatch",
    "missing_candidate", "missing_workspace", "noncanonical_json", "output_too_large",
    "owner_role_mismatch", "proposer_role_mismatch", "rendezvous_reply_mismatch",
    "task_candidate_mismatch",
    "transport_output_too_large", "unexpected_expert_signal",
    "unexpected_workspace", "unsafe_path", "wrong_effect_identity",
})
RETRYABLE_INFRASTRUCTURE_FAILURES = frozenset({
    "invalid_provider_envelope", "invalid_provider_metadata",
    "invalid_provider_type", "invalid_turn_usage", "malformed_json",
    "nonzero_exit", "provider_error", "provider_not_success", "timed_out",
    "transport_stderr_too_large", "resource_infrastructure_blocked",
})
RETRYABLE_AGENT_FAILURES = RETRYABLE_MODEL_FAILURES | RETRYABLE_INFRASTRUCTURE_FAILURES
_TERMINAL_PROVIDER_SUBTYPES = frozenset({"error_max_budget_usd", "error_max_turns"})
_RETRYABLE_CANDIDATE_FAILURES = frozenset({
    "candidate_artifact_requirements", "candidate_component",
    "candidate_component_role", "candidate_evaluator", "candidate_method_family",
    "candidate_metrics", "candidate_owner_role", "candidate_workspace_files",
    "hypothesis_lineage_mismatch", "invalid_candidate",
    "resource_unsatisfied",
})
_MAX_ALLOWED_SUPPORTING_REFS_BYTES = 16 * 1024


def _allowed_supporting_refs(request: AgentRequest) -> tuple[str, ...] | None:
    """Return the request's exact citable refs, or None when unsafe to project."""

    refs = request.immutable_context_refs
    if not refs:
        return None
    projected: list[str] = []
    for ref in refs:
        clean = bounded_diagnostic_text(ref)
        if clean is None or clean != ref:
            return None
        projected.append(clean)
    if sum(len(ref.encode("utf-8")) for ref in projected) > (
        _MAX_ALLOWED_SUPPORTING_REFS_BYTES
    ):
        return None
    return tuple(projected)


def _failure_diagnostics(details: Mapping[str, Any] | None) -> Mapping[str, Any]:
    diagnostics = None if details is None else details.get("diagnostics")
    return diagnostics if isinstance(diagnostics, Mapping) else {}


def agent_retry_class(code: str, details: Mapping[str, Any] | None,
                      *, retryable: bool, bounded_context: bool = False) -> str | None:
    """Classify one declared retry without creating another authority."""

    if not retryable: return None
    diagnostics = _failure_diagnostics(details)
    subtype = diagnostics.get("provider_subtype")
    if subtype in _TERMINAL_PROVIDER_SUBTYPES: return None
    failure = diagnostics.get("provider_failure")
    failure = failure if isinstance(failure, Mapping) else {}
    from .context_management import CONTEXT_FAILURES
    if failure.get("code") in CONTEXT_FAILURES:
        if (failure.get("code") == "context_capacity" and failure.get("source") == "local_context_limit"
                and failure.get("recovery_action") == "input_reconfiguration_required"):
            return None
        return "INFRASTRUCTURE" if bounded_context else None
    if failure.get("code") in {"input_too_long", "token_budget_exceeded"}: return None
    if failure.get("code") in PROVIDER_UNAVAILABLE: return "INFRASTRUCTURE"
    correction = diagnostics.get("structured_output_correction") or {}
    if (bounded_context and not failure and not correction.get("error") and correction.get("status") == "unresolved"
            and type(correction.get("rejected_count")) is int and correction["rejected_count"] > 0):
        return "MODEL"  # Native semantic rejection still spends corrective retries.
    if subtype == "error_max_structured_output_retries": return "MODEL"
    if subtype == "error_during_execution": return "INFRASTRUCTURE"
    return (
        "MODEL"
        if code in RETRYABLE_MODEL_FAILURES or candidate_failure_retryable(code)
        else "INFRASTRUCTURE"
    )


def agent_failure_stage(code: str, details: Mapping[str, Any] | None) -> str:
    explicit = None if details is None else details.get("failure_stage")
    if isinstance(explicit, str) and explicit: return explicit
    if code.startswith("literature_"): return "literature_retrieval"
    if code in {"agent_workspace_preparation", "target_binding_missing"}: return "workspace_preparation"
    if code == "invalid_tool_workspace": return "agent_workspace"
    if code == "host_restart": return "host_recovery"
    failure = _failure_diagnostics(details).get("provider_failure")
    if isinstance(failure, Mapping) and failure.get("code") in {"context_capacity", "compaction_failed"}:
        return "context_reconstruction"
    if isinstance(failure, Mapping) and failure.get("code") in PROVIDER_UNAVAILABLE | {"input_too_long", "token_budget_exceeded"}:
        return "provider_admission"
    if _failure_diagnostics(details).get("provider_subtype") == "error_max_structured_output_retries": return "provider_structured_output"
    if code in RETRYABLE_MODEL_FAILURES: return "provider_output"
    if code == "resource_infrastructure_blocked": return "resource_admission"
    if code in RETRYABLE_INFRASTRUCTURE_FAILURES: return "provider_process"
    return "host_admission"


def candidate_failure_retryable(code: str) -> bool:
    return code in _RETRYABLE_CANDIDATE_FAILURES


def operational_agent_failure(observation: Mapping[str, Any]) -> bool:
    """A failed invocation is not itself scientific evidence against a method."""
    # Host archive/stop failures supersede any earlier provider diagnostic.
    # Reuse the existing retry classification boundary rather than relaxing it.
    if observation.get("failure_code") not in RETRYABLE_AGENT_FAILURES | {"host_restart"}:
        return False
    failure = _failure_diagnostics(observation).get("provider_failure") or {}
    return (failure.get("code") in PROVIDER_UNAVAILABLE | {"input_too_long", "context_capacity", "compaction_failed",
            "empty_output_limit", "token_budget_exceeded"}
        or observation.get("failure_code") in RETRYABLE_INFRASTRUCTURE_FAILURES | {"host_restart"})


def retryable_agent_validation_details(
    exc: AgentACIError,
) -> Mapping[str, Any] | None:
    """Retain only a bounded actionable output-validation diagnostic."""

    if exc.code not in RETRYABLE_MODEL_FAILURES and not candidate_failure_retryable(exc.code):
        return None
    message = bounded_diagnostic_text(
        exc.message.removeprefix(f"{exc.code}: ")
    ) or exc.code
    return {"diagnostics": {"validation_error": {
        "code": exc.code, "message": message,
    }}}


def _latest_closed_agent_attempt(
    store: Any, workflow_id: str, command_id: str,
) -> Mapping[str, Any] | None:
    attempts = sorted(
        (
            row
            for row in store.list_attempts(
                workflow_id=workflow_id, command_id=command_id
            )
            if row["kind"] == "AGENT" and row["state"] == "CLOSED"
        ),
        key=lambda row: int(row["attempt_ordinal"]),
    )
    return None if not attempts else attempts[-1]


def _is_query_plan_request(request: AgentRequest) -> bool:
    task = request.task_context
    return (
        isinstance(task, ResearchNodeTask)
        and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN
    ) or (
        isinstance(task, AuxiliaryTask)
        and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
    )


def _query_plan_correction_source(request: AgentRequest) -> str | None:
    sources: list[str] = []
    for item in request.input_artifacts:
        if not item.ref.endswith((
            "--candidate-validation-error", "--agent-retry-diagnostic",
        )):
            continue
        try:
            payload = json.loads(item.content_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        unresolved = payload.get("unresolved_validation_error")
        source = (
            unresolved.get("source_attempt_id")
            if isinstance(unresolved, Mapping)
            else payload.get("source_attempt_id")
        )
        if not isinstance(source, str) or not source:
            return None
        sources.append(source)
    return sources[0] if len(sources) == 1 else None


def _query_plan_validation_from_attempt(
    store: Any,
    workflow_id: str,
    command_id: str,
    source_attempt_id: str,
) -> Mapping[str, Any] | None:
    """Project one durable QueryPlan coverage rejection by source Attempt."""

    source = store.get_attempt(source_attempt_id)
    if (
        source is None
        or source["kind"] != "AGENT"
        or source["state"] != "CLOSED"
        or source["terminal_reason"] != AttemptTerminalReason.FAILED.value
        or source["workflow_id"] != workflow_id
        or source["command_id"] != command_id
    ):
        return None
    try:
        observation = json.loads(source["observation_json"])
        source_request = AgentRequest.from_dict(observation["request"])
        response = AgentResponse.from_dict(observation["rejected_response"])
    except (AgentACIError, KeyError, TypeError, json.JSONDecodeError):
        return None
    diagnostics = observation.get("diagnostics")
    validation = (
        diagnostics.get("validation_error")
        if isinstance(diagnostics, Mapping)
        else None
    )
    if (
        observation.get("failure_code") != "invalid_candidate"
        or not _is_query_plan_request(source_request)
        or source_request.attempt_id != source_attempt_id
        or source_request.owner_workflow_id != workflow_id
        or source_request.command_id != command_id
        or response.command is not source_request.command
        or response.command_id != source_request.command_id
        or response.attempt_id != source_request.attempt_id
        or response.attempt_ordinal != source_request.attempt_ordinal
        or not isinstance(validation, Mapping)
        or validation.get("code") != "invalid_candidate"
    ):
        return None
    message = bounded_diagnostic_text(validation.get("message"))
    if message is None:
        return None
    try:
        rejected = LiteratureQuery.from_dict(response.typed_candidate)
        template_input, = (
            item for item in source_request.input_artifacts
            if item.ref.endswith("--literature-query-template")
        )
        template = LiteratureQuery.from_dict(json.loads(template_input.content_bytes()))
        admit_literature_query_plan(rejected, template=template)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    mutable: dict[str, Any] = {"query": rejected.query}
    if rejected.preferred_venues:
        mutable["preferred_venues"] = [
            venue.to_dict() for venue in rejected.preferred_venues
        ]
    return {
        "unresolved_validation_error": {
            "source_attempt_id": source_attempt_id,
            "code": "invalid_candidate",
            "message": message,
        },
        "rejected_candidate": mutable,
    }


def unresolved_query_plan_validation(
    store: Any, request: AgentRequest,
) -> Mapping[str, Any] | None:
    """Resolve one still-actionable coverage rejection from durable Attempts."""

    if not _is_query_plan_request(request):
        return None
    source_attempt_id = _query_plan_correction_source(request)
    if source_attempt_id is None:
        return None
    return _query_plan_validation_from_attempt(
        store,
        request.owner_workflow_id,
        request.command_id,
        source_attempt_id,
    )


def _persisted_rejected_structured_output(
    observation: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Accept only an adapter-persisted, credential-safe JSON mapping."""

    diagnostics = observation.get("diagnostics")
    candidate = (
        diagnostics.get("provider_structured_candidate")
        if isinstance(diagnostics, Mapping)
        else None
    )
    if not isinstance(candidate, Mapping):
        return None
    try:
        encoded = canonical_json(candidate)
    except AgentACIError:
        return None
    if (
        len(encoded) > 64 * 1024
        or credential_shaped_text(encoded.decode("utf-8"))
    ):
        return None
    try:
        decoded = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(decoded, Mapping)
        or decoded != candidate
    ):
        return None
    return decoded


def candidate_retry_inputs(
    store: Any, workflow_id: str, command_id: str
) -> tuple[EmbeddedInput, ...]:
    """Project the latest candidate validation error into its bounded retry."""

    latest = _latest_closed_agent_attempt(store, workflow_id, command_id)
    if latest is None:
        return ()
    if latest["terminal_reason"] != AttemptTerminalReason.FAILED.value:
        return ()
    observation = json.loads(latest["observation_json"])
    if not isinstance(observation, Mapping) or observation.get("failure_code") != "invalid_candidate":
        return ()
    diagnostics = observation.get("diagnostics")
    validation_error = (
        diagnostics.get("validation_error")
        if isinstance(diagnostics, Mapping)
        else None
    )
    if (
        not isinstance(validation_error, Mapping)
        or validation_error.get("code") != "invalid_candidate"
        or not isinstance(validation_error.get("message"), str)
    ):
        return ()
    payload = {
        "source_attempt_id": str(latest["attempt_id"]),
        "validation_error": {
            "code": "invalid_candidate",
            "message": validation_error["message"],
        },
    }
    if (unresolved := _query_plan_validation_from_attempt(
        store,
        workflow_id,
        command_id,
        str(latest["attempt_id"]),
    )) is not None:
        payload["rejected_candidate"] = unresolved["rejected_candidate"]
    ref = f"{latest['attempt_id']}--candidate-validation-error"
    return (
        EmbeddedInput.from_bytes(
            ref, "application/json", canonical_json(payload)
        ),
    )


def agent_retry_inputs(
    store: Any, workflow_id: str, command_id: str,
) -> tuple[EmbeddedInput, ...]:
    """Project the direct prior actionable Agent failure into one retry.

    The Attempt row remains the sole source.  This creates no retry record or
    correction authority: it only renders the latest durable failure into the
    next request for the same stable Command.
    """

    candidate = candidate_retry_inputs(store, workflow_id, command_id)
    if candidate:
        return candidate
    latest = _latest_closed_agent_attempt(store, workflow_id, command_id)
    if latest is None or latest["terminal_reason"] not in {
        AttemptTerminalReason.FAILED.value,
        AttemptTerminalReason.TIMED_OUT.value,
        AttemptTerminalReason.LOST.value,
    }:
        return ()
    observation = json.loads(latest["observation_json"])
    if not isinstance(observation, Mapping) or observation.get("retry_class") != "MODEL":
        return ()
    failure_code = bounded_diagnostic_text(observation.get("failure_code"))
    failure_stage = bounded_diagnostic_text(observation.get("failure_stage"))
    if failure_code is None or failure_stage is None:
        return ()
    payload: dict[str, Any] = {
        "source_attempt_id": str(latest["attempt_id"]),
        "source_attempt_ordinal": int(latest["attempt_ordinal"]),
        "terminal_reason": str(latest["terminal_reason"]),
        "retry_class": "MODEL",
        "failure_code": failure_code,
        "failure_stage": failure_stage,
    }
    if (summary := diagnostic_summary(observation)) is not None:
        payload["diagnostic_summary"] = summary
    if (rejected := _persisted_rejected_structured_output(
        observation,
    )) is not None:
        payload["rejected_structured_output"] = rejected
    raw_request = observation.get("request")
    if isinstance(raw_request, Mapping):
        try:
            predecessor_request = AgentRequest.from_dict(raw_request)
        except AgentACIError:
            predecessor_request = None
        if (
            predecessor_request is not None
            and predecessor_request.owner_workflow_id == workflow_id
            and predecessor_request.command_id == command_id
            and predecessor_request.attempt_id == latest["attempt_id"]
            and predecessor_request.attempt_ordinal == latest["attempt_ordinal"]
        ):
            if failure_stage in {"provider_structured_output", "provider_output"}:
                allowed = _allowed_supporting_refs(predecessor_request)
                if allowed is not None:
                    payload["allowed_supporting_refs"] = list(allowed)
            if (unresolved := unresolved_query_plan_validation(
                store, predecessor_request,
            )) is not None:
                payload.update(unresolved)
    ref = f"{latest['attempt_id']}--agent-retry-diagnostic"
    return (
        EmbeddedInput.from_bytes(
            ref, "application/json", canonical_json(payload),
        ),
    )


def infrastructure_retry_request(
    store: Any, workflow_id: str, command_id: str,
) -> tuple[AgentRequest, bool] | None:
    """Restore an infrastructure retry or successful execution handoff request.

    Every infrastructure retry reuses the durable request's exact pre-effect
    context while receiving a fresh Attempt identity, deadline and budget.
    A retrieval failure additionally invokes the fixed outward adapter again;
    this preserves any earlier MODEL correction without caching a failed probe.
    """

    latest = _latest_closed_agent_attempt(store, workflow_id, command_id)
    if latest is not None and latest["terminal_reason"] == "SUCCEEDED":
        from .agent_aci import execution_handoff
        observation = json.loads(latest["observation_json"])
        if execution_handoff(observation):
            return AgentRequest.from_dict(observation["request"]), False
    if latest is None or latest["terminal_reason"] not in {
        AttemptTerminalReason.FAILED.value,
        AttemptTerminalReason.TIMED_OUT.value,
        AttemptTerminalReason.LOST.value,
    }:
        return None
    observation = json.loads(latest["observation_json"])
    if not isinstance(observation, Mapping):
        raise ValueError("infrastructure retry observation is invalid")
    if observation.get("retry_class") != "INFRASTRUCTURE":
        return None
    repeat_retrieval = observation.get("failure_stage") == "literature_retrieval"
    raw_request = observation.get("request")
    if not isinstance(raw_request, Mapping):
        raise ValueError("infrastructure retry lacks its durable Agent request")
    try:
        request = AgentRequest.from_dict(raw_request)
    except AgentACIError as exc:
        raise ValueError("infrastructure retry Agent request is invalid") from exc
    if (request.owner_workflow_id != workflow_id
            or request.command_id != command_id):
        raise ValueError("infrastructure retry Agent request identity differs")
    return request, repeat_retrieval


def validated_draft_source(store: Any, experiment_id: str) -> Mapping[str, Any] | None:
    attempts = store.list_attempts(execution_id=f"{experiment_id}--candidate-validation")
    latest = max(attempts, key=lambda row: (float(row["started_at"]), str(row["attempt_id"])), default=None)
    if latest is None or latest["state"] != "CLOSED" or latest["terminal_reason"] != AttemptTerminalReason.SUCCEEDED.value: return None
    diagnostic = json.loads(latest["observation_json"]); source = store.get_attempt(str(diagnostic.get("source_agent_attempt_id")))
    if source is None or source["kind"] != "AGENT" or source["state"] != "CLOSED" or source["terminal_reason"] != AttemptTerminalReason.SUCCEEDED.value: return None
    observation = json.loads(source["observation_json"])
    return source if isinstance(observation.get("request"), Mapping) and isinstance(observation.get("response"), Mapping) else None

def _attempt_log_tail(
    publisher: Any, attempt_id: object, name: str
) -> bytes | None:
    path = (
        publisher.root
        / "result-staging"
        / str(attempt_id)
        / "logs"
        / f"{name}.log"
    )
    return path.read_bytes()[-65_536:] if path.is_file() else None


def _validated_submission_source(
    command: Mapping[str, Any], run_id: str,
    source_attempt: Mapping[str, Any] | None,
) -> tuple[AgentRequest, Mapping[str, Any], ValidatedSubmission]:
    """Read one accepted submission without requiring its Agent to be CLOSED."""
    if (
        command.get("command_type") != "CREATE_EXPERIMENT"
        or command.get("response_status") != "accepted"
        or command.get("run_id") != run_id
        or source_attempt is None or source_attempt.get("kind") != "AGENT"
        or source_attempt.get("run_id") != run_id
    ):
        raise ValueError("submitted experiment source command ownership differs")
    try:
        data = json.loads(command["request_json"])
        observation = json.loads(source_attempt["observation_json"])
        if not isinstance(data, Mapping) or not isinstance(observation, Mapping):
            raise ValueError("submit source and Agent observation must be objects")
        request = AgentRequest.from_dict(data["source_request"])
        original = AgentRequest.from_dict(observation["request"])
        validated = validate_submission_snapshot(request, data["source_snapshot"])
        if (
            data.get("tool") != "submit_experiment"
            or data.get("execution_node_command_id") != request.command_id
            or request.to_dict() != original.to_dict()
            or request.run_id != run_id
            or request.owner_workflow_id != source_attempt.get("workflow_id")
            or request.command_id != source_attempt.get("command_id")
            or request.attempt_id != source_attempt.get("attempt_id")
            or request.attempt_ordinal != source_attempt.get("attempt_ordinal")
        ):
            raise ValueError("submit source differs from its exact Agent")
    except (AgentACIError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"submitted experiment source is invalid: {exc}") from exc
    return request, data["source_snapshot"], validated


def _submitted_source(
    store: Any, run_id: str, spec: Mapping[str, Any],
    source_attempt: Mapping[str, Any] | None,
) -> ValidatedSubmission:
    payload = spec["payload"]
    contract = payload["execution_contract"]
    submit_id = contract.get("source_submit_command_id")
    if not isinstance(submit_id, str) or not submit_id:
        raise ValueError("submitted experiment source command identity is invalid")
    commands = store.inspect("SELECT * FROM workflow_commands WHERE command_id=?", (submit_id,))
    if len(commands) != 1:
        raise ValueError("submitted experiment source command is unavailable")
    command = commands[0]
    request, _, validated = _validated_submission_source(command, run_id, source_attempt)
    if (
        command.get("command_id") != submit_id
        or command.get("workflow_id") != spec.get("experiment_id")
        or request.attempt_id != contract.get("source_agent_attempt_id")
        or request.component_id != payload.get("component_id")
        or request.method_family_id != payload.get("method_family_id")
        or validated.output_digest != contract.get("source_agent_output_digest")
    ):
        raise ValueError("submitted experiment source differs from its preregistration")
    return validated


def _submitted_workspace_files(
    store: Any, run_id: str, spec: Mapping[str, Any],
    source_attempt: Mapping[str, Any] | None,
) -> Mapping[str, bytes]:
    return _submitted_source(store, run_id, spec, source_attempt).workspace_files


def _submission_source_inputs(prefix: str, submission: ValidatedSubmission) -> tuple[EmbeddedInput, ...]:
    """Expose the admitted command's actual sources, never a fabricated .py file."""
    candidate, files = submission.candidate, submission.workspace_files
    if candidate.execution_argv is None:
        if "experiment.py" not in files:
            raise ValueError("submitted source experiment.py is unavailable")
        return (EmbeddedInput.from_bytes(prefix + "experiment.py", "text/x-python", files["experiment.py"]),)
    measurement = candidate.measurement
    if measurement is None and candidate.evaluation_scope != "GOAL":
        raise ValueError("submitted generic source has no measurement declaration")
    paths = set(() if measurement is None else measurement.parser_files)
    for argument in candidate.execution_argv:
        if argument.startswith("/input/"):
            paths.add(argument.removeprefix("/input/"))
        elif not argument.startswith(("/", "-")):
            relative = argument.removeprefix("./")
            if relative in files:
                paths.add(relative)
    if not paths <= files.keys():
        raise ValueError("submitted command or parser source is unavailable")
    sources = tuple(EmbeddedInput.from_bytes(prefix + path,
                    "text/x-python" if path.endswith(".py") else "text/plain", files[path])
                    for path in sorted(paths))
    command = EmbeddedInput.from_bytes(prefix + "source-command", "application/json", canonical_json({
        "execution_argv": list(candidate.execution_argv), "parser_argv": None if measurement is None else list(measurement.parser_argv),
        "sources": [{"path": path, "ref": prefix + path} for path in sorted(paths)],
    }))
    return (command, *sources)


def _analysis_experiment_id(
    store: Any,
    run_id: str,
    task: ResearchNodeTask,
) -> str:
    """Select the exact chain iteration's repair tip, not global/latest order."""

    key = task.key
    if (
        key.run_id != run_id
        or key.node_kind.value != "RESULT_ANALYSIS"
        or key.experiment_ordinal is None
    ):
        raise ValueError("result analysis task identity is invalid")
    creations: list[tuple[str, Mapping[str, Any]]] = []
    for row in store.inspect(
        "SELECT workflow_id,payload_json FROM workflow_transitions "
        "WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1",
        (run_id,),
    ):
        try:
            identity = json.loads(row["payload_json"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("result analysis experiment identity is invalid") from exc
        if not isinstance(identity, Mapping):
            raise ValueError("result analysis experiment identity is invalid")
        scope_matches = (
            identity.get("component_id") == key.component_id
            and identity.get("role") == key.expert_slot
            and identity.get("expert_cycle", 1) == key.cycle
        )
        iteration_matches = identity.get("experiment_ordinal", 1) == key.experiment_ordinal
        if key.chain is not None:
            iteration_matches = (identity.get("chain") == key.chain
                and identity.get("chain_iteration") == key.experiment_ordinal
                and identity.get("execution_node_command_id") == replace(key, node_kind=ResearchNodeKind.EXPERIMENT_EXECUTION).command_id)
        if scope_matches and iteration_matches:
            creations.append((str(row["workflow_id"]), identity))
    if key.chain is not None and creations:
        members = dict(creations)
        if len(members) != len(creations):
            raise ValueError("result analysis has duplicate experiment creation identities")
        outcomes = {str(row["outcome_id"]): str(row["experiment_id"])
                    for row in store.list_facts(run_id, "CanonicalOutcomeAccepted")}
        parents = {}
        for experiment, identity in creations:
            preflight, revision = identity.get("preflight_repair_of_experiment_id"), identity.get("revision_of_outcome_id")
            if preflight is not None and revision is not None:
                raise ValueError("result analysis repair has two predecessor types")
            parent = preflight if preflight is not None else outcomes.get(revision)
            if (preflight is not None or revision is not None) and (parent not in members or parent == experiment):
                raise ValueError("result analysis repair predecessor belongs to another execution source")
            parents[experiment] = parent
        predecessors = [parent for parent in parents.values() if parent is not None]
        tips = set(members) - set(predecessors)
        if len(tips) != 1 or len(predecessors) != len(set(predecessors)):
            raise ValueError("result analysis repair chain has no unique terminal tip")
        tip = next(iter(tips))
        visited, cursor = set(), tip
        while cursor is not None and cursor not in visited:
            visited.add(cursor)
            cursor = parents[cursor]
        if visited != set(members) or cursor is not None:
            raise ValueError("result analysis repairs do not form one causal execution chain")
        creations = [(tip, members[tip])]
    if len(creations) != 1:
        raise ValueError("result analysis current experiment is unavailable")
    experiment_id, _ = creations[0]
    return experiment_id


def result_analysis_inputs(
    store: Any,
    publisher: Any,
    run_id: str,
    task: ResearchNodeTask,
) -> tuple[EmbeddedInput, ...]:
    """Project the exact evaluated experiment named by one Result Analysis key."""
    key = task.key
    experiment_id = _analysis_experiment_id(store, run_id, task)
    spec = store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
    outcomes = [
        row
        for row in store.list_facts(run_id, "CanonicalOutcomeAccepted")
        if str(row.get("experiment_id")) == experiment_id
    ]
    if spec is None or len(outcomes) != 1:
        raise ValueError("result analysis current facts are unavailable")
    outcome = outcomes[0]
    if (
        spec.get("experiment_id") != experiment_id
        or spec.get("execution_id") != outcome.get("execution_id")
        or spec.get("payload", {}).get("component_id") != key.component_id
        or outcome.get("payload", {}).get("component_id") != key.component_id
    ):
        raise ValueError("result analysis current facts differ from its task")
    transitions = store.inspect(
        "SELECT payload_json FROM workflow_transitions "
        "WHERE run_id=? AND workflow_id=? AND fact_id=? ORDER BY revision",
        (run_id, experiment_id, str(outcome["fact_id"])),
    )
    if len(transitions) != 1:
        raise ValueError("result analysis evaluator diagnostic is unavailable")
    try:
        terminal = json.loads(transitions[0]["payload_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("result analysis terminal transition is invalid") from exc
    evaluation = terminal.get("outcome") if isinstance(terminal, Mapping) else None
    process_attempt_id = terminal.get("attempt_id") if isinstance(terminal, Mapping) else None
    if (
        not isinstance(evaluation, Mapping)
        or not isinstance(process_attempt_id, str)
        or evaluation.get("outcome") != outcome.get("payload", {}).get("outcome")
    ):
        raise ValueError("result analysis terminal evaluation is invalid")
    process = store.get_attempt(process_attempt_id)
    if (
        process is None
        or process.get("kind") != "PROCESS"
        or process.get("state") != "CLOSED"
        or process.get("workflow_id") != experiment_id
        or process.get("experiment_id") != experiment_id
        or process.get("execution_id") != outcome.get("execution_id")
    ):
        raise ValueError("result analysis Process Attempt is unavailable")
    try:
        observation = json.loads(process["observation_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("result analysis Process observation is invalid") from exc
    if not isinstance(observation, Mapping):
        raise ValueError("result analysis Process observation is invalid")
    artifacts = store.inspect(
        "SELECT * FROM execution_artifacts WHERE attempt_id=?",
        (process_attempt_id,),
    )
    if (
        publisher is None
        or len(artifacts) != 1
        or artifacts[0].get("artifact_id") != outcome.get("artifact_id")
    ):
        raise ValueError("result analysis publication is unavailable")
    root = publisher.root.joinpath(*str(artifacts[0]["path"]).split("/"))
    result_path = root / "result.json"
    result_paths = {"result.json"}
    binding = spec["payload"].get("goal_evaluation")
    if binding is not None and binding["scope"] == "GOAL":
        result_paths.add("goal/result.json")
        result_paths.update("raw/" + path for path in binding["acceptance"]["raw_artifacts"])
    measurement = spec["payload"].get("measurement")
    if measurement is not None:
        result_paths.update("raw/" + path for path in measurement["raw_artifacts"])
    source_attempt_id = spec.get("payload", {}).get(
        "execution_contract", {}
    ).get("source_agent_attempt_id")
    source_attempt = (
        store.get_attempt(source_attempt_id)
        if isinstance(source_attempt_id, str)
        else None
    )
    if spec["payload"]["execution_contract"].get("source_submit_command_id") is not None:
        source_inputs = _submission_source_inputs(
            f"{task.command_id}--current-", _submitted_source(store, run_id, spec, source_attempt))
    else:
        if (
            source_attempt is None
            or source_attempt.get("kind") != "AGENT"
            or source_attempt.get("state") != "CLOSED"
            or source_attempt.get("terminal_reason")
            != AttemptTerminalReason.SUCCEEDED.value
        ):
            raise ValueError("result analysis source Agent Attempt is unavailable")
        try:
            source_observation = json.loads(source_attempt["observation_json"])
            response = AgentResponse.from_dict(source_observation["response"])
        except (AgentACIError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("result analysis source Agent response is invalid") from exc
        source = _workspace_artifact(response, "experiment.py")
        if source is None:
            raise ValueError("result analysis source experiment.py is unavailable")
        source_inputs = (EmbeddedInput.from_bytes(
            f"{task.command_id}--current-experiment.py", "text/x-python", source[-65_536:]),)
    prefix = task.command_id
    return (
        EmbeddedInput.from_bytes(
            f"{prefix}--current-spec", "application/json", canonical_json(spec)
        ),
        EmbeddedInput.from_bytes(
            f"{prefix}--current-outcome", "application/json", canonical_json(outcome)
        ),
        EmbeddedInput.from_bytes(
            f"{prefix}--current-evaluation",
            "application/json",
            canonical_json(evaluation),
        ),
        EmbeddedInput.from_bytes(
            f"{prefix}--current-process",
            "application/json",
            canonical_json(observation),
        ),
        EmbeddedInput.from_bytes(f"{prefix}--current-result-files", "application/json", canonical_json({
            "artifact_id": outcome["artifact_id"],
            "available": sorted(path for path in result_paths if (root / path).is_file()),
            "missing": sorted(path for path in result_paths if not (root / path).is_file()),
            "common_result_path": "goal/result.json" if binding is not None and binding["scope"] == "GOAL" else None})),
        *source_inputs,
        *((EmbeddedInput.from_bytes(
            f"{prefix}--current-result.json", "application/json",
            result_path.read_bytes()[-65_536:],
        ),) if result_path.is_file() else ()),
        *((EmbeddedInput.from_bytes(f"{prefix}--current-goal-result.json", "application/json",
            (root / "goal/result.json").read_bytes()[-65_536:]),) if (root / "goal/result.json").is_file() else ()),
        EmbeddedInput.from_bytes(
            f"{prefix}--current-stdout",
            "text/plain",
            _attempt_log_tail(publisher, process_attempt_id, "stdout") or b"",
        ),
        EmbeddedInput.from_bytes(
            f"{prefix}--current-stderr",
            "text/plain",
            _attempt_log_tail(publisher, process_attempt_id, "stderr") or b"",
        ),
    )


def _selected_review_inputs(
    origins: tuple[RendezvousOrigin, ...],
    replies: Mapping[str, RendezvousReply],
    selections: tuple[tuple[str, str], ...],
) -> tuple[EmbeddedInput, ...]:
    result: list[EmbeddedInput] = []
    for phase, attempt_id in selections:
        matches = [origin for origin in origins if origin.phase == phase and origin.source_attempt_id == attempt_id]
        if len(matches) > 1: raise ValueError(f"successor DRAFT predecessor {phase} review is ambiguous")
        if not matches: continue
        origin = matches[0]; reply = replies.get(origin.rendezvous_id)
        if reply is None: raise ValueError(f"successor DRAFT predecessor {phase} reply is unavailable")
        ref = f"{origin.rendezvous_id}--reply"
        result.append(EmbeddedInput.from_bytes(ref, "application/json", canonical_json(reply.to_dict())))
    return tuple(result)


def predecessor_review_inputs(origins: tuple[RendezvousOrigin, ...], replies: Mapping[str, RendezvousReply], *, source_attempt_id: str, process_attempt_id: str) -> tuple[EmbeddedInput, ...]:
    """Project at most the immediate predecessor's preflight and analysis replies."""

    return _selected_review_inputs(origins, replies, (
        ("preflight", source_attempt_id), ("analyze", process_attempt_id),
    ))


def _workspace_artifact(
    response: AgentResponse, path: str,
) -> bytes | None:
    return next(
        (item.content_bytes() for item in response.proposed_workspace_artifacts
         if item.path == path),
        None,
    )


def draft_repair_inputs(store: Any, publisher: Any, run_id: str, owner_id: str, *, origins: tuple[RendezvousOrigin, ...], replies: Mapping[str, RendezvousReply]) -> tuple[EmbeddedInput, ...]:
    result: list[EmbeddedInput] = []; validations = sorted((row for row in store.list_attempts(execution_id=f"{owner_id}--candidate-validation") if row["state"] == "CLOSED"), key=lambda row: (float(row["started_at"]), str(row["attempt_id"])))
    if validations:
        validation = validations[-1]; diagnostic = json.loads(validation["observation_json"]); source = store.get_attempt(str(diagnostic.get("source_agent_attempt_id")))
        if source is not None:
            response = AgentResponse.from_dict(json.loads(source["observation_json"])["response"]); content = _source(response)
            result.extend((EmbeddedInput.from_bytes(f"{validation['attempt_id']}--previous-experiment.py", "text/x-python", content), EmbeddedInput.from_bytes(f"{validation['attempt_id']}--validation-diagnostics", "application/json", canonical_json(diagnostic))))
            patch = _workspace_artifact(response, "implementation.patch")
            if patch is not None:
                result.append(EmbeddedInput.from_bytes(
                    f"{validation['attempt_id']}--previous-implementation.patch",
                    "text/x-diff", patch,
                ))
            result.extend(_selected_review_inputs(
                origins, replies, (("preflight", str(source["attempt_id"])),),
            ))
    creation = store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (owner_id,)); revision = None if not creation else json.loads(creation[0]["payload_json"]).get("revision_of_outcome_id")
    if revision is None: return tuple(result)
    outcome = store.find_fact(run_id, "CanonicalOutcomeAccepted", str(revision)); spec = None if outcome is None else store.find_fact(run_id, "ExperimentPreregistered", str(outcome["experiment_id"]))
    source = None if spec is None else store.get_attempt(str(spec["payload"]["execution_contract"]["source_agent_attempt_id"]))
    process = None if outcome is None else next((row for row in store.list_attempts(execution_id=str(outcome["execution_id"])) if row["state"] == "CLOSED" and row["terminal_reason"] != AttemptTerminalReason.LOST.value), None)
    if outcome is None or spec is None or source is None or process is None: raise ValueError("successor DRAFT predecessor context is incomplete")
    evaluations = store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_id=? AND fact_id=?", (run_id, str(outcome["experiment_id"]), str(outcome["fact_id"])))
    if len(evaluations) != 1: raise ValueError("successor DRAFT predecessor evaluation is unavailable")
    evaluation = json.loads(evaluations[0]["payload_json"]).get("outcome")
    if not isinstance(evaluation, Mapping): raise ValueError("successor DRAFT predecessor evaluation is invalid")
    response = AgentResponse.from_dict(json.loads(source["observation_json"])["response"]); observation = json.loads(process["observation_json"])
    result.extend((EmbeddedInput.from_bytes(f"{owner_id}--predecessor-spec", "application/json", canonical_json(spec)), EmbeddedInput.from_bytes(f"{owner_id}--predecessor-outcome", "application/json", canonical_json(outcome)), EmbeddedInput.from_bytes(f"{owner_id}--predecessor-evaluation", "application/json", canonical_json(evaluation)), EmbeddedInput.from_bytes(f"{owner_id}--predecessor-experiment.py", "text/x-python", _source(response)), EmbeddedInput.from_bytes(f"{owner_id}--predecessor-process", "application/json", canonical_json(observation))))
    patch = _workspace_artifact(response, "implementation.patch")
    if patch is not None:
        result.append(EmbeddedInput.from_bytes(
            f"{owner_id}--predecessor-implementation.patch", "text/x-diff", patch,
        ))
    artifacts = store.inspect("SELECT * FROM execution_artifacts WHERE attempt_id=?", (process["attempt_id"],))
    if len(artifacts) == 1 and publisher is not None:
        root = publisher.root.joinpath(*str(artifacts[0]["path"]).split("/"))
        result_path = root / "result.json"
        if result_path.is_file():
            result.append(EmbeddedInput.from_bytes(f"{owner_id}--predecessor-result.json", "application/json", result_path.read_bytes()[-65_536:]))
        for name in ("stdout", "stderr"):
            content = _attempt_log_tail(publisher, process["attempt_id"], name)
            if content is not None:
                result.append(EmbeddedInput.from_bytes(f"{owner_id}--predecessor-logs-{name}.log", "text/plain", content))
    result.extend(predecessor_review_inputs(origins, replies, source_attempt_id=str(source["attempt_id"]), process_attempt_id=str(process["attempt_id"])))
    return tuple(result)

def rendezvous_inputs(store: Any, publisher: Any, run_id: str, source: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, EmbeddedInput]:
    by_ref: dict[str, EmbeddedInput] = {}
    if source["kind"] == "AGENT":
        request = AgentRequest.from_dict(observation["request"])
        preflight_request = request.command.value in {"DRAFT_EXPERIMENT_SPEC", "EXPERIMENT_EXECUTION"}
        for item in request.input_artifacts:
            if preflight_request and item.ref.endswith("--fact-goal"):
                by_ref[item.ref] = _preflight_goal_input(item)
            elif preflight_request and item.ref.endswith("-config"):
                by_ref[item.ref] = _preflight_config_input(item, request.component_id)
            else:
                by_ref[item.ref] = item
        if isinstance(observation.get("response"), Mapping):
            ref = f"{source['attempt_id']}--response"; response = AgentResponse.from_dict(observation["response"])
            response_payload = ({"typed_candidate": response.typed_candidate}
                                if request.command.value == "DRAFT_EXPERIMENT_SPEC"
                                else observation["response"])
            by_ref[ref] = EmbeddedInput.from_bytes(ref, "application/json", canonical_json(response_payload)); content = next((item.content_bytes() for item in response.proposed_workspace_artifacts if item.path == "experiment.py"), None)
            if content is not None: by_ref[f"{source['attempt_id']}--experiment.py"] = EmbeddedInput.from_bytes(f"{source['attempt_id']}--experiment.py", "text/x-python", content)
        if request.command.value == "EXPERIMENT_EXECUTION":
            for command in store.inspect(
                "SELECT * FROM workflow_commands WHERE run_id=? AND command_type='CREATE_EXPERIMENT' "
                "AND response_status='accepted' AND json_extract(request_json,'$.source_request.attempt_id')=? "
                "ORDER BY command_id", (run_id, source["attempt_id"]),
            ):
                _, snapshot, validated = _validated_submission_source(command, run_id, source)
                ref = f"{command['workflow_id']}--submission"
                projected = {"source_submit_command_id": command["command_id"],
                    "experiment_id": command["workflow_id"], "typed_candidate": snapshot["arguments"]["candidate"]}
                by_ref[ref] = EmbeddedInput.from_bytes(ref, "application/json", canonical_json(projected))
                by_ref.update({item.ref: item for item in _submission_source_inputs(
                    f"{command['workflow_id']}--", validated)})
            return by_ref
        for validation in store.list_attempts(execution_id=f"{source['workflow_id']}--candidate-validation"):
            if validation["state"] != "CLOSED": continue
            diagnostic = json.loads(validation["observation_json"])
            if diagnostic.get("source_agent_attempt_id") == source["attempt_id"]:
                ref = f"{validation['attempt_id']}--observation"; by_ref[ref] = EmbeddedInput.from_bytes(ref, "application/json", canonical_json(diagnostic))
        return by_ref
    spec = store.find_fact(run_id, "ExperimentPreregistered", source["experiment_id"]); artifacts = store.inspect("SELECT * FROM execution_artifacts WHERE attempt_id=?", (source["attempt_id"],))
    if spec is None or len(artifacts) != 1 or publisher is None: raise ValueError("analysis rendezvous inputs are unavailable")
    by_ref[str(spec["fact_id"])] = EmbeddedInput.from_bytes(str(spec["fact_id"]), "application/json", canonical_json(spec)); artifact = artifacts[0]; root = publisher.root.joinpath(*str(artifact["path"]).split("/")); by_ref[str(artifact["artifact_id"])] = EmbeddedInput.from_bytes(str(artifact["artifact_id"]), "application/json", (root / "result.json").read_bytes())
    observation_ref = f"{source['attempt_id']}--observation"; by_ref[observation_ref] = EmbeddedInput.from_bytes(observation_ref, "application/json", canonical_json(observation))
    evaluation_ref = f"{source['attempt_id']}--evaluation"
    evaluations = []
    for row in store.inspect(
        "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_id=? ORDER BY revision",
        (run_id, str(source["workflow_id"])),
    ):
        payload = json.loads(row["payload_json"])
        origin = payload.get("rendezvous_origin")
        candidate = payload.get("outcome")
        if (isinstance(origin, Mapping)
                and origin.get("source_attempt_id") == source["attempt_id"]
                and isinstance(candidate, Mapping)):
            evaluations.append(candidate)
    if len(evaluations) != 1:
        raise ValueError("analysis rendezvous evaluator diagnostic is unavailable")
    by_ref[evaluation_ref] = EmbeddedInput.from_bytes(
        evaluation_ref, "application/json", canonical_json(evaluations[0])
    )
    source_attempt = store.get_attempt(str(spec["payload"]["execution_contract"]["source_agent_attempt_id"]))
    if source_attempt is None: raise ValueError("analysis source lacks its DRAFT Agent attempt")
    if spec["payload"]["execution_contract"].get("source_submit_command_id") is not None:
        by_ref.update({item.ref: item for item in _submission_source_inputs(
            f"{source_attempt['attempt_id']}--", _submitted_source(store, run_id, spec, source_attempt))})
    else:
        response = AgentResponse.from_dict(json.loads(source_attempt["observation_json"])["response"])
        content = _source(response)
        source_ref = f"{source_attempt['attempt_id']}--experiment.py"
        by_ref[source_ref] = EmbeddedInput.from_bytes(source_ref, "text/x-python", content)
    for name in ("stdout", "stderr"):
        content = _attempt_log_tail(publisher, source["attempt_id"], name)
        ref = f"{source['attempt_id']}--{name}"
        by_ref[ref] = EmbeddedInput.from_bytes(
            ref, "text/plain", b"" if content is None else content
        )
    return by_ref

def preflight_origin(request: AgentRequest, contract: Any, validation_attempt_id: str | None, hypothesis_refs: set[str], role: str, *, source_submit_command_id: str | None = None, experiment_id: str | None = None, source_submission: ValidatedSubmission | None = None) -> tuple[RendezvousOrigin, dict[str, Any]]:
    if source_submit_command_id is not None:
        if request.command.value != "EXPERIMENT_EXECUTION" or not isinstance(source_submit_command_id, str) or not source_submit_command_id or not isinstance(experiment_id, str) or not experiment_id or validation_attempt_id is not None:
            raise ValueError("submitted preflight requires its experiment/submit and no validation Attempt")
        identity = experiment_id
        source_refs = (f"{identity}--submission",) + (
            tuple(item.ref for item in _submission_source_inputs(f"{identity}--", source_submission))
            if source_submission is not None else (f"{identity}--experiment.py",))
    else:
        if not isinstance(validation_attempt_id, str) or not validation_attempt_id:
            raise ValueError("legacy preflight requires its validation Attempt")
        identity = request.attempt_id
        source_refs = (f"{identity}--response", f"{identity}--experiment.py", f"{validation_attempt_id}--observation")
    refs = tuple(ref for ref in request.immutable_context_refs if ref.endswith("--fact-goal") or "--fact-outcome-" in ref or ref in hypothesis_refs or ref.endswith("-config") or ref.endswith("--compute-capabilities") or ref.endswith("--resource-planning-skill") or ref.endswith("--resource-experience")) + source_refs
    if len(refs) > 16: raise ValueError("preflight context exceeds the bounded rendezvous contract")
    signal = ExpertSignal(schema_version=1, kind=ExpertSignalKind.HELP, target_role_hint=role, question=f"Preflight the Host-compiled source, frozen inputs and artifact contract for {request.component_id}.", context_refs=refs); origin = RendezvousOrigin(rendezvous_id=f"{identity}--preflight-rendezvous", requester_role=request.role, requester_slot=contract.role_ids.index(request.role) + 1, source_attempt_id=request.attempt_id, target_role=role, signal=signal, component_id=request.component_id, phase="preflight")
    return origin, {"rendezvous_id": origin.rendezvous_id, "requester_role": origin.requester_role, "requester_slot": origin.requester_slot, "source_attempt_id": origin.source_attempt_id, "target_role": origin.target_role, "expert_signal": signal.to_dict(), "component_id": request.component_id, "phase": "preflight"}

def _source(response: AgentResponse) -> bytes:
    return next(item.content_bytes() for item in response.proposed_workspace_artifacts if item.path == "experiment.py")


def _preflight_goal_input(item: EmbeddedInput) -> EmbeddedInput:
    try:
        value = json.loads(item.content_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("preflight goal fact is invalid JSON") from exc
    if not isinstance(value, Mapping) or value.get("fact_type") != "GoalDefined":
        raise ValueError("preflight goal input is not GoalDefined")
    payload = value.get("payload")
    contract = payload.get("research_contract") if isinstance(payload, Mapping) else None
    goal = contract.get("goal") if isinstance(contract, Mapping) else None
    if not isinstance(value.get("fact_id"), str) or not isinstance(goal, Mapping):
        raise ValueError("preflight GoalDefined lacks its scientific goal")
    projection = {
        "fact_id": value["fact_id"],
        "fact_type": "GoalDefined",
        "goal": goal,
    }
    return EmbeddedInput.from_bytes(item.ref, item.media_type, canonical_json(projection))


def _preflight_config_input(item: EmbeddedInput, component_id: str) -> EmbeddedInput:
    try:
        value = json.loads(item.content_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("preflight component config is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("preflight component config must be an object")
    component = value.get("component")
    protocol = value.get("protocol")
    required = value.get("required_experiment_spec")
    execution = value.get("execution")
    if (not isinstance(component, Mapping) or component.get("id") != component_id
            or not isinstance(protocol, Mapping) or not isinstance(required, Mapping)
            or not isinstance(execution, Mapping)):
        raise ValueError("preflight component config lacks its frozen current-component context")
    projected_required = {
        key: value for key, value in required.items() if key not in {"component", "protocol"}
    }
    projection: dict[str, Any] = {
        "execution": execution,
        "component": component,
        "protocol": protocol,
        "required_experiment_spec": projected_required,
    }
    delivery = value.get("delivery_contracts")
    if isinstance(delivery, Mapping):
        protocol_id = component.get("protocol")
        protocols = delivery.get("protocols")
        selected = protocols.get(protocol_id) if isinstance(protocols, Mapping) else None
        if selected is not None and not isinstance(selected, Mapping):
            raise ValueError("preflight delivery contract must be an object")
        projection["delivery_contract"] = {
            "enabled": delivery.get("enabled"),
            "schema_version": delivery.get("schema_version"),
            "protocol_id": protocol_id,
            "contract": selected,
        }
    return EmbeddedInput.from_bytes(item.ref, item.media_type, canonical_json(projection))

__all__ = [
    "RETRYABLE_AGENT_FAILURES",
    "RETRYABLE_INFRASTRUCTURE_FAILURES",
    "RETRYABLE_MODEL_FAILURES",
    "agent_failure_stage",
    "agent_retry_class",
    "agent_retry_inputs",
    "candidate_failure_retryable",
    "candidate_retry_inputs",
    "draft_repair_inputs",
    "infrastructure_retry_request",
    "predecessor_review_inputs",
    "preflight_origin",
    "rendezvous_inputs",
    "retryable_agent_validation_details",
    "unresolved_query_plan_validation",
    "validated_draft_source",
]


def expert_inputs(host, run_id: str, task: ResearchNodeTask | AuxiliaryTask,
                   component_id: str, role: str, method_family_id: str | None) -> tuple[EmbeddedInput, ...]:
    """Build expert inputs from the Host's existing accepted facts and bindings."""
    import uuid
    from .candidate_validation import component_result_contract
    from .compute_resources import ComputeCapabilities
    from .component_contract import component_is_production
    from .domain import ResourcePreference
    from .execution_workspace import execution_environment_contract, execution_read_root_projection
    from .expert_loop import ResearchNodeKey
    from .expert_batch import batch_limits
    from .grounding_context import build_grounding_context_bytes
    from .literature import LITERATURE_PACKET_MEDIA_TYPE, bounded_context_refs, expert_literature_query, grounding_literature_query
    from .outcome_adapter import OUTCOME_ADAPTER_VERSION
    from .profile_language import apply_profile_language
    from .profile_v4 import restrict_profile_capabilities
    from .workflow_host import OperationalHalt, _CPU_SANDBOX_PROFILE, _GPU_SANDBOX_PROFILE

    if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.PAPER_READ: return host.paper_reading.inputs(task)
    if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.GRAPH_EVOLVE: return host._manager_inputs(run_id, task)
    if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.FINAL_REPORT: return host._final_report_inputs(run_id, task)
    if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.OBSERVER: return host.interaction.inputs(run_id, task)
    if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.LEADER: return host.interaction.guidance_task_inputs(run_id, task)
    if isinstance(task, AuxiliaryTask) and task.kind in {AuxiliaryRequestKind.DEBATE, AuxiliaryRequestKind.DEBATE_JUDGE}: return host.interaction.debate.inputs(run_id, task)
    contract = host._research_contract(run_id); component = contract.component(component_id)
    if component is None: raise OperationalHalt(f"Agent action names unknown component {component_id!r}")
    goal = host.store.list_facts(run_id, "GoalDefined")[0]
    contract_document = contract.to_dict(); raw_component = next(
        item for item in contract_document["components"] if item["id"] == component_id)
    protocol = ({"result_kind": component.result_kind.value, "requirements": {}}
                if component.protocol is None else contract_document["protocols"][component.protocol])
    if isinstance(task, AuxiliaryTask):
        config: dict[str, Any] = {"execution": dict(host._run_config(run_id))["execution"],
            "components": contract_document["components"],
            "protocol_catalog": contract_document["protocols"],
            "delivery_contracts": contract_document.get("delivery_contracts", {}),
            "goal": contract_document["goal"], "grounding_scope": "role-neutral"}
        config_ref = f"{run_id}--grounding-config"
    else:
        config = {"execution": dict(host._run_config(run_id))["execution"],
            "component": raw_component, "protocol": protocol,
            "protocol_catalog": contract_document["protocols"],
            "delivery_contracts": contract_document.get("delivery_contracts", {}),
            "goal": contract_document["goal"], "expert_slot": role,
            "expert": next({"id": item["id"], "focus": item["focus"]}
                for item in contract_document["roles"] if item["id"] == role)}
        if component.required_inputs:
            required_family = host._required_input_method_family(run_id, component_id)
            if required_family is None: raise OperationalHalt("expert component lacks one ready required-input family")
            config["required_method_family_id"] = required_family
        config_ref = f"{run_id}--{component_id}--expert-config"
        if host._batch_enabled(run_id):
            config["inner_loop_limits"] = batch_limits(contract_document, role)
            config["effective_max_forks"] = host._effective_batch_forks(run_id, role)
            if task.key.node_kind is ResearchNodeKind.HYPOTHESIS_PROPOSAL:
                config["exploration_progress"] = host._batch_history(run_id, component_id, role).exploration_progress(
                    host._active_expert_chains(run_id, role))
    context_plan = {"production": component_is_production(contract_document, component_id)}
    _, root_environment = execution_read_root_projection(config["execution"], context_plan,
        generic=bool(raw_component.get("result", {}).get("deferred")) or host.shared_acceptance.enabled(run_id))
    if "HYPOLOOM_READ_ROOT_MAP" in root_environment:
        config["execution"] = {**config["execution"],
            "read_root_map": json.loads(root_environment["HYPOLOOM_READ_ROOT_MAP"]),
            "sandbox_read_roots": json.loads(root_environment["HYPOLOOM_READ_ROOTS"])}
    leader_inputs = (host.interaction.leader_inputs(run_id, task)
        if isinstance(task, ResearchNodeTask) and host._batch_enabled(run_id) else ())
    apply_profile_language(config, contract_document, task, designated_leader=bool(leader_inputs)); inputs = [EmbeddedInput.from_bytes(str(goal["fact_id"]), "application/json",
        canonical_json({"fact_id": goal["fact_id"], "fact_type": goal["fact_type"], "payload": goal["payload"]})),
        EmbeddedInput.from_bytes(config_ref, "application/json", canonical_json(config))]
    if isinstance(task, AuxiliaryTask):
        inputs.append(EmbeddedInput.from_bytes(f"{run_id}--grounding-context",
            "application/json", build_grounding_context_bytes(config["execution"])))
        if task.kind is AuxiliaryRequestKind.GROUNDING and int(host.store.get_run(run_id)["agent_aci_version"]) >= 14:
            maximum = config.get("node_runtime", {}).get("memory", {}).get("max_plugin_bytes", 1024 * 1024)
            plugin = host._plugin_binding(run_id, task, "grounding", "Ground the current commission and manage accepted research evidence.",
                max_bytes=maximum, owner_kind="grounding")
            inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--grounding-resources", "application/json",
                canonical_json({"plugin": plugin.to_dict()})))
        if task.kind is AuxiliaryRequestKind.GROUNDING and int(host.store.get_run(run_id)["agent_aci_version"]) >= 15:
            inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--grounding-dialogue", "application/json",
                canonical_json(host.grounding_dialogue.history(run_id, task.ordinal))))
    grounding = AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id=run_id)
    if not isinstance(task, AuxiliaryTask):
        inputs.append(host._expert_response_input(grounding))
        if host.shared_acceptance.enabled(run_id): inputs.append(host.shared_acceptance.input(run_id))
        if int(host.store.get_run(run_id)["agent_aci_version"]) >= 15:
            inputs.append(EmbeddedInput.from_bytes(f"{run_id}--grounding-dialogue", "application/json",
                canonical_json(host.grounding_dialogue.history(run_id, host._grounding_task(run_id).ordinal))))
        inputs.extend(host._expert_dependency_update_inputs(run_id, component_id))
        lineage = task.key
        if lineage.cycle > 1:
            if host._batch_enabled(run_id) and int(host.store.get_run(run_id)["agent_aci_version"]) >= 17:
                history = host._batch_history(run_id, component_id, role)
                if len(history.batches) < lineage.cycle - 1: raise OperationalHalt("successor node lacks its prior batch")
                prior = history.batches[lineage.cycle - 2]
                if prior.selection is None: raise OperationalHalt("successor node lacks accepted prior adjudication")
                selected = prior.selection.get("selected_tip")
                parent_hypothesis_id = None if selected is None else selected["node_id"]
                directed = host.interaction.directed_round(run_id, role, component_id, lineage.cycle)
                if directed is not None:
                    parent_hypothesis_id = directed["parent_hypothesis_id"]
                if lineage.chain is not None:
                    parent_hypothesis_id = history.hypothesis(lineage.cycle, lineage.chain)["payload"]["parent_hypothesis_id"]
                elif len(history.batches) < lineage.cycle and selected is None and directed is None:
                    if not host._unselected_batch_revision_ready(run_id, component_id, role): raise OperationalHalt("unselected revision controls are not closed")
                    context, _, _ = host._batch_context(run_id, component_id, role, lineage.cycle - 1)
                    inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--batch-context", "application/json", canonical_json(context)))
            else:
                chain = host._expert_hypothesis_chain(run_id, component_id, role)
                if len(chain) < lineage.cycle - 1: raise OperationalHalt("successor node lacks its parent hypothesis")
                parent_hypothesis_id = str(chain[lineage.cycle - 2]["hypothesis_id"])
            inputs.extend((
                EmbeddedInput.from_bytes(
                    f"{run_id}--{component_id}--{role}--cycle-{lineage.cycle}--lineage-context",
                    "application/json",
                    canonical_json({"cycle": lineage.cycle,
                        "parent_hypothesis_id": parent_hypothesis_id}),
                ),
                host._expert_response_input(ResearchNodeTask(key=ResearchNodeKey(
                    run_id=run_id, component_id=component_id,
                    expert_slot=role, cycle=lineage.cycle - 1,
                    node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE,
                ))),
            ))
        prior_keys: list[ResearchNodeKey] = []
        if lineage.node_kind is ResearchNodeKind.SELF_EVOLVE:
            report = ResearchNodeTask(key=replace(lineage, node_kind=ResearchNodeKind.CYCLE_REPORT))
            failure_input = host._expert_failure_input(run_id, report)
            if failure_input is None: prior_keys.append(report.key)
            else: inputs.append(failure_input)
        elif lineage.node_kind is ResearchNodeKind.HYPOTHESIS_PROPOSAL:
            if lineage.subkind is ResearchNodeSubkind.FORWARD:
                restart = host.interaction.debate.designated_round(run_id, component_id) if host._batch_enabled(run_id) else None
                if restart is not None and restart["cycle"] == lineage.cycle and restart.get("restart_from_hypothesis"):
                    source = host.interaction.debate.literature_source(run_id, role, component_id, restart["source_cycle"])
                    if source is None: raise OperationalHalt("Debate restart lacks its actual literature Attempt")
                    observation = json.loads(source["observation_json"]); original = AgentRequest.from_dict(observation["request"])
                    expected = ResearchNodeTask(key=replace(lineage, cycle=original.task_context.key.cycle, node_kind=ResearchNodeKind.LITERATURE_REVIEW, subkind=ResearchNodeSubkind.MAIN))
                    if (source["run_id"], source["attempt_id"], source["command_id"], source["state"], source["terminal_reason"], original.task_context) != (run_id, restart["source_literature_attempt_id"], restart["source_literature_command_id"], "CLOSED", "SUCCEEDED", expected):
                        raise OperationalHalt("Debate restart literature source differs from its authorized original cycle")
                    inputs.append(EmbeddedInput.from_bytes(source["attempt_id"] + "--response", "application/json", canonical_json(observation["response"])))
                else:
                    prior_keys.append(ResearchNodeKey(run_id=run_id, component_id=component_id,
                        expert_slot=role, cycle=lineage.cycle,
                        node_kind=ResearchNodeKind.LITERATURE_REVIEW))
            elif lineage.subkind is ResearchNodeSubkind.SYNTHESIS:
                prior_keys.extend((ResearchNodeKey(run_id=run_id, component_id=component_id,
                    expert_slot=role, cycle=lineage.cycle, node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL,
                    subkind=ResearchNodeSubkind.FORWARD), ResearchNodeKey(run_id=run_id,
                    component_id=component_id, expert_slot=role, cycle=lineage.cycle,
                    node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL, subkind=ResearchNodeSubkind.BACKWARD)))
        elif lineage.node_kind in {ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeKind.EXPERIMENT_EXECUTION, ResearchNodeKind.RESULT_ANALYSIS, ResearchNodeKind.HYPOTHESIS_UPDATE}:
            prior_keys.append(ResearchNodeKey(run_id=run_id, component_id=component_id,
                expert_slot=role, cycle=lineage.cycle, node_kind=ResearchNodeKind.HYPOTHESIS_PROPOSAL, subkind=ResearchNodeSubkind.SYNTHESIS))
            if lineage.node_kind is ResearchNodeKind.EXPERIMENT_EXECUTION:
                prior_keys.append(ResearchNodeKey(run_id=run_id, component_id=component_id,
                    expert_slot=role, cycle=lineage.cycle, node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
                    experiment_ordinal=lineage.experiment_ordinal, chain=lineage.chain))
            if lineage.node_kind is ResearchNodeKind.HYPOTHESIS_UPDATE:
                if host._batch_enabled(run_id): prior_keys.append(ResearchNodeKey(run_id=run_id, component_id=component_id,
                    expert_slot=role, cycle=lineage.cycle, node_kind=ResearchNodeKind.ADJUDICATE))
                else: prior_keys.append(ResearchNodeKey(run_id=run_id, component_id=component_id,
                    expert_slot=role, cycle=lineage.cycle, node_kind=ResearchNodeKind.RESULT_ANALYSIS,
                    experiment_ordinal=host._expert_experiment_ordinal(run_id, component_id, role, lineage.cycle)))
        inputs.extend(host._expert_response_input(ResearchNodeTask(key=key)) for key in prior_keys)
        if host._batch_enabled(run_id) and (history := host._batch_history(run_id, component_id, role)).latest is not None and len(history.batches) >= lineage.cycle:
            context, _, _ = host._batch_context(run_id, component_id, role, lineage.cycle)
            inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--batch-context", "application/json", canonical_json(context)))
            if lineage.chain is not None and lineage.experiment_ordinal > 1 and lineage.node_kind in {ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeKind.EXPERIMENT_EXECUTION}:
                prior = ResearchNodeTask(key=replace(lineage, node_kind=ResearchNodeKind.RESULT_ANALYSIS, experiment_ordinal=lineage.experiment_ordinal - 1))
                accepted = host._expert_success(prior)
                if accepted is None: raise OperationalHalt("scientific iteration lacks accepted prior analysis")
                prior_request = AgentRequest.from_dict(json.loads(accepted["observation_json"])["request"])
                outcome_input = next(item for item in prior_request.input_artifacts if item.ref.endswith("--current-outcome"))
                outcome = json.loads(outcome_input.content_bytes())
                inputs.extend((host._expert_response_input(prior), EmbeddedInput.from_bytes(f"{task.command_id}--iteration-context", "application/json",
                    canonical_json({"iteration_of_experiment_id": outcome["experiment_id"], "analysis_attempt_id": accepted["attempt_id"]}))))
    result_analysis = isinstance(task, ResearchNodeTask) and task.key.node_kind is ResearchNodeKind.RESULT_ANALYSIS
    if result_analysis:
        try: inputs.extend(result_analysis_inputs(host.store, host.publisher, run_id, task))
        except ValueError as exc: raise OperationalHalt(str(exc)) from exc
    relevant = ({component_id} if result_analysis else {component_id, *(str(value) for value in component.dependencies)}); fact_inputs = [EmbeddedInput.from_bytes(str(row["fact_id"]), "application/json",
        canonical_json({"fact_id": row["fact_id"], "fact_type": row["fact_type"], "payload": row["payload"]}))
        for row in host.store.list_facts(run_id)
        if row["fact_type"] != "GoalDefined" and row["payload"].get("component_id") in relevant
        and not (result_analysis and row["payload"].get("component_id") == component_id and row["fact_type"] in {"ExperimentPreregistered", "CanonicalOutcomeAccepted"})]; inputs.extend(fact_inputs); fact_refs = tuple(item.ref for item in fact_inputs)
    if isinstance(task, AuxiliaryTask):
        run = host.store.get_run(run_id)
        if run is None: raise OperationalHalt("grounding literature query lacks its run record")
        if task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN:
            inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--openalex-query-planning-skill", "text/markdown", host.query_skill)); query = grounding_literature_query(contract.document, latest_as_of=str(run["created_at"]).partition("T")[0], immutable_context_refs=bounded_context_refs(tuple(item.ref for item in inputs), fact_refs))
            ref = f"{task.command_id}--literature-query-template"
        elif task.kind is AuxiliaryRequestKind.GROUNDING:
            plan, query = host._expert_literature_query(AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id=run_id), "Grounding lacks a successful role-neutral query plan", "stored Grounding literature query plan is invalid")
            ref = f"{plan['attempt_id']}--literature-query"
        else: raise OperationalHalt("unsupported auxiliary task entered Grounding inputs")
        inputs.append(EmbeddedInput.from_bytes(ref, LITERATURE_PACKET_MEDIA_TYPE, canonical_json(query.to_dict())))
        if task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN:
            prior_plan = next((row for row in reversed(host._expert_attempts(task))
                if row["terminal_reason"] == "SUCCEEDED"), None)
            gap = None if prior_plan is None else host._grounding_query_gap(run_id, prior_plan)
            if gap is not None:
                observation = json.loads(gap["observation_json"])
                prior_request = AgentRequest.from_dict(observation["request"])
                query_input = next(item for item in prior_request.input_artifacts
                    if item.ref == f"{prior_plan['attempt_id']}--literature-query")
                inputs.append(EmbeddedInput.from_bytes(f"{gap['attempt_id']}--grounding-query-coverage-error",
                    "application/json", canonical_json({"source_attempt_id": gap["attempt_id"],
                        "query_plan_attempt_id": prior_plan["attempt_id"],
                        "rejected_query": json.loads(query_input.content_bytes()),
                        "failure_code": observation["failure_code"],
                        "diagnostic": observation.get("literature_diagnostic"),
                        "instruction": "Revise this query to cover the missing strata before Grounding retrieval."})))
    elif task.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW:
        if task.key.subkind is ResearchNodeSubkind.QUERY_PLAN:
            run = host.store.get_run(run_id)
            if run is None: raise OperationalHalt("literature query plan lacks its run record")
            inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--openalex-query-planning-skill", "text/markdown", host.query_skill))
            query = expert_literature_query(contract.document, component_id=component_id,
                role=role, latest_as_of=str(run["created_at"]).partition("T")[0],
                immutable_context_refs=bounded_context_refs(tuple(item.ref for item in inputs), fact_refs))
            ref = f"{task.command_id}--literature-query-template"
        else:
            plan_task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=task.key.cycle, node_kind=ResearchNodeKind.LITERATURE_REVIEW, subkind=ResearchNodeSubkind.QUERY_PLAN))
            plan, query = host._expert_literature_query(plan_task, "literature MAIN lacks a successful query plan", "stored literature query plan is invalid")
            ref = f"{plan['attempt_id']}--literature-query"
        inputs.append(EmbeddedInput.from_bytes(ref, LITERATURE_PACKET_MEDIA_TYPE, canonical_json(query.to_dict())))
        if task.key.subkind is ResearchNodeSubkind.QUERY_PLAN:
            prior_plan = next((row for row in reversed(host._expert_attempts(task))
                if row["terminal_reason"] == "SUCCEEDED"), None)
            gap = None if prior_plan is None else host._literature_query_gap(task, prior_plan)
            if gap is not None:
                observation = json.loads(gap["observation_json"])
                prior_request = AgentRequest.from_dict(observation["request"])
                query_input = next(item for item in prior_request.input_artifacts
                    if item.ref == f"{prior_plan['attempt_id']}--literature-query")
                inputs.append(EmbeddedInput.from_bytes(f"{gap['attempt_id']}--literature-query-coverage-error",
                    "application/json", canonical_json({"source_attempt_id": gap["attempt_id"],
                        "query_plan_attempt_id": prior_plan["attempt_id"],
                        "rejected_query": json.loads(query_input.content_bytes()),
                        "failure_code": observation["failure_code"],
                        "missing_strata": observation.get("literature_missing_strata", []),
                        "diagnostic": observation.get("literature_diagnostic"),
                        "instruction": "Revise this query to cover the missing strata before literature retrieval. Preserve the scientific topic and use broader methodological terms where necessary."})))
    if isinstance(task, ResearchNodeTask) and task.key.node_kind in {ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeKind.EXPERIMENT_EXECUTION}:
        if method_family_id is None: raise OperationalHalt("design node lacks a selected method family")
        config["required_experiment_spec"] = {"evaluator_version": OUTCOME_ADAPTER_VERSION,
            "workspace_artifacts": ["experiment.py", "implementation.patch"],
            "result_envelope": "result.json", "artifact_requirements": ["result.json"],
            "required_artifacts": list(component.required_artifacts),
            "result_contract": component_result_contract(raw_component, protocol), "execution_environment": {"cpu": execution_environment_contract(_CPU_SANDBOX_PROFILE), "gpu": execution_environment_contract(_GPU_SANDBOX_PROFILE)},
            "result_kind": component.result_kind.value, "component": raw_component, "protocol": protocol,
            "metric": None if component.metric is None else {"name": component.metric.name,
                "direction": component.metric.direction.value, "target": component.metric.target},
            "trial_plan": {"seeds": list(contract.trial_plan_for(component_id).seeds),
                "arms": list(contract.trial_plan_for(component_id).arms),
                "trial_ids": list(contract.trial_plan_for(component_id).trial_ids)},
            "resource_preferences": [item.value for item in ResourcePreference],
            "hypothesis_id": (host._batch_history(run_id, component_id, role).hypothesis(task.key.cycle, task.key.chain)["hypothesis_id"] if host._batch_enabled(run_id)
                else next(row["hypothesis_id"] for row in reversed(host.store.list_facts(run_id, "HypothesisProposed")) if row["payload"].get("component_id") == component_id and row["payload"].get("proposer_role") == role)),
            "method_family_id": method_family_id}
        if component.deferred_measurement:
            measure = host._hypothesis_measurement(run_id, component_id, role, task.key.cycle, chain=task.key.chain)
            config["required_experiment_spec"].update({"measurement": None if measure is None else measure.to_dict(),
                "metric": None if measure is None else measure.to_dict()["metric"], "workspace_artifacts": ["implementation.patch", *(() if measure is None else measure.parser_files)],
                "deferred_measurement": True})
        inputs[1] = EmbeddedInput.from_bytes(f"{run_id}--{component_id}--expert-config",
            "application/json", canonical_json(config))
        operational = dict(host._run_config(run_id)); capabilities = (
            host.compute_resources.snapshot() if operational["execution"]["allow_gpu"] else ComputeCapabilities(gpus=()))
        capabilities = restrict_profile_capabilities(contract.document, capabilities) if operational["execution"]["allow_gpu"] else capabilities
        busy = set(capabilities.busy_gpu_uuids) | set(host._busy_gpu_uuids())
        capabilities = ComputeCapabilities(gpus=capabilities.gpus,
            busy_gpu_uuids=tuple(sorted(gpu.uuid for gpu in capabilities.gpus if gpu.uuid in busy)))
        inputs.extend((EmbeddedInput.from_bytes(f"{run_id}--{component_id}--compute-capabilities",
            "application/json", capabilities.to_bytes()),
            EmbeddedInput.from_bytes(f"{run_id}--{component_id}--resource-planning-skill",
            "text/markdown", host.resource_skill),
            EmbeddedInput.from_bytes(f"{run_id}--{component_id}--resource-experience",
            "application/json", canonical_json(host._resource_experience(run_id, role, component_id, method_family_id)))))
    if host.shared_gpu is not None and isinstance(task, ResearchNodeTask) and task.key.node_kind in {ResearchNodeKind.CYCLE_REPORT, ResearchNodeKind.SELF_EVOLVE}:
        if method_family_id is not None:
            inputs.append(EmbeddedInput.from_bytes(f"{run_id}--{component_id}--resource-experience", "application/json", canonical_json(host._resource_experience(run_id, role, component_id, method_family_id))))
    if host._cycle_reports_enabled(run_id) and isinstance(task, ResearchNodeTask):
        from .expert_memory import ExpertMemory, report_path
        memory = ExpertMemory(host.state_root); memory_commit = memory.ensure(run_id, role)
        focus = next(item["focus"] for item in contract.document["roles"] if item["id"] == role)
        plugin_limit = config.get("node_runtime", {}).get("memory", {}).get("max_plugin_bytes", 1024 * 1024)
        plugin = host._plugin_binding(run_id, task, role, focus, max_bytes=plugin_limit)
        from .research_projection import freeze_role_view
        view_id = str(uuid.uuid5(uuid.NAMESPACE_URL, task.command_id))
        freeze_role_view(host.state_root, run_id, role, view_id)
        resources = {"plugin": plugin.to_dict(), "memory_commit": memory_commit, "research_view_id": view_id}
        report_component = component_id if host._batch_enabled(run_id) and sum(item.owner_role == role for item in contract.components) > 1 else None
        if report_component is not None: resources["report_component_id"] = report_component
        inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--expert-resources", "application/json", canonical_json(resources)))
        if task.key.node_kind in {ResearchNodeKind.CYCLE_REPORT, ResearchNodeKind.SELF_EVOLVE}:
            hypothesis_ids = (set(host._batch_history(run_id, component_id, role).batches[task.key.cycle - 1].members.values()) if host._batch_enabled(run_id)
                else {str(host._expert_hypothesis_chain(run_id, component_id, role)[task.key.cycle - 1]["hypothesis_id"])})
            facts = [row for row in host.store.list_facts(run_id, "HypothesisAssessed") if str(row["hypothesis_id"]) in hypothesis_ids]
            creations = [row for row in host.store.inspect(
                "SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))
                if (identity := json.loads(row["payload_json"])).get("component_id") == component_id and identity.get("role") == role]
            records = [host._experiment_tool_view(run_id, row["workflow_id"], include_log=False) for row in creations
                if json.loads(row["payload_json"]).get("expert_cycle", 1) == task.key.cycle]
            inputs.append(EmbeddedInput.from_bytes(f"{task.command_id}--cycle-assessments", "application/json", canonical_json({"cycle": task.key.cycle, "assessments": facts,
                "report_path": report_path(task.key.cycle, report_component), "experiment_records": records,
                "counts": {"cycle_experiments": len(records), "component_experiments_total": len(creations)}})))
            update = ResearchNodeTask(key=replace(task.key, node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
            failure_input = host._expert_failure_input(run_id, update) if host._batch_enabled(run_id) else None
            inputs.append(host._expert_response_input(update) if failure_input is None else failure_input)
    return tuple(inputs) + (leader_inputs + host.interaction.guidance_inputs(run_id, task) + host.interaction.debate.promotion_inputs(run_id, task) + host.interaction.debate.planning_inputs(run_id, task)
        if isinstance(task, ResearchNodeTask) and host._batch_enabled(run_id) else ())


def select_interaction_facts(rows: Iterable[Mapping[str, Any]], *, hypothesis_ids: Iterable[str],
                             reference_ids: Iterable[str] = ()) -> list[Mapping[str, Any]]:
    """Keep exact current/cited facts and their dependencies, not old payloads.

    Keep the immediate parents' evidence for baseline comparisons. Older
    hypotheses retain lineage definitions and explicitly cited evidence; existing
    cycle reports supply the historical narrative.
    This disposable view never changes, summarizes or accepts a scientific fact.
    """
    from .expert_interaction import _FACT_ENTITY_FIELDS

    rows = list(rows)
    payloads = {row["fact_id"]: json.loads(row["payload_json"]) if "payload_json" in row
        else row.get("payload", row) for row in rows}
    index = {identity: {identity} for identity in payloads}
    for identity, payload in payloads.items():
        for field in _FACT_ENTITY_FIELDS.get(payload["fact_type"], ()):
            if isinstance(payload.get(field), str):
                index.setdefault(payload[field], set()).add(identity)
    current = set(hypothesis_ids)
    hypotheses = set(current)
    hypotheses.update(p["parent_hypothesis_id"] for p in payloads.values()
        if p["fact_type"] == "HypothesisProposed" and p.get("hypothesis_id") in current
        and p.get("parent_hypothesis_id"))
    experiments = {p["experiment_id"] for p in payloads.values()
        if p["fact_type"] == "ExperimentPreregistered" and p.get("hypothesis_id") in hypotheses}
    selected = {identity for identity, payload in payloads.items()
        if payload["fact_type"] == "GoalDefined" or payload.get("hypothesis_id") in hypotheses
        or payload.get("experiment_id") in experiments}
    for ref in reference_ids:
        selected.update(index.get(ref, ()))
    pending = list(selected)
    while pending:
        payload = payloads[pending.pop()]
        if payload["fact_type"] == "GoalDefined":
            continue
        refs = [payload.get(field) for field in ("hypothesis_id", "parent_hypothesis_id", "experiment_id", "outcome_id", "artifact_id")]
        refs.extend(payload.get("evidence_ids", ()))
        refs.extend(payload.get("supporting_refs", ()))
        for ref in refs:
            if not isinstance(ref, str): continue
            for identity in index.get(ref, set()) - selected:
                selected.add(identity)
                pending.append(identity)
    return [row for row in rows if row["fact_id"] in selected]
