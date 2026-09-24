"""Actual CLI file report after scientific CPU closure, before Host completion."""

import json
import secrets
import time
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact, with_output_digest
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude
from test_claude_final_report_cli import _FinalReportScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _BatchScenario, _payload
from test_expert_batch_host import BatchAgent
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


class _OneChainAgent(BatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                and isinstance(request.task_context, ResearchNodeTask)
                and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS):
            return with_output_digest(replace(response, typed_candidate={**response.typed_candidate,
                "hypotheses": response.typed_candidate["hypotheses"][:1]}))
        return response


class _FinalReportHostScenario(_BatchScenario):
    def __init__(self, private_root):
        super().__init__(private_root)
        self.reviser = _OneChainAgent()
        self.reports = {}

    def record(self, document):
        number = super().record(document)
        request = _request_from_provider(document)
        if request.command is AgentCommand.FINAL_REPORT:
            if request.attempt_id not in self.reports:
                report = _FinalReportScenario(request)
                report.tool_prefix = "probe_" + request.attempt_id[-16:] + "__"
                self.reports[request.attempt_id] = report
            report = self.reports[request.attempt_id]
            generated = self.responses[request.attempt_id]
            report.structured_output = {"typed_candidate": generated.typed_candidate,
                "declared_effects": [{"effect": item.effect} for item in generated.declared_effects]}
            report.record(document)
            self.errors.extend(report.errors)
        return number

    def _unscoped_tool_calls(self, number):
        if self.current is not None and self.current.command is AgentCommand.FINAL_REPORT:
            return self.reports[self.current.attempt_id].tool_calls(number)
        return super()._unscoped_tool_calls(number)


def test_real_cli_host_final_report_cites_accepted_cpu_science_and_archives_before_completion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    executable = _installed_claude()
    scenario = _FinalReportHostScenario(tmp_path)
    key = "host-only-final-report-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-final-report-g3"
    observed_open_report = False
    try:
        payload = _payload(tmp_path, run_id)
        settings = payload["research_contract"]["methodology"]["controls"]["product"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["interaction"] = {
            "leader": {"enabled": False, "check_after": "cycle_report", "min_rounds_before_leader": 1},
            "debate": {"enabled": False, "rounds": 3}}
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-native-final-report",
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            state = host.store.latest_transition(run_id)["state"]
            if any(handle.request.command is AgentCommand.FINAL_REPORT for handle in host.running_agents.values()):
                assert state == "ACTIVE" and not host.running
                assert len(host.store.list_facts(run_id, "GoalVerdictRecorded")) == 1
                observed_open_report = True
            if state in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
        assert observed_open_report
        assert scenario.local_tests == set(scenario.execution_results) == {("chain-a", 1)}
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 1
        observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        reports = [row for row in observations if row["request"]["command"] == "FINAL_REPORT"]
        assert len(reports) == len(scenario.reports) == 1
        observation = reports[0]
        report = scenario.reports[observation["request"]["attempt_id"]]
        assert len(report.evidence) == 4 and len(report.requests) == 5
        actual = {row["fact_id"]: row["payload"] for row in host.store.list_facts(run_id)}
        assert {row["fact_id"]: json.loads(row["payload_json"]) for row in report.context["facts"]} == actual
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(outcomes) == len(verdicts) == 1
        assert outcomes[0]["payload"]["outcome"] == "SUCCESS"
        process, = [row for row in attempts if row["kind"] == "PROCESS"]
        detail, = report.context["run_outcome"]["process_attempt_details"]
        assert detail["attempt_id"] == process["attempt_id"]
        assert detail["evidence_scope"] == "accepted_scientific_evidence"
        assert detail["canonical_outcome"]["outcome_id"] == outcomes[0]["payload"]["outcome_id"]
        assert detail["completed_process"] is True and detail["gpu_device_monitored"] is False
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        assert outcomes[0]["payload"]["outcome_id"] in report.report
        assert verdicts[0]["fact_id"] in report.report and "GOAL_MET" in report.report
        files = observation["response"]["proposed_workspace_artifacts"]
        assert len(files) == 1 and WorkspaceArtifact.from_dict(files[0]).content_bytes() == report.report.encode()
        archive = host.publisher.root / observation["session"]["transcript"]["path"]
        assert (archive / "FINAL_REPORT.md").read_bytes() == report.report.encode()
        assert key.encode() not in (archive / "transcript.jsonl").read_bytes()
        assert observation["session"]["transcript_complete"] is True
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 1 and audit.artifact_counts["TRANSCRIPT"] == len(observations)
        assert audit.open_attempt_count == audit.pending_effect_count == 0
        (tmp_path / "final-report-native-evidence.json").write_text(json.dumps(report.evidence, indent=2))
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        host.close(); provider.shutdown(); provider.server_close(); thread.join(timeout=5)
