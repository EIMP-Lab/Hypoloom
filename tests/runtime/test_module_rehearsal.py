from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

import hypoloom.module_rehearsal as module_rehearsal
from hypoloom.agent_aci import (
    CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AgentRequest, AgentResponse, EmbeddedInput, WorkspaceArtifact,
    validate_postflight,
)
from hypoloom.cli import main as cli_main
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, HypothesisAssessment
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.fake_agent import FakeAgent, _redigest, default_request
from hypoloom.module_rehearsal import (
    ModuleRehearsalError,
    rehearse_design_chain,
    rehearse_module_chain,
)
from hypoloom.oracle import Oracle
from hypoloom.profile_admission import PROFILE_FILES, admit_profile
from hypoloom.state_store import StateStore
from hypoloom.workflow_host import WorkflowHost


_REPOSITORY = Path(__file__).resolve().parents[2]
_PROFILE_TEMPLATE = _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture
def mount_root() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-module-rehearsal-",
        dir="/var/tmp",
    ) as raw:
        yield Path(raw)


def _profile(tmp_path: Path, mount_root: Path) -> Path:
    target = mount_root / "target"
    target.mkdir()
    (target / "README.md").write_text(
        "Bounded design-to-closure module rehearsal target.\n",
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
    (data / "README.txt").write_text(
        "bounded module rehearsal fixture metadata\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in PROFILE_FILES:
        text = (_PROFILE_TEMPLATE / name).read_text(encoding="utf-8")
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
    return profile


def _payload(profile: Path, run_id: str) -> dict[str, object]:
    report = admit_profile(profile, run_id=run_id)
    assert report.admitted and report.create_run_payload is not None, report.to_dict()
    return report.create_run_payload


def _role_mismatched_payload(
    profile: Path,
    destination: Path,
    run_id: str,
) -> dict[str, object]:
    destination.mkdir()
    for name in PROFILE_FILES:
        text = (profile / name).read_text(encoding="utf-8")
        (destination / name).write_text(
            text.replace("ml-researcher", "alternate-researcher"),
            encoding="utf-8",
        )
    return _payload(destination, run_id)


def _drive(host: WorkflowHost, run_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        host.tick()
        head = host.store.latest_transition(run_id)
        if head is not None and head["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return
        time.sleep(0.005)
    raise AssertionError(
        f"source module run did not terminate: {host.store.latest_transition(run_id)}"
    )


def _source_run(
    state_root: Path, profile: Path, run_id: str, *,
    fail_design: bool = False, source_version: int = 14,
) -> tuple[str, str, str]:
    # Source import remains explicitly limited to retained ACI13/14; the
    # destination rehearsal below uses the unpatched current Host.
    with pytest.MonkeyPatch.context() as source_wire:
        source_wire.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", source_version)
        return _source_run_pinned(state_root, profile, run_id, fail_design=fail_design)


def _source_run_pinned(
    state_root: Path,
    profile: Path,
    run_id: str,
    *,
    fail_design: bool = False,
) -> tuple[str, str, str]:
    host = WorkflowHost(state_root)
    host.agent = _FailDesignAgent() if fail_design else _RefutingFirstCycleAgent()
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_payload(profile, run_id),
        ))
        assert created.status == "accepted", created
        _drive(host, run_id)
        attempts = tuple(
            row for row in host.store.list_attempts() if row["run_id"] == run_id
        )
        grounding_id = next(
            str(row["attempt_id"])
            for row in attempts
            if row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"]
            == AgentCommand.GROUNDING.value
        )
        query_plan_id = next(
            str(row["attempt_id"])
            for row in attempts
            if row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"]
            == AgentCommand.LITERATURE_REVIEW.value
            and json.loads(row["observation_json"])["request"]["task_context"][
                "kind"
            ]
            == "RESEARCH_NODE"
            and json.loads(row["observation_json"])["request"]["task_context"][
                "key"
            ]["subkind"]
            == "QUERY_PLAN"
        )
        design_id = next(
            str(row["attempt_id"])
            for row in attempts
            if row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"]
            == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value
        )
        return grounding_id, query_plan_id, design_id
    finally:
        host.close()


@pytest.mark.parametrize("source_version", [13, 14])
def test_source_fixture_pins_only_retained_source_and_restores_current_host(monkeypatch, source_version):
    import hypoloom.workflow_host as host_module

    current = host_module.EXPERT_LOOP_ACI_SCHEMA_VERSION

    def source(*_args, **_kwargs):
        assert host_module.EXPERT_LOOP_ACI_SCHEMA_VERSION == source_version
        return "grounding", "query", "design"

    monkeypatch.setattr(__name__ + "._source_run_pinned", source)
    assert _source_run(Path("source"), Path("profile"), "run", source_version=source_version) == (
        "grounding", "query", "design"
    )
    assert host_module.EXPERT_LOOP_ACI_SCHEMA_VERSION == current


class _FailDesignAgent(FakeAgent):
    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return b'{"status":'
        return super().invoke(request)


class _RefutingFirstCycleAgent(FakeAgent):
    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        response = super().invoke(request)
        if not isinstance(response, AgentResponse):
            return response
        task = request.task_context
        if (
            request.schema_version >= 14
            and request.command is AgentCommand.HYPOTHESIS_PROPOSAL
            and isinstance(task, ResearchNodeTask)
            and task.key.subkind is ResearchNodeSubkind.SYNTHESIS
        ):
            member, = response.typed_candidate["hypotheses"]
            return _redigest(replace(response, typed_candidate={
                "schema_version": 1, "hypotheses": [{**member, "chain_id": "chain-b"}],
            }))
        if (
            request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
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
            request.command is AgentCommand.HYPOTHESIS_UPDATE
            and isinstance(task, ResearchNodeTask)
            and task.key.cycle == 1
        ):
            candidate = dict(response.typed_candidate or {})
            candidate["update_disposition"] = "REVISE_NEXT_CYCLE"
            return _redigest(replace(response, typed_candidate=candidate))
        return response


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


def test_cuda_evidence_reads_the_published_attempt_log(tmp_path: Path) -> None:
    attempt_id = "run--experiment--process-1"
    logs = (
        tmp_path
        / "artifacts"
        / "result-staging"
        / attempt_id
        / "logs"
    )
    logs.mkdir(parents=True)
    (logs / "stdout.log").write_text(
        '{"cuda_available":true,"cuda_device_count":1}\n',
        encoding="utf-8",
    )

    assert module_rehearsal._cuda_stdout_evidence(
        tmp_path,
        attempt_id,
        {"stdout_path": "stdout.log"},
    ) == {"cuda_available": True, "cuda_device_count": 1}
    assert module_rehearsal._cuda_stdout_evidence(
        tmp_path,
        attempt_id,
        {"stdout_path": "../stdout.log"},
    ) is None


def _source_exchange(version: int, *, chain: str | None = "chain-b"):
    request = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    task = ResearchNodeTask(key=replace(request.task_context.key,
        chain=chain if version == 14 else None))
    request = replace(request, schema_version=version, task_context=task,
        command_id=task.command_id)
    facts = [{"fact_type": "HypothesisProposed", "payload": {
        "run_id": request.run_id, "hypothesis_id": "hypothesis-" + name,
        "component_id": request.component_id, "proposer_role": request.role,
        "method_family_id": request.method_family_id, "parent_hypothesis_id": None,
        "claim": "Distinct mechanism " + name, "assumptions": [name]}}
        for name in (["a", "b"] if version == 14 else ["b"])]
    if version == 14:
        context = EmbeddedInput.from_bytes(task.command_id + "--batch-context",
            "application/json", json.dumps({"batch_command_id": "source-synthesis",
                "cycle": 1, "chains": {"chain-a": "hypothesis-a", "chain-b": "hypothesis-b"},
                "terminal_tips": {}, "selected_tip": None}).encode())
        request = replace(request, input_artifacts=(context,), immutable_context_refs=(context.ref,))
    return request, FakeAgent().invoke(request), facts


def _source_report(request, response, facts):
    return {"attempts": [{"kind": "AGENT", "state": "CLOSED", "terminal_reason": "SUCCEEDED",
        "attempt_id": request.attempt_id, "attempt_ordinal": request.attempt_ordinal,
        "run_id": request.run_id, "workflow_id": request.owner_workflow_id,
        "command_id": request.command_id,
        "observation": {"request": request.to_dict(), "response": response.to_dict()}}],
        "related_records": {"facts": facts}}


@pytest.mark.parametrize("version", [13, 14])
def test_source_case_selects_exact_chain_not_a_same_family_peer(monkeypatch, version):
    request, response, facts = _source_exchange(version)
    report = _source_report(request, response, facts)
    monkeypatch.setattr(module_rehearsal, "build_debug_report", lambda *args, **kwargs: report)
    selected_request, _, hypothesis = module_rehearsal._source_case(
        Path("/not-read"), request.run_id, request.attempt_id, design_only=True)
    assert selected_request == request
    assert hypothesis["hypothesis_id"] == "hypothesis-b"


@pytest.mark.parametrize("damage", ["no-chain", "missing-context", "missing-chain", "wrong-cycle", "wrong-fact"])
def test_source_case_rejects_unbound_aci14_chain(monkeypatch, damage):
    request, _, facts = _source_exchange(14, chain=None if damage == "no-chain" else "chain-b")
    if damage == "missing-context":
        request = replace(request, input_artifacts=(), immutable_context_refs=())
    elif damage in {"missing-chain", "wrong-cycle"}:
        item = request.input_artifacts[0]
        context = json.loads(item.content_bytes())
        if damage == "missing-chain":
            del context["chains"]["chain-b"]
        else:
            context["cycle"] = 2
        request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(
            item.ref, item.media_type, json.dumps(context).encode()),))
    if damage == "wrong-fact":
        facts[-1]["payload"]["run_id"] = "another-run"
    report = _source_report(request, FakeAgent().invoke(request), facts)
    monkeypatch.setattr(module_rehearsal, "build_debug_report", lambda *args, **kwargs: report)
    with pytest.raises(ModuleRehearsalError, match="chain|batch|hypothesis"):
        module_rehearsal._source_case(Path("/not-read"), request.run_id, request.attempt_id, design_only=True)


