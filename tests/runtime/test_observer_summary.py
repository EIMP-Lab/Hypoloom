from __future__ import annotations

import base64
import json

from hypoloom.expert_interaction import InteractionFacts, admit_observer_proposal
from hypoloom.observer_summary import build_run_summary, literature_catalog
from test_expert_interaction import _facts, _observer


def _attempt(identity, *, node="RESULT_ANALYSIS", role="scientist", chain="a", cycle=1,
             component="goal", command_id=None, state="CLOSED", reason="SUCCEEDED", start=10,
             end=20, inputs=(), update=None, subkind="MAIN", ordinal=1):
    request = {"run_id": "r", "role": role, "command": node, "component_id": component,
        "command_id": command_id or identity, "input_artifacts": list(inputs),
        "task_context": {"key": {"node_kind": node, "subkind": subkind, "chain": chain,
            "component_id": component, "cycle": cycle, "experiment_ordinal": ordinal}}}
    observation = {"request": request}
    if update is not None:
        observation["plugin_update"] = update
    return {"run_id": "r", "attempt_id": identity, "kind": "AGENT", "command_id": command_id or identity,
        "experiment_id": None, "state": state, "terminal_reason": reason if state == "CLOSED" else None,
        "started_at": start, "ended_at": end if state == "CLOSED" else None,
        "observation_json": json.dumps(observation)}


def _transition(revision, *, kind="RUN", identity="r", state="ACTIVE", at=0, payload=None):
    return {"workflow_id": identity, "workflow_kind": kind, "revision": revision,
        "command_id": f"cmd-{identity}-{revision}", "command_type": "ACCEPT_EXPERT_INTERACTION",
        "state": state, "created_at": at, "payload": payload or {}}


def _fact(kind, identity, payload, at=0):
    return {"fact_type": kind, "fact_id": identity, "payload": payload, "created_at": at}


def _summary(tmp_path, **kwargs):
    defaults = {"facts": [], "transitions": [], "attempts": [], "experts": [{"role": "scientist"}],
        "graph": {"nodes": [], "edges": []}, "observed_at": 100, "created_at": 0}
    defaults.update(kwargs)
    return build_run_summary(tmp_path, "r", **defaults)


def _packet(ref, papers):
    return {"ref": ref + "--literature-packet", "media_type": "application/json",
        "content_base64": base64.b64encode(json.dumps({"papers": papers}).encode()).decode()}


def test_reading_projection_counts_commands_separately_from_retries_and_waits(tmp_path):
    rows = [_attempt("parent", node="LITERATURE_REVIEW", state="OPEN"),
        _attempt("bad", node="PAPER_READ", command_id="reader-1", reason="FAILED"),
        _attempt("good", node="PAPER_READ", command_id="reader-1", start=30)]
    rows[1]["attempt_ordinal"], rows[2]["attempt_ordinal"] = 1, 2
    commands = [{"command_id": identity, "request_json": json.dumps({
        "task_context": {"expert_slot": "scientist"},
        "paper_reading": {"reading_id": identity, "parent_attempt_id": "parent"}})}
        for identity in ("reader-1", "reader-2", "reader-3")]
    commands.append({"command_id": "cancel", "request_json": json.dumps({"cancel_paper_reading": "reader-3"})})
    result = _summary(tmp_path, attempts=rows, reading_commands=commands, transitions=[_transition(1)])
    readers = result["experts"][0]["parallel_activity"]["readers"]
    assert readers["tasks"] == 3 and readers["attempts"] == 2
    assert readers["states"] == {"queued": 1, "running": 0, "completed": 1, "failed": 0, "cancelled": 1, "unknown": 0}
    assert readers["calls"]["unknown_attempts"] == 3
    assert readers["calls"]["rejected"] == 0  # An unobserved lower bound, not zero errors.


def test_current_proposal_counts_use_the_request_limit_and_two_wings(tmp_path):
    inputs = [{"ref": "config--expert-config", "media_type": "application/json",
        "content_base64": base64.b64encode(json.dumps({"effective_max_forks": 2}).encode()).decode()}]
    rows = [_attempt(name, node="HYPOTHESIS_PROPOSAL", subkind=name, state="OPEN", chain=None, inputs=inputs)
        for name in ("FORWARD", "BACKWARD")]
    value = _summary(tmp_path, attempts=rows)["experts"][0]
    proposal, = value["parallel_activity"]["proposals"]
    assert value["active_chains"] == value["hypotheses"] == proposal["accepted_candidates"] == 0
    assert proposal["candidate_limit"] == 2
    assert {stage["subkind"] for stage in proposal["stages"]} == {"FORWARD", "BACKWARD"}
    assert value["parallel_activity"]["readers"]["tasks"] == 0


