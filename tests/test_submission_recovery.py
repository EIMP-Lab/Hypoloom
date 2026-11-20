from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import (AgentACIError, WorkspaceArtifact, canonical_json,
    validate_experiment_submission, validate_submission_snapshot)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.execution_workspace import ExecutionWorkspace, sandbox_command
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.lifecycle import Command, ExperimentState, RunState
from hypoloom.monitor import build_status_projection
from hypoloom.oracle import _audit_submitted_experiments, _row_identifier
from hypoloom.state_store import IdentityConflict
from hypoloom.runner import LocalRunner
from hypoloom.verification_probe import RUNNER_PATH, RESULT_PATH, execution_files, validate_result
from hypoloom.workflow_host import OperationalHalt, WorkflowHost


def submission():
    candidate = FakeAgent().invoke(default_request(AgentCommand.DRAFT_EXPERIMENT_SPEC)).typed_candidate
    base = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    request = replace(base, schema_version=41, owner_workflow_id=base.run_id,
        method_family_id=candidate["method_family_id"])
    args = {"candidate": dict(candidate), "workspace_files": [
        WorkspaceArtifact.from_bytes("experiment.py", b"print(1)\n").to_dict(),
        WorkspaceArtifact.from_bytes("implementation.patch", b"").to_dict()]}
    return request, args


def historical_draft(host, *, corrupt=False):
    request, args = submission()
    snapshot = validate_experiment_submission(request, args)[2]
    args["candidate"]["acceptance_criteria"] = ["valid", "E", "E"]
    snapshot["arguments"]["candidate"] = deepcopy(args["candidate"])
    del snapshot["output_digest"]
    snapshot["output_digest"] = "0" * 64 if corrupt else hashlib.sha256(canonical_json(snapshot)).hexdigest()
    submit = request.command_id + "--submit--initial"
    identity = {"source_submit_command_id": submit, "source_agent_attempt_id": request.attempt_id,
        "execution_node_command_id": request.command_id, "component_id": request.component_id,
        "role": request.role, "expert_cycle": 1, "experiment_ordinal": 1}
    with host.store.transaction() as tx:
        tx.record_command({"command_id": request.command_id, "run_id": request.run_id, "workflow_id": request.run_id,
            "command_type": "REQUEST_RESEARCH_NODE", "request": {"task_context": request.task_context.to_dict()}})
        tx.open_attempt({"attempt_id": request.attempt_id, "run_id": request.run_id, "workflow_id": request.run_id,
            "kind": "AGENT", "command_id": request.command_id, "host_generation": "original", "attempt_ordinal": 1,
            "started_at": time.time(), "observation": {"request": request.to_dict()}})
        tx.record_command({"command_id": submit, "run_id": request.run_id, "workflow_id": "draft-A",
            "command_type": "CREATE_EXPERIMENT", "status": "accepted", "result": {"exp_id": "draft-A"}, "revision": 1,
            "request": {"arguments": args, "predecessor_experiment_id": None, "execution_node_command_id": request.command_id,
                "source_request": request.to_dict(), "source_snapshot": snapshot, "tool": "submit_experiment"}})
        tx.append_transition(host._transition_record(tx, request.run_id, "draft-A", "EXPERIMENT", Command.CREATE_EXPERIMENT,
            ExperimentState.DRAFTED, command_id=submit, next_command=Command.PREREGISTER.value, payload=identity))
        tx.close_attempt(request.attempt_id, terminal_reason="FAILED", ended_at=time.time(),
            payload={"request": request.to_dict(), "failure_code": "dispatch_paused"})
    return request, args, snapshot


@pytest.fixture
def host(tmp_path):
    host = WorkflowHost(tmp_path / "state")
    host.store.initialize()
    with host.store.transaction() as tx:
        tx.create_run("run-fake", host.version)
        tx.append_transition(host._transition_record(tx, "run-fake", "run-fake", "RUN", Command.CREATE_RUN, RunState.CREATED))
    try: yield host
    finally: host.close()


