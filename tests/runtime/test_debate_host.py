from __future__ import annotations

import json
from uuid import UUID

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.expert_loop import AuxiliaryRequestKind, ResearchNodeSubkind
from hypoloom.interaction_host import InteractionHost
from hypoloom.lifecycle import Command, RunState
from test_expert_interaction import _observer
from test_interaction_host import StoredHost, node, reasons


def session(identity, parent=None, *, first=False):
    binding = {"session_id": identity, "parent_session_id": parent}
    launch = ["--session-id", identity] if parent is None else ["--resume", identity]
    if parent is not None and first:
        launch = ["--resume", parent, "--fork-session", "--session-id", identity]
    return {"session_binding": binding, "session": {**binding, "launch_arguments": launch}}


def setup(tmp_path, *, rounds=4, resources=False, history_bytes=0, interaction_options=None, agent_version=14, selected=True, pending_self=False, peer_inflight=False):
    """SQLite controller unit seam; session metadata is a fixture, not real CC."""
    host = StoredHost(tmp_path / "state", debate=True, rounds=rounds, interaction_options=interaction_options, agent_version=agent_version, selected=selected, pending_self=pending_self)
    originals = {}
    for number, (role, component, hypothesis) in enumerate((("researcher", "primary.score", "hyp-1"), ("critic", "must.robustness", "hyp-2")), 1):
        host.record(node("LITERATURE_REVIEW", role, component))
        task = node("RESULT_ANALYSIS", role, component, chain="chain-a")
        item = EmbeddedInput.from_bytes(task.command_id + "--batch-context", "application/json",
            json.dumps({"chains": {"chain-a": hypothesis}}).encode())
        inputs = [item]
        if history_bytes:
            inputs.append(EmbeddedInput.from_bytes(task.command_id + "--old-history", "text/plain", b"x" * history_bytes))
        if resources:
            from hypoloom.expert_plugin import ExpertPluginStore
            from hypoloom.expert_memory import ExpertMemory
            from hypoloom.research_projection import ResearchProjection, freeze_role_view
            plugin = ExpertPluginStore(host.state_root).ensure("run-1", role, "Inspect accepted evidence.")
            memory = ExpertMemory(host.state_root).ensure("run-1", role)
            view_id = role + "-source-view"
            files = {"background.md": b"# Existing fixture background\n", "graph/edges.jsonl": b"",
                f"experts/{role}/state.json": json.dumps({"role": role}).encode()}
            projection = ResearchProjection(host.state_root, "run-1", {}, {role: files})
            freeze_role_view(host.state_root, "run-1", role, view_id, projection=projection)
            inputs.append(EmbeddedInput.from_bytes(task.command_id + "--expert-resources", "application/json",
                json.dumps({"plugin": plugin.to_dict(), "memory_commit": memory, "research_view_id": view_id}).encode()))
        originals[role] = host.record(task, inputs=inputs, extra_observation=session(str(UUID(int=number))))
    if peer_inflight:
        host.record(node("LITERATURE_REVIEW", "researcher", "primary.score", cycle=2), terminal="FAILED")
    view = InteractionHost(host)
    task = view._triggers("run-1")[0][0]
    proposal = _observer("debate")
    proposal["debate"]["participants"][1]["scope"]["chain"] = "chain-a"
    source = host.record(task, candidate=proposal, inputs=view.inputs("run-1", task))
    view.drive("run-1")
    return host, view, source, originals


def test_judge_input_does_not_repeat_large_original_session_history(tmp_path):
    host, view, _, _ = setup(tmp_path, history_bytes=650_000)
    try:
        view, active, binding, completed = debate_rounds(host, view)
        assert len(json.dumps(completed).encode()) > 4 * 1024 * 1024
        inputs = view.debate.inputs("run-1", view.debate._judge_task("run-1", active))
        assert sum(len(item.content_bytes()) for item in inputs) < 200_000
        context = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--debate-judge-context")))
        assert "round_attempts" not in context
        assert {item["source_attempt_id"] for item in context["rounds"]} == {row["attempt_id"] for row in completed}
        originals = {row["attempt_id"]: json.loads(row["observation_json"])["response"]["typed_candidate"] for row in completed}
        assert all(item["statement"] == originals[item["source_attempt_id"]] for item in context["rounds"])
    finally:
        host.store.close()


