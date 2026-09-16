"""Opt-in current component-runtime G5 qualification against one real Claude model.

The lane combines one fresh-root real ``WorkflowHost`` canary with direct
``ANSWER_RENDEZVOUS`` and current ACI-10 ``QUERY_PLAN`` contracts through the
same production adapter.  The Host owns the durable proposal/spec/process
lifecycle and raw Oracle boundary.  Direct calls remain bounded and do not
create a durable run.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import pytest

from hypoloom.agent_aci import (
    ACI_SCHEMA_VERSION,
    DEFAULT_REAL_AGENT_BUDGET,
    EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AgentBudget,
    AgentCommand,
    AgentRequest,
    AgentResponse,
    EmbeddedInput,
    ExpertLoopCandidate,
    ExperimentSpecCandidate,
    HypothesisCandidate,
    ValidatedAgentResponse,
    canonical_json,
    required_effect,
    required_output_schema,
    validate_postflight,
)
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION,
    CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
    CLAUDE_AGENT_TRANSPORT_RETRIES,
    AgentHostTerminalReason,
    ClaudeAgentHost,
    process_group_exists,
    validate_claude_model_selector,
)
from hypoloom.collaboration import (
    ExpertSignalKind,
    RendezvousDisposition,
    RendezvousReply,
    RendezvousRequest,
)
from hypoloom.compute_resources import LocalComputeResources
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import (
    FACT_SCHEMA_VERSION,
    AttemptTerminalReason,
    MetricDirection,
    ResourcePreference,
)
from hypoloom.oracle import audit_all_runs
from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
from hypoloom.profile_admission import admit_profile
from hypoloom.execution_workspace import ExecutionWorkspace, GitTargetBinding
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    HypothesisUpdateDisposition,
)
from hypoloom.fake_agent import default_request
from hypoloom.literature import (
    LITERATURE_STRATA,
    LiteratureFilters,
    LiteratureQuery,
    ScholarlySourceClass,
    admit_literature_query_plan,
)
from hypoloom.workflow_host import WorkflowHost


_ENABLE_ENV = "HYPOLOOM_REAL_CLAUDE"
_EXECUTABLE_ENV = "HYPOLOOM_CLAUDE_EXECUTABLE"
_SETTINGS_ENV = "HYPOLOOM_CLAUDE_SETTINGS"
_MODEL_ENV = "HYPOLOOM_CLAUDE_MODEL"
_FILE_TOOLS_ENABLE_ENV = "HYPOLOOM_REAL_CLAUDE_FILE_TOOLS"
_GPU_ENABLE_ENV = "HYPOLOOM_REAL_GPU"
_GPU_UUID_ENV = "HYPOLOOM_REAL_GPU_UUID"
_GPU_RUNTIME_ENV = "HYPOLOOM_REAL_GPU_RUNTIME"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RUN_ID = "component-v5-real-claude-aci6"
_QUERY_PLAN_RUN_ID = "aci10-real-claude-grounding-query-plan"
_RENDEZVOUS_ID = f"{_RUN_ID}--component-1--experiment--agent-1--preflight-rendezvous"
_MAX_ACCEPTED_CALLS = 3
_MAX_PROVIDER_CALLS = 4
_BUDGET = DEFAULT_REAL_AGENT_BUDGET
# The qualified Read -> Write -> StructuredOutput exchange is reported by
# Claude Code as four turns.  This canary freezes that observed transport cost
# without changing or overriding any admitted research profile.
_FILE_TOOL_BUDGET = AgentBudget(
    max_turns=4,
    max_tokens=_BUDGET.max_tokens,
    max_output_bytes=_BUDGET.max_output_bytes,
    wall_seconds=_BUDGET.wall_seconds,
    max_cost=_BUDGET.max_cost,
    token_enforcement_mode=_BUDGET.token_enforcement_mode,
)
_GPU_MINIMUM_MEMORY_MIB = 8_192
_GPU_RESERVE_MEMORY_MIB = 4_096
_GPU_QUALIFICATION_OBJECTIVE = (
    "Qualify one bounded GPU-only hypothesis evolution. CPU execution is "
    "scientifically invalid for this qualification, so the experiment draft "
    "must select GPU_REQUIRED. The exact experiment.py must import PyTorch, "
    "assert that CUDA is available with exactly one visible device, perform an "
    "actual CUDA tensor operation, and print one JSON object containing "
    "cuda_available, cuda_device_count, cuda_device_name, and cuda_runtime. "
    "Keep the CUDA tensor and context alive for at least five seconds after "
    "printing the evidence, then write the exact rendered metric result "
    "contract with one clean score=3 trial to HYPOLOOM_RESULT_PATH. "
    "This qualification is self-contained."
)


_REAL_CLAUDE_ONLY = pytest.mark.skipif(
    os.environ.get(_ENABLE_ENV) != "1",
    reason=f"set {_ENABLE_ENV}=1 with exact provider configuration",
)

_REAL_CLAUDE_GPU_ONLY = pytest.mark.skipif(
    os.environ.get(_ENABLE_ENV) != "1"
    or os.environ.get(_GPU_ENABLE_ENV) != "1",
    reason=(
        f"set both {_ENABLE_ENV}=1 and {_GPU_ENABLE_ENV}=1 with exact provider "
        "and authorized GPU configuration"
    ),
)

_REAL_CLAUDE_FILE_TOOLS_ONLY = pytest.mark.skipif(
    os.environ.get(_ENABLE_ENV) != "1"
    or os.environ.get(_FILE_TOOLS_ENABLE_ENV) != "1",
    reason=(
        f"set both {_ENABLE_ENV}=1 and {_FILE_TOOLS_ENABLE_ENV}=1 with exact "
        "provider configuration"
    ),
)

_EXPECTED_COMMANDS = frozenset(
    {
        AgentCommand.PROPOSE_HYPOTHESIS,
        AgentCommand.DRAFT_EXPERIMENT_SPEC,
        AgentCommand.ANSWER_RENDEZVOUS,
    }
)


def _configured_path(name: str) -> Path:
    raw = os.environ.get(name)
    assert raw, f"{name} is required when {_ENABLE_ENV}=1"
    path = Path(raw)
    assert path.is_absolute(), f"{name} must be absolute"
    assert not path.is_symlink(), f"{name} must not be a symlink"
    assert path.is_file(), f"{name} must name a regular file"
    return path


def _provider_environment_for_sanitized_settings(
    configured_settings: Path,
) -> dict[str, str]:
    """Move only provider auth into the sandbox environment for one canary.

    The temporary settings file mounted at ``/config`` is deliberately empty,
    so a failed permission rule cannot disclose the operator's real settings.
    """

    document = json.loads(configured_settings.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    configured_environment = document.get("env", {})
    assert isinstance(configured_environment, dict)
    result: dict[str, str] = {}
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        value = os.environ.get(name, configured_environment.get(name))
        if isinstance(value, str) and value:
            result[name] = value
    if not ({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} & set(result)):
        pytest.skip(
            "file-tool qualification requires environment-style provider auth"
        )
    return result


def _configured_gpu() -> tuple[str, Path]:
    gpu_uuid = os.environ.get(_GPU_UUID_ENV)
    runtime_raw = os.environ.get(_GPU_RUNTIME_ENV)
    assert gpu_uuid and runtime_raw, (
        f"{_GPU_UUID_ENV} and {_GPU_RUNTIME_ENV} are required when both real "
        "qualification opt-ins are enabled"
    )
    assert gpu_uuid == gpu_uuid.strip(), f"{_GPU_UUID_ENV} must be exact trimmed text"
    assert runtime_raw == runtime_raw.strip(), (
        f"{_GPU_RUNTIME_ENV} must be exact trimmed text"
    )
    configured_runtime = Path(runtime_raw)
    assert configured_runtime.is_absolute(), f"{_GPU_RUNTIME_ENV} must be absolute"
    assert not configured_runtime.is_symlink(), (
        f"{_GPU_RUNTIME_ENV} must not be a symlink"
    )
    runtime = configured_runtime.resolve()
    assert runtime.is_dir() and (runtime / "bin" / "python").is_file()
    return gpu_uuid, runtime


def _nvidia_smi(*arguments: str) -> list[str]:
    result = subprocess.run(
        ["nvidia-smi", *arguments],
        cwd=_PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (arguments, result.stdout, result.stderr)
    return result.stdout.splitlines()


def _selected_gpu_compute_pids(gpu_uuid: str) -> set[int]:
    lines = _nvidia_smi(
        "--query-compute-apps=gpu_uuid,pid",
        "--format=csv,noheader,nounits",
    )
    return {
        int(pid.strip())
        for line in lines
        if len(parts := line.split(",", 1)) == 2
        and parts[0].strip() == gpu_uuid
        and (pid := parts[1]).strip().isdigit()
    }


def _process_parent_and_group(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[int, int] | None:
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2 :].split()
    if len(fields) < 3 or fields[0] == "Z":
        return None
    try:
        return int(fields[1]), int(fields[2])
    except ValueError:
        return None


def _compute_pids_owned_by_process_group(
    compute_pids: set[int],
    process_group_id: int,
    *,
    proc_root: Path = Path("/proc"),
) -> set[int]:
    """Return GPU PIDs whose outer-process ancestry reaches the managed PGID.

    Bubblewrap's ``--new-session`` gives the sandbox command a child PGID, so
    direct PGID equality would reject a genuine CUDA process.  Its outer PID
    remains a descendant of the Runner-owned bubblewrap process group.
    """

    owned: set[int] = set()
    for compute_pid in compute_pids:
        current = compute_pid
        visited: set[int] = set()
        while current > 0 and current not in visited:
            visited.add(current)
            identity = _process_parent_and_group(current, proc_root=proc_root)
            if identity is None:
                break
            parent_pid, observed_group = identity
            if observed_group == process_group_id:
                owned.add(compute_pid)
                break
            current = parent_pid
    return owned


def _require_observed_owned_gpu_process(
    observation: tuple[str, int, int] | None,
) -> tuple[str, int, int]:
    assert observation is not None, (
        "qualification completed without observing an NVIDIA compute PID "
        "owned by the scientific Process group"
    )
    return observation


def _assert_selected_gpu_idle(gpu_uuid: str) -> None:
    deadline = time.monotonic() + 20
    latest: tuple[list[str], set[int]] | None = None
    while time.monotonic() < deadline:
        inventory = _nvidia_smi(
            "--query-gpu=uuid,utilization.gpu,memory.used,temperature.gpu",
            "--format=csv,noheader,nounits",
        )
        matches = [
            line for line in inventory if line.split(",", 1)[0].strip() == gpu_uuid
        ]
        assert len(matches) == 1, (gpu_uuid, inventory)
        _, utilization, memory_used, temperature = [
            item.strip() for item in matches[0].split(",")
        ]
        compute_pids = _selected_gpu_compute_pids(gpu_uuid)
        latest = (matches, compute_pids)
        if (
            int(utilization) <= 10
            and int(memory_used) <= 512
            and int(temperature) <= 60
            and not compute_pids
        ):
            return
        time.sleep(0.2)
    raise AssertionError(f"selected GPU did not become safely idle: {latest!r}")


def _request(
    command: str,
    payload: Mapping[str, object],
    *,
    revision: int | None,
) -> ControlRequest:
    return ControlRequest(
        schema_version=1,
        request_id=f"{_RUN_ID}--{command.lower()}",
        expected_revision=revision,
        command=command,
        payload=payload,
    )


def _create_payload(*, gpu_required: bool = False) -> dict[str, object]:
    admission = admit_profile(
        _PROJECT_ROOT / "tests" / "fixtures" / "profiles" / "component-demo",
        run_id=_RUN_ID,
    )
    assert admission.admitted, admission.to_dict()
    assert admission.create_run_payload is not None
    payload = admission.create_run_payload
    contract = payload["research_contract"]
    budget = contract["budget"]
    budget["agent_invocation"] = _BUDGET.to_dict()
    if gpu_required:
        contract["goal"]["objective"] = _GPU_QUALIFICATION_OBJECTIVE
        budget.update(
            {
                "experiment_timeout": 60,
                "gpu_vram_reserve_mb": _GPU_RESERVE_MEMORY_MIB,
                "max_experiments": 1,
                "max_retries": 0,
                "min_vram_per_experiment": _GPU_MINIMUM_MEMORY_MIB,
            }
        )
        payload["execution"]["allow_gpu"] = True
    return payload


def _await_completed(host: WorkflowHost) -> Mapping[str, object]:
    deadline = time.monotonic() + 500
    latest = None
    while time.monotonic() < deadline:
        host.tick()
        latest = host.handle(_request("STATUS", {"run_id": _RUN_ID}, revision=None))
        if latest.result is not None and latest.result["run_state"] == "COMPLETED":
            return latest.result
        time.sleep(0.02)
    raise AssertionError(f"real-Claude Host run did not complete: {latest!r}")


def _await_gpu_completed(
    host: WorkflowHost,
    gpu_uuid: str,
) -> tuple[Mapping[str, object], tuple[str, int, int]]:
    deadline = time.monotonic() + 500
    latest = None
    observed_gpu_process: tuple[str, int, int] | None = None
    while time.monotonic() < deadline:
        host.tick()
        open_processes = tuple(
            row
            for row in host.store.list_attempts()
            if row["run_id"] == _RUN_ID
            and row["kind"] == "PROCESS"
            and row["state"] == "OPEN"
        )
        assert len(open_processes) <= 1, open_processes
        if (
            observed_gpu_process is None
            and open_processes
            and open_processes[0]["process_group_id"] is not None
        ):
            process_attempt = open_processes[0]
            process_group_id = int(process_attempt["process_group_id"])
            owned = _compute_pids_owned_by_process_group(
                _selected_gpu_compute_pids(gpu_uuid),
                process_group_id,
            )
            if owned:
                observed_gpu_process = (
                    str(process_attempt["attempt_id"]),
                    process_group_id,
                    min(owned),
                )
        latest = host.handle(_request("STATUS", {"run_id": _RUN_ID}, revision=None))
        if latest.result is not None and latest.result["run_state"] == "COMPLETED":
            return (
                latest.result,
                _require_observed_owned_gpu_process(observed_gpu_process),
            )
        time.sleep(0.02)
    raise AssertionError(f"real-Claude GPU Host run did not complete: {latest!r}")


def _validated_host_responses(
    host: WorkflowHost,
    model_identity: str,
) -> tuple[
    dict[AgentCommand, tuple[AgentRequest, ValidatedAgentResponse]],
    list[int],
    int,
    bool,
]:
    rows = host.store.inspect(
        "SELECT * FROM execution_attempts WHERE run_id=? ORDER BY started_at,attempt_id",
        (_RUN_ID,),
    )
    agent_rows = [row for row in rows if row["kind"] == "AGENT"]
    assert all(row["state"] == "CLOSED" for row in rows)
    responses: dict[
        AgentCommand,
        tuple[AgentRequest, ValidatedAgentResponse],
    ] = {}
    attempts: list[tuple[AgentCommand, AttemptTerminalReason]] = []
    for row in agent_rows:
        envelope = json.loads(row["observation_json"])
        request = AgentRequest.from_dict(envelope["request"])
        assert request.budget == _BUDGET
        reason = AttemptTerminalReason(row["terminal_reason"])
        attempts.append((request.command, reason))
        if reason is not AttemptTerminalReason.SUCCEEDED:
            continue
        response = AgentResponse.from_dict(envelope["response"])
        validated = validate_postflight(request, response)
        assert response.model_identity == model_identity
        assert response.adapter_version == CLAUDE_AGENT_HOST_VERSION
        assert response.token_usage["total_tokens"] == (
            response.token_usage["input_tokens"] + response.token_usage["output_tokens"]
        )
        assert request.command not in responses
        responses[request.command] = (request, validated)
    direct_answer_required = _direct_answer_required(attempts)
    expected_host_commands = _EXPECTED_COMMANDS - (
        {AgentCommand.ANSWER_RENDEZVOUS} if direct_answer_required else set()
    )
    assert set(responses) == expected_host_commands
    process_groups = [
        int(row["process_group_id"])
        for row in rows
        if row["process_group_id"] is not None
    ]
    return responses, process_groups, len(agent_rows), direct_answer_required


def _direct_answer_required(
    attempts: list[tuple[AgentCommand, AttemptTerminalReason]],
) -> bool:
    successful = [
        command
        for command, reason in attempts
        if reason is AttemptTerminalReason.SUCCEEDED
    ]
    assert len(successful) == len(set(successful)), successful
    successful_commands = set(successful)
    assert successful_commands <= _EXPECTED_COMMANDS
    assert {
        AgentCommand.PROPOSE_HYPOTHESIS,
        AgentCommand.DRAFT_EXPERIMENT_SPEC,
    } <= successful_commands
    direct_answer_required = AgentCommand.ANSWER_RENDEZVOUS not in successful_commands
    provider_launches = len(attempts) + int(direct_answer_required)
    assert provider_launches <= _MAX_PROVIDER_CALLS, [
        (command.value, reason.value) for command, reason in attempts
    ]
    return direct_answer_required


def _answer_request(
    model_identity: str,
    source_request: AgentRequest,
    source_response: AgentResponse,
) -> AgentRequest:
    # Reuse the exact immutable inputs accepted by the durable spec call.  This
    # makes the direct adapter sample a Host-shaped component preflight without
    # adding a second durable run or depending on the model emitting a signal.
    assert source_request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC
    assert source_request.run_id == _RUN_ID
    assert source_request.owner_workflow_id == (f"{_RUN_ID}--component-1--experiment")
    assert source_request.component_id == "primary.score"
    assert source_request.method_family_id is not None

    by_ref = {item.ref: item for item in source_request.input_artifacts}
    goal_input = next(
        item
        for item in source_request.input_artifacts
        if item.ref.endswith("--fact-goal")
    )
    goal_envelope = json.loads(goal_input.content_bytes())
    assert goal_envelope["fact_type"] == "GoalDefined"
    goal_payload = goal_envelope["payload"]
    assert goal_payload["schema_version"] == FACT_SCHEMA_VERSION
    assert goal_payload["run_id"] == _RUN_ID
    contract = goal_payload["research_contract"]
    assert contract["schema_version"] == 1
    component = next(
        item
        for item in contract["components"]
        if item["id"] == source_request.component_id
    )
    assert component["owner"] == source_request.role == "scientist-a"
    role_ids = [item["id"] for item in contract["roles"]]
    target_role = "scientist-b"
    assert target_role in role_ids

    response_ref = f"{source_request.attempt_id}--response"
    by_ref[response_ref] = EmbeddedInput.from_bytes(
        response_ref,
        "application/json",
        canonical_json(source_response.to_dict()),
    )
    context_refs = source_request.immutable_context_refs + (response_ref,)
    rendezvous = RendezvousRequest(
        schema_version=1,
        rendezvous_id=_RENDEZVOUS_ID,
        run_id=_RUN_ID,
        requester_role=source_request.role,
        requester_slot=role_ids.index(source_request.role) + 1,
        target_role=target_role,
        source_attempt_id=source_request.attempt_id,
        kind=ExpertSignalKind.HELP,
        question="Which cited assumption should be checked before freezing the method?",
        context_refs=context_refs,
        round=1,
        max_rounds=1,
    )
    inputs = (
        EmbeddedInput.from_bytes(
            f"{_RENDEZVOUS_ID}--rendezvous",
            "application/json",
            canonical_json(rendezvous.to_dict()),
        ),
        *(by_ref[ref] for ref in context_refs),
    )
    deadline = datetime.now(timezone.utc) + timedelta(seconds=_BUDGET.wall_seconds)
    return AgentRequest(
        schema_version=ACI_SCHEMA_VERSION,
        run_id=_RUN_ID,
        owner_workflow_id=source_request.owner_workflow_id,
        command=AgentCommand.ANSWER_RENDEZVOUS,
        command_id=f"{_RUN_ID}--command-rendezvous-qualification",
        attempt_id=f"{_RENDEZVOUS_ID}--agent-1",
        attempt_ordinal=1,
        role=target_role,
        component_id=source_request.component_id,
        method_family_id=source_request.method_family_id,
        immutable_context_refs=context_refs,
        input_artifacts=inputs,
        required_output_schema=required_output_schema(AgentCommand.ANSWER_RENDEZVOUS),
        effect_requirements=(required_effect(AgentCommand.ANSWER_RENDEZVOUS),),
        tool_permission_profile=CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
        budget=_BUDGET,
        deadline=deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
        max_input_bytes=65_536,
        required_model_identity=model_identity,
        required_adapter_version=CLAUDE_AGENT_HOST_VERSION,
    )


def _grounding_query_plan_request(
    model_identity: str,
) -> tuple[AgentRequest, LiteratureQuery]:
    """Build one current, Host-shaped request without creating a durable run."""

    task = AuxiliaryTask(
        kind=AuxiliaryRequestKind.GROUNDING_QUERY_PLAN,
        run_id=_QUERY_PLAN_RUN_ID,
    )
    goal = EmbeddedInput.from_bytes(
        f"{_QUERY_PLAN_RUN_ID}--goal",
        "application/json",
        canonical_json({
            "objective": (
                "Compare durable causal and temporal mechanisms for multimodal "
                "emotion recognition."
            ),
        }),
    )
    grounding_config = EmbeddedInput.from_bytes(
        f"{_QUERY_PLAN_RUN_ID}--grounding-config",
        "application/json",
        canonical_json({
            "grounding_scope": "role-neutral",
            "components": [{"id": "primary.score"}],
            "protocol_catalog": {
                "primary": {"metric": "weighted_f1"},
            },
            "language_route": {
                "route": "literature_review.query_plan",
                "output_language": "en",
            },
        }),
    )
    grounding_context = EmbeddedInput.from_bytes(
        f"{_QUERY_PLAN_RUN_ID}--grounding-context",
        "application/json",
        canonical_json({"target": {"entries": []}, "read_roots": []}),
    )
    skill = EmbeddedInput.from_bytes(
        f"{task.command_id}--openalex-query-planning-skill",
        "text/markdown",
        (
            _PROJECT_ROOT
            / "hypoloom"
            / "skills"
            / "openalex-query-planning"
            / "SKILL.md"
        ).read_bytes(),
    )
    context = (goal, grounding_config, grounding_context, skill)
    template = LiteratureQuery(
        schema_version=2,
        query='"multimodal emotion recognition" AND (causal OR temporal)',
        filters=LiteratureFilters(
            year_from=1990,
            year_to=2026,
            languages=("en",),
        ),
        max_items=6,
        allowed_source_classes=(
            ScholarlySourceClass.JOURNAL,
            ScholarlySourceClass.CONFERENCE,
        ),
        strata=LITERATURE_STRATA,
        latest_as_of="2026-09-01",
        immutable_context_refs=tuple(item.ref for item in context),
    )
    template_input = EmbeddedInput.from_bytes(
        f"{task.command_id}--literature-query-template",
        "application/json",
        canonical_json(template.to_dict()),
    )
    inputs = (*context, template_input)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=_BUDGET.wall_seconds)
    request = AgentRequest(
        schema_version=EXPERT_LOOP_ACI_SCHEMA_VERSION,
        run_id=_QUERY_PLAN_RUN_ID,
        owner_workflow_id=_QUERY_PLAN_RUN_ID,
        command=AgentCommand.LITERATURE_REVIEW,
        task_context=task,
        command_id=task.command_id,
        attempt_id=f"{_QUERY_PLAN_RUN_ID}--agent-1",
        attempt_ordinal=1,
        role="transport-scientist",
        component_id="primary.score",
        method_family_id=None,
        immutable_context_refs=tuple(item.ref for item in inputs),
        input_artifacts=inputs,
        required_output_schema=required_output_schema(
            AgentCommand.LITERATURE_REVIEW,
            task,
            schema_version=EXPERT_LOOP_ACI_SCHEMA_VERSION,
        ),
        effect_requirements=(required_effect(AgentCommand.LITERATURE_REVIEW),),
        tool_permission_profile=CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
        budget=_BUDGET,
        deadline=deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
        max_input_bytes=4 * 1024 * 1024,
        required_model_identity=model_identity,
        required_adapter_version=CLAUDE_AGENT_HOST_VERSION,
    )
    return AgentRequest.from_dict(request.to_dict()), template


def _supported_hypothesis_update_request(model_identity: str) -> AgentRequest:
    """Build one exact current-tip SUPPORTED request for the adapter canary."""

    request = default_request(AgentCommand.HYPOTHESIS_UPDATE)
    hypothesis_id = "hypothesis-current"
    family = "family-current"
    hypothesis_fact_id = f"fact-{hypothesis_id}"
    assessment_fact_id = f"fact-assessment-{hypothesis_id}"
    component_config = EmbeddedInput.from_bytes(
        f"{request.run_id}--{request.component_id}--expert-config",
        "application/json",
        canonical_json(
            {
                "component": {
                    "id": request.component_id,
                    "dependencies": ["must.upstream"],
                },
                "goal": {"summary": "all required components must close"},
            }
        ),
    )
    hypothesis = EmbeddedInput.from_bytes(
        hypothesis_fact_id,
        "application/json",
        canonical_json(
            {
                "fact_id": hypothesis_fact_id,
                "fact_type": "HypothesisProposed",
                "payload": {
                    "fact_type": "HypothesisProposed",
                    "fact_id": hypothesis_fact_id,
                    "run_id": request.run_id,
                    "schema_version": FACT_SCHEMA_VERSION,
                    "hypothesis_id": hypothesis_id,
                    "goal_id": "goal-fake",
                    "component_id": request.component_id,
                    "proposer_role": request.role,
                    "method_family_id": family,
                    "claim": "the current local criterion is met",
                    "assumptions": [],
                    "parent_hypothesis_id": None,
                    "relation": None,
                },
            }
        ),
    )
    assessment = EmbeddedInput.from_bytes(
        assessment_fact_id,
        "application/json",
        canonical_json(
            {
                "fact_id": assessment_fact_id,
                "fact_type": "HypothesisAssessed",
                "payload": {
                    "fact_type": "HypothesisAssessed",
                    "fact_id": assessment_fact_id,
                    "run_id": request.run_id,
                    "schema_version": FACT_SCHEMA_VERSION,
                    "assessment_id": f"assessment-{hypothesis_id}",
                    "hypothesis_id": hypothesis_id,
                    "component_id": request.component_id,
                    "method_family_id": family,
                    "evidence_ids": [f"evidence-{hypothesis_id}"],
                    "assessment": "SUPPORTED",
                },
            }
        ),
    )
    inputs = (component_config, hypothesis, assessment)
    return request.__class__.from_dict(
        {
            **request.to_dict(),
            "method_family_id": family,
            "input_artifacts": [item.to_dict() for item in inputs],
            "immutable_context_refs": [item.ref for item in inputs],
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "budget": _BUDGET.to_dict(),
            "required_model_identity": model_identity,
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )


def _invoke_answer(
    adapter: ClaudeAgentHost,
    request: AgentRequest,
    model_identity: str,
) -> tuple[ValidatedAgentResponse, int]:
    bindings = []
    handle = adapter.start(
        request,
        model=model_identity,
        provider_environment={},
        bind_process_group=bindings.append,
    )
    try:
        observation = adapter.wait(handle)
    finally:
        adapter.cleanup(handle)
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.attempt_id == request.attempt_id
    assert binding.pid == handle.pid
    assert binding.process_group_id == handle.process_group_id
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
        observation.terminal_reason.value,
        observation.failure_code,
    )
    assert observation.returncode == 0
    assert observation.response is not None
    validated = validate_postflight(request, observation.response)
    assert validated.response.model_identity == model_identity
    assert validated.response.adapter_version == CLAUDE_AGENT_HOST_VERSION
    return validated, handle.process_group_id


def _assert_process_groups_gone(process_groups: list[int]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        process_group_exists(process_group_id) for process_group_id in process_groups
    ):
        time.sleep(0.02)
    assert all(not process_group_exists(item) for item in process_groups)


def _resource_argv(row: Mapping[str, object]) -> tuple[tuple[str, ...], ...]:
    observation = json.loads(str(row["observation_json"]))
    argv = observation["process_request"]["argv"]
    selected: list[tuple[str, ...]] = []
    for index, value in enumerate(argv):
        if value == "--dev-bind":
            selected.append(tuple(argv[index : index + 3]))
        elif value == "--setenv" and argv[index + 1].startswith("CUDA_"):
            selected.append(tuple(argv[index : index + 3]))
    return tuple(selected)


def _assert_resource_binding(row: Mapping[str, object], gpu_uuid: str) -> None:
    arguments = _resource_argv(row)
    assert ("--setenv", "CUDA_VISIBLE_DEVICES", gpu_uuid) in arguments
    assert ("--setenv", "CUDA_DEVICE_ORDER", "PCI_BUS_ID") in arguments
    assert len([item for item in arguments if item[0] == "--dev-bind"]) == 3


def _cuda_evidence(state_root: Path, attempt_id: str) -> dict[str, Any]:
    log = (
        state_root
        / "artifacts"
        / "result-staging"
        / attempt_id
        / "logs"
        / "stdout.log"
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and "cuda_device_count" in value:
                    return value
        time.sleep(0.05)
    raise AssertionError(f"CUDA evidence was not observed for {attempt_id}")


def _assert_cuda_evidence(value: Mapping[str, object]) -> None:
    assert value["cuda_available"] is True
    assert value["cuda_device_count"] == 1
    assert value["cuda_runtime"] == "12.8"
    assert "RTX 3090" in str(value["cuda_device_name"])


def _assert_host_candidates(
    responses: dict[
        AgentCommand,
        tuple[AgentRequest, ValidatedAgentResponse],
    ],
    *,
    allowed_resource_preferences: frozenset[ResourcePreference] = frozenset(
        {ResourcePreference.CPU, ResourcePreference.AUTO}
    ),
) -> None:
    proposal = responses[AgentCommand.PROPOSE_HYPOTHESIS][1]
    assert isinstance(proposal.candidate, HypothesisCandidate)
    assert proposal.candidate.component_id == "primary.score"
    assert proposal.candidate.proposer_role == "scientist-a"
    assert proposal.candidate.method_family_id
    assert proposal.workspace_files == {}

    draft = responses[AgentCommand.DRAFT_EXPERIMENT_SPEC][1]
    assert isinstance(draft.candidate, ExperimentSpecCandidate)
    assert draft.candidate.component_id == "primary.score"
    assert draft.candidate.owner_role == "scientist-a"
    assert draft.candidate.method_family_id == proposal.candidate.method_family_id
    assert len(draft.candidate.metrics) == 1
    metric = draft.candidate.metrics[0]
    assert (metric.name, metric.direction, metric.target) == (
        "score",
        MetricDirection.MAXIMIZE,
        "3",
    )
    assert draft.candidate.evaluator_version == OUTCOME_ADAPTER_VERSION
    assert draft.candidate.artifact_requirements == ("result.json",)
    assert draft.candidate.resource_preference in allowed_resource_preferences
    assert set(draft.workspace_files) == {"experiment.py"}
    assert draft.workspace_files["experiment.py"]


def _assert_answer(
    request: AgentRequest,
    answer: ValidatedAgentResponse,
) -> None:
    rendezvous_input = next(
        item for item in request.input_artifacts if item.ref.endswith("--rendezvous")
    )
    rendezvous = RendezvousRequest.from_dict(
        json.loads(rendezvous_input.content_bytes())
    )
    assert request.command is AgentCommand.ANSWER_RENDEZVOUS
    assert isinstance(answer.candidate, RendezvousReply)
    assert answer.candidate.rendezvous_id == rendezvous.rendezvous_id
    assert answer.candidate.kind is rendezvous.kind
    assert answer.candidate.responder_role == request.role == rendezvous.target_role
    assert answer.candidate.disposition in {
        RendezvousDisposition.ANSWERED,
        RendezvousDisposition.DECLINED,
    }
    assert set(answer.candidate.supporting_refs) <= set(request.immutable_context_refs)
    assert answer.workspace_files == {}


def test_real_qualification_payload_freezes_profile_owned_agent_budget() -> None:
    payload = _create_payload()
    contract = payload["research_contract"]
    assert contract["budget"]["agent_invocation"] == _BUDGET.to_dict()
    assert payload["execution"]["allow_gpu"] is False

    gpu_payload = _create_payload(gpu_required=True)
    gpu_contract = gpu_payload["research_contract"]
    gpu_budget = gpu_contract["budget"]
    assert gpu_budget["agent_invocation"] == _BUDGET.to_dict()
    assert gpu_budget["min_vram_per_experiment"] == _GPU_MINIMUM_MEMORY_MIB
    assert gpu_budget["gpu_vram_reserve_mb"] == _GPU_RESERVE_MEMORY_MIB
    assert gpu_budget["max_experiments"] == 1
    assert gpu_budget["max_retries"] == 0
    assert gpu_payload["execution"]["allow_gpu"] is True
    assert gpu_contract["goal"]["objective"] == _GPU_QUALIFICATION_OBJECTIVE


def test_gpu_qualification_rejects_forged_output_without_owned_compute_pid(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"

    def add_process(pid: int, parent_pid: int, process_group_id: int) -> None:
        process_root = proc_root / str(pid)
        process_root.mkdir(parents=True)
        (process_root / "stat").write_text(
            f"{pid} (test process) S {parent_pid} {process_group_id} 0 0 0\n",
            encoding="utf-8",
        )

    managed_group = 10
    add_process(10, 1, managed_group)
    add_process(20, 10, 20)  # bubblewrap --new-session child
    add_process(30, 20, 20)  # CUDA process reported by nvidia-smi
    add_process(40, 1, 40)  # unrelated process on the selected GPU

    assert _compute_pids_owned_by_process_group(
        {30, 40}, managed_group, proc_root=proc_root,
    ) == {30}
    assert _compute_pids_owned_by_process_group(
        set(), managed_group, proc_root=proc_root,
    ) == set()

    forged_stdout = {
        "cuda_available": True,
        "cuda_device_count": 1,
        "cuda_device_name": "NVIDIA GeForce RTX 3090",
        "cuda_runtime": "12.8",
    }
    _assert_cuda_evidence(forged_stdout)
    with pytest.raises(AssertionError, match="owned by the scientific Process group"):
        _require_observed_owned_gpu_process(None)


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [
        (
            [
                (AgentCommand.PROPOSE_HYPOTHESIS, AttemptTerminalReason.SUCCEEDED),
                (
                    AgentCommand.DRAFT_EXPERIMENT_SPEC,
                    AttemptTerminalReason.SUCCEEDED,
                ),
            ],
            True,
        ),
        (
            [
                (AgentCommand.PROPOSE_HYPOTHESIS, AttemptTerminalReason.SUCCEEDED),
                (AgentCommand.ANSWER_RENDEZVOUS, AttemptTerminalReason.SUCCEEDED),
                (
                    AgentCommand.DRAFT_EXPERIMENT_SPEC,
                    AttemptTerminalReason.TIMED_OUT,
                ),
                (
                    AgentCommand.DRAFT_EXPERIMENT_SPEC,
                    AttemptTerminalReason.SUCCEEDED,
                ),
            ],
            False,
        ),
    ],
)
def test_direct_answer_required_within_provider_cap(
    attempts: list[tuple[AgentCommand, AttemptTerminalReason]],
    expected: bool,
) -> None:
    assert _direct_answer_required(attempts) is expected


def test_direct_answer_refuses_a_fifth_provider_launch() -> None:
    attempts = [
        (AgentCommand.PROPOSE_HYPOTHESIS, AttemptTerminalReason.TIMED_OUT),
        (AgentCommand.PROPOSE_HYPOTHESIS, AttemptTerminalReason.SUCCEEDED),
        (AgentCommand.DRAFT_EXPERIMENT_SPEC, AttemptTerminalReason.TIMED_OUT),
        (AgentCommand.DRAFT_EXPERIMENT_SPEC, AttemptTerminalReason.SUCCEEDED),
    ]
    with pytest.raises(AssertionError):
        _direct_answer_required(attempts)


def test_host_answer_does_not_mask_a_failed_draft() -> None:
    attempts = [
        (AgentCommand.PROPOSE_HYPOTHESIS, AttemptTerminalReason.SUCCEEDED),
        (AgentCommand.ANSWER_RENDEZVOUS, AttemptTerminalReason.SUCCEEDED),
        (AgentCommand.DRAFT_EXPERIMENT_SPEC, AttemptTerminalReason.TIMED_OUT),
        (AgentCommand.DRAFT_EXPERIMENT_SPEC, AttemptTerminalReason.FAILED),
    ]
    with pytest.raises(AssertionError):
        _direct_answer_required(attempts)


@_REAL_CLAUDE_ONLY
def test_real_claude_aci10_grounding_query_plan_contract() -> None:
    """Qualify one bounded QUERY_PLAN adapter turn without creating a run."""

    executable = _configured_path(_EXECUTABLE_ENV)
    settings = _configured_path(_SETTINGS_ENV)
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert os.access(executable, os.X_OK)
    configured_model = os.environ.get(_MODEL_ENV)
    assert configured_model, f"{_MODEL_ENV} is required when {_ENABLE_ENV}=1"
    model_identity = validate_claude_model_selector(configured_model)
    assert CLAUDE_AGENT_HOST_VERSION == "claude-code-bwrap/31"
    assert CLAUDE_AGENT_TRANSPORT_RETRIES == 0

    request, template = _grounding_query_plan_request(model_identity)
    assert request.schema_version == EXPERT_LOOP_ACI_SCHEMA_VERSION == 10
    assert request.required_output_schema == "LiteratureQuery/v2"
    assert isinstance(request.task_context, AuxiliaryTask)
    assert (
        request.task_context.kind
        is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
    )

    adapter = ClaudeAgentHost(
        claude_executable=executable,
        settings_path=settings,
    )
    adapter.preflight(model=model_identity)
    bindings = []
    handle = adapter.start(
        request,
        model=model_identity,
        provider_environment={},
        bind_process_group=bindings.append,
    )
    try:
        observation = adapter.wait(handle)
    finally:
        adapter.cleanup(handle)

    assert len(bindings) == 1
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
        observation.failure_code,
        observation.diagnostics,
    )
    assert observation.response is not None
    assert observation.response.typed_candidate is not None
    validated = validate_postflight(request, observation.response)
    assert isinstance(validated.candidate, LiteratureQuery)
    candidate = validated.candidate
    assert admit_literature_query_plan(candidate, template=template) is candidate
    assert candidate.schema_version == 2
    assert candidate.query
    assert candidate.filters == template.filters
    assert candidate.max_items == template.max_items
    assert candidate.allowed_source_classes == template.allowed_source_classes
    assert candidate.strata == template.strata
    assert candidate.latest_as_of == template.latest_as_of
    assert candidate.immutable_context_refs == template.immutable_context_refs
    assert validated.workspace_files == {}
    assert validated.response.model_identity == model_identity
    assert validated.response.adapter_version == CLAUDE_AGENT_HOST_VERSION
    _assert_process_groups_gone([handle.process_group_id])


@_REAL_CLAUDE_ONLY
def test_real_claude_hypothesis_update_local_lineage_contract() -> None:
    """Qualify the current SUPPORTED-tip lineage decision in one provider turn."""

    executable = _configured_path(_EXECUTABLE_ENV)
    settings = _configured_path(_SETTINGS_ENV)
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    configured_model = os.environ.get(_MODEL_ENV)
    assert configured_model, f"{_MODEL_ENV} is required when {_ENABLE_ENV}=1"
    model_identity = validate_claude_model_selector(configured_model)
    request = _supported_hypothesis_update_request(model_identity)

    adapter = ClaudeAgentHost(
        claude_executable=executable,
        settings_path=settings,
    )
    adapter.preflight(model=model_identity)
    bindings = []
    handle = adapter.start(
        request,
        model=model_identity,
        provider_environment={},
        bind_process_group=bindings.append,
    )
    try:
        observation = adapter.wait(handle)
    finally:
        adapter.cleanup(handle)

    assert len(bindings) == 1
    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
        observation.failure_code,
        observation.diagnostics,
    )
    assert observation.response is not None
    validated = validate_postflight(request, observation.response)
    assert isinstance(validated.candidate, ExpertLoopCandidate)
    assert (
        validated.candidate.update_disposition
        is HypothesisUpdateDisposition.TERMINATE_LINEAGE
    )
    assert set(validated.candidate.supporting_refs) <= set(
        request.immutable_context_refs
    )
    assert validated.workspace_files == {}
    assert validated.response.model_identity == model_identity
    assert validated.response.adapter_version == CLAUDE_AGENT_HOST_VERSION
    _assert_process_groups_gone([handle.process_group_id])


@_REAL_CLAUDE_FILE_TOOLS_ONLY
def test_real_claude_draft_file_tools_boundary(tmp_path: Path) -> None:
    """Qualify one real, bounded CC file-tool turn without creating a run."""

    executable = _configured_path(_EXECUTABLE_ENV)
    configured_settings = _configured_path(_SETTINGS_ENV)
    configured_model = os.environ.get(_MODEL_ENV)
    assert configured_model, f"{_MODEL_ENV} is required when {_ENABLE_ENV}=1"
    model_identity = validate_claude_model_selector(configured_model)
    provider_environment = _provider_environment_for_sanitized_settings(
        configured_settings
    )

    safe_settings = tmp_path / "settings.json"
    safe_settings.write_text("{}\n", encoding="utf-8")
    safe_settings.chmod(0o600)

    target = tmp_path / "target"
    target.mkdir()

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(target), *arguments],
            cwd=_PROJECT_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, (arguments, result.stdout, result.stderr)
        return result.stdout.strip()

    git("init", "-q")
    canary = 'TARGET_NONCE = "v16-frozen-target-visible"\n'
    target_file = target / "canary.py"
    target_file.write_text(canary, encoding="utf-8")
    git("add", "canary.py")
    git(
        "-c",
        "user.name=Hypoloom Qualification",
        "-c",
        "user.email=qualification@invalid",
        "commit",
        "-qm",
        "qualification target",
    )
    target_commit = git("rev-parse", "HEAD")
    original_target = target_file.read_bytes()
    assert git("status", "--porcelain=v1", "--untracked-files=all") == ""

    boundary_ref = "real-file-tools-boundary"
    boundary = EmbeddedInput.from_bytes(
        boundary_ref,
        "text/plain",
        (
            "This is a bounded four-turn file-tool permission qualification. In "
            "your first tool-use turn, Read /target/canary.py exactly once. In your "
            "second tool-use turn, use Write to leave exactly one valid Python file "
            "at /workspace/experiment.py. The Python file must "
            "contain the exact TARGET_NONCE value read from the frozen target and "
            "the literal assignment FILE_TOOL_QUALIFIED = True. Create no other file. "
            "Return a typed experiment candidate for metric score, direction "
            "MAXIMIZE, target 1, evaluator_version "
            f"{OUTCOME_ADAPTER_VERSION}, artifact_requirements [result.json], and "
            "resource_preference CPU."
        ).encode("utf-8"),
    )
    base = default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)
    deadline = datetime.now(timezone.utc) + timedelta(
        seconds=_FILE_TOOL_BUDGET.wall_seconds
    )
    request = AgentRequest.from_dict(
        {
            **base.to_dict(),
            "run_id": "real-claude-file-tools",
            "owner_workflow_id": "real-claude-file-tools--experiment",
            "command_id": "real-claude-file-tools--draft",
            "attempt_id": "real-claude-file-tools--agent-1",
            "immutable_context_refs": [boundary_ref],
            "input_artifacts": [boundary.to_dict()],
            "tool_permission_profile": CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
            "budget": _FILE_TOOL_BUDGET.to_dict(),
            "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "required_model_identity": model_identity,
            "required_adapter_version": CLAUDE_AGENT_HOST_VERSION,
        }
    )

    state_root = tmp_path / "state"
    artifact_root = state_root / "artifacts"
    artifact_root.mkdir(parents=True)
    workspace_owner = ExecutionWorkspace(artifact_root, state_root=state_root)
    workspace = workspace_owner.prepare_agent_workspace(
        attempt_id=request.attempt_id,
        target_binding=GitTargetBinding(
            checkout=target.resolve(), commit=target_commit
        ),
    )
    adapter = ClaudeAgentHost(
        claude_executable=executable,
        settings_path=safe_settings,
    )
    adapter.preflight(
        model=model_identity,
        provider_environment=provider_environment,
    )
    bindings = []
    handle = adapter.start(
        request,
        model=model_identity,
        provider_environment=provider_environment,
        bind_process_group=bindings.append,
        tool_workspace=workspace,
    )
    observation = adapter.wait(handle)

    assert observation.terminal_reason is AgentHostTerminalReason.SUCCEEDED, (
        observation.failure_code,
        observation.diagnostics,
    )
    assert observation.response is not None
    validated = validate_postflight(request, observation.response)
    source = validated.workspace_files["experiment.py"]
    compile(source, "experiment.py", "exec")
    rendered_source = source.decode("utf-8")
    assert "v16-frozen-target-visible" in rendered_source
    assert "FILE_TOOL_QUALIFIED = True" in rendered_source
    assert len(bindings) == 1
    assert target_file.read_bytes() == original_target
    assert git("rev-parse", "HEAD") == target_commit
    assert git("status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not workspace.attempt_root.exists()
    _assert_process_groups_gone([handle.process_group_id])


@_REAL_CLAUDE_ONLY
def test_real_claude_aci6_component_v5_and_rendezvous_compatibility(
    tmp_path: Path,
) -> None:
    executable = _configured_path(_EXECUTABLE_ENV)
    settings = _configured_path(_SETTINGS_ENV)
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert os.access(executable, os.X_OK)
    configured_model = os.environ.get(_MODEL_ENV)
    assert configured_model, f"{_MODEL_ENV} is required when {_ENABLE_ENV}=1"
    model_identity = validate_claude_model_selector(configured_model)

    adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
    adapter.preflight(model=model_identity)
    state_root = tmp_path / "state"
    host = WorkflowHost(
        state_root,
        claude_agent_host=adapter,
        claude_model=model_identity,
        agent_budget=_BUDGET,
    )
    started_at = time.monotonic()
    process_groups: list[int] = []
    host_responses: dict[
        AgentCommand,
        tuple[AgentRequest, ValidatedAgentResponse],
    ] = {}
    try:
        host.initialize()
        created = host.handle(_request("CREATE_RUN", _create_payload(), revision=0))
        assert created.status == "accepted", created
        status = _await_completed(host)
        assert status["open_attempts"] == 0
        assert not host.running
        assert not host.running_agents
        (
            host_responses,
            process_groups,
            host_provider_launches,
            direct_answer_required,
        ) = _validated_host_responses(host, model_identity)
        _assert_host_candidates(host_responses)
    finally:
        host.close()

    reports = audit_all_runs(state_root)
    assert len(reports) == 1
    report = reports[0]
    assert report.run_id == _RUN_ID
    assert report.ok
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.owned_process_residue == ()
    work = state_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())

    accepted_responses = dict(host_responses)
    if direct_answer_required:
        source_request, source_validated = host_responses[
            AgentCommand.DRAFT_EXPERIMENT_SPEC
        ]
        answer_request = _answer_request(
            model_identity,
            source_request,
            source_validated.response,
        )
        answer, answer_process_group = _invoke_answer(
            adapter,
            answer_request,
            model_identity,
        )
        process_groups.append(answer_process_group)
        accepted_responses[AgentCommand.ANSWER_RENDEZVOUS] = (
            answer_request,
            answer,
        )
    provider_launches = host_provider_launches + int(direct_answer_required)
    assert provider_launches <= _MAX_PROVIDER_CALLS
    assert set(accepted_responses) == _EXPECTED_COMMANDS
    answer_request, answer = accepted_responses[AgentCommand.ANSWER_RENDEZVOUS]
    _assert_answer(answer_request, answer)
    _assert_process_groups_gone(process_groups)

    accepted = [
        accepted_responses[command][1].response
        for command in (
            AgentCommand.PROPOSE_HYPOTHESIS,
            AgentCommand.DRAFT_EXPERIMENT_SPEC,
            AgentCommand.ANSWER_RENDEZVOUS,
        )
    ]
    assert len(accepted) == _MAX_ACCEPTED_CALLS
    total_tokens = sum(item.token_usage["total_tokens"] for item in accepted)
    total_turns = sum(item.turn_count for item in accepted)
    total_cost = sum(
        (Decimal(item.cost) for item in accepted),
        start=Decimal("0"),
    )
    assert total_tokens <= _MAX_ACCEPTED_CALLS * _BUDGET.max_tokens
    assert total_turns <= _MAX_ACCEPTED_CALLS * _BUDGET.max_turns
    assert total_cost <= _MAX_ACCEPTED_CALLS * Decimal(_BUDGET.max_cost)
    assert time.monotonic() - started_at <= 540
    print(
        json.dumps(
            {
                "adapter_version": CLAUDE_AGENT_HOST_VERSION,
                "agent_aci_version": ACI_SCHEMA_VERSION,
                "commands": [
                    {
                        "command": item.command.value,
                        "cost_usd": item.cost,
                        "tokens": item.token_usage["total_tokens"],
                        "turns": item.turn_count,
                    }
                    for item in accepted
                ],
                "maximum_provider_calls_on_failure": _MAX_PROVIDER_CALLS,
                "model_identity": model_identity,
                "oracle_ok": report.ok,
                "provider_launches": provider_launches,
                "run_state": "COMPLETED",
                "total_cost_usd": str(total_cost),
                "total_tokens": total_tokens,
                "total_turns": total_turns,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@_REAL_CLAUDE_GPU_ONLY
def test_real_claude_gpu_required_hypothesis_evolution(tmp_path: Path) -> None:
    executable = _configured_path(_EXECUTABLE_ENV)
    settings = _configured_path(_SETTINGS_ENV)
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert os.access(executable, os.X_OK)
    configured_model = os.environ.get(_MODEL_ENV)
    assert configured_model, f"{_MODEL_ENV} is required when {_ENABLE_ENV}=1"
    model_identity = validate_claude_model_selector(configured_model)
    gpu_uuid, runtime = _configured_gpu()
    _assert_selected_gpu_idle(gpu_uuid)

    adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
    adapter.preflight(model=model_identity)
    resources = LocalComputeResources(
        allowed_gpu_uuids=(gpu_uuid,),
        gpu_runtime_prefix=runtime,
    )
    state_root = tmp_path / "state"
    host = WorkflowHost(
        state_root,
        claude_agent_host=adapter,
        claude_model=model_identity,
        agent_budget=_BUDGET,
        compute_resources=resources,
    )
    started_at = time.monotonic()
    process_groups: list[int] = []
    host_responses: dict[
        AgentCommand,
        tuple[AgentRequest, ValidatedAgentResponse],
    ] = {}
    host_provider_launches = 0
    scientific_attempt: Mapping[str, object] | None = None
    try:
        host.initialize()
        created = host.handle(
            _request("CREATE_RUN", _create_payload(gpu_required=True), revision=0)
        )
        assert created.status == "accepted", created
        status, gpu_process_observation = _await_gpu_completed(host, gpu_uuid)
        assert status["open_attempts"] == 0
        assert status["canonical_outcome_count"] == 1
        assert not host.running
        assert not host.running_agents
        (
            host_responses,
            process_groups,
            host_provider_launches,
            _,
        ) = _validated_host_responses(host, model_identity)
        _assert_host_candidates(
            host_responses,
            allowed_resource_preferences=frozenset(
                {ResourcePreference.GPU_REQUIRED}
            ),
        )

        draft_request, draft = host_responses[
            AgentCommand.DRAFT_EXPERIMENT_SPEC
        ]
        source = draft.workspace_files["experiment.py"].decode("utf-8")
        assert "torch" in source
        assert "cuda" in source.casefold()
        assert "torch.cuda.is_available" in source
        assert "torch.cuda.device_count" in source

        specs = host.store.list_facts(_RUN_ID, "ExperimentPreregistered")
        assert len(specs) == 1
        spec = specs[0]
        contract = spec["payload"]["execution_contract"]
        assert contract["source_agent_attempt_id"] == draft_request.attempt_id
        assert (
            contract["source_agent_output_digest"]
            == draft.response.output_digest
        )
        assert contract["resource_preference"] == "GPU_REQUIRED"
        assert contract["resource_grant"] == {
            "kind": "GPU",
            "gpu_uuid": gpu_uuid,
        }

        scientific_attempts = tuple(
            row
            for row in host.store.list_attempts(
                execution_id=str(spec["execution_id"])
            )
            if row["kind"] == "PROCESS"
        )
        assert len(scientific_attempts) == 1
        scientific_attempt = scientific_attempts[0]
        assert scientific_attempt["state"] == "CLOSED"
        assert scientific_attempt["terminal_reason"] == "SUCCEEDED"
        observed_attempt_id, observed_process_group, observed_compute_pid = (
            gpu_process_observation
        )
        assert observed_attempt_id == scientific_attempt["attempt_id"]
        assert observed_process_group == scientific_attempt["process_group_id"]
        assert observed_compute_pid > 0
        _assert_resource_binding(scientific_attempt, gpu_uuid)
        observation = json.loads(str(scientific_attempt["observation_json"]))
        usage = observation["resource_usage"]
        assert usage["gpu_uuid"] == gpu_uuid
        assert usage["sample_count"] >= 1
        assert usage["pressure_exceeded"] is False
        _assert_cuda_evidence(
            _cuda_evidence(state_root, str(scientific_attempt["attempt_id"]))
        )

        outcomes = host.store.list_facts(_RUN_ID, "CanonicalOutcomeAccepted")
        assert len(outcomes) == 1
        assert outcomes[0]["payload"]["outcome"] == "SUCCESS"
        verdicts = host.store.list_facts(_RUN_ID, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
    finally:
        host.close()

    assert scientific_attempt is not None
    reports = audit_all_runs(state_root)
    assert len(reports) == 1
    report = reports[0]
    assert report.run_id == _RUN_ID
    assert report.ok
    assert report.canonical_outcome_count == 1
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.owned_process_residue == ()
    work = state_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())
    _assert_process_groups_gone(process_groups)
    _assert_selected_gpu_idle(gpu_uuid)
    assert host_provider_launches <= _MAX_PROVIDER_CALLS
    assert time.monotonic() - started_at <= 540
    print(
        json.dumps(
            {
                "adapter_version": CLAUDE_AGENT_HOST_VERSION,
                "agent_aci_version": ACI_SCHEMA_VERSION,
                "gpu_uuid": gpu_uuid,
                "model_identity": model_identity,
                "oracle_ok": report.ok,
                "cuda_evidence_observed": True,
                "provider_launches": host_provider_launches,
                "run_state": "COMPLETED",
                "scientific_outcome": "SUCCESS",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
