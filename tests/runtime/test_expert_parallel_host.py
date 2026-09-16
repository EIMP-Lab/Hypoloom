from __future__ import annotations

import base64
import copy
import json
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import pytest

from hypoloom.agent_aci import (
    AgentBudget,
    AgentCommand,
    AgentRequest,
    AgentResponse,
    WorkspaceArtifact,
    canonical_json,
    request_digest,
    with_output_digest,
)
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.control_protocol import ControlRequest
from hypoloom.fake_agent import FakeAgent
from hypoloom.expert_loop import ResearchNodeTask, ResearchNodeSubkind
from hypoloom.oracle import Oracle
from hypoloom.literature import (LITERATURE_STRATA, FetchResponse,
    FixedLiteratureIO, LiteratureQuery, PdfExtraction, ScholarlySourceClass, SearchPaper)
from hypoloom.profile_admission import PROFILE_FILES, admit_profile
from hypoloom.runner import process_group_exists
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]


def _refute_accuracy(response: AgentResponse) -> AgentResponse:
    artifacts = tuple(
        WorkspaceArtifact.from_bytes(
            item.path,
            item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50'),
        )
        for item in response.proposed_workspace_artifacts
    )
    assert artifacts != response.proposed_workspace_artifacts
    return with_output_digest(replace(
        response,
        proposed_workspace_artifacts=artifacts,
        output_digest="",
    ))


class _NoPdfOpenAlexFixture:
    configuration = {"adapter": "openalex", "host": "api.openalex.org",
        "timeout_seconds": 1.0, "max_response_bytes": 4096}

    def preflight(self) -> dict[str, object]:
        return {"adapter": "openalex", "network_probe_performed": False}

    def search(self, query: LiteratureQuery) -> Iterable[SearchPaper]:
        latest = date.fromisoformat(query.latest_as_of).year
        lower = query.filters.year_from or latest - 80
        upper = min(query.filters.year_to or latest, latest)
        years = (lower, lower + (upper - lower) // 2, upper)
        for index, (stratum, year) in enumerate(zip(
                LITERATURE_STRATA, years, strict=True), 1):
            yield SearchPaper(bibliographic_identity=f"openalex:W{index}",
                title=f"Parallel fixture {index}", authors=("Test Researcher",),
                year=year, language="en",
                landing_uri=f"https://catalog.example.org/works/{index}",
                source_uri=f"https://catalog.example.org/records/{index}",
                source_class=ScholarlySourceClass.JOURNAL, stratum=stratum, pdf_uri=None)

    def resolve(self, hostname: str) -> Sequence[str]:
        assert hostname == "catalog.example.org"
        return ("8.8.8.8",)

    def fetch(self, uri: str, *, approved_addresses: tuple[str, ...],
              max_bytes: int) -> FetchResponse:
        raise AssertionError("fixture has no PDF")

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        raise AssertionError("fixture has no PDF")


class _ReviseDependencyOnceAgent(FakeAgent):
    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = super().invoke(request)
        if not isinstance(response, AgentResponse):
            return response
        task = request.task_context
        if (
            request.component_id == "primary.parallel_1"
            and request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 1
        ):
            return _refute_accuracy(response)
        if (
            request.component_id == "primary.parallel_1"
            and request.command is AgentCommand.HYPOTHESIS_UPDATE
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 1
        ):
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
            return with_output_digest(replace(
                response, typed_candidate=candidate, output_digest="",
            ))
        if (
            request.component_id == "primary.parallel_1"
            and request.command is AgentCommand.HYPOTHESIS_PROPOSAL
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 2
            and task.key.subkind is ResearchNodeSubkind.SYNTHESIS
        ):
            candidate = dict(response.typed_candidate or {})
            candidate["method_family_id"] = "revised-family-v2"
            return with_output_digest(replace(
                response, typed_candidate=candidate, output_digest="",
            ))
        if request.command in {
            AgentCommand.RESULT_ANALYSIS,
            AgentCommand.HYPOTHESIS_UPDATE,
        }:
            candidate = dict(response.typed_candidate or {})
            candidate["summary"] = "Δ evidence 与当前假设完全对齐。"
            return with_output_digest(replace(
                response, typed_candidate=candidate, output_digest="",
            ))
        return response


class _WrongDependentFamilyOnceAgent(FakeAgent):
    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = super().invoke(request)
        task = request.task_context
        if (
            isinstance(response, AgentResponse)
            and request.component_id == "primary.parallel_2"
            and request.command is AgentCommand.HYPOTHESIS_PROPOSAL
            and isinstance(task, ResearchNodeTask)
            and task.key.subkind is ResearchNodeSubkind.SYNTHESIS
            and request.attempt_ordinal == 1
        ):
            candidate = dict(response.typed_candidate or {})
            candidate["method_family_id"] = "wrong-family-v1"
            return with_output_digest(replace(
                response, typed_candidate=candidate, output_digest="",
            ))
        return response


class _ReviseDependencyThroughCycleNineAgent(FakeAgent):
    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = super().invoke(request)
        task = request.task_context
        if (
            isinstance(response, AgentResponse)
            and request.component_id == "primary.parallel_1"
            and request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle < 10
        ):
            return _refute_accuracy(response)
        if (
            isinstance(response, AgentResponse)
            and request.component_id == "primary.parallel_1"
            and request.command is AgentCommand.HYPOTHESIS_UPDATE
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle < 10
        ):
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
            return with_output_digest(replace(
                response, typed_candidate=candidate, output_digest="",
            ))
        return response


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments), cwd=repository, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _backup_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
        source_db.backup(target_db)


