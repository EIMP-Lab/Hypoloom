"""Native manager persistence/plugin revision, not WorkflowHost trigger dispatch."""

import json
import secrets
from pathlib import Path

from hypoloom.agent_aci import validate_postflight
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.expert_plugin import ExpertPluginStore

from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_full_cc_adapter import _invoke_adapter, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_grounding_manager import _request, _with_plugin


_LINE = "Recommend a Leader only when at least one candidate has an accepted SUCCESS or"


class _ManagerScenario:
    def __init__(self):
        self.stage, self.stage_step = "first", 0
        self.requests, self.errors = [], []
        self.structured_output = {}
        self.evidence = {}
        self.nonce = "manager-trigger-revision-" + secrets.token_hex(16)
        self.entry_messages = 0

    def record(self, document):
        self.requests.append(document)
        self.stage_step += 1
        step = self.stage_step
        try:
            if step == 1:
                self.entry_messages = len(document["messages"])
                text = json.dumps(document)
                assert "hypoloom-grounding:leader-trigger" in text
                assert "hypoloom-grounding:experiment-report" not in text
            previous = ({2: "graph", 3: "leader", 4: "read", 5: "edit", 6: "protection"}
                        if self.stage == "first" else {2: "leader", 3: "protection"}).get(step)
            if previous:
                result = _tool_results(document)[self.stage + "_" + previous]
                assert not result.get("is_error"), result
                self.evidence[self.stage + "_" + previous] = _result_text(result)
            if self.stage == "first" and step == 3:
                assert "This is a stateless Observer decision" in json.dumps(document["messages"][self.entry_messages:])
            if self.stage == "second" and step == 2:
                # The old edit is already in persistent history; only *new*
                # messages caused by this Skill invocation prove native reload.
                fresh = json.dumps(document["messages"][self.entry_messages:])
                assert self.nonce in fresh, "resumed manager's native Skill did not load the newly selected Git revision"
                self.evidence["second_native_skill_body"] = fresh
        except Exception as exc:
            self.errors.append(f"{self.stage}/{step}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        if self.errors:
            return ()
        step = self.stage_step
        if self.stage == "first" and step == 1:
            return (("first_graph", "Skill", {"skill": "hypoloom-grounding:graph-evolve"}),)
        if (self.stage == "first" and step == 2) or (self.stage == "second" and step == 1):
            return ((self.stage + "_leader", "Skill", {"skill": "hypoloom-grounding:leader-trigger"}),)
        if self.stage == "first" and step == 3:
            return (("first_read", "Read", {"file_path": "/plugin-update/skills/leader-trigger/SKILL.md"}),)
        if self.stage == "first" and step == 4:
            return (("first_edit", "Edit", {"file_path": "/plugin-update/skills/leader-trigger/SKILL.md",
                "old_string": _LINE, "new_string": "Native compatibility lesson: " + self.nonce + ".\n\n" + _LINE}),)
        if (self.stage == "first" and step == 5) or (self.stage == "second" and step == 2):
            script = (
                "import errno, os\nfrom pathlib import Path\n"
                "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                f"assert {self.nonce!r} in Path('/plugin-update/skills/leader-trigger/SKILL.md').read_text()\n"
                f"assert ({self.nonce!r} in Path('/expert-plugin/skills/leader-trigger/SKILL.md').read_text()) is {self.stage == 'second'}\n"
                "assert not Path('/expert-plugin/skills/experiment-report').exists()\n"
                "assert not Path('/memory').exists() and not Path('/state').exists()\n"
                "for path in ['/plugin-update/.git', '/expert-plugin/skills/leader-trigger/SKILL.md']:\n"
                "    try:\n        fd = os.open(path, os.O_WRONLY | os.O_APPEND)\n"
                "    except OSError as error:\n        assert error.errno == errno.EROFS, (path, error)\n"
                "    else:\n        os.close(fd)\n        raise AssertionError('manager authority exceeded')\n"
                "print('MANAGER_PLUGIN_REVISION_AND_BOUNDARIES')\n"
            )
            return ((self.stage + "_protection", "Bash",
                     {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
        output = {**self.structured_output, "typed_candidate": {**self.structured_output["typed_candidate"],
            "graph_evolution": [{"role": "scientist", "effective_max_forks": 2,
                "reason": "Bounded native manager compatibility proposal within the frozen limit."}] if self.stage == "first" else []}}
        return ((self.stage + "_structured", "StructuredOutput", output),)


def test_native_manager_second_trigger_resumes_and_skill_loads_new_plugin_revision(tmp_path: Path):
    executable = _installed_claude()
    manager = _manager(tmp_path)
    private = ExpertPluginStore(manager.state_root).ensure("run-fake", "grounding", "Private expert namespace.")
    scenario = _ManagerScenario()
    key = "host-only-manager-" + secrets.token_hex(16)
    provider, thread = _start_provider(scenario, key)
    adapter = ClaudeAgentHost(claude_executable=executable)
    environment = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"}
    metadata, accepted = [], []
    try:
        for cycle, stage in enumerate(("first", "second"), start=1):
            scenario.stage = stage
            if cycle == 2:
                manager = type(manager)(manager.state_root, manager.publisher)
            store, plugin, request = _with_plugin(manager, _request(cycle))
            request = native_request(request, stage, None)
            workspace = manager.prepare(request, None)
            if cycle == 2:
                assert plugin.commit == accepted[0]["commit"]
                assert workspace.session_arguments == ("--resume", metadata[0]["session_id"])
            response, archived = _invoke_adapter(adapter, request, workspace, scenario, environment, tmp_path / stage)
            parsed = validate_postflight(request, response)
            assert len(parsed.candidate.graph_evolution) == (1 if cycle == 1 else 0)
            metadata.append(archived)
            accepted.append(store.accept_request_update(request))
        assert metadata[0]["session_id"] == metadata[1]["session_id"]
        assert metadata[0]["launch_arguments"] == ["--session-id", metadata[0]["session_id"]]
        assert metadata[1]["launch_arguments"] == ["--resume", metadata[0]["session_id"]]
        assert accepted[0]["base_commit"] != accepted[0]["commit"]
        assert accepted[1]["base_commit"] == accepted[1]["commit"] == accepted[0]["commit"]
        old = store.read("run-fake", "grounding", commit=accepted[0]["base_commit"],
            attempt_id="inspect-old-manager", max_bytes=1024 * 1024)
        try:
            assert scenario.nonce not in (old.root / "skills/leader-trigger/SKILL.md").read_text()
        finally:
            old.close()
        assert ExpertPluginStore(manager.state_root).ensure("run-fake", "grounding", "ignored existing seed").commit == private.commit
        assert len(scenario.requests) == 9 and len(scenario.evidence) == 8
        assert not scenario.errors
        for record in metadata:
            transcript = manager.publisher.root / record["transcript"]["path"] / "transcript.jsonl"
            assert transcript.is_file() and key.encode() not in transcript.read_bytes()
        (tmp_path / "plugin-updates.json").write_text(json.dumps(accepted, indent=2))
        (tmp_path / "native-evidence.json").write_text(json.dumps(scenario.evidence, indent=2))
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        provider.shutdown(); provider.server_close(); thread.join(timeout=5)
