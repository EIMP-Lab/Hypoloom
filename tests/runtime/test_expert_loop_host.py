from __future__ import annotations

import base64
import json
import sqlite3
import stat
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.agent_aci import (
    AgentRequest,
    AgentResponse,
    WorkspaceArtifact,
    hypothesis_update_current_assessment,
)
from hypoloom.debug_mode import (
    build_debug_report,
    validate_debug_fixture,
    write_debug_fixture,
)
from hypoloom.domain import AgentCommand, HypothesisAssessment
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.fake_agent import FakeAgent, _redigest
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import PROFILE_FILES, PROFILE_V3_FILES, admit_profile
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def retained_single_lineage_protocol(monkeypatch):
    """Keep this predecessor's exact node/order/Oracle-code regression explicit.

    Fresh ACI14 batches, including legacy-profile admission, are covered by
    test_expert_batch_host and the current-candidate/native Host tests.
    """
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 13)


class _ReviseOnceAgent:
    def __init__(self) -> None:
        self.delegate = FakeAgent()
        self.update_assessments: list[HypothesisAssessment | None] = []

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        if request.command is AgentCommand.HYPOTHESIS_UPDATE:
            self.update_assessments.append(
                hypothesis_update_current_assessment(request)
            )
        if (isinstance(response, AgentResponse)
                and request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
                and isinstance(request.task_context, ResearchNodeTask)
                and request.task_context.key.cycle == 1):
            artifacts = tuple(WorkspaceArtifact.from_bytes(
                item.path, item.content_bytes().replace(b'"accuracy": 0.55', b'"accuracy": 0.50'),
            ) for item in response.proposed_workspace_artifacts)
            assert artifacts != response.proposed_workspace_artifacts
            return _redigest(replace(response, proposed_workspace_artifacts=artifacts))
        if (isinstance(response, AgentResponse)
                and request.command is AgentCommand.HYPOTHESIS_UPDATE
                and isinstance(request.task_context, ResearchNodeTask)
                and request.task_context.key.cycle == 1):
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
            return _redigest(replace(response, typed_candidate=candidate))
        return response


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments), cwd=repository, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(prefix="hypoloom-expert-loop-", dir="/var/tmp") as raw:
        yield Path(raw)


def _pilot_payload(tmp_path: Path, mount_root: Path, run_id: str) -> dict[str, object]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text("bounded expert-loop target\n", encoding="utf-8")
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "-c", "user.name=Hypoloom Test", "-c",
         "user.email=hypoloom@example.invalid", "commit", "-qm", "base")
    data = mount_root / "data"
    data.mkdir()
    (data / "README.txt").write_text("bounded fixture metadata\n", encoding="utf-8")
    source = REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in PROFILE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace("/REPLACE_ME/expert-loop-pilot-target", str(target.resolve()))
        text = text.replace("/REPLACE_ME/expert-loop-pilot-data", str(data.resolve()))
        if name == "safety.yaml":
            text = "allow_read_paths: []\nsandbox_os: linux\n"
        (profile / name).write_text(text, encoding="utf-8")
    report = admit_profile(profile, run_id=run_id)
    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    return report.create_run_payload


def _pilot_payload_v3(tmp_path: Path, mount_root: Path, run_id: str) -> dict[str, object]:
    target = mount_root / "target-v3"
    target.mkdir()
    (target / "README.md").write_text("bounded profile-v3 target\n", encoding="utf-8")
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "-c", "user.name=Hypoloom Test", "-c",
         "user.email=hypoloom@example.invalid", "commit", "-qm", "base")
    data = mount_root / "data-v3"
    data.mkdir()
    profile = tmp_path / "profile-v3"
    profile.mkdir()
    source = REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot-v3"
    for name in PROFILE_V3_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace("/REPLACE_ME/expert-loop-pilot-target", str(target.resolve()))
        text = text.replace("/REPLACE_ME/expert-loop-pilot-data", str(data.resolve()))
        if name == "language.yaml":
            text = "default: en\noverrides:\n  hypothesis_update: zh-CN\n"
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
    raise AssertionError(f"expert-loop run did not terminate: {host.store.latest_transition(run_id)}")