def test_open_process_keeps_chain_active_and_deduplicates_its_execution_agent(tmp_path):
    creation = _transition(1, kind="EXPERIMENT", identity="exp", payload={
        "role": "scientist", "component_id": "primary.score", "expert_cycle": 2, "chain": "mechanism"})
    creation["command_type"] = "CREATE_EXPERIMENT"
    process = {"kind": "PROCESS", "attempt_id": "cpu", "run_id": "r", "workflow_id": "exp",
        "state": "OPEN", "started_at": 10, "ended_at": None}
    result = _summary(tmp_path, transitions=[creation], attempts=[process])
    assert result["experts"][0]["active_chains"] == result["totals"]["active_chains"] == 1
    agent = _attempt("execution", node="EXPERIMENT_EXECUTION", state="OPEN",
        component="primary.score", cycle=2, chain="mechanism")
    result = _summary(tmp_path, transitions=[creation], attempts=[process, agent])
    assert result["experts"][0]["active_chains"] == result["totals"]["active_chains"] == 1


def test_capacity_and_handoff_projection_separates_review_and_open_controller(tmp_path):
    from hypoloom.contract_normalization import EXPERT_LOOP_RESEARCH_LOOP
    from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
    contract = {"budget": {"max_active_agents": 4, "max_active_processes": 2, "max_active_reviews": 1},
        "methodology": {"controls": {"research_loop": EXPERT_LOOP_RESEARCH_LOOP}}}
    creation = _transition(1, kind="EXPERIMENT", identity="exp", at=20, payload={
        "role": "scientist", "component_id": "primary.score", "chain": "mechanism", "execution_node_command_id": "execution"})
    creation['command_type'] = 'CREATE_EXPERIMENT'
    process = {"kind": "PROCESS", "attempt_id": "cpu", "run_id": "r", "workflow_id": "exp",
        "state": "OPEN", "started_at": 21}
    controller = _attempt("execution", node="EXPERIMENT_EXECUTION", state="OPEN")
    review = _attempt("review", node="ANSWER_RENDEZVOUS", state="OPEN", chain=None)
    args = dict(facts=[_fact('GoalDefined', 'goal', {'research_contract': contract})],
        transitions=[creation], attempts=[process, controller, review], agent_aci_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION)
    value = _summary(tmp_path, **args)
    assert value['concurrency']['slots'] == {'AGENT': {'used': 1, 'limit': 4},
        'PROCESS': {'used': 1, 'limit': 2}, 'REVIEW': {'used': 1, 'limit': 1}}
    wait = value['experts'][0]['execution_wait']
    assert (wait['open_controllers'], wait['handed_off'], wait['oldest_controller_seconds']) == (1, 0, 80)
    controller.update(state='CLOSED', terminal_reason='SUCCEEDED', ended_at=25)
    observation = json.loads(controller['observation_json'])
    observation['request']['schema_version'] = CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
    observation['response'] = {'typed_candidate': {'waiting_for_experiment_id': 'exp'}}
    controller['observation_json'] = json.dumps(observation)
    value = _summary(tmp_path, **args)
    assert value['concurrency']['slots']['AGENT']['used'] == 0
    wait = value['experts'][0]['execution_wait']
    assert (wait['open_controllers'], wait['handed_off'], wait['pending_experiments']) == (0, 1, 1)
    args['agent_aci_version'] = None
    assert _summary(tmp_path, **args)['concurrency']['slots']['AGENT']['limit'] is None


