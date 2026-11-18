"""Real native MCP preflight/correction, Stop, CPU probe and independent review."""
from copy import deepcopy
import json
import sqlite3

from hypoloom.domain import AgentCommand
from hypoloom.profile_v4 import project_profile_v4
from hypoloom.verification import ImplementationVerification
import test_claude_file_tool_cli as provider
import test_full_cc_host_g3 as native
from test_profile_v4 import documents


def test_native_verifier_prepares_corrects_and_finishes_without_repeating_report(tmp_path, monkeypatch):
    scenario = native._current_host_scenario(tmp_path)
    original_tools, original_payload = scenario.tool_calls, native._pilot_payload
    prepared, corrected = set(), set()

    def payload(*args):
        value = original_payload(*args)
        settings = project_profile_v4(**documents()).settings
        settings["experts"]["experts"][0]["name"] = value["research_contract"]["roles"][0]["id"]
        settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
        settings["goal"]["completion_mode"] = "explicit_signals"
        settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 1}
        settings["methodology"]["interaction"] = {"leader": {"enabled": False}, "debate": {"enabled": False}}
        settings["methodology"]["graph_evolution"]["enabled"] = False
        settings["methodology"]["output_correction"] = {"max_failed_validations": 3}
        value["research_contract"]["methodology"]["controls"]["product"] = settings
        return value

    def tools(number):
        calls = original_tools(number)
        request = scenario.current
        if request.command is not AgentCommand.VERIFY_IMPLEMENTATION:
            return calls
        document = scenario.requests[-1]
        tool_names = {t["name"] for t in document["tools"]}
        assert "mcp__verifier-output__submit_review" in tool_names
        assert not tool_names & {"Bash", "Write", "Edit", "StructuredOutput"}
        output = provider.displayed_candidate(document, deepcopy(calls[0][2]))
        report = {k: v for k, v in output["typed_candidate"].items() if k != "task_kind"}
        step = scenario.steps[request.attempt_id]
        inspect = report["probe"] is not None
        if step == 1 and inspect:
            report["probe"]["files"]["verification/probe.py"] = "def broken("
            return (("bad_review", "mcp__verifier-output__submit_review", report),)
        results = provider._tool_results(document)
        if step == 2 and inspect:
            failed = results["bad_review"]
            assert failed.get("is_error") is True
            assert "does not compile" in provider._result_text(failed)
            corrected.add(request.attempt_id)
        if step == (2 if inspect else 1):
            return (("good_review", "mcp__verifier-output__submit_review", report),)
        assert step == (3 if inspect else 2)
        assert results["good_review"].get("is_error") is not True
        assert "review_submitted" in provider._result_text(results["good_review"])
        prepared.add(request.attempt_id)
        return (("complete", "StructuredOutput", {"review_submitted": True}),)

    scenario.tool_calls = tools
    monkeypatch.setattr(native, "_current_host_scenario", lambda _: scenario)
    monkeypatch.setattr(native, "_pilot_payload", payload)
    monkeypatch.setattr(ImplementationVerification, "enabled", lambda self, run: True)
    native._exercise_full_cc_host(tmp_path, timeout_seconds=240, implementation_probes=2)
    with sqlite3.connect(f"file:{tmp_path / 'state/state.sqlite'}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT attempt_id,terminal_reason,observation_json FROM execution_attempts "
            "WHERE json_extract(observation_json,'$.request.command')='VERIFY_IMPLEMENTATION'").fetchall()
    assert len(rows) == len(prepared) == 4 and len(corrected) == 2
    evidence = []
    for identity, reason, encoded in rows:
        observation = json.loads(encoded)
        trace = observation["diagnostics"]["structured_output_correction"]
        assert reason == "SUCCEEDED" and trace["accepted"]
        assert trace["rejected_count"] == int(identity in corrected)
        assert [r["status"] for r in trace["rounds"]] == (["rejected"] if identity in corrected else []) + ["prepared", "accepted"]
        assert observation["response"]["typed_candidate"]["task_kind"] == "VERIFY_IMPLEMENTATION"
        evidence.append({"attempt_id": identity, "trace": trace})
    (tmp_path / "structured-review-evidence.json").write_text(json.dumps(evidence, indent=2))
