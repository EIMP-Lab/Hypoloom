"""Native failed Grounding exhausts, survives Host recreation and reports honestly.

The existing local provider emits a structurally valid evaluator declaration but
never writes its source. Real Claude/bwrap and the production capture validator
reject both attempts. No model backend, experiment or scientific result is faked
into the Host; only the model choices and literature transport are fixtures.
"""

from __future__ import annotations

import copy
import json
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from hypoloom.agent_aci import with_output_digest
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _DeclaredScenario, _NoPublicWeb
from test_claude_shared_acceptance_g3 import _DECLARATION, _RUN, _payload
from test_full_cc_host_g3 import _new_host, _start_provider


class _MissingEvaluatorScenario(_DeclaredScenario):
    def record(self, document):
        number = super().record(document)
        request = self.current
        if request is not None and request.command is AgentCommand.GROUNDING:
            # Stop feedback can request another output within the same Attempt.
            # This failure scenario never performs the successful fixture's
            # ground_target/ground_write tools, including on correction turns.
            response = self.responses[request.attempt_id]
            candidate = {**response.typed_candidate, "acceptance": copy.deepcopy(_DECLARATION)}
            self.responses[request.attempt_id] = with_output_digest(
                replace(response, typed_candidate=candidate))
        return number

    def tool_calls(self, number):
        request = self.current
        if request is not None and request.command is AgentCommand.GROUNDING:
            # A declaration alone is not an evaluator. Production capture must
            # reject the absent file, not silently supply an evaluator source.
            return self._structured(request) if not self.errors else ()
        return super().tool_calls(number)


def _agent_observations(host):
    return [(row, json.loads(row["observation_json"]))
            for row in host.store.list_attempts() if row["kind"] == "AGENT"]


