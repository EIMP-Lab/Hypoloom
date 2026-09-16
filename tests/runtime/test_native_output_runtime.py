"""Native Stop wiring preserves sandbox, Bash policy and admitted budgets."""

from dataclasses import replace
import json
from pathlib import Path
import shlex

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.fake_agent import default_request
from hypoloom import full_cc_runtime, native_output

from test_full_cc_runtime import _workspace


@pytest.mark.parametrize("cap", [1, 3, 11])
def test_native_stop_block_cap_follows_admitted_correction_cap(cap):
    environment = full_cc_runtime.full_cc_environment(structured_output_failed_validation_cap=cap)
    assert environment["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"] == str(cap)
    assert environment["MAX_STRUCTURED_OUTPUT_RETRIES"] == str(cap)
    assert "ANTHROPIC_AUTH_TOKEN" not in environment


def _argv(tmp_path, *, endpoint=True, patterns=(), max_output_bytes=65536):
    request = default_request()
    request = replace(request, budget=replace(request.budget, max_output_bytes=max_output_bytes, wall_seconds=37.5),
        input_artifacts=(EmbeddedInput.from_bytes("node--expert-config", "application/json",
            json.dumps({"node_runtime": {"safety": {"bash": {"deny_patterns": list(patterns)}}}}).encode()),))
    schema = {"type": "object", "required": ["typed_candidate"]}
    return full_cc_runtime.full_cc_argv(
        bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude", request=request,
        model="mimo-v2.5-pro", workspace=_workspace(tmp_path), provider_socket=tmp_path / "provider.sock",
        host_socket=None, output_socket=tmp_path / "output.sock" if endpoint else None,
        output_schema=schema, extra_paths=()), schema


@pytest.mark.parametrize("patterns", [(), ("forbidden-command",)])
@pytest.mark.parametrize("max_bytes", [65536, 3 * 1024 * 1024])
def test_native_output_socket_hooks_and_budgeted_stdlib_client(tmp_path, patterns, max_bytes):
    argv, _ = _argv(tmp_path, patterns=patterns, max_output_bytes=max_bytes)
    assert "--json-schema" not in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    triples = tuple(zip(argv, argv[1:], argv[2:]))
    for source, destination in ((str(Path(native_output.__file__).resolve()), "/runtime/native_output.py"),
                                (str(tmp_path / "output.sock"), "/output-validation.sock")):
        assert ("--ro-bind", source, destination) in triples
        assert ("--bind", source, destination) not in triples
    settings = json.loads(argv[argv.index("--settings") + 1])
    hook = settings["hooks"]["Stop"][0]["hooks"][0]
    assert hook["type"] == "command" and hook["timeout"] == 37.5
    command = shlex.split(hook["command"])
    assert command == ["/usr/bin/python3", "/runtime/native_output.py", "--socket", "/output-validation.sock",
                       "--max-bytes", str(max(4 * 1024 * 1024, max_bytes * 2)), "--timeout-seconds", "37.5"]
    if patterns:
        bash = settings["hooks"]["PreToolUse"][0]
        assert bash["matcher"] == "Bash"
        assert shlex.split(bash["hooks"][0]["command"])[-1] == json.dumps(list(patterns))
    else:
        assert "PreToolUse" not in settings["hooks"]
    assert argv[argv.index("--tools") + 1] == ",".join(full_cc_runtime.FULL_CC_TOOLS)
    assert "--session-id" in argv and "--unshare-all" in argv


def test_legacy_helper_without_output_endpoint_keeps_native_schema(tmp_path):
    argv, schema = _argv(tmp_path, endpoint=False)
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema
    assert "/runtime/native_output.py" not in argv and "/output-validation.sock" not in argv
    assert "Stop" not in json.loads(argv[argv.index("--settings") + 1]).get("hooks", {})


def test_profile_cannot_shadow_output_validation_socket(tmp_path, monkeypatch):
    root = Path("/output-validation.sock")
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    with pytest.raises(ValueError, match="shadows sandbox"):
        full_cc_runtime._profile_mounts(_workspace(tmp_path), None, (root,), ())


def test_instruction_requires_normal_final_json_without_reducing_capabilities():
    instruction = full_cc_runtime.full_cc_instruction(default_request())
    assert "ordinary final text" in instruction
    assert "one complete JSON object" in instruction
    assert "Read/Write/Edit/Glob/Grep/Bash/Skill" in instruction
    assert "declared_effects" in instruction
    assert "StructuredOutput" not in instruction


def test_design_stop_forwards_to_host_without_agent_parser_mounts(tmp_path):
    from hypoloom.domain import AgentCommand
    from test_claude_measurement_capture import _deferred
    request = _deferred(default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT))
    argv = full_cc_runtime.full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude",
        request=request, model="mimo-v2.5-pro", workspace=_workspace(tmp_path),
        provider_socket=tmp_path / "provider.sock", host_socket=None,
        output_socket=tmp_path / "output.sock", output_schema={}, extra_paths=())
    command = shlex.split(json.loads(argv[argv.index("--settings") + 1])["hooks"]["Stop"][0]["hooks"][0]["command"])
    assert "--measurement-smoke" not in command and "--acceptance-smoke" not in command
    assert not {"/input", "/raw", "/parsed", "/acceptance-smoke", "/runtime/measurement_driver.py"} & set(argv)
    instruction = full_cc_runtime.full_cc_instruction(request)
    assert "/workspace/smoke/<relative-path>" in instruction and "at most 15 seconds" in instruction
    assert "same isolated CPU runtime as production parsing" in instruction
    assert "does not inherit HYPOLOOM_READ_ROOTS" in instruction
