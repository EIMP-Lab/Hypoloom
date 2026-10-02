"""Installed CLI repairs Git/encoded output bounds before one real CPU submit."""

import json
import secrets
import tempfile
import time
from pathlib import Path

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.oracle import Oracle
from test_claude_file_tool_cli import _installed_claude
from test_claude_measurement_g3 import (
    _DeclaredScenario, _NoPublicWeb, _six_yaml_profile_v4_payload,
)
from test_full_cc_host_g3 import _new_host, _start_provider


_LARGE = "#" + "x" * 69998 + "\n"
_ENCODED = "#" + "y" * 51998 + "\n"


class _OutputRepairScenario(_DeclaredScenario):
    def __init__(self, root):
        super().__init__(root, "output-size-repair")
        self.feedback = []

    def record(self, document):
        number = super().record(document)
        request = self.current
        if request is not None and request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            step = self.steps[request.attempt_id]
            if step in {7, 9}:
                # These are actual native Stop messages from the real CLI.
                text = json.dumps(document["messages"][-1])
                expected = "implementation.patch exceeds its byte bound" if step == 7 else "candidate and encoded workspace"
                if expected not in text or "131072" not in text:
                    self.errors.append(f"missing native size feedback at step {step}: {text[:1500]}")
                self.feedback.append(expected)
        return number

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        request = self.current
        if request is None or request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return calls
        step = self.steps[request.attempt_id]
        if step == 1:
            return tuple((identity, name, {**args, "content": args["content"] + _LARGE})
                for identity, name, args in calls)
        if step in {7, 9}:
            return ((f"shrink_output_{step}", "Edit", {
                "file_path": "/workspace/experiment.sh",
                "old_string": _LARGE if step == 7 else _ENCODED,
                "new_string": _ENCODED if step == 7 else "",
            }),)
        return calls


def test_native_design_repairs_both_output_bounds_before_cpu_publication(tmp_path):
    executable = _installed_claude()
    scenario = _OutputRepairScenario(tmp_path)
    key = "local-output-repair-" + secrets.token_hex(12)
    provider, thread = _start_provider(scenario, key)
    (tmp_path / "settings.json").write_text("{}\n")
    (tmp_path / "settings.json").chmod(0o600)
    host = _new_host(tmp_path, executable, f"http://127.0.0.1:{provider.server_port}", key)
    host.grounding_web = _NoPublicWeb()
    run_id = "native-design-output-repair"
    try:
        with tempfile.TemporaryDirectory(prefix="hypoloom-output-g3-", dir="/var/tmp") as mount:
            payload = _six_yaml_profile_v4_payload(tmp_path, Path(mount), run_id, correction_cap=3)
            host.initialize()
            created = host.handle(ControlRequest(1, "create-output-repair", 0, "CREATE_RUN", payload))
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
            agents = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
            designs = [x for x in agents if x["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"]
            assert len(designs) == 1 and designs[0]["request"]["attempt_ordinal"] == 1
            design = designs[0]
            assert design["response"]["status"] == "SUCCEEDED"
            correction = design["diagnostics"]["structured_output_correction"]
            assert correction["status"] == "recovered" and correction["accepted"]
            assert correction["rejected_count"] == 2 and correction["call_count"] == 3
            assert len(scenario.feedback) == 2
            assert len(json.dumps(design["response"], separators=(",", ":")).encode()) < 131072
            processes = [row for row in attempts if row["kind"] == "PROCESS"]
            assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
            outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
            assert len(outcomes) == 1 and outcomes[0]["payload"]["metric_value"] == "-0.1"
            assert outcomes[0]["payload"]["outcome"] == "SUCCESS"
            assert not host.running and not host.running_agents
            assert all(not process_group_exists(row["process_group_id"])
                for row in attempts if row["process_group_id"] is not None)
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            assert audit.ok and audit.open_attempt_count == audit.pending_effect_count == 0, audit.to_dict()
            (tmp_path / "output-recovery-evidence.json").write_text(json.dumps({
                "agents": len(agents), "processes": len(processes), "design_attempts": len(designs),
                "native_correction": correction, "oracle": audit.to_dict()}, indent=2))
    finally:
        host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