def test_success_stop_aborts_debate_without_judge_or_restart(tmp_path):
    # Controller unit seam; no claim that fixture sessions are native CC.
    host, view, source, _ = setup(tmp_path)
    active = view.debate._active("run-1")
    facts = host.store.list_facts("run-1")
    with host.store.transaction() as tx:
        tx.append_transition(host._transition_record(tx, "run-1", "run-1", "RUN", Command.RECORD_GOAL_VERDICT,
            RunState.STOPPING, payload={"stop_disposition": "COMPLETED"}))
    view.stop_for_goal("run-1", "unit-verdict")
    view.stop_for_goal("run-1", "unit-verdict")
    restored = InteractionHost(host)
    assert restored.debate._active("run-1") is None and not reasons(host)
    events = [event for _, event in restored._events("run-1")]
    assert [event["action"] for event in events] == ["debate_proposed", "debate_aborted"]
    assert events[-1]["debate_id"] == active["debate_id"]
    assert events[-1]["source_attempt_id"] == source["attempt_id"]
    assert events[-1]["goal_verdict_id"] == "unit-verdict"
    assert host.store.list_facts("run-1") == facts
    host.store.close()


def debate_rounds(host, view):
    view.drive("run-1")  # persist exact original/fork relationship first
    active = view.debate._active("run-1")
    started = view.debate._event("run-1", active["debate_id"], "debate_started")
    binding = view.debate._binding("run-1", started)
    completed = []
    for number in range(1, 4):
        for fork in binding.participants:
            view.drive("run-1")
            task, role, component = host.launched[-1]
            assert task.kind is AuxiliaryRequestKind.DEBATE
            assert task == view.debate._round_task(active, fork, number)
            inputs = view.debate.inputs("run-1", task)
            context = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--debate-context")))
            assert context["round"] == number
            assert task.ordinal == active["source_revision"] * 3 + number
            assert len([item for item in inputs if item.ref == fork.original_attempt_id]) == 1
            if number > 1:
                assert context["previous_opponent_attempt_id"] in {row["attempt_id"] for row in completed}
            candidate = {"position": "Compare the existing evidence.", "arguments": ["The scoped criterion matters."],
                "evidence": ["out-1", "out-2"], "concessions": [], "web_sources": []}
            from hypoloom.expert_interaction import debate_session_id
            version = int(host.store.get_run("run-1")["agent_aci_version"])
            identity = f"attempt-{len(host.store.list_attempts()) + 1}"
            actual = debate_session_id(fork.session_id, identity, version)
            row = host.record(task, candidate=candidate, inputs=inputs,
                extra_observation=session(actual, None if version >= 29 else fork.original_session_id, first=number == 1))
            completed.append(row)
            view = InteractionHost(host)  # no in-memory Debate progress or round queue
    return view, active, binding, completed


def judge(host, view, active, completed):
    view.drive("run-1")
    task, role, component = host.launched[-1]
    assert (task.kind, role, component) == (AuxiliaryRequestKind.DEBATE_JUDGE, "grounding", "grounding")
    inputs = view.debate.inputs("run-1", task)
    context = json.loads(next(item.content_bytes() for item in inputs if item.ref.endswith("--debate-judge-context")))
    assert {item["source_attempt_id"] for item in context["rounds"]} == {row["attempt_id"] for row in completed}
    candidate = {"winner": "critic", "scores": [{"role": role, "score": score,
        "round_refs": [row["attempt_id"] for row in completed if json.loads(row["observation_json"])["request"]["role"] == role]}
        for role, score in (("researcher", "7"), ("critic", "8"))], "keep": ["critic"], "prune": ["researcher"],
        "rationale": "The critique cites the relevant criterion.", "supporting_refs": [row["attempt_id"] for row in completed] + ["out-2"]}
    row = host.record(task, inputs=inputs, candidate=candidate, extra_observation=session(str(UUID(int=99))))
    view.drive("run-1")
    return row


