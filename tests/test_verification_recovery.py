"""Probe repairs preserve captured code, evidence provenance and finite budgets."""
from copy import deepcopy
from dataclasses import replace
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import EmbeddedInput, WorkspaceArtifact
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.lifecycle import Command, RunState
from hypoloom.verification import ImplementationVerification, review_rows
from hypoloom.verification_audit import formal_gate, request_binding
from hypoloom.verification_contract import context, review_schema, validate_review
from hypoloom.verification_probe import execution_files, RUNNER_PATH, RESULT_PATH, decode_result
from hypoloom.workflow_host import WorkflowHost


def embedded(ref, value):
    return EmbeddedInput.from_bytes(ref, "application/json", json.dumps(value).encode())


def binding_fixture(diagnostic_kind="candidate-validation-error"):
    base = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    commission = {"phase": "inspect", "experiment_id": "experiment",
        "source_submit_command_id": "submit", "source_output_digest": "a" * 64}
    source = {"candidate.py": "answer = 42\n"}
    command = {"run_id": base.run_id, "command_id": base.command_id,
        "request_json": json.dumps({"implementation_verification": commission})}
    commands = {base.command_id: command, "submit": {"run_id": base.run_id, "workflow_id": "experiment",
        "request_json": json.dumps({"source_snapshot": {"output_digest": "a" * 64, "arguments": {
            "workspace_files": [WorkspaceArtifact.from_bytes(p, s.encode()).to_dict() for p, s in source.items()]}}})}}
    previous = {"attempt_id": "previous", "run_id": base.run_id, "workflow_id": base.run_id,
        "command_id": base.command_id, "kind": "AGENT", "state": "CLOSED", "attempt_ordinal": 1,
        "terminal_reason": "FAILED"}
    observation = {"failure_code": "invalid_candidate", "failure_stage": "provider_output", "retry_class": "MODEL",
        "diagnostics": {"validation_error": {"code": "invalid_candidate", "message": "invalid JSON"}}}
    if diagnostic_kind == "candidate-validation-error":
        diagnostic = {"source_attempt_id": "previous", "validation_error": observation["diagnostics"]["validation_error"]}
    else:
        diagnostic = {"source_attempt_id": "previous", "source_attempt_ordinal": 1, "terminal_reason": "FAILED",
            "retry_class": "MODEL", "failure_code": "invalid_candidate", "failure_stage": "provider_output"}
    request = replace(base, schema_version=35, owner_workflow_id=base.run_id, role="verifier", component_id="verifier",
        attempt_id="retry", attempt_ordinal=2, input_artifacts=(
            embedded("job--expert-config", {}), embedded("job--verification-context", commission),
            embedded("job--implementation-source", source), embedded("previous--" + diagnostic_kind, diagnostic)))
    return request, command, commands, {"previous": previous}, {"previous": observation}


@pytest.mark.parametrize("kind", ["candidate-validation-error", "agent-retry-diagnostic"])
def test_retry_diagnostics_are_accepted_only_with_matching_provenance(kind):
    args = binding_fixture(kind)
    request_binding(*args)
    for key, value in (("run_id", "another-run"), ("command_id", "another-command"),
                       ("state", "OPEN"), ("attempt_ordinal", 2)):
        altered = deepcopy(args)
        altered[3]["previous"][key] = value
        with pytest.raises(AssertionError):
            request_binding(*altered)


@pytest.mark.parametrize("mutation", ["source", "unknown_input", "duplicate", "diagnostic"])
def test_retry_allowance_does_not_relax_source_or_input_validation(mutation):
    request, *rest = binding_fixture()
    inputs = list(request.input_artifacts)
    if mutation == "source": inputs[2] = embedded(inputs[2].ref, {"candidate.py": "answer = 0\n"})
    elif mutation == "unknown_input": inputs.append(embedded("job--untrusted", {}))
    elif mutation == "duplicate": inputs.append(embedded("duplicate--expert-config", {}))
    else:
        value = json.loads(inputs[-1].content_bytes())
        value["validation_error"]["message"] = "invented diagnostic"
        inputs[-1] = embedded(inputs[-1].ref, value)
    with pytest.raises(AssertionError):
        request_binding(replace(request, input_artifacts=tuple(inputs)), *rest)


