from __future__ import annotations

import asyncio
import ast
import base64
import copy
import importlib
import io
import json
import socket
import sqlite3
import threading
import time
from dataclasses import replace
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from hypoloom.agent_aci import required_output_schema
from hypoloom.cli import main as cli_main
from hypoloom.control_protocol import ControlRequest
from hypoloom.monitor import (
    AttemptView,
    ComponentView,
    HypothesisView,
    RendezvousView,
    WorkflowView,
    _attempt_phase,
    _attempt_reported_tokens,
    _component_views,
    _expert_lineage_termination_components,
    _session_transcript_path,
    _transcript_context_tokens,
    _with_live_attempt_context,
    build_monitor_snapshot,
    build_status_projection,
    main as monitor_main,
    run_monitor,
)
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.fake_agent import default_request
from hypoloom.profile_admission import admit_profile
from hypoloom.state_store import StateTransaction
from hypoloom.workflow_host import WorkflowHost


_REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("diagnostics,expected", [
    ({"provider_subtype": "error_max_budget_usd"}, "error_max_budget_usd"),
    ({"provider_diagnostic": "Reached maximum budget ($2); token=SECRET"}, "error_max_budget_usd"),
    ({}, "provider_not_success"),
])
def test_final_agent_failure_projects_a_safe_code_and_remains_visible_after_completion(tmp_path, diagnostics, expected):
    from hypoloom.monitor import _attempt_views
    from hypoloom.tui import SummaryPanel
    from textual.app import App
    from textual.widgets import Static
    row = {"attempt_id": "failed-call", "kind": "AGENT", "state": "CLOSED", "workflow_id": "expert",
        "command_type": "LITERATURE", "transition_payload_json": "{}",
        "observation_json": json.dumps({"diagnostics": diagnostics}),
        "attempt_ordinal": 1, "terminal_reason": "FAILED", "process_group_id": None,
        "started_at": 1., "ended_at": 2.}
    reader = SimpleNamespace(list_attempts=lambda **_: [row], execute=lambda sql, *_: SimpleNamespace(fetchall=lambda:
        [("failed-call", "provider_not_success")] if "agent_final_failure" in sql else [row]))
    attempts, _ = _attempt_views(reader, "run", (), {}, {}, frozenset())
    assert attempts[0].operational_reason == expected
    root = tmp_path / "budget-hud"
    _create_fresh_run(root, "budget-hud")
    snapshot = build_monitor_snapshot(root, "budget-hud")
    snapshot = replace(snapshot, attempts=attempts, status={**snapshot.status, "run_state": "COMPLETED"})
    class TestApp(App):
        def compose(self):
            yield SummaryPanel()
    async def exercise():
        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(SummaryPanel)
            panel.update_snapshot(snapshot)
            await pilot.pause()
            text = _static_text(panel.query_one(Static))
            assert expected in text and "SECRET" not in text
            assert "Recorded final node failures: 1" in text
            if expected == "error_max_budget_usd":
                assert "BUDGET LIMIT" in text
            assert "calls terminated" not in text
    asyncio.run(exercise())


def test_monitor_mounts_before_initial_full_snapshot(tmp_path, monkeypatch):
    from hypoloom import tui
    root = tmp_path / "initial-audit"
    _create_fresh_run(root, "initial-audit")
    mounted = []
    def slow_snapshot(*args, **kwargs):
        raise AssertionError("full snapshot must not precede mounting TUI")
    monkeypatch.setattr("hypoloom.monitor.build_observer_snapshot", slow_snapshot)
    monkeypatch.setattr(tui, "run_tui", lambda *args, **kwargs: mounted.append((args, kwargs)))
    assert run_monitor(root, "initial-audit", json_output=False, web_enabled=False) == 0
    assert len(mounted) == 1 and mounted[0][1].get("initial_snapshot") is None


@pytest.mark.parametrize("web_enabled", [False, True])
def test_tui_initial_snapshot_and_reentry_do_not_wait_for_full_audit(tmp_path, monkeypatch, web_enabled):
    from hypoloom.tui import HypoloomVNextMonitor
    root = tmp_path / "slow-audit-reentry"
    _create_fresh_run(root, "slow-audit-reentry")
    started, release = threading.Event(), threading.Event()
    def slow_audit(reader):
        started.set()
        release.wait(30)
        return {"ok": True, "issues": []}
    monkeypatch.setattr("hypoloom.observer_refresh.ObserverRefresh._audit", slow_audit)
    async def exercise():
        port = 0
        for _ in range(2):
            app = HypoloomVNextMonitor(root, "slow-audit-reentry", interval=60,
                web_enabled=web_enabled, web_port=port)
            async with app.run_test(size=(120, 40)) as pilot:
                for _ in range(50):
                    await pilot.pause(0.05)
                    if app._last_snapshot is not None: break
                assert started.is_set() and not release.is_set(), _static_text(app.query_one("#freshness"))
                assert app._last_snapshot.oracle["observation"]["state"] == "pending"
                if web_enabled:
                    assert app._web_server is not None
                    port = app._web_server.server_address[1]
                app.exit()
            assert app._web_server is None
    try:
        asyncio.run(exercise())
    finally:
        release.set()


def _static_text(widget):
    """Read rendered text from either plain Static content or Rich cards."""
    from rich.console import Console
    output = io.StringIO()
    Console(file=output, width=max(20, widget.content_size.width), color_system=None).print(widget.content)
    return output.getvalue()


def _patch_tui_reader(monkeypatch, builder):
    monkeypatch.setattr("hypoloom.tui.ObserverRefresh.get",
        lambda reader, **kwargs: {"snapshot": builder(reader.root, reader.run_id)})


def _dependency_component_views(
    *, assessment: str, outcome: str,
    expert_lineage_terminated: frozenset[str] | None = None,
) -> dict[str, ComponentView]:
    tier = SimpleNamespace(value="must")
    upstream = SimpleNamespace(
        component_id="must.upstream",
        dependencies=(),
        required_inputs=(),
        tier=tier,
        owner_role="scientist-a",
        proposer_roles=("scientist-a",),
    )
    ordinary = SimpleNamespace(
        component_id="must.ordinary-consumer",
        dependencies=("must.upstream",),
        required_inputs=(),
        tier=tier,
        owner_role="scientist-b",
        proposer_roles=("scientist-b",),
    )
    input_consumer = SimpleNamespace(
        component_id="must.input-consumer",
        dependencies=("must.upstream",),
        required_inputs=(SimpleNamespace(produced_by_component="must.upstream"),),
        tier=tier,
        owner_role="scientist-c",
        proposer_roles=("scientist-c",),
    )
    views = _component_views(
        SimpleNamespace(components=(upstream, ordinary, input_consumer)),
        ({
            "workflow_id": "upstream-experiment",
            "workflow_kind": "EXPERIMENT",
            "state": "TERMINAL",
        },),
        {"upstream-experiment": "must.upstream"},
        {},
        (),
        ({"payload": {
            "component_id": "must.upstream",
            "assessment": assessment,
        }},),
        ({
            "experiment_id": "upstream-experiment",
            "payload": {
                "component_id": "must.upstream",
                "outcome": outcome,
                "method_family_id": "family-a",
            },
        },),
        expert_lineage_terminated,
    )
    return {item.component_id: item for item in views}


def _revision_warning_component_view(
    cycle_revisions: int, *, cycle: int = 1,
) -> ComponentView:
    from hypoloom.observer_revisions import warning_level

    contract = SimpleNamespace(components=(SimpleNamespace(
        component_id="primary.probe",
        dependencies=(),
        required_inputs=(),
        tier=SimpleNamespace(value="primary"),
        owner_role="scientist-a",
        proposer_roles=("scientist-a",),
    ),))
    views = _component_views(
        contract,
        (),
        {},
        {},
        (),
        (),
        (),
        revision_activity={"primary.probe": {
            "mode": "legacy", "complete": True, "cycle": cycle,
            "operational_repairs": cycle_revisions,
            "scopes": [{"repairs": cycle_revisions, "historical": False,
                "warning": warning_level(cycle_revisions)}],
        }},
    )
    return views[0]


def test_component_revision_warning_levels_follow_the_breaker_thresholds() -> None:
    assert _revision_warning_component_view(0).revision_warning is None
    assert _revision_warning_component_view(10).revision_warning is None
    assert _revision_warning_component_view(11).revision_warning == "general"
    assert _revision_warning_component_view(20).revision_warning == "general"
    assert _revision_warning_component_view(21).revision_warning == "severe"
    assert _revision_warning_component_view(30).revision_warning == "severe"
    assert _revision_warning_component_view(31).revision_warning == "tripped"
    view = _revision_warning_component_view(12, cycle=4)
    assert view.cycle_revisions == 12
    assert view.current_cycle == 4


def test_monitor_blocks_ordinary_dependency_after_inconclusive_assessment() -> None:
    components = _dependency_component_views(
        assessment="INCONCLUSIVE",
        outcome="INTEGRITY_REJECTED",
    )

    ordinary = components["must.ordinary-consumer"]
    assert ordinary.blocked
    assert ordinary.blocked_reasons == ("dependencies:must.upstream",)


def test_monitor_unblocks_ordinary_dependency_after_supported_assessment() -> None:
    components = _dependency_component_views(
        assessment="SUPPORTED",
        outcome="SUCCESS",
    )

    ordinary = components["must.ordinary-consumer"]
    assert not ordinary.blocked
    assert ordinary.blocked_reasons == ()


def test_monitor_unblocks_input_dependency_after_comparable_clean_result() -> None:
    components = _dependency_component_views(
        assessment="REFUTED",
        outcome="CLEAN_REFUTATION",
    )

    ordinary = components["must.ordinary-consumer"]
    input_consumer = components["must.input-consumer"]
    assert ordinary.blocked
    assert ordinary.blocked_reasons == ("dependencies:must.upstream",)
    assert not input_consumer.blocked
    assert input_consumer.blocked_reasons == ()


