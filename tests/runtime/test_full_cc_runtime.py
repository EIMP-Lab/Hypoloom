"""Per-request Profile mounts do not become adapter-global or Host authority."""

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import json

import pytest

from hypoloom.fake_agent import default_request
from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.domain import AgentCommand
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_environment, full_cc_instruction


def test_unbounded_cost_omits_native_flag_but_keeps_turn_limit(tmp_path):
    request = default_request()
    request = replace(request, schema_version=19, budget=replace(request.budget, max_cost=None))
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude",
        request=request, model="claude-sonnet-4-6", workspace=_workspace(tmp_path),
        provider_socket=tmp_path / "provider.sock", host_socket=None, output_schema={}, extra_paths=())
    assert "--max-budget-usd" not in argv
    assert argv[argv.index("--max-turns") + 1] == str(request.budget.max_turns)


@pytest.mark.parametrize("command", (AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION))
@pytest.mark.parametrize("role,plugin_name", (("alpha", "hypoloom-alpha"), ("GPU_Expert", "hypoloom-gpu-expert")))
def test_resource_skill_prompt_uses_the_frozen_plugin_name(command, role, plugin_name):
    request = default_request(command)
    task = replace(request.task_context, key=replace(request.task_context.key, expert_slot=role))
    binding = {"run_id": request.run_id, "role": role, "name": plugin_name, "commit": "a" * 40}
    request = replace(request, schema_version=18, role=role, task_context=task, command_id=task.command_id,
        input_artifacts=(EmbeddedInput.from_bytes("node--expert-resources", "application/json",
            json.dumps({"plugin": binding}).encode()),))
    instruction = full_cc_instruction(request)
    assert f"Your bound native resource Skill is `{plugin_name}:resource-planning`" in instruction
    assert "only if it appears in the available Skill list" in instruction
    assert "Retained plugins may omit this entrypoint" in instruction
    assert "Before editing code or submitting an experiment in this node" in instruction
    assert f"If `{plugin_name}:resource-planning` is listed, you must invoke it now" in instruction
    assert "A prior node's Skill call or injected guidance does not replace this node's load" in instruction
    assert "then query mcp__host-tools__get_resources" in instruction
    assert "Host-injected resource guidance is not a callable Skill" in instruction
    assert "experiment-resource-planning" not in instruction
    assert "hypoloom-researcher:" not in instruction
    if "_" in role:
        assert f"`hypoloom-{role}:resource-planning`" not in instruction
    retained = full_cc_instruction(replace(request, schema_version=17))
    assert "bound native resource Skill" not in retained


@pytest.mark.parametrize("command", (AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, AgentCommand.EXPERIMENT_EXECUTION))
def test_resource_guidance_without_a_plugin_binding_does_not_invent_a_skill(command):
    instruction = full_cc_instruction(replace(default_request(command), schema_version=18))
    assert "No native resource Skill binding was supplied" in instruction
    assert ":resource-planning" not in instruction
    assert "mcp__host-tools__get_resources" in instruction


@pytest.mark.parametrize("command", (AgentCommand.GROUNDING, AgentCommand.RESULT_ANALYSIS,
    AgentCommand.SELF_EVOLVE))
def test_resource_action_instructions_are_scoped_to_coding_nodes(command):
    request = default_request(command)
    instruction = full_cc_instruction(replace(request, schema_version=18,
        required_output_schema=required_output_schema(command, request.task_context, schema_version=18)))
    assert "Follow only the actions for this node" in instruction
    assert "you must invoke it now" not in instruction
    assert "bound native resource Skill" not in instruction


def test_full_cc_shell_can_grep_in_the_actual_readonly_runtime():
    import shutil
    import subprocess
    from hypoloom.full_cc_runtime import runtime_mounts

    bwrap = shutil.which("bwrap")
    if not bwrap:
        pytest.skip("bubblewrap is unavailable")
    argv = [bwrap, "--unshare-all", "--die-with-parent",
            "--setenv", "PATH", "/usr/bin:/bin", "--tmpfs", "/tmp"]
    for source, destination in runtime_mounts(()):
        argv.extend(("--ro-bind", str(source), destination))
    argv.extend(("--", "/bin/bash", "-c", "printf 'report-ready\\n' | grep -Fx report-ready"))
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15,
                            env={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == "report-ready\n"


def test_declared_measurement_prompt_describes_the_actual_target_overlay():
    from hypoloom.domain import AgentCommand
    from test_claude_measurement_capture import _deferred

    request = _deferred(default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT))
    instruction = full_cc_instruction(request)
    assert "The Host applies your captured patch" in instruction
    assert "HYPOLOOM_TARGET_ROOT" in instruction
    assert "Own patches are not automatically applied" not in instruction


