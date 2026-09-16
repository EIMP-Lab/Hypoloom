"""Final-report retries preserve frozen inputs, with or without common acceptance."""

from __future__ import annotations

import json

import pytest

from hypoloom.domain import AgentCommand, AttemptTerminalReason
from hypoloom.fake_agent import FakeAgent, FakeAgentMode
from hypoloom.final_report import accepted_report_view
from hypoloom.oracle import Oracle

from tests.runtime.test_host_experiment_tools import _until
from tests.runtime.test_shared_acceptance_host import RUN_ID, shared_host as shared_host


@pytest.mark.parametrize("common_ready", [True, False], ids=["accepted-common", "missing-common"])
@pytest.mark.parametrize("failure_class", ["MODEL", "INFRASTRUCTURE"])
def test_final_report_retry_keeps_common_input_once_and_completes(
    shared_host, monkeypatch, tmp_path, common_ready, failure_class,
):
    host, agent = shared_host
    invoke = agent.invoke
    failed_model = FakeAgent(FakeAgentMode.NONZERO_EXIT)
    reports = []

    def report_choices(request):
        if request.command is AgentCommand.GROUNDING and not common_ready:
            return failed_model.invoke(request)
        if request.command is AgentCommand.FINAL_REPORT:
            reports.append(request)
            if len(reports) == 1 and failure_class == "MODEL":
                return failed_model.invoke(request)
        return invoke(request)

    monkeypatch.setattr(agent, "invoke", report_choices)
    finish = host._finish_fake_agent

    def finish_with_one_infrastructure_failure(request, raw):
        if (request.command is AgentCommand.FINAL_REPORT
                and request.attempt_ordinal == 1 and failure_class == "INFRASTRUCTURE"):
            # FakeAgent has no provider transport. Supply its existing failure
            # observation at the same Host seam as the retry-context regressions.
            host._agent_failure(request, AttemptTerminalReason.FAILED, "nonzero_exit",
                retryable=True, details={"returncode": 23, "diagnostics": {
                    "provider_subtype": "error_during_execution",
                    "failure": "bounded final-report provider interruption"}})
            return
        finish(request, raw)

    monkeypatch.setattr(host, "_finish_fake_agent", finish_with_one_infrastructure_failure)
    _until(host, lambda: host.store.latest_transition(RUN_ID)["state"] == "COMPLETED")
    assert len(reports) == 2
    first, retry = reports
    assert first.attempt_id != retry.attempt_id
    assert (first.attempt_ordinal, retry.attempt_ordinal) == (1, 2)
    assert first.command_id == retry.command_id and first.task_context == retry.task_context
    assert first.immutable_context_refs == retry.immutable_context_refs
    assert tuple(item for item in retry.input_artifacts
                 if item.ref in retry.immutable_context_refs) == first.input_artifacts
    for request in reports:
        refs = [item.ref for item in request.input_artifacts]
        assert len(refs) == len(set(refs))
        shared = [item for item in request.input_artifacts if item.ref.endswith("--shared-acceptance")]
        assert len(shared) == int(common_ready)
        if common_ready:
            assert shared[0] == host.shared_acceptance.input(RUN_ID)
            assert shared[0].ref in request.immutable_context_refs
    correction = [item for item in retry.input_artifacts if item.ref.endswith("--agent-retry-diagnostic")]
    if failure_class == "MODEL":
        assert len(correction) == 1
        assert correction[0].ref == first.attempt_id + "--agent-retry-diagnostic"
        assert correction[0].ref not in retry.immutable_context_refs
        assert json.loads(correction[0].content_bytes())["source_attempt_id"] == first.attempt_id
        assert len(retry.input_artifacts) == len(first.input_artifacts) + 1
    else:
        assert not correction and retry.input_artifacts == first.input_artifacts
    attempts = host.store.list_attempts()
    final_attempts = [row for row in attempts if row["command_id"] == first.command_id]
    assert [row["terminal_reason"] for row in final_attempts] == ["FAILED", "SUCCEEDED"]
    assert json.loads(final_attempts[0]["observation_json"])["retry_class"] == failure_class
    assert all(row["state"] == "CLOSED" for row in attempts)
    verdicts = host.store.list_facts(RUN_ID, "GoalVerdictRecorded")
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["verdict"] == ("GOAL_MET" if common_ready else "INCONCLUSIVE")
    assert len(host.store.list_facts(RUN_ID, "CanonicalOutcomeAccepted")) == int(common_ready)
    if not common_ready:
        assert host.shared_acceptance.source(RUN_ID) is None
        for kind in ("HypothesisProposed", "ExperimentPreregistered", "HypothesisAssessed"):
            assert not host.store.list_facts(RUN_ID, kind)
    report = accepted_report_view(RUN_ID, attempts)
    assert report["attempt_id"] == retry.attempt_id
    assert report["verdict"] == verdicts[0]["payload"]["verdict"]
    audit = Oracle(host.store.database_path, host.publisher.root).audit(RUN_ID)
    assert audit.ok, audit.to_dict()
    assert audit.open_attempt_count == audit.pending_effect_count == 0
    assert not audit.owned_process_residue
    (tmp_path / "final-report-retry-evidence.json").write_text(json.dumps({
        "common_ready": common_ready, "failure_class": failure_class,
        "requests": [request.to_dict() for request in reports], "attempts": final_attempts,
        "report": report, "verdicts": verdicts, "oracle": audit.to_dict()}, indent=2))
