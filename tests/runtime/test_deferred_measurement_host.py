import json
import shutil
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path
import pytest

from hypoloom.agent_aci import (AgentACIError, AgentResponse, DeclaredEffect, WorkspaceArtifact,
                                       request_digest, required_effect, required_output_schema, with_output_digest)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.oracle import Oracle
from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_loop_host import _pilot_payload
from tests.runtime.test_measurement_contract import declaration
from tests.runtime.test_host_experiment_tools import _until, _terminal


def _candidate_bodies(request, candidate):
    return ([item["candidate"] for item in candidate["hypotheses"]]
        if request.required_output_schema == "HypothesisSet/v1" else [candidate])


class DeclaredMeasurementAgent(FakeAgent):
    def invoke(self, request):
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            response = super().invoke(request)
            if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                    and isinstance(request.task_context, ResearchNodeTask)
                    and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS):
                candidate = json.loads(json.dumps(response.typed_candidate))
                for body in _candidate_bodies(request, candidate):
                    body["measurement"] = declaration().to_dict()
                return with_output_digest(replace(response, typed_candidate=candidate))
            return response
        response = super().invoke(default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT))
        assert isinstance(response, AgentResponse)
        candidate = {"component_id": request.component_id, "owner_role": request.role,
                     "method_family_id": request.method_family_id, "method": "Shell timings and custom CSV median",
                     "metrics": [declaration().to_dict()["metric"]], "measurement": declaration().to_dict(),
                     "execution_argv": ["/bin/sh", "/input/experiment.sh"],
                     "acceptance_criteria": ["relative median latency delta <= -0.10"],
                     "evaluator_version": OUTCOME_ADAPTER_VERSION,
                     "artifact_requirements": ["result.json"], "resource_preference": "CPU"}
        files = {
            "experiment.sh": b"printf '10,8\n10,9\n10,100\n' > timings.csv\necho raw-experiment-complete\n",
            "implementation.patch": b"",
            "parse_metrics.py": b'''import csv, json, statistics
from pathlib import Path
rows = list(csv.reader(Path('/raw/timings.csv').open()))
Path('/parsed/result.json').write_text(json.dumps({'metric':'median_latency_delta',
    'value': str(statistics.median(float(row[1]) for row in rows)),
    'baseline': str(statistics.median(float(row[0]) for row in rows))}))
''',
        }
        return with_output_digest(replace(response, schema_version=request.schema_version,
            command_id=request.command_id, attempt_id=request.attempt_id, attempt_ordinal=request.attempt_ordinal,
            typed_candidate=candidate, proposed_workspace_artifacts=tuple(
                WorkspaceArtifact.from_bytes(path, content) for path, content in files.items()),
            declared_effects=(DeclaredEffect(effect=required_effect(request.command), command_id=request.command_id,
                                            attempt_id=request.attempt_id),), input_digest=request_digest(request)))


class DecimalSpellingAgent(DeclaredMeasurementAgent):
    def invoke(self, request):
        response = super().invoke(request)
        candidate = json.loads(json.dumps(response.typed_candidate))
        bodies = [body for body in _candidate_bodies(request, candidate) if "measurement" in body]
        if bodies:
            for body in bodies:
                body["measurement"]["metric"]["target"] = "-0.10"
                if "metrics" in body: body["metrics"][0]["target"] = "-0.100"
            response = with_output_digest(replace(response, typed_candidate=candidate))
        return response


