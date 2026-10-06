from __future__ import annotations

import copy
import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import (
    AgentCommand,
    AgentRequest,
    AgentResponse,
    with_output_digest,
)
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION,
    CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
    AgentHostTerminalReason,
    AgentProcessBinding,
    AgentToolWorkspace,
    ClaudeAgentObservation,
    validate_claude_model_selector,
)
from hypoloom.collaboration import ExpertSignal, ExpertSignalKind
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AttemptTerminalReason
from hypoloom.fake_agent import FakeAgent
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import ProcessObservation, ProcessRequest
from hypoloom.workflow_host import WorkflowHost


_MODEL = "phase8-controlled-agent"
_REPOSITORY = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class _AgentHandle:
    request: AgentRequest
    response: AgentResponse
    pid: int
    process_group_id: int
    started_at: float
    tool_workspace: object | None = None
    runtime_prefix: Path | None = None
    read_roots: tuple[Path, ...] = ()
    cancelled: bool = False


class _ControlledAgentHost:
    """Delay alpha's proposal while every independent Agent call completes."""

    def __init__(self, runtime_file: Path, *, model_identity: str = _MODEL) -> None:
        self.claude_executable = runtime_file
        self.bwrap_path = runtime_file
        self.settings_path: Path | None = runtime_file
        self.model_identity = validate_claude_model_selector(model_identity)
        self.release_alpha_proposal = False
        self.started: list[_AgentHandle] = []
        self.provider_environments: list[dict[str, str]] = []
        self._fake = FakeAgent()

    def preflight(
        self,
        *,
        model: str,
        tool_permission_profile: str = CLAUDE_AGENT_TOOL_PERMISSION_PROFILE,
        provider_environment: Mapping[str, str] | None = None,
    ) -> None:
        assert model == self.model_identity
        assert isinstance(tool_permission_profile, str)
        assert provider_environment is None or isinstance(provider_environment, Mapping)

    def start(
        self,
        request: AgentRequest,
        *,
        model: str,
        provider_environment: Mapping[str, str],
        diagnostics_root: Path,
        bind_process_group: Callable[[AgentProcessBinding], None],
        tool_workspace: AgentToolWorkspace | None = None,
        host_tool_handler: Callable[[str, str, Mapping[str, object]], dict[str, object]] | None = None,
        runtime_prefix: Path | None = None,
        read_roots: tuple[Path, ...] = (),
        traffic_binding=None,
    ) -> _AgentHandle:
        assert model == self.model_identity
        assert request.required_model_identity == self.model_identity
        assert isinstance(provider_environment, Mapping)
        assert isinstance(diagnostics_root, Path) and diagnostics_root.is_absolute()
        assert host_tool_handler is None or callable(host_tool_handler)
        assert runtime_prefix is None or isinstance(runtime_prefix, Path)
        assert isinstance(read_roots, tuple) and all(isinstance(root, Path) for root in read_roots)
        self.provider_environments.append(dict(provider_environment))
        response = self._fake.invoke(request)
        assert isinstance(response, AgentResponse)
        signal = None
        if (
            request.command is AgentCommand.PROPOSE_HYPOTHESIS
            and request.role == "alpha"
        ):
            signal = ExpertSignal(
                schema_version=1,
                kind=ExpertSignalKind.HELP,
                target_role_hint="beta",
                question="Which assumption should beta challenge?",
                context_refs=request.immutable_context_refs[:1],
            )
        response = with_output_digest(
            replace(
                response,
                model_identity=self.model_identity,
                adapter_version=CLAUDE_AGENT_HOST_VERSION,
                expert_signal=signal,
                output_digest="",
            )
        )
        pid = 1_900_000_000 + len(self.started)
        handle = _AgentHandle(
            request=request,
            response=response,
            pid=pid,
            process_group_id=pid,
            started_at=time.monotonic(),
            tool_workspace=tool_workspace,
            runtime_prefix=runtime_prefix,
            read_roots=read_roots,
        )
        bind_process_group(
            AgentProcessBinding(
                attempt_id=request.attempt_id,
                pid=pid,
                process_group_id=pid,
            )
        )
        self.started.append(handle)
        return handle

    def poll(self, handle: _AgentHandle) -> ClaudeAgentObservation | None:
        if (
            handle.request.command is AgentCommand.PROPOSE_HYPOTHESIS
            and handle.request.role == "alpha"
            and not self.release_alpha_proposal
            and not handle.cancelled
        ):
            return None
        terminal_reason = (
            AgentHostTerminalReason.CANCELLED
            if handle.cancelled
            else AgentHostTerminalReason.SUCCEEDED
        )
        if handle.tool_workspace is not None:
            handle.tool_workspace.cleanup()  # type: ignore[attr-defined]
            handle.tool_workspace = None
        return ClaudeAgentObservation(
            attempt_id=handle.request.attempt_id,
            attempt_ordinal=handle.request.attempt_ordinal,
            terminal_reason=terminal_reason,
            returncode=-15 if handle.cancelled else 0,
            pid=handle.pid,
            process_group_id=handle.process_group_id,
            started_at=handle.started_at,
            ended_at=time.monotonic(),
            response=None if handle.cancelled else handle.response,
            failure_code="cancelled" if handle.cancelled else None,
            diagnostics={},
        )

    def cancel(self, handle: _AgentHandle) -> bool:
        if handle.cancelled:
            return False
        handle.cancelled = True
        return True

    def cleanup(self, handle: _AgentHandle) -> None:
        if handle.tool_workspace is not None:
            handle.tool_workspace.cleanup()  # type: ignore[attr-defined]
            handle.tool_workspace = None