def _replace_observation_input(
    observation: dict[str, object], ref: str, content: bytes,
) -> dict[str, object]:
    replaced = copy.deepcopy(observation)
    request_value = replaced["request"]
    assert isinstance(request_value, dict)
    inputs = request_value.get("input_artifacts")
    assert isinstance(inputs, list)
    matches = [item for item in inputs if item.get("ref") == ref]
    assert len(matches) == 1
    matches[0]["content_base64"] = base64.b64encode(content).decode("ascii")
    request = AgentRequest.from_dict(request_value)
    response_value = replaced["response"]
    assert isinstance(response_value, dict)
    response = AgentResponse.from_dict(response_value)
    replaced["request"] = request.to_dict()
    replaced["response"] = with_output_digest(replace(
        response,
        input_digest=request_digest(request),
        output_digest="",
    )).to_dict()
    return replaced


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-expert-parallel-", dir="/var/tmp",
    ) as raw:
        yield Path(raw)


def _payload(tmp_path: Path, mount_root: Path, run_id: str) -> dict[str, object]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text("parallel expert target\n", encoding="utf-8")
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "-c", "user.name=Hypoloom Test", "-c",
         "user.email=hypoloom@example.invalid", "commit", "-qm", "base")
    data = mount_root / "data"
    data.mkdir()
    profile = tmp_path / "profile"
    profile.mkdir()
    source = REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
    for name in PROFILE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace("/REPLACE_ME/expert-loop-pilot-target", str(target))
        text = text.replace("/REPLACE_ME/expert-loop-pilot-data", str(data))
        (profile / name).write_text(text, encoding="utf-8")
    report = admit_profile(profile, run_id=run_id)
    assert report.admitted and report.create_run_payload is not None
    payload = report.create_run_payload
    contract = payload["research_contract"]
    base_role = contract["roles"][0]
    roles = ("vision-expert", "robustness-expert")
    contract["roles"] = [{**copy.deepcopy(base_role), "id": role} for role in roles]
    base_component = contract["components"][0]
    contract["protocols"] = {
        "parallel-score": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": ["accuracy"],
                "required_artifacts": [],
                "artifact_validators": [],
            },
        },
    }
    contract["components"] = [
        {
            **copy.deepcopy(base_component),
            "id": f"primary.parallel_{index}",
            "name": f"parallel_{index}",
            "owner": role,
            "proposers": [role],
            "protocol": "parallel-score",
            "required_artifacts": [],
        }
        for index, role in enumerate(roles, 1)
    ]
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": "2",
        "display_only": True,
    }
    contract["budget"]["max_experiments"] = 2
    return payload


def _grounding_then_hanging_stub(root: Path) -> ClaudeAgentHost:
    root.mkdir()
    stub = root / "claude-stub"
    stub.write_text(
        """#!/usr/bin/sh
IFS= read -r PROMPT || true
case "$PROMPT" in
  *'"request_kind":"GROUNDING_QUERY_PLAN"'*)
    COMMAND_ID=$(printf '%s' "$PROMPT" | /usr/bin/sed -n 's/.*"command_id":"\\([^"]*\\)".*/\\1/p')
    ATTEMPT_ID=$(printf '%s' "$PROMPT" | /usr/bin/sed -n 's/.*"attempt_id":"\\([^"]*\\)".*/\\1/p')
    printf '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"total_cost_usd":0,"usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"stub-model":{}},"structured_output":{"typed_candidate":{"query":"Image classification"}}}\n'
    ;;
  *'"command":"GROUNDING"'*)
    printf '{"type":"result","subtype":"success","is_error":false,"num_turns":1,"total_cost_usd":0,"usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"stub-model":{}},"structured_output":{"typed_candidate":{"task_kind":"GROUNDING","summary":"bounded grounding","supporting_refs":[]},"declared_effects":[{"effect":"GROUNDING_CANDIDATE_PRODUCED"}]}}\n'
    ;;
  *) while :; do /usr/bin/sleep 1; done ;;
esac
""",
        encoding="utf-8",
    )
    stub.chmod(0o700)
    settings = root / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    settings.chmod(0o600)
    return ClaudeAgentHost(
        claude_executable=stub,
        settings_path=settings,
        allowed_environment_names=(),
        extra_runtime_paths=(
            "/usr/bin/sh", "/usr/bin/sed", "/usr/bin/sleep",
            "/lib/x86_64-linux-gnu/libacl.so.1",
            "/lib/x86_64-linux-gnu/libselinux.so.1",
            "/lib/x86_64-linux-gnu/libpcre2-8.so.0",
        ),
        terminate_grace_seconds=0.05,
        kill_grace_seconds=1.0,
    )


