"""Successful native DESIGN preserves correction diagnostics through Host close.

The reused fresh Host fixture owns actual CC tools, MCP submission, CPU and
Oracle closure. Only the local provider's first two DESIGN outputs are invalid;
its third complete output succeeds within the same CLI/Attempt.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from hypoloom.claude_agent_host import process_group_exists
from hypoloom.debug_mode import build_debug_report
from hypoloom.runtime_diagnostics import sanitize_payload

import test_real_design_recovery_probe as probe
from test_claude_shared_acceptance_g3 import _AcceptanceScenario
from test_full_cc_host_g3 import _rows


class _SameAttemptDesign(probe._LocalDesignScenario):
    def tool_calls(self, number):
        calls = []
        # Keep the original real source-writing/testing sequence, bypassing
        # only the old fixture's deliberate cross-Attempt rejection sequence.
        for identity, tool, arguments in _AcceptanceScenario.tool_calls(self, number):
            arguments = copy.deepcopy(arguments)
            if tool == "StructuredOutput":
                request = self.current
                self.output_counts[request.attempt_id] += 1
                ordinal = self.output_counts[request.attempt_id]
                identity = f"{identity}-{ordinal}"
                candidate = arguments["typed_candidate"]
                if ordinal == 1:
                    for field in probe._CORRECTION_FIELDS:
                        candidate.pop(field)
                elif ordinal == 2:
                    arguments["typed_candidate"] = {
                        field: candidate[field] for field in probe._CORRECTION_FIELDS}
                else:
                    assert ordinal == 3, "same-Attempt correction exceeded its configured cap"
                    candidate["method"] += " Complete object after two native schema rejections."
                self.output_calls.append({"attempt_id": request.attempt_id,
                    "arguments": copy.deepcopy(arguments)})
            calls.append((identity, tool, arguments))
        return tuple(calls)


def test_successful_host_design_retains_native_correction_trace_and_closes_cpu(tmp_path: Path, monkeypatch):
    for name in ("HYPOLOOM_REAL_DESIGN_RECOVERY", "HYPOLOOM_REAL_CLAUDE"):
        monkeypatch.delenv(name, raising=False)
    assert not probe._paid_enabled()
    monkeypatch.setattr(probe, "_LocalDesignScenario", _SameAttemptDesign)
    closure = probe._run_probe(tmp_path)
    assert closure["paid_design"] is False and closure["gpu"] is False
    assert not closure.get("fixture_errors"), closure.get("fixture_errors")
    assert closure["design_accepted"] and closure["natural_cpu_closure"], closure
    assert len(closure["design_attempts"]) == 1
    selected = closure["design_attempts"][0]
    identity = selected["attempt_id"]
    assert selected["state"] == "CLOSED" and selected["terminal_reason"] == "SUCCEEDED"
    attempts = _rows(tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    row = next(item for item in attempts if item["attempt_id"] == identity)
    observation = json.loads(row["observation_json"])
    assert row["attempt_ordinal"] == 1
    assert all(item["state"] == "CLOSED" for item in attempts)
    assert all(not process_group_exists(item["process_group_id"]) for item in attempts if item["process_group_id"])
    source = Path(closure["diagnostics_root"]) / identity
    captured = json.loads((source / "result.json").read_text())
    parser = captured["parser_diagnostics"]
    assert captured["terminal_reason"] == "SUCCEEDED" and captured["failure_code"] is None
    trace = parser["structured_output_correction"]
    assert trace["scope"] == "native_stop_validation" and trace["status"] == "recovered"
    assert trace["validation_source"] == "host_stop_hook" and trace["accepted"] is True
    assert (trace["call_count"], trace["rejected_count"]) == (3, 2)
    assert parser["adapter_retry_policy"]["structured_output_failed_validation_cap"] == 3
    assert [item["status"] for item in trace["rounds"]] == ["rejected", "rejected", "accepted"]
    assert [item["ordinal"] for item in trace["rounds"]] == [1, 2, 3]
    assert all(set(item) == {"ordinal", "status", "validation_error"} for item in trace["rounds"])
    records = [json.loads(line) for line in (source / "stdout.log").read_text().splitlines()]
    raw_outputs, observed_inputs = [], {}
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record["message"]
        observed_inputs[message["id"]] = max(observed_inputs.get(message["id"], 0), message["usage"]["input_tokens"])
        assert not any(block.get("type") == "tool_use" and block.get("name") == "StructuredOutput"
                       for block in message["content"])
        raw_outputs.extend({"attempt_id": identity, "arguments": json.loads(block["text"])}
                           for block in message["content"] if block.get("type") == "text")
    assert raw_outputs == closure["design_outputs"] and len(raw_outputs) == 3
    validations = [json.loads(path.read_text()) for path in sorted((source / "native-output").glob("*.json"))]
    assert [json.loads(item["last_assistant_message"]) for item in validations] == [item["arguments"] for item in raw_outputs]
    assert [item["error"] for item in validations] == [item["validation_error"] for item in trace["rounds"]]
    assert {item["session_id"] for item in validations} == {observation["session"]["session_id"]}
    assert set(raw_outputs[0]["arguments"]["typed_candidate"]) == probe._DESIGN_FIELDS - probe._CORRECTION_FIELDS
    assert set(raw_outputs[1]["arguments"]["typed_candidate"]) == probe._CORRECTION_FIELDS
    complete = raw_outputs[2]["arguments"]["typed_candidate"]
    assert set(complete) == probe._DESIGN_FIELDS
    assert observation["response"]["typed_candidate"] == complete
    assert closure["accepted_designs_used_by_execution"] == {identity: complete}
    # Recorder copies redact opaque embedded content; the authoritative request
    # remains intact, and the actual final JSON objects above are unmerged.
    assert json.loads((source / "request.json").read_text()) == sanitize_payload(observation["request"])
    result = next(item for item in reversed(records) if item.get("type") == "result")
    assert result["subtype"] == "success" and result["session_id"] == observation["session"]["session_id"]
    assert captured["returncode"] == 0 and result["is_error"] is False
    assert result.get("structured_output") is None and json.loads(result["result"]) == raw_outputs[-1]["arguments"]
    assert len(observed_inputs) == sum(observed_inputs.values()) == 8
    assert result["usage"]["input_tokens"] == observation["response"]["token_usage"]["input_tokens"] == 8
    exchanges = sorted((source / "provider").glob("*/exchange.json"))
    assert len(exchanges) == 8
    for path in exchanges:
        exchange = json.loads(path.read_text())
        assert exchange["request_body_complete"] and exchange["response_body_complete"]
        assert exchange["failure_stage"] is None
    report = build_debug_report(tmp_path / "state", closure["run_id"], attempt_id=identity)
    projected = next(item for item in report["attempts"] if item["attempt_id"] == identity)
    stored = observation.get("diagnostics")
    # Save the independent raw-vs-durable comparison before the old-source red.
    (tmp_path / "successful-agent-diagnostics-evidence.json").write_text(json.dumps({
        "paid_model": False, "gpu": False, "design_attempt_id": identity,
        "agent_count": sum(item["kind"] == "AGENT" for item in attempts), "process_count": 1,
        "raw_result_path": str(source / "result.json"), "raw_correction_trace": trace,
        "stored_diagnostics": stored, "public_debug_attempt": projected,
        "raw_outputs": raw_outputs, "observed_input_by_message_id": observed_inputs,
        "oracle": closure["oracle"], "head": closure["head"],
        "owned_process_residue_after_close": closure["owned_process_residue_after_close"]}, indent=2))
    assert isinstance(stored, dict), "successful Host closure dropped actual adapter diagnostics"
    assert stored["structured_output_correction"] == trace
    assert stored["adapter_retry_policy"] == parser["adapter_retry_policy"]
    assert projected["structured_output_correction"] == trace
    assert projected["adapter_retry_policy"]["structured_output_failed_validation_cap"] == 3
    assert projected["terminal_reason"] == "SUCCEEDED" and projected["attempt_ordinal"] == 1
    assert projected["session_id"] == observation["session"]["session_id"]
    assert projected["retry_of_attempt_id"] is None and projected["recovered_by_later_attempt"] is False
    assert "CC" in projected["diagnostic_summary"] and "Host" in projected["diagnostic_summary"]
    assert "observation" not in projected and "response" not in projected
    assert closure["head"]["state"] == "COMPLETED" and closure["oracle"]["ok"]
    assert closure["oracle"]["canonical_outcome_count"] == 1
    assert closure["oracle"]["open_attempt_count"] == closure["oracle"]["pending_effect_count"] == 0
    assert closure["oracle"]["duplicate_effect_count"] == 0 and not closure["owned_process_residue_after_close"]
