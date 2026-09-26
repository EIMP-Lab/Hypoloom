"""Native analysis corrects an inadmissible ITERATE through the existing retry.

The local scripted provider changes the parser during execution after a valid
DESIGN sample, submits a wrong frozen local-parser envelope, then
requests scientific iteration on its operational failure. Real Claude/bwrap,
Host, CPU collection, retry/session persistence and Oracle own the boundaries.
The common parser independently qualifies the goal; local failure stays failed.
"""

from __future__ import annotations

import copy
import json
import secrets
import tempfile
import time
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import _output_schema, process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude, _tool_results, native_output_schema
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceScenario, _DECLARATION, _GOAL, _RUN, _common_source,
    _experiment_files, _local_measurement, _payload,
)
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider
from test_real_analysis_probe import _input
from test_real_current_host_g5 import _native_tools
from test_real_design_recovery_probe import _write_json


_ANALYSIS = AgentCommand.RESULT_ANALYSIS
_ERROR = "invalid_iteration_candidate"


class _AnalysisCorrectionScenario(_AcceptanceScenario):
    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.rejected_analysis = None
        self.analysis_feedback = {}

    def record(self, document):
        request = _request_from_provider(document)
        if request.command is AgentCommand.EXPERIMENT_EXECUTION and request.attempt_id not in self.agent_requests:
            # One native Write precedes the parent's submit/wait sequence.
            self.steps[request.attempt_id] = -1
        observed = copy.deepcopy(document)
        prefix = request.attempt_id + "__"
        for message in observed.get("messages", []):
            content = message.get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result" and block.get("tool_use_id", "").startswith(prefix):
                    block["tool_use_id"] = block["tool_use_id"][len(prefix):]
        number = super().record(observed)
        try:
            if request.command is AgentCommand.EXPERIMENT_EXECUTION and self.steps[request.attempt_id] == 1:
                assert not _tool_results(observed)["wrong_local_envelope"].get("is_error")
            if request.command is _ANALYSIS:
                assert native_output_schema(document) == _output_schema(request, tool_workspace=True)
                outcome = json.loads(next(item.content_bytes() for item in request.input_artifacts
                    if item.ref == request.command_id + "--current-outcome"))
                assert outcome["payload"]["outcome"] == "EXECUTION_FAILURE"
                assert outcome["payload"]["validation_code"] == "invalid_parsed_measurement"
                if self.rejected_analysis is None:
                    self.rejected_analysis = request.attempt_id
                elif request.attempt_id != self.rejected_analysis:
                    correction = next(item for item in request.input_artifacts
                        if item.ref == self.rejected_analysis + "--agent-retry-diagnostic")
                    feedback = json.loads(correction.content_bytes())
                    assert feedback["source_attempt_id"] == self.rejected_analysis
                    assert feedback["failure_code"] == _ERROR and feedback["retry_class"] == "MODEL"
                    assert "ITERATE" in feedback["diagnostic_summary"]
                    assert "operational" in feedback["diagnostic_summary"]
                    self.analysis_feedback[request.attempt_id] = feedback
        except Exception as exc:
            self.errors.append(f"analysis correction provider: {type(exc).__name__}: {exc}")
        self.requests[-1] = document
        return number

    def tool_calls(self, number):
        request = self.current
        if request is None or self.errors:
            return ()
        if request.command is AgentCommand.EXPERIMENT_EXECUTION and self.steps[request.attempt_id] == 0:
            # DESIGN now rejects this envelope before preregistration. Inject
            # it in EXECUTION to reach the analysis retry boundary under test.
            calls = (("wrong_local_envelope", "Write", {"file_path": "/workspace/parse_local.py",
                "content": _common_source(self.nonce)}),)
        else:
            calls = super().tool_calls(number)
        if request.command is _ANALYSIS:
            calls = copy.deepcopy(calls)
            for _, tool, arguments in calls:
                if tool != "StructuredOutput":
                    continue
                candidate = arguments["typed_candidate"]
                rejected = request.attempt_id == self.rejected_analysis
                candidate["iteration_disposition"] = "ITERATE" if rejected else "COMPLETE"
                candidate["summary"] = (
                    "Repair the operational local-parser failure through ITERATE."
                    if rejected else
                    "The local parser returned the wrong envelope: retain EXECUTION_FAILURE and "
                    "INCONCLUSIVE locally. COMPLETE this analysis without a scientific iteration. "
                    "The unchanged independent common evaluator passed both suites at 0.75.")
                if not rejected:
                    assert request.attempt_id in self.analysis_feedback
        return tuple((request.attempt_id + "__" + identity, tool, arguments)
            for identity, tool, arguments in calls)