def test_monitor_keeps_a_supported_dependency_blocked_until_expert_termination() -> None:
    components = _dependency_component_views(
        assessment="SUPPORTED",
        outcome="SUCCESS",
        expert_lineage_terminated=frozenset(),
    )

    ordinary = components["must.ordinary-consumer"]
    input_consumer = components["must.input-consumer"]
    assert ordinary.blocked_reasons == ("expert-lineage:must.upstream",)
    assert input_consumer.blocked_reasons == ("expert-lineage:must.upstream",)

    expert_contract = SimpleNamespace(document={
        "methodology": {"controls": {"research_loop": "expert-six-node/1"}}
    })
    assert _expert_lineage_termination_components(
        expert_contract, (), (), research_contract_version="hypoloom-research-v6",
    ) is None
    assert _expert_lineage_termination_components(
        expert_contract, (), (), research_contract_version="hypoloom-research-v7",
    ) == frozenset()
    assert _expert_lineage_termination_components(
        expert_contract, (), (), research_contract_version="hypoloom-research-v8",
    ) == frozenset()
    assert _expert_lineage_termination_components(
        expert_contract, (), (), research_contract_version="hypoloom-research-v9",
    ) == frozenset()


def test_monitor_selects_numeric_experiment_tip_after_ordinal_nine() -> None:
    tier = SimpleNamespace(value="must")
    upstream_a = SimpleNamespace(
        component_id="must.upstream-a", dependencies=(), required_inputs=(),
        tier=tier, owner_role="a", proposer_roles=("a",),
    )
    upstream_b = SimpleNamespace(
        component_id="must.upstream-b", dependencies=(), required_inputs=(),
        tier=tier, owner_role="b", proposer_roles=("b",),
    )
    consumer = SimpleNamespace(
        component_id="must.consumer",
        dependencies=("must.upstream-a", "must.upstream-b"),
        required_inputs=(
            SimpleNamespace(produced_by_component="must.upstream-a"),
            SimpleNamespace(produced_by_component="must.upstream-b"),
        ),
        tier=tier, owner_role="c", proposer_roles=("c",),
    )
    heads = (
        {"workflow_id": "experiment-a-10", "workflow_kind": "EXPERIMENT", "state": "TERMINAL"},
        {"workflow_id": "experiment-a-9", "workflow_kind": "EXPERIMENT", "state": "TERMINAL"},
        {"workflow_id": "experiment-b-1", "workflow_kind": "EXPERIMENT", "state": "TERMINAL"},
    )
    workflow_components = {
        "experiment-a-9": "must.upstream-a",
        "experiment-a-10": "must.upstream-a",
        "experiment-b-1": "must.upstream-b",
    }
    assessments = (
        {"payload": {"component_id": "must.upstream-a", "assessment": "SUPPORTED"}},
        {"payload": {"component_id": "must.upstream-b", "assessment": "SUPPORTED"}},
    )
    outcomes = (
        {"experiment_id": "experiment-a-9", "payload": {"component_id": "must.upstream-a", "outcome": "SUCCESS", "method_family_id": "old-family"}},
        {"experiment_id": "experiment-a-10", "payload": {"component_id": "must.upstream-a", "outcome": "SUCCESS", "method_family_id": "selected-family"}},
        {"experiment_id": "experiment-b-1", "payload": {"component_id": "must.upstream-b", "outcome": "SUCCESS", "method_family_id": "other-family"}},
    )
    experiment_ordinals = {
        "experiment-a-9": 9,
        "experiment-a-10": 10,
        "experiment-b-1": 1,
    }
    views = _component_views(
        SimpleNamespace(components=(upstream_a, upstream_b, consumer)),
        heads, workflow_components, {}, (), assessments, outcomes,
        experiment_ordinals=experiment_ordinals,
    )
    by_id = {item.component_id: item for item in views}

    assert by_id["must.upstream-a"].workflow_id == "experiment-a-10"
    assert by_id["must.consumer"].blocked_reasons == ()

    converged_views = _component_views(
        SimpleNamespace(components=(upstream_a, upstream_b, consumer)),
        heads, workflow_components, {}, (), assessments, outcomes,
        expert_lineage_terminated=frozenset({
            "must.upstream-a", "must.upstream-b",
        }),
        experiment_ordinals=experiment_ordinals,
    )
    converged_by_id = {item.component_id: item for item in converged_views}
    assert converged_by_id["must.consumer"].blocked_reasons == (
        "required-input-family:conflict",
    )


def test_aci9_and_retained_aci8_attempt_phases_include_subkind() -> None:
    request = default_request(AgentCommand.LITERATURE_REVIEW)
    assert isinstance(request.task_context, ResearchNodeTask)
    main_phase = _attempt_phase(
        "AGENT", request.command.value, {}, {}, request=request.to_dict(),
    )
    query_plan = replace(
        request,
        task_context=(task := ResearchNodeTask(key=replace(
            request.task_context.key,
            subkind=ResearchNodeSubkind.QUERY_PLAN,
        ))),
        command_id=task.command_id,
        required_output_schema=required_output_schema(request.command, task),
    )
    query_plan_phase = _attempt_phase(
        "AGENT", query_plan.command.value, {}, {}, request=query_plan.to_dict(),
    )
    retained = replace(request, schema_version=8)
    retained_plan = replace(
        query_plan,
        schema_version=8,
        required_output_schema=required_output_schema(
            request.command, task, schema_version=8,
        ),
    )
    historical = replace(request, schema_version=7)
    legacy = default_request(AgentCommand.PROPOSE_HYPOTHESIS)
    auxiliary = default_request(AgentCommand.GROUNDING)

    assert (query_plan_phase, main_phase) == (
        "literature_review/query_plan",
        "literature_review/main",
    )
    assert _attempt_phase(
        "AGENT", retained.command.value, {}, {}, request=retained.to_dict(),
    ) == "literature_review/main"
    assert _attempt_phase(
        "AGENT", retained_plan.command.value, {}, {}, request=retained_plan.to_dict(),
    ) == "literature_review/query_plan"
    assert _attempt_phase(
        "AGENT", historical.command.value, {}, {}, request=historical.to_dict(),
    ) == "literature_review"
    assert _attempt_phase(
        "AGENT", legacy.command.value, {}, {}, request=legacy.to_dict(),
    ) == "proposal"
    assert _attempt_phase(
        "AGENT", auxiliary.command.value, {}, {}, request=auxiliary.to_dict(),
    ) == "grounding"


def test_attempt_reported_tokens_accepts_settled_and_lower_bound_usage() -> None:
    settled = {"diagnostics": {"provider_accounting": {
        "token_usage": {"total_tokens": 45200}, "turn_count": 3}}}
    lower_bound = {"diagnostics": {
        "provider_reported_lower_bound": {"token_usage": {"total_tokens": 1200}}}}
    assert _attempt_reported_tokens(settled) == 45200
    assert _attempt_reported_tokens(lower_bound) == 1200
    assert _attempt_reported_tokens({"diagnostics": {}}) is None
    assert _attempt_reported_tokens({}) is None
    assert _attempt_reported_tokens({"diagnostics": {
        "provider_accounting": {"token_usage": {"total_tokens": "45200"}}}}) is None


def _session_observation(**overrides):
    observation = {
        "request": {"role": "ml-expert", "component_id": "primary.ml-expert_goal"},
        "session": {"scope": "chain-v1.abc", "cycle": 2, "session_id": "sid-1"},
    }
    for key, value in overrides.items():
        observation[key].update(value)
    return observation


def test_session_transcript_path_resolves_only_recorded_identities(tmp_path: Path) -> None:
    transcript = (tmp_path / "run/sessions/ml-expert/c2/primary.ml-expert_goal"
        / "chain-v1.abc/projects/sid-1.jsonl")
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    assert _session_transcript_path(tmp_path, "run", _session_observation()) == transcript.resolve()
    assert _session_transcript_path(tmp_path, "run", _session_observation(
        session={"session_id": "missing"})) is None
    assert _session_transcript_path(tmp_path, "run", _session_observation(
        session={"scope": ".."})) is None
    assert _session_transcript_path(tmp_path, "run", _session_observation(
        session={"cycle": None})) is None
    assert _session_transcript_path(tmp_path, "run", {"request": {}, "session": {}}) is None


def test_session_transcript_path_falls_back_to_open_attempt_session_binding(tmp_path: Path) -> None:
    transcript = (tmp_path / "run/sessions/ml-expert/c2/primary.ml-expert_goal"
        / "chain-v1.abc/projects/sid-1.jsonl")
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")

    def open_observation(**binding_overrides):
        binding = {"scope": "chain-v1.abc", "cycle": 2, "session_id": "sid-1",
                   "parent_session_id": None, "chain": None,
                   "branch": "prom/ml-expert/r2/chain-v1.abc", "version": "chain-session/1"}
        binding.update(binding_overrides)
        return {"request": {"role": "ml-expert", "component_id": "primary.ml-expert_goal"},
                "session_binding": binding}

    assert _session_transcript_path(tmp_path, "run", open_observation()) == transcript.resolve()
    assert _session_transcript_path(tmp_path, "run", open_observation(session_id="missing")) is None
    assert _session_transcript_path(tmp_path, "run", open_observation(scope="..")) is None
    assert _session_transcript_path(tmp_path, "run", open_observation(cycle="2")) is None