@pytest.mark.parametrize("version", [13, 14])
def test_rehearsal_projects_one_source_hypothesis_into_current_synthesis(version):
    from tests.runtime.test_agent_aci import _pincer_request
    source, response, facts = _source_exchange(version)
    agent = module_rehearsal._ModuleRehearsalAgent(source_request=source,
        source_response=response, source_hypothesis=facts[-1]["payload"])
    synthesis = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    synthesis = replace(synthesis, schema_version=14,
        required_output_schema="HypothesisSet/v1",
        required_model_identity=agent.model_identity, required_adapter_version=agent.adapter_version)
    projected = agent.invoke(synthesis)
    validated = validate_postflight(synthesis, projected)
    member, = validated.candidate.hypotheses
    assert member.chain_id == ("chain-b" if version == 14 else "1")
    assert member.candidate.claim == "Distinct mechanism b"
    assert member.candidate.assumptions == ("b",)
    assert agent.rebound_fresh_attempt_ids == []


def test_aci14_rebinding_preserves_base_identity_but_requires_exact_chain():
    source, response, facts = _source_exchange(14)
    agent = module_rehearsal._ModuleRehearsalAgent(source_request=source,
        source_response=response, source_hypothesis=facts[-1]["payload"])
    peer_task = ResearchNodeTask(key=replace(source.task_context.key, chain="chain-a"))
    peer = replace(source, task_context=peer_task, command_id=peer_task.command_id,
        attempt_id="fresh-peer", required_model_identity=agent.model_identity,
        required_adapter_version=agent.adapter_version)
    assert module_rehearsal._module_identity(peer) == module_rehearsal._module_identity(source)
    # This small default_request is not the bounded accuracy pilot. A peer
    # must reach the ordinary fixture branch, never consume the source output.
    with pytest.raises(ModuleRehearsalError, match="bounded pilot metric fixture"):
        agent.invoke(peer)
    assert agent.rebound_fresh_attempt_ids == []
    selected = replace(peer, task_context=source.task_context, command_id=source.command_id,
        attempt_id="fresh-selected")
    agent.invoke(selected)
    assert agent.rebound_fresh_attempt_ids == ["fresh-selected"]


