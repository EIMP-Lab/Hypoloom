from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentRequest, AgentResponse, WorkspaceArtifact, request_digest, with_output_digest
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.fake_agent import FakeAgent, FakeAgentMode, _redigest
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import PROFILE_FILES, admit_profile
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]


class _FailingNodeAgent:
    def __init__(
        self,
        command: AgentCommand,
        *,
        subkind: ResearchNodeSubkind | None = None,
        cycle: int | None = None,
        revise_cycle_one: bool = False,
    ) -> None:
        self.command = command
        self.subkind = subkind
        self.cycle = cycle
        self.revise_cycle_one = revise_cycle_one
        self.delegate = FakeAgent()
        self.failure = FakeAgent(FakeAgentMode.NONZERO_EXIT)
        self.requests: list[AgentRequest] = []

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        self.requests.append(request)
        task = request.task_context
        matches = request.command is self.command
        if self.subkind is not None:
            matches = (
                matches
                and isinstance(task, ResearchNodeTask)
                and task.key.subkind is self.subkind
            )
        if self.cycle is not None:
            matches = (
                matches
                and isinstance(task, ResearchNodeTask)
                and task.key.cycle == self.cycle
            )
        if matches:
            return self.failure.invoke(request)
        response = self.delegate.invoke(request)
        if (
            self.revise_cycle_one
            and isinstance(response, AgentResponse)
            and request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 1
        ):
            artifacts = tuple(
                WorkspaceArtifact.from_bytes(
                    item.path,
                    item.content_bytes().replace(
                        b'"accuracy": 0.55', b'"accuracy": 0.50'
                    ),
                )
                for item in response.proposed_workspace_artifacts
            )
            assert artifacts != response.proposed_workspace_artifacts
            return _redigest(replace(
                response,
                proposed_workspace_artifacts=artifacts,
            ))
        if (
            self.revise_cycle_one
            and isinstance(response, AgentResponse)
            and request.command is AgentCommand.HYPOTHESIS_UPDATE
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 1
        ):
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
            return _redigest(replace(response, typed_candidate=candidate))
        return response


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-expert-failure-", dir="/var/tmp"
    ) as raw:
        yield Path(raw)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments), cwd=repository, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _payload(tmp_path: Path, mount_root: Path, run_id: str) -> dict[str, object]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text("expert failure target\n", encoding="utf-8")
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target, "-c", "user.name=Hypoloom Test", "-c",
        "user.email=hypoloom@example.invalid", "commit", "-qm", "base",
    )
    data = mount_root / "data"
    data.mkdir()
    (data / "README.txt").write_text("fixture\n", encoding="utf-8")
    source = REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in PROFILE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-target", str(target.resolve())
        )
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-data", str(data.resolve())
        )
        if name == "safety.yaml":
            text = "allow_read_paths: []\nsandbox_os: linux\n"
        (profile / name).write_text(text, encoding="utf-8")
    report = admit_profile(profile, run_id=run_id)
    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    return report.create_run_payload


