from dataclasses import replace
import copy
import json

import pytest

from hypoloom.agent_aci import AgentACIError, validate_postflight, with_output_digest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.verification import review_rows, accepted_review
from hypoloom.verification_contract import validate_review
from test_host_experiment_tools import execution, _until
from test_execution_handoff import handoff


def test_inspection_cannot_self_approve_or_overwrite_candidate():
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    response = FakeAgent().invoke(request)
    validate_postflight(request, response)
    from hypoloom.claude_agent_host import _output_schema
    from hypoloom.reference_aliases import reference_aliases, canonical_output
    schema = _output_schema(request, tool_workspace=True)
    assert schema["properties"]["typed_candidate"]["properties"]["supporting_refs"]["items"]["enum"] == ["R1"]
    aliases = reference_aliases(request, _output_schema(request, reference_aliases=False))
    aliased = copy.deepcopy(response.typed_candidate)
    aliased["supporting_refs"] = ["R1"]
    validate_review(request, canonical_output({"typed_candidate": aliased}, aliases)["typed_candidate"])
    candidate = copy.deepcopy(response.typed_candidate)
    candidate.update(verdict="passed", probe=None)
    with pytest.raises(ValueError, match="initial inspection"):
        validate_review(request, candidate)
    candidate = copy.deepcopy(response.typed_candidate)
    candidate["probe"]["files"] = {"experiment.py": "print('forged')"}
    with pytest.raises(ValueError, match="verification/"):
        validate_review(request, candidate)


@pytest.mark.parametrize("mutation", ["reference", "timeout", "empty", "blocker"])
def test_reviewer_output_is_validated(mutation):
    request = default_request(AgentCommand.VERIFY_IMPLEMENTATION)
    response = FakeAgent().invoke(request)
    candidate = copy.deepcopy(response.typed_candidate)
    if mutation == "reference": candidate["supporting_refs"] = ["another-run"]
    if mutation == "timeout": candidate["probe"]["timeout_seconds"] = 3600
    if mutation == "empty": candidate["probe"]["files"] = {}
    if mutation == "blocker": candidate.update(verdict="changes_required", probe=None)
    with pytest.raises(AgentACIError): validate_postflight(request, with_output_digest(replace(response, typed_candidate=candidate)))


@pytest.mark.parametrize("probe_fails", [False, True])
def test_host_probe_then_independent_review_before_training(execution, monkeypatch, probe_fails):
    host, author, arguments = execution
    monkeypatch.setattr(host.verification, "enabled", lambda _: True)
    capacity = host._effect_capacity
    monkeypatch.setattr(host, "_effect_capacity", lambda run, kind="AGENT": 1 if kind == "AGENT" else capacity(run, kind))
    invoke = host.agent.invoke
    seen = []
    def review(request):
        response = invoke(request)
        if request.command is AgentCommand.VERIFY_IMPLEMENTATION:
            seen.append(request)
            candidate = copy.deepcopy(response.typed_candidate)
            if candidate["probe"]:
                candidate["probe"]["files"]["verification/probe.py"] = (
                    "from pathlib import Path\nfrom hypoloom_probe import check\nwith check('fixture') as c:\n"
                    " c.true(Path('/input/experiment.py').is_file(),'captured candidate exists')\n"
                    " try:\n  Path('/input/experiment.py').write_text('changed')\n  c.true(False,'candidate writable')\n"
                    " except OSError:\n  c.true(True,'candidate is read-only')\n"
                    " print('candidate is read-only')\n" + (" c.true(False,'independent negative probe')\n" if probe_fails else ""))
            response = with_output_digest(replace(response, typed_candidate=candidate))
        return response
    monkeypatch.setattr(host.agent, "invoke", review)
    result = host.handle_agent_tool(author.attempt_id, "submit_experiment", arguments)
    host._finish_fake_agent(author, handoff(author, result["exp_id"]))
    _until(host, lambda: host.store.latest_transition(result["exp_id"])["state"] == "TERMINAL")
    assert len(seen) == 2
    assert [json.loads(next(i.content_bytes() for i in r.input_artifacts if i.ref.endswith("--verification-context")))["phase"] for r in seen] == ["inspect", "review"]
    assert all(not any(i.ref.endswith(("--goal", "--expert-resources", "--hypothesis")) for i in r.input_artifacts) for r in seen)
    probes = host.store.list_attempts(execution_id=result["exp_id"] + "--implementation-probe")
    assert len(probes) == 1 and probes[0]["state"] == "CLOSED"
    evidence = json.loads(probes[0]["observation_json"])
    assert "candidate is read-only" in evidence["stdout"]
    assert not host.running
    rows = review_rows(host.store, author.run_id)
    final = accepted_review(host.store, rows[-1])
    assert final["report"]["verdict"] == ("inconclusive" if probe_fails else "passed")
    formal = [r for r in host.store.list_attempts(workflow_id=result["exp_id"]) if r["kind"] == "PROCESS" and r not in probes]
    assert len(formal) == 1
    observation = json.loads(formal[0]["observation_json"])
    assert bool(observation.get("synthetic_before_spawn")) is probe_fails
    assert formal[0]["started_at"] >= max(host.store.get_attempt(r.attempt_id)["ended_at"] for r in seen)
    from hypoloom.oracle import Oracle
    audit = Oracle(host.store.database_path, host.publisher.root).audit(author.run_id)
    assert audit.ok, audit.to_dict()


