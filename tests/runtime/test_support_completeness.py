"""Support exports retain causal evidence and explain bounded omissions."""
from copy import deepcopy
import json
import time
from types import SimpleNamespace

import pytest

import hypoloom.support_bundle as support
from hypoloom.support_evidence import select_evidence, mark_export_fidelity, trim_payloads
from hypoloom.support_presentation import support_profile_summary
from test_debug_mode import _seed_agent_retry


def _history():
    attempts = [{"attempt_id": f"a{i}", "command_id": f"c{i}", "kind": "AGENT",
        "state": "CLOSED", "terminal_reason": "FAILED", "started_at": i + 1.,
        "ended_at": i + 1.5, "retry_of_attempt_id": f"a{i-1}" if i in (1, 2) else None,
        "module": "DESIGN", "observation": {}} for i in range(6)]
    return {"run_id": "run", "attempts": attempts,
        "summary": {"first_unrecovered_failure_attempt_id": "a0"},
        "related_records": {}, "oracle": {}}


def test_recent_failures_keep_first_failure_retry_chain_and_accounting():
    projection = _history()
    projection["attempts"][0]["accounting"] = {"source": "declared", "cost_usd": "8"}
    selected, supporting, total = select_evidence(projection, None, True)
    assert selected == {"a3", "a4", "a5"} and total == 6
    assert supporting == {"a0", "a1", "a2"}
    assert projection["run_attempts"][0]["accounting"]["source"] == "declared"


def test_recent_failures_keep_terminal_facts_and_final_report_but_exact_stays_narrow():
    original = _history()
    original["attempts"].append({**original["attempts"][-1], "attempt_id": "final",
        "command_id": "final-command", "terminal_reason": "SUCCEEDED", "module": "FINAL_REPORT"})
    original["related_records"]["facts"] = [
        {"fact_id": "goal", "fact_type": "GoalDefined", "payload": {}},
        {"fact_id": "outcome", "fact_type": "CanonicalOutcomeAccepted",
         "execution_id": "execution", "payload": {"outcome": "CLEAN_REFUTATION"}},
        {"fact_id": "verdict", "fact_type": "GoalVerdictRecorded", "payload": {"verdict": "INCONCLUSIVE"}}]
    projection = deepcopy(original)
    _, supporting, _ = select_evidence(projection, None, True)
    assert "final" in supporting
    assert {r["fact_id"] for r in projection["related_records"]["facts"]} == {"goal", "outcome", "verdict"}
    projection = deepcopy(original)
    selected, supporting, _ = select_evidence(projection, "a5", False)
    assert selected == {"a5"} and not supporting


def test_budget_summary_preserves_current_fields_and_decimal_strings():
    budget = {"total_budget_usd": "150", "per_expert_budget_usd": "120",
        "run_tokens": 1000000, "max_retries": 8, "max_infrastructure_retries": 3,
        "agent_invocation": {"max_cost": "8", "token_enforcement_mode": "observe"}}
    summary = support_profile_summary({"research_contract": {"budget": budget}})
    assert summary["budget"] == budget


def test_trimmed_request_is_not_described_as_exported_input():
    source = {"attempts": [{"attempt_id": "a", "observation": {
        "request": {"text": "x" * 5000}, "response": {"status": "failed"}},
        "reconstructability": {"terminal_evidence": "EXACT_RECORDED_OUTPUT", "pure_contract_input": "AVAILABLE"}}]}
    exported = deepcopy(source)
    assert trim_payloads(exported, 1000, support._json_bytes)
    mark_export_fidelity(source, exported, "export_size_limit")
    fidelity = exported["attempts"][0]["reconstructability"]
    assert fidelity["pure_contract_input"] == "NOT_INCLUDED"
    assert fidelity["terminal_evidence"] == "EXACT_RECORDED_OUTPUT"


