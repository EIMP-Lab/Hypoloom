import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from hypoloom.hook_safety import is_write_allowed, is_destructive_command_safe, research_shell_command
from tests.runtime.test_hook_isolation import _run_hook


@pytest.mark.parametrize("path", ["hypoloom/bootstrap.py", "tests/test.py", "docs/guide.md", "CLAUDE.md",
                                      ".git/config", ".claude/settings.json", ".conda/bin/python", "new-source.py"])
def test_research_file_tools_cannot_edit_framework(tmp_path, path):
    assert not is_write_allowed(tmp_path / path, repo_root=tmp_path)[0]


def test_private_writes_and_symlinks_use_resolved_authority(tmp_path):
    for path in (".local/answer.json", "profiles/local/topic/goal.yaml"):
        assert is_write_allowed(tmp_path / path, repo_root=tmp_path)[0]
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/alias").symlink_to(tmp_path, target_is_directory=True)
    assert not is_write_allowed(tmp_path / ".local/alias/hypoloom.py", repo_root=tmp_path)[0]
    assert not is_write_allowed(tmp_path / "state/run/state.sqlite", repo_root=tmp_path)[0]


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
def test_shell_hook_wraps_original_text_and_preserves_native_tool_fields(tmp_path, tool):
    command = "python -c 'print(\"a|b; \\\"quoted\\\"\")'\nprintf 'next\\n'"
    result = _run_hook(tmp_path, "dispatch.py", args=("pre",), payload={"tool_name": tool,
        "tool_input": {"command": command, "timeout": 20000, "run_in_background": True}})
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout.splitlines()[0])["hookSpecificOutput"]
    assert "permissionDecision" not in output, "wrapping must not approve a user's command"
    updated = output["updatedInput"]
    argv = shlex.split(updated["command"])
    assert argv[0] == "/usr/bin/bwrap" and argv[-3:] == ["/bin/bash", "-c", command]
    assert updated["timeout"] == 20000 and updated["run_in_background"] is True


def test_quoted_shell_program_is_parsed_before_separators(tmp_path):
    assert is_destructive_command_safe("python -c 'print(\"a|b\")'", repo_root=tmp_path)[0]
    assert not is_destructive_command_safe("printf 'a|b'; rm /etc/source", repo_root=tmp_path)[0]
    assert not is_destructive_command_safe("bash -c 'rm /etc/source'", repo_root=tmp_path)[0]


def test_writable_mount_cannot_alias_framework(tmp_path):
    (tmp_path / ".local").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        research_shell_command("true", repo_root=tmp_path)


@pytest.mark.skipif(os.environ.get("HYPOLOOM_RESEARCH_SHELL_TEST") != "1", reason="opt-in Linux mount enforcement")
def test_real_shell_allows_private_outputs_and_denies_multiple_source_write_paths(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    source = tmp_path / "hypoloom/source.py"
    source.parent.mkdir(); source.write_text("original\n")
    def run(command):
        wrapped = research_shell_command(command, repo_root=tmp_path)
        return subprocess.run(shlex.split(wrapped), capture_output=True, text=True, timeout=15)
    positive = run("printf local > .local/output; printf profile > profiles/local/output; printf state > state/output; cat hypoloom/source.py")
    assert positive.returncode == 0, positive.stderr
    assert positive.stdout == "original\n"
    git = run("git rev-parse --show-toplevel")
    assert git.returncode == 0 and git.stdout.strip() == str(tmp_path), git.stderr
    for path, value in [(".local/output", "local"), ("profiles/local/output", "profile"), ("state/output", "state")]:
        assert (tmp_path / path).read_text() == value
    for command in ["printf changed > hypoloom/source.py", "sed -i s/original/changed/ hypoloom/source.py",
                    "cp .local/output hypoloom/source.py", "mv .local/output hypoloom/source.py",
                    shlex.join([sys.executable, "-B", "-c", "from pathlib import Path; Path('hypoloom/source.py').write_text('changed')"])]:
        result = run(command)
        assert result.returncode != 0, command
        assert source.read_text() == "original\n"