def _drive(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        host.tick()
        head = host.store.latest_transition(run_id)
        if head is not None and head["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return
        time.sleep(0.005)
    raise AssertionError(f"expert failure run did not terminate: {host.store.latest_transition(run_id)}")


def _request(row: dict[str, object]) -> dict[str, object]:
    return json.loads(str(row["observation_json"]))["request"]


def _assert_exact_final_failure(
    host: WorkflowHost,
    run_id: str,
    command: AgentCommand,
    subkind: ResearchNodeSubkind | None,
    cycle: int | None = None,
) -> None:
    attempts = []
    for row in host.store.list_attempts():
        if row["run_id"] != run_id or row["kind"] != "AGENT":
            continue
        request = _request(row)
        task = request.get("task_context")
        key = task.get("key") if isinstance(task, dict) else None
        if request["command"] != command.value:
            continue
        if subkind is not None and (
            not isinstance(key, dict) or key.get("subkind") != subkind.value
        ):
            continue
        if cycle is not None and (
            not isinstance(key, dict) or key.get("cycle") != cycle
        ):
            continue
        attempts.append(row)
    retry_budget = host._research_contract(run_id).document["budget"]["max_retries"]
    assert isinstance(retry_budget, int) and not isinstance(retry_budget, bool)
    assert len(attempts) == retry_budget + 1
    assert all(row["state"] == "CLOSED" for row in attempts)
    transitions = host.store.inspect(
        "SELECT payload_json FROM workflow_transitions WHERE run_id=? "
        "AND json_extract(payload_json,'$.agent_final_failure')=1 "
        "AND json_type(payload_json,'$.failed_task_command_id')='text'",
        (run_id,),
    )
    matching = []
    for row in transitions:
        payload = json.loads(row["payload_json"])
        if payload["failed_task_command_id"] == attempts[-1]["command_id"]:
            matching.append(payload)
    assert len(matching) == 1
    assert matching[0]["attempt_id"] == attempts[-1]["attempt_id"]


class _InvalidCycleReportAgent(FakeAgent):
    """Every report proposes the wrong cycle file; all other nodes are real fixtures."""

    def __init__(self, *, fail_self: bool = False) -> None:
        super().__init__()
        self.fail_self = fail_self

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        if self.fail_self and request.command is AgentCommand.SELF_EVOLVE:
            return FakeAgent(FakeAgentMode.NONZERO_EXIT).invoke(request)
        response = super().invoke(request)
        if request.command is AgentCommand.CYCLE_REPORT:
            assert isinstance(response, AgentResponse)
            artifacts = tuple(WorkspaceArtifact.from_bytes(
                "rounds/999.md" if item.path.startswith("rounds/") else item.path,
                item.content_bytes(),
            ) for item in response.proposed_workspace_artifacts)
            assert artifacts != response.proposed_workspace_artifacts
            return _redigest(replace(response, proposed_workspace_artifacts=artifacts))
        return response


@pytest.mark.parametrize(("restart", "fail_self"), [(False, False), (True, False), (True, True)],
    ids=["same-host", "restart", "restart-self-exhausted"])
def test_permanent_cycle_report_failure_closes_without_rewriting_accepted_science(
    tmp_path: Path, mount_root: Path, restart: bool, fail_self: bool,
) -> None:
    run_id = "permanent-cycle-report-failure"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    host.agent = _InvalidCycleReportAgent(fail_self=fail_self)
    try:
        host.initialize()
        assert host.handle(ControlRequest(1, "create-" + run_id, 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id))).status == "accepted"
        assert host.store.get_run(run_id)["agent_aci_version"] == "20"
        deadline = time.monotonic() + 30
        failure = None
        while time.monotonic() < deadline:
            host.tick()
            failures = host._expert_final_failures(run_id)
            if failures:
                failure = failures[0]
                break
            time.sleep(0.005)
        assert failure is not None, "invalid report never reached its durable final failure"
        failed = host.store.get_attempt(failure["attempt_id"])
        assert failed is not None and _request(failed)["command"] == "CYCLE_REPORT"
        _assert_exact_final_failure(host, run_id, AgentCommand.CYCLE_REPORT, None)
        scientific_facts = {
            kind: host.store.list_facts(run_id, kind)
            for kind in ("CanonicalOutcomeAccepted", "HypothesisAssessed")
        }
        assert [row["payload"]["outcome"] for row in scientific_facts["CanonicalOutcomeAccepted"]] == ["SUCCESS"]
        assert [row["payload"]["assessment"] for row in scientific_facts["HypothesisAssessed"]] == ["SUPPORTED"]
        if restart:
            host.close()
            host = WorkflowHost(state_root)
            host.agent = _InvalidCycleReportAgent(fail_self=fail_self)
            host.initialize()
        # The only remaining work is bounded local control/report closure, not
        # another experiment or another exhausted report retry loop.
        for _ in range(60):
            host.tick()
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.005)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        for kind, rows in scientific_facts.items():
            assert host.store.list_facts(run_id, kind) == rows
        reports = [row for row in host.store.list_attempts() if row["kind"] == "AGENT"
                   and _request(row)["command"] == "CYCLE_REPORT"]
        assert reports and all(row["terminal_reason"] == "FAILED" for row in reports)
        assert all("memory" not in json.loads(row["observation_json"]) for row in reports)
        _assert_exact_final_failure(host, run_id, AgentCommand.CYCLE_REPORT, None)
        assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
        assert host.store.list_facts(run_id, "GoalVerdictRecorded")[0]["payload"]["verdict"] == "INCONCLUSIVE"
        followups = {command: [row for row in host.store.list_attempts()
            if row["kind"] == "AGENT" and _request(row)["command"] == command]
            for command in ("SELF_EVOLVE", "FINAL_REPORT", "GRAPH_EVOLVE", "OBSERVER")}
        assert not followups["GRAPH_EVOLVE"] and not followups["OBSERVER"]
        for command in ("SELF_EVOLVE", "FINAL_REPORT"):
            failed_self = fail_self and command == "SELF_EVOLVE"
            if failed_self:
                _assert_exact_final_failure(host, run_id, AgentCommand.SELF_EVOLVE, None)
            else:
                assert len(followups[command]) == 1
            for row in followups[command]:
                assert row["terminal_reason"] == ("FAILED" if failed_self else "SUCCEEDED")
                observation = json.loads(row["observation_json"])
                request = AgentRequest.from_dict(observation["request"])
                source = next(item for item in request.input_artifacts if item.ref.endswith("--final-failure"))
                assert source.ref == failed["attempt_id"] + "--final-failure"
                assert json.loads(source.content_bytes()) == {"attempt": failed, "control": failure}
                if command == "FINAL_REPORT":
                    response = AgentResponse.from_dict(observation["response"])
                    assert failed["attempt_id"].encode() in response.proposed_workspace_artifacts[0].content_bytes()
        report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
        assert report.ok, report.to_json()
        # A forged source still fails independent raw-row validation after the
        # ordinary request/response transport digests are recomputed.
        for command, code in (("SELF_EVOLVE", "batch_closure_invalid"),
                              ("FINAL_REPORT", "final_report_source_or_publication_invalid")):
            if command == "SELF_EVOLVE" and fail_self: continue
            row = followups[command][0]
            observation = json.loads(row["observation_json"])
            request = AgentRequest.from_dict(observation["request"])
            source = next(item for item in request.input_artifacts if item.ref.endswith("--final-failure"))
            value = json.loads(source.content_bytes())
            value["attempt"]["terminal_reason"] = "SUCCEEDED"
            changed = type(source).from_bytes(source.ref, source.media_type, json.dumps(value).encode())
            request = replace(request, input_artifacts=tuple(changed if item is source else item for item in request.input_artifacts))
            response = AgentResponse.from_dict(observation["response"])
            observation["request"] = request.to_dict()
            observation["response"] = with_output_digest(replace(response,
                input_digest=request_digest(request), output_digest="")).to_dict()
            database = tmp_path / f"forged-{command.lower()}-source.sqlite"
            with sqlite3.connect(state_root / "state.sqlite") as original, sqlite3.connect(database) as copy:
                original.backup(copy)
                copy.execute("DROP TRIGGER execution_attempt_update_rules")
                copy.execute("UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                    (json.dumps(observation), row["attempt_id"]))
            broken = Oracle(database, state_root / "artifacts").audit(run_id)
            assert code in {issue.code for issue in broken.issues}, broken.to_json()
        attempts_before = host.store.list_attempts()
        failures_before = host._expert_final_failures(run_id)
        host.close()
        host = WorkflowHost(state_root)
        host.agent = _InvalidCycleReportAgent(fail_self=fail_self)
        host.initialize()
        for _ in range(3): host.tick()
        assert host.store.list_attempts() == attempts_before
        assert host._expert_final_failures(run_id) == failures_before
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()


@pytest.mark.parametrize(
    ("command", "subkind"),
    (
        (AgentCommand.GROUNDING, None),
        (AgentCommand.LITERATURE_REVIEW, None),
        (AgentCommand.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.FORWARD),
        (AgentCommand.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.BACKWARD),
        (AgentCommand.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.SYNTHESIS),
        (AgentCommand.RESULT_ANALYSIS, None),
        (AgentCommand.HYPOTHESIS_UPDATE, None),
    ),
)
def test_required_expert_node_exhaustion_closes_honestly(
    tmp_path: Path,
    mount_root: Path,
    command: AgentCommand,
    subkind: ResearchNodeSubkind | None,
) -> None:
    run_id = f"expert-failure-{command.value.lower()}-{(subkind or ResearchNodeSubkind.MAIN).value.lower()}"
    host = WorkflowHost(tmp_path / "state")
    host.agent = _FailingNodeAgent(command, subkind=subkind)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "INCONCLUSIVE"
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        if command in {
            AgentCommand.GROUNDING,
            AgentCommand.LITERATURE_REVIEW,
            AgentCommand.HYPOTHESIS_PROPOSAL,
        }:
            assert assessments == ()
        else:
            assert len(assessments) == 1
            if command is AgentCommand.RESULT_ANALYSIS:
                assert assessments[0]["payload"]["assessment"] == "SUPPORTED"
            # ACI14 can report the accepted scientific result after an analysis
            # or update failure, but cannot promote that incomplete lineage.
            commands = {_request(row)["command"] for row in host.store.list_attempts()
                        if row["kind"] == "AGENT" and row["terminal_reason"] == "SUCCEEDED"}
            assert {"ADJUDICATE", "CYCLE_REPORT", "SELF_EVOLVE", "FINAL_REPORT"} <= commands
        assert not [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["state"] == "OPEN"
        ]
        _assert_exact_final_failure(host, run_id, command, subkind)
        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
        if command is AgentCommand.HYPOTHESIS_UPDATE:
            report_attempt = next(row for row in host.store.list_attempts()
                if row["kind"] == "AGENT" and _request(row)["command"] == "CYCLE_REPORT")
            observation = json.loads(report_attempt["observation_json"])
            request = AgentRequest.from_dict(observation["request"])
            original = next(item for item in request.input_artifacts if item.ref.endswith("--final-failure"))
            failure_source = json.loads(original.content_bytes())
            assert failure_source["attempt"]["terminal_reason"] == "FAILED"
            assert "response" not in json.loads(failure_source["attempt"]["observation_json"])
            # Recompute the ordinary transport digests: the independent raw-row
            # closure audit must catch a fabricated successful update source.
            failure_source["attempt"]["terminal_reason"] = "SUCCEEDED"
            changed = type(original).from_bytes(original.ref, original.media_type, json.dumps(failure_source).encode())
            request = replace(request, input_artifacts=tuple(changed if item is original else item for item in request.input_artifacts))
            response = AgentResponse.from_dict(observation["response"])
            observation["request"] = request.to_dict()
            observation["response"] = with_output_digest(replace(response, input_digest=request_digest(request), output_digest="")).to_dict()
            copy = tmp_path / "false-update-source.sqlite"
            with sqlite3.connect(host.state_root / "state.sqlite") as source, sqlite3.connect(copy) as destination:
                source.backup(destination)
                destination.execute("DROP TRIGGER execution_attempt_update_rules")
                destination.execute("UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                    (json.dumps(observation), report_attempt["attempt_id"]))
            broken = Oracle(copy, host.state_root / "artifacts").audit(run_id)
            assert "batch_closure_invalid" in {issue.code for issue in broken.issues}
    finally:
        host.close()


@pytest.mark.parametrize("command", [AgentCommand.RESULT_ANALYSIS, AgentCommand.HYPOTHESIS_UPDATE])
def test_exhausted_batch_node_recovers_control_closure_without_claiming_scientific_success(
    tmp_path: Path, mount_root: Path, command: AgentCommand,
) -> None:
    run_id = "failed-node-restart-" + command.value.lower()
    state_root = tmp_path / "state"
    agent = _FailingNodeAgent(command)
    host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        assert host.handle(ControlRequest(1, "create-" + run_id, 0, "CREATE_RUN",
            _payload(tmp_path, mount_root, run_id))).status == "accepted"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            host.tick()
            failures = host._expert_final_failures(run_id)
            if failures:
                break
        else:
            pytest.fail("required node did not reach its actual final failure")
        failed_id = failures[0]["attempt_id"]
        host.close()
        host = WorkflowHost(state_root)
        host.agent = agent
        host.initialize()
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        assert host.store.list_facts(run_id, "HypothesisAssessed")[0]["payload"]["assessment"] == "SUPPORTED"
        assert host.store.list_facts(run_id, "GoalVerdictRecorded")[0]["payload"]["verdict"] == "INCONCLUSIVE"
        assert [item["attempt_id"] for item in host._expert_final_failures(run_id)] == [failed_id]
        assert all(row["state"] == "CLOSED" for row in host.store.list_attempts())
        report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
        assert report.ok, report.to_json()
    finally:
        host.close()


def test_expert_budget_rejection_is_a_zero_charge_local_attempt(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-local-budget-rejection"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    contract["budget"]["max_turns"] = 24
    host = WorkflowHost(tmp_path / "state")
    agent = _FailingNodeAgent(AgentCommand.GROUNDING)
    host.agent = agent
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=payload,
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)

        attempts = tuple(
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "AGENT"
        )
        rejected = tuple(
            row for row in attempts
            if json.loads(str(row["observation_json"])).get("failure_code")
            == "profile_budget_exhausted"
        )
        assert {_request(row)["command"] for row in rejected} == {"GROUNDING", "FINAL_REPORT"}
        assert len(rejected) == 2
        assert host.store.latest_transition(run_id)["state"] == "FAILED"
        assert host.store.list_facts(run_id, "GoalVerdictRecorded")[0]["payload"]["verdict"] == "INCONCLUSIVE"
        for row in rejected:
            observation = json.loads(str(row["observation_json"]))
            assert observation["budget_rejected"] is True
            assert row["attempt_id"] not in {request.attempt_id for request in agent.requests}
            charge = next(item for item in fold_agent_attempts(attempts) if item.attempt_id == row["attempt_id"])
            assert (charge.source, charge.tokens, charge.turns, str(charge.cost)) == ("rejected", 0, 0, "0")
        assert len(agent.requests) == len(attempts) - len(rejected)
        status = host.handle(ControlRequest(
            1, f"status-{run_id}", None, "STATUS", {"run_id": run_id},
        ))
        assert status.result is not None
        assert status.result["agent_usage"]["budget_rejected_attempts"] == 2
        report = Oracle(host.state_root / "state.sqlite", host.state_root / "artifacts").audit(run_id)
        assert report.ok, report.to_json()
    finally:
        host.close()


def test_successor_proposal_exhaustion_cannot_reuse_parent_goal_met(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-successor-literature-failure"
    host = WorkflowHost(tmp_path / "state")
    host.agent = _FailingNodeAgent(
        AgentCommand.LITERATURE_REVIEW,
        cycle=2,
        revise_cycle_one=True,
    )
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
            failures = host.store.inspect(
                "SELECT 1 FROM workflow_transitions WHERE run_id=? "
                "AND json_extract(payload_json,'$.agent_final_failure')=1 "
                "AND json_type(payload_json,'$.failed_task_command_id')='text'",
                (run_id,),
            )
            if failures:
                break
        else:
            raise AssertionError("successor literature failure was not selected")
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        assert len(host.store.list_facts(run_id, "HypothesisProposed")) == 1
        assert len(host.store.list_facts(run_id, "HypothesisAssessed")) == 1
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "INCONCLUSIVE"
        _assert_exact_final_failure(
            host, run_id, AgentCommand.LITERATURE_REVIEW, None, cycle=2,
        )
        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
    finally:
        host.close()


def test_failed_analysis_control_closure_overrides_a_later_revise_suggestion() -> None:
    from datetime import datetime, timezone
    from test_batch_oracle import _closed_cycle, _input

    data = _closed_cycle()
    analysis = next(row for row in data.attempts if row["attempt_id"] == "analysis-1")
    analysis["terminal_reason"] = "FAILED"
    request = data.attempt_payloads["analysis-1"]["request"]
    data.attempt_payloads["analysis-1"].pop("response")
    failed = data.transition("AGENT_ATTEMPT_FINAL_FAILURE", {
        "agent_final_failure": True, "attempt_id": "analysis-1",
        "failed_task_command_id": request["command_id"],
        "role": request["role"], "component_id": request["component_id"],
    })
    data.transitions.pop()
    index = next(index for index, row in enumerate(data.transitions)
                 if row["command_type"] == "ACCEPT_ADJUDICATION")
    judged = next(row for row in data.attempts if row["attempt_id"] == "adjudicate")
    failed["created_at"] = datetime.fromtimestamp(judged["started_at"] - 0.1, timezone.utc).isoformat()
    data.transitions.insert(index, failed)
    revisions = {}
    for ordinal, row in enumerate(data.transitions, 1):
        owner = row["workflow_id"]
        revisions[owner] = revisions.get(owner, 0) + 1
        row.update(raw_order=ordinal, revision=revisions[owner])
    update = data.attempt_payloads["update"]["response"]
    update["typed_candidate"]["update_disposition"] = "REVISE_NEXT_CYCLE"
    report = data.attempt_payloads["report"]["request"]
    report["input_artifacts"] = [_input("update--response", update).to_dict()]
    assert data.audit() == []
