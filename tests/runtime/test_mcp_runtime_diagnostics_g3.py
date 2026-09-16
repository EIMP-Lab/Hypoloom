"""Native CC repairs a Host MCP failure and exports its actual Python stack.

Only model choices and literature are local fixtures. The one injected handler
exception precedes every submission effect; retry, CPU work, publication and
independent Oracle checks all use the production topology.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import time
from pathlib import Path

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from hypoloom.support_bundle import export_support_bundle

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_shared_acceptance_g3 import (
    _AcceptanceScenario, _BROKEN, _DECLARATION, _GOAL, _REPAIRED, _RUN, _payload,
)
from test_full_cc_host_g3 import _new_host, _request_from_provider, _start_provider


_MCP_FAILURE = "G3_HOST_SUBMIT_HANDLER_IO_FAILURE_BEFORE_EFFECT"


class _McpRetryScenario(_AcceptanceScenario):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.failed_attempt = None
        self.failed_result = None

    def record(self, document):
        request = _request_from_provider(document)
        failed = _tool_results(document).get("mcp_failed_submit")
        if (request.command is AgentCommand.EXPERIMENT_EXECUTION
                and failed is not None and self.failed_attempt is None):
            self.requests.append(document)
            self.current = request
            self.failed_attempt = request.attempt_id
            self.failed_result = _result_text(failed)
            if not failed.get("is_error") or _MCP_FAILURE not in self.failed_result:
                self.errors.append("native CC did not receive the injected Host MCP error")
            # Leave the successful-submission scenario at its first step: its
            # next native tool call retries the unchanged candidate, and only
            # the actual Host response advances its normal acceptance checks.
            return len(self.requests)
        return super().record(document)

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        if self.failed_attempt is None:
            return tuple(("mcp_failed_submit" if identity == "shared_submit" else identity,
                          tool, arguments) for identity, tool, arguments in calls)
        return calls


def _json_lines(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_native_mcp_handler_traceback_survives_retry_and_private_support_export(tmp_path: Path, monkeypatch):
    executable = _installed_claude()
    scenario = _McpRetryScenario(tmp_path, "mcp-diagnostic-" + secrets.token_hex(12))
    key = "local-mcp-diagnostic-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    handler = host.handle_agent_tool
    submissions = []
    evidence = {"paid_model": False, "gpu": False}

    def fail_first_submit(attempt_id, tool, arguments):
        if tool == "submit_experiment":
            submissions.append({"attempt_id": attempt_id, "arguments": arguments})
            if len(submissions) == 1:
                assert host.store.list_facts(_RUN, "ExperimentPreregistered") == ()
                raise OSError(_MCP_FAILURE)
        return handler(attempt_id, tool, arguments)

    monkeypatch.setattr(host, "handle_agent_tool", fail_first_submit)
    try:
        with tempfile.TemporaryDirectory(prefix="prom-mcp-diag-g3-", dir="/var/tmp") as mount:
            payload = _payload(tmp_path, Path(mount), roles=(_GOAL,))
            host.initialize()
            created = host.handle(ControlRequest(1, "create-mcp-diagnostic", 0, "CREATE_RUN", payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(_RUN)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            assert host.store.latest_transition(_RUN)["state"] == "COMPLETED", host.store.list_attempts()
            assert len(submissions) == 2 and submissions[0] == submissions[1]
            assert scenario.failed_attempt == submissions[0]["attempt_id"]
            assert scenario.failed_result and _MCP_FAILURE in scenario.failed_result
            attempts = host.store.list_attempts()
            assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
            agents = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"]
            execution = [(row, observed) for row, observed in agents
                         if observed["request"]["command"] == "EXPERIMENT_EXECUTION"]
            assert len(execution) == 1 and execution[0][0]["attempt_id"] == scenario.failed_attempt
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            assert len(processes) == 1
            specs = host.store.list_facts(_RUN, "ExperimentPreregistered")
            outcomes = host.store.list_facts(_RUN, "CanonicalOutcomeAccepted")
            verdicts = host.store.list_facts(_RUN, "GoalVerdictRecorded")
            assert len(specs) == len(outcomes) == len(verdicts) == 1
            assert specs[0]["payload"]["goal_evaluation"] == {
                "scope": "GOAL", "source_attempt_id": scenario.source_attempt_id, "acceptance": _DECLARATION}
            assert outcomes[0]["payload"]["goal_evaluation"] == {
                "source_attempt_id": scenario.source_attempt_id,
                "measurements": {"suite_one_accuracy": "0.75", "suite_two_accuracy": "0.75"},
                "checks": {"complete": True, "single_candidate": True}}
            assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
            audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN)
            assert audit.ok, audit.to_dict()
            assert audit.open_attempt_count == audit.pending_effect_count == 0
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"])

            diagnostic_root = tmp_path / "state/diagnostics/agents" / _RUN
            mcp_events = [event for path in diagnostic_root.glob("*/events.jsonl")
                          for event in _json_lines(path) if event["kind"] == "mcp_exception"]
            assert len(mcp_events) == 1
            event = mcp_events[0]
            assert event["attempt_id"] == scenario.failed_attempt
            assert event["tool"] == "submit_experiment" and event["stage"] == "handler"
            assert event["exception_type"] == "OSError" and event["message"] == _MCP_FAILURE
            assert all(text in event["traceback"] for text in (
                "Traceback (most recent call last)", "host_tools_server.py", "fail_first_submit", _MCP_FAILURE))
            assert key not in json.dumps(event)
            stream = _json_lines(diagnostic_root / scenario.failed_attempt / "events.jsonl")
            assert [item["kind"] for item in stream] == ["prepared", "started", "mcp_exception", "ended"]
            execution_transcript = host.publisher.root / execution[0][1]["session"]["transcript"]["path"] / "transcript.jsonl"
            assert _MCP_FAILURE in execution_transcript.read_text()
            ground = next(observed for _, observed in agents if observed["request"]["command"] == "GROUNDING")
            grounding_transcript = host.publisher.root / ground["session"]["transcript"]["path"] / "transcript.jsonl"
            assert all(text in grounding_transcript.read_text() for text in (_BROKEN, _REPAIRED))
            assert not any(item["kind"] == "mcp_exception" for item in
                           _json_lines(diagnostic_root / ground["request"]["attempt_id"] / "events.jsonl"))

            private_output, default_output = tmp_path / "private-support", tmp_path / "default-support"
            private_report = export_support_bundle(tmp_path / "state", _RUN, private_output, include_private_data=True)
            default_report = export_support_bundle(tmp_path / "state", _RUN, default_output)
            relative = Path("files/diagnostics/agents") / _RUN / scenario.failed_attempt / "events.jsonl"
            exported = next(item for item in _json_lines(private_output / relative) if item["kind"] == "mcp_exception")
            assert exported == event
            public_event = next(item for item in default_report["attachments"] if item.get("path", "").endswith("events.jsonl")
                and any(event.get("kind") == "mcp_exception" for event in _json_lines(default_output / item["path"])))
            metadata = next(item for item in _json_lines(default_output / public_event["path"]) if item["kind"] == "mcp_exception")
            assert metadata["attempt_id"] == public_event["attempt_id"] and metadata["exception_type"] == "OSError"
            assert metadata["attempt_id"] != scenario.failed_attempt
            assert "message" not in metadata and "traceback" not in metadata
            assert any(item["path"] == public_event["path"] and "non_metadata_fields_omitted" in item["redactions"]
                       for item in default_report["attachments"] if item.get("path"))
            for output, report in ((private_output, private_report), (default_output, default_report)):
                assert report["source_read_only"] and report["non_authoritative"]
                assert len(json.loads((output / "debug.json").read_text())["attempts"]) == len(attempts)
                for path in output.rglob("*"):
                    assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
                    if path.is_file():
                        assert key.encode() not in path.read_bytes()
            evidence.update(agent_count=len(agents), process_count=len(processes),
                submission_calls=len(submissions), accepted_experiments=len(specs),
                execution_attempt=scenario.failed_attempt, mcp_event=event,
                cli_internal_failure=_BROKEN, private_support=str(private_output),
                default_support=str(default_output), oracle=audit.to_dict())
    finally:
        evidence["head"] = dict(host.store.latest_transition(_RUN) or {})
        (tmp_path / "mcp-diagnostic-g3-evidence.json").write_text(json.dumps(evidence, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
