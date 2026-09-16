from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import hypoloom.agent_budget as agent_budget_module
import hypoloom.claude_agent_host as claude_agent_host_module
import hypoloom.workflow_host as workflow_host_module
from hypoloom.agent_aci import (
    AgentBudget,
    AgentCommand,
    AgentRequest,
    AgentResponse,
    with_output_digest,
)
from hypoloom.agent_sessions import AgentSessions, PreparedSessionWorkspace
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.compute_resources import (
    ComputeCapabilities,
    GpuCapability,
    LocalComputeResources,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AttemptTerminalReason, ResearchContract
from hypoloom.execution_workspace import ExecutionWorkspace
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import (
    ProcessObservation,
    ProcessRequest,
    process_group_exists,
)
from hypoloom.workflow_host import WorkflowHost


_GPU_UUID = "GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("failure_code", ("provider_not_success", "nonzero_exit"))
def test_provider_budget_subtype_is_terminal_independent_of_exit_code(
    failure_code: str,
) -> None:
    details = {"diagnostics": {"provider_subtype": "error_max_budget_usd"}}
    assert workflow_host_module._agent_retry_class(
        failure_code, details, retryable=True,
    ) is None


@pytest.mark.parametrize("failure_code", ("provider_not_success", "nonzero_exit"))
def test_provider_structured_output_subtype_uses_model_retry_budget(
    failure_code: str,
) -> None:
    details = {
        "diagnostics": {
            "provider_subtype": "error_max_structured_output_retries",
        },
    }
    assert workflow_host_module._agent_retry_class(
        failure_code, details, retryable=True,
    ) == "MODEL"


def _payload(
    run_id: str,
    *,
    roles: tuple[str, ...] = ("analyst", "critic"),
    component_count: int = 2,
    allow_gpu: bool = False,
    delay_seconds: float = 0.4,
    runtime_prefix: Path | None = None,
) -> dict[str, object]:
    admission = admit_profile(_REPOSITORY / "tests/fixtures/profiles/component-demo", run_id=run_id,
                              allow_gpu=allow_gpu, delay_seconds=delay_seconds)
    assert admission.admitted and admission.create_run_payload is not None
    payload = admission.create_run_payload
    contract = payload["research_contract"]
    template_role = contract["roles"][0]
    contract["roles"] = [{**template_role, "id": role} for role in roles]
    base = contract["components"][0]
    contract["protocols"] = {"parallel-score": {"result_kind": "metric", "requirements": {
        "required_metrics": ["score"], "required_artifacts": [], "artifact_validators": []}}}
    contract["components"] = [
        {**copy.deepcopy(base), "id": f"primary.score-{index}", "name": f"score-{index}",
         "owner": roles[(index - 1) % len(roles)],
         "proposers": [roles[(index - 1) % len(roles)]],
         "protocol": "parallel-score",
         "result": {"kind": "metric", "metric": "score", "direction": "MAXIMIZE", "target": "1"}}
        for index in range(1, component_count + 1)
    ]
    contract["goal"]["summary_metric"] = ({"name": "score", "direction": "MAXIMIZE",
        "target": "1", "display_only": True} if component_count == 1 else
        {"name": "required_component_completion", "direction": "MAXIMIZE",
         "target": str(component_count), "display_only": True})
    contract["budget"]["max_experiments"] = component_count
    if runtime_prefix is not None:
        runtime = str(runtime_prefix.resolve())
        contract["runtime"]["conda_env"] = runtime
        payload["execution"]["runtime_prefix"] = runtime
    return payload


def _create(host: WorkflowHost, run_id: str, **payload_changes: object) -> None:
    roles = tuple(payload_changes.pop("expert_roles", ["analyst", "critic"]))
    component_count = int(payload_changes.pop("experiment_budget", 2))
    execution = dict(payload_changes.pop("execution", {}))
    if payload_changes:
        raise AssertionError(f"unknown component test payload changes: {payload_changes}")
    allow_gpu = bool(execution.get("allow_gpu", False))
    payload = _payload(run_id, roles=roles, component_count=component_count,
        allow_gpu=allow_gpu, delay_seconds=float(execution.get("delay_seconds", 0.4)),
        runtime_prefix=host.compute_resources.gpu_runtime_prefix if allow_gpu else None)
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


def _is_scientific_process_attempt(
    host: WorkflowHost, row: dict[str, object]
) -> bool:
    if row["kind"] != "PROCESS":
        return False
    spec = host.store.find_fact(
        str(row["run_id"]), "ExperimentPreregistered", str(row["experiment_id"])
    )
    return spec is not None and row["execution_id"] == spec["execution_id"]


def _process_attempts(host: WorkflowHost, run_id: str):
    return tuple(
        row
        for row in host.store.list_attempts()
        if row["run_id"] == run_id and _is_scientific_process_attempt(host, row)
    )


def _running_processes(host: WorkflowHost, run_id: str):
    scientific_ids = {
        str(row["attempt_id"]) for row in _process_attempts(host, run_id)
    }
    return {
        attempt_id: handle
        for attempt_id, handle in host.running.items()
        if attempt_id in scientific_ids
    }


def _started_process_requests(
    host: WorkflowHost, run_id: str, requests: list[ProcessRequest]
) -> tuple[ProcessRequest, ...]:
    execution_ids = {
        str(row["execution_id"])
        for row in host.store.list_facts(run_id, "ExperimentPreregistered")
    }
    return tuple(
        request for request in requests if request.execution_id in execution_ids
    )


def _running_gpu_uuids(host: WorkflowHost, run_id: str) -> tuple[str, ...]:
    uuids = []
    for attempt_id in _running_processes(host, run_id):
        attempt = host.store.get_attempt(attempt_id)
        assert attempt is not None
        spec = host.store.find_fact(
            run_id, "ExperimentPreregistered", attempt["experiment_id"]
        )
        assert spec is not None
        gpu_uuid = spec["payload"]["execution_contract"]["resource_grant"][
            "gpu_uuid"
        ]
        assert isinstance(gpu_uuid, str)
        uuids.append(gpu_uuid)
    return tuple(uuids)


def _assert_clean_oracle(state_root: Path, run_id: str) -> None:
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.duplicate_effect_count == 0
    assert report.owned_process_residue == ()


class _ScriptedClaudeHost(ClaudeAgentHost):
    """Real adapter/bwrap lifecycle with a local executable, never native CC.

    Full CC intentionally strips STUB_* from its environment. Only these test
    response envelopes are embedded in the already read-only executable fixture;
    relay auth stays Host-side and points exclusively at loopback port 9.
    Follow actual argv: current final text invokes the real Host Stop client;
    a retained --json-schema invocation keeps its legacy envelope.
    """

    _fixture_fields = frozenset({
        "STUB_FIRST_ATTEMPT_ID", "STUB_FIRST_OUTPUT", "STUB_SECOND_OUTPUT",
        "STUB_VALID_OUTPUT",
    })

    def __init__(self, root: Path, mode: str, values: dict[str, str] | None = None) -> None:
        root.mkdir(parents=True)
        stub = root / "claude-stub"
        stub.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, shlex, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "prompt = json.load(sys.stdin)\n"
            "request = prompt['agent_request_metadata']\n"
            f"mode = {mode!r}\n"
            f"values = {values or {}!r}\n"
            "assert not any(name.startswith('STUB_') for name in os.environ)\n"
            "assert os.environ.get('ANTHROPIC_API_KEY') != 'phase8-dummy-host-key'\n"
            "if mode == 'hang':\n"
            "    while True: time.sleep(1)\n"
            "if mode == 'attempt-aware':\n"
            "    key = 'STUB_FIRST_OUTPUT' if request['attempt_id'] == values['STUB_FIRST_ATTEMPT_ID'] else 'STUB_SECOND_OUTPUT'\n"
            "else: key = 'STUB_VALID_OUTPUT'\n"
            "session_flag = '--session-id' if '--session-id' in sys.argv else '--resume'\n"
            "session = sys.argv[sys.argv.index(session_flag) + 1]\n"
            "output = json.loads(values[key])\n"
            "if '--json-schema' not in sys.argv:\n"
            "    output['result'] = json.dumps(output.pop('structured_output'))\n"
            "    settings = json.loads(sys.argv[sys.argv.index('--settings') + 1])\n"
            "    hook = settings['hooks']['Stop'][0]['hooks'][0]\n"
            "    payload = {'session_id': session, 'last_assistant_message': output['result']}\n"
            "    validated = subprocess.run(shlex.split(hook['command']), input=json.dumps(payload).encode(), capture_output=True, check=True, timeout=hook['timeout'])\n"
            "    assert json.loads(validated.stdout) == {}\n"
            # Late-success tests need valid output followed by delayed delivery,
            # not an already-expired Stop request. Keep their original delay.
            "if mode == 'delayed': time.sleep(0.9)\n"
            "transcript = Path('/home/agent/.claude/projects/-workspace') / (session + '.jsonl')\n"
            "with transcript.open('a') as stream:\n"
            "    stream.write(json.dumps({'type': 'user', 'sessionId': session, 'message': {'role': 'user', 'content': json.dumps(prompt)}}) + '\\n')\n"
            "    stream.write(json.dumps({'type': 'assistant', 'sessionId': session, 'message': {'role': 'assistant', 'content': output.get('result', 'Scripted lifecycle test response; not native CC.')}}) + '\\n')\n"
            "print(json.dumps(output), flush=True)\n",
            encoding="utf-8",
        )
        stub.chmod(0o700)
        settings = root / "settings.json"
        settings.write_text(json.dumps({"env": {
            "ANTHROPIC_API_KEY": "phase8-dummy-host-key",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
        }}) + "\n", encoding="utf-8")
        settings.chmod(0o600)
        super().__init__(
            claude_executable=stub, settings_path=settings,
            allowed_environment_names=tuple(self._fixture_fields) + (
                "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
            ),
            terminate_grace_seconds=0.05, kill_grace_seconds=1.0,
        )