def test_transcript_context_tokens_reads_last_turn_usage_from_tail(tmp_path: Path) -> None:
    transcript = tmp_path / "sid-1.jsonl"
    records = [
        {"type": "user", "message": {"content": "hello"}},
        {"type": "assistant", "message": {"usage": {
            "input_tokens": 100, "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30, "output_tokens": 5}}},
        {"type": "assistant", "message": {"usage": {
            "input_tokens": 200, "output_tokens": 7}}},
    ]
    transcript.write_text("".join(json.dumps(item) + "\n" for item in records) + "{partial")
    assert _transcript_context_tokens(transcript) == 200
    transcript.write_text(json.dumps(records[1]) + "\n")
    assert _transcript_context_tokens(transcript) == 150
    transcript.write_text("{}\n")
    assert _transcript_context_tokens(transcript) is None
    assert _transcript_context_tokens(tmp_path / "absent.jsonl") is None


def test_live_attempt_context_overrides_only_open_agent_attempts(tmp_path: Path) -> None:
    transcript = (tmp_path / "run/sessions/ml-expert/c2/primary.ml-expert_goal"
        / "chain-v1.abc/projects/sid-1.jsonl")
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {
        "input_tokens": 321, "cache_read_input_tokens": 9, "output_tokens": 3}}}) + "\n")

    def attempt(attempt_id, kind, state):
        return AttemptView(
            attempt_id=attempt_id, kind=kind, state=state, workflow_id="workflow",
            command="LITERATURE_REVIEW", component_id="primary.ml-expert_goal",
            phase="literature_review/main", role="ml-expert", slot_ordinal=1,
            attempt_ordinal=1, terminal_reason=None, process_group_id=None,
            started_at=1.0, ended_at=None, gpu_uuid=None,
            minimum_free_memory_mib=None, latest_free_memory_mib=None,
            latest_utilization_percent=None, resource_pressure_exceeded=None,
            context_tokens=111,
        )

    opened = attempt("open-1", "AGENT", "OPEN")
    closed = attempt("closed-1", "AGENT", "CLOSED")
    process = attempt("process-1", "PROCESS", "OPEN")
    observations = {
        "open-1": {"request": {"role": "ml-expert", "component_id": "primary.ml-expert_goal"},
                   "session_binding": {"scope": "chain-v1.abc", "cycle": 2, "session_id": "sid-1",
                                       "parent_session_id": None, "chain": None,
                                       "branch": "prom/ml-expert/r2/chain-v1.abc",
                                       "version": "chain-session/1"}},
        "closed-1": _session_observation(),
        "process-1": _session_observation(),
    }
    rendered = {item.attempt_id: item for item in _with_live_attempt_context(
        tmp_path, "run", (opened, closed, process), observations)}
    assert rendered["open-1"].context_tokens == 330
    assert rendered["open-1"].context_source == "transcript"
    assert rendered["closed-1"].context_tokens == 111
    assert rendered["closed-1"].context_source is None
    assert rendered["process-1"].context_tokens == 111
    missing = _with_live_attempt_context(tmp_path, "run", (opened,), {})[0]
    assert missing.context_tokens == 111 and missing.context_source is None

    # An old CLOSED invocation uses its own wire capture, never a later turn
    # appended to the mutable shared session. Cumulative accounting stays separate.
    historical = replace(closed, attempt_id="old-wire", context_tokens=None, cumulative_tokens=3722840)
    diagnostic = tmp_path / "diagnostics/agents/run/old-wire/provider/000001"
    diagnostic.mkdir(parents=True)
    (diagnostic / "exchange.json").write_text(json.dumps({
        "request_path": "/v1/messages", "served_models": ["model"],
        "usage": {"input_tokens": 472337, "output_tokens": 10},
        "response_body_complete": True, "message_complete": True}))
    old_observation = {"old-wire": observations["open-1"]}
    observed = _with_live_attempt_context(tmp_path, "run", (historical,), old_observation)[0]
    assert observed.context_tokens == 472337 and observed.cumulative_tokens == 3722840
    assert observed.context_source == "provider_usage_final"
    no_wire = replace(historical, attempt_id="no-wire")
    assert _with_live_attempt_context(tmp_path, "run", (no_wire,),
        {"no-wire": observations["open-1"]})[0].context_tokens is None


@pytest.mark.parametrize("state", ["OPEN", "CLOSED"])
def test_rejected_context_preserves_prior_usage_without_assigning_it_to_current_request(tmp_path, state):
    from hypoloom.context_management import context_policy
    from hypoloom.tui import AttemptsPanel
    request = replace(default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT), schema_version=25)
    policy = context_policy(request)
    attempt = AttemptView(attempt_id=request.attempt_id, kind="AGENT", state=state,
        workflow_id=request.owner_workflow_id, command=request.command.value, component_id=request.component_id,
        phase="design", role=request.role, slot_ordinal=1, attempt_ordinal=1, terminal_reason=None,
        process_group_id=None, started_at=1, ended_at=None, gpu_uuid=None, minimum_free_memory_mib=None,
        latest_free_memory_mib=None, latest_utilization_percent=None, resource_pressure_exceeded=None,
        cumulative_tokens=3722840)
    session = {"scope": "chain", "cycle": 1, "session_id": "session"}
    observation = {"request": request.to_dict(), "session_binding": session}
    transcript = (tmp_path / "run/sessions" / request.role / "c1" / request.component_id
        / "chain/projects/session.jsonl")
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 999999}}}) + "\n")
    records = [{"exchange": 5, "upstream_status": 200, "usage": {"input_tokens": 59326},
        "response_body_complete": True, "message_complete": True},
        {"exchange": 6, "failure_reason": "context_capacity", "context_capacity": {
            **policy, "estimated_input_tokens": 211464, "fits": False,
            "estimate_source": "provider_usage_plus_delta_estimate", "estimate_reason": "unchanged_prefix"}}]
    for record in records:
        directory = tmp_path / "diagnostics/agents/run" / request.attempt_id / "provider" / f"{record['exchange']:06d}"
        directory.mkdir(parents=True)
        (directory / "exchange.json").write_text(json.dumps({"request_path": "/v1/messages", "served_models": ["model"], **record}))
    observed, = _with_live_attempt_context(tmp_path, "run", (attempt,), {request.attempt_id: observation})
    assert observed.context_tokens is None and observed.context_source == "estimate"
    assert observed.cumulative_tokens == 3722840
    assert observed.context["last_observed_input_tokens"] == 59326
    assert observed.context["last_observed_exchange"] == 5
    assert observed.context["current_request_exchange"] == 6
    assert observed.context["current_request_estimated_input_tokens"] == 211464
    lines = []
    AttemptsPanel.update_snapshot(SimpleNamespace(update=lines.append),
        SimpleNamespace(attempts=(observed,), observed_at=10))
    assert "last measured input=59326 · exchange=5 (earlier request)" in lines[0]
    assert "current request exchange=6 · provider input=pending or unavailable" in lines[0]
    assert "current request estimate=211464 · source=provider_usage_plus_delta_estimate" in lines[0]
    assert "999999" not in lines[0]


def _v4_payload(
    run_id: str, *, component_count: int = 1, delay_seconds: float = 0.0,
) -> dict[str, object]:
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        delay_seconds=delay_seconds,
    )
    assert admission.admitted and admission.create_run_payload is not None
    payload = copy.deepcopy(admission.create_run_payload)
    if component_count == 1:
        return payload

    contract = payload["research_contract"]
    roles = [str(item["id"]) for item in contract["roles"]]
    base = contract["components"][0]
    contract["protocols"] = {
        "parallel-score": {
            "result_kind": "metric",
            "requirements": {
                "required_metrics": ["score"],
                "required_artifacts": [],
                "artifact_validators": [],
            },
        },
    }
    contract["components"] = [
        {
            **copy.deepcopy(base),
            "id": f"primary.score-{index}",
            "name": f"score-{index}",
            "owner": roles[index - 1],
            "proposers": [roles[index - 1]],
            "protocol": "parallel-score",
            "result": {
                "kind": "metric",
                "metric": "score",
                "direction": "MAXIMIZE",
                "target": "1",
            },
        }
        for index in range(1, component_count + 1)
    ]
    contract["goal"]["summary_metric"] = {
        "name": "required_component_completion",
        "direction": "MAXIMIZE",
        "target": str(component_count),
        "display_only": True,
    }
    contract["budget"]["max_experiments"] = component_count
    return payload


def _create_fresh_run(state_root: Path, run_id: str = "monitor-fresh") -> dict[str, object]:
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=_v4_payload(run_id),
        ))
        assert created.status == "accepted"
        status = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"status-{run_id}",
            expected_revision=None,
            command="STATUS",
            payload={"run_id": run_id},
        ))
        assert status.status == "accepted"
        assert status.result is not None
        return dict(status.result)
    finally:
        host.close()


def _row_census(database: Path) -> dict[str, int]:
    with sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True,
    ) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }


def _open_scientific_process_attempt_ids(
    host: WorkflowHost, run_id: str
) -> frozenset[str]:
    execution_ids = {
        str(row["experiment_id"]): str(row["execution_id"])
        for row in host.store.list_facts(run_id, "ExperimentPreregistered")
    }
    return frozenset(
        str(row["attempt_id"])
        for row in host.store.list_attempts()
        if row["run_id"] == run_id
        and row["kind"] == "PROCESS"
        and row["state"] == "OPEN"
        and execution_ids.get(str(row["experiment_id"])) == row["execution_id"]
    )