def test_deadline_discloses_unvisited_selected_scope(tmp_path, monkeypatch):
    root = tmp_path / "state"
    run_id, first, second = _seed_agent_retry(root)
    capture = root / "diagnostics/agents" / run_id / first
    capture.mkdir(parents=True)
    (capture / "result.json").write_text('{"status":"failed"}')
    ticks = iter([0.])
    monkeypatch.setattr(support, "time", SimpleNamespace(time=time.time, monotonic=lambda: next(ticks, 16.)))
    monkeypatch.setattr(support, "support_environment", lambda: {})
    report, files = support.build_support_bundle(root, run_id, include_private_data=True)
    assert report["limited"]
    omitted = [a for a in report["attachments"] if a["status"] == "not_inspected"]
    assert {a["attempt_id"] for a in omitted} >= {first, second}
    assert all(a["reason"] == "inspection_time_limit" for a in omitted)
    assert report["limits"]["inspection_seconds"] == 15
    assert b"inspection_time_limit" in files["README.md"]


def test_metadata_survives_private_wire_consuming_collection_window(tmp_path, monkeypatch):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    capture = root / "diagnostics/agents" / run_id / first
    for i in range(1, 4):
        exchange = capture / "provider" / f"{i:06d}"
        exchange.mkdir(parents=True)
        (exchange / "exchange.json").write_text(json.dumps({"exchange_number": i, "status": 200}))
        (exchange / "response.body").write_text("large-wire")
    elapsed = [0.]
    clean = support._clean_bytes
    def slow_body(raw, *args):
        result = clean(raw, *args)
        if raw == b"large-wire":
            elapsed[0] = 16.
        return result
    monkeypatch.setattr(support, "_clean_bytes", slow_body)
    monkeypatch.setattr(support, "time", SimpleNamespace(time=time.time, monotonic=lambda: elapsed[0]))
    monkeypatch.setattr(support, "support_environment", lambda: {})
    report, files = support.build_support_bundle(root, run_id, attempt_id=first, include_private_data=True)
    assert len([p for p in files if p.endswith("exchange.json")]) == 3
    assert any(a["status"] == "not_inspected" for a in report["attachments"])


@pytest.mark.parametrize("max_mib", [1, 20, 64])
def test_debug_allowance_is_disclosed_and_scales_with_package(tmp_path, monkeypatch, max_mib):
    monkeypatch.setattr(support, "support_environment", lambda: {})
    report, files = support.build_support_bundle(tmp_path / "absent", None, max_bytes=max_mib * 1024**2)
    assert report["limits"]["debug_max_bytes"] == max_mib * 1024**2 // 4
    assert sum(map(len, files.values())) <= report["limits"]["max_bytes"]


def test_run_budget_keeps_unselected_unknown_cost_and_aliases_roles(tmp_path, monkeypatch):
    import hypoloom.debug_mode as debug
    root = tmp_path / "state"
    run_id, first, second = _seed_agent_retry(root)
    rows = list(debug._read_debug_rows(root, run_id))
    private_role = "PRIVATE_ROLE_729"
    for row in rows[2]:
        observation = json.loads(row["observation_json"])
        observation["request"]["role"] = private_role
        observation["request"]["budget"]["max_cost"] = "8"
        if "response" in observation:
            observation["response"]["cost"] = "2"
        row["observation_json"] = json.dumps(observation)
    rows[4] = ({"fact_id": "goal", "fact_type": "GoalDefined", "payload": {"research_contract": {
        "roles": [{"id": private_role}], "budget": {"total_budget_usd": "150", "per_expert_budget_usd": "120"}}}},)
    monkeypatch.setattr(debug, "_read_debug_rows", lambda *a: tuple(rows))
    monkeypatch.setattr(support, "support_environment", lambda: {})
    report, files = support.build_support_bundle(root, run_id, attempt_id=second)
    usage = report["budget_usage"]
    assert usage["scope"] == "run"
    assert usage["observed_lower_bound"]["cost"] == "2"
    assert usage["unobserved_closed_attempts"] == 1
    assert usage["profile_cumulative"]["cost"]["charged_or_reserved"] == "10"
    assert usage["profile_cumulative"]["cost"]["remaining"] == "140"
    assert usage["profile_cumulative"]["roles"][0]["role"].startswith("role-")
    assert private_role.encode() not in b"\n".join(files.values())
    assert report["selection"]["selected_detail_count"] == 1