def _hanging_claude_stub(root: Path) -> ClaudeAgentHost:
    return _ScriptedClaudeHost(root, "hang")


def _delayed_claude_stub(root: Path, run_id: str) -> ClaudeAgentHost:
    return _ScriptedClaudeHost(root, "delayed", {
        "STUB_VALID_OUTPUT": json.dumps(_proposal_provider_output(run_id)),
    })


def _attempt_aware_claude_stub(
    root: Path, first_attempt_id: str,
    first_output: dict[str, object], second_output: dict[str, object],
) -> ClaudeAgentHost:
    return _ScriptedClaudeHost(root, "attempt-aware", {
        "STUB_FIRST_ATTEMPT_ID": first_attempt_id,
        "STUB_FIRST_OUTPUT": json.dumps(first_output),
        "STUB_SECOND_OUTPUT": json.dumps(second_output),
    })


def test_three_roles_have_isolated_durable_running_agent_attempts_under_global_cap(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "parallel-agent"
    run_id = "phase8-agent-parallel"
    waiting_run_id = "phase8-agent-cap-waiter"
    roles = ("analyst", "critic", "replicator")
    claude = _hanging_claude_stub(tmp_path / "provider")
    host = WorkflowHost(
        state_root,
        claude_agent_host=claude,
        claude_model="stub-model",
        agent_budget=AgentBudget(
            max_turns=1,
            max_tokens=128,
            max_output_bytes=4096,
            wall_seconds=30,
            max_cost="0.01",
        ),
    )
    maximum_open_external = 0
    process_group_ids: tuple[int, ...] = ()
    try:
        host.initialize()
        _create(
            host,
            run_id,
            expert_roles=list(roles),
            experiment_budget=3,
        )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            open_external = host.store.inspect(
                "SELECT * FROM execution_attempts "
                "WHERE state='OPEN' AND kind IN ('AGENT','PROCESS') "
                "ORDER BY attempt_id"
            )
            maximum_open_external = max(maximum_open_external, len(open_external))
            assert len(open_external) <= 3
            if len(open_external) == 3 and len(host.running_agents) == 3:
                break
            time.sleep(0.005)
        else:
            raise AssertionError(
                "three proposal Agent attempts never became durably OPEN and running"
            )

        open_agents = host.store.inspect(
            "SELECT a.*,t.payload_json AS transition_payload_json "
            "FROM execution_attempts a "
            "JOIN workflow_transitions t "
            "ON t.workflow_id=a.workflow_id AND t.command_id=a.command_id "
            "WHERE a.run_id=? AND a.kind='AGENT' AND a.state='OPEN' "
            "ORDER BY json_extract(t.payload_json,'$.component_id')",
            (run_id,),
        )
        assert len(open_agents) == 3
        assert {row["kind"] for row in open_agents} == {"AGENT"}
        assert len({row["attempt_id"] for row in open_agents}) == 3
        assert len({row["command_id"] for row in open_agents}) == 3
        assert len({row["process_group_id"] for row in open_agents}) == 3

        process_group_ids = tuple(int(row["process_group_id"]) for row in open_agents)
        for expected_index, (expected_role, row) in enumerate(
            zip(roles, open_agents, strict=True), start=1
        ):
            attempt_id = str(row["attempt_id"])
            transition_payload = json.loads(row["transition_payload_json"])
            request = AgentRequest.from_dict(
                json.loads(row["observation_json"])["request"]
            )
            handle = host.running_agents[attempt_id]

            assert transition_payload["component_id"] == f"primary.score-{expected_index}"
            assert transition_payload["role"] == expected_role
            assert request.command is AgentCommand.PROPOSE_HYPOTHESIS
            assert request.role == expected_role
            assert request.attempt_id == attempt_id == handle.request.attempt_id
            assert request.command_id == row["command_id"]
            assert request.owner_workflow_id == run_id
            assert handle.process_group_id == row["process_group_id"]
            assert handle.returncode is None
            assert process_group_exists(handle.process_group_id)
            binding = AgentSessions.binding(request)
            session_index = handle.launch_argv.index("--session-id")
            assert handle.launch_argv[session_index + 1] == binding["session_id"]
            assert handle.tool_workspace.binding == binding
        assert len({
            handle.tool_workspace.binding["session_id"]
            for handle in host.running_agents.values()
        }) == len(roles)

        # A fourth eligible run cannot bypass the aggregate AGENT+PROCESS cap.
        _create(
            host,
            waiting_run_id,
            expert_roles=["observer"],
            experiment_budget=1,
        )
        for _ in range(5):
            host.tick()
            open_external = host.store.inspect(
                "SELECT run_id,kind FROM execution_attempts "
                "WHERE state='OPEN' AND kind IN ('AGENT','PROCESS')"
            )
            maximum_open_external = max(maximum_open_external, len(open_external))
            assert len(open_external) <= 3
            assert {row["run_id"] for row in open_external} == {run_id}

        assert maximum_open_external == 3

        for target in (run_id, waiting_run_id):
            head = host.store.latest_transition(target)
            assert head is not None
            cancelled = host.handle(
                ControlRequest(
                    schema_version=1,
                    request_id=f"cancel-{target}",
                    expected_revision=int(head["revision"]),
                    command="CANCEL",
                    payload={"run_id": target, "cause": "test_cleanup"},
                )
            )
            assert cancelled.status == "accepted", cancelled

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            heads = [host.store.latest_transition(target) for target in (run_id, waiting_run_id)]
            if (
                all(head is not None and head["state"] == "CANCELLED" for head in heads)
                and not host.running_agents
            ):
                break
            time.sleep(0.005)
        else:
            raise AssertionError("cancel did not close the bounded Agent attempts")

        assert not host.store.inspect(
            "SELECT 1 FROM execution_attempts WHERE state='OPEN'"
        )
    finally:
        host.close()

    assert all(not process_group_exists(pgid) for pgid in process_group_ids)


@pytest.mark.parametrize("valid_on_second", (True, False))
@pytest.mark.parametrize(
    ("invalid_lineage", "expected_message"),
    (
        (
            {"parent_hypothesis_id": None, "relation": "challenge"},
            "parent_hypothesis_id and relation must be supplied together",
        ),
        (
            {
                "parent_hypothesis_id": "dependency-component-hypothesis",
                "relation": "derived_from",
            },
            "missing parent hypothesis 'dependency-component-hypothesis'",
        ),
    ),
)
def test_invalid_candidate_uses_one_bounded_retry_with_previous_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_on_second: bool,
    invalid_lineage: dict[str, str | None],
    expected_message: str,
) -> None:
    shape = "paired" if invalid_lineage["parent_hypothesis_id"] else "asymmetric"
    run_id = (
        f"candidate-retry-{shape}-"
        f"{'success' if valid_on_second else 'final'}"
    )
    host = WorkflowHost(tmp_path / run_id)
    requests: list[AgentRequest] = []

    def invoke(request: AgentRequest) -> AgentResponse | bytes:
        requests.append(request)
        response = FakeAgent().invoke(request)
        assert isinstance(response, AgentResponse)
        if (
            request.command is AgentCommand.PROPOSE_HYPOTHESIS
            and (request.attempt_ordinal == 1 or not valid_on_second)
        ):
            candidate = dict(response.typed_candidate or {})
            candidate.update(invalid_lineage)
            return with_output_digest(
                replace(response, typed_candidate=candidate, output_digest="")
            )
        return response

    monkeypatch.setattr(host.agent, "invoke", invoke)
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        for _ in range(20):
            host.tick()
            proposal_attempts = [
                row
                for row in host.store.list_attempts(workflow_id=run_id)
                if row["kind"] == "AGENT"
            ]
            final_failure = host.store.inspect(
                "SELECT 1 FROM workflow_transitions WHERE run_id=? "
                "AND json_extract(payload_json,'$.agent_final_failure')=1",
                (run_id,),
            )
            if len(proposal_attempts) == 2 and (
                host.store.list_facts(run_id, "HypothesisProposed") or final_failure
            ):
                break
        else:
            raise AssertionError("candidate retry did not reach its bounded result")

        assert [request.attempt_ordinal for request in requests] == [1, 2]
        assert len({request.command_id for request in requests}) == 1
        retry_input = next(
            item
            for item in requests[1].input_artifacts
            if item.ref.endswith("--candidate-validation-error")
        )
        assert json.loads(retry_input.content_bytes()) == {
            "source_attempt_id": requests[0].attempt_id,
            "validation_error": {
                "code": "invalid_candidate",
                "message": expected_message,
            },
        }
        attempts = sorted(
            (
                row
                for row in host.store.list_attempts(workflow_id=run_id)
                if row["kind"] == "AGENT"
            ),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        assert [row["terminal_reason"] for row in attempts] == [
            "FAILED",
            "SUCCEEDED" if valid_on_second else "FAILED",
        ]
        facts = host.store.list_facts(run_id, "HypothesisProposed")
        failures = host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? "
            "AND json_extract(payload_json,'$.agent_final_failure')=1",
            (run_id,),
        )
        assert len(facts) == (1 if valid_on_second else 0)
        assert len(failures) == (0 if valid_on_second else 1)

        for _ in range(3):
            host.tick()
        assert len(
            [
                row
                for row in host.store.list_attempts(workflow_id=run_id)
                if row["kind"] == "AGENT"
                and json.loads(row["observation_json"])["request"]["command"]
                == AgentCommand.PROPOSE_HYPOTHESIS.value
            ]
        ) == 2
    finally:
        host.close()