def test_offline_json_monitor_reuses_status_store_and_oracle_without_writes(
    tmp_path: Path, capsys,
) -> None:
    state_root = tmp_path / "fresh-state"
    run_id = "monitor-json"
    host_status = _create_fresh_run(state_root, run_id)
    before = _row_census(state_root / "state.sqlite")

    snapshot = build_monitor_snapshot(state_root, run_id)
    repeated = build_monitor_snapshot(state_root, run_id)

    assert _row_census(state_root / "state.sqlite") == before
    assert snapshot.status_source == "offline-state-store"
    assert snapshot.status["quiescent_for_backup"] is None
    assert snapshot.status["agent_usage"] is None
    assert snapshot.status["goal_verdict"] is None
    assert snapshot.status["required_component_count"] == 1
    assert snapshot.status["required_assessment_count"] == 0
    assert snapshot.durable_open_attempts_state_root == 0
    assert [(item.slot_ordinal, item.role) for item in snapshot.experts] == [
        (1, "scientist-a"),
        (2, "scientist-b"),
        (3, "scientist-c"),
    ]
    assert [item.component_id for item in snapshot.components] == ["primary.score"]
    assert snapshot.components[0].owner_role == "scientist-a"
    assert snapshot.components[0].proposer_roles == ("scientist-a",)
    assert snapshot.components[0].state == "PENDING"
    assert snapshot.components[0].phase == "proposal"
    assert snapshot.oracle["run_id"] == run_id
    assert snapshot.fact_counts == {"GoalDefined": 1}
    assert repeated.status["run_revision"] == snapshot.status["run_revision"]
    for key in (
        "run_id",
        "run_state",
        "run_revision",
        "experiment_states",
        "open_attempts",
        "canonical_outcome_count",
        "goal_verdict",
        "required_component_count",
        "required_assessment_count",
        "wal_autocheckpoint",
    ):
        assert snapshot.status[key] == host_status[key]

    assert cli_main([
        "monitor", "--state-root", str(state_root), "--run-id", run_id, "--json",
    ]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["run_id"] == run_id
    assert rendered["oracle"]["run_id"] == run_id
    assert _row_census(state_root / "state.sqlite") == before

    assert monitor_main([
        "--state-root", str(state_root), "--run-id", run_id, "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["status"]["run_state"] == "CREATED"
    assert _row_census(state_root / "state.sqlite") == before


def test_monitor_snapshot_and_json_carry_revision_breaker_fields(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "breaker-fields"
    run_id = "monitor-breaker-fields"
    _create_fresh_run(state_root, run_id)

    snapshot = build_monitor_snapshot(state_root, run_id)

    component = snapshot.components[0]
    assert component.cycle_revisions == 0
    assert component.current_cycle == 0
    assert component.revision_warning is None
    rendered = json.loads(snapshot.to_json())["components"][0]
    assert rendered["cycle_revisions"] == 0
    assert rendered["current_cycle"] == 0
    assert rendered["revision_warning"] is None


@pytest.mark.parametrize("port_args, expected_port", [([], 0), (["--web-port", "0"], 0), (["--web-port", "43210"], 43210)])
def test_cli_monitor_forwards_tui_web_controls(tmp_path: Path, monkeypatch, port_args, expected_port) -> None:
    state_root = tmp_path / "monitor-web-controls"
    calls: list[dict[str, object]] = []

    def fake_run_monitor(
        selected_root: Path,
        selected_run_id: str,
        *,
        json_output: bool,
        export_dir: str | Path | None,
        interval: float,
        web_enabled: bool,
        web_port: int,
    ) -> int:
        calls.append({
            "state_root": selected_root,
            "run_id": selected_run_id,
            "json_output": json_output,
            "export_dir": export_dir,
            "interval": interval,
            "web_enabled": web_enabled,
            "web_port": web_port,
        })
        return 0

    monkeypatch.setattr("hypoloom.monitor.run_monitor", fake_run_monitor)
    assert cli_main([
        "monitor",
        "--state-root",
        str(state_root),
        "--run-id",
        "monitor-web-controls",
        "--interval",
        "1.5",
        *port_args,
        "--no-web",
    ]) == 0
    assert calls == [{
        "state_root": state_root.resolve(),
        "run_id": "monitor-web-controls",
        "json_output": False,
        "export_dir": None,
        "interval": 1.5,
        "web_enabled": False,
        "web_port": expected_port,
    }]


def test_json_monitor_never_imports_tui_or_validates_web_port(
    tmp_path: Path, monkeypatch,
) -> None:
    state_root = tmp_path / "monitor-json-no-web"
    run_id = "monitor-json-no-web"
    _create_fresh_run(state_root, run_id)

    def unexpected_import(*_args, **_kwargs):
        raise AssertionError("JSON monitor must not import the TUI")

    monkeypatch.setattr("hypoloom.monitor.importlib.import_module", unexpected_import)
    output = io.StringIO()
    assert run_monitor(
        state_root,
        run_id,
        json_output=True,
        web_enabled=True,
        web_port=-1,
        stdout=output,
    ) == 0
    assert json.loads(output.getvalue())["run_id"] == run_id


def test_host_status_calls_the_shared_projection(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "shared-status"
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1, "create-shared", 0, "CREATE_RUN", _v4_payload("shared-status"),
        ))
        assert created.status == "accepted"
        calls: list[str] = []

        def recording_projection(*args, **kwargs):
            calls.append(str(args[1]))
            return build_status_projection(*args, **kwargs)

        monkeypatch.setattr(
            "hypoloom.workflow_host.build_status_projection",
            recording_projection,
        )
        response = host.handle(ControlRequest(
            1, "status-shared", None, "STATUS", {"run_id": "shared-status"},
        ))
        assert response.status == "accepted"
        assert calls == ["shared-status"]
        assert response.result is not None
        assert response.result["agent_usage"] is not None
        assert response.result["quiescent_for_backup"] is True
    finally:
        host.close()


def test_offline_snapshot_keeps_one_database_read_view_across_a_host_commit(
    tmp_path: Path, monkeypatch,
) -> None:
    state_root = tmp_path / "single-read-view"
    run_id = "single-read-view"
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1,
            "create-single-read-view",
            0,
            "CREATE_RUN",
            _v4_payload(run_id),
        ))
        assert created.status == "accepted"
        initial_head = host.store.latest_transition(run_id)
        assert initial_head is not None
        assert initial_head["state"] == "CREATED"

        original_list_facts = StateTransaction.list_facts
        committed_after_fact_read = False

        def cancel_after_fact_read(
            transaction: StateTransaction,
            selected_run_id: str,
            fact_type=None,
        ):
            nonlocal committed_after_fact_read
            facts = original_list_facts(transaction, selected_run_id, fact_type)
            if (
                selected_run_id == run_id
                and fact_type is None
                and not committed_after_fact_read
            ):
                committed_after_fact_read = True
                cancelled = host.handle(ControlRequest(
                    1,
                    "cancel-after-monitor-facts",
                    int(initial_head["revision"]),
                    "CANCEL",
                    {"run_id": run_id, "cause": "monitor snapshot race probe"},
                ))
                assert cancelled.status == "accepted"
            return facts

        monkeypatch.setattr(StateTransaction, "list_facts", cancel_after_fact_read)

        snapshot = build_monitor_snapshot(state_root, run_id)

        assert committed_after_fact_read
        latest_head = host.store.latest_transition(run_id)
        assert latest_head is not None
        assert latest_head["state"] == "STOPPING"
        assert snapshot.status["run_state"] == "CREATED"
        assert snapshot.status["run_revision"] == initial_head["revision"]
        run_view = next(item for item in snapshot.workflows if item.kind == "RUN")
        assert run_view.state == "CREATED"
        assert run_view.revision == initial_head["revision"]
        assert snapshot.fact_counts == {"GoalDefined": 1}
    finally:
        host.close()


def test_monitor_shows_two_live_expert_processes_on_a_fresh_fake_run(tmp_path: Path) -> None:
    state_root = tmp_path / "parallel-monitor"
    run_id = "parallel-monitor"
    host = WorkflowHost(state_root)
    overlap = None
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1,
            "create-parallel-monitor",
            0,
            "CREATE_RUN",
            # Keep the first job alive through the second job's real validation
            # and the independent monitor snapshot, even on a shared CI host.
            _v4_payload(run_id, component_count=2, delay_seconds=2.0),
        ))
        assert created.status == "accepted"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            host.tick()
            snapshot = build_monitor_snapshot(state_root, run_id)
            scientific_ids = _open_scientific_process_attempt_ids(host, run_id)
            live = tuple(
                item for item in snapshot.attempts
                if item.state == "OPEN" and item.attempt_id in scientific_ids
            )
            if len(live) == 2:
                overlap = (snapshot, scientific_ids)
                break
            time.sleep(0.005)
        assert overlap is not None
        overlap, scientific_ids = overlap
        open_attempts = tuple(
            item for item in overlap.attempts if item.state == "OPEN"
        )
        scientific_attempts = tuple(
            item for item in open_attempts if item.attempt_id in scientific_ids
        )
        assert {item.attempt_id for item in open_attempts} == scientific_ids
        assert len(scientific_attempts) == 2
        assert {item.kind for item in scientific_attempts} == {"PROCESS"}
        assert {item.role for item in scientific_attempts} == {
            "scientist-a", "scientist-b",
        }
        assert {
            item.component_id for item in scientific_attempts
        } == {"primary.score-1", "primary.score-2"}
        assert {
            item.phase for item in scientific_attempts
        } == {"execute"}
        assert [item.component_id for item in overlap.components] == [
            "primary.score-1", "primary.score-2",
        ]
        assert [item.owner_role for item in overlap.components] == [
            "scientist-a", "scientist-b",
        ]
        assert all(item.proposer_roles == (item.owner_role,) for item in overlap.components)
        assert all(item.state == "EXECUTING" for item in overlap.components)
        assert all(item.phase == "execute" for item in overlap.components)
        assert all(not item.blocked for item in overlap.components)
        assert all(len(item.open_attempt_ids) == 1 for item in overlap.components)
        expert_by_role = {item.role: item for item in overlap.experts}
        assert len(expert_by_role["scientist-a"].open_attempt_ids) == 1
        assert len(expert_by_role["scientist-b"].open_attempt_ids) == 1
        assert expert_by_role["scientist-c"].open_attempt_ids == ()

        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("fresh fake run did not close")
    finally:
        host.close()


def test_candidate_validation_is_control_work_not_a_scientific_process_attempt(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "candidate-validation-monitor"
    run_id = "candidate-validation-monitor"
    host = WorkflowHost(state_root)
    validation_snapshot = None
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            1,
            "create-candidate-validation-monitor",
            0,
            "CREATE_RUN",
            _v4_payload(run_id, delay_seconds=0.05),
        ))
        assert created.status == "accepted"

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            host.tick()
            snapshot = build_monitor_snapshot(state_root, run_id)
            validation = next((
                item for item in snapshot.attempts
                if item.command == "VALIDATE_SPEC_CANDIDATE" and item.state == "OPEN"
            ), None)
            if validation is not None:
                validation_snapshot = (snapshot, validation)
                break
            time.sleep(0.001)
        assert validation_snapshot is not None
        snapshot, validation = validation_snapshot
        scientist = next(item for item in snapshot.experts if item.role == "scientist-a")
        assert validation.kind == "PROCESS"
        assert validation.phase == "validation"
        assert validation.gpu_uuid is None
        assert scientist.process_attempts == 0
        assert validation.attempt_id in scientist.open_attempt_ids
        assert snapshot.status["open_attempts"] == 1
        assert snapshot.durable_open_attempts_state_root == 1

        while time.monotonic() < deadline:
            host.tick()
            if host.store.latest_transition(run_id)["state"] == "COMPLETED":
                break
            time.sleep(0.001)
        else:
            raise AssertionError("fresh fake run did not close")

        completed = build_monitor_snapshot(state_root, run_id)
        process_attempts = tuple(
            item for item in completed.attempts if item.kind == "PROCESS"
        )
        assert [(item.command, item.phase) for item in process_attempts] == [
            ("VALIDATE_SPEC_CANDIDATE", "validation"),
            ("EXECUTE", "execute"),
        ]
        scientist = next(item for item in completed.experts if item.role == "scientist-a")
        assert scientist.process_attempts == 1
    finally:
        host.close()