def test_native_analysis_candidate_retries_same_session_and_preserves_operational_failure(tmp_path):
    nonce = "analysis-correction-" + secrets.token_hex(12)
    scenario = _AnalysisCorrectionScenario(tmp_path, nonce)
    key = "local-analysis-correction-only-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    evidence = {"paid_model": False, "gpu": False, "run_id": _RUN}
    first_outcomes = first_failed_analysis = None
    try:
        mount = Path(tempfile.mkdtemp(prefix="prom-analysis-correction-", dir="/var/tmp"))
        evidence["fixture_root"] = str(mount)
        payload = _payload(tmp_path, mount, roles=(_GOAL,))
        original = copy.deepcopy(payload)
        host.initialize()
        created = host.handle(ControlRequest(1, "create-analysis-correction", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            host.tick()
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            if outcomes and first_outcomes is None:
                first_outcomes = copy.deepcopy(outcomes)
            if scenario.rejected_analysis is not None and first_failed_analysis is None:
                row = host.store.get_attempt(scenario.rejected_analysis)
                if row is not None and row["state"] == "CLOSED":
                    first_failed_analysis = dict(row)
            if scenario.errors or host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.01)
        assert payload == original
        attempts = host.store.list_attempts()
        agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
        analyses = [(row, observed) for row, observed in agents if observed["request"]["command"] == _ANALYSIS.value]
        specs = host.store.list_facts(_RUN, "ExperimentPreregistered")
        outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
        assessments = host.store.list_facts(_RUN, "HypothesisAssessed")
        verdicts = host.store.list_facts(_RUN, "GoalVerdictRecorded")
        audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
        evidence.update(head=dict(host.store.latest_transition(_RUN)), attempts=[dict(row) for row in attempts],
            fixture_errors=scenario.errors, analysis_feedback=scenario.analysis_feedback,
            preregistrations=specs, outcomes=outcomes, assessments=assessments, verdicts=verdicts,
            oracle=audit.to_dict())
        assert not scenario.errors, scenario.errors
        assert len(analyses) == 2, "operational ITERATE rejection must retry the same analysis command"
        failed, retry = analyses
        assert failed[0] == first_failed_analysis, "the rejected Attempt remains unchanged after correction"
        assert [row["terminal_reason"] for row, _ in analyses] == ["FAILED", "SUCCEEDED"]
        assert [row["attempt_ordinal"] for row, _ in analyses] == [1, 2]
        assert failed[0]["command_id"] == retry[0]["command_id"]
        assert failed[1]["failure_code"] == _ERROR and failed[1]["retry_class"] == "MODEL"
        assert failed[1]["diagnostics"]["validation_error"]["code"] == _ERROR
        assert failed[1]["diagnostics"]["provider_subtype"] == "success"
        assert failed[1]["returncode"] == 0 and failed[1].get("response") is None
        assert retry[1]["response"]["typed_candidate"]["iteration_disposition"] == "COMPLETE"
        inner = [observed for _, observed in agents if observed["request"]["command"] in {
            "EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}]
        sessions = {observed["session"]["session_id"] for observed in inner}
        assert len(sessions) == len(inner)
        assert len({observed["session"]["branch"] for observed in inner}) == 1
        for observed in inner:
            if observed["request"]["command"] != "EXPERIMENT_DESIGN_IMPLEMENT":
                assert observed["session"]["launch_arguments"] == ["--session-id", observed["session"]["session_id"]]
        first_request, retry_request = failed[1]["request"], retry[1]["request"]
        assert first_request["task_context"] == retry_request["task_context"]
        assert first_request["immutable_context_refs"] == retry_request["immutable_context_refs"]
        assert [item for item in retry_request["input_artifacts"]
            if item["ref"] in retry_request["immutable_context_refs"]] == first_request["input_artifacts"]
        correction_ref = failed[0]["attempt_id"] + "--agent-retry-diagnostic"
        assert json.loads(_input(retry_request, correction_ref, immutable=False)) == scenario.analysis_feedback[retry[0]["attempt_id"]]
        assert correction_ref not in retry_request["immutable_context_refs"]
        assert len(retry_request["input_artifacts"]) == len(first_request["input_artifacts"]) + 1
        assert outcomes == first_outcomes and len(specs) == len(outcomes) == len(assessments) == len(verdicts) == 1
        spec, outcome = specs[0], outcomes[0]
        assert spec["payload"]["measurement"] == _local_measurement().to_dict()
        assert outcome["payload"]["outcome"] == "EXECUTION_FAILURE" and outcome["payload"]["metric_value"] is None
        assert outcome["payload"]["validation_code"] == "invalid_parsed_measurement"
        assert assessments[0]["payload"]["assessment"] == "INCONCLUSIVE"
        assert outcome["payload"]["goal_evaluation"] == {"source_attempt_id": scenario.source_attempt_id,
            "measurements": {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"},
            "checks": {"complete": True, "single_candidate": True}}
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        processes = [row for row in attempts if row["kind"] == "PROCESS"]
        assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
        process = json.loads(processes[0]["observation_json"])
        assert process["returncode"] == 0 and not process.get("synthetic_before_spawn")
        for _, observed in analyses:
            request = observed["request"]
            prefix = request["command_id"] + "--current-"
            assert json.loads(_input(request, prefix + "spec")) == spec
            assert json.loads(_input(request, prefix + "outcome")) == outcome
            assert json.loads(_input(request, prefix + "process")) == process
            assert _input(request, prefix + "parse_local.py") == _common_source(nonce).encode()
            assert _input(request, prefix + "experiment.py") == _experiment_files(_GOAL)["experiment.py"]
        assert spec["payload"]["goal_evaluation"] == {
            "scope": "GOAL", "source_attempt_id": scenario.source_attempt_id, "acceptance": _DECLARATION}
        ground = next(observed for _, observed in agents if observed["request"]["command"] == "GROUNDING")
        common_path = host.publisher.root / ground["session"]["transcript"]["path"] / "evaluator/evaluate.py"
        assert common_path.read_text() == _common_source(nonce)
        submit = host.store.inspect("SELECT request_json FROM workflow_commands WHERE command_id=?",
            (spec["payload"]["execution_contract"]["source_submit_command_id"],))[0]
        captured = json.loads(submit["request_json"])["source_snapshot"]["arguments"]["workspace_files"]
        assert next(WorkspaceArtifact.from_dict(item).content_bytes() for item in captured
            if item["path"] == "parse_local.py") == _common_source(nonce).encode()
        tools = []
        for row, observed in agents:
            assert observed["session"]["transcript_complete"] is True
            archive = host.publisher.root / observed["session"]["transcript"]["path"]
            content = (archive / "transcript.jsonl").read_bytes()
            assert content and key.encode() not in content
            uses, _ = _native_tools(content)
            tools.extend(uses)
            if row["attempt_id"] == failed[0]["attempt_id"]:
                assert not any(tool["name"] == "StructuredOutput" for tool in uses)
                source = tmp_path / "state/diagnostics/agents" / _RUN / row["attempt_id"] / "stdout.log"
                records = [json.loads(line) for line in source.read_text().splitlines()]
                result = next(item for item in reversed(records) if item.get("type") == "result")
                candidate = json.loads(result["result"])
                assert candidate["typed_candidate"]["iteration_disposition"] == "ITERATE"
                archived = [json.loads(line) for line in content.splitlines()]
                assert any(block.get("type") == "text" and block.get("text") == result["result"]
                    for item in archived if item.get("message", {}).get("role") == "assistant"
                    for block in item["message"].get("content", []))
                trace = observed["diagnostics"]["structured_output_correction"]
                assert trace["scope"] == "native_stop_validation" and trace["status"] == "first_pass"
                assert trace["accepted"] is True and trace["rejected_count"] == 0
            if row["attempt_id"] == retry[0]["attempt_id"]:
                assert correction_ref.encode() in content and _ERROR.encode() in content
        assert {"mcp__host-tools__submit_experiment", "mcp__host-tools__wait_experiment"} <= {tool["name"] for tool in tools}
        assert any(tool["name"] == "Skill" and tool["input"]["skill"].endswith(":final-report") for tool in tools)
        assert len(agents) == 12 and len(attempts) == 13
        assert sum(observed["request"]["command"] == "FINAL_REPORT" and row["terminal_reason"] == "SUCCEEDED"
            for row, observed in agents) == 1
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert all(row["terminal_reason"] == "SUCCEEDED" for row in attempts if row["attempt_id"] != failed[0]["attempt_id"])
        charges = fold_agent_attempts(sorted(attempts, key=lambda row: (row["started_at"], row["attempt_id"])))
        assert all(charge.source != "declared" for charge in charges), "scripted complete usage remains accounted on rejected output"
        assert audit.ok, audit.to_dict()
        assert audit.artifact_counts["TRANSCRIPT"] == len(agents)
        assert audit.open_attempt_count == audit.pending_effect_count == audit.duplicate_effect_count == 0
        assert not audit.owned_process_residue and not host.running and not host.running_agents
        assert evidence["head"]["state"] == "COMPLETED"
    finally:
        groups = [] if not host.store.database_path.exists() else [row["process_group_id"]
            for row in host.store.list_attempts() if row["process_group_id"]]
        host.close()
        evidence["owned_process_residue_after_close"] = [group for group in groups if process_group_exists(group)]
        _write_json(tmp_path / "analysis-candidate-recovery.json", evidence)
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    assert not evidence["owned_process_residue_after_close"]