def test_demonstrated_blocker_reaches_author_without_starting_training(execution, monkeypatch):
    host, author, arguments = execution
    monkeypatch.setattr(host.verification, "enabled", lambda _: True)
    invoke = host.agent.invoke
    def review(request):
        response = invoke(request)
        if request.command is AgentCommand.VERIFY_IMPLEMENTATION:
            candidate = copy.deepcopy(response.typed_candidate)
            candidate.update(verdict="changes_required", probe=None, findings=[{
                "id": "dispatch", "severity": "blocker", "message": "The entrypoint calls the inherited method instead of the supplied implementation.",
                "evidence_refs": list(request.immutable_context_refs), "resolution_condition": "Call the supplied implementation and prove dispatch with a small probe.", "followup_of": None}])
            response = with_output_digest(replace(response, typed_candidate=candidate))
        return response
    monkeypatch.setattr(host.agent, "invoke", review)
    result = host.handle_agent_tool(author.attempt_id, "submit_experiment", arguments)
    host._finish_fake_agent(author, handoff(author, result["exp_id"]))
    _until(host, lambda: bool(host.local_execution_agents))
    resumed = next(iter(host.local_execution_agents.values()))
    feedback = json.loads(next(i.content_bytes() for i in resumed.input_artifacts if i.ref.endswith("--implementation-reviews")))
    assert feedback["reports"][0]["report"]["findings"][0]["id"] == "dispatch"
    assert not host.running and not host.store.list_attempts(execution_id=result["exp_id"] + "--implementation-probe")


def test_model_overlay_is_admitted_without_profile_writes(tmp_path):
    import yaml
    from test_profile_v4 import canonical_documents
    from hypoloom.profile_admission import admit_profile
    from hypoloom.profile_v4 import resolve_node_runtime
    documents = canonical_documents()
    from pathlib import Path
    import subprocess
    target = tmp_path / "target"
    target.mkdir()
    (target / "source.py").write_text("print('target')\n")
    for argv in (["init", "-q"], ["add", "."], ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "target"]):
        subprocess.run(["git", "-C", str(target), *argv], check=True)
    documents["methodology"]["runtime"]["target_repo"] = str(target)
    documents["safety"]["allow_read_paths"] = [str(tmp_path)]
    source = {name + ".yaml": yaml.safe_dump(value) for name, value in documents.items()}
    source_before = copy.deepcopy(source)
    report = admit_profile(tmp_path / "unwritten", run_id="verification-model", source_texts=source,
        verifier_model="claude-opus-4-6", verifier_effort="high")
    assert report.admitted, report.to_dict()
    contract = report.create_run_payload["research_contract"]
    runtime = resolve_node_runtime(contract, None, "verifier")
    assert runtime["model"] == "claude-opus-4-6" and runtime["effort"] == "high"
    assert source == source_before and not (tmp_path / "unwritten").exists()