def test_textual_tui_mounts_and_refreshes_headlessly(tmp_path: Path) -> None:
    pytest.importorskip("textual")
    from textual.containers import Grid
    from textual.widgets import Static

    from hypoloom.tui import (
        CockpitPanel,
        EvidencePanel,
        ExpertsPanel,
        FreshnessBanner,
        HelpLegend,
        HypothesesPanel,
        HypoloomVNextMonitor,
        SummaryPanel,
        WorkflowsPanel,
    )

    state_root = tmp_path / "textual-headless"
    run_id = "textual-headless"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_port=0,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            panel_ids = (
                "summary", "experts", "components", "workflows",
                "hypotheses", "attempts", "evidence",
            )
            assert all(
                app.query_one(f"#{panel_id}", CockpitPanel).can_focus
                for panel_id in panel_ids
            )
            cockpit = app.query_one("#cockpit", Grid)
            assert cockpit.styles.grid_size_columns == 2
            assert app.query_one("#evidence", EvidencePanel).styles.column_span == 2
            summary = app.query_one("#summary", SummaryPanel)
            experts = app.query_one("#experts", ExpertsPanel)
            workflows = app.query_one("#workflows", WorkflowsPanel)
            hypotheses = app.query_one("#hypotheses", HypothesesPanel)
            evidence = app.query_one("#evidence", EvidencePanel)
            banner = app.query_one("#freshness", FreshnessBanner)
            legend = app.query_one("#help-legend", HelpLegend)
            assert "READ-ONLY / OFFLINE" in str(banner.render())
            assert "OPEN? = unconfirmed activity" in _static_text(experts.query_one(Static))
            rendered_summary = str(summary.query_one(Static).render())
            assert "Run created" in rendered_summary
            assert "Verdict PENDING" in rendered_summary
            assert "Required assessed 0/1" in str(evidence.query_one(Static).render())
            assert "not started" in _static_text(experts.query_one(Static))
            assert "ACTIVATE" in str(workflows.query_one(Static).render())
            assert "No hypotheses proposed yet" in str(
                hypotheses.query_one(Static).render()
            )
            assert "not a scientific-success verdict" in str(
                evidence.query_one(Static).render()
            )
            assert run_id in app.sub_title

            assert not legend.has_class("visible")
            await pilot.click("#help-button")
            await pilot.pause()
            assert legend.has_class("visible")
            help_text = _static_text(legend.query_one(Static))
            assert "WINDOW GUIDE" in help_text and "EXECUTION RELIABILITY" in help_text
            assert "SUCCEEDED" in help_text
            assert "Browser URL" not in help_text
            assert app.focused is legend and legend.max_scroll_y > 0
            await pilot.press("end")
            await pilot.pause()
            assert legend.scroll_y == legend.max_scroll_y
            await pilot.press("?")
            await pilot.pause()
            assert not legend.has_class("visible")

            summary.update("\n".join(f"scroll row {row}" for row in range(40)))
            summary.focus()
            await pilot.pause()
            await pilot.press("end")
            await pilot.pause()
            assert app.focused is summary
            assert summary.scroll_y > 0

            await pilot.press("r")
            await pilot.pause()
            assert "read-only" in app.sub_title

    asyncio.run(exercise())


def test_terminal_clipboard_request_uses_one_shot_tmux_passthrough() -> None:
    pytest.importorskip("textual")

    from hypoloom.tui import _send_terminal_clipboard_request

    url = "http://127.0.0.1:8770/#token=a%2Fb&view=map"
    writes: list[str] = []
    tmux_app = SimpleNamespace(_driver=SimpleNamespace(write=writes.append))

    assert _send_terminal_clipboard_request(
        tmux_app, url, inside_tmux=True,
    ) == "tmux"
    encoded = base64.b64encode(url.encode("utf-8")).decode("ascii")
    assert writes == [f"\x1bPtmux;\x1b\x1b]52;c;{encoded}\a\x1b\\"]

    copied: list[str] = []
    direct_app = SimpleNamespace(copy_to_clipboard=copied.append)
    assert _send_terminal_clipboard_request(
        direct_app, url, inside_tmux=False,
    ) == "terminal"
    assert copied == [url]
    assert _send_terminal_clipboard_request(
        SimpleNamespace(), url, inside_tmux=True,
    ) is None


def test_textual_tui_tmux_copy_is_explicit_and_reports_no_ack(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")
    from textual.widgets import Button, Static

    from hypoloom import tui

    state_root = tmp_path / "textual-tmux-copy"
    run_id = "textual-tmux-copy"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    before = _row_census(state_root / "state.sqlite")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
    requests: list[tuple[str, bool]] = []
    notifications: list[tuple[str, dict[str, object]]] = []

    def request(_app, text: str, *, inside_tmux: bool) -> str:
        requests.append((text, inside_tmux))
        return "tmux"

    monkeypatch.setattr(tui, "_send_terminal_clipboard_request", request)

    async def exercise() -> None:
        app = tui.HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_port=0,
        )
        monkeypatch.setattr(
            app,
            "notify",
            lambda message, **kwargs: notifications.append((message, kwargs)),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            url = app.web_access_url
            assert url is not None
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, tui.WebAccessScreen)
            assert requests == []
            assert "Copy URL via tmux" in str(
                app.screen.query_one("#copy-web-url", Button).label
            )
            help_text = str(
                app.screen.query_one("#web-access-content", Static).render()
            )
            assert "does not change tmux settings" in help_text
            assert "Shift while dragging" in help_text
            await pilot.click("#copy-web-url")
            await pilot.pause()
            assert requests == [(url, True)]
            assert len(notifications) == 1
            message, kwargs = notifications[0]
            assert "request sent through tmux" in message
            assert "paste to verify" in message
            assert " copied" not in message.lower()
            assert "token=" not in message
            assert kwargs == {"timeout": 10}

    asyncio.run(exercise())
    assert _row_census(state_root / "state.sqlite") == before


def test_textual_tui_partial_context_refresh_keeps_web_available(tmp_path, monkeypatch):
    """A readable CLI stream does not imply that its transcript exists yet."""
    pytest.importorskip("textual")
    from textual.widgets import Static
    from hypoloom.context_observation import read_native_events, transcript_metrics
    from hypoloom.tui import AttemptsPanel, HypoloomVNextMonitor

    root, run_id = tmp_path / "partial-context", "partial-context"
    _create_fresh_run(root, run_id)
    snapshot = build_monitor_snapshot(root, run_id)
    before = _row_census(root / "state.sqlite")
    attempt = AttemptView(
        attempt_id="starting-agent", kind="AGENT", state="OPEN", workflow_id=run_id,
        command="GROUNDING", component_id=None, phase="grounding", role="scientist-a",
        slot_ordinal=None, attempt_ordinal=1, terminal_reason=None, process_group_id=None,
        started_at=1.0, ended_at=None, gpu_uuid=None, minimum_free_memory_mib=None,
        latest_free_memory_mib=None, latest_utilization_percent=None,
        resource_pressure_exceeded=None,
    )
    stream = root / "diagnostics/agents" / run_id / attempt.attempt_id / "stdout.log"
    stream.parent.mkdir(parents=True)
    stream.write_text('{"type":"system","subtype":"init"}\n')
    observation = {attempt.attempt_id: {"request": {"role": attempt.role}}}
    partial, = _with_live_attempt_context(root, run_id, (attempt,), observation)
    assert partial.context_observation["native_compaction_count"] == 0
    assert partial.context_observation["coverage"] == "unavailable"
    assert "read_calls" not in partial.context_observation
    stream.write_text('{"type":"system","subtype":"compact_boundary"}\n')
    compacted, = _with_live_attempt_context(root, run_id, (attempt,), observation)
    assert compacted.context_observation["native_compaction_count"] == 1
    stream_only = replace(attempt, context_observation=read_native_events(stream))
    assert "coverage" not in stream_only.context_observation
    # A readable empty transcript has known zero reads; absence stays unknown.
    measured = replace(attempt, context_observation=transcript_metrics(b"", "session", 1.0))
    current = [snapshot]
    _patch_tui_reader(monkeypatch, lambda *_: current[0])

    async def exercise():
        app = HypoloomVNextMonitor(root, run_id, interval=60,
            initial_snapshot=snapshot, web_port=0)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            server = app._web_server
            assert server is not None
            parsed = urlsplit(app.web_access_url)
            token = parse_qs(parsed.fragment)["token"][0]

            def fetch_snapshot():
                connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
                try:
                    connection.request("GET", "/api/snapshot",
                        headers={"Authorization": f"Bearer {token}"})
                    response = connection.getresponse()
                    assert response.status == 200
                    return json.loads(response.read())
                finally:
                    connection.close()

            for rendered, reads in ((partial, "?"), (measured, "0"), (compacted, "?"), (stream_only, "?")):
                current[0] = replace(snapshot, attempts=(rendered,))
                app.action_refresh()
                for _ in range(50):
                    await pilot.pause(0.02)
                    if app._last_snapshot is current[0] and app._refresh_thread is None:
                        break
                assert app._last_snapshot is current[0]
                assert app.return_code is None
                panel = " ".join(_static_text(app.query_one(AttemptsPanel).query_one(Static)).split())
                assert f"successful Read={reads}" in panel
                assert f"exact frozen-input rereads={reads}" in panel
                payload = await asyncio.to_thread(fetch_snapshot)
                assert payload["attempts"][0]["context_observation"] == rendered.context_observation
                assert app._web_server is server and app._web_thread.is_alive()
            port = parsed.port
        assert app._web_server is None
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    asyncio.run(exercise())
    assert _row_census(root / "state.sqlite") == before


