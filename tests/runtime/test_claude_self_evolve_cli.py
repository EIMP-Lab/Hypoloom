"""Native plugin update/next-consumer compatibility, not SELF_EVOLVE Host dispatch."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from hypoloom.agent_aci import AgentBudget
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_argv, full_cc_environment
from hypoloom.provider_relay import ProviderRelay

from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results
from test_claude_session_cli import _MODEL, _SCHEMA


_SELF = "skills/self-evolve/SKILL.md"
_NEW = "skills/measured-repair/SKILL.md"


class _EvolveScenario:
    def __init__(self, original: str, invalid: str):
        self.original, self.invalid = original, invalid
        self.nonce = "learned-native-repair-" + secrets.token_hex(12)
        self.new_skill = ("---\nname: measured-repair\ndescription: Apply the measured parser repair lesson.\n---\n"
            "Read the experimental parser before editing it. Confirm the changed parser with a local test.\n"
            + self.nonce + "\n")
        self.stage, self.step = "invalid", 0
        self.requests: list[Mapping[str, Any]] = []
        self.errors: list[str] = []
        self.evidence: dict[str, str] = {}

    def record(self, document):
        self.requests.append(document)
        self.step += 1
        try:
            text = json.dumps(document)
            tools = {tool["name"] for tool in document["tools"]}
            assert {"Skill", "Read", "Write", "Edit", "Bash"} <= tools
            if self.stage == "consumer" and self.step == 1:
                assert "hypoloom-alpha:measured-repair" in text
                assert self.nonce not in text, "new skill body was supplied before native Skill invocation"
            if self.stage == "old-consumer":
                assert "hypoloom-alpha:measured-repair" not in text
                assert self.nonce not in text
            names = {
                "invalid": {2: "evolve_skill", 3: "read_update", 4: "edit_skill", 5: "invalid_write"},
                "repair": {2: "prepare_repair", 3: "repair_update"},
                "consumer": {2: "new_skill", 3: "use_lesson"},
                "old-consumer": {2: "old_skill"},
            }
            previous = names[self.stage].get(self.step)
            if previous:
                result = _tool_results(document)[previous]
                assert not result.get("is_error"), result
                rendered = _result_text(result)
                self.evidence[previous] = rendered
                if previous == "evolve_skill":
                    assert "Only a SELF_EVOLVE request grants" in text
                if previous == "new_skill":
                    assert self.nonce in text
                if previous == "use_lesson":
                    assert self.nonce in rendered and "PLUGIN_READONLY" in rendered
        except (AssertionError, KeyError) as exc:
            self.errors.append(f"{self.stage}/{self.step}: {exc}")
        return len(self.requests)

    def tool_calls(self, _number):
        if self.errors:
            return ()
        if self.stage == "invalid":
            steps = (
                ("evolve_skill", "Skill", {"skill": "hypoloom-alpha:self-evolve"}),
                ("read_update", "Read", {"file_path": "/workspace/" + _SELF}),
                ("edit_skill", "Edit", {"file_path": "/workspace/" + _SELF,
                    "old_string": self.original,
                    "new_string": self.original + "\nObserved parser repair lesson: " + self.nonce + "\n"}),
                ("invalid_write", "Write", {"file_path": "/workspace/" + ("ILLEGAL.md" if self.invalid == "path" else _NEW),
                    "content": "Outside native skills.\n" if self.invalid == "path" else self.new_skill.replace("name: measured-repair", "name: wrong-name")}),
            )
            if self.step <= len(steps):
                return (steps[self.step - 1],)
        elif self.stage == "repair":
            if self.step == 1:
                return (("prepare_repair", "Bash", {"command": "rm /workspace/ILLEGAL.md", "timeout": 10000}),) if self.invalid == "path" else (
                    ("prepare_repair", "Read", {"file_path": "/workspace/" + _NEW}),)
            if self.step == 2:
                return (("repair_update", "Write", {"file_path": "/workspace/" + _NEW, "content": self.new_skill}),) if self.invalid == "path" else (
                    ("repair_update", "Edit", {"file_path": "/workspace/" + _NEW,
                        "old_string": "name: wrong-name", "new_string": "name: measured-repair"}),)
        elif self.stage == "consumer":
            if self.step == 1:
                return (("new_skill", "Skill", {"skill": "hypoloom-alpha:measured-repair"}),)
            if self.step == 2:
                script = ("python3 - <<'PY'\nimport errno, os\nfrom pathlib import Path\n"
                    f"path = Path('/expert-plugin/{_NEW}')\nassert {self.nonce!r} in path.read_text()\n"
                    "try:\n    fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                    "except OSError as error:\n    assert error.errno == errno.EROFS\n"
                    "else:\n    os.close(fd)\n    raise AssertionError('plugin is writable')\n"
                    f"print({self.nonce!r}, 'PLUGIN_READONLY')\nPY")
                return (("use_lesson", "Bash", {"command": script, "timeout": 10000}),)
        elif self.stage == "old-consumer" and self.step == 1:
            return (("old_skill", "Skill", {"skill": "hypoloom-alpha:self-evolve"}),)
        return (("finish", "StructuredOutput", {"stage": self.stage, "ok": True}),)


def _invoke(tmp_path, executable, scenario, snapshot, worktree, sessions, session_arguments, base_url, key,
            *, writable=None, prompt=None):
    logs = tmp_path / scenario.stage
    logs.mkdir()
    scenario.step = 0
    workspace = SimpleNamespace(state_root=tmp_path / "plugin-state", session_root=sessions,
        worktree_root=worktree, input_root=tmp_path / "inputs", plugin_root=snapshot.root,
        writable=scenario.stage in {"invalid", "repair"} if writable is None else writable,
        session_arguments=session_arguments)
    request = replace(default_request(), budget=AgentBudget(max_turns=10, max_tokens=20000,
        max_output_bytes=131072, wall_seconds=40, max_cost="1"))
    relay = ProviderRelay(logs / "provider.sock", base_url=base_url,
        provider_environment={"ANTHROPIC_API_KEY": key}, model=_MODEL,
        max_request_bytes=4*1024*1024, max_response_bytes=1024*1024, max_requests=12,
        deadline_at=time.monotonic()+45)
    argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=executable, request=request,
        model=_MODEL, workspace=workspace, provider_socket=relay.socket_path, host_socket=None,
        output_schema=_SCHEMA, extra_paths=())
    (logs / "argv.json").write_text(json.dumps(argv))
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=full_cc_environment(), start_new_session=True)
        prompt = prompt or ("Native SELF_EVOLVE compatibility: the explicit writable plugin worktree is /workspace. Invoke self-evolve and edit only the plugin candidate; Host validates it."
            if scenario.stage == "invalid" else "Host rejected the update path or frontmatter. Repair this same plugin candidate."
            if scenario.stage == "repair" else "Invoke the selected native skill from the bound plugin snapshot.")
        stdout, stderr = process.communicate(prompt.encode(), timeout=45)
        (logs / "stdout.jsonl").write_bytes(stdout)
        (logs / "stderr.txt").write_bytes(stderr)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        assert not scenario.errors, scenario.errors
        result = next(json.loads(line) for line in stdout.splitlines() if json.loads(line).get("type") == "result")
        assert result.get("subtype") == "success", result
        assert result["structured_output"] == {"stage": scenario.stage, "ok": True}
        assert key.encode() not in stdout + stderr
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        relay.close()


@pytest.mark.parametrize("invalid", ("path", "frontmatter"))
def test_native_self_evolve_repairs_rejected_update_and_new_session_loads_only_selected_git_revision(tmp_path: Path, invalid: str):
    executable = _installed_claude()
    store = ExpertPluginStore(tmp_path / "plugin-state")
    base = store.ensure("run", "alpha", "Use actual parser repair observations.")
    old = store.read("run", "alpha", commit=base.commit, attempt_id="old-reader", max_bytes=131072)
    original = (old.root / _SELF).read_bytes()
    scenario = _EvolveScenario(original.decode(), invalid)
    command = "self-evolve-run-alpha-cycle-1"
    update = store.prepare_update("run", "alpha", commit=base.commit,
        attempt_id="evolve-attempt-1", command_id=command)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (tmp_path / "inputs").mkdir()
    consumer_workspace = tmp_path / "consumer-workspace"
    consumer_workspace.mkdir()
    (consumer_workspace / ".git").write_text("gitdir: /not-mounted\n")
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{provider.server_port}"
    key = "host-only-self-evolve-key-" + secrets.token_hex(12)
    session = str(uuid.uuid4())
    new = None
    try:
        _invoke(tmp_path, executable, scenario, old, update.root, sessions, ("--session-id", session), url, key)
        with pytest.raises(ValueError, match="confined|frontmatter"):
            store.finish_update(update, max_bytes=131072)
        assert store.ensure("run", "alpha", "ignored").commit == base.commit
        assert (old.root / _SELF).read_bytes() == original
        store = ExpertPluginStore(tmp_path / "plugin-state")
        recovered = store.prepare_update("run", "alpha", commit=base.commit,
            attempt_id="evolve-attempt-2", command_id=command)
        assert recovered.root == update.root
        scenario.stage = "repair"
        _invoke(tmp_path, executable, scenario, old, recovered.root, sessions, ("--resume", session), url, key)
        accepted = store.finish_update(recovered, max_bytes=131072)
        assert accepted.commit != base.commit
        assert store.finish_update(recovered, max_bytes=131072) == accepted
        assert store._git(recovered.repository_root, "rev-list", "--count", "HEAD").strip() == b"2"
        assert (old.root / _SELF).read_bytes() == original and not (old.root / _NEW).exists()
        new = store.read("run", "alpha", commit=accepted.commit, attempt_id="next-cycle", max_bytes=131072)
        assert (new.root / _NEW).read_text() == scenario.new_skill
        for stage, snapshot in (("consumer", new), ("old-consumer", old)):
            scenario.stage = stage
            _invoke(tmp_path, executable, scenario, snapshot, consumer_workspace, sessions,
                ("--session-id", str(uuid.uuid4())), url, key)
        assert {"evolve_skill", "edit_skill", "invalid_write", "repair_update", "new_skill", "use_lesson", "old_skill"} <= set(scenario.evidence)
        (tmp_path / "plugin-revisions.json").write_text(json.dumps({"base": base.to_dict(),
            "accepted": accepted.to_dict(), "command_id": command, "update_session_id": session}, indent=2))
        assert all(key.encode() not in path.read_bytes() for path in sessions.glob("*.jsonl"))
    finally:
        if new is not None:
            new.close()
        old.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