def test_infrastructure_failure_does_not_consume_model_correction_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "mixed-agent-retry-budgets"
    host = WorkflowHost(tmp_path / run_id)
    requests: list[AgentRequest] = []
    finish = host._finish_fake_agent

    def invoke(request: AgentRequest) -> AgentResponse | bytes:
        requests.append(request)
        response = FakeAgent().invoke(request)
        assert isinstance(response, AgentResponse)
        if (request.command is AgentCommand.PROPOSE_HYPOTHESIS
                and request.attempt_ordinal == 2):
            candidate = dict(response.typed_candidate or {})
            candidate.update({
                "parent_hypothesis_id": "dependency-component-hypothesis",
                "relation": "derived_from",
            })
            return with_output_digest(
                replace(response, typed_candidate=candidate, output_digest="")
            )
        return response

    def fail_first_transport(request: AgentRequest, raw: AgentResponse | bytes) -> None:
        if (request.command is AgentCommand.PROPOSE_HYPOTHESIS
                and request.attempt_ordinal == 1):
            host._agent_failure(
                request, AttemptTerminalReason.TIMED_OUT, "timed_out",
                retryable=True, details={"failure_stage": "provider_process"},
            )
            return
        finish(request, raw)

    monkeypatch.setattr(host.agent, "invoke", invoke)
    monkeypatch.setattr(host, "_finish_fake_agent", fail_first_transport)
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        budget = payload["research_contract"]["budget"]
        budget.update({
            "max_retries": 1,
            "max_infrastructure_retries": 2,
            "max_process_lost_retries": 2,
        })
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        for _ in range(30):
            host.tick()
            if host.store.list_facts(run_id, "HypothesisProposed"):
                break
        else:
            raise AssertionError("mixed retry chain did not reach a valid hypothesis")

        attempts = sorted(
            (row for row in host.store.list_attempts(workflow_id=run_id)
             if row["kind"] == "AGENT"),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        assert [row["terminal_reason"] for row in attempts] == [
            "TIMED_OUT", "FAILED", "SUCCEEDED",
        ]
        first, second, _ = (
            json.loads(row["observation_json"]) for row in attempts
        )
        assert first["retry_class"] == "INFRASTRUCTURE"
        assert second["retry_class"] == "MODEL"
        assert second["failure_stage"] == "host_candidate_validation"
        assert "rejected_response" in second and "response" not in second
        assert any(
            item.ref.endswith("--candidate-validation-error")
            for item in requests[2].input_artifacts
        )
    finally:
        host.close()

    report = Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id)
    assert report.ok, report.issues


def test_explicit_zero_retry_budgets_allow_only_the_initial_attempt() -> None:
    payload = _payload("zero-retry-limits", roles=("analyst",), component_count=1)
    budget = payload["research_contract"]["budget"]
    budget.update({
        "max_retries": 0,
        "max_infrastructure_retries": 0,
        "max_process_lost_retries": 0,
    })
    contract = ResearchContract.from_mapping(payload["research_contract"])

    assert WorkflowHost._agent_attempt_limits(contract) == (1, 1)
    assert WorkflowHost._effect_attempt_cap(contract, "agent") == 1
    assert WorkflowHost._effect_attempt_cap(contract, "process") == 1


