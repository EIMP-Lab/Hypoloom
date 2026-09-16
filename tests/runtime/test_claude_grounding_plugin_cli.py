"""Native Grounding plugin consumers/update, not Observer or manager Host dispatch."""

from __future__ import annotations

import json
import secrets
import threading
import uuid
from http.server import HTTPServer
from pathlib import Path

from hypoloom.expert_plugin import ExpertPluginStore

from test_claude_file_tool_cli import _handler_for, _installed_claude, _result_text, _tool_results
from test_claude_self_evolve_cli import _invoke


_TRIGGER = "skills/leader-trigger/SKILL.md"
_REQUIRED = {"leader-trigger", "debate-trigger", "debate-judge", "graph-evolve", "final-report", "self-evolve"}


class _GroundingPluginScenario:
    stage, step = "trigger-report", 0

    def __init__(self, original: str):
        self.original = original
        self.nonce = "observed-trigger-lesson-" + secrets.token_hex(12)
        self.report = ("# FINAL_REPORT\n\nScripted compatibility fixture, not a scientific research run.\n"
            "Fixture goal: inspect shared-goal progress. Source: fixture-exp-1 / fixture-outcome-1.\n"
            "No GPU or public provider was used. Host verdict authority is unchanged.\n")
        self.requests, self.errors, self.evidence = [], [], {}

    def record(self, document):
        self.requests.append(document)
        self.step += 1
        try:
            rendered = json.dumps(document)
            assert "Skill" in {tool["name"] for tool in document["tools"]}
            if self.step == 1:
                assert all(f"hypoloom-grounding:{name}" in rendered for name in _REQUIRED)
                assert "hypoloom-grounding:experiment-report" not in rendered
                assert self.nonce not in rendered
            if self.stage == "old-trigger":
                assert self.nonce not in rendered
            names = {"trigger-report": {2: "read_sources", 3: "leader_skill", 4: "report_skill", 5: "write_report", 6: "check_report"},
                "update": {2: "evolve_skill", 3: "read_trigger", 4: "edit_trigger"},
                "next-trigger": {2: "new_trigger"}, "old-trigger": {2: "old_trigger"}}
            previous = names[self.stage].get(self.step)
            if previous:
                result = _tool_results(document)[previous]
                assert not result.get("is_error"), result
                self.evidence[previous] = _result_text(result)
                if previous == "leader_skill":
                    assert "stateless Observer decision" in rendered
                elif previous == "report_skill":
                    assert "not a new" in rendered and "GoalVerdictRecorded" in rendered
                elif previous == "check_report":
                    assert "GROUNDING_REPORT_AND_PLUGIN_READONLY" in self.evidence[previous]
                elif previous == "new_trigger":
                    assert self.nonce in rendered
        except (AssertionError, KeyError) as exc:
            self.errors.append(f"{self.stage}/{self.step}: {exc}")
        return len(self.requests)

    def tool_calls(self, _number):
        if self.errors:
            return ()
        if self.stage == "trigger-report":
            script = ("python3 - <<'PY'\nimport errno, os\nfrom pathlib import Path\n"
                f"assert Path('/workspace/FINAL_REPORT.md').read_text() == {self.report!r}\n"
                "try:\n    fd = os.open('/expert-plugin/skills/final-report/SKILL.md', os.O_WRONLY | os.O_APPEND)\n"
                "except OSError as error:\n    assert error.errno == errno.EROFS\n"
                "else:\n    os.close(fd)\n    raise AssertionError('Grounding plugin writable')\n"
                "print('GROUNDING_REPORT_AND_PLUGIN_READONLY')\nPY")
            steps = (
                ("read_sources", "Read", {"file_path": "/inputs/0000.txt"}),
                ("leader_skill", "Skill", {"skill": "hypoloom-grounding:leader-trigger"}),
                ("report_skill", "Skill", {"skill": "hypoloom-grounding:final-report"}),
                ("write_report", "Write", {"file_path": "/workspace/FINAL_REPORT.md", "content": self.report}),
                ("check_report", "Bash", {"command": script, "timeout": 10000}),
            )
            if self.step <= len(steps):
                return (steps[self.step - 1],)
        elif self.stage == "update":
            steps = (
                ("evolve_skill", "Skill", {"skill": "hypoloom-grounding:self-evolve"}),
                ("read_trigger", "Read", {"file_path": "/workspace/" + _TRIGGER}),
                ("edit_trigger", "Edit", {"file_path": "/workspace/" + _TRIGGER,
                    "old_string": self.original, "new_string": self.original + "\nObserved trigger repair lesson: " + self.nonce + "\n"}),
            )
            if self.step <= len(steps):
                return (steps[self.step - 1],)
        elif self.step == 1:
            return (("new_trigger" if self.stage == "next-trigger" else "old_trigger", "Skill",
                {"skill": "hypoloom-grounding:leader-trigger"}),)
        return (("finish", "StructuredOutput", {"stage": self.stage, "ok": True}),)