@pytest.mark.parametrize("field", ["diagnostic_summary", "rejected_structured_output", "allowed_supporting_refs", "extra"])
def test_retry_diagnostic_cannot_inject_unrecorded_content(field):
    request, *rest = binding_fixture("agent-retry-diagnostic")
    observation = rest[-1]["previous"]
    observation["request"] = {"immutable_context_refs": ["captured"]}
    observation["diagnostics"]["provider_structured_candidate"] = {"summary": "captured"}
    value = json.loads(request.input_artifacts[-1].content_bytes())
    value[field] = "invented"
    with pytest.raises(AssertionError):
        request_binding(replace(request, input_artifacts=request.input_artifacts[:-1] + (
            embedded(request.input_artifacts[-1].ref, value),)), *rest)


def review_request(*, remaining=2, version=36):
    request = replace(default_request(AgentCommand.VERIFY_IMPLEMENTATION), schema_version=version)
    plan = FakeAgent().invoke(request).typed_candidate["probe"]
    ctx = {**context(request), "phase": "review", "probe_plan": plan, "probe_report_ref": "inspection",
        "probe_repairs_remaining": remaining,
        "probe_evidence": {"usable": False, "attempt_id": "failed-probe", "observation": {}}}
    request = replace(request, input_artifacts=(embedded(request.input_artifacts[0].ref, ctx),))
    report = FakeAgent().invoke(request).typed_candidate
    report.update(verdict="probe_required", probe=deepcopy(plan), summary="The probe omitted an import; preserve checks and fix it.")
    report["probe"]["files"]["verification/probe.py"] = "import math\n" + plan["files"]["verification/probe.py"]
    return request, report


def test_review_can_correct_probe_but_never_drop_checks_or_bypass_a_blocker():
    request, report = review_request()
    validate_review(request, report)
    assert "probe_required" in review_schema(request)["properties"]["verdict"]["enum"]
    for mutation in ("checks", "unchanged", "syntax", "blocker", "passed"):
        altered = deepcopy(report)
        if mutation == "checks": altered["probe"]["checks"][0]["required"] = False
        elif mutation == "unchanged": altered["probe"] = context(request)["probe_plan"]
        elif mutation == "syntax": altered["probe"]["files"]["verification/probe.py"] = "def broken("
        elif mutation == "blocker":
            altered["findings"] = [{"id": "wrong", "severity": "blocker", "message": "Candidate is wrong",
                "evidence_refs": [request.immutable_context_refs[0]], "resolution_condition": "Fix it", "followup_of": None}]
        else: altered.update(verdict="passed", probe=None)
        with pytest.raises(ValueError):
            validate_review(request, altered)


@pytest.mark.parametrize("version,remaining", [(35, 2), (36, 0)])
def test_old_runs_and_exhausted_allowances_cannot_start_probe_repairs(version, remaining):
    request, report = review_request(version=version, remaining=remaining)
    assert "probe_required" not in review_schema(request)["properties"]["verdict"]["enum"]
    with pytest.raises(ValueError, match="exhausted or unsupported"):
        validate_review(request, report)


