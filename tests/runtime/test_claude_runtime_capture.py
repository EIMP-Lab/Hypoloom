"""Adapter capture on its real gated/bubblewrap credential-free child boundary."""

import json
import time

from dataclasses import replace

from hypoloom.agent_aci import AgentCommand, EmbeddedInput, canonical_json
from hypoloom.agent_sessions import AgentSessions
from hypoloom.artifacts import ArtifactPublisher
from hypoloom.claude_agent_host import (
    AgentHostTerminalReason,
    ClaudeAgentHost,
    _prompt,
    _provider_response,
)
from hypoloom.execution_workspace import GitTargetBinding
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import FakeAgent, default_request
from tests.runtime.test_claude_agent_host import _claude_pincer_request, _claude_request, _host
from tests.runtime.test_claude_file_tool_cli import _git
from tests.runtime.test_full_cc_adapter import _MODEL, _request


def _prepare(tmp_path, *, hang=False):
    target = tmp_path / "target"
    target.mkdir()
    (target / "model.py").write_text("x = 1\n")
    _git(target, "init", "-q")
    _git(target, "add", "model.py")
    _git(target, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-qm", "test source")
    state = tmp_path / "state"
    state.mkdir()
    request = _request(_claude_pincer_request(ResearchNodeSubkind.SYNTHESIS), "capture", None)
    workspace = AgentSessions(state, ArtifactPublisher(state / "artifacts")).prepare(
        request, GitTargetBinding(checkout=target, commit=_git(target, "rev-parse", "HEAD")))
    candidate = FakeAgent().invoke(request)
    document = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                "total_cost_usd": 0.5, "usage": {"input_tokens": 3, "output_tokens": 4},
                "modelUsage": {_MODEL: {}}, "structured_output": {
                    "typed_candidate": candidate.typed_candidate,
                    "declared_effects": [{"effect": item.effect} for item in candidate.declared_effects]}}
    document["result"] = json.dumps(document.pop("structured_output"))
    raw = json.dumps(document).encode() + b"\n"
    stub = tmp_path / "credential-free-cli"
    stub.write_text("#!/usr/bin/python3\nimport json,os,shlex,subprocess,sys,time\nfrom pathlib import Path\n"
        "sys.stdin.buffer.read()\n"
        "assert 'ANTHROPIC_AUTH_TOKEN' not in os.environ\n"
        f"assert not Path({str(state)!r}).exists()\n"
        "settings=json.loads(sys.argv[sys.argv.index('--settings')+1])\n"
        "hook=settings['hooks']['Stop'][0]['hooks'][0]['command']\n"
        f"payload={{'session_id':{workspace.binding['session_id']!r},'last_assistant_message':{document['result']!r}}}\n"
        "validated=subprocess.run(shlex.split(hook),input=json.dumps(payload).encode(),capture_output=True,check=True)\n"
        "assert json.loads(validated.stdout)=={}\n"
        f"os.write(1, {raw!r})\nos.write(2,b'CLI diagnostic context\\n')\n"
        + ("time.sleep(60)\n" if hang else ""))
    stub.chmod(0o700)
    host = ClaudeAgentHost(claude_executable=stub)
    return state, request, workspace, host, raw


