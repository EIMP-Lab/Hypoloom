"""Actual CC receives stage-scoped shared-evaluator prompts through CPU closure.

The existing local provider scripts choices, not Host acceptance, tools or CPU
results. This verifies transmitted instructions, not real-model compliance.
"""

from __future__ import annotations

import json
from pathlib import Path

import test_claude_shared_final_retry_g3 as shared_retry
from test_claude_file_tool_cli import native_output_schema
from test_claude_shared_acceptance_g3 import _RUN
from test_full_cc_host_g3 import _rows


_CODING_ONLY = (
    "Write/test source under /workspace",
    "author its declared files under /workspace",
    "metrics=[]",
    "metrics exactly [measurement.metric]",
    "DESIGN and submit candidate must select evaluation_scope",
)
_READ_ONLY = "/workspace remains read-only at this stage; use /tmp for any useful local exploration."
_PROPOSAL = (
    "This proposal returns only task_kind, summary and supporting_refs inside typed_candidate; "
    "do not emit experiment-design fields.")
_SYNTHESIS = (
    "Declare the hypothesis now; implementation and parser authoring belong to later DESIGN/execution nodes.")
_SYNTHESIS_ENVELOPE = (
    "one HypothesisSet/v1 in typed_candidate, with schema_version=1 and a nonempty hypotheses array.",
    "Each hypotheses item contains only chain_id and candidate; candidate is one complete falsifiable HypothesisCandidate.",
    "Keep this wrapper even for a single hypothesis.",
    "Use unique chain_id values and do not exceed the admitted effective_max_forks.",
)
_EXECUTION = (
    "The MCP submit candidate is an ExperimentSpecCandidate; the final JSON object is the execution response, "
    "not that candidate.")


def _latest_wire_prompt(document):
    # Same frozen-prompt marker as the existing native provider fixture. A
    # resumed session also carries older prompts, which are not this Attempt.
    for message in reversed(document["messages"]):
        if message.get("role") != "user":
            continue
        content = message.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in reversed(content):
            text = block.get("text", "")
            start = text.find('{"agent_request_metadata":')
            if start >= 0:
                return json.JSONDecoder().raw_decode(text[start:])[0]
    raise AssertionError("actual provider request has no frozen Agent prompt")