@pytest.fixture
def recovery(tmp_path, monkeypatch, request):
    scenario = getattr(request, "param", "repaired")
    host = WorkflowHost(tmp_path / "state")
    host.store.initialize()
    document = {"budget": {"max_retries": 2}, "methodology": {"controls": {"product": {
        "experts": {"verifier": {"model": "review-model"}}, "methodology": {}, "language": {}, "safety": {}}}}}
    contract = SimpleNamespace(document=document, to_dict=lambda: document)
    monkeypatch.setattr(host, "_research_contract", lambda _: contract)
    monkeypatch.setattr(host, "_run_config", lambda _: {"execution": {}})
    with host.store.transaction() as tx:
        tx.create_run("run-fake", host.version)
        tx.append_transition(host._transition_record(tx, "run-fake", "run-fake", "RUN", Command.CREATE_RUN, RunState.CREATED))
        tx.append_transition(host._transition_record(tx, "run-fake", "run-fake", "RUN", Command.ACTIVATE, RunState.ACTIVE))
        tx.record_command({"command_id": "submit", "run_id": "run-fake", "workflow_id": "experiment",
            "command_type": "CREATE_EXPERIMENT", "request": {"source_snapshot": {"output_digest": "a" * 64,
                "arguments": {"workspace_files": [WorkspaceArtifact.from_bytes("candidate.py", b"answer = 42\n").to_dict()]}}}})
    author = SimpleNamespace(role="author", component_id="code", command_id="delivery")
    source = ({}, {"source_submit_command_id": "submit", "argv": ["python", "candidate.py"], "environment": {}},
        author, {"arguments": {}}, SimpleNamespace(output_digest="a" * 64, workspace_files={"candidate.py": b"answer = 42\n"}))
    verification = ImplementationVerification(host)
    monkeypatch.setattr(verification, "source", lambda *_: source)
    failures = []
    monkeypatch.setattr(verification, "_reject", lambda run, experiment, report: failures.append(report))
    clock = [10.0]
    requests = []
    # Reviewers are deterministic; the probe itself executes in a real child.
    def dispatch(run, owner, task, *args):
        inputs = verification.inputs(task)
        request = replace(default_request(AgentCommand.VERIFY_IMPLEMENTATION),
            task_context=task, command_id=task.command_id, owner_workflow_id=run,
            role="verifier", component_id="verifier", attempt_id=f"review-{len(requests)}",
            input_artifacts=inputs, immutable_context_refs=tuple(i.ref for i in inputs))
        ctx = context(request)
        response = FakeAgent().invoke(request)
        report = deepcopy(response.typed_candidate)
        if ctx["phase"] == "inspect":
            report["probe"]["files"]["verification/probe.py"] = (
                "from hypoloom_probe import check\nwith check('fixture') as c: c.true(math.isfinite(" +
                ("undefined_value" if scenario == "persistent" else "42") + "))\n")
            if scenario == "comparison":
                report["probe"]["checks"].append({"id": "paired", "kind": "comparison", "required": True,
                    "purpose": "Compare captured integer summation", "basis": "Equal range, independent arithmetic identity"})
                report["probe"]["files"]["verification/probe.py"] = (
                    "from hypoloom_probe import check, compare\nimport runpy\nanswer = runpy.run_path('candidate.py')['answer']\n"
                    "def reference(): return sum(range(answer))\n"
                    "def candidate(): return answer*(answer-1)//2\n"
                    "with check('fixture') as c: c.equal(reference(), candidate())\n"
                    "compare('paired', reference, candidate, workload='sum range(42), CPU, integer output', repeats=3)\n")
        elif scenario == "blocker":
            report.update(verdict="changes_required", summary="The candidate contains a demonstrated defect.",
                findings=[{"id": "wrong", "severity": "blocker", "message": "Candidate is wrong",
                    "evidence_refs": [request.immutable_context_refs[0]], "resolution_condition": "Fix the candidate", "followup_of": None}])
        elif not ctx["probe_evidence"]["usable"] and ctx["probe_repairs_remaining"]:
            report.update(verdict="probe_required", probe=deepcopy(ctx["probe_plan"]),
                summary="Correct the missing math import and retain the implementation assertion.")
            report["probe"]["files"]["verification/probe.py"] = "import math\n" + report["probe"]["files"]["verification/probe.py"]
        validate_review(request, report)
        requests.append(request)
        clock[0] += 1
        with host.store.transaction() as tx:
            tx.open_attempt({"attempt_id": request.attempt_id, "run_id": run, "workflow_id": run,
                "command_id": task.command_id, "kind": "AGENT", "attempt_ordinal": 1,
                "host_generation": "fixture", "started_at": clock[0], "observation": {"request": request.to_dict()}})
            clock[0] += 1
            tx.close_attempt(request.attempt_id, terminal_reason="SUCCEEDED", ended_at=clock[0],
                payload={"request": request.to_dict(), "response": {"typed_candidate": report}})
    monkeypatch.setattr(host, "_expert_agent_step", dispatch)

    def execute(plan, *, terminal_reason=None, close=True):
        ordinal = len(host.store.list_attempts(execution_id="experiment--implementation-probe")) + 1
        aid = f"experiment--probe-{ordinal}"
        folder = tmp_path / aid
        files = {**source[-1].workspace_files, **{p: s.encode() for p, s in plan["files"].items()},
            **execution_files(plan, attempt_id=aid, report_ref=plan["report_ref"])}
        for name, data in files.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        process = subprocess.run([sys.executable, str(folder / RUNNER_PATH)], cwd=folder, capture_output=True, timeout=10)
        result = decode_result((folder / RESULT_PATH).read_bytes())
        reason = "SUCCEEDED" if process.returncode == 0 else "FAILED"
        clock[0] += 1
        with host.store.transaction() as tx:
            if ordinal == 1:
                tx.record_command({"command_id": "probe-command", "run_id": "run-fake", "workflow_id": "experiment",
                    "command_type": "VERIFY_PROCESS", "request": {}})
            tx.open_attempt({"attempt_id": aid, "run_id": "run-fake", "workflow_id": "experiment",
                "command_id": "probe-command", "kind": "PROCESS", "experiment_id": "experiment",
                "execution_id": "experiment--implementation-probe", "attempt_ordinal": ordinal,
                "host_generation": "fixture", "started_at": clock[0]})
            if close:
                clock[0] += 1
                tx.close_attempt(aid, terminal_reason=terminal_reason or reason, ended_at=clock[0], payload={
                    "implementation_probe": True, "check_results": result, "stdout": process.stdout.decode(),
                    "stderr": process.stderr.decode(), "returncode": process.returncode})
        return result
    yield host, verification, execute, requests, failures, document
    host.store.close()
    host._projection_executor.shutdown(wait=True)