def test_textual_tui_owns_web_observer_and_releases_its_port(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")
    from textual.widgets import Button, Static

    from hypoloom.tui import HypoloomVNextMonitor, WebAccessScreen

    state_root = tmp_path / "textual-web-sidecar"
    run_id = "textual-web-sidecar"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    before = _row_census(state_root / "state.sqlite")
    bound_port: int | None = None
    persistent_connection: HTTPConnection | None = None
    monkeypatch.delenv("TMUX", raising=False)

    async def exercise() -> None:
        nonlocal bound_port, persistent_connection

        unmounted = asyncio.Event()

        class TrackedMonitor(HypoloomVNextMonitor):
            def on_unmount(self) -> None:
                super().on_unmount()
                unmounted.set()

        app = TrackedMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_port=0,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            url = app.web_access_url
            assert url is not None
            parsed = urlsplit(url)
            assert parsed.hostname == "127.0.0.1"
            assert parsed.port is not None and parsed.port > 0
            bound_port = parsed.port
            token = parse_qs(parsed.fragment)["token"][0]
            assert str(app.query_one("#help-button", Button).label) == "Help"

            def fetch_snapshot() -> tuple[HTTPConnection, dict[str, object]]:
                connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
                try:
                    connection.request(
                        "GET",
                        "/api/snapshot",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    response = connection.getresponse()
                    assert response.status == 200
                    return connection, json.loads(response.read())
                except Exception:
                    connection.close()
                    raise

            persistent_connection, payload = await asyncio.to_thread(fetch_snapshot)
            assert payload["run_id"] == run_id

            await pilot.click("#help-button")
            await pilot.pause()
            assert not isinstance(app.screen, WebAccessScreen)
            assert app.query_one("#help-legend").has_class("visible")
            await pilot.press("?")
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WebAccessScreen)
            help_text = str(
                app.screen.query_one("#web-access-content", Static).render()
            )
            assert "WEB OBSERVER READY" in help_text
            assert "OS-selected port" in help_text
            assert url in help_text
            assert f"ssh -N -L {bound_port}:127.0.0.1:{bound_port}" in help_text
            assert "stops when the TUI exits" in help_text
            assert "unconfirmed terminal request" in help_text
            copied: list[str] = []
            notifications: list[tuple[str, dict[str, object]]] = []
            monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notifications.append((message, kwargs)),
            )
            await pilot.click("#copy-web-url")
            await pilot.pause()
            assert copied == [url]
            assert len(notifications) == 1
            assert "request sent" in notifications[0][0]
            assert "paste to verify" in notifications[0][0]
            assert " copied" not in notifications[0][0].lower()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WebAccessScreen)
            await pilot.press("q")
            assert app.return_code == 0

        assert unmounted.is_set()
        assert app.web_access_url is None

        def request_after_unmount() -> None:
            assert persistent_connection is not None
            persistent_connection.request(
                "GET",
                "/api/snapshot",
                headers={"Authorization": f"Bearer {token}"},
            )
            persistent_connection.getresponse()

        with pytest.raises((OSError, HTTPException)):
            await asyncio.to_thread(request_after_unmount)

    try:
        asyncio.run(exercise())
    finally:
        if persistent_connection is not None:
            persistent_connection.close()
    assert bound_port is not None
    assert _row_census(state_root / "state.sqlite") == before
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", bound_port))


def test_textual_tui_survives_web_port_collision_and_explains_recovery(
    tmp_path: Path,
) -> None:
    pytest.importorskip("textual")
    from textual.containers import VerticalScroll
    from textual.widgets import Button

    from hypoloom.tui import HypoloomVNextMonitor, WebAccessScreen

    state_root = tmp_path / "textual-web-collision"
    run_id = "textual-web-collision"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = int(occupied.getsockname()[1])

        async def exercise() -> None:
            app = HypoloomVNextMonitor(
                state_root,
                run_id,
                interval=60.0,
                initial_snapshot=snapshot,
                web_port=port,
            )
            async with app.run_test(size=(50, 14)) as pilot:
                await pilot.pause()
                assert app.web_access_url is None
                assert app.web_error
                assert str(app.query_one("#help-button", Button).label) == "Help"
                await pilot.press("w")
                await pilot.pause()
                assert isinstance(app.screen, WebAccessScreen)
                visible = "\n".join(
                    strip.text for strip in app.screen._compositor.render_strips()
                )
                assert "WEB OBSERVER UNAVAILABLE" in visible
                assert f"Requested loopback port: {port}" in visible
                assert "--web-port" not in visible
                scroll = app.screen.query_one("#web-access-scroll", VerticalScroll)
                assert scroll.max_scroll_y > 0
                scroll.focus()
                await pilot.press("end")
                await pilot.pause()
                assert scroll.scroll_y == scroll.max_scroll_y
                visible = "\n".join(
                    strip.text for strip in app.screen._compositor.render_strips()
                )
                assert "--web-port" in visible
                assert "--web-port 0" in app._web_access_content()

        asyncio.run(exercise())


def test_textual_tui_no_web_mode_never_binds(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")
    from textual.widgets import Static

    from hypoloom.tui import HypoloomVNextMonitor, WebAccessScreen

    state_root = tmp_path / "textual-no-web"
    run_id = "textual-no-web"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)

    def unexpected_server(*_args, **_kwargs):
        raise AssertionError("--no-web must not create a server")

    monkeypatch.setattr("hypoloom.tui.create_server", unexpected_server)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_enabled=False,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.web_access_url is None
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WebAccessScreen)
            help_text = str(
                app.screen.query_one("#web-access-content", Static).render()
            )
            assert "WEB OBSERVER DISABLED" in help_text
            assert "--no-web" in help_text

    asyncio.run(exercise())


def test_textual_tui_renders_research_progress_and_attempt_terminal_semantics(
    tmp_path: Path,
) -> None:
    pytest.importorskip("textual")
    from textual.widgets import Static

    from hypoloom.tui import (
        AttemptsPanel,
        ComponentsPanel,
        FreshnessBanner,
        HypothesesPanel,
        HypoloomVNextMonitor,
        SummaryPanel,
        WorkflowsPanel,
    )

    state_root = tmp_path / "textual-semantics"
    run_id = "textual-semantics"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    component = replace(
        snapshot.components[0],
        workflow_id="workflow-score",
        state="EVALUATING",
        phase="analyze",
    )
    hypotheses = (
        HypothesisView(
            hypothesis_id="hypothesis-parent",
            component_id=component.component_id,
            proposer_role="scientist-a",
            method_family_id="method-a",
            claim="The treatment improves the primary score.",
            assumptions=("measurement is stable",),
            parent_hypothesis_id=None,
            relation=None,
            assessment_id="assessment-parent",
            evidence_ids=("evidence-1",),
            assessment="SUPPORTED",
        ),
        HypothesisView(
            hypothesis_id="hypothesis-child",
            component_id=component.component_id,
            proposer_role="scientist-b",
            method_family_id="method-b",
            claim="A narrower treatment may improve robustness.",
            assumptions=(),
            parent_hypothesis_id="hypothesis-parent",
            relation="derived_from",
            assessment_id=None,
            evidence_ids=(),
            assessment=None,
        ),
    )
    workflows = (
        WorkflowView(
            workflow_id="workflow-score",
            kind="EXPERIMENT",
            component_id=component.component_id,
            state="READY",
            revision=4,
            next_command="EXECUTE",
            due_at=snapshot.observed_at - 5,
        ),
        WorkflowView(
            workflow_id=run_id,
            kind="RUN",
            component_id=None,
            state="ACTIVE",
            revision=3,
            next_command=None,
            due_at=None,
        ),
    )
    rendezvous = (
        RendezvousView(
            rendezvous_id="rendezvous-help",
            component_id=component.component_id,
            phase="help",
            kind="HELP",
            status="RESOLVED",
            requester_role="scientist-a",
            requester_slot=1,
            target_role="scientist-b",
            responder_role="scientist-b",
            source_attempt_id="attempt-source",
            question="Which robustness check best isolates the claimed effect?",
            context_refs=("hypothesis-parent",),
            disposition="ANSWERED",
            summary="Use the held-out perturbation cohort.",
            supporting_refs=("evidence-1",),
        ),
    )
    terminals = (None, "SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST")
    attempts = tuple(
        AttemptView(
            attempt_id=f"attempt-{index}",
            kind="PROCESS",
            state="OPEN" if terminal is None else "CLOSED",
            workflow_id="workflow-score",
            command="EXECUTE",
            component_id=component.component_id,
            phase="execute",
            role="scientist-a",
            slot_ordinal=1,
            attempt_ordinal=index,
            terminal_reason=terminal,
            process_group_id=None,
            started_at=snapshot.observed_at - 10,
            ended_at=None if terminal is None else snapshot.observed_at - 2,
            gpu_uuid=None,
            minimum_free_memory_mib=None,
            latest_free_memory_mib=None,
            latest_utilization_percent=None,
            resource_pressure_exceeded=None,
        )
        for index, terminal in enumerate(terminals, 1)
    )
    enriched = replace(
        snapshot,
        # This synthetic fixture exercises the legacy projection fallback;
        # current chain/summary interaction is covered in test_observer_cockpit.
        overview={},
        status_source="offline-state-store+resident-host-status",
        status={
            **snapshot.status,
            "run_state": "ACTIVE",
            "open_attempts": 1,
            "quiescent_for_backup": False,
            "agent_usage": {
                "observed_lower_bound": {
                    "tokens": 123,
                    "turns": 4,
                    "cost": "0.12",
                },
                "profile_cumulative": {
                    "tokens": {
                        "charged_or_reserved": 1200,
                        "limit": 1000,
                        "overage": 200,
                    },
                    "turns": {"charged_or_reserved": 2, "limit": 10},
                },
                "open_declared_limits": {
                    "attempts": 1,
                    "tokens": 100,
                    "turns": 2,
                },
                "closed_unknown_declared_limits": {
                    "attempts": 2,
                    "tokens": 200,
                    "turns": 4,
                },
                "unobserved_closed_attempts": 2,
                "budget_rejected_attempts": 1,
                "token_enforcement": {
                    "modes": ["observe"],
                    "observed_overage_attempts": 1,
                    "overage_tokens": 23,
                },
            },
            "resource_waits": [
                {
                    "experiment_id": "workflow-score",
                    "reason": "frozen GPU is busy in this state root",
                },
            ],
        },
        components=(component,),
        hypotheses=hypotheses,
        rendezvous=rendezvous,
        workflows=workflows,
        attempts=attempts,
    )

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=enriched,
            web_port=0,
        )
        async with app.run_test(size=(120, 48)) as pilot:
            await pilot.pause()
            banner = str(app.query_one("#freshness", FreshnessBanner).render())
            summary = str(app.query_one("#summary", SummaryPanel).query_one(Static).render())
            components = str(
                app.query_one("#components", ComponentsPanel).query_one(Static).render()
            )
            rendered_hypotheses = str(
                app.query_one("#hypotheses", HypothesesPanel).query_one(Static).render()
            )
            rendered_workflows = str(
                app.query_one("#workflows", WorkflowsPanel).query_one(Static).render()
            )
            rendered_attempts = str(
                app.query_one("#attempts", AttemptsPanel).query_one(Static).render()
            )

            assert "READ-ONLY / HOST STATUS" in banner
            assert "Research workflow active" in summary
            assert "1/2 assessed" in summary
            assert "≥123 tok" in summary and "$" not in summary
            assert "Tok ref 1.2K/1K" in summary and "120%" in summary
            assert "Res ≤100 tok (1 open)" in summary
            assert "closed ?2 ≤200 tok" in summary
            assert "rejected 1" in summary
            assert "observe: call overages 1 (+23 tok)" in summary
            assert "6/7" in components
            assert "outcome assessment" in components
            assert "SUPPORTED" in rendered_hypotheses
            assert "PENDING" in rendered_hypotheses
            assert "derived_from hypothesis-parent" in rendered_hypotheses
            assert "evidence=evidence-1" in rendered_hypotheses
            assert "next=EXECUTE" in rendered_workflows
            assert "overdue by 5s" in rendered_workflows
            assert "PEER RENDEZVOUS" in rendered_workflows
            assert "RESOLVED" in rendered_workflows
            assert "ANSWERED by scientist-b" in rendered_workflows
            assert "LIVE RESOURCE WAITS" in rendered_workflows
            assert "frozen GPU is busy in this state root" in rendered_workflows
            for terminal in ("OPEN", "SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"):
                assert terminal in rendered_attempts

    asyncio.run(exercise())


