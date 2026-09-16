"""Actual CLI native denial followed by an allowed Bash tool in the same session."""
import json
import secrets
import threading
from dataclasses import replace
from http.server import HTTPServer

from hypoloom.agent_aci import AgentBudget, EmbeddedInput
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from test_agent_sessions import _manager
from test_claude_chain_cli import _invoke
from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results
from test_git_authoring_workspace import make_target


class _PolicyScenario:
    stage, chain = "profile-policy", None

    def __init__(self):
        self.requests, self.errors, self.evidence = [], [], {}

    def record(self, document):
        self.requests.append(document)
        step = len(self.requests)
        try:
            assert "Bash" in {tool["name"] for tool in document["tools"]}
            if step == 2:
                result = _tool_results(document)["denied_operation"]
                assert result.get("is_error"), result
                assert "deny_patterns" in _result_text(result), result
                self.evidence["denied_operation"] = result
            if step == 3:
                result = _tool_results(document)["allowed_operation"]
                assert not result.get("is_error"), result
                assert "PROFILE_NATIVE_POLICY_OK" in _result_text(result)
                self.evidence["allowed_operation"] = result
        except Exception as exc: self.errors.append(str(exc))
        return step

    def tool_calls(self, number):
        if self.errors: return ()
        if number == 1: return (("denied_operation", "Bash", {"command": "touch /tmp/profile-policy-denied", "timeout": 10000}),)
        if number == 2:
            return (("allowed_operation", "Bash", {"command": "python3 - <<'PY'\nfrom pathlib import Path\nassert not Path('/tmp/profile-policy-denied').exists()\nPath('/tmp/ordinary-test-output').write_text('allowed')\nprint('PROFILE_NATIVE_POLICY_OK')\nPY", "timeout": 10000}),)
        return (("finish", "StructuredOutput", {"stage": self.stage, "ok": True}),)


def test_native_bash_regex_policy(tmp_path):
    executable = _installed_claude()
    _, target = make_target(tmp_path)
    request = replace(default_request(AgentCommand.GROUNDING), attempt_id="native-profile-policy",
        input_artifacts=(EmbeddedInput.from_bytes("node--grounding-config", "application/json", json.dumps({
            "node_runtime": {"safety": {"bash": {"deny_patterns": [r"^touch\s+/tmp/profile-policy-denied$"]}}}}).encode()),),
        budget=AgentBudget(max_turns=8, max_tokens=20000, max_output_bytes=131072, wall_seconds=40, max_cost="1"))
    workspace = _manager(tmp_path).prepare(request, target)
    scenario = _PolicyScenario()
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True); thread.start()
    try:
        metadata = _invoke(tmp_path, executable, workspace, scenario,
            f"http://127.0.0.1:{provider.server_port}", "host-only-policy-key-" + secrets.token_hex(12))
        assert set(scenario.evidence) == {"denied_operation", "allowed_operation"}
        assert metadata["transcript_complete"] is True
        (tmp_path / "policy-tool-evidence.json").write_text(json.dumps(scenario.evidence, indent=2))
    finally:
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
