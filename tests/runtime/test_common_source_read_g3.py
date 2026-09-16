"""Real Read and Bash cat consume the same accepted evaluator in native CPU G3."""

import json
import sqlite3
from pathlib import Path

from hypoloom.domain import AgentCommand
import test_claude_shared_acceptance_g3 as native
from test_real_current_host_g5 import _has_common_source_read, _native_tools


class _CatAcceptanceScenario(native._AcceptanceScenario):
    source_command = "cat /acceptance/evaluate.py"

    def tool_calls(self, number):
        calls = super().tool_calls(number)
        request = self.current
        if (request is not None
                and request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT
                and request.role == native._GOAL
                and self.steps[request.attempt_id] == 1):
            assert calls == (("expert_read", "Read", {"file_path": "/acceptance/evaluate.py"}),)
            return (("expert_read", "Bash", {
                "command": self.source_command, "timeout": 10000}),)
        return calls


def _run_source_read_case(tmp_path, monkeypatch, source_command):
    monkeypatch.setattr(_CatAcceptanceScenario, "source_command", source_command)
    monkeypatch.setattr(native, "_AcceptanceScenario", _CatAcceptanceScenario)
    # Keep all existing authoring/repair, read-only source, two-CPU, common-goal,
    # session, publication, and Oracle assertions in the production-shaped run.
    native.test_native_grounding_shared_source_local_failure_and_complete_goal_stop(tmp_path, monkeypatch)

    evidence = json.loads((tmp_path / "shared-acceptance-evidence.json").read_text())
    source = (Path(evidence["grounding_transcript"]) / "evaluator/evaluate.py").read_bytes()
    database = tmp_path / "state/state.sqlite"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        observations = [json.loads(row[0]) for row in connection.execute(
            "SELECT observation_json FROM execution_attempts WHERE kind='AGENT' "
            "AND json_extract(observation_json,'$.request.command')='EXPERIMENT_DESIGN_IMPLEMENT' "
            "ORDER BY started_at")]
    assert len(observations) == 2
    assert {row["request"]["role"] for row in observations} == {native._LOCAL, native._GOAL}
    for observation in observations:
        archive = tmp_path / "state/artifacts" / observation["session"]["transcript"]["path"]
        uses, results = _native_tools((archive / "transcript.jsonl").read_bytes())
        reads = [use for use in uses if use.get("name") == "Read"
                 and use.get("input", {}).get("file_path") == "/acceptance/evaluate.py"]
        cats = [use for use in uses if use.get("name") == "Bash"
                and use.get("input", {}).get("command") == source_command]
        if observation["request"]["role"] == native._GOAL:
            assert len(cats) == 1 and not reads
            matched = cats[0]
        else:
            assert len(reads) == 1 and not cats
            matched = reads[0]
        assert _has_common_source_read(uses, results, source)
        without_read_result = [result for result in results if result.get("tool_use_id") != matched["id"]]
        assert len(without_read_result) < len(results)
        assert not _has_common_source_read(uses, without_read_result, source)


def test_native_read_and_cat_require_matching_source_results(tmp_path, monkeypatch):
    _run_source_read_case(tmp_path, monkeypatch, "cat /acceptance/evaluate.py")


def test_native_cat_with_diagnostic_fallback_requires_exact_source(tmp_path, monkeypatch):
    _run_source_read_case(tmp_path, monkeypatch,
                         'cat /acceptance/evaluate.py 2>/dev/null || echo "File not found"')
