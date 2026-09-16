"""Bounded fresh-root module-chain rehearsals over retained Agent output.

The retained run is read-only.  A selected successful Agent response is
projected onto a fresh invocation identity, then the ordinary Workflow Host,
candidate validator, Runner, Outcome Adapter, Kernel and Oracle own every
downstream effect and record.  The resulting run is developer evidence, not a
scientific continuation of the retained run.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .agent_aci import (
    EXPERT_LOOP_ACI_SCHEMA_VERSION,
    MULTICHAIN_ACI_SCHEMA_VERSION,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    DeclaredEffect,
    WorkspaceArtifact,
    hypothesis_update_current_assessment,
    request_digest,
    required_effect,
    validate_postflight,
    with_output_digest,
)
from .compute_resources import LocalComputeResources
from .control_protocol import ControlRequest
from .debug_mode import build_debug_report, validate_attempt_payload
from .domain import AgentCommand, HypothesisAssessment
from .expert_loop import (
    AuxiliaryTask,
    HypothesisUpdateDisposition,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from .fake_agent import FakeAgent
from .literature import (
    LITERATURE_STRATA,
    FetchResponse,
    FixedLiteratureIO,
    LiteratureQuery,
    PdfExtraction,
    ScholarlySourceClass,
    SearchPaper,
)
from .oracle import Oracle
from .workflow_host import WorkflowHost

REHEARSAL_AGENT_MODEL_IDENTITY = "debug:module-rehearsal"
REHEARSAL_AGENT_ADAPTER_VERSION = "debug-rehearsal-fixture/1"
REHEARSAL_AGENT_PERMISSION_PROFILE = "debug-rehearsal-local-no-network/1"


class ModuleRehearsalError(RuntimeError):
    """A requested module rehearsal is unsafe, unsupported, or did not close."""


class _OfflineLiteratureFixture:
    """Produce bounded three-stratum packet inputs without network effects."""

    configuration = {
        "adapter": "debug-rehearsal-offline-literature/1",
        "host": "none",
        "timeout_seconds": 1.0,
        "max_response_bytes": 4_096,
    }

    def preflight(self) -> dict[str, object]:
        return {
            "adapter": self.configuration["adapter"],
            "qualified": True,
            "network_probe_performed": False,
        }

    def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
        latest_year = int(query.latest_as_of[:4])
        years = (max(1900, latest_year - 25), max(1900, latest_year - 10), latest_year)
        return tuple(
            SearchPaper(
                bibliographic_identity=f"openalex:W90000000{index}",
                title=f"Non-authoritative module rehearsal {stratum.value}",
                authors=("Hypoloom Debug Fixture",),
                year=year,
                language="en",
                landing_uri=f"https://literature.example.org/rehearsal/{index}",
                source_uri=f"https://literature.example.org/rehearsal/record/{index}",
                source_class=ScholarlySourceClass.JOURNAL,
                stratum=stratum,
                pdf_uri=None,
            )
            for index, (stratum, year) in enumerate(
                zip(LITERATURE_STRATA, years, strict=True), 1,
            )
        )

    def resolve(self, hostname: str) -> Sequence[str]:
        if hostname != "literature.example.org":
            raise AssertionError(f"unexpected offline rehearsal host {hostname!r}")
        return ("8.8.8.8",)

    def fetch(
        self,
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        raise AssertionError(f"offline rehearsal must not fetch {uri!r}")

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        raise AssertionError("offline rehearsal has no PDF bytes")


def _module_identity(request: AgentRequest) -> tuple[object, ...]:
    task = request.task_context
    if isinstance(task, ResearchNodeTask):
        key = task.key
        return (
            request.command.value,
            request.component_id,
            request.role,
            key.cycle,
            key.node_kind.value,
            key.subkind.value,
            key.experiment_ordinal,
        )
    if isinstance(task, AuxiliaryTask):
        return (
            request.command.value,
            task.kind.value,
            task.component_id,
            task.expert_slot,
            task.cycle,
            task.ordinal,
        )
    return (request.command.value, request.component_id, request.role)


def _candidate_response(
    request: AgentRequest,
    candidate: Mapping[str, Any],
    *,
    artifacts: tuple[WorkspaceArtifact, ...] = (),
) -> AgentResponse:
    response = AgentResponse(
        schema_version=request.schema_version,
        command=request.command,
        command_id=request.command_id,
        attempt_id=request.attempt_id,
        attempt_ordinal=request.attempt_ordinal,
        status=AgentStatus.SUCCEEDED,
        typed_candidate=dict(candidate),
        proposed_workspace_artifacts=artifacts,
        declared_effects=(DeclaredEffect(
            effect=required_effect(request.command),
            command_id=request.command_id,
            attempt_id=request.attempt_id,
        ),),
        diagnostics={"adapter_mode": "module_chain_rehearsal"},
        model_identity=REHEARSAL_AGENT_MODEL_IDENTITY,
        adapter_version=REHEARSAL_AGENT_ADAPTER_VERSION,
        token_usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        turn_count=1,
        cost="0",
        cost_currency="USD",
        input_digest=request_digest(request),
        output_digest="",
    )
    return with_output_digest(response)


def _fresh_query_candidate(
    request: AgentRequest,
    source_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    templates = tuple(
        item for item in request.input_artifacts
        if item.ref.endswith("--literature-query-template")
    )
    if len(templates) != 1:
        raise ModuleRehearsalError(
            "fresh query-plan request lacks exactly one production template"
        )
    try:
        template = LiteratureQuery.from_dict(
            json.loads(templates[0].content_bytes())
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ModuleRehearsalError("fresh literature query template is invalid") from exc
    candidate = template.to_dict()
    candidate["query"] = source_candidate.get("query")
    candidate["preferred_venues"] = source_candidate.get("preferred_venues", [])
    return candidate


class _ModuleRehearsalAgent(FakeAgent):
    """Local fixture projection plus a deterministic two-cycle continuation."""

    model_identity = REHEARSAL_AGENT_MODEL_IDENTITY
    adapter_version = REHEARSAL_AGENT_ADAPTER_VERSION
    permission_profile = REHEARSAL_AGENT_PERMISSION_PROFILE

    def __init__(
        self,
        *,
        source_request: AgentRequest,
        source_response: AgentResponse,
        source_hypothesis: Mapping[str, Any] | None,
    ) -> None:
        super().__init__()
        self.source_request = source_request
        self.source_response = source_response
        self.source_hypothesis = (
            None if source_hypothesis is None else dict(source_hypothesis)
        )
        self.rebound_fresh_attempt_ids: list[str] = []

    def _project_source(self, request: AgentRequest) -> AgentResponse:
        source_candidate = self.source_response.typed_candidate
        if not isinstance(source_candidate, Mapping):
            raise ModuleRehearsalError("source Agent response has no typed candidate")
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            candidate = dict(source_candidate)
            artifacts = self.source_response.proposed_workspace_artifacts
        elif (
            request.command is AgentCommand.LITERATURE_REVIEW
            and isinstance(request.task_context, ResearchNodeTask)
            and request.task_context.key.subkind is ResearchNodeSubkind.QUERY_PLAN
        ):
            candidate = _fresh_query_candidate(request, source_candidate)
            artifacts = ()
        else:
            candidate = dict(source_candidate)
            candidate["supporting_refs"] = list(request.immutable_context_refs)
            artifacts = ()
        response = _candidate_response(request, candidate, artifacts=artifacts)
        validate_postflight(request, response)
        self.rebound_fresh_attempt_ids.append(request.attempt_id)
        return response

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        source_task = self.source_request.task_context
        same_chain = (
            self.source_request.schema_version < MULTICHAIN_ACI_SCHEMA_VERSION
            or not isinstance(source_task, ResearchNodeTask)
            or isinstance(request.task_context, ResearchNodeTask)
            and request.task_context.key.chain == source_task.key.chain
        )
        if same_chain and _module_identity(request) == _module_identity(self.source_request):
            return self._project_source(request)
        response = super().invoke(request)
        if not isinstance(response, AgentResponse):
            return response
        candidate = dict(response.typed_candidate or {})
        task = request.task_context
        artifacts = response.proposed_workspace_artifacts
        if (
            self.source_hypothesis is not None
            and request.command is AgentCommand.HYPOTHESIS_PROPOSAL
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 1
            and task.key.subkind is ResearchNodeSubkind.SYNTHESIS
            and request.component_id == self.source_request.component_id
            and request.role == self.source_request.role
        ):
            hypothesis = {
                "component_id": request.component_id,
                "proposer_role": request.role,
                "method_family_id": self.source_hypothesis["method_family_id"],
                "claim": self.source_hypothesis["claim"],
                "assumptions": list(self.source_hypothesis["assumptions"]),
                "parent_hypothesis_id": None,
                "relation": None,
            }
            if self.source_hypothesis.get("measurement") is not None:
                hypothesis["measurement"] = self.source_hypothesis["measurement"]
            if request.schema_version >= MULTICHAIN_ACI_SCHEMA_VERSION:
                source_chain = source_task.key.chain if isinstance(source_task, ResearchNodeTask) else None
                candidate = {"schema_version": 1, "hypotheses": [{
                    "chain_id": source_chain or "1", "candidate": hypothesis,
                }]}
            else:
                candidate.update(hypothesis)
        if (
            request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 1
            and request.component_id == self.source_request.component_id
            and request.role == self.source_request.role
        ):
            artifacts = tuple(
                WorkspaceArtifact.from_bytes(
                    item.path,
                    item.content_bytes().replace(
                        b'"accuracy": 0.55', b'"accuracy": 0.50'
                    ),
                )
                for item in artifacts
            )
            if artifacts == response.proposed_workspace_artifacts:
                raise ModuleRehearsalError(
                    "query-plan rehearsal requires the bounded pilot metric fixture"
                )
        if (
            request.command is AgentCommand.HYPOTHESIS_UPDATE
            and isinstance(task, ResearchNodeTask)
            and request.component_id == self.source_request.component_id
            and request.role == self.source_request.role
        ):
            candidate["update_disposition"] = (
                HypothesisUpdateDisposition.REVISE_NEXT_CYCLE.value
                if task.key.cycle == 1
                and hypothesis_update_current_assessment(request)
                is not HypothesisAssessment.SUPPORTED
                else HypothesisUpdateDisposition.TERMINATE_LINEAGE.value
            )
        return _candidate_response(
            request,
            candidate,
            artifacts=artifacts,
        )


def _source_case(
    source_state_root: Path,
    source_run_id: str,
    source_attempt_id: str,
    *,
    design_only: bool,
) -> tuple[AgentRequest, AgentResponse, Mapping[str, Any] | None]:
    try:
        report = build_debug_report(
            source_state_root,
            source_run_id,
            attempt_id=source_attempt_id,
            include_payloads=True,
        )
    except (RuntimeError, ValueError) as exc:
        raise ModuleRehearsalError(str(exc)) from exc
    attempt = report["attempts"][0]
    validated = validate_attempt_payload(attempt)
    if not validated["valid"]:
        raise ModuleRehearsalError(
            "source Attempt fails current production validation: "
            + "; ".join(validated["errors"])
        )
    observation = attempt.get("observation")
    if (
        attempt.get("kind") != "AGENT"
        or attempt.get("terminal_reason") != "SUCCEEDED"
        or not isinstance(observation, Mapping)
        or not isinstance(observation.get("request"), Mapping)
        or not isinstance(observation.get("response"), Mapping)
    ):
        raise ModuleRehearsalError(
            "source must be one successful Agent Attempt with an exact response"
        )
    request = AgentRequest.from_dict(observation["request"])
    response = AgentResponse.from_dict(observation["response"])
    if request.schema_version not in {EXPERT_LOOP_ACI_SCHEMA_VERSION, MULTICHAIN_ACI_SCHEMA_VERSION}:
        raise ModuleRehearsalError("source must use supported expert-loop ACI 13 or 14")
    task = request.task_context
    supported = (
        request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
        and isinstance(task, ResearchNodeTask)
        and task.key.node_kind is ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT
        and task.key.cycle == 1
        and task.key.experiment_ordinal == 1
    ) or (
        request.command is AgentCommand.LITERATURE_REVIEW
        and isinstance(task, ResearchNodeTask)
        and task.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW
        and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN
        and task.key.cycle == 1
    )
    if design_only and request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
        raise ModuleRehearsalError("design rehearsal requires a design Agent Attempt")
    if not supported:
        raise ModuleRehearsalError(
            "first handler supports only cycle-one QUERY_PLAN or design Attempts"
        )
    validate_postflight(request, response)
    hypothesis: Mapping[str, Any] | None = None
    if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
        hypothesis_id = None
        if request.schema_version >= MULTICHAIN_ACI_SCHEMA_VERSION:
            contexts = tuple(item for item in request.input_artifacts
                if item.ref == f"{request.command_id}--batch-context")
            if task.key.chain is None or len(contexts) != 1:
                raise ModuleRehearsalError("source design lacks its exact chain/batch context")
            try:
                context = json.loads(contexts[0].content_bytes())
                if (contexts[0].media_type != "application/json" or not isinstance(context, Mapping)
                        or type(context.get("cycle")) is not int or context["cycle"] != task.key.cycle
                        or not isinstance(context.get("chains"), Mapping)):
                    raise ValueError("invalid batch context")
                hypothesis_id = context["chains"].get(task.key.chain)
                if not isinstance(hypothesis_id, str) or not hypothesis_id:
                    raise ValueError("source chain is absent from its batch")
            except (UnicodeDecodeError, TypeError, ValueError) as exc:
                raise ModuleRehearsalError("source design has invalid chain/batch binding") from exc
        related = report.get("related_records")
        facts = related.get("facts") if isinstance(related, Mapping) else None
        if not isinstance(facts, list):
            raise ModuleRehearsalError("source design lacks its related fact set")
        matches = [
            row for row in facts
            if isinstance(row, Mapping)
            and row.get("fact_type") == "HypothesisProposed"
            and isinstance(row.get("payload"), Mapping)
            and row["payload"].get("run_id") == request.run_id
            and row["payload"].get("component_id") == request.component_id
            and row["payload"].get("proposer_role") == request.role
            and row["payload"].get("method_family_id") == request.method_family_id
            and row["payload"].get("parent_hypothesis_id") is None
            and (hypothesis_id is None or row["payload"].get("hypothesis_id") == hypothesis_id)
        ]
        if len(matches) != 1:
            raise ModuleRehearsalError(
                "source design does not bind one cycle-one root hypothesis"
            )
        hypothesis = matches[0]["payload"]
    return request, response, hypothesis


def _empty_directory(path: Path) -> bool:
    return not path.exists() or path.is_dir() and next(path.iterdir(), None) is None


def _cuda_stdout_evidence(
    state_root: Path,
    attempt_id: str,
    observation: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    value = observation.get("stdout_path")
    if not isinstance(value, str):
        return None
    relative = Path(value)
    if (
        relative.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None
    try:
        staging = (state_root / "artifacts" / "result-staging").resolve(strict=True)
        log_root = (staging / attempt_id / "logs").resolve(strict=True)
        selected = (log_root / relative).resolve(strict=True)
    except OSError:
        return None
    if (
        not log_root.is_relative_to(staging)
        or not selected.is_relative_to(log_root)
        or not selected.is_file()
    ):
        return None
    try:
        lines = selected.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(candidate, Mapping)
            and candidate.get("cuda_available") is True
            and candidate.get("cuda_device_count") == 1
        ):
            return dict(candidate)
    return None


def rehearse_module_chain(
    *,
    source_state_root: str | Path,
    source_run_id: str,
    source_attempt_id: str,
    state_root: str | Path,
    run_id: str,
    create_run_payload: Mapping[str, Any],
    allow_agent: bool,
    allow_process: bool,
    allow_gpu: bool = False,
    compute_resources: LocalComputeResources | None = None,
    wall_seconds: float = 3_600.0,
    design_only: bool = False,
) -> dict[str, Any]:
    """Run one fresh deterministic continuation through ordinary authorities."""

    if allow_agent is not True or allow_process is not True:
        raise ModuleRehearsalError(
            "Agent and process effects must be explicitly authorized"
        )
    if (
        isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or not math.isfinite(float(wall_seconds))
        or wall_seconds <= 0
    ):
        raise ModuleRehearsalError("wall_seconds must be finite and positive")
    source_requested = Path(source_state_root).expanduser()
    destination_requested = Path(state_root).expanduser()
    if not source_requested.is_absolute() or not destination_requested.is_absolute():
        raise ModuleRehearsalError("source and rehearsal state roots must be absolute")
    source_root = source_requested.resolve()
    if os.path.lexists(destination_requested):
        raise ModuleRehearsalError("fresh rehearsal state root must not exist")
    try:
        destination_parent = destination_requested.parent.resolve(strict=True)
    except OSError as exc:
        raise ModuleRehearsalError(
            "fresh rehearsal state-root parent must already exist"
        ) from exc
    destination = destination_parent / destination_requested.name
    if destination == source_root or destination.is_relative_to(source_root) or source_root.is_relative_to(destination):
        raise ModuleRehearsalError("source and rehearsal state roots must be disjoint")
    if not isinstance(source_run_id, str) or not source_run_id:
        raise ModuleRehearsalError("source_run_id must be non-empty text")
    if not isinstance(run_id, str) or not run_id or run_id == source_run_id:
        raise ModuleRehearsalError("rehearsal run_id must be new non-empty text")
    if create_run_payload.get("run_id") != run_id:
        raise ModuleRehearsalError("CREATE_RUN payload does not bind the fresh run_id")
    execution = create_run_payload.get("execution")
    if not isinstance(execution, Mapping) or execution.get("allow_gpu") is not allow_gpu:
        raise ModuleRehearsalError("GPU authorization differs from CREATE_RUN")
    resources = compute_resources or LocalComputeResources()
    if bool(resources.allowed_gpu_uuids) != allow_gpu:
        raise ModuleRehearsalError(
            "GPU authorization requires an exact non-empty compute allowlist"
            if allow_gpu
            else "CPU rehearsal must not configure a GPU allowlist"
        )
    source_request, source_response, source_hypothesis = _source_case(
        source_root,
        source_run_id,
        source_attempt_id,
        design_only=design_only,
    )
    try:
        destination.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise ModuleRehearsalError(
            "fresh rehearsal state root was claimed concurrently"
        ) from exc
    except OSError as exc:
        raise ModuleRehearsalError(
            "fresh rehearsal state root could not be created atomically"
        ) from exc
    agent = _ModuleRehearsalAgent(
        source_request=source_request,
        source_response=source_response,
        source_hypothesis=source_hypothesis,
    )
    host = WorkflowHost(
        destination,
        compute_resources=resources,
        literature_io=FixedLiteratureIO(_OfflineLiteratureFixture()),
        local_agent=agent,
    )
    fresh_attempts: tuple[Mapping[str, Any], ...] = ()
    facts: tuple[Mapping[str, Any], ...] = ()
    run_state: str | None = None
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"{run_id}-module-rehearsal-create-1",
            expected_revision=0,
            command="CREATE_RUN",
            payload=dict(create_run_payload),
        ))
        if created.status != "accepted":
            raise ModuleRehearsalError(
                f"fresh Host rejected CREATE_RUN: {created.to_mapping()}"
            )
        deadline = time.monotonic() + float(wall_seconds)
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                run_state = str(head["state"])
                break
            time.sleep(0.005)
        else:
            raise ModuleRehearsalError("fresh module rehearsal exceeded wall_seconds")
        fresh_attempts = tuple(
            row for row in host.store.list_attempts() if row["run_id"] == run_id
        )
        facts = tuple(host.store.list_facts(run_id))
    except ModuleRehearsalError:
        raise
    except Exception as exc:
        raise ModuleRehearsalError(
            f"fresh module rehearsal failed: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        host.close()

    oracle = Oracle(destination / "state.sqlite", destination / "artifacts").audit(run_id)
    all_specs = tuple(
        row for row in facts if row["fact_type"] == "ExperimentPreregistered"
    )
    all_execution_ids = {str(row["execution_id"]) for row in all_specs}
    all_scientific_processes = tuple(
        row for row in fresh_attempts
        if row["kind"] == "PROCESS"
        and str(row["execution_id"]) in all_execution_ids
    )
    validation_processes = tuple(
        row for row in fresh_attempts
        if row["kind"] == "PROCESS"
        and str(row["execution_id"]) not in all_execution_ids
    )
    specs = tuple(
        row for row in all_specs
        if row["payload"].get("component_id") == source_request.component_id
    )
    execution_ids = {str(row["execution_id"]) for row in specs}
    scientific_processes = tuple(
        row for row in all_scientific_processes
        if str(row["execution_id"]) in execution_ids
    )
    gpu_processes: list[dict[str, Any]] = []
    for row in scientific_processes:
        observation = json.loads(row["observation_json"])
        usage = observation.get("resource_usage")
        gpu_uuid = usage.get("gpu_uuid") if isinstance(usage, Mapping) else None
        cuda = _cuda_stdout_evidence(
            destination,
            str(row["attempt_id"]),
            observation,
        )
        if isinstance(gpu_uuid, str):
            gpu_processes.append({
                "attempt_id": row["attempt_id"],
                "gpu_uuid": gpu_uuid,
                "terminal_reason": row["terminal_reason"],
                "cuda_evidence": cuda,
            })
    hypotheses = tuple(
        row for row in facts
        if row["fact_type"] == "HypothesisProposed"
        and row["payload"].get("component_id") == source_request.component_id
        and row["payload"].get("proposer_role") == source_request.role
    )
    edges = [
        {
            "parent_hypothesis_id": row["payload"].get("parent_hypothesis_id"),
            "hypothesis_id": row["hypothesis_id"],
            "relation": row["payload"].get("relation"),
        }
        for row in hypotheses
        if row["payload"].get("parent_hypothesis_id") is not None
    ]
    verdicts = tuple(row for row in facts if row["fact_type"] == "GoalVerdictRecorded")
    outcomes = tuple(
        row for row in facts
        if row["fact_type"] == "CanonicalOutcomeAccepted"
        and row["payload"].get("component_id") == source_request.component_id
    )
    hypothesis_ids = {str(row["hypothesis_id"]) for row in hypotheses}
    assessments = tuple(
        row for row in facts
        if row["fact_type"] == "HypothesisAssessed"
        and str(row["hypothesis_id"]) in hypothesis_ids
    )
    residue_free = (
        oracle.open_attempt_count == 0
        and oracle.pending_effect_count == 0
        and not oracle.owned_process_residue
        and _empty_directory(destination / "artifacts" / "work")
        and _empty_directory(destination / "git-authoring-worktrees")
    )
    gpu_closed = bool(gpu_processes) and any(
        item["terminal_reason"] == "SUCCEEDED"
        and isinstance(item["cuda_evidence"], Mapping)
        for item in gpu_processes
    )
    graph_closed = any(
        edge["relation"] == "revises" for edge in edges
    )
    two_cycle_closed = (
        len(hypotheses) >= 2
        and len(specs) >= 2
        and len(outcomes) >= 2
        and len(assessments) >= 2
        and len(verdicts) == 1
        and sum(
            row["terminal_reason"] == "SUCCEEDED"
            for row in scientific_processes
        ) >= 2
    )
    source_output_rebound_once = len(agent.rebound_fresh_attempt_ids) == 1
    handler = (
        "design-to-closure/1"
        if source_request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
        else "query-plan-to-closure/1"
    )
    result = {
        "non_authoritative": True,
        "mode": "MODULE_CHAIN_REHEARSAL",
        "handler": handler,
        "source": {
            "state_root": str(source_root),
            "run_id": source_run_id,
            "attempt_id": source_attempt_id,
            "command": source_request.command.value,
        },
        "fresh_run": {
            "state_root": str(destination),
            "run_id": run_id,
            "state": run_state,
            "adapter_version": REHEARSAL_AGENT_ADAPTER_VERSION,
            "source_output_rebound_once": source_output_rebound_once,
            "rebound_fresh_attempt_ids": list(agent.rebound_fresh_attempt_ids),
        },
        "closure": {
            "complete": (
                run_state == "COMPLETED"
                and oracle.ok
                and residue_free
                and graph_closed
                and two_cycle_closed
                and source_output_rebound_once
                and (not allow_gpu or gpu_closed)
            ),
            "oracle_ok": oracle.ok,
            "oracle_issues": [issue.to_dict() for issue in oracle.issues],
            "residue_free": residue_free,
            "scientific_process_attempt_ids": [
                row["attempt_id"] for row in scientific_processes
            ],
            "validation_process_attempt_ids": [
                row["attempt_id"] for row in validation_processes
            ],
            "gpu_requested": allow_gpu,
            "gpu_closed": gpu_closed,
            "gpu_processes": gpu_processes,
            "hypothesis_edges": edges,
            "graph_evolved": graph_closed,
            "two_cycle_closed": two_cycle_closed,
            "fresh_fact_counts": {
                "hypotheses": len(hypotheses),
                "experiment_specs": len(specs),
                "outcomes": len(outcomes),
                "assessments": len(assessments),
                "verdicts": len(verdicts),
            },
            "verdict": None if not verdicts else verdicts[-1]["payload"],
        },
    }
    if result["closure"]["complete"] is not True:
        raise ModuleRehearsalError(
            "fresh module rehearsal did not close its required graph/process/"
            f"Oracle boundary; retained evidence is at {destination}"
        )
    return result


def rehearse_design_chain(**kwargs: Any) -> dict[str, Any]:
    """Qualified cycle-one design boundary wrapper for the first handler."""

    return rehearse_module_chain(**kwargs, design_only=True)


__all__ = [
    "ModuleRehearsalError",
    "REHEARSAL_AGENT_ADAPTER_VERSION",
    "rehearse_design_chain",
    "rehearse_module_chain",
]
