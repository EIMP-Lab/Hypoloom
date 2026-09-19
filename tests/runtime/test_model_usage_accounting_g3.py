"""Real CC fallback JSON, Host billing and CPU/publication use one fresh run."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from hypoloom.agent_budget import fold_agent_attempts
from hypoloom.agent_sessions import _session_cost_state
from hypoloom.claude_agent_host import _session_usage_view
from hypoloom.domain import AgentCommand

import test_claude_file_tool_cli as provider_fixture
import test_claude_shared_final_retry_g3 as shared_retry
from test_claude_shared_acceptance_g3 import _RUN
from test_full_cc_host_g3 import _rows


def _normalized_input(usage):
    return sum(usage.get(key, 0) for key in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def test_native_model_usage_includes_fallback_json_and_closes_report_cpu_and_billing(tmp_path: Path, monkeypatch):
    start_provider = shared_retry._start_provider
    send_response = provider_fixture._send_response
    fallback = {"started": False, "completed": False}

    def start(scenario, key):
        record = scenario.record
        tool_calls = scenario.tool_calls

        def record_or_fallback(document):
            if fallback["started"] and not fallback["completed"]:
                assert document.get("stream") is not True
                assert document["messages"] == fallback["request"]["messages"]
                assert document["model"] == fallback["model"]
                fallback["serving"] = True
                return fallback["request_number"]
            return record(document)

        def calls_or_fallback(number):
            if fallback.get("serving"):
                assert number == fallback["request_number"]
                return fallback["calls"]
            return tool_calls(number)

        def send(handler, *, request_number, model, tool_calls):
            request = scenario.current
            final_output = any(tool == "StructuredOutput" for _, tool, _ in tool_calls)
            selected = (request.command is AgentCommand.FINAL_REPORT
                and request.attempt_id != scenario.rejected_final and final_output)
            if selected and not fallback["started"]:
                fallback.update(started=True, request_number=request_number, model=model,
                    calls=copy.deepcopy(tool_calls), request=copy.deepcopy(scenario.requests[-1]),
                    attempt_id=request.attempt_id)
                handler.send_response(200)
                handler.send_header("Content-Type", "text/event-stream")
                handler.end_headers()
                provider_fixture._send_event(handler, "message_start", {
                    "type": "message_start", "message": {
                        "id": f"msg_partial_fallback_{request_number}", "type": "message",
                        "role": "assistant", "model": model, "content": [],
                        "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": 17, "cache_creation_input_tokens": 3,
                                  "cache_read_input_tokens": 5, "output_tokens": 0}}})
                # HTTP/1.0 closes here, before any tool or terminal stream event.
                # CC itself must request its complete nonstream fallback.
                return
            if fallback.get("serving"):
                final_text = (getattr(handler, "structured_output_as_text", False)
                    and len(tool_calls) == 1 and tool_calls[0][1] == "StructuredOutput")
                body = {"id": f"msg_complete_fallback_{request_number}", "type": "message",
                    "role": "assistant", "model": model,
                    "content": [{"type": "text", "text": json.dumps(tool_calls[0][2],
                        ensure_ascii=True, separators=(",", ":"))}] if final_text else [
                        {"type": "tool_use", "id": identity, "name": tool, "input": arguments}
                        for identity, tool, arguments in tool_calls],
                    "stop_reason": "end_turn" if final_text else "tool_use", "stop_sequence": None,
                    "usage": {"input_tokens": 17, "cache_creation_input_tokens": 3,
                              "cache_read_input_tokens": 5, "output_tokens": 7}}
                payload = json.dumps(body).encode()
                handler.send_response(200)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(payload)))
                handler.end_headers()
                handler.wfile.write(payload)
                fallback.update(completed=True, serving=False, response=body)
                return
            send_response(handler, request_number=request_number, model=model, tool_calls=tool_calls)

        scenario.record = record_or_fallback
        scenario.tool_calls = calls_or_fallback
        monkeypatch.setattr(provider_fixture, "_send_response", send)
        return start_provider(scenario, key)

    monkeypatch.setattr(shared_retry, "_start_provider", start)
    shared_retry.test_native_shared_final_report_retries_with_one_common_input_and_same_session(tmp_path)
    assert fallback["started"] and fallback["completed"]
    evidence = _assert_closed_billing(tmp_path)
    assert evidence["final_attempts"][1]["attempt_id"] == fallback["attempt_id"]
    (tmp_path / "model-usage-accounting-evidence.json").write_text(json.dumps(evidence, indent=2))


def _assert_closed_billing(tmp_path: Path):
    """Read-only assertions also support explicit rechecking of ended fixtures."""
    attempts = _rows(tmp_path / "state/state.sqlite", "SELECT * FROM execution_attempts ORDER BY started_at")
    finals = [(row, json.loads(row["observation_json"])) for row in attempts if row["kind"] == "AGENT"
              and json.loads(row["observation_json"])["request"]["command"] == "FINAL_REPORT"]
    assert len(finals) == 2
    failed, accepted = finals
    charges = {item.attempt_id: item for item in fold_agent_attempts(attempts)}
    # Since CC 2.1.277 the result event's modelUsage/total_cost_usd accumulate
    # over a resumed session (saved cost-state records); the archived prior
    # transcript holds exactly the carry the next invocation was seeded with.
    baselines: dict[str, Mapping[str, object] | None] = {}
    previous = None
    for row, observation in finals:
        baselines[row["attempt_id"]] = None if previous is None else _session_cost_state(
            (tmp_path / "state/artifacts" / previous["session"]["transcript"]["path"]
             / "transcript.jsonl").read_bytes())
        previous = observation
    records = {}
    checks = []
    for row, observation in finals:
        source = tmp_path / "state/diagnostics/agents" / _RUN / row["attempt_id"] / "stdout.log"
        records[row["attempt_id"]] = [json.loads(line) for line in source.read_text().splitlines()]
        events = records[row["attempt_id"]]
        outer = events[-1]
        view = _session_usage_view(outer, baselines[row["attempt_id"]])
        model = view["modelUsage"][observation["request"]["required_model_identity"]]
        model_input = model["inputTokens"] + model["cacheCreationInputTokens"] + model["cacheReadInputTokens"]
        observed = {}
        for event in events:
            if event.get("type") == "assistant":
                message = event["message"]
                observed[message["id"]] = max(observed.get(message["id"], 0), _normalized_input(message["usage"]))
        assert Decimal(str(model["costUSD"])) == Decimal(str(view["total_cost_usd"]))
        diagnostics = observation["diagnostics"]
        assert diagnostics["provider_usage_normalization"]["source"] == "modelUsage"
        charge = charges[row["attempt_id"]]
        if row["terminal_reason"] == "FAILED":
            # Both native validations drain their rejected final responses. The
            # existing Host schema rejection keeps this explicit native bill.
            assert model_input == _normalized_input(outer["usage"]) == sum(observed.values()) == 6
            assert model["outputTokens"] == outer["usage"]["output_tokens"] == 6
            accounting = diagnostics["provider_accounting"]
            assert charge.source == "provider"
            assert charge.tokens == accounting["token_usage"]["total_tokens"]
            assert charge.cost == Decimal(str(view["total_cost_usd"]))
        else:
            assert model_input == sum(observed.values()) == 29 > _normalized_input(outer["usage"]) == 4
            accounting = diagnostics["provider_accounting"]
            assert charge.source == "response"
            assert charge.tokens == accounting["token_usage"]["total_tokens"]
            assert charge.cost == Decimal(str(view["total_cost_usd"]))
        assert accounting["token_usage"] == {"input_tokens": model_input,
            "output_tokens": model["outputTokens"], "total_tokens": model_input + model["outputTokens"]}
        checks.append({"attempt_id": row["attempt_id"], "terminal_reason": row["terminal_reason"],
            "cli_stdout": str(source), "outer_usage": outer["usage"], "model_usage": model,
            "cli_session_totals": {"model_usage": outer["modelUsage"][
                observation["request"]["required_model_identity"]],
                "total_cost_usd": outer["total_cost_usd"]},
            "observed_input_by_message_id": observed, "cli_reported_accounting": accounting,
            "charge_source": charge.source, "charge_cost": str(charge.cost)})
    assert failed[1].get("response") is None and failed[1]["failure_code"] == "invalid_structured_output"
    assert accepted[1]["response"]["token_usage"] == accepted[1]["diagnostics"]["provider_accounting"]["token_usage"]
    outer = records[accepted[0]["attempt_id"]][-1]
    model = _session_usage_view(outer, baselines[accepted[0]["attempt_id"]])[
        "modelUsage"][accepted[1]["request"]["required_model_identity"]]
    assert model["inputTokens"] - outer["usage"]["input_tokens"] == 17
    assert model["cacheCreationInputTokens"] - outer["usage"].get("cache_creation_input_tokens", 0) == 3
    assert model["cacheReadInputTokens"] - outer["usage"].get("cache_read_input_tokens", 0) == 5
    assert model["outputTokens"] - outer["usage"]["output_tokens"] == 7
    accepted_root = tmp_path / "state/diagnostics/agents" / _RUN / accepted[0]["attempt_id"]
    responses = sorted((accepted_root / "provider").glob("*/response.body"))
    assert len(responses) == 6
    partial = responses[-2].read_text()
    assert "event: message_start" in partial and "event: message_stop" not in partial
    complete = json.loads(responses[-1].read_text())
    assert complete["usage"] == {"input_tokens": 17, "cache_creation_input_tokens": 3,
                                 "cache_read_input_tokens": 5, "output_tokens": 7}
    assert json.loads(responses[-2].with_name("request.body").read_text())["stream"] is True
    assert json.loads(responses[-1].with_name("request.body").read_text()).get("stream") is not True
    closure = json.loads((tmp_path / "shared-final-retry-evidence.json").read_text())
    assert closure["agent_count"] == 12 and closure["process_count"] == 1
    assert closure["oracle"]["ok"] and closure["head"]["state"] == "COMPLETED"
    return {
        "paid_model": False, "gpu": False, "agent_count": closure["agent_count"],
        "process_count": closure["process_count"], "final_attempts": checks,
        "fallback_response_usage": complete["usage"],
        "session_id": closure["session_id"], "head": closure["head"], "oracle": closure["oracle"]}
