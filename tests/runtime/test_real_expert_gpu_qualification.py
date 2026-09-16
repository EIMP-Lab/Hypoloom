"""Opt-in G4 qualification for two current expert cycles on one real GPU.

The normal test suite never opens a CUDA context.  This lane reuses the tracked
``expert-loop-pilot`` profile, admits a fresh target/data binding, and requires
an exact authorized GPU UUID plus a frozen Python runtime.  Its deterministic
Agent asks for that GPU in both scientific cycles; the production Host, local
process-group Runner, bwrap workspace and ArtifactPublisher perform the effects.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import (
    CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AgentRequest,
    AgentResponse,
    WorkspaceArtifact,
)
from hypoloom.artifacts import ArtifactPublisher
from hypoloom.compute_resources import LocalComputeResources
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, ResourcePreference
from hypoloom.expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    HypothesisUpdateDisposition,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from hypoloom.fake_agent import FakeAgent, _redigest
from hypoloom.literature import (
    ClaimPageRef,
    FetchResponse,
    FixedLiteratureIO,
    LiteraturePacket,
    LiteratureQuery,
    LiteratureStratum,
    PdfExtraction,
    ScholarlySourceClass,
    SearchPaper,
)
from hypoloom.module_rehearsal import rehearse_design_chain
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import PROFILE_FILES, admit_profile
from hypoloom.runner import LocalRunner
from hypoloom.state_store import StateStore
from hypoloom.workflow_host import WorkflowHost


_ENABLE_ENV = "HYPOLOOM_REAL_GPU"
_UUID_ENV = "HYPOLOOM_REAL_GPU_UUID"
_RUNTIME_ENV = "HYPOLOOM_REAL_GPU_RUNTIME"
_REPOSITORY = Path(__file__).resolve().parents[2]
_PROFILE_TEMPLATE = _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
_REAL_GPU_ONLY = pytest.mark.skipif(
    os.environ.get(_ENABLE_ENV) != "1",
    reason=f"set {_ENABLE_ENV}=1 with an exact authorized UUID/runtime",
)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _configured_gpu() -> tuple[str, Path]:
    gpu_uuid = os.environ.get(_UUID_ENV)
    runtime_raw = os.environ.get(_RUNTIME_ENV)
    assert gpu_uuid and runtime_raw, f"{_UUID_ENV} and {_RUNTIME_ENV} are required"
    assert gpu_uuid == gpu_uuid.strip() and gpu_uuid.startswith("GPU-")
    assert runtime_raw == runtime_raw.strip()
    configured = Path(runtime_raw)
    assert configured.is_absolute() and not configured.is_symlink()
    runtime = configured.resolve()
    assert runtime.is_dir() and (runtime / "bin" / "python").is_file()
    assert shutil.which("nvidia-smi") is not None
    assert shutil.which("bwrap") is not None
    return gpu_uuid, runtime


def _command(arguments: tuple[str, ...]) -> tuple[str, ...]:
    result = subprocess.run(
        arguments,
        cwd=_REPOSITORY,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (arguments, result.stdout, result.stderr)
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _assert_gpu_idle(gpu_uuid: str) -> None:
    deadline = time.monotonic() + 20
    latest: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    while time.monotonic() < deadline:
        inventory = _command((
            "nvidia-smi",
            "--query-gpu=uuid,utilization.gpu,memory.used,temperature.gpu",
            "--format=csv,noheader,nounits",
        ))
        selected_inventory = tuple(
            line for line in inventory if line.split(",", 1)[0].strip() == gpu_uuid
        )
        assert len(selected_inventory) == 1, (gpu_uuid, inventory)
        _, utilization, memory_used, temperature = (
            item.strip() for item in selected_inventory[0].split(",")
        )
        applications = _command((
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid",
            "--format=csv,noheader,nounits",
        ))
        selected_processes = tuple(
            line for line in applications if line.split(",", 1)[0].strip() == gpu_uuid
        )
        latest = selected_inventory, selected_processes
        if (
            int(utilization) <= 10
            and int(memory_used) <= 512
            and int(temperature) <= 60
            and not selected_processes
        ):
            return
        time.sleep(0.2)
    raise AssertionError(f"authorized GPU did not become safely idle: {latest!r}")


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-expert-real-gpu-",
        dir="/var/tmp",
    ) as raw:
        yield Path(raw)


def _admitted_payload(
    root: Path,
    mount_root: Path,
    run_id: str,
) -> dict[str, object]:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text(
        "Bounded two-cycle CUDA validation target.\n",
        encoding="utf-8",
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom G4",
        "-c",
        "user.email=hypoloom@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    data = mount_root / "data"
    data.mkdir()
    (data / "validation.json").write_text(
        json.dumps({"labels": [0, 1, 1, 0], "predictions": [0, 1, 1, 0]}),
        encoding="utf-8",
    )
    profile = root / "profile"
    profile.mkdir()
    for name in PROFILE_FILES:
        text = (_PROFILE_TEMPLATE / name).read_text(encoding="utf-8")
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-target", str(target.resolve())
        )
        text = text.replace(
            "/REPLACE_ME/expert-loop-pilot-data", str(data.resolve())
        )
        (profile / name).write_text(text, encoding="utf-8")
    report = admit_profile(profile, run_id=run_id, allow_gpu=True)
    assert report.admitted and report.create_run_payload is not None, report.to_dict()
    return report.create_run_payload


class _TwoCycleGpuAgent(FakeAgent):
    def __init__(self) -> None:
        super().__init__()
        self.design_cycles: list[int] = []
        self.update_cycles: list[int] = []

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = super().invoke(request)
        if not isinstance(response, AgentResponse):
            return response
        task = request.task_context
        if not isinstance(task, ResearchNodeTask):
            return response
        candidate = dict(response.typed_candidate or {})
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            self.design_cycles.append(task.key.cycle)
            candidate["resource_preference"] = ResourcePreference.GPU_REQUIRED.value
            experiment = next(
                item for item in response.proposed_workspace_artifacts
                if item.path == "experiment.py"
            )
            assert b"torch.cuda.is_available" in experiment.content_bytes()
            artifacts = response.proposed_workspace_artifacts
            if task.key.cycle == 1:
                artifacts = tuple(WorkspaceArtifact.from_bytes(
                    item.path,
                    item.content_bytes().replace(
                        b'"accuracy": 0.55', b'"accuracy": 0.50'
                    ),
                ) for item in artifacts)
                assert artifacts != response.proposed_workspace_artifacts
            return _redigest(replace(
                response,
                typed_candidate=candidate,
                proposed_workspace_artifacts=artifacts,
            ))
        if request.command is AgentCommand.HYPOTHESIS_UPDATE:
            self.update_cycles.append(task.key.cycle)
            candidate["update_disposition"] = (
                HypothesisUpdateDisposition.REVISE_NEXT_CYCLE.value
                if task.key.cycle == 1
                else HypothesisUpdateDisposition.TERMINATE_LINEAGE.value
            )
            return _redigest(replace(response, typed_candidate=candidate))
        return response


class _DeterministicLiteratureFetcher:
    configuration = {"adapter": "deterministic-expert-g4"}

    def __init__(self) -> None:
        self.preflight_count = 0
        self.search_calls: list[LiteratureQuery] = []
        self.fetch_calls: list[str] = []

    def preflight(self) -> dict[str, object]:
        self.preflight_count += 1
        return {"adapter": self.configuration["adapter"], "ready": True}

    def search(self, query: LiteratureQuery) -> tuple[SearchPaper, ...]:
        self.search_calls.append(query)
        latest_year = int(query.latest_as_of[:4])
        values = (
            (
                LiteratureStratum.FOUNDATIONAL,
                "doi:10.1000/expert-g4-foundation",
                latest_year - 25,
            ),
            (
                LiteratureStratum.INTERMEDIATE,
                "arxiv:1801.00001",
                latest_year - 15,
            ),
            (
                LiteratureStratum.LATEST_AS_OF_RUN,
                "openalex:W123456789",
                latest_year,
            ),
        )
        return tuple(
            SearchPaper(
                bibliographic_identity=identity,
                title=f"Deterministic {stratum.value} CUDA pilot evidence",
                authors=("Hypoloom Qualification",),
                year=year,
                language="en",
                landing_uri=f"https://literature.example.org/{ordinal}",
                source_uri=f"https://literature.example.org/record/{ordinal}",
                source_class=ScholarlySourceClass.JOURNAL,
                stratum=stratum,
                pdf_uri=f"https://literature.example.org/paper-{ordinal}.pdf",
            )
            for ordinal, (stratum, identity, year) in enumerate(values, 1)
        )

    def resolve(self, hostname: str) -> tuple[str, ...]:
        assert hostname == "literature.example.org"
        return ("8.8.8.8",)

    def fetch(
        self,
        uri: str,
        *,
        approved_addresses: tuple[str, ...],
        max_bytes: int,
    ) -> FetchResponse:
        assert approved_addresses == ("8.8.8.8",)
        body = f"%PDF-1.7\n{uri}\n%%EOF\n".encode()
        assert len(body) <= max_bytes
        self.fetch_calls.append(uri)
        return FetchResponse(
            requested_uri=uri,
            status_code=200,
            peer_address="8.8.8.8",
            media_type="application/pdf",
            content_length=len(body),
            location=None,
            body=body,
        )

    def extract_pdf(self, content: bytes, *, max_pages: int) -> PdfExtraction:
        assert content.startswith(b"%PDF-1.7") and max_pages >= 2
        return PdfExtraction(
            page_count=2,
            abstract_summary="Deterministic abstract for the bounded G4 fixture.",
            conclusions="Deterministic conclusion for the bounded G4 fixture.",
            claim_support=(ClaimPageRef(
                claim_ref=f"claim-{len(self.fetch_calls)}",
                section="Conclusion",
                page_start=2,
                page_end=2,
            ),),
        )


def _assert_literature_provenance(attempts, fetcher, *, cycles=(1, 2)) -> None:
    plans, packets = {}, {}
    for row in sorted(attempts, key=lambda item: item["started_at"]):
        if row["kind"] != "AGENT":
            continue
        observation = json.loads(row["observation_json"])
        request = AgentRequest.from_dict(observation["request"])
        assert request.schema_version == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
        task = request.task_context
        if isinstance(task, AuxiliaryTask) and task.kind in {
            AuxiliaryRequestKind.GROUNDING_QUERY_PLAN, AuxiliaryRequestKind.GROUNDING,
        }:
            scope = ("GROUNDING", None)
            is_plan = task.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
        elif isinstance(task, ResearchNodeTask) and task.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW:
            scope = ("LITERATURE_REVIEW", task.key.cycle)
            is_plan = task.key.subkind is ResearchNodeSubkind.QUERY_PLAN
        else:
            continue
        assert row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
        if is_plan:
            assert scope not in plans
            query = LiteratureQuery.from_dict(observation["response"]["typed_candidate"])
            assert f"{request.command_id}--openalex-query-planning-skill" in query.immutable_context_refs
            plans[scope] = query
        else:
            assert scope not in packets
            inputs = [item for item in request.input_artifacts if item.ref.endswith("--literature-packet")]
            assert len(inputs) == 1
            packets[scope] = LiteraturePacket.from_bytes(inputs[0].content_bytes())
    scopes = [("GROUNDING", None), *(("LITERATURE_REVIEW", cycle) for cycle in cycles)]
    assert list(plans) == list(packets) == scopes
    # Current Grounding has its own accepted QUERY_PLAN, just like each LR:
    # one coverage probe plus one MAIN retrieval per exact admitted query.
    assert fetcher.search_calls == [plans[scope] for scope in scopes for _ in range(2)]
    expected_fetches = []
    for scope in scopes:
        packet = packets[scope]
        assert packet.query == plans[scope]
        assert len(packet.papers) == 3
        for paper in packet.papers:
            assert paper.pdf_trace is not None
            expected_fetches.append(paper.pdf_trace.source_uri)
    assert fetcher.fetch_calls == expected_fetches


def _drive(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        host.tick()
        head = host.store.latest_transition(run_id)
        if head is not None and head["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return
        time.sleep(0.01)
    raise AssertionError(f"real expert GPU run did not terminate: {host.store.latest_transition(run_id)}")


def _cuda_evidence(state_root: Path, attempt_id: str) -> dict[str, object]:
    path = (
        state_root / "artifacts" / "result-staging" / attempt_id / "logs" / "stdout.log"
    )
    assert path.is_file(), path
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "cuda_device_count" in value:
            return value
    raise AssertionError(f"attempt {attempt_id} lacks CUDA stdout evidence")


def _assert_process_gpu_binding(observation: dict[str, object], gpu_uuid: str) -> None:
    process_request = observation["process_request"]
    assert isinstance(process_request, dict)
    argv = process_request["argv"]
    assert isinstance(argv, list) and Path(argv[0]).name == "bwrap"
    triples = tuple(
        tuple(argv[index : index + 3])
        for index, value in enumerate(argv)
        if value in {"--setenv", "--dev-bind"}
    )
    assert ("--setenv", "CUDA_VISIBLE_DEVICES", gpu_uuid) in triples
    assert ("--setenv", "CUDA_DEVICE_ORDER", "PCI_BUS_ID") in triples
    assert len([item for item in triples if item[0] == "--dev-bind"]) == 3
    usage = observation["resource_usage"]
    assert isinstance(usage, dict) and usage["gpu_uuid"] == gpu_uuid


@_REAL_GPU_ONLY
def test_expert_two_parent_linked_cycles_close_on_real_gpu(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    gpu_uuid, runtime = _configured_gpu()
    _assert_gpu_idle(gpu_uuid)
    run_id = "expert-loop-current-real-gpu-two-cycle"
    state_root = tmp_path / "state"
    resources = LocalComputeResources(
        allowed_gpu_uuids=(gpu_uuid,),
        gpu_runtime_prefix=runtime,
    )
    literature_fetcher = _DeterministicLiteratureFetcher()
    literature_io = FixedLiteratureIO(
        literature_fetcher,
        max_pdf_bytes=4_096,
        max_pdf_pages=8,
        max_redirects=0,
    )
    agent = _TwoCycleGpuAgent()
    host = WorkflowHost(
        state_root,
        compute_resources=resources,
        literature_io=literature_io,
    )
    host.agent = agent
    try:
        host.initialize()
        assert host.compute_resources is resources
        assert isinstance(host.runner, LocalRunner)
        assert isinstance(host.publisher, ArtifactPublisher)
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_admitted_payload(tmp_path, mount_root, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"

        hypotheses = host.store.list_facts(run_id, "HypothesisProposed")
        specs = host.store.list_facts(run_id, "ExperimentPreregistered")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        evidence = host.store.list_facts(run_id, "EvidenceLinked")
        assessments = host.store.list_facts(run_id, "HypothesisAssessed")
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(hypotheses) == len(specs) == len(outcomes) == 2
        assert len(evidence) == len(assessments) == 2
        assert len(verdicts) == 1
        assert hypotheses[1]["payload"]["parent_hypothesis_id"] == hypotheses[0]["hypothesis_id"]
        assert hypotheses[1]["payload"]["relation"] == "revises"
        assert verdicts[0]["payload"]["assessment_ids"] == [
            assessments[1]["payload"]["assessment_id"]
        ]
        assert agent.design_cycles == [1, 2]
        assert agent.update_cycles == [1, 2]
        assert literature_fetcher.preflight_count == 1
        _assert_literature_provenance(
            [row for row in host.store.list_attempts() if row["run_id"] == run_id],
            literature_fetcher,
        )
        assert [row["payload"]["outcome"] for row in outcomes] == [
            "CLEAN_REFUTATION", "SUCCESS",
        ]
        for spec in specs:
            assert spec["payload"]["resource_preference"] == "GPU_REQUIRED"
            assert spec["payload"]["execution_contract"]["resource_grant"] == {
                "kind": "GPU",
                "gpu_uuid": gpu_uuid,
            }

        execution_ids = {str(row["execution_id"]) for row in specs}
        attempts = [
            row for row in host.store.list_attempts()
            if row["run_id"] == run_id
            and row["kind"] == "PROCESS"
            and str(row["execution_id"]) in execution_ids
        ]
        assert len(attempts) == 2
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert all(row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        for row in attempts:
            observation = json.loads(row["observation_json"])
            _assert_process_gpu_binding(observation, gpu_uuid)
            cuda = _cuda_evidence(state_root, str(row["attempt_id"]))
            assert cuda["cuda_available"] is True
            assert cuda["cuda_device_count"] == 1
            assert isinstance(cuda["cuda_device_name"], str) and cuda["cuda_device_name"]
            assert isinstance(cuda["cuda_runtime"], str) and cuda["cuda_runtime"]

        publications = host.store.inspect(
            "SELECT * FROM execution_artifacts WHERE run_id=? ORDER BY attempt_id",
            (run_id,),
        )
        assert len(publications) == 2
        assert not host.running and not host.running_agents
    finally:
        host.close()

    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(run_id)
    assert report.ok, report.to_json()
    assert report.canonical_outcome_count == 2
    assert report.open_attempt_count == 0
    assert report.pending_effect_count == 0
    assert report.owned_process_residue == ()
    work = state_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())
    assert not (state_root / "git-authoring-worktrees").exists()
    _assert_gpu_idle(gpu_uuid)


@_REAL_GPU_ONLY
def test_design_module_rehearsal_reaches_parent_linked_real_gpu_closure(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    gpu_uuid, runtime = _configured_gpu()
    _assert_gpu_idle(gpu_uuid)
    source_run_id = "design-module-rehearsal-source"
    fresh_run_id = "design-module-rehearsal-real-gpu"
    fresh_payload = _admitted_payload(tmp_path, mount_root, fresh_run_id)
    source_admission = admit_profile(
        tmp_path / "profile",
        run_id=source_run_id,
        allow_gpu=True,
    )
    assert source_admission.admitted
    assert source_admission.create_run_payload is not None
    source_root = tmp_path / "source-state"
    source = WorkflowHost(
        source_root,
        compute_resources=LocalComputeResources(
            allowed_gpu_uuids=(gpu_uuid,),
            gpu_runtime_prefix=runtime,
        ),
    )
    source.agent = _TwoCycleGpuAgent()
    try:
        source.initialize()
        created = source.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{source_run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=source_admission.create_run_payload,
        ))
        assert created.status == "accepted", created
        _drive(source, source_run_id)
        design_attempt_id = next(
            str(row["attempt_id"])
            for row in source.store.list_attempts()
            if row["run_id"] == source_run_id
            and row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"]
            == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value
        )
    finally:
        source.close()

    state_root = tmp_path / "fresh-state"
    resources = LocalComputeResources(
        allowed_gpu_uuids=(gpu_uuid,),
        gpu_runtime_prefix=runtime,
    )
    result = rehearse_design_chain(
        source_state_root=source_root,
        source_run_id=source_run_id,
        source_attempt_id=design_attempt_id,
        state_root=state_root,
        run_id=fresh_run_id,
        create_run_payload=fresh_payload,
        allow_agent=True,
        allow_process=True,
        allow_gpu=True,
        compute_resources=resources,
        wall_seconds=180.0,
    )

    assert result["closure"]["complete"] is True, result
    assert result["closure"]["graph_evolved"] is True
    assert result["closure"]["two_cycle_closed"] is True
    assert result["closure"]["gpu_closed"] is True
    assert result["closure"]["oracle_ok"] is True
    assert result["closure"]["residue_free"] is True
    assert result["closure"]["gpu_processes"]
    assert {
        item["gpu_uuid"] for item in result["closure"]["gpu_processes"]
    } == {gpu_uuid}
    assert all(
        isinstance(item["cuda_evidence"], dict)
        for item in result["closure"]["gpu_processes"]
    )

    store = StateStore(state_root)
    try:
        hypotheses = store.list_facts(fresh_run_id, "HypothesisProposed")
        assert len(hypotheses) == 2
        assert hypotheses[1]["payload"]["parent_hypothesis_id"] == (
            hypotheses[0]["hypothesis_id"]
        )
        assert hypotheses[1]["payload"]["relation"] == "revises"
        gpu_attempt_ids = {
            str(item["attempt_id"])
            for item in result["closure"]["gpu_processes"]
        }
        attempts = {
            str(row["attempt_id"]): row
            for row in store.list_attempts()
            if row["run_id"] == fresh_run_id
        }
        for attempt_id in gpu_attempt_ids:
            row = attempts[attempt_id]
            observation = json.loads(row["observation_json"])
            _assert_process_gpu_binding(observation, gpu_uuid)
            cuda = _cuda_evidence(state_root, attempt_id)
            assert cuda["cuda_available"] is True
            assert cuda["cuda_device_count"] == 1
    finally:
        store.close()

    oracle = Oracle(
        state_root / "state.sqlite",
        state_root / "artifacts",
    ).audit(fresh_run_id)
    assert oracle.ok, oracle.to_json()
    assert oracle.open_attempt_count == 0
    assert oracle.pending_effect_count == 0
    assert oracle.owned_process_residue == ()
    _assert_gpu_idle(gpu_uuid)
