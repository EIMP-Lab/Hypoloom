"""Native Observer failure, durable Host recovery and a fresh accepted retry."""

import json
import os
import secrets
import signal
import time
from pathlib import Path

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle

from test_claude_file_tool_cli import _installed_claude
from test_claude_leader_host_g3 import _LeaderHostScenario, _ROLES
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _payload
from test_full_cc_host_g3 import _new_host, _start_provider


class _ObserverRetryScenario(_LeaderHostScenario):
    def __init__(self, private_root):
        super().__init__(private_root)
        self.failed_attempt_id = None
        self.failed_context = "failed-observer-private-" + secrets.token_hex(16)

    def _unscoped_tool_calls(self, number):
        request = self.current
        if request is not None and request.command is AgentCommand.OBSERVER:
            if self.failed_attempt_id is None:
                self.failed_attempt_id = request.attempt_id
            if request.attempt_id == self.failed_attempt_id and self.steps[request.attempt_id] == 2:
                # The test kills only this fresh, owned native process after
                # its real transcript contains this tool call.
                return (("observer_interrupted", "Bash", {
                    "command": f"printf '%s\\n' '{self.failed_context}'; sleep 30",
                    "timeout": 35000,
                }),)
        return super()._unscoped_tool_calls(number)


def test_real_cli_observer_failed_attempt_survives_restart_and_retries_stateless(tmp_path: Path, monkeypatch):
    # Reuse the bounded native interaction fixture's transport schema. Session
    # identity and Observer acceptance use the current production code.
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    executable = _installed_claude()
    scenario = _ObserverRetryScenario(tmp_path)
    key = "host-only-observer-retry-g3-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}\n")
    settings_path.chmod(0o600)

    def new_host():
        result = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
        result.grounding_web = _NoPublicWeb()
        return result

    host = new_host()
    run_id = "native-observer-retry-g3"
    failed_transcript = None
    failed_bytes = None
    restarted = False
    try:
        payload = _payload(tmp_path, run_id)
        settings = payload["research_contract"]["methodology"]["controls"]["product"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["methodology"]["inner_loop"]["max_iterations_per_hypothesis"] = 1
        settings["methodology"]["interaction"] = {
            "leader": {"enabled": True, "check_after": "cycle_report", "min_rounds_before_leader": 1},
            "debate": {"enabled": False, "rounds": 3},
        }
        host.initialize()
        created = host.handle(ControlRequest(1, "create-observer-retry", 0, "CREATE_RUN", payload))
        assert created.status == "accepted", created
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            host.tick()
            assert not scenario.errors, scenario.errors
            handle = host.running_agents.get(scenario.failed_attempt_id)
            if not restarted and handle is not None:
                workspace = handle.tool_workspace
                transcript = workspace.session_root / f"{workspace.binding['session_id']}.jsonl"
                if transcript.exists() and scenario.failed_context.encode() in transcript.read_bytes():
                    os.killpg(handle.process_group_id, signal.SIGKILL)
                    # Close the failed Attempt through the production poller,
                    # then restart before scheduling its retry.
                    poll_deadline = time.monotonic() + 15
                    while time.monotonic() < poll_deadline:
                        host._poll_agents()
                        failed = host.store.get_attempt(scenario.failed_attempt_id)
                        if failed["state"] == "CLOSED":
                            break
                        time.sleep(.01)
                    assert failed["terminal_reason"] == "FAILED", failed
                    observed = json.loads(failed["observation_json"])
                    assert observed["failure_code"] == "nonzero_exit"
                    assert observed["session"]["transcript_complete"] is True
                    failed_transcript = host.publisher.root / observed["session"]["transcript"]["path"] / "transcript.jsonl"
                    failed_bytes = failed_transcript.read_bytes()
                    assert scenario.failed_context.encode() in failed_bytes
                    assert not host.running and not host.running_agents
                    host.close()
                    host = new_host()
                    host.initialize()
                    restarted = True
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(.01)

        assert restarted
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
        attempts = host.store.list_attempts()
        assert all(row["state"] == "CLOSED" for row in attempts)
        assert [row["attempt_id"] for row in attempts if row["terminal_reason"] != "SUCCEEDED"] == [scenario.failed_attempt_id]
        assert len([row for row in attempts if row["kind"] == "PROCESS"]) == 1
        assert scenario.local_tests == set(scenario.execution_results) == {(_ROLES[0], "primary.validation_accuracy", 1, "chain-a", 1)}
        observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
        observers = [row for row in observations if row["request"]["command"] == "OBSERVER"]
        assert len(observers) == 2
        assert len({row["request"]["command_id"] for row in observers}) == 1
        assert len({row["session"]["session_id"] for row in observers}) == 2
        for observed in observers:
            session = observed["session"]
            assert session["parent_session_id"] is None
            assert session["launch_arguments"] == ["--session-id", session["session_id"]]
            assert session["transcript_complete"] is True
        retry = next(row for row in observers if "response" in row)
        assert retry["response"]["typed_candidate"]["action"] == "none"
        retry_transcript = host.publisher.root / retry["session"]["transcript"]["path"] / "transcript.jsonl"
        assert scenario.failed_context.encode() not in retry_transcript.read_bytes()
        assert failed_transcript.read_bytes() == failed_bytes
        assert any(row["request"]["command"] == "FINAL_REPORT" for row in observations)
        assert not host.running and not host.running_agents
        assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
        audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
        (tmp_path / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
        assert audit.ok, audit.to_dict()
        assert audit.canonical_outcome_count == 1
        assert audit.open_attempt_count == audit.pending_effect_count == 0
        assert audit.artifact_counts["TRANSCRIPT"] == len(observations)
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