def test_multiple_model_corrections_are_each_durable_and_causally_linked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "recorded-multiple-model-corrections"
    host = WorkflowHost(tmp_path / run_id)
    requests: list[AgentRequest] = []

    def invoke(request: AgentRequest) -> AgentResponse | bytes:
        requests.append(request)
        response = FakeAgent().invoke(request)
        assert isinstance(response, AgentResponse)
        if (request.command is AgentCommand.PROPOSE_HYPOTHESIS
                and request.attempt_ordinal <= 3):
            candidate = dict(response.typed_candidate or {})
            candidate.update({
                "parent_hypothesis_id": "dependency-component-hypothesis",
                "relation": "derived_from",
            })
            return with_output_digest(
                replace(response, typed_candidate=candidate, output_digest="")
            )
        return response

    monkeypatch.setattr(host.agent, "invoke", invoke)
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"].update({
            "max_retries": 3,
            "max_infrastructure_retries": 0,
            "max_process_lost_retries": 0,
        })
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        for _ in range(40):
            host.tick()
            if host.store.list_facts(run_id, "HypothesisProposed"):
                break
        else:
            raise AssertionError("model correction chain did not reach success")

        proposal_requests = [
            request for request in requests
            if request.command is AgentCommand.PROPOSE_HYPOTHESIS
        ]
        assert [request.attempt_ordinal for request in proposal_requests] == [
            1, 2, 3, 4,
        ]
        attempts = sorted(
            (
                row for row in host.store.list_attempts(workflow_id=run_id)
                if row["kind"] == "AGENT"
            ),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        assert [row["terminal_reason"] for row in attempts] == [
            "FAILED", "FAILED", "FAILED", "SUCCEEDED",
        ]
        observations = [
            json.loads(row["observation_json"]) for row in attempts
        ]
        for observation in observations[:3]:
            assert observation["retry_class"] == "MODEL"
            assert observation["failure_stage"] == "host_candidate_validation"
            assert "rejected_response" in observation
            assert "response" not in observation
        assert "response" in observations[3]
        for index, request in enumerate(proposal_requests[1:], start=1):
            correction = next(
                item for item in request.input_artifacts
                if item.ref.endswith("--candidate-validation-error")
            )
            assert json.loads(correction.content_bytes())["source_attempt_id"] == (
                proposal_requests[index - 1].attempt_id
            )
    finally:
        host.close()

    report = Oracle(
        host.state_root / "state.sqlite", host.state_root / "artifacts",
    ).audit(run_id)
    assert report.ok, report.issues


def test_profile_agent_budget_overrides_host_default_and_reserves_run_cap(
    tmp_path: Path,
) -> None:
    run_id = "phase8-profile-agent-budget"
    claude = _hanging_claude_stub(tmp_path / "provider-budget")
    host = WorkflowHost(
        tmp_path / "profile-budget",
        claude_agent_host=claude,
        claude_model="stub-model",
        agent_budget=AgentBudget(
            max_turns=9,
            max_tokens=999,
            max_output_bytes=9999,
            wall_seconds=99,
            max_cost="9.99",
        ),
    )
    try:
        host.initialize()
        payload = _payload(
            run_id,
            roles=("analyst", "critic", "replicator"),
            component_count=3,
        )
        payload["research_contract"]["budget"].update(
            {
                "agent_invocation": {
                    "max_turns": 2,
                    "max_tokens": 100,
                    "max_output_bytes": 4096,
                    "wall_seconds": 30,
                    "max_cost": "0.25",
                    "token_enforcement_mode": "enforce",
                },
                "run_tokens": 200,
                "per_expert_tokens": 100,
                "max_turns": 10,
            }
        )
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            attempts = host.store.inspect(
                "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT'",
                (run_id,),
            )
            if len(attempts) == 2 and len(host.running_agents) == 2:
                break
            time.sleep(0.005)
        else:
            raise AssertionError("profile run cap did not reserve exactly two parallel dispatches")

        requests = [
            json.loads(row["observation_json"])["request"] for row in attempts
        ]
        assert {item["budget"]["max_tokens"] for item in requests} == {100}
        assert {item["budget"]["max_turns"] for item in requests} == {2}
        for _ in range(3):
            host.tick()
        assert len(host.store.inspect(
            "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT'", (run_id,))) == 2
        status = host.handle(ControlRequest(1, f"status-{run_id}", None, "STATUS", {"run_id": run_id}))
        assert status.status == "accepted" and status.result is not None
        usage = status.result["agent_usage"]
        assert usage["profile_cumulative"]["tokens"] == {
            "limit": 200,
            "charged_or_reserved": 200,
            "remaining": 0,
        }
        assert usage["remaining_declared_limits"]["tokens"] == 0
        assert usage["declared_limits"]["tokens"] == 200

        released_id, released = next(iter(host.running_agents.items()))
        host.running_agents.pop(released_id)
        claude.cancel(released); claude.cleanup(released)
        host._agent_failure(
            released.request,
            AttemptTerminalReason.FAILED,
            "provider_error",
            retryable=False,
            details={"diagnostics": {"provider_accounting": {
                "token_usage": {"total_tokens": 40},
                "turn_count": 1,
                "cost": "0.20",
                "cost_currency": "USD",
            }}},
        )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            attempts = host.store.inspect(
                "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT'",
                (run_id,),
            )
            if len(attempts) == 3 and len(host.running_agents) == 2:
                break
            time.sleep(0.005)
        else:
            raise AssertionError("released reservation did not admit the waiting role")
        newest = next(
            AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
            for row in attempts
            if json.loads(row["observation_json"])["request"]["role"] == "replicator"
        )
        assert newest.budget.max_tokens == 60
        assert newest.role == "replicator"
    finally:
        host.close()


def test_observe_mode_treats_run_tokens_as_reported_threshold_not_dispatch_cap(
    tmp_path: Path,
) -> None:
    run_id = "phase8-observe-token-threshold"
    host = WorkflowHost(
        tmp_path / run_id,
        claude_agent_host=_hanging_claude_stub(tmp_path / "provider-observe-budget"),
        claude_model="stub-model",
    )
    try:
        host.initialize()
        payload = _payload(
            run_id,
            roles=("analyst", "critic", "replicator"),
            component_count=3,
        )
        payload["research_contract"]["budget"].update({
            "agent_invocation": {
                "max_turns": 2,
                "max_tokens": 100,
                "max_output_bytes": 4096,
                "wall_seconds": 30,
                "max_cost": "0.25",
                "token_enforcement_mode": "observe",
            },
            "run_tokens": 100,
            "per_expert_tokens": 100,
            "max_turns": 10,
        })
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            attempts = host.store.inspect(
                "SELECT * FROM execution_attempts WHERE run_id=? AND kind='AGENT'",
                (run_id,),
            )
            if len(attempts) == 3 and len(host.running_agents) == 3:
                break
            time.sleep(0.005)
        else:
            raise AssertionError("observe mode did not admit calls beyond token thresholds")

        requests = [
            AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
            for row in attempts
        ]
        assert {request.budget.max_tokens for request in requests} == {100}
        assert {request.budget.token_enforcement_mode for request in requests} == {
            "observe"
        }
        status = host.handle(
            ControlRequest(1, f"status-{run_id}", None, "STATUS", {"run_id": run_id})
        )
        assert status.status == "accepted" and status.result is not None
        assert status.result["agent_usage"]["profile_cumulative"]["tokens"] == {
            "limit": 100,
            "charged_or_reserved": 300,
            "remaining": 0,
            "overage": 200,
        }
    finally:
        host.close()


def test_status_clamps_legacy_remaining_limits_to_role_budget(tmp_path: Path) -> None:
    run_id = "phase8-role-budget-status"
    host = WorkflowHost(
        tmp_path / run_id,
        claude_agent_host=_hanging_claude_stub(tmp_path / "provider-role-budget"),
        claude_model="stub-model",
    )
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["agent_invocation"].update({
            "max_turns": 2,
            "max_tokens": 100,
            "wall_seconds": 30,
        })
        payload["research_contract"]["roles"][0]["budget"].update({
            "per_expert_tokens": 100,
            "max_turns": 2,
        })
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not host.running_agents:
            host.tick(); time.sleep(0.005)
        assert len(host.running_agents) == 1

        status = host.handle(ControlRequest(1, f"status-{run_id}", None, "STATUS", {"run_id": run_id}))
        assert status.status == "accepted" and status.result is not None
        usage = status.result["agent_usage"]
        role = usage["profile_cumulative"]["roles"]["analyst"]
        assert role["tokens"] == {"limit": 100, "charged_or_reserved": 100, "remaining": 0}
        assert role["turns"] == {"limit": 2, "charged_or_reserved": 2, "remaining": 0}
        assert usage["remaining_declared_limits"]["tokens"] == 0
        assert usage["remaining_declared_limits"]["turns"] == 0
        assert usage["declared_limits"]["tokens"] == 100
        assert usage["declared_limits"]["turns"] == 2
    finally:
        host.close()


@pytest.mark.parametrize("invalid_cost", (None, "0.00"))
def test_create_run_rejects_missing_or_noncanonical_profile_agent_budget(
    tmp_path: Path,
    invalid_cost: str | None,
) -> None:
    host = WorkflowHost(tmp_path / f"invalid-new-budget-{invalid_cost}")
    try:
        host.initialize()
        payload = _payload("phase8-invalid-new-budget", roles=("analyst",), component_count=1)
        if invalid_cost is None:
            del payload["research_contract"]["budget"]["agent_invocation"]
        else:
            payload["research_contract"]["budget"]["agent_invocation"]["max_cost"] = invalid_cost
        response = host.handle(ControlRequest(
            1, f"create-invalid-{invalid_cost}", 0, "CREATE_RUN", payload))
        assert response.status == "rejected"
        assert response.error is not None and response.error["code"] == "invalid_request"
        assert host.store.get_run("phase8-invalid-new-budget") is None
    finally:
        host.close()


def test_create_run_rejects_null_profile_agent_budget(tmp_path: Path) -> None:
    run_id = "phase8-null-agent-budget"
    host = WorkflowHost(tmp_path / run_id)
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["agent_invocation"] = None
        response = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert response.status == "rejected"
        assert response.error is not None and response.error["code"] == "invalid_request"
        assert host.store.get_run(run_id) is None
    finally:
        host.close()


@pytest.mark.parametrize("case", ("negative", "global-null", "role-null"))
def test_create_run_rejects_invalid_cumulative_budget_before_persistence(
    tmp_path: Path,
    case: str,
) -> None:
    run_id = f"phase8-invalid-cumulative-{case}"
    host = WorkflowHost(tmp_path / run_id)
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        if case == "negative":
            payload["research_contract"]["budget"]["run_tokens"] = -1
        elif case == "global-null":
            payload["research_contract"]["budget"]["run_tokens"] = None
        else:
            payload["research_contract"]["roles"][0]["budget"]["per_expert_tokens"] = None
        response = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert response.status == "rejected"
        assert host.store.get_run(run_id) is None
    finally:
        host.close()


@pytest.mark.parametrize("scope", ("global", "role"))
def test_create_run_rejects_unknown_budget_fields_before_persistence(
    tmp_path: Path,
    scope: str,
) -> None:
    run_id = f"phase8-unknown-{scope}-budget"
    host = WorkflowHost(tmp_path / run_id)
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        if scope == "global":
            payload["research_contract"]["budget"]["run_token"] = 1
        else:
            payload["research_contract"]["roles"][0]["budget"]["per_expert_token"] = 1
        response = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert response.status == "rejected"
        assert response.error is not None and response.error["code"] == "invalid_request"
        assert host.store.get_run(run_id) is None
    finally:
        host.close()


def test_expired_profile_wallclock_closes_inconclusive_without_external_work(
    tmp_path: Path,
) -> None:
    run_id = "phase8-expired-wallclock"
    host = WorkflowHost(tmp_path / "expired-wallclock")
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1e-9
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created

        for _ in range(20):
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] == "COMPLETED":
                break
        else:
            raise AssertionError("expired run did not close deterministically")

        attempts = tuple(row for row in host.store.list_attempts() if row["run_id"] == run_id)
        assert attempts and {row["kind"] for row in attempts} == {"AGENT"}
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert all(json.loads(row["observation_json"])["failure_code"] == "profile_budget_exhausted" for row in attempts)
        assert not host.running and not host.running_agents
        verdict = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdict) == 1
        assert verdict[0]["payload"]["verdict"] == "INCONCLUSIVE"
    finally:
        host.close()