def test_textual_tui_component_panel_shows_revision_breaker_warnings(
    tmp_path: Path,
) -> None:
    pytest.importorskip("textual")
    from textual.widgets import Static

    from hypoloom.tui import ComponentsPanel, HypoloomVNextMonitor

    state_root = tmp_path / "textual-breaker"
    run_id = "textual-breaker"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    warned = (
        replace(_revision_warning_component_view(11), component_id="primary.general"),
        replace(_revision_warning_component_view(21, cycle=2), component_id="primary.severe"),
        replace(_revision_warning_component_view(31), component_id="primary.tripped"),
    )
    enriched = replace(snapshot, components=warned)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=enriched,
            web_port=0,
        )
        async with app.run_test(size=(120, 48)) as pilot:
            await pilot.pause()
            components = str(
                app.query_one("#components", ComponentsPanel)
                .query_one(Static)
                .render()
            )
            assert "Cycle 1 · recorded execution repairs 11" in components
            assert "Cycle 2 · recorded execution repairs 21" in components
            assert "Repeated execution repairs" in components
            assert "convergence" not in components and "cause unverified" not in components
            assert "base-model capability" not in components
            assert "Cycle 1 · recorded execution repairs 31" in components
            assert "Execution repair circuit-breaker threshold exceeded" in components

    asyncio.run(exercise())


def test_agent_usage_lines_accept_an_older_host_snapshot_without_breakdown() -> None:
    pytest.importorskip("textual")
    from hypoloom.tui import _agent_usage_lines

    lines = _agent_usage_lines({
        "agent_usage": {
            "observed_lower_bound": {"tokens": 40, "turns": 2, "cost": "0.1"},
            "profile_cumulative": {
                "tokens": {"charged_or_reserved": 100, "limit": 200},
                "turns": {"charged_or_reserved": 3, "limit": 10},
            },
            "token_enforcement": {
                "modes": ["enforce"],
                "observed_overage_attempts": 0,
                "overage_tokens": 0,
            },
        },
    })

    assert "≥40 tok" in lines[0]
    assert "Res ≤? tok (? open)" in lines[3]
    assert "closed ?? ≤? tok" in lines[3]

    incomplete = _agent_usage_lines({"agent_usage": {}})
    assert "Tok limit ?/?" in incomplete[1]
    assert "Turns ?/?" in incomplete[2]