def test_status_projects_all_reachable_multi_component_agent_attempts(
    tmp_path: Path, mount_root: Path,
) -> None:
    run_id = "expert-loop-agent-telemetry"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    third_role = {**copy.deepcopy(contract["roles"][0]), "id": "methods-expert"}
    third_component = {**copy.deepcopy(contract["components"][0]),
        "id": "primary.parallel_3", "name": "parallel_3",
        "owner": "methods-expert", "proposers": ["methods-expert"]}
    contract["roles"].append(third_role)
    contract["components"].append(third_component)
    contract["goal"]["summary_metric"]["target"] = "3"
    host = WorkflowHost(tmp_path / "state")
    try:
        host.initialize()
        created = host.handle(ControlRequest(1, f"create-{run_id}", 0,
            "CREATE_RUN", payload))
        assert created.status == "accepted", created
        status = host.handle(ControlRequest(1, f"status-{run_id}", None,
            "STATUS", {"run_id": run_id}))
        assert status.result is not None
        # Three run nodes (Grounding plan/Grounding/FINAL), five proposal nodes
        # per round, three inner nodes per experiment, and four closure nodes
        # per round. Zero-experiment failed batches still need ADJ/UPDATE/REPORT/
        # SELF, so round estimate=max(3 components,2 experiments), not just2.
        # Eight Attempts per logical task: stop when either retry class expires.
        usage = status.result["agent_usage"]
        assert usage["remaining_declared_limits"]["attempts"] == 8 * (3 + 5 * 3 + 3 * 2 + 4 * 3)
        assert usage["estimate_scope"] == {"kind": "core_expert_nodes_estimate", "strict_upper_bound": False,
            "excluded_auxiliary_kinds": ["GRAPH_EVOLVE", "OBSERVER", "DEBATE", "DEBATE_JUDGE"]}
    finally:
        host.close()


def test_out_of_request_grounding_ref_uses_bounded_diagnostic_retry(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "expert-loop-ref-retry"
    payload = _payload(tmp_path, mount_root, run_id)
    host = WorkflowHost(tmp_path / "state")
    grounding_requests: list[AgentRequest] = []

    def invoke(request: AgentRequest) -> AgentResponse | bytes:
        response = FakeAgent().invoke(request)
        if request.command is AgentCommand.GROUNDING:
            grounding_requests.append(request)
            assert isinstance(response, AgentResponse)
            if request.attempt_ordinal == 1:
                return with_output_digest(replace(
                    response,
                    typed_candidate={
                        **dict(response.typed_candidate or {}),
                        "supporting_refs": ["not-in-the-request"],
                    },
                    output_digest="",
                ))
        return response

    monkeypatch.setattr(host.agent, "invoke", invoke)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=payload,
        ))
        assert created.status == "accepted", created

        for _ in range(40):
            host.tick()
            if len(grounding_requests) == 2:
                break
        else:
            raise AssertionError("out-of-request ref did not reach its bounded retry")

        assert [request.attempt_ordinal for request in grounding_requests] == [1, 2]
        assert len({request.command_id for request in grounding_requests}) == 1
        retry_input = next(
            item
            for item in grounding_requests[1].input_artifacts
            if item.ref.endswith("--candidate-validation-error")
        )
        assert json.loads(retry_input.content_bytes())["validation_error"] == {
            "code": "invalid_candidate",
            "message": "candidate cites context outside the request",
        }
        attempts = host.store.list_attempts(
            workflow_id=grounding_requests[0].owner_workflow_id,
            command_id=grounding_requests[0].command_id,
        )
        assert [row["state"] for row in attempts] == ["CLOSED", "CLOSED"]
        assert [row["terminal_reason"] for row in attempts] == [
            "FAILED",
            "SUCCEEDED",
        ]
        first_observation = json.loads(attempts[0]["observation_json"])
        assert first_observation["failure_code"] == "invalid_candidate"
        retry_diagnostic = json.loads(retry_input.content_bytes())
        assert retry_diagnostic["source_attempt_id"] == attempts[0]["attempt_id"]
    finally:
        host.close()


