"""Real CC corrects a semantic Observer error in one stateless invocation.

The local provider chooses outputs; the native hook, sandbox, session capture,
postflight and accounting are production code. Host effects are tested separately.
"""

import json
import secrets
from dataclasses import replace

from hypoloom.agent_aci import EmbeddedInput, required_effect, validate_postflight
from hypoloom.claude_agent_host import ClaudeAgentHost, process_group_exists

from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude
from test_full_cc_adapter import _MODEL, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_grounding_manager import _with_plugin
from test_observer_aci14 import observer_request
from test_observer_native_output import observer_candidate


class _ObserverCorrection:
    def __init__(self, request):
        self.requests, self.errors = [], []
        self.effect = required_effect(request.command)

    def record(self, document):
        self.requests.append(document)
        if len(self.requests) == 2:
            messages = json.dumps(document["messages"])
            assert "Stop hook feedback:" in messages
            assert "Leader selection must cite its actual own verified contribution" in messages
        assert len(self.requests) <= 2
        return len(self.requests)

    def tool_calls(self, number):
        return (("observer-output", "StructuredOutput", {
            "typed_candidate": observer_candidate(valid=number == 2), "declared_effects": [{"effect": self.effect}]}),)


def test_native_observer_corrects_semantics_and_accepts_fact_aliases_in_one_attempt(tmp_path):
    executable = _installed_claude()
    manager = _manager(tmp_path)
    _, _, request = _with_plugin(manager, observer_request(version=22))
    config = EmbeddedInput.from_bytes("observer--expert-config", "application/json",
        json.dumps({"node_runtime": {"output_correction": {"max_failed_validations": 3}}}).encode())
    request = replace(request, input_artifacts=(*request.input_artifacts, config),
        immutable_context_refs=(*request.immutable_context_refs, config.ref))
    request = native_request(request, "observer", None)
    scenario = _ObserverCorrection(request)
    key = "local-observer-provider-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    adapter = ClaudeAgentHost(claude_executable=executable)
    workspace = manager.prepare(request, None)
    bindings = []
    handle = None
    try:
        handle = adapter.start(request, model=_MODEL, tool_workspace=workspace,
            provider_environment={"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"},
            bind_process_group=bindings.append, diagnostics_root=manager.state_root)
        observation = adapter.wait(handle, timeout=60)
        assert observation.terminal_reason.value == "SUCCEEDED", observation
        assert len(bindings) == 1 and bindings[0].attempt_id == request.attempt_id
        assert not scenario.errors and len(scenario.requests) == 2
        candidate = validate_postflight(request, observation.response).candidate.to_dict()
        assert candidate == observer_candidate()
        trace = observation.diagnostics["structured_output_correction"]
        assert trace["status"] == "recovered" and trace["accepted"]
        assert (trace["call_count"], trace["rejected_count"]) == (2, 1)
        assert "own verified contribution" in trace["rounds"][0]["validation_error"]
        metadata = workspace.finish_node()
        archive = manager.publisher.root / metadata["transcript"]["path"]
        assert metadata["transcript_complete"] and metadata["parent_session_id"] is None
        assert key.encode() not in (archive / "transcript.jsonl").read_bytes()
        diagnostics = manager.state_root / "diagnostics/agents" / request.run_id / request.attempt_id
        validations = [json.loads(path.read_text()) for path in sorted((diagnostics / "native-output").glob("*.json"))]
        assert len(validations) == 2 and {item["session_id"] for item in validations} == {metadata["session_id"]}
        assert validations[0]["error"] and validations[1]["error"] is None
        (tmp_path / "observer-evidence.json").write_text(json.dumps({"attempt_id": request.attempt_id,
            "native_stop": trace, "candidate": candidate, "session": metadata}, indent=2))
    finally:
        if handle is not None:
            adapter.cleanup(handle)
            assert not process_group_exists(handle.process_group_id)
        else:
            workspace.cleanup()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    assert workspace.resources == []
    assert not list(tmp_path.rglob("*.sock"))
