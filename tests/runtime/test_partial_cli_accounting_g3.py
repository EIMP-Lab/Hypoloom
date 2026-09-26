"""Native complete billing plus an explicitly in-memory partial-ledger control.

Reuse the actual shared-report/CPU/retry rehearsal. Adapter55's normal final
text drains all six responses, including both schema-invalid ones. Only a copy
of its terminal counters is fault-injected; no native transcript or DB is edited.
The actual adapter54 early-exit omission remains retained historical evidence,
not a claim about the successor's native behavior.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

from hypoloom.agent_aci import AgentRequest
from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.claude_agent_host import _provider_response

import test_structured_failure_accounting_g3 as complete_native
from test_claude_shared_acceptance_g3 import _RUN
from test_full_cc_host_g3 import _rows


def _input_tokens(usage):
    return sum(usage.get(key, 0) for key in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def test_native_partial_cli_accounting_retains_reservation_then_retry_closes_cpu(tmp_path: Path):
    complete_native.test_native_structured_failure_accounts_final_exchange_before_same_session_retry(tmp_path)
    attempts = _rows(tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    finals = []
    for row in attempts:
        if row["kind"] != "AGENT":
            continue
        observed = json.loads(row["observation_json"])
        if observed["request"]["command"] == "FINAL_REPORT":
            finals.append((row, observed))
    assert len(finals) == 2
    failed, retry = finals
    assert [row["terminal_reason"] for row, _ in finals] == ["FAILED", "SUCCEEDED"]
    assert failed[1]["failure_code"] == "invalid_structured_output" and failed[1]["retry_class"] == "MODEL"
    assert failed[1].get("response") is None

    source = tmp_path / "state/diagnostics/agents" / _RUN / failed[0]["attempt_id"] / "stdout.log"
    native_raw = source.read_bytes()
    records = [json.loads(line) for line in native_raw.splitlines()]
    inputs, calls, results = {}, [], {}
    for index, event in enumerate(records):
        if event.get("type") == "assistant":
            message = event["message"]
            identity = message["id"]
            inputs[identity] = max(inputs.get(identity, 0), _input_tokens(message["usage"]))
            calls.extend({"message_id": identity, "tool_use_id": block["id"],
                          "tool": block["name"], "record_index": index}
                         for block in message["content"] if block.get("type") == "tool_use")
        elif event.get("type") == "user":
            for block in event["message"]["content"]:
                if block.get("type") == "tool_result":
                    results[block["tool_use_id"]] = {
                        "is_error": block.get("is_error", False), "record_index": index}
    assert len(inputs) == 6 and set(inputs.values()) == {1}
    assert [call["tool"] for call in calls] == ["Skill", "Read", "Write", "Bash"]
    final_text_index = next(index for index in reversed(range(len(records)))
                            if records[index].get("type") == "assistant")
    for call in calls:
        result = results[call["tool_use_id"]]
        assert result["is_error"] is False
        assert call["record_index"] < result["record_index"] < final_text_index
    outer = records[-1]
    assert outer["type"] == "result" and outer["subtype"] == "success"
    observed_input = sum(inputs.values())
    request = AgentRequest.from_dict(failed[1]["request"])
    model = request.required_model_identity
    response, failure, native_diagnostics = _provider_response(request, model, native_raw,
        final_text_output=True)
    assert response is None and failure == "invalid_structured_output"
    assert "/typed_candidate/supporting_refs/0" in native_diagnostics["provider_diagnostic"]
    assert native_diagnostics["provider_accounting"]["token_usage"] == {
        "input_tokens": 6, "output_tokens": 6, "total_tokens": 12}
    native_charges = {charge.attempt_id: charge for charge in fold_agent_attempts(attempts)}
    native_charge = native_charges[failed[0]["attempt_id"]]
    assert native_charge.source == "provider"
    assert native_charge.cost == Decimal(str(outer["total_cost_usd"])) > 0

    # COUNTERFACTUAL ONLY: omit one exchange's counters from BOTH ledgers while
    # leaving every observed assistant message and the explicit native USD alone.
    # This tests the lower-bound guard, not a native55 omission or a new bill.
    injected_records = copy.deepcopy(records)
    injected_outer = injected_records[-1]
    for ledger, input_key, output_key in (
        (injected_outer["usage"], "input_tokens", "output_tokens"),
        (injected_outer["modelUsage"][model], "inputTokens", "outputTokens"),
    ):
        assert ledger[input_key] == ledger[output_key] == 6
        ledger[input_key] -= 1
        ledger[output_key] -= 1
        if "total_tokens" in ledger:
            ledger["total_tokens"] -= 2
    assert injected_records[:-1] == records[:-1]
    assert injected_outer["total_cost_usd"] == outer["total_cost_usd"]
    assert injected_outer["modelUsage"][model]["costUSD"] == outer["modelUsage"][model]["costUSD"]
    injected_raw = b"\n".join(json.dumps(event).encode() for event in injected_records) + b"\n"
    response, failure, diagnostics = _provider_response(request, model, injected_raw,
        final_text_output=True)
    assert response is None and failure == "invalid_provider_metadata"
    reported_input = _input_tokens(injected_outer["usage"])
    assert 0 < reported_input == injected_outer["modelUsage"][model]["inputTokens"] == 5 < observed_input
    assert diagnostics["provider_observed_input_tokens_lower_bound"] == observed_input
    assert diagnostics["provider_accounting_error"] == (
        "CLI reported input_tokens below observed assistant input lower bound")
    assert "provider_accounting" not in diagnostics
    partial = diagnostics["provider_reported_lower_bound"]
    assert partial["token_usage"] == {
        "input_tokens": reported_input, "output_tokens": 5, "total_tokens": 10}
    assert partial["turn_count"] == len(inputs) > 0
    assert diagnostics["num_turns"] == outer["num_turns"]
    assert partial["cost_currency"] == "USD"
    assert Decimal(partial["cost"]) == Decimal(str(outer["total_cost_usd"])) > 0

    # Exercise the real conservative fold on a memory-only observation. Keep the
    # actual native failure, fee and successful retry untouched in durable state.
    injected_attempt = {**failed[0], "observation_json": json.dumps({
        "request": failed[1]["request"], "failure_code": failure, "diagnostics": diagnostics})}
    injected_attempts = [injected_attempt if row["attempt_id"] == failed[0]["attempt_id"] else row
                         for row in attempts]
    charges = {charge.attempt_id: charge for charge in fold_agent_attempts(injected_attempts)}
    assert len(charges) == len(native_charges) == 12
    charge = charges[failed[0]["attempt_id"]]
    budget = failed[1]["request"]["budget"]
    assert (charge.source, charge.tokens, charge.turns, charge.cost) == (
        "declared", budget["max_tokens"], budget["max_turns"], Decimal(budget["max_cost"]))
    assert charge.tokens > partial["token_usage"]["total_tokens"]
    assert charge.cost > Decimal(partial["cost"])
    assert retry[1]["session"]["session_id"] == failed[1]["session"]["session_id"]
    assert retry[1]["session"]["branch"] == failed[1]["session"]["branch"]
    retry_source = source.parent.parent / retry[0]["attempt_id"] / "stdout.log"
    retry_records = [json.loads(line) for line in retry_source.read_text().splitlines()]
    retry_inputs = {}
    for event in retry_records:
        if event.get("type") == "assistant":
            message = event["message"]
            identity = message["id"]
            retry_inputs[identity] = max(retry_inputs.get(identity, 0), _input_tokens(message["usage"]))
    assert len(retry_inputs) == 5 and set(retry_inputs).isdisjoint(inputs)
    assert retry_records[-1]["type"] == "result" and retry_records[-1]["subtype"] == "success"
    assert _input_tokens(retry_records[-1]["usage"]) == sum(retry_inputs.values()) == 5
    assert retry[1]["response"]["token_usage"]["input_tokens"] == 5
    assert charges[retry[0]["attempt_id"]].source == "response"
    assert all(charges[identity] == native_charges[identity]
               for identity in charges if identity != failed[0]["attempt_id"])
    closure = json.loads((tmp_path / "shared-final-retry-evidence.json").read_text())
    assert closure["agent_count"] == 12 and closure["process_count"] == 1
    assert closure["oracle"]["ok"] and closure["head"]["state"] == "COMPLETED"
    assert source.read_bytes() == native_raw
    assert _rows(tmp_path / "state/state.sqlite",
        "SELECT * FROM execution_attempts ORDER BY started_at") == attempts
    (tmp_path / "partial-cli-accounting-evidence.json").write_text(json.dumps({
        "paid_model": False, "gpu": False, "agent_count": closure["agent_count"],
        "process_count": closure["process_count"], "final_attempts": closure["final_attempts"],
        "session_id": closure["session_id"],
        "native_complete": {"detail": str(tmp_path / "structured-failure-accounting-evidence.json"),
            "failed_cli_stdout": str(source), "failure_code": failed[1]["failure_code"],
            "observed_input_by_message_id": inputs, "raw_tool_identities": calls,
            "reported_input_tokens": _input_tokens(outer["usage"]),
            "failed_charge_source": native_charge.source, "failed_reported_cost": str(native_charge.cost),
            "retry_charge_source": native_charges[retry[0]["attempt_id"]].source,
            "retry_reported_cost": str(native_charges[retry[0]["attempt_id"]].cost),
            "retry_observed_input_by_message_id": retry_inputs, "retry_cli_stdout": str(retry_source)},
        "in_memory_fault_injection": {"label": "counterfactual_both_ledgers_omit_one_exchange",
            "native_omission_observed": False, "additional_provider_calls": 0,
            "provider_observed_input_tokens_lower_bound": observed_input,
            "provider_reported_lower_bound": partial,
            "provider_accounting_error": diagnostics["provider_accounting_error"],
            "failed_charge_source": charge.source,
            "declared_reservation_not_actual_fee": {
                "tokens": charge.tokens, "turns": charge.turns, "cost": str(charge.cost)},
            "explicit_native_cost_unchanged": True, "durable_attempts_and_stdout_unchanged": True},
        "oracle": closure["oracle"], "head": closure["head"]}, indent=2))
