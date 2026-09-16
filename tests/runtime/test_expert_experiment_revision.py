"""Retained ACI10 redesign/revision protocol, before same-EXEC Host MCP repair.

These cases intentionally require a new EDI request after a failed Process,
predecessor inputs on that design, and the predecessor revision circuit breaker.
ACI11+ repairs through submit/wait in the original EXEC controller instead;
its current coverage lives in test_host_experiment_tools and the production
submission repair test in test_component_host_g3. This module must not impose
the old redesign protocol on newly created current-version runs.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import AgentRequest, AgentResponse, WorkspaceArtifact
from hypoloom.collaboration import RendezvousDisposition
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.fake_agent import FakeAgent, _new_file_patch, _redigest
from hypoloom.monitor import build_monitor_snapshot
from hypoloom.oracle import Oracle
from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
from hypoloom.profile_admission import PROFILE_FILES, admit_profile
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]
_MALFORMED_RESULT_PATCH = (
    b"--- /dev/null+++ b/src/models/candidate.py@@ -0,0 +1 @@+candidate = True\n"
)
_MALFORMED_RESULT_DIAGNOSTIC = (
    "invalid_unified_diff@implementation_patch-1.patch: "
    "must contain paired headers and a text hunk"
)


@pytest.fixture(autouse=True)
def retained_pre_mcp_revision_protocol(monkeypatch):
    from hypoloom.agent_aci import supports_experiment_submission

    assert not supports_experiment_submission(10)
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 10)


class _FirstProcessFailsAgent:
    """Produce valid designs while making only formal Process ordinal 1 fail."""

    def __init__(self) -> None:
        self.delegate = FakeAgent()
        self.design_requests: list[AgentRequest] = []
        self.failing_source: bytes | None = None
        self.failing_patch: bytes | None = None

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        task = request.task_context
        if (
            not isinstance(response, AgentResponse)
            or request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
            or not isinstance(task, ResearchNodeTask)
        ):
            return response
        self.design_requests.append(request)
        if getattr(task.key, "experiment_ordinal", 1) != 1:
            return response

        artifacts = {
            artifact.path: artifact.content_bytes()
            for artifact in response.proposed_workspace_artifacts
        }
        assert set(artifacts) == {"experiment.py", "implementation.patch"}
        marker = (
            b"time.sleep(float(os.environ.get('HYPOLOOM_DELAY', '0')))\n"
        )
        source = artifacts["experiment.py"]
        assert source.count(marker) == 1
        failing_source = source.replace(
            marker,
            marker
            + b"print('predecessor stdout sentinel', flush=True)\n"
            + b"raise RuntimeError('formal process ordinal 1 failure')\n",
        )
        failing_patch = _new_file_patch("experiment.py", failing_source)
        self.failing_source = failing_source
        self.failing_patch = failing_patch
        return _redigest(replace(
            response,
            proposed_workspace_artifacts=(
                WorkspaceArtifact.from_bytes("experiment.py", failing_source),
                WorkspaceArtifact.from_bytes(
                    "implementation.patch",
                    failing_patch,
                ),
            ),
        ))


class _MalformedResultDiffRepairAgent:
    """Emit one rc=0 malformed result patch, then consume its exact correction."""

    def __init__(self) -> None:
        self.delegate = FakeAgent()
        self.design_requests: list[AgentRequest] = []
        self.analysis_requests: list[AgentRequest] = []

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        assert isinstance(response, AgentResponse)
        if request.command is AgentCommand.RESULT_ANALYSIS:
            self.analysis_requests.append(request)
            return response
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        self.design_requests.append(request)
        if len(self.design_requests) != 1:
            return response
        artifacts = {
            artifact.path: artifact.content_bytes()
            for artifact in response.proposed_workspace_artifacts
        }
        source = artifacts["experiment.py"] + (
            b"\nimport hashlib\n"
            b"result_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
            b"result = json.loads(result_path.read_text(encoding='utf-8'))\n"
            b"bad_patch = " + repr(_MALFORMED_RESULT_PATCH).encode("ascii") + b"\n"
            b"patch_ref = result['artifacts']['implementation_patch']\n"
            b"(result_path.parent / patch_ref).write_bytes(bad_patch)\n"
            b"result['identity']['implementation_sha256'] = "
            b"hashlib.sha256(bad_patch).hexdigest()\n"
            b"result_path.write_text(json.dumps(result, sort_keys=True), "
            b"encoding='utf-8')\n"
        )
        return _redigest(replace(
            response,
            proposed_workspace_artifacts=(
                WorkspaceArtifact.from_bytes("experiment.py", source),
                WorkspaceArtifact.from_bytes(
                    "implementation.patch",
                    _new_file_patch("experiment.py", source),
                ),
            ),
        ))


class _ValidationAndPreflightRepairAgent:
    """Reject one compile and one preflight before accepting the same node."""

    declined_summary = "Decline this candidate until the cited issue is repaired."

    def __init__(self) -> None:
        self.delegate = FakeAgent()
        self.design_requests: list[AgentRequest] = []
        self.invalid_source = b"def broken(:\n"
        self.invalid_patch = _new_file_patch(
            "experiment.py",
            self.invalid_source,
        )
        self.declined_rendezvous_id: str | None = None

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        assert isinstance(response, AgentResponse)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            self.design_requests.append(request)
            if len(self.design_requests) == 1:
                return _redigest(replace(
                    response,
                    proposed_workspace_artifacts=(
                        WorkspaceArtifact.from_bytes(
                            "experiment.py",
                            self.invalid_source,
                        ),
                        WorkspaceArtifact.from_bytes(
                            "implementation.patch",
                            self.invalid_patch,
                        ),
                    ),
                ))
            return response

        if (
            request.command is AgentCommand.ANSWER_RENDEZVOUS
            and self.declined_rendezvous_id is None
        ):
            rendezvous = json.loads(next(
                item.content_bytes()
                for item in request.input_artifacts
                if item.ref.endswith("--rendezvous")
            ))
            if str(rendezvous["rendezvous_id"]).endswith(
                "--preflight-rendezvous"
            ):
                assert len(self.design_requests) == 2
                candidate = dict(response.typed_candidate or {})
                candidate.update({
                    "disposition": RendezvousDisposition.DECLINED.value,
                    "summary": self.declined_summary,
                    "supporting_refs": [],
                })
                self.declined_rendezvous_id = str(rendezvous["rendezvous_id"])
                return _redigest(replace(
                    response,
                    typed_candidate=candidate,
                ))
        return response


class _MissingIdentityRepairAgent:
    """Omit the result identity block once, then repair it on the revision."""

    def __init__(self) -> None:
        self.delegate = FakeAgent()
        self.design_requests: list[AgentRequest] = []
        self.analysis_requests: list[AgentRequest] = []

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        assert isinstance(response, AgentResponse)
        if request.command is AgentCommand.RESULT_ANALYSIS:
            self.analysis_requests.append(request)
            return response
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        self.design_requests.append(request)
        if len(self.design_requests) != 1:
            return response
        artifacts = {
            artifact.path: artifact.content_bytes()
            for artifact in response.proposed_workspace_artifacts
        }
        source = artifacts["experiment.py"] + (
            b"\nresult_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
            b"result = json.loads(result_path.read_text(encoding='utf-8'))\n"
            b"result.pop('identity', None)\n"
            b"result_path.write_text(json.dumps(result, sort_keys=True), "
            b"encoding='utf-8')\n"
        )
        return _redigest(replace(
            response,
            proposed_workspace_artifacts=(
                WorkspaceArtifact.from_bytes("experiment.py", source),
                WorkspaceArtifact.from_bytes(
                    "implementation.patch",
                    _new_file_patch("experiment.py", source),
                ),
            ),
        ))


class _MissingResultRepairAgent:
    """Reference result.json but rename it away once, then repair on revision."""

    def __init__(self) -> None:
        self.delegate = FakeAgent()
        self.design_requests: list[AgentRequest] = []
        self.analysis_requests: list[AgentRequest] = []

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        assert isinstance(response, AgentResponse)
        if request.command is AgentCommand.RESULT_ANALYSIS:
            self.analysis_requests.append(request)
            return response
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        self.design_requests.append(request)
        if len(self.design_requests) != 1:
            return response
        artifacts = {
            artifact.path: artifact.content_bytes()
            for artifact in response.proposed_workspace_artifacts
        }
        source = artifacts["experiment.py"] + (
            b"\nresult_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
            b"result_path.rename(result_path.with_name('result.json.bak'))\n"
        )
        return _redigest(replace(
            response,
            proposed_workspace_artifacts=(
                WorkspaceArtifact.from_bytes("experiment.py", source),
                WorkspaceArtifact.from_bytes(
                    "implementation.patch",
                    _new_file_patch("experiment.py", source),
                ),
            ),
        ))


class _RevisionThenTwoCyclesAgent:
    """Cycle 1: one operational failure then a refuted metric; cycle 2 succeeds."""

    def __init__(self) -> None:
        self.delegate = FakeAgent()

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        task = request.task_context
        if (isinstance(response, AgentResponse)
                and request.command is AgentCommand.HYPOTHESIS_UPDATE
                and isinstance(task, ResearchNodeTask)
                and task.key.cycle == 1):
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
            return _redigest(replace(response, typed_candidate=candidate))
        if (not isinstance(response, AgentResponse)
                or request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
                or not isinstance(task, ResearchNodeTask)):
            return response
        artifacts = tuple(
            WorkspaceArtifact.from_bytes(item.path, item.content_bytes())
            for item in response.proposed_workspace_artifacts
        )
        if task.key.experiment_ordinal == 1:
            marker = (
                b"time.sleep(float(os.environ.get('HYPOLOOM_DELAY', '0')))\n"
            )
            source = artifacts[0].content_bytes()
            assert artifacts[0].path == "experiment.py" and source.count(marker) == 1
            failing = source.replace(
                marker, marker + b"raise RuntimeError('cycle one probe failure')\n",
            )
            artifacts = (
                WorkspaceArtifact.from_bytes("experiment.py", failing),
                WorkspaceArtifact.from_bytes(
                    "implementation.patch",
                    _new_file_patch("experiment.py", failing),
                ),
            )
        elif task.key.cycle == 1:
            artifacts = tuple(
                WorkspaceArtifact.from_bytes(
                    item.path,
                    item.content_bytes().replace(
                        b'"accuracy": 0.55', b'"accuracy": 0.50',
                    ),
                )
                for item in artifacts
            )
        else:
            return response
        return _redigest(replace(response, proposed_workspace_artifacts=artifacts))


class _UnconvergedLoopAgent:
    """Fail every experiment operationally and always ask for the next cycle."""

    def __init__(self) -> None:
        self.delegate = FakeAgent()

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = self.delegate.invoke(request)
        if (isinstance(response, AgentResponse)
                and request.command is AgentCommand.HYPOTHESIS_UPDATE):
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
            return _redigest(replace(response, typed_candidate=candidate))
        if (not isinstance(response, AgentResponse)
                or request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT):
            return response
        artifacts = {
            artifact.path: artifact.content_bytes()
            for artifact in response.proposed_workspace_artifacts
        }
        assert set(artifacts) == {"experiment.py", "implementation.patch"}
        marker = (
            b"time.sleep(float(os.environ.get('HYPOLOOM_DELAY', '0')))\n"
        )
        source = artifacts["experiment.py"]
        assert source.count(marker) == 1
        failing = source.replace(
            marker, marker + b"raise RuntimeError('unconverged probe failure')\n",
        )
        return _redigest(replace(
            response,
            proposed_workspace_artifacts=(
                WorkspaceArtifact.from_bytes("experiment.py", failing),
                WorkspaceArtifact.from_bytes(
                    "implementation.patch",
                    _new_file_patch("experiment.py", failing),
                ),
            ),
        ))


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-expert-experiment-revision-",
        dir="/var/tmp",
    ) as raw:
        yield Path(raw)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _payload(tmp_path: Path, mount_root: Path, run_id: str) -> dict[str, object]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text(
        "expert experiment revision target\n",
        encoding="utf-8",
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Test",
        "-c",
        "user.email=hypoloom@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    data = mount_root / "data"
    data.mkdir()
    (data / "README.txt").write_text("bounded fixture\n", encoding="utf-8")

    source = REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in PROFILE_FILES:
        text = (source / name).read_text(encoding="utf-8")
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-target",
            str(target.resolve()),
        )
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-data",
            str(data.resolve()),
        )
        if name == "safety.yaml":
            text = "allow_read_paths: []\nsandbox_os: linux\n"
        (profile / name).write_text(text, encoding="utf-8")

    report = admit_profile(profile, run_id=run_id)
    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    return report.create_run_payload


def _result_patch_payload(
    tmp_path: Path,
    mount_root: Path,
    run_id: str,
) -> dict[str, object]:
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    assert isinstance(contract, dict)
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": "1",
        "display_only": True,
    }
    component = contract["components"][0]
    component.update({
        "protocol": "implementation-artifact",
        "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
        "seeds": [],
        "required_artifacts": ["implementation_patch", "runner_config"],
    })
    contract["protocols"] = {
        "implementation-artifact": {
            "result_kind": "artifact",
            "requirements": {
                "required_artifacts": ["implementation_patch", "runner_config"],
                "artifact_validators": [
                    {
                        "artifact": "implementation_patch",
                        "type": "unified_diff",
                        "apply_check": True,
                    },
                    {
                        "artifact": "runner_config",
                        "type": "json_contract",
                        "required_paths": ["method_family_id", "datasets"],
                    },
                ],
            },
        },
    }
    return payload


def _creation_identities(host: WorkflowHost, run_id: str) -> list[dict[str, object]]:
    rows = host.store.inspect(
        "SELECT workflow_id,payload_json FROM workflow_transitions "
        "WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1",
        (run_id,),
    )
    identities = [
        {"experiment_id": row["workflow_id"], **json.loads(row["payload_json"])}
        for row in rows
    ]
    return sorted(
        identities,
        key=lambda item: int(item.get("experiment_ordinal", 1)),
    )


def _agent_requests(host: WorkflowHost, run_id: str) -> list[dict[str, object]]:
    attempts = sorted(
        (
            row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "AGENT"
        ),
        key=lambda row: (float(row["started_at"]), str(row["attempt_id"])),
    )
    return [
        json.loads(row["observation_json"])["request"]
        for row in attempts
        if json.loads(row["observation_json"]).get("request") is not None
    ]


def _input_with_suffix(request: AgentRequest, suffix: str):
    matches = [
        item for item in request.input_artifacts if item.ref.endswith(suffix)
    ]
    assert len(matches) == 1, (
        suffix,
        [item.ref for item in request.input_artifacts],
    )
    return matches[0]


def _drive(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        host.tick()
        head = host.store.latest_transition(run_id)
        if head is not None and head["state"] in {
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }:
            return
        time.sleep(0.005)

    identities = _creation_identities(host, run_id)
    assert [item["experiment_ordinal"] for item in identities] == [1, 2]
    second = host.store.latest_transition(str(identities[1]["experiment_id"]))
    assert second is not None and second["state"] == "DRAFTED"
    design_requests = [
        request
        for request in _agent_requests(host, run_id)
        if request["command"] == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value
    ]
    raise AssertionError(
        "expert experiment ordinal 2 remained DRAFTED without an independent "
        f"design task; design_requests={design_requests!r}"
    )


def test_process_failure_redesigns_same_cycle_before_success(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-experiment-revision"
    state_root = tmp_path / "state"
    payload = _payload(tmp_path, mount_root, run_id)
    research_contract = payload["research_contract"]
    assert isinstance(research_contract, dict)
    roles = research_contract["roles"]
    assert isinstance(roles, list) and len(roles) == 1
    reviewer = json.loads(json.dumps(roles[0]))
    reviewer["id"] = "execution-reviewer"
    reviewer["focus"] = "Review experiment candidates and execution results."
    roles.append(reviewer)
    methodology = research_contract["methodology"]
    assert isinstance(methodology, dict)
    controls = methodology.setdefault("controls", {})
    assert isinstance(controls, dict)
    controls["experiment_campaigns"] = {
        "enabled": True,
        "required_roles": ["preflight", "analyze"],
        "role_owners": {
            "*": {
                "preflight": "execution-reviewer",
                "analyze": "execution-reviewer",
            },
        },
    }
    host = WorkflowHost(state_root)
    agent = _FirstProcessFailsAgent()
    host.agent = agent
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
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"

        identities = _creation_identities(host, run_id)
        assert [item["experiment_ordinal"] for item in identities] == [1, 2]
        assert identities[0]["hypothesis_id"] == identities[1]["hypothesis_id"]
        assert identities[0]["expert_cycle"] == identities[1]["expert_cycle"] == 1

        outcomes = {
            str(row["experiment_id"]): row
            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        }
        assert [
            outcomes[str(identity["experiment_id"])]["payload"]["outcome"]
            for identity in identities
        ] == ["EXECUTION_FAILURE", "SUCCESS"]
        assert identities[1]["revision_of_outcome_id"] == outcomes[
            str(identities[0]["experiment_id"])
        ]["outcome_id"]

        process_attempts = {
            str(row["workflow_id"]): row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "PROCESS"
        }
        assert [
            process_attempts[str(identity["experiment_id"])]["terminal_reason"]
            for identity in identities
        ] == ["FAILED", "SUCCEEDED"]

        assert len(agent.design_requests) == 2
        predecessor_design, successor_design = agent.design_requests
        assert predecessor_design.role == successor_design.role == "ml-researcher"
        assert predecessor_design.component_id == successor_design.component_id
        predecessor_experiment_id = str(identities[0]["experiment_id"])
        predecessor_spec = host.store.find_fact(
            run_id,
            "ExperimentPreregistered",
            predecessor_experiment_id,
        )
        assert predecessor_spec is not None
        predecessor_outcome = outcomes[predecessor_experiment_id]
        predecessor_process = process_attempts[predecessor_experiment_id]
        assert json.loads(_input_with_suffix(
            successor_design,
            "--predecessor-spec",
        ).content_bytes()) == predecessor_spec
        assert json.loads(_input_with_suffix(
            successor_design,
            "--predecessor-outcome",
        ).content_bytes()) == predecessor_outcome
        evaluation = json.loads(_input_with_suffix(
            successor_design,
            "--predecessor-evaluation",
        ).content_bytes())
        assert evaluation["outcome"] == "EXECUTION_FAILURE"
        assert evaluation["execution_integrity_error"] is None
        assert json.loads(_input_with_suffix(
            successor_design,
            "--predecessor-process",
        ).content_bytes()) == json.loads(predecessor_process["observation_json"])
        assert agent.failing_source is not None
        assert _input_with_suffix(
            successor_design,
            "--predecessor-experiment.py",
        ).content_bytes() == agent.failing_source
        assert agent.failing_patch is not None
        assert _input_with_suffix(
            successor_design,
            "--predecessor-implementation.patch",
        ).content_bytes() == agent.failing_patch
        assert b"predecessor stdout sentinel" in _input_with_suffix(
            successor_design,
            "--predecessor-logs-stdout.log",
        ).content_bytes()
        assert b"formal process ordinal 1 failure" in _input_with_suffix(
            successor_design,
            "--predecessor-logs-stderr.log",
        ).content_bytes()
        successor_repair_inputs = (
            _input_with_suffix(successor_design, "--predecessor-spec"),
            _input_with_suffix(successor_design, "--predecessor-outcome"),
            _input_with_suffix(successor_design, "--predecessor-evaluation"),
            _input_with_suffix(successor_design, "--predecessor-process"),
            _input_with_suffix(
                successor_design,
                "--predecessor-experiment.py",
            ),
            _input_with_suffix(
                successor_design,
                "--predecessor-implementation.patch",
            ),
            _input_with_suffix(
                successor_design,
                "--predecessor-logs-stdout.log",
            ),
            _input_with_suffix(
                successor_design,
                "--predecessor-logs-stderr.log",
            ),
        )
        predecessor_reply_inputs = tuple(
            item
            for item in successor_design.input_artifacts
            if item.ref.endswith("--reply")
        )
        assert len(predecessor_reply_inputs) == 2
        successor_repair_refs = {
            item.ref
            for item in (*successor_repair_inputs, *predecessor_reply_inputs)
        }
        assert successor_repair_refs.isdisjoint(
            successor_design.immutable_context_refs,
        )

        requests = _agent_requests(host, run_id)
        design_requests = [
            request
            for request in requests
            if request["command"]
            == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value
        ]
        assert [
            request["task_context"]["key"]["experiment_ordinal"]
            for request in design_requests
        ] == [1, 2]
        assert design_requests[0]["command_id"] != design_requests[1]["command_id"]
        assert [
            request["task_context"]["key"]["cycle"]
            for request in design_requests
        ] == [1, 1]

        analyses = [
            request
            for request in requests
            if request["command"] == AgentCommand.RESULT_ANALYSIS.value
        ]
        assert analyses
        latest_analysis_key = analyses[-1]["task_context"]["key"]
        assert latest_analysis_key["cycle"] == 1
        assert latest_analysis_key["experiment_ordinal"] == 2
        latest_analysis = AgentRequest.from_dict(analyses[-1])
        analysis_inputs = {
            item.ref: item for item in latest_analysis.input_artifacts
        }
        analysis_prefix = latest_analysis.command_id
        current_experiment_id = str(identities[1]["experiment_id"])
        current_spec = host.store.find_fact(
            run_id,
            "ExperimentPreregistered",
            current_experiment_id,
        )
        assert current_spec is not None
        current_outcome = outcomes[current_experiment_id]
        current_process = process_attempts[current_experiment_id]
        assert json.loads(analysis_inputs[
            f"{analysis_prefix}--current-spec"
        ].content_bytes()) == current_spec
        assert json.loads(analysis_inputs[
            f"{analysis_prefix}--current-outcome"
        ].content_bytes()) == current_outcome
        evaluation = json.loads(analysis_inputs[
            f"{analysis_prefix}--current-evaluation"
        ].content_bytes())
        assert evaluation["outcome"] == "SUCCESS"
        assert evaluation["validation_error"] is None
        assert json.loads(analysis_inputs[
            f"{analysis_prefix}--current-process"
        ].content_bytes()) == json.loads(current_process["observation_json"])
        source_attempt = host.store.get_attempt(str(current_spec["payload"]
            ["execution_contract"]["source_agent_attempt_id"]))
        assert source_attempt is not None
        source_response = AgentResponse.from_dict(
            json.loads(source_attempt["observation_json"])["response"]
        )
        assert analysis_inputs[
            f"{analysis_prefix}--current-experiment.py"
        ].content_bytes() == next(
            item.content_bytes()
            for item in source_response.proposed_workspace_artifacts
            if item.path == "experiment.py"
        )[-65_536:]
        current_refs = {
            f"{analysis_prefix}--current-{suffix}"
            for suffix in (
                "spec", "outcome", "evaluation", "process", "experiment.py",
                "result.json", "stdout", "stderr",
            )
        }
        assert current_refs <= set(analysis_inputs)
        assert current_refs <= set(latest_analysis.immutable_context_refs)
        assert str(predecessor_spec["fact_id"]) not in analysis_inputs
        assert str(predecessor_outcome["fact_id"]) not in analysis_inputs
        assert str(current_spec["fact_id"]) not in analysis_inputs
        assert str(current_outcome["fact_id"]) not in analysis_inputs
        assert b"formal process ordinal 1 failure" not in b"\n".join(
            item.content_bytes() for item in latest_analysis.input_artifacts
        )

        report = Oracle(
            state_root / "state.sqlite",
            state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
        assert report.open_attempt_count == 0
        assert report.pending_effect_count == 0
        assert report.owned_process_residue == ()
        assert not host.running and not host.running_agents
        work = state_root / "artifacts" / "work"
        assert not work.exists() or not tuple(work.iterdir())
        assert not (state_root / "git-authoring-worktrees").exists()
    finally:
        host.close()


def test_malformed_result_diff_retries_same_hypothesis_in_real_bwrap(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-malformed-result-diff-repair"
    state_root = tmp_path / "state"
    payload = _result_patch_payload(tmp_path, mount_root, run_id)
    agent = _MalformedResultDiffRepairAgent()
    host = WorkflowHost(state_root)
    host.agent = agent
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
        _drive(host, run_id)

        run = host.store.get_run(run_id)
        assert run is not None
        assert run["evaluator_version"] == OUTCOME_ADAPTER_VERSION
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        identities = _creation_identities(host, run_id)
        assert [item["experiment_ordinal"] for item in identities] == [1, 2]
        assert identities[0]["hypothesis_id"] == identities[1]["hypothesis_id"]
        assert identities[0]["expert_cycle"] == identities[1]["expert_cycle"] == 1

        outcomes = {
            str(row["experiment_id"]): row
            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        }
        ordered_outcomes = [
            outcomes[str(identity["experiment_id"])] for identity in identities
        ]
        assert [row["payload"]["outcome"] for row in ordered_outcomes] == [
            "EXECUTION_FAILURE",
            "SUCCESS",
        ]
        assert identities[1]["revision_of_outcome_id"] == ordered_outcomes[0][
            "outcome_id"
        ]
        assert len(agent.design_requests) == 2
        predecessor_evaluation = json.loads(_input_with_suffix(
            agent.design_requests[1],
            "--predecessor-evaluation",
        ).content_bytes())
        assert predecessor_evaluation == {
            "outcome": "EXECUTION_FAILURE",
            "evaluator_version": OUTCOME_ADAPTER_VERSION,
            "metric_value": None,
            "criterion_met": None,
            "validation_error": _MALFORMED_RESULT_DIAGNOSTIC,
            "execution_integrity_error": None,
        }

        process_attempts = [
            host.store.list_attempts(
                execution_id=str(outcome["execution_id"])
            )[0]
            for outcome in ordered_outcomes
        ]
        assert [row["terminal_reason"] for row in process_attempts] == [
            "SUCCEEDED",
            "SUCCEEDED",
        ]
        observations = [
            json.loads(row["observation_json"]) for row in process_attempts
        ]
        assert [item["returncode"] for item in observations] == [0, 0]
        assert [Path(item["process_request"]["argv"][0]).name
                for item in observations] == ["bwrap", "bwrap"]

        assert len(agent.analysis_requests) == 2
        first_analysis, analysis = agent.analysis_requests
        assert isinstance(first_analysis.task_context, ResearchNodeTask)
        assert isinstance(analysis.task_context, ResearchNodeTask)
        assert [item.task_context.key.experiment_ordinal
                for item in agent.analysis_requests] == [1, 2]
        first_inputs = {
            item.ref: item for item in first_analysis.input_artifacts
        }
        first_prefix = first_analysis.command_id
        assert json.loads(first_inputs[
            f"{first_prefix}--current-evaluation"
        ].content_bytes()) == predecessor_evaluation
        assert _MALFORMED_RESULT_DIAGNOSTIC.encode() in first_inputs[
            f"{first_prefix}--current-evaluation"
        ].content_bytes()
        assert f"{first_prefix}--current-result.json" in first_inputs

        analysis_inputs = {item.ref: item for item in analysis.input_artifacts}
        prefix = analysis.command_id
        assert json.loads(analysis_inputs[
            f"{prefix}--current-evaluation"
        ].content_bytes()) == {
            "outcome": "SUCCESS",
            "evaluator_version": OUTCOME_ADAPTER_VERSION,
            "metric_value": None,
            "criterion_met": True,
            "validation_error": None,
            "execution_integrity_error": None,
        }
        assert str(ordered_outcomes[0]["fact_id"]) not in analysis_inputs
        assert _MALFORMED_RESULT_DIAGNOSTIC.encode() not in b"\n".join(
            item.content_bytes() for item in analysis.input_artifacts
        )
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
    finally:
        host.close()

    report = Oracle(
        state_root / "state.sqlite",
        state_root / "artifacts",
    ).audit(run_id)
    assert report.ok, report.to_json()
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.owned_process_residue == ()
    work = state_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())
    assert not (state_root / "git-authoring-worktrees").exists()


def test_design_retry_receives_compile_context_and_declined_preflight_reply(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-design-repair-context"
    state_root = tmp_path / "state"
    payload = _payload(tmp_path, mount_root, run_id)
    research_contract = payload["research_contract"]
    assert isinstance(research_contract, dict)
    roles = research_contract["roles"]
    assert isinstance(roles, list) and len(roles) == 1
    reviewer = json.loads(json.dumps(roles[0]))
    reviewer["id"] = "preflight-reviewer"
    reviewer["focus"] = "Peer review of compiled experiment candidates."
    roles.append(reviewer)
    methodology = research_contract["methodology"]
    assert isinstance(methodology, dict)
    controls = methodology.setdefault("controls", {})
    assert isinstance(controls, dict)
    controls["experiment_campaigns"] = {
        "enabled": True,
        "required_roles": ["preflight"],
        "role_owners": {"*": {"preflight": "preflight-reviewer"}},
    }

    agent = _ValidationAndPreflightRepairAgent()
    host = WorkflowHost(state_root)
    host.agent = agent
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
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"

        assert len(agent.design_requests) == 3
        first, compile_retry, preflight_retry = agent.design_requests
        assert [
            request.attempt_ordinal
            for request in agent.design_requests
        ] == [1, 2, 3]
        assert len({request.owner_workflow_id for request in agent.design_requests}) == 1
        assert len({request.command_id for request in agent.design_requests}) == 1
        assert [request.role for request in agent.design_requests] == [
            "ml-researcher",
            "ml-researcher",
            "ml-researcher",
        ]
        assert first.task_context == compile_retry.task_context
        assert compile_retry.task_context == preflight_retry.task_context
        assert isinstance(first.task_context, ResearchNodeTask)
        assert first.task_context.key.experiment_ordinal == 1

        previous_source = _input_with_suffix(
            compile_retry,
            "--previous-experiment.py",
        )
        assert previous_source.content_bytes() == agent.invalid_source
        previous_patch = _input_with_suffix(
            compile_retry,
            "--previous-implementation.patch",
        )
        assert previous_patch.content_bytes() == agent.invalid_patch
        validation_input = _input_with_suffix(
            compile_retry,
            "--validation-diagnostics",
        )
        diagnostics = json.loads(validation_input.content_bytes())
        assert diagnostics["validation_version"] == "candidate-compile/3"
        assert diagnostics["source_agent_attempt_id"] == first.attempt_id
        assert diagnostics["terminal_reason"] == "FAILED"
        assert diagnostics["returncode"] != 0
        assert "SyntaxError" in diagnostics["stderr_tail"]
        compile_repair_refs = {
            previous_source.ref,
            previous_patch.ref,
            validation_input.ref,
        }
        assert compile_repair_refs.isdisjoint(
            compile_retry.immutable_context_refs,
        )

        validation_attempts = sorted(
            host.store.list_attempts(
                execution_id=(
                    f"{first.owner_workflow_id}--candidate-validation"
                ),
            ),
            key=lambda row: str(row["attempt_id"]),
        )
        assert [row["terminal_reason"] for row in validation_attempts] == [
            "FAILED",
            "SUCCEEDED",
            "SUCCEEDED",
        ]

        assert agent.declined_rendezvous_id is not None
        reply_ref = f"{agent.declined_rendezvous_id}--reply"
        reply_inputs = {
            item.ref: item
            for item in preflight_retry.input_artifacts
            if item.ref.endswith("--reply")
        }
        assert set(reply_inputs) == {reply_ref}
        assert json.loads(reply_inputs[reply_ref].content_bytes()) == {
            "schema_version": 1,
            "rendezvous_id": agent.declined_rendezvous_id,
            "kind": "HELP",
            "disposition": "DECLINED",
            "responder_role": "preflight-reviewer",
            "summary": agent.declined_summary,
            "supporting_refs": [],
        }
        preflight_repair_inputs = (
            _input_with_suffix(preflight_retry, "--previous-experiment.py"),
            _input_with_suffix(
                preflight_retry,
                "--previous-implementation.patch",
            ),
            _input_with_suffix(preflight_retry, "--validation-diagnostics"),
            reply_inputs[reply_ref],
        )
        preflight_repair_refs = {
            item.ref for item in preflight_repair_inputs
        }
        assert preflight_repair_refs.isdisjoint(
            preflight_retry.immutable_context_refs,
        )

        report = Oracle(
            state_root / "state.sqlite",
            state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
        assert report.open_attempt_count == 0
        assert report.pending_effect_count == 0
        assert report.owned_process_residue == ()
    finally:
        host.close()


def test_missing_result_digest_revises_same_hypothesis_without_new_cycle(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-missing-digest-repair"
    state_root = tmp_path / "state"
    payload = _result_patch_payload(tmp_path, mount_root, run_id)
    agent = _MissingIdentityRepairAgent()
    host = WorkflowHost(state_root)
    host.agent = agent
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
        _drive(host, run_id)

        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        identities = _creation_identities(host, run_id)
        assert [item["experiment_ordinal"] for item in identities] == [1, 2]
        assert identities[0]["hypothesis_id"] == identities[1]["hypothesis_id"]
        assert identities[0]["expert_cycle"] == identities[1]["expert_cycle"] == 1

        outcomes = {
            str(row["experiment_id"]): row
            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        }
        ordered_outcomes = [
            outcomes[str(identity["experiment_id"])] for identity in identities
        ]
        assert [row["payload"]["outcome"] for row in ordered_outcomes] == [
            "INTEGRITY_REJECTED",
            "SUCCESS",
        ]
        assert ordered_outcomes[0]["payload"]["validation_code"] == (
            "missing_implementation_digest"
        )
        assert ordered_outcomes[1]["payload"]["validation_code"] is None
        assert identities[1]["revision_of_outcome_id"] == ordered_outcomes[0][
            "outcome_id"
        ]

        # The repair stayed on the same hypothesis: no new proposal, no
        # second literature review, no new cycle.
        assert len(host.store.list_facts(run_id, "HypothesisProposed")) == 1
        literature_commands = [
            request["command"]
            for request in _agent_requests(host, run_id)
            if request["command"] == AgentCommand.LITERATURE_REVIEW.value
            and request["task_context"].get("kind") == "RESEARCH_NODE"
        ]
        assert len(literature_commands) == 2

        assert len(agent.design_requests) == 2
        predecessor_evaluation = json.loads(_input_with_suffix(
            agent.design_requests[1],
            "--predecessor-evaluation",
        ).content_bytes())
        assert predecessor_evaluation["outcome"] == "INTEGRITY_REJECTED"
        assert predecessor_evaluation["validation_error"] == (
            "missing_implementation_digest@implementation_sha256: is required"
        )
        assert len(agent.analysis_requests) == 2
    finally:
        host.close()

    report = Oracle(
        state_root / "state.sqlite",
        state_root / "artifacts",
    ).audit(run_id)
    assert report.ok, report.to_json()
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0


def test_missing_result_on_zero_exit_revises_same_hypothesis_without_new_cycle(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-missing-result-repair"
    state_root = tmp_path / "state"
    payload = _result_patch_payload(tmp_path, mount_root, run_id)
    agent = _MissingResultRepairAgent()
    host = WorkflowHost(state_root)
    host.agent = agent
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
        _drive(host, run_id)

        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
        identities = _creation_identities(host, run_id)
        assert [item["experiment_ordinal"] for item in identities] == [1, 2]
        assert identities[0]["hypothesis_id"] == identities[1]["hypothesis_id"]
        assert identities[0]["expert_cycle"] == identities[1]["expert_cycle"] == 1

        outcomes = {
            str(row["experiment_id"]): row
            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        }
        ordered_outcomes = [
            outcomes[str(identity["experiment_id"])] for identity in identities
        ]
        assert [row["payload"]["outcome"] for row in ordered_outcomes] == [
            "EXECUTION_FAILURE",
            "SUCCESS",
        ]
        assert ordered_outcomes[0]["payload"]["validation_code"] == (
            "missing_result"
        )
        assert ordered_outcomes[1]["payload"]["validation_code"] is None
        assert identities[1]["revision_of_outcome_id"] == ordered_outcomes[0][
            "outcome_id"
        ]

        # The zero-exit process is terminal SUCCEEDED; the missing result is a
        # validator-detected repairable failure, not an integrity rejection.
        process_attempts = {
            str(row["workflow_id"]): row
            for row in host.store.list_attempts()
            if row["run_id"] == run_id and row["kind"] == "PROCESS"
        }
        assert [
            process_attempts[str(identity["experiment_id"])]["terminal_reason"]
            for identity in identities
        ] == ["SUCCEEDED", "SUCCEEDED"]

        # The repair stayed on the same hypothesis: no new proposal, no
        # second literature review, no new cycle.
        assert len(host.store.list_facts(run_id, "HypothesisProposed")) == 1
        literature_commands = [
            request["command"]
            for request in _agent_requests(host, run_id)
            if request["command"] == AgentCommand.LITERATURE_REVIEW.value
            and request["task_context"].get("kind") == "RESEARCH_NODE"
        ]
        assert len(literature_commands) == 2

        assert len(agent.design_requests) == 2
        predecessor_evaluation = json.loads(_input_with_suffix(
            agent.design_requests[1],
            "--predecessor-evaluation",
        ).content_bytes())
        assert predecessor_evaluation["outcome"] == "EXECUTION_FAILURE"
        assert predecessor_evaluation["validation_error"] == (
            "missing_result@result.json: file is required"
        )
        assert predecessor_evaluation["execution_integrity_error"] is None
        assert len(agent.analysis_requests) == 2
    finally:
        host.close()

    report = Oracle(
        state_root / "state.sqlite",
        state_root / "artifacts",
    ).audit(run_id)
    assert report.ok, report.to_json()
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.owned_process_residue == ()


def test_current_cycle_revisions_counts_only_the_latest_expert_cycle(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-cycle-revision-counting"
    state_root = tmp_path / "state"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    assert isinstance(contract, dict)
    contract["budget"]["max_experiments"] = 3
    roles = contract["roles"]
    assert isinstance(roles, list)
    roles[0]["budget"]["max_experiments"] = 3
    host = WorkflowHost(state_root)
    host.agent = _RevisionThenTwoCyclesAgent()
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
        observed_revision_count: int | None = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if (observed_revision_count is None and head is not None
                    and len(_creation_identities(host, run_id)) == 2):
                observed_revision_count = host._policy_snapshot(
                    head,
                ).components[0].current_cycle_revisions
            if head is not None and head["state"] in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }:
                break
            time.sleep(0.005)
        head = host.store.latest_transition(run_id)
        assert head is not None and head["state"] == "COMPLETED"
        assert observed_revision_count == 1

        identities = _creation_identities(host, run_id)
        assert [item["experiment_ordinal"] for item in identities] == [1, 2, 3]
        assert [item["expert_cycle"] for item in identities] == [1, 1, 2]
        final_view = host._policy_snapshot(head)
        assert final_view.components[0].current_cycle_revisions == 0

        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
    finally:
        host.close()

    report = Oracle(
        state_root / "state.sqlite",
        state_root / "artifacts",
    ).audit(run_id)
    assert report.ok, report.to_json()


def test_revision_circuit_breaker_stops_an_unconverged_cycle_inconclusive(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    run_id = "expert-revision-circuit-breaker"
    state_root = tmp_path / "state"
    payload = _payload(tmp_path, mount_root, run_id)
    contract = payload["research_contract"]
    assert isinstance(contract, dict)
    contract["budget"]["max_experiments"] = 40
    roles = contract["roles"]
    assert isinstance(roles, list)
    roles[0]["budget"]["max_experiments"] = 40
    host = WorkflowHost(state_root)
    host.agent = _UnconvergedLoopAgent()
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
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }:
                break
            time.sleep(0.005)
        head = host.store.latest_transition(run_id)
        assert head is not None and head["state"] == "COMPLETED"

        # 32 creations mean 31 revisions in cycle 1: the breaker trips above 30
        # and the 33rd experiment of the cycle is never created.
        identities = _creation_identities(host, run_id)
        assert [item["experiment_ordinal"] for item in identities] == list(
            range(1, 33),
        )
        assert {item["expert_cycle"] for item in identities} == {1}
        assert len({item["hypothesis_id"] for item in identities}) == 1

        outcomes = {
            str(row["experiment_id"]): row
            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        }
        assert [
            outcomes[str(identity["experiment_id"])]["payload"]["outcome"]
            for identity in identities
        ] == ["EXECUTION_FAILURE"] * 32

        # No second cycle opened: one hypothesis, one assessment, and the run
        # closes INCONCLUSIVE because the lineage never terminated.
        assert len(host.store.list_facts(run_id, "HypothesisProposed")) == 1
        assert len(host.store.list_facts(run_id, "HypothesisAssessed")) == 1
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "INCONCLUSIVE"

        snapshot = build_monitor_snapshot(state_root, run_id)
        component = snapshot.components[0]
        assert component.cycle_revisions == 31
        assert component.current_cycle == 1
        assert component.revision_warning == "tripped"

        report = Oracle(
            state_root / "state.sqlite",
            state_root / "artifacts",
        ).audit(run_id)
        assert report.ok, report.to_json()
        assert report.open_attempt_count == 0
        assert report.pending_effect_count == 0
        assert report.owned_process_residue == ()
    finally:
        host.close()