@pytest.mark.parametrize("version", [14, 29])
def test_three_rounds_two_participants_are_six_real_attempt_rows_with_one_judge(tmp_path, version):
    host, view, observer, originals = setup(tmp_path, agent_version=version)
    before = host.store.list_facts("run-1")
    assert reasons(host, "researcher", "primary.score") == ()  # pause is exact chain, not whole component
    assert view.debate.pending("run-1")
    view, active, binding, completed = debate_rounds(host, view)
    assert len(completed) == len({row["attempt_id"] for row in completed}) == 6
    assert {fork.original_attempt_id for fork in binding.participants} == {row["attempt_id"] for row in originals.values()}
    source = judge(host, view, active, completed)
    verdict = view.debate._event("run-1", active["debate_id"], "debate_verdict")
    assert verdict["parent_observer_attempt_id"] == observer["attempt_id"]
    assert verdict["source_attempt_id"] == source["attempt_id"]
    assert verdict["pruned_by"][0]["debate_id"] == active["debate_id"]
    assert verdict["pruned_by"][0]["loser_hypothesis_id"] == "hyp-1"
    assert host.store.list_facts("run-1") == before
    host.store.close()


@pytest.mark.parametrize("version", [14, 29])
def test_judge_releases_only_debate_and_restarts_both_at_hypothesis_stage(tmp_path, version):
    host, view, _, _ = setup(tmp_path, agent_version=version)
    host.control(Command.PAUSE_PARTICIPANT, {"participant_control": {"action": "pause",
        "scope": {"role": "researcher", "component_id": "primary.score", "chain": "chain-a"}, "reason": "operator"}})
    view, active, binding, completed = debate_rounds(host, view)
    judge(host, view, active, completed)
    from hypoloom.participant_controls import ParticipantScope, fold_participant_controls
    controls = fold_participant_controls(host.store.inspect("SELECT * FROM workflow_transitions WHERE workflow_kind='RUN'"), host.contract.role_ids)
    assert controls.reasons_for(ParticipantScope("researcher", "primary.score", "chain-a")) == ("operator",)
    view.drive("run-1")
    view = InteractionHost(host)  # The authorization is durable, not this delegate's cache.
    for role, component in (("researcher", "primary.score"), ("critic", "must.robustness")):
        plan = view.debate.designated_round("run-1", component)
        assert plan["cycle"] == 2 and plan["source_cycle"] == 1 and plan["restart_from_hypothesis"]
        assert not view.blocks_next_cycle("run-1", component, 2), "an accepted two-participant restart cannot wait on an Observer that Debate itself defers"
        assert view.blocks_next_cycle("run-1", component, 3)
        literature = host.store.get_attempt(plan["source_literature_attempt_id"])
        assert literature["command_id"] == plan["source_literature_command_id"]
        assert json.loads(literature["observation_json"])["request"]["command"] == "LITERATURE_REVIEW"
        synthesis = node("HYPOTHESIS_PROPOSAL", role, component, cycle=2, subkind=ResearchNodeSubkind.SYNTHESIS)
        inputs = view.debate.promotion_inputs("run-1", synthesis)
        assert bool(inputs) == (role == "critic")
        if inputs:
            context = json.loads(inputs[0].content_bytes())
            assert context["source_attempt_id"] == completed[-1]["attempt_id"]
            assert view.debate.promotion_inputs("run-1", node("EXPERIMENT_DESIGN_IMPLEMENT", role, component, cycle=2, chain="new-chain"))
        assert not view.debate.promotion_inputs("run-1", node("CYCLE_REPORT", role, component, cycle=2))
        host.finish_round(role, component, 2)
    view.drive("run-1")
    assert not view.debate.pending("run-1")
    host.store.close()