def test_host_repairs_probe_then_requires_fresh_evidence_and_review(recovery):
    host, verification, execute, requests, failures, _ = recovery
    allowed, first = verification.gate("run-fake", "experiment")
    assert allowed and first
    assert execute(first)["checks"][0]["status"] == "ERROR"
    allowed, repaired = verification.gate("run-fake", "experiment")
    assert allowed and repaired and repaired["report_ref"] != first["report_ref"]
    assert repaired["checks"] == first["checks"]
    assert repaired["files"] != first["files"]
    before = len(requests)
    # Reconstruct the service from durable records, as after a Host restart.
    restored = ImplementationVerification(host)
    restored.source = verification.source
    assert restored.gate("run-fake", "experiment") == (True, repaired)
    assert len(requests) == before
    assert execute(repaired)["checks"][0]["status"] == "PASS"
    assert verification.gate("run-fake", "experiment") == (True, None)
    assert verification.gate("run-fake", "experiment") == (True, None)
    assert len(requests) == 3 and not failures
    review = context(requests[-1])
    reports = {entry["report_ref"]: entry["report"] for entry in review["prior_reports"]}
    assert review["probe_plan"]["files"] == repaired["files"]
    assert "probe" not in reports[review["probe_report_ref"]]
    assert reports[first["report_ref"]]["probe"]["files"] == first["files"]
    assert len(review_rows(host.store, "run-fake", phase="inspect")) == 1
    commands = {r["command_id"]: r for r in host.store.inspect("SELECT * FROM workflow_commands")}
    attempts = {r["attempt_id"]: r for r in host.store.list_attempts(run_id="run-fake")}
    observations = {k: json.loads(r["observation_json"] or "{}") for k, r in attempts.items()}
    for request in requests:
        request_binding(request, commands[request.command_id], commands, attempts, observations)
    formal_gate({"experiment_id": "experiment", "run_id": "run-fake", "started_at": 100},
        {"contract": {"source_submit_command_id": "submit", "source_agent_output_digest": "a" * 64}},
        commands, attempts, observations)
    # A forged plan/probe binding must not survive an otherwise successful run.
    bad = deepcopy(observations)
    review_ctx = context(requests[-1])
    review_ctx["probe_report_ref"] = first["report_ref"]
    forged = replace(requests[-1], input_artifacts=tuple(
        embedded(i.ref, review_ctx) if i.ref.endswith("--verification-context") else i for i in requests[-1].input_artifacts))
    with pytest.raises(AssertionError):
        request_binding(forged, commands[forged.command_id], commands, attempts, bad)