def test_two_experts_dispatch_independent_literature_nodes_concurrently(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 10)
    run_id = "expert-loop-parallel"
    adapter = _grounding_then_hanging_stub(tmp_path / "provider")
    host = WorkflowHost(
        tmp_path / "state",
        claude_agent_host=adapter,
        claude_model="stub-model",
        agent_budget=AgentBudget(
            max_turns=1, max_tokens=128, max_output_bytes=4096,
            wall_seconds=30, max_cost="0.01",
        ),
        literature_io=FixedLiteratureIO(_NoPdfOpenAlexFixture()),
    )
    process_groups: tuple[int, ...] = ()
    # This shell stub implements retained pre-session ACI10 only. The actual
    # ACI14 adapter/topology is exercised by test_claude_literature_host_g3 and
    # test_claude_multichain_g3, not by inventing transcripts in this stub.
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            open_attempts = [
                row for row in host.store.list_attempts()
                if row["run_id"] == run_id and row["state"] == "OPEN"
            ]
            if len(open_attempts) == 2 and len(host.running_agents) == 2:
                requests = [
                    AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
                    for row in open_attempts
                ]
                if {request.command.value for request in requests} == {"LITERATURE_REVIEW"}:
                    break
            time.sleep(0.005)
        else:
            diagnostic = [
                {
                    "state": row["state"],
                    "terminal_reason": row["terminal_reason"],
                    "request": json.loads(row["observation_json"]).get("request"),
                    "observation": json.loads(row["observation_json"]),
                }
                for row in host.store.list_attempts()
                if row["run_id"] == run_id
            ]
            raise AssertionError(
                f"independent literature nodes did not overlap: {diagnostic!r}"
            )

        assert {request.role for request in requests} == {
            "vision-expert", "robustness-expert",
        }
        assert len({request.command_id for request in requests}) == 2
        assert all(request.task_context.key.cycle == 1 for request in requests)  # type: ignore[union-attr]
        grounding = [
            row for row in host.store.list_attempts()
            if json.loads(row["observation_json"])["request"]["command"] == "GROUNDING"
        ]
        assert len(grounding) == 1
        assert grounding[0]["terminal_reason"] == "SUCCEEDED"
        process_groups = tuple(handle.process_group_id for handle in host.running_agents.values())

        head = host.store.latest_transition(run_id)
        assert head is not None
        cancelled = host.handle(ControlRequest(
            schema_version=1, request_id=f"cancel-{run_id}",
            expected_revision=int(head["revision"]), command="CANCEL",
            payload={"run_id": run_id, "cause": "test_cleanup"},
        ))
        assert cancelled.status == "accepted", cancelled
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "CANCELLED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("parallel expert cancellation did not settle")
        assert not host.running_agents
        assert not [row for row in host.store.list_attempts() if row["state"] == "OPEN"]
    finally:
        host.close()
    assert all(not process_group_exists(pgid) for pgid in process_groups)


def test_two_experts_each_close_a_complete_loop_without_a_global_round_barrier(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-parallel-complete"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError(
                f"parallel expert loops did not close: {host.store.latest_transition(run_id)!r}"
            )

        attempts = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "AGENT"
        ]
        requests = [
            AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
            for row in attempts
        ]
        grounding = [request for request in requests if request.command.value == "GROUNDING"]
        assert len(grounding) == 1
        expected = {
            "LITERATURE_REVIEW",
            "HYPOTHESIS_PROPOSAL",
            "EXPERIMENT_DESIGN_IMPLEMENT",
            "EXPERIMENT_EXECUTION",
            "RESULT_ANALYSIS",
            "ADJUDICATE",
            "HYPOTHESIS_UPDATE",
            "CYCLE_REPORT",
            "SELF_EVOLVE",
        }
        for role in ("vision-expert", "robustness-expert"):
            owned = [
                request.command.value for request in requests
                if request.role == role
                and isinstance(request.task_context, ResearchNodeTask)
            ]
            assert expected <= set(owned)
            assert owned.count("LITERATURE_REVIEW") == 2
            assert owned.count("HYPOTHESIS_PROPOSAL") == 3

        assert {
            row["payload"]["component_id"]
            for row in host.store.list_facts(run_id, "HypothesisAssessed")
        } == {"primary.parallel_1", "primary.parallel_2"}
        report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
            run_id
        )
        assert report.ok, report.to_json()
        assert report.open_attempt_count == 0
        assert report.pending_effect_count == 0
    finally:
        host.close()