def test_directed_restart_uses_debate_parent_when_adjudication_selected_nothing(tmp_path):
    host, view, _, _ = setup(tmp_path, agent_version=21, selected=False)
    try:
        view, active, binding, completed = debate_rounds(host, view)
        judge(host, view, active, completed)
        view.drive("run-1")
        assert len(view.debate._designations("run-1", active["debate_id"])) == 2
        for role, component, parent in (("researcher", "primary.score", "hyp-1"), ("critic", "must.robustness", "hyp-2")):
            plan = view.debate.designated_round("run-1", component)
            assert plan["parent_hypothesis_id"] == parent and plan["cycle"] == 2
            assert host._batch_history("run-1", component, role).selected_tip is None
            assert view.directed_round("run-1", role, component, 2) == plan
            task = node("HYPOTHESIS_PROPOSAL", role, component, cycle=2, subkind=ResearchNodeSubkind.FORWARD)
            guidance = json.loads(view.debate.planning_inputs("run-1", task)[0].content_bytes())
            assert guidance["source_judge_attempt_id"] == view.debate._event("run-1", active["debate_id"], "debate_verdict")["source_attempt_id"]
        recovered = InteractionHost(host)
        recovered.drive("run-1")
        assert len(recovered.debate._designations("run-1", active["debate_id"])) == 2
        assert recovered.debate._event("run-1", active["debate_id"], "debate_restarts_finished") is None
    finally:
        host.store.close()


def test_each_debater_waits_only_for_its_own_admitted_cycle(tmp_path):
    host, view, _, _ = setup(tmp_path, agent_version=21, selected=False, peer_inflight=True)
    try:
        view, active, binding, completed = debate_rounds(host, view)
        judge(host, view, active, completed)
        view.drive("run-1")
        assert view.debate.designated_round("run-1", "must.robustness")["cycle"] == 2
        assert view.debate.designated_round("run-1", "primary.score") is None
        assert view.debate.blocks_next_cycle("run-1", "primary.score", 3)
        assert not view.debate.blocks_next_cycle("run-1", "primary.score", 2)
        assert view.debate._event("run-1", active["debate_id"], "debate_restarts_finished") is None
        # The failed source becomes terminal through the existing Host decision.
        task = node("LITERATURE_REVIEW", "researcher", "primary.score", cycle=2)
        attempt = host._expert_attempts(task)[0]
        host.control(Command.AGENT_ATTEMPT_FINAL_FAILURE, {"agent_final_failure": True,
            "attempt_id": attempt["attempt_id"], "failure_code": "literature_exhausted",
            "failed_task_command_id": task.command_id, "role": "researcher", "component_id": "primary.score"})
        view = InteractionHost(host)
        view.drive("run-1")
        decisions = view.debate._designations("run-1", active["debate_id"])
        assert len(decisions) == 2
        assert decisions[-1][1]["not_started"] == [{"role": "researcher", "component_id": "primary.score", "reason": "source_cycle_failed"}]
    finally:
        host.store.close()


def test_restart_parent_can_precede_the_current_source_cycle(tmp_path):
    from dataclasses import replace
    from hypoloom.research_kernel import ResearchKernel
    from test_domain import all_facts
    host, view, _, _ = setup(tmp_path, agent_version=21, selected=False, peer_inflight=True)
    try:
        view, active, binding, completed = debate_rounds(host, view)
        judge(host, view, active, completed)
        view.drive("run-1")
        assert view.debate.designated_round("run-1", "primary.score") is None
        hypothesis = replace(all_facts()[1], fact_id="fact-hyp-c2", hypothesis_id="hyp-c2",
            parent_hypothesis_id="hyp-1", relation="revision")
        with host.store.transaction() as tx:
            tx.append_fact(ResearchKernel().construct_fact(hypothesis, tx))
        host.control(Command.ACCEPT_HYPOTHESIS, {"role": "researcher", "component_id": "primary.score",
            "node_command_id": "c2-synthesis", "agent_attempt_id": "c2-synthesis-attempt",
            "hypothesis_batch": {"cycle": 2, "chains": {"chain-a": "hyp-c2"}}})
        host.control(Command.ACCEPT_ADJUDICATION, {"role": "researcher", "component_id": "primary.score",
            "adjudication": {"cycle": 2, "batch_command_id": "c2-synthesis", "selected_tip": None}})
        literature = host.record(node("LITERATURE_REVIEW", "researcher", "primary.score", cycle=2))
        host.finish_round("researcher", "primary.score", cycle=2)
        view = InteractionHost(host)
        view.drive("run-1")
        plan = view.debate.designated_round("run-1", "primary.score")
        assert (plan["parent_hypothesis_id"], plan["source_cycle"], plan["cycle"]) == ("hyp-1", 2, 3)
        assert plan["source_literature_attempt_id"] == literature["attempt_id"]
        assert view.directed_round("run-1", "researcher", "primary.score", 3) == plan
    finally:
        host.store.close()