def test_native_grounding_trigger_report_and_evolved_trigger_keep_product_namespace_and_git_snapshot(tmp_path: Path):
    executable = _installed_claude()
    state = tmp_path / "plugin-state"
    expert_store = ExpertPluginStore(state)
    expert = expert_store.ensure("run", "grounding", "A legal same-named user expert must remain separate.")
    store = ExpertPluginStore(state, owner_kind="grounding")
    base = store.ensure("run", "grounding", "Interpret shared-goal progress from exact accepted records.")
    old = store.read("run", "grounding", commit=base.commit, attempt_id="grounding-initial", max_bytes=131072)
    scenario = _GroundingPluginScenario((old.root / _TRIGGER).read_text())
    consumer = tmp_path / "report-workspace"
    consumer.mkdir()
    (consumer / ".git").write_text("gitdir: /not-mounted\n")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "0000.txt").write_text(json.dumps({"fixture_only": True, "goal": "inspect shared-goal progress",
        "experiments": [{"experiment_id": "fixture-exp-1", "outcome_id": "fixture-outcome-1"}]}))
    provider = HTTPServer(("127.0.0.1", 0), _handler_for(scenario))
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{provider.server_port}"
    key = "host-only-grounding-plugin-key-" + secrets.token_hex(12)
    selected_sessions = {}
    new = None
    try:
        def invoke(snapshot, worktree, writable, prompt):
            session_id = str(uuid.uuid4())
            selected_sessions[scenario.stage] = session_id
            _invoke(tmp_path, executable, scenario, snapshot, worktree, sessions,
                ("--session-id", session_id), url, key, writable=writable, prompt=prompt)
        invoke(old, consumer, True, "Native Grounding plugin compatibility only: read /inputs/0000.txt, invoke leader-trigger and final-report, and write /workspace/FINAL_REPORT.md. Do not claim a real research run.")
        assert (consumer / "FINAL_REPORT.md").read_text() == scenario.report
        update = store.prepare_update("run", "grounding", commit=base.commit,
            attempt_id="manager-update-1", command_id="grounding-trigger-evolution-cycle-1")
        scenario.stage = "update"
        invoke(old, update.root, True, "Explicit SELF_EVOLVE plugin update compatibility: invoke self-evolve and edit only the candidate plugin at /workspace, preserving all six Grounding entrypoints.")
        accepted = store.finish_update(update, max_bytes=131072)
        assert accepted.owner_kind == "grounding" and accepted.commit != base.commit
        assert expert_store.ensure("run", "grounding", "ignored") == expert
        assert (old.root / _TRIGGER).read_text() == scenario.original
        new = store.read("run", "grounding", commit=accepted.commit, attempt_id="next-observer", max_bytes=131072)
        for stage, snapshot in (("next-trigger", new), ("old-trigger", old)):
            scenario.stage = stage
            invoke(snapshot, consumer, False, "In this fresh stateless Observer compatibility session invoke leader-trigger from the explicitly selected native plugin snapshot.")
        assert {"leader_skill", "report_skill", "write_report", "evolve_skill", "edit_trigger", "new_trigger", "old_trigger"} <= set(scenario.evidence)
        assert store._git(update.repository_root, "rev-list", "--count", "HEAD").strip() == b"2"
        (tmp_path / "grounding-plugin-revisions.json").write_text(json.dumps({"base": base.to_dict(),
            "accepted": accepted.to_dict(), "same_named_expert": expert.to_dict(),
            "sessions": selected_sessions}, indent=2))
        assert all(key.encode() not in path.read_bytes() for path in sessions.glob("*.jsonl"))
    finally:
        if new is not None:
            new.close()
        old.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