def test_dependent_expert_waits_for_the_current_tip_termination(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 13)
    run_id = "expert-loop-dependent-termination"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    contract["components"][1]["dependencies"] = ["primary.parallel_1"]
    contract["budget"]["max_experiments"] = 3
    host = WorkflowHost(tmp_path / "state")
    host.agent = _ReviseDependencyOnceAgent()
    host_closed = False
    # Retained single-root UPDATE-response authorization (including the v6
    # negative below). ACI14's distinct ADJ authority is covered by
    # test_downstream_freezes_adjudicated_first_chain_not_later_completed_peer.
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=payload,
        ))
        assert created.status == "accepted", created

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError(
                f"dependent expert loop did not close: "
                f"{host.store.latest_transition(run_id)!r}"
            )

        attempts = tuple(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "AGENT"
        )
        requests = {
            str(row["attempt_id"]): AgentRequest.from_dict(
                json.loads(row["observation_json"])["request"]
            )
            for row in attempts
        }
        upstream_updates = [
            row for row in attempts
            if requests[str(row["attempt_id"])].component_id == "primary.parallel_1"
            and requests[str(row["attempt_id"])].command
            is AgentCommand.HYPOTHESIS_UPDATE
        ]
        assert len(upstream_updates) == 2
        terminal_update = next(
            row for row in upstream_updates
            if json.loads(row["observation_json"])["response"]["typed_candidate"]
            ["update_disposition"] == "TERMINATE_LINEAGE"
        )
        downstream = min(
            (
                row for row in attempts
                if requests[str(row["attempt_id"])].component_id
                == "primary.parallel_2"
            ),
            key=lambda row: float(row["started_at"]),
        )
        downstream_request = requests[str(downstream["attempt_id"])]
        assert float(downstream["started_at"]) >= float(terminal_update["ended_at"])
        assert (
            f"{terminal_update['attempt_id']}--response"
            in downstream_request.immutable_context_refs
        )
        assert not [
            row for row in attempts
            if json.loads(row["observation_json"]).get("failure_code")
            == "candidate_method_family"
        ]
        verdict = host.store.list_facts(run_id, "GoalVerdictRecorded")[0]["payload"]
        assert verdict["verdict"] == "INCONCLUSIVE"
        assert verdict["method_family_id"] is None
        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()

        terminal_ref = f"{terminal_update['attempt_id']}--response"
        later_downstream = next(
            row for row in attempts
            if requests[str(row["attempt_id"])].component_id == "primary.parallel_2"
            and requests[str(row["attempt_id"])].command is AgentCommand.HYPOTHESIS_UPDATE
        )
        later_request = requests[str(later_downstream["attempt_id"])]
        observation = json.loads(later_downstream["observation_json"])
        retained_inputs = tuple(
            item for item in later_request.input_artifacts
            if item.ref != terminal_ref
        )
        stripped_request = replace(
            later_request,
            immutable_context_refs=tuple(item.ref for item in retained_inputs),
            input_artifacts=retained_inputs,
        )
        response = AgentResponse.from_dict(observation["response"])
        candidate = dict(response.typed_candidate or {})
        if isinstance(candidate.get("supporting_refs"), list):
            candidate["supporting_refs"] = [
                item for item in candidate["supporting_refs"]
                if item != terminal_ref
            ]
        stripped_response = with_output_digest(replace(
            response,
            typed_candidate=candidate,
            input_digest=request_digest(stripped_request),
            output_digest="",
        ))
        observation["request"] = stripped_request.to_dict()
        observation["response"] = stripped_response.to_dict()
        host.close()
        host_closed = True
        tampered_db = tmp_path / "missing-terminal-ref.sqlite"
        _backup_database(host.state_root / "state.sqlite", tampered_db)
        with sqlite3.connect(tampered_db) as connection:
            # Model the historical Host-created request while leaving the
            # immutable source database and retained run semantics untouched.
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            connection.execute(
                "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                (
                    json.dumps(observation, sort_keys=True, separators=(",", ":")),
                    later_downstream["attempt_id"],
                ),
            )
        broken = Oracle(
            tampered_db, host.state_root / "artifacts",
        ).audit(run_id)
        assert "expert_dependency_authorization_mismatch" in {
            issue.code for issue in broken.issues
        }
        legacy_db = tmp_path / "v6-missing-terminal-ref.sqlite"
        _backup_database(tampered_db, legacy_db)
        with sqlite3.connect(legacy_db) as connection:
            connection.execute("DROP TRIGGER immutable_workflow_runs_update")
            connection.execute(
                "UPDATE workflow_runs SET research_contract_version="
                "'hypoloom-research-v6' WHERE run_id=?",
                (run_id,),
            )
        legacy = Oracle(legacy_db, host.state_root / "artifacts").audit(run_id)
        assert "expert_dependency_authorization_mismatch" not in {
            issue.code for issue in legacy.issues
        }
        assert "component_dependency_closure_mismatch" in {
            issue.code for issue in legacy.issues
        }

        corrupt_observation = json.loads(downstream["observation_json"])
        corrupt_request_value = copy.deepcopy(corrupt_observation["request"])
        terminal_input = next(
            item for item in corrupt_request_value["input_artifacts"]
            if item["ref"] == terminal_ref
        )
        terminal_input["content_base64"] = base64.b64encode(b"{}").decode("ascii")
        corrupt_request = AgentRequest.from_dict(corrupt_request_value)
        corrupt_response = AgentResponse.from_dict(corrupt_observation["response"])
        corrupt_observation["request"] = corrupt_request.to_dict()
        corrupt_observation["response"] = with_output_digest(replace(
            corrupt_response,
            input_digest=request_digest(corrupt_request),
            output_digest="",
        )).to_dict()
        corrupt_db = tmp_path / "corrupt-terminal-input.sqlite"
        _backup_database(host.state_root / "state.sqlite", corrupt_db)
        with sqlite3.connect(corrupt_db) as connection:
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            connection.execute(
                "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                (
                    json.dumps(corrupt_observation, sort_keys=True, separators=(",", ":")),
                    downstream["attempt_id"],
                ),
            )
        corrupt = Oracle(corrupt_db, host.state_root / "artifacts").audit(run_id)
        assert "expert_dependency_authorization_mismatch" in {
            issue.code for issue in corrupt.issues
        }
    finally:
        if not host_closed:
            host.close()