def test_profile_wallclock_cancels_an_already_running_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "phase8-wallclock-running-agent"
    claude = _hanging_claude_stub(tmp_path / "provider-wallclock")
    host = WorkflowHost(
        tmp_path / "wallclock-running-agent",
        claude_agent_host=claude,
        claude_model="stub-model",
    )
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 0.005
        payload["research_contract"]["budget"]["agent_invocation"]["wall_seconds"] = 30
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        # Control the profile clock, not Host state or the real child process.
        # A 180 ms real-time window can expire during dispatch on a loaded host.
        run = host.store.get_run(run_id)
        assert run is not None
        clock = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))

        class ProfileClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.astimezone(tz) if tz is not None else clock.replace(tzinfo=None)

        monkeypatch.setattr(agent_budget_module, "datetime", ProfileClock)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not host.running_agents:
            host.tick(); time.sleep(0.005)
        assert len(host.running_agents) == 1
        request = next(iter(host.running_agents.values())).request
        assert request.budget.wall_seconds == 18 < 30

        clock += timedelta(seconds=19)
        assert host._run_wallclock_expired(run_id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("expired running Agent did not close")
        assert not host.running_agents
        attempts = tuple(row for row in host.store.list_attempts() if row["run_id"] == run_id)
        assert attempts and all(row["state"] == "CLOSED" for row in attempts)
        cancelled = host.store.get_attempt(request.attempt_id)
        assert cancelled is not None and cancelled["terminal_reason"] == "TIMED_OUT"
        assert json.loads(cancelled["observation_json"])["failure_code"] == "profile_wallclock_exhausted"
        assert not host.store.inspect("SELECT 1 FROM execution_attempts WHERE run_id=? AND state='OPEN'", (run_id,))
    finally:
        host.close()


def _proposal_provider_output(
    run_id: str,
    *,
    attempt_ordinal: int = 1,
) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 7, "output_tokens": 11},
        "modelUsage": {"stub-model": {}},
        "structured_output": {
            "typed_candidate": {
                "component_id": "primary.score-1",
                "proposer_role": "analyst",
                "method_family_id": "late-family",
                "claim": "late provider response",
                "assumptions": [],
                "parent_hypothesis_id": None,
                "relation": None,
            },
            "declared_effects": [{
                "effect": "HYPOTHESIS_CANDIDATE_PROPOSED",
            }],
        },
    }


def test_invalid_provider_turn_usage_uses_infrastructure_exact_replay(
    tmp_path: Path,
) -> None:
    run_id = "phase8-provider-turn-usage-infrastructure-replay"
    command_id = f"{run_id}--r3--request_hypothesis_candidate"
    first_attempt_id = f"{run_id}--proposal-1-1--agent-1"
    invalid = _proposal_provider_output(run_id)
    invalid["num_turns"] = "not-an-integer"
    recovered = _proposal_provider_output(run_id, attempt_ordinal=2)
    host = WorkflowHost(
        tmp_path / "provider-turn-usage-infrastructure-replay",
        claude_agent_host=_attempt_aware_claude_stub(
            tmp_path / "provider-turn-usage-stub", first_attempt_id, invalid, recovered,
        ),
        claude_model="stub-model",
        provider_environment={
            "STUB_FIRST_ATTEMPT_ID": first_attempt_id,
            "STUB_FIRST_OUTPUT": json.dumps(invalid),
            "STUB_SECOND_OUTPUT": json.dumps(recovered),
        },
    )
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"].update({
            "max_retries": 0,
            "max_infrastructure_retries": 1,
            "max_process_lost_retries": 0,
        })
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            if host.store.list_facts(run_id, "HypothesisProposed"):
                break
            time.sleep(0.005)
        else:
            raise AssertionError("provider accounting retry did not recover")

        attempts = sorted(
            host.store.list_attempts(workflow_id=run_id, command_id=command_id),
            key=lambda row: int(row["attempt_ordinal"]),
        )
        assert [int(row["attempt_ordinal"]) for row in attempts] == [1, 2]
        assert [row["terminal_reason"] for row in attempts] == [
            "FAILED",
            "SUCCEEDED",
        ]
        observations = [
            json.loads(row["observation_json"]) for row in attempts
        ]
        assert observations[0]["failure_code"] == "invalid_turn_usage"
        assert observations[0]["retry_class"] == "INFRASTRUCTURE"
        first_request, second_request = (
            AgentRequest.from_dict(observation["request"])
            for observation in observations
        )
        assert second_request.input_artifacts == first_request.input_artifacts
        assert (
            second_request.immutable_context_refs
            == first_request.immutable_context_refs
        )
        assert not any(
            item.ref.endswith(
                ("--agent-retry-diagnostic", "--candidate-validation-error")
            )
            for item in second_request.input_artifacts
        )
    finally:
        host.close()


def test_provider_turn_budget_exceeded_remains_terminal(
    tmp_path: Path,
) -> None:
    run_id = "phase8-provider-turn-budget-terminal"
    command_id = f"{run_id}--r3--request_hypothesis_candidate"
    first_attempt_id = f"{run_id}--proposal-1-1--agent-1"
    over_limit = _proposal_provider_output(run_id)
    over_limit["num_turns"] = 2
    host = WorkflowHost(
        tmp_path / "provider-turn-budget-terminal",
        claude_agent_host=_attempt_aware_claude_stub(
            tmp_path / "provider-turn-budget-stub", first_attempt_id, over_limit,
            _proposal_provider_output(run_id, attempt_ordinal=2),
        ),
        claude_model="stub-model",
        provider_environment={
            "STUB_FIRST_ATTEMPT_ID": first_attempt_id,
            "STUB_FIRST_OUTPUT": json.dumps(over_limit),
            "STUB_SECOND_OUTPUT": json.dumps(
                _proposal_provider_output(run_id, attempt_ordinal=2)
            ),
        },
    )
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        budget = payload["research_contract"]["budget"]
        budget.update({
            "max_retries": 3,
            "max_infrastructure_retries": 3,
            "max_process_lost_retries": 0,
        })
        budget["agent_invocation"]["max_turns"] = 1
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            first = host.store.get_attempt(first_attempt_id)
            if (
                first is not None
                and first["state"] == "CLOSED"
                and not host.running_agents
            ):
                break
            time.sleep(0.005)
        else:
            raise AssertionError("turn-budget failure did not close")
        for _ in range(5):
            host.tick()

        attempts = host.store.list_attempts(
            workflow_id=run_id, command_id=command_id
        )
        assert len(attempts) == 1
        observation = json.loads(attempts[0]["observation_json"])
        assert attempts[0]["terminal_reason"] == "FAILED"
        assert observation["failure_code"] == "turn_budget_exceeded"
        assert observation["retry_class"] is None
        assert not host.store.list_facts(run_id, "HypothesisProposed")
    finally:
        host.close()