def test_debate_cannot_skip_rounds_to_launch_judge_or_overwrite_source(tmp_path):
    host, view, _, originals = setup(tmp_path)
    source_bytes = {role: row["observation_json"] for role, row in originals.items()}
    view.drive("run-1")
    active = view.debate._active("run-1")
    with pytest.raises(ValueError, match="exactly three rounds"):
        view.debate.inputs("run-1", view.debate._judge_task("run-1", active))
    assert {role: host.store.get_attempt(row["attempt_id"])["observation_json"] for role, row in originals.items()} == source_bytes
    host.store.close()


def test_debate_round_inputs_keep_fixed_origin_without_reembedding_previous_requests(tmp_path):
    host, view, _, originals = setup(tmp_path)
    try:
        view, active, binding, completed = debate_rounds(host, view)
        projected = 0
        sizes = {}
        for row in completed:
            observed = json.loads(row["observation_json"])
            inputs = [EmbeddedInput.from_dict(item) for item in observed["request"]["input_artifacts"]]
            sources = [json.loads(item.content_bytes()) for item in inputs
                       if item.ref in {x["attempt_id"] for x in (*originals.values(), *completed)}]
            assert sources
            for source in sources:
                if source["attempt_id"] in {item["attempt_id"] for item in originals.values()}:
                    assert source == dict(host.store.get_attempt(source["attempt_id"]))
                    continue
                projected += 1
                assert "observation_json" not in source, "source requests recursively contain all prior round inputs"
                assert "input_artifacts" not in source
                original = json.loads(host.store.get_attempt(source["attempt_id"])["observation_json"])
                assert source["typed_candidate"] == original["response"]["typed_candidate"]
                assert source["role"] == original["request"]["role"]
            sizes.setdefault(observed["request"]["role"], []).append(sum(len(item.content_bytes()) for item in inputs))
        assert projected == 4
        assert all(values[-1] < values[0] * 2 for values in sizes.values())
        # The judge's six canonical records remain exact for independent validation.
        judge(host, view, active, completed)
    finally:
        host.store.close()


def test_round_failure_releases_debate_without_judgment_or_fake_scientific_refutation(tmp_path):
    host, view, _, _ = setup(tmp_path)
    facts = host.store.list_facts("run-1")
    view.drive("run-1")
    view.drive("run-1")
    task = host.launched[-1][0]
    host.record(task, inputs=view.debate.inputs("run-1", task), terminal="FAILED")
    view.drive("run-1")
    assert not view.debate.pending("run-1")
    assert not any(event["action"] == "debate_verdict" for _, event in view._events("run-1"))
    assert host.store.list_facts("run-1") == facts
    host.store.close()


def test_finished_judge_is_retained_when_round_budget_prevents_new_science(tmp_path):
    host, view, _, _ = setup(tmp_path, rounds=1)
    view, active, binding, completed = debate_rounds(host, view)
    judge(host, view, active, completed)
    view.drive("run-1")
    planned = view.debate._event("run-1", active["debate_id"], "debate_restarts_designated")
    assert planned["plans"] == [] and len(planned["not_started"]) == 2
    assert not view.debate.pending("run-1")
    assert view.debate._event("run-1", active["debate_id"], "debate_verdict") is not None
    host.store.close()


