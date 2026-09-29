"""Task ownership changes displays, never transport identities or run evidence."""
import base64
import copy
import json

import pytest

from hypoloom.observer_scope import attempt_scopes, grounding_sharing
from hypoloom.observer_details import _sessions
from test_observer_summary import _attempt, _summary, _transition


def record(identity, command, *, role="scientist", kind=None, parent=None, accepted=False, **kwargs):
    row = _attempt(identity, node=command, role=role, **kwargs)
    value = json.loads(row["observation_json"])
    request = value["request"]
    if kind:
        request["task_context"] = {"kind": "AUXILIARY", "request_kind": kind, "run_id": "r"}
    else:
        request["task_context"]["kind"] = "RESEARCH_NODE"
    if parent:
        request["input_artifacts"] = [{"ref": identity + "--paper-question", "content_base64":
            base64.b64encode(json.dumps({"parent_attempt_id": parent}).encode()).decode()}]
    if accepted:
        value["response"] = {"typed_candidate": {"background": {}, "acceptance": {}}}
    value["diagnostics"] = {"provider_accounting": {
        "token_usage": {"total_tokens": 10}, "turn_count": 1, "cost": "0", "cost_currency": "USD"}}
    row["observation_json"] = json.dumps(value)
    return row


@pytest.mark.parametrize("task,command", [("GROUNDING_QUERY_PLAN", "LITERATURE_REVIEW"),
    ("GROUNDING", "GROUNDING"), ("OBSERVER", "OBSERVER"), ("GRAPH_EVOLVE", "GRAPH_EVOLVE"),
    ("FINAL_REPORT", "FINAL_REPORT"), ("DEBATE_JUDGE", "DEBATE_JUDGE")])
def test_global_tasks_preserve_execution_identity(task, command):
    row = record("g", command, kind=task)
    before = copy.deepcopy(row)
    assert attempt_scopes([row])["g"] == {"task_scope": "run", "owner_role": None, "task_kind": task}
    assert row == before


def test_reader_follows_actual_parent_and_missing_evidence_stays_unknown():
    rows = [record("g", "GROUNDING", kind="GROUNDING"),
        record("e", "LITERATURE_REVIEW"), record("leader", "LEADER", kind="LEADER"),
        record("debate", "DEBATE", kind="DEBATE"),
        record("gr", "PAPER_READ", kind="PAPER_READ", parent="g"),
        record("er", "PAPER_READ", kind="PAPER_READ", parent="e"),
        record("lost", "PAPER_READ", kind="PAPER_READ", parent="absent"),
        record("future", "FUTURE_UNKNOWN_TASK", role="grounding"),
        record("cycle", "PAPER_READ", kind="PAPER_READ", parent="cycle")]
    scopes = attempt_scopes(rows)
    assert {key for key, value in scopes.items() if value["task_scope"] == "run"} == {"g", "gr"}
    assert {key for key, value in scopes.items() if value["task_scope"] == "expert"} == {"e", "leader", "debate", "er"}
    assert {key for key, value in scopes.items() if value["task_scope"] == "unknown"} == {"lost", "future", "cycle"}


def test_scope_totals_partition_retries_without_changing_run_accounting(tmp_path):
    rows = [record("g1", "GROUNDING", kind="GROUNDING", reason="FAILED", command_id="g"),
        record("g2", "GROUNDING", kind="GROUNDING", start=30, command_id="g", accepted=True),
        record("e", "RESULT_ANALYSIS"), record("reader", "PAPER_READ", kind="PAPER_READ", parent="g2"),
        record("unknown", "FUTURE_UNKNOWN_TASK", role="grounding")]
    before = copy.deepcopy(rows)
    summary = _summary(tmp_path, attempts=rows)
    assert [item["role"] for item in summary["experts"]] == ["scientist"]
    own, = summary["experts"]
    assert own["accounting"]["reported_tokens"] == 10
    assert summary["global_tasks"]["accounting"]["reported_tokens"] == 30
    assert summary["unknown_tasks"]["accounting"]["reported_tokens"] == 10
    assert next(stage for stage in summary["global_tasks"]["stages"] if stage["command_id"] == "g")["failed_attempts"] == 1
    for key, total in summary["accounting"].items():
        assert total == sum(group["accounting"][key] for group in
            [own, summary["global_tasks"], summary["unknown_tasks"]])
    assert own["reliability"]["counts"]["agent_attempts"] == 1
    assert rows == before