def test_zero_repair_budget_stays_inconclusive_and_never_authorizes_training(recovery):
    _, verification, execute, requests, failures, document = recovery
    document["budget"]["max_retries"] = 0
    _, probe = verification.gate("run-fake", "experiment")
    execute(probe)
    assert verification.gate("run-fake", "experiment") == (False, None)
    assert len(requests) == 2 and failures[-1]["report"]["verdict"] == "inconclusive"
    assert context(requests[-1])["probe_repairs_remaining"] == 0


@pytest.mark.parametrize("recovery", ["persistent"], indirect=True)
def test_probe_repairs_stop_after_two_even_with_a_larger_model_retry_budget(recovery):
    host, verification, execute, requests, failures, document = recovery
    document["budget"]["max_retries"] = 10
    for _ in range(3):
        allowed, plan = verification.gate("run-fake", "experiment")
        assert allowed and plan
        assert execute(plan)["checks"][0]["status"] == "ERROR"
    assert verification.gate("run-fake", "experiment") == (False, None)
    assert len(requests) == 4 and failures[-1]["report"]["verdict"] == "inconclusive"
    assert len(host.store.list_attempts(kind="PROCESS")) == 3
    assert [context(r).get("probe_repairs_remaining") for r in requests[1:]] == [2, 1, 0]


@pytest.mark.parametrize("recovery", ["blocker"], indirect=True)
def test_demonstrated_candidate_blocker_is_not_routed_to_probe_repair(recovery):
    _, verification, execute, requests, failures, _ = recovery
    _, plan = verification.gate("run-fake", "experiment")
    execute(plan)
    assert verification.gate("run-fake", "experiment") == (False, None)
    assert len(requests) == 2 and failures[-1]["report"]["verdict"] == "changes_required"


def test_open_probe_cannot_be_reviewed_or_duplicated(recovery):
    _, verification, execute, requests, failures, _ = recovery
    _, plan = verification.gate("run-fake", "experiment")
    execute(plan, close=False)
    assert verification.gate("run-fake", "experiment") == (False, None)
    assert len(requests) == 1 and not failures


def test_lost_process_retry_retains_plan_and_does_not_spend_probe_repairs(recovery):
    _, verification, execute, requests, failures, _ = recovery
    _, plan = verification.gate("run-fake", "experiment")
    execute(plan, terminal_reason="LOST")
    assert verification.gate("run-fake", "experiment") == (True, plan)
    assert len(requests) == 1
    execute(plan)
    allowed, repaired = verification.gate("run-fake", "experiment")
    assert allowed and repaired and not failures
    assert context(requests[-1])["probe_repairs_remaining"] == 2


def test_author_feedback_removes_probe_source_and_explains_missing_training_logs(recovery):
    _, verification, execute, _, _, _ = recovery
    _, probe = verification.gate("run-fake", "experiment")
    execute(probe)
    verification.gate("run-fake", "experiment")
    feedback = json.loads(verification.feedback("run-fake", "author", "code", "delivery")[0].content_bytes())
    assert feedback["current_actions"]
    assert all("probe" not in row["report"] for row in feedback["reports"])
    assert "formal training never started" in feedback["scope"]