def test_full_cc_retains_actual_launch_schema_prompt_and_unmodified_parser_input(tmp_path):
    state, request, workspace, host, raw = _prepare(tmp_path)
    expected_response, expected_failure, _ = _provider_response(request, _MODEL, raw, final_text_output=True)
    handle = host.start(request, model=_MODEL,
        provider_environment={"ANTHROPIC_AUTH_TOKEN": "never-in-child-key"},
        bind_process_group=lambda _: None, tool_workspace=workspace, diagnostics_root=state)
    observed = host.wait(handle)
    root = state / "diagnostics/agents" / request.run_id / request.attempt_id
    assert observed.response == expected_response
    assert observed.failure_code == expected_failure
    assert (root / "stdout.log").read_bytes() == raw
    assert (root / "stderr.log").read_bytes() == b"CLI diagnostic context\n"
    expected_manifest: dict[str, object] = {}
    assert (root / "prompt.txt").read_bytes() == _prompt(
        request, tool_workspace=True, git_authoring=True, manifest=expected_manifest)
    assert observed.diagnostics["prompt_bytes"] == (root / "prompt.txt").stat().st_size
    assert observed.diagnostics["rendered_input_bytes"] == expected_manifest["rendered_input_bytes"]
    assert observed.diagnostics["offloaded_refs"] == expected_manifest["offloaded_refs"] == []
    launch = json.loads((root / "launch.json").read_text())
    assert launch["argv"] == list(handle.launch_argv)
    assert "--json-schema" not in launch["argv"]
    instruction = json.loads((root / "prompt.txt").read_text())["instruction"]
    supplied = instruction.split("HYPOLOOM_OUTPUT_SCHEMA_JSON\n", 1)[1].split(
        "\nEND_HYPOLOOM_OUTPUT_SCHEMA_JSON", 1)[0]
    assert json.loads(supplied) == launch["output_schema"]
    validation = json.loads((root / "native-output/000001.json").read_text())
    assert validation["last_assistant_message"] == json.loads(raw)["result"]
    assert validation["error"] is None
    assert json.loads((root / "request.json").read_text()) == request.to_dict()
    assert json.loads((root / "result.json").read_text())["capture"]["capture_errors"] == []
    assert [json.loads(line)["kind"] for line in (root / "events.jsonl").read_text().splitlines()] == ["prepared", "started", "ended"]
    assert all(b"never-in-child-key" not in path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_forced_stop_keeps_written_stdio_and_names_unknown_completion(tmp_path):
    state, request, workspace, host, raw = _prepare(tmp_path, hang=True)
    handle = host.start(request, model=_MODEL,
        provider_environment={"ANTHROPIC_AUTH_TOKEN": "never-in-child-key"},
        bind_process_group=lambda _: None, tool_workspace=workspace, diagnostics_root=state)
    root = state / "diagnostics/agents" / request.run_id / request.attempt_id
    try:
        deadline = time.monotonic() + 10
        while (root / "stdout.log").stat().st_size == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (root / "stdout.log").read_bytes() == raw
        assert not (root / "result.json").exists()
        host.cancel(handle)
        observed = host.wait(handle)
        assert observed.failure_code == "cancelled"
        result = json.loads((root / "result.json").read_text())
        assert result["forced_reason"] == "CANCELLED"
        assert (root / "stdout.log").read_bytes() == raw
    finally:
        host.cleanup(handle)


def test_legacy_capture_is_explicitly_unavailable(tmp_path):
    host = _host(tmp_path)
    request = _claude_request()
    state = tmp_path / "state"
    handle = host.start(request, model="stub-model",
        provider_environment={"STUB_MODE": "malformed", "STUB_VALID_OUTPUT": ""},
        bind_process_group=lambda _: None, diagnostics_root=state)
    host.wait(handle)
    root = state / "diagnostics/agents" / request.run_id / request.attempt_id
    assert not (root / "stdout.log").exists()
    event = json.loads((root / "events.jsonl").read_text())
    assert event["kind"] == "capture_unavailable"
    assert "credential-free" in event["reason"]


def test_prompt_manifest_maps_each_rendered_input_to_its_prompt_size():
    suffix = EmbeddedInput.from_bytes("run-fake--shared-acceptance", "text/plain", b"s" * 2048)
    big = EmbeddedInput.from_bytes("agent-19ddafda-1--response", "text/plain", b"b" * (70 * 1024))
    base = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    inputs = (suffix, big)
    request = replace(base, schema_version=14, input_artifacts=inputs,
        immutable_context_refs=(suffix.ref, big.ref), max_input_bytes=4 * 1024 * 1024)
    manifest: dict[str, object] = {}
    prompt = _prompt(request, tool_workspace=True, git_authoring=True, manifest=manifest)
    rendered = json.loads(prompt)["rendered_inputs"]
    assert manifest["rendered_input_bytes"] == {
        item["ref"]: len(canonical_json(item)) for item in rendered}
    assert manifest["offloaded_refs"] == [suffix.ref, big.ref]
    advertised = next(item for item in rendered if item["ref"] == big.ref)
    assert advertised["size_bytes"] == 70 * 1024 and "content_utf8" not in advertised
    assert manifest["rendered_input_bytes"][big.ref] < 1024


def _oversize_prepare(tmp_path, request):
    target = tmp_path / "target"
    target.mkdir()
    (target / "model.py").write_text("x = 1\n")
    _git(target, "init", "-q")
    _git(target, "add", "model.py")
    _git(target, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-qm", "test source")
    state = tmp_path / "state"
    state.mkdir()
    workspace = AgentSessions(state, ArtifactPublisher(state / "artifacts")).prepare(
        request, GitTargetBinding(checkout=target, commit=_git(target, "rev-parse", "HEAD")))
    document = {"type": "result", "subtype": "success", "is_error": True,
                "result": "Prompt is too long", "num_turns": 1,
                "total_cost_usd": 0.5, "usage": {"input_tokens": 3, "output_tokens": 4},
                "modelUsage": {_MODEL: {}}}
    raw = json.dumps(document).encode() + b"\n"
    stub = tmp_path / "oversize-refusing-cli"
    stub.write_text("#!/usr/bin/python3\nimport os,sys\nsys.stdin.buffer.read()\n"
        f"os.write(1, {raw!r})\nos.write(2, b'Prompt is too long\\n')\nraise SystemExit(1)\n")
    stub.chmod(0o700)
    return state, workspace, ClaudeAgentHost(claude_executable=stub)


def test_cli_oversize_stateless_attempt_is_terminal_on_first_try(tmp_path):
    from hypoloom.agent_context import agent_failure_stage, agent_retry_class
    request = _request(default_request(AgentCommand.OBSERVER), "observer-oversize", None)
    state, workspace, host = _oversize_prepare(tmp_path, request)
    handle = host.start(request, model=_MODEL,
        provider_environment={"ANTHROPIC_AUTH_TOKEN": "never-in-child-key"},
        bind_process_group=lambda _: None, tool_workspace=workspace, diagnostics_root=state)
    observed = host.wait(handle)
    assert observed.terminal_reason is AgentHostTerminalReason.FAILED
    assert observed.failure_code == "nonzero_exit"
    assert observed.diagnostics["provider_diagnostic"] == "Prompt is too long"
    assert observed.diagnostics["provider_failure"] == {
        "code": "input_too_long", "http_status": None, "source": "cli_compaction"}
    assert "provider_failure_hint" not in observed.diagnostics
    details = {"diagnostics": observed.diagnostics}
    assert agent_retry_class(observed.failure_code, details, retryable=True) is None
    assert agent_failure_stage(observed.failure_code, details) == "provider_admission"
    root = state / "diagnostics/agents" / request.run_id / request.attempt_id
    result = json.loads((root / "result.json").read_text())
    assert result["parser_diagnostics"]["provider_failure"]["source"] == "cli_compaction"
    assert observed.diagnostics["prompt_bytes"] == (root / "prompt.txt").stat().st_size


def test_cli_oversize_stateful_attempt_keeps_resume_retry(tmp_path):
    from hypoloom.agent_context import agent_failure_stage, agent_retry_class
    request = _request(_claude_pincer_request(ResearchNodeSubkind.SYNTHESIS), "capture", None)
    state, workspace, host = _oversize_prepare(tmp_path, request)
    handle = host.start(request, model=_MODEL,
        provider_environment={"ANTHROPIC_AUTH_TOKEN": "never-in-child-key"},
        bind_process_group=lambda _: None, tool_workspace=workspace, diagnostics_root=state)
    observed = host.wait(handle)
    assert observed.terminal_reason is AgentHostTerminalReason.FAILED
    assert observed.failure_code == "nonzero_exit"
    assert observed.diagnostics["provider_diagnostic"] == "Prompt is too long"
    assert "provider_failure" not in observed.diagnostics
    assert observed.diagnostics["provider_failure_hint"] == {
        "code": "input_too_long", "source": "cli_compaction"}
    details = {"diagnostics": observed.diagnostics}
    assert agent_retry_class(observed.failure_code, details, retryable=True) == "INFRASTRUCTURE"
    assert agent_failure_stage(observed.failure_code, details) == "provider_process"