def test_profile_wallclock_rejects_agent_success_observed_after_deadline(
    tmp_path: Path,
) -> None:
    run_id = "phase8-wallclock-late-agent"
    attempt_id = f"{run_id}--proposal-1-1--agent-1"
    host = WorkflowHost(
        tmp_path / "wallclock-late-agent",
        claude_agent_host=_delayed_claude_stub(tmp_path / "provider-late", run_id),
        claude_model="stub-model",
        provider_environment={"STUB_VALID_OUTPUT": json.dumps(_proposal_provider_output(run_id))},
    )
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 0.0002
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not host.running_agents:
            host.tick(); time.sleep(0.005)
        assert len(host.running_agents) == 1

        handle = next(iter(host.running_agents.values()))
        assert handle._process.wait(timeout=5) == 0
        assert host._run_wallclock_expired(run_id)
        host.tick()
        attempt = host.store.get_attempt(attempt_id)
        assert attempt is not None and attempt["terminal_reason"] == "TIMED_OUT"
        observation = json.loads(attempt["observation_json"])
        assert observation["failure_code"] == "profile_wallclock_exhausted"
        assert "response" not in observation
        assert not host.store.list_facts(run_id, "HypothesisProposed")
    finally:
        host.close()


def _record_draft_workspaces(
    monkeypatch: pytest.MonkeyPatch,
) -> list[PreparedSessionWorkspace]:
    prepared: list[PreparedSessionWorkspace] = []
    original_prepare = AgentSessions.prepare

    def record_prepare(manager, request, target):
        workspace = original_prepare(manager, request, target)
        if request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
            assert workspace.worktree_root.is_dir()
            prepared.append(workspace)
        return workspace

    monkeypatch.setattr(AgentSessions, "prepare", record_prepare)
    return prepared


def test_draft_workspace_wallclock_expiry_closes_without_provider_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "draft-workspace-wallclock"
    run_id = "phase8-draft-workspace-wallclock"
    draft_attempt_id = f"{run_id}--component-1--experiment--agent-1"
    prepared = _record_draft_workspaces(monkeypatch)
    claude = _delayed_claude_stub(tmp_path / "provider-draft-workspace-wallclock", run_id)
    original_start = claude.start
    provider_requests: list[AgentRequest] = []
    saw_prepared_workspace = False

    def record_start(request: AgentRequest, **kwargs: object):
        provider_requests.append(request)
        if request.command is AgentCommand.DRAFT_EXPERIMENT_SPEC:
            pytest.fail("expired DRAFT workspace reached the Agent provider")
        return original_start(request, **kwargs)

    def expire_after_workspace_preparation(target_run_id: str) -> bool:
        nonlocal saw_prepared_workspace
        assert target_run_id == run_id
        if prepared:
            saw_prepared_workspace = True
            return True
        return False

    monkeypatch.setattr(claude, "start", record_start)
    host = WorkflowHost(
        state_root,
        claude_agent_host=claude,
        claude_model="stub-model",
        provider_environment={
            "STUB_VALID_OUTPUT": json.dumps(_proposal_provider_output(run_id))
        },
    )
    monkeypatch.setattr(host, "_run_wallclock_expired", expire_after_workspace_preparation)
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            attempt = host.store.get_attempt(draft_attempt_id)
            if attempt is not None and attempt["state"] == "CLOSED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("DRAFT workspace expiry did not close its Attempt")

        assert saw_prepared_workspace
        assert [request.command for request in provider_requests] == [
            AgentCommand.PROPOSE_HYPOTHESIS
        ]
        assert attempt is not None
        assert attempt["terminal_reason"] == AttemptTerminalReason.TIMED_OUT.value
        observation = json.loads(attempt["observation_json"])
        assert observation["failure_code"] == "profile_wallclock_exhausted"
        assert len(prepared) == 1
        assert prepared[0].resources == []
        assert prepared[0].worktree_root.is_dir()  # Persistent recovery context.
        assert draft_attempt_id not in host.running_agents
    finally:
        host.close()


def test_draft_invocation_deadline_after_pgid_bind_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "draft-invocation-deadline"
    run_id = "phase8-draft-invocation-deadline"
    experiment_id = f"{run_id}--component-1--experiment"
    first_attempt_id = f"{experiment_id}--agent-1"
    second_attempt_id = f"{experiment_id}--agent-2"
    prepared = _record_draft_workspaces(monkeypatch)
    claude = _delayed_claude_stub(tmp_path / "provider-draft-invocation-deadline", run_id)
    clock_offset = [0.0]
    claude._clock = lambda: time.monotonic() + clock_offset[0]
    real_os = claude_agent_host_module.os
    gate_releases: list[int] = []

    class RecordingOs:
        def __getattr__(self, name: str):
            return getattr(real_os, name)

        def write(self, file_descriptor: int, content: bytes) -> int:
            if content == b"G":
                gate_releases.append(file_descriptor)
            return real_os.write(file_descriptor, content)

    monkeypatch.setattr(claude_agent_host_module, "os", RecordingOs())
    host = WorkflowHost(
        state_root,
        claude_agent_host=claude,
        claude_model="stub-model",
        provider_environment={
            "STUB_VALID_OUTPUT": json.dumps(_proposal_provider_output(run_id))
        },
    )
    crossed_invocation_deadline = False
    original_crash = host._crash

    def cross_first_draft_deadline(point: str) -> None:
        nonlocal crossed_invocation_deadline
        original_crash(point)
        if (
            point == "after_agent_pgid_bind_before_release"
            and not crossed_invocation_deadline
            and prepared and prepared[-1].attempt_id == first_attempt_id
        ):
            row = host.store.get_attempt(first_attempt_id)
            assert row is not None
            request = AgentRequest.from_dict(
                json.loads(row["observation_json"])["request"]
            )
            clock_offset[0] += request.budget.wall_seconds + 1
            crossed_invocation_deadline = True

    monkeypatch.setattr(host, "_crash", cross_first_draft_deadline)
    second_pgid: int | None = None
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        created = host.handle(
            ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            first = host.store.get_attempt(first_attempt_id)
            if first is not None and first["state"] == "CLOSED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("DRAFT invocation deadline did not close its Attempt")

        assert crossed_invocation_deadline
        assert len(gate_releases) == 1  # proposal only; first DRAFT stayed gated
        assert not host._run_wallclock_expired(run_id)
        assert first is not None
        assert first["terminal_reason"] == AttemptTerminalReason.TIMED_OUT.value
        first_observation = json.loads(first["observation_json"])
        assert first_observation["failure_code"] == "timed_out"
        assert first_observation["failure_code"] != "profile_wallclock_exhausted"
        first_pgid = int(first["process_group_id"])
        assert not process_group_exists(first_pgid)
        assert prepared[0].resources == []
        assert prepared[0].worktree_root.is_dir()
        assert not host.store.inspect(
            "SELECT 1 FROM workflow_transitions WHERE run_id=? "
            "AND json_extract(payload_json,'$.agent_final_failure')=1 "
            "AND json_extract(payload_json,'$.attempt_id')=?",
            (run_id, first_attempt_id),
        )

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            second = host.store.get_attempt(second_attempt_id)
            if second is not None and second["state"] == "OPEN":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("retryable DRAFT did not open its bounded successor")

        assert second is not None and second["attempt_ordinal"] == 2
        assert second_attempt_id in host.running_agents
        assert len(gate_releases) == 2
        second_pgid = int(second["process_group_id"])
        head = host.store.latest_transition(run_id)
        assert head is not None
        cancelled = host.handle(
            ControlRequest(
                1,
                f"cancel-{run_id}",
                int(head["revision"]),
                "CANCEL",
                {"run_id": run_id, "cause": "test_cleanup"},
            )
        )
        assert cancelled.status == "accepted", cancelled
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and host.running_agents:
            host.tick()
            time.sleep(0.005)
        assert not host.running_agents
    finally:
        host.close()

    assert [workspace.attempt_id for workspace in prepared] == [first_attempt_id, second_attempt_id]
    assert prepared[0].binding == prepared[1].binding
    assert all(workspace.resources == [] for workspace in prepared)
    assert all(workspace.worktree_root.is_dir() for workspace in prepared)
    assert second_pgid is not None and not process_group_exists(second_pgid)


def test_profile_invocation_wallclock_rejects_late_agent_success(
    tmp_path: Path,
) -> None:
    run_id = "phase8-invocation-wallclock-late-agent"
    attempt_id = f"{run_id}--proposal-1-1--agent-1"
    host = WorkflowHost(
        tmp_path / "invocation-wallclock-late-agent",
        claude_agent_host=_delayed_claude_stub(tmp_path / "provider-invocation-late", run_id),
        claude_model="stub-model",
        provider_environment={"STUB_VALID_OUTPUT": json.dumps(_proposal_provider_output(run_id))},
    )
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        payload["research_contract"]["budget"]["agent_invocation"]["wall_seconds"] = 0.5
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not host.running_agents:
            host.tick(); time.sleep(0.005)
        assert len(host.running_agents) == 1

        handle = next(iter(host.running_agents.values()))
        assert handle._process.wait(timeout=5) == 0
        assert time.monotonic() > handle.deadline_at
        host.tick()
        attempt = host.store.get_attempt(attempt_id)
        assert attempt is not None and attempt["terminal_reason"] == "TIMED_OUT"
        observation = json.loads(attempt["observation_json"])
        assert observation["failure_code"] == "timed_out"
        assert "response" not in observation
        assert not host.store.list_facts(run_id, "HypothesisProposed")
    finally:
        host.close()


def test_profile_wallclock_cancels_an_already_running_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "phase8-wallclock-running-process"
    host = WorkflowHost(tmp_path / "wallclock-running-process")
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1, delay_seconds=5)
        payload["research_contract"]["budget"]["wallclock_hours"] = 0.0002
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        # Hold only the Profile clock until the real Process starts: the 0.72s
        # run budget must not expire while this test establishes its precondition.
        run = host.store.get_run(run_id)
        assert run is not None
        clock = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))

        class ProfileClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.astimezone(tz) if tz is not None else clock.replace(tzinfo=None)

        monkeypatch.setattr(agent_budget_module, "datetime", ProfileClock)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _running_processes(host, run_id):
            host.tick(); time.sleep(0.005)
        assert len(_running_processes(host, run_id)) == 1

        handle = next(iter(_running_processes(host, run_id).values()))
        assert handle.returncode is None
        assert time.monotonic() - handle.started_at < handle.request.timeout_seconds
        clock += timedelta(seconds=0.8)
        assert host._run_wallclock_expired(run_id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("expired running Process did not close")
        process_attempts = _process_attempts(host, run_id)
        assert process_attempts
        assert process_attempts[-1]["terminal_reason"] == "TIMED_OUT"
        assert not host.running
    finally:
        host.close()


def test_profile_wallclock_rejects_process_success_observed_after_deadline(
    tmp_path: Path,
) -> None:
    run_id = "phase8-wallclock-late-process"
    host = WorkflowHost(tmp_path / "wallclock-late-process")
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1, delay_seconds=1.7)
        payload["research_contract"]["budget"]["wallclock_hours"] = 0.0004
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _running_processes(host, run_id):
            host.tick(); time.sleep(0.005)
        running = _running_processes(host, run_id)
        assert len(running) == 1

        time.sleep(1.9)
        assert next(iter(running.values())).returncode == 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("late Process result did not close")
        process_attempt = _process_attempts(host, run_id)[-1]
        assert process_attempt["terminal_reason"] == "TIMED_OUT"
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert len(outcomes) == 1
        assert outcomes[0]["payload"]["outcome"] == "EXECUTION_FAILURE"
    finally:
        host.close()