def test_debate_cooldown_then_new_evidence_are_both_required(tmp_path):
    from datetime import datetime, timezone
    from hypoloom.interaction_host import InteractionHost
    host, view, _, _ = setup(tmp_path, rounds=1,
        interaction_options={"debate": {"min_interval_seconds": 1800, "min_new_outcomes": 1}})
    try:
        view, active, _, completed = debate_rounds(host, view)
        judge(host, view, active, completed)
        view.drive("run-1")
        assert not view.debate.pending("run-1") and not reasons(host)
        facts = host.store.list_facts("run-1")
        last = next(row for row, event in view._events("run-1") if event["action"] == "debate_restarts_finished")
        ended = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00")).timestamp()
        assert view._debate_deferred("run-1", facts, observed_at=ended + 1799)["reason"] == "cooldown"
        after = ended + 1801
        restored = InteractionHost(host)
        assert restored._debate_deferred("run-1", facts, observed_at=after)["reason"] == "insufficient_new_outcomes"
        newer = [*facts, {"fact_type": "CanonicalOutcomeAccepted", "fact_id": "new-unit-outcome",
            "created_at": datetime.fromtimestamp(after, timezone.utc).isoformat()}]
        assert restored._debate_deferred("run-1", newer, observed_at=after) is None
        assert restored._debate_deferred("run-1", newer, observed_at=after - 0.1)["reason"] == "insufficient_new_outcomes"
    finally:
        host.store.close()


def test_all_three_rounds_reuse_original_private_plugin_memory_view_despite_new_git_head(tmp_path):
    from hypoloom.expert_plugin import ExpertPluginStore
    from hypoloom.expert_memory import ExpertMemory
    from hypoloom.research_projection import read_role_view
    import yaml
    host, view, _, originals = setup(tmp_path, resources=True)
    original_resources = {}
    for role, source in originals.items():
        request = json.loads(source["observation_json"])["request"]
        selected = next(item for item in request["input_artifacts"] if item["ref"].endswith("--expert-resources"))
        original_resources[role] = EmbeddedInput.from_dict(selected).content_bytes()
        bound = json.loads(original_resources[role])
        assert all(not name.startswith("experts/") or name.startswith(f"experts/{role}/") for name in read_role_view(
            host.state_root, "run-1", role, bound["research_view_id"]))
        plugins = ExpertPluginStore(host.state_root)
        prepared = plugins.prepare_update("run-1", role, commit=bound["plugin"]["commit"], attempt_id=role + "-later")
        path = prepared.root / "skills/experiment-report/SKILL.md"
        path.write_bytes(path.read_bytes() + b"\nA later accepted plugin lesson.\n")
        assert plugins.finish_update(prepared, max_bytes=131072).commit != bound["plugin"]["commit"]
        facts = [row for row in host.store.list_facts("run-1", "HypothesisAssessed") if row["payload"]["component_id"] == request["component_id"]]
        header = {"assessments": [{"fact_id": row["fact_id"], "hypothesis_id": row["hypothesis_id"], "assessment": row["payload"]["assessment"]} for row in facts]}
        memory = ExpertMemory(host.state_root)
        committed = memory.commit_report("run-1", role, bound["memory_commit"], role + "-later-report",
            {"MEMORY.md": b"# A later accepted report\n", "rounds/1.md": ("---\n" + yaml.safe_dump(header) + "---\nReport cites the accepted facts.\n").encode()},
            cycle=1, facts=facts, max_bytes=131072)
        assert committed != bound["memory_commit"]
    view, active, binding, completed = debate_rounds(host, view)
    for row in completed:
        request = json.loads(row["observation_json"])["request"]
        selected = next(item for item in request["input_artifacts"] if item["ref"].endswith("--expert-resources"))
        assert EmbeddedInput.from_dict(selected).content_bytes() == original_resources[request["role"]]
    host.store.close()


