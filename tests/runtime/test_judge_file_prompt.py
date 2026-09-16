"""Large six-source Debate context stays complete on disk, not twice in prompt."""
import json
from dataclasses import replace
import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import _prompt
from test_debate_aci14 import judge_request


def test_full_cc_judge_advertises_exact_file_without_inlining_recursive_rows():
    request, _ = judge_request()
    item = request.input_artifacts[0]
    context = json.loads(item.content_bytes())
    context["round_attempts"][0]["preserved_test_context"] = "retained-source-bytes" * 20000
    large = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(context).encode())
    request = replace(request, input_artifacts=(large,), max_input_bytes=4 * 1024 * 1024)
    before = request.to_dict()
    payload = json.loads(_prompt(request, tool_workspace=True))
    assert len(json.dumps(payload)) < 20000
    assert "retained-source-bytes" not in json.dumps(payload)
    record = payload["rendered_inputs"][0]
    assert record["ref"] == item.ref and record["path"] == "/inputs/0000.txt"
    assert record["size_bytes"] == len(large.content_bytes())
    assert "all six" in payload["instruction"] and "Bash" in payload["instruction"]
    assert request.to_dict() == before


def test_real_start_uses_the_prepared_judge_input_file(tmp_path, monkeypatch):
    import hypoloom.claude_agent_host as adapter
    from test_agent_sessions import _manager
    from test_claude_agent_host import _host

    request, _ = judge_request()
    request = replace(request, required_adapter_version=adapter.CLAUDE_AGENT_HOST_VERSION,
                      required_model_identity="stub-model",
                      tool_permission_profile=adapter.CLAUDE_AGENT_TOOL_PERMISSION_PROFILE)
    workspace = _manager(tmp_path).prepare(request, None)
    host = _host(tmp_path)
    monkeypatch.setattr(host, "preflight", lambda **kwargs: None)
    captured = {}
    original = adapter._prompt

    def capture(*args, **kwargs):
        rendered = original(*args, **kwargs)
        captured.update(json.loads(rendered))
        return rendered

    class StopBeforeTransport(Exception):
        pass

    def stop(_request):
        raise StopBeforeTransport()

    monkeypatch.setattr(adapter, "_prompt", capture)
    monkeypatch.setattr(adapter, "_request_deadline_remaining", stop)
    with pytest.raises(adapter.AgentHostError) as stopped:
        host.start(request, model="stub-model", provider_environment={},
                   bind_process_group=lambda binding: None, tool_workspace=workspace)
    assert isinstance(stopped.value.__cause__, StopBeforeTransport)
    record = captured["rendered_inputs"][0]
    assert "content_utf8" not in record and "content_base64" not in record
    actual = workspace.input_root / record["path"].removeprefix("/inputs/")
    assert actual.read_bytes() == request.input_artifacts[0].content_bytes()