def test_process_poll_rechecks_profile_wallclock_after_runner_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "phase8-wallclock-crosses-in-wait"
    host = WorkflowHost(tmp_path / "wallclock-crosses-in-wait")
    try:
        host.initialize()
        payload = _payload(run_id, roles=("analyst",), component_count=1, delay_seconds=0.05)
        payload["research_contract"]["budget"]["wallclock_hours"] = 1
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _running_processes(host, run_id):
            host.tick(); time.sleep(0.005)
        running = _running_processes(host, run_id)
        assert len(running) == 1
        handle = next(iter(running.values()))
        while time.monotonic() < deadline and handle.returncode is None:
            time.sleep(0.005)
        assert handle.returncode == 0

        checks = 0

        def crosses_during_wait(_run_id: str) -> bool:
            nonlocal checks
            checks += 1
            return checks >= 3

        with monkeypatch.context() as patcher:
            patcher.setattr(host, "_run_wallclock_expired", crosses_during_wait)
            host.tick()
        assert checks >= 3
        process_attempt = _process_attempts(host, run_id)[-1]
        assert process_attempt["terminal_reason"] == "TIMED_OUT"

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("Process crossing the deadline in wait did not close")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert len(outcomes) == 1
        assert outcomes[0]["payload"]["outcome"] == "EXECUTION_FAILURE"
    finally:
        host.close()