def test_logical_retry_cost_reuses_closed_accounting_and_separates_reservations(tmp_path):
    rows = [_attempt("failed", command_id="design", reason="FAILED"),
            _attempt("good", command_id="design", start=25),
            _attempt("unknown", command_id="design", reason="LOST", start=30),
            _attempt("open", command_id="design", state="OPEN", start=40)]
    for row, cost in zip(rows, ("2.5", "1.25", None, "999")):
        observation = json.loads(row["observation_json"])
        observation["request"]["budget"] = {"max_cost": "7.5", "max_tokens": 100, "max_turns": 10}
        if cost is not None:
            observation["diagnostics"] = {"provider_accounting": {
                "token_usage": {"total_tokens": 10}, "turn_count": 1, "cost": cost, "cost_currency": "USD"}}
        row["observation_json"] = json.dumps(observation)
    stage = _summary(tmp_path, attempts=rows)["experts"][0]["stages"][0]
    assert stage["attempts"] == 4
    assert stage["failed_attempts"] == 2
    assert stage["accounting"] == {"reported_cost_usd": "3.75", "failed_reported_cost_usd": "2.5",
        "reserved_cost_usd": "15.0", "unknown_closed_attempts": 1, "open_attempts": 1,
        "reported_tokens": 20, "failed_reported_tokens": 10, "observed_unsettled_tokens": 0,
        "reserved_tokens": 200, "unavailable_token_attempts": 0}
    # Stale live diagnostics/raw streams must never be promoted to accepted cost.
    assert "999" not in json.dumps(stage["accounting"])


def test_unknown_call_observed_usage_is_visible_without_double_counting_or_settlement(tmp_path):
    row = _attempt("timeout", reason="TIMED_OUT")
    value = json.loads(row["observation_json"])
    value["request"]["budget"] = {"max_cost": "7.5", "max_tokens": 100, "max_turns": 10}
    row["observation_json"] = json.dumps(value)
    directory = tmp_path / "diagnostics/agents/r/timeout/provider/000001"
    directory.mkdir(parents=True)
    metadata = {"request_path": "/v1/messages", "upstream_status": 200,
        "response_body_complete": True, "message_complete": True, "served_models": ["model"],
        "usage": {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 40}}
    (directory / "exchange.json").write_text(json.dumps(metadata))
    summary = _summary(tmp_path, attempts=[row])
    assert summary["accounting"]["observed_unsettled_tokens"] == 53
    assert summary["accounting"]["reported_tokens"] == 0
    assert summary["accounting"]["reserved_tokens"] == 100
    assert summary["experts"][0]["stages"][0]["accounting"]["reported_cost_usd"] == "0"
    value["response"] = {"token_usage": {"total_tokens": 53}, "turn_count": 1, "cost": "0.1"}
    row["observation_json"] = json.dumps(value)
    accounting = _summary(tmp_path, attempts=[row])["accounting"]
    assert accounting["reported_tokens"] == 53 and accounting["observed_unsettled_tokens"] == 0
    assert accounting["reserved_tokens"] == 0 and accounting["unknown_closed_attempts"] == 0
    assert json.loads((directory / "exchange.json").read_text()) == metadata


