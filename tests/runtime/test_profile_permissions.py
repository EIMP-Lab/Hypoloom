import json
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_argv
from hypoloom.profile_v4 import project_profile_v4, resolve_node_runtime
from test_full_cc_runtime import _workspace
from test_profile_v4 import documents


def test_profile_regex_policy_is_validated_before_admission():
    source = documents()
    source["safety"]["bash"] = {"deny_patterns": ["[unclosed"]}
    assert any(issue.location == "safety.yaml.bash.deny_patterns" for issue in project_profile_v4(**source).issues)


def test_frozen_node_carries_bash_policy_and_launches_native_readonly_hook(tmp_path):
    source = documents(); source["safety"]["bash"] = {"deny_patterns": [r"touch .*denied", "git push"], "network_allowed": False}
    projection = project_profile_v4(**source)
    runtime = resolve_node_runtime({"methodology": {"controls": {"product": projection.settings}}}, "scientist", "inner_loop.code")
    assert runtime["safety"]["bash"] == source["safety"]["bash"]
    request = replace(default_request(), input_artifacts=(EmbeddedInput.from_bytes("node--expert-config", "application/json",
        json.dumps({"node_runtime": runtime}).encode()),))
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude", request=request,
        model="test", workspace=_workspace(tmp_path), provider_socket=tmp_path / "provider.sock", host_socket=None,
        output_schema={}, extra_paths=())
    settings = json.loads(argv[argv.index("--settings") + 1])
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Bash"
    assert "/runtime/profile_permissions.py" in hook["hooks"][0]["command"]
    assert any(flag == "--ro-bind" and dest == "/runtime/profile_permissions.py" for flag, _, dest in zip(argv, argv[1:], argv[2:]))


@pytest.mark.parametrize("command,denied", [("touch /workspace/denied", True),
    ("echo safe\ntouch /workspace/denied", True), ("git push origin branch", True),
    ("python3 -m pytest", False), ("git diff", False)])
def test_native_decision_preserves_regex_semantics_and_only_denies_matches(command, denied):
    from hypoloom.profile_permissions import permission_decision
    result = permission_decision({"tool_name": "Bash", "tool_input": {"command": command}}, [r"touch .*denied", r"git\s+push"])
    assert (result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny") is denied


def test_malformed_hook_input_denies_without_echoing_command_or_secrets():
    from hypoloom.profile_permissions import permission_decision
    result = permission_decision({"tool_name": "Bash", "tool_input": {"command": {"secret": "secret-value"}}}, ["x"])
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "secret-value" not in json.dumps(result)
