"""Actual CC retries a rejected final report after shared-goal CPU closure.

Only model choices and literature use existing local fixtures. Real tools,
capture, immutable inputs, Host retry and Oracle remain production behavior.
"""

from __future__ import annotations

import copy
import json
import secrets
import tempfile
import time
from pathlib import Path

from hypoloom.agent_aci import WorkspaceArtifact
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceScenario, _DECLARATION, _GOAL, _RUN, _common_source, _payload,
)
from test_full_cc_host_g3 import _new_host, _start_provider


_INVALID_REF = "not-an-accepted-final-report-reference"


class _FinalRetryScenario(_AcceptanceScenario):
    def __init__(self, root, nonce):
        super().__init__(root, nonce)
        self.rejected_final = None

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        request = self.current
        if request is None or request.command is not AgentCommand.FINAL_REPORT:
            return calls
        if any(tool == "StructuredOutput" for _, tool, _ in calls):
            if self.rejected_final is None:
                self.rejected_final = request.attempt_id
            if request.attempt_id == self.rejected_final:
                calls = copy.deepcopy(calls)
                for _, tool, arguments in calls:
                    if tool == "StructuredOutput":
                        arguments["typed_candidate"]["supporting_refs"] = [_INVALID_REF]
        return calls


def test_native_shared_final_report_retries_with_one_common_input_and_same_session(tmp_path: Path):
    scenario = _FinalRetryScenario(tmp_path, "shared-final-retry-" + secrets.token_hex(12))
    key = "local-final-retry-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, _installed_claude(), f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    evidence = {"paid_model": False, "gpu": False}
    try:
        with tempfile.TemporaryDirectory(prefix="prom-shared-final-", dir="/var/tmp") as mount:
            payload = _payload(tmp_path, Path(mount), roles=(_GOAL,))
            host.initialize()
            created = host.handle(ControlRequest(1, "create-shared-final-retry", 0, "CREATE_RUN", payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED", host.store.list_attempts()
            attempts = host.store.list_attempts()
            agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
            finals = [(row, observed) for row, observed in agents if observed["request"]["command"] == "FINAL_REPORT"]
            assert len(finals) == 2
            failed, retry = finals
            assert failed[0]["attempt_id"] == scenario.rejected_final
            assert [row["terminal_reason"] for row, _ in finals] == ["FAILED", "SUCCEEDED"]
            assert [row["attempt_ordinal"] for row, _ in finals] == [1, 2]
            assert failed[0]["command_id"] == retry[0]["command_id"]
            assert failed[1]["failure_code"] == "invalid_structured_output" and failed[1]["retry_class"] == "MODEL"
            assert "/typed_candidate/supporting_refs/0" in failed[1]["diagnostics"]["provider_diagnostic"]
            assert failed[1].get("response") is None
            correction = failed[1]["diagnostics"]["structured_output_correction"]
            assert correction["status"] == "unresolved" and correction["accepted"] is False
            assert correction["call_count"] == correction["rejected_count"] == 2
            assert all("/typed_candidate/supporting_refs/0" in item["validation_error"]
                       for item in correction["rounds"])
            accepted = retry[1]["diagnostics"]["structured_output_correction"]
            assert accepted["status"] == "first_pass" and accepted["accepted"] is True
            assert accepted["call_count"] == 1 and accepted["rejected_count"] == 0
            session_id = failed[1]["session"]["session_id"]
            assert retry[1]["session"]["session_id"] == session_id
            assert retry[1]["session"]["launch_arguments"] == ["--resume", session_id]
            assert retry[1]["session"]["branch"] == failed[1]["session"]["branch"]
            for name in ("command_id", "task_context", "required_model_identity", "required_adapter_version", "tool_permission_profile"):
                assert failed[1]["request"][name] == retry[1]["request"][name]
            context = failed[1]["diagnostics"]["context"]
            assert context["current_request_fits"] is True
            assert context["last_observed_exchange"] == context["current_request_exchange"]
            assert failed[1]["session"]["transcript_complete"] is True
            assert retry[1]["diagnostics"]["provider_session_carryover"]["source"] == "session_cost_state"
            common = []
            for _, observed in finals:
                request = observed["request"]
                refs = request["immutable_context_refs"]
                inputs = request["input_artifacts"]
                assert len(refs) == len(set(refs))
                assert len(inputs) == len({item["ref"] for item in inputs})
                shared = [item for item in inputs if item["ref"].endswith("--shared-acceptance")]
                assert len(shared) == 1 and refs.count(shared[0]["ref"]) == 1
                common.append(shared[0])
            assert common[0] == common[1]
            assert failed[1]["request"]["immutable_context_refs"] == retry[1]["request"]["immutable_context_refs"]
            diagnostic_ref = failed[0]["attempt_id"] + "--agent-retry-diagnostic"
            corrections = [item for item in retry[1]["request"]["input_artifacts"] if item["ref"] == diagnostic_ref]
            assert len(corrections) == 1 and diagnostic_ref not in retry[1]["request"]["immutable_context_refs"]
            assert retry[1]["response"]["typed_candidate"]["supporting_refs"]
            assert _INVALID_REF not in retry[1]["response"]["typed_candidate"]["supporting_refs"]
            for (row, observed), expected_calls in zip(finals, (6, 5), strict=True):
                report = scenario.final_reports[row["attempt_id"]]
                assert len(report.requests) == expected_calls and set(report.evidence) == {
                    "report_skill", "report_sources", "report_write", "report_boundaries"}
                archive = host.publisher.root / observed["session"]["transcript"]["path"]
                assert (archive / "FINAL_REPORT.md").read_text() == report.report
                assert key not in (archive / "transcript.jsonl").read_text()
            files = retry[1]["response"]["proposed_workspace_artifacts"]
            assert len(files) == 1 and files[0]["path"] == "FINAL_REPORT.md"
            assert WorkspaceArtifact.from_dict(files[0]).content_bytes() == scenario.final_reports[retry[0]["attempt_id"]].report.encode()
            specs = host.store.list_facts(_RUN, "ExperimentPreregistered")
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            verdicts = host.store.list_facts(_RUN, "GoalVerdictRecorded")
            assert len(specs) == len(outcomes) == len(verdicts) == 1
            assert specs[0]["payload"]["goal_evaluation"] == {
                "scope": "GOAL", "source_attempt_id": scenario.source_attempt_id, "acceptance": _DECLARATION}
            assert outcomes[0]["payload"]["outcome"] == "CLEAN_REFUTATION"
            assert outcomes[0]["payload"]["goal_evaluation"] == {
                "source_attempt_id": scenario.source_attempt_id,
                "measurements": {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"},
                "checks": {"complete": True, "single_candidate": True}}
            assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
            assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 1
            assert all(row["state"] == "CLOSED" for row in attempts)
            assert len([row for row, observed in agents
                        if observed["request"]["command"] == "FINAL_REPORT" and row["terminal_reason"] == "SUCCEEDED"]) == 1
            ground = next(observed for _, observed in agents if observed["request"]["command"] == "GROUNDING")
            source = host.publisher.root / ground["session"]["transcript"]["path"] / "evaluator/evaluate.py"
            assert source.read_text() == _common_source(scenario.nonce)
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert audit.ok, audit.to_dict()
            assert audit.open_attempt_count == audit.pending_effect_count == 0
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])
            evidence.update(agent_count=len(agents), process_count=1, session_id=session_id,
                final_attempts=[row["attempt_id"] for row, _ in finals],
                shared_input_ref=common[0]["ref"], correction_ref=diagnostic_ref,
                outcomes=outcomes, verdicts=verdicts, oracle=audit.to_dict())
    finally:
        evidence["head"] = dict(host.store.latest_transition(_RUN))
        (tmp_path / "shared-final-retry-evidence.json").write_text(json.dumps(evidence, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