def test_unchanged_capture_reuses_review_but_changed_code_requires_new_review(execution, monkeypatch):
    host, author, arguments = execution
    monkeypatch.setattr(host.verification, "enabled", lambda _: True)
    result = host.handle_agent_tool(author.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: host.store.latest_transition(result["exp_id"])["state"] == "TERMINAL")
    # A repeat after a successful experiment belongs to a new delivery, so test
    # reuse at the same accepted capture boundary without forging its source.
    source = host.verification.source(author.run_id, result["exp_id"])
    original = host.verification.source
    monkeypatch.setattr(host.verification, "source", lambda r, e: source if e == "new-delivery" else original(r, e))
    row = host.verification._task(author.run_id, "new-delivery", "inspect", source)
    assert row["request"]["implementation_verification"]["reused_from"]
    modified = list(source)
    modified[3] = copy.deepcopy(source[3])
    modified[3]["arguments"]["workspace_files"][0]["content_base64"] = "Y2hhbmdlZA=="
    row = host.verification._task(author.run_id, "changed-delivery", "inspect", tuple(modified))
    assert "reused_from" not in row["request"]["implementation_verification"]


def test_probe_cancel_is_not_a_scientific_execution(execution, monkeypatch):
    host, author, arguments = execution
    monkeypatch.setattr(host.verification, "enabled", lambda _: True)
    invoke = host.agent.invoke
    def review(request):
        response = invoke(request)
        if request.command is AgentCommand.VERIFY_IMPLEMENTATION and response.typed_candidate["probe"]:
            candidate = copy.deepcopy(response.typed_candidate)
            candidate["probe"]["files"]["verification/probe.py"] = "import time\ntime.sleep(30)\n"
            response = with_output_digest(replace(response, typed_candidate=candidate))
        return response
    monkeypatch.setattr(host.agent, "invoke", review)
    result = host.handle_agent_tool(author.attempt_id, "submit_experiment", arguments)
    _until(host, lambda: bool(host.running))
    host.handle_agent_tool(author.attempt_id, "cancel_experiment", {"exp_id": result["exp_id"]})
    _until(host, lambda: host.store.latest_transition(result["exp_id"])["state"] == "TERMINAL")
    assert not host.running
    probes = host.store.list_attempts(execution_id=result["exp_id"] + "--implementation-probe")
    assert probes[0]["terminal_reason"] == "CANCELLED"
    assert len(review_rows(host.store, author.run_id)) == 1


def test_recovery_retries_probe_without_consuming_formal_retry(execution, monkeypatch):
    from hypoloom.verification import ImplementationVerification
    from hypoloom.workflow_host import WorkflowHost
    host, author, arguments = execution
    monkeypatch.setattr(ImplementationVerification, "enabled", lambda self, _: True)
    result = host.handle_agent_tool(author.attempt_id, "submit_experiment", arguments)
    host._finish_fake_agent(author, handoff(author, result["exp_id"]))
    class Crash(BaseException): pass
    def crash(point):
        if point == "after_process_intent_before_spawn": raise Crash()
    monkeypatch.setattr(host, "_crash", crash)
    with pytest.raises(Crash): _until(host, lambda: False)
    root = host.state_root
    host.close()
    reopened = WorkflowHost(root)
    reopened.initialize()
    try:
        try:
            _until(reopened, lambda: reopened.store.latest_transition(result["exp_id"])["state"] == "TERMINAL")
        except AssertionError:
            assert not reopened.dispatch_errors
            raise
        probes = reopened.store.list_attempts(execution_id=result["exp_id"] + "--implementation-probe")
        assert [r["terminal_reason"] for r in probes] == ["LOST", "SUCCEEDED"]
        spec = reopened.store.find_fact(author.run_id, "ExperimentPreregistered", result["exp_id"])
        formal = reopened.store.list_attempts(execution_id=spec["execution_id"])
        assert len(formal) == 1 and formal[0]["attempt_ordinal"] == 1
        from hypoloom.oracle import Oracle
        audit = Oracle(reopened.store.database_path, reopened.publisher.root).audit(author.run_id)
        assert audit.ok, audit.to_dict()
    finally: reopened.close()