def test_shared_result_requires_actual_reference_and_ignores_clarification():
    source = record("accepted", "GROUNDING", kind="GROUNDING", accepted=True)
    other = record("peer", "RESULT_ANALYSIS", role="peer", inputs=[{"ref": "accepted--response"}])
    unrelated = record("other", "RESULT_ANALYSIS", inputs=[{"ref": "old--response"}])
    value = grounding_sharing([source, other, unrelated], ["scientist", "peer"], "ACTIVE")
    assert value["state"] == "accepted" and value["source_attempt_id"] == "accepted"
    assert value["experts"] == [{"role": "scientist", "provided": False, "attempt_ids": []},
        {"role": "peer", "provided": True, "attempt_ids": ["peer"]}]
    observed = json.loads(source["observation_json"])
    observed["response"]["typed_candidate"]["clarification"] = {"question": "Which data?"}
    source["observation_json"] = json.dumps(observed)
    value = grounding_sharing([source, other], ["peer"], "WAITING_HUMAN")
    assert value["state"] == "waiting_human" and value["source_attempt_id"] is None
    assert not value["experts"][0]["provided"]


def test_sessions_filter_task_ownership_separately_from_execution_role():
    rows = [record("g", "GROUNDING", kind="GROUNDING"), record("e", "RESULT_ANALYSIS"),
        record("reader", "PAPER_READ", kind="PAPER_READ", parent="g")]
    def identities(**selectors):
        return {attempt["attempt_id"] for session in _sessions(rows, selectors)["sessions"] for attempt in session["attempts"]}
    assert identities(role="scientist") == {"e"}
    assert identities(task_scope="run") == {"g", "reader"}
    assert identities(execution_role="scientist") == {"g", "reader", "e"}
    assert identities(role="scientist", task_scope="run") == set()


def test_global_grounding_does_not_change_expert_component_progress():
    from types import SimpleNamespace
    from hypoloom.monitor import _component_views
    component = SimpleNamespace(component_id="goal", dependencies=(), required_inputs=(),
        tier=SimpleNamespace(value="primary"), owner_role="scientist", proposer_roles=("scientist",))
    grounding = SimpleNamespace(component_id="goal", role="scientist", state="OPEN",
        attempt_id="g", phase="grounding", **attempt_scopes([record("g", "GROUNDING", kind="GROUNDING")])["g"])
    contract = SimpleNamespace(components=(component,))
    view, = _component_views(contract, (), {}, {}, (grounding,), (), ())
    assert view.phase == "proposal" and view.open_attempt_ids == ()
    research = SimpleNamespace(component_id="goal", role="scientist", state="OPEN",
        attempt_id="e", phase="literature_review", **attempt_scopes([record("e", "LITERATURE_REVIEW")])["e"])
    view, = _component_views(contract, (), {}, {}, (grounding, research), (), ())
    assert view.phase == "literature_review" and view.open_attempt_ids == ("e",)


def test_queued_readers_count_under_parent_not_transport_role(tmp_path):
    rows = [record("g", "GROUNDING", kind="GROUNDING", state="OPEN")]
    commands = [{"command_id": "reader", "request_json": json.dumps({
        "task_context": {"expert_slot": "scientist"}, "paper_reading": {"parent_attempt_id": "g"}})}]
    result = _summary(tmp_path, attempts=rows, reading_commands=commands, transitions=[_transition(1)])
    assert result["experts"][0]["parallel_activity"]["readers"]["tasks"] == 0


