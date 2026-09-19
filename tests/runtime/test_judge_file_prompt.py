"""Large frozen inputs stay complete on disk, not twice in prompt."""
import json
from dataclasses import replace
import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import _prompt
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from test_debate_aci14 import judge_request
from test_final_report import report_request
from test_observer_aci14 import observer_request


_RETAINED = "retained-source-bytes"


def test_full_cc_judge_advertises_exact_file_without_inlining_recursive_rows():
    request, _ = judge_request()
    item = request.input_artifacts[0]
    context = json.loads(item.content_bytes())
    context["round_attempts"][0]["preserved_test_context"] = _RETAINED * 20000
    large = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(context).encode())
    request = replace(request, input_artifacts=(large,), max_input_bytes=4 * 1024 * 1024)
    before = request.to_dict()
    payload = json.loads(_prompt(request, tool_workspace=True))
    assert len(json.dumps(payload)) < 20000
    assert _RETAINED not in json.dumps(payload)
    record = payload["rendered_inputs"][0]
    assert record["ref"] == item.ref and record["path"] == "/inputs/0000.txt"
    assert record["size_bytes"] == len(large.content_bytes())
    assert "all six" in payload["instruction"] and "Bash" in payload["instruction"]
    assert request.to_dict() == before


def _observer_offload_request():
    request = observer_request()
    item = request.input_artifacts[0]
    context = json.loads(item.content_bytes())
    context["preserved_test_context"] = _RETAINED * 20000
    large = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(context).encode())
    return replace(request, input_artifacts=(large,), max_input_bytes=4 * 1024 * 1024)


def _final_report_offload_request():
    request = report_request()
    item = request.input_artifacts[0]
    context = json.loads(item.content_bytes())
    context["preserved_test_context"] = _RETAINED * 20000
    large = EmbeddedInput.from_bytes(item.ref, item.media_type, json.dumps(context).encode())
    return replace(request, input_artifacts=(large,), max_input_bytes=4 * 1024 * 1024)


def _proposal_offload_request():
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    acceptance = EmbeddedInput.from_bytes("run-fake--shared-acceptance", "application/json",
        json.dumps({"criterion": "shared", "preserved_test_context": _RETAINED * 20000}).encode())
    grounding = EmbeddedInput.from_bytes("agent-19ddafda-1--response", "application/json",
        json.dumps({"typed_candidate": {"background": _RETAINED * 20000}}).encode())
    inputs = (acceptance, grounding)
    return replace(request, schema_version=14, input_artifacts=inputs,
        immutable_context_refs=(*request.immutable_context_refs, *(item.ref for item in inputs)),
        max_input_bytes=4 * 1024 * 1024)


def _execution_offload_request():
    request = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    config = EmbeddedInput.from_bytes(
        f"{request.component_id}--expert-config",
        "application/json",
        json.dumps(
            {
                "execution": {"runtime_prefix": None},
                "required_experiment_spec": {
                    "metric": {"name": "score", "direction": "MAXIMIZE", "target": "1"},
                    "evaluator_version": "component-result/7",
                    "artifact_requirements": ["result.json"],
                },
            },
            separators=(",", ":"),
        ).encode(),
    )
    design = EmbeddedInput.from_bytes("agent-design-1--response", "application/json",
        json.dumps({"typed_candidate": {"method": _RETAINED}, "preserved_test_context": _RETAINED * 20000}).encode())
    inputs = (config, design)
    return replace(request, schema_version=14, input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
        max_input_bytes=4 * 1024 * 1024)


def _assert_offloaded(request, offloaded_refs):
    before = request.to_dict()
    payload = json.loads(_prompt(request, tool_workspace=True))
    assert len(json.dumps(payload)) < 20000
    assert _RETAINED not in json.dumps(payload)
    assert "complete frozen source is at that advertised path" in payload["instruction"]
    assert "do not accept a truncated Read as complete" in payload["instruction"]
    for index, (item, rendered) in enumerate(
            zip(request.input_artifacts, payload["rendered_inputs"], strict=True)):
        if item.ref in offloaded_refs:
            assert rendered == {
                "ref": item.ref,
                "media_type": item.media_type,
                "path": f"/inputs/{index:04d}.txt",
                "size_bytes": len(item.content_bytes()),
            }
        else:
            assert "path" not in rendered
            assert rendered["content_utf8"] == item.content_bytes().decode("utf-8")
    assert request.to_dict() == before
    inline = json.loads(_prompt(request, tool_workspace=False))
    assert all("path" not in item for item in inline["rendered_inputs"])
    assert "complete frozen source is at that advertised path" not in inline["instruction"]


def test_full_cc_observer_offloads_interaction_context_to_its_exact_file():
    request = _observer_offload_request()
    _assert_offloaded(request, {request.input_artifacts[0].ref})


def test_full_cc_final_report_offloads_report_context_without_inline_content():
    request = _final_report_offload_request()
    _assert_offloaded(request, {request.input_artifacts[0].ref})


def test_full_cc_proposal_offloads_shared_acceptance_and_large_responses():
    request = _proposal_offload_request()
    _assert_offloaded(request, {item.ref for item in request.input_artifacts})


def test_full_cc_execution_offloads_large_prior_design_response():
    request = _execution_offload_request()
    _assert_offloaded(request, {request.input_artifacts[1].ref})


def _assert_start_uses_prepared_input_files(request, tmp_path, monkeypatch, manager=None):
    import hypoloom.claude_agent_host as adapter
    from test_agent_sessions import _manager
    from test_claude_agent_host import _host

    request = replace(request, required_adapter_version=adapter.CLAUDE_AGENT_HOST_VERSION,
                      required_model_identity="stub-model",
                      tool_permission_profile=adapter.CLAUDE_AGENT_TOOL_PERMISSION_PROFILE)
    workspace = (manager or _manager(tmp_path)).prepare(request, None)
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
    items = {item.ref: item for item in request.input_artifacts}
    manifests = [record for record in captured["rendered_inputs"] if "path" in record]
    assert manifests
    for record in manifests:
        assert "content_utf8" not in record and "content_base64" not in record
        actual = workspace.input_root / record["path"].removeprefix("/inputs/")
        assert actual.read_bytes() == items[record["ref"]].content_bytes()


def test_real_start_uses_the_prepared_judge_input_file(tmp_path, monkeypatch):
    request, _ = judge_request()
    _assert_start_uses_prepared_input_files(request, tmp_path, monkeypatch)


def test_real_start_uses_the_prepared_observer_input_file(tmp_path, monkeypatch):
    _assert_start_uses_prepared_input_files(_observer_offload_request(), tmp_path, monkeypatch)


def test_real_start_uses_the_prepared_final_report_input_file(tmp_path, monkeypatch):
    _assert_start_uses_prepared_input_files(_final_report_offload_request(), tmp_path, monkeypatch)


def test_real_start_uses_the_prepared_proposal_input_files(tmp_path, monkeypatch):
    _assert_start_uses_prepared_input_files(_proposal_offload_request(), tmp_path, monkeypatch)


def test_real_start_uses_the_prepared_execution_input_file(tmp_path, monkeypatch):
    from test_agent_sessions import _manager, _seed

    manager = _manager(tmp_path)
    _seed(manager, None)
    _assert_start_uses_prepared_input_files(
        _execution_offload_request(), tmp_path, monkeypatch, manager=manager)
