"""Resident sole-writer Workflow Host for the bounded Hypoloom workflow."""
from __future__ import annotations
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import sqlite3
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, ContextManager, Mapping
from .agent_aci import (CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION as EXPERT_LOOP_ACI_SCHEMA_VERSION, DEFAULT_REAL_AGENT_BUDGET, AgentACIError, AgentBudget, AgentRequest, AgentResponse, AgentStatus, EmbeddedInput, GroundingCandidate, LiteratureSummaryCandidate, canonical_json, request_digest, required_effect, required_output_schema, supports_experiment_submission, supports_grounding_web, validate_postflight, validate_submission_snapshot, with_output_digest)
from .agent_capabilities import capabilities_for
from .agent_sessions import (AgentSessions, SessionFinishError, archived_web_results, node_memory_limit, retrieved_session_urls, session_resume_allowed)
from .context_management import continuation_observation
from .agent_budget import AgentChargeCache, agent_attempt_limits, agent_attempts_exhausted, effect_attempt_cap, fold_agent_attempts, profile_agent_budget, profile_role_cap, remaining_agent_budget, role_agent_budget_state, usage_projection, validate_new_run_budget, wallclock_expired, wallclock_remaining_seconds
from .agent_context import RETRYABLE_AGENT_FAILURES as _RETRYABLE_AGENT_FAILURES, RETRYABLE_MODEL_FAILURES as _RETRYABLE_MODEL_FAILURES, agent_failure_stage as _agent_failure_stage, agent_retry_class as _agent_retry_class, agent_retry_inputs, candidate_failure_retryable, draft_repair_inputs, infrastructure_retry_request, preflight_origin, rendezvous_inputs, result_analysis_inputs, retryable_agent_validation_details, unresolved_query_plan_validation, validated_draft_source
from .artifacts import (AgentArtifactKey, ArtifactDurabilityError, ArtifactError, ArtifactKey, ArtifactPublication, ArtifactPublisher, inventory_relative_paths)
from .backup_restore import create_quiesced_backup
from .host_settings import HostConfigurationError, PRIVATE_SETTINGS_NAME, prepare_host_settings, validate_private_settings, provider_retry_status, provider_connection
from .candidate_validation import assert_candidate_patch_compiles, bounded_diagnostics, component_result_contract, prepare_candidate_validation
from .control_protocol import ControlRequest, ControlResponse, ProtocolError, receive_message, send_message, socket_address
from .claude_agent_host import (CLAUDE_AGENT_GIT_TOOL_PERMISSION_PROFILE, CLAUDE_AGENT_HOST_VERSION, CLAUDE_AGENT_STRUCTURED_OUTPUT_FAILED_VALIDATION_CAP, CLAUDE_AGENT_TOOL_PERMISSION_PROFILE, CLAUDE_AGENT_TRANSPORT_RETRIES, AgentDeadlineExpired, AgentHostError, AgentHostTerminalReason, ClaudeAgentHost, RunningClaudeAgent, SandboxUnavailable, _cleanup_tool_workspace, validate_claude_model_selector)
from .compute_resources import ComputeCapabilities, GpuPressureTracker, LocalComputeResources, ResourceUnavailable, gpu_memory_reserve_mib, gpu_pressure_attribution, gpu_pressure_payload
from .gpu_pool import PoolError, resource_wait_message
from .component_contract import component_execution_blockers, component_execution_plan, component_is_production
from .measurement_contract import ScientificMeasurement, assert_frozen_measurement
from .collaboration import (ExpertSignal, ExpertSignalKind, RendezvousDisposition, RendezvousOrigin, RendezvousRequest, derive_rendezvous_records)
from .contract_normalization import EXPERT_LOOP_RESEARCH_LOOP
from .diagnostics import bounded_diagnostic_text
from .runtime_diagnostics import record_host_exception
from .domain import (AgentCommand, ArtifactId, AssessmentId, AttemptTerminalReason, CanonicalOutcomeAccepted, ComponentId, ComponentResultKind, EvidenceId, EvidenceLinked, ExecutionId, ExperimentId, ExperimentPreregistered, FactId, GoalDefined, GoalId, GoalVerdict, GoalVerdictRecorded, HypothesisAssessed, HypothesisAssessment, HypothesisId, HypothesisProposed, ExecutionContract, LogicalArtifact, MethodFamilyId, ProducerOutcomeBinding, ResearchContract, ResourceGrant, ResourceIntent, ResourceKind, ResourcePreference, OutcomeId, RunId, RunVersion, ScientificOutcome, VerdictId, REVISIONABLE_INTEGRITY_VALIDATION_CODES)
from .fake_agent import FakeAgent, FakeAgentMode
from .execution_workspace import (EXECUTION_WORKSPACE_VERSION, ExecutionWorkspace, ExecutionWorkspaceError, GitTargetBinding, PreparedAgentWorkspace, PreparedExecution, TargetPreparationError, bind_clean_git_target, execution_environment_contract, execution_read_root_projection, verify_downstream_implementation_contracts)
from .expert_loop import (AuxiliaryRequestKind, AuxiliaryTask, AuxiliaryTaskView, DerivedWorkState, ExpertLineage, ExpertLineageInput, HypothesisUpdateDisposition, ResearchNodeKey, ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask, ResearchNodeView, ScientificNodeRefs, _EXPERT_NODE_ORDER, reduce_expert_lineage)
from .expert_batch import BatchHistory, batch_limits, supports_hypothesis_batches, proposal_siblings, sibling_proposal_failed
from .git_authoring_workspace import (GIT_AUTHORING_WORKSPACE_VERSION, GitAuthoringPreparationError, GitAuthoringIntegrityError, GitAuthoringWorkspace, PreparedGitAuthoringWorkspace)
from .lifecycle import Command, ExperimentState, Machine, RunState, WORKFLOW_DEFINITION_VERSION, validate_transition
from .literature import (LITERATURE_PACKET_MEDIA_TYPE, FixedLiteratureIO, LiteratureCoverageError, LiteratureError, LiteratureQuery, LiteratureRead, admit_literature_query_plan, bounded_context_refs, expert_literature_query, grounding_literature_query, literature_failure_retryable, literature_read_diagnostics)
from .monitor import build_status_projection
from .outcome_adapter import (OUTCOME_ADAPTER_VERSION, ComponentResultValidationError, repairable_component_result_error, validate_component_result, validate_measurement_result)
from .oracle import audit_all_runs
from .policy import ComponentSnapshot, PolicyDecision, PolicySnapshot, ProposalSnapshot, ResearchPolicy, _REVISION_BREAKER_MAX; from .profile_language import apply_profile_language
from .recovery import abandoned_attempt_to_lost
from .research_kernel import ResearchKernel, ResearchValidationError
from .resource_experience import RESOURCE_EXPERIENCE_VERSION, summarize_resource_experience
from .profile_v4 import request_literature_tools, restrict_profile_capabilities
from .runner import RUNNER_VERSION, LocalRunner, ProcessRequest, RunningProcess, process_group_exists
from .state_store import HostWriterCapability, IdentityConflict, RevisionConflict, StateStore, StateTransaction
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TERMINAL_RUNS = {state.value for state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)}
_VERSION_FIELDS = tuple(RunVersion.__dataclass_fields__)
_PRESSURE_TERMINATE_GRACE_SECONDS = 30.0
_CONFIG = {"agent_attempt_cap": 2, "agent_infrastructure_attempt_cap": 3, "process_attempt_cap": 2, "process_timeout": 10.0, "evaluator_retry_delay": 1.0}
_RESOURCE_SKILL = Path(__file__).resolve().parent / "skills/experiment-resource-planning/SKILL.md"; _RESOURCE_SKILL_VERSION = "experiment-resource-planning/6"; _QUERY_SKILL = Path(__file__).resolve().parent / "skills/openalex-query-planning/SKILL.md"; _QUERY_SKILL_VERSION = "openalex-query-planning/6"
_CPU_SANDBOX_PROFILE = "bwrap-cpu-no-network/2"; _GPU_SANDBOX_PROFILE = "bwrap-gpu-single-no-network/1"
_GPU_MULTI_SANDBOX_PROFILE = "bwrap-gpu-multi-no-network/1"
def _grant_profile(grant: ResourceGrant | ResourceIntent) -> str:
    count = grant.gpu_count if isinstance(grant, ResourceIntent) else len(grant.device_uuids)
    return _GPU_MULTI_SANDBOX_PROFILE if count > 1 else _GPU_SANDBOX_PROFILE if grant.kind is ResourceKind.GPU else _CPU_SANDBOX_PROFILE
def _resource_contract_fields(resource: ResourceGrant | ResourceIntent) -> dict[str, Any]:
    return {"resource_intent" if isinstance(resource, ResourceIntent) else "resource_grant": resource}
def _contract_gpu(contract: Mapping[str, Any]) -> bool:
    return (contract.get("resource_intent") or contract.get("resource_grant") or {}).get("kind") == "GPU"
def _grant_identity(grant: ResourceGrant) -> dict[str, Any]:
    return {key: value for key, value in grant.to_dict().items() if key != "kind"}
_FATAL_SQLITE = {sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY}
def _leader_round_matches(leader: Mapping[str, Any] | None, role: str, component_id: str, cycle: int) -> bool:
    return leader is not None and (leader["role"], leader["component_id"], leader["cycle"]) == (role, component_id, cycle)

def _uses_expert_loop(contract: ResearchContract) -> bool:
    controls = contract.document.get("methodology", {}).get("controls", {})
    return isinstance(controls, Mapping) and controls.get("research_loop") == EXPERT_LOOP_RESEARCH_LOOP
_EXPERT_COMMAND_BY_NODE = {
    ResearchNodeKind.LITERATURE_REVIEW: AgentCommand.LITERATURE_REVIEW,
    ResearchNodeKind.HYPOTHESIS_PROPOSAL: AgentCommand.HYPOTHESIS_PROPOSAL,
    ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT: AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    ResearchNodeKind.EXPERIMENT_EXECUTION: AgentCommand.EXPERIMENT_EXECUTION,
    ResearchNodeKind.RESULT_ANALYSIS: AgentCommand.RESULT_ANALYSIS,
    ResearchNodeKind.HYPOTHESIS_UPDATE: AgentCommand.HYPOTHESIS_UPDATE,
    ResearchNodeKind.CYCLE_REPORT: AgentCommand.CYCLE_REPORT,
    ResearchNodeKind.ADJUDICATE: AgentCommand.ADJUDICATE,
    ResearchNodeKind.SELF_EVOLVE: AgentCommand.SELF_EVOLVE,
}
class HostError(RuntimeError): pass
class OperationalHalt(HostError): pass
class PinnedRuntimeError(OperationalHalt, HostConfigurationError): pass
class NonQuiescentBackup(HostError): pass
def _canonical(value: Mapping[str, Any]) -> bytes: return canonical_json(value)
def _trusted_execution_files(plan: Mapping[str, Any]) -> dict[str, bytes]:
    if plan.get("batch") is not True: return {}
    package = Path(__file__).resolve().parent
    return {str(plan["source_relative_path"]): (package / "trial_driver.py").read_bytes(), ".hypoloom/data_contracts.py": (package / "data_contracts.py").read_bytes()}
def _component_plan(document: Mapping[str, Any], component_id: str, family: str, patch: bytes | None, gpu: bool) -> dict[str, object]:
    return component_execution_plan(document, component_id, method_family_id=family, implementation_sha256=hashlib.sha256(patch).hexdigest() if patch is not None else "0" * 64, device="cuda" if gpu else "cpu")
def installed_run_version(configuration: Mapping[str, Any] | None = None, runtime_files: tuple[Path, ...] = (), *, private_settings_path: Path | None = None) -> RunVersion:
    package = Path(__file__).resolve().parent; digest = hashlib.sha256()
    if private_settings_path is not None:
        if private_settings_path not in runtime_files: raise ValueError("private settings must be included in runtime_files")
        validate_private_settings(private_settings_path)
    for part in (sys.version, str(Path(sys.executable).resolve()), sqlite3.sqlite_version):
        raw = part.encode(); digest.update(len(raw).to_bytes(8, "big") + raw)
    files = sorted(package.glob("*.py")) + sorted((package / "migrations").glob("*.sql"))
    files += sorted(path for seed in ("expert-seed", "grounding-seed", "research-workflow") for path in (package / "skills" / seed).rglob("*") if path.is_file())
    environment = package.parent / "environment.yml"; files += [environment] if environment.is_file() else []
    for path in (*files, *runtime_files):
        if path == private_settings_path: name = PRIVATE_SETTINGS_NAME.encode()
        else:
            try: name = path.relative_to(package.parent).as_posix().encode()
            except ValueError: name = path.resolve().as_posix().encode()
        digest.update(len(name).to_bytes(8, "big") + name); digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for content in iter(lambda: stream.read(1024 * 1024), b""): digest.update(content)
    resolved_configuration = {**_CONFIG, **dict(configuration or {})}; return RunVersion(workflow_definition_version=WORKFLOW_DEFINITION_VERSION, research_contract_version="hypoloom-research-v12" if resolved_configuration.get("gpu_pool") is not None else "hypoloom-research-v10", evaluator_version=OUTCOME_ADAPTER_VERSION, agent_aci_version=str(EXPERT_LOOP_ACI_SCHEMA_VERSION), runner_version=RUNNER_VERSION, artifact_result_schema_version="2", runtime_environment_id=f"sha256:{digest.hexdigest()}",
        configuration_id=f"sha256:{hashlib.sha256(_canonical(resolved_configuration)).hexdigest()}", sqlite_runtime_version=sqlite3.sqlite_version)
def _ids(run_id: str, component: int = 1, proposer: int = 1, experiment_ordinal: int = 1) -> dict[str, str]:
    if isinstance(experiment_ordinal, bool) or not isinstance(experiment_ordinal, int) or experiment_ordinal < 1: raise ValueError("experiment_ordinal must be a positive integer")
    base = f"{run_id}--component-{component}"; revision = "" if experiment_ordinal == 1 else f"-{experiment_ordinal}"
    return {"goal": f"{run_id}--goal", "hypothesis": f"{base}--hypothesis-{proposer}", "experiment": f"{base}--experiment{revision}", "execution": f"{base}--execution{revision}", "outcome": f"{base}--outcome{revision}", "evidence": f"{base}--evidence{revision}", "assessment": f"{base}--assessment", "verdict": f"{run_id}--verdict-1"}
def _fact_id(run_id: str, name: str, *identity: int) -> FactId: return FactId(f"{run_id}--fact-{name}{''.join(f'-{value}' for value in identity)}")
def _component_ids(run_id: str, contract: ResearchContract, component_id: str, proposer_role: str | None = None, *, experiment_ordinal: int = 1) -> tuple[dict[str, str], int, int]:
    try: component_index = next(index for index, item in enumerate(contract.components, 1) if item.component_id == component_id)
    except StopIteration as exc: raise OperationalHalt(f"unknown component {component_id!r}") from exc
    component = contract.components[component_index - 1]; role = proposer_role or component.owner_role
    try: proposer_index = component.proposer_roles.index(role) + 1
    except ValueError as exc: raise OperationalHalt(f"role {role!r} cannot propose for {component_id!r}") from exc
    return _ids(run_id, component_index, proposer_index, experiment_ordinal), component_index, proposer_index
class WorkflowHost:
    """Serialize control commands, recovery, dispatch and state transactions."""
    def __init__(self, state_root: Path, *, fault_point: str | None = None, fake_agent_mode: FakeAgentMode | str = FakeAgentMode.VALID,
                 claude_agent_host: ClaudeAgentHost | None = None, claude_model: str | None = None, agent_budget: AgentBudget | None = None,
                 compute_resources: LocalComputeResources | None = None, provider_environment: Mapping[str, str] | None = None, gpu_pressure_grace_seconds: float = 5.0,
                 literature_io: FixedLiteratureIO | None = None, local_agent: FakeAgent | None = None,
                 grounding_web: Any = None, institutional_access: Any = None, gpu_pool_socket: Path | None = None,
                 gpu_pool_namespace: str | None = None, gpu_pool_server_uid: int | None = None,
                 private_settings_path: Path | None = None) -> None:
        self.state_root = state_root; self.generation = str(uuid.uuid4())
        self.private_settings_path = private_settings_path
        if private_settings_path is not None and (private_settings_path != state_root / PRIVATE_SETTINGS_NAME or claude_agent_host is None or claude_agent_host.settings_path != private_settings_path):
            raise ValueError("private settings must bind this Host's private companion")
        self.claude_agent_host = claude_agent_host; self.claude_model = validate_claude_model_selector(claude_model) if claude_model is not None else None
        if (claude_agent_host is None) != (claude_model is None) or claude_agent_host is not None and local_agent is not None: raise ValueError("Claude, model and local Agent adapter configuration is inconsistent")
        if provider_environment is not None and not isinstance(provider_environment, Mapping): raise TypeError("provider_environment must be a mapping")
        if isinstance(gpu_pressure_grace_seconds, bool) or not isinstance(gpu_pressure_grace_seconds, (int, float)) or not math.isfinite(float(gpu_pressure_grace_seconds)) or gpu_pressure_grace_seconds < 0: raise ValueError("gpu_pressure_grace_seconds must be finite and non-negative")
        self._provider_environment = MappingProxyType(dict(provider_environment or {})); self.gpu_pressure_grace_seconds = float(gpu_pressure_grace_seconds)
        self._provider_connections: dict[str, tuple[str, dict[str, str] | None, str | None]] = {}
        self.observer_services = None
        if claude_agent_host is None and self._provider_environment: raise ValueError("provider environment requires the Claude Agent Host")
        self.agent_budget = agent_budget or (DEFAULT_REAL_AGENT_BUDGET if claude_agent_host else AgentBudget(max_turns=2, max_tokens=512, max_output_bytes=16_384, wall_seconds=5, max_cost="0"))
        mode = FakeAgentMode(fake_agent_mode)
        self.agent = local_agent or FakeAgent(mode); self.local_agent_model = self.agent.model_identity; self.local_agent_adapter = self.agent.adapter_version; self.local_agent_profile = self.agent.permission_profile
        self.agent_configuration = {"agent_adapter": CLAUDE_AGENT_HOST_VERSION if claude_agent_host else self.local_agent_adapter, "agent_model": self.claude_model or self.local_agent_model, "agent_budget": self.agent_budget.to_dict(), "agent_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE if claude_agent_host else self.local_agent_profile, "agent_structured_output_failed_validation_default": CLAUDE_AGENT_STRUCTURED_OUTPUT_FAILED_VALIDATION_CAP if claude_agent_host else None, "agent_transport_retries": CLAUDE_AGENT_TRANSPORT_RETRIES if claude_agent_host else None, "agent_workspace": EXECUTION_WORKSPACE_VERSION if claude_agent_host else None, "agent_git_profile": CLAUDE_AGENT_GIT_TOOL_PERMISSION_PROFILE if claude_agent_host else None, "agent_git_workspace": GIT_AUTHORING_WORKSPACE_VERSION if claude_agent_host else None, "fake_agent_mode": mode.value if claude_agent_host is None and local_agent is None else None}
        self.agent_runtime_files = (() if claude_agent_host is None else tuple(path for path in (claude_agent_host.claude_executable, claude_agent_host.bwrap_path, claude_agent_host.settings_path) if path is not None))
        self.compute_resources = compute_resources or LocalComputeResources()
        if (gpu_pool_socket is None) != (gpu_pool_namespace is None) or gpu_pool_socket is None and gpu_pool_server_uid is not None:
            raise ValueError("shared GPU pool requires both socket and state-root namespace")
        from .shared_gpu_host import SharedGpuHost
        from .resource_recovery import ResourceRecovery
        self.resource_recovery = ResourceRecovery(self)
        self.shared_gpu = None if gpu_pool_socket is None else SharedGpuHost(self, gpu_pool_socket, gpu_pool_namespace, gpu_pool_server_uid)
        if literature_io is not None and not isinstance(literature_io, FixedLiteratureIO): raise TypeError("literature_io must be FixedLiteratureIO")
        self.literature_io = literature_io
        self.institutional_access = institutional_access
        self._literature_executor = None if literature_io is None else ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="hypoloom-literature")
        from .paper_reading import PaperReadingHost
        self.paper_reading = PaperReadingHost(self)
        self._literature_jobs: dict[tuple[str, str], LiteratureRead] = {}
        self._literature_responses: dict[str, tuple[AgentRequest, AgentResponse, Mapping[str, Any] | None]] = {}
        if any(path.is_symlink() or not path.is_file() for path in (_RESOURCE_SKILL, _QUERY_SKILL)): raise ValueError("packaged Agent skill is unavailable")
        self.resource_skill = _RESOURCE_SKILL.read_bytes(); self.query_skill = _QUERY_SKILL.read_bytes()
        self.configuration = {**self.agent_configuration, "compute_resources": self.compute_resources.configuration, "resource_planning_skill": _RESOURCE_SKILL_VERSION, "openalex_query_planning_skill": _QUERY_SKILL_VERSION, "resource_experience": RESOURCE_EXPERIENCE_VERSION, "gpu_pressure_grace_seconds": self.gpu_pressure_grace_seconds, "literature": None if literature_io is None else literature_io.configuration}
        if institutional_access is not None:
            self.configuration["institutional_access"] = institutional_access.configuration
        if self.shared_gpu is not None: self.configuration["gpu_pool"] = self.shared_gpu.configuration
        if grounding_web is not None and hasattr(grounding_web, "configuration"):
            self.configuration["grounding_web"] = grounding_web.configuration
        self.runtime_files = (*self.agent_runtime_files, *self.compute_resources.runtime_files, _RESOURCE_SKILL, _QUERY_SKILL)
        self.version = self._runtime_version()
        self.store = StateStore(state_root, HostWriterCapability.issue()); self.kernel = ResearchKernel()
        from .interaction_host import InteractionHost
        self.interaction = InteractionHost(self)
        from .grounding_dialogue import GroundingDialogue
        self.grounding_dialogue = GroundingDialogue(self)
        from .shared_acceptance_host import SharedAcceptanceHost
        self.shared_acceptance = SharedAcceptanceHost(self)
        self.runner = LocalRunner()
        self.publisher: ArtifactPublisher | None = None; self.policy = ResearchPolicy(); self.fault_point = fault_point
        self.running: dict[str, RunningProcess] = {}; self.running_agents: dict[str, RunningClaudeAgent] = {}; self.spec_validations: dict[str, tuple[PreparedExecution, ExecutionWorkspace]] = {}
        self._provider_traffic = {}
        self.local_execution_agents: dict[str, AgentRequest] = {}
        self.agent_session_metadata: dict[str, Mapping[str, Any]] = {}
        self.grounding_web = grounding_web
        self.web_tool_results: dict[str, list[dict[str, Any]]] = {}
        self.projection_errors: dict[str, str] = {}; self.projection_refresh_at: dict[str, float] = {}
        self.projection_changes: dict[str, int] = {}
        self._agent_charges = AgentChargeCache()
        self._projection_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-projection")
        self._projection_jobs = {}
        self._batch_cursors = {}
        self._agent_scheduling = {}
        self._tick_deadline = None
        self._agent_dispatches = 0
        self._role_view_jobs = {}
        self._role_views_ready = set()
        self._role_view_errors = {}
        self.archive_errors: dict[str, str] = {}
        self.dispatch_errors: dict[str, dict[str, str]] = {}
        self._diagnostic_context: dict[str, Any] = {}
        self.gpu_pressure: dict[str, tuple[tuple[GpuPressureTracker, ...], int, dict[str, tuple[str, int | None]]]] = {}; self.resource_waits: dict[str, tuple[str, str]] = {}; self.stop_requested = False; self.operational_halt: str | None = None; self.terminal_exit_ready = False
    def initialize(self) -> None:
        try: self._initialize()
        except Exception as exc:
            self._record_exception(exc, phase="initialize"); raise
    def _record_exception(self, exc: Exception, **context: Any) -> None:
        environments = [self._provider_environment, *(entry[1] or {} for entry in self._provider_connections.values())]
        record_host_exception(self.state_root, exc, host_generation=self.generation,
            secrets=tuple(value for env in environments for key, value in env.items() if key in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} and value), **context)
    def _initialize(self) -> None:
        if self.literature_io is not None: self.literature_io.preflight()
        if self.claude_agent_host is not None: self.claude_agent_host.preflight(model=str(self.claude_model), provider_environment=self._provider_environment)
        self.compute_resources.snapshot(); self.store.initialize(); self.publisher = ArtifactPublisher(self.state_root / "artifacts")
        from .institutional_controls import bind
        bind(self)
        self._verify_versions(); self._recover_abandoned_attempts(); self._reconcile_terminal_attempt_residue()
        self.paper_reading.tick()
        from .paper_sources import reconcile_sources
        reconcile_sources(self)
        if self.shared_gpu is not None: self.shared_gpu.reconcile()
    def close(self) -> None:
        try: self._close()
        except Exception as exc:
            self._record_exception(exc, phase="close"); raise
    def _close(self) -> None:
        if self.institutional_access is not None:
            self.institutional_access.close()
        self._projection_executor.shutdown(wait=True)
        self._collect_role_views()
        for run_id in tuple(self._projection_jobs):
            self._collect_research_projection(run_id)
        if self.publisher is not None: self.paper_reading.close()
        for job in self._literature_jobs.values(): job.cancelled.set(); job.future.cancel()
        if self.claude_agent_host is not None:
            for handle in tuple(self.running_agents.values()): self.claude_agent_host.cancel(handle); self.claude_agent_host.cleanup(handle)
        self.running_agents.clear(); self.local_execution_agents.clear()
        for handle in tuple(self.running.values()): self.runner.cancel(handle); self.runner.cleanup(handle)
        if self.shared_gpu is not None: self.shared_gpu.reconcile()
        if self._literature_executor is not None:
            self._literature_executor.shutdown(wait=True, cancel_futures=True)
        self._literature_jobs.clear(); self._literature_responses.clear()
        self.running.clear(); self.spec_validations.clear(); self.gpu_pressure.clear(); self.resource_waits.clear(); self.store.close()
    def _crash(self, point: str) -> None:
        if self.fault_point == point: os._exit(86)
    def _runtime_version(self) -> RunVersion:
        return installed_run_version(self.configuration, self.runtime_files, private_settings_path=self.private_settings_path)
    def _check_agent_runtime(self) -> None:
        if self.claude_agent_host is not None and self._runtime_version() != self.version:
            raise PinnedRuntimeError("pinned Agent/compute configuration or runtime drifted")
    def _verify_versions(self) -> None:
        expected = dict(zip(_VERSION_FIELDS, self.version.as_tuple(), strict=True))
        rows = self.store.inspect(
            "SELECT r.* FROM workflow_runs r JOIN workflow_transitions t ON t.run_id=r.run_id "
            "AND t.workflow_kind='RUN' WHERE t.state NOT IN('COMPLETED','FAILED','CANCELLED') "
            "AND NOT EXISTS(SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id "
            "AND n.revision>t.revision) ORDER BY r.run_id")
        for row in rows:
            mismatch = [field for field in _VERSION_FIELDS if row[field] != expected[field]]
            if mismatch: raise PinnedRuntimeError(f"run {row['run_id']} requires a different immutable bundle: {mismatch}")
    def _recover_abandoned_attempts(self) -> None:
        with self.store.transaction() as tx: abandoned = abandoned_attempt_to_lost(tx, self.generation)
        for attempt in abandoned:
            pgid = attempt["process_group_id"]
            if pgid is not None:
                deadline = time.monotonic() + 3.0
                while process_group_exists(int(pgid)) and time.monotonic() < deadline: time.sleep(0.02)
                if process_group_exists(int(pgid)): raise OperationalHalt(f"owned process group {pgid} remains for {attempt['attempt_id']}")
            observation: dict[str, Any] = {"absence_proven": True, "prior_generation": attempt["host_generation"]}
            if attempt["kind"] == "PROCESS":
                rows = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND json_extract(payload_json,'$.attempt_id')=? ORDER BY revision DESC LIMIT 2", (attempt["workflow_id"], attempt["attempt_id"]))
                if len(rows) != 1: raise OperationalHalt(f"process attempt {attempt['attempt_id']} lacks one intent")
                if not isinstance((intent := json.loads(rows[0]["payload_json"])).get("process_request"), Mapping): raise OperationalHalt(f"process attempt {attempt['attempt_id']} lacks ProcessRequest")
                observation.update({"attempt_id": attempt["attempt_id"], "attempt_ordinal": attempt["attempt_ordinal"],
                    "execution_id": attempt["execution_id"], "terminal_reason": AttemptTerminalReason.LOST.value,
                    "process_request": intent["process_request"], "source_agent_attempt_id": intent.get("agent_attempt_id")})
            if attempt["kind"] == "AGENT":
                intent = json.loads(attempt["observation_json"]); request = AgentRequest.from_dict(intent["request"])
                if intent.get("session_binding") is not None:
                    target = self._run_config(request.run_id)["execution"].get("target")
                    binding = None if not isinstance(target, Mapping) else GitTargetBinding(checkout=Path(str(target["checkout"])), commit=str(target["commit"]))
                    prepared = None
                    try:
                        previous = [row for row in self.store.list_attempts(workflow_id=request.owner_workflow_id, command_id=request.command_id)
                            if row["kind"] == "AGENT" and row["attempt_ordinal"] < request.attempt_ordinal]
                        prepared = AgentSessions(self.state_root, self.publisher).prepare(request, None if request.command is AgentCommand.PAPER_READ else binding,
                            prior=previous[-1] if previous else None, recovering=True, admitted_binding=intent["session_binding"])
                        if prepared.binding != intent["session_binding"]:
                            raise GitAuthoringIntegrityError("recovered session differs from its OPEN binding")
                        from .paper_sources import restore_sources
                        restore_sources(self, prepared)
                        self.agent_session_metadata[request.attempt_id] = {**prepared.finish_node(), "launch_arguments": None, "archive_after_loss": True}
                    except (OSError, ValueError, GitAuthoringPreparationError, GitAuthoringIntegrityError) as exc:
                        if isinstance(exc, SessionFinishError):
                            self.agent_session_metadata[request.attempt_id] = {**exc.metadata,
                                "launch_arguments": None, "archive_after_loss": True}
                        observation["session_recovery_error"] = str(exc)[:1024]
                    finally:
                        if prepared is not None:
                            prepared.cleanup()
                            prepared.discard_papers()
                self._agent_failure(request, AttemptTerminalReason.LOST, "host_restart", retryable=True, details=observation)
            else:
                with self.store.transaction() as tx:
                    tx.close_attempt(attempt["attempt_id"], terminal_reason=AttemptTerminalReason.LOST, ended_at=time.time(), payload=observation)
                    command = tx.execute("SELECT command_type FROM workflow_commands WHERE command_id=?", (attempt["command_id"],)).fetchone()
                    if command is not None and command[0] == Command.VALIDATE_SPEC_CANDIDATE.value: tx.append_transition(self._transition_record(tx, attempt["run_id"], attempt["workflow_id"], "EXPERIMENT", Command.OBSERVE_SPEC_VALIDATION, ExperimentState.DRAFTED, payload={"attempt_id": attempt["attempt_id"], "validation_succeeded": False}))
            self._remove_attempt_residue(attempt)
    def _reconcile_terminal_attempt_residue(self, run_id: str | None = None) -> None:
        if self.publisher is None: raise OperationalHalt("Workflow Host is not initialized")
        rows = self.store.inspect("SELECT a.*,c.command_type FROM execution_attempts a JOIN workflow_commands c ON c.command_id=a.command_id WHERE a.state='CLOSED' AND (? IS NULL OR a.run_id=?) AND ((a.kind='AGENT' AND c.command_type IN(?,?)) OR (a.kind='PROCESS' AND (c.command_type=? OR EXISTS(SELECT 1 FROM workflow_transitions t JOIN workflow_commands tc ON tc.command_id=t.command_id WHERE t.workflow_id=a.workflow_id AND tc.command_type=? AND json_extract(t.payload_json,'$.attempt_id')=a.attempt_id)))) ORDER BY a.run_id,a.workflow_id,a.attempt_id", (run_id, run_id, Command.REQUEST_SPEC_CANDIDATE.value, Command.REQUEST_RESEARCH_NODE.value, Command.VALIDATE_SPEC_CANDIDATE.value, Command.ACCEPT_OUTCOME.value))
        workspace = ExecutionWorkspace(self.publisher.root, state_root=self.state_root)
        git_workspace = GitAuthoringWorkspace(self.state_root)
        for attempt in rows:
            workspace.cleanup_attempt(str(attempt["attempt_id"]), preserve_logs=attempt["kind"] == "PROCESS" and attempt["command_type"] != Command.VALIDATE_SPEC_CANDIDATE.value)
            if attempt["kind"] == "AGENT": git_workspace.cleanup_attempt(str(attempt["attempt_id"]))
    def _remove_attempt_residue(self, attempt: Mapping[str, Any]) -> None:
        for category in ("work", "result-staging"): self._safe_remove(self.state_root / "artifacts" / category / attempt["attempt_id"])
        if attempt["kind"] == "AGENT": GitAuthoringWorkspace(self.state_root).cleanup_attempt(str(attempt["attempt_id"]))
        if attempt["kind"] == "PROCESS":
            key = ArtifactKey(run_id=attempt["run_id"], experiment_id=attempt["experiment_id"], execution_id=attempt["execution_id"], attempt_id=attempt["attempt_id"])
            recorded = self.store.inspect("SELECT 1 FROM execution_artifacts WHERE attempt_id=?", (attempt["attempt_id"],))
            if not recorded:
                if self.publisher is None: raise OperationalHalt("Workflow Host is not initialized")
                self._safe_remove(self.publisher.path_for(key))
    def _safe_remove(self, path: Path) -> None:
        root = (self.state_root / "artifacts").resolve()
        candidate = path.resolve(strict=False)
        if not candidate.is_relative_to(root): raise OperationalHalt(f"cleanup escaped artifact root: {candidate}")
        if candidate.is_symlink() or candidate.is_file(): candidate.unlink(missing_ok=True)
        elif candidate.is_dir(): shutil.rmtree(candidate)
    def handle(self, request: ControlRequest) -> ControlResponse:
        with self.store.cache_reads():
            return self._handle(request)
    def _handle(self, request: ControlRequest) -> ControlResponse:
        def record(exc: Exception) -> None:
            self._record_exception(exc, phase="control", request_id=request.request_id,
                run_id=request.payload.get("run_id"), command=request.command)
        try:
            if request.command == "STATUS": return self._status_response(request)
            if request.command == "INSTITUTIONAL_CONNECTIONS":
                from .institutional_client import host_connections
                return ControlResponse.accepted(request.request_id, None, host_connections(self, request.payload))
            if request.command == "LITERATURE_ACCESS": return self._literature_access_control(request)
            if request.command == "BACKUP": return self._backup(request)
            if request.command == "CREATE_RUN": return self._create_run(request)
            if request.command == "CANCEL": return self._cancel_run(request)
            if request.command == "ANSWER": return self.grounding_dialogue.answer(request)
            if request.command in {"PAUSE", "RESUME", "INJECT"}: return self._control_participant(request)
            if request.command == "RESOURCE_RECHECK": return self.resource_recovery.recheck(request)
            if request.command == "PROVIDER_RETRY_CLEAR": return self._provider_retry_clear(request)
            if request.command == "NOTIFICATION_RETRY":
                if set(request.payload) != {"run_id", "sink"} or self.observer_services is None:
                    raise ValueError("notification retry requires run_id, sink and a managed notification service")
                if not isinstance(request.payload["run_id"], str) or request.payload["sink"] not in ("telegram", "webhook"):
                    raise ValueError("notification retry requires a run ID and a supported sink")
                if self.store.get_run(request.payload["run_id"]) is None: raise ValueError("unknown run")
                return ControlResponse.accepted(request.request_id, None,
                    self.observer_services.notification_retry(request.payload["run_id"], request.payload["sink"]))
            if request.command in {"PIN_PLUGIN", "REVERT_PLUGIN", "UNPIN_PLUGIN"}: return self._control_plugin(request)
            return ControlResponse.failure(request.request_id, "rejected", "unknown_command", request.command)
        except NonQuiescentBackup as exc: record(exc); return ControlResponse.failure(request.request_id, "rejected", "non_quiescent", str(exc))
        except IdentityConflict as exc: record(exc); return ControlResponse.failure(request.request_id, "conflict", "identity_conflict", str(exc))
        except RevisionConflict as exc: record(exc); return ControlResponse.failure(request.request_id, "conflict", "revision_conflict", str(exc))
        except (KeyError, TargetPreparationError, TypeError, ValueError) as exc: record(exc); return ControlResponse.failure(request.request_id, "rejected", "invalid_request", str(exc))
        except sqlite3.Error as exc:
            record(exc)
            code = getattr(exc, "sqlite_errorcode", None)
            if isinstance(code, int) and code & 0xFF in _FATAL_SQLITE:
                self.operational_halt = f"{type(exc).__name__}: {exc}"; self.stop_requested = True
                return ControlResponse.failure(request.request_id, "error", "operational_halt", self.operational_halt)
            return ControlResponse.failure(request.request_id, "error", "host_error", str(exc))
        except Exception as exc: record(exc); return ControlResponse.failure(request.request_id, "error", "host_error", str(exc))
    def _create_run(self, request: ControlRequest) -> ControlResponse:
        from .gpu_pool_lifecycle import admission_lock
        socket_path = None if self.shared_gpu is None else self.shared_gpu.client.socket_path
        with admission_lock(socket_path, self.state_root):
            return self._create_run_with_pool_ready(request)
    def _create_run_with_pool_ready(self, request: ControlRequest) -> ControlResponse:
        payload = request.payload; expected = {"run_id", "research_contract", "execution"}
        if set(payload) != expected: raise ValueError("CREATE_RUN payload has unknown or missing fields")
        run_id = str(payload["run_id"])
        if _SAFE_ID.fullmatch(run_id) is None: raise ValueError("run_id must be one safe path component")
        raw_contract = payload["research_contract"]; execution = payload["execution"]
        if not isinstance(raw_contract, Mapping): raise ValueError("research_contract must be an object")
        contract = ResearchContract.from_mapping(raw_contract)
        validate_new_run_budget(contract.document, self.agent_budget)
        if _uses_expert_loop(contract) and self.claude_agent_host is not None and (self.literature_io is None or self.literature_io.configuration.get("adapter") != "openalex"):
            raise ValueError("real ACI-7 expert loop requires a qualified OpenAlex literature adapter")
        execution_blockers = component_execution_blockers(contract.document)
        if execution_blockers: raise ValueError("; ".join(item.message for item in execution_blockers))
        execution_fields = {"delay_seconds", "allow_gpu", "target", "runtime_prefix", "read_roots"}
        if not isinstance(execution, Mapping) or set(execution) != execution_fields: raise ValueError("execution has unknown or missing operational bindings")
        raw_delay = execution["delay_seconds"]
        allow_gpu = execution.get("allow_gpu", False)
        if not isinstance(allow_gpu, bool): raise ValueError("allow_gpu must be boolean")
        if allow_gpu:
            from .profile_v4 import validate_gpu_pool_budget
            validate_gpu_pool_budget(contract.document, pooled=self.shared_gpu is not None)
            if self.shared_gpu is not None and int(self.version.agent_aci_version) >= 28:
                from .profile_v4 import restrict_profile_capabilities
                from .gpu_pool_client import validate_pool_snapshot
                capabilities = restrict_profile_capabilities(contract.document, self.compute_resources.snapshot())
                if not capabilities.gpus:
                    raise ValueError("GPU launch has no authorized devices")
                try:
                    validate_pool_snapshot(self.shared_gpu.client.request("snapshot", {}),
                        aci_version=int(self.version.agent_aci_version),
                        allowed_gpu_uuids=[gpu.uuid for gpu in capabilities.gpus])
                except PoolError as exc:
                    raise ValueError(f"GPU pool preflight failed ({exc.code}): {exc}") from exc
        if isinstance(raw_delay, bool) or not isinstance(raw_delay, (int, float)): raise ValueError("delay_seconds must be numeric")
        delay = float(raw_delay)
        if not math.isfinite(delay) or delay < 0: raise ValueError("delay_seconds must be finite and non-negative")
        target = execution["target"]; target_record = None
        if target is not None:
            if not isinstance(target, Mapping) or set(target) != {"checkout", "commit"}: raise ValueError("target binding must contain checkout and commit")
            bound = bind_clean_git_target(str(target["checkout"]))
            if bound.commit != target["commit"]: raise ValueError("target binding changed after profile admission")
            if self.state_root.resolve().is_relative_to(bound.checkout) or bound.checkout.is_relative_to(self.state_root.resolve()):
                raise ValueError("target checkout and state root must be disjoint")
            target_record = {"checkout": str(bound.checkout), "commit": bound.commit}
        runtime_prefix = execution["runtime_prefix"]
        if runtime_prefix is not None:
            prefix = Path(str(runtime_prefix))
            if prefix.is_symlink() or not prefix.is_absolute() or not (prefix / "bin" / "python").is_file(): raise ValueError("runtime_prefix is unavailable")
            runtime_prefix = str(prefix.resolve())
        roots = execution["read_roots"]
        if not isinstance(roots, list) or len(set(roots)) != len(roots): raise ValueError("read_roots must be a unique list")
        resolved_roots = []
        for value in roots:
            path = Path(value) if isinstance(value, str) else Path()
            if not isinstance(value, str) or path.is_symlink() or not path.is_absolute() or not path.is_dir(): raise ValueError("read root is unavailable")
            resolved_roots.append(str(path.resolve()))
        requested_runtime = contract.document["runtime"]; requested_safety = contract.document["safety"]
        requested_target = requested_runtime.get("target_repo")
        expected_checkout = None if requested_target is None else str(Path(str(requested_target)).resolve())
        if expected_checkout != (None if target_record is None else target_record["checkout"]): raise ValueError("resolved target differs from research contract")
        requested_prefix = requested_runtime.get("conda_env")
        expected_prefix = None if requested_prefix is None else str(Path(str(requested_prefix)).resolve())
        if expected_prefix != runtime_prefix: raise ValueError("resolved runtime differs from research contract")
        if [str(Path(str(value)).resolve()) for value in requested_safety.get("read_roots", ())] != resolved_roots: raise ValueError("resolved read roots differ from research contract")
        config = {"execution": {"allow_gpu": allow_gpu, "delay_seconds": delay, "target": target_record,
            "runtime_prefix": runtime_prefix, "read_roots": resolved_roots}}
        if self.shared_gpu is not None: config["gpu_pool"] = self.shared_gpu.configuration
        def operation(tx: StateTransaction) -> Mapping[str, Any]:
            tx.create_run(run_id, self.version); ids = _ids(run_id)
            candidate = GoalDefined(fact_id=_fact_id(run_id, "goal"), run_id=RunId(run_id),
                goal_id=GoalId(ids["goal"]), research_contract=contract)
            mutation = self.kernel.construct_fact(candidate, tx); tx.append_fact(mutation)
            transition = self._transition_record(tx, run_id, run_id, "RUN", Command.CREATE_RUN, RunState.CREATED,
                request.request_id, fact_id=str(candidate.fact_id), next_command=Command.ACTIVATE.value, payload=config)
            tx.append_transition(transition)
            return {"status": "accepted", "revision": transition["revision"],
                    "result": {"run_id": run_id, "run_state": RunState.CREATED.value}}
        stored = self.store.execute_command({"command_id": request.request_id, "run_id": run_id, "workflow_id": run_id,
            "expected_revision": request.expected_revision, "command_type": Command.CREATE_RUN.value,
            "request": request.to_mapping()}, operation)
        return ControlResponse.accepted(request.request_id, stored.revision, stored.result or {})
    def _cancel_run(self, request: ControlRequest) -> ControlResponse:
        run_id = str(request.payload.get("run_id", "")); cause = request.payload.get("cause")
        if set(request.payload) != {"run_id", "cause"} or not run_id or not isinstance(cause, str): raise ValueError("CANCEL requires run_id and cause")
        def operation(tx: StateTransaction) -> Mapping[str, Any]:
            head = tx.latest_transition(run_id)
            if head is None or head["workflow_kind"] != "RUN": raise ValueError(f"unknown run {run_id!r}")
            if head["state"] in _TERMINAL_RUNS:
                return {"status": "rejected", "error": {"code": "already_terminal", "message": head["state"]},
                        "revision": head["revision"]}
            transition = self._transition_record(tx, run_id, run_id, "RUN", Command.CANCEL, RunState.STOPPING,
                request.request_id, payload={"stop_disposition": self._stop_disposition(run_id).value, "cause": cause})
            self.grounding_dialogue.cancel_pending(tx, run_id)
            tx.append_transition(transition)
            return {"status": "accepted", "revision": transition["revision"],
                    "result": {"run_id": run_id, "run_state": RunState.STOPPING.value}}
        stored = self.store.execute_command({"command_id": request.request_id, "run_id": run_id, "workflow_id": run_id,
            "expected_revision": request.expected_revision, "command_type": Command.CANCEL.value,
            "request": request.to_mapping()}, operation)
        if stored.status != "accepted":
            error = stored.error or {"code": "rejected", "message": "cancel rejected"}
            return ControlResponse.failure(request.request_id, "rejected", str(error.get("code")), str(error.get("message")), stored.revision)
        self.resource_waits = {workflow: wait for workflow, wait in self.resource_waits.items() if wait[0] != run_id}
        for job in self._literature_jobs.values():
            if job.run_id == run_id: job.cancelled.set(); job.future.cancel()
        for attempt_id, handle in tuple(self.running.items()): self.runner.cancel(handle) if self.store.get_attempt(attempt_id)["run_id"] == run_id else None
        for attempt_id, handle in tuple(self.running_agents.items()) if self.claude_agent_host is not None else ():
            if self.store.get_attempt(attempt_id)["run_id"] == run_id and not self._is_final_report_request(handle.request): self.claude_agent_host.cancel(handle)
        return ControlResponse.accepted(request.request_id, stored.revision, stored.result or {})
    def _participant_controls(self, run_id: str) -> Any:
        from .participant_controls import fold_participant_controls
        return fold_participant_controls(self.store.inspect(
            "SELECT * FROM workflow_transitions WHERE run_id=? AND workflow_kind='RUN' ORDER BY revision", (run_id,)),
            self._research_contract(run_id).role_ids)
    def _control_participant(self, request: ControlRequest) -> ControlResponse:
        from .participant_controls import ParticipantScope, supports_participant_controls
        payload = request.payload
        if set(payload) != {"run_id", "scope", "reason"} | ({"text"} if request.command == "INJECT" else set()):
            raise ValueError("participant control payload fields are not exact")
        run_id = payload["run_id"]; scope = ParticipantScope.from_dict(payload["scope"])
        if not isinstance(run_id, str) or payload["reason"] != "operator": raise ValueError("public participant controls require operator reason")
        run = self.store.get_run(run_id)
        if run is None or not supports_participant_controls(run["workflow_definition_version"]): raise ValueError("run lifecycle does not support participant controls")
        contract = self._research_contract(run_id)
        if scope.role is not None and scope.role not in contract.role_ids: raise ValueError("participant role is not admitted")
        component = None if scope.component_id is None else contract.component(scope.component_id)
        if scope.component_id is not None and (component is None or scope.role not in component.proposer_roles):
            raise ValueError("participant component does not belong to its role")
        control = {"action": request.command.lower(), "scope": payload["scope"], "reason": "operator"}
        if request.command == "INJECT":
            if not isinstance(payload["text"], str) or not payload["text"].strip() or "\x00" in payload["text"]: raise ValueError("operator note requires nonempty text")
            control["text"] = payload["text"]
        command = {"PAUSE": Command.PAUSE_PARTICIPANT, "RESUME": Command.RESUME_PARTICIPANT, "INJECT": Command.INJECT_NOTE}[request.command]
        def operation(tx: StateTransaction) -> Mapping[str, Any]:
            head = tx.latest_transition(run_id)
            if head is None or head["state"] not in {RunState.CREATED.value, RunState.ACTIVE.value}:
                raise ValueError("participant controls require a CREATED or ACTIVE run")
            row = self._transition_record(tx, run_id, run_id, "RUN", command, RunState(head["state"]),
                request.request_id, payload={"participant_control": control})
            tx.append_transition(row)
            return {"status": "accepted", "revision": row["revision"], "result": {"run_id": run_id,
                "run_state": head["state"], "participant_controls": self._participant_controls(run_id).to_dict()}}
        stored = self.store.execute_command({"command_id": request.request_id, "run_id": run_id, "workflow_id": run_id,
            "expected_revision": request.expected_revision, "command_type": command.value, "request": request.to_mapping()}, operation)
        return ControlResponse.accepted(request.request_id, stored.revision, stored.result or {})
    def _provider_retry_clear(self, request: ControlRequest) -> ControlResponse:
        if request.payload.get("service") == "openalex": return self._literature_retry_clear(request)
        from .provider_recovery import retry_control
        return retry_control(self, request)

    def _literature_retry_clear(self, request):
        if set(request.payload) != {"run_id", "reason", "service"} or request.payload["reason"] != "operator":
            raise ValueError("literature retry requires run_id, operator reason and service=openalex")
        run_id = request.payload["run_id"]
        if self.literature_io is None: raise ValueError("literature service is not configured")
        def operation(tx):
            head = tx.latest_transition(run_id)
            if head is None or head["state"] not in {"CREATED", "ACTIVE"}: raise ValueError("literature retry requires an active run")
            row = self._transition_record(tx, run_id, run_id, "RUN", Command.PROVIDER_RETRY_CLEAR,
                RunState(head["state"]), request.request_id, payload={"literature_retry_clear": {"cleared_at": time.time(), "reason": "operator"}})
            tx.append_transition(row)
            return {"status": "accepted", "revision": row["revision"], "result": {"run_id": run_id}}
        stored = self.store.execute_command({"command_id": request.request_id, "run_id": run_id, "workflow_id": run_id,
            "expected_revision": request.expected_revision, "command_type": Command.PROVIDER_RETRY_CLEAR.value,
            "request": request.to_mapping()}, operation)
        if stored.status != "accepted":
            return ControlResponse.failure(request.request_id, "rejected", "literature_retry_rejected", "Literature retry was not accepted", stored.revision)
        from .literature_recovery import status
        return ControlResponse.accepted(request.request_id, stored.revision, {"literature_retry": status(self.store, run_id)})
    def _control_plugin(self, request: ControlRequest) -> ControlResponse:
        from .expert_plugin import ExpertPluginStore, _full_commit, select_plugin_commit
        from .profile_v4 import resolve_node_runtime
        payload = request.payload
        if set(payload) != {"run_id", "role", "owner_kind", "commit"}: raise ValueError("plugin control payload fields are not exact")
        run_id, role, owner_kind, requested = (payload[name] for name in ("run_id", "role", "owner_kind", "commit"))
        if not isinstance(run_id, str) or not isinstance(role, str) or owner_kind not in {"expert", "grounding"}: raise ValueError("plugin owner identity is invalid")
        run = self.store.get_run(run_id)
        if run is None or int(run["agent_aci_version"]) < 14 or not self._batch_enabled(run_id): raise ValueError("plugin controls require an ACI14 expert-loop run")
        contract = self._research_contract(run_id)
        components = tuple(item for item in contract.components if item.owner_role == role)
        if owner_kind == "expert" and (role not in contract.role_ids or not components) or owner_kind == "grounding" and role != "grounding":
            raise ValueError("plugin role is not an admitted owner")
        if requested is not None: _full_commit(requested)
        elif request.command != "UNPIN_PLUGIN": raise ValueError("pin/revert require an explicit full Git commit")
        focus = "Manage the shared research graph and trigger skills." if owner_kind == "grounding" else next(item["focus"] for item in contract.document["roles"] if item["id"] == role)
        runtime = resolve_node_runtime(contract.to_dict(), None if owner_kind == "grounding" else role,
            "graph_evolve" if owner_kind == "grounding" else "self_evolve")
        maximum = runtime.get("memory", {}).get("max_plugin_bytes", 1024 * 1024)
        def operation(tx: StateTransaction) -> Mapping[str, Any]:
            head = tx.latest_transition(run_id)
            if head is None or head["state"] not in {RunState.CREATED.value, RunState.ACTIVE.value}: raise ValueError("plugin controls require a CREATED or ACTIVE run")
            attempts = [dict(row) for row in tx.execute("SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT' ORDER BY rowid", (run_id,)).fetchall()]
            effective = {str(item.component_id): 1 for item in components} if owner_kind == "expert" else {}
            for row in attempts:
                prior = json.loads(row["observation_json"])["request"]; key = prior.get("task_context", {}).get("key", {})
                component = prior.get("component_id")
                if prior.get("role") == role and component in effective and type(key.get("cycle")) is int:
                    effective[component] = max(effective[component], key["cycle"] + 1)
            store = ExpertPluginStore(self.state_root, owner_kind=owner_kind)
            seed = store.ensure(run_id, role, focus, max_bytes=maximum)
            commit = requested
            if commit is None:
                commit = select_plugin_commit([], attempts, run_id=run_id, role=role, owner_kind=owner_kind,
                    component_id=None if owner_kind == "grounding" else str(components[0].component_id),
                    cycle=None if owner_kind == "grounding" else effective[str(components[0].component_id)], default_commit=seed.commit)["commit"]
            try:
                selected = store.binding_at(run_id, role, commit, max_bytes=maximum)
                if request.command == "REVERT_PLUGIN": selected = store.revert_to(run_id, role, target_commit=commit, command_id=request.request_id, max_bytes=maximum)
            except (ValueError, OSError, GitAuthoringPreparationError, GitAuthoringIntegrityError) as exc: raise ValueError(str(exc)) from exc
            control = {"action": request.command.removesuffix("_PLUGIN").lower(), "owner_kind": owner_kind, "role": role,
                "requested_commit": requested, "commit": selected.commit, "effective_cycles": effective}
            row = self._transition_record(tx, run_id, run_id, "RUN", Command.SELECT_PLUGIN, RunState(head["state"]),
                request.request_id, next_command=head["next_command"], payload={"plugin_selection": control})
            tx.append_transition(row)
            return {"status": "accepted", "revision": row["revision"], "result": {"run_id": run_id, "run_state": head["state"], "plugin_selection": control}}
        stored = self.store.execute_command({"command_id": request.request_id, "run_id": run_id, "workflow_id": run_id,
            "expected_revision": request.expected_revision, "command_type": Command.SELECT_PLUGIN.value, "request": request.to_mapping()}, operation)
        return ControlResponse.accepted(request.request_id, stored.revision, stored.result or {})
    def _plugin_binding(self, run_id: str, task: ResearchNodeTask | AuxiliaryTask, role: str, focus: str,
                        *, max_bytes: int, owner_kind: str = "expert"):
        from .expert_plugin import ExpertPluginStore, PluginBinding, select_plugin_commit
        store = ExpertPluginStore(self.state_root, owner_kind=owner_kind)
        seed = store.ensure(run_id, role, focus, max_bytes=max_bytes)
        attempts = self.store.inspect("SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT' ORDER BY rowid", (run_id,))
        current = self._batch_enabled(run_id)
        suffix = "--grounding-resources" if owner_kind == "grounding" else "--expert-resources"
        for row in attempts:
            prior = json.loads(row["observation_json"])["request"]; key = (prior.get("task_context") or {}).get("key", {})
            matches = prior.get("command_id") == task.command_id if owner_kind == "grounding" else (
                prior.get("role") == role and key.get("cycle") == task.key.cycle
                and (not current or prior.get("component_id") == task.key.component_id))
            if not matches: continue
            resource = next((item for item in prior.get("input_artifacts", ()) if item["ref"].endswith(suffix)), None)
            if resource is None: continue
            binding = PluginBinding(**json.loads(EmbeddedInput.from_dict(resource).content_bytes())["plugin"])
            if (binding.run_id, binding.role, binding.owner_kind) != (run_id, role, owner_kind): raise ValueError("frozen plugin belongs to another owner")
            return store.binding_at(run_id, role, binding.commit, max_bytes=max_bytes)
        if not current: return seed
        controls = self.store.inspect("SELECT * FROM workflow_transitions WHERE run_id=? AND workflow_kind='RUN' ORDER BY revision", (run_id,))
        selection = select_plugin_commit(controls, attempts, run_id=run_id, role=role, owner_kind=owner_kind,
            component_id=task.key.component_id if owner_kind == "expert" else None,
            cycle=task.key.cycle if owner_kind == "expert" else None, default_commit=seed.commit)
        return store.binding_at(run_id, role, selection["commit"], max_bytes=max_bytes)
    def _participant_paused(self, run_id: str, role: str, component_id: str, command: str, chain: str | None = None) -> bool:
        from .participant_controls import ParticipantScope
        return self._participant_controls(run_id).blocks(ParticipantScope(role, component_id, chain), command)
    def _literature_cycle_paused(self, run_id: str, role: str, component_id: str, cycle: int) -> bool:
        if not self._participant_paused(run_id, role, component_id, "LITERATURE_REVIEW"): return False
        # Grounding is role-neutral; a participant pause does not stop its prelude.
        if self._expert_success(AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id=run_id)) is None: return False
        return not self.store.inspect("SELECT 1 FROM execution_attempts WHERE run_id=? AND kind='AGENT' "
            "AND json_extract(observation_json,'$.request.role')=? AND json_extract(observation_json,'$.request.component_id')=? "
            "AND json_extract(observation_json,'$.request.command')='LITERATURE_REVIEW' "
            "AND json_extract(observation_json,'$.request.task_context.key.cycle')=? LIMIT 1", (run_id, role, component_id, cycle))
    def _operator_note_inputs(self, run_id: str, task: ResearchNodeTask, role: str, component_id: str) -> tuple[EmbeddedInput, ...]:
        from .participant_controls import ParticipantScope
        controls = self._participant_controls(run_id); notes = {item.command_id: item for item in controls.notes}
        attempts = self._expert_attempts(task)
        if attempts:
            original = AgentRequest.from_dict(json.loads(attempts[0]["observation_json"])["request"])
            return tuple(item for item in original.input_artifacts if item.ref in notes)
        chain = getattr(task.key, "chain", None)
        if chain is None and task.key.node_kind in {ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeKind.EXPERIMENT_EXECUTION, ResearchNodeKind.RESULT_ANALYSIS}: chain = "1"
        participant = ParticipantScope(role, component_id, chain); consumed = set()
        for row in self.store.inspect("SELECT observation_json FROM execution_attempts WHERE run_id=? AND kind='AGENT'", (run_id,)):
            previous = json.loads(row["observation_json"])["request"]
            if previous.get("role") != role: continue
            for item in previous.get("input_artifacts", ()):
                note = notes.get(item["ref"])
                if note is not None and note.scope.contains(participant): consumed.add(note.command_id)
        return tuple(EmbeddedInput.from_bytes(note.command_id, "application/json", _canonical({
            "source_command_id": note.command_id, "scope": {"role": note.scope.role,
                "component_id": note.scope.component_id, "chain": note.scope.chain}, "operator_instruction": note.text}))
            for note in controls.pending_notes(participant, consumed))
    def _literature_access_control(self, request: ControlRequest) -> ControlResponse:
        from .institutional_controls import control
        return control(self, request)

    def _status_response(self, request: ControlRequest) -> ControlResponse:
        if set(request.payload) != {"run_id"}: raise ValueError("STATUS requires only run_id")
        run_id = str(request.payload["run_id"])
        attempts = self.store.list_attempts(run_id=run_id, include_observation=False)
        # Only Grounding contributes the declaration displayed by STATUS.
        grounding = {row["attempt_id"]: row for row in self.store.inspect(
            "SELECT a.* FROM execution_attempts a JOIN workflow_commands c ON c.command_id=a.command_id "
            "WHERE a.run_id=? AND a.kind='AGENT' AND a.state='CLOSED' AND a.terminal_reason='SUCCEEDED' "
            "AND json_extract(c.request_json,'$.task_context.request_kind')='GROUNDING'", (run_id,))}
        attempts = tuple(grounding.get(row["attempt_id"], row) for row in attempts)
        result = build_status_projection(self.store, run_id,
            quiescent_for_backup=self._backup_busy_reason() is None, attempts=attempts)
        if result is None: return ControlResponse.failure(request.request_id, "rejected", "not_found", run_id)
        result["dispatch_error"] = self.dispatch_errors.get(run_id)
        scheduling = self._agent_scheduling.get(run_id)
        result["agent_scheduling"] = None if scheduling is None else {
            "observed_at": scheduling["observed_at"], "tasks": list(scheduling["tasks"].values())}
        result["provider_retry"] = self._provider_retry_status(run_id)
        from .provider_traffic import for_run
        result['provider_traffic'] = for_run(self, run_id).snapshot() if self.claude_agent_host is not None else None
        from .literature_recovery import status as literature_status
        result["literature_retry"] = literature_status(self.store, run_id) if self.literature_io is not None else None
        result["notifications"] = ({"state": "unavailable", "sinks": {}} if self.observer_services is None
            else self.observer_services.notification_status(run_id))
        result["literature_access"] = ({"enabled": False, "pending": 0, "available": 0, "groups": []}
            if self.institutional_access is None else self.institutional_access.status(run_id))
        result["participant_controls"] = self._participant_controls(run_id).to_dict()
        result["agent_usage"] = self._agent_usage(run_id, attempts, result["run_state"] in _TERMINAL_RUNS)
        from .process_observation import process_activity
        result["process_activity"] = [process_activity(self, row) for row in attempts
            if row["kind"] == "PROCESS" and row["state"] == "OPEN"]
        result["resource_blocked"] = list(self.resource_recovery.blocked(run_id).values())
        result["resource_waits"] = [{"experiment_id": workflow, "reason": reason,
            "message": resource_wait_message(reason),
            **({"resource_planning": self.shared_gpu.experiment_view(run_id, workflow)} if self.shared_gpu else {})}
            for workflow, (owner, reason) in sorted(self.resource_waits.items()) if owner == run_id]
        if self._cycle_reports_enabled(run_id): result["research_projection"] = {"path": f"{run_id}/projection", "error": self.projection_errors.get(run_id)}
        mode = self._research_contract(run_id).document.get("methodology", {}).get("controls", {}).get("product", {}).get("methodology", {}).get("memory", {}).get("retention", "retain")
        if mode != "retain": result["session_retention"] = {"mode": mode, "archive": f"{run_id}/archives/sessions.tar.gz" if (self.state_root / run_id / "archives/sessions.tar.gz").is_file() else None, "error": self.archive_errors.get(run_id)}
        return ControlResponse.accepted(request.request_id, result["run_revision"], result)
    def _backup_busy_reason(self) -> str | None:
        if self._role_view_jobs:
            return "private research view is still in flight"
        if self._projection_jobs:
            return "research projection is still in flight"
        if any(not job.future.done() for job in self._literature_jobs.values()):
            return "literature read is still in flight"
        if self.running or self.running_agents:
            return "in-memory process or Agent attempt is running"
        rows = self.store.inspect("SELECT attempt_id FROM execution_attempts WHERE state='OPEN' LIMIT 1")
        if rows:
            return f"durable attempt remains OPEN: {rows[0]['attempt_id']}"
        return None
    def _assert_backup_quiescent(self, root: Path, phase: str) -> None:
        if root != self.state_root.resolve():
            raise NonQuiescentBackup("backup source root differs from resident Host root")
        reason = self._backup_busy_reason()
        if reason is not None:
            raise NonQuiescentBackup(f"backup is non-quiescent at {phase}: {reason}")
    def _backup(self, request: ControlRequest) -> ControlResponse:
        if request.expected_revision is not None:
            raise ValueError("BACKUP requires expected_revision=null")
        if set(request.payload) != {"backup_root", "backup_id"}:
            raise ValueError("BACKUP requires only backup_root and backup_id")
        backup_root, backup_id = request.payload["backup_root"], request.payload["backup_id"]
        if not isinstance(backup_root, str) or not isinstance(backup_id, str):
            raise ValueError("backup_root and backup_id must be text")
        self._assert_backup_quiescent(self.state_root.resolve(), "request")
        published = create_quiesced_backup(self.state_root, backup_root, backup_id,
            assert_quiescent=self._assert_backup_quiescent, audit=audit_all_runs)
        try:
            result = self.store.checkpoint_truncate()
            checkpoint = {"status": "busy" if result[0] else "ok", "busy": bool(result[0]),
                "log_frames": result[1], "checkpointed_frames": result[2]}
        except Exception as exc:
            checkpoint = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        return ControlResponse.accepted(request.request_id, None, {"path": str(published.path), "reused": published.reused,
            "checkpoint": checkpoint})
    def _agent_usage(self, run_id: str, attempts: tuple[Mapping[str, Any], ...], terminal: bool) -> dict[str, Any]:
        agent_attempts = [row for row in attempts if row["kind"] == "AGENT"]; estimate_scope = None
        try:
            contract = self._research_contract(run_id)
            if _uses_expert_loop(contract):
                experiments = int(contract.document["budget"]["max_experiments"]); components = sum(item.tier.value != "should" for item in contract.components)
                pre = sum(kind in {ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeKind.HYPOTHESIS_PROPOSAL} for kind, _ in _EXPERT_NODE_ORDER); post = sum(kind not in {ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeKind.EXPERIMENT_EXECUTION} for kind, _ in _EXPERT_NODE_ORDER)
                if self._tool_execution_enabled(run_id): post += 1
                calls = 2 + pre * max(components, experiments) + post * experiments
                if self._batch_enabled(run_id):
                    # A failed zero-experiment batch still closes via ADJ,
                    # UPDATE, REPORT and SELF. This is a core-node estimate,
                    # not an upper bound for enabled management interactions.
                    calls = 3 + pre * max(components, experiments) + 3 * experiments + 4 * max(components, experiments)
                    estimate_scope = {"kind": "core_expert_nodes_estimate", "strict_upper_bound": False,
                        "excluded_auxiliary_kinds": ["GRAPH_EVOLVE", "OBSERVER", "DEBATE", "DEBATE_JUDGE"]}
            else: calls = sum(len(item.proposer_roles) + 1 for item in contract.components) * (2 if len(contract.role_ids) > 1 else 1)
            max_attempts = self._effect_attempt_cap(contract, "agent") * calls; future_budget = profile_agent_budget(contract.document, self.agent_budget).to_dict(); contract_document = contract.document
        except (AgentACIError, IndexError, KeyError, TypeError, ValueError): max_attempts = 0; future_budget = None; contract_document = None
        unlaunched = max(0, max_attempts - len(agent_attempts)); run = self.store.get_run(run_id); same_bundle = run is not None and all(run[field] == getattr(self.version, field) for field in _VERSION_FIELDS)
        result = usage_projection(agent_attempts, future_attempts=0 if terminal else unlaunched,
            future_budget=future_budget if same_bundle else None, document=contract_document,
            charges=self._agent_charges.read(self.store, run_id, committed=True))
        if estimate_scope is not None: result["estimate_scope"] = estimate_scope
        return result
    def _transition_record(self, tx: StateTransaction, run_id: str, workflow_id: str, kind: str, command: Command,
        next_state: RunState | ExperimentState, command_id: str | None = None, *, fact_id: str | None = None,
        next_command: str | None = None, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        head = tx.latest_transition(workflow_id); current = None if head is None else head["state"]
        machine = Machine.RUN_WORKFLOW if kind == "RUN" else Machine.EXPERIMENT_WORKFLOW
        validate_transition(machine, current, command, next_state)
        revision = 1 if head is None else int(head["revision"]) + 1; record_internal_command = command_id is None
        command_id = command_id or f"{workflow_id}--r{revision}--{command.value.lower()}"
        if record_internal_command and tx.get_command(command_id) is None:
            tx.record_command({"command_id": command_id, "run_id": run_id, "workflow_id": workflow_id,
                "expected_revision": 0 if head is None else head["revision"], "command_type": command.value,
                "request": {"command": command.value, "workflow_id": workflow_id}, "status": "accepted",
                "result": {"state": next_state.value}, "revision": revision})
        return {"workflow_id": workflow_id, "revision": revision, "run_id": run_id, "workflow_kind": kind,
            "previous_state": current, "state": next_state.value, "command_id": command_id,
            "next_command": next_command, "due_at": time.time() if next_command else None,
            "fact_id": fact_id, "payload": dict(payload or {})}
    def tick(self) -> bool:
        self._diagnostic_context = {"phase": "tick"}
        try:
            with self.store.cache_reads(): return self._tick()
        except Exception as exc:
            self._record_exception(exc, **self._diagnostic_context); raise
        finally:
            from .institutional_runtime import RunAccess
            if isinstance(self.institutional_access, RunAccess):
                live = {row["run_id"] for row in self.store.inspect("SELECT t.run_id,t.state FROM workflow_transitions t WHERE t.workflow_kind='RUN' AND NOT EXISTS(SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision)")
                    if row["state"] not in _TERMINAL_RUNS | {RunState.STOPPING.value}}
                self.institutional_access.sync(live, create=False)
            self._diagnostic_context = {}; self._tick_deadline = None
    def _tick(self) -> bool:
        self._collect_role_views()
        self._agent_dispatches = 0
        active_at_start = {row["run_id"] for row in self.store.inspect("SELECT t.run_id,t.state FROM workflow_transitions t WHERE t.workflow_kind='RUN' AND NOT EXISTS(SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision)") if row["state"] not in _TERMINAL_RUNS}
        if self.institutional_access is not None:
            self.institutional_access.tick({run_id for run_id in active_at_start
                if self.store.latest_transition(run_id)["state"] != RunState.STOPPING.value})
        if self.claude_agent_host is not None and hasattr(self.claude_agent_host, "service_tools"):
            self.claude_agent_host.service_tools()
        self._cancel_wallclock_expired_effects()
        progressed = self._poll_agents()
        self._diagnostic_context = {"phase": "poll_processes"}
        if self._poll_processes(): progressed = True
        if self.shared_gpu is not None: self.shared_gpu.reconcile()
        self._diagnostic_context = {"phase": "poll_local_execution_agents"}
        if self._poll_local_execution_agents(): progressed = True
        self._diagnostic_context = {"phase": "dispatch"}
        self._tick_deadline = time.monotonic() + 0.25
        rows = self.store.inspect("SELECT t.* FROM workflow_transitions t WHERE t.workflow_kind='RUN' "
            "AND NOT EXISTS(SELECT 1 FROM workflow_transitions n "
            "WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision) ORDER BY t.run_id")
        for run in rows:
            self._diagnostic_context = {"phase": "dispatch", "run_id": run["run_id"]}
            if run["state"] in _TERMINAL_RUNS or run["state"] == RunState.WAITING_HUMAN.value: continue
            if run["run_id"] in self.dispatch_errors and run["state"] != RunState.STOPPING.value: continue
            try:
                from .provider_recovery import apply_scheduled_retry
                if apply_scheduled_retry(self, run["run_id"]):
                    run = self.store.latest_transition(run["run_id"])
                if run["state"] == RunState.CREATED.value: self._activate(run["run_id"])
                elif run["state"] == RunState.STOPPING.value: self._drive_stopping(run["run_id"])
                elif run["state"] == RunState.COMPLETING.value: self._complete(run["run_id"])
                else: self._drive_active(run)
            except (OperationalHalt, HostConfigurationError, sqlite3.DatabaseError):
                raise
            except Exception as exc:
                # Preserve the failed source and the control interface. Cleanup
                # failures still halt: quarantine must never hide failed shutdown.
                if run["state"] == RunState.STOPPING.value: raise
                self.dispatch_errors[run["run_id"]] = {"exception_type": type(exc).__name__, "phase": "dispatch",
                    "host_generation": self.generation}
                self._record_exception(exc, **{**self._diagnostic_context, "phase": "dispatch_quarantined", "run_id": run["run_id"]})
                continue
            if self._cycle_reports_enabled(str(run["run_id"])):
                current = self.store.latest_transition(str(run["run_id"]))
                if current["state"] in _TERMINAL_RUNS or time.monotonic() - self.projection_refresh_at.get(str(run["run_id"]), 0) >= 1:
                    self._refresh_research_projection(str(run["run_id"]))
            progressed = True
        # Complete deferred validation after dispatch, so its next model action
        # starts on the next tick, with a control/cancellation opportunity first.
        if self._poll_literature(): progressed = True
        if self.paper_reading.tick(): progressed = True
        for run_id in active_at_start:
            self._diagnostic_context = {"phase": "archive", "run_id": run_id}
            if self.store.latest_transition(run_id)["state"] in _TERMINAL_RUNS:
                from .session_archive import archive_run_sessions
                try: archive_run_sessions(self.state_root, run_id)
                except (OSError, ValueError) as exc: self._record_exception(exc, phase="archive", run_id=run_id); self.archive_errors[run_id] = str(exc)[:2048]
                else: self.archive_errors.pop(run_id, None)
        for run in rows:
            if run["run_id"] in self._projection_jobs or (run["state"] in _TERMINAL_RUNS
                    and self._cycle_reports_enabled(run["run_id"])):
                self._refresh_research_projection(run["run_id"])
        self._diagnostic_context = {"phase": "terminal_exit"}
        self.terminal_exit_ready = bool(rows) and all(row["state"] in _TERMINAL_RUNS for row in rows) and self._backup_busy_reason() is None
        return progressed
    def _collect_role_views(self):
        for run_id, (command, future) in tuple(self._role_view_jobs.items()):
            if not future.done(): continue
            self._role_view_jobs.pop(run_id)
            try:
                future.result()
            except Exception as exc:
                self._role_view_errors[command] = str(exc)
                self._record_exception(exc, phase="private_projection", run_id=run_id)
            else:
                self._role_views_ready.add(command)

    def _prepare_role_view(self, run_id, role, task):
        from .research_projection import freeze_role_view, ResearchProjectionError
        self._collect_role_views()
        if task.command_id in self._role_view_errors:
            raise ResearchProjectionError(self._role_view_errors[task.command_id])
        if task.command_id in self._role_views_ready: return True
        if run_id not in self._role_view_jobs:
            # One preparation per run, bound to the exact logical node. Normal
            # admission is checked again on the next dispatch before any intent.
            view_id = str(uuid.uuid5(uuid.NAMESPACE_URL, task.command_id))
            self._role_view_jobs[run_id] = (task.command_id, self._projection_executor.submit(
                freeze_role_view, self.state_root, run_id, role, view_id))
        return False

    def _collect_research_projection(self, run_id: str) -> None:
        from .research_projection import publish_research_projection
        job = self._projection_jobs.get(run_id)
        if job is None or not job[0].done(): return
        future, changes, state = self._projection_jobs.pop(run_id)
        prepared = None
        try:
            prepared = future.result()
            current = self.store.latest_transition(run_id)["state"]
            if current in _TERMINAL_RUNS and state != current:
                return  # Never publish an old active view after terminal closure.
            publish_research_projection(prepared)
            self.projection_changes[run_id] = changes
            self.projection_errors.pop(run_id, None)
        except Exception as exc:
            self._record_exception(exc, phase="projection", run_id=run_id)
            self.projection_errors[run_id] = str(exc)[:2048]
            if self.store.latest_transition(run_id)["state"] in _TERMINAL_RUNS:
                self.projection_changes[run_id] = changes
        finally:
            if prepared is not None and prepared[0].exists():
                # Only disposable derived files; no research state is removed.
                try: shutil.rmtree(prepared[0])
                except OSError as exc:
                    self._record_exception(exc, phase="projection_cleanup", run_id=run_id)
                    self.projection_errors[run_id] = str(exc)[:2048]

    def _refresh_research_projection(self, run_id: str) -> None:
        from .research_projection import prepare_research_projection
        self._collect_research_projection(run_id)
        changes = self.store.total_changes
        self.projection_refresh_at[run_id] = time.monotonic()
        if run_id in self._projection_jobs or self.projection_changes.get(run_id) == changes:
            return
        state = self.store.latest_transition(run_id)["state"]
        self._projection_jobs[run_id] = (self._projection_executor.submit(
            prepare_research_projection, self.state_root, run_id), changes, state)
    def _cancel_wallclock_expired_effects(self) -> None:
        for handle in tuple(self.running_agents.values()):
            if self._run_wallclock_expired(handle.request.run_id): self.claude_agent_host.cancel(handle)  # type: ignore[union-attr]
        for attempt_id, handle in tuple(self.running.items()):
            attempt = self.store.get_attempt(attempt_id); self.runner.cancel(handle) if attempt is not None and self._run_wallclock_expired(attempt["run_id"]) else None
    def _run_wallclock_expired(self, run_id: str) -> bool:
        run = self.store.get_run(run_id); return run is not None and wallclock_expired(self._research_contract(run_id).document, run["created_at"])
    def _activate(self, run_id: str) -> None:
        with self.store.transaction() as tx:
            tx.append_transition(self._transition_record(tx, run_id, run_id, "RUN", Command.ACTIVATE, RunState.ACTIVE, next_command=Command.REQUEST_HYPOTHESIS_CANDIDATE.value))
    def _rendezvous_records(self, run_id: str) -> tuple[Any, ...]:
        rows = self.store.inspect("SELECT c.command_type,t.payload_json FROM workflow_transitions t JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id=? ORDER BY t.rowid", (run_id,)); return derive_rendezvous_records((row["command_type"], json.loads(row["payload_json"])) for row in rows)
    def _research_contract(self, run_id: str) -> ResearchContract:
        return self.store.cached_read(("research-contract", run_id), lambda: self._read_research_contract(run_id))
    def _read_research_contract(self, run_id: str) -> ResearchContract:
        rows = self.store.list_facts(run_id, "GoalDefined")
        if len(rows) != 1: raise OperationalHalt(f"run {run_id!r} lacks one GoalDefined fact")
        raw = rows[0]["payload"].get("research_contract")
        if not isinstance(raw, Mapping): raise OperationalHalt("GoalDefined lacks its component contract")
        return ResearchContract.from_mapping(raw)
    @staticmethod
    def _agent_attempt_limits(contract: ResearchContract) -> tuple[int, int]:
        try: return agent_attempt_limits(contract.document, model_fallback=int(_CONFIG["agent_attempt_cap"]), infrastructure_fallback=int(_CONFIG["agent_infrastructure_attempt_cap"]))
        except ValueError as exc: raise OperationalHalt(str(exc)) from exc
    @staticmethod
    def _effect_attempt_cap(contract: ResearchContract, kind: str) -> int:
        try: return effect_attempt_cap(contract.document, kind, model_fallback=int(_CONFIG["agent_attempt_cap"]), infrastructure_fallback=int(_CONFIG["agent_infrastructure_attempt_cap"]), process_fallback=int(_CONFIG["process_attempt_cap"]))
        except ValueError as exc: raise OperationalHalt(str(exc)) from exc
    @staticmethod
    def _agent_attempts_exhausted(contract: ResearchContract, attempts: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]) -> bool:
        try: return agent_attempts_exhausted(contract.document, attempts, model_fallback=int(_CONFIG["agent_attempt_cap"]), infrastructure_fallback=int(_CONFIG["agent_infrastructure_attempt_cap"]))
        except (KeyError, TypeError, ValueError) as exc: raise OperationalHalt(f"Agent retry history is invalid: {exc}") from exc
    @staticmethod
    def _review_role(contract: ResearchContract, component_id: str, phase: str) -> str | None:
        campaigns = contract.document["methodology"].get("controls", {}).get("experiment_campaigns", {})
        if not campaigns.get("enabled") or phase not in campaigns.get("required_roles", ()): return None
        owners = campaigns.get("role_owners", {}); selected = owners.get(component_id, owners.get("*", {}))
        role = selected.get(phase) if isinstance(selected, Mapping) else None
        if role not in contract.role_ids: raise OperationalHalt(f"{phase} role is absent from the frozen roster")
        return str(role)
    def _review_status(self, run_id: str, component_id: str, phase: str, experiment_id: str | None = None) -> tuple[RendezvousOrigin | None, Any]:
        origins, _, replies = self._rendezvous_records(run_id); matches = [item for item in origins if item.component_id == component_id and item.phase == phase]
        if experiment_id is not None: matches = [item for item in matches if self._review_experiment(item) == experiment_id]
        origin = matches[-1] if matches else None; return origin, None if origin is None else replies.get(origin.rendezvous_id)
    def _review_experiment(self, origin: RendezvousOrigin) -> str | None:
        rows = self.store.inspect("SELECT workflow_id FROM workflow_transitions WHERE json_extract(payload_json,'$.rendezvous_origin.rendezvous_id')=?", (origin.rendezvous_id,))
        if len(rows) == 1: return str(rows[0]["workflow_id"])
        source = self.store.get_attempt(origin.source_attempt_id)
        return None if source is None else str(source["workflow_id"])
    def _tool_execution_enabled(self, run_id: str) -> bool:
        record = self.store.get_run(run_id)
        return record is not None and supports_experiment_submission(record["agent_aci_version"]) and _uses_expert_loop(self._research_contract(run_id))
    def _batch_enabled(self, run_id: str) -> bool:
        record = self.store.get_run(run_id)
        return record is not None and supports_hypothesis_batches(int(record["agent_aci_version"])) and _uses_expert_loop(self._research_contract(run_id))
    def _batch_history(self, run_id: str, component_id: str, role: str | None = None) -> BatchHistory:
        component = self._research_contract(run_id).component(component_id)
        if component is None: raise OperationalHalt("unknown batch component")
        try: return BatchHistory.from_rows(run_id, component_id, role or component.owner_role,
            self.store.inspect("SELECT t.*,c.command_type FROM workflow_transitions t JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id=? ORDER BY t.rowid", (run_id,)),
            self.store.list_facts(run_id, "HypothesisProposed"), agent_aci_version=int(self.store.get_run(run_id)["agent_aci_version"]))
        except (KeyError, TypeError, ValueError) as exc: raise OperationalHalt(str(exc)) from exc
    def _agent_budget_available(self, run_id: str, role: str) -> bool:
        return not self._run_wallclock_expired(run_id) and remaining_agent_budget(
            self._research_contract(run_id).document, role,
            self._agent_charges.read(self.store, run_id, committed=True), self.agent_budget) is not None

    def _batch_creation_available(self, run_id: str, role: str) -> bool:
        document = self._research_contract(run_id).document
        rows = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))
        cap = profile_role_cap(document, role, "max_experiments")
        return len(rows) < document["budget"]["max_experiments"] and (cap is None or sum(json.loads(row["payload_json"]).get("role") == role for row in rows) < cap)
    def _effective_batch_forks(self, run_id: str, role: str) -> int:
        from .manager_context import effective_forks
        maximum = batch_limits(self._research_contract(run_id).document, role)["forks"]
        return effective_forks(self.store.inspect("SELECT t.*,c.command_type FROM workflow_transitions t JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.run_id=? ORDER BY t.rowid", (run_id,)), role, default=maximum, maximum=maximum)
    def _ensure_graph_evolution(self, report: ResearchNodeTask) -> bool:
        run_id = report.key.run_id
        product = self._research_contract(run_id).document.get("methodology", {}).get("controls", {}).get("product", {})
        if not product or not product.get("methodology", {}).get("graph_evolution", {}).get("enabled", True): return True
        rows = self.store.inspect("SELECT revision FROM workflow_transitions WHERE workflow_id=? AND command_id=?", (run_id, report.command_id))
        if len(rows) != 1 or self._expert_success(report) is None: raise OperationalHalt("manager trigger lacks its accepted report command")
        task = AuxiliaryTask(kind=AuxiliaryRequestKind.GRAPH_EVOLVE, run_id=run_id, cycle=report.key.cycle, ordinal=int(rows[0]["revision"]))
        if self._expert_success(task) is not None or self._expert_task_final_failure(run_id, task) is not None: return True
        if self._expert_task_exhausted(run_id, task):
            self._record_expert_final_failure(run_id, "grounding", "grounding", task, "manager_exhausted"); return True
        self._expert_agent_step(run_id, run_id, task, "grounding", "grounding")
        return self._expert_success(task) is not None
    def _manager_inputs(self, run_id: str, task: AuxiliaryTask) -> tuple[EmbeddedInput, ...]:
        contract = self._research_contract(run_id); document = contract.to_dict()
        rows = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=?", (run_id, task.ordinal))
        if len(rows) != 1: raise OperationalHalt("manager trigger report revision is absent")
        report = ResearchNodeTask(key=ResearchNodeKey.from_dict(json.loads(rows[0]["payload_json"])["task_context"]["key"]))
        if report.key.node_kind is not ResearchNodeKind.CYCLE_REPORT or report.key.cycle != task.cycle: raise OperationalHalt("manager trigger is not its report/cycle")
        source = self._expert_response_input(report)
        config = {"goal": document["goal"], "grounding_scope": "role-neutral", "execution": self._run_config(run_id)["execution"]}
        apply_profile_language(config, document, task)
        maximum = config.get("node_runtime", {}).get("memory", {}).get("max_plugin_bytes", 1024 * 1024)
        plugin = self._plugin_binding(run_id, task, "grounding", "Manage the shared research graph and trigger skills.", max_bytes=maximum, owner_kind="grounding")
        facts = self.store.list_facts(run_id); goal = next(item for item in facts if item["fact_type"] == "GoalDefined")
        context = {"max_hypotheses_per_round": {role: batch_limits(document, role)["forks"] for role in contract.role_ids},
            "effective_max_forks": {role: self._effective_batch_forks(run_id, role) for role in contract.role_ids},
            "exploration_progress": [self._batch_history(run_id, str(c.component_id), c.owner_role).exploration_progress(
                self._active_expert_chains(run_id, c.owner_role)) for c in contract.components],
            "source_report_refs": [source.ref], "enabled": True,
            "graph": [{"fact_id": item["fact_id"], "fact_type": item["fact_type"], "payload": item["payload"]} for item in facts if item is not goal]}
        settings = document["methodology"].get("controls", {}).get("product", {}).get("methodology", {}).get("graph_evolution", {})
        context["settings"] = settings
        window = settings.get("stagnation_rounds")
        context["stagnation_guidance"] = (f"Review available accepted facts and reports for scientific progress over {window} completed rounds. "
            "This is an advisory review window, not a mandatory trigger or scientific verdict; missing rounds or unchanged scores do not require a graph change."
            if window is not None else "Review actual accepted scientific progress; no Profile stagnation window was specified.")
        return (EmbeddedInput.from_bytes(str(goal["fact_id"]), "application/json", _canonical({"fact_id": goal["fact_id"], "fact_type": goal["fact_type"], "payload": goal["payload"]})),
            EmbeddedInput.from_bytes(f"{task.command_id}--grounding-config", "application/json", _canonical(config)), source,
            EmbeddedInput.from_bytes(f"{task.command_id}--grounding-resources", "application/json", _canonical({"plugin": plugin.to_dict()})),
            EmbeddedInput.from_bytes(f"{task.command_id}--manager-context", "application/json", _canonical(context)))
    def _ensure_final_report(self, run_id: str) -> bool:
        if not self._batch_enabled(run_id): return True
        task = AuxiliaryTask(kind=AuxiliaryRequestKind.FINAL_REPORT, run_id=run_id)
        if self._expert_success(task) is not None: return True
        if self._expert_task_exhausted(run_id, task):
            self._record_expert_final_failure(run_id, "grounding", "grounding", task, "final_report_exhausted")
            failure = self._expert_task_final_failure(run_id, task)
            stopping = self.store.latest_transition(run_id)["state"] == RunState.STOPPING.value
            if self.store.inspect("SELECT 1 FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.run_id=? AND t.workflow_id=? AND c.command_type='FINAL_REPORT_FAILED'", (run_id, run_id)): return stopping
            with self.store.transaction() as tx:
                tx.append_transition(self._transition_record(tx, run_id, run_id, "RUN", Command.FINAL_REPORT_FAILED, RunState.STOPPING if stopping else RunState.FAILED,
                    payload={"report_missing": True, "source_attempt_id": failure["attempt_id"], "final_failure": dict(failure),
                        **({"stop_disposition": "FAILED"} if stopping else {})}))
            return stopping
        self._expert_agent_step(run_id, run_id, task, "grounding", "grounding")
        return self._expert_success(task) is not None
    def _final_report_inputs(self, run_id: str, task: AuxiliaryTask) -> tuple[EmbeddedInput, ...]:
        attempts = self._expert_attempts(task)
        if attempts:
            first = AgentRequest.from_dict(json.loads(attempts[0]["observation_json"])["request"])
            return tuple(item for item in first.input_artifacts if item.ref in first.immutable_context_refs)
        document = self._research_contract(run_id).to_dict(); facts = self.store.list_facts(run_id)
        verdicts = [item for item in facts if item["fact_type"] == "GoalVerdictRecorded"]
        stopping = self.store.latest_transition(run_id)["state"] == RunState.STOPPING.value
        if len(verdicts) > 1 or not stopping and len(verdicts) != 1: raise OperationalHalt("normal completion report requires its accepted scientific verdict")
        controls = self._stop_controls(run_id) if stopping else ()
        if stopping and not controls: raise OperationalHalt("stop report requires its actual stop command")
        config = {"goal": document["goal"], "grounding_scope": "role-neutral", "execution": self._run_config(run_id)["execution"]}
        apply_profile_language(config, document, task)
        maximum = config.get("node_runtime", {}).get("memory", {}).get("max_plugin_bytes", 1024 * 1024)
        plugin = self._plugin_binding(run_id, task, "grounding", "Report the actual accepted research results and stop reason.", max_bytes=maximum, owner_kind="grounding")
        context = {"facts": [{name: row[name] for name in ("fact_id", "fact_type", "run_id", "payload_json")} for row in facts],
            "stop_reason": (f"As of the first report request, the run is stopping with disposition {self._stop_disposition(run_id).value}. Actual stop controls: "
                + _canonical(list(controls)).decode() if stopping else
                f"As of the first report request, Research Policy recorded {verdicts[0]['payload']['verdict']}. Lifecycle closure alone does not establish completed research."),
            **({"stop_controls": list(controls)} if stopping else {})}
        from .final_report import accepted_evaluation_summary_inputs, declared_measurement_inputs, run_outcome_context
        context["run_outcome"] = outcome = run_outcome_context(facts,
            [row for row in self.store.list_attempts() if row["run_id"] == run_id], self._expert_final_failures(run_id),
            artifacts=self.store.inspect("SELECT artifact_id,execution_id,attempt_id FROM execution_artifacts WHERE run_id=?", (run_id,)))
        summary_inputs, summary_index = accepted_evaluation_summary_inputs(
            facts,
            lambda outcome_id, artifact_id, logical_id: self._logical_sources(
                run_id, outcome_id, artifact_id, logical_id),
            max_bytes=maximum,
        )
        context["accepted_evaluation_summaries"] = summary_index
        raw_inputs, raw_index = declared_measurement_inputs(facts,
            lambda outcome_id, artifact_id: self._declared_measurement_sources(run_id, outcome_id, artifact_id),
            max_bytes=maximum - summary_index["bytes_included"])
        context["declared_raw_measurements"] = raw_index
        context["stop_reason"] += (f" Actual progress: {outcome['expert_nodes_started']} expert nodes started, "
            f"{outcome['fact_counts']['HypothesisProposed']} hypotheses, "
            f"{outcome['fact_counts']['ExperimentPreregistered']} experiments, "
            f"{outcome['fact_counts']['CanonicalOutcomeAccepted']} accepted outcomes.")
        if outcome["final_failures"]:
            context["stop_reason"] += " Exhausted/nonretryable Agent nodes: " + _canonical(outcome["final_failures"]).decode()
        failed_reports = []
        for failure in self._expert_final_failures(run_id):
            failed = self.store.get_attempt(failure["attempt_id"])
            request = AgentRequest.from_dict(json.loads(failed["observation_json"])["request"])
            if request.command is AgentCommand.CYCLE_REPORT:
                failed_reports.append(self._expert_failure_input(run_id, request.task_context))
                context["stop_reason"] += (f" Required CYCLE_REPORT {failed['attempt_id']} permanently failed "
                    f"({failure['failure_code']}); no successful memory report was published for that node.")
        inputs = [EmbeddedInput.from_bytes(f"{task.command_id}--grounding-config", "application/json", _canonical(config)),
            EmbeddedInput.from_bytes(f"{task.command_id}--grounding-resources", "application/json", _canonical({"plugin": plugin.to_dict()})),
            EmbeddedInput.from_bytes(f"{task.command_id}--final-report-context", "application/json", _canonical(context))]
        inputs.extend((*summary_inputs, *raw_inputs))
        inputs.extend(failed_reports)
        for row in self.store.list_attempts():
            if row["run_id"] == run_id and row["kind"] == "AGENT" and row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
                observation = json.loads(row["observation_json"])
                if observation.get("request", {}).get("command") in {"GROUNDING", "CYCLE_REPORT"}:
                    inputs.append(EmbeddedInput.from_bytes(f"{row['attempt_id']}--response", "application/json", _canonical(observation["response"])))
        return tuple(inputs)
    @staticmethod
    def _is_final_report_request(request: AgentRequest) -> bool:
        return request.schema_version >= 14 and request.command is AgentCommand.FINAL_REPORT and isinstance(request.task_context, AuxiliaryTask) and request.task_context.kind is AuxiliaryRequestKind.FINAL_REPORT
    def _stop_controls(self, run_id: str) -> tuple[dict[str, Any], ...]:
        return tuple({"command_id": row["command_id"], "revision": row["revision"], "command_type": row["command_type"], "payload": json.loads(row["payload_json"])}
            for row in self.store.inspect("SELECT t.*,c.command_type FROM workflow_transitions t JOIN workflow_commands c USING(command_id) WHERE t.run_id=? AND t.workflow_id=? AND (c.command_type IN('CANCEL','FAIL_INTEGRITY','RESOLVE_CANCEL','FINAL_REPORT_FAILED') OR (c.command_type='RECORD_GOAL_VERDICT' AND t.state='STOPPING')) ORDER BY t.revision", (run_id, run_id)))
    def _stop_disposition(self, run_id: str) -> RunState:
        controls = self._stop_controls(run_id)
        if any(row["command_type"] in {"FAIL_INTEGRITY", "FINAL_REPORT_FAILED"} or row["payload"].get("stop_disposition") == "FAILED" for row in controls): return RunState.FAILED
        return RunState.COMPLETED if any(row["command_type"] == "RECORD_GOAL_VERDICT" and row["payload"].get("stop_disposition") == "COMPLETED" for row in controls) else RunState.CANCELLED
    def _shared_goal(self, run_id: str) -> bool:
        return self._batch_enabled(run_id) and (self.shared_acceptance.enabled(run_id) or self._research_contract(run_id).document.get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {}).get("completion_mode") == "shared_goal")
    def _begin_shared_goal_stop(self, run_id: str) -> bool:
        if not self._shared_goal(run_id) or not self.store.list_facts(run_id, "HypothesisAssessed"): return False
        snapshot = self._policy_snapshot(self.store.latest_transition(run_id))
        decision = self.policy.next_command(snapshot)
        if decision is not None and decision.command == Command.RECORD_GOAL_VERDICT.value and decision.payload["verdict"] == GoalVerdict.GOAL_MET.value:
            self._record_verdict(run_id, decision); return True
        return False
    def _active_expert_chains(self, run_id: str, role: str) -> set[tuple[str, str]]:
        active = set()
        for row in self.store.inspect("SELECT * FROM execution_attempts WHERE run_id=? AND state='OPEN'", (run_id,)):
            if row["kind"] == "AGENT":
                request = json.loads(row["observation_json"]).get("request", {}); key = request.get("task_context", {}).get("key", {})
                if request.get("role") == role and key.get("chain") is not None: active.add((request["component_id"], key["chain"]))
            else:
                creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (row["workflow_id"],))
                identity = {} if not creation else json.loads(creation[0]["payload_json"])
                if identity.get("role") == role and identity.get("chain") is not None: active.add((identity["component_id"], identity["chain"]))
        return active
    def _effect_capacity(self, run_id: str, kind: str = "AGENT") -> int:
        from .agent_budget import profile_effect_capacity
        contract = self._research_contract(run_id)
        return profile_effect_capacity(contract.document, kind)
    def _at_capacity(self, run_id: str | None = None, kind: str = "AGENT") -> bool:
        if run_id is not None and self._tool_execution_enabled(run_id):
            sql = "SELECT COUNT(*) AS n FROM execution_attempts WHERE run_id=? AND state='OPEN' AND kind=?"
            if kind in {"AGENT", "REVIEW"}: sql += " AND (json_extract(observation_json,'$.request.command')='ANSWER_RENDEZVOUS')=" + ("1" if kind == "REVIEW" else "0")
            return int(self.store.inspect(sql, (run_id, "AGENT" if kind == "REVIEW" else kind))[0]["n"]) >= self._effect_capacity(run_id, kind)
        return int(self.store.inspect("SELECT COUNT(*) AS n FROM execution_attempts WHERE state='OPEN'")[0]["n"]) >= 3
    def _participant_wait_reason(self, run_id: str, role: str, *, execution_node_command_id: str | None = None,
                          component_id: str | None = None, chain: str | None = None, proposal_task=None) -> str | None:
        if self._batch_enabled(run_id) and chain is not None:
            active = self._active_expert_chains(run_id, role)
            if (component_id, chain) not in active and len(active) >= batch_limits(self._research_contract(run_id).document, role)["parallel_chains"]: return "chain_capacity"
            for row in self.store.inspect("SELECT * FROM execution_attempts WHERE run_id=? AND state='OPEN'", (run_id,)):
                if row["kind"] == "AGENT":
                    request = json.loads(row["observation_json"]).get("request", {}); key = request.get("task_context", {}).get("key", {})
                    if request.get("role") == role and request.get("component_id") == component_id and key.get("chain") == chain and row["command_id"] != execution_node_command_id: return "chain_busy"
                else:
                    creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (row["workflow_id"],))
                    identity = {} if not creation else json.loads(creation[0]["payload_json"])
                    if identity.get("role") == role and identity.get("component_id") == component_id and identity.get("chain") == chain and identity.get("execution_node_command_id") != execution_node_command_id: return "chain_busy"
            return None
        busy = self.store.inspect("SELECT a.* FROM execution_attempts a JOIN workflow_transitions t ON t.workflow_id=a.workflow_id AND t.command_id=a.command_id WHERE a.run_id=? AND a.state='OPEN' AND json_extract(t.payload_json,'$.role')=? AND NOT((a.kind='AGENT' AND a.command_id=COALESCE(?,'')) OR (a.kind='PROCESS' AND EXISTS(SELECT 1 FROM workflow_transitions creation WHERE creation.workflow_id=a.workflow_id AND creation.revision=1 AND json_extract(creation.payload_json,'$.execution_node_command_id')=COALESCE(?,''))))", (run_id, role, execution_node_command_id, execution_node_command_id))
        version = int(self.store.get_run(run_id)["agent_aci_version"])
        return "expert_busy" if any(row["kind"] != "AGENT" or not proposal_siblings(proposal_task,
            (json.loads(row["observation_json"]).get("request", {}).get("task_context") or {}).get("key", {}), version) for row in busy) else None
    def _participant_busy(self, run_id, role, **kwargs) -> bool:
        return self._participant_wait_reason(run_id, role, **kwargs) is not None
    def _busy_gpu_uuids(self, exclude_workflow: str | None = None) -> tuple[str, ...]:
        rows = self.store.inspect("SELECT a.workflow_id,COALESCE(json_extract(f.payload_json,'$.execution_contract.resource_grant'),(SELECT json_extract(t.payload_json,'$.resource_grant') FROM workflow_transitions t WHERE t.run_id=a.run_id AND t.workflow_id=a.workflow_id AND json_extract(t.payload_json,'$.attempt_id')=a.attempt_id AND json_type(t.payload_json,'$.resource_grant')='object')) resource_grant FROM execution_attempts a JOIN research_facts f ON f.run_id=a.run_id AND f.fact_type='ExperimentPreregistered' AND f.execution_id=a.execution_id WHERE a.kind='PROCESS' AND a.state='OPEN'")
        return tuple(sorted({uuid for row in rows if row["workflow_id"] != exclude_workflow and row["resource_grant"] is not None
            for uuid in ResourceGrant.from_dict(json.loads(row["resource_grant"])).device_uuids}))
    def _resource_experience(self, run_id: str, role: str, component_id: str, method_family: str) -> Mapping[str, object]:
        current = self.store.inspect("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,))[0]; current_document = self._research_contract(run_id).document
        compatible_runs = {str(row["run_id"]) for row in self.store.inspect("SELECT * FROM workflow_runs") if all(row[field] == current[field] for field in _VERSION_FIELDS) and self._research_contract(str(row["run_id"])).document == current_document}; records = []
        if self.shared_gpu is not None: compatible_runs = {run_id}
        for attempt in self.store.list_attempts():
            if attempt["run_id"] not in compatible_runs or attempt["kind"] != "PROCESS" or attempt["state"] != "CLOSED": continue
            spec = self.store.find_fact(attempt["run_id"], "ExperimentPreregistered", attempt["experiment_id"]); creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (attempt["workflow_id"],))
            if spec is None or attempt["execution_id"] != spec["execution_id"] or len(creation) != 1: continue
            identity = json.loads(creation[0]["payload_json"]); payload = spec["payload"]; observation = json.loads(attempt["observation_json"])
            if identity.get("role") != role or payload.get("component_id") != component_id or payload.get("method_family_id") != method_family: continue
            grant = payload.get("execution_contract", {}).get("resource_grant")
            if payload.get("execution_contract", {}).get("resource_intent") is not None:
                dispatches = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_id=? AND json_extract(payload_json,'$.attempt_id')=? AND json_type(payload_json,'$.resource_grant')='object'", (attempt["run_id"], attempt["workflow_id"], attempt["attempt_id"]))
                grant = None if len(dispatches) != 1 else json.loads(dispatches[0]["payload_json"])["resource_grant"]
            records.append({"kind": "PROCESS", "state": "CLOSED", "role": identity.get("role"), "component_id": payload.get("component_id"), "method_family": payload.get("method_family_id"), "resource_preference": payload.get("resource_preference"),
                "resource_grant": grant, "terminal_status": attempt["terminal_reason"], "ended_at": attempt["ended_at"], "resource_usage": observation.get("resource_usage")})
        result = summarize_resource_experience(records)
        return result if self.shared_gpu is None else {**result, "mode": "shared-pool", "measurements": self.shared_gpu.measurements(run_id, role, component_id, method_family)}
    def _resolve_rendezvous(self, run_id: str, origin: RendezvousOrigin, disposition: RendezvousDisposition, summary: str, responder: str = "WorkflowHost") -> None:
        with self.store.transaction() as tx:
            if (head := tx.latest_transition(run_id)) is None: raise OperationalHalt("rendezvous owner Run is absent")
            tx.append_transition(self._transition_record(tx, run_id, run_id, "RUN", Command.RESOLVE_RENDEZVOUS, RunState(head["state"]),
                payload={"rendezvous_id": origin.rendezvous_id, "requester_slot": origin.requester_slot, "rendezvous_reply": origin.terminal_reply(disposition, summary, responder).to_dict()}))
    def _drive_rendezvous(self, run_id: str) -> None:
        origins, requests, replies = self._rendezvous_records(run_id); roles = self._research_contract(run_id).role_ids
        for origin in origins:
            if origin.rendezvous_id in replies: continue
            if origin.target_role is None:
                self._resolve_rendezvous(run_id, origin, RendezvousDisposition.NO_TARGET if len(roles) == 1 else RendezvousDisposition.INTEGRITY_REJECTED, "No eligible peer target exists in the frozen roster."); return
            request = requests.get(origin.rendezvous_id) or origin.request(run_id); source = self.store.get_attempt(origin.source_attempt_id)
            if source is None: raise OperationalHalt("rendezvous source Agent attempt is absent")
            observation = json.loads(source["observation_json"])
            if source["kind"] == "AGENT":
                source_request = AgentRequest.from_dict(observation["request"]); candidate = observation.get("response", {}).get("typed_candidate", {})
                component_id = source_request.component_id; family = source_request.method_family_id or (candidate.get("method_family_id") if isinstance(candidate, Mapping) else None)
            else:
                spec = self.store.find_fact(run_id, "ExperimentPreregistered", source["experiment_id"])
                if spec is None: raise OperationalHalt("analysis rendezvous source lacks preregistration")
                component_id = str(spec["payload"]["component_id"]); family = str(spec["payload"]["method_family_id"])
            if self._participant_busy(run_id, origin.target_role) or self._at_capacity(run_id, "REVIEW"): continue
            self._agent_step(run_id, self._review_experiment(origin) or str(source["workflow_id"]), AgentCommand.ANSWER_RENDEZVOUS, origin.target_role,
                             component_id, family, request); return
    def _policy_snapshot(self, run: Mapping[str, Any], *, chain_context: ResearchNodeKey | None = None) -> PolicySnapshot:
        run_id = str(run["run_id"]); contract = self._research_contract(run_id)
        facts = self.store.list_facts(run_id); by_kind: dict[str, list[Mapping[str, Any]]] = {}
        for fact in facts: by_kind.setdefault(str(fact["fact_type"]), []).append(fact)
        hypotheses = ({(str(component.component_id), component.owner_role): chain[-1]
            for component in contract.components if (chain := self._expert_hypothesis_chain(run_id, str(component.component_id), component.owner_role))}
            if _uses_expert_loop(contract) else {(str(fact["payload"]["component_id"]), str(fact["payload"]["proposer_role"])): fact for fact in by_kind.get("HypothesisProposed", [])})
        batch_mode = self._batch_enabled(run_id)
        histories = ({str(item.component_id): self._batch_history(run_id, str(item.component_id)) for item in contract.components} if batch_mode else {})
        if batch_mode and int(self.store.get_run(run_id)["agent_aci_version"]) >= 17:
            for component_id, history in histories.items():
                if history.latest is not None and (component_id, history.role) not in hypotheses:
                    # This scalar proves that the proposal slot was filled, not that a result won.
                    # Only selected_binding below can bind an experiment or scientific outcome.
                    hypotheses[(component_id, history.role)] = history.hypotheses[min(history.latest.members.values())]
        if chain_context is not None:
            hypotheses[(chain_context.component_id, chain_context.expert_slot)] = histories[chain_context.component_id].hypothesis(chain_context.cycle, chain_context.chain)
        heads = {str(row["workflow_id"]): row for row in self.store.list_workflow_heads(run_id, "EXPERIMENT")}
        creations = {str(row["workflow_id"]): json.loads(row["payload_json"]) for row in self.store.inspect("SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))}
        role_experiments = {role: sum(payload.get("role") == role for payload in creations.values()) for role in contract.role_ids}
        chains: dict[str, list[tuple[int, str, Mapping[str, Any]]]] = {}; cycle_revisions: dict[str, int] = {}
        for workflow_id, payload in creations.items():
            if isinstance(ordinal := payload.get("experiment_ordinal", 1), bool) or not isinstance(ordinal, int) or ordinal < 1: raise OperationalHalt("experiment ordinal is invalid")
            chains.setdefault(str(payload["component_id"]), []).append((ordinal, workflow_id, payload))
        for component_id, chain in chains.items():
            chain.sort(); ordinals = [item[0] for item in chain]; cycles = [int(item[2].get("expert_cycle", 1)) for item in chain]; cycle_revisions[component_id] = cycles.count(max(cycles)) - 1
            if ordinals != list(range(1, len(chain) + 1)): raise OperationalHalt(f"component {component_id!r} experiment chain is not contiguous")
        outcomes = {str(row["experiment_id"]): row for row in by_kind.get("CanonicalOutcomeAccepted", [])}; evidence = {str(row["outcome_id"]): row for row in by_kind.get("EvidenceLinked", [])}
        assessments = {(str(row["payload"]["component_id"]), str(row["hypothesis_id"])): row for row in by_kind.get("HypothesisAssessed", [])}; specs = {str(row["experiment_id"]): row for row in by_kind.get("ExperimentPreregistered", [])}
        run_record = self.store.get_run(run_id)
        if run_record is None: raise OperationalHalt(f"run {run_id!r} is absent")
        charges = self.store.cached_read(("agent-charges", run_id),
            lambda: self._agent_charges.read(self.store, run_id, committed=True))
        run_wallclock_expired = wallclock_expired(contract.document, run_record["created_at"])
        origins, _, replies = self._rendezvous_records(run_id); pending = {item.rendezvous_id for item in origins if item.rendezvous_id not in replies}
        reviews = {(self._review_experiment(origin), origin.phase): origin for origin in origins}
        open_rows = self.store.inspect("SELECT a.workflow_id,a.kind,t.payload_json FROM execution_attempts a JOIN workflow_transitions t ON t.workflow_id=a.workflow_id AND t.command_id=a.command_id WHERE a.run_id=? AND a.state='OPEN'", (run_id,)); open_effects = [(str(row["workflow_id"]), str(row["kind"]), json.loads(row["payload_json"])) for row in open_rows]
        busy_roles = {str(payload["role"]) for _, _, payload in open_effects if isinstance(payload.get("role"), str) and (not batch_mode or payload["role"] in contract.role_ids)}
        failed_proposals: set[tuple[str, str]] = set(); failed_specs: set[str] = set(); blocked_workflows: set[str] = set()
        grounding_failed = False
        grounding_command_ids = {AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id=run_id).command_id, self._grounding_task(run_id).command_id}
        for row in self.store.inspect("SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=?", (run_id,)):
            payload = json.loads(row["payload_json"]); component_id, role = payload.get("component_id"), payload.get("role")
            if payload.get("agent_final_failure") and isinstance(component_id, str) and isinstance(role, str):
                grounding_failed = grounding_failed or payload.get("failed_task_command_id") in grounding_command_ids
                if row["workflow_id"] != run_id:
                    failed_specs.add(str(row["workflow_id"]))
                elif (component_id, role) not in hypotheses:
                    failed_proposals.add((component_id, role))
            if payload.get("rendezvous_id") in pending: blocked_workflows.add(str(row["workflow_id"]))
        components = []
        for component in contract.components:
            component_id = str(component.component_id); chain = chains.get(component_id, []); tip = chain[-1] if chain else None
            context = chain_context if chain_context is not None and chain_context.component_id == component_id else None
            history = histories.get(component_id)
            if history is not None:
                selected_binding = history.selected_tip
                # A validated shared-goal result does not wait for batch selection.
                supported = sorted((row for row in by_kind.get("HypothesisAssessed", [])
                    if row["payload"].get("component_id") == component_id and self.shared_acceptance.assessment_qualifies(run_id, row, evidence, outcomes)), key=lambda row: str(row["hypothesis_id"])) if context is None and self._shared_goal(run_id) else []
                if supported:
                    winner = supported[0]
                    linked = {item["outcome_id"] for item in evidence.values() if item["subject_id"] in winner["payload"]["evidence_ids"]}
                    selected_experiments = {item["experiment_id"] for item in outcomes.values() if item["outcome_id"] in linked}
                    winning_tip = max((item for item in chain if item[1] in selected_experiments), key=lambda item: item[0])
                    selected_binding = {"experiment_id": winning_tip[1]}
                chain = ([item for item in chain if item[2].get("chain") == context.chain and item[2].get("expert_cycle") == context.cycle
                          and item[2].get("chain_iteration") == context.experiment_ordinal]
                    if context is not None else [item for item in chain if selected_binding is not None and item[1] == selected_binding["experiment_id"]])
                tip = chain[-1] if chain else None
            experiment_id = None if tip is None else tip[1]; identity = {} if tip is None else tip[2]; head = None if experiment_id is None else heads.get(experiment_id)
            spec = None if experiment_id is None else specs.get(experiment_id); outcome = None if experiment_id is None else outcomes.get(experiment_id)
            selected = (None if spec is None else str(spec["hypothesis_id"])) or identity.get("hypothesis_id"); selected_fact = next((row for row in by_kind.get("HypothesisProposed", []) if row["hypothesis_id"] == selected), None)
            assessment = None if selected is None else assessments.get((component_id, str(selected)))
            if batch_mode:
                current_evidence = None if outcome is None else evidence.get(str(outcome["outcome_id"]))
                assessment = next((row for row in reversed(by_kind.get("HypothesisAssessed", []))
                    if current_evidence is not None and str(current_evidence["subject_id"]) in row["payload"]["evidence_ids"]), None)
            family = None if selected_fact is None else str(selected_fact["payload"]["method_family_id"])
            gpus = () if spec is None or spec["payload"]["execution_contract"].get("resource_intent") is not None else ResourceGrant.from_dict(spec["payload"]["execution_contract"]["resource_grant"]).device_uuids
            busy_gpu = bool(set(gpus).intersection(self._busy_gpu_uuids(experiment_id)))
            criterion = None
            if outcome is not None and ScientificOutcome(outcome["payload"]["outcome"]) in {ScientificOutcome.SUCCESS, ScientificOutcome.CLEAN_REFUTATION}:
                criterion = ScientificOutcome(outcome["payload"]["outcome"]) is ScientificOutcome.SUCCESS if component.metric is None else component.metric.accepts(outcome["payload"]["metric_value"])
            proposals = tuple(ProposalSnapshot(role=role, hypothesis_id=None if (candidate := hypotheses.get((component_id, role))) is None else str(candidate["hypothesis_id"]), final_failure=candidate is None and (grounding_failed or (component_id, role) in failed_proposals), effect_in_flight=any(kind == "AGENT" and payload.get("component_id") == component_id and payload.get("role") == role for _, kind, payload in open_effects)) for role in component.proposer_roles)
            if batch_mode and selected_fact is not None:
                proposals = tuple(replace(item, hypothesis_id=str(selected), final_failure=False) if item.role == component.owner_role else item for item in proposals)
            proposals = tuple(ProposalSnapshot(role=item.role, hypothesis_id=item.hypothesis_id,
                final_failure=item.final_failure, effect_in_flight=item.effect_in_flight,
                dispatch_paused=self._literature_cycle_paused(run_id, item.role, component_id, 1)) for item in proposals)
            review = None if experiment_id is None else reviews.get((experiment_id, "preflight")); reply = None if review is None else replies.get(review.rendezvous_id)
            draft_attempts = [] if experiment_id is None else [row for row in self.store.list_attempts(workflow_id=experiment_id) if row["kind"] == "AGENT" and json.loads(row["observation_json"]).get("request", {}).get("command") in {AgentCommand.DRAFT_EXPERIMENT_SPEC.value, AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value}]
            validation_attempts = [] if experiment_id is None else list(self.store.list_attempts(execution_id=f"{experiment_id}--candidate-validation")); latest_validation = max(validation_attempts, key=lambda row: (float(row["started_at"]), str(row["attempt_id"])), default=None)
            draft_in_flight = any(row["state"] == "OPEN" for row in draft_attempts); exhausted = self._agent_attempts_exhausted(contract, draft_attempts)
            final_review = reply is not None and reply.disposition is not RendezvousDisposition.ANSWERED and (bool(identity.get("source_submit_command_id")) or reply.disposition is not RendezvousDisposition.DECLINED or exhausted)
            latest_diagnostic = ({} if latest_validation is None or latest_validation["state"] != "CLOSED"
                else json.loads(latest_validation["observation_json"]))
            final_validation = bool(latest_validation is not None and latest_validation["state"] == "CLOSED"
                and latest_validation["terminal_reason"] != AttemptTerminalReason.SUCCEEDED.value
                and not draft_in_flight and (exhausted or latest_diagnostic.get("validation_error") == "profile_wallclock_exhausted"))
            successor_budget_state = role_agent_budget_state(contract.document, component.owner_role, charges, self.agent_budget, wallclock_is_expired=run_wallclock_expired)
            tool_execution = self._tool_execution_enabled(run_id)
            controller_open = tool_execution and self._execution_controller_open(run_id, component_id, chain=None if context is None else context.chain)
            execution_failed = tool_execution and any(item.get("component_id") == component_id and item.get("failure_code") == "execution_node_exhausted" for item in self._expert_final_failures(run_id))
            # Accepted no-selection closes batch execution; the global Policy
            # must not reinterpret its hypotheses as an unbound legacy draft.
            execution_failed = execution_failed or (history is not None and context is None and history.latest is not None
                and ((history.latest.selection is not None and history.selected_tip is None)
                     or (history.latest.selection is None and self._batch_control_closed(run_id, component_id, component.owner_role))))
            pending_hypothesis = None
            if tool_execution and selected is not None and not batch_mode:
                latest = hypotheses.get((component_id, component.owner_role))
                if latest is not None and latest["hypothesis_id"] != selected:
                    pending_hypothesis = str(latest["hypothesis_id"])
                    proposals = tuple(ProposalSnapshot(role=item.role,
                        hypothesis_id=str(selected) if item.role == component.owner_role else item.hypothesis_id,
                        final_failure=item.final_failure, effect_in_flight=item.effect_in_flight, dispatch_paused=item.dispatch_paused) for item in proposals)
            if controller_open and not run_wallclock_expired: successor_budget_state = "available"
            components.append(ComponentSnapshot(component_id=component_id, tier=component.tier.value, owner_role=component.owner_role, proposals=proposals, dependencies=tuple(str(value) for value in component.dependencies),
                input_dependencies=tuple(dict.fromkeys(str(value.produced_by_component) for value in component.required_inputs)),
                experiment_id=None if head is None else experiment_id, selected_hypothesis_id=selected,
                method_family_id=family, experiment_revision=None if head is None else int(head["revision"]), experiment_state=None if head is None else str(head["state"]),
                spec_final_failure=head is not None and head["state"] == ExperimentState.DRAFTED.value and (experiment_id in failed_specs or final_review or final_validation),
                spec_candidate_ready=((review is not None and reply is not None and reply.disposition is RendezvousDisposition.ANSWERED) or (self._review_role(contract, component_id, "preflight") is None and (bool(identity.get("source_submit_command_id")) or latest_validation is not None and latest_validation["state"] == "CLOSED" and latest_validation["terminal_reason"] == AttemptTerminalReason.SUCCEEDED.value))) and experiment_id not in specs,
                outcome=None if outcome is None else ScientificOutcome(outcome["payload"]["outcome"]), criterion_met=criterion,
                goal_evaluation=None if outcome is None else outcome["payload"].get("goal_evaluation"),
                evidence_linked=outcome is not None and str(outcome["outcome_id"]) in evidence,
                revision_of_outcome_id=None if outcome is None or (outcome["payload"]["outcome"] != ScientificOutcome.EXECUTION_FAILURE.value and outcome["payload"].get("validation_code") not in REVISIONABLE_INTEGRITY_VALIDATION_CODES) else str(outcome["outcome_id"]), revision_tip=outcome is not None and tip is not None and (batch_mode or tip[0] == len(chain)), current_cycle_revisions=max(0, len(chain) - 1) if batch_mode else cycle_revisions.get(component_id, 0),
                assessment=None if assessment is None else HypothesisAssessment(assessment["payload"]["assessment"]), assessment_id=None if assessment is None else str(assessment["subject_id"]),
                effect_in_flight=experiment_id is not None and any(workflow == experiment_id for workflow, _, _ in open_effects),
                resource_blocked=busy_gpu, rendezvous_blocked=experiment_id in blocked_workflows, creation_budget_exhausted=((cap := profile_role_cap(contract.document, component.owner_role, "max_experiments")) is not None and role_experiments[component.owner_role] >= cap),
                successor_budget_state=successor_budget_state, expert_lineage_terminated=(context is not None or not _uses_expert_loop(contract) or self._expert_lineage_terminated(run_id, component_id, component.owner_role)),
                execution_controller_open=controller_open, execution_final_failure=execution_failed, tool_execution=tool_execution,
                preflight_rejected=experiment_id is not None and self._preflight_rejection(run_id, experiment_id) is not None,
                pending_hypothesis_id=pending_hypothesis,
                creation_paused=self._participant_paused(run_id, component.owner_role, component_id, "CREATE_EXPERIMENT", context.chain if context is not None else "1"),
                batch_pending=history is not None and history.latest is not None and context is None and not self._batch_control_closed(run_id, component_id, component.owner_role)))
        shared_source = self.shared_acceptance.source(run_id)
        return PolicySnapshot(run_id=run_id, run_state=str(run["state"]), run_revision=int(run["revision"]),
            goal_evaluation_required=self.shared_acceptance.enabled(run_id),
            shared_acceptance=None if shared_source is None else shared_source[1],
            shared_acceptance_source_attempt_id=None if shared_source is None else shared_source[0],
            completion_mode=(contract.document.get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {}).get("completion_mode", "required_conjunction") if batch_mode else "required_conjunction"),
            roster=contract.role_ids, components=tuple(components), busy_roles=tuple(sorted(busy_roles)),
            live_effect_count=len(open_effects), live_effect_limit=sum(self._effect_capacity(run_id, kind) for kind in ("AGENT", "PROCESS", "REVIEW")) if self._tool_execution_enabled(run_id) else 3, budget_exhausted=len(heads) >= int(contract.document["budget"].get("max_experiments", len(contract.components))),
            has_goal_verdict=bool(by_kind.get("GoalVerdictRecorded")), open_obligation_count=0)
    def _drive_batches(self, run_id: str) -> bool:
        from .policy import _dependencies_ready
        contract = self._research_contract(run_id); pending = False; executions = []; agent_work = []
        def dispatch(*args): agent_work.append(args)
        priority = self.interaction.priority_roles(run_id)
        components = sorted(contract.components, key=lambda item: item.owner_role not in priority)
        cursor = self._batch_cursors.get(run_id, 0) % len(components)
        components = components[cursor:] + components[:cursor]
        self._batch_cursors[run_id] = (cursor + 1) % len(components)
        for component in components:
            if component.tier.value == "should": continue
            component_id, role = str(component.component_id), component.owner_role
            history = self._batch_history(run_id, component_id, role); batch = history.latest
            if any(item.selection is not None for item in history.batches):
                AgentSessions(self.state_root, self.publisher).retire_adjudicated_worktrees(
                    history, self.store.list_attempts(state="OPEN"), creations=self.store.inspect(
                        "SELECT run_id,workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,)))
            snapshot = self._policy_snapshot(self.store.latest_transition(run_id))
            selected_component = next(item for item in snapshot.components if item.component_id == component_id)
            if not _dependencies_ready(selected_component, {item.component_id: item for item in snapshot.components}): continue
            if batch is None:
                if not selected_component.proposal_terminal:
                    self._drive_expert_proposal(run_id, component_id, role, dispatch=dispatch); pending = True
                continue
            leader = self.interaction.leader_round(run_id, component_id)
            designated = leader or self.interaction.debate.designated_round(run_id, component_id)
            if designated is not None and designated["cycle"] > batch.cycle:
                pending = True
                if not self.interaction.blocks_next_cycle(run_id, component_id, designated["cycle"]):
                    self._drive_expert_proposal(run_id, component_id, role, dispatch=dispatch, cycle=designated["cycle"],
                        parent_hypothesis_id=designated["parent_hypothesis_id"],
                        restart_from_hypothesis=designated.get("restart_from_hypothesis", False))
                continue
            if self._batch_control_closed(run_id, component_id, role): continue
            pending = True
            if batch.selection is not None:
                update = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role,
                    cycle=batch.cycle, node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
                report = self._cycle_report_task(run_id, component_id, role, batch.cycle)
                evolve = ResearchNodeTask(key=replace(report.key, node_kind=ResearchNodeKind.SELF_EVOLVE))
                family = None if history.selected_tip is None else history.selected_tip["method_family_id"]
                for task in (update, report, evolve):
                    if task == evolve and self._expert_success(report) is not None and not self._ensure_graph_evolution(report): break
                    if self._expert_task_final_failure(run_id, task) is not None: continue
                    if self._expert_success(task) is None:
                        if self._expert_task_exhausted(run_id, task):
                            self._record_expert_final_failure(run_id, component_id, role, task, "batch_close_exhausted")
                        else: dispatch(run_id, run_id, task, role, component_id, family)
                        break
                else:
                    if not self._batch_control_closed(run_id, component_id, role) and not self.interaction.blocks_next_cycle(run_id, component_id, batch.cycle + 1):
                        self._drive_expert_proposal(run_id, component_id, role, dispatch=dispatch, cycle=batch.cycle + 1,
                            parent_hypothesis_id=None if history.selected_tip is None else history.selected_tip["node_id"])
                continue
            _, _, terminal = self._batch_context(run_id, component_id, role, batch.cycle)
            for chain in batch.chains:
                if chain in terminal: continue
                chain_failed = self._batch_chain_failed(run_id, component_id, role, batch.cycle, chain)
                rows = self.store.inspect("SELECT rowid AS submit_order,workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1 AND json_extract(payload_json,'$.hypothesis_id')=? ORDER BY rowid", (run_id, batch.members[chain]))
                identity = {} if not rows else json.loads(rows[-1]["payload_json"])
                iteration = int(identity.get("chain_iteration", 1))
                analysis = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role,
                    cycle=batch.cycle, chain=chain, node_kind=ResearchNodeKind.RESULT_ANALYSIS, experiment_ordinal=iteration))
                accepted_analysis = self._expert_success(analysis)
                if accepted_analysis is not None:
                    disposition = json.loads(accepted_analysis["observation_json"])["response"]["typed_candidate"]["iteration_disposition"]
                    measured = next(item for item in self._policy_snapshot(self.store.latest_transition(run_id), chain_context=analysis.key).components if item.component_id == component_id)
                    goal_met = (self.shared_acceptance.qualifies(run_id, measured.goal_evaluation) if self.shared_acceptance.enabled(run_id)
                        else self._shared_goal(run_id) and measured.outcome is ScientificOutcome.SUCCESS and measured.criterion_met is True)
                    if disposition == "ITERATE" and not chain_failed and not goal_met and iteration < batch_limits(contract.document, role)["iterations"] and self._batch_creation_available(run_id, role): iteration += 1
                key = ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=batch.cycle,
                    chain=chain, node_kind=ResearchNodeKind.EXPERIMENT_EXECUTION, experiment_ordinal=iteration)
                view = self._policy_snapshot(self.store.latest_transition(run_id), chain_context=key)
                current = next(item for item in view.components if item.component_id == component_id)
                # Reuse the ordinary Policy on this exact chain, retaining run
                # budgets/capacities. Dependencies were checked above.
                scoped = replace(view, components=(replace(current, dependencies=(), input_dependencies=()),), busy_roles=())
                decision = self.policy.next_command(scoped)
                if decision is None: continue
                if decision.owner_id != run_id:
                    if decision.command == Command.EXECUTE.value:
                        leader_round = _leader_round_matches(leader, role, component_id, batch.cycle)
                        executions.append((not leader_round, int(rows[-1]["submit_order"]), decision))
                    else: self._drive_experiment(run_id, self.store.latest_transition(decision.owner_id), decision.command)
                elif decision.command == Command.CREATE_EXPERIMENT.value:
                    design = ResearchNodeTask(key=replace(key, node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT))
                    task = ResearchNodeTask(key=key) if self._expert_success(design) is not None else design
                    if self._expert_task_exhausted(run_id, task): self._record_expert_final_failure(run_id, component_id, role, task, "execution_node_exhausted")
                    else: dispatch(run_id, run_id, task, role, component_id,
                        str(history.hypotheses[batch.members[chain]]["payload"]["method_family_id"]))
                elif decision.command == Command.LINK_EVIDENCE.value:
                    self._link_evidence(run_id, component_id, current.experiment_id)
                elif decision.command == Command.RECORD_ASSESSMENT.value:
                    if not self._ensure_expert_analysis(run_id, component_id, current.experiment_id, dispatch=dispatch): continue
                    analysis = self._expert_success(ResearchNodeTask(key=replace(key, node_kind=ResearchNodeKind.RESULT_ANALYSIS)))
                    raw = {} if analysis is None else json.loads(analysis["observation_json"])["response"]["typed_candidate"]
                    goal_done = (self.shared_acceptance.qualifies(run_id, current.goal_evaluation) if self.shared_acceptance.enabled(run_id)
                        else self._shared_goal(run_id) and decision.payload["assessment"] == "SUPPORTED")
                    if raw.get("iteration_disposition") == "ITERATE" and not chain_failed and not goal_done and iteration < batch_limits(contract.document, role)["iterations"] and self._batch_creation_available(run_id, role): continue
                    self._record_assessment(run_id, replace(decision, payload={**decision.payload, "experiment_id": current.experiment_id}))
                    if self._begin_shared_goal_stop(run_id): return True
            _, _, terminal = self._batch_context(run_id, component_id, role, batch.cycle)
            if set(terminal) == set(batch.chains):
                task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role,
                    cycle=batch.cycle, node_kind=ResearchNodeKind.ADJUDICATE))
                if self._expert_task_exhausted(run_id, task): self._record_expert_final_failure(run_id, component_id, role, task, "adjudication_exhausted")
                else: dispatch(run_id, run_id, task, role, component_id)
        from .expert_batch import dispatch_batch_agents
        dispatch_batch_agents(self, agent_work)
        # The existing creation journal is the queue: exact Leader round first,
        # then submission order across every component and chain. Admission and
        # per-dispatch resource checks remain in Policy and _start_process.
        for _, _, decision in sorted(executions, key=lambda item: item[:2]):
            self._drive_experiment(run_id, self.store.latest_transition(decision.owner_id), decision.command)
        return pending
    def _drive_active(self, run: Mapping[str, Any]) -> None:
        run_id = str(run["run_id"])
        self._agent_scheduling[run_id] = {"observed_at": time.time(), "tasks": {}}
        if self._begin_shared_goal_stop(run_id): return
        self._drive_rendezvous(run_id); current = self.store.latest_transition(run_id)
        if current is None or current["state"] != RunState.ACTIVE.value: return
        if self._tool_execution_enabled(run_id): self._resume_tool_execution_agents(run_id)
        if self._batch_enabled(run_id):
            self.interaction.drive(run_id)
            if self._drive_batches(run_id):
                if self.store.latest_transition(run_id)["state"] != RunState.ACTIVE.value: return
                for head in self.store.list_workflow_heads(run_id, "EXPERIMENT"):
                    if head["state"] == ExperimentState.EXECUTING.value: self._drive_experiment(run_id, head, None)
                return
        elif (_uses_expert_loop(self._research_contract(run_id)) and (self._drive_ready_cycle_report(run_id) or self._drive_expert_revisions(run_id) or self._drive_ready_expert_update(run_id))): return
        snapshot = self._policy_snapshot(self.store.latest_transition(run_id))
        decision = self.policy.next_command(snapshot)
        if (decision is not None and decision.command in {Command.RECORD_GOAL_VERDICT.value, Command.BEGIN_COMPLETION.value}
                and self._batch_enabled(run_id) and self.interaction.pending(run_id)): return
        if decision is not None and decision.expected_revision != snapshot.revision_for(decision.owner_id): raise OperationalHalt("Research Policy returned a stale owner revision")
        if decision is None:
            executing = (row for row in self.store.list_workflow_heads(run_id, "EXPERIMENT")
                         if row["state"] == ExperimentState.EXECUTING.value)
            for head in executing: self._drive_experiment(run_id, head, None)
            return
        if decision.owner_id != run_id:
            head = next((row for row in self.store.list_workflow_heads(run_id, "EXPERIMENT") if row["workflow_id"] == decision.owner_id), None)
            if head is None: raise OperationalHalt(f"policy selected unknown experiment {decision.owner_id!r}")
            self._drive_experiment(run_id, head, decision.command); return
        command = None if decision is None else decision.command
        if command == Command.REQUEST_HYPOTHESIS_CANDIDATE.value:
            role, component_id = str(decision.payload["role"]), str(decision.payload["component_id"])
            if _uses_expert_loop(self._research_contract(run_id)):
                self._drive_expert_proposal(run_id, component_id, role)
            else:
                self._agent_step(run_id, run_id, AgentCommand.PROPOSE_HYPOTHESIS, role, component_id)
        elif command == Command.CREATE_EXPERIMENT.value:
            if self._tool_execution_enabled(run_id): self._drive_tool_execution(run_id, decision)
            else: self._create_experiment(run_id, decision)
        elif command == Command.LINK_EVIDENCE.value:
            component_id = str(decision.payload["component_id"])
            if (_uses_expert_loop(self._research_contract(run_id)) and not self._execution_controller_open(run_id, component_id)
                    and not self._ensure_expert_analysis(run_id, component_id)):
                return
            self._link_evidence(run_id, component_id)
        elif command == Command.RECORD_ASSESSMENT.value:
            if self._tool_execution_enabled(run_id) and not self._ensure_expert_analysis(run_id, str(decision.payload["component_id"])): return
            self._record_assessment(run_id, decision)
        elif command == Command.RECORD_GOAL_VERDICT.value:
            self._record_verdict(run_id, decision)
        elif command == Command.BEGIN_COMPLETION.value:
            if not self._ensure_final_report(run_id): return
            with self.store.transaction() as tx:
                transition = self._transition_record(tx, run_id, run_id, "RUN", Command.BEGIN_COMPLETION,
                    RunState.COMPLETING, next_command=Command.COMPLETE_CLEAN.value)
                tx.append_transition(transition)
        else: raise OperationalHalt(f"Research Policy returned unsupported ACTIVE command {command!r}")
    def _expert_attempts(self, task: ResearchNodeTask | AuxiliaryTask) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.store.inspect(
            "SELECT * FROM execution_attempts WHERE command_id=? ORDER BY attempt_ordinal",
            (task.command_id,),
        ))
    def _expert_success(self, task: ResearchNodeTask | AuxiliaryTask) -> Mapping[str, Any] | None:
        from .agent_aci import execution_handoff
        if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.GROUNDING and task.ordinal == 1:
            task = self._grounding_task(task.run_id)
        attempts = self._expert_attempts(task)
        query_plan = (
            isinstance(task, ResearchNodeTask)
            and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN
        ) or (
            isinstance(task, AuxiliaryTask)
            and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
        )
        candidates = attempts[-1:] if query_plan else tuple(reversed(attempts))
        accepted = next((row for row in candidates
                     if row["state"] == "CLOSED" and row["terminal_reason"] == AttemptTerminalReason.SUCCEEDED.value
                     and not execution_handoff(json.loads(row["observation_json"]))
                     and not (isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.GROUNDING
                         and json.loads(row["observation_json"]).get("response", {}).get("typed_candidate", {}).get("clarification") is not None)), None)

        if (accepted is not None and query_plan
                and self._literature_query_gap(task, accepted) is not None):
            return None
        return accepted

    def _grounding_query_gap(self, run_id: str, plan: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return self._literature_query_gap(
            AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id=run_id), plan)

    def _literature_query_gap(self, task: ResearchNodeTask | AuxiliaryTask, plan: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """An actual retrieval failure invalidates only the exact consumed plan."""
        if isinstance(task, AuxiliaryTask):
            if task.kind is not AuxiliaryRequestKind.GROUNDING_QUERY_PLAN: return None
            main = self._grounding_task(task.run_id)
        else:
            if task.key.node_kind is not ResearchNodeKind.LITERATURE_REVIEW or task.key.subkind is not ResearchNodeSubkind.QUERY_PLAN: return None
            main = ResearchNodeTask(key=replace(task.key, subkind=ResearchNodeSubkind.MAIN))
        attempts = self._expert_attempts(main)
        if not attempts:
            return None
        row = attempts[-1]
        observation = json.loads(row["observation_json"])
        if (row["state"] != "CLOSED" or row["terminal_reason"] != "FAILED"
                or observation.get("failure_stage") != "literature_retrieval"
                or observation.get("literature_failure_class") != "LiteratureCoverageError"):
            return None
        request = AgentRequest.from_dict(observation["request"])
        ref = f"{plan['attempt_id']}--literature-query"
        if not any(item.ref == ref for item in request.input_artifacts):
            return None
        return row

    def _grounding_task(self, run_id: str) -> AuxiliaryTask:
        run = self.store.get_run(run_id)
        return (self.grounding_dialogue.current_task(run_id) if run is not None and int(run["agent_aci_version"]) >= 15
                else AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id=run_id))
    def _expert_response_input(self, task: ResearchNodeTask | AuxiliaryTask) -> EmbeddedInput:
        attempt = self._expert_success(task)
        if attempt is None: raise OperationalHalt(f"expert task {task.command_id!r} lacks a successful Attempt")
        observation = json.loads(attempt["observation_json"])
        response = observation.get("response")
        if not isinstance(response, Mapping): raise OperationalHalt("successful expert Attempt lacks its response")
        return EmbeddedInput.from_bytes(f"{attempt['attempt_id']}--response", "application/json", _canonical(response))
    def _expert_failure_input(self, run_id: str, task: ResearchNodeTask | AuxiliaryTask) -> EmbeddedInput | None:
        failure = self._expert_task_final_failure(run_id, task)
        if failure is None: return None
        failed = self.store.get_attempt(failure["attempt_id"])
        return EmbeddedInput.from_bytes(f"{failed['attempt_id']}--final-failure", "application/json",
            _canonical({"control": dict(failure), "attempt": dict(failed)}))
    def _expert_literature_query(self, task: ResearchNodeTask | AuxiliaryTask, missing: str, invalid: str) -> tuple[Mapping[str, Any], LiteratureQuery]:
        plan = self._expert_success(task)
        if plan is None: raise OperationalHalt(missing)
        observation = json.loads(plan["observation_json"]); request = AgentRequest.from_dict(observation["request"]); template = LiteratureQuery.from_dict(json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--literature-query-template")).content_bytes()))
        try: return plan, admit_literature_query_plan(LiteratureQuery.from_dict(observation["response"]["typed_candidate"]), template=template)
        except LiteratureError as exc: raise OperationalHalt(invalid) from exc
    def _expert_lineage_terminated(self, run_id: str, component_id: str, role: str) -> bool:
        if self._batch_enabled(run_id):
            batch = self._batch_history(run_id, component_id, role).latest
            return (batch is not None and self._batch_control_closed(run_id, component_id, role)
                and not self._batch_science_failed(run_id, component_id, role, batch.cycle))
        chain = self._expert_hypothesis_chain(run_id, component_id, role)
        if not chain: return False
        task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=len(chain), node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
        success = self._expert_success(task)
        if success is None: return False
        if self._cycle_reports_enabled(run_id) and self._expert_success(self._cycle_report_task(run_id, component_id, role, len(chain))) is None: return False
        response = json.loads(success["observation_json"]).get("response", {}); candidate = response.get("typed_candidate", {}) if isinstance(response, Mapping) else {}
        return candidate.get("update_disposition") == HypothesisUpdateDisposition.TERMINATE_LINEAGE.value
    def _batch_science_failed(self, run_id: str, component_id: str, role: str, cycle: int) -> bool:
        return self.store.cached_read(("batch-science-failed", run_id, component_id, role, cycle),
            lambda: self._read_batch_science_failed(run_id, component_id, role, cycle))
    def _read_batch_science_failed(self, run_id: str, component_id: str, role: str, cycle: int) -> bool:
        from .expert_batch import read_batch_science_failed
        return read_batch_science_failed(self, run_id, component_id, role, cycle)
    def _batch_control_closed(self, run_id: str, component_id: str, role: str) -> bool:
        batch = self._batch_history(run_id, component_id, role).latest
        if batch is None: return False
        if batch.selection is None:
            task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id,
                expert_slot=role, cycle=batch.cycle, node_kind=ResearchNodeKind.ADJUDICATE))
            return self._expert_task_final_failure(run_id, task) is not None
        report = self._cycle_report_task(run_id, component_id, role, batch.cycle)
        update = ResearchNodeTask(key=replace(report.key, node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
        evolve = ResearchNodeTask(key=replace(report.key, node_kind=ResearchNodeKind.SELF_EVOLVE))
        success = self._expert_success(update)
        if success is None and self._expert_task_final_failure(run_id, update) is None: return False
        if self._expert_success(report) is None and self._expert_task_final_failure(run_id, report) is None: return False
        if self._expert_success(evolve) is None and self._expert_task_final_failure(run_id, evolve) is None: return False
        disposition = None if success is None else json.loads(success["observation_json"])["response"]["typed_candidate"].get("update_disposition")
        return (self._batch_science_failed(run_id, component_id, role, batch.cycle) or disposition == "TERMINATE_LINEAGE"
            or batch.cycle >= batch_limits(self._research_contract(run_id).document, role)["rounds"]
            or (batch.selection.get("selected_tip") is None and (int(self.store.get_run(run_id)["agent_aci_version"]) < 17
                or disposition != "REVISE_NEXT_CYCLE")) or not self._batch_creation_available(run_id, role))
    def _unselected_batch_revision_ready(self, run_id: str, component_id: str, role: str) -> bool:
        batch = self._batch_history(run_id, component_id, role).latest
        if (int(self.store.get_run(run_id)["agent_aci_version"]) < 17 or batch is None or batch.selection is None
                or batch.selection.get("selected_tip") is not None or self._batch_control_closed(run_id, component_id, role)): return False
        report = self._cycle_report_task(run_id, component_id, role, batch.cycle)
        update = ResearchNodeTask(key=replace(report.key, node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
        evolve = ResearchNodeTask(key=replace(report.key, node_kind=ResearchNodeKind.SELF_EVOLVE))
        success = self._expert_success(update)
        return (success is not None and json.loads(success["observation_json"])["response"]["typed_candidate"].get("update_disposition") == "REVISE_NEXT_CYCLE"
            and self._expert_success(report) is not None
            and (self._expert_success(evolve) is not None or self._expert_task_final_failure(run_id, evolve) is not None))
    def _cycle_reports_enabled(self, run_id: str) -> bool:
        return int(self.store.get_run(run_id)["agent_aci_version"]) >= 12 and _uses_expert_loop(self._research_contract(run_id))
    @staticmethod
    def _cycle_report_task(run_id: str, component_id: str, role: str, cycle: int) -> ResearchNodeTask:
        return ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id,
            expert_slot=role, cycle=cycle, node_kind=ResearchNodeKind.CYCLE_REPORT))
    def _drive_ready_cycle_report(self, run_id: str) -> bool:
        if not self._cycle_reports_enabled(run_id): return False
        for component in self._research_contract(run_id).components:
            if component.tier.value == "should": continue
            component_id, role = str(component.component_id), component.owner_role
            chain = self._expert_hypothesis_chain(run_id, component_id, role)
            if not chain: continue
            update = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id,
                expert_slot=role, cycle=len(chain), node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
            if self._expert_success(update) is None: continue
            task = self._cycle_report_task(run_id, component_id, role, len(chain))
            if self._expert_success(task) is not None or self._expert_task_final_failure(run_id, task) is not None: continue
            if any(row["state"] == "OPEN" for row in self._expert_attempts(task)): return True
            if self._expert_task_exhausted(run_id, task):
                self._record_expert_final_failure(run_id, component_id, role, task, "cycle_report_exhausted"); return True
            self._expert_agent_step(run_id, run_id, task, role, component_id, str(chain[-1]["payload"]["method_family_id"]))
            return True
        return False
    def _expert_task_exhausted(self, run_id: str, task: ResearchNodeTask | AuxiliaryTask) -> bool:
        if task.command_id in self.resource_recovery.blocked(run_id): return False
        attempts = self._expert_attempts(task)
        return (self._expert_task_final_failure(run_id, task) is not None
                or not any(row["state"] == "OPEN" for row in attempts)
                and self._expert_success(task) is None
                and self._agent_attempts_exhausted(self._research_contract(run_id), list(attempts)))
    def _expert_task_final_failure(self, run_id: str, task: ResearchNodeTask | AuxiliaryTask) -> Mapping[str, Any] | None:
        matches = tuple(payload for payload in self._expert_final_failures(run_id)
            if payload["failed_task_command_id"] == task.command_id)
        if not matches: return None
        if len(matches) != 1: raise OperationalHalt("expert task has duplicate final-failure transitions")
        return matches[0]
    def _expert_final_failures(self, run_id: str) -> tuple[Mapping[str, Any], ...]:
        return self.store.cached_read(("expert-final-failures", run_id), lambda: self._read_expert_final_failures(run_id))
    def _read_expert_final_failures(self, run_id: str) -> tuple[Mapping[str, Any], ...]:
        failures: list[Mapping[str, Any]] = []
        for row in self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? "
                "AND json_extract(payload_json,'$.agent_final_failure')=1 "
                "AND json_type(payload_json,'$.failed_task_command_id')='text' ORDER BY rowid", (run_id,)):
            payload = json.loads(row["payload_json"])
            attempt_id = payload.get("attempt_id"); task_command_id = payload.get("failed_task_command_id")
            attempt = self.store.get_attempt(attempt_id) if isinstance(attempt_id, str) else None
            try:
                request = None if attempt is None else AgentRequest.from_dict(json.loads(attempt["observation_json"])["request"])
            except (AgentACIError, KeyError, TypeError, ValueError) as exc:
                raise OperationalHalt("expert final failure has an invalid Agent request binding") from exc
            if (attempt is None or attempt["run_id"] != run_id
                    or attempt["command_id"] != task_command_id
                    or attempt["state"] != "CLOSED"
                    or attempt["terminal_reason"] == AttemptTerminalReason.SUCCEEDED.value
                    or request is None or not isinstance(request.task_context, (ResearchNodeTask, AuxiliaryTask))
                    or request.task_context.command_id != task_command_id):
                raise OperationalHalt("expert final failure does not bind its failed Attempt")
            failures.append(payload)
        return tuple(failures)
    def _record_expert_final_failure(self, run_id: str, component_id: str, role: str,
                                     task: ResearchNodeTask | AuxiliaryTask, code: str) -> None:
        if self._expert_task_final_failure(run_id, task) is not None:
            return
        attempts = self._expert_attempts(task)
        failed = tuple(row for row in attempts
                       if row["state"] == "CLOSED"
                       and row["terminal_reason"] != AttemptTerminalReason.SUCCEEDED.value)
        if (not failed or any(row["state"] == "OPEN" for row in attempts)
                or self._expert_success(task) is not None):
            raise OperationalHalt("expert final failure lacks an exhausted failed Attempt")
        attempt = failed[-1]
        with self.store.transaction() as tx:
            state = RunState.STOPPING if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.FINAL_REPORT and tx.latest_transition(run_id)["state"] == RunState.STOPPING.value else RunState.ACTIVE
            transition = self._transition_record(tx, run_id, run_id, "RUN",
                Command.AGENT_ATTEMPT_FINAL_FAILURE, state,
                payload={"agent_final_failure": True,
                    "attempt_id": str(attempt["attempt_id"]), "failure_code": code,
                    "failed_task_command_id": task.command_id,
                    "role": role, "component_id": component_id})
            tx.append_transition(transition)
    def _expert_node_view(self, key: ResearchNodeKey) -> ResearchNodeView:
        task = ResearchNodeTask(key=key)
        attempts = self._expert_attempts(task)
        success = self._expert_success(task)
        state = (DerivedWorkState.OPEN if any(row["state"] == "OPEN" for row in attempts)
                 else DerivedWorkState.CLOSED if success is not None
                 else DerivedWorkState.READY)
        return ResearchNodeView(
            key=key, command_id=key.command_id, state=state,
            attempt_ids=tuple(str(row["attempt_id"]) for row in attempts),
            artifact_ids=(() if success is None else
                (f"{success['attempt_id']}--response",)),
        )
    def _expert_grounding_view(self, run_id: str) -> AuxiliaryTaskView | None:
        task = self._grounding_task(run_id)
        attempts = self._expert_attempts(task)
        if not attempts and task.ordinal == 1:
            return None
        success = self._expert_success(task)
        state = (DerivedWorkState.OPEN if any(row["state"] == "OPEN" for row in attempts)
                 else DerivedWorkState.CLOSED if success is not None
                 else DerivedWorkState.READY)
        return AuxiliaryTaskView(
            task=task, state=state,
            attempt_ids=tuple(str(row["attempt_id"]) for row in attempts),
            artifact_ids=(() if success is None else
                (f"{success['attempt_id']}--response",)),
        )
    def _expert_hypothesis_chain(
        self, run_id: str, component_id: str, role: str,
    ) -> tuple[Mapping[str, Any], ...]:
        if self._batch_enabled(run_id):
            history = self._batch_history(run_id, component_id, role)
            return tuple(history.hypotheses[batch.selection["selected_tip"]["node_id"]]
                for batch in history.batches if batch.selection is not None and batch.selection.get("selected_tip") is not None)
        candidates = [
            row for row in self.store.list_facts(run_id, "HypothesisProposed")
            if row["payload"].get("component_id") == component_id
            and row["payload"].get("proposer_role") == role
        ]
        if not candidates:
            return ()
        roots = [row for row in candidates
                 if row["payload"].get("parent_hypothesis_id") is None]
        if len(roots) != 1:
            raise OperationalHalt("expert hypothesis lineage must have one root")
        by_parent: dict[str, list[Mapping[str, Any]]] = {}
        for row in candidates:
            parent = row["payload"].get("parent_hypothesis_id")
            if parent is not None:
                by_parent.setdefault(str(parent), []).append(row)
        chain = [roots[0]]
        while children := by_parent.get(str(chain[-1]["hypothesis_id"]), []):
            if len(children) != 1:
                raise OperationalHalt("expert hypothesis lineage branches")
            chain.append(children[0])
        if len(chain) != len(candidates):
            raise OperationalHalt("expert hypothesis lineage is disconnected")
        return tuple(chain)
    def _drive_expert_proposal(self, run_id, component_id, role, **kwargs):
        from .expert_batch import drive_expert_proposal
        drive_expert_proposal(self, run_id, component_id, role, **kwargs)
    def _ensure_expert_analysis(self, run_id: str, component_id: str, experiment_id: str | None = None, *, dispatch=None) -> bool:
        contract = self._research_contract(run_id); component = contract.component(component_id)
        if component is None: raise OperationalHalt(f"unknown analysis component {component_id!r}")
        outcomes = [row for row in self.store.list_facts(run_id, "CanonicalOutcomeAccepted")
                    if row["payload"].get("component_id") == component_id and (experiment_id is None or row["experiment_id"] == experiment_id)]
        if not outcomes: raise OperationalHalt("analysis node lacks a canonical outcome")
        creations = {str(row["workflow_id"]): json.loads(row["payload_json"])
            for row in self.store.inspect("SELECT workflow_id,payload_json FROM workflow_transitions "
                "WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))}
        outcome = max(outcomes, key=lambda row: int(
            creations[str(row["experiment_id"])].get("experiment_ordinal", 1)))
        identity = creations[str(outcome["experiment_id"])]
        role, cycle = str(identity["role"]), int(identity.get("expert_cycle", 1))
        ordinal = int(identity.get("chain_iteration", identity.get("experiment_ordinal", 1)))
        task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id,
            component_id=component_id, expert_slot=role, cycle=cycle,
            node_kind=ResearchNodeKind.RESULT_ANALYSIS,
            experiment_ordinal=ordinal, chain=identity.get("chain")))
        if self._expert_success(task) is not None: return True
        if self._expert_task_exhausted(run_id, task):
            self._record_expert_final_failure(run_id, component_id, role, task, "result_analysis_exhausted")
            return True
        (dispatch or self._expert_agent_step)(run_id, run_id, task, role, component_id,
            str(outcome["payload"]["method_family_id"]))
        return False
    def _expert_experiment_ordinal(self, run_id: str, component_id: str,
                                   role: str, cycle: int) -> int:
        identities = (json.loads(row["payload_json"]) for row in self.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? "
            "AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,)))
        ordinals = [int(item.get("experiment_ordinal", 1)) for item in identities
                    if item.get("component_id") == component_id
                    and item.get("role") == role
                    and int(item.get("expert_cycle", 1)) == cycle]
        if not ordinals: raise OperationalHalt("expert cycle lacks an experiment identity")
        return max(ordinals)
    def _drive_ready_expert_update(self, run_id: str) -> bool:
        contract = self._research_contract(run_id)
        pending = False
        for component in contract.components:
            if component.tier.value == "should": continue
            component_id, role = str(component.component_id), component.owner_role
            chain = self._expert_hypothesis_chain(run_id, component_id, role)
            if not chain: continue
            hypothesis_id = str(chain[-1]["hypothesis_id"])
            assessments = [row for row in self.store.list_facts(run_id, "HypothesisAssessed")
                if row["payload"].get("component_id") == component_id and str(row["hypothesis_id"]) == hypothesis_id]
            if not assessments: continue
            analysis = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id,
                component_id=component_id, expert_slot=role, cycle=len(chain), node_kind=ResearchNodeKind.RESULT_ANALYSIS,
                experiment_ordinal=self._expert_experiment_ordinal(
                    run_id, component_id, role, len(chain))))
            if self._expert_task_final_failure(run_id, analysis) is not None: continue
            task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id,
                component_id=component_id, expert_slot=role, cycle=len(chain), node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
            if self._expert_success(task) is not None or self._expert_task_final_failure(run_id, task) is not None: continue
            # Preparing a view or waiting for dispatch is still required work.
            # Do not let the ordinary policy conclude before this update closes.
            pending = True
            attempts = self._expert_attempts(task)
            if any(row["state"] == "OPEN" for row in attempts): continue
            if self._expert_task_exhausted(run_id, task):
                self._record_expert_final_failure(run_id, component_id, role, task, "hypothesis_update_exhausted")
                return True
            before = len(attempts)
            self._expert_agent_step(run_id, run_id, task, role, component_id,
                str(assessments[-1]["payload"]["method_family_id"]))
            if len(self._expert_attempts(task)) > before: return True
        return pending
    def _drive_expert_revisions(self, run_id: str) -> bool:
        contract = self._research_contract(run_id)
        creations = [json.loads(row["payload_json"]) for row in self.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? "
            "AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))]
        failed = {json.loads(row["payload_json"]).get("failed_task_command_id")
            for row in self.store.inspect("SELECT payload_json FROM workflow_transitions "
                "WHERE run_id=?", (run_id,))}
        for component in contract.components:
            if component.tier.value == "should": continue
            component_id, role = str(component.component_id), component.owner_role
            chain = self._expert_hypothesis_chain(run_id, component_id, role)
            if not chain: continue
            cycle, hypothesis_id = len(chain), str(chain[-1]["hypothesis_id"])
            component_creations = [item for item in creations
                                   if item.get("component_id") == component_id]
            if not any(item.get("hypothesis_id") == hypothesis_id
                       for item in component_creations):
                if cycle == 1: continue
                if self._participant_paused(run_id, role, component_id, "CREATE_EXPERIMENT", "1"): continue
                head = self.store.latest_transition(run_id)
                if head is None: raise OperationalHalt("expert successor Run is absent")
                successor_decision = PolicyDecision(
                    command=Command.CREATE_EXPERIMENT.value, owner_id=run_id,
                    expected_revision=int(head["revision"]),
                    payload={"component_id": component_id,
                        "hypothesis_id": hypothesis_id, "role": role,
                        "expert_cycle": cycle},
                    reason="accepted hypothesis update opened the next expert cycle",
                )
                if self._tool_execution_enabled(run_id): self._drive_tool_execution(run_id, successor_decision)
                else: self._create_experiment(run_id, successor_decision)
                return True
            update = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id,
                component_id=component_id, expert_slot=role, cycle=cycle,
                node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
            success = self._expert_success(update)
            if success is None: continue
            if self._cycle_reports_enabled(run_id) and self._expert_success(self._cycle_report_task(run_id, component_id, role, cycle)) is None: continue
            response = json.loads(success["observation_json"]).get("response", {})
            candidate = response.get("typed_candidate", {}) if isinstance(response, Mapping) else {}
            if candidate.get("update_disposition") != HypothesisUpdateDisposition.REVISE_NEXT_CYCLE.value:
                continue
            if self._literature_cycle_paused(run_id, role, component_id, cycle + 1): continue
            cap = profile_role_cap(contract.document, role, "max_experiments")
            role_used = sum(item.get("role") == role for item in creations)
            if (len(creations) >= int(contract.document["budget"]["max_experiments"]) or cap is not None and role_used >= cap
                    or sum(int(item.get("expert_cycle", 1)) == cycle for item in component_creations) - 1 > _REVISION_BREAKER_MAX):
                continue
            next_cycle = cycle + 1
            keys = (ResearchNodeKey(run_id=run_id, component_id=component_id,
                expert_slot=role, cycle=next_cycle, node_kind=kind, subkind=subkind)
                for kind, subkind in (
                    (ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeSubkind.QUERY_PLAN),
                    (ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeSubkind.MAIN),
                    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.FORWARD),
                    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.BACKWARD),
                    (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.SYNTHESIS)))
            if any(key.command_id in failed for key in keys): continue
            self._drive_expert_proposal(run_id, component_id, role,
                cycle=next_cycle, parent_hypothesis_id=hypothesis_id)
            return True
        return False
    def _required_input_method_family(self, run_id: str, component_id: str) -> str | None:
        component = self._research_contract(run_id).component(component_id)
        if component is None: raise OperationalHalt(f"unknown required-input component {component_id!r}")
        producer_ids = tuple(dict.fromkeys(str(item.produced_by_component) for item in component.required_inputs))
        if not producer_ids: return None
        run = self.store.latest_transition(run_id)
        if run is None: raise OperationalHalt("required-input family lacks its Run")
        by_id = {item.component_id: item for item in self._policy_snapshot(run).components}
        producers = tuple(by_id[item] for item in producer_ids); families = {item.method_family_id for item in producers}
        if len(families) != 1 or any(not item.expert_lineage_terminated or item.outcome not in {ScientificOutcome.SUCCESS,
                ScientificOutcome.CLEAN_REFUTATION} or item.method_family_id is None for item in producers): return None
        return str(next(iter(families)))
    def _validate_required_input_family(self, request: AgentRequest, candidate: Any, *, retryable: bool = False) -> None:
        component = self._research_contract(request.run_id).component(request.component_id)
        if component is None or not component.required_inputs: return
        family = self._required_input_method_family(request.run_id, request.component_id)
        if family is None: raise OperationalHalt("required input lineages do not expose one ready method family")
        if candidate.method_family_id != family: raise AgentACIError("invalid_candidate" if retryable else "candidate_method_family", "candidate must continue the required-input method family")
    def _expert_dependency_update_inputs(self, run_id: str, component_id: str) -> tuple[EmbeddedInput, ...]:
        contract = self._research_contract(run_id); component = contract.component(component_id)
        if component is None: raise OperationalHalt(f"unknown expert component {component_id!r}")
        result: list[EmbeddedInput] = []
        for dependency_id in component.dependencies:
            dependency = contract.component(str(dependency_id))
            if dependency is None: raise OperationalHalt("expert dependency is absent from the contract")
            if self._batch_enabled(run_id):
                history = self._batch_history(run_id, str(dependency_id))
                if history.latest is None or history.selected_tip is None or not self._expert_lineage_terminated(run_id, str(dependency_id), dependency.owner_role):
                    raise OperationalHalt("expert dependency lacks an adjudicated terminal selection")
                result.append(self._expert_response_input(ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=str(dependency_id),
                    expert_slot=dependency.owner_role, cycle=history.latest.cycle, node_kind=ResearchNodeKind.ADJUDICATE))))
                continue
            chain = self._expert_hypothesis_chain(run_id, str(dependency_id), dependency.owner_role)
            if not chain or not self._expert_lineage_terminated(run_id, str(dependency_id), dependency.owner_role): raise OperationalHalt("expert dependency has not terminated")
            task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=str(dependency_id),
                expert_slot=dependency.owner_role, cycle=len(chain), node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
            result.append(self._expert_response_input(task))
        return tuple(result)
    def _expert_inputs(self, run_id: str, task: ResearchNodeTask | AuxiliaryTask,
                       component_id: str, role: str, method_family_id: str | None) -> tuple[EmbeddedInput, ...]:
        from .agent_context import expert_inputs
        return expert_inputs(self, run_id, task, component_id, role, method_family_id)
    def _literature_read_pending(self, run_id: str) -> bool:
        from .literature_recovery import status
        return status(self.store, run_id)["pending"] or any(job.run_id == run_id for job in self._literature_jobs.values())
    def _read_literature(self, run_id: str, kind: str, identity: str, query: LiteratureQuery) -> dict[str, Any] | None:
        key = (kind, identity)
        job = self._literature_jobs.get(key)
        if job is None:
            run = self.store.latest_transition(run_id)
            if run is None or run["state"] in _TERMINAL_RUNS | {RunState.STOPPING.value} or self._run_wallclock_expired(run_id): return None
            if self._literature_read_pending(run_id): return None
            self._literature_jobs[key] = self.literature_io.submit_read(
                self._literature_executor, run_id, kind, query)
            return None
        if job.run_id != run_id or job.query != query:
            raise OperationalHalt("pending literature read differs from its current task query")
        return job.future.result() if job.future.done() and not job.future.cancelled() else None
    def _poll_literature(self) -> bool:
        progressed = False
        for attempt_id, (request, response, diagnostics) in tuple(self._literature_responses.items()):
            job = self._literature_jobs.get(("coverage", attempt_id))
            attempt = self.store.get_attempt(attempt_id)
            run = self.store.latest_transition(request.run_id)
            stale = attempt is None or attempt["state"] != "OPEN" or attempt["host_generation"] != self.generation
            stopped = run is None or run["state"] in _TERMINAL_RUNS or run["state"] == RunState.STOPPING.value
            expired = not stopped and self._run_wallclock_expired(request.run_id)
            if not (stale or stopped or expired):
                if job is not None and not job.future.done(): continue
                if job is None and self._literature_read_pending(request.run_id): continue
            try:
                self._literature_responses.pop(attempt_id, None)
                if stale or stopped or expired:
                    if job is not None: job.cancelled.set(); job.future.cancel()
                    if not stale:
                        self._agent_failure(request, AttemptTerminalReason.TIMED_OUT if expired else AttemptTerminalReason.CANCELLED,
                            "profile_wallclock_exhausted" if expired else "run_stopping", retryable=False,
                            details={"diagnostics": dict(diagnostics or {}), "response_discarded_after_stop": stopped, "discarded_response": response.to_dict()},
                            owner_transition=False)
                else:
                    try: self._accept_agent_response(request, response, diagnostics=diagnostics)
                    except AgentACIError as exc:
                        self._agent_failure(request, AttemptTerminalReason.FAILED, exc.code,
                            retryable=exc.code in _RETRYABLE_MODEL_FAILURES or candidate_failure_retryable(exc.code),
                            details={"diagnostics": dict(diagnostics or {}),
                                **dict(retryable_agent_validation_details(exc) or {}),
                                **literature_read_diagnostics(self._literature_jobs.get(("coverage", request.attempt_id))),
                                "failure_stage": "host_candidate_validation", "rejected_response": response.to_dict()})
            finally:
                # A coverage read may only now have been submitted after the
                # shared cooldown/previous read. Keep its original response.
                if attempt_id not in self._literature_responses: self.agent_session_metadata.pop(attempt_id, None)
                if job is not None and job.future.done(): self._literature_jobs.pop(("coverage", attempt_id), None)
            progressed = True
        for key, job in tuple(self._literature_jobs.items()):
            run = self.store.latest_transition(job.run_id)
            if run is None or run["state"] in _TERMINAL_RUNS or run["state"] == RunState.STOPPING.value or self._run_wallclock_expired(job.run_id):
                job.cancelled.set(); job.future.cancel()
                if job.future.done(): self._literature_jobs.pop(key, None)
        return progressed
    def _provider_retry_status(self, run_id: str) -> dict[str, Any]:
        return provider_retry_status(self, run_id)
    def _provider_connection(self, run_id: str) -> tuple[dict[str, str], str | None]:
        return provider_connection(self, run_id)
    def _provider_environment_for(self, run_id: str) -> Mapping[str, str]:
        environment, error = self._provider_connection(run_id)
        if error: raise HostConfigurationError(error)
        return environment
    def _provider_retry_pending(self, run_id: str) -> bool:
        return bool(self._provider_retry_status(run_id)["pending"])

    def _expert_agent_step(self, run_id: str, owner_id: str, task: ResearchNodeTask | AuxiliaryTask,
                           role: str, component_id: str, method_family_id: str | None = None) -> None:
        def note(reason):
            observed = self._agent_scheduling.setdefault(run_id, {"observed_at": time.time(), "tasks": {}})
            observed["tasks"][task.command_id] = {"command_id": task.command_id, "role": role,
                "component_id": component_id, "chain": getattr(getattr(task, "key", None), "chain", None),
                "reason": reason}
        note("checking")
        if self._tick_deadline is not None and self._agent_dispatches and time.monotonic() >= self._tick_deadline:
            return note("dispatch_yield")  # No intent/effect has been created; continue after servicing control.
        self._diagnostic_context = {"phase": "agent_request", "run_id": run_id, "command_id": task.command_id}
        run_state = self.store.latest_transition(run_id)["state"]
        if run_state in _TERMINAL_RUNS: return note("run_stopped")
        if task.command_id in self.resource_recovery.blocked(run_id): return note("resource_recovery")
        if run_state == RunState.ACTIVE.value and self._provider_retry_pending(run_id): return note("provider_backoff")
        if run_state == RunState.STOPPING.value and not (self._batch_enabled(run_id) and isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.FINAL_REPORT): return note("run_stopped")
        command = (AgentCommand.PAPER_READ if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.PAPER_READ
            else AgentCommand.GRAPH_EVOLVE if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.GRAPH_EVOLVE
            else AgentCommand.FINAL_REPORT if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.FINAL_REPORT
            else AgentCommand.DEBATE if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.DEBATE
            else AgentCommand.DEBATE_JUDGE if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.DEBATE_JUDGE
            else AgentCommand.OBSERVER if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.OBSERVER
            else AgentCommand.LEADER if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.LEADER
            else AgentCommand.LITERATURE_REVIEW if isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN else AgentCommand.GROUNDING if isinstance(task, AuxiliaryTask) else _EXPERT_COMMAND_BY_NODE.get(task.key.node_kind))
        if command is None or command is AgentCommand.EXPERIMENT_EXECUTION and not self._tool_execution_enabled(run_id):
            raise OperationalHalt("selected expert task is Host/Runner-owned")
        chain = getattr(task.key, "chain", None) if isinstance(task, ResearchNodeTask) else None
        if isinstance(task, ResearchNodeTask) and not self._expert_attempts(task):
            if command is AgentCommand.LITERATURE_REVIEW and self._literature_cycle_paused(run_id, role, component_id, task.key.cycle): return note("participant_paused")
            if command is not AgentCommand.LITERATURE_REVIEW and self._participant_paused(run_id, role, component_id, command.value,
                    chain or ("1" if command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT else None)): return note("participant_paused")
        if command is AgentCommand.LEADER and self._participant_paused(run_id, role, component_id, command.value): return note("participant_paused")
        contract = self._research_contract(run_id); attempts = self._expert_attempts(task)
        if any(row["state"] == "OPEN" for row in attempts): return note("active")
        if self._agent_attempts_exhausted(contract, list(attempts)): return note("attempts_exhausted")
        if command is AgentCommand.EXPERIMENT_EXECUTION and attempts:
            previous = AgentRequest.from_dict(json.loads(attempts[-1]["observation_json"])["request"])
            if any(self.store.latest_transition(item["exp_id"])["state"] != ExperimentState.TERMINAL.value
                    for item in self._owned_tool_experiments(previous)):
                # Runner/collection continue independently. A timed-out Agent
                # need not be relaunched just to wait for the same owned work.
                return note("experiment_wait")
        if self._at_capacity(run_id): return note("agent_capacity")
        if command is not AgentCommand.PAPER_READ:
            reason = self._participant_wait_reason(run_id, role,
                execution_node_command_id=task.command_id if command is AgentCommand.EXPERIMENT_EXECUTION else None,
                component_id=component_id, chain=chain, proposal_task=task)
            if reason is not None: return note(reason)
        if self._tick_deadline is not None and isinstance(task, ResearchNodeTask) and self._cycle_reports_enabled(run_id):
            if not self._prepare_role_view(run_id, role, task): return note("preparing_workspace")
        try: replay_context = infrastructure_retry_request(self.store, owner_id, task.command_id)
        except ValueError as exc: raise OperationalHalt(str(exc)) from exc
        replay_request = None if replay_context is None else replay_context[0]; repeat_retrieval = False if replay_context is None else replay_context[1]
        if replay_request is not None:
            if replay_request.command is not command or replay_request.task_context != task or replay_request.role != role or replay_request.component_id != component_id or replay_request.method_family_id != method_family_id: raise OperationalHalt("infrastructure retry Agent task identity differs")
            inputs = replay_request.input_artifacts; immutable_context_refs = replay_request.immutable_context_refs
        else:
            inputs = self._expert_inputs(run_id, task, component_id, role, method_family_id)
            if isinstance(task, ResearchNodeTask):
                from .paper_sources import history_input
                inputs += history_input(self, run_id, role, component_id, task.command_id)
            if (self.shared_acceptance.enabled(run_id) and isinstance(task, AuxiliaryTask)
                    and task.kind not in {AuxiliaryRequestKind.GROUNDING, AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, AuxiliaryRequestKind.PAPER_READ}
                    and (task.kind is not AuxiliaryRequestKind.FINAL_REPORT
                         or not attempts and self.shared_acceptance.source(run_id) is not None)):
                # Report retries already restore their first immutable inputs.
                # An unresolved Grounding failure report can omit the source;
                # scientific consumers still require the accepted evaluator.
                inputs += (self.shared_acceptance.input(run_id),)
            if isinstance(task, ResearchNodeTask): inputs += self._operator_note_inputs(run_id, task, role, component_id)
            repair_inputs: tuple[EmbeddedInput, ...] = ()
            if isinstance(task, ResearchNodeTask) and task.key.node_kind is ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT and owner_id != run_id:
                origins, _, replies = self._rendezvous_records(run_id)
                try: repair_inputs = draft_repair_inputs(self.store, self.publisher, run_id, owner_id, origins=origins, replies=replies)
                except ValueError as exc: raise OperationalHalt(str(exc)) from exc
                inputs += repair_inputs
            correction_inputs = agent_retry_inputs(self.store, owner_id, task.command_id); inputs += correction_inputs; correction_refs = frozenset(item.ref for item in (*repair_inputs, *correction_inputs)); immutable_context_refs = tuple(item.ref for item in inputs if item.ref not in correction_refs)
        retrieval = None
        lifecycle_command = Command.REQUEST_AUXILIARY if isinstance(task, AuxiliaryTask) else Command.REQUEST_RESEARCH_NODE
        literature_failure: tuple[str, dict[str, object], bool] | None = None; coverage_failure = False; preflight_diagnostics: dict[str, object] = {}; attempt_started_at: float | None = None
        retrieve = command is AgentCommand.GROUNDING or (command is AgentCommand.LITERATURE_REVIEW and isinstance(task, ResearchNodeTask) and task.key.subkind is ResearchNodeSubkind.MAIN)
        if repeat_retrieval and (not retrieve or self.literature_io is None): raise OperationalHalt("infrastructure retry requires its literature adapter")
        if replay_request is None and retrieve and self.literature_io is not None:
            # A MODEL/output retry did not invalidate its exact query's packet.
            # Reuse the accepted retrieval from the existing Attempt request.
            query_input = next(item for item in inputs if item.ref.endswith("--literature-query"))
            for prior in reversed(attempts):
                observation = json.loads(prior["observation_json"])
                if prior["state"] != "CLOSED" or observation.get("retry_class") != "MODEL": continue
                previous = AgentRequest.from_dict(observation["request"])
                packet = next((item for item in previous.input_artifacts if item.ref == f"{task.command_id}--literature-packet"), None)
                matching_query = any(item.ref == query_input.ref and item.content_bytes() == query_input.content_bytes() for item in previous.input_artifacts)
                if packet is not None and matching_query:
                    inputs = (*inputs, packet); immutable_context_refs = (*immutable_context_refs, packet.ref)
                    preflight_diagnostics = {"literature_reused_from_attempt": prior["attempt_id"], "literature_retrieval_seconds": 0.0}
                    retrieve = False
                    break
        if (replay_request is None or repeat_retrieval) and retrieve and self.literature_io is not None:
            query = LiteratureQuery.from_dict(json.loads(next(item for item in inputs if item.ref.endswith("--literature-query")).content_bytes()))
            read = self._read_literature(run_id, "retrieve", task.command_id, query)
            if read is None: return note("preparing_literature")
            self._literature_jobs.pop(("retrieve", task.command_id))
            attempt_started_at = read["started_at"]
            preflight_diagnostics = dict(read["diagnostics"])
            try:
                if read["error"] is not None: raise read["error"]
                retrieval = read["value"]
            except LiteratureError as exc:
                details: dict[str, object] = {"literature_failure_class": type(exc).__name__, "literature_retrieval_seconds": read["seconds"]}
                if (diagnostic := bounded_diagnostic_text(str(exc))) is not None: details["literature_diagnostic"] = diagnostic
                if exc.http_status is not None: details["literature_http_status"] = exc.http_status
                if exc.network_routes: details["literature_network_routes"] = list(exc.network_routes)
                if preflight_diagnostics: details["preflight_diagnostics"] = preflight_diagnostics
                details["failure_stage"] = "literature_retrieval"
                from .literature_recovery import service_failure
                service = service_failure(exc)
                if service is not None: details["literature_service"] = {**service, "observed_at": time.time()}
                coverage_failure = isinstance(exc, LiteratureCoverageError)
                if coverage_failure: details["literature_missing_strata"] = [value.value for value in exc.missing_strata]
                literature_failure = (f"literature_{exc.code.value.lower()}", details, service is not None or literature_failure_retryable(exc))
            except Exception as exc:
                literature_failure = ("literature_adapter_failed", {"literature_failure_class": type(exc).__name__, "literature_retrieval_seconds": read["seconds"]}, True)
            else:
                preflight_diagnostics["literature_retrieval_seconds"] = read["seconds"]
                preflight_diagnostics["literature_service"] = {
                    **preflight_diagnostics.get("literature_search", {"service": "openalex", "available": True}),
                    "observed_at": time.time()}
                packet_input = EmbeddedInput.from_bytes(f"{task.command_id}--literature-packet", LITERATURE_PACKET_MEDIA_TYPE, retrieval.packet.to_bytes())
                inputs = (*inputs, packet_input); immutable_context_refs = (*immutable_context_refs, packet_input.ref)
        if coverage_failure and retrieve:
            # The next plan reads this exact pre-model failure. Reuse each
            # task's existing bounded plan/MAIN attempts, including on restart.
            plan_task = (AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, run_id=run_id)
                if isinstance(task, AuxiliaryTask) else
                ResearchNodeTask(key=replace(task.key, subkind=ResearchNodeSubkind.QUERY_PLAN)))
            can_replan = not self._agent_attempts_exhausted(contract, self._expert_attempts(plan_task))
            literature_failure = ("invalid_candidate", literature_failure[1], can_replan)
        self._check_agent_runtime()
        self._crash("before_agent_intent_commit"); request: AgentRequest
        with self.store.transaction() as tx:
            head = tx.latest_transition(owner_id)
            if head is None: raise OperationalHalt("expert task owner workflow is absent")
            kind = "RUN" if owner_id == run_id else "EXPERIMENT"
            state: RunState | ExperimentState = RunState(head["state"]) if kind == "RUN" else ExperimentState(head["state"])
            if tx.get_command(task.command_id) is None:
                revision = int(head["revision"]) + 1
                tx.record_command({"command_id": task.command_id, "run_id": run_id,
                    "workflow_id": owner_id, "expected_revision": head["revision"],
                    "command_type": lifecycle_command.value,
                    "request": {"task_context": task.to_dict()}, "status": "accepted",
                    "result": {"admitted": True}, "revision": revision})
                tx.append_transition(self._transition_record(tx, run_id, owner_id, kind,
                    lifecycle_command, state, command_id=task.command_id,
                    payload={"task_context": task.to_dict(), "role": role,
                        "component_id": component_id, "method_family_id": method_family_id}))
            attempts = tx.list_attempts(workflow_id=owner_id, command_id=task.command_id)
            if any(row["state"] == "OPEN" for row in attempts) or self._agent_attempts_exhausted(contract, list(attempts)): return
            ordinal = len(attempts) + 1; attempt_id = f"agent-{uuid.uuid4().hex}-{ordinal}"
            base_budget = profile_agent_budget(contract.document, self.agent_budget)
            charges = self._agent_charges.read(tx, run_id)
            run_scoped = self._batch_enabled(run_id) and isinstance(task, AuxiliaryTask) and task.kind in {
                AuxiliaryRequestKind.GRAPH_EVOLVE, AuxiliaryRequestKind.OBSERVER,
                AuxiliaryRequestKind.GROUNDING, AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, AuxiliaryRequestKind.FINAL_REPORT, AuxiliaryRequestKind.DEBATE_JUDGE}
            request_budget = remaining_agent_budget(contract.document, role, charges, self.agent_budget, run_scoped=run_scoped)
            run_record = tx.get_run(run_id); remaining_wall = None if run_record is None else wallclock_remaining_seconds(contract.document, run_record["created_at"])
            if command is AgentCommand.PAPER_READ: remaining_wall = min((request_budget or base_budget).wall_seconds, self.paper_reading.remaining_wall(task), remaining_wall if remaining_wall is not None else float("inf"))
            expired = run_record is None or remaining_wall is not None and remaining_wall <= 0
            rejected = request_budget is None or expired; request_budget = request_budget or base_budget
            if not rejected and remaining_wall is not None and remaining_wall < request_budget.wall_seconds:
                request_budget = AgentBudget(max_turns=request_budget.max_turns,
                    max_tokens=request_budget.max_tokens, max_output_bytes=request_budget.max_output_bytes,
                    wall_seconds=remaining_wall, max_cost=request_budget.max_cost,
                    token_enforcement_mode=request_budget.token_enforcement_mode)
            real = self.claude_agent_host is not None
            permission_profile = (CLAUDE_AGENT_GIT_TOOL_PERMISSION_PROFILE
                if command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and real
                else CLAUDE_AGENT_TOOL_PERMISSION_PROFILE if real else capabilities_for(command).profile)
            from .profile_v4 import request_node_runtime
            selected_model = (replay_request.required_model_identity if replay_request is not None else
                validate_claude_model_selector(request_node_runtime(inputs).get("model", str(self.claude_model)))) if real else self.local_agent_model
            request = AgentRequest(schema_version=EXPERT_LOOP_ACI_SCHEMA_VERSION,
                run_id=run_id, owner_workflow_id=owner_id, command=command,
                task_context=task, command_id=task.command_id, attempt_id=attempt_id,
                attempt_ordinal=ordinal, role=role, component_id=component_id,
                method_family_id=method_family_id,
                immutable_context_refs=immutable_context_refs, input_artifacts=inputs,
                required_output_schema=required_output_schema(command, task, schema_version=EXPERT_LOOP_ACI_SCHEMA_VERSION),
                effect_requirements=(required_effect(command),), tool_permission_profile=permission_profile,
                budget=request_budget, deadline=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(time.time() + request_budget.wall_seconds)), max_input_bytes=4 * 1024 * 1024,
                required_model_identity=selected_model,
                required_adapter_version=CLAUDE_AGENT_HOST_VERSION if real else self.local_agent_adapter)
            intent = {"request": request.to_dict(), **({"preflight_diagnostics": preflight_diagnostics} if preflight_diagnostics else {})}
            if real and request.schema_version >= 11:
                intent["session_binding"] = AgentSessions.binding(request, attempts[-1] if attempts else None,
                    allow_resume=session_resume_allowed(tx, request))
            tx.open_attempt({"attempt_id": attempt_id, "run_id": run_id, "workflow_id": owner_id, "command_id": task.command_id, "kind": "AGENT", "attempt_ordinal": ordinal, "host_generation": self.generation, "started_at": time.time() if attempt_started_at is None else attempt_started_at, "payload": intent})
        # Waiting for owned Processes, retrieval or an existing Attempt is not
        # a dispatch. Counting those visits can starve ready peers every tick.
        self._agent_dispatches += 1
        note("budget_exhausted" if rejected else "admitted")
        self._diagnostic_context = {"phase": "agent_launch", "run_id": run_id, "command_id": task.command_id, "attempt_id": attempt_id}
        if rejected:
            self._agent_failure(request, AttemptTerminalReason.FAILED, "profile_budget_exhausted", retryable=False, details={"budget_rejected": True})
            return
        if literature_failure is not None:
            self._agent_failure(request, AttemptTerminalReason.FAILED, literature_failure[0], retryable=literature_failure[2],
                details=literature_failure[1]); return
        self._crash("after_agent_intent_before_start")
        if self.claude_agent_host is None:
            if command is AgentCommand.EXPERIMENT_EXECUTION:
                self.local_execution_agents[attempt_id] = request; return
            self._finish_fake_agent(request, self.agent.invoke(request)); return
        self._check_agent_runtime()
        tool_workspace: PreparedGitAuthoringWorkspace | None = None
        if request.schema_version >= 11:
            target = dict(self._run_config(run_id))["execution"].get("target")
            binding = None if not isinstance(target, Mapping) else GitTargetBinding(checkout=Path(str(target["checkout"])), commit=str(target["commit"]))
            try:
                tool_workspace = AgentSessions(self.state_root, self.publisher).prepare(request, None if command is AgentCommand.PAPER_READ else binding,
                    prior=attempts[-1] if attempts else None, admitted_binding=intent["session_binding"])
                if tool_workspace.binding != intent["session_binding"]:
                    raise GitAuthoringIntegrityError("prepared session differs from its OPEN binding")
                from .paper_sources import prepare_sources
                prepare_sources(self, tool_workspace, retrieval)
                if request.schema_version >= 24:
                    prior = next((row for row in reversed(attempts) if row["state"] == "CLOSED"), None)
                    if prior is not None:
                        tool_workspace.continuation_observation = continuation_observation(self.publisher, prior, tool_workspace)
                        if command is AgentCommand.EXPERIMENT_EXECUTION:
                            tool_workspace.continuation_observation["owned_experiments"] = [
                                {"exp_id": item["exp_id"], "state": self.store.latest_transition(item["exp_id"])["state"]}
                                for item in self._owned_tool_experiments(request)]
                if request.schema_version >= 24 and command in {AgentCommand.GROUNDING, AgentCommand.LITERATURE_REVIEW, AgentCommand.DEBATE}:
                    tool_workspace.prior_web_urls = frozenset(self._grounding_retrieved_urls(request))
            except (GitAuthoringPreparationError, OSError, ValueError) as exc:
                _cleanup_tool_workspace(tool_workspace)
                self._agent_failure(request, AttemptTerminalReason.FAILED, "agent_workspace_preparation", retryable=True,
                    details={"workspace_error": str(exc)[:1024]}); return
        elif command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            target = dict(self._run_config(run_id))["execution"].get("target")
            if not isinstance(target, Mapping):
                self._agent_failure(request, AttemptTerminalReason.FAILED, "target_binding_missing", retryable=False); return
            try:
                binding = GitTargetBinding(checkout=Path(str(target["checkout"])), commit=str(target["commit"]))
                tool_workspace = GitAuthoringWorkspace(self.state_root).prepare(binding, attempt_id)
            except (GitAuthoringPreparationError, OSError, ValueError) as exc:
                self._agent_failure(request, AttemptTerminalReason.FAILED,
                    "agent_workspace_preparation", retryable=True,
                    details={"workspace_error": str(exc)[:1024]}); return
        def bind_process(binding: object) -> None:
            with self.store.transaction() as tx:
                tx.bind_attempt_process(attempt_id, binding.process_group_id)  # type: ignore[attr-defined]
        try:
            from .provider_traffic import for_request
            handle = self.claude_agent_host.start(request, model=request.required_model_identity,
                provider_environment=self._provider_environment_for(run_id), diagnostics_root=self.state_root,
                traffic_binding=for_request(self, request),
                bind_process_group=bind_process, tool_workspace=tool_workspace,
                **(self._agent_runtime_arguments(run_id) if request.schema_version >= 11 and command is not AgentCommand.PAPER_READ else {}),
                **({"host_tool_handler": self.handle_agent_tool} if command is AgentCommand.EXPERIMENT_EXECUTION or command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and request.schema_version >= 18 or command is AgentCommand.GROUNDING and supports_grounding_web(request.schema_version) or request_literature_tools(request) else {}))
        except AgentDeadlineExpired:
            _cleanup_tool_workspace(tool_workspace)
            self._agent_failure(request, AttemptTerminalReason.TIMED_OUT, "timed_out", retryable=True); return
        except SandboxUnavailable as exc:
            _cleanup_tool_workspace(tool_workspace)
            self._agent_failure(request, AttemptTerminalReason.FAILED, "sandbox_unavailable", retryable=False,
                details={"agent_host_diagnostic": bounded_diagnostic_text(str(exc)) or type(exc).__name__})
            raise OperationalHalt(str(exc)) from exc
        except AgentHostError as exc:
            _cleanup_tool_workspace(tool_workspace)
            self._agent_failure(request, AttemptTerminalReason.FAILED, type(exc).__name__, retryable=True,
                details={"agent_host_diagnostic": bounded_diagnostic_text(str(exc)) or type(exc).__name__}); return
        except HostConfigurationError as exc:
            _cleanup_tool_workspace(tool_workspace)
            self._agent_failure(request, AttemptTerminalReason.FAILED, "provider_configuration", retryable=False,
                details={"agent_host_diagnostic": str(exc)}); return
        self.running_agents[attempt_id] = handle
    def _agent_runtime_arguments(self, run_id: str) -> dict[str, Any]:
        execution = self._run_config(run_id)["execution"]
        prefix = execution["runtime_prefix"]
        return {"runtime_prefix": None if prefix is None else Path(prefix),
                "read_roots": tuple(Path(root) for root in execution["read_roots"])}
    def _agent_step(self, run_id: str, owner_id: str, command: AgentCommand, role: str,
                    component_id: str, method_family_id: str | None = None,
                    rendezvous: RendezvousRequest | None = None) -> None:
        if self._provider_retry_pending(run_id): return
        lifecycle_command = {AgentCommand.PROPOSE_HYPOTHESIS: Command.REQUEST_HYPOTHESIS_CANDIDATE, AgentCommand.DRAFT_EXPERIMENT_SPEC: Command.REQUEST_SPEC_CANDIDATE, AgentCommand.ANSWER_RENDEZVOUS: Command.REQUEST_RENDEZVOUS}[command]
        if (command is AgentCommand.ANSWER_RENDEZVOUS) != (rendezvous is not None): raise OperationalHalt("rendezvous request/command mismatch")
        contract = self._research_contract(run_id); operational = dict(self._run_config(run_id))
        component = contract.component(component_id)
        if component is None: raise OperationalHalt(f"Agent action names unknown component {component_id!r}")
        rows = self.store.inspect("SELECT t.command_id,t.payload_json FROM workflow_transitions t JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.workflow_id=? AND c.command_type=? ORDER BY t.revision", (owner_id, lifecycle_command.value))
        rows = [row for row in rows if ((json.loads(row["payload_json"]).get("rendezvous_id") == rendezvous.rendezvous_id) if rendezvous else
                json.loads(row["payload_json"]).get("component_id") == component_id and json.loads(row["payload_json"]).get("role") == role)]
        replay_context = None
        if rows:
            try: replay_context = infrastructure_retry_request(self.store, owner_id, str(rows[0]["command_id"]))
            except ValueError as exc: raise OperationalHalt(str(exc)) from exc
        replay_request = None if replay_context is None else replay_context[0]
        if replay_context is not None and replay_context[1]: raise OperationalHalt("legacy Agent retry cannot own literature retrieval")
        if replay_request is not None and (replay_request.command is not command or replay_request.role != role or replay_request.component_id != component_id or replay_request.method_family_id != method_family_id): raise OperationalHalt("infrastructure retry Agent task identity differs")
        ids, component_index, proposer_index = _component_ids(run_id, contract, component_id, role if command is AgentCommand.PROPOSE_HYPOTHESIS else None)
        if replay_request is not None:
            inputs = replay_request.input_artifacts; immutable_context_refs = replay_request.immutable_context_refs
        else:
            goal_fact = self.store.list_facts(run_id, "GoalDefined")[0]; context_facts = [goal_fact]
            contract_document = contract.to_dict(); raw_component = next(item for item in contract_document["components"] if item["id"] == component_id)
            protocol = {"result_kind": component.result_kind.value, "requirements": {}} if component.protocol is None else contract_document["protocols"][component.protocol]
            config: dict[str, Any] = {"execution": operational["execution"], "component": raw_component, "protocol": protocol, "protocol_catalog": contract_document["protocols"], "delivery_contracts": contract_document.get("delivery_contracts", {}), "goal": contract_document["goal"]}
            dependencies = {str(item) for item in component.dependencies}; context_facts.extend(row for row in self.store.list_facts(run_id) if row["fact_type"] != "GoalDefined" and row["payload"].get("component_id") in dependencies)
            if command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
                creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (owner_id,))
                if not creation: raise OperationalHalt(f"experiment {owner_id!r} lacks its creation transition")
                selected_id = json.loads(creation[0]["payload_json"]).get("hypothesis_id")
                hypothesis = self.store.find_fact(run_id, "HypothesisProposed", selected_id)
                if hypothesis is None: raise OperationalHalt(f"component {component_id!r} lacks its selected hypothesis")
                method_family_id = str(hypothesis["payload"]["method_family_id"])
                context_facts.extend(row for row in self.store.list_facts(run_id, "HypothesisProposed") if row["payload"].get("component_id") == component_id)
                config["required_experiment_spec"] = {"evaluator_version": OUTCOME_ADAPTER_VERSION,
                    "workspace_artifacts": ["experiment.py"], "result_envelope": "result.json", "artifact_requirements": ["result.json"], "required_artifacts": list(component.required_artifacts),
                    "result_contract": component_result_contract(raw_component, protocol), "result_kind": component.result_kind.value, "component": raw_component, "protocol": protocol, "execution_environment": {"cpu": execution_environment_contract(_CPU_SANDBOX_PROFILE), "gpu": execution_environment_contract(_GPU_SANDBOX_PROFILE)},
                    "metric": None if component.metric is None else {"name": component.metric.name, "direction": component.metric.direction.value, "target": component.metric.target},
                    "trial_plan": {"seeds": list(contract.trial_plan_for(component_id).seeds), "arms": list(contract.trial_plan_for(component_id).arms), "trial_ids": list(contract.trial_plan_for(component_id).trial_ids)},
                    "resource_preferences": [item.value for item in ResourcePreference], "resource_rule": "CPU=>CPU; AUTO=>greatest-free non-busy capacity-qualified GPU else CPU; GPU_REQUIRED=>prefer greatest-free non-busy GPU, or freeze the greatest-free authorized GPU and wait when all are busy",
                    "delay_env": "HYPOLOOM_DELAY", "result_path_env": "HYPOLOOM_RESULT_PATH",
                    "hypothesis_id": selected_id, "method_family_id": method_family_id}
            if rendezvous is not None:
                if (source := self.store.get_attempt(rendezvous.source_attempt_id)) is None: raise OperationalHalt("rendezvous source Agent attempt is unavailable")
                submitted_review = self._tool_execution_enabled(run_id) and bool(self.store.inspect("SELECT 1 FROM workflow_transitions WHERE workflow_id=? AND json_extract(payload_json,'$.rendezvous_id')=? AND json_extract(payload_json,'$.source_submit_command_id') IS NOT NULL", (owner_id, rendezvous.rendezvous_id)))
                if source["state"] != "CLOSED" and not submitted_review: raise OperationalHalt("rendezvous source Agent attempt is not closed")
                try: by_ref = rendezvous_inputs(self.store, self.publisher, run_id, source, json.loads(source["observation_json"]))
                except ValueError as exc: raise OperationalHalt(str(exc)) from exc
                if not set(rendezvous.context_refs) <= set(by_ref): raise OperationalHalt("rendezvous refs differ from source Agent input")
                inputs = (EmbeddedInput.from_bytes(f"{rendezvous.rendezvous_id}--rendezvous", "application/json", _canonical(rendezvous.to_dict())),) + tuple(by_ref[ref] for ref in rendezvous.context_refs)
            else:
                inputs = tuple(EmbeddedInput.from_bytes(row["fact_id"], "application/json", _canonical({"fact_id": row["fact_id"], "fact_type": row["fact_type"], "payload": row["payload"]})) for row in context_facts) + (EmbeddedInput.from_bytes(f"{run_id}--component-{component_index}-config", "application/json", _canonical(config)),)
            if command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
                capabilities = self.compute_resources.snapshot() if operational["execution"]["allow_gpu"] else ComputeCapabilities(gpus=()); busy = set(self._busy_gpu_uuids(owner_id)); capabilities = ComputeCapabilities(gpus=capabilities.gpus, busy_gpu_uuids=tuple(gpu.uuid for gpu in capabilities.gpus if gpu.uuid in busy))
                capabilities = restrict_profile_capabilities(contract.document, capabilities) if operational["execution"]["allow_gpu"] else capabilities
                inputs += (EmbeddedInput.from_bytes(f"{run_id}--component-{component_index}--compute-capabilities", "application/json", capabilities.to_bytes()), EmbeddedInput.from_bytes(f"{run_id}--component-{component_index}--resource-planning-skill", "text/markdown", self.resource_skill), EmbeddedInput.from_bytes(f"{run_id}--component-{component_index}--resource-experience", "application/json", _canonical(self._resource_experience(run_id, role, component_id, str(method_family_id)))))
                origins, _, replies = self._rendezvous_records(run_id)
                try: repair_inputs = draft_repair_inputs(self.store, self.publisher, run_id, owner_id, origins=origins, replies=replies)
                except ValueError as exc: raise OperationalHalt(str(exc)) from exc
                inputs += repair_inputs
                inputs += tuple(EmbeddedInput.from_bytes(f"{origin.rendezvous_id}--reply", "application/json", _canonical(replies[origin.rendezvous_id].to_dict())) for origin in origins if origin.phase == "optional" and origin.component_id == component_id and origin.requester_role == role and origin.rendezvous_id in replies)
            else: repair_inputs = ()
            correction_inputs = () if not rows else agent_retry_inputs(self.store, owner_id, str(rows[0]["command_id"])); inputs += correction_inputs
            correction_refs = frozenset(item.ref for item in (*repair_inputs, *correction_inputs)); immutable_context_refs = rendezvous.context_refs if rendezvous else tuple(item.ref for item in inputs if item.ref not in correction_refs)
        if self._at_capacity(run_id, "REVIEW" if rendezvous is not None else "AGENT"): return
        self._check_agent_runtime()
        self._crash("before_agent_intent_commit")
        rejected = False
        with self.store.transaction() as tx:
            if rows: effect_command_id = rows[0]["command_id"]
            else:
                head = tx.latest_transition(owner_id)
                if head is None: raise OperationalHalt("rendezvous owner workflow is absent")
                state = RunState(head["state"]) if owner_id == run_id else ExperimentState(head["state"])
                payload = {"role": role, "component_id": component_id,
                    "hypothesis_id": ids["hypothesis"], "method_family_id": method_family_id}
                if rendezvous is not None: payload.update({"rendezvous_id": rendezvous.rendezvous_id, "requester_slot": rendezvous.requester_slot, "rendezvous_request": rendezvous.to_dict()})
                transition = self._transition_record(tx, run_id, owner_id, "RUN" if owner_id == run_id else "EXPERIMENT", lifecycle_command, state, payload=payload)
                effect_command_id = transition["command_id"]; tx.append_transition(transition)
            attempts = tx.list_attempts(workflow_id=owner_id, command_id=effect_command_id)
            if (any(row["state"] == "OPEN" for row in attempts)
                    or self._agent_attempts_exhausted(contract, list(attempts))): return
            ordinal = len(attempts) + 1; attempt_id = f"{rendezvous.rendezvous_id}--agent-{ordinal}" if rendezvous else (f"{run_id}--proposal-{component_index}-{proposer_index}--agent-{ordinal}" if owner_id == run_id else f"{owner_id}--agent-{ordinal}")
            real = self.claude_agent_host is not None
            base_budget = profile_agent_budget(contract.document, self.agent_budget)
            charges = self._agent_charges.read(tx, run_id)
            request_budget = remaining_agent_budget(contract.document, role, charges, self.agent_budget)
            run_record = tx.get_run(run_id)
            remaining_wall = None if run_record is None else wallclock_remaining_seconds(contract.document, run_record["created_at"])
            expired = run_record is None or remaining_wall is not None and remaining_wall <= 0
            if (request_budget is None and not expired
                    and remaining_agent_budget(contract.document, role, (item for item in charges if item.state == "CLOSED"), self.agent_budget) is not None):
                return
            rejected = request_budget is None or expired
            request_budget = request_budget or base_budget
            if not rejected and remaining_wall is not None and remaining_wall < request_budget.wall_seconds:
                request_budget = AgentBudget(max_turns=request_budget.max_turns, max_tokens=request_budget.max_tokens,
                    max_output_bytes=request_budget.max_output_bytes, wall_seconds=remaining_wall, max_cost=request_budget.max_cost, token_enforcement_mode=request_budget.token_enforcement_mode)
            request = AgentRequest(
                schema_version=EXPERT_LOOP_ACI_SCHEMA_VERSION, run_id=run_id, owner_workflow_id=owner_id, command=command,
                command_id=effect_command_id, attempt_id=attempt_id, attempt_ordinal=ordinal, role=role,
                component_id=component_id, method_family_id=method_family_id,
                immutable_context_refs=immutable_context_refs, input_artifacts=inputs,
                required_output_schema=required_output_schema(command),
                effect_requirements=(required_effect(command),),
                tool_permission_profile=CLAUDE_AGENT_TOOL_PERMISSION_PROFILE if real else self.local_agent_profile,
                budget=request_budget, deadline=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + request_budget.wall_seconds)), max_input_bytes=4 * 1024 * 1024,
                required_model_identity=str(self.claude_model) if real else self.local_agent_model,
                required_adapter_version=CLAUDE_AGENT_HOST_VERSION if real else self.local_agent_adapter)
            intent = {"request": request.to_dict()}
            if real and request.schema_version >= 11:
                intent["session_binding"] = AgentSessions.binding(request)
            tx.open_attempt({"attempt_id": attempt_id, "run_id": run_id, "workflow_id": owner_id,
                "command_id": effect_command_id, "kind": "AGENT", "attempt_ordinal": ordinal,
                "host_generation": self.generation, "started_at": time.time(), "payload": intent})
        if rejected:
            self._agent_failure(request, AttemptTerminalReason.FAILED, "profile_budget_exhausted",
                retryable=False, details={"budget_rejected": True}); return
        self._crash("after_agent_intent_before_start")
        if self.claude_agent_host is None: self._finish_fake_agent(request, self.agent.invoke(request)); return
        self._check_agent_runtime()
        def bind_process(binding: object) -> None:
            self._crash("after_agent_spawn_before_pgid_bind")
            with self.store.transaction() as tx: tx.bind_attempt_process(attempt_id, binding.process_group_id)  # type: ignore[attr-defined]
            self._crash("after_agent_pgid_bind_before_release")
        tool_workspace: PreparedAgentWorkspace | None = None
        if request.schema_version >= 11:
            target = operational["execution"]["target"]
            try:
                binding = None if target is None else GitTargetBinding(checkout=Path(str(target["checkout"])), commit=str(target["commit"]))
                tool_workspace = AgentSessions(self.state_root, self.publisher).prepare(request, binding)
            except (GitAuthoringPreparationError, OSError, ValueError) as exc:
                self._agent_failure(request, AttemptTerminalReason.FAILED, "agent_workspace_preparation", retryable=True, details={"workspace_error": str(exc)[:1024]}); return
        elif request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
            if self.publisher is None: raise OperationalHalt("Workflow Host is not initialized")
            target = operational["execution"]["target"]
            try: binding = None if target is None else GitTargetBinding(checkout=Path(str(target["checkout"])), commit=str(target["commit"])); tool_workspace = ExecutionWorkspace(self.publisher.root, state_root=self.state_root).prepare_agent_workspace(attempt_id=attempt_id, target_binding=binding, target_patch=self._implementation_patch(run_id, component_id, str(method_family_id)))
            except (ExecutionWorkspaceError, OSError, ValueError) as exc: self._agent_failure(request, AttemptTerminalReason.FAILED, "agent_workspace_preparation", retryable=True, details={"workspace_error": str(exc)[:1024]}); return
        if self._run_wallclock_expired(run_id):
            cleanup_error = _cleanup_tool_workspace(tool_workspace); self._agent_failure(request, AttemptTerminalReason.TIMED_OUT, "profile_wallclock_exhausted", retryable=False,
                details={"workspace_cleanup_error": cleanup_error} if cleanup_error is not None else None)
            if cleanup_error is not None: raise OperationalHalt("expired Agent workspace cleanup failed")
            return
        from .provider_traffic import for_request
        try: handle = self.claude_agent_host.start(request, model=request.required_model_identity, provider_environment=self._provider_environment_for(run_id), diagnostics_root=self.state_root, bind_process_group=bind_process, tool_workspace=tool_workspace, traffic_binding=for_request(self, request),
            **(self._agent_runtime_arguments(run_id) if request.schema_version >= 11 else {}))
        except SandboxUnavailable as exc: self._agent_failure(request, AttemptTerminalReason.FAILED, "sandbox_unavailable", retryable=False); raise OperationalHalt(str(exc)) from exc
        except AgentDeadlineExpired:
            profile_expired = self._run_wallclock_expired(run_id); self._agent_failure(request, AttemptTerminalReason.TIMED_OUT, "profile_wallclock_exhausted" if profile_expired else "timed_out", retryable=not profile_expired); return
        except AgentHostError as exc: self._agent_failure(request, AttemptTerminalReason.FAILED, type(exc).__name__, retryable=True); return
        self.running_agents[attempt_id] = handle; self._crash("after_agent_release_before_observation")
    def _finish_fake_agent(self, request: AgentRequest, raw: AgentResponse | bytes) -> None:
        if self.resource_recovery.close(request): return
        try: response = validate_postflight(request, raw).response
        except AgentACIError as exc: reason = AttemptTerminalReason.TIMED_OUT if isinstance(raw, AgentResponse) and raw.status is AgentStatus.TIMED_OUT else AttemptTerminalReason.FAILED; self._agent_failure(request, reason, exc.code, retryable=exc.code in _RETRYABLE_MODEL_FAILURES or candidate_failure_retryable(exc.code), details={**dict(retryable_agent_validation_details(exc) or {}), "failure_stage": "adapter_postflight"}); return
        try: self._accept_agent_response(request, response)
        except AgentACIError as exc:
            details = {**dict(retryable_agent_validation_details(exc) or {}), **literature_read_diagnostics(self._literature_jobs.get(("coverage", request.attempt_id))), "failure_stage": "host_candidate_validation", "rejected_response": response.to_dict()}
            self._agent_failure(request, AttemptTerminalReason.FAILED, exc.code,
                retryable=exc.code in _RETRYABLE_MODEL_FAILURES or candidate_failure_retryable(exc.code),
                details=details)
    def _accept_expert_packet(self, request: AgentRequest, response: AgentResponse, *, diagnostics: Mapping[str, Any] | None = None) -> None:
        if self.resource_recovery.for_request(request):
            raise AgentACIError("resource_infrastructure_blocked", "Host-observed pool failure permits only technical closure")
        validated = validate_postflight(request, response)
        task = request.task_context
        literature_diagnostics: dict[str, object] = {}
        if not isinstance(task, (ResearchNodeTask, AuxiliaryTask)):
            raise AgentACIError("invalid_task_context", "expert packet lacks its typed task")
        if request.command in {AgentCommand.SELF_EVOLVE, AgentCommand.GRAPH_EVOLVE}:
            from .expert_plugin import ExpertPluginStore
            try: literature_diagnostics["plugin_update"] = ExpertPluginStore(self.state_root, owner_kind="grounding" if request.command is AgentCommand.GRAPH_EVOLVE else "expert").accept_request_update(request, advance=False)
            except (ValueError, OSError) as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
        if request.command is AgentCommand.CYCLE_REPORT:
            from .expert_memory import ExpertMemory, MemoryError
            resources = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--expert-resources")).content_bytes())
            supplied = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--cycle-assessments")).content_bytes())
            try:
                commit = ExpertMemory(self.state_root).commit_report(request.run_id, request.role,
                    resources["memory_commit"], request.command_id, validated.workspace_files,
                    cycle=task.key.cycle, facts=supplied["assessments"], max_bytes=request.budget.max_output_bytes,
                    max_memory_bytes=node_memory_limit(request, "max_memory_bytes", 64 * 1024 * 1024),
                    **({"component_id": resources["report_component_id"]} if "report_component_id" in resources else {}))
            except (MemoryError, OSError) as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
            literature_diagnostics["memory"] = {"base_commit": resources["memory_commit"], "commit": commit}
        if request.command is AgentCommand.EXPERIMENT_EXECUTION:
            from .agent_aci import validate_execution_completion
            submitted = self._owned_tool_experiments(request)
            latest = None if not submitted else str(submitted[-1]["exp_id"])
            validate_execution_completion(None if latest is None else self.store.latest_transition(latest)["state"],
                waiting_for=validated.candidate.waiting_for_experiment_id, latest_experiment_id=latest,
                can_handoff=latest is not None and self._execution_can_handoff(request, latest))
        if isinstance(validated.candidate, (GroundingCandidate, LiteratureSummaryCandidate)) or request.command is AgentCommand.DEBATE and request.schema_version >= 14:
            from .grounding_background import validate_web_sources
            try: validate_web_sources(validated.candidate.web_sources or (), self._grounding_retrieved_urls(request))
            except ValueError as exc: raise AgentACIError("invalid_candidate", "Grounding web sources: " + str(exc)) from exc
        if isinstance(validated.candidate, GroundingCandidate) and request.schema_version >= 15:
            if task != self._grounding_task(request.run_id) or self._expert_success(task) is not None:
                raise AgentACIError("invalid_candidate", "Grounding response is not the current unfinished dialogue round")
        if ((isinstance(task, ResearchNodeTask) and task.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW and task.key.subkind is ResearchNodeSubkind.QUERY_PLAN) or isinstance(task, AuxiliaryTask) and task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN):
            try:
                template = LiteratureQuery.from_dict(json.loads(next(item for item in request.input_artifacts
                    if item.ref.endswith("--literature-query-template")).content_bytes()))
                query = admit_literature_query_plan(validated.candidate, template=template)
                if self.literature_io is not None:
                    unresolved = unresolved_query_plan_validation(self.store, request); rejected = None if unresolved is None else unresolved.get("rejected_candidate"); validation = None if unresolved is None else unresolved.get("unresolved_validation_error")
                    if (isinstance(rejected, Mapping) and query.query == rejected.get("query") and isinstance(validation, Mapping)): raise AgentACIError("invalid_candidate", f"query plan exactly repeats rejected query from Attempt {validation.get('source_attempt_id')}; change query text before another coverage probe")
                    read = self._read_literature(request.run_id, "coverage", request.attempt_id, query)
                    if read is None:
                        self._literature_responses[request.attempt_id] = (request, response, diagnostics)
                        return
                    if read["error"] is not None: raise read["error"]
                    literature_diagnostics = dict(read["diagnostics"])
                    if "literature_search" in literature_diagnostics:
                        diagnostics = dict(diagnostics or {}, literature_search=literature_diagnostics.pop("literature_search"))
            except (LiteratureError, StopIteration) as exc:
                raise AgentACIError("invalid_candidate", str(exc)) from exc
        synthesis = (isinstance(task, ResearchNodeTask)
            and task.key.node_kind is ResearchNodeKind.HYPOTHESIS_PROPOSAL
            and task.key.subkind is ResearchNodeSubkind.SYNTHESIS)
        if synthesis:
            contract = self._research_contract(request.run_id)
            component = contract.component(request.component_id)
            if component is None: raise OperationalHalt("synthesis component is absent from GoalDefined")
            if request.role not in component.proposer_roles: raise AgentACIError("candidate_component_role", "synthesis differs from the frozen proposer")
            candidates = ([member.candidate for member in validated.candidate.hypotheses]
                if self._batch_enabled(request.run_id) else [validated.candidate])
            for candidate in candidates: self._validate_required_input_family(request, candidate, retryable=True)
            if self._batch_enabled(request.run_id):
                config = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--expert-config")).content_bytes())
                if len(candidates) > config["effective_max_forks"]: raise AgentACIError("invalid_candidate", "SYNTHESIS exceeds its frozen admitted hypothesis count")
        adjudication = None
        if request.command.value == "ADJUDICATE":
            from .expert_loop import admit_adjudication
            history = self._batch_history(request.run_id, request.component_id, request.role)
            batch = history.latest
            if batch is None or batch.cycle != task.key.cycle: raise AgentACIError("invalid_candidate", "adjudication lacks its exact accepted batch")
            context, tips, terminal = self._batch_context(request.run_id, request.component_id, request.role, task.key.cycle)
            parent = history.hypotheses[next(iter(batch.members.values()))]["payload"].get("parent_hypothesis_id")
            try: adjudication = admit_adjudication(response.typed_candidate,
                lineage=ExpertLineage(run_id=request.run_id, component_id=request.component_id, expert_slot=request.role, cycle=task.key.cycle, parent_hypothesis_id=parent),
                batch=batch.members, terminal_chains=terminal, terminal_tips=tips, source_attempt_id=request.attempt_id,
                accepted_reference_ids=request.immutable_context_refs, required_method_family_id=self._required_input_method_family(request.run_id, request.component_id))
            except ValueError as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
        with self.store.transaction() as tx:
            run_head = tx.latest_transition(request.run_id)
            if run_head is not None and run_head["state"] == RunState.STOPPING.value and not self._is_final_report_request(request):
                tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.CANCELLED,
                    ended_at=time.time(), payload={"response_discarded_after_stop": True}); return
            tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.SUCCEEDED,
                ended_at=time.time(), payload={"request": request.to_dict(),
                    "response": response.to_dict(), **literature_diagnostics,
                    **({"diagnostics": dict(diagnostics)} if diagnostics is not None else {}),
                    **self._grounding_completed_web_metadata(request),
                    **({"session": self.agent_session_metadata[request.attempt_id]} if request.attempt_id in self.agent_session_metadata else {})})
            if isinstance(validated.candidate, GroundingCandidate) and validated.candidate.clarification is not None:
                self.grounding_dialogue.open_question(tx, request, validated.candidate.clarification)
                return
            if adjudication is not None:
                tx.append_transition(self._transition_record(tx, request.run_id, request.run_id, "RUN", Command.ACCEPT_ADJUDICATION, RunState.ACTIVE,
                    payload={"node_command_id": task.command_id, "agent_attempt_id": request.attempt_id, "agent_output_digest": response.output_digest,
                        "role": request.role, "component_id": request.component_id, "adjudication": {
                            "cycle": task.key.cycle, "batch_command_id": batch.command_id, "candidate": adjudication.to_dict(),
                            "selected_tip": None if adjudication.selected_tip is None else adjudication.selected_tip.to_dict(),
                            "pruned_by": [dict(item) for item in adjudication.pruned_by]}}))
                return
            if request.command is AgentCommand.GRAPH_EVOLVE:
                context = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--manager-context")).content_bytes())
                tx.append_transition(self._transition_record(tx, request.run_id, request.run_id, "RUN", Command.ACCEPT_GRAPH_EVOLUTION, RunState.ACTIVE,
                    payload={"node_command_id": task.command_id, "agent_attempt_id": request.attempt_id, "agent_output_digest": response.output_digest,
                        "graph_evolution": [item.to_dict() for item in validated.candidate.graph_evolution],
                        "source_report_refs": context["source_report_refs"], "plugin_update": literature_diagnostics["plugin_update"]}))
                return
            if not synthesis: return
            if self._batch_enabled(request.run_id):
                self._accept_hypothesis_batch(tx, request, response, validated.candidate); return
            candidate = validated.candidate
            ids, component_index, proposer_index = _component_ids(
                request.run_id, contract, request.component_id, request.role)
            cycle = task.key.cycle
            chain = self._expert_hypothesis_chain(
                request.run_id, request.component_id, request.role,
            )
            expected_parent = (None if cycle == 1 or len(chain) < cycle - 1
                               else str(chain[cycle - 2]["hypothesis_id"]))
            if (len(chain) != cycle - 1
                    or candidate.parent_hypothesis_id != expected_parent
                    or (cycle == 1) != (candidate.relation is None)):
                raise AgentACIError(
                    "hypothesis_lineage_mismatch",
                    "synthesis differs from the exact predecessor lineage",
                )
            hypothesis_id = (ids["hypothesis"] if cycle == 1
                             else f"{ids['hypothesis']}--cycle-{cycle}")
            fact = HypothesisProposed(
                fact_id=_fact_id(request.run_id, "hypothesis", component_index,
                    proposer_index, *((cycle,) if cycle > 1 else ())),
                run_id=RunId(request.run_id), hypothesis_id=HypothesisId(hypothesis_id),
                goal_id=GoalId(ids["goal"]), component_id=ComponentId(request.component_id),
                proposer_role=candidate.proposer_role,
                method_family_id=MethodFamilyId(candidate.method_family_id), claim=candidate.claim,
                assumptions=candidate.assumptions,
                parent_hypothesis_id=candidate.parent_hypothesis_id, relation=candidate.relation,
                measurement=candidate.measurement)
            try: mutation = self.kernel.construct_fact(fact, tx)
            except ResearchValidationError as exc:
                raise AgentACIError("invalid_candidate", str(exc)) from exc
            tx.append_fact(mutation)
            transition = self._transition_record(tx, request.run_id, request.run_id, "RUN",
                Command.ACCEPT_HYPOTHESIS, RunState.ACTIVE, fact_id=str(fact.fact_id),
                payload={"node_command_id": task.command_id,
                    "agent_attempt_id": request.attempt_id,
                    "agent_output_digest": response.output_digest,
                    "role": request.role, "component_id": request.component_id,
                    "method_family_id": str(fact.method_family_id),
                    "hypothesis_id": str(fact.hypothesis_id)})
            tx.append_transition(transition)
    def _accept_hypothesis_batch(self, tx: StateTransaction, request: AgentRequest, response: AgentResponse, candidate: Any) -> None:
        from .expert_loop import chain_session_scope
        task = request.task_context; contract = self._research_contract(request.run_id)
        history = self._batch_history(request.run_id, request.component_id, request.role)
        cycle = task.key.cycle
        parent = None if history.selected_tip is None else history.selected_tip["node_id"]
        if len(history.batches) != cycle - 1: raise AgentACIError("invalid_candidate", "SYNTHESIS batch cycle is not the next accepted cycle")
        directed = self.interaction.directed_round(request.run_id, request.role, request.component_id, cycle)
        if directed is not None: parent = directed["parent_hypothesis_id"]
        parents = {parent}
        if cycle > 1 and parent is None:
            if request.schema_version < 17 or not self._unselected_batch_revision_ready(request.run_id, request.component_id, request.role):
                raise AgentACIError("invalid_candidate", "unselected batch successor lacks closed explicit revision")
            parents = set(history.latest.members.values())
        ids, component_index, proposer_index = _component_ids(request.run_id, contract, request.component_id, request.role)
        members = {item.chain_id: f"{ids['hypothesis']}--cycle-{cycle}--{chain_session_scope(item.chain_id)}" for item in candidate.hypotheses}
        for index, member in enumerate(candidate.hypotheses):
            proposed = member.candidate
            if proposed.parent_hypothesis_id not in parents or (cycle == 1) != (proposed.relation is None):
                raise AgentACIError("invalid_candidate", "batch member differs from the accepted parent selection")
            fact = HypothesisProposed(fact_id=FactId(f"{_fact_id(request.run_id, 'hypothesis', component_index, proposer_index, cycle)}--{chain_session_scope(member.chain_id)}"),
                run_id=RunId(request.run_id), hypothesis_id=HypothesisId(members[member.chain_id]), goal_id=GoalId(ids["goal"]),
                component_id=ComponentId(request.component_id), proposer_role=proposed.proposer_role,
                method_family_id=MethodFamilyId(proposed.method_family_id), claim=proposed.claim, assumptions=proposed.assumptions,
                parent_hypothesis_id=proposed.parent_hypothesis_id, relation=proposed.relation, measurement=proposed.measurement)
            try: tx.append_fact(self.kernel.construct_fact(fact, tx))
            except ResearchValidationError as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
            tx.append_transition(self._transition_record(tx, request.run_id, request.run_id, "RUN", Command.ACCEPT_HYPOTHESIS, RunState.ACTIVE,
                fact_id=str(fact.fact_id), payload={"node_command_id": task.command_id, "agent_attempt_id": request.attempt_id,
                    "agent_output_digest": response.output_digest, "role": request.role, "component_id": request.component_id,
                    "method_family_id": str(fact.method_family_id), "hypothesis_id": str(fact.hypothesis_id), "chain": member.chain_id,
                    **({"hypothesis_batch": {"cycle": cycle, "chains": members}} if index == len(candidate.hypotheses) - 1 else {})}))
    def _batch_chain_failed(self, run_id: str, component_id: str, role: str, cycle: int, chain: str) -> bool:
        from .expert_batch import batch_chain_failed
        return batch_chain_failed(self, run_id, component_id, role, cycle, chain)
    def _batch_context(self, run_id: str, component_id: str, role: str, cycle: int) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
        from .expert_loop import compared_tip
        history = self._batch_history(run_id, component_id, role); batch = history.batches[cycle - 1]
        tips = {}; terminal = []
        for chain, hypothesis_id in batch.members.items():
            rows = self.store.inspect("SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1 AND json_extract(payload_json,'$.hypothesis_id')=? ORDER BY rowid", (run_id, hypothesis_id))
            tip = None; assessed = False
            if rows:
                identity = json.loads(rows[-1]["payload_json"]); experiment_id = str(rows[-1]["workflow_id"])
                spec = self.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
                outcomes = [row for row in self.store.list_facts(run_id, "CanonicalOutcomeAccepted") if row["experiment_id"] == experiment_id]
                if outcomes and spec is not None:
                    def raw(row): return {**row, "payload_json": json.dumps(row["payload"])}
                    tip = compared_tip(chain, hypothesis=raw(history.hypotheses[hypothesis_id]), preregistration=raw(spec), outcome=raw(outcomes[0]))
                    analysis = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role, cycle=cycle,
                        chain=chain, node_kind=ResearchNodeKind.RESULT_ANALYSIS, experiment_ordinal=identity["chain_iteration"]))
                    accepted = self._expert_success(analysis)
                    decision = {} if accepted is None else json.loads(accepted["observation_json"])["response"]["typed_candidate"]
                    assessed = any(str(row["hypothesis_id"]) == hypothesis_id for row in self.store.list_facts(run_id, "HypothesisAssessed"))
                    if assessed and (self._expert_task_final_failure(run_id, analysis) is not None or decision.get("iteration_disposition") == "COMPLETE" or identity["chain_iteration"] >= batch_limits(self._research_contract(run_id).document, role)["iterations"] or not self._batch_creation_available(run_id, role)): terminal.append(chain)
            # A terminal Agent does not close an experiment it already submitted.
            # Keep that chain schedulable through collection and assessment.
            if not rows or self.store.latest_transition(experiment_id)["state"] == ExperimentState.TERMINAL.value:
                # Settle the measured tip before adjudication, including when
                # its requested successor failed before submission.
                if (tip is None or assessed) and self._batch_chain_failed(run_id, component_id, role, cycle, chain):
                    terminal.append(chain)
            tips[chain] = tip
        context = {"batch_command_id": batch.command_id, "cycle": cycle, "chains": dict(batch.members),
            "terminal_tips": {chain: None if tip is None else tip.to_dict() for chain, tip in tips.items()},
            "selected_tip": None if batch.selection is None else batch.selection.get("selected_tip")}
        return context, tips, tuple(sorted(set(terminal)))
    def _poll_agents(self) -> bool:
        if self.claude_agent_host is None: return False
        progressed = False
        for attempt_id, handle in tuple(self.running_agents.items()):
            self._diagnostic_context = {"phase": "agent_observation", "run_id": handle.request.run_id, "command_id": handle.request.command_id, "attempt_id": attempt_id}
            run_head = self.store.latest_transition(handle.request.run_id)
            paused = handle.request.run_id in self.dispatch_errors and run_head["state"] != RunState.STOPPING.value
            sibling_failed = sibling_proposal_failed(self, handle.request)
            resource_blocked = self.resource_recovery.for_request(handle.request)
            if paused or sibling_failed or resource_blocked: self.claude_agent_host.cancel(handle)
            observation = self.claude_agent_host.poll(handle)
            if observation is None: continue
            progressed = True; self.running_agents.pop(attempt_id, None)
            details = {"returncode": observation.returncode, "pid": observation.pid, "process_group_id": observation.process_group_id, "failure_code": observation.failure_code, "diagnostics": dict(observation.diagnostics)}
            if handle.request.schema_version >= 11 and hasattr(handle.tool_workspace, "finish_node"):
                try:
                    session = handle.tool_workspace.finish_node()
                    self.agent_session_metadata[attempt_id] = session
                    if observation.response is not None and (not isinstance(session.get("transcript"), Mapping) or session.get("transcript_complete") is not True):
                        raise ValueError("successful Agent requires a complete archived transcript")
                except (OSError, ValueError, GitAuthoringPreparationError, GitAuthoringIntegrityError) as exc:
                    if isinstance(exc, SessionFinishError):
                        self.agent_session_metadata[attempt_id] = exc.metadata
                    self._agent_failure(handle.request, AttemptTerminalReason.FAILED, "session_archive_failed", retryable=False,
                        details={**details, "session_error": str(exc)[:1024],
                            **({"discarded_response": observation.response.to_dict()} if observation.response is not None else {})})
                    self.claude_agent_host.cleanup(handle); self.agent_session_metadata.pop(attempt_id, None); continue
            if run_head is not None and run_head["state"] == RunState.STOPPING.value and not self._is_final_report_request(handle.request):
                self._agent_failure(handle.request, AttemptTerminalReason.CANCELLED, "run_stopping", retryable=False, details=details, owner_transition=False)
            elif paused:
                self._agent_failure(handle.request, AttemptTerminalReason.FAILED, "dispatch_paused", retryable=False, owner_transition=False, details={**details, "dispatch_error": dict(self.dispatch_errors[handle.request.run_id]), **({"discarded_response": observation.response.to_dict()} if observation.response is not None else {})})
            elif sibling_failed:
                self._agent_failure(handle.request, AttemptTerminalReason.CANCELLED, "proposal_sibling_failed", retryable=False,
                    owner_transition=False, details={**details, **({"discarded_response": observation.response.to_dict()} if observation.response is not None else {})})
            elif self._run_wallclock_expired(handle.request.run_id):
                details.update({"discarded_response": observation.response.to_dict()} if observation.response is not None else {}); self._agent_failure(handle.request, AttemptTerminalReason.TIMED_OUT, "profile_wallclock_exhausted", retryable=False, details=details)
            elif resource_blocked and (observation.failure_code == "resource_cleanup_failed" or observation.diagnostics.get("resource_cleanup_error")):
                self._agent_failure(handle.request, AttemptTerminalReason.FAILED, "resource_cleanup_failed", retryable=False, details=details)
            elif resource_blocked:
                self.resource_recovery.close(handle.request, details={**details,
                    **({"discarded_response": observation.response.to_dict()} if observation.response is not None else {})})
            elif observation.response is not None:
                try: self._accept_agent_response(handle.request, observation.response, diagnostics=observation.diagnostics)
                except AgentACIError as exc:
                    if (validation_details := retryable_agent_validation_details(exc)) is not None: details["diagnostics"] = {**dict(details.get("diagnostics", {})), **dict(validation_details["diagnostics"])}
                    details.update({"failure_stage": "host_candidate_validation",
                                    "rejected_response": observation.response.to_dict(),
                                    **literature_read_diagnostics(self._literature_jobs.get(("coverage", handle.request.attempt_id)))})
                    self._agent_failure(handle.request, AttemptTerminalReason.FAILED, exc.code,
                        retryable=exc.code in _RETRYABLE_MODEL_FAILURES or candidate_failure_retryable(exc.code),
                        details=details)
            else:
                reason = {AgentHostTerminalReason.TIMED_OUT: AttemptTerminalReason.TIMED_OUT, AgentHostTerminalReason.CANCELLED: AttemptTerminalReason.CANCELLED}.get(observation.terminal_reason, AttemptTerminalReason.FAILED)
                self._agent_failure(handle.request, reason, observation.failure_code or "agent_host_failure", retryable=(observation.failure_code or "") in _RETRYABLE_AGENT_FAILURES or candidate_failure_retryable(observation.failure_code or ""), details=details)
            self.claude_agent_host.cleanup(handle)
            if attempt_id not in self._literature_responses and attempt_id not in self.paper_reading.completions:
                self.agent_session_metadata.pop(attempt_id, None)
        return progressed
    def _agent_failure(self, request: AgentRequest, reason: AttemptTerminalReason, code: str, *, retryable: bool, details: Mapping[str, Any] | None = None, owner_transition: bool = True) -> None:
        if self.paper_reading.defer_failure(request, reason, code, retryable=retryable, details=details, owner_transition=owner_transition): return
        if request.command is AgentCommand.PAPER_READ: owner_transition = False
        rendezvous = None if request.command is not AgentCommand.ANSWER_RENDEZVOUS else RendezvousRequest.from_dict(json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--rendezvous")).content_bytes()))
        retry_class = _agent_retry_class(code, details, retryable=retryable,
            bounded_context=request.schema_version >= 24 and (request.command is not AgentCommand.DEBATE or request.schema_version >= 29))
        observation = {**dict(details or {}), "failure_code": code, "failure_stage": _agent_failure_stage(code, details), "retry_class": retry_class,
            **self._grounding_completed_web_metadata(request),
            **({"session": self.agent_session_metadata[request.attempt_id]} if request.attempt_id in self.agent_session_metadata else {})}
        with self.store.transaction() as tx:
            tx.close_attempt(request.attempt_id, terminal_reason=reason, ended_at=time.time(), payload=observation)
            from .literature_recovery import deferred_attempt
            if deferred_attempt(observation) and tx.latest_transition(request.run_id)["state"] in {"CREATED", "ACTIVE"}:
                return  # Shared literature availability owns recovery; no node exhaustion.
            from .provider_observation import PROVIDER_DEFERRED
            if (observation.get("diagnostics", {}).get("provider_failure", {}).get("code") in PROVIDER_DEFERRED
                    and retry_class == "INFRASTRUCTURE"
                    and not self._is_final_report_request(request)
                    and tx.latest_transition(request.run_id)["state"] in {RunState.CREATED.value, RunState.ACTIVE.value}):
                return  # Admission/service waits do not exhaust scientific nodes.
            if self.resource_recovery.for_request(request): return
            head = tx.latest_transition(request.owner_workflow_id)
            if not owner_transition or head is None: return
            state = (RunState(head["state"]) if request.owner_workflow_id == request.run_id else ExperimentState(head["state"])) if rendezvous is not None else (RunState.ACTIVE if request.owner_workflow_id == request.run_id else ExperimentState.DRAFTED)
            if self._is_final_report_request(request) and head["state"] == RunState.STOPPING.value: state = RunState.STOPPING
            if head["state"] != state.value: return
            attempts = list(tx.list_attempts(workflow_id=request.owner_workflow_id, command_id=request.command_id))
            if retry_class is not None and not self._agent_attempts_exhausted(self._research_contract(request.run_id), attempts): return
            if rendezvous is not None:
                disposition = (RendezvousDisposition.CANCELLED if reason is AttemptTerminalReason.CANCELLED else RendezvousDisposition.TIMED_OUT if reason in {AttemptTerminalReason.TIMED_OUT, AttemptTerminalReason.LOST} else RendezvousDisposition.INTEGRITY_REJECTED)
                reply = rendezvous.terminal_reply(disposition, request.role, f"Rendezvous ended without an accepted reply: {str(code)[:256]}.")
                owner = request.owner_workflow_id; owner_head = tx.latest_transition(owner)
                if owner_head is None: raise OperationalHalt("rendezvous owner workflow is absent")
                owner_state = RunState(owner_head["state"]) if owner == request.run_id else ExperimentState(owner_head["state"])
                tx.append_transition(self._transition_record(tx, request.run_id, owner, "RUN" if owner == request.run_id else "EXPERIMENT", Command.RESOLVE_RENDEZVOUS, owner_state,
                    payload={"rendezvous_id": rendezvous.rendezvous_id, "requester_slot": rendezvous.requester_slot, "rendezvous_reply": reply.to_dict()})); return
            payload = {"agent_final_failure": True,
                "attempt_id": request.attempt_id, "failure_code": code,
                "role": request.role, "component_id": request.component_id,
                "method_family_id": request.method_family_id}
            if isinstance(request.task_context, (ResearchNodeTask, AuxiliaryTask)):
                payload["failed_task_command_id"] = request.task_context.command_id
            transition = self._transition_record(tx, request.run_id, request.owner_workflow_id, "RUN" if request.owner_workflow_id == request.run_id else "EXPERIMENT", Command.AGENT_ATTEMPT_FINAL_FAILURE, state,
                payload=payload)
            tx.append_transition(transition)
    def _start_spec_validation(self, request: AgentRequest, response: AgentResponse, *, diagnostics: Mapping[str, Any] | None = None) -> None:
        if self.publisher is None: raise OperationalHalt("Workflow Host is not initialized")
        contract = self._research_contract(request.run_id); execution_id = f"{request.owner_workflow_id}--candidate-validation"; validation_index = len(self.store.list_attempts(execution_id=execution_id)) + 1; ordinal = 1; attempt_id = f"{request.owner_workflow_id}--spec-validation-{validation_index}"; timeout = min(float(contract.document["budget"].get("experiment_timeout", _CONFIG["process_timeout"])), _CONFIG["process_timeout"]); agent_request = request.to_dict(); agent_response = response.to_dict()
        prepared, workspace = prepare_candidate_validation(self.publisher.root, self.state_root, self.compute_resources, agent_request=agent_request, agent_response=agent_response, attempt_id=attempt_id, attempt_ordinal=ordinal, execution_id=execution_id, timeout_seconds=timeout, require_result_reference=not component_is_production(contract.document, str(request.component_id))); expired_before_open = False
        with self.store.transaction() as tx:
            if (run_head := tx.latest_transition(request.run_id)) is not None and run_head["state"] == RunState.STOPPING.value: tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.CANCELLED, ended_at=time.time(), payload={"response_discarded_after_stop": True}); return
            run_record = tx.get_run(request.run_id)
            expired_before_open = run_record is None or wallclock_expired(contract.document, run_record["created_at"])
            if not expired_before_open:
                tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.SUCCEEDED, ended_at=time.time(), payload={"response": agent_response, **({"diagnostics": dict(diagnostics)} if diagnostics is not None else {}), **({"session": self.agent_session_metadata[request.attempt_id]} if request.attempt_id in self.agent_session_metadata else {})})
                transition = self._transition_record(tx, request.run_id, request.owner_workflow_id, "EXPERIMENT", Command.VALIDATE_SPEC_CANDIDATE, ExperimentState.DRAFTED, payload={"attempt_id": attempt_id, "agent_attempt_id": request.attempt_id, "agent_output_digest": response.output_digest, "process_request": prepared.process_request.to_dict(), "role": request.role, "component_id": request.component_id})
                tx.open_attempt({"attempt_id": attempt_id, "run_id": request.run_id, "workflow_id": request.owner_workflow_id, "command_id": transition["command_id"], "kind": "PROCESS", "experiment_id": request.owner_workflow_id, "execution_id": execution_id, "attempt_ordinal": ordinal, "host_generation": self.generation, "started_at": time.time()}); tx.append_transition(transition)
        if expired_before_open: workspace.cleanup_attempt(prepared.attempt_id); self._agent_failure(request, AttemptTerminalReason.TIMED_OUT, "profile_wallclock_exhausted", retryable=False); return
        try:
            assert_candidate_patch_compiles(workspace, contract.document, str(request.component_id), agent_request=agent_request, agent_response=agent_response); workspace.materialize(prepared, agent_request=agent_request, agent_response=agent_response); workspace.assert_prestart_clean(prepared, agent_request=agent_request, agent_response=agent_response)
            if self._run_wallclock_expired(request.run_id):
                self._finish_spec_validation(self.store.get_attempt(attempt_id), prepared, workspace, None, terminal_reason=AttemptTerminalReason.TIMED_OUT); return
            handle = self._start_registered_process(prepared.process_request)
        except (ExecutionWorkspaceError, OSError, ValueError) as exc: self._finish_spec_validation(self.store.get_attempt(attempt_id), prepared, workspace, None, exc); return
        self.running[attempt_id] = handle; self.spec_validations[attempt_id] = (prepared, workspace)
    def _finish_spec_validation(self, attempt: Mapping[str, Any] | None, prepared: PreparedExecution, workspace: ExecutionWorkspace, observation: Any | None, error: Exception | None = None, *, terminal_reason: AttemptTerminalReason | None = None) -> None:
        if attempt is None: raise OperationalHalt("candidate validation attempt is absent")
        failure = "profile_wallclock_exhausted" if terminal_reason is AttemptTerminalReason.TIMED_OUT else None if error is None else f"{type(error).__name__}:{error}"
        diagnostics = bounded_diagnostics(prepared, observation, failure=failure); reason = terminal_reason or (AttemptTerminalReason.FAILED if observation is None else observation.terminal_reason); diagnostics.update({"attempt_id": prepared.attempt_id, "attempt_ordinal": prepared.process_request.attempt_ordinal, "execution_id": prepared.process_request.execution_id, "terminal_reason": reason.value})
        if (source := self.store.get_attempt(prepared.agent_attempt_id)) is None: raise OperationalHalt("candidate validation source Agent attempt is absent")
        source_observation = json.loads(source["observation_json"]); request = AgentRequest.from_dict(source_observation["request"]); response = AgentResponse.from_dict(source_observation["response"]); success = reason is AttemptTerminalReason.SUCCEEDED; contract = self._research_contract(request.run_id); preflight_role = self._review_role(contract, request.component_id, "preflight"); workspace.cleanup_attempt(prepared.attempt_id)
        with self.store.transaction() as tx:
            proceed = (run_head := tx.latest_transition(request.run_id)) is not None and run_head["state"] != RunState.STOPPING.value
            tx.close_attempt(prepared.attempt_id, terminal_reason=reason, ended_at=time.time(), process_group_id=attempt["process_group_id"] if observation is None else observation.process_group_id, payload=diagnostics)
            tx.append_transition(self._transition_record(tx, request.run_id, request.owner_workflow_id, "EXPERIMENT", Command.OBSERVE_SPEC_VALIDATION, ExperimentState.DRAFTED, payload={"attempt_id": prepared.attempt_id, "agent_attempt_id": request.attempt_id, "component_id": request.component_id, "validation_succeeded": success}))
            if proceed and success and preflight_role is not None: hypotheses = {str(row["fact_id"]) for row in tx.list_facts(request.run_id, "HypothesisProposed") if row["payload"].get("component_id") == request.component_id}; origin, record = preflight_origin(request, contract, prepared.attempt_id, hypotheses, preflight_role); tx.append_transition(self._transition_record(tx, request.run_id, request.owner_workflow_id, "EXPERIMENT", Command.REQUEST_RENDEZVOUS, ExperimentState.DRAFTED, payload={"role": request.role, "component_id": request.component_id, "rendezvous_id": origin.rendezvous_id, "rendezvous_origin": record, "rendezvous_request": origin.request(request.run_id).to_dict()}))
        if proceed and success and preflight_role is None: self._accept_agent_response(request, response, review_complete=True)
    def _accept_agent_response(self, request: AgentRequest, response: AgentResponse, *, review_complete: bool = False, diagnostics: Mapping[str, Any] | None = None) -> None:
        if self.resource_recovery.close(request, details={"discarded_response": response.to_dict(), "diagnostics": dict(diagnostics or {})}): return
        if sibling_proposal_failed(self, request):
            self._agent_failure(request, AttemptTerminalReason.CANCELLED, "proposal_sibling_failed", retryable=False,
                owner_transition=False, details={"discarded_response": response.to_dict(), "diagnostics": dict(diagnostics or {})}); return
        if self.paper_reading.defer_response(request, response, review_complete=review_complete, diagnostics=diagnostics): return
        validated = validate_postflight(request, response); self._crash("after_agent_observation_before_close")
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and self._tool_execution_enabled(request.run_id):
            self._accept_expert_packet(request, response, diagnostics=diagnostics)
            if request.attempt_id not in self._literature_responses: self._crash("after_agent_terminal_commit")
            return
        if request.command in {
            AgentCommand.GROUNDING,
            AgentCommand.PAPER_READ,
            AgentCommand.LITERATURE_REVIEW,
            AgentCommand.HYPOTHESIS_PROPOSAL,
            AgentCommand.ADJUDICATE,
            AgentCommand.SELF_EVOLVE,
            AgentCommand.GRAPH_EVOLVE,
            AgentCommand.FINAL_REPORT,
            AgentCommand.EXPERIMENT_EXECUTION,
            AgentCommand.RESULT_ANALYSIS,
            AgentCommand.HYPOTHESIS_UPDATE,
            AgentCommand.CYCLE_REPORT,
            AgentCommand.OBSERVER,
            AgentCommand.DEBATE,
            AgentCommand.DEBATE_JUDGE,
            AgentCommand.LEADER,
            AgentCommand.COMPACT_CONTEXT,
        }:
            self._accept_expert_packet(request, response, diagnostics=diagnostics)
            if request.attempt_id not in self._literature_responses: self._crash("after_agent_terminal_commit")
            return
        if request.command is AgentCommand.ANSWER_RENDEZVOUS:
            rendezvous = RendezvousRequest.from_dict(json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--rendezvous")).content_bytes())); reply = validated.candidate
            with self.store.transaction() as tx:
                if (head := tx.latest_transition(request.run_id)) is not None and head["state"] == RunState.STOPPING.value: tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.CANCELLED, ended_at=time.time(), payload={"response_discarded_after_stop": True}); return
                tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.SUCCEEDED, ended_at=time.time(), payload={"response": response.to_dict(), **({"diagnostics": dict(diagnostics)} if diagnostics is not None else {}), **({"session": self.agent_session_metadata[request.attempt_id]} if request.attempt_id in self.agent_session_metadata else {})})
                owner = request.owner_workflow_id; owner_head = tx.latest_transition(owner)
                if owner_head is None: raise OperationalHalt("rendezvous owner workflow is absent")
                state = RunState(owner_head["state"]) if owner == request.run_id else ExperimentState(owner_head["state"])
                tx.append_transition(self._transition_record(tx, request.run_id, owner, "RUN" if owner == request.run_id else "EXPERIMENT", Command.RESOLVE_RENDEZVOUS, state,
                    payload={"rendezvous_id": rendezvous.rendezvous_id, "requester_slot": rendezvous.requester_slot, "agent_attempt_id": request.attempt_id, "rendezvous_reply": reply.to_dict()}))
            self._crash("after_agent_terminal_commit"); return
        config = self._run_config(request.run_id); contract = self._research_contract(request.run_id)
        component = contract.component(request.component_id)
        if component is None: raise OperationalHalt("request component is absent from GoalDefined")
        raw_component = next(item for item in contract.to_dict()["components"] if item["id"] == request.component_id)
        ids, component_index, proposer_index = _component_ids(request.run_id, contract, request.component_id, request.role if request.command is AgentCommand.PROPOSE_HYPOTHESIS else None)
        selected_id: str | None = None
        if request.command in {AgentCommand.DRAFT_EXPERIMENT_SPEC, AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT}:
            candidate = validated.candidate; expected_metrics = () if component.metric is None else (component.metric,)
            expected_workspace = ({"experiment.py"} if request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC
                                  else {"experiment.py", "implementation.patch"})
            checks = ((candidate.component_id == request.component_id, "candidate_component"),
                (candidate.owner_role == component.owner_role == request.role, "candidate_owner_role"),
                (candidate.method_family_id == request.method_family_id, "candidate_method_family"),
                (candidate.metrics == expected_metrics, "candidate_metrics"),
                (candidate.evaluator_version == OUTCOME_ADAPTER_VERSION, "candidate_evaluator"),
                (tuple(candidate.artifact_requirements) == ("result.json",), "candidate_artifact_requirements"),
                (set(validated.workspace_files) == expected_workspace, "candidate_workspace_files"))
            failure = next((code for accepted, code in checks if not accepted), None)
            if failure: raise AgentACIError(failure, "spec differs from the frozen component/evaluator contract")
            capability_input = next((item for item in request.input_artifacts if item.ref.endswith("--compute-capabilities")), None)
            if capability_input is None: raise AgentACIError("compute_capabilities_missing", "spec request lacks frozen compute capabilities")
            try: capabilities = ComputeCapabilities.from_bytes(capability_input.content_bytes())
            except ValueError as exc: raise AgentACIError("resource_unsatisfied", str(exc)) from exc
            try: cpu_only_artifact = (component.result_kind is ComponentResultKind.ARTIFACT and component_execution_plan(contract.document, request.component_id).get("production") is False)
            except ValueError as exc: raise AgentACIError("execution_plan", str(exc)) from exc
            if cpu_only_artifact and candidate.resource_preference is ResourcePreference.GPU_REQUIRED: raise AgentACIError("resource_unsatisfied", "non-production artifact work is CPU-only; use CPU or AUTO")
            budget = contract.document["budget"]
            minimum = int(budget.get("min_vram_per_experiment", 0))
            if self.shared_gpu is not None: minimum = self.shared_gpu.workload_budget(request, {}, minimum)
            try: grant = ResourceGrant(kind=ResourceKind.CPU) if cpu_only_artifact else (self.compute_resources.plan_intent if self.shared_gpu else self.compute_resources.select)(candidate.resource_preference, capabilities, min_vram_per_experiment=minimum, gpu_vram_reserve_mb=gpu_memory_reserve_mib(budget, minimum))
            except (ResourceUnavailable, ValueError) as exc: raise AgentACIError("resource_unsatisfied", str(exc)) from exc
            if isinstance(grant, ResourceIntent): grant = self.shared_gpu.plan_intent(request, {}, grant)
            creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (request.owner_workflow_id,))
            if not creation: raise AgentACIError("experiment_identity", "experiment lacks its creation transition")
            creation_identity = json.loads(creation[0]["payload_json"]); selected_id = str(creation_identity["hypothesis_id"])
            experiment_ordinal = int(creation_identity.get("experiment_ordinal", 1))
            ids, component_index, proposer_index = _component_ids(request.run_id, contract, request.component_id,
                experiment_ordinal=experiment_ordinal)
        else:
            candidate = validated.candidate
            if (candidate.component_id != request.component_id or candidate.proposer_role != request.role
                    or request.role not in component.proposer_roles):
                raise AgentACIError("candidate_component_role", "hypothesis differs from its frozen component proposer")
        self._validate_required_input_family(request, candidate, retryable=request.command is AgentCommand.PROPOSE_HYPOTHESIS)
        if request.command in {AgentCommand.DRAFT_EXPERIMENT_SPEC, AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT} and not review_complete:
            self._start_spec_validation(request, response, diagnostics=diagnostics); self._crash("after_agent_terminal_commit"); return
        execution_plan = None
        if request.command in {AgentCommand.DRAFT_EXPERIMENT_SPEC, AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT}:
            try:
                patch = self._implementation_patch(request.run_id, request.component_id, candidate.method_family_id)
                execution_plan = _component_plan(contract.document, request.component_id, candidate.method_family_id, patch, grant.kind is ResourceKind.GPU)
            except (OSError, TargetPreparationError, ValueError) as exc:
                raise AgentACIError("execution_plan", str(exc)) from exc
        with self.store.transaction() as tx:
            head = tx.latest_transition(request.run_id)
            if head is not None and head["state"] == RunState.STOPPING.value:
                tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.CANCELLED,
                    ended_at=time.time(), payload={"response_discarded_after_stop": True}); return
            if not review_complete: tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.SUCCEEDED, ended_at=time.time(), payload={"response": response.to_dict(), **({"diagnostics": dict(diagnostics)} if diagnostics is not None else {}), **({"session": self.agent_session_metadata[request.attempt_id]} if request.attempt_id in self.agent_session_metadata else {})})
            self._crash("inside_agent_after_close_before_fact")
            if request.command is AgentCommand.PROPOSE_HYPOTHESIS:
                candidate = validated.candidate
                fact = HypothesisProposed(fact_id=_fact_id(request.run_id, "hypothesis", component_index, proposer_index), run_id=RunId(request.run_id),
                    hypothesis_id=HypothesisId(ids["hypothesis"]), goal_id=GoalId(ids["goal"]),
                    component_id=ComponentId(request.component_id), proposer_role=candidate.proposer_role,
                    method_family_id=MethodFamilyId(candidate.method_family_id), claim=candidate.claim,
                    assumptions=candidate.assumptions, parent_hypothesis_id=candidate.parent_hypothesis_id,
                    relation=candidate.relation)
                transition_command = Command.ACCEPT_HYPOTHESIS; kind = "RUN"; next_state: RunState | ExperimentState = RunState.ACTIVE
            else:
                candidate = validated.candidate; execution = config["execution"]
                if execution_plan is None: raise AssertionError("validated spec lacks its execution plan")
                if selected_id is None: raise AssertionError("validated spec lacks selected hypothesis")
                upstream = []; required_inputs: dict[str, list[str]] = {}
                for input_index, requirement in enumerate(component.required_inputs, 1):
                    matches = [row for row in tx.list_facts(request.run_id, "CanonicalOutcomeAccepted")
                               if row["payload"].get("component_id") == str(requirement.produced_by_component)
                               and row["payload"].get("method_family_id") == candidate.method_family_id
                               and row["payload"].get("outcome") in {ScientificOutcome.SUCCESS.value, ScientificOutcome.CLEAN_REFUTATION.value}]
                    if not matches: raise AgentACIError("upstream_outcome", f"required producer {requirement.produced_by_component!r} has no comparable accepted tip")
                    def outcome_ordinal(row: Mapping[str, Any]) -> int:
                        if (creation := tx.execute("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (row["experiment_id"],)).fetchone()) is None: raise OperationalHalt("producer outcome lacks its experiment creation")
                        ordinal = json.loads(creation["payload_json"]).get("experiment_ordinal", 1)
                        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1: raise OperationalHalt("producer experiment ordinal is invalid")
                        return ordinal
                    producer = max(matches, key=outcome_ordinal)
                    logical = next((item for item in producer["payload"].get("logical_artifacts", ())
                                    if item.get("logical_artifact") == requirement.logical_artifact), None)
                    paths = () if not isinstance(logical, Mapping) else tuple(logical.get("paths", ()))
                    if len(paths) < requirement.min_count: raise AgentACIError("upstream_artifact", f"producer lacks {requirement.logical_artifact!r}")
                    required_inputs[requirement.resource] = [f"/hypoloom-inputs/{input_index}/{index}" for index in range(1, len(paths) + 1)]
                    upstream.append(ProducerOutcomeBinding(resource=requirement.resource,
                        produced_by_component=requirement.produced_by_component, logical_artifact=requirement.logical_artifact,
                        min_count=requirement.min_count, outcome_id=OutcomeId(str(producer["outcome_id"])),
                        artifact_id=ArtifactId(str(producer["artifact_id"]))))
                timeout = float(contract.document["budget"].get("experiment_timeout", _CONFIG["process_timeout"]))
                environment = {"HYPOLOOM_DELAY": str(execution["delay_seconds"]),
                    "HYPOLOOM_RESULT_PATH": "result.json", "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                    "CUDA_VISIBLE_DEVICES": "" if isinstance(grant, ResourceIntent) else ",".join(grant.device_uuids), "HYPOLOOM_REQUIRED_INPUTS": json.dumps(required_inputs, sort_keys=True, separators=(",", ":")),
                    "HYPOLOOM_READ_ROOTS": json.dumps(execution["read_roots"], separators=(",", ":"))}
                environment.update(execution_read_root_projection(execution, execution_plan,
                    generic=getattr(candidate, "measurement", None) is not None or getattr(candidate, "evaluation_scope", None) == "GOAL")[1])
                if execution["target"] is not None: environment["HYPOLOOM_TARGET_ROOT"] = execution["target"]["checkout"]
                environment.update(execution_plan["environment"])
                fact = ExperimentPreregistered(fact_id=_fact_id(request.run_id, "experiment", component_index,
                    *(() if experiment_ordinal == 1 else (experiment_ordinal,))), run_id=RunId(request.run_id),
                    experiment_id=ExperimentId(ids["experiment"]), hypothesis_id=HypothesisId(selected_id),
                    execution_id=ExecutionId(ids["execution"]), component_id=ComponentId(request.component_id),
                    method_family_id=MethodFamilyId(candidate.method_family_id), method=candidate.method,
                    result_kind=component.result_kind, metric=component.metric, trial_plan=contract.trial_plan_for(request.component_id),
                    acceptance_criteria=candidate.acceptance_criteria, evaluator_version=candidate.evaluator_version,
                    required_artifacts=component.required_artifacts, upstream_outcomes=tuple(upstream),
                    resource_preference=candidate.resource_preference,
                    revision_of_outcome_id=(None if creation_identity.get("revision_of_outcome_id") is None
                        else OutcomeId(str(creation_identity["revision_of_outcome_id"]))),
                    execution_contract=ExecutionContract(source_agent_attempt_id=request.attempt_id,
                        source_agent_output_digest=response.output_digest, argv=tuple(execution_plan["argv"]),
                        environment=tuple(sorted(environment.items())),
                        data_splits=(str(raw_component.get("result", {}).get("dataset") or request.component_id),),
                        sandbox_profile=_grant_profile(grant),
                        timeout_seconds=timeout, resource_preference=candidate.resource_preference,
                        **_resource_contract_fields(grant)))
                transition_command = Command.PREREGISTER; kind = "EXPERIMENT"; next_state = ExperimentState.PREREGISTERED
            try:
                mutation = self.kernel.construct_fact(fact, tx)
            except ResearchValidationError as exc:
                if request.command is AgentCommand.PROPOSE_HYPOTHESIS:
                    raise AgentACIError("invalid_candidate", str(exc)) from exc
                raise
            tx.append_fact(mutation)
            self._crash("inside_agent_after_fact_before_transition")
            payload = {"agent_attempt_id": request.attempt_id, "agent_output_digest": response.output_digest,
                "role": request.role, "component_id": request.component_id,
                "method_family_id": str(fact.method_family_id), "hypothesis_id": str(fact.hypothesis_id)}
            if response.expert_signal is not None:
                peers = tuple(role for role in contract.role_ids if role != request.role); hint = response.expert_signal.target_role_hint
                target = hint if hint in peers else (peers[0] if hint is None and peers else None)
                payload.update({"rendezvous_id": f"{request.attempt_id}--rendezvous", "slot_ordinal": contract.role_ids.index(request.role) + 1,
                    "expert_signal": response.expert_signal.to_dict(), "target_role": target})
            transition = self._transition_record(tx, request.run_id, request.owner_workflow_id, kind, transition_command,
                next_state, fact_id=str(fact.fact_id),
                next_command=Command.PREPARE.value if request.command in {AgentCommand.DRAFT_EXPERIMENT_SPEC, AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT} else None,
                payload=payload)
            tx.append_transition(transition)
        self._crash("after_agent_terminal_commit")
    def _execution_task(self, run_id: str, component_id: str, role: str, cycle: int) -> ResearchNodeTask:
        creations = [json.loads(row["payload_json"]) for row in self.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))]
        component = [item for item in creations if item.get("component_id") == component_id]
        current = [item for item in component if int(item.get("expert_cycle", 1)) == cycle]
        ordinal = min((int(item["experiment_ordinal"]) for item in current), default=len(component) + 1)
        return ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id,
            expert_slot=role, cycle=cycle, node_kind=ResearchNodeKind.EXPERIMENT_EXECUTION,
            experiment_ordinal=ordinal))

    def _execution_controller_open(self, run_id: str, component_id: str, *, chain: str | None = None) -> bool:
        return bool(self.store.inspect("SELECT 1 FROM execution_attempts WHERE run_id=? AND kind='AGENT' AND state='OPEN' AND json_extract(observation_json,'$.request.component_id')=? AND json_extract(observation_json,'$.request.command')=?" + (" AND json_extract(observation_json,'$.request.task_context.key.chain')=?" if chain is not None else "") + " LIMIT 1", (run_id, component_id, AgentCommand.EXPERIMENT_EXECUTION.value, *((chain,) if chain is not None else ()))))

    def _resume_tool_execution_agents(self, run_id: str) -> None:
        rows = self.store.inspect("SELECT observation_json FROM execution_attempts WHERE run_id=? AND kind='AGENT' AND json_extract(observation_json,'$.request.command')=? ORDER BY started_at", (run_id, AgentCommand.EXPERIMENT_EXECUTION.value))
        requests = {request.command_id: request for row in rows
            if isinstance(request := AgentRequest.from_dict(json.loads(row["observation_json"])["request"]), AgentRequest)}
        for request in requests.values():
            task = request.task_context; assert isinstance(task, ResearchNodeTask)
            attempts = self._expert_attempts(task)
            if any(row["state"] == "OPEN" for row in attempts) or self._expert_success(task) is not None: continue
            if self._expert_task_exhausted(run_id, task):
                self._record_expert_final_failure(run_id, str(request.component_id), request.role, task, "execution_node_exhausted")
                continue
            self._expert_agent_step(run_id, run_id, task, request.role, str(request.component_id), request.method_family_id)

    def _drive_tool_execution(self, run_id: str, decision: PolicyDecision) -> None:
        component_id, role = str(decision.payload["component_id"]), str(decision.payload["role"])
        if self._participant_paused(run_id, role, component_id, "CREATE_EXPERIMENT", decision.payload.get("chain") or "1"): return
        chain = self._expert_hypothesis_chain(run_id, component_id, role)
        if not chain: raise OperationalHalt("execution lacks its accepted hypothesis")
        task = self._execution_task(run_id, component_id, role, len(chain))
        design = ResearchNodeTask(key=ResearchNodeKey(**{**task.key.to_dict(),
            "node_kind": ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
            "subkind": task.key.subkind}))
        selected = task if self._expert_success(design) is not None else design
        if self._expert_success(task) is not None: return
        if self._expert_task_exhausted(run_id, selected):
            self._record_expert_final_failure(run_id, component_id, role, selected, "execution_node_exhausted")
            return
        self._expert_agent_step(run_id, run_id, selected, role, component_id,
            str(chain[-1]["payload"]["method_family_id"]))

    def _agent_tool_request(self, attempt_id: str, *, grounding: bool = False, resources: bool = False) -> AgentRequest:
        attempt = self.store.get_attempt(attempt_id)
        if (attempt is None or attempt["kind"] != "AGENT" or attempt["state"] != "OPEN"
                or attempt["host_generation"] != self.generation):
            raise AgentACIError("tool_caller_unavailable", "Host tools require the current OPEN Agent Attempt")
        request = AgentRequest.from_dict(json.loads(attempt["observation_json"])["request"])
        if resources:
            if (request.schema_version < 18 or request.command not in {AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION}
                    or not isinstance(request.task_context, ResearchNodeTask) or request.task_context.key.node_kind.value != request.command.value):
                raise AgentACIError("tool_caller_scope", "resources belong to this design or execution node")
            return request
        if grounding:
            if (not request_literature_tools(request) and (request.command is not AgentCommand.GROUNDING or not supports_grounding_web(request.schema_version)
                    or not isinstance(request.task_context, AuxiliaryTask) or request.task_context.kind is not AuxiliaryRequestKind.GROUNDING
                    or request.owner_workflow_id != request.run_id)):
                raise AgentACIError("tool_caller_scope", "web tools belong to the current role-neutral Grounding invocation")
            return request
        if (not self._tool_execution_enabled(request.run_id)
                or request.command is not AgentCommand.EXPERIMENT_EXECUTION
                or not isinstance(request.task_context, ResearchNodeTask)
                or request.task_context.key.node_kind is not ResearchNodeKind.EXPERIMENT_EXECUTION):
            raise AgentACIError("tool_caller_scope", "experiment tools belong to an execution node")
        return request

    def _grounding_web_tool(self, request: AgentRequest, tool: str, arguments: Mapping[str, Any], *, defer: bool = False) -> Any:
        from .paper_sources import web_tool
        return web_tool(self, request, tool, arguments, defer=defer)

    def _grounding_session_web_results(self, request: AgentRequest, session: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        return archived_web_results(self.publisher, request, session)

    def _grounding_completed_web_metadata(self, request: AgentRequest) -> dict[str, Any]:
        from .grounding_background import delivered_web_metadata
        records = self.web_tool_results.pop(request.attempt_id, None)
        if records is None: return {}
        if getattr(self, "claude_agent_host", None) is not None:
            session = self.agent_session_metadata.get(request.attempt_id, {})
            records = delivered_web_metadata(records, self._grounding_session_web_results(request, session))
        return {"web_tool_results": records}

    def _grounding_retrieved_urls(self, request: AgentRequest) -> set[str]:
        real = getattr(self, "claude_agent_host", None) is not None
        session = getattr(self, "agent_session_metadata", {}).get(request.attempt_id)
        records = list(self._grounding_session_web_results(request, session or {})) if real or session is not None else list(self.web_tool_results.get(request.attempt_id, ()))
        return retrieved_session_urls(self.publisher, request, records,
            self.store.list_attempts(workflow_id=request.owner_workflow_id, command_id=request.command_id), real=real)

    def _tool_command(self, command_id: str) -> Mapping[str, Any] | None:
        rows = self.store.inspect("SELECT * FROM workflow_commands WHERE command_id=?", (command_id,))
        return rows[0] if rows else None

    def _owned_tool_experiments(self, request: AgentRequest) -> tuple[Mapping[str, Any], ...]:
        task = request.task_context
        assert isinstance(task, ResearchNodeTask)
        rows = self.store.inspect("SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1 ORDER BY rowid", (request.run_id,))
        return tuple({"exp_id": row["workflow_id"], **identity} for row in rows
            if (identity := json.loads(row["payload_json"])).get("role") == request.role
            and identity.get("component_id") == request.component_id
            and int(identity.get("expert_cycle", 1)) == task.key.cycle
            and (not self._batch_enabled(request.run_id) or identity.get("execution_node_command_id") == task.command_id))

    def _experiment_tool_view(self, run_id: str, experiment_id: str, *, include_log: bool = True) -> dict[str, Any]:
        head = self.store.latest_transition(experiment_id)
        if head is None: raise AgentACIError("unknown_experiment", "experiment is absent")
        outcome = next(
            (row for row in self.store.list_facts(run_id, "CanonicalOutcomeAccepted") if row["experiment_id"] == experiment_id), None)
        terminal = head["state"] == ExperimentState.TERMINAL.value
        status = "completed" if terminal else "running" if head["state"] in {ExperimentState.EXECUTING.value, ExperimentState.EVALUATING.value} else "queued"
        attempts = [item for item in self.store.list_attempts(workflow_id=experiment_id) if item["kind"] == "PROCESS"]
        end = (max(float(item["ended_at"]) for item in attempts)
               if attempts and all(item["state"] == "CLOSED" for item in attempts) else time.time())
        elapsed = max(0.0, end - min((float(item["started_at"]) for item in attempts), default=end))
        result: dict[str, Any] = {"exp_id": experiment_id, "status": status, "state": head["state"],
            "terminal": terminal, "elapsed": elapsed, "outcome": None if outcome is None else outcome["payload"],
            **({"tail_log": self._experiment_tool_log(experiment_id)} if include_log else {})}
        wait = self.resource_waits.get(experiment_id)
        if not terminal and wait is not None and wait[0] == run_id:
            result["resource_wait"] = wait[1]
        if not terminal and run_id in self.dispatch_errors:
            result.update(status="blocked", dispatch_error=dict(self.dispatch_errors[run_id]))
        if attempts:
            latest = attempts[-1]; observation = json.loads(latest["observation_json"] or "{}")
            if latest["state"] == "OPEN":
                from .process_observation import process_activity
                result["activity"] = process_activity(self, latest)
            result["process"] = {"attempt_id": latest["attempt_id"], "state": latest["state"],
                "started_at": latest["started_at"], "ended_at": latest["ended_at"],
                "terminal_reason": latest.get("terminal_reason"), "returncode": observation.get("returncode"),
                "synthetic_before_spawn": observation.get("synthetic_before_spawn", False)}
            if observation.get("resource_failure") == "capacity_requires_revision":
                result.update(repairable=True, resource_failure="capacity_requires_revision",
                    capacity=observation.get("capacity"),
                    repair_instruction="Reduce actual memory demand and submit a replacement; this is a technical failure, not scientific refutation.")
        if outcome is not None:
            result["evaluation"] = json.loads(head["payload_json"]).get("outcome")
        rejection = self._preflight_rejection(run_id, experiment_id)
        if rejection is not None:
            result.update(preflight={"status": "rejected", "reply": rejection.to_dict()}, repairable=True)
        spec = self.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        if self.shared_gpu is not None and include_log: result["resource_planning"] = self.shared_gpu.experiment_view(run_id, experiment_id)
        if spec is not None and outcome is not None and self.publisher is not None:
            artifacts = self.store.inspect("SELECT path FROM execution_artifacts WHERE artifact_id=?", (outcome["artifact_id"],))
            if len(artifacts) == 1:
                root = self.publisher.root / artifacts[0]["path"]
                payload = spec["payload"]; expected = {"result.json"}
                binding = payload.get("goal_evaluation")
                if binding is not None and binding["scope"] == "GOAL":
                    expected.add("goal/result.json")
                    expected.update("raw/" + path for path in binding["acceptance"]["raw_artifacts"])
                if payload.get("measurement") is not None:
                    expected.update("raw/" + path for path in payload["measurement"]["raw_artifacts"])
                result["result_files"] = {"artifact_id": outcome["artifact_id"],
                    "available": sorted(path for path in expected if (root / path).is_file()),
                    "missing": sorted(path for path in expected if not (root / path).is_file())}
        if spec is not None and spec["payload"].get("measurement") is not None:
            result["measurement"] = {"hypothesis_id": spec["hypothesis_id"], "parser_change_requires_new_hypothesis": True,
                "repair_guidance": "The first preregistration froze measurement fields and parser bytes. Repair raw execution with the same parser, or propose a revised hypothesis/new cycle to change parsing."}
        return result

    def _preflight_rejection(self, run_id: str, experiment_id: str) -> Any:
        head = self.store.latest_transition(experiment_id)
        if head is None or head["state"] != ExperimentState.TERMINAL.value: return None
        payload = json.loads(head["payload_json"])
        if payload.get("reason") != "preflight_declined" or not payload.get("discarded_before_preregistration"): return None
        creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (experiment_id,))
        if len(creation) != 1 or payload.get("source_submit_command_id") != json.loads(creation[0]["payload_json"]).get("source_submit_command_id"): return None
        origin, reply = self._review_status(run_id, str(payload["component_id"]), "preflight", experiment_id)
        if origin is None or payload.get("rendezvous_id") != origin.rendezvous_id or reply is None or reply.disposition is not RendezvousDisposition.DECLINED: return None
        if self.store.find_fact(run_id, "ExperimentPreregistered", experiment_id) is not None: return None
        return reply

    def _experiment_tool_log(self, experiment_id: str, max_bytes: int = 16384) -> str:
        attempts = [row for row in self.store.list_attempts(workflow_id=experiment_id) if row["kind"] == "PROCESS"]
        if not attempts: return ""
        attempt = max(attempts, key=lambda row: (float(row["started_at"]), str(row["attempt_id"])))
        parts: list[str] = []
        for name in ("stdout.log", "stderr.log"):
            path = self.state_root / "artifacts" / "work" / str(attempt["attempt_id"]) / "logs" / name
            if not path.exists(): path = self.state_root / "artifacts" / "result-staging" / str(attempt["attempt_id"]) / "logs" / name
            if path.is_symlink() or not path.is_file(): continue
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - max_bytes // 2))
                parts.append(stream.read(max_bytes // 2).decode("utf-8", errors="replace"))
        return "\n".join(parts)[-max_bytes:]

    def handle_agent_tool(self, attempt_id: str, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Admit one scoped MCP operation on the Host writer thread; never wait."""
        if tool == "read_papers": return self.paper_reading.handle(self._agent_tool_request(attempt_id, grounding=True), arguments)
        if tool == "get_resources":
            request = self._agent_tool_request(attempt_id, resources=True)
            allowed = {"resource_estimate", "gpus"} if request.schema_version >= 27 else set()
            if not isinstance(arguments, Mapping) or set(arguments) - allowed:
                raise AgentACIError("tool_arguments", "get_resources accepts only resource_estimate and gpus in ACI 27+")
            if "gpus" in arguments and (type(arguments["gpus"]) is not int or arguments["gpus"] < 1):
                raise AgentACIError("tool_arguments", "gpus must be a positive integer")
            if self.shared_gpu is not None:
                try:
                    result = self.shared_gpu.resources(request, arguments)
                    self.resource_recovery.record(request, tool, result.get("reason") if result.get("status") == "unavailable" else result.get("status"))
                    return result
                except PoolError as exc:
                    self.resource_recovery.record(request, tool, exc.code)
                    raise AgentACIError(exc.code, str(exc)) from exc
                except ValueError as exc: raise AgentACIError("resource_estimate_invalid", str(exc)) from exc
            return {"mode": "nonparticipating", "observed_at": time.time(), "reason": "shared_pool_not_configured", "devices": [], "measurements": []}
        if tool in {"web_search", "web_fetch"}:
            return self._grounding_web_tool(self._agent_tool_request(attempt_id, grounding=True), tool, arguments,
                defer=getattr(self, "claude_agent_host", None) is not None)
        request = self._agent_tool_request(attempt_id)
        if not isinstance(arguments, Mapping): raise AgentACIError("tool_arguments", "tool arguments must be an object")
        if tool == "submit_experiment":
            if self.resource_recovery.for_request(request):
                raise AgentACIError("resource_infrastructure_blocked", "GPU infrastructure is blocked. End this attempt; the operator must recheck resources. Do not downgrade to CPU.")
            return self._submit_experiment(request, arguments)
        owned = {str(item["exp_id"]): item for item in self._owned_tool_experiments(request)}
        if tool == "list_my_experiments":
            if arguments: raise AgentACIError("tool_arguments", "list_my_experiments takes no arguments")
            return {"resource_blocked": self.resource_recovery.for_request(request), "experiments": [{**self._experiment_tool_view(request.run_id, experiment_id),
                **({"can_handoff": self._execution_can_handoff(request, experiment_id)} if request.schema_version >= 22 else {})}
                for experiment_id in owned]}
        if tool not in {"wait_experiment", "get_experiment_log", "cancel_experiment"}:
            raise AgentACIError("unknown_tool", "unknown Host experiment tool")
        allowed = {"exp_id", "max_wait_seconds"} if tool == "wait_experiment" else {"exp_id", "max_bytes"} if tool == "get_experiment_log" else {"exp_id"}
        if set(arguments) - allowed or not isinstance(arguments.get("exp_id"), str):
            raise AgentACIError("tool_arguments", "tool arguments do not match its schema")
        experiment_id = str(arguments["exp_id"])
        if experiment_id not in owned: raise AgentACIError("tool_chain_mismatch", "experiment does not belong to the caller's chain")
        if tool == "wait_experiment":
            wait = arguments.get("max_wait_seconds", 600)
            if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait) or not 0 <= wait <= 600:
                raise AgentACIError("tool_arguments", "max_wait_seconds must be between 0 and 600")
        if tool == "get_experiment_log":
            limit = arguments.get("max_bytes", 16384)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 65536:
                raise AgentACIError("tool_arguments", "max_bytes must be between 1 and 65536")
            return {"exp_id": experiment_id, "tail_log": self._experiment_tool_log(experiment_id, limit)}
        if tool == "cancel_experiment": self._cancel_tool_experiment(request, experiment_id)
        return self._experiment_tool_view(request.run_id, experiment_id)

    def _execution_can_handoff(self, request: AgentRequest, experiment_id: str) -> bool:
        from .agent_aci import execution_handoff
        return request.schema_version >= 22 and not any(
            row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
            and execution_handoff(json.loads(row["observation_json"])) == experiment_id
            for row in self._expert_attempts(request.task_context))

    def _submit_experiment(self, request: AgentRequest, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from .agent_aci import validate_experiment_submission
        task = request.task_context
        assert isinstance(task, ResearchNodeTask)
        predecessor = arguments.get("replaces_experiment_id")
        if predecessor is not None and (not isinstance(predecessor, str) or not predecessor):
            raise AgentACIError("tool_arguments", "replaces_experiment_id must be a nonempty experiment ID")
        owned = {str(item["exp_id"]): item for item in self._owned_tool_experiments(request)}
        if predecessor is not None and predecessor not in owned:
            raise AgentACIError("tool_chain_mismatch", "repair predecessor does not belong to this chain")
        command_id = f"{task.command_id}--submit--{predecessor or 'initial'}"
        normalized_arguments = json.loads(_canonical(dict(arguments)))
        previous = self._tool_command(command_id)
        if previous is not None:
            source = json.loads(previous["request_json"])
            if source.get("arguments") != normalized_arguments:
                raise IdentityConflict("accepted submit key already has a different immutable candidate")
            return dict(json.loads(previous["result_json"]))
        if request.run_id in self.dispatch_errors:
            raise AgentACIError("dispatch_paused", "Host dispatch is quarantined; a new experiment cannot be scheduled")
        head = self.store.latest_transition(request.run_id)
        if head is None: raise OperationalHalt("execution node lacks its run")
        if self._participant_paused(request.run_id, request.role, str(request.component_id), "CREATE_EXPERIMENT", getattr(task.key, "chain", None) or "1"):
            raise AgentACIError("participant_paused", "new experiment submission is paused; admitted experiments continue")
        snapshot = self._policy_snapshot(head, chain_context=task.key if self._batch_enabled(request.run_id) else None)
        try: decision = self.policy.admit_experiment_submission(snapshot, str(request.component_id), predecessor)
        except ValueError as exc: raise AgentACIError("experiment_not_eligible", str(exc)) from exc
        decision = PolicyDecision(command=decision.command, owner_id=decision.owner_id,
            expected_revision=decision.expected_revision, payload={**decision.payload, "expert_cycle": task.key.cycle,
                **({"chain": task.key.chain, "chain_iteration": task.key.experiment_ordinal,
                    **self._scientific_iteration_source(request)} if self._batch_enabled(request.run_id) else {})},
            reason=decision.reason, policy_version=decision.policy_version)
        contract = self._research_contract(request.run_id)
        repairs = sum(item.get("execution_node_command_id") == task.command_id for item in owned.values())
        repair_cap = batch_limits(contract.document, request.role)["repairs"]
        if predecessor is not None and repairs > repair_cap:
            raise AgentACIError("repair_budget_exhausted", "execution node exhausted its repair attempts")
        try: resolved_arguments = normalized_arguments if self.shared_gpu is None else self.shared_gpu.resolve_arguments(request, normalized_arguments)
        except ValueError as exc: raise AgentACIError("resource_estimate_invalid", str(exc)) from exc
        if self.shared_gpu is None and "resource_estimate" in normalized_arguments:
            raise AgentACIError("resource_estimate_unavailable", "learned estimates require an explicitly configured shared pool")
        candidate, files, source_snapshot = validate_experiment_submission(request, resolved_arguments)
        self._validate_submission_candidate(request, candidate, files, source_snapshot)
        submission = {"tool": "submit_experiment", "execution_node_command_id": task.command_id,
            "predecessor_experiment_id": predecessor, "arguments": normalized_arguments,
            "source_request": request.to_dict(), "source_snapshot": dict(source_snapshot)}
        self._crash("before_submit_commit")
        experiment_id = self._create_experiment(request.run_id, decision, submission=(command_id, submission))
        if experiment_id is None: raise AgentACIError("experiment_not_eligible", "the experiment budget is unavailable")
        self._crash("after_submit_commit_before_reply")
        accepted = self._tool_command(command_id)
        assert accepted is not None
        return dict(json.loads(accepted["result_json"]))

    def _scientific_iteration_source(self, request: AgentRequest) -> dict[str, str]:
        key = request.task_context.key
        if key.experiment_ordinal == 1: return {}
        if key.experiment_ordinal > batch_limits(self._research_contract(request.run_id).document, request.role)["iterations"]:
            raise AgentACIError("iteration_budget_exhausted", "scientific iteration budget is exhausted")
        inputs = [item for item in request.input_artifacts if item.ref.endswith("--iteration-context")]
        if len(inputs) != 1: raise AgentACIError("invalid_candidate", "scientific iteration lacks its frozen analysis source")
        source = json.loads(inputs[0].content_bytes())
        prior_task = ResearchNodeTask(key=replace(key, node_kind=ResearchNodeKind.RESULT_ANALYSIS, experiment_ordinal=key.experiment_ordinal - 1))
        attempt = self._expert_success(prior_task)
        outcome = next((row for row in self.store.list_facts(request.run_id, "CanonicalOutcomeAccepted") if row["experiment_id"] == source.get("iteration_of_experiment_id")), None)
        if (attempt is None or attempt["attempt_id"] != source.get("analysis_attempt_id") or outcome is None
                or outcome["payload"]["outcome"] not in {"SUCCESS", "CLEAN_REFUTATION"}
                or json.loads(attempt["observation_json"])["response"]["typed_candidate"].get("iteration_disposition") != "ITERATE"):
            raise AgentACIError("invalid_candidate", "scientific iteration does not follow its exact accepted comparable analysis")
        return {"iteration_of_experiment_id": source["iteration_of_experiment_id"], "analysis_attempt_id": source["analysis_attempt_id"]}

    def _validate_submission_candidate(self, request: AgentRequest, candidate: Any,
                                       files: Mapping[str, bytes], source_snapshot: Mapping[str, Any]) -> None:
        contract = self._research_contract(request.run_id); component = contract.component(request.component_id)
        if component is None: raise AgentACIError("candidate_component", "unknown component")
        if component.deferred_measurement:
            task = request.task_context; assert isinstance(task, ResearchNodeTask)
            measure = self._hypothesis_measurement(request.run_id, request.component_id, request.role, task.key.cycle, chain=task.key.chain)
            if candidate.measurement != measure:
                raise AgentACIError("measurement_frozen", "submission differs from its hypothesis scientific declaration")
            hypothesis = (self._batch_history(request.run_id, request.component_id, request.role).hypothesis(task.key.cycle, task.key.chain)
                if self._batch_enabled(request.run_id) else self._expert_hypothesis_chain(request.run_id, request.component_id, request.role)[task.key.cycle - 1])
            original = next((row for row in self.store.list_facts(request.run_id, "ExperimentPreregistered")
                             if row["hypothesis_id"] == hypothesis["hypothesis_id"]), None)
            if original is not None and measure is not None:
                source_id = original["payload"]["execution_contract"].get("source_submit_command_id")
                _, _, _, frozen = self._submission_source(source_id)
                try: assert_frozen_measurement(ScientificMeasurement.from_dict(original["payload"]["measurement"]),
                    measure, frozen.workspace_files, files)
                except ValueError as exc: raise AgentACIError("measurement_frozen", str(exc)) from exc
        else:
            measure = None
            if candidate.measurement is not None: raise AgentACIError("candidate_metrics", "static component cannot replace its result protocol")
        checks = ((candidate.component_id == request.component_id, "candidate_component"),
            (candidate.owner_role == component.owner_role == request.role, "candidate_owner_role"),
            (candidate.method_family_id == request.method_family_id, "candidate_method_family"),
            (candidate.metrics == ((measure.metric,) if measure is not None else () if component.metric is None else (component.metric,)), "candidate_metrics"),
            (candidate.evaluator_version == OUTCOME_ADAPTER_VERSION, "candidate_evaluator"),
            (tuple(candidate.artifact_requirements) == ("result.json",), "candidate_artifact_requirements"),
            (measure is not None or candidate.evaluation_scope == "GOAL" or set(files) == {"experiment.py", "implementation.patch"}, "candidate_workspace_files"))
        failure = next((code for accepted, code in checks if not accepted), None)
        if failure: raise AgentACIError(failure, "submission differs from its frozen component contract")
        try:
            for path, content in files.items():
                if path.endswith(".py"): compile(content, path, "exec")
        except (SyntaxError, ValueError) as exc: raise AgentACIError("invalid_candidate", f"candidate compile failed: {exc}") from exc
        if (measure is None and candidate.evaluation_scope != "GOAL" and not component_is_production(contract.document, str(request.component_id))
                and b"HYPOLOOM_RESULT_PATH" not in files["experiment.py"] and b"result.json" not in files["experiment.py"]):
            raise AgentACIError("invalid_candidate", "experiment.py must write its declared result.json")
        if self.publisher is None: raise OperationalHalt("Host is not initialized")
        workspace = ExecutionWorkspace(self.publisher.root, state_root=self.state_root)
        try: assert_candidate_patch_compiles(workspace, contract.document, str(request.component_id), agent_request=request.to_dict(), agent_response=source_snapshot)
        except (ExecutionWorkspaceError, OSError, ValueError) as exc: raise AgentACIError("invalid_candidate", str(exc)) from exc
        self._validate_required_input_family(request, candidate, retryable=True)
        _, resource, _, _, _ = self._submission_execution_plan(request, candidate, source_snapshot["arguments"])
        if isinstance(resource, ResourceIntent) and resource.sharing is not None:
            try: assessment = self.shared_gpu.assess_intent(resource)
            except PoolError as exc:
                if exc.code != "unavailable" or request.schema_version >= 28:
                    self.resource_recovery.record(request, "submit_experiment", exc.code)
                    raise AgentACIError(exc.code, str(exc)) from exc
            else:
                if assessment["reason"] == "capacity_requires_revision":
                    raise AgentACIError("capacity_requires_revision", "Revise actual GPU memory demand; " + json.dumps(assessment, sort_keys=True))

    def _hypothesis_measurement(self, run_id: str, component_id: str, role: str, cycle: int, *, chain: str | None = None) -> ScientificMeasurement | None:
        hypotheses = ([self._batch_history(run_id, component_id, role).hypothesis(cycle, chain)] if self._batch_enabled(run_id)
            else self._expert_hypothesis_chain(run_id, component_id, role)[cycle - 1:cycle])
        if hypotheses and hypotheses[0]["payload"].get("measurement") is None and self.shared_acceptance.enabled(run_id): return None
        if not hypotheses or not isinstance(hypotheses[0]["payload"].get("measurement"), Mapping):
            raise AgentACIError("invalid_measurement", "hypothesis lacks an accepted scientific measurement")
        return ScientificMeasurement.from_dict(hypotheses[0]["payload"]["measurement"])

    def _submission_source(self, command_id: str) -> tuple[Mapping[str, Any], AgentRequest, Mapping[str, Any], Any]:
        from .agent_aci import validate_submission_snapshot
        command = self._tool_command(command_id)
        if command is None or command["command_type"] != Command.CREATE_EXPERIMENT.value or command["response_status"] != "accepted":
            raise OperationalHalt("execution source lacks its accepted submit command")
        data = json.loads(command["request_json"])
        request = AgentRequest.from_dict(data["source_request"])
        if (data.get("tool") != "submit_experiment" or data.get("execution_node_command_id") != request.command_id
                or command["run_id"] != request.run_id):
            raise OperationalHalt("submit command ownership differs from its invocation")
        source = data["source_snapshot"]
        validated = validate_submission_snapshot(request, source)
        return command, request, source, validated

    def _submission_execution_plan(self, request: AgentRequest, candidate: Any,
                                    arguments: Mapping[str, Any]) -> tuple[Any, ...]:
        import shlex
        research = self._research_contract(request.run_id); component = research.component(request.component_id)
        assert component is not None
        capability_input = next((item for item in request.input_artifacts if item.ref.endswith("--compute-capabilities")), None)
        if capability_input is None: raise AgentACIError("compute_capabilities_missing", "submission lacks frozen compute capabilities")
        capabilities = ComputeCapabilities.from_bytes(capability_input.content_bytes())
        cpu_only = component.result_kind is ComponentResultKind.ARTIFACT and component_execution_plan(research.document, request.component_id).get("production") is False
        preference = candidate.resource_preference
        if arguments.get("gpus", 0) > 0: preference = ResourcePreference.GPU_REQUIRED
        elif arguments.get("gpus") == 0: preference = ResourcePreference.CPU
        if cpu_only and preference is ResourcePreference.GPU_REQUIRED:
            raise AgentACIError("resource_unsatisfied", "non-production artifact work is CPU-only")
        budget = research.document["budget"]
        safety = research.document.get("methodology", {}).get("controls", {}).get("product", {}).get("safety", {})
        try:
            minimum = int(budget.get("min_vram_per_experiment", 0))
            if self.shared_gpu is not None: minimum = self.shared_gpu.workload_budget(request, arguments, minimum)
            grant = ResourceGrant(kind=ResourceKind.CPU) if cpu_only else (self.compute_resources.plan_intent if self.shared_gpu else self.compute_resources.select)(candidate.resource_preference, capabilities,
                gpu_count=arguments.get("gpus"), max_gpus_per_experiment=safety.get("max_gpus_per_experiment", 1),
                min_vram_per_experiment=minimum, gpu_vram_reserve_mb=gpu_memory_reserve_mib(budget, minimum))
        except (ResourceUnavailable, ValueError) as exc: raise AgentACIError("resource_unsatisfied", str(exc)) from exc
        if isinstance(grant, ResourceIntent): grant = self.shared_gpu.plan_intent(request, arguments, grant)
        patch = self._implementation_patch(request.run_id, str(request.component_id), str(candidate.method_family_id))
        plan = _component_plan(research.document, str(request.component_id), str(candidate.method_family_id), patch, grant.kind is ResourceKind.GPU)
        supplied = arguments.get("command", candidate.execution_argv or plan["argv"])
        argv = tuple(shlex.split(supplied) if isinstance(supplied, str) else supplied)
        if any(value == "/workspace" or value.startswith("/workspace/") for value in argv):
            raise AgentACIError("invalid_command", "formal execution cannot reference authoring-only /workspace; captured source is available under /input")
        if not argv or (candidate.measurement is None and candidate.evaluation_scope != "GOAL" and argv[0] != "python"):
            raise AgentACIError("invalid_command", "the execution contract requires a Python argv")
        if component_is_production(research.document, str(request.component_id)) and argv != tuple(plan["argv"]):
            raise AgentACIError("invalid_command", "production execution must use its declared trial driver")
        timeout = min(float(budget.get("experiment_timeout", _CONFIG["process_timeout"])),
            float(arguments.get("timeout_min", float("inf"))) * 60)
        return capabilities, grant, plan, argv, timeout

    def _preregister_submission(self, run_id: str, experiment_id: str, identity: Mapping[str, Any]) -> None:
        command_id = str(identity["source_submit_command_id"])
        _source_command, request, source, validated = self._submission_source(command_id)
        candidate = validated.candidate; contract = self._research_contract(run_id)
        component = contract.component(str(identity["component_id"]))
        assert component is not None
        raw_component = next(item for item in contract.to_dict()["components"] if item["id"] == request.component_id)
        arguments = source["arguments"]
        _, grant, plan, argv, timeout = self._submission_execution_plan(request, candidate, arguments)
        ordinal = int(identity["experiment_ordinal"])
        ids, component_index, _ = _component_ids(run_id, contract, str(request.component_id), experiment_ordinal=ordinal)
        execution = self._run_config(run_id)["execution"]
        with self.store.transaction() as tx:
            upstream = []; required_inputs: dict[str, list[str]] = {}
            for input_index, requirement in enumerate(component.required_inputs, 1):
                matches = [row for row in tx.list_facts(run_id, "CanonicalOutcomeAccepted")
                    if row["payload"].get("component_id") == str(requirement.produced_by_component)
                    and row["payload"].get("method_family_id") == candidate.method_family_id
                    and row["payload"].get("outcome") in {ScientificOutcome.SUCCESS.value, ScientificOutcome.CLEAN_REFUTATION.value}]
                if not matches: raise AgentACIError("upstream_outcome", "required producer has no accepted comparable outcome")
                def creation_ordinal(row: Mapping[str, Any]) -> int:
                    creation = tx.execute("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (row["experiment_id"],)).fetchone()
                    if creation is None: raise OperationalHalt("producer lacks experiment creation")
                    return int(json.loads(creation[0])["experiment_ordinal"])
                if self._batch_enabled(run_id):
                    selected = self._batch_history(run_id, str(requirement.produced_by_component)).selected_tip
                    producer = next((row for row in matches if selected is not None and str(row["outcome_id"]) == selected["outcome_id"]), None)
                    if producer is None: raise AgentACIError("upstream_outcome", "required producer lacks its exact adjudicated outcome")
                else: producer = max(matches, key=creation_ordinal)
                logical = next((item for item in producer["payload"].get("logical_artifacts", ()) if item.get("logical_artifact") == requirement.logical_artifact), None)
                paths = () if not isinstance(logical, Mapping) else tuple(logical.get("paths", ()))
                if len(paths) < requirement.min_count: raise AgentACIError("upstream_artifact", "required producer artifact is absent")
                required_inputs[requirement.resource] = [f"/hypoloom-inputs/{input_index}/{index}" for index in range(1, len(paths) + 1)]
                upstream.append(ProducerOutcomeBinding(resource=requirement.resource,
                    produced_by_component=requirement.produced_by_component, logical_artifact=requirement.logical_artifact,
                    min_count=requirement.min_count, outcome_id=OutcomeId(str(producer["outcome_id"])), artifact_id=ArtifactId(str(producer["artifact_id"]))))
            environment = {"HYPOLOOM_DELAY": str(execution["delay_seconds"]),
                "HYPOLOOM_RESULT_PATH": "result.json", "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                "CUDA_VISIBLE_DEVICES": "" if isinstance(grant, ResourceIntent) else ",".join(grant.device_uuids), "HYPOLOOM_REQUIRED_INPUTS": json.dumps(required_inputs, sort_keys=True, separators=(",", ":")),
                "HYPOLOOM_READ_ROOTS": json.dumps(execution["read_roots"], separators=(",", ":"))}
            environment.update(execution_read_root_projection(execution, plan,
                generic=candidate.measurement is not None or candidate.evaluation_scope == "GOAL")[1])
            if execution["target"] is not None: environment["HYPOLOOM_TARGET_ROOT"] = execution["target"]["checkout"]
            environment.update(plan["environment"])
            if candidate.measurement is not None or candidate.evaluation_scope == "GOAL": environment["PATH"] = "/runtime/bin:/usr/bin:/bin"
            fact = ExperimentPreregistered(fact_id=_fact_id(run_id, "experiment", component_index, *((ordinal,) if ordinal > 1 else ())),
                run_id=RunId(run_id), experiment_id=ExperimentId(experiment_id), hypothesis_id=HypothesisId(str(identity["hypothesis_id"])),
                execution_id=ExecutionId(ids["execution"]), component_id=ComponentId(str(request.component_id)),
                method_family_id=MethodFamilyId(candidate.method_family_id), method=candidate.method,
                result_kind=ComponentResultKind.ARTIFACT if candidate.evaluation_scope == "GOAL" and candidate.measurement is None else component.result_kind,
                metric=candidate.measurement.metric if candidate.measurement else component.metric,
                measurement=candidate.measurement, trial_plan=contract.trial_plan_for(str(request.component_id)),
                goal_evaluation=self.shared_acceptance.binding(run_id, candidate.evaluation_scope),
                acceptance_criteria=candidate.acceptance_criteria, evaluator_version=candidate.evaluator_version,
                required_artifacts=component.required_artifacts, upstream_outcomes=tuple(upstream), resource_preference=candidate.resource_preference,
                revision_of_outcome_id=None if identity.get("revision_of_outcome_id") is None else OutcomeId(str(identity["revision_of_outcome_id"])),
                execution_contract=ExecutionContract(source_agent_attempt_id=request.attempt_id,
                    source_agent_output_digest=validated.output_digest, source_submit_command_id=command_id,
                    argv=argv, environment=tuple(sorted(environment.items())),
                    data_splits=(str(raw_component.get("result", {}).get("dataset") or request.component_id),),
                    sandbox_profile=_grant_profile(grant),
                    timeout_seconds=timeout, resource_preference=candidate.resource_preference, **_resource_contract_fields(grant)))
            tx.append_fact(self.kernel.construct_fact(fact, tx))
            tx.append_transition(self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.PREREGISTER,
                ExperimentState.PREREGISTERED, fact_id=str(fact.fact_id), next_command=Command.PREPARE.value,
                payload={"source_submit_command_id": command_id, "agent_attempt_id": request.attempt_id,
                    "agent_output_digest": validated.output_digest, "role": request.role,
                    "component_id": request.component_id, "method_family_id": candidate.method_family_id,
                    "hypothesis_id": identity["hypothesis_id"]}))

    def _cancel_tool_experiment(self, request: AgentRequest, experiment_id: str) -> None:
        head = self.store.latest_transition(experiment_id)
        assert head is not None
        if head["state"] == ExperimentState.TERMINAL.value: return
        command_id = f"{experiment_id}--agent-cancel"
        if self._tool_command(command_id) is not None: return
        state = ExperimentState(head["state"])
        command = Command.REQUEST_CANCEL if state is ExperimentState.EXECUTING else Command.CANCEL
        next_state = state if state is ExperimentState.EXECUTING else ExperimentState.TERMINAL if state is ExperimentState.DRAFTED else ExperimentState.EVALUATING
        with self.store.transaction() as tx:
            tx.record_command({"command_id": command_id, "run_id": request.run_id, "workflow_id": experiment_id,
                "expected_revision": head["revision"], "command_type": command.value,
                "request": {"tool": "cancel_experiment", "source_agent_attempt_id": request.attempt_id, "exp_id": experiment_id},
                "status": "accepted", "result": {"cancel_requested": True}, "revision": int(head["revision"]) + 1})
            tx.append_transition(self._transition_record(tx, request.run_id, experiment_id, "EXPERIMENT", command,
                next_state, command_id=command_id, payload={"cancel_requested": True, "source_agent_attempt_id": request.attempt_id,
                    **({"discarded_before_preregistration": True} if state is ExperimentState.DRAFTED else {})}))
        for attempt_id, handle in tuple(self.running.items()):
            attempt = self.store.get_attempt(attempt_id)
            if attempt is not None and attempt["workflow_id"] == experiment_id: self.runner.cancel(handle)
        if state in {ExperimentState.PREREGISTERED, ExperimentState.READY}:
            self._synthetic_terminal(request.run_id, experiment_id, AttemptTerminalReason.CANCELLED)

    def _poll_local_execution_agents(self) -> bool:
        progressed = False
        for attempt_id, request in tuple(self.local_execution_agents.items()):
            head = self.store.latest_transition(request.run_id)
            if head is None or head["state"] == RunState.STOPPING.value:
                self._agent_failure(request, AttemptTerminalReason.CANCELLED, "run_stopping", retryable=False, owner_transition=False)
                self.local_execution_agents.pop(attempt_id); progressed = True; continue
            if request.run_id in self.dispatch_errors: self._agent_failure(request, AttemptTerminalReason.FAILED, "dispatch_paused", retryable=False, owner_transition=False); self.local_execution_agents.pop(attempt_id); progressed = True; continue
            if hasattr(self.agent, "poll_execution"):
                response = self.agent.poll_execution(request, self.handle_agent_tool)
                if response is None: continue
            else:
                owned = self._owned_tool_experiments(request)
                if not owned:
                    if self._participant_paused(request.run_id, request.role, str(request.component_id), "CREATE_EXPERIMENT", getattr(request.task_context.key, "chain", None) or "1"): continue
                    task = request.task_context; assert isinstance(task, ResearchNodeTask)
                    design = ResearchNodeTask(key=ResearchNodeKey(**{**task.key.to_dict(), "node_kind": ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, "subkind": task.key.subkind}))
                    source = self._expert_success(design)
                    if source is None: raise OperationalHalt("fake execution adapter lacks accepted design")
                    packet = json.loads(source["observation_json"])["response"]
                    self.handle_agent_tool(attempt_id, "submit_experiment", {"candidate": packet["typed_candidate"], "workspace_files": packet["proposed_workspace_artifacts"]})
                    progressed = True; continue
                view = self.handle_agent_tool(attempt_id, "wait_experiment", {"exp_id": owned[-1]["exp_id"], "max_wait_seconds": 0})
                if not view["terminal"] and request.schema_version < 22: continue
                response = self.agent.invoke(request)
                if not view["terminal"]:
                    from .agent_aci import with_output_digest
                    response = with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                        "waiting_for_experiment_id": owned[-1]["exp_id"]}))
            self._finish_fake_agent(request, response)
            self.local_execution_agents.pop(attempt_id); progressed = True
        return progressed

    def _create_experiment(self, run_id: str, decision: PolicyDecision, *,
                           submission: tuple[str, Mapping[str, Any]] | None = None) -> str | None:
        component_id = str(decision.payload["component_id"]); contract = self._research_contract(run_id)
        if self._participant_paused(run_id, str(decision.payload["role"]), component_id, "CREATE_EXPERIMENT", decision.payload.get("chain") or "1"):
            if submission is not None: raise AgentACIError("participant_paused", "new experiment submission is paused; admitted experiments continue")
            return None
        hypothesis_id = str(decision.payload["hypothesis_id"]); hypothesis = self.store.find_fact(run_id, "HypothesisProposed", hypothesis_id)
        if hypothesis is None: raise OperationalHalt(f"selected hypothesis {hypothesis_id!r} is absent")
        with self.store.transaction() as tx:
            role = str(decision.payload["role"]); cap = profile_role_cap(contract.document, role, "max_experiments")
            creations = [json.loads(row[0]) for row in tx.execute("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))]
            used = sum(item.get("role") == role for item in creations)
            if cap is not None and used >= cap: raise OperationalHalt(f"profile experiment budget for role {role!r} is exhausted")
            component_creations = [item for item in creations if item.get("component_id") == component_id]
            component_creations.sort(key=lambda item: int(item.get("experiment_ordinal", 1)))
            ordinal = len(component_creations) + 1; ids, _, _ = _component_ids(run_id, contract, component_id, experiment_ordinal=ordinal)
            revision_of = decision.payload.get("revision_of_outcome_id")
            preflight_predecessor = decision.payload.get("preflight_repair_of_experiment_id")
            batch_mode = self._batch_enabled(run_id)
            if batch_mode:
                key = AgentRequest.from_dict(submission[1]["source_request"]).task_context.key
                selected = self._batch_history(run_id, component_id, role).hypothesis(key.cycle, key.chain)
                if selected["hypothesis_id"] != hypothesis_id or key.experiment_ordinal != decision.payload["chain_iteration"]:
                    raise OperationalHalt("submission differs from its exact accepted chain hypothesis")
                component_creations = [item for item in component_creations if item.get("chain") == key.chain
                    and item.get("expert_cycle") == key.cycle and item.get("chain_iteration") == key.experiment_ordinal]
            if preflight_predecessor is not None:
                prior = self.store.latest_transition(str(preflight_predecessor))
                if (submission is None or submission[1]["predecessor_experiment_id"] != preflight_predecessor
                        or prior is None or self._preflight_rejection(run_id, str(preflight_predecessor)) is None
                        or component_creations[-1].get("source_submit_command_id") != json.loads(prior["payload_json"]).get("source_submit_command_id")
                        or component_creations[-1].get("hypothesis_id") != hypothesis_id):
                    raise OperationalHalt("preflight repair lacks its exact declined control predecessor")
            expert_cycle = decision.payload.get("expert_cycle")
            if batch_mode:
                if bool(component_creations) != (revision_of is not None or preflight_predecessor is not None):
                    raise OperationalHalt("chain iteration submission must name its exact repair predecessor")
            elif _uses_expert_loop(contract):
                if expert_cycle is None:
                    expert_cycle = (1 if ordinal == 1 else
                        component_creations[-1].get("expert_cycle", 1))
                scientific_successor = ordinal > 1 and revision_of is None and preflight_predecessor is None
                parent_id = hypothesis["payload"].get("parent_hypothesis_id")
                if scientific_successor:
                    update = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id,
                        component_id=component_id, expert_slot=role,
                        cycle=int(expert_cycle) - 1,
                        node_kind=ResearchNodeKind.HYPOTHESIS_UPDATE))
                    accepted = self._expert_success(update)
                    response = {} if accepted is None else json.loads(accepted["observation_json"]).get("response", {})
                    candidate = response.get("typed_candidate", {}) if isinstance(response, Mapping) else {}
                    if (not isinstance(expert_cycle, int) or expert_cycle < 2
                            or parent_id != component_creations[-1].get("hypothesis_id")
                            or candidate.get("update_disposition") != HypothesisUpdateDisposition.REVISE_NEXT_CYCLE.value):
                        raise OperationalHalt("scientific successor lacks its accepted update lineage")
                elif preflight_predecessor is None and (ordinal == 1) != (revision_of is None):
                    raise OperationalHalt("experiment revision identity is inconsistent")
            elif (ordinal == 1) != (revision_of is None): raise OperationalHalt("experiment revision identity is inconsistent")
            if revision_of is not None and submission is None:
                run_record = tx.get_run(run_id)
                charges = self._agent_charges.read(tx, run_id)
                if run_record is None or role_agent_budget_state(contract.document, role, charges, self.agent_budget, wallclock_is_expired=wallclock_expired(contract.document, run_record["created_at"])) != "available": return
            experiment_id = ids["experiment"]
            submit_id = None if submission is None else submission[0]
            if submission is not None:
                tx.record_command({"command_id": submit_id, "run_id": run_id, "workflow_id": experiment_id,
                    "expected_revision": 0, "command_type": Command.CREATE_EXPERIMENT.value,
                    "request": submission[1], "status": "accepted", "revision": 1,
                    "result": {"exp_id": experiment_id, "queue_position": sum(row["state"] != ExperimentState.TERMINAL.value for row in self.store.list_workflow_heads(run_id, "EXPERIMENT"))}})
            transition = self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.CREATE_EXPERIMENT, ExperimentState.DRAFTED,
                command_id=submit_id, next_command=Command.PREREGISTER.value if submission is not None else Command.REQUEST_SPEC_CANDIDATE.value,
                payload={"hypothesis_id": hypothesis_id, "role": decision.payload["role"],
                    "component_id": component_id, "method_family_id": hypothesis["payload"]["method_family_id"],
                    "experiment_ordinal": ordinal, "revision_of_outcome_id": revision_of,
                    **({"preflight_repair_of_experiment_id": preflight_predecessor} if preflight_predecessor is not None else {}),
                    **({"expert_cycle": expert_cycle} if _uses_expert_loop(contract) else {}),
                    **({"chain": decision.payload["chain"], "chain_iteration": decision.payload["chain_iteration"]} if batch_mode else {}),
                    **({key: decision.payload[key] for key in ("iteration_of_experiment_id", "analysis_attempt_id")} if batch_mode and decision.payload["chain_iteration"] > 1 else {}),
                    **({"source_submit_command_id": submit_id,
                        "execution_node_command_id": submission[1]["execution_node_command_id"],
                        "source_agent_attempt_id": submission[1]["source_request"]["attempt_id"]} if submission is not None else {})})
            tx.append_transition(transition)
            if submission is not None and (preflight_role := self._review_role(contract, component_id, "preflight")) is not None:
                source_request = AgentRequest.from_dict(submission[1]["source_request"])
                hypotheses = {str(row["fact_id"]) for row in tx.list_facts(run_id, "HypothesisProposed") if row["payload"].get("component_id") == component_id}
                origin, record = preflight_origin(source_request, contract, None, hypotheses, preflight_role, source_submit_command_id=str(submit_id), experiment_id=experiment_id,
                    source_submission=validate_submission_snapshot(source_request, submission[1]["source_snapshot"]))
                tx.append_transition(self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.REQUEST_RENDEZVOUS, ExperimentState.DRAFTED,
                    payload={"role": source_request.role, "component_id": component_id, "source_submit_command_id": submit_id,
                        "rendezvous_id": origin.rendezvous_id, "rendezvous_origin": record, "rendezvous_request": origin.request(run_id).to_dict()}))
        return experiment_id
    def _drive_experiment(self, run_id: str, head: Mapping[str, Any], command: str | None) -> None:
        state = head["state"]
        creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (head["workflow_id"],))
        if not creation: raise OperationalHalt(f"experiment {head['workflow_id']!r} lacks creation payload")
        identity = json.loads(creation[0]["payload_json"])
        if command == Command.REQUEST_SPEC_CANDIDATE.value:
            if _uses_expert_loop(self._research_contract(run_id)):
                task = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id,
                    component_id=str(identity["component_id"]), expert_slot=str(identity["role"]),
                    cycle=int(identity.get("expert_cycle", 1)),
                    node_kind=ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
                    experiment_ordinal=int(identity.get("experiment_ordinal", 1))))
                self._expert_agent_step(run_id, str(head["workflow_id"]), task,
                    str(identity["role"]), str(identity["component_id"]), str(identity["method_family_id"]))
            else:
                self._agent_step(run_id, head["workflow_id"], AgentCommand.DRAFT_EXPERIMENT_SPEC,
                    str(identity["role"]), str(identity["component_id"]), str(identity["method_family_id"]))
        elif command == Command.PREREGISTER.value:
            if identity.get("source_submit_command_id"):
                if self._review_role(self._research_contract(run_id), str(identity["component_id"]), "preflight") is not None:
                    origin, reply = self._review_status(run_id, str(identity["component_id"]), "preflight", str(head["workflow_id"]))
                    if origin is None or reply is None or reply.disposition is not RendezvousDisposition.ANSWERED:
                        raise OperationalHalt("submitted PREREGISTER requires its answered preflight")
                self._preregister_submission(run_id, str(head["workflow_id"]), identity); return
            preflight = self._review_role(self._research_contract(run_id), str(identity["component_id"]), "preflight"); origin, reply = self._review_status(run_id, str(identity["component_id"]), "preflight", str(head["workflow_id"]))
            if preflight is None: source = validated_draft_source(self.store, str(head["workflow_id"]))
            elif origin is None or reply is None or reply.disposition is not RendezvousDisposition.ANSWERED:
                raise OperationalHalt("PREREGISTER requires an answered component preflight")
            else: source = self.store.get_attempt(origin.source_attempt_id)
            if source is None or source["kind"] != "AGENT" or source["terminal_reason"] != AttemptTerminalReason.SUCCEEDED.value:
                raise OperationalHalt("reviewed spec Agent attempt is unavailable")
            observation = json.loads(source["observation_json"])
            self._accept_agent_response(AgentRequest.from_dict(observation["request"]),
                AgentResponse.from_dict(observation["response"]), review_complete=True)
        elif command == Command.PREPARE.value:
            spec = self.store.find_fact(run_id, "ExperimentPreregistered", head["workflow_id"])
            if spec is None: raise OperationalHalt(f"experiment {head['workflow_id']} lacks preregistration")
            with self.store.transaction() as tx:
                transition = self._transition_record(tx, run_id, head["workflow_id"], "EXPERIMENT", Command.PREPARE,
                    ExperimentState.READY, next_command=Command.EXECUTE.value, payload={"spec_fact_id": spec["fact_id"]})
                tx.append_transition(transition)
        elif command == Command.EXECUTE.value: self._start_process(run_id, head["workflow_id"], retry=False)
        elif command == Command.ACCEPT_OUTCOME.value:
            if head["due_at"] is not None and float(head["due_at"]) > time.time(): return
            self._evaluate_experiment(run_id, head["workflow_id"])
        elif command == Command.CANCEL.value and state == ExperimentState.DRAFTED.value:
            rejection: dict[str, Any] = {}
            if identity.get("source_submit_command_id"):
                origin, reply = self._review_status(run_id, str(identity["component_id"]), "preflight", str(head["workflow_id"]))
                if origin is not None and reply is not None and reply.disposition is RendezvousDisposition.DECLINED:
                    rejection = {"reason": "preflight_declined", "source_submit_command_id": identity["source_submit_command_id"],
                        "rendezvous_id": origin.rendezvous_id, "component_id": identity["component_id"]}
            with self.store.transaction() as tx:
                transition = self._transition_record(tx, run_id, head["workflow_id"], "EXPERIMENT",
                    Command.CANCEL, ExperimentState.TERMINAL, payload={"discarded_before_preregistration": True, **rejection})
                tx.append_transition(transition)
        elif command is None and state == ExperimentState.EXECUTING.value:
            spec = self.store.find_fact(run_id, "ExperimentPreregistered", head["workflow_id"])
            if spec is None: raise OperationalHalt(f"executing experiment {head['workflow_id']} lacks preregistration")
            attempts = self.store.list_attempts(execution_id=spec["execution_id"])
            if not any(row["state"] == "OPEN" for row in attempts):
                if self._tool_command(f"{head['workflow_id']}--agent-cancel") is not None:
                    self._synthetic_terminal(run_id, str(head["workflow_id"]), AttemptTerminalReason.CANCELLED)
                elif sum(not json.loads(row["observation_json"] or "{}").get("resource_wait_before_exec", False) for row in attempts) >= self._effect_attempt_cap(self._research_contract(run_id), "process"):
                    self._synthetic_terminal(run_id, head["workflow_id"], AttemptTerminalReason.FAILED)
                else: self._start_process(run_id, head["workflow_id"], retry=True)
        else: raise OperationalHalt(f"Research Policy returned unsupported experiment command {command!r}")
    def _start_registered_process(self, request: ProcessRequest, *, pool_key: Mapping[str, str] | None = None) -> RunningProcess:
        def register(process_group_id: int) -> None:
            self._crash("before_process_registration")
            with self.store.transaction() as tx: tx.bind_attempt_process(request.attempt_id, process_group_id)
            self._crash("after_process_registration_before_exec")
            if pool_key is not None:
                self.shared_gpu.activate(pool_key, process_group_id)
                self._crash("after_pool_activation_before_exec")
        return self.runner.start(request, before_exec=register)
    def _start_process(self, run_id: str, experiment_id: str, *, retry: bool) -> None:
        pending = self._tool_command(f"{experiment_id}--resource-revision")
        if pending is not None:
            self._finish_resource_revision(run_id, experiment_id, json.loads(pending["request_json"]))
            return
        creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (experiment_id,))
        if not creation: raise OperationalHalt(f"experiment {experiment_id!r} lacks creation payload")
        identity = json.loads(creation[0]["payload_json"]); role = str(identity["role"])
        if self._at_capacity(run_id, "PROCESS") or self._participant_busy(run_id, role, execution_node_command_id=identity.get("execution_node_command_id"),
                component_id=identity.get("component_id"), chain=identity.get("chain")): return
        run_record = self.store.get_run(run_id); research = self._research_contract(run_id)
        if run_record is None: raise OperationalHalt(f"run {run_id!r} is absent")
        if wallclock_expired(research.document, run_record["created_at"]): self._synthetic_terminal(run_id, experiment_id, AttemptTerminalReason.TIMED_OUT); return
        spec = self.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        if spec is None: raise OperationalHalt(f"experiment {experiment_id} lacks preregistration")
        execution_id = spec["execution_id"]; attempts = self.store.list_attempts(execution_id=execution_id)
        ordinal = len(attempts) + 1; attempt_id = f"{experiment_id}--process-{ordinal}"
        intent = spec["payload"]["execution_contract"].get("resource_intent"); pool_key = None; selected = None
        if intent is not None:
            if self.shared_gpu is None: raise OperationalHalt("GPU resource intent requires its configured pool")
            if not self.shared_gpu.reconcile(): return
            pool_key = self.shared_gpu.key(run_id, execution_id, attempt_id)
            try: selected = self.shared_gpu.reserve(ResourceIntent.from_dict(intent), pool_key)
            except PoolError as exc:
                if exc.code != "capacity_requires_revision": raise
                try: assessment = self.shared_gpu.assess_intent(ResourceIntent.from_dict(intent))
                except PoolError as unavailable:
                    if unavailable.code == "unavailable":
                        self.resource_waits[experiment_id] = (run_id, "unavailable")
                        return
                    raise
                if assessment["reason"] != "capacity_requires_revision": return
                diagnostic = {"resource_failure": "capacity_requires_revision", "capacity": assessment}
                with self.store.transaction() as tx:
                    tx.record_command({"command_id": f"{experiment_id}--resource-revision", "run_id": run_id,
                        "workflow_id": experiment_id, "command_type": Command.CANCEL.value,
                        "request": diagnostic, "status": "accepted", "result": {"repair_required": True},
                        "revision": tx.latest_transition(experiment_id)["revision"]})
                self._crash("after_resource_revision_intent")
                self._finish_resource_revision(run_id, experiment_id, diagnostic)
                return
            if selected is None: return
        spec, contract, agent_request, agent_response, source_path, result_path, workspace, capabilities, grant = self._execution_inputs(run_id, experiment_id, actual_grant=selected)
        if pool_key is None and set(grant.device_uuids).intersection(self._busy_gpu_uuids(experiment_id)): self.resource_waits[experiment_id] = (run_id, "frozen GPU is busy in this state root"); return
        bundle_current = self._runtime_version() == self.version; budget = self._research_contract(run_id).document["budget"]
        reserve = (contract["resource_intent"]["reserve_memory_mib"] if pool_key is not None
                   else gpu_memory_reserve_mib(budget, int(budget.get("min_vram_per_experiment", 0))))
        if bundle_current:
            try:
                if pool_key is not None: self.compute_resources.validate_identity(grant, capabilities)
                else: self.compute_resources.validate_live(grant, capabilities, busy_gpu_uuids=self._busy_gpu_uuids(experiment_id), min_vram_per_experiment=int(budget.get("min_vram_per_experiment", 0)), gpu_vram_reserve_mb=reserve)
            except ResourceUnavailable as exc: self.resource_waits[experiment_id] = (run_id, str(exc)[:512]); return
        self.resource_waits.pop(experiment_id, None)
        self._crash("before_process_intent_commit")
        mounts, target_binding, patch = self._execution_mounts(run_id, spec, workspace, attempt_id)
        plan = _component_plan(self._research_contract(run_id).document, str(spec["payload"]["component_id"]), str(spec["payload"]["method_family_id"]), self._implementation_patch(run_id, str(spec["payload"]["component_id"]), str(spec["payload"]["method_family_id"])), grant.kind is ResourceKind.GPU)
        prepared = workspace.plan(agent_request=agent_request, agent_response=agent_response, attempt_id=attempt_id, attempt_ordinal=ordinal,
            execution_id=execution_id, target_env=dict(contract["environment"]), timeout_seconds=contract["timeout_seconds"], source_relative_path=source_path,
            result_relative_path=result_path, execution_argv=tuple(contract["argv"]),
            trusted_workspace_files=_trusted_execution_files(plan), read_only_bindings=mounts,
            allow_generic_argv=spec["payload"].get("measurement") is not None or spec["payload"].get("goal_evaluation") is not None)
        parser = None
        if spec["payload"].get("measurement") is not None or spec["payload"].get("goal_evaluation") is not None:
            prepared, parser = self._measurement_execution(spec, prepared, workspace, agent_response)
        with self.store.transaction() as tx:
            if len(tx.list_attempts(execution_id=execution_id)) + 1 != ordinal: raise OperationalHalt("process attempt ordinal changed")
            if retry:
                owner = attempts[0]["command_id"]; transition = self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.OPEN_RETRY_ATTEMPT, ExperimentState.EXECUTING)
            else:
                transition = self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.EXECUTE, ExperimentState.EXECUTING); owner = transition["command_id"]
            tx.open_attempt({"attempt_id": attempt_id, "run_id": run_id, "workflow_id": experiment_id, "command_id": owner, "kind": "PROCESS", "experiment_id": experiment_id,
                "execution_id": execution_id, "attempt_ordinal": ordinal,
                "host_generation": self.generation, "started_at": time.time()})
            transition["payload"] = {"attempt_id": attempt_id, "process_request": prepared.process_request.to_dict(), "spec_fact_id": spec["fact_id"],
                "agent_attempt_id": prepared.agent_attempt_id, "agent_output_digest": prepared.agent_output_digest, "source_relative_path": source_path, "result_relative_path": result_path,
                "role": role, "component_id": identity["component_id"], **_grant_identity(grant)}
            if identity.get("source_submit_command_id"):
                transition["payload"]["source_submit_command_id"] = identity["source_submit_command_id"]
            if pool_key is not None: transition["payload"].update(resource_grant=grant.to_dict(), gpu_pool_key=pool_key)
            if retry:
                transition["payload"].update({"retry_of_attempt_id": str(attempts[-1]["attempt_id"]), "retry_reason": "process_lost"})
            tx.append_transition(transition)
        self._crash("after_process_intent_before_spawn"); trackers = ()
        try:
            if not bundle_current: raise ResourceUnavailable("pinned Agent/compute configuration or runtime drifted")
            workspace.materialize(prepared, agent_request=agent_request, agent_response=agent_response)
            if parser is not None:
                from .measurement_execution import materialize_parser
                if spec["payload"].get("goal_evaluation") is not None:
                    from .shared_acceptance_execution import materialize_evaluation
                    materialize_evaluation(parser)
                else: materialize_parser(parser)
            if target_binding is not None:
                target_root = workspace.materialize_target_overlay(target_binding, attempt_id=attempt_id, patch=patch)
                control_root = (workspace.materialize_target_overlay(target_binding, attempt_id=attempt_id, patch=None, target_directory="control-target") if plan.get("control_target") is True else None)
                workspace.assert_component_preflight(plan=plan, target_root=target_root, control_root=control_root)
            workspace.assert_prestart_clean(prepared, agent_request=agent_request, agent_response=agent_response)
            if pool_key is not None: self.compute_resources.validate_identity(grant, capabilities)
            else: self.compute_resources.validate_live(grant, capabilities, busy_gpu_uuids=self._busy_gpu_uuids(experiment_id), min_vram_per_experiment=int(budget.get("min_vram_per_experiment", 0)), gpu_vram_reserve_mb=reserve)
            trackers = tuple(GpuPressureTracker(initial_observation=self.compute_resources.observe_runtime(uuid), reserve_memory_mib=reserve, grace_seconds=self.gpu_pressure_grace_seconds) for uuid in grant.device_uuids)
            if self._runtime_version() != self.version: raise ResourceUnavailable("pinned Agent/compute configuration or runtime drifted")
            handle = self._start_registered_process(prepared.process_request, pool_key=pool_key)
        except PoolError as exc:
            with self.store.transaction() as tx:
                tx.close_attempt(attempt_id, terminal_reason=AttemptTerminalReason.LOST, ended_at=time.time(), payload={
                    "attempt_id": attempt_id, "attempt_ordinal": ordinal, "execution_id": execution_id,
                    "terminal_reason": "LOST", "process_request": prepared.process_request.to_dict(),
                    "absence_proven": True, "resource_wait_before_exec": True, "reason": str(exc)[:512]})
            self._remove_attempt_residue(self.store.get_attempt(attempt_id))
            self.resource_waits[experiment_id] = (run_id, str(exc)[:512]); return
        except (ExecutionWorkspaceError, ResourceUnavailable, OSError, ValueError) as exc: self._close_prestart_failure(run_id, experiment_id, prepared, exc); return
        self.running[attempt_id] = handle
        if trackers: self.gpu_pressure[attempt_id] = (trackers, reserve, {})
        self._crash("after_spawn_before_observation")
    def _execution_inputs(self, run_id: str, experiment_id: str, *, actual_grant: ResourceGrant | None = None,
                          attempt_id: str | None = None, synthetic: bool = False) -> tuple[Any, ...]:
        spec = self.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        if spec is None: raise OperationalHalt(f"experiment {experiment_id} lacks preregistration")
        payload = spec["payload"]; contract = payload.get("execution_contract"); research = self._research_contract(run_id)
        if not isinstance(contract, Mapping): raise OperationalHalt("preregistration lacks a typed execution contract")
        attempt = self.store.get_attempt(str(contract.get("source_agent_attempt_id")))
        submit_id = contract.get("source_submit_command_id")
        if submit_id is not None:
            command, submitted_request, response, validated = self._submission_source(str(submit_id))
            if (attempt is None or attempt["kind"] != "AGENT" or command["workflow_id"] != experiment_id
                    or submitted_request.attempt_id != attempt["attempt_id"]
                    or contract.get("source_agent_output_digest") != validated.output_digest):
                raise OperationalHalt("execution source differs from its admitted submit command")
            request = submitted_request.to_dict(); candidate = response["arguments"]["candidate"]
        else:
            if (attempt is None or attempt["kind"] != "AGENT" or attempt["state"] != "CLOSED"
                    or attempt["terminal_reason"] != AttemptTerminalReason.SUCCEEDED.value):
                raise OperationalHalt("execution source is not a successful committed Agent attempt")
            observation = json.loads(attempt["observation_json"])
            request, response = observation.get("request"), observation.get("response")
            candidate = response.get("typed_candidate") if isinstance(response, Mapping) else None
        argv = contract.get("argv")
        requirements = candidate.get("artifact_requirements") if isinstance(candidate, Mapping) else None
        try:
            typed_request = AgentRequest.from_dict(request); preference = ResourcePreference(contract.get("resource_preference"))
            resource = (ResourceIntent.from_dict(contract["resource_intent"]) if "resource_intent" in contract
                else ResourceGrant.from_dict(contract.get("resource_grant")))
            item = next(value for value in typed_request.input_artifacts if value.ref.endswith("--compute-capabilities"))
            capabilities = ComputeCapabilities.from_bytes(item.content_bytes())
            if isinstance(resource, ResourceIntent):
                if self.shared_gpu is None or self._run_config(run_id).get("gpu_pool") != self.shared_gpu.configuration:
                    raise ValueError("GPU intent lacks its run-bound pool")
                if not set(resource.allowed_gpu_uuids) <= {item.uuid for item in capabilities.gpus}:
                    raise ValueError("GPU intent is absent from captured capabilities")
                if contract.get("resource_grant") is not None or dict(contract["environment"]).get("CUDA_VISIBLE_DEVICES") != "":
                    raise ValueError("GPU intent cannot preselect visible devices")
                if attempt_id is not None:
                    dispatch = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_id=? AND json_extract(payload_json,'$.attempt_id')=? AND json_type(payload_json,'$.process_request')='object'", (run_id, experiment_id, attempt_id))
                    binding = json.loads(dispatch[0]["payload_json"]) if len(dispatch) == 1 else {}
                    if not synthetic or "resource_grant" in binding:
                        if binding.get("gpu_pool_key") != self.shared_gpu.key(run_id, str(spec["execution_id"]), attempt_id):
                            raise ValueError("process lacks its one allocation dispatch and matching owner")
                        actual_grant = ResourceGrant.from_dict(binding.get("resource_grant")); synthetic = False
                grant = ResourceGrant(kind=ResourceKind.CPU) if synthetic else actual_grant
                if not synthetic and (grant is None or grant.kind is not ResourceKind.GPU
                        or len(grant.device_uuids) != resource.gpu_count or not set(grant.device_uuids) <= set(resource.allowed_gpu_uuids)):
                    raise ValueError("actual GPU allocation differs from its intent")
            else: grant = resource
            if grant.kind is ResourceKind.GPU:
                if not set(grant.device_uuids) <= {item.uuid for item in capabilities.gpus}: raise ValueError("GPU grant is absent from captured capabilities")
        except (AgentACIError, KeyError, StopIteration, ResourceUnavailable, TypeError, ValueError) as exc:
            raise OperationalHalt("preregistered compute authority is invalid") from exc
        expected_profile = _grant_profile(resource)
        patch = self._implementation_patch(run_id, str(payload.get("component_id")), str(payload.get("method_family_id")))
        expected_plan = _component_plan(research.document, str(payload.get("component_id")), str(payload.get("method_family_id")), patch, resource.kind is ResourceKind.GPU)
        expected_argv = expected_plan["argv"]
        if submit_id is not None:
            _, expected_grant, _, expected_argv, expected_timeout = self._submission_execution_plan(typed_request, validated.candidate, response["arguments"])
            if expected_grant != resource: raise OperationalHalt("submitted resource intent/grant differs from preregistration")
            if float(contract.get("timeout_seconds", 0)) != expected_timeout:
                raise OperationalHalt("submitted timeout differs from preregistration")
        elif isinstance(resource, ResourceIntent):
            budget = research.document["budget"]
            minimum = self.shared_gpu.workload_budget(typed_request, {}, int(budget.get("min_vram_per_experiment", 0)))
            expected_intent = self.compute_resources.plan_intent(preference, capabilities,
                min_vram_per_experiment=minimum, gpu_vram_reserve_mb=gpu_memory_reserve_mib(budget, minimum))
            expected_intent = self.shared_gpu.plan_intent(typed_request, {}, expected_intent)
            if resource != expected_intent: raise OperationalHalt("resource intent differs from its source and reviewed Profile")
        if (not isinstance(request, Mapping) or not isinstance(response, Mapping)
                or response.get("output_digest") != contract.get("source_agent_output_digest")
                or request.get("attempt_id") != attempt["attempt_id"]
                or payload.get("evaluator_version") != OUTCOME_ADAPTER_VERSION
                or not isinstance(argv, list) or argv != list(expected_argv)
                or requirements != ["result.json"]
                or contract.get("sandbox_profile") != expected_profile
                or payload.get("resource_preference") != preference.value
                or not isinstance(candidate, Mapping) or candidate.get("resource_preference") != preference.value
                or candidate.get("component_id") != payload.get("component_id")
                or candidate.get("method_family_id") != payload.get("method_family_id")):
            raise OperationalHalt("preregistered execution inputs do not match their committed authorities")
        if self.publisher is None: raise OperationalHalt("Workflow Host is not initialized")
        kwargs = self.compute_resources.workspace_kwargs(grant, capabilities)
        if payload.get("measurement") is not None or payload.get("goal_evaluation") is not None:
            kwargs["system_runtime_paths"] = ("/lib", "/lib64", "/usr/lib", "/etc/ld.so.cache", "/usr/bin", "/bin")
        runtime_prefix = self._run_config(run_id)["execution"]["runtime_prefix"]
        if runtime_prefix is not None:
            prefix = Path(runtime_prefix)
            if grant.kind is ResourceKind.GPU and Path(kwargs["runtime_prefix"]).resolve() != prefix:
                raise OperationalHalt("GPU grant runtime differs from the run-bound reference runtime")
            kwargs.update({"runtime_prefix": prefix, "python_executable": prefix / "bin" / "python"})
        workspace = ExecutionWorkspace(self.publisher.root, state_root=self.state_root, **kwargs)
        if isinstance(resource, ResourceIntent):
            environment = dict(contract["environment"]); environment["CUDA_VISIBLE_DEVICES"] = ",".join(grant.device_uuids)
            contract = {**contract, "environment": sorted(environment.items())}
        source_path = str(expected_plan.get("source_relative_path") or "experiment.py")
        if payload.get("measurement") is not None or payload.get("goal_evaluation") is not None:
            supplied = candidate.get("execution_argv", ())
            fallback = payload["measurement"]["parser_files"][0] if payload.get("measurement") is not None else "experiment.py"
            source_path = next((item[len("/input/"):] for item in supplied if item.startswith("/input/")),
                               fallback)
        return spec, contract, request, response, source_path, "result.json", workspace, capabilities, grant

    def _measurement_execution(self, spec: Mapping[str, Any], prepared: PreparedExecution,
                               workspace: ExecutionWorkspace, response: Mapping[str, Any]) -> tuple[Any, Any]:
        if spec["payload"].get("goal_evaluation") is not None:
            return self.shared_acceptance.plan(spec, prepared, workspace, response)
        if spec["payload"].get("measurement") is None: return prepared, None
        from .measurement_driver import sequence_request
        from .measurement_execution import plan_parser
        from .agent_aci import WorkspaceArtifact
        measure = ScientificMeasurement.from_dict(spec["payload"]["measurement"])
        artifacts = response["arguments"]["workspace_files"]
        files = {item.path: item.content_bytes() for raw in artifacts if (item := WorkspaceArtifact.from_dict(raw))}
        raw = replace(prepared.process_request, stdout_path="raw-stdout.log", stderr_path="raw-stderr.log")
        parser = plan_parser(measure, files, raw_dir=prepared.output_dir,
            work_dir=prepared.attempt_root / "measurement-parser", attempt_id=prepared.attempt_id + "--parser",
            execution_id=str(spec["execution_id"]), timeout_seconds=min(30.0, float(raw.timeout_seconds)),
            python_executable=str(workspace.python_executable), runtime_prefix=str(workspace.runtime_prefix),
            raw_log_paths={"stdout.log": prepared.log_dir / "raw-stdout.log", "stderr.log": prepared.log_dir / "raw-stderr.log"})
        outer = sequence_request(raw, parser.process_request, attempt_id=prepared.attempt_id,
            attempt_ordinal=raw.attempt_ordinal, execution_id=str(spec["execution_id"]), cwd=prepared.log_dir,
            timeout_seconds=float(spec["payload"]["execution_contract"]["timeout_seconds"]))
        return replace(prepared, process_request=outer), parser
    def _outcome_publication(self, run_id: str, outcome_id: str, artifact_id: str):
        if self.publisher is None: raise OperationalHalt("Workflow Host is not initialized")
        outcome = self.store.find_fact(run_id, "CanonicalOutcomeAccepted", outcome_id)
        rows = self.store.inspect("SELECT * FROM execution_artifacts WHERE artifact_id=?", (artifact_id,))
        if outcome is None or len(rows) != 1 or outcome.get("artifact_id") != artifact_id: raise OperationalHalt("upstream artifact authority is absent")
        row = rows[0]; key = ArtifactKey(run_id=row["run_id"], experiment_id=row["experiment_id"], execution_id=row["execution_id"], attempt_id=row["attempt_id"], artifact_kind=row["artifact_kind"])
        root = self.publisher.path_for(key); paths = inventory_relative_paths(root)
        publication = ArtifactPublication(key=key, schema_version=int(row["schema_version"]), digest_algorithm=row["digest_algorithm"],
            digest=row["digest"], path=str(root), relative_paths=paths, reused=True)
        if outcome["payload"].get("artifact_set_digest") != publication.digest: raise OperationalHalt("upstream outcome and publication digest differ")
        try: self.publisher.verify(publication)
        except ArtifactError as exc: raise OperationalHalt(f"upstream publication failed verification: {exc}") from exc
        if any(row[name] != outcome[name] for name in ("run_id", "experiment_id", "execution_id")):
            raise OperationalHalt("outcome and publication ownership differ")
        return outcome, root, paths

    def _declared_measurement_sources(self, run_id: str, outcome_id: str, artifact_id: str):
        from .artifacts import normalize_relative_paths
        outcome = self.store.find_fact(run_id, "CanonicalOutcomeAccepted", outcome_id)
        spec = None if outcome is None else self.store.find_fact(run_id, "ExperimentPreregistered", outcome["experiment_id"])
        if spec is None or spec["execution_id"] != outcome["execution_id"]:
            raise OperationalHalt("report measurement lacks its exact preregistration")
        declaration = spec["payload"].get("measurement")
        binding = spec["payload"].get("goal_evaluation")
        if binding is not None and binding["scope"] == "GOAL":
            declaration = binding["acceptance"]
        if declaration is None:
            return ()
        relative = normalize_relative_paths("raw/" + name for name in declaration["raw_artifacts"])
        _, root, _ = self._outcome_publication(run_id, outcome_id, artifact_id)
        return tuple((name, root.joinpath(*name.split("/"))) for name in relative)

    def _logical_sources(self, run_id: str, outcome_id: str, artifact_id: str, logical_id: str) -> tuple[Path, ...]:
        outcome, root, paths = self._outcome_publication(run_id, outcome_id, artifact_id)
        logical = next((item for item in outcome["payload"].get("logical_artifacts", ()) if item.get("logical_artifact") == logical_id), None)
        if not isinstance(logical, Mapping): raise OperationalHalt(f"upstream logical artifact {logical_id!r} is absent")
        selected = tuple(root.joinpath(*str(value).split("/")) for value in logical.get("paths", ()))
        if not selected or any(path.relative_to(root).as_posix() not in paths for path in selected): raise OperationalHalt("upstream logical artifact paths differ from publication")
        return selected
    def _implementation_patch(self, run_id: str, component_id: str, family: str) -> bytes | None:
        contract = self._research_contract(run_id); raw = next(value for value in contract.to_dict()["components"] if value["id"] == component_id)
        if raw["result"].get("target_overlay_policy") != "latest": return None
        ancestors: set[str] = set()
        def visit(identity: str) -> None:
            item = contract.component(identity)
            for dependency in () if item is None else item.dependencies:
                if str(dependency) not in ancestors: ancestors.add(str(dependency)); visit(str(dependency))
        visit(component_id); orders = {str(row["fact_id"]): int(row["raw_order"]) for row in self.store.inspect("SELECT rowid AS raw_order,fact_id FROM research_facts WHERE run_id=?", (run_id,))}; candidates: dict[str, tuple[int, tuple[Path, ...]]] = {}
        for outcome in self.store.list_facts(run_id, "CanonicalOutcomeAccepted"):
            if outcome["payload"].get("component_id") in ancestors and outcome["payload"].get("method_family_id") == family and any(
                    item.get("logical_artifact") == "implementation_patch" for item in outcome["payload"].get("logical_artifacts", ())):
                key = str(outcome["payload"]["component_id"]); candidate = (orders[str(outcome["fact_id"])], self._logical_sources(run_id, outcome["outcome_id"], outcome["artifact_id"], "implementation_patch")); candidates[key] = max(candidates.get(key, candidate), candidate, key=lambda item: item[0])
        if len(candidates) != 1 or len(next(iter(candidates.values()))[1]) != 1: raise TargetPreparationError("latest overlay requires one accepted ancestor implementation patch")
        return next(iter(candidates.values()))[1][0].read_bytes()
    def _submitted_execution_patch(self, request: AgentRequest, candidate: Any,
                                   files: Mapping[str, bytes]) -> bytes | None:
        """Select existing captured source, preserving explicit ancestor contracts."""
        contract = self._research_contract(request.run_id)
        result = next(item["result"] for item in contract.document["components"] if item["id"] == request.component_id)
        run = self.store.get_run(request.run_id)
        version = re.fullmatch(r"hypoloom-research-v([0-9]+)", str(run["research_contract_version"])) if run else None
        own_target = (version is not None and int(version[1]) >= 9 and request.schema_version >= 14
            and result["kind"] == "metric" and result.get("target_overlay_policy") != "latest"
            and self._run_config(request.run_id)["execution"]["target"] is not None)
        if candidate.measurement is not None or candidate.evaluation_scope == "GOAL" or own_target:
            return files["implementation.patch"] or None
        return self._implementation_patch(request.run_id, str(request.component_id), str(candidate.method_family_id))

    def _execution_mounts(self, run_id: str, spec: Mapping[str, Any], workspace: ExecutionWorkspace,
                          attempt_id: str) -> tuple[tuple[tuple[Path, str], ...], GitTargetBinding | None, bytes | None]:
        execution = self._run_config(run_id)["execution"]; payload = spec["payload"]; target = execution["target"]
        binding = None if target is None else GitTargetBinding(checkout=Path(target["checkout"]), commit=target["commit"])
        patch = self._implementation_patch(run_id, str(payload["component_id"]), str(payload["method_family_id"]))
        kind = "GPU" if _contract_gpu(payload["execution_contract"]) else "CPU"
        plan = _component_plan(self._research_contract(run_id).document, str(payload["component_id"]), str(payload["method_family_id"]), patch, kind == ResourceKind.GPU.value)
        if submit_id := payload["execution_contract"].get("source_submit_command_id"):
            _, request, _, validated = self._submission_source(str(submit_id))
            patch = self._submitted_execution_patch(request, validated.candidate, validated.workspace_files)
        paths = workspace.paths(attempt_id); mounts: list[tuple[Path, str]] = []
        if binding is not None:
            mounts.append((paths.attempt_root / "target", str(binding.checkout)))
            if plan.get("control_target") is True: mounts.append((paths.attempt_root / "control-target", "/hypoloom-control-target"))
        data_sources = []
        for item in plan.get("data_bindings", ()):
            if not isinstance(item, Mapping): raise OperationalHalt("component data binding is invalid")
            source = Path(str(item["source"])); data_sources.append(source); mounts.append((source, str(item["sandbox"])))
        read_bindings, _ = execution_read_root_projection(execution, plan,
            generic=payload.get("measurement") is not None or (payload.get("goal_evaluation") or {}).get("scope") == "GOAL")
        for source, destination in read_bindings:
            if binding is not None and source == binding.checkout and destination == str(source) or any(source == item or source in item.parents or item in source.parents for item in data_sources): continue
            mounts.append((source, destination))
        for input_index, item in enumerate(payload.get("upstream_outcomes", ()), 1):
            sources = self._logical_sources(run_id, item["outcome_id"], item["artifact_id"], item["logical_artifact"])
            if len(sources) < int(item["min_count"]): raise OperationalHalt("upstream logical artifact count is insufficient")
            mounts.extend((source, f"/hypoloom-inputs/{input_index}/{index}") for index, source in enumerate(sources, 1))
        contract = self._research_contract(run_id); component = contract.component(str(payload["component_id"]))
        if component is None: raise OperationalHalt("execution component is absent")
        return tuple(mounts), binding, patch
    def _close_prestart_failure(self, run_id: str, experiment_id: str, prepared: PreparedExecution, error: Exception) -> None:
        reason = f"{type(error).__name__}:{error}"[:1024]; request = prepared.process_request
        observation = {"attempt_id": request.attempt_id, "attempt_ordinal": request.attempt_ordinal,
            "execution_id": request.execution_id, "terminal_reason": AttemptTerminalReason.FAILED.value, "process_request": request.to_dict(), "synthetic_before_spawn": True}
        if isinstance(error, ResourceUnavailable): observation["resource_diagnostic"] = bounded_diagnostic_text(reason) or type(error).__name__
        else: observation["integrity_error"] = reason
        with self.store.transaction() as tx:
            tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.FAILED, ended_at=time.time(), payload=observation)
            transition = self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.OBSERVE_ATTEMPT_TERMINAL,
                ExperimentState.EVALUATING, payload={"attempt_id": request.attempt_id}, next_command=Command.ACCEPT_OUTCOME.value)
            tx.append_transition(transition)
    def _poll_processes(self) -> bool:
        for attempt_id, handle in tuple(self.running.items()):
            if (attempt := self.store.get_attempt(attempt_id)) is None: raise OperationalHalt(f"missing attempt {attempt_id}")
            if attempt_id in self.spec_validations:
                expired = time.monotonic() >= handle.started_at + handle.request.timeout_seconds; run_expired = self._run_wallclock_expired(attempt["run_id"])
                if handle.returncode is None and not expired and not run_expired: continue
                if handle.returncode is None: self.runner.cancel(handle)
                observation = self.runner.wait(handle); self.running.pop(attempt_id, None); prepared, workspace = self.spec_validations.pop(attempt_id)
                self._finish_spec_validation(attempt, prepared, workspace, observation); return True
            run_expired = self._run_wallclock_expired(attempt["run_id"]); pressure = self.gpu_pressure.get(attempt_id); pressure_summaries = (); terminal = handle.returncode is not None; pressure_cancelled = False
            if run_expired and not terminal: self.runner.cancel(handle)
            if pressure is not None and not terminal:
                devices = {}
                for tracker in pressure[0]:
                    try:
                        device = self.compute_resources.observe_runtime(tracker.summary.gpu_uuid); devices[device.uuid] = device; tracker.sample(device)
                    except ResourceUnavailable: pass
                if self.shared_gpu is not None: self.shared_gpu.sample_owned(attempt_id, handle, tuple(tracker.summary.gpu_uuid for tracker in pressure[0]), devices)
                pressure_summaries = tuple(tracker.summary for tracker in pressure[0])
                for summary in (summary for summary in pressure_summaries if summary.pressure_exceeded):
                    owned_mib = self.shared_gpu.owned_latest_memory_mib(attempt_id, summary.gpu_uuid) if self.shared_gpu is not None else None
                    pressure[2][summary.gpu_uuid] = (gpu_pressure_attribution(latest_free_memory_mib=summary.gpu_latest_free_memory_mib, owned_latest_memory_mib=owned_mib, reserve_memory_mib=pressure[1]), owned_mib)
                if any(pressure[2][summary.gpu_uuid][0] != "external" for summary in pressure_summaries if summary.pressure_exceeded): pressure_cancelled = self.runner.cancel(handle, terminate_grace_seconds=_PRESSURE_TERMINATE_GRACE_SECONDS)
            expired = time.monotonic() >= handle.started_at + handle.request.timeout_seconds
            if terminal or handle.returncode is not None or expired or run_expired:
                observation = self.runner.wait(handle); run_expired = run_expired or self._run_wallclock_expired(attempt["run_id"]); self.running.pop(attempt_id, None); pressure = self.gpu_pressure.pop(attempt_id, None)
                if not pressure_summaries and pressure is not None: pressure_summaries = tuple(tracker.summary for tracker in pressure[0])
                reason = AttemptTerminalReason.TIMED_OUT if run_expired else AttemptTerminalReason.FAILED if pressure_cancelled else observation.terminal_reason; payload = {**observation.to_dict(), "terminal_reason": reason.value, "process_request": handle.request.to_dict()}
                if pressure_summaries:
                    decisions = pressure[2] if pressure is not None else {}
                    usage = [{**summary.to_dict(), "pressure_exceeded": bool((pressure_cancelled or decisions.get(summary.gpu_uuid, (None,))[0] == "external") and summary.pressure_exceeded)} for summary in pressure_summaries]
                    for item in usage:
                        if item["pressure_exceeded"]: item["pressure_attribution"] = decisions[item["gpu_uuid"]][0]
                    if self.shared_gpu is not None:
                        owned = self.shared_gpu.finish_owned(attempt_id)
                        for item in usage: item["owned_memory"] = owned.get(item["gpu_uuid"], {"sample_count": 0, "unknown_sample_count": 0, "latest": None, "peak": None})
                    payload["resource_usage"] = usage[0] if len(usage) == 1 else usage
                if pressure_cancelled:
                    payload["resource_pressure"] = gpu_pressure_payload(tuple(decisions[summary.gpu_uuid] for summary in pressure_summaries if summary.pressure_exceeded), reserve_memory_mib=pressure[1] if pressure is not None else 0, terminate_grace_seconds=_PRESSURE_TERMINATE_GRACE_SECONDS)
                    if len(pressure_summaries) > 1: payload["resource_pressure"]["gpu_uuids"] = [summary.gpu_uuid for summary in pressure_summaries if summary.pressure_exceeded]
                if self.shared_gpu is not None and pressure_summaries: self.shared_gpu.observe_oom(payload, Path(handle.request.cwd) / handle.request.stderr_path)
                with self.store.transaction() as tx:
                    tx.close_attempt(attempt_id, terminal_reason=reason, ended_at=time.time(), process_group_id=observation.process_group_id, payload=payload)
                    transition = self._transition_record(tx, attempt["run_id"], attempt["workflow_id"],
                        "EXPERIMENT", Command.OBSERVE_ATTEMPT_TERMINAL, ExperimentState.EVALUATING,
                        payload={"attempt_id": attempt_id}, next_command=Command.ACCEPT_OUTCOME.value)
                    tx.append_transition(transition)
                self._crash("after_observation_before_artifact_collection")
                return True
        return False
    def _schedule_evaluator_retry(self, run_id: str, experiment_id: str, attempt_id: str, exc: Exception, *, stage: str) -> None:
        with self.store.transaction() as tx:
            row = tx.execute("SELECT COUNT(*) AS count FROM workflow_transitions t JOIN workflow_commands c ON c.command_id=t.command_id WHERE t.workflow_id=? AND c.command_type=?", (experiment_id, Command.RETRY_EVALUATOR.value)).fetchone(); ordinal = int(row["count"]) + 1; delay = min(float(_CONFIG["evaluator_retry_delay"]) * (2 ** min(ordinal - 1, 30)), 30.0)
            payload = {"source_attempt_id": attempt_id, "evaluator_retry_ordinal": ordinal, "failure_stage": stage, "failure_class": type(exc).__name__, "retry_after_seconds": delay}
            transition = self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.RETRY_EVALUATOR, ExperimentState.EVALUATING, next_command=Command.ACCEPT_OUTCOME.value, payload=payload)
            transition["due_at"] = time.time() + delay; tx.append_transition(transition)
    def _evaluate_experiment(self, run_id: str, experiment_id: str) -> None:
        spec = self.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        if spec is None: raise OperationalHalt(f"experiment {experiment_id} lacks preregistration")
        execution_id = spec["execution_id"]; attempts = self.store.list_attempts(execution_id=execution_id)
        eligible = [row for row in attempts if row["state"] == "CLOSED" and row["terminal_reason"] != "LOST"]
        if not eligible: raise OperationalHalt(f"EVALUATING experiment {experiment_id} has no candidate attempt")
        attempt = eligible[0]; observation = json.loads(attempt["observation_json"])
        spec, contract, agent_request, agent_response, source_path, result_path, workspace, _, grant = self._execution_inputs(
            run_id, experiment_id, attempt_id=attempt["attempt_id"], synthetic=bool(observation.get("synthetic_before_spawn")))
        if self.publisher is None: raise OperationalHalt("Workflow Host is not initialized")
        process_request = observation.get("process_request")
        if not isinstance(process_request, Mapping): raise OperationalHalt("terminal observation lacks its committed ProcessRequest")
        mounts, target_binding, patch = self._execution_mounts(run_id, spec, workspace, attempt["attempt_id"])
        # Submitted target code is source, not a newly declared result artifact.
        implementation_bytes = self._implementation_patch(run_id, str(spec["payload"]["component_id"]), str(spec["payload"]["method_family_id"]))
        submitted_target_patch = patch is not None and implementation_bytes is None
        plan = _component_plan(self._research_contract(run_id).document, str(spec["payload"]["component_id"]), str(spec["payload"]["method_family_id"]), implementation_bytes, _contract_gpu(spec["payload"]["execution_contract"]))
        planning = dict(agent_request=agent_request, agent_response=agent_response,
            target_env=dict(contract["environment"]), timeout_seconds=contract["timeout_seconds"],
            source_relative_path=source_path, result_relative_path=result_path, execution_argv=tuple(contract["argv"]),
            trusted_workspace_files=_trusted_execution_files(plan), read_only_bindings=mounts)
        parser = None
        if spec["payload"].get("measurement") is not None or spec["payload"].get("goal_evaluation") is not None:
            expected = workspace.plan(**planning, attempt_id=str(attempt["attempt_id"]),
                attempt_ordinal=int(attempt["attempt_ordinal"]), execution_id=str(execution_id), allow_generic_argv=True)
            prepared, parser = self._measurement_execution(spec, expected, workspace, agent_response)
            if prepared.process_request != ProcessRequest.from_dict(process_request):
                raise OperationalHalt("measurement ProcessRequest differs from its frozen two-stage plan")
        else: prepared = workspace.recover(process_request, **planning)
        integrity_error = observation.get("integrity_error")
        try:
            if integrity_error is not None or observation.get("synthetic_before_spawn"):
                collected = workspace.integrity_diagnostic(prepared, str(integrity_error or "synthetic_terminal_before_spawn"),
                    agent_request=agent_request, agent_response=agent_response)
            else: collected = workspace.collect(prepared, observation, agent_request=agent_request, agent_response=agent_response,
                preserve_partial_outputs=spec["payload"].get("goal_evaluation") is not None)
        except (ExecutionWorkspaceError, OSError, ValueError) as exc:
            integrity_error = f"{type(exc).__name__}:{exc}"[:1024]
            collected = workspace.integrity_diagnostic(prepared, integrity_error, agent_request=agent_request, agent_response=agent_response)
        if parser is not None and spec["payload"].get("goal_evaluation") is not None:
            from .shared_acceptance_execution import collect_evaluation
            collected = collect_evaluation(parser, collected, observation)
        elif parser is not None:
            from .execution_workspace import _stage_candidate
            files = {"raw/" + path: (collected.staging_dir / path).read_bytes() for path in collected.relative_paths}
            files.update({"parser/" + path: data for path, data in parser.source_files})
            for stream in ("stdout", "stderr"):
                path = prepared.log_dir / f"raw-{stream}.log"
                if path.is_file(): files[f"raw/{stream}.log"] = path.read_bytes()
            parsed_path = parser.output_dir / "result.json"
            if parsed_path.is_file() and not parsed_path.is_symlink() and parsed_path.stat().st_size <= parser.max_output_bytes:
                files["result.json"] = parsed_path.read_bytes()
            else: files["result.json"] = _canonical({"terminal_reason": observation["terminal_reason"], "parser_result_unavailable": True})
            stage = prepared.staging_root / "measurement"
            paths = _stage_candidate(stage, files)
            collected = replace(collected, staging_dir=stage, relative_paths=paths)
        key = ArtifactKey(run_id=run_id, experiment_id=experiment_id, execution_id=execution_id,
            attempt_id=attempt["attempt_id"])
        try:
            publication = self.publisher.publish(key, collected.staging_dir, collected.relative_paths, schema_version=2)
        except ArtifactDurabilityError as exc: self._schedule_evaluator_retry(run_id, experiment_id, str(attempt["attempt_id"]), exc, stage="artifact_publication"); return
        except ArtifactError as exc:
            if self.store.inspect("SELECT 1 FROM execution_artifacts WHERE attempt_id=?", (attempt["attempt_id"],)):
                raise OperationalHalt(f"recorded artifact conflicts during evaluation: {exc}") from exc
            integrity_error = f"{type(exc).__name__}:{exc}"[:1024]
            self._safe_remove(self.publisher.path_for(key))
            collected = workspace.integrity_diagnostic(prepared, integrity_error, agent_request=agent_request, agent_response=agent_response)
            try:
                publication = self.publisher.publish(key, collected.staging_dir, collected.relative_paths, schema_version=2)
            except ArtifactError as retry_exc: self._schedule_evaluator_retry(run_id, experiment_id, str(attempt["attempt_id"]), retry_exc, stage="artifact_diagnostic_publication"); return
        self._crash("after_artifact_publish_before_digest_commit")
        evaluation_observation = dict(observation)
        if integrity_error is not None: evaluation_observation["integrity_error"] = integrity_error
        payload = spec["payload"]; research = self._research_contract(run_id); component_id = str(payload["component_id"])
        component = research.component(component_id)
        if component is None: raise OperationalHalt(f"preregistered component {component_id!r} is absent")
        raw_component = next(item for item in research.to_dict()["components"] if item["id"] == component_id)
        protocol = ({"result_kind": component.result_kind.value, "requirements": {}} if component.protocol is None
                    else research.to_dict()["protocols"][component.protocol])
        reason = AttemptTerminalReason(evaluation_observation["terminal_reason"])
        validated_result = None; validation_error = None; validation_code = None; repairable_result_envelope = False
        goal_result = None; goal_validation_error = None
        if integrity_error is None and payload.get("goal_evaluation") is not None:
            validated_result, goal_result, validation_error = self.shared_acceptance.evaluate(spec, Path(publication.path), evaluation_observation, parser)
            goal_validation_error = validation_error
            if validated_result is not None:
                reason = AttemptTerminalReason.SUCCEEDED
                validation_error = None
            elif validation_error is not None:
                from .shared_acceptance_execution import evaluation_phase_success, raw_execution_failed
                raw_ok, _, _ = evaluation_phase_success(evaluation_observation,
                    has_local=parser.local_parser is not None, has_goal=parser.goal_parser is not None)
                validation_code = ("invalid_parsed_measurement" if raw_ok else
                    "raw_execution_failed" if raw_execution_failed(evaluation_observation) else None)
                repairable_result_envelope = True
        elif integrity_error is None and reason is AttemptTerminalReason.SUCCEEDED:
            try:
                if target_binding is None:
                    apply_check = None
                elif submitted_target_patch:
                    apply_check = lambda value: (
                        "differs from the submitted patch used for execution"
                        if value != patch else workspace.check_bound_target_patch(
                            target_binding, patch=value))
                else:
                    apply_check = lambda value: workspace.check_target_patch(
                        attempt_id=attempt["attempt_id"], patch=value)
                validated_result = (validate_measurement_result(raw_component, ScientificMeasurement.from_dict(payload["measurement"]), publication.path)
                    if payload.get("measurement") is not None else validate_component_result(raw_component, protocol, publication.path,
                    git_apply_check=apply_check, implementation_bytes=implementation_bytes))
                if validated_result["method_family_id"] is not None and validated_result["method_family_id"] != payload["method_family_id"]: raise ComponentResultValidationError("method_family_mismatch", "result.json.identity.method_family_id", "result lineage differs from preregistration")
                if target_binding is not None: verify_downstream_implementation_contracts(self._research_contract(run_id).to_dict(), str(payload["component_id"]), str(payload["method_family_id"]), validated_result, Path(publication.path), workspace=workspace, binding=target_binding, attempt_id=str(attempt["attempt_id"]))
            except ComponentResultValidationError as exc: validated_result, validation_error, validation_code, repairable_result_envelope = None, str(exc), exc.code, payload.get("measurement") is not None or repairable_component_result_error(raw_component, protocol, publication.path, exc, expected_method_family_id=str(payload["method_family_id"]), git_apply_check=apply_check, implementation_bytes=implementation_bytes)
        if integrity_error is not None or (validation_error is not None and not repairable_result_envelope):
            scientific = ScientificOutcome.INTEGRITY_REJECTED
        elif validation_error is not None: scientific = ScientificOutcome.EXECUTION_FAILURE
        elif reason is AttemptTerminalReason.CANCELLED: scientific = ScientificOutcome.CANCELLED
        elif reason is not AttemptTerminalReason.SUCCEEDED: scientific = ScientificOutcome.EXECUTION_FAILURE
        elif validated_result is not None and validated_result["criterion_met"] is None:
            scientific = ScientificOutcome.INCONCLUSIVE
        else: scientific = ScientificOutcome.SUCCESS if validated_result and validated_result["criterion_met"] else ScientificOutcome.CLEAN_REFUTATION
        scientific_result = scientific in {ScientificOutcome.SUCCESS, ScientificOutcome.CLEAN_REFUTATION}
        metric_value = None if validated_result is None or not scientific_result else validated_result["metric_value"]
        logical = () if validated_result is None or not scientific_result else tuple(LogicalArtifact(
            logical_artifact=name, paths=validated_result["logical_artifacts"][name])
            for name in component.required_artifacts)
        candidate = {"outcome": scientific.value, "evaluator_version": payload["evaluator_version"],
            "metric_value": metric_value, "criterion_met": None if validated_result is None else validated_result["criterion_met"],
            "validation_error": validation_error, "execution_integrity_error": integrity_error}
        if payload.get("goal_evaluation") is not None:
            candidate.update(goal_evaluation=goal_result, parser_validation_error=goal_validation_error)
        analyze_role = self._review_role(research, component_id, "analyze")
        if analyze_role is not None:
            origin, reply = self._review_status(run_id, component_id, "analyze", experiment_id)
            if origin is None:
                source_attempt_id = str(payload["execution_contract"]["source_agent_attempt_id"])
                source_refs = (f"{source_attempt_id}--experiment.py",)
                if payload.get("measurement") is not None or payload.get("goal_evaluation") is not None:
                    from .agent_context import _submission_source_inputs
                    submitted = self._submission_source(str(payload["execution_contract"]["source_submit_command_id"]))[3]
                    source_refs = tuple(item.ref for item in _submission_source_inputs(source_attempt_id + "--", submitted))
                refs = (str(spec["fact_id"]), publication.artifact_id, f"{attempt['attempt_id']}--observation", f"{attempt['attempt_id']}--evaluation", *source_refs, f"{attempt['attempt_id']}--stdout", f"{attempt['attempt_id']}--stderr")
                signal = ExpertSignal(schema_version=1, kind=ExpertSignalKind.DEBATE, target_role_hint=analyze_role,
                    question=f"Analyze the immutable result and supporting refs for {component_id}.", context_refs=refs)
                origin = RendezvousOrigin(rendezvous_id=f"{attempt['attempt_id']}--analyze-rendezvous",
                    requester_role=component.owner_role, requester_slot=research.role_ids.index(component.owner_role) + 1,
                    source_attempt_id=attempt["attempt_id"], target_role=analyze_role, signal=signal,
                    component_id=component_id, phase="analyze")
                origin_record = {"rendezvous_id": origin.rendezvous_id, "requester_role": origin.requester_role,
                    "requester_slot": origin.requester_slot, "source_attempt_id": origin.source_attempt_id,
                    "target_role": origin.target_role, "expert_signal": signal.to_dict(),
                    "component_id": component_id, "phase": "analyze"}
                with self.store.transaction() as tx:
                    tx.record_artifact(self._artifact_record(publication))
                    tx.append_transition(self._transition_record(tx, run_id, experiment_id, "EXPERIMENT",
                        Command.REQUEST_RENDEZVOUS, ExperimentState.EVALUATING,
                        payload={"role": component.owner_role, "component_id": component_id,
                            "rendezvous_id": origin.rendezvous_id, "rendezvous_origin": origin_record,
                            "rendezvous_request": origin.request(run_id).to_dict(), "outcome": candidate}))
                return
            if reply is None: return
            if reply.disposition is not RendezvousDisposition.ANSWERED:
                scientific = ScientificOutcome.INTEGRITY_REJECTED; validation_error = f"analysis_{reply.disposition.value.lower()}"; validated_result = metric_value = None; logical = ()
                candidate.update({"outcome": scientific.value, "metric_value": None, "criterion_met": None, "validation_error": validation_error})
        if scientific in {ScientificOutcome.INTEGRITY_REJECTED, ScientificOutcome.CANCELLED}:
            goal_result = None
            if payload.get("goal_evaluation") is not None: candidate["goal_evaluation"] = None
        self._crash("after_evaluator_before_outcome_acceptance")
        creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (experiment_id,))
        if len(creation) != 1: raise OperationalHalt("evaluated experiment lacks one creation identity")
        experiment_ordinal = int(json.loads(creation[0]["payload_json"]).get("experiment_ordinal", 1)); ids, component_index, _ = _component_ids(run_id, research, component_id, experiment_ordinal=experiment_ordinal)
        outcome = CanonicalOutcomeAccepted(fact_id=_fact_id(run_id, "outcome", component_index, *(() if experiment_ordinal == 1 else (experiment_ordinal,))), run_id=RunId(run_id),
            outcome_id=OutcomeId(ids["outcome"]), experiment_id=ExperimentId(experiment_id),
            execution_id=ExecutionId(execution_id), artifact_id=ArtifactId(publication.artifact_id),
            component_id=ComponentId(component_id), method_family_id=MethodFamilyId(str(payload["method_family_id"])),
            result_kind=ComponentResultKind(str(payload["result_kind"])), outcome=scientific,
            artifact_set_digest=publication.digest, evaluator_version=str(payload["evaluator_version"]),
            metric_value=metric_value, logical_artifacts=logical, validation_code=validation_code,
            goal_evaluation=goal_result)
        with self.store.transaction() as tx:
            transition = self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.ACCEPT_OUTCOME,
                ExperimentState.TERMINAL, fact_id=str(outcome.fact_id),
                payload={"outcome": candidate, "attempt_id": attempt["attempt_id"], "component_id": component_id,
                         "stdout_path": None if collected.stdout_path is None else str(collected.stdout_path),
                         "stderr_path": None if collected.stderr_path is None else str(collected.stderr_path)})
            tx.record_artifact(self._artifact_record(publication)); self._crash("inside_outcome_after_artifact_before_fact")
            mutation = self.kernel.construct_fact(outcome, tx); tx.append_fact(mutation)
            self._crash("inside_outcome_after_fact_before_terminal")
            tx.append_transition(transition)
        self._crash("after_outcome_commit_before_cleanup"); workspace.cleanup_attempt(attempt["attempt_id"], preserve_logs=True)
    @staticmethod
    def _artifact_record(publication: ArtifactPublication) -> dict[str, Any]:
        return {"artifact_id": publication.artifact_id, **publication.key.to_dict(),
            "schema_version": str(publication.schema_version),
            "digest_algorithm": publication.digest_algorithm,
            "digest": publication.digest,
            "path": "/".join(publication.key.as_tuple())}
    def _finish_resource_revision(self, run_id: str, experiment_id: str, diagnostic: Mapping[str, Any]) -> None:
        head = self.store.latest_transition(experiment_id)
        if head["state"] not in {"READY", "PREREGISTERED", "EXECUTING"}: return
        spec = self.store.find_fact(run_id, "ExperimentPreregistered", experiment_id)
        attempts = self.store.list_attempts(execution_id=spec["execution_id"])
        if any(a["state"] == "OPEN" for a in attempts): return
        key = self.shared_gpu.key(run_id, spec["execution_id"], f"{experiment_id}--process-{len(attempts) + 1}")
        try:
            if not self.shared_gpu.release(key): return
        except PoolError as exc:
            if exc.code == "unavailable": return
            raise
        self._crash("after_resource_revision_release")
        self._synthetic_terminal(run_id, experiment_id, AttemptTerminalReason.FAILED, diagnostic=diagnostic)

    def _synthetic_terminal(self, run_id: str, experiment_id: str, reason: AttemptTerminalReason,
                            *, diagnostic: Mapping[str, Any] | None = None) -> None:
        spec, contract, agent_request, agent_response, source_path, result_path, workspace, _, grant = self._execution_inputs(run_id, experiment_id, synthetic=True)
        execution_id = spec["execution_id"]; attempts = self.store.list_attempts(execution_id=execution_id)
        ordinal = len(attempts) + 1; attempt_id = f"{experiment_id}--process-{ordinal}"
        mounts, _, patch = self._execution_mounts(run_id, spec, workspace, attempt_id)
        plan = _component_plan(self._research_contract(run_id).document, str(spec["payload"]["component_id"]), str(spec["payload"]["method_family_id"]), patch, _contract_gpu(spec["payload"]["execution_contract"]))
        prepared = workspace.plan(agent_request=agent_request, agent_response=agent_response, attempt_id=attempt_id, attempt_ordinal=ordinal, execution_id=execution_id,
            target_env=dict(contract["environment"]), timeout_seconds=contract["timeout_seconds"], source_relative_path=source_path, result_relative_path=result_path, trusted_workspace_files=_trusted_execution_files(plan), execution_argv=tuple(contract["argv"]), read_only_bindings=mounts, allow_generic_argv=spec["payload"].get("measurement") is not None or spec["payload"].get("goal_evaluation") is not None)
        prepared, _ = self._measurement_execution(spec, prepared, workspace, agent_response)
        with self.store.transaction() as tx:
            attempts = tx.list_attempts(execution_id=execution_id)
            owner = attempts[0]["command_id"] if attempts else f"{experiment_id}--resource-revision" if diagnostic else f"{experiment_id}--cancel-effect"
            head = tx.latest_transition(experiment_id)
            if attempts and head is not None and head["state"] == ExperimentState.EXECUTING.value:
                retry_reason = (diagnostic["resource_failure"] if diagnostic else
                    "process_retry_budget_exhausted" if reason is AttemptTerminalReason.FAILED else f"synthetic_{reason.value.lower()}")
                retry_payload = {"attempt_id": attempt_id, "process_request": prepared.process_request.to_dict(), "spec_fact_id": spec["fact_id"], "agent_attempt_id": prepared.agent_attempt_id, "agent_output_digest": prepared.agent_output_digest, "source_relative_path": source_path, "result_relative_path": result_path, "role": str(agent_request["role"]), "component_id": spec["payload"]["component_id"], **_grant_identity(grant), "retry_of_attempt_id": str(attempts[-1]["attempt_id"]), "retry_reason": retry_reason, "synthetic_before_spawn": True}
                if contract.get("source_submit_command_id") is not None: retry_payload["source_submit_command_id"] = contract["source_submit_command_id"]
                tx.append_transition(self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", Command.OPEN_RETRY_ATTEMPT, ExperimentState.EXECUTING, payload=retry_payload))
            if not attempts and not diagnostic:
                tx.record_command({"command_id": owner, "run_id": run_id, "workflow_id": experiment_id, "command_type": Command.CANCEL.value, "request": {"synthetic_cancel": True}, "status": "accepted", "result": {"attempt_id": attempt_id}, "revision": tx.latest_transition(experiment_id)["revision"]})
            tx.open_attempt({"attempt_id": attempt_id, "run_id": run_id, "workflow_id": experiment_id, "command_id": owner, "kind": "PROCESS", "experiment_id": experiment_id, "execution_id": execution_id, "attempt_ordinal": ordinal, "host_generation": self.generation, "started_at": time.time()})
            tx.close_attempt(attempt_id, terminal_reason=reason, ended_at=time.time(), payload={"attempt_id": attempt_id, "attempt_ordinal": ordinal, "execution_id": execution_id, "terminal_reason": reason.value, "synthetic_before_spawn": True, "process_request": prepared.process_request.to_dict(), **dict(diagnostic or {})})
            head = tx.latest_transition(experiment_id)
            command = Command.OBSERVE_ATTEMPT_TERMINAL if head["state"] == ExperimentState.EXECUTING.value else Command.CANCEL
            transition = self._transition_record(tx, run_id, experiment_id, "EXPERIMENT", command, ExperimentState.EVALUATING, next_command=Command.ACCEPT_OUTCOME.value, payload={"attempt_id": attempt_id, "synthetic": True})
            tx.append_transition(transition)
    def _drive_stopping(self, run_id: str) -> None:
        with self.store.transaction() as tx:
            self.grounding_dialogue.cancel_pending(tx, run_id)
        if self._stop_disposition(run_id) is RunState.COMPLETED:
            verdict = self.store.list_facts(run_id, "GoalVerdictRecorded")[0]
            self.interaction.stop_for_goal(run_id, str(verdict["subject_id"]))
        else:
            self.interaction.stop_guidance(run_id)
        origins, _, replies = self._rendezvous_records(run_id); pending = next((origin for origin in origins if origin.rendezvous_id not in replies), None)
        if pending is not None: self._resolve_rendezvous(run_id, pending, RendezvousDisposition.CANCELLED, "Run cancellation ended the peer request."); return
        experiments = self.store.list_workflow_heads(run_id, "EXPERIMENT")
        for experiment in experiments:
            state = experiment["state"]
            if state == ExperimentState.TERMINAL.value: continue
            if state == ExperimentState.DRAFTED.value:
                opens = [row for row in self.store.list_attempts(workflow_id=experiment["workflow_id"]) if row["state"] == "OPEN"]
                if opens:
                    if self.claude_agent_host is not None:
                        for row in opens:
                            if row["attempt_id"] in self.running_agents: self.claude_agent_host.cancel(self.running_agents[row["attempt_id"]])
                    if not json.loads(experiment["payload_json"]).get("agent_cancel_requested"):
                        with self.store.transaction() as tx:
                            transition = self._transition_record(tx, run_id, experiment["workflow_id"], "EXPERIMENT", Command.CANCEL, ExperimentState.DRAFTED, payload={"agent_cancel_requested": True})
                            tx.append_transition(transition)
                    return
                with self.store.transaction() as tx:
                    transition = self._transition_record(tx, run_id, experiment["workflow_id"], "EXPERIMENT", Command.CANCEL, ExperimentState.TERMINAL, payload={"discarded_before_preregistration": True})
                    tx.append_transition(transition)
            elif state in {ExperimentState.PREREGISTERED.value, ExperimentState.READY.value}:
                with self.store.transaction() as tx:
                    transition = self._transition_record(tx, run_id, experiment["workflow_id"], "EXPERIMENT", Command.CANCEL, ExperimentState.EVALUATING)
                    tx.append_transition(transition)
                self._synthetic_terminal(run_id, experiment["workflow_id"], AttemptTerminalReason.CANCELLED)
            elif state == ExperimentState.EXECUTING.value:
                for attempt_id, handle in tuple(self.running.items()):
                    row = self.store.get_attempt(attempt_id)
                    if row["run_id"] == run_id: self.runner.cancel(handle)
                attempts = tuple(row for row in self.store.list_attempts(workflow_id=experiment["workflow_id"]) if row["kind"] == "PROCESS")
                if not any(row["state"] == "OPEN" for row in attempts):
                    self._synthetic_terminal(run_id, experiment["workflow_id"], AttemptTerminalReason.CANCELLED)
            elif state == ExperimentState.EVALUATING.value:
                if experiment["due_at"] is not None and float(experiment["due_at"]) > time.time(): return
                self._evaluate_experiment(run_id, experiment["workflow_id"])
            return
        report_command = AuxiliaryTask(kind=AuxiliaryRequestKind.FINAL_REPORT, run_id=run_id).command_id if self._batch_enabled(run_id) else None
        open_attempts = [row for row in self.store.list_attempts(run_id=run_id, state="OPEN") if row["command_id"] != report_command]
        if self.claude_agent_host is not None:
            for row in open_attempts:
                if row["attempt_id"] in self.running_agents: self.claude_agent_host.cancel(self.running_agents[row["attempt_id"]])
        open_decisions = self.store.inspect("SELECT 1 FROM workflow_human_decisions WHERE run_id=? AND state='OPEN'", (run_id,))
        owned_live = any((row := self.store.get_attempt(attempt_id))["run_id"] == run_id and row["command_id"] != report_command for attempt_id in (*self.running, *self.running_agents))
        if open_attempts or open_decisions or owned_live: return
        if not self._ensure_final_report(run_id): return
        disposition = self._stop_disposition(run_id)
        with self.store.transaction() as tx:
            transition = self._transition_record(tx, run_id, run_id, "RUN", Command.FINISH_STOP, disposition, payload={"stop_disposition": disposition.value})
            tx.append_transition(transition)
    def _link_evidence(self, run_id: str, component_id: str, experiment_id: str | None = None) -> None:
        contract = self._research_contract(run_id); candidates = [row for row in self.store.list_facts(run_id, "CanonicalOutcomeAccepted") if row["payload"].get("component_id") == component_id and (experiment_id is None or row["experiment_id"] == experiment_id)]
        creations = {str(row["workflow_id"]): json.loads(row["payload_json"]) for row in self.store.inspect("SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))}
        outcome = max(candidates, key=lambda row: int(creations[str(row["experiment_id"])].get("experiment_ordinal", 1)), default=None)
        if outcome is None: raise OperationalHalt(f"component {component_id!r} lacks canonical outcome")
        ordinal = int(creations[str(outcome["experiment_id"])].get("experiment_ordinal", 1)); ids, component_index, _ = _component_ids(run_id, contract, component_id, experiment_ordinal=ordinal)
        if (spec := self.store.find_fact(run_id, "ExperimentPreregistered", str(outcome["experiment_id"]))) is None: raise OperationalHalt(f"component {component_id!r} lacks preregistration")
        hypothesis_id = str(spec["hypothesis_id"]); family = str(outcome["payload"]["method_family_id"])
        fact = EvidenceLinked(fact_id=_fact_id(run_id, "evidence", component_index, *(() if ordinal == 1 else (ordinal,))), run_id=RunId(run_id), evidence_id=EvidenceId(ids["evidence"]),
            hypothesis_id=HypothesisId(hypothesis_id), outcome_id=OutcomeId(ids["outcome"]),
            artifact_id=ArtifactId(str(outcome["artifact_id"])), component_id=ComponentId(component_id),
            method_family_id=MethodFamilyId(family), evidence_kind="canonical_component_result")
        self._append_run_fact(run_id, fact, Command.LINK_EVIDENCE)
    def _record_assessment(self, run_id: str, decision: PolicyDecision) -> None:
        component_id = str(decision.payload["component_id"]); contract = self._research_contract(run_id)
        evidences = [row for row in self.store.list_facts(run_id, "EvidenceLinked") if row["payload"].get("component_id") == component_id]
        if not evidences: raise OperationalHalt(f"component {component_id!r} lacks evidence")
        outcomes = {str(row["outcome_id"]): row for row in self.store.list_facts(run_id, "CanonicalOutcomeAccepted")}; creations = {str(row["workflow_id"]): json.loads(row["payload_json"]) for row in self.store.inspect("SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1", (run_id,))}
        evidences.sort(key=lambda row: int(creations[str(outcomes[str(row["outcome_id"])]["experiment_id"])].get("experiment_ordinal", 1)))
        all_evidences = evidences
        if self._batch_enabled(run_id):
            evidences = [row for row in evidences if row["hypothesis_id"] == decision.payload["hypothesis_id"]
                and outcomes[str(row["outcome_id"])]["experiment_id"] == decision.payload["experiment_id"]]
        evidence = evidences[-1]; hypothesis_id = str(evidence["hypothesis_id"]); family = str(evidence["payload"]["method_family_id"])
        outcome = outcomes[str(evidence["outcome_id"])]
        creation = creations[str(outcome["experiment_id"])]
        ordinal = int(creation.get("experiment_ordinal", 1)); ids, component_index, _ = _component_ids(run_id, contract, component_id, experiment_ordinal=ordinal)
        evidences = [item for item in all_evidences if str(item["hypothesis_id"]) == hypothesis_id]
        successor = _uses_expert_loop(contract) and ordinal > 1
        assessment_id = ids["assessment"] if not successor else f"{ids['assessment']}-{ordinal}"
        assessment = HypothesisAssessment(str(decision.payload["assessment"]))
        fact = HypothesisAssessed(fact_id=_fact_id(run_id, "assessment", component_index, *((ordinal,) if successor else ())), run_id=RunId(run_id), assessment_id=AssessmentId(assessment_id), hypothesis_id=HypothesisId(hypothesis_id),
            component_id=ComponentId(component_id), method_family_id=MethodFamilyId(family), evidence_ids=tuple(EvidenceId(str(item["subject_id"])) for item in evidences),
            assessment=assessment)
        self._append_run_fact(run_id, fact, Command.RECORD_ASSESSMENT)
    def _record_verdict(self, run_id: str, decision: PolicyDecision) -> None:
        fact = GoalVerdictRecorded(fact_id=_fact_id(run_id, "verdict"), run_id=RunId(run_id),
            verdict_id=VerdictId(_ids(run_id)["verdict"]),
            goal_id=GoalId(_ids(run_id)["goal"]),
            method_family_id=None if decision.payload["method_family_id"] is None else MethodFamilyId(str(decision.payload["method_family_id"])),
            assessment_ids=tuple(AssessmentId(value) for value in decision.payload["assessment_ids"]),
            verdict=GoalVerdict(str(decision.payload["verdict"])))
        if self._shared_goal(run_id) and fact.verdict is GoalVerdict.GOAL_MET:
            with self.store.transaction() as tx:
                tx.append_fact(self.kernel.construct_fact(fact, tx))
                tx.append_transition(self._transition_record(tx, run_id, run_id, "RUN", Command.RECORD_GOAL_VERDICT,
                    RunState.STOPPING, fact_id=str(fact.fact_id), payload={"stop_disposition": "COMPLETED"}))
            self._crash("after_goal_verdict_commit")
        else: self._append_run_fact(run_id, fact, Command.RECORD_GOAL_VERDICT)
    def _append_run_fact(self, run_id: str, fact: Any, command: Command) -> None:
        with self.store.transaction() as tx:
            mutation = self.kernel.construct_fact(fact, tx); tx.append_fact(mutation)
            transition = self._transition_record(tx, run_id, run_id, "RUN", command, RunState.ACTIVE, fact_id=str(fact.fact_id))
            tx.append_transition(transition)
    def _complete(self, run_id: str) -> None:
        self._reconcile_terminal_attempt_residue(run_id); experiments = self.store.list_workflow_heads(run_id, "EXPERIMENT")
        attempts = [row for row in self.store.list_attempts() if row["run_id"] == run_id]
        if any(row["state"] != ExperimentState.TERMINAL.value for row in experiments): raise OperationalHalt("COMPLETING run owns a nonterminal experiment")
        if any(row["state"] == "OPEN" for row in attempts): return
        with self.store.transaction() as tx:
            transition = self._transition_record(tx, run_id, run_id, "RUN", Command.COMPLETE_CLEAN, RunState.COMPLETED)
            tx.append_transition(transition)
    def _run_config(self, run_id: str) -> Mapping[str, Any]:
        rows = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (run_id,))
        if not rows: raise OperationalHalt(f"run {run_id} has no creation payload")
        return json.loads(rows[0]["payload_json"])
def _send_control_response(peer: socket.socket, response: ControlResponse) -> None:
    try: send_message(peer, response.to_mapping())
    except (BrokenPipeError, ConnectionResetError, TimeoutError): pass
def serve(
    state_root: Path,
    *,
    fault_point: str | None = None,
    fake_agent_mode: str = "VALID",
    claude_agent_host: ClaudeAgentHost | None = None,
    claude_model: str | None = None,
    agent_budget: AgentBudget | None = None,
    compute_resources: LocalComputeResources | None = None, provider_environment: Mapping[str, str] | None = None, exit_when_terminal: bool = False,
    literature_io: FixedLiteratureIO | None = None,
    grounding_web: Any = None,
    institutional_access: Any = None,
    services_factory: Callable[[], ContextManager[object]] | None = None,
    gpu_pool_socket: Path | None = None, gpu_pool_namespace: str | None = None,
    gpu_pool_server_uid: int | None = None,
) -> int:
    """Run one bounded UDS Host. The CLI must invoke this explicitly."""
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_root, 0o700)
    lock_path = state_root / "controller.lock"
    lock = lock_path.open("a+")
    os.chmod(lock_path, 0o600)
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise HostError(f"another Workflow Host owns {lock_path}")
    try:
        private_settings_path = None
        if claude_agent_host is not None:
            private_settings_path = prepare_host_settings(state_root, claude_agent_host.settings_path)
            claude_agent_host.settings_path = private_settings_path
        host = WorkflowHost(
            state_root, fault_point=fault_point, fake_agent_mode=FakeAgentMode(fake_agent_mode),
            claude_agent_host=claude_agent_host, claude_model=claude_model, agent_budget=agent_budget,
            compute_resources=compute_resources, provider_environment=provider_environment,
            literature_io=literature_io, grounding_web=grounding_web, institutional_access=institutional_access, private_settings_path=private_settings_path,
            gpu_pool_socket=gpu_pool_socket, gpu_pool_namespace=gpu_pool_namespace, gpu_pool_server_uid=gpu_pool_server_uid,
        )
    except BaseException:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN); lock.close(); raise
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_path = state_root / "control.sock"
    services = ExitStack()
    try:
        host.initialize()
        socket_path.unlink(missing_ok=True)
        address, directory_fd = socket_address(socket_path)
        try:
            server.bind(address)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        os.chmod(socket_path, 0o600)
        server.listen(16)
        server.settimeout(0.02)
        if services_factory is not None:
            observers = host.observer_services = services.enter_context(services_factory())
            if host.institutional_access is not None:
                host.institutional_access.event_sink = getattr(observers, "record_literature_event", None)
        def request_stop(_signum: int, _frame: object) -> None:
            host.stop_requested = True
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        while not host.stop_requested:
            # Share one scheduling tick across a bounded burst of waiting
            # clients. Neither a busy control queue nor the scheduler owns the
            # loop indefinitely; all Host mutations remain on this thread.
            backup_request = False
            control_deadline = time.monotonic() + 0.25
            for _ in range(16):
                try:
                    peer, _ = server.accept()
                except TimeoutError:
                    break
                with peer:
                    peer.settimeout(1.0)
                    try:
                        request = ControlRequest.from_mapping(receive_message(peer))
                        backup_request = backup_request or request.command == "BACKUP"
                        response = host.handle(request)
                    except (ProtocolError, TimeoutError) as exc:
                        response = ControlResponse.failure(
                            "invalid-request", "rejected", "protocol_error", str(exc)
                        )
                    _send_control_response(peer, response)
                if host.stop_requested or time.monotonic() >= control_deadline:
                    break
            if not host.stop_requested and not backup_request: host.tick()
            if exit_when_terminal and not host.stop_requested and host.terminal_exit_ready: return 0
        return 2 if host.operational_halt is not None else 0
    except Exception as exc:
        record_host_exception(state_root, exc, phase="serve",
            secrets=tuple(value for key, value in (provider_environment or {}).items() if key in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"})); raise
    finally:
        try:
            try: services.close()
            except Exception as exc:
                record_host_exception(state_root, exc, phase="services_close",
                    secrets=tuple(value for key, value in (provider_environment or {}).items() if key in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"})); raise
        finally:
            server.close()
            socket_path.unlink(missing_ok=True)
            host.close()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
