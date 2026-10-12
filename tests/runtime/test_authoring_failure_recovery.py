"""Native parser rejection must survive delivery and repair through the real Host."""
import json
import secrets
import tempfile
import time
from pathlib import Path

import pytest

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.agent_aci import AgentRequest
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import _DeclaredScenario, _NoPublicWeb, _six_yaml_profile_v4_payload
from test_full_cc_host_g3 import _new_host, _start_provider


_BAD_BASELINE = "\nfrom pathlib import Path\nimport json\np=Path('/parsed/result.json')\nd=json.loads(p.read_text())\nd['baseline']={'dataset': {'f1': 0.7}}\np.write_text(json.dumps(d))\n"


class _BaselineScenario(_DeclaredScenario):
    def __init__(self, root):
        super().__init__(root, "baseline-field-repair")
        self.feedback = []

    def record(self, document):
        number = super().record(document)
        r = self.current
        if r.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and self.steps[r.attempt_id] == 8:
            message = json.dumps(document["messages"][-1])
            if "result.baseline" not in message:
                self.errors.append("native Stop lost the baseline field error: " + message[:500])
            self.feedback.append(message)
        return number

    def tool_calls(self, number):
        r = self.current
        if r.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT and r.attempt_ordinal == 1:
            step = self.steps[r.attempt_id]
            if step in {6, 8}:
                source = next(a.content_bytes().decode() for a in self.responses[r.attempt_id].proposed_workspace_artifacts
                              if a.path == "parse_metrics.py")
                return ((f"baseline_{step}", "Write", {"file_path": "/workspace/parse_metrics.py",
                    "content": source + (_BAD_BASELINE if step == 6 else "")}),)
        return super().tool_calls(number)


@pytest.mark.parametrize("cap", [1, 3], ids=["next-attempt", "same-stop"])
def test_native_parser_failure(tmp_path, cap):
    executable = _installed_claude()
    scenario = _BaselineScenario(tmp_path)
    key = "baseline-repair-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    run = "baseline-repair"
    try:
        with tempfile.TemporaryDirectory(prefix="hypoloom-baseline-", dir="/var/tmp") as mount:
            payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), run, correction_cap=cap)
            host.initialize()
            assert host.handle(ControlRequest(1, "create", 0, "CREATE_RUN", payload)).status == "accepted"
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                host.tick()
                assert not scenario.errors, scenario.errors
                if host.store.latest_transition(run)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(.01)
            attempts = host.store.list_attempts()
            assert host.store.latest_transition(run)["state"] == "COMPLETED"
            agents = [json.loads(a["observation_json"]) for a in attempts if a["kind"] == "AGENT"]
            designs = [a for a in agents if a["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"]
            # Large retries reconstruct directly and preserve the original
            # parser correction without an extra capacity rejection.
            assert len(designs) == (2 if cap == 1 else 1)
            first = designs[0]
            trace = first["diagnostics"]["structured_output_correction"]
            assert trace["rejected_count"] == 1
            if cap == 1:
                assert first["failure_code"] == "invalid_candidate"
                assert "result.baseline" in first["diagnostics"]["validation_error"]["message"]
                assert "implementation.patch" not in first["diagnostics"]["provider_diagnostic"]
                assert "provider_accounting" in first["diagnostics"]
                assert designs[1]["session"]["session_id"] != first["session"]["session_id"]
                assert designs[1]["session"]["branch"] == first["session"]["branch"]
                assert designs[1]["session"]["launch_arguments"][0] == "--session-id"
                for retry_observation in designs[1:]:
                    retry = AgentRequest.from_dict(retry_observation["request"])
                    assert any(b"result.baseline" in a.content_bytes() for a in retry.input_artifacts)
                assert designs[1]["response"]["status"] == "SUCCEEDED"
            else:
                assert trace["accepted"] and trace["status"] == "recovered"
                assert len(scenario.feedback) == 1
            processes = [a for a in attempts if a["kind"] == "PROCESS"]
            assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
            outcomes = host.store.list_facts(run, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["metric_value"] == "-0.1"
            assert all(a["state"] == "CLOSED" for a in attempts)
            assert all(not process_group_exists(a["process_group_id"]) for a in attempts if a["process_group_id"])
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run)
            assert audit.ok and audit.open_attempt_count == audit.pending_effect_count == 0, audit.to_dict()
            (tmp_path / "census.json").write_text(json.dumps({"agents": len(agents), "processes": len(processes),
                "designs": len(designs), "oracle": audit.to_dict()}, indent=2))
    finally:
        host.close()
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
