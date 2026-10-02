"""Actual Host/CPU publication remains diagnosable from recent-failure copies."""
import json
import os

from hypoloom.agent_budget import usage_projection
from hypoloom.final_report import accepted_report_view
from hypoloom.oracle import Oracle
from hypoloom.support_bundle import build_support_bundle
from tests.runtime.test_shared_acceptance_host import RUN_ID, completed, shared_host as shared_host
from tests.runtime.test_web_observer import _row_census


def test_support_retains_real_terminal_sources_without_mutating_closed_host(shared_host, tmp_path):
    host, _ = shared_host
    completed(host)
    root = host.state_root
    attempts = host.store.list_attempts(run_id=RUN_ID)
    facts = host.store.list_facts(RUN_ID)
    source_report = accepted_report_view(RUN_ID, attempts)
    before = _row_census(root / "state.sqlite")
    contract = next(f["payload"]["research_contract"] for f in facts if f["fact_type"] == "GoalDefined")
    expected_usage = usage_projection(attempts, future_attempts=0, future_budget=None, document=contract)
    public, public_files = build_support_bundle(root, RUN_ID, recent_failures=True)
    private, files = build_support_bundle(root, RUN_ID, recent_failures=True, include_private_data=True)
    debug = json.loads(files["debug.json"])
    assert private["budget_usage"]["observed_lower_bound"] == expected_usage["observed_lower_bound"]
    assert private["budget_usage"]["profile_cumulative"]["cost"] == expected_usage["profile_cumulative"]["cost"]
    wanted = {f["fact_id"] for f in facts if f["fact_type"] in {"GoalDefined", "CanonicalOutcomeAccepted", "GoalVerdictRecorded"}}
    assert wanted <= {f["fact_id"] for f in debug["related_records"]["facts"]}
    assert wanted == {f["fact_id"] for f in private["run_evidence"]["facts"]}
    report_attempt = next(a for a in debug["attempts"] + debug["supporting_attempts"] if a["attempt_id"] == source_report["attempt_id"])
    artifacts = report_attempt["observation"]["response"]["proposed_workspace_artifacts"]
    assert any(a["path"] == "FINAL_REPORT.md" for a in artifacts)
    assert b"FINAL_REPORT.md" not in b"\n".join(public_files.values())
    assert public["run_evidence"]["final_report_attempt_ids"]
    assert _row_census(root / "state.sqlite") == before
    assert Oracle(root / "state.sqlite", root / "artifacts").audit(RUN_ID).ok
    assert all(a["state"] == "CLOSED" for a in attempts)
    assert not host.running and not host.running_agents
    for a in attempts:
        if a.get("process_group_id"):
            try:
                os.killpg(a["process_group_id"], 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("closed test process group remains live")
    (tmp_path / "support-closure.json").write_text(json.dumps({
        "run_id": RUN_ID, "attempts": len(attempts),
        "agents": sum(a["kind"] == "AGENT" for a in attempts),
        "processes": sum(a["kind"] == "PROCESS" for a in attempts),
        "outcomes": sum(f["fact_type"] == "CanonicalOutcomeAccepted" for f in facts),
        "verdicts": sum(f["fact_type"] == "GoalVerdictRecorded" for f in facts),
        "terminal_fact_ids": sorted(wanted), "state_unchanged": True,
        "public_file_count": len(public_files), "private_file_count": len(files),
        "private_limited": private["limited"], "oracle_ok": True, "owned_processes_absent": True}, indent=2))
