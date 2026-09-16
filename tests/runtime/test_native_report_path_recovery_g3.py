"""Actual installed CC with local scripted responses; no external model calls."""

import json
from pathlib import Path
import secrets
import tempfile
import time

import pytest

import test_claude_plugin_cli as plugin_fixture
from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_memory import ExpertMemory
from hypoloom.oracle import Oracle
from test_expert_memory_g3 import _FinalReportMemoryScenario, _cycle
from test_expert_loop_host import _pilot_payload
from test_full_cc_host_g3 import _new_host, _start_provider


@pytest.mark.parametrize("enabled", [True, False])
def test_installed_cc_suppresses_native_auto_memory_system_instruction(tmp_path, monkeypatch, enabled):
    original_argv = plugin_fixture.full_cc_argv
    def probe_argv(**arguments):
        argv = list(original_argv(**arguments))
        if enabled:
            settings_index = argv.index("--settings") + 1
            settings = json.loads(argv[settings_index])
            settings["autoMemoryEnabled"] = True
            argv[settings_index] = json.dumps(settings)
        return tuple(argv)

    class NoAutoMemoryScenario(plugin_fixture._PluginScenario):
        def record(self, document):
            step = super().record(document)
            (tmp_path / f"provider-{step}.json").write_text(json.dumps(document, indent=2))
            system = json.dumps(document.get("system", []))
            if ("/home/agent/.claude/projects/-workspace/memory/" in system) != enabled:
                self.errors.append(f"native auto-memory system instruction differs from enabled={enabled}")
            return step

    monkeypatch.setattr(plugin_fixture, "_PluginScenario", NoAutoMemoryScenario)
    monkeypatch.setattr(plugin_fixture, "full_cc_argv", probe_argv)
    plugin_fixture.test_native_plugin_skill_is_invoked_by_real_sandboxed_cli(
        tmp_path, "alpha", "low", plugin_fixture._MODEL, False)


class _WrongReportPathScenario(_FinalReportMemoryScenario):
    def __init__(self, private_root, nonce):
        super().__init__(private_root, nonce)
        self.restart_completed = True
        self.retry_diagnostic = None

    def record(self, document):
        number = super().record(document)
        request = self.current
        if request is not None and request.command is AgentCommand.CYCLE_REPORT:
            try:
                supplied = json.loads(next(item for item in request.input_artifacts
                    if item.ref.endswith("--cycle-assessments")).content_bytes())
                assert supplied["report_path"] == f"rounds/{_cycle(request)}.md"
                assert "/home/agent/.claude/projects/-workspace/memory/" not in json.dumps(document.get("system", []))
                if _cycle(request) == 1 and request.attempt_ordinal == 2:
                    self.retry_diagnostic = json.loads(next(item for item in request.input_artifacts
                        if item.ref.endswith("--agent-retry-diagnostic")).content_bytes())
                    summary = self.retry_diagnostic["diagnostic_summary"]
                    assert "missing" in summary and "/memory/rounds/1.md" in summary
                    assert "outside the allowed set" in summary and "wrong-component/1.md" in summary
                    assert str(self.private_root) not in summary
            except (AssertionError, KeyError, StopIteration) as exc:
                self.errors.append(f"report repair evidence: {exc}")
        return number

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        request = self.current
        if (request is not None and request.command is AgentCommand.CYCLE_REPORT
                and _cycle(request) == 1 and request.attempt_ordinal == 1):
            return tuple((identity, name, {**arguments, "file_path": "/memory/rounds/wrong-component/1.md"})
                if identity == "report_round" else (identity, name, arguments)
                for identity, name, arguments in calls)
        return calls


def test_native_report_wrong_path_retries_same_session_and_publishes_canonical_memory(tmp_path):
    executable = plugin_fixture._installed_claude()
    scenario = _WrongReportPathScenario(tmp_path, "report-path-lesson-" + secrets.token_hex(8))
    key = "local-only-report-repair-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    run_id = "native-report-path-repair"
    try:
        with tempfile.TemporaryDirectory(prefix="hypoloom-report-path-", dir="/var/tmp") as mount:
            payload = _pilot_payload(tmp_path, Path(mount), run_id)
            host.initialize()
            created = host.handle(ControlRequest(schema_version=1, request_id="create-report-path",
                expected_revision=0, command="CREATE_RUN", payload=payload))
            assert created.status == "accepted", created
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.01)
            attempts = host.store.list_attempts()
            assert host.store.latest_transition(run_id)["state"] == "COMPLETED", attempts
            assert all(row["state"] == "CLOSED" for row in attempts)
            observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
            reports = sorted((item for item in observations
                if item["request"]["command"] == "CYCLE_REPORT"
                and item["request"]["task_context"]["key"]["cycle"] == 1),
                key=lambda item: item["request"]["attempt_ordinal"])
            assert len(reports) == 2
            rejected, accepted = reports
            assert rejected["failure_code"] == "invalid_tool_workspace"
            assert rejected["retry_class"] == "MODEL"
            assert accepted["response"]["status"] == "SUCCEEDED"
            assert rejected["session_binding"] == accepted["session_binding"]
            assert accepted["session"]["launch_arguments"] == ["--resume", accepted["session"]["session_id"]]
            assert rejected["session"]["launch_arguments"] == ["--session-id", rejected["session"]["session_id"]]
            assert scenario.retry_diagnostic["source_attempt_id"] == rejected["request"]["attempt_id"]
            role = accepted["request"]["role"]
            memory = ExpertMemory(host.state_root)
            files = memory.files(run_id, role, accepted["memory"]["commit"])
            assert set(files) == {"MEMORY.md", "rounds/1.md"}
            assert scenario.nonce.encode() in files["rounds/1.md"]
            archive = host.publisher.root / accepted["session"]["transcript"]["path"]
            assert (archive / "memory/rounds/1.md").read_bytes() == files["rounds/1.md"]
            transcript = (archive / "transcript.jsonl").read_text()
            assert "/memory/rounds/wrong-component/1.md" in transcript
            assert "/memory/rounds/1.md" in transcript and key not in transcript
            forks = [item for item in observations if "--fork-session" in item.get("session", {}).get("launch_arguments", [])]
            assert forks, "the native rehearsal must also retain real fork parent material"
            for item in forks:
                session = item["session"]
                projects = (host.state_root / run_id / "sessions" / item["request"]["role"]
                    / f"c{session['cycle']}" / item["request"]["component_id"] / session["scope"] / "projects")
                assert (projects / (session["parent_session_id"] + ".jsonl")).is_file()
                assert (projects / (session["session_id"] + ".jsonl")).is_file()
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"])
                for row in attempts if row["process_group_id"] is not None)
            oracle = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert oracle.ok, oracle.to_dict()
            assert oracle.open_attempt_count == oracle.pending_effect_count == 0
            (tmp_path / "report-recovery-evidence.json").write_text(json.dumps({
                "attempts": len(attempts), "agent_attempts": len(observations),
                "process_attempts": sum(row["kind"] == "PROCESS" for row in attempts),
                "rejected_attempt": rejected["request"]["attempt_id"],
                "accepted_attempt": accepted["request"]["attempt_id"],
                "retry_diagnostic": scenario.retry_diagnostic, "oracle": oracle.to_dict()}, indent=2))
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
