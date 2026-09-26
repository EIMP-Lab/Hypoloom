"""Native process overlap, isolation, recovery and CPU publication through Host."""
import json
import secrets
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.claude_agent_host import AgentHostTerminalReason, process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.oracle import Oracle
from test_claude_file_tool_cli import _installed_claude
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _payload
from test_full_cc_host_g3 import _new_host, _start_provider


def _wing(request):
    return (request.task_context.key.subkind.value if request.command is AgentCommand.HYPOTHESIS_PROPOSAL
        and isinstance(request.task_context, ResearchNodeTask) else None)


class ParallelScenario(_FinalReportHostScenario):
    def __init__(self, root):
        super().__init__(root)
        self.release = threading.Event()

    def record(self, document):
        number = super().record(document)
        if _wing(self.current) in {"FORWARD", "BACKWARD"}:
            assert self.release.wait(30), "Host did not admit both native wings"
        return number


@pytest.mark.parametrize("mode", ["complete", "retry", "exhausted", "stop", "restart"])
def test_native_parallel_proposal_ownership_and_closure(tmp_path, monkeypatch, mode):
    scenario = ParallelScenario(tmp_path)
    secret = "native-parallel-fixture-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, secret)
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n"); settings.chmod(0o600)
    endpoint = f"http://127.0.0.1:{provider.server_port}"
    executable = _installed_claude()
    host = _new_host(tmp_path, executable, endpoint, secret)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-parallel-" + mode
    observed_overlap = False
    affected = None
    try:
        payload = _payload(tmp_path, run_id)
        contract = payload["research_contract"]
        product = contract["methodology"]["controls"]["product"]
        product["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        product["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        contract["budget"]["max_active_agents"] = 3
        host.initialize()
        assert host.handle(ControlRequest(1, "create-parallel", 0, "CREATE_RUN", payload)).status == "accepted"
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            assert not host.dispatch_errors, host.dispatch_errors
            wings = { _wing(handle.request): handle for handle in host.running_agents.values()
                if _wing(handle.request) in {"FORWARD", "BACKWARD"}}
            if len(wings) == 2 and not observed_overlap:
                observed_overlap = True
                assert all(process_group_exists(handle.process_group_id) for handle in wings.values())
                requests = [handle.request for handle in wings.values()]
                assert len({request.command_id for request in requests}) == 2
                scenario.release.set()
                if mode == "stop":
                    assert host.handle(ControlRequest(1, "stop-parallel", None, "CANCEL", {
                        "run_id": run_id, "cause": "bounded native parallel stop test"})).status == "accepted"
                elif mode == "restart":
                    host.close()
                    host = _new_host(tmp_path, executable, endpoint, secret)
                    host.grounding_web = _NoPublicWeb()
                    host.initialize()
                elif mode in {"retry", "exhausted"}:
                    affected = wings["FORWARD"].request.attempt_id
                    poll = host.claude_agent_host.poll
                    def failed_once(handle):
                        result = poll(handle)
                        if result is not None and handle.request.attempt_id == affected:
                            return replace(result, terminal_reason=AgentHostTerminalReason.FAILED,
                                response=None, failure_code="nonzero_exit" if mode == "retry" else "test_permanent")
                        return result
                    monkeypatch.setattr(host.claude_agent_host, "poll", failed_once)
                    host.claude_agent_host.cancel(wings["FORWARD"])
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}: break
            time.sleep(0.01)
        assert observed_overlap
        attempts = host.store.list_attempts()
        assert not [row for row in attempts if row["state"] == "OPEN"]
        # Explicit-signals runs close after operational failure and reporting;
        # workflow COMPLETED does not assert scientific success.
        expected = "CANCELLED" if mode == "stop" else "COMPLETED"
        assert host.store.latest_transition(run_id)["state"] == expected
        observed = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        proposals = [value for value in observed if value.get("request", {}).get("command") == "HYPOTHESIS_PROPOSAL"]
        # Eligible sequential retries may resume their own complete archive.
        # Distinct logical tasks must never share a session or writable scope.
        bound = [value for value in proposals if value.get("session", {}).get("session_id")]
        for field in ("session_id", "scope", "branch"):
            owners = {}
            for value in bound:
                owner = value["request"]["command_id"]
                assert owners.setdefault(value["session"][field], owner) == owner
        for index, left in enumerate(attempts):
            if left["kind"] != "AGENT": continue
            left_session = json.loads(left["observation_json"]).get("session", {}).get("session_id")
            if not left_session: continue
            for right in attempts[index + 1:]:
                if json.loads(right["observation_json"]).get("session", {}).get("session_id") == left_session:
                    assert left["ended_at"] <= right["started_at"] or right["ended_at"] <= left["started_at"]
        if mode in {"complete", "retry", "restart"}:
            assert len(host.store.list_facts(run_id, "CanonicalOutcomeAccepted")) == 1
            assert len([value for value in proposals if value["request"]["task_context"]["key"]["subkind"] == "SYNTHESIS"]) == 1
        if mode == "retry":
            assert len([value for value in proposals if value["request"]["task_context"]["key"]["subkind"] == "BACKWARD"]) == 1
        if mode == "exhausted":
            assert not host.store.list_facts(run_id, "HypothesisProposed")
            assert not host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            failures = host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE json_extract(payload_json,'$.agent_final_failure')=1")
            assert len(failures) == 1
            assert json.loads(failures[0]["payload_json"])["attempt_id"] == affected
            assert any(value.get("failure_code") == "proposal_sibling_failed" for value in proposals)
        report = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(report.to_dict(), indent=2))
        assert report.ok, report.to_dict()
        assert not host.running_agents and not host.running
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
    finally:
        scenario.release.set()
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors))
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
        if (tmp_path / "admitted-target.txt").exists(): shutil.rmtree(Path((tmp_path / "admitted-target.txt").read_text().strip()))