@pytest.mark.parametrize("schema_version", [13, 14])
@pytest.mark.parametrize("agent_type", [DeclaredMeasurementAgent, DecimalSpellingAgent])
def test_measurement_fixture_keeps_exact_legacy_or_batch_candidate_shape(schema_version, agent_type, monkeypatch):
    base = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    task = replace(base.task_context, key=replace(base.task_context.key, subkind=ResearchNodeSubkind.SYNTHESIS))
    request = replace(base, schema_version=schema_version, task_context=task, command_id=task.command_id,
        required_output_schema=required_output_schema(base.command, task, schema_version=schema_version))
    invoke = FakeAgent.invoke

    def two_members(self, actual):
        response = invoke(self, actual)
        if actual.required_output_schema == "HypothesisSet/v1":
            candidate = json.loads(json.dumps(response.typed_candidate))
            member = candidate["hypotheses"][0]
            candidate["hypotheses"] = [{**member, "chain_id": chain} for chain in ("a", "b")]
            return with_output_digest(replace(response, typed_candidate=candidate))
        return response

    monkeypatch.setattr(FakeAgent, "invoke", two_members)
    response = agent_type().invoke(request)
    raw = response.typed_candidate
    if schema_version == 14:
        assert set(raw) == {"schema_version", "hypotheses"}
        assert [item["chain_id"] for item in raw["hypotheses"]] == ["a", "b"]
        candidates = [item["candidate"] for item in raw["hypotheses"]]
    else:
        assert "hypotheses" not in raw and "schema_version" not in raw
        candidates = [raw]
    expected = declaration().to_dict()
    if agent_type is DecimalSpellingAgent:
        expected["metric"]["target"] = "-0.10"
    assert all(candidate["measurement"] == expected for candidate in candidates)


def deferred_payload(tmp_path: Path, target: Path):
    payload = _pilot_payload(tmp_path, target, "c5-host")
    contract = payload["research_contract"]
    contract["schema_version"] = 2
    component = contract["components"][0]
    component["result"] = {"kind": "metric", "deferred": True}
    component["seeds"] = []
    component["protocol"] = None
    component["required_artifacts"] = []
    contract["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE",
                                             "target": "1", "display_only": True}
    return payload


@pytest.fixture
def deferred_host(tmp_path: Path, request):
    with tempfile.TemporaryDirectory(prefix="hypoloom-c5-target-", dir="/var/tmp") as directory:
        agent = DecimalSpellingAgent() if getattr(request, "param", None) == "decimal-spelling" else DeclaredMeasurementAgent()
        host = WorkflowHost(tmp_path / "state", local_agent=agent)
        host.initialize()
        try:
            payload = deferred_payload(tmp_path, Path(directory))
            if getattr(request, "param", None) == "campaign":
                contract = payload["research_contract"]
                contract["roles"].append({**contract["roles"][0], "id": "reviewer"})
                contract["methodology"]["controls"]["experiment_campaigns"] = {
                    "enabled": True, "required_roles": ["preflight", "analyze"],
                    "role_owners": {"*": {"preflight": "reviewer", "analyze": "reviewer"}}}
            response = host.handle(ControlRequest(schema_version=1, request_id="create-c5", expected_revision=0,
                                                  command="CREATE_RUN", payload=payload))
            assert response.status == "accepted", response
            yield host
        finally:
            host.close()