def test_current_preset_prompt_distinguishes_own_target_from_explicit_upstream_contract():
    from hypoloom.claude_agent_host import _prompt
    from test_claude_agent_host import _claude_expert_design_request

    # The native final-text prompt embeds the full schema and requires the real
    # admitted experiment contract even when testing only instruction wording.
    request = replace(_claude_expert_design_request(), schema_version=14)
    instruction = full_cc_instruction(request)
    assert "research-v9" in instruction
    assert "target_overlay_policy=latest" in instruction
    assert "HYPOLOOM_TARGET_ROOT" in instruction
    assert "experiment.py must be self-contained" not in instruction
    complete = json.loads(_prompt(request, tool_workspace=True, git_authoring=True))["instruction"]
    assert "research-v9" in complete
    assert "implementation.patch is NOT applied" not in complete
    assert "experiment.py must be self-contained" not in complete


def test_retained_preset_prompt_keeps_its_predecessor_execution_boundary():
    from hypoloom.claude_agent_host import _prompt
    from test_claude_agent_host import _claude_expert_design_request

    request = replace(_claude_expert_design_request(), schema_version=13)
    complete = json.loads(_prompt(request, tool_workspace=True, git_authoring=True))["instruction"]
    assert "implementation.patch is NOT applied" in complete
    assert "experiment.py must be self-contained" in complete
    assert "research-v9" not in complete


def _workspace(root: Path) -> SimpleNamespace:
    return SimpleNamespace(state_root=root / "state", session_root=root / "session",
        worktree_root=root / "worktree", input_root=root / "inputs", writable=True,
        session_arguments=("--session-id", "test-session"))


def _argv(root: Path, **kwargs: object) -> tuple[str, ...]:
    return full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=root / "claude",
        request=default_request(), model="claude-sonnet-4-6", workspace=_workspace(root),
        provider_socket=root / "provider.sock", host_socket=None,
        output_schema={}, extra_paths=(), **kwargs)


def test_profile_runtime_and_data_are_request_scoped_readonly_mounts(tmp_path: Path) -> None:
    # An actual standalone environment; no package installation or paid provider.
    import sys

    runtime = Path(sys.prefix).resolve()
    data = tmp_path / "data"
    data.mkdir()
    argv = _argv(tmp_path, runtime_prefix=runtime, read_roots=(data,))
    triples = tuple(zip(argv, argv[1:], argv[2:]))
    assert ("--ro-bind", str(runtime), str(runtime)) in triples
    assert ("--ro-bind", str(data), str(data)) in triples
    assert ("--bind", str(runtime), str(runtime)) not in triples
    assert ("--bind", str(data), str(data)) not in triples
    environment = full_cc_environment(runtime_prefix=runtime, read_roots=(data,))
    assert environment["PATH"].split(":")[0] == str(runtime / "bin")
    assert environment["HYPOLOOM_READ_ROOTS"] == f'["{data}"]'
    assert "ANTHROPIC_AUTH_TOKEN" not in environment
    # Reusing the adapter must not leak the preceding run's paths into the next.
    assert str(data) not in _argv(tmp_path)
    assert str(runtime) not in _argv(tmp_path)
    assert str(runtime) not in full_cc_environment()["PATH"]


@pytest.mark.parametrize("mount_kind", ["state", "state-parent", "settings", "reserved"])
def test_profile_mount_cannot_expose_host_authority_or_shadow_sandbox(
    tmp_path: Path, mount_kind: str,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    settings = tmp_path / "provider-settings" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"env":{"ANTHROPIC_AUTH_TOKEN":"not-for-agent"}}')
    roots = {"state": state, "state-parent": tmp_path,
        "settings": settings.parent, "reserved": Path("/tmp")}
    with pytest.raises(ValueError, match="mount"):
        _argv(tmp_path, read_roots=(roots[mount_kind],), protected_paths=(settings,))


def test_missing_profile_mount_fails_before_process_launch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mount"):
        _argv(tmp_path, read_roots=(tmp_path / "missing-data",))