@dataclass(slots=True)
class _BlockedProcess:
    request: ProcessRequest
    pid: int
    process_group_id: int
    started_at: float
    complete: bool = False
    cancelled: bool = False

    @property
    def returncode(self) -> int | None:
        if self.cancelled:
            return -15
        return 0 if self.complete else None


class _BlockingRunner:
    def __init__(self) -> None:
        self.started: list[_BlockedProcess] = []

    def start(
        self, request: ProcessRequest, *, before_exec: Callable[[int], None] | None = None,
    ) -> _BlockedProcess:
        pid = 2_000_000_000 + len(self.started)
        if before_exec is not None:
            before_exec(pid)
        handle = _BlockedProcess(
            request=request,
            pid=pid,
            process_group_id=pid,
            started_at=time.monotonic(),
            complete=request.execution_id.endswith("--candidate-validation"),
        )
        self.started.append(handle)
        return handle

    def wait(self, handle: _BlockedProcess) -> ProcessObservation:
        return ProcessObservation(
            attempt_id=handle.request.attempt_id,
            attempt_ordinal=handle.request.attempt_ordinal,
            execution_id=handle.request.execution_id,
            terminal_reason=(
                AttemptTerminalReason.CANCELLED
                if handle.cancelled
                else AttemptTerminalReason.SUCCEEDED
            ),
            returncode=handle.returncode,
            pid=handle.pid,
            process_group_id=handle.process_group_id,
            started_at=handle.started_at,
            ended_at=time.monotonic(),
            stdout_path=handle.request.stdout_path,
            stderr_path=handle.request.stderr_path,
        )

    def cancel(self, handle: _BlockedProcess) -> bool:
        if handle.cancelled:
            return False
        handle.cancelled = True
        return True

    def cleanup(self, handle: _BlockedProcess) -> None:
        return None


def _open_attempts(host: WorkflowHost, run_id: str) -> tuple[dict[str, object], ...]:
    return tuple(
        row
        for row in host.store.list_attempts()
        if row["run_id"] == run_id and row["state"] == "OPEN"
    )


def _is_scientific_process_attempt(
    host: WorkflowHost, row: dict[str, object]
) -> bool:
    if row["kind"] != "PROCESS":
        return False
    spec = host.store.find_fact(
        str(row["run_id"]), "ExperimentPreregistered", str(row["experiment_id"])
    )
    return spec is not None and row["execution_id"] == spec["execution_id"]


def _scientific_process_attempts(
    host: WorkflowHost, run_id: str
) -> tuple[dict[str, object], ...]:
    return tuple(
        row
        for row in host.store.list_attempts()
        if row["run_id"] == run_id and _is_scientific_process_attempt(host, row)
    )