@pytest.fixture
def recalled(recovery):
    host, verification, execute, requests, failures, _ = recovery
    base = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    task = replace(base.task_context, key=replace(base.task_context.key, expert_slot="author", component_id="code"))
    consumer = replace(base, role="author", component_id="code", schema_version=37,
        task_context=task, command_id=task.command_id)
    with host.store.transaction() as tx:
        tx.append_fact({"fact_id": "registered", "run_id": "run-fake", "fact_type": "ExperimentPreregistered",
            "schema_version": 1, "subject_id": "experiment", "hypothesis_id": "hypothesis",
            "experiment_id": "experiment", "execution_id": "execution", "payload": {"execution_contract": {
                "source_submit_command_id": "submit", "source_agent_output_digest": "a" * 64}}})
    assert verification.memory("run-fake", "author", "code", "next") == ()
    _, plan = verification.gate("run-fake", "experiment")
    execute(plan)
    allowed, plan = verification.gate("run-fake", "experiment")
    if plan is not None:
        assert allowed and verification.memory("run-fake", "author", "code", "next") == ()
        execute(plan)
        assert verification.gate("run-fake", "experiment") == (True, None)
    assert not failures
    artifact, = verification.memory("run-fake", "author", "code", consumer.command_id)
    commands = {r["command_id"]: r for r in host.store.inspect("SELECT * FROM workflow_commands")}
    attempts = {r["attempt_id"]: r for r in host.store.list_attempts(run_id="run-fake")}
    observations = {k: json.loads(r["observation_json"] or "{}") for k, r in attempts.items()}
    consumer = replace(consumer, input_artifacts=(artifact,), immutable_context_refs=(artifact.ref,))
    return host, verification, consumer, commands, attempts, observations


@pytest.mark.parametrize("recovery", ["repaired", "comparison"], indirect=True)
def test_memory_requires_completed_process_and_review_then_survives_service_restart(recalled):
    from hypoloom.verification_audit import engineering_memory_binding
    host, verification, request, commands, attempts, observations = recalled
    artifact, = request.input_artifacts
    document = json.loads(artifact.content_bytes())
    entry, = document["entries"]
    assert entry["source_output_digest"] == "a" * 64
    assert entry["probe_attempt_id"] in attempts and entry["review_attempt_id"] in attempts
    assert "summary" not in entry and "files" not in entry
    engineering_memory_binding(request, {"started_at": 100}, commands, attempts, observations)
    assert ImplementationVerification(host).memory("run-fake", "author", "code", request.command_id) == (artifact,)
    assert verification.feedback("run-fake", "author", "code", request.command_id)[-1] == artifact
    assert verification.memory("run-fake", "peer", "code", "next") == ()
    assert verification.memory("run-fake", "author", "other-component", "next") == ()
    for check in entry["checks"]:
        actual = next(c for c in observations[entry["probe_attempt_id"]]["check_results"]["checks"] if c["id"] == check["id"])
        assert check["measurement"] == actual["measurement"]


@pytest.mark.parametrize("mutation", ["scope", "version", "timing", "source", "future", "failed", "synthetic", "coverage"])
def test_independent_memory_audit_rejects_forged_or_unfinished_evidence(recalled, mutation):
    import hashlib
    from hypoloom.agent_aci import canonical_json
    from hypoloom.verification_audit import engineering_memory_binding
    _, _, request, commands, attempts, observations = recalled
    document = json.loads(request.input_artifacts[0].content_bytes())
    entry = document["entries"][0]
    probe = attempts[entry["probe_attempt_id"]]
    actual = observations[entry["probe_attempt_id"]]
    if mutation == "scope": document["expert"] = "peer"
    elif mutation == "version": request = replace(request, schema_version=36)
    elif mutation == "timing": entry["checks"][0]["measurement"] = {"speedup": 99}
    elif mutation == "source": entry["source_output_digest"] = "b" * 64
    elif mutation == "future": attempts[entry["review_attempt_id"]]["ended_at"] = 101
    elif mutation == "failed": probe["terminal_reason"] = "TIMED_OUT"
    elif mutation == "synthetic": actual["synthetic_before_spawn"] = True
    else: entry["covered"] = []
    # A matching document hash alone must not authorize invented content.
    document["version_id"] = hashlib.sha256(canonical_json({k: v for k, v in document.items() if k != "version_id"})).hexdigest()
    request = replace(request, input_artifacts=(embedded(request.input_artifacts[0].ref, document),))
    with pytest.raises((AssertionError, ValueError)):
        engineering_memory_binding(request, {"started_at": 100}, commands, attempts, observations)