def test_textual_tui_long_unchanged_panel_skips_static_layout(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")
    from textual.widgets import Static

    from hypoloom.tui import ComponentsPanel, HypoloomVNextMonitor

    state_root = tmp_path / "textual-long-stable-panel"
    run_id = "textual-long-stable-panel"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    template = snapshot.components[0]
    components = tuple(
        replace(
            template,
            display_ordinal=index,
            component_id=f"must.long-component-{index:03d}-" + "x" * 80,
            workflow_id=f"workflow-long-{index:03d}",
            blocked_reasons=("waiting for a deliberately long dependency label " + "y" * 80,),
        )
        for index in range(1, 201)
    )
    long_snapshot = replace(snapshot, components=components)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=long_snapshot,
            web_enabled=False,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            panel = app.query_one("#components", ComponentsPanel)
            await pilot.press("d")
            await pilot.pause()
            assert panel.max_scroll_y > 0
            panel.scroll_to(y=panel.max_scroll_y / 2, animate=False)
            await pilot.pause()
            scroll_y = panel.scroll_y
            content = panel.query_one(Static)
            real_update = content.update
            updates: list[str] = []

            def counted_update(*args, **kwargs) -> None:
                updates.append(str(args[0]))
                real_update(*args, **kwargs)

            monkeypatch.setattr(content, "update", counted_update)
            app._render(replace(long_snapshot, observed_at=long_snapshot.observed_at + 2))
            await pilot.pause()
            assert updates == []
            assert panel.scroll_y == scroll_y

            changed = replace(
                long_snapshot,
                components=(replace(components[0], state="DRAFTED"), *components[1:]),
            )
            app._render(changed)
            await pilot.pause()
            assert len(updates) == 1

    asyncio.run(exercise())


def test_textual_tui_snapshot_reader_runs_off_the_ui_thread(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")

    from hypoloom.tui import HypoloomVNextMonitor

    state_root = tmp_path / "textual-background-refresh"
    run_id = "textual-background-refresh"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    builder_threads: list[int] = []
    completed = threading.Event()

    def observed_builder(_state_root: Path, _run_id: str):
        builder_threads.append(threading.get_ident())
        completed.set()
        return snapshot

    _patch_tui_reader(monkeypatch, observed_builder)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_enabled=False,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            ui_thread = threading.get_ident()
            app.action_refresh()
            assert await asyncio.to_thread(completed.wait, 2.0)
            await pilot.pause()
            assert builder_threads == [builder_threads[0]]
            assert builder_threads[0] != ui_thread

    asyncio.run(exercise())


def test_textual_tui_coalesces_refresh_requests_while_reader_is_busy(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")

    from hypoloom.tui import HypoloomVNextMonitor

    state_root = tmp_path / "textual-refresh-coalescing"
    run_id = "textual-refresh-coalescing"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    first_started = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    lock = threading.Lock()
    calls = 0
    active = 0
    maximum_active = 0

    def blocking_builder(_state_root: Path, _run_id: str):
        nonlocal calls, active, maximum_active
        with lock:
            calls += 1
            call = calls
            active += 1
            maximum_active = max(maximum_active, active)
        if call == 1:
            first_started.set()
            assert release_first.wait(2.0)
        with lock:
            active -= 1
        if call == 2:
            second_finished.set()
        return snapshot

    _patch_tui_reader(monkeypatch, blocking_builder)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_enabled=False,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_refresh()
            assert await asyncio.to_thread(first_started.wait, 2.0)
            app.action_refresh()
            app.action_refresh()
            await pilot.pause()
            with lock:
                assert calls == 1
                assert maximum_active == 1
            release_first.set()
            assert await asyncio.to_thread(second_finished.wait, 2.0)
            await pilot.pause()
            with lock:
                assert calls == 2
                assert maximum_active == 1

    asyncio.run(exercise())


def test_textual_tui_timer_poll_does_not_queue_a_busy_reader(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")

    from hypoloom.tui import HypoloomVNextMonitor

    state_root = tmp_path / "textual-refresh-timer-backpressure"
    run_id = "textual-refresh-timer-backpressure"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    lock = threading.Lock()
    calls = 0

    def blocking_builder(_state_root: Path, _run_id: str):
        nonlocal calls
        with lock:
            calls += 1
            call = calls
        if call == 1:
            first_started.set()
            assert release_first.wait(2.0)
        else:
            second_started.set()
        return snapshot

    _patch_tui_reader(monkeypatch, blocking_builder)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_enabled=False,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._refresh_snapshot()
            assert await asyncio.to_thread(first_started.wait, 2.0)
            app._refresh_snapshot()
            app._refresh_snapshot()
            release_first.set()
            assert not await asyncio.to_thread(second_started.wait, 0.2)
            await pilot.pause()
            with lock:
                assert calls == 1

    asyncio.run(exercise())


def test_textual_tui_unmount_does_not_wait_for_busy_snapshot_reader(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")

    from hypoloom.tui import HypoloomVNextMonitor

    state_root = tmp_path / "textual-refresh-prompt-unmount"
    run_id = "textual-refresh-prompt-unmount"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_builder(_state_root: Path, _run_id: str):
        started.set()
        release.wait(2.0)
        finished.set()
        return snapshot

    _patch_tui_reader(monkeypatch, blocking_builder)

    async def exercise() -> float:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_enabled=False,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_refresh()
            assert await asyncio.to_thread(started.wait, 2.0)
            unmount_started_at = time.monotonic()
            app.exit()
        return unmount_started_at

    try:
        unmount_started_at = asyncio.run(exercise())
        elapsed = time.monotonic() - unmount_started_at
        assert elapsed < 0.5
        assert not finished.is_set()
    finally:
        release.set()
        finished.wait(2.0)


def test_textual_tui_narrow_layout_stacks_panels(tmp_path: Path) -> None:
    pytest.importorskip("textual")
    from textual.containers import Grid, VerticalScroll
    from textual.widgets import Button

    from hypoloom.tui import (
        EvidencePanel,
        HypoloomVNextMonitor,
        WebAccessScreen,
    )

    state_root = tmp_path / "textual-narrow"
    run_id = "textual-narrow"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_port=0,
        )
        async with app.run_test(size=(50, 16)) as pilot:
            await pilot.pause()
            assert app.screen.has_class("narrow")
            assert app.query_one("#cockpit", Grid).styles.grid_size_columns == 1
            assert app.query_one("#evidence", EvidencePanel).styles.column_span == 1
            visible = "\n".join(
                strip.text for strip in app.screen._compositor.render_strips()
            )
            for button_id, label in (("help-button", "Help"),
                                     ("support-help-button", "Report Issue"),
                                     ("end-run-button", "End run")):
                button = app.query_one(f"#{button_id}", Button)
                assert button.region.right <= 50
                assert button.content_region.width >= len(label)
                assert label in visible
            await pilot.click("#help-button")
            await pilot.pause()
            legend = app.query_one("#help-legend")
            assert legend.has_class("visible") and legend.max_scroll_y > 0
            await pilot.press("end")
            await pilot.pause()
            assert legend.scroll_y == legend.max_scroll_y
            await pilot.press("?")
            await pilot.press("w")
            await pilot.pause()
            assert isinstance(app.screen, WebAccessScreen)
            assert app.screen.query_one("#web-access-dialog").region.width <= 50
            visible = "\n".join(
                strip.text for strip in app.screen._compositor.render_strips()
            )
            assert "SSH tunnel must be started" not in visible
            scroll = app.screen.query_one("#web-access-scroll", VerticalScroll)
            assert scroll.max_scroll_y > 0
            scroll.focus()
            await pilot.press("end")
            await pilot.pause()
            assert scroll.scroll_y == scroll.max_scroll_y
            visible = "\n".join(
                strip.text for strip in app.screen._compositor.render_strips()
            )
            assert "Do not share the token URL. The SSH" in visible
            assert "tunnel must be started on the browser" in visible
            assert "that local tunnel for you." in visible

    asyncio.run(exercise())


def test_textual_tui_refresh_failure_marks_retained_snapshot_stale(
    tmp_path: Path, monkeypatch,
) -> None:
    pytest.importorskip("textual")
    from hypoloom.tui import FreshnessBanner, HypoloomVNextMonitor

    state_root = tmp_path / "textual-stale"
    run_id = "textual-stale"
    _create_fresh_run(state_root, run_id)
    snapshot = build_monitor_snapshot(state_root, run_id)

    def fail_refresh(_state_root: Path, _run_id: str):
        raise RuntimeError("offline reader unavailable")

    _patch_tui_reader(monkeypatch, fail_refresh)

    async def exercise() -> None:
        app = HypoloomVNextMonitor(
            state_root,
            run_id,
            interval=60.0,
            initial_snapshot=snapshot,
            web_port=0,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            banner = app.query_one("#freshness", FreshnessBanner)
            rendered = str(banner.render())
            assert banner.has_class("stale")
            assert "STALE · READ-ONLY" in rendered
            assert "showing last successful snapshot" in rendered
            assert "offline reader unavailable" in rendered
            assert "STALE" in app.sub_title

    asyncio.run(exercise())


@pytest.mark.parametrize("missing_module", ("textual", "rich"))
def test_missing_tui_dependency_has_clear_fallback_message(
    tmp_path: Path, monkeypatch, missing_module: str,
) -> None:
    state_root = tmp_path / f"missing-{missing_module}"
    run_id = f"missing-{missing_module}"
    _create_fresh_run(state_root, run_id)
    real_import = importlib.import_module

    def missing_dependency(name: str, package: str | None = None):
        if name == "hypoloom.tui":
            raise ModuleNotFoundError(
                f"No module named {missing_module!r}", name=missing_module,
            )
        return real_import(name, package)

    monkeypatch.setattr("hypoloom.monitor.importlib.import_module", missing_dependency)
    stdout = io.StringIO()
    stderr = io.StringIO()
    assert run_monitor(
        state_root, run_id, json_output=False,
        stdout=stdout, stderr=stderr,
    ) == 1
    assert stdout.getvalue() == ""
    assert "requires Textual" in stderr.getvalue()
    assert "--json" in stderr.getvalue()


def test_read_only_observers_have_no_legacy_runtime_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "hypoloom/monitor.py",
        "hypoloom/tui.py",
        "hypoloom/web_observer.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(name == "hypoloom" or name.startswith("hypoloom.") for name in imported)


def test_compact_limits_keep_unknown_unbounded_and_observe_overage_distinct():
    from hypoloom.tui import _compact, _limit_line
    assert [_compact(value) for value in (0, 1234, 1250000, 999999999, 2500000000000)] == ["0", "1.23K", "1.25M", "1B", "2.5T"]
    assert all(_compact(value) == "?" for value in (None, True, -1, float("nan")))
    bounded = _limit_line("Tok ref", 1500, 1000)
    assert "1.5K/1K" in bounded and "150%" in bounded
    for value in (_limit_line("Tok", 50, None), _limit_line("Tok", None, 100),
                  _limit_line("Tok", 50, None, known=False)):
        assert "%" not in value and "━" not in value
    assert "∞" in _limit_line("Tok", 0, None)
    assert "?" in _limit_line("Tok", 0, None, known=False)


def test_textual_compact_lanes_and_colored_tree_preserve_selection(tmp_path, monkeypatch):
    from copy import deepcopy
    from textual.widgets import Static
    from hypoloom.tui import ExpertsPanel, HypothesesPanel, HypoloomVNextMonitor
    from test_observer_summary import _attempt, _summary
    from test_observer_reliability import row
    # Exercise actual colors even when the development shell requests no color.
    monkeypatch.delenv("NO_COLOR", raising=False)
    root = tmp_path / "compact"
    _create_fresh_run(root, "compact")
    snapshot = build_monitor_snapshot(root, "compact")
    role = snapshot.experts[0].role
    summary = _summary(tmp_path, experts=[{"role": role}], attempts=[
        row("delivered", role=role, chain="[red]literal[/red]", reason="SUCCEEDED"),
        _attempt("design", role=role, chain="main", node="EXPERIMENT_DESIGN_IMPLEMENT", state="OPEN"),
        _attempt("execute", role=role, chain="parallel", node="EXPERIMENT_EXECUTION", state="OPEN")])
    summary["host_observation"] = {"availability": "absent"}
    summary["experts"][0]["context_recovery"].update(context_failure_attempts=1,
        recovery_attempts=2, recovery_reported_tokens=1234, recovery_unknown_token_attempts=1,
        cli_unknown_pricing_attempts=3, cli_pricing_unavailable_attempts=1)
    goal = deepcopy(snapshot.goal)
    goal["research_contract"]["budget"].update(wallclock_hours=1, max_experiments=10)
    snapshot = replace(snapshot, overview=summary, goal=goal)

    async def exercise():
        app = HypoloomVNextMonitor(root, "compact", initial_snapshot=snapshot, interval=60, web_port=0)
        async with app.run_test(size=(150, 54)) as pilot:
            await pilot.pause()
            lanes = app.query_one(ExpertsPanel)
            text = _static_text(lanes.query_one(Static))
            assert "DES" in text and "RUN" in text and "2 OPEN" in text
            assert "OPEN?" in text and "Host absent" in text and "$" not in text
            assert "reported ≥1.23K tokens" in text and "usage unknown 1" in " ".join(text.replace("│", " ").split())
            assert "pricing" not in text
            tree_panel = app.query_one(HypothesesPanel)
            tree = tree_panel.chain_tree
            expert_node = tree.root.children[0]
            branch = next(node for node in expert_node.children if "[red]literal[/red]" in node.label.plain)
            assert "[red]literal[/red]" in branch.label.plain
            stage = branch.children[0]
            assert "✓ SUCCEEDED" in stage.label.plain
            assert any("70e1c1" in str(span.style) for span in stage.label.spans)
            branch.expand()
            await pilot.pause()
            tree.move_cursor(stage)
            await pilot.pause()
            selected = stage.data
            updated = deepcopy(summary)
            updated["experts"][0]["chains"][0]["stages"][0]["attempts"] = 2
            tree_panel._update_tree(updated, "")
            await pilot.pause()
            assert tree.cursor_node.data == selected and tree.cursor_node.parent.is_expanded
            app.save_screenshot("compact-tui.svg", path=tmp_path)
            lanes.update_snapshot(snapshot, "missing-role")
            assert role not in _static_text(lanes.query_one(Static))
            await pilot.resize_terminal(80, 32)
            await pilot.pause()
            assert app.screen.has_class("narrow")
            await pilot.press("?")
            await pilot.pause()
            assert "FP first output pass" in _static_text(app.query_one("#help-legend").query_one(Static))
    asyncio.run(exercise())


def test_institutional_panel_does_not_shadow_existing_service_retry_keys(tmp_path, monkeypatch):
    from hypoloom.literature_access_tui import LiteratureAccessScreen
    from hypoloom.tui import HypoloomVNextMonitor

    root, run_id = tmp_path / "institutional-keys", "institutional-keys"
    _create_fresh_run(root, run_id)
    snapshot = build_monitor_snapshot(root, run_id)
    _patch_tui_reader(monkeypatch, lambda *_: snapshot)
    retries = []
    monkeypatch.setattr(HypoloomVNextMonitor, "_dependency_retry", lambda self, service: retries.append(service))

    async def exercise():
        app = HypoloomVNextMonitor(root, run_id, initial_snapshot=snapshot, interval=60, web_port=0)
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            assert isinstance(app.screen, LiteratureAccessScreen)
            await pilot.press("l", "n")
            assert not retries
            await pilot.press("escape")
            await pilot.press("l", "n")
            assert retries == ["openalex", "telegram"]

    asyncio.run(exercise())