def test_native_exhausted_grounding_restarts_and_reports_inconclusive(tmp_path: Path):
    executable = _installed_claude()
    scenario = _MissingEvaluatorScenario(tmp_path, "missing-evaluator-" + secrets.token_hex(12))
    key = "local-grounding-failure-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    base_url = f"http://127.0.0.1:{provider.server_port}"
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, base_url, key)
    host.grounding_web = _NoPublicWeb()
    evidence = {"recovery": "Host.close/reinitialize, not SIGKILL", "paid_model": False,
                "gpu": False, "experiment_processes": 0}
    try:
        with tempfile.TemporaryDirectory(prefix="prom-ground-failure-", dir="/var/tmp") as mount:
            payload = _payload(tmp_path, Path(mount), roles=("never-started",))
            host.initialize()
            created = host.handle(ControlRequest(1, "create-grounding-failure", 0, "CREATE_RUN", payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host._expert_final_failures(_RUN):
                    break
                time.sleep(0.01)
            failures = host._expert_final_failures(_RUN)
            assert len(failures) == 1, host.store.list_attempts()
            grounding = [(row, observation) for row, observation in _agent_observations(host)
                         if observation["request"]["command"] == "GROUNDING"]
            assert len(grounding) == 2, grounding  # Original max_retries=1.
            assert [row["attempt_ordinal"] for row, _ in grounding] == [1, 2]
            assert len({row["command_id"] for row, _ in grounding}) == 1
            assert len({observation["session"]["session_id"] for _, observation in grounding}) == 1
            assert grounding[1][1]["session"]["launch_arguments"] == ["--resume", grounding[0][1]["session"]["session_id"]]
            assert len({observation["session"]["branch"] for _, observation in grounding}) == 1
            assert any(item["ref"] == grounding[0][0]["attempt_id"] + "--agent-retry-diagnostic"
                       for item in grounding[1][1]["request"]["input_artifacts"])
            assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "FAILED"
                       for row, _ in grounding)
            assert all(observation["failure_code"] == "invalid_tool_workspace"
                       and observation["retry_class"] == "MODEL"
                       and "evaluate.py" in observation["diagnostics"]["workspace_error"]
                       and "No such file" in observation["diagnostics"]["workspace_error"]
                       for _, observation in grounding)
            for _, observation in grounding:
                correction = observation["diagnostics"]["structured_output_correction"]
                assert correction["status"] == "unresolved" and correction["accepted"] is False
                assert correction["call_count"] == correction["rejected_count"] == 2
                assert all("missing regular smoke input: parser_files/evaluate.py" in item["validation_error"]
                           for item in correction["rounds"])
            assert not any(row["state"] == "OPEN" for row in host.store.list_attempts())
            assert host.shared_acceptance.source(_RUN) is None
            evidence["failed_grounding"] = [{"attempt": row, "observation": observation}
                                            for row, observation in grounding]
            evidence["final_failure"] = list(failures)
            evidence["head_before_restart"] = dict(host.store.latest_transition(_RUN))
            attempts_before = [row["attempt_id"] for row in host.store.list_attempts()]
            host.close()
            host = _new_host(tmp_path, executable, base_url, key)
            host.grounding_web = _NoPublicWeb()
            host.initialize()
            assert [row["attempt_id"] for row in host.store.list_attempts()] == attempts_before
            assert host._expert_final_failures(_RUN) == failures
            assert host.shared_acceptance.source(_RUN) is None
            # Policy10 returned None here indefinitely: no accepted evaluator,
            # exhausted preparation, no OPEN effect, and no scientific evidence.
            snapshot = host._policy_snapshot(host.store.latest_transition(_RUN))
            decision = host.policy.next_command(snapshot)
            evidence["policy_after_restart"] = None if decision is None else decision.command
            assert decision is not None and decision.command in {
                "RECORD_GOAL_VERDICT", "BEGIN_COMPLETION"}, evidence
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED", host.store.list_attempts()
            verdicts = host.store.list_facts(_RUN, "GoalVerdictRecorded")
            assert len(verdicts) == 1 and verdicts[0]["payload"]["verdict"] == "INCONCLUSIVE"
            for kind in ("HypothesisProposed", "ExperimentPreregistered", "CanonicalOutcomeAccepted", "HypothesisAssessed"):
                assert not host.store.list_facts(_RUN, kind), kind
            assert host.shared_acceptance.source(_RUN) is None
            observations = _agent_observations(host)
            assert [row["attempt_id"] for row, observation in observations
                    if observation["request"]["command"] == "GROUNDING"] == [row["attempt_id"] for row, _ in grounding]
            assert {observation["request"]["command"] for _, observation in observations} == {
                "LITERATURE_REVIEW", "GROUNDING", "FINAL_REPORT"}
            prelude = [observation for _, observation in observations
                       if observation["request"]["command"] == "LITERATURE_REVIEW"]
            assert len(prelude) == 1 and prelude[0]["request"]["task_context"]["request_kind"] == "GROUNDING_QUERY_PLAN"
            final = [(row, observation) for row, observation in observations
                     if observation["request"]["command"] == "FINAL_REPORT"]
            assert len(final) == 1 and final[0][0]["terminal_reason"] == "SUCCEEDED", final
            report = scenario.final_reports[final[0][0]["attempt_id"]]
            assert len(report.requests) == 5 and set(report.evidence) == {
                "report_skill", "report_sources", "report_write", "report_boundaries"}
            assert "INCONCLUSIVE" in report.report and "NO_ACCEPTED_VERDICT" not in report.report
            assert not any(item["ref"].endswith("--shared-acceptance")
                           for item in final[0][1]["request"]["input_artifacts"])
            archive = host.publisher.root / final[0][1]["session"]["transcript"]["path"]
            assert (archive / "FINAL_REPORT.md").read_bytes() == report.report.encode()
            attempts = host.store.list_attempts()
            assert len(attempts) == len(observations) == 4
            assert all(row["kind"] == "AGENT" and row["state"] == "CLOSED" for row in attempts)
            charges = fold_agent_attempts(attempts)
            assert len(charges) == 4 and all(charge.source in {"response", "provider"} for charge in charges)
            assert {charge.attempt_id for charge in charges if charge.source == "provider"} == {
                row["attempt_id"] for row, _ in grounding}
            assert all(charge.tokens > 0 and charge.turns > 0 and charge.cost is not None for charge in charges)
            evidence["charges"] = [{"attempt_id": charge.attempt_id, "source": charge.source,
                "tokens": charge.tokens, "turns": charge.turns, "cost": str(charge.cost)} for charge in charges]
            transcripts = [host.publisher.root / observation["session"]["transcript"]["path"] / "transcript.jsonl"
                           for _, observation in observations]
            assert all(path.is_file() and key not in path.read_text() for path in transcripts)
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert audit.ok, audit.to_dict()
            assert not host.running_agents and not host.running
            assert all(not process_group_exists(row["process_group_id"])
                       for row in attempts if row["process_group_id"])
            evidence.update(verdicts=verdicts, final_report=str(archive / "FINAL_REPORT.md"),
                            final_report_tools=report.evidence, oracle=audit.to_dict(),
                            transcripts=[str(path) for path in transcripts],
                            attempts=attempts, head=dict(host.store.latest_transition(_RUN)))
    finally:
        (tmp_path / "grounding-failure-evidence.json").write_text(json.dumps(evidence, indent=2))
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