def test_result_analysis_citation_refs_exclude_dependency_facts(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 13)
    run_id = "expert-loop-analysis-dependency-ref-scope"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    contract["components"][1]["dependencies"] = ["primary.parallel_1"]
    contract["budget"]["max_experiments"] = 3
    host = WorkflowHost(tmp_path / "state")
    host.agent = _ReviseDependencyOnceAgent()
    # Retained single-root family/UPDATE inputs; the ACI14 selected-tip reader
    # is exercised by test_downstream_freezes_adjudicated_first_chain_not_later_completed_peer.
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=payload,
        ))
        assert created.status == "accepted", created

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("dependent expert loop did not close")

        analysis_requests = [
            AgentRequest.from_dict(json.loads(row["observation_json"])["request"])
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "AGENT"
            and json.loads(row["observation_json"]).get("request", {})
            .get("command") == AgentCommand.RESULT_ANALYSIS.value
            and json.loads(row["observation_json"])["request"]
            .get("component_id") == "primary.parallel_2"
        ]
        assert analysis_requests, "dependent component ran no RESULT_ANALYSIS"
        analysis = analysis_requests[0]
        input_refs = {item.ref for item in analysis.input_artifacts}

        upstream_facts = {
            str(row["fact_id"])
            for row in host.store.list_facts(run_id)
            if row["payload"].get("component_id") == "primary.parallel_1"
        }
        assert upstream_facts
        assert not upstream_facts & input_refs, (
            f"dependency facts leaked into analysis citation refs: "
            f"{sorted(upstream_facts & input_refs)}"
        )

        own_facts = {
            str(row["fact_id"])
            for row in host.store.list_facts(run_id)
            if row["payload"].get("component_id") == "primary.parallel_2"
            and row["fact_type"] not in {
                "ExperimentPreregistered", "CanonicalOutcomeAccepted",
            }
        }
        assert own_facts & input_refs, (
            "own-component facts must stay citable for analysis"
        )

        # Upstream conclusions remain available through the terminal update
        # response input, which the Oracle dependency audit requires.
        assert any(ref.endswith("--response") for ref in input_refs)

        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
    finally:
        host.close()


def test_required_input_family_is_corrected_before_experiment_design(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-required-family-retry"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    contract["protocols"]["parallel-score"]["requirements"][
        "required_artifacts"
    ] = ["handoff"]
    contract["components"][0]["required_artifacts"] = ["handoff"]
    contract["components"][1]["required_artifacts"] = ["handoff"]
    contract["components"][1]["dependencies"] = ["primary.parallel_1"]
    contract["components"][1]["required_inputs"] = [{
        "resource": "upstream-handoff",
        "produced_by": "primary.parallel_1",
        "artifact": "handoff",
        "min_count": 1,
    }]
    host = WorkflowHost(tmp_path / "state")
    host.agent = _WrongDependentFamilyOnceAgent()
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created

        # /45 reached 24 successful Agents/two CPU outcomes at 30s, with the
        # intended invalid-family retry already corrected. Allow report closure.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("required-input family retry did not close")

        synthesis = []
        for row in host.store.list_attempts():
            if row["run_id"] != run_id or row["kind"] != "AGENT":
                continue
            observation = json.loads(row["observation_json"])
            request = AgentRequest.from_dict(observation["request"])
            task = request.task_context
            if (
                request.component_id == "primary.parallel_2"
                and request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                and isinstance(task, ResearchNodeTask)
                and task.key.subkind is ResearchNodeSubkind.SYNTHESIS
            ):
                synthesis.append((row, request, observation))
        assert len(synthesis) == 2
        assert [item[0]["terminal_reason"] for item in synthesis] == [
            "FAILED", "SUCCEEDED",
        ]
        assert len({item[1].command_id for item in synthesis}) == 1
        assert synthesis[0][2]["failure_code"] == "invalid_candidate"
        assert any(
            item.ref.endswith("--candidate-validation-error")
            for item in synthesis[1][1].input_artifacts
        )
        hypotheses = [
            row for row in host.store.list_facts(run_id, "HypothesisProposed")
            if row["payload"]["component_id"] == "primary.parallel_2"
        ]
        assert len(hypotheses) == 1
        assert hypotheses[0]["payload"]["method_family_id"] == "fake-family-v1"
        verdict = host.store.list_facts(run_id, "GoalVerdictRecorded")[0]["payload"]
        assert verdict["verdict"] == "GOAL_MET"
        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()

        analysis = next(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"]
            == AgentCommand.RESULT_ANALYSIS.value
            and json.loads(row["observation_json"])["request"]["component_id"]
            == "primary.parallel_2"
        )
        analysis_observation = json.loads(analysis["observation_json"])
        failed_observation = {
            "request": analysis_observation["request"],
            "failure_code": "synthetic_analysis_loss",
        }
        tampered_db = tmp_path / "failed-analysis.sqlite"
        _backup_database(host.state_root / "state.sqlite", tampered_db)
        with sqlite3.connect(tampered_db) as connection:
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            connection.execute(
                "UPDATE execution_attempts SET terminal_reason='FAILED', "
                "observation_json=? WHERE attempt_id=?",
                (
                    json.dumps(failed_observation, sort_keys=True, separators=(",", ":")),
                    analysis["attempt_id"],
                ),
            )
        tampered = Oracle(tampered_db, host.state_root / "artifacts").audit(run_id)
        assert "verdict_component_conjunction_mismatch" in {
            issue.code for issue in tampered.issues
        }
    finally:
        host.close()


