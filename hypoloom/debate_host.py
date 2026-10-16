"""Three real Debate rounds delegated to the sole Host's existing writer.

Attempts, accepted interaction controls and canonical session archives carry
recovery. No separate queue, science fact, pause state or transcript is created.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .agent_aci import EmbeddedInput, canonical_json
from .expert_batch import batch_limits
from .expert_interaction import (DebateBinding, DebateFork, DebateParticipant, InteractionFacts,
    bind_debate, debate_judgment_from_attempt, read_debate_rounds, select_interaction_facts, validate_original_debate_scope)
from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeKind, ResearchNodeSubkind
from .lifecycle import Command, RunState
from .participant_controls import ParticipantScope
from .profile_language import apply_profile_language


class DebateHost:
    def __init__(self, host, interaction):
        self.host, self.interaction, self.store = host, interaction, host.store

    def _events(self, run_id):
        return self.interaction._events(run_id)

    def _active(self, run_id):
        active = {}
        for _, event in self._events(run_id):
            if event["action"] == "debate_proposed":
                active[event["debate_id"]] = event
            elif event["action"] in {"debate_failed", "debate_restarts_finished", "debate_aborted"}:
                active.pop(event["debate_id"], None)
        if len(active) > 1:
            raise ValueError("multiple active debates overlap")
        return next(iter(active.values()), None)

    def _event(self, run_id, debate_id, action):
        return next((event for _, event in reversed(self._events(run_id))
            if event.get("debate_id") == debate_id and event["action"] == action), None)

    def _append(self, run_id, identity, event, *, pauses=(), release=False):
        command = Command.RESUME_PARTICIPANT if release else Command.ACCEPT_EXPERT_INTERACTION
        reason = "debate:" + event["debate_id"]
        payload = {"expert_interaction": event}
        if release:
            payload["participant_control"] = {"action": "resume", "scope": asdict(ParticipantScope()), "reason": reason}
        def operation(tx):
            head = tx.latest_transition(run_id)
            aborting = release and event.get("action") == "debate_aborted" and head is not None and head["state"] == "STOPPING"
            if head is None or head["state"] != "ACTIVE" and not aborting:
                raise ValueError("Debate control requires its ACTIVE run")
            transition = self.host._transition_record(tx, run_id, run_id, "RUN", command, RunState(head["state"]),
                command_id=identity, payload=payload)
            tx.append_transition(transition)
            for scope in pauses:
                tx.append_transition(self.host._transition_record(tx, run_id, run_id, "RUN", Command.PAUSE_PARTICIPANT, RunState.ACTIVE,
                    payload={"participant_control": {"action": "pause", "scope": asdict(scope), "reason": reason},
                        "interaction_source_attempt_id": event["source_attempt_id"], "debate_id": event["debate_id"]}))
            return {"status": "accepted", "revision": transition["revision"], "result": {"action": event["action"]}}
        self.store.execute_command({"command_id": identity, "run_id": run_id, "workflow_id": run_id,
            "expected_revision": None, "command_type": command.value, "request": payload}, operation)

    def _original_sources(self, run_id):
        rows = self.store.inspect("SELECT a.* FROM execution_attempts a JOIN workflow_commands c ON c.command_id=a.command_id "
            "WHERE a.run_id=? AND a.kind='AGENT' AND a.state='CLOSED' AND a.terminal_reason='SUCCEEDED' "
            "AND json_extract(c.request_json,'$.task_context.key.node_kind') IN "
            "('RESULT_ANALYSIS','EXPERIMENT_EXECUTION','EXPERIMENT_DESIGN_IMPLEMENT')", (run_id,))
        return [(row, json.loads(row["observation_json"])) for row in rows]

    def _original(self, run_id, participant, *, sources=None):
        candidates = []
        for row, observation in (self._original_sources(run_id) if sources is None else sources):
            request = observation["request"]
            if request.get("command") not in {"RESULT_ANALYSIS", "EXPERIMENT_EXECUTION", "EXPERIMENT_DESIGN_IMPLEMENT"} or "session" not in observation:
                continue
            try:
                validate_original_debate_scope(request, participant)
            except (KeyError, ValueError, TypeError):
                continue
            key = request["task_context"]["key"]
            rank = (key["experiment_ordinal"], {"EXPERIMENT_DESIGN_IMPLEMENT": 0, "EXPERIMENT_EXECUTION": 1, "RESULT_ANALYSIS": 2}[request["command"]], row["attempt_ordinal"])
            candidates.append((rank, row))
        return None if not candidates else max(candidates, key=lambda item: item[0])[1]

    def eligible_participants(self, run_id) -> list[dict[str, Any]]:
        result = []
        sources = self._original_sources(run_id)
        for component in self.host._research_contract(run_id).components:
            history = self.host._batch_history(run_id, str(component.component_id), component.owner_role)
            if history.latest is None:
                continue
            for chain, hypothesis in history.latest.members.items():
                participant = DebateParticipant(ParticipantScope(component.owner_role, str(component.component_id), chain), hypothesis)
                if self._original(run_id, participant, sources=sources) is not None:
                    result.append(asdict(participant))
        return result

    def accept_observer(self, run_id, task, row, proposal, context):
        if proposal.debate is None:
            raise ValueError("Debate requires its actual typed Observer proposal")
        permitted = context["debate_participants"]
        if any(asdict(item) not in permitted for item in proposal.debate.participants):
            raise ValueError("Debate selected a hypothesis/chain without a frozen source session")
        intents = [effect for effect in self.interaction._rows(run_id) if effect["command_id"] == task.command_id
            and effect["command_type"] == "REQUEST_AUXILIARY"]
        if len(intents) != 1:
            raise ValueError("Debate trigger lacks its actual Observer request revision")
        source = row["attempt_id"]
        response = json.loads(row["observation_json"])["response"]
        event = {**proposal.control_payload(source)["expert_interaction"], "debate_id": source,
            "source_revision": intents[0]["revision"], "node_command_id": task.command_id,
            "agent_output_digest": response["output_digest"], "source_report_ref": context["source_report_ref"]}
        self._append(run_id, source + "--interaction", event, pauses=tuple(item.scope for item in proposal.debate.participants))

    def _drained(self, run_id, participants):
        for row in self.store.list_attempts(run_id=run_id, state="OPEN"):
            if row["kind"] == "AGENT":
                request = json.loads(row["observation_json"])["request"]
                key = request.get("task_context", {}).get("key", {})
                scope = ParticipantScope(request["role"], request["component_id"], key.get("chain"))
            else:
                rows = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (row["experiment_id"],))
                if not rows:
                    raise ValueError("in-flight Debate participant Process lacks its Experiment")
                value = json.loads(rows[0]["payload_json"])
                scope = ParticipantScope(value["role"], value["component_id"], value.get("chain"))
            if any(item.scope.contains(scope) for item in participants):
                return False
        for head in self.store.list_workflow_heads(run_id, "EXPERIMENT"):
            if head["state"] == "TERMINAL":
                continue
            rows = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (head["workflow_id"],))
            value = json.loads(rows[0]["payload_json"])
            if any(item.scope.contains(ParticipantScope(value["role"], value["component_id"], value.get("chain"))) for item in participants):
                return False
        return True

    @staticmethod
    def _binding(run_id, event):
        return DebateBinding(event["debate_id"], run_id, tuple(DebateFork(
            DebateParticipant(ParticipantScope.from_dict(item["participant"]["scope"]), item["participant"]["hypothesis_id"]),
            item["original_attempt_id"], item["original_session_id"], item["session_id"]) for item in event["participants"]))

    def _round_task(self, active, fork, number):
        original = self.store.get_attempt(fork.original_attempt_id)
        cycle = json.loads(original["observation_json"])["request"]["task_context"]["key"]["cycle"]
        return AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE, run_id=original["run_id"], cycle=cycle,
            component_id=fork.participant.scope.component_id, expert_slot=fork.participant.scope.role,
            ordinal=active["source_revision"] * 3 + number)

    def _judge_task(self, run_id, active):
        observer = self.store.get_attempt(active["source_attempt_id"])
        cycle = json.loads(observer["observation_json"])["request"]["task_context"]["cycle"]
        return AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE_JUDGE, run_id=run_id, cycle=cycle, ordinal=active["source_revision"])

    def _rounds(self, run_id, active, binding):
        return [row for number in range(1, 4) for fork in binding.participants
            if (row := self.host._expert_success(self._round_task(active, fork, number))) is not None]

    @staticmethod
    def _source_input(row):
        # The canonical Attempt stays in SQLite. A statement needs its result
        # and identity, not the request containing every earlier embedded input.
        observed = json.loads(row["observation_json"])
        request = observed["request"]
        return EmbeddedInput.from_bytes(row["attempt_id"], "application/json", canonical_json({
            "attempt_id": row["attempt_id"], "command_id": row["command_id"],
            "role": request["role"], "command": request["command"],
            "task_context": request["task_context"],
            "typed_candidate": observed["response"]["typed_candidate"]}))

    def inputs(self, run_id: str, task: AuxiliaryTask) -> tuple[EmbeddedInput, ...]:
        active = self._active(run_id)
        started = None if active is None else self._event(run_id, active["debate_id"], "debate_started")
        if started is None:
            raise ValueError("Debate inputs require their accepted source/fork relationship")
        binding = self._binding(run_id, started)
        document = self.host._research_contract(run_id).to_dict()
        facts = [{name: row[name] for name in ("fact_id", "fact_type", "run_id", "payload_json")} for row in self.store.list_facts(run_id)]
        config = {"execution": dict(self.host._run_config(run_id))["execution"], "goal": document["goal"]}
        apply_profile_language(config, document, task)
        result = []
        if task.kind is AuxiliaryRequestKind.DEBATE_JUDGE:
            if task != self._judge_task(run_id, active):
                raise ValueError("judge task belongs to another Debate trigger")
            rounds = self._rounds(run_id, active, binding)
            statements = read_debate_rounds(rounds, binding=binding, facts=InteractionFacts.from_rows(run_id, facts, agent_aci_version=self.interaction._version(run_id)))
            facts = select_interaction_facts(facts,
                hypothesis_ids=[item.participant.hypothesis_id for item in binding.participants],
                reference_ids=[ref for item in statements for ref in item.statement.evidence])
            context = {"debate_id": binding.debate_id, "participants": [asdict(item) for item in binding.participants],
                "facts": facts, "rounds": [item.to_dict() for item in statements]}
            config["grounding_scope"] = "role-neutral"
            maximum = config.get("node_runtime", {}).get("memory", {}).get("max_plugin_bytes", 1024 * 1024)
            plugin = self.host._plugin_binding(run_id, task, "grounding", "Judge the exact three-round Debate records.", max_bytes=maximum, owner_kind="grounding")
            result.extend((EmbeddedInput.from_bytes(task.command_id + "--grounding-config", "application/json", canonical_json(config)),
                EmbeddedInput.from_bytes(task.command_id + "--grounding-resources", "application/json", canonical_json({"plugin": plugin.to_dict()})),
                EmbeddedInput.from_bytes(task.command_id + "--debate-judge-context", "application/json", canonical_json(context))))
        elif task.kind is AuxiliaryRequestKind.DEBATE:
            fork = next((item for item in binding.participants if item.participant.scope.role == task.expert_slot), None)
            number = task.ordinal - active["source_revision"] * 3
            if fork is None or number not in {1, 2, 3} or task != self._round_task(active, fork, number):
                raise ValueError("Debate task belongs to another participant or round")
            previous = None
            if number > 1:
                peer = next(item for item in binding.participants if item is not fork)
                previous = self.host._expert_success(self._round_task(active, peer, number - 1))
                if previous is None:
                    raise ValueError("Debate round lacks the completed preceding opponent")
                result.append(self._source_input(previous))
                if self.interaction._version(run_id) >= 29:
                    own_previous = self.host._expert_success(self._round_task(active, fork, number - 1))
                    if own_previous is None:
                        raise ValueError("Debate round lacks its completed preceding own statement")
                    result.append(self._source_input(own_previous))
            original = self.store.get_attempt(fork.original_attempt_id)
            original_request = json.loads(original["observation_json"])["request"]
            resources = [item for item in original_request["input_artifacts"] if item["ref"].endswith("--expert-resources")]
            if len(resources) == 1:
                selected = EmbeddedInput.from_dict(resources[0])
                result.append(EmbeddedInput.from_bytes(task.command_id + "--expert-resources", "application/json", selected.content_bytes()))
            elif original_request.get("required_adapter_version") != "fake-agent/4":
                raise ValueError("Debate original lacks its actual own-role plugin/memory/research resources")
            previous_refs = [] if previous is None else json.loads(previous["observation_json"])["response"]["typed_candidate"]["evidence"]
            if previous is not None and self.interaction._version(run_id) >= 29:
                previous_refs += json.loads(own_previous["observation_json"])["response"]["typed_candidate"]["evidence"]
            facts = select_interaction_facts(facts,
                hypothesis_ids=[item.participant.hypothesis_id for item in binding.participants], reference_ids=previous_refs)
            interaction_context = {"facts": facts}
            if self.interaction._version(run_id) >= 29:
                interaction_context.update(topic=active["proposal"]["debate"]["topic"],
                    original_statement=json.loads(self._source_input(original).content_bytes()))
            # Session transfer validates the exact original Attempt, including
            # its archived binding. This fixed source does not grow each round;
            # previous statements and the task view avoid recursive history.
            result.extend((EmbeddedInput.from_bytes(original["attempt_id"], "application/json", canonical_json(dict(original))),
                EmbeddedInput.from_bytes(binding.input_ref, "application/json", canonical_json(binding.round_context(fork.participant.scope.role, number,
                    None if previous is None else previous["attempt_id"]))),
                EmbeddedInput.from_bytes(task.command_id + "--interaction-context", "application/json", canonical_json(interaction_context)),
                EmbeddedInput.from_bytes(task.command_id + "--expert-config", "application/json", canonical_json(config))))
        else:
            raise ValueError("Debate delegate accepts only DEBATE or DEBATE_JUDGE")
        return tuple(result)

    def _step(self, run_id, active, task, role, component):
        source = self.host._expert_success(task)
        if source is not None:
            return source
        if self.host._expert_task_exhausted(run_id, task):
            self.host._record_expert_final_failure(run_id, component, role, task, "debate_exhausted")
            failure = self.host._expert_task_final_failure(run_id, task)
            self._append(run_id, active["debate_id"] + "--debate-failed", {"action": "debate_failed", "debate_id": active["debate_id"],
                "source_attempt_id": active["source_attempt_id"], "failed_task_command_id": task.command_id, "attempt_id": failure["attempt_id"]}, release=True)
        else:
            self.host._expert_agent_step(run_id, run_id, task, role, component)
        return None

    def literature_source(self, run_id, role, component, cycle, *, before_revision=None):
        """Read the real LR, following only already accepted HP-restart ancestry."""
        from .expert_loop import ResearchNodeKey, ResearchNodeTask
        direct = self.host._expert_success(ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component,
            expert_slot=role, cycle=cycle, node_kind=ResearchNodeKind.LITERATURE_REVIEW)))
        if direct is not None:
            return direct
        ancestors = [(row, plan) for row, event in self._events(run_id)
            if event["action"] == "debate_restarts_designated" and (before_revision is None or row["revision"] < before_revision)
            for plan in event["plans"] if (plan["role"], plan["component_id"], plan["cycle"]) == (role, component, cycle)]
        if len(ancestors) != 1:
            raise ValueError("Debate HP restart lacks its actual prior literature context")
        row, plan = ancestors[0]
        if cycle <= 1 or plan["source_cycle"] != cycle - 1 or plan["restart_from_hypothesis"] is not True:
            raise ValueError("Debate literature ancestry changed its accepted prior cycle")
        source = self.literature_source(run_id, role, component, cycle - 1, before_revision=row["revision"])
        if (source["attempt_id"], source["command_id"]) != (plan["source_literature_attempt_id"], plan["source_literature_command_id"]):
            raise ValueError("Debate literature ancestry changed its actual source")
        return source

    def _designations(self, run_id, debate_id):
        return [(row, event) for row, event in self._events(run_id)
            if event.get("debate_id") == debate_id and event["action"] == "debate_restarts_designated"]

    def _directed_restarts(self, run_id, active, verdict, binding):
        debate_id = active["debate_id"]
        designated = self._designations(run_id, debate_id)
        covered = {(item["role"], item["component_id"]) for _, event in designated
            for item in (*event["plans"], *event["not_started"])}
        revision = self.interaction._selection_revision(run_id, active)
        for fork in binding.participants:
            scope = fork.participant.scope
            if (scope.role, scope.component_id) in covered: continue
            cycle = self.interaction._admitted_cycle(run_id, asdict(scope), before_revision=revision)
            target = {"role": scope.role, "component_id": scope.component_id, "cycle": cycle}
            reason = None
            if self.interaction._round_failure(run_id, target) is not None:
                reason = "source_cycle_failed"
            elif not self.interaction._closed(run_id, scope.role, scope.component_id, cycle):
                continue
            elif cycle + 1 > batch_limits(self.host._research_contract(run_id).document, scope.role)["rounds"]:
                reason = "round_budget_exhausted"
            elif not self.host._batch_creation_available(run_id, scope.role):
                reason = "experiment_budget_exhausted"
            elif not self.host._agent_budget_available(run_id, scope.role):
                reason = "agent_budget_unavailable"
            plans, skipped = [], []
            if reason is None:
                literature = self.literature_source(run_id, scope.role, scope.component_id, cycle)
                plans.append({**target, "cycle": cycle + 1, "source_cycle": cycle,
                    "source_literature_attempt_id": literature["attempt_id"],
                    "source_literature_command_id": literature["command_id"],
                    "parent_hypothesis_id": fork.participant.hypothesis_id, "restart_from_hypothesis": True})
            else:
                skipped.append({"role": scope.role, "component_id": scope.component_id, "reason": reason})
            # Existing component ownership makes this identity unique per participant.
            self._append(run_id, debate_id + "--restarts--" + scope.component_id,
                {"action": "debate_restarts_designated", "debate_id": debate_id,
                 "source_attempt_id": verdict["source_attempt_id"], "plans": plans, "not_started": skipped})
        designated = self._designations(run_id, debate_id)
        plans = [item for _, event in designated for item in event["plans"]]
        covered = {(item["role"], item["component_id"]) for _, event in designated
            for item in (*event["plans"], *event["not_started"])}
        if len(covered) == len(binding.participants) and all(
                self.interaction._closed(run_id, plan["role"], plan["component_id"], plan["cycle"])
                or self.interaction._round_failure(run_id, plan) is not None for plan in plans):
            self._append(run_id, debate_id + "--restarts-finished", {"action": "debate_restarts_finished",
                "debate_id": debate_id, "source_attempt_id": verdict["source_attempt_id"], "plans": plans})

    def _restarts(self, run_id, active, verdict, binding):
        if self.interaction._version(run_id) >= 21:
            self._directed_restarts(run_id, active, verdict, binding)
            return
        if self._event(run_id, active["debate_id"], "debate_restarts_designated") is None:
            plans, not_started = [], []
            for fork in binding.participants:
                scope = fork.participant.scope
                history = self.host._batch_history(run_id, scope.component_id, scope.role)
                cycle = self.interaction._admitted_cycle(run_id, {"role": scope.role, "component_id": scope.component_id})
                if history.latest is None or history.latest.cycle != cycle or not self.interaction._closed(run_id, scope.role, scope.component_id, cycle):
                    return
                if cycle + 1 > batch_limits(self.host._research_contract(run_id).document, scope.role)["rounds"] or not self.host._batch_creation_available(run_id, scope.role) or history.selected_tip is None:
                    not_started.append({"role": scope.role, "component_id": scope.component_id, "reason": "budget_or_parent_unavailable"})
                    continue
                literature = self.literature_source(run_id, scope.role, scope.component_id, cycle)
                plans.append({"role": scope.role, "component_id": scope.component_id, "cycle": cycle + 1,
                    "source_cycle": cycle, "source_literature_attempt_id": literature["attempt_id"], "source_literature_command_id": literature["command_id"],
                    "parent_hypothesis_id": history.selected_tip["node_id"], "restart_from_hypothesis": True})
            self._append(run_id, active["debate_id"] + "--restarts", {"action": "debate_restarts_designated", "debate_id": active["debate_id"],
                "source_attempt_id": verdict["source_attempt_id"], "plans": plans, "not_started": not_started})
        plans = self._event(run_id, active["debate_id"], "debate_restarts_designated")["plans"]
        if all(self.interaction._closed(run_id, plan["role"], plan["component_id"], plan["cycle"])
                or self.interaction._round_failure(run_id, plan) is not None for plan in plans):
            self._append(run_id, active["debate_id"] + "--restarts-finished", {"action": "debate_restarts_finished", "debate_id": active["debate_id"],
                "source_attempt_id": verdict["source_attempt_id"], "plans": plans})

    def drive(self, run_id: str) -> None:
        active = self._active(run_id)
        if active is None:
            return
        started = self._event(run_id, active["debate_id"], "debate_started")
        facts = InteractionFacts.from_rows(run_id, self.store.list_facts(run_id), agent_aci_version=self.interaction._version(run_id))
        if started is None:
            from .expert_interaction import admit_observer_proposal
            proposal = admit_observer_proposal(active["proposal"], facts=facts).debate
            if not self._drained(run_id, proposal.participants):
                return
            originals = [self._original(run_id, participant) for participant in proposal.participants]
            if any(row is None for row in originals):
                raise ValueError("accepted Debate no longer has its original archived expert sources")
            binding = bind_debate(active["debate_id"], proposal, facts=facts, original_attempts=originals,
                fork_session_ids={item.scope.role: str(uuid5(NAMESPACE_URL, f"hypoloom-debate/{run_id}/{active['debate_id']}/{item.scope.role}")) for item in proposal.participants})
            self._append(run_id, active["debate_id"] + "--debate-started", {"action": "debate_started", "debate_id": active["debate_id"],
                "source_attempt_id": active["source_attempt_id"], "participants": [asdict(item) for item in binding.participants]})
            return
        binding = self._binding(run_id, started)
        verdict = self._event(run_id, active["debate_id"], "debate_verdict")
        if verdict is not None:
            self._restarts(run_id, active, verdict, binding)
            return
        for number in range(1, 4):
            for fork in binding.participants:
                task = self._round_task(active, fork, number)
                if self._step(run_id, active, task, fork.participant.scope.role, fork.participant.scope.component_id) is None:
                    return
        judge = self._judge_task(run_id, active)
        source = self._step(run_id, active, judge, "grounding", "grounding")
        if source is None:
            return
        judgment = debate_judgment_from_attempt(source, binding=binding, round_attempts=self._rounds(run_id, active, binding), facts=facts)
        response = json.loads(source["observation_json"])["response"]
        event = {**judgment.control_payload()["expert_interaction"], "parent_observer_attempt_id": active["source_attempt_id"],
            "node_command_id": judge.command_id, "agent_output_digest": response["output_digest"]}
        self._append(run_id, active["debate_id"] + "--verdict", event, release=True)

    def pending(self, run_id: str) -> bool:
        return self._active(run_id) is not None

    def designated_round(self, run_id, component_id):
        active = self._active(run_id)
        if active is None: return None
        return next((plan for _, event in self._designations(run_id, active["debate_id"])
            for plan in event["plans"] if plan["component_id"] == component_id), None)

    def blocks_next_cycle(self, run_id, component_id, cycle):
        active = self._active(run_id)
        if active is None:
            return False
        participant = next((item for item in active["proposal"]["debate"]["participants"] if item["scope"]["component_id"] == component_id), None)
        if participant is None:
            return False
        plan = self.designated_round(run_id, component_id)
        bound = self.interaction._admitted_cycle(run_id, participant["scope"], before_revision=
            self.interaction._selection_revision(run_id, active) if self.interaction._version(run_id) >= 21 else None) if plan is None else plan["cycle"]
        return cycle > bound

    def planning_inputs(self, run_id, task):
        key = task.key
        if self.interaction._version(run_id) < 21 or key.node_kind is not ResearchNodeKind.HYPOTHESIS_PROPOSAL:
            return ()
        for _, event in reversed(self._events(run_id)):
            if event["action"] != "debate_restarts_designated": continue
            if not any((p["role"], p["component_id"], p["cycle"]) ==
                    (key.expert_slot, key.component_id, key.cycle) for p in event["plans"]): continue
            verdict = self._event(run_id, event["debate_id"], "debate_verdict")
            observed = json.loads(self.store.get_attempt(verdict["source_attempt_id"])["observation_json"])
            judgment = observed["response"]["typed_candidate"]
            context = json.loads(next(EmbeddedInput.from_dict(item).content_bytes() for item in observed["request"]["input_artifacts"]
                if item["ref"].endswith("--debate-judge-context")))
            return (EmbeddedInput.from_bytes(task.command_id + "--debate-guidance", "application/json",
                canonical_json({"debate_id": event["debate_id"], "source_judge_attempt_id": verdict["source_attempt_id"],
                    "judgment": judgment, "facts": context["facts"], "instruction": "Address this public judgment with a discriminating experiment, controls and abandonment criteria. It is guidance, not completed experimental evidence."})),)
        return ()

    def promotion_inputs(self, run_id, task):
        key = task.key
        if not (key.node_kind in {ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeKind.EXPERIMENT_EXECUTION, ResearchNodeKind.RESULT_ANALYSIS}
                or key.node_kind is ResearchNodeKind.HYPOTHESIS_PROPOSAL and key.subkind is ResearchNodeSubkind.SYNTHESIS):
            return ()
        for _, event in reversed(self._events(run_id)):
            if event["action"] != "debate_restarts_designated":
                continue
            plan = next((item for item in event["plans"] if (item["role"], item["component_id"], item["cycle"])
                == (key.expert_slot, key.component_id, key.cycle)), None)
            if plan is None:
                continue
            verdict = self._event(run_id, event["debate_id"], "debate_verdict")
            if verdict["winner"] != key.expert_slot:
                return ()
            active = self._event(run_id, event["debate_id"], "debate_proposed")
            binding = self._binding(run_id, self._event(run_id, event["debate_id"], "debate_started"))
            fork = next(item for item in binding.participants if item.participant.scope.role == key.expert_slot)
            source = self.host._expert_success(self._round_task(active, fork, 3))
            context = {"debate_id": event["debate_id"], "source_judge_attempt_id": verdict["source_attempt_id"],
                "role": key.expert_slot, "component_id": key.component_id, "cycle": key.cycle,
                "original_session_id": fork.original_session_id,
                "session_id": json.loads(source["observation_json"])["session"]["session_id"], "source_attempt_id": source["attempt_id"]}
            return (EmbeddedInput.from_bytes(task.command_id + "--session-promotion", "application/json", canonical_json(context)),
                EmbeddedInput.from_bytes(source["attempt_id"], "application/json", canonical_json(dict(source))))
        return ()


__all__ = ["DebateHost"]