def test_native_stage_scoped_shared_prompts_close_proposals_design_run_and_report(tmp_path: Path):
    shared_retry.test_native_shared_final_report_retries_with_one_common_input_and_same_session(tmp_path)
    attempts = _rows(tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    checked, stages = [], {}
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        observed = json.loads(row["observation_json"])
        request = observed["request"]
        command = request["command"]
        if command not in {"HYPOTHESIS_PROPOSAL", "EXPERIMENT_DESIGN_IMPLEMENT",
                           "EXPERIMENT_EXECUTION", "FINAL_REPORT"}:
            continue
        stage = request["task_context"]["key"]["subkind"] if command == "HYPOTHESIS_PROPOSAL" else command
        stages.setdefault(stage, []).append((row, observed))
        private = tmp_path / "state/diagnostics/agents" / _RUN / row["attempt_id"]
        source = private / "provider/000001/request.body"
        document = json.loads(source.read_text())
        prompt = _latest_wire_prompt(document)
        assert prompt == json.loads((private / "prompt.txt").read_text())
        assert prompt["agent_request_metadata"]["attempt_id"] == row["attempt_id"]
        instruction = prompt["instruction"]
        schema = native_output_schema(document)
        candidate = schema["properties"]["typed_candidate"]
        assert set(schema["properties"]) == {"typed_candidate", "declared_effects"}
        assert schema["additionalProperties"] is False and candidate["additionalProperties"] is False
        assert "No local C5 result alone proves the whole goal" in instruction
        if stage in {"FORWARD", "BACKWARD", "SYNTHESIS", "FINAL_REPORT"}:
            assert _READ_ONLY in instruction
            assert all(text not in instruction for text in _CODING_ONLY)
        if stage in {"FORWARD", "BACKWARD"}:
            assert _PROPOSAL in instruction
            assert set(candidate["properties"]) == {"task_kind", "summary", "supporting_refs"}
            assert row["terminal_reason"] == "SUCCEEDED"
        elif stage == "SYNTHESIS":
            assert _SYNTHESIS in instruction
            assert "SYNTHESIS may declare a local C5 measurement or measurement=null" in instruction
            assert all(text in instruction for text in _SYNTHESIS_ENVELOPE)
            assert "produce one falsifiable HypothesisCandidate" not in instruction
            assert set(candidate["properties"]) == {"schema_version", "hypotheses"}
            assert candidate["properties"]["schema_version"] == {"const": 1}
            assert set(candidate["properties"]["hypotheses"]["items"]["properties"]) == {"chain_id", "candidate"}
            hypothesis = candidate["properties"]["hypotheses"]["items"]["properties"]["candidate"]
            assert "measurement" in hypothesis["properties"] and "execution_argv" not in hypothesis["properties"]
            assert row["terminal_reason"] == "SUCCEEDED"
        elif stage == "EXPERIMENT_DESIGN_IMPLEMENT":
            assert "Write/test source under /workspace" in instruction
            assert "metrics exactly [measurement.metric]" in instruction
            assert "ExperimentSpecCandidate: include its required method and omit supporting_refs" in instruction
            assert {"method", "measurement", "metrics", "execution_argv", "evaluation_scope"} <= set(candidate["properties"])
            assert "supporting_refs" not in candidate["properties"]
            assert row["terminal_reason"] == "SUCCEEDED"
        elif stage == "EXPERIMENT_EXECUTION":
            assert _EXECUTION in instruction
            assert "Keep task_kind, summary and supporting_refs in the final execution typed_candidate." in instruction
            assert "submit_experiment" in instruction and "wait_experiment" in instruction
            assert set(candidate["properties"]) == {"task_kind", "summary", "supporting_refs", "waiting_for_experiment_id"}
            assert "waiting_for_experiment_id" not in candidate["required"]
            assert row["terminal_reason"] == "SUCCEEDED"
        else:
            assert instruction.startswith("Invoke Skill hypoloom-grounding:final-report")
            assert "/report/FINAL_REPORT.md" in instruction
            assert set(candidate["properties"]) == {"task_kind", "summary", "supporting_refs"}
        stdout = [json.loads(line) for line in (private / "stdout.log").read_text().splitlines()]
        calls = [{"tool_use_id": block["id"], "tool": block["name"]}
                 for event in stdout if event.get("type") == "assistant"
                 for block in event["message"]["content"] if block.get("type") == "tool_use"]
        assert not any(call["tool"] == "StructuredOutput" for call in calls)
        result = next(item for item in reversed(stdout) if item.get("type") == "result")
        assert result["subtype"] == "success" and result.get("structured_output") is None
        assert set(json.loads(result["result"])) == {"typed_candidate", "declared_effects"}
        trace = observed["diagnostics"]["structured_output_correction"]
        rejected = row["terminal_reason"] == "FAILED"
        assert trace["scope"] == "native_stop_validation"
        assert trace["call_count"] == (2 if rejected else 1)
        assert [item["status"] for item in trace["rounds"]] == (
            ["rejected", "rejected"] if rejected else ["accepted"])
        if stage == "EXPERIMENT_EXECUTION":
            assert sum(call["tool"] == "mcp__host-tools__submit_experiment" for call in calls) == 1
            assert any(call["tool"] == "mcp__host-tools__wait_experiment" for call in calls)
        checked.append({"attempt_id": row["attempt_id"], "stage": stage, "provider_request": str(source),
            "candidate_properties": sorted(candidate["properties"]), "raw_tool_identities": calls})
    assert {stage: len(rows) for stage, rows in stages.items()} == {
        "FORWARD": 1, "BACKWARD": 1, "SYNTHESIS": 1, "EXPERIMENT_DESIGN_IMPLEMENT": 1,
        "EXPERIMENT_EXECUTION": 1, "FINAL_REPORT": 2}
    design = stages["EXPERIMENT_DESIGN_IMPLEMENT"][0][1]
    execution = stages["EXPERIMENT_EXECUTION"][0][1]
    assert execution["session"]["session_id"] != design["session"]["session_id"]
    assert execution["session"]["branch"] == design["session"]["branch"]
    assert execution["session"]["launch_arguments"] == ["--session-id", execution["session"]["session_id"]]
    closure = json.loads((tmp_path / "shared-final-retry-evidence.json").read_text())
    assert closure["agent_count"] == 12 and closure["process_count"] == 1
    assert closure["oracle"]["ok"] and closure["head"]["state"] == "COMPLETED"
    (tmp_path / "stage-scoped-shared-prompt-evidence.json").write_text(json.dumps({
        "paid_model": False, "gpu": False, "real_model_compliance_tested": False,
        "agent_count": closure["agent_count"], "process_count": closure["process_count"],
        "checked_attempts": checked, "design_execution_session": design["session"]["session_id"],
        "verified_synthesis_envelope_instruction": list(_SYNTHESIS_ENVELOPE),
        "final_attempts": closure["final_attempts"], "oracle": closure["oracle"], "head": closure["head"]}, indent=2))