def _started_scientific_processes(
    host: WorkflowHost, run_id: str, runner: _BlockingRunner
) -> tuple[_BlockedProcess, ...]:
    execution_ids = {
        str(row["execution_id"])
        for row in host.store.list_facts(run_id, "ExperimentPreregistered")
    }
    return tuple(
        handle
        for handle in runner.started
        if handle.request.execution_id in execution_ids
    )


def _answer_attempts(
    attempts: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    answers = []
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        observation = json.loads(str(row["observation_json"]))
        request = AgentRequest.from_dict(observation["request"])
        if request.command is AgentCommand.ANSWER_RENDEZVOUS:
            answers.append(row)
    return tuple(answers)


def _create_run(
    host: WorkflowHost,
    run_id: str,
    *,
    runtime_prefix: Path | None = None,
    read_roots: tuple[Path, ...] = (),
) -> None:
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo", run_id=run_id, delay_seconds=0.0
    )
    assert admission.admitted and admission.create_run_payload is not None
    payload = admission.create_run_payload
    contract = payload["research_contract"]
    role_template = contract["roles"][0]
    contract["roles"] = [
        {**role_template, "id": role} for role in ("alpha", "beta")
    ]
    base = contract["components"][0]
    contract["protocols"] = {
        "parallel-score": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": ["score"],
                "required_artifacts": [],
                "artifact_validators": [],
            },
        }
    }
    contract["components"] = [
        {
            **copy.deepcopy(base),
            "id": f"primary.score-{index}",
            "name": f"score-{index}",
            "owner": role,
            "proposers": [role],
            "protocol": "parallel-score",
            "result": {
                "kind": "metric",
                "metric": "score",
                "direction": "MAXIMIZE",
                "target": "1",
            },
        }
        for index, role in enumerate(("alpha", "beta"), 1)
    ]
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": "2",
        "display_only": True,
    }
    contract["budget"]["max_experiments"] = 2
    if runtime_prefix is not None:
        contract["runtime"]["conda_env"] = str(runtime_prefix)
        payload["execution"]["runtime_prefix"] = str(runtime_prefix)
    if read_roots:
        contract["safety"]["read_roots"] = [str(root) for root in read_roots]
        payload["execution"]["read_roots"] = [str(root) for root in read_roots]
    response = host.handle(
        ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=payload,
        )
    )
    assert response.status == "accepted", response


def test_workflow_host_freezes_and_forwards_provider_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "provider-environment"
    run_id = "phase8-provider-environment"
    secret = "startup-api-key"
    supplied = {"ANTHROPIC_API_KEY": secret}
    agent_host = _ControlledAgentHost(Path(__file__).resolve())
    agent_host.settings_path = None
    host = WorkflowHost(
        state_root,
        claude_agent_host=agent_host,  # type: ignore[arg-type]
        claude_model=_MODEL,
        provider_environment=supplied,
    )
    assert None not in host.agent_runtime_files
    supplied["ANTHROPIC_API_KEY"] = "mutated-caller-value"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mutated-ambient-value")
    try:
        host.initialize()
        _create_run(host, run_id)

        for _ in range(10):
            host.tick()
            if agent_host.provider_environments:
                break

        assert agent_host.provider_environments
        assert all(
            item == {"ANTHROPIC_API_KEY": secret}
            for item in agent_host.provider_environments
        )
        assert secret not in json.dumps(host.configuration)
    finally:
        host.close()


def test_workflow_host_forwards_each_runs_frozen_runtime_and_read_roots(
    tmp_path: Path,
) -> None:
    agent_host = _ControlledAgentHost(Path(__file__).resolve())
    runtime = Path(sys.prefix).resolve()
    first_data, second_data = tmp_path / "first-data", tmp_path / "second-data"
    first_data.mkdir()
    second_data.mkdir()
    expected = {
        "phase8-runtime-first": (runtime, (first_data,)),
        "phase8-runtime-second": (None, (second_data,)),
    }
    host = WorkflowHost(
        tmp_path / "per-run-runtime",
        claude_agent_host=agent_host,  # type: ignore[arg-type]
        claude_model=_MODEL,
    )
    try:
        host.initialize()
        for run_id, (prefix, roots) in expected.items():
            _create_run(host, run_id, runtime_prefix=prefix, read_roots=roots)
        for _ in range(10):
            host.tick()
            if {handle.request.run_id for handle in agent_host.started} == set(expected):
                break
        assert {handle.request.run_id for handle in agent_host.started} == set(expected)
        for handle in agent_host.started:
            assert (handle.runtime_prefix, handle.read_roots) == expected[handle.request.run_id]
            execution = host._run_config(handle.request.run_id)["execution"]
            assert (None if handle.runtime_prefix is None else str(handle.runtime_prefix)) == execution["runtime_prefix"]
            assert tuple(map(str, handle.read_roots)) == tuple(execution["read_roots"])
    finally:
        host.close()