def test_offline_host_exception_is_historical_not_a_liveness_claim(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    path = diagnostics / "host.jsonl"
    path.write_text(json.dumps({"kind": "host_exception", "timestamp": "2026-09-09T04:36:47Z",
        "phase": "agent_launch", "exception_type": "OperationalHalt",
        "message": "private /home/user token=do-not-display", "traceback": "private trace"}) + "\n{partial")
    before = path.read_bytes()
    summary = _summary(tmp_path, attempts=[_attempt("open", state="OPEN")])
    host = summary["host_observation"]
    assert host["availability"] == "absent"
    assert host["lock"] == "missing" and host["control_socket"] == "missing"
    assert host["orphaned"] is True
    assert host["last_recorded_exception"] == {"timestamp": "2026-09-09T04:36:47Z",
        "stage": "agent_launch", "exception_type": "OperationalHalt", "evidence": "diagnostics/host.jsonl"}
    assert "private" not in json.dumps(host)
    assert path.read_bytes() == before


def test_held_or_unreadable_lock_does_not_claim_an_orphan(tmp_path, monkeypatch):
    import fcntl
    import os
    from hypoloom.observer_summary import _host_observation
    with (tmp_path / "controller.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _host_observation(tmp_path)["lock"] == "held"
        assert _host_observation(tmp_path)["availability"] == "unconfirmed"
    assert _host_observation(tmp_path)["availability"] == "absent"
    def denied(*args, **kwargs):
        raise PermissionError("fixture")
    monkeypatch.setattr(os, "open", denied)
    assert _host_observation(tmp_path)["lock"] == "unknown"
    assert _host_observation(tmp_path)["availability"] == "unconfirmed"


def test_normal_cancellation_is_not_a_failed_attempt(tmp_path):
    stage = _summary(tmp_path, attempts=[_attempt("cancelled", reason="CANCELLED")])["stages"][0]
    assert stage["failed_attempts"] == 0


def test_cumulative_tokens_include_earlier_cycles_retries_and_unknown_reserves(tmp_path):
    from test_observer_reliability import row
    attempts = [row("first", command="old", reason="FAILED", cycle=1),
        row("retry", command="old", ordinal=2, cycle=1, start=30),
        row("latest", command="new", cycle=2, state="OPEN", start=50),
        row("unknown", command="other", role="peer", cost=None),
        _attempt("legacy", role="peer")]
    summary = _summary(tmp_path, attempts=attempts)
    own = next(expert for expert in summary["experts"] if expert["role"] == "scientist")
    assert [stage["attempt_id"] for stage in own["activity"]] == ["latest"]
    assert own["accounting"] == {"reported_tokens": 20, "failed_reported_tokens": 10, "reserved_tokens": 100,
        "observed_unsettled_tokens": 0, "unknown_closed_attempts": 0, "open_attempts": 1, "unavailable_token_attempts": 0}
    assert summary["accounting"] == {"reported_tokens": 20, "failed_reported_tokens": 10, "reserved_tokens": 200,
        "observed_unsettled_tokens": 0, "unknown_closed_attempts": 2, "open_attempts": 1, "unavailable_token_attempts": 1}
    # Current lane changes neither all-run nor per-expert accounting denominators.
    assert own["reliability"]["costs"]["retry_reported_tokens"] == 10


def test_current_expert_activity_includes_jobs_between_agent_calls(tmp_path):
    attempts = [_attempt("design", node="EXPERIMENT_DESIGN_IMPLEMENT"),
        _attempt("parallel", node="RESULT_ANALYSIS", chain="b", state="OPEN"),
        {"kind": "PROCESS", "attempt_id": "job", "experiment_id": "exp",
         "state": "OPEN", "started_at": 50}]
    transitions = [_transition(1, kind="EXPERIMENT", identity="exp", state="EXECUTING",
        payload={"role": "scientist"})]
    summary = _summary(tmp_path, attempts=attempts, transitions=transitions)
    activity = summary["experts"][0]["activity"]
    assert {item["attempt_id"] for item in activity} == {"parallel", "job"}
    process = next(item for item in activity if item["attempt_id"] == "job")
    assert process["node"] == "EXPERIMENT_EXECUTION" and process["elapsed_seconds"] == 50
    assert summary["reliability"]["counts"]["agent_attempts"] == 2
    assert summary["accounting"]["open_attempts"] == 1


def test_finished_lane_shows_latest_cycle_without_discarding_chain_history(tmp_path):
    summary = _summary(tmp_path, attempts=[_attempt("old", cycle=1, chain="old"),
        _attempt("recent", cycle=2, chain="new", start=50)])
    expert = summary["experts"][0]
    assert [item["attempt_id"] for item in expert["activity"]] == ["recent"]
    assert len(expert["chains"]) == 2


def test_terminal_elapsed_and_separate_human_wait_use_run_creation(tmp_path):
    facts = [_fact("GoalDefined", "goal", {"research_contract": {"budget": {"wallclock_hours": 1}}}, 10)]
    transitions = [_transition(0, at=3, state="CREATED"), _transition(1, at=20, state="WAITING_HUMAN"),
        _transition(2, at=30, state="WAITING_HUMAN"), _transition(3, at=50),
        _transition(4, at=80, state="COMPLETED")]
    summary = _summary(tmp_path, facts=facts, transitions=transitions, observed_at=500)
    assert summary["elapsed_seconds"] == 80
    assert summary["waiting_human_seconds"] == 30
    assert summary["remaining_seconds"] == 3520
    assert summary["last_activity_at"] == 80


def test_open_stages_preserve_parallel_chains_and_collapse_retry_attempts(tmp_path):
    attempts = [_attempt("first", command_id="design-a", node="EXPERIMENT_DESIGN_IMPLEMENT", reason="FAILED"),
        _attempt("retry", command_id="design-a", node="EXPERIMENT_DESIGN_IMPLEMENT", state="OPEN", start=30),
        _attempt("peer", node="EXPERIMENT_EXECUTION", state="OPEN", chain="b", start=40)]
    summary = _summary(tmp_path, attempts=attempts)
    stages = summary["experts"][0]["stages"]
    assert {item["attempt_id"] for item in stages} == {"retry", "peer"}
    assert next(item for item in stages if item["chain"] == "a")["attempts"] == 2
    assert next(item for item in stages if item["chain"] == "a")["elapsed_seconds"] == 70
    assert summary["totals"]["active_chains"] == 2
    assert any("heartbeat" in notice for notice in summary["notices"])


def test_papers_deduplicate_exact_bibliography_across_retries_and_roles(tmp_path):
    packet = _packet("query", [{"bibliographic_identity": "doi:10.1/a", "title": "A", "retrieval_disposition": "PDF_UNAVAILABLE"},
        {"bibliographic_identity": "openalex:W2", "title": "B", "retrieval_disposition": "RETRIEVED"}])
    another = _packet("query-next", [{"bibliographic_identity": "doi:10.1/a", "title": "A", "retrieval_disposition": "RETRIEVED"}])
    attempts = [_attempt("lit-a", node="LITERATURE_REVIEW", inputs=[packet]),
        _attempt("lit-retry", node="LITERATURE_REVIEW", inputs=[packet]),
        _attempt("lit-b", node="LITERATURE_REVIEW", role="other", inputs=[another])]
    summary = _summary(tmp_path, attempts=attempts)
    assert summary["totals"]["literature"] == {"retrieved": 2, "pdf_available": 2, "coverage": "complete"}
    own = next(item for item in summary["experts"] if item["role"] == "scientist")
    assert own["literature"] == {"retrieved": 2, "pdf_available": 1, "coverage": "complete"}
    catalog = literature_catalog(tmp_path, "r", attempts)
    assert len(catalog["papers"]) == 2
    assert "read" not in summary["totals"]["literature"]


def test_download_reasons_follow_exact_packet_identity_and_keep_old_unknown(tmp_path):
    packet = _packet('q', [{'bibliographic_identity': 'doi:10.1/a', 'title': 'A',
        'retrieval_disposition': 'PDF_UNAVAILABLE'}])
    old = _attempt('old', node='LITERATURE_REVIEW', inputs=[packet])
    new = _attempt('new', node='LITERATURE_REVIEW', inputs=[packet])
    observation = json.loads(new['observation_json'])
    observation['preflight_diagnostics'] = {'literature_download_trace': [
        {'bibliographic_identity': 'doi:10.1/foreign', 'events': [{'outcome': 'private-wrong-paper'}]},
        {'bibliographic_identity': 'doi:10.1/a', 'events': [{'stage': 'fetch', 'outcome': 'failed',
            'failure_code': 'PDF_HTTP', 'http_status': 403, 'source_host': 'example.org', 'raw_body': 'private'}]}]}
    new['observation_json'] = json.dumps(observation)
    assert literature_catalog(tmp_path, 'r', [old])['papers'][0]['download_coverage'] == 'unknown'
    result = literature_catalog(tmp_path, 'r', [old, new])['papers'][0]
    assert result['retrieval_disposition'] == 'PDF_UNAVAILABLE'
    assert result['download_coverage'] == 'recorded'
    assert len(result['download_attempts']) == 1
    assert result['download_attempts'][0]['attempt_id'] == 'new'
    assert result['download_attempts'][0]['events'][0]['http_status'] == 403
    assert 'private' not in json.dumps(result)
    assert _summary(tmp_path, attempts=[old, new])['totals']['literature']['pdf_available'] == 0


def test_absent_old_literature_evidence_is_unknown_but_query_plan_is_not_missing(tmp_path):
    attempts = [_attempt("old", node="LITERATURE_REVIEW")]
    summary = _summary(tmp_path, attempts=attempts)
    assert summary["totals"]["literature"] == {"retrieved": None, "pdf_available": None, "coverage": "unavailable"}
    assert summary["totals"]["skills"]["count"] is None
    query = _summary(tmp_path, attempts=[_attempt("query", node="LITERATURE_REVIEW", subkind="QUERY_PLAN")])
    assert query["totals"]["literature"] == {"retrieved": 0, "pdf_available": 0, "coverage": "complete"}


def test_experiments_and_hypotheses_are_not_attempts_and_pruning_retains_assessment(tmp_path):
    facts = [_fact("HypothesisProposed", "hf", {"hypothesis_id": "h", "proposer_role": "scientist"}),
        _fact("ExperimentPreregistered", "ef", {"experiment_id": "e", "hypothesis_id": "h"}),
        _fact("CanonicalOutcomeAccepted", "of", {"experiment_id": "e", "outcome": "SUCCESS"})]
    graph = {"nodes": [{"hypothesis_id": "h", "proposer_role": "scientist", "component_id": "goal",
        "chain": "a", "cycle": 1, "assessment": "SUPPORTED", "state": "PRUNED"}], "edges": []}
    transitions = [_transition(1, kind="EXPERIMENT", identity="e", state="EXECUTING"),
        _transition(2, kind="EXPERIMENT", identity="e", state="TERMINAL")]
    summary = _summary(tmp_path, facts=facts, graph=graph, transitions=transitions,
        attempts=[_attempt("one"), _attempt("two")])
    assert summary["totals"]["experiments"] == {"total": 1, "states": {"TERMINAL": 1}, "outcomes": {"SUCCESS": 1}}
    assert summary["totals"]["hypotheses"] == 1
    own = summary["experts"][0]
    assert own["experiments"]["total"] == 1
    assert own["chains"][0]["nodes"] == [{"hypothesis_id": "h", "assessment": "SUPPORTED", "pruned": True}]


def test_evolution_counts_logical_completion_changed_acceptance_and_failed_attempts(tmp_path):
    attempts = [_attempt("failed", node="SELF_EVOLVE", command_id="evolve", reason="FAILED", update={"base_commit": "a", "commit": "b"}),
        _attempt("retry", node="SELF_EVOLVE", command_id="evolve", start=25, update={"base_commit": "a", "commit": "b"}),
        _attempt("noop", node="SELF_EVOLVE", cycle=2, start=30, update={"base_commit": "b", "commit": "b"})]
    evolution = _summary(tmp_path, attempts=attempts)["totals"]["evolutions"]
    assert evolution == {"completed": 2, "changed": 1, "failed": 1}


def test_interaction_counts_occurrences_and_goal_stop_aborts_close_activity(tmp_path):
    events = [{"action": "leader_selected", "source_attempt_id": "l", "leader": {"role": "scientist"}},
        {"action": "leader_round_started", "source_attempt_id": "l", "leader": {"role": "scientist"}},
        {"action": "leader_aborted", "source_attempt_id": "l", "leader": {"role": "scientist"}},
        {"action": "debate_proposed", "debate_id": "d", "source_attempt_id": "x", "proposal": {"debate": {"participants": [{"scope": {"role": "scientist"}}]}}},
        {"action": "debate_started", "debate_id": "d", "source_attempt_id": "x"},
        {"action": "debate_aborted", "debate_id": "d", "source_attempt_id": "x"}]
    transitions = [_transition(i, payload={"expert_interaction": event}) for i, event in enumerate(events)]
    summary = _summary(tmp_path, transitions=transitions)
    expected = {"started": 1, "active": 0, "completed": 0, "failed": 0, "aborted": 1}
    assert summary["totals"]["leader"] == expected
    assert summary["totals"]["debate"] == expected
    assert summary["experts"][0]["debate"] == expected


def test_admitted_leader_null_debate_preserves_selection_start_and_completion(tmp_path):
    proposal = admit_observer_proposal(_observer(), facts=InteractionFacts.from_rows("run-1", _facts()))
    selected = proposal.control_payload("observer")["expert_interaction"]
    assert selected["proposal"]["debate"] is None
    leader = {"role": proposal.leader.role, "component_id": proposal.leader.contributions[0].component_id,
        "after_cycle": 1}
    selected["leader"] = leader
    transitions = [_transition(1, payload={"expert_interaction": selected})]
    counts = {"started": 0, "active": 0, "completed": 0, "failed": 0, "aborted": 0}
    for action, expected in ((None, counts),
            ("leader_round_started", {**counts, "started": 1, "active": 1}),
            ("leader_round_finished", {**counts, "started": 1, "completed": 1})):
        if action is not None:
            transitions.append(_transition(len(transitions) + 1, payload={"expert_interaction": {
                "action": action, "source_attempt_id": "observer", "leader": {**leader, "cycle": 2}}}))
        summary = _summary(tmp_path, transitions=transitions, experts=[{"role": leader["role"]}])
        assert summary["totals"]["leader"] == expected
        assert summary["experts"][0]["leader"] == expected
        assert summary["totals"]["debate"] == counts


def test_admitted_observation_null_leader_and_debate_add_no_interactions(tmp_path):
    proposal = admit_observer_proposal(_observer("none"), facts=InteractionFacts.from_rows("run-1", _facts()))
    assert proposal.leader is None and proposal.debate is None
    summary = _summary(tmp_path, transitions=[_transition(1, payload=proposal.control_payload("observer"))])
    counts = {"started": 0, "active": 0, "completed": 0, "failed": 0, "aborted": 0}
    assert summary["totals"]["leader"] == summary["totals"]["debate"] == counts


def test_admitted_debate_null_leader_keeps_roles_with_unscoped_chains(tmp_path):
    candidate = _observer("debate")
    for participant in candidate["debate"]["participants"]:
        participant["scope"]["chain"] = None
    proposal = admit_observer_proposal(candidate, facts=InteractionFacts.from_rows("run-1", _facts()))
    proposed = proposal.control_payload("observer")["expert_interaction"]
    assert proposed["proposal"]["leader"] is None
    proposed["debate_id"] = "debate"
    transitions = [_transition(1, payload={"expert_interaction": proposed}),
        _transition(2, payload={"expert_interaction": {"action": "debate_started", "debate_id": "debate",
            "source_attempt_id": "observer", "participants": [
                {"participant": participant} for participant in proposed["proposal"]["debate"]["participants"]]}})]
    roles = [item.scope.role for item in proposal.debate.participants]
    summary = _summary(tmp_path, transitions=transitions, experts=[{"role": role} for role in roles])
    expected = {"started": 1, "active": 1, "completed": 0, "failed": 0, "aborted": 0}
    assert summary["totals"]["debate"] == expected
    assert all(expert["debate"] == expected for expert in summary["experts"])
    assert summary["totals"]["leader"]["started"] == 0


def test_waiting_human_to_observation_and_missing_start_are_explicit(tmp_path):
    summary = _summary(tmp_path, transitions=[_transition(1, at=30, state="WAITING_HUMAN")])
    assert summary["waiting_human_seconds"] == 70
    missing = _summary(tmp_path, created_at=None)
    assert missing["elapsed_seconds"] is None
    assert missing["remaining_seconds"] is None


def test_same_chain_names_in_different_components_are_distinct(tmp_path):
    summary = _summary(tmp_path, attempts=[_attempt("a", state="OPEN", component="left"),
        _attempt("b", state="OPEN", component="right")])
    assert summary["totals"]["chains"] == 2
    assert summary["totals"]["active_chains"] == 2
    assert {chain["component_id"] for chain in summary["experts"][0]["chains"]} == {"left", "right"}


def test_literature_cache_is_reused_without_mutable_output_aliasing(tmp_path):
    from hypoloom.observer_summary import _packet_papers

    _packet_papers.cache_clear()
    packet = _packet("query", [{"bibliographic_identity": "doi:10.1/cache", "title": "Cached",
        "retrieval_disposition": "RETRIEVED", "pdf_trace": {"abstract_summary": "original"}}])
    attempts = [_attempt("paper", node="LITERATURE_REVIEW", inputs=[packet])]
    first = literature_catalog(tmp_path, "r", attempts)
    first["papers"][0]["pdf_trace"]["abstract_summary"] = "changed by consumer"
    second = literature_catalog(tmp_path, "r", attempts)
    assert second["papers"][0]["pdf_trace"]["abstract_summary"] == "original"
    assert _packet_papers.cache_info().misses == 1
    assert _packet_papers.cache_info().hits == 1


def test_malformed_packet_is_unavailable_and_foreign_run_is_excluded(tmp_path):
    missing = _attempt("broken", node="LITERATURE_REVIEW",
        inputs=[{"ref": "bad--literature-packet", "content_base64": "???"}])
    foreign = {**_attempt("foreign", node="LITERATURE_REVIEW"), "run_id": "other-run"}
    catalog = literature_catalog(tmp_path, "r", [missing, foreign])
    assert catalog["coverage"] == "unavailable"
    assert catalog["unavailable_attempt_ids"] == ["broken"]


def test_missing_legacy_chain_and_evolution_evidence_are_unknown(tmp_path):
    graph = {"nodes": [{"hypothesis_id": "old", "component_id": "goal", "proposer_role": "scientist"}], "edges": []}
    summary = _summary(tmp_path, graph=graph, attempts=[_attempt("old-evolve", node="SELF_EVOLVE", chain=None)])
    assert summary["totals"]["chains"] is None
    assert summary["totals"]["chain_coverage"] == "unavailable"
    assert summary["totals"]["evolutions"] == {"completed": 1, "changed": None, "failed": 0}


def test_one_known_plugin_does_not_hide_another_experts_missing_binding(tmp_path, monkeypatch):
    from hypoloom import observer_details

    monkeypatch.setattr(observer_details, "skill_catalog", lambda *args: {
        "skills": [{"role": "scientist", "name": "native-skill", "current": True}],
        "versions": [{"role": "scientist", "available": True, "current": True}]})
    summary = _summary(tmp_path, experts=[{"role": "scientist"}, {"role": "old-expert"}])
    assert summary["totals"]["skills"] == {"count": 1, "coverage": "partial"}
    assert summary["experts"][1]["skills"] == {"count": None, "coverage": "unavailable"}


def test_grounding_query_auxiliary_uses_request_kind_and_needs_no_packet(tmp_path):
    # Real G5: GROUNDING_QUERY_PLAN deliberately shares LITERATURE_REVIEW's
    # Agent command but is an AuxiliaryTask, not a MAIN ResearchNodeTask.
    row = _attempt("grounding-query", node="LITERATURE_REVIEW", chain=None, state="OPEN")
    observation = json.loads(row["observation_json"])
    observation["request"]["task_context"] = {"kind": "AUXILIARY", "request_kind": "GROUNDING_QUERY_PLAN",
        "run_id": "r", "component_id": "goal", "expert_slot": "scientist", "cycle": None, "ordinal": 1}
    row["observation_json"] = json.dumps(observation)
    summary = _summary(tmp_path, attempts=[row])
    assert summary["totals"]["literature"] == {"retrieved": 0, "pdf_available": 0, "coverage": "complete"}
    assert summary["experts"][0]["stages"] == []
    assert summary["global_tasks"]["stages"][0]["node"] == "GROUNDING_QUERY_PLAN"


def test_current_batch_level_hypothesis_update_does_not_imply_missing_chain(tmp_path):
    # Accepted batched runs use one chain-neutral HYPOTHESIS_UPDATE after
    # adjudication; only experiment-bound nodes require a chain identity.
    graph = {"nodes": [{"hypothesis_id": "a", "component_id": "goal", "proposer_role": "scientist", "chain": "a", "cycle": 1},
        {"hypothesis_id": "b", "component_id": "goal", "proposer_role": "scientist", "chain": "b", "cycle": 1}], "edges": []}
    summary = _summary(tmp_path, graph=graph, attempts=[_attempt("update", node="HYPOTHESIS_UPDATE", chain=None)])
    assert summary["totals"]["chain_coverage"] == "complete"
    assert summary["totals"]["chains"] == 2


def test_stage_token_totals_partition_cycles_retries_and_roles(tmp_path):
    from test_observer_reliability import row
    attempts = [row("failed", command="old", reason="FAILED", cycle=1),
        row("retried", command="old", ordinal=2, cycle=1, start=30),
        row("later", command="new", cycle=2, state="OPEN", start=50),
        row("peer", command="peer", role="peer", cost=None)]
    summary = _summary(tmp_path, attempts=attempts)
    for aggregate in [summary, *summary["experts"]]:
        for name, value in aggregate["accounting"].items():
            assert sum(stage["accounting"][name] for stage in aggregate["stage_accounting"]) == value
    assert summary["accounting"]["reported_tokens"] == 20
    assert summary["accounting"]["failed_reported_tokens"] == 10
    assert summary["accounting"]["reserved_tokens"] == 200
    assert summary["accounting"]["unknown_closed_attempts"] == 1