def test_same_run_overlaps_two_real_bwrap_cpu_attempts_and_closes_cleanly(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "parallel-cpu"
    run_id = "phase8-parallel-cpu"
    host = WorkflowHost(state_root)
    overlap: dict[str, object] | None = None
    maximum_open_processes = 0
    try:
        host.initialize()
        _create(host, run_id)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            host.tick()
            attempts = _process_attempts(host, run_id)
            open_attempts = {
                str(row["attempt_id"]): row
                for row in attempts
                if row["state"] == "OPEN"
            }
            running = _running_processes(host, run_id)
            assert set(running) <= set(open_attempts)
            maximum_open_processes = max(maximum_open_processes, len(open_attempts))
            if overlap is None and len(running) >= 2:
                attempt_ids = tuple(sorted(running))
                handles = tuple(running[attempt_id] for attempt_id in attempt_ids)
                process_group_ids = tuple(handle.process_group_id for handle in handles)
                workspaces = tuple(
                    Path(handle.request.cwd).parent for handle in handles
                )
                overlap = {
                    "attempt_ids": attempt_ids,
                    "open_attempt_ids": tuple(sorted(open_attempts)),
                    "process_group_ids": process_group_ids,
                    "durable_process_group_ids": tuple(
                        int(open_attempts[attempt_id]["process_group_id"])
                        for attempt_id in attempt_ids
                    ),
                    "workspaces": workspaces,
                    "workspaces_live": all(
                        workspace.is_dir()
                        and workspace.name == attempt_id
                        and workspace.is_relative_to(state_root / "artifacts" / "work")
                        for attempt_id, workspace in zip(
                            attempt_ids, workspaces, strict=True
                        )
                    ),
                    "groups_live": all(
                        process_group_exists(pgid) for pgid in process_group_ids
                    ),
                    "argv0": tuple(handle.request.argv[0] for handle in handles),
                }
            head = host.store.latest_transition(run_id)
            assert head is not None
            if head["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError(f"run {run_id} did not complete")

        terminal_attempts = _process_attempts(host, run_id)
        assert len(terminal_attempts) == 2
        assert {row["state"] for row in terminal_attempts} == {"CLOSED"}
        assert {row["terminal_reason"] for row in terminal_attempts} == {"SUCCEEDED"}
        assert not host.running
    finally:
        host.close()

    _assert_clean_oracle(state_root, run_id)
    assert overlap is not None, (
        "two independent PROCESS attempts never overlapped; "
        f"maximum durable OPEN process attempts was {maximum_open_processes}"
    )
    assert overlap["attempt_ids"] == overlap["open_attempt_ids"]
    assert len(set(overlap["process_group_ids"])) == 2
    assert overlap["process_group_ids"] == overlap["durable_process_group_ids"]
    assert len(set(overlap["workspaces"])) == 2
    assert overlap["workspaces_live"] is True
    assert overlap["groups_live"] is True
    assert overlap["argv0"] == ("/usr/bin/bwrap", "/usr/bin/bwrap")


def _gpu_capabilities() -> ComputeCapabilities:
    return ComputeCapabilities(
        gpus=(
            GpuCapability(
                index=2,
                uuid=_GPU_UUID,
                name="synthetic scheduling-only GPU",
                total_memory_mib=24_576,
                free_memory_mib=20_480,
                driver_version="570.133.07",
            ),
        )
    )


def _gpu_resources(tmp_path: Path) -> LocalComputeResources:
    runtime = tmp_path / "synthetic-gpu-runtime"
    (runtime / "bin").mkdir(parents=True)
    python = runtime / "bin" / "python"
    python.write_bytes(b"scheduling-only-python")
    python.chmod(0o755)
    return LocalComputeResources(
        allowed_gpu_uuids=(_GPU_UUID,),
        gpu_runtime_prefix=runtime,
        inventory_provider=_gpu_capabilities,
    )


class _GpuRequiredFake(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC:
            return response
        assert isinstance(response, AgentResponse)
        candidate = dict(response.typed_candidate or {})
        candidate["resource_preference"] = "GPU_REQUIRED"
        return with_output_digest(
            replace(response, typed_candidate=candidate, output_digest="")
        )


def _output_binding(request: ProcessRequest) -> Path:
    for index, value in enumerate(request.argv[:-2]):
        if value == "--bind" and request.argv[index + 2] == "/output":
            return Path(request.argv[index + 1])
    raise AssertionError("ProcessRequest lacks its canonical /output binding")


@dataclass(slots=True)
class _SyntheticHandle:
    request: ProcessRequest
    pid: int
    process_group_id: int
    started_at: float
    finish_at: float
    cancelled: bool = False

    @property
    def returncode(self) -> int | None:
        if self.cancelled:
            return -15
        return 0 if time.monotonic() >= self.finish_at else None


class _DelayedSyntheticRunner:
    """Scheduling probe that never executes the GPU-shaped ProcessRequest."""

    def __init__(self, delay_seconds: float = 0.35) -> None:
        self.delay_seconds = delay_seconds
        self.started: list[ProcessRequest] = []

    def start(
        self, request: ProcessRequest, *, before_exec: Callable[[int], None] | None = None,
    ) -> _SyntheticHandle:
        ordinal = len(self.started) + 1
        if before_exec is not None:
            before_exec(2_000_000_000 + ordinal)
        self.started.append(request)
        log_root = Path(request.cwd)
        assert request.stdout_path is not None and request.stderr_path is not None
        (log_root / request.stdout_path).write_bytes(b"")
        (log_root / request.stderr_path).write_bytes(b"")
        branch = int(
            request.attempt_id.split("--component-", 1)[1].split("--", 1)[0]
        )
        (_output_binding(request) / "result.json").write_text(
            json.dumps({"measurements": [branch, branch, branch]}, sort_keys=True),
            encoding="utf-8",
        )
        started_at = time.monotonic()
        delay_seconds = (
            0.0
            if request.execution_id.endswith("--candidate-validation")
            else self.delay_seconds
        )
        return _SyntheticHandle(
            request=request,
            pid=2_000_000_000 + ordinal,
            process_group_id=2_000_000_000 + ordinal,
            started_at=started_at,
            finish_at=started_at + delay_seconds,
        )

    def wait(self, handle: _SyntheticHandle) -> ProcessObservation:
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

    def cancel(self, handle: _SyntheticHandle) -> bool:
        if handle.returncode is not None:
            return False
        handle.cancelled = True
        return True

    def cleanup(self, handle: _SyntheticHandle) -> None:
        if handle.returncode is None:
            self.cancel(handle)


def test_same_gpu_uuid_never_has_two_running_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "same-gpu"
    run_id = "phase8-same-gpu"
    resources = _gpu_resources(tmp_path)
    monkeypatch.setattr(
        resources, "validate_live", lambda grant, snapshot, **constraints: None
    )
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    host = WorkflowHost(state_root, compute_resources=resources)
    host.agent = _GpuRequiredFake()
    runner = _DelayedSyntheticRunner(delay_seconds=1.0)
    host.runner = runner  # type: ignore[assignment]
    maximum_same_uuid_running = 0
    try:
        host.initialize()
        _create(host, run_id, execution={"allow_gpu": True, "delay_seconds": 0.4})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            host.tick()
            active_uuids = _running_gpu_uuids(host, run_id)
            maximum_same_uuid_running = max(
                maximum_same_uuid_running,
                sum(uuid == _GPU_UUID for uuid in active_uuids),
            )
            head = host.store.latest_transition(run_id)
            assert head is not None
            if head["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError(f"run {run_id} did not complete")

        specs = host.store.list_facts(run_id, "ExperimentPreregistered")
        assert len(specs) == 2
        assert {
            row["payload"]["execution_contract"]["resource_grant"]["gpu_uuid"]
            for row in specs
        } == {_GPU_UUID}
        assert len(_started_process_requests(host, run_id, runner.started)) == 2
        assert maximum_same_uuid_running == 1
    finally:
        host.close()

    _assert_clean_oracle(state_root, run_id)


def test_same_gpu_uuid_serializes_across_runs_in_one_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "same-gpu-cross-run"
    run_ids = ("phase8-same-gpu-run-a", "phase8-same-gpu-run-b")
    resources = _gpu_resources(tmp_path)
    monkeypatch.setattr(
        resources, "validate_live", lambda grant, snapshot, **constraints: None
    )
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    host = WorkflowHost(state_root, compute_resources=resources)
    host.agent = _GpuRequiredFake()
    runner = _DelayedSyntheticRunner()
    host.runner = runner  # type: ignore[assignment]
    try:
        host.initialize()
        for run_id in run_ids:
            _create(
                host,
                run_id,
                expert_roles=["analyst"],
                experiment_budget=1,
                execution={"allow_gpu": True, "delay_seconds": 0.4},
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            host.tick()
            active_uuids = [
                gpu_uuid
                for run_id in run_ids
                for gpu_uuid in _running_gpu_uuids(host, run_id)
            ]
            assert sum(uuid == _GPU_UUID for uuid in active_uuids) <= 1
            heads = [host.store.latest_transition(run_id) for run_id in run_ids]
            if all(head is not None and head["state"] == "COMPLETED" for head in heads):
                break
            time.sleep(0.005)
        else:
            raise AssertionError("cross-run same-GPU cohort did not complete")
        assert sum(
            len(_started_process_requests(host, run_id, runner.started))
            for run_id in run_ids
        ) == 2
    finally:
        host.close()

    for run_id in run_ids:
        _assert_clean_oracle(state_root, run_id)


def test_same_gpu_retry_waits_for_running_sibling_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "same-gpu-retry"
    run_id = "phase8-same-gpu-retry"
    resources = _gpu_resources(tmp_path)
    monkeypatch.setattr(
        resources, "validate_live", lambda grant, snapshot, **constraints: None
    )
    monkeypatch.setattr(ExecutionWorkspace, "_check_sandbox", lambda self: None)
    first = WorkflowHost(state_root, compute_resources=resources)
    first.agent = _GpuRequiredFake()
    first.runner = _DelayedSyntheticRunner(delay_seconds=30)  # type: ignore[assignment]
    try:
        first.initialize()
        _create(first, run_id, execution={"allow_gpu": True, "delay_seconds": 0.4})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            first.tick()
            attempts = _process_attempts(first, run_id)
            second = first.store.latest_transition(
                f"{run_id}--component-2--experiment"
            )
            if (
                len(attempts) == 1
                and attempts[0]["state"] == "OPEN"
                and second is not None
                and second["state"] == "READY"
            ):
                break
            time.sleep(0.005)
        else:
            raise AssertionError("first GPU attempt and sibling READY state were not reached")
    finally:
        first.close()

    resumed = WorkflowHost(state_root, compute_resources=resources)
    resumed.agent = _GpuRequiredFake()
    runner = _DelayedSyntheticRunner()
    resumed.runner = runner  # type: ignore[assignment]
    try:
        resumed.initialize()
        recovered = _process_attempts(resumed, run_id)
        assert [(row["attempt_ordinal"], row["terminal_reason"]) for row in recovered] == [
            (1, "LOST")
        ]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            resumed.tick()
            active_uuids = _running_gpu_uuids(resumed, run_id)
            assert sum(uuid == _GPU_UUID for uuid in active_uuids) <= 1
            head = resumed.store.latest_transition(run_id)
            assert head is not None
            if head["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError(f"run {run_id} did not complete")

        first_branch = [
            row
            for row in _process_attempts(resumed, run_id)
            if row["experiment_id"].endswith("--component-1--experiment")
        ]
        assert [row["terminal_reason"] for row in first_branch] == [
            "LOST",
            "SUCCEEDED",
        ]
        retry_transitions = resumed.store.inspect(
            "SELECT t.payload_json FROM workflow_transitions t "
            "JOIN workflow_commands c ON c.command_id=t.command_id "
            "WHERE t.run_id=? AND c.command_type='OPEN_RETRY_ATTEMPT'",
            (run_id,),
        )
        assert len(retry_transitions) == 1
        retry_payload = json.loads(retry_transitions[0]["payload_json"])
        assert retry_payload["retry_of_attempt_id"] == first_branch[0]["attempt_id"]
        assert retry_payload["retry_reason"] == "process_lost"
        assert len(_started_process_requests(resumed, run_id, runner.started)) == 2
    finally:
        resumed.close()

    _assert_clean_oracle(state_root, run_id)