def test_workflow_host_freezes_one_exact_model_identity_in_configuration(
    tmp_path: Path,
) -> None:
    plain = WorkflowHost(
        tmp_path / "plain",
        claude_agent_host=_ControlledAgentHost(Path(__file__).resolve()),  # type: ignore[arg-type]
        claude_model=_MODEL,
    )
    extended = WorkflowHost(
        tmp_path / "extended",
        claude_agent_host=_ControlledAgentHost(Path(__file__).resolve()),  # type: ignore[arg-type]
        claude_model=f"{_MODEL}[1m]",
    )
    try:
        assert plain.claude_model == _MODEL
        assert extended.claude_model == f"{_MODEL}[1m]"
        assert plain.agent_configuration["agent_model"] == _MODEL
        assert extended.agent_configuration["agent_model"] == f"{_MODEL}[1m]"
        assert "agent_model_selector" not in plain.agent_configuration
        assert "agent_model_selector" not in extended.agent_configuration
        assert (
            plain.version.runtime_environment_id
            == extended.version.runtime_environment_id
        )
        assert plain.version.configuration_id != extended.version.configuration_id
    finally:
        plain.close()
        extended.close()


def test_workflow_host_dispatches_one_exact_model_identity(
    tmp_path: Path,
) -> None:
    selector = f"{_MODEL}[1m]"
    agent_host = _ControlledAgentHost(
        Path(__file__).resolve(), model_identity=selector
    )
    host = WorkflowHost(
        tmp_path / "selector-dispatch",
        claude_agent_host=agent_host,  # type: ignore[arg-type]
        claude_model=selector,
    )
    try:
        host.initialize()
        _create_run(host, "phase8-selector-dispatch")
        for _ in range(10):
            host.tick()
            if agent_host.started:
                break

        assert agent_host.started
        assert all(
            handle.request.required_model_identity == selector
            for handle in agent_host.started
        )
        assert all(
            handle.response.model_identity == selector for handle in agent_host.started
        )
    finally:
        host.close()