def test_component_explains_exhausted_prerequisite_without_blocking_later_or_peer_work():
    from types import SimpleNamespace
    from hypoloom.monitor import _component_views
    component = SimpleNamespace(component_id="goal", dependencies=(), required_inputs=(),
        tier=SimpleNamespace(value="primary"), owner_role="scientist", proposer_roles=("scientist",))
    contract = SimpleNamespace(components=(component,))
    failure = SimpleNamespace(component_id="goal", task_scope="expert", owner_role="scientist",
        task_kind="LITERATURE_REVIEW", state="CLOSED", started_at=10, attempt_id="failed",
        phase="literature_review", failure_code="literature_fetch_failed", operational_status="recovery_exhausted")

    def view(*attempts):
        return _component_views(contract, (), {}, {}, attempts, (), ())[0]

    result = view(failure)
    assert result.state == "PENDING" and result.phase == "literature_review" and result.blocked
    assert "automatic recovery ended" in result.blocked_reasons[0]
    transient = SimpleNamespace(**{**vars(failure), "operational_status": None})
    assert not view(transient).blocked
    global_failure = SimpleNamespace(**{**vars(failure), "task_scope": "run", "owner_role": None})
    assert not view(global_failure).blocked
    later = SimpleNamespace(**{**vars(failure), "started_at": 30, "attempt_id": "later",
        "operational_status": None, "failure_code": None})
    assert not view(failure, later).blocked
    component.proposer_roles = ("scientist", "peer")
    assert not view(failure).blocked  # An unstarted peer is still eligible to propose.


def exhausted_literature_summary(tmp_path, role="scientist"):
    rows = [record(f"failed-{i}", "LITERATURE_REVIEW", role=role, reason="FAILED",
                   start=i * 10, end=i * 10 + 5, command_id="lit", chain=None) for i in range(1, 4)]
    for row in rows:
        observed = json.loads(row["observation_json"])
        observed.update(failure_code="literature_fetch_failed", failure_stage="literature_retrieval")
        row["observation_json"] = json.dumps(observed)
    transition = _transition(9, payload={"agent_final_failure": True, "attempt_id": "failed-3"})
    return _summary(tmp_path, attempts=rows, transitions=[transition], experts=[{"role": role}])


def test_tui_exhausted_literature_is_visible_and_clears_for_a_new_cycle(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from textual.app import App
    from textual.widgets import Static
    from hypoloom.tui import ExpertsPanel
    from test_monitor import _static_text

    snapshot = SimpleNamespace(overview=exhausted_literature_summary(tmp_path), status_source="offline-state-store",
        experts=[SimpleNamespace(role="scientist", slot_ordinal=1)], status={})

    class View(App):
        def compose(self):
            yield ExpertsPanel()

    async def exercise():
        app = View()
        async with app.run_test(size=(100, 38)) as pilot:
            panel = app.query_one(ExpertsPanel)
            panel.update_snapshot(snapshot)
            await pilot.pause()
            text = _static_text(panel.query_one(Static))
            assert "Literature preparation" in text and "3 attempts" in text
            assert "automatic recovery ended" in text and "literature_fetch_failed" in text
            snapshot.overview = _summary(tmp_path, attempts=[record("next", "LITERATURE_REVIEW", cycle=2, state="OPEN")])
            panel.update_snapshot(snapshot)
            await pilot.pause()
            text = _static_text(panel.query_one(Static))
            assert "automatic recovery ended" not in text and "1 OPEN" in text
    asyncio.run(exercise())


def test_tui_global_lane_survives_expert_filter_and_completed_refresh(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from textual.app import App
    from textual.widgets import Static
    from hypoloom.tui import ExpertsPanel
    from test_monitor import _static_text

    rows = [record("g", "GROUNDING", kind="GROUNDING", state="OPEN")]
    snapshot = SimpleNamespace(overview=_summary(tmp_path, attempts=rows), status_source="offline-state-store",
        experts=[SimpleNamespace(role="scientist", slot_ordinal=1)], status={})

    class View(App):
        def compose(self):
            yield ExpertsPanel()

    async def exercise():
        app = View()
        async with app.run_test(size=(80, 32)) as pilot:
            panel = app.query_one(ExpertsPanel)
            panel.update_snapshot(snapshot, "missing-expert")
            await pilot.pause()
            text = _static_text(panel.query_one(Static))
            assert "GLOBAL TASKS" in text and "GROUNDING" in text
            assert "No experts match" in text and "OPEN?" in text
            rows[0] = record("g", "GROUNDING", kind="GROUNDING", accepted=True)
            snapshot.overview = _summary(tmp_path, attempts=rows)
            panel.update_snapshot(snapshot)
            await pilot.pause()
            text = _static_text(panel.query_one(Static))
            assert "Grounding accepted" in text and "supplied to 0/1" in text
            assert "not started" in text
    asyncio.run(exercise())