@pytest.mark.parametrize("deferred_host", [None, "decimal-spelling"], indirect=True)
def test_null_profile_metric_custom_shell_and_parser_reach_real_outcome_and_oracle(deferred_host):
    host = deferred_host
    _until(host, lambda: host.store.latest_transition("c5-host")["state"] == "COMPLETED")
    outcomes = host.store.list_facts("c5-host", "CanonicalOutcomeAccepted")
    assert len(outcomes) == 1
    assert outcomes[0]["payload"]["outcome"] == "SUCCESS"
    assert outcomes[0]["payload"]["metric_value"] == "-0.1"
    processes = [row for row in host.store.list_attempts() if row["run_id"] == "c5-host" and row["kind"] == "PROCESS"]
    assert len(processes) == 1
    assert processes[0]["terminal_reason"] == "SUCCEEDED"
    report = next(json.loads(row["observation_json"])["request"] for row in host.store.list_attempts()
        if row["kind"] == "AGENT" and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT")
    from hypoloom.agent_aci import AgentRequest
    report = AgentRequest.from_dict(report)
    raw = [item for item in report.input_artifacts if "--declared-raw-measurement-" in item.ref]
    assert len(raw) == 1 and raw[0].content_bytes() == b"10,8\n10,9\n10,100\n"
    context = json.loads(next(item.content_bytes() for item in report.input_artifacts
        if item.ref.endswith("--final-report-context")))
    included = context["declared_raw_measurements"]["included"]
    assert included[0]["artifact_id"] == outcomes[0]["payload"]["artifact_id"]
    assert included[0]["outcome_id"] == outcomes[0]["payload"]["outcome_id"]
    assert included[0]["path"] == "raw/timings.csv"
    audit = Oracle(host.store.database_path, host.publisher.root).audit("c5-host")
    assert audit.ok, audit.to_dict()


def _manual_submission(host):
    _until(host, lambda: bool(host.local_execution_agents))
    request = next(iter(host.local_execution_agents.values()))
    host.local_execution_agents.clear()
    source = next(json.loads(row["observation_json"])["response"] for row in host.store.list_attempts()
                  if row["kind"] == "AGENT" and json.loads(row["observation_json"])["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT")
    return request, {"candidate": source["typed_candidate"], "workspace_files": source["proposed_workspace_artifacts"]}


def test_quarantine_rejects_new_submit_preserves_replay_and_reports_blocked_wait(deferred_host):
    host = deferred_host
    request, arguments = _manual_submission(host)
    error = {"exception_type": "AgentACIError", "phase": "dispatch", "host_generation": "test-injection"}
    host.dispatch_errors[request.run_id] = error
    before = host.store.list_workflow_heads(request.run_id, "EXPERIMENT")
    with pytest.raises(AgentACIError, match="dispatch_paused"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    assert host.store.list_workflow_heads(request.run_id, "EXPERIMENT") == before
    host.dispatch_errors.clear()  # Test injection ends; this is not a runtime resume API.
    accepted = host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments)
    host.dispatch_errors[request.run_id] = error
    assert host.handle_agent_tool(request.attempt_id, "submit_experiment", arguments) == accepted
    view = host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"]})
    assert view["status"] == "blocked" and view["terminal"] is False
    assert view["state"] == "DRAFTED" and view["dispatch_error"] == error
    assert view["outcome"] is None
    host.dispatch_errors.clear()
    _until(host, lambda: _terminal(host, accepted["exp_id"]))
    assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": accepted["exp_id"]})["terminal"] is True
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition(request.run_id)["state"] == "COMPLETED")
    assert Oracle(host.store.database_path, host.publisher.root).audit(request.run_id).ok


def test_same_hypothesis_parser_bytes_freeze_but_experiment_repairs_use_same_execution(deferred_host):
    host = deferred_host
    request, arguments = _manual_submission(host)
    failed = {**arguments, "workspace_files": [WorkspaceArtifact.from_bytes(item["path"], b"exit 7\n"
        if item["path"] == "experiment.sh" else WorkspaceArtifact.from_dict(item).content_bytes()).to_dict()
        for item in arguments["workspace_files"]]}
    first = host.handle_agent_tool(request.attempt_id, "submit_experiment", failed)
    _until(host, lambda: _terminal(host, first["exp_id"]) and bool(host.store.list_facts("c5-host", "EvidenceLinked")))
    assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": first["exp_id"]})["outcome"]["outcome"] == "EXECUTION_FAILURE"
    changed_parser = {**arguments, "replaces_experiment_id": first["exp_id"], "workspace_files": [
        WorkspaceArtifact.from_bytes(item["path"], WorkspaceArtifact.from_dict(item).content_bytes() +
            (b"\n# changed parser bytes\n" if item["path"] == "parse_metrics.py" else b"")).to_dict() for item in arguments["workspace_files"]]}
    with pytest.raises(AgentACIError, match="measurement_frozen"):
        host.handle_agent_tool(request.attempt_id, "submit_experiment", changed_parser)
    assert len(host.store.list_workflow_heads("c5-host", "EXPERIMENT")) == 1
    second = host.handle_agent_tool(request.attempt_id, "submit_experiment", {**arguments, "replaces_experiment_id": first["exp_id"]})
    _until(host, lambda: _terminal(host, second["exp_id"]))
    assert host.handle_agent_tool(request.attempt_id, "wait_experiment", {"exp_id": second["exp_id"]})["outcome"]["outcome"] == "SUCCESS"
    host._finish_fake_agent(request, FakeAgent().invoke(request))
    _until(host, lambda: host.store.latest_transition("c5-host")["state"] == "COMPLETED")
    audit = Oracle(host.store.database_path, host.publisher.root).audit("c5-host")
    assert audit.ok, audit.to_dict()


@pytest.mark.parametrize("deferred_host", ["campaign"], indirect=True)
def test_generic_sources_reach_required_preflight_and_analysis_reviews(deferred_host):
    host = deferred_host
    _until(host, lambda: host.store.latest_transition("c5-host")["state"] == "COMPLETED")
    reviews = [json.loads(row["observation_json"]) for row in host.store.list_attempts()
               if row["kind"] == "AGENT" and json.loads(row["observation_json"])["request"]["command"] == "ANSWER_RENDEZVOUS"]
    assert len(reviews) == 2
    for review in reviews:
        refs = [item["ref"] for item in review["request"]["input_artifacts"]]
        assert any(ref.endswith("--experiment.sh") for ref in refs)
        assert any(ref.endswith("--parse_metrics.py") for ref in refs)
        assert not any(ref.endswith("--experiment.py") for ref in refs)
    report = Oracle(host.store.database_path, host.publisher.root).audit("c5-host")
    assert report.ok, report.to_dict()


@pytest.mark.parametrize("tamper", ["parsed", "raw", "parser", "comparison"])
def test_oracle_rederives_after_existing_digest_is_recomputed(deferred_host, tmp_path, tamper):
    from hypoloom.oracle import _raw_artifact_digest
    host = deferred_host
    _until(host, lambda: host.store.latest_transition("c5-host")["state"] == "COMPLETED")
    copied = tmp_path / (tamper + ".sqlite")
    with sqlite3.connect(host.store.database_path) as source, sqlite3.connect(copied) as destination:
        source.backup(destination)
    artifacts = tmp_path / (tamper + "-artifacts")
    shutil.copytree(host.publisher.root, artifacts)
    with sqlite3.connect(copied) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        row = db.execute("SELECT artifact_id,path FROM execution_artifacts WHERE artifact_kind='result'").fetchone()
        root = artifacts / row[1]
        if tamper == "comparison":
            db.execute("UPDATE research_facts SET payload_json=json_set(payload_json,'$.metric_value','-0.2') WHERE fact_type='CanonicalOutcomeAccepted'")
        else:
            target, content = {"parsed": ("result.json", b'{"metric":"median_latency_delta","value":"8","baseline":"10"}'),
                "raw": ("raw/timings.csv", b"10,1\n10,2\n10,3\n"),
                "parser": ("parser/parse_metrics.py", b"raise RuntimeError('changed parser')\n")}[tamper]
            (root / target).write_bytes(content)
            digest = _raw_artifact_digest(root)
            db.execute("UPDATE execution_artifacts SET digest=? WHERE artifact_id=?", (digest, row[0]))
            db.execute("UPDATE research_facts SET payload_json=json_set(payload_json,'$.artifact_set_digest',?) WHERE fact_type='CanonicalOutcomeAccepted'", (digest,))
        db.commit()
    audit = Oracle(copied, artifacts).audit("c5-host")
    codes = {issue.code for issue in audit.issues}
    assert ("measurement_comparison_mismatch" if tamper == "comparison" else "measurement_result_replay_mismatch") in codes, audit.to_dict()
    assert not any("digest" in code or "hash" in code for code in codes), audit.to_dict()
