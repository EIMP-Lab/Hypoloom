"""A native failed StructuredOutput call must retain complete CLI accounting.

The existing shared-report rehearsal supplies real CC/bwrap, one CPU experiment,
Host retry, publication and Oracle. The local provider emits ordinary final JSON
text; no cost is reconstructed from its scripted tokens.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from hypoloom.agent_budget import fold_agent_attempts

import test_claude_shared_final_retry_g3 as shared_retry
from test_claude_shared_acceptance_g3 import _RUN
from test_full_cc_host_g3 import _rows


def _input_tokens(usage):
    return sum(usage.get(key, 0) for key in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def test_native_structured_failure_accounts_final_exchange_before_same_session_retry(tmp_path: Path):
    shared_retry.test_native_shared_final_report_retries_with_one_common_input_and_same_session(tmp_path)
    attempts = _rows(tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    finals = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"
              and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT"]
    failed, retry = finals
    assert failed[0]["terminal_reason"] == "FAILED" and retry[0]["terminal_reason"] == "SUCCEEDED"
    assert failed[1]["failure_code"] == "invalid_structured_output" and failed[1]["retry_class"] == "MODEL"
    assert failed[1].get("response") is None
    assert failed[0]["command_id"] == retry[0]["command_id"]
    assert retry[1]["session"]["session_id"] != failed[1]["session"]["session_id"]
    assert retry[1]["session"]["branch"] == failed[1]["session"]["branch"]
    assert retry[1]["session"]["launch_arguments"] == ["--session-id", retry[1]["session"]["session_id"]]

    diagnostic_root = tmp_path / "state/diagnostics/agents" / _RUN / failed[0]["attempt_id"]
    source = diagnostic_root / "stdout.log"
    records = [json.loads(line) for line in source.read_text().splitlines()]
    captured = json.loads((diagnostic_root / "result.json").read_text())
    assert captured["returncode"] == 0 and captured["terminal_reason"] == "INVALID_OUTPUT"
    assert captured["failure_code"] == "invalid_structured_output"
    outer = records[-1]
    assert outer["type"] == "result" and outer["subtype"] == "success" and outer["is_error"] is False
    assert outer.get("structured_output") is None
    assert json.loads(outer["result"])["typed_candidate"]["supporting_refs"] == [shared_retry._INVALID_REF]
    correction = failed[1]["diagnostics"]["structured_output_correction"]
    assert correction["scope"] == "native_stop_validation" and correction["status"] == "unresolved"
    assert correction["call_count"] == correction["rejected_count"] == 2
    observed = {}
    for event in records:
        if event.get("type") == "assistant":
            message = event["message"]
            observed[message["id"]] = max(observed.get(message["id"], 0), _input_tokens(message["usage"]))
    assert len(observed) == 6 and set(observed.values()) == {1}
    blocks = [block for event in records if event.get("type") == "assistant" for block in event["message"]["content"]]
    assert [block["name"] for block in blocks if block.get("type") == "tool_use"] == ["Skill", "Read", "Write", "Bash"]
    assert any(block.get("type") == "text" and block.get("text") == outer["result"] for block in blocks)
    exchanges = []
    for response in sorted((diagnostic_root / "provider").glob("*/response.body")):
        raw = response.read_text()
        if raw.startswith("event:"):
            events = [json.loads(line.removeprefix("data: ")) for line in raw.splitlines() if line.startswith("data: ")]
            assert events[0]["type"] == "message_start" and events[-1]["type"] == "message_stop"
            message = events[0]["message"]
            final_usage = next(event["usage"] for event in events if event["type"] == "message_delta")
            usage = {**message["usage"], **final_usage}
        else:
            message = json.loads(raw)
            usage = message["usage"]
            assert message["stop_reason"] in {"tool_use", "end_turn"}
        assert _input_tokens(usage) == usage["output_tokens"] == 1
        exchanges.append({"response_path": str(response), "message_id": message["id"], "usage": usage})
    assert len(exchanges) == 6 and {item["message_id"] for item in exchanges} == set(observed)
    charges = {item.attempt_id: item for item in fold_agent_attempts(attempts)}
    charge = charges[failed[0]["attempt_id"]]
    closure = json.loads((tmp_path / "shared-final-retry-evidence.json").read_text())
    assert closure["agent_count"] == 12 and closure["process_count"] == 1
    assert closure["oracle"]["ok"] and closure["head"]["state"] == "COMPLETED"
    evidence = {"paid_model": False, "gpu": False, "agent_count": 12, "process_count": 1,
        "failed_attempt": failed[0]["attempt_id"], "retry_attempt": retry[0]["attempt_id"],
        "session_id": failed[1]["session"]["session_id"], "failed_cli_stdout": str(source),
        "complete_provider_exchanges": exchanges,
        "observed_input_by_message_id": observed, "cli_result": outer,
        "cli_returncode": captured["returncode"], "adapter_terminal_reason": captured["terminal_reason"],
        "diagnostics": failed[1]["diagnostics"],
        "charge": {"source": charge.source, "tokens": charge.tokens, "turns": charge.turns, "cost": str(charge.cost)},
        "head": closure["head"], "oracle": closure["oracle"]}
    (tmp_path / "structured-failure-accounting-evidence.json").write_text(json.dumps(evidence, indent=2))

    # Require an explicit complete native bill without accepting the failed
    # candidate or settling from token-derived prices. Preserve its actual
    # normal-exit output and semantic rejection before checking the charge.
    assert charge.source == "provider", evidence["charge"]
    accounting = failed[1]["diagnostics"]["provider_accounting"]
    assert accounting["token_usage"] == {"input_tokens": 6, "output_tokens": 6, "total_tokens": 12}
    assert charge.tokens == 12 and charge.turns == len(observed) == len(exchanges)
    assert failed[1]["diagnostics"]["num_turns"] == outer["num_turns"]
    assert charge.cost == Decimal(str(outer["total_cost_usd"])) > 0
    model_usage = outer["modelUsage"][failed[1]["request"]["required_model_identity"]]
    assert _input_tokens(outer["usage"]) == model_usage["inputTokens"] == 6
    assert outer["usage"]["output_tokens"] == model_usage["outputTokens"] == 6
    assert Decimal(str(model_usage["costUSD"])) == charge.cost
    assert "provider_accounting_error" not in failed[1]["diagnostics"]
    assert charges[retry[0]["attempt_id"]].source == "response"