def test_first_submission_rejects_before_allocating_a_workflow_or_command(host):
    request, args = submission()
    args["candidate"]["acceptance_criteria"] = ["first", "value", "value"]
    before = host.store.inspect("SELECT command_id FROM workflow_commands")
    with pytest.raises(AgentACIError) as error:
        host._submit_experiment(request, args)
    assert error.value.detail == {"field": "acceptance_criteria", "code": "duplicate_item", "first_index": 1, "index": 2}
    assert host.store.inspect("SELECT command_id FROM workflow_commands") == before
    assert not host.store.list_workflow_heads(request.run_id, "EXPERIMENT")


def test_native_and_domain_validation_identify_the_same_hypothesis_duplicate():
    from hypoloom.agent_aci import _hypothesis_candidate
    from hypoloom.domain import HypothesisCandidate
    from hypoloom.native_output import decode_output
    raw = {"component_id": "c", "proposer_role": "expert", "method_family_id": "m", "claim": "hypothesis",
        "assumptions": ["A", "B", "B"]}
    with pytest.raises(AgentACIError) as aci: _hypothesis_candidate(raw)
    with pytest.raises(ValueError) as domain:
        HypothesisCandidate(claim=raw["claim"], owner_role=raw["proposer_role"], assumptions=tuple(raw["assumptions"]))
    assert aci.value.detail == domain.value.detail == {"field": "assumptions", "code": "duplicate_item", "first_index": 1, "index": 2}
    with pytest.raises(ValueError) as native:
        decode_output(json.dumps(raw), {"type": "object", "properties": {"assumptions": {"type": "array", "uniqueItems": True}}})
    assert "index 2 matches index 1" in native.value.correction_hint


@pytest.mark.parametrize("command,field,item,nonempty", [
    (AgentCommand.DRAFT_EXPERIMENT_SPEC, "acceptance_criteria", {"type": "string"}, True),
    (AgentCommand.DRAFT_EXPERIMENT_SPEC, "artifact_requirements", {"const": "result.json"}, True),
    (AgentCommand.PROPOSE_HYPOTHESIS, "assumptions", {"type": "string"}, False),
])
def test_native_candidate_schema_introduces_list_constraints_at_aci_42(command, field, item, nonempty):
    from hypoloom.claude_agent_host import _output_schema

    def schema(version):
        request = replace(default_request(command), schema_version=version)
        return _output_schema(request)["properties"]["typed_candidate"]["properties"][field]

    assert schema(41) == {"type": "array", "items": item}
    current_item = {**item, "minLength": 1} if "type" in item else item
    assert schema(42) == {"type": "array", "items": current_item, "uniqueItems": True,
        **({"minItems": 1} if nonempty else {})}