def _authority_snapshot(
    state_root: Path,
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    with sqlite3.connect(
        f"file:{(state_root / 'state.sqlite').resolve().as_posix()}?mode=ro",
        uri=True,
    ) as connection:
        tables = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return tuple(
            (
                table,
                tuple(tuple(row) for row in connection.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid',
                )),
            )
            for table in tables
        )


def _artifact_snapshot(
    root: Path,
) -> tuple[tuple[str, int, bytes | str | None], ...]:
    if not root.exists():
        return ()
    entries: list[tuple[str, int, bytes | str | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        content: bytes | str | None = None
        if stat.S_ISREG(metadata.st_mode):
            content = path.read_bytes()
        elif stat.S_ISLNK(metadata.st_mode):
            content = path.readlink().as_posix()
        entries.append((path.relative_to(root).as_posix(), metadata.st_mode, content))
    return tuple(entries)


def test_fake_successor_closes_grounding_pincer_design_execution_analysis_and_update(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-fake"
    host = WorkflowHost(tmp_path / "state")
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_pilot_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        attempts = sorted((row for row in host.store.list_attempts()
                           if row["run_id"] == run_id and row["kind"] == "AGENT"),
                          key=lambda row: row["started_at"])
        commands = [json.loads(row["observation_json"])["request"]["command"]
                    for row in attempts]
        assert commands == [
            AgentCommand.LITERATURE_REVIEW.value,
            AgentCommand.GROUNDING.value,
            AgentCommand.LITERATURE_REVIEW.value,
            AgentCommand.LITERATURE_REVIEW.value,
            AgentCommand.HYPOTHESIS_PROPOSAL.value,
            AgentCommand.HYPOTHESIS_PROPOSAL.value,
            AgentCommand.HYPOTHESIS_PROPOSAL.value,
            AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value,
            AgentCommand.EXPERIMENT_EXECUTION.value,
            AgentCommand.RESULT_ANALYSIS.value,
            AgentCommand.HYPOTHESIS_UPDATE.value,
            AgentCommand.CYCLE_REPORT.value,
        ]
        observations = [json.loads(row["observation_json"]) for row in attempts]
        report_observation = observations[-1]
        assert report_observation["memory"]["commit"] != report_observation["memory"]["base_commit"]
        from hypoloom.expert_memory import ExpertMemory
        report_files = ExpertMemory(host.state_root).files(run_id, report_observation["request"]["role"], report_observation["memory"]["commit"])
        assert set(report_files) == {"MEMORY.md", "rounds/1.md"}
        literature_index = next(index for index, observation in enumerate(observations)
            if observation["request"]["command"] == AgentCommand.LITERATURE_REVIEW.value
            and observation["request"]["task_context"].get("key", {}).get("subkind") == "MAIN")
        literature_ref = f"{attempts[literature_index]['attempt_id']}--response"
        proposal_requests = {
            observation["request"]["task_context"]["key"]["subkind"]: observation["request"]
            for observation in observations
            if observation["request"]["command"] == AgentCommand.HYPOTHESIS_PROPOSAL.value
        }
        proposal_attempts = {
            observation["request"]["task_context"]["key"]["subkind"]: row
            for row, observation in zip(attempts, observations, strict=True)
            if observation["request"]["command"] == AgentCommand.HYPOTHESIS_PROPOSAL.value
        }
        forward_refs = set(proposal_requests["FORWARD"]["immutable_context_refs"])
        backward_refs = set(proposal_requests["BACKWARD"]["immutable_context_refs"])
        synthesis_refs = set(proposal_requests["SYNTHESIS"]["immutable_context_refs"])
        assert literature_ref in forward_refs
        assert literature_ref not in backward_refs
        assert literature_ref not in synthesis_refs
        assert f"{proposal_attempts['FORWARD']['attempt_id']}--response" in synthesis_refs
        assert f"{proposal_attempts['BACKWARD']['attempt_id']}--response" in synthesis_refs
        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
        assert report.open_attempt_count == 0
        assert report.pending_effect_count == 0
        assert not (host.state_root / "git-authoring-worktrees").exists()
        design = next(row for row in attempts if json.loads(row["observation_json"])["request"]["command"] == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value)
        residue = host.state_root / "git-authoring-worktrees" / design["attempt_id"]
        residue.mkdir(parents=True)
        (residue / "partial-clone").mkdir()
        host._reconcile_terminal_attempt_residue(run_id)
        assert not residue.exists()
        abandoned = host.state_root / "git-authoring-worktrees" / "abandoned-design"
        abandoned.mkdir()
        (abandoned / "partial-clone").mkdir()
        host._remove_attempt_residue({
            "attempt_id": "abandoned-design",
            "kind": "AGENT",
        })
        assert not abandoned.exists()
    finally:
        host.close()


def test_profile_v3_language_routes_reach_only_the_matching_agent_node(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-profile-v3-language"
    host = WorkflowHost(tmp_path / "state-v3")
    expected = (
        "literature_review.query_plan",
        "grounding",
        "literature_review.query_plan",
        "literature_review.main",
        "hypothesis_proposal.forward",
        "hypothesis_proposal.backward",
        "hypothesis_proposal.synthesis",
        "experiment_design_implement",
        "experiment_execution",
        "result_analysis",
        "hypothesis_update",
        "cycle_report",
    )
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_pilot_payload_v3(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        attempts = sorted(
            (
                row
                for row in host.store.list_attempts()
                if row["run_id"] == run_id and row["kind"] == "AGENT"
            ),
            key=lambda row: row["started_at"],
        )
        resolved: list[dict[str, object]] = []
        for row in attempts:
            request = json.loads(row["observation_json"])["request"]
            config = next(
                item
                for item in request["input_artifacts"]
                if item["ref"].endswith(("--grounding-config", "--expert-config"))
            )
            content = json.loads(base64.b64decode(config["content_base64"]))
            resolved.append(content["language_route"])
        assert tuple(item["route"] for item in resolved) == expected
        assert all(set(item) == {"route", "output_language"} for item in resolved)
        assert all(item["output_language"] == "en" for item in resolved if item["route"] != "hypothesis_update")
        assert next(item for item in resolved if item["route"] == "hypothesis_update")["output_language"] == "zh-CN"
    finally:
        host.close()


def test_debug_export_validates_real_host_agent_and_process_without_mutation(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-debug-shape"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_pilot_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        attempts = tuple(
            row for row in host.store.list_attempts() if row["run_id"] == run_id
        )
        selected = (
            next(row for row in attempts if row["kind"] == "AGENT"),
            next(row for row in attempts if row["kind"] == "PROCESS"),
        )
        before = _authority_snapshot(state_root)
        artifact_inventory = _artifact_snapshot(state_root / "artifacts")

        for attempt in selected:
            report = build_debug_report(
                state_root,
                run_id,
                attempt_id=str(attempt["attempt_id"]),
                include_payloads=True,
            )
            fixture = write_debug_fixture(
                tmp_path / f"{attempt['attempt_id']}.json",
                report,
            )
            validation = validate_debug_fixture(
                fixture,
                validation_mode="candidate",
            )
            assert validation["valid"] is True, validation

        assert _authority_snapshot(state_root) == before
        assert _artifact_snapshot(state_root / "artifacts") == artifact_inventory
    finally:
        host.close()


def test_accepted_update_runs_one_parent_linked_successor_cycle(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-successor"
    host = WorkflowHost(tmp_path / "state")
    agent = _ReviseOnceAgent()
    host.agent = agent
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_pilot_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        hypotheses = host.store.list_facts(run_id, "HypothesisProposed")
        assert len(hypotheses) == 2
        assert hypotheses[1]["payload"]["parent_hypothesis_id"] == hypotheses[0]["hypothesis_id"]
        assert hypotheses[1]["payload"]["relation"] == "revises"
        assert len(host.store.list_facts(run_id, "ExperimentPreregistered")) == 2
        assert len(host.store.list_facts(run_id, "CanonicalOutcomeAccepted")) == 2
        assert len(host.store.list_facts(run_id, "HypothesisAssessed")) == 2
        updates = [json.loads(row["observation_json"])["response"]["typed_candidate"]
                   for row in host.store.list_attempts()
                   if row["kind"] == "AGENT"
                   and json.loads(row["observation_json"])["request"]["command"]
                   == AgentCommand.HYPOTHESIS_UPDATE.value]
        assert [item["update_disposition"] for item in updates] == [
            "REVISE_NEXT_CYCLE", "TERMINATE_LINEAGE",
        ]
        assert agent.update_assessments == [
            HypothesisAssessment.REFUTED,
            HypothesisAssessment.SUPPORTED,
        ]
        report = Oracle(
            host.state_root / "state.sqlite", host.state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
    finally:
        host.close()


@pytest.mark.parametrize("tamper", ("update_disposition", "creation_cycle"))
def test_successor_requires_parent_update_and_creation_authorization(
    tmp_path: Path,
    mount_root: Path,
    tamper: str,
) -> None:
    run_id = "expert-loop-successor-authorization"
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    host.agent = _ReviseOnceAgent()
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1, request_id=f"create-{run_id}", expected_revision=0,
            command="CREATE_RUN", payload=_pilot_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()

    connection = sqlite3.connect(state_root / "state.sqlite")
    try:
        if tamper == "update_disposition":
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            rows = connection.execute(
                "SELECT attempt_id,observation_json FROM execution_attempts "
                "WHERE run_id=? AND kind='AGENT' AND terminal_reason='SUCCEEDED' "
                "AND json_extract(observation_json,'$.request.command')=?",
                (run_id, AgentCommand.HYPOTHESIS_UPDATE.value),
            ).fetchall()
            first_cycle = next(
                (attempt_id, json.loads(observation_json))
                for attempt_id, observation_json in rows
                if json.loads(observation_json)["request"]["task_context"]["key"]["cycle"]
                == 1
            )
            attempt_id, observation = first_cycle
            response = AgentResponse.from_dict(observation["response"])
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "TERMINATE_LINEAGE"
            observation["response"] = _redigest(
                replace(response, typed_candidate=candidate)
            ).to_dict()
            connection.execute(
                "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                (
                    json.dumps(observation, sort_keys=True, separators=(",", ":")),
                    attempt_id,
                ),
            )
        else:
            connection.execute("DROP TRIGGER immutable_workflow_transitions_update")
            workflow_id, payload_json = connection.execute(
                "SELECT workflow_id,payload_json FROM workflow_transitions "
                "WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1 "
                "AND json_extract(payload_json,'$.expert_cycle')=2",
                (run_id,),
            ).fetchone()
            payload = json.loads(payload_json)
            payload["expert_cycle"] = 99
            connection.execute(
                "UPDATE workflow_transitions SET payload_json=? "
                "WHERE workflow_id=? AND revision=1",
                (
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    workflow_id,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert "expert_successor_authorization_mismatch" in {
        issue.code for issue in report.issues
    }, report.to_json()


def test_design_request_carries_execution_environment_contract(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-loop-execution-environment"
    host = WorkflowHost(tmp_path / "state-env")
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_pilot_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        observations = (
            json.loads(row["observation_json"])
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["observation_json"] is not None
        )
        request = next(
            observation["request"] for observation in observations
            if observation.get("request", {}).get("command")
            == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value
        )
        config = json.loads(base64.b64decode(next(
            item for item in request["input_artifacts"]
            if item["ref"].endswith("--expert-config"))["content_base64"]))
        environment = config["required_experiment_spec"]["execution_environment"]
        assert environment["cpu"]["sandbox_profile"] == "bwrap-cpu-no-network/2"
        assert environment["cpu"]["network"] == "none"
        assert environment["cpu"]["writable_roots"] == ["/output", "/tmp"]
        assert environment["cpu"]["gpu"] is None
        assert environment["gpu"]["sandbox_profile"] == "bwrap-gpu-single-no-network/2"
        assert environment["gpu"]["gpu"] is not None
    finally:
        host.close()