@pytest.mark.parametrize("source_version", [14, 13])
def test_design_chain_rehearsal_uses_fresh_authority_and_closes_downstream(
    tmp_path: Path,
    mount_root: Path,
    source_version: int,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(
        source_root, profile, source_run_id, source_version=source_version
    )
    source_authority = _authority_snapshot(source_root)
    source_artifacts = _artifact_snapshot(source_root / "artifacts")
    source_resources = _artifact_snapshot(source_root / source_run_id)

    fresh_root = tmp_path / "fresh-state"
    fresh_run_id = "module-rehearsal-fresh"
    result = rehearse_design_chain(
        source_state_root=source_root,
        source_run_id=source_run_id,
        source_attempt_id=design_attempt_id,
        state_root=fresh_root,
        run_id=fresh_run_id,
        create_run_payload=_payload(profile, fresh_run_id),
        allow_agent=True,
        allow_process=True,
        wall_seconds=60.0,
    )

    assert result["non_authoritative"] is True
    assert result["mode"] == "MODULE_CHAIN_REHEARSAL"
    assert result["handler"] == "design-to-closure/1"
    assert result["source"]["state_root"] == str(source_root.resolve())
    assert result["source"]["run_id"] == source_run_id
    assert result["source"]["attempt_id"] == design_attempt_id
    assert result["fresh_run"]["state_root"] == str(fresh_root.resolve())
    assert result["fresh_run"]["run_id"] == fresh_run_id
    assert result["closure"]["oracle_ok"] is True, result
    assert result["closure"]["residue_free"] is True, result
    assert result["closure"]["two_cycle_closed"] is True, result
    assert result["fresh_run"]["source_output_rebound_once"] is True
    assert len(result["fresh_run"]["rebound_fresh_attempt_ids"]) == 1

    assert _authority_snapshot(source_root) == source_authority
    assert _artifact_snapshot(source_root / "artifacts") == source_artifacts
    assert _artifact_snapshot(source_root / source_run_id) == source_resources

    store = StateStore(fresh_root)
    try:
        run = store.get_run(fresh_run_id)
        assert run is not None
        assert run["agent_aci_version"] == str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION)
        assert store.latest_transition(fresh_run_id)["state"] == "COMPLETED"
        attempts = tuple(
            row for row in store.list_attempts() if row["run_id"] == fresh_run_id
        )
        fresh_attempt_ids = {str(row["attempt_id"]) for row in attempts}
        assert design_attempt_id not in fresh_attempt_ids
        assert all(row["state"] == "CLOSED" for row in attempts)
        process_attempts = tuple(row for row in attempts if row["kind"] == "PROCESS")
        specs = store.list_facts(fresh_run_id, "ExperimentPreregistered")
        scientific_execution_ids = {str(row["execution_id"]) for row in specs}
        scientific_attempts = tuple(
            row for row in process_attempts
            if str(row["execution_id"]) in scientific_execution_ids
        )
        validation_attempts = tuple(
            row for row in process_attempts
            if str(row["execution_id"]) not in scientific_execution_ids
        )
        assert len(scientific_attempts) == 2
        assert validation_attempts == ()
        assert all(
            row["terminal_reason"] == "SUCCEEDED" for row in process_attempts
        )
        agent_commands = {
            json.loads(row["observation_json"])["request"]["command"]
            for row in attempts
            if row["kind"] == "AGENT"
        }
        assert AgentCommand.RESULT_ANALYSIS.value in agent_commands
        assert AgentCommand.HYPOTHESIS_UPDATE.value in agent_commands
        assert {
            AgentCommand.ADJUDICATE.value, AgentCommand.CYCLE_REPORT.value,
            AgentCommand.SELF_EVOLVE.value, AgentCommand.FINAL_REPORT.value,
        } <= agent_commands
        assert all(row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        source_store = StateStore(source_root)
        try:
            source_design = AgentRequest.from_dict(json.loads(
                source_store.get_attempt(design_attempt_id)["observation_json"])["request"])
        finally:
            source_store.close()
        assert source_design.schema_version == source_version
        rebound = store.get_attempt(result["fresh_run"]["rebound_fresh_attempt_ids"][0])
        rebound_request = AgentRequest.from_dict(json.loads(rebound["observation_json"])["request"])
        assert rebound_request.task_context.key.chain == ("chain-b" if source_version == 14 else "1")
        first_cycle_designs = [row for row in attempts if row["kind"] == "AGENT"
            and (body := json.loads(row["observation_json"])["request"])["command"] == AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT.value
            and body["task_context"]["key"]["cycle"] == 1]
        assert len(first_cycle_designs) == 1
        updates = sorted(
            (
                json.loads(row["observation_json"])
                for row in attempts
                if row["kind"] == "AGENT"
                and json.loads(row["observation_json"])["request"]["command"]
                == AgentCommand.HYPOTHESIS_UPDATE.value
            ),
            key=lambda item: item["request"]["task_context"]["key"]["cycle"],
        )
        assert [
            item["response"]["typed_candidate"]["update_disposition"]
            for item in updates
        ] == ["REVISE_NEXT_CYCLE", "TERMINATE_LINEAGE"]
        hypotheses = store.list_facts(fresh_run_id, "HypothesisProposed")
        assert len(hypotheses) == 2
        assert hypotheses[1]["payload"]["parent_hypothesis_id"] == (
            hypotheses[0]["hypothesis_id"]
        )
        assert hypotheses[1]["payload"]["relation"] == "revises"
        assert len(specs) == 2
        assert len(store.list_facts(fresh_run_id, "CanonicalOutcomeAccepted")) == 2
        assessments = store.list_facts(fresh_run_id, "HypothesisAssessed")
        assert [row["payload"]["assessment"] for row in assessments] == [
            HypothesisAssessment.REFUTED.value,
            HypothesisAssessment.SUPPORTED.value,
        ]
        assert len(store.list_facts(fresh_run_id, "GoalVerdictRecorded")) == 1
    finally:
        store.close()

    oracle = Oracle(
        fresh_root / "state.sqlite",
        fresh_root / "artifacts",
    ).audit(fresh_run_id)
    assert oracle.ok, oracle.to_json()
    assert oracle.open_attempt_count == 0
    assert oracle.pending_effect_count == 0
    assert oracle.owned_process_residue == ()
    work = fresh_root / "artifacts" / "work"
    assert not work.exists() or not tuple(work.iterdir())
    assert not (fresh_root / "git-authoring-worktrees").exists()


def test_query_plan_chain_rehearsal_rebinds_once_and_closes_two_cycles(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "query-plan-rehearsal-source"
    _, query_plan_attempt_id, _ = _source_run(
        source_root,
        profile,
        source_run_id,
    )
    source_authority = _authority_snapshot(source_root)
    source_artifacts = _artifact_snapshot(source_root / "artifacts")
    fresh_root = tmp_path / "fresh-state"
    fresh_run_id = "query-plan-rehearsal-fresh"

    result = rehearse_module_chain(
        source_state_root=source_root,
        source_run_id=source_run_id,
        source_attempt_id=query_plan_attempt_id,
        state_root=fresh_root,
        run_id=fresh_run_id,
        create_run_payload=_payload(profile, fresh_run_id),
        allow_agent=True,
        allow_process=True,
    )

    assert result["handler"] == "query-plan-to-closure/1"
    assert result["closure"]["complete"] is True, result
    assert result["closure"]["graph_evolved"] is True
    assert result["closure"]["two_cycle_closed"] is True
    assert result["fresh_run"]["source_output_rebound_once"] is True
    rebound_fresh = result["fresh_run"]["rebound_fresh_attempt_ids"]
    assert len(rebound_fresh) == 1
    assert rebound_fresh != [query_plan_attempt_id]
    assert _authority_snapshot(source_root) == source_authority
    assert _artifact_snapshot(source_root / "artifacts") == source_artifacts

    store = StateStore(fresh_root)
    try:
        hypotheses = store.list_facts(fresh_run_id, "HypothesisProposed")
        assert len(hypotheses) == 2
        assert hypotheses[1]["payload"]["parent_hypothesis_id"] == (
            hypotheses[0]["hypothesis_id"]
        )
        assert len(store.list_facts(fresh_run_id, "CanonicalOutcomeAccepted")) == 2
        assessments = store.list_facts(fresh_run_id, "HypothesisAssessed")
        assert [row["payload"]["assessment"] for row in assessments] == [
            HypothesisAssessment.REFUTED.value,
            HypothesisAssessment.SUPPORTED.value,
        ]
    finally:
        store.close()


def test_design_chain_rehearsal_rejects_existing_destination_before_source_work(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(source_root, profile, source_run_id)
    source_authority = _authority_snapshot(source_root)
    fresh_root = tmp_path / "already-exists"
    fresh_root.mkdir()
    sentinel = fresh_root / "sentinel"
    sentinel.write_text("operator-owned\n", encoding="utf-8")

    with pytest.raises(ModuleRehearsalError, match="must not exist"):
        rehearse_design_chain(
            source_state_root=source_root,
            source_run_id=source_run_id,
            source_attempt_id=design_attempt_id,
            state_root=fresh_root,
            run_id="module-rehearsal-fresh",
            create_run_payload=_payload(profile, "module-rehearsal-fresh"),
            allow_agent=True,
            allow_process=True,
        )

    assert sentinel.read_text(encoding="utf-8") == "operator-owned\n"
    assert _authority_snapshot(source_root) == source_authority


def test_rehearsal_atomically_claims_destination_before_host_writes(
    tmp_path: Path,
    mount_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(source_root, profile, source_run_id)
    source_authority = _authority_snapshot(source_root)
    fresh_root = tmp_path / "concurrently-claimed"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir(mode=0o700)
    original_source_case = module_rehearsal._source_case

    def race_destination(*args: object, **kwargs: object) -> object:
        selected = original_source_case(*args, **kwargs)
        fresh_root.symlink_to(unrelated, target_is_directory=True)
        return selected

    monkeypatch.setattr(module_rehearsal, "_source_case", race_destination)

    with pytest.raises(ModuleRehearsalError, match="claimed concurrently"):
        rehearse_design_chain(
            source_state_root=source_root,
            source_run_id=source_run_id,
            source_attempt_id=design_attempt_id,
            state_root=fresh_root,
            run_id="module-rehearsal-fresh",
            create_run_payload=_payload(profile, "module-rehearsal-fresh"),
            allow_agent=True,
            allow_process=True,
        )

    assert fresh_root.is_symlink()
    assert tuple(unrelated.iterdir()) == ()
    assert _authority_snapshot(source_root) == source_authority


def test_design_chain_rehearsal_requires_explicit_agent_and_process_effects(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(source_root, profile, source_run_id)
    source_authority = _authority_snapshot(source_root)

    for suffix, allow_agent, allow_process in (
        ("no-agent", False, True),
        ("no-process", True, False),
    ):
        fresh_root = tmp_path / suffix
        with pytest.raises(
            ModuleRehearsalError,
            match="explicitly authorized",
        ):
            rehearse_design_chain(
                source_state_root=source_root,
                source_run_id=source_run_id,
                source_attempt_id=design_attempt_id,
                state_root=fresh_root,
                run_id=f"module-rehearsal-{suffix}",
                create_run_payload=_payload(
                    profile,
                    f"module-rehearsal-{suffix}",
                ),
                allow_agent=allow_agent,
                allow_process=allow_process,
            )
        assert not fresh_root.exists()

    assert _authority_snapshot(source_root) == source_authority


def test_design_chain_rehearsal_requires_absolute_disjoint_roots(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(source_root, profile, source_run_id)

    with pytest.raises(ModuleRehearsalError, match="must be absolute"):
        rehearse_design_chain(
            source_state_root=source_root,
            source_run_id=source_run_id,
            source_attempt_id=design_attempt_id,
            state_root=Path("relative-fresh-state"),
            run_id="module-rehearsal-relative",
            create_run_payload=_payload(profile, "module-rehearsal-relative"),
            allow_agent=True,
            allow_process=True,
        )

    assert not Path("relative-fresh-state").exists()


def test_design_chain_rehearsal_does_not_accept_a_mismatched_fresh_profile(
    tmp_path: Path,
    mount_root: Path,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(source_root, profile, source_run_id)
    source_authority = _authority_snapshot(source_root)
    source_artifacts = _artifact_snapshot(source_root / "artifacts")
    fresh_run_id = "module-rehearsal-mismatched"
    fresh_root = tmp_path / "fresh-state"
    payload = _role_mismatched_payload(
        profile,
        tmp_path / "mismatched-profile",
        fresh_run_id,
    )

    with pytest.raises(ModuleRehearsalError, match="did not close"):
        rehearse_design_chain(
            source_state_root=source_root,
            source_run_id=source_run_id,
            source_attempt_id=design_attempt_id,
            state_root=fresh_root,
            run_id=fresh_run_id,
            create_run_payload=payload,
            allow_agent=True,
            allow_process=True,
        )

    assert fresh_root.is_dir()
    assert _authority_snapshot(source_root) == source_authority
    assert _artifact_snapshot(source_root / "artifacts") == source_artifacts
    store = StateStore(fresh_root)
    try:
        assert len(store.list_facts(fresh_run_id, "HypothesisProposed")) == 1
        updates = [
            json.loads(row["observation_json"])["response"]["typed_candidate"]
            for row in store.list_attempts()
            if row["run_id"] == fresh_run_id
            and row["kind"] == "AGENT"
            and json.loads(row["observation_json"])["request"]["command"]
            == AgentCommand.HYPOTHESIS_UPDATE.value
        ]
        assert [item["update_disposition"] for item in updates] == [
            "TERMINATE_LINEAGE"
        ]
    finally:
        store.close()


def test_rehearsal_cli_refuses_implicit_effects_without_creating_root(
    tmp_path: Path,
    mount_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(source_root, profile, source_run_id)
    source_authority = _authority_snapshot(source_root)
    fresh_root = tmp_path / "fresh-state"

    assert cli_main([
        "debug",
        "rehearse-chain",
        "--source-state-root",
        str(source_root),
        "--source-run-id",
        source_run_id,
        "--through-attempt-id",
        design_attempt_id,
        "profile",
        "--profiles-root",
        str(tmp_path),
        "--state-root",
        str(fresh_root),
        "--run-id",
        "module-rehearsal-fresh",
    ]) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "error"
    assert "explicitly authorized" in error["error"]
    assert not fresh_root.exists()
    assert _authority_snapshot(source_root) == source_authority


def test_rehearsal_cli_runs_complete_cpu_chain_with_explicit_effects(
    tmp_path: Path,
    mount_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    _, _, design_attempt_id = _source_run(source_root, profile, source_run_id)
    source_authority = _authority_snapshot(source_root)
    source_artifacts = _artifact_snapshot(source_root / "artifacts")
    fresh_root = tmp_path / "fresh-state"

    assert cli_main([
        "debug",
        "rehearse-chain",
        "--source-state-root",
        str(source_root),
        "--source-run-id",
        source_run_id,
        "--through-attempt-id",
        design_attempt_id,
        "profile",
        "--profiles-root",
        str(tmp_path),
        "--state-root",
        str(fresh_root),
        "--run-id",
        "module-rehearsal-fresh",
        "--allow-agent",
        "--allow-process",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["handler"] == "design-to-closure/1"
    assert result["closure"]["complete"] is True
    assert result["closure"]["oracle_ok"] is True
    assert result["closure"]["graph_evolved"] is True
    assert result["closure"]["two_cycle_closed"] is True
    assert result["fresh_run"]["source_output_rebound_once"] is True
    assert _authority_snapshot(source_root) == source_authority
    assert _artifact_snapshot(source_root / "artifacts") == source_artifacts


@pytest.mark.parametrize("invalid_source", ("non_design", "non_success", "tampered"))
def test_design_chain_rehearsal_revalidates_source_before_creating_destination(
    tmp_path: Path,
    mount_root: Path,
    invalid_source: str,
) -> None:
    profile = _profile(tmp_path, mount_root)
    source_root = tmp_path / "source-state"
    source_run_id = "module-rehearsal-source"
    grounding_attempt_id, _, design_attempt_id = _source_run(
        source_root,
        profile,
        source_run_id,
        fail_design=invalid_source == "non_success",
    )
    selected_id = design_attempt_id
    if invalid_source == "non_design":
        selected_id = grounding_attempt_id
    elif invalid_source == "tampered":
        with sqlite3.connect(source_root / "state.sqlite") as connection:
            # Deliberately construct a corrupt retained-store fixture. Production
            # writes cannot bypass this immutability trigger.
            connection.execute("DROP TRIGGER execution_attempt_update_rules")
            row = connection.execute(
                "SELECT observation_json FROM execution_attempts WHERE attempt_id=?",
                (design_attempt_id,),
            ).fetchone()
            assert row is not None
            observation = json.loads(row[0])
            observation["response"]["command_id"] = "tampered-command"
            connection.execute(
                "UPDATE execution_attempts SET observation_json=? WHERE attempt_id=?",
                (
                    json.dumps(observation, sort_keys=True, separators=(",", ":")),
                    design_attempt_id,
                ),
            )
    source_authority = _authority_snapshot(source_root)
    source_artifacts = _artifact_snapshot(source_root / "artifacts")
    fresh_root = tmp_path / "fresh-state"

    with pytest.raises(ModuleRehearsalError):
        rehearse_design_chain(
            source_state_root=source_root,
            source_run_id=source_run_id,
            source_attempt_id=selected_id,
            state_root=fresh_root,
            run_id="module-rehearsal-fresh",
            create_run_payload=_payload(profile, "module-rehearsal-fresh"),
            allow_agent=True,
            allow_process=True,
        )

    assert not fresh_root.exists()
    assert _authority_snapshot(source_root) == source_authority
    assert _artifact_snapshot(source_root / "artifacts") == source_artifacts