@pytest.mark.parametrize("source", ["rejected", "pre_model"])
def test_pre_model_capture_is_not_applicable(tmp_path, monkeypatch, source):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    projection = support.build_debug_report(root, run_id, include_payloads=True)
    projection["attempts"][0]["accounting"]["source"] = source
    monkeypatch.setattr(support, "build_debug_report", lambda *a, **k: deepcopy(projection))
    monkeypatch.setattr(support, "support_environment", lambda: {})
    report, _ = support.build_support_bundle(root, run_id, attempt_id=first, include_private_data=True)
    assert report["agent_captures"] == [{"attempt_id": first, "status": "not_applicable", "reason": "agent_not_launched"}]
    assert not any(a.get("attempt_id") == first and a["status"] == "unavailable" for a in report["attachments"])


def test_response_omission_does_not_mislabel_unchanged_input():
    source = {"attempts": [{"attempt_id": "a", "observation": {
        "request": {"budget": {"max_cost": "8"}}, "response": {"text": "x" * 5000}},
        "reconstructability": {"terminal_evidence": "EXACT_RECORDED_OUTPUT", "pure_contract_input": "AVAILABLE"}}]}
    exported = deepcopy(source)
    trim_payloads(exported, 1000, support._json_bytes)
    mark_export_fidelity(source, exported, "export_size_limit")
    fidelity = exported["attempts"][0]["reconstructability"]
    assert fidelity["pure_contract_input"] == "AVAILABLE"
    assert fidelity["terminal_evidence"] == "SUMMARY_ONLY"


def test_inspection_count_omission_is_distinct_from_package_file_limit(tmp_path, monkeypatch):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    capture = root / "diagnostics/agents" / run_id / first
    capture.mkdir(parents=True)
    for i in range(25):
        (capture / f"file-{i:02d}.txt").write_text("bounded body")
    monkeypatch.setattr(support, "support_environment", lambda: {})
    report, files = support.build_support_bundle(root, run_id, attempt_id=first, include_private_data=True, max_files=8)
    assert report["inspected_file_count"] == 16
    assert "inspection_file_limit" in report["coverage"]["limiting_reasons"]
    assert "export_size_or_file_limit" in report["coverage"]["limiting_reasons"]
    assert report["coverage"]["uninspected_file_count"] is None
    assert len(files) <= 8


def test_small_package_discloses_inventory_trim_in_readme_and_preview(tmp_path, monkeypatch):
    root = tmp_path / "state"
    run_id, first, _ = _seed_agent_retry(root)
    capture = root / "diagnostics/agents" / run_id / first
    capture.mkdir(parents=True)
    for i in range(200):
        (capture / f"file-{i:04d}.txt").write_text("small evidence")
    monkeypatch.setattr(support, "support_environment", lambda: {})
    report, files = support.build_support_bundle(root, run_id, attempt_id=first,
        include_private_data=True, max_bytes=65536)
    assert report["limited"] and not report["coverage"]["inventory_complete"]
    assert "report_size_limit" in report["coverage"]["limiting_reasons"]
    assert b"report_size_limit" in files["README.md"]
    count = report["attachments_omitted_from_inventory"]
    assert count == sum(report["coverage"]["attachment_status_counts"].values()) - len(report["attachments"])
    assert f"{count} attachments".encode() in files["README.md"]
    assert sum(map(len, files.values())) <= 65536
    assert len(files) == report["exported_file_count"]
    assert sum(map(len, files.values())) - len(files["report.json"]) == report["exported_payload_bytes"]