def test_minimum_sample_executes_captured_source_in_the_admitted_cpu_sandbox(tmp_path):
    request, args = submission()
    smoke = {"args": ["--tiny"], "outputs": ["sample.json"]}
    args["entrypoint_smoke"] = smoke
    args["command"] = ["python", "experiment.py", "--config", "original"]
    source = (b"import os,sys\nfrom pathlib import Path\n"
        b"assert sys.argv[1:] == ['--config','original','--tiny'] and Path.cwd() == Path('/output')\n"
        b"assert Path(__file__) == Path('/input/experiment.py') and os.getenv('SAMPLE_MODE') == 'frozen'\n"
        b"Path('sample.json').write_text(Path('/fixtures/item.txt').read_text())\n")
    args["workspace_files"][0] = WorkspaceArtifact.from_bytes("experiment.py", source).to_dict()
    with pytest.raises(AgentACIError): validate_experiment_submission(request, args)
    request = replace(request, schema_version=42)
    _, _, receipt = validate_experiment_submission(request, args)
    assert validate_submission_snapshot(request, receipt).output_digest == receipt["output_digest"]
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "item.txt").write_text("42")
    state = tmp_path / "state"
    artifacts = state / "artifacts"
    artifacts.mkdir(parents=True)
    workspace = ExecutionWorkspace(artifacts, state_root=state)
    checks = [{"id": key, "purpose": key, "basis": "one captured input", "kind": "assertion", "required": True}
              for key in ("formal_entrypoint", "content")]
    probe = {"checks": checks, "argv": ["python", "verification/check.py"]}
    frozen = sandbox_command(receipt["arguments"]["command"], "experiment.py", "python")
    files = execution_files(probe, attempt_id="probe1", report_ref="review1", formal_argv=frozen, smoke=smoke)
    files["verification/check.py"] = (b"from pathlib import Path\nfrom hypoloom_probe import check\n"
        b"with check('content') as c: c.equal(Path('sample.json').read_text(), '42')\n")
    prepared = workspace.plan(agent_request=request.to_dict(), agent_response=receipt, attempt_id="probe1",
        attempt_ordinal=1, execution_id="execution1", target_env={"SAMPLE_MODE": "frozen"}, timeout_seconds=10,
        source_relative_path=RUNNER_PATH, execution_argv=["python", RUNNER_PATH], trusted_workspace_files=files,
        read_only_bindings=[(fixture, "/fixtures")])
    workspace.materialize(prepared, agent_request=request.to_dict(), agent_response=receipt)
    process = LocalRunner().run(prepared.process_request)
    assert process.returncode == 0, (prepared.process_request.cwd, process)
    result = json.loads((prepared.output_dir / RESULT_PATH).read_bytes())
    assert validate_result(result, checks, attempt_id="probe1", report_ref="review1")["passed"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_authenticated_legacy_draft_replays_and_only_field_defects_can_close_it(host, monkeypatch, corrupt):
    request, args, snapshot = historical_draft(host, corrupt=corrupt)
    head = host.store.latest_transition("draft-A")
    monkeypatch.setattr(host, "_review_role", lambda *_: None)
    monkeypatch.setattr(host, "_batch_enabled", lambda _: False)
    monkeypatch.setattr(host, "_research_contract", lambda *_: None)
    if corrupt:
        with pytest.raises(OperationalHalt, match="immutable"):
            host._drive_experiment(request.run_id, head, "PREREGISTER")
        with pytest.raises(OperationalHalt, match="immutable"):
            host._submit_experiment(request, args)
        assert host.store.latest_transition("draft-A") == head
        return
    assert validate_submission_snapshot(request, snapshot).output_digest == snapshot["output_digest"]
    assert host._submit_experiment(request, args) == {"exp_id": "draft-A"}
    changed = deepcopy(args)
    changed["candidate"]["acceptance_criteria"].pop()
    with pytest.raises(IdentityConflict): host._submit_experiment(request, changed)
    host._drive_experiment(request.run_id, head, "PREREGISTER")
    head = host.store.latest_transition("draft-A")
    assert head["previous_state"] == "DRAFTED" and head["state"] == "TERMINAL"
    assert not host.store.list_facts(request.run_id) and not host.store.list_attempts(workflow_id="draft-A")
    assert not host.dispatch_errors
    rejected = host._preflight_rejection(request.run_id, "draft-A")
    assert rejected["field_error"]["index"] == 2 and rejected["reason"] == "invalid_submission"
    assert host._submit_experiment(request, args) == {"exp_id": "draft-A"}
    status = build_status_projection(host.store, request.run_id, quiescent_for_backup=None)
    assert status["experiment_queue"]["recent_rejections"][0]["field_error"] == rejected["field_error"]
    commands = host.store.inspect("SELECT * FROM workflow_commands")
    transitions = host.store.inspect("SELECT rowid AS raw_order,* FROM workflow_transitions ORDER BY rowid")
    attempts = host.store.list_attempts(run_id=request.run_id)
    contents = {_row_identifier(row): json.loads(row["payload_json"]) for row in transitions}
    observations = {a["attempt_id"]: json.loads(a["observation_json"]) for a in attempts}
    issues = []
    _audit_submitted_experiments(commands, transitions, contents, attempts, observations, issues)
    assert not issues
    contents[_row_identifier(head)]["field_error"]["index"] = 5
    _audit_submitted_experiments(commands, transitions, contents, attempts, observations, issues)
    assert [issue.code for issue in issues] == ["preflight_control_close_invalid"]


def cpu_run(host):
    metric = {"name": "score", "direction": "MAXIMIZE", "target": "1"}
    component = {"id": "primary.score", "tier": "primary", "name": "score", "owner": "scientist",
        "proposers": ["scientist"], "dependencies": [], "required_inputs": [], "protocol": None,
        "result": {"kind": "metric", "metric": "score", "direction": "MAXIMIZE", "target": "1"},
        "seeds": [], "required_artifacts": []}
    contract = {"schema_version": 1, "goal": {"objective": "Check a deterministic CPU result", "language": "en",
        "verdict_rule": "required_conjunction", "summary_metric": {**metric, "display_only": True}},
        "roles": [{"id": "scientist"}], "components": [component], "support_role_budgets": {},
        "protocols": {}, "delivery_contracts": {}, "methodology": {}, "runtime": {},
        "budget": {"max_experiments": 4, "agent_invocation": host.agent_budget.to_dict()}, "safety": {}, "observer": {}}
    execution = {"delay_seconds": 0, "allow_gpu": False, "target": None, "runtime_prefix": None, "read_roots": []}
    response = host.handle(ControlRequest(1, "create", 0, "CREATE_RUN",
        {"run_id": "run-fake", "research_contract": contract, "execution": execution}))
    assert response.status == "accepted", response.error


def pending_cpu_process(host, monkeypatch):
    cpu_run(host)
    pending = []
    with monkeypatch.context() as pause:
        pause.setattr(host, "_evaluate_experiment", lambda run, experiment, **kwargs: pending.append(experiment))
        deadline = time.monotonic() + 10
        while not pending and time.monotonic() < deadline:
            host.tick()
            assert not host.dispatch_errors, host.dispatch_errors
            time.sleep(.01)
    assert pending
    spec = host.store.find_fact("run-fake", "ExperimentPreregistered", pending[0])
    attempt, = host.store.list_attempts(execution_id=spec["execution_id"])
    assert attempt["terminal_reason"] == "SUCCEEDED"
    return pending[0], attempt["attempt_id"]


def test_local_rejection_leaves_sibling_preparation_and_collection_available(tmp_path, monkeypatch):
    host = WorkflowHost(tmp_path / "local")
    host.initialize()
    try:
        experiment, _ = pending_cpu_process(host, monkeypatch)
        request, _, _ = historical_draft(host)
        run = request.run_id
        hypothesis = host.store.find_fact(run, "ExperimentPreregistered", experiment)["hypothesis_id"]
        with host.store.transaction() as tx:
            tx.append_transition(host._transition_record(tx, run, "sibling-C", "EXPERIMENT", Command.CREATE_EXPERIMENT,
                ExperimentState.DRAFTED, payload={"component_id": "primary.score"}))
            tx.append_fact({"fact_id": "spec-C", "run_id": run, "fact_type": "ExperimentPreregistered",
                "schema_version": 1, "subject_id": "sibling-C", "experiment_id": "sibling-C",
                "hypothesis_id": hypothesis, "execution_id": "execution-C", "payload": {"component_id": "primary.score"}})
            tx.append_transition(host._transition_record(tx, run, "sibling-C", "EXPERIMENT", Command.PREREGISTER,
                ExperimentState.PREREGISTERED, fact_id="spec-C", next_command="PREPARE"))
        for workflow, command in (("draft-A", "PREREGISTER"), ("sibling-C", "PREPARE"), (experiment, "ACCEPT_OUTCOME")):
            host._drive_experiment(run, host.store.latest_transition(workflow), command)
        assert host.store.latest_transition("sibling-C")["state"] == "READY"
        assert host.store.latest_transition("draft-A")["state"] == "TERMINAL"
        assert [f["payload"]["outcome"] for f in host.store.list_facts(run, "CanonicalOutcomeAccepted")] == ["SUCCESS"]
        assert not host.dispatch_errors
    finally:
        host.close()


def test_collection_refuses_mismatched_terminal_attempt_without_publishing(tmp_path, monkeypatch):
    host = WorkflowHost(tmp_path / "mismatch")
    host.initialize()
    try:
        experiment, attempt = pending_cpu_process(host, monkeypatch)
        read = host.store.list_attempts
        def inconsistent_observation(**filters):
            rows = read(**filters)
            for row in rows:
                if row["attempt_id"] == attempt:
                    observation = json.loads(row["observation_json"])
                    observation["process_request"]["attempt_id"] = "someone-else"
                    row["observation_json"] = json.dumps(observation)
            return rows
        monkeypatch.setattr(host.store, "list_attempts", inconsistent_observation)
        with pytest.raises(OperationalHalt, match="Process identity"):
            host._collect_admitted_outcome("run-fake", "independent-draft")
        assert host.store.latest_transition(experiment)["state"] == "EVALUATING"
        assert not host.store.list_facts("run-fake", "CanonicalOutcomeAccepted")
        assert not host.store.inspect("SELECT artifact_id FROM execution_artifacts WHERE attempt_id=?", (attempt,))
    finally:
        host.close()


def test_only_an_already_bound_analysis_reply_can_finish_during_quarantine(tmp_path, monkeypatch):
    from hypoloom.collaboration import RendezvousRequest
    host = WorkflowHost(tmp_path / "reply")
    host.initialize()
    try:
        experiment, _ = pending_cpu_process(host, monkeypatch)
        request = default_request(AgentCommand.ANSWER_RENDEZVOUS)
        envelope = next(i for i in request.input_artifacts if i.ref.endswith("--rendezvous"))
        expected = RendezvousRequest.from_dict(json.loads(envelope.content_bytes()))
        request = replace(request, owner_workflow_id=experiment, role=expected.target_role,
            immutable_context_refs=expected.context_refs, input_artifacts=(
                replace(envelope, ref=expected.rendezvous_id + "--rendezvous"),))
        origin = SimpleNamespace(rendezvous_id=expected.rendezvous_id, target_role=expected.target_role, request=lambda _: expected)
        monkeypatch.setattr(host, "_review_status", lambda *_: (origin, None))
        assert host._is_admitted_outcome_review(request)
        assert not host._is_admitted_outcome_review(replace(request, role="different-reviewer"))
        monkeypatch.setattr(host, "_review_status", lambda *_: (None, None))
        assert not host._is_admitted_outcome_review(request)
    finally:
        host.close()


@pytest.mark.parametrize("boundary", [None, "after_artifact_publish_before_digest_commit",
    "inside_outcome_after_artifact_before_fact", "inside_outcome_after_fact_before_terminal", "after_outcome_commit_before_cleanup"])
def test_quarantined_intake_collects_existing_cpu_process_once(tmp_path, monkeypatch, boundary):
    root = tmp_path / "cpu"
    host = WorkflowHost(root)
    host.initialize()
    try:
        experiment, attempt = pending_cpu_process(host, monkeypatch)
        request, _, _ = historical_draft(host)
        # An unexpected dispatch failure stays attributed to A and blocks admission.
        def fail_dispatch(*args):
            raise RuntimeError("injected scheduling fault")
        with monkeypatch.context() as inject:
            inject.setattr(host, "_preregister_submission", fail_dispatch)
            inject.setattr(host, "_drive_active", lambda run: host._drive_experiment(request.run_id,
                host.store.latest_transition("draft-A"), "PREREGISTER"))
            host.tick()
        fault = host.dispatch_errors[request.run_id]
        assert fault["experiment_id"] == "draft-A" and fault["command"] == "PREREGISTER" and fault["allow_collection"]
        assert fault["execution_node_command_id"] == request.command_id
        if boundary is not None:
            def crash(point):
                if point == boundary: raise SystemExit(86)
            with monkeypatch.context() as inject:
                inject.setattr(host, "_crash", crash)
                with pytest.raises(SystemExit): host.tick()
            host.close()
            host = WorkflowHost(root)
            host.initialize()
            # Restart replays committed facts and removes disposable work. The
            # explicit collection pass also exercises a second delivery after commit.
            host._collect_admitted_outcome(request.run_id, "draft-A")
        else:
            host.tick()
        assert not host._collect_admitted_outcome(request.run_id, "draft-A")
        assert len(host.store.list_facts(request.run_id, "CanonicalOutcomeAccepted")) == 1
        assert host.store.latest_transition(experiment)["state"] == "TERMINAL"
        assert len(host.store.inspect("SELECT artifact_id FROM execution_artifacts WHERE attempt_id=?", (attempt,))) == 1
        assert not (root / "artifacts" / "work" / attempt).exists()
    finally:
        host.close()