@pytest.mark.parametrize("effort", ("low", "medium", "high", "xhigh", "max"))
def test_frozen_node_effort_and_comment_language_reach_native_launch(tmp_path: Path, effort: str) -> None:
    request = replace(default_request(), input_artifacts=(EmbeddedInput.from_bytes(
        "node--expert-config", "application/json", json.dumps({"node_runtime": {
            "effort": effort, "code_comment_language": "Chinese"}}).encode()),))
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude",
        request=request, model="claude-sonnet-4-6", workspace=_workspace(tmp_path),
        provider_socket=tmp_path / "provider.sock", host_socket=None,
        output_schema={}, extra_paths=())
    assert argv[argv.index("--effort") + 1] == effort
    assert "Chinese" in full_cc_instruction(request)
    assert "--effort" not in _argv(tmp_path)


def test_unknown_effort_fails_before_launch(tmp_path: Path) -> None:
    request = replace(default_request(), input_artifacts=(EmbeddedInput.from_bytes(
        "node--expert-config", "application/json", b'{"node_runtime":{"effort":"unknown"}}'),))
    with pytest.raises(ValueError, match="effort"):
        full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude",
            request=request, model="claude-sonnet-4-6", workspace=_workspace(tmp_path),
            provider_socket=tmp_path / "provider.sock", host_socket=None,
            output_schema={}, extra_paths=())


def _protected_argv(tmp_path: Path, paths: object) -> tuple[str, ...]:
    request = replace(default_request(), input_artifacts=(EmbeddedInput.from_bytes(
        "node--expert-config", "application/json", json.dumps({"node_runtime": {
            "safety": {"filesystem": {"protected_paths": paths}}}}).encode()),))
    return full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude",
        request=request, model="claude-sonnet-4-6", workspace=_workspace(tmp_path),
        provider_socket=tmp_path / "provider.sock", host_socket=None,
        output_schema={}, extra_paths=())


def test_frozen_protected_files_and_directories_are_readonly_without_external_mounts(tmp_path: Path):
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text("gitdir: /not-visible\n")
    (root / "data").mkdir()
    (root / "file.txt").write_text("protected")
    external = tmp_path / "not-admitted"
    external.mkdir()
    argv = _protected_argv(tmp_path, ["data/", ".git/", "/workspace/file.txt", str(external)])
    triples = tuple(zip(argv, argv[1:], argv[2:]))
    for name in ("data", ".git", "file.txt"):
        assert ("--ro-bind", str(root / name), "/workspace/" + name) in triples
    assert str(external) not in argv


@pytest.mark.parametrize("path", ["missing/", "../escape", "/workspace/../escape", "link/file", "data//file", ""])
def test_bad_protected_path_fails_before_launch_instead_of_silently_allowing_writes(tmp_path: Path, path: str):
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="protected.*path|protected_paths"):
        _protected_argv(tmp_path, [path])


@pytest.mark.parametrize("paths", ["data/", 1, None, [1]])
def test_protected_paths_requires_the_typed_frozen_list(tmp_path: Path, paths: object):
    with pytest.raises(ValueError, match="protected.path"):
        _protected_argv(tmp_path, paths)


@pytest.mark.parametrize("allowed", [True, False])
def test_network_socket_and_public_ca_follow_frozen_bash_permission(tmp_path, allowed):
    request = replace(default_request(), input_artifacts=(EmbeddedInput.from_bytes(
        "node--expert-config", "application/json", json.dumps({"node_runtime": {
            "safety": {"bash": {"network_allowed": allowed}}}}).encode()),))
    kwargs = dict(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude", request=request,
        model="claude-sonnet-4-6", workspace=_workspace(tmp_path), provider_socket=tmp_path / "provider.sock",
        host_socket=None, output_schema={}, extra_paths=())
    argv = full_cc_argv(**kwargs, network_socket=tmp_path / "network.sock" if allowed else None)
    assert "--unshare-all" in argv and "--share-net" not in argv
    assert ("/network.sock" in argv) is allowed
    assert ("/runtime/network_proxy.py" in argv) is allowed
    assert ("/runtime/ca-certificates.crt" in argv) is allowed
    if allowed:
        with pytest.raises(ValueError, match="network"):
            full_cc_argv(**kwargs)
    else:
        with pytest.raises(ValueError, match="network"):
            full_cc_argv(**kwargs, network_socket=tmp_path / "foreign.sock")