def test_rendezvous_answer_waits_while_target_participant_process_is_open(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "target-process-busy"
    run_id = "phase8-target-process-busy"
    agent_host = _ControlledAgentHost(Path(__file__).resolve())
    host = WorkflowHost(
        state_root,
        claude_agent_host=agent_host,  # type: ignore[arg-type]
        claude_model=_MODEL,
    )
    host.runner = _BlockingRunner()  # type: ignore[assignment]
    try:
        host.initialize()
        _create_run(host, run_id)

        for _ in range(20):
            host.tick()
            open_attempts = _open_attempts(host, run_id)
            alpha_proposal = [
                row
                for row in open_attempts
                if str(row["attempt_id"]).startswith(f"{run_id}--proposal-1-1--")
            ]
            beta_process = [
                row
                for row in open_attempts
                if _is_scientific_process_attempt(host, row)
                and row["experiment_id"] == f"{run_id}--component-2--experiment"
            ]
            if alpha_proposal and beta_process:
                break
        else:
            raise AssertionError(
                "beta PROCESS did not start while alpha proposal remained in flight"
            )

        agent_host.release_alpha_proposal = True
        host.tick()

        open_attempts = _open_attempts(host, run_id)
        beta_process = [
            row
            for row in open_attempts
            if _is_scientific_process_attempt(host, row)
            and row["experiment_id"] == f"{run_id}--component-2--experiment"
        ]
        answer_attempts = _answer_attempts(open_attempts)

        assert len(beta_process) == 1
        assert answer_attempts == (), (
            "beta already owns an OPEN PROCESS effect, so its ANSWER_RENDEZVOUS "
            "effect must remain locally deferred"
        )
    finally:
        host.close()


def test_open_rendezvous_answer_prevents_target_ready_process_start(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "answer-blocks-ready-process"
    run_id = "phase8-answer-blocks-ready-process"
    agent_host = _ControlledAgentHost(Path(__file__).resolve())
    runner = _BlockingRunner()
    host = WorkflowHost(
        state_root,
        claude_agent_host=agent_host,  # type: ignore[arg-type]
        claude_model=_MODEL,
    )
    host.runner = runner  # type: ignore[assignment]
    try:
        host.initialize()
        _create_run(host, run_id)

        for _ in range(20):
            host.tick()
            beta = host.store.latest_transition(f"{run_id}--component-2--experiment")
            if (
                beta is not None
                and beta["state"] == "READY"
                and any(
                    str(row["attempt_id"]).startswith(
                        f"{run_id}--proposal-1-1--"
                    )
                    for row in _open_attempts(host, run_id)
                )
            ):
                break
        else:
            raise AssertionError(
                "beta did not reach READY while alpha proposal remained in flight"
            )
        assert _started_scientific_processes(host, run_id, runner) == ()

        agent_host.release_alpha_proposal = True
        host.tick()

        open_attempts = _open_attempts(host, run_id)
        assert len(_answer_attempts(open_attempts)) == 1
        assert not any(
            _is_scientific_process_attempt(host, row) for row in open_attempts
        )
        assert _started_scientific_processes(host, run_id, runner) == ()
        beta = host.store.latest_transition(f"{run_id}--component-2--experiment")
        assert beta is not None and beta["state"] == "READY"
    finally:
        host.close()


def test_open_rendezvous_answer_prevents_target_lost_process_retry(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "answer-blocks-process-retry"
    run_id = "phase8-answer-blocks-process-retry"
    first_agent = _ControlledAgentHost(Path(__file__).resolve())
    first = WorkflowHost(
        state_root,
        claude_agent_host=first_agent,  # type: ignore[arg-type]
        claude_model=_MODEL,
    )
    first.runner = _BlockingRunner()  # type: ignore[assignment]
    try:
        first.initialize()
        _create_run(first, run_id)
        for _ in range(20):
            first.tick()
            open_attempts = _open_attempts(first, run_id)
            if (
                any(
                    str(row["attempt_id"]).startswith(
                        f"{run_id}--proposal-1-1--"
                    )
                    for row in open_attempts
                )
                and any(
                    _is_scientific_process_attempt(first, row)
                    and row["experiment_id"] == f"{run_id}--component-2--experiment"
                    for row in open_attempts
                )
            ):
                break
        else:
            raise AssertionError(
                "beta PROCESS did not start before simulated Host loss"
            )
    finally:
        first.close()

    resumed_agent = _ControlledAgentHost(Path(__file__).resolve())
    resumed_agent.release_alpha_proposal = True
    resumed_runner = _BlockingRunner()
    resumed = WorkflowHost(
        state_root,
        claude_agent_host=resumed_agent,  # type: ignore[arg-type]
        claude_model=_MODEL,
    )
    resumed.runner = resumed_runner  # type: ignore[assignment]
    try:
        resumed.initialize()
        recovered_processes = _scientific_process_attempts(resumed, run_id)
        assert [
            (row["attempt_ordinal"], row["state"], row["terminal_reason"])
            for row in recovered_processes
        ] == [(1, "CLOSED", "LOST")]

        for _ in range(5):
            resumed.tick()
            open_attempts = _open_attempts(resumed, run_id)
            if _answer_attempts(open_attempts):
                break
        else:
            raise AssertionError("rendezvous answer did not become OPEN after recovery")

        assert len(_answer_attempts(open_attempts)) == 1
        assert not any(
            _is_scientific_process_attempt(resumed, row) for row in open_attempts
        )
        assert _started_scientific_processes(
            resumed, run_id, resumed_runner
        ) == ()
        processes = _scientific_process_attempts(resumed, run_id)
        assert [row["attempt_ordinal"] for row in processes] == [1]
    finally:
        resumed.close()