def test_second_debate_inherits_actual_literature_through_accepted_hp_restart(tmp_path):
    host, view, _, _ = setup(tmp_path)
    view, active, binding, completed = debate_rounds(host, view)
    source = judge(host, view, active, completed)
    view.drive("run-1")
    previous = {plan["role"]: plan for plan in view.debate._event("run-1", active["debate_id"], "debate_restarts_designated")["plans"]}
    for role, component, hypothesis in (("researcher", "primary.score", "hyp-1"), ("critic", "must.robustness", "hyp-2")):
        old = next(row for row in host.store.list_facts("run-1", "HypothesisProposed") if row["hypothesis_id"] == hypothesis)
        with host.store.transaction() as tx:
            tx.append_fact({"fact_id": old["fact_id"] + "--cycle-2", "fact_type": "HypothesisProposed", "run_id": "run-1",
                "hypothesis_id": hypothesis + "--cycle-2", "subject_id": hypothesis + "--cycle-2", "schema_version": old["schema_version"], "goal_id": old["goal_id"],
                "payload": {**old["payload"], "fact_id": old["fact_id"] + "--cycle-2", "hypothesis_id": hypothesis + "--cycle-2", "parent_hypothesis_id": hypothesis}})
        hypothesis += "--cycle-2"
        host.control(Command.ACCEPT_HYPOTHESIS, {"component_id": component, "role": role,
            "node_command_id": role + "--syn-2", "agent_attempt_id": role + "--syn-2-a",
            "hypothesis_batch": {"cycle": 2, "chains": {"chain-a": hypothesis}}})
        host.control(Command.ACCEPT_ADJUDICATION, {"component_id": component, "role": role,
            "adjudication": {"cycle": 2, "batch_command_id": role + "--syn-2", "selected_tip": {"node_id": hypothesis, "chain_id": "chain-a"}}})
        task = node("RESULT_ANALYSIS", role, component, cycle=2, chain="chain-a")
        host.record(task, inputs=[EmbeddedInput.from_bytes(task.command_id + "--batch-context", "application/json",
            json.dumps({"chains": {"chain-a": hypothesis}}).encode())],
            extra_observation=session(str(UUID(int=21 if role == "critic" else 22))))
        host.finish_round(role, component, 2)
    view.drive("run-1")  # both first-Debate restarts have closed
    assert not view.debate.pending("run-1")
    # Consume any remaining first-cycle report Observer without a new action.
    for task, _ in view._triggers("run-1"):
        if task.cycle == 1 and not view._handled("run-1", task):
            host.record(task, candidate=_observer("none"), inputs=view.inputs("run-1", task))
            view.drive("run-1")
    task = next(task for task, _ in view._triggers("run-1") if task.cycle == 2 and not view._handled("run-1", task))
    proposal = _observer("debate")
    for participant in proposal["debate"]["participants"]:
        participant["hypothesis_id"] += "--cycle-2"
        participant["scope"]["chain"] = "chain-a"
    host.record(task, candidate=proposal, inputs=view.inputs("run-1", task))
    view.drive("run-1")
    view, second, _, second_rounds = debate_rounds(host, view)
    second_judge = judge(host, view, second, second_rounds)
    assert second_judge["attempt_id"] != source["attempt_id"]
    view.drive("run-1")
    event = view.debate._event("run-1", second["debate_id"], "debate_restarts_designated")
    for plan in event["plans"]:
        assert plan["cycle"] == 3 and plan["source_cycle"] == 2
        assert plan["source_literature_attempt_id"] == previous[plan["role"]]["source_literature_attempt_id"]
        assert plan["source_literature_command_id"] == previous[plan["role"]]["source_literature_command_id"]
    host.store.close()


def test_bounded_debate_has_exact_previous_own_and_peer_statements(tmp_path):
    host, view, _, _ = setup(tmp_path, agent_version=29, history_bytes=650_000)
    try:
        view, active, binding, rows = debate_rounds(host, view)
        for row in rows[2:]:
            request = json.loads(row["observation_json"])["request"]
            artifacts = {item["ref"]: EmbeddedInput.from_dict(item).content_bytes() for item in request["input_artifacts"]}
            context = json.loads(next(value for ref, value in artifacts.items() if ref.endswith("--debate-context")))
            number = context["round"]
            bounded = json.loads(next(value for ref, value in artifacts.items() if ref.endswith("--interaction-context")))
            assert bounded["topic"] == active["proposal"]["debate"]["topic"]
            assert "input_artifacts" not in bounded["original_statement"]
            for previous in rows[2 * (number - 2):2 * (number - 1)]:
                supplied = json.loads(artifacts[previous["attempt_id"]])
                assert supplied["typed_candidate"] == json.loads(previous["observation_json"])["response"]["typed_candidate"]
                assert "observation_json" not in supplied
                assert len(artifacts[previous["attempt_id"]]) < 3000
        judge(host, view, active, rows)
    finally:
        host.store.close()