def test_captured_submission_can_complete_verification_after_author_timeout(recovery):
    host, verification, execute, _, failures, _ = recovery
    request = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    with host.store.transaction() as tx:
        tx.open_attempt({"attempt_id": "author-timeout", "run_id": "run-fake", "workflow_id": "run-fake",
            "command_id": "submit", "kind": "AGENT", "attempt_ordinal": 1, "host_generation": "fixture",
            "started_at": 1, "observation": {"request": request.to_dict()}})
        tx.close_attempt("author-timeout", terminal_reason="TIMED_OUT", ended_at=2,
            payload={"request": request.to_dict(), "failure_code": "timeout"})
    for _ in range(2):
        allowed, probe = verification.gate("run-fake", "experiment")
        assert allowed and probe
        execute(probe)
    assert verification.gate("run-fake", "experiment") == (True, None) and not failures
    assert host.store.get_attempt("author-timeout")["terminal_reason"] == "TIMED_OUT"


def test_retained_run_never_receives_new_memory(recalled, monkeypatch):
    host, verification, request, *_ = recalled
    frozen = {**host.store.get_run("run-fake"), "agent_aci_version": "36"}
    monkeypatch.setattr(host.store, "get_run", lambda _: frozen)
    assert verification.memory("run-fake", "author", "code", request.command_id) == ()
    assert len(verification.feedback("run-fake", "author", "code", request.command_id)) == 1


@pytest.mark.parametrize("mutation", ["returncode", "source", "check", "blocking_gap", "unreviewed"])
def test_memory_producer_rejects_broken_evidence_even_if_author_says_passed(recalled, mutation):
    from hypoloom.engineering_memory import verified_entry
    _, _, request, commands, attempts, observations = recalled
    entry = json.loads(request.input_artifacts[0].content_bytes())["entries"][0]
    review, plan, probe = (attempts[entry[k]] for k in ("review_attempt_id", "probe_report_ref", "probe_attempt_id"))
    commission = json.loads(commands[review["command_id"]]["request_json"])["implementation_verification"]
    planned = json.loads(commands[plan["command_id"]]["request_json"])["implementation_verification"]
    execution = {"source_submit_command_id": "submit", "source_agent_output_digest": "a" * 64}
    if mutation == "returncode": observations[probe["attempt_id"]]["returncode"] = 1
    elif mutation == "source": execution["source_agent_output_digest"] = "b" * 64
    elif mutation == "check": observations[probe["attempt_id"]]["check_results"]["checks"][0]["status"] = "ERROR"
    elif mutation == "blocking_gap":
        observations[review["attempt_id"]]["response"]["typed_candidate"]["coverage"]["uncovered"] = [
            {"condition": "CUDA", "reason": "Not measured", "blocking": True}]
    else: review["state"] = "OPEN"
    for row in (review, plan, probe): row["observation_json"] = json.dumps(observations[row["attempt_id"]])
    assert verified_entry(run_id="run-fake", role="author", component_id="code", commission=commission,
        review=review, plan_commission=planned, plan_attempt=plan, probe=probe, execution=execution) is None


def test_oracle_routes_memory_checks_with_process_attempts_included(recalled):
    from hypoloom.oracle import _audit_agent_attempts
    _, _, request, commands, attempts, observations = recalled
    consumer = {"attempt_id": request.attempt_id, "run_id": request.run_id, "workflow_id": request.owner_workflow_id,
        "command_id": request.command_id, "kind": "AGENT", "attempt_ordinal": 1, "state": "OPEN",
        "started_at": 100, "ended_at": None, "terminal_reason": None}
    attempts[request.attempt_id] = consumer
    observations[request.attempt_id] = {"request": request.to_dict()}
    commands[request.command_id] = {"command_id": request.command_id, "run_id": request.run_id,
        "workflow_id": request.owner_workflow_id, "command_type": "REQUEST_RESEARCH_NODE", "request_json": "{}"}
    for forged in (False, True):
        if forged:
            entry = json.loads(request.input_artifacts[0].content_bytes())["entries"][0]
            observations[entry["probe_attempt_id"]]["returncode"] = 1
        issues = []
        _audit_agent_attempts("1", "37", [], {}, [], {}, list(commands.values()), list(attempts.values()), observations, issues)
        assert any(i.code == "engineering_memory_binding_invalid" for i in issues) is forged