def test_required_input_binds_numeric_producer_tip_after_ordinal_nine(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 13)
    run_id = "expert-loop-required-input-ordinal-10"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    contract["protocols"]["parallel-score"]["requirements"][
        "required_artifacts"
    ] = ["handoff"]
    contract["components"][0]["required_artifacts"] = ["handoff"]
    contract["components"][1]["required_artifacts"] = ["handoff"]
    contract["components"][1]["dependencies"] = ["primary.parallel_1"]
    contract["components"][1]["required_inputs"] = [{
        "resource": "upstream-handoff",
        "produced_by": "primary.parallel_1",
        "artifact": "handoff",
        "min_count": 1,
    }]
    contract["budget"]["max_experiments"] = 11
    contract["roles"][0]["budget"]["max_experiments"] = 10
    host = WorkflowHost(tmp_path / "state")
    host.agent = _ReviseDependencyThroughCycleNineAgent()
    # Keep the numeric single-root ordinal regression. Explicit ACI14 chains
    # use chain_iteration and selected ADJ, covered by test_expert_batch_host.
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, f"create-{run_id}", 0, "CREATE_RUN", payload,
        ))
        assert created.status == "accepted", created

        # Eleven actual isolated CPU executions plus growing report/Oracle work:
        # /45 reached 105 successful Agents/ten CPU outcomes at 360s. Keep the
        # ordinal-10 downstream binding and full Oracle checks, with bounded
        # time for the remaining consumer/report work rather than fewer cycles.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("ordinal-10 producer run did not close")

        creations = {
            str(row["workflow_id"]): json.loads(row["payload_json"])
            for row in host.store.inspect(
                "SELECT workflow_id,payload_json FROM workflow_transitions "
                "WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1",
                (run_id,),
            )
        }
        producer_outcomes = [
            row for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            if row["payload"]["component_id"] == "primary.parallel_1"
        ]
        assert sorted(
            int(creations[str(row["experiment_id"])]["experiment_ordinal"])
            for row in producer_outcomes
        ) == list(range(1, 11))
        by_ordinal = {
            int(creations[str(row["experiment_id"])]["experiment_ordinal"]): row
            for row in producer_outcomes
        }
        consumer_spec = next(
            row for row in host.store.list_facts(run_id, "ExperimentPreregistered")
            if row["payload"]["component_id"] == "primary.parallel_2"
        )
        binding = consumer_spec["payload"]["upstream_outcomes"][0]
        assert binding["outcome_id"] == by_ordinal[10]["outcome_id"]
        assert binding["artifact_id"] == by_ordinal[10]["artifact_id"]
        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()

        final_update = next(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and row["kind"] == "AGENT"
            and (request := AgentRequest.from_dict(
                json.loads(row["observation_json"])["request"]
            )).component_id == "primary.parallel_1"
            and request.command is AgentCommand.HYPOTHESIS_UPDATE
            and isinstance(request.task_context, ResearchNodeTask)
            and request.task_context.key.cycle == 10
        )
        revised_observation = json.loads(final_update["observation_json"])
        revised_response = AgentResponse.from_dict(revised_observation["response"])
        revised_candidate = dict(revised_response.typed_candidate or {})
        revised_candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
        revised_observation["response"] = with_output_digest(replace(
            revised_response,
            typed_candidate=revised_candidate,
            output_digest="",
        )).to_dict()
        revised_db = tmp_path / "unterminated-v7.sqlite"
        _backup_database(host.state_root / "state.sqlite", revised_db)
        with sqlite3.connect(revised_db) as connection:
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            connection.execute(
                "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                (
                    json.dumps(
                        revised_observation, sort_keys=True, separators=(",", ":")
                    ),
                    final_update["attempt_id"],
                ),
            )
        revised = Oracle(revised_db, host.state_root / "artifacts").audit(run_id)
        assert "verdict_component_conjunction_mismatch" in {
            issue.code for issue in revised.issues
        }
        retained_v6_db = tmp_path / "unterminated-v6.sqlite"
        _backup_database(revised_db, retained_v6_db)
        with sqlite3.connect(retained_v6_db) as connection:
            connection.execute("DROP TRIGGER immutable_workflow_runs_update")
            connection.execute(
                "UPDATE workflow_runs SET research_contract_version="
                "'hypoloom-research-v6' WHERE run_id=?",
                (run_id,),
            )
        retained_v6 = Oracle(
            retained_v6_db, host.state_root / "artifacts",
        ).audit(run_id)
        assert "verdict_component_conjunction_mismatch" not in {
            issue.code for issue in retained_v6.issues
        }

        analysis = next(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and row["kind"] == "AGENT"
            and (request := AgentRequest.from_dict(
                json.loads(row["observation_json"])["request"]
            )).component_id == "primary.parallel_1"
            and request.command is AgentCommand.RESULT_ANALYSIS
            and isinstance(request.task_context, ResearchNodeTask)
            and request.task_context.key.cycle == 10
            and request.task_context.key.experiment_ordinal == 10
        )
        analysis_request = AgentRequest.from_dict(
            json.loads(analysis["observation_json"])["request"]
        )
        bad_analysis = _replace_observation_input(
            json.loads(analysis["observation_json"]),
            f"{analysis_request.command_id}--current-outcome",
            b"{}",
        )
        bad_analysis_response = AgentResponse.from_dict(bad_analysis["response"])
        cascaded_update = _replace_observation_input(
            json.loads(final_update["observation_json"]),
            f"{analysis['attempt_id']}--response",
            canonical_json(bad_analysis_response.to_dict()),
        )
        bad_outcome_db = tmp_path / "analysis-wrong-outcome-bytes.sqlite"
        _backup_database(host.state_root / "state.sqlite", bad_outcome_db)
        with sqlite3.connect(bad_outcome_db) as connection:
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            connection.executemany(
                "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                (
                    (
                        json.dumps(bad_analysis, sort_keys=True, separators=(",", ":")),
                        analysis["attempt_id"],
                    ),
                    (
                        json.dumps(
                            cascaded_update, sort_keys=True, separators=(",", ":")
                        ),
                        final_update["attempt_id"],
                    ),
                ),
            )
        bad_outcome = Oracle(
            bad_outcome_db, host.state_root / "artifacts",
        ).audit(run_id)
        assert "verdict_component_conjunction_mismatch" in {
            issue.code for issue in bad_outcome.issues
        }

        tip_hypothesis_id = creations[
            str(by_ordinal[10]["experiment_id"])
        ]["hypothesis_id"]
        tip_assessment = next(
            row for row in host.store.list_facts(run_id, "HypothesisAssessed")
            if str(row["hypothesis_id"]) == tip_hypothesis_id
        )
        bad_update = _replace_observation_input(
            json.loads(final_update["observation_json"]),
            str(tip_assessment["fact_id"]),
            b"{}",
        )
        bad_assessment_db = tmp_path / "update-wrong-assessment-bytes.sqlite"
        _backup_database(host.state_root / "state.sqlite", bad_assessment_db)
        with sqlite3.connect(bad_assessment_db) as connection:
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            connection.execute(
                "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                (
                    json.dumps(bad_update, sort_keys=True, separators=(",", ":")),
                    final_update["attempt_id"],
                ),
            )
        bad_assessment = Oracle(
            bad_assessment_db, host.state_root / "artifacts",
        ).audit(run_id)
        assert "verdict_component_conjunction_mismatch" in {
            issue.code for issue in bad_assessment.issues
        }

        stale_payload = copy.deepcopy(consumer_spec["payload"])
        stale_payload["upstream_outcomes"][0].update({
            "outcome_id": by_ordinal[9]["outcome_id"],
            "artifact_id": by_ordinal[9]["artifact_id"],
        })
        tampered_db = tmp_path / "stale-producer-tip.sqlite"
        _backup_database(host.state_root / "state.sqlite", tampered_db)
        with sqlite3.connect(tampered_db) as connection:
            connection.execute("DROP TRIGGER immutable_research_facts_update")
            connection.execute(
                "UPDATE research_facts SET outcome_id=?,artifact_id=?,payload_json=? "
                "WHERE fact_id=?",
                (
                    by_ordinal[9]["outcome_id"],
                    by_ordinal[9]["artifact_id"],
                    json.dumps(stale_payload, sort_keys=True, separators=(",", ":")),
                    consumer_spec["fact_id"],
                ),
            )
        tampered = Oracle(tampered_db, host.state_root / "artifacts").audit(run_id)
        assert "component_input_binding_mismatch" in {
            issue.code for issue in tampered.issues
        }
        legacy_db = tmp_path / "v6-stale-producer-tip.sqlite"
        _backup_database(tampered_db, legacy_db)
        with sqlite3.connect(legacy_db) as connection:
            connection.execute("DROP TRIGGER immutable_workflow_runs_update")
            connection.execute(
                "UPDATE workflow_runs SET research_contract_version="
                "'hypoloom-research-v6' WHERE run_id=?",
                (run_id,),
            )
        legacy = Oracle(legacy_db, host.state_root / "artifacts").audit(run_id)
        # Cycles 1..9 are now legitimate local REFUTED revisions rather than
        # supported tips.  Binding the consumer to ordinal 9 is therefore an
        # ordinary bad scientific input even under the retained v6 reading.
        assert "component_input_binding_mismatch" in {
            issue.code for issue in legacy.issues
        }
    finally:
        host.close()
