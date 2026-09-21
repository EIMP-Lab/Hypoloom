"""Observer/Leader dispatch delegated to the existing sole Host and its store.

All durable state below is ordinary accepted control/Attempt data. Scientific
facts, participant reason folding, effect scheduling and Git remain unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime
from dataclasses import asdict
from typing import Any, Mapping

from .agent_aci import EmbeddedInput, canonical_json
from .expert_batch import batch_limits
from .expert_interaction import InteractionFacts, admit_observer_proposal, observer_from_attempt, select_interaction_facts
from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask
from .expert_memory import ExpertMemory
from .lifecycle import Command, RunState
from .participant_controls import ParticipantScope
from .profile_language import apply_profile_language


def cited_leader_component(candidate: Mapping[str, Any], *, facts: InteractionFacts) -> str:
    proposal = admit_observer_proposal(candidate, facts=facts)
    if proposal.leader is None:
        raise ValueError("proposal does not select a Leader")
    components = {str(item.component_id) for item in proposal.leader.contributions
        if set(item.qualification_refs).intersection(proposal.supporting_refs)}
    if len(components) != 1:
        raise ValueError("Leader must cite one exact qualified component")
    return components.pop()


class InteractionHost:
    def __init__(self, host):
        self.host = host
        self.store = host.store
        from .debate_host import DebateHost
        self.debate = DebateHost(host, self)

    def _rows(self, run_id):
        return self.store.inspect("SELECT t.*,c.command_type FROM workflow_transitions t JOIN workflow_commands c "
            "ON c.command_id=t.command_id WHERE t.run_id=? AND t.workflow_kind='RUN' ORDER BY t.revision", (run_id,))

    def _events(self, run_id):
        return [(row, value) for row in self._rows(run_id) if isinstance(
            value := json.loads(row["payload_json"]).get("expert_interaction"), Mapping)]

    def _settings(self, run_id):
        return self.host._research_contract(run_id).document.get("methodology", {}).get("controls", {}).get("product", {}).get("methodology", {}).get("interaction", {})

    def _enabled(self, run_id):
        run = self.store.get_run(run_id)
        return run is not None and int(run["agent_aci_version"]) >= 14 and any(
            self._settings(run_id).get(kind, {}).get("enabled", False) for kind in ("leader", "debate"))

    def _version(self, run_id):
        return int(self.store.get_run(run_id)["agent_aci_version"])

    def _facts(self, run_id, rows):
        return InteractionFacts.from_rows(run_id, rows, agent_aci_version=self._version(run_id))

    def directed_round(self, run_id, role, component_id, cycle):
        if self._version(run_id) < 21: return None
        from .expert_batch import directed_research_plan
        plan = directed_research_plan(self._rows(run_id), run_id, role, component_id, cycle)
        if plan is not None:
            self._facts(run_id, self.store.list_facts(run_id)).hypothesis(
                plan["parent_hypothesis_id"], ParticipantScope(role, component_id))
            if not self._closed(run_id, role, component_id, cycle - 1):
                raise ValueError("directed research precedes its admitted cycle closure")
        return plan

    def _eligible_contributions(self, run_id, facts, reports, minimum):
        result = []
        for item in self._facts(run_id, facts).leader_qualifications():
            history = self.host._batch_history(run_id, str(item.component_id), item.role)
            cycles = [batch.cycle for batch in history.batches if item.hypothesis_id in batch.members.values()]
            if (len(cycles) == 1 and cycles[0] >= minimum
                    and self._closed(run_id, item.role, item.component_id, cycles[0], reports=reports)
                    and history.latest.cycle < batch_limits(self.host._research_contract(run_id).document, item.role)["rounds"]
                    and self.host._batch_creation_available(run_id, item.role)):
                result.append(item)
        return result

    def _reports(self, run_id):
        result = []
        commands = [row["command_id"] for row in self._rows(run_id)
            if row["command_type"] == "REQUEST_RESEARCH_NODE" and
            json.loads(row["payload_json"]).get("task_context", {}).get("key", {}).get("node_kind") == "CYCLE_REPORT"]
        for row in (attempt for command in commands for attempt in self.store.list_attempts(
                command_id=command, run_id=run_id, kind="AGENT", state="CLOSED", terminal_reason="SUCCEEDED")):
            observation = json.loads(row["observation_json"])
            request = observation.get("request", {})
            if request.get("command") == "CYCLE_REPORT" and request.get("schema_version", 0) >= 14:
                result.append((row, request, observation))
        return result

    def _closed(self, run_id, role, component_id, cycle, *, reports=None):
        report = next((item for item in (self._reports(run_id) if reports is None else reports) if (item[1]["role"], item[1]["component_id"],
            item[1]["task_context"]["key"]["cycle"]) == (role, component_id, cycle)), None)
        if report is None:
            return False
        from .expert_loop import ResearchNodeKey
        evolve = ResearchNodeTask(key=ResearchNodeKey(run_id=run_id, component_id=component_id, expert_slot=role,
            cycle=cycle, node_kind=ResearchNodeKind.SELF_EVOLVE))
        return self.host._expert_success(evolve) is not None or self.host._expert_task_final_failure(run_id, evolve) is not None

    def _task(self, run_id, report):
        request = report[1]
        intents = [row for row in self._rows(run_id) if row["command_type"] == "REQUEST_RESEARCH_NODE" and row["command_id"] == request["command_id"]]
        if len(intents) != 1:
            raise ValueError("Observer trigger lacks its actual report request revision")
        return AuxiliaryTask(kind=AuxiliaryRequestKind.OBSERVER, run_id=run_id,
            cycle=request["task_context"]["key"]["cycle"], ordinal=intents[0]["revision"])

    def _handled(self, run_id, task):
        for _, event in self._events(run_id):
            if event.get("action") in {"observed", "leader_selected", "leader_not_started", "debate_proposed"} and event.get("node_command_id") == task.command_id:
                return True
        return self.host._expert_task_final_failure(run_id, task) is not None

    def _covered_report(self, run_id, report):
        """A successful accepted Observer may cover peer reports it saw closed."""
        if not self._settings(run_id).get("observer", {}).get("coalesce_reports", False):
            return False
        from .expert_loop import ResearchNodeKey
        from .agent_aci import EmbeddedInput
        request = report[1]
        evolve = self.host._expert_success(ResearchNodeTask(key=ResearchNodeKey(run_id=run_id,
            component_id=request["component_id"], expert_slot=request["role"],
            cycle=request["task_context"]["key"]["cycle"], node_kind=ResearchNodeKind.SELF_EVOLVE)))
        # A formally exhausted self-evolution still gets its own Observer;
        # don't infer when its separate final-failure decision became visible.
        if evolve is None:
            return False
        ref = report[0]["attempt_id"] + "--response"
        for _, event in self._events(run_id):
            if event["action"] not in {"observed", "leader_selected", "leader_not_started", "debate_proposed"}:
                continue
            source = self.store.get_attempt(event["source_attempt_id"])
            if source is None:
                continue
            started = min(item["started_at"] for item in self.store.list_attempts(command_id=source["command_id"]))
            if max(report[0]["ended_at"], evolve["ended_at"]) > started:
                continue
            observation = json.loads(source["observation_json"])
            inputs = observation["request"].get("input_artifacts", ())
            if any(item["ref"] == ref and json.loads(EmbeddedInput.from_dict(item).content_bytes()) == report[2]["response"] for item in inputs):
                return True
        return False

    def _triggers(self, run_id):
        ready = []
        reports = self._reports(run_id)
        for report in reports:
            request = report[1]
            if self._closed(run_id, request["role"], request["component_id"], request["task_context"]["key"]["cycle"], reports=reports):
                task = self._task(run_id, report)
                if not self._handled(run_id, task) and not self._covered_report(run_id, report):
                    ready.append((task, report))
        return sorted(ready, key=lambda item: item[0].ordinal)

    def _active(self, run_id):
        selected = {}
        for _, event in self._events(run_id):
            source = event.get("source_attempt_id")
            if event.get("action") == "leader_selected":
                selected[source] = event
            elif event.get("action") in {"leader_round_finished", "leader_not_started", "leader_aborted"}:
                selected.pop(source, None)
        if len(selected) > 1:
            raise ValueError("multiple accepted Leaders overlap")
        return next(iter(selected.values()), None)

    def _closing(self, run_id):
        product = self.host._research_contract(run_id).document.get("methodology", {}).get("controls", {}).get("product", {})
        if product.get("goal", {}).get("completion_mode") != "shared_goal":
            return False
        assessments = self.store.list_facts(run_id, "HypothesisAssessed")
        if not assessments:
            return False
        evidence = {row["outcome_id"]: row for row in self.store.list_facts(run_id, "EvidenceLinked")}
        outcomes = {row["experiment_id"]: row for row in self.store.list_facts(run_id, "CanonicalOutcomeAccepted")}
        # Local support can differ from common acceptance in either direction.
        return any(self.host.shared_acceptance.assessment_qualifies(run_id, row, evidence, outcomes)
            for row in assessments)

    def _debate_deferred(self, run_id, facts, *, observed_at):
        settings = self._settings(run_id).get("debate", {})
        interval, minimum = settings.get("min_interval_seconds", 0), settings.get("min_new_outcomes", 0)
        if not interval and not minimum:
            return None
        finished = [(row, event) for row, event in self._events(run_id)
            if event["action"] in {"debate_restarts_finished", "debate_failed", "debate_aborted"}]
        if not finished:
            return None
        row, event = finished[-1]
        ended = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).timestamp()
        if interval and observed_at < ended + interval:
            return {"reason": "cooldown", "debate_id": event["debate_id"], "available_after": ended + interval}
        added = {item["fact_id"] for item in facts if item["fact_type"] == "CanonicalOutcomeAccepted"
            and ended < datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).timestamp() <= observed_at} if minimum else set()
        if len(added) < minimum:
            return {"reason": "insufficient_new_outcomes", "debate_id": event["debate_id"],
                "new_outcomes": len(added), "required": minimum}
        return None

    def inputs(self, run_id: str, task: AuxiliaryTask) -> tuple[EmbeddedInput, ...]:
        if task.kind is not AuxiliaryRequestKind.OBSERVER or task.run_id != run_id or task.component_id is not None or task.expert_slot is not None:
            raise ValueError("interaction inputs require their role-neutral Observer task")
        reports = self._reports(run_id)
        source = next((item for item in reports if self._task(run_id, item) == task), None)
        if source is None or not self._closed(run_id, source[1]["role"], source[1]["component_id"], task.cycle):
            raise ValueError("Observer cannot precede its expert round closure")
        facts = self.store.list_facts(run_id)
        # Use the trigger's durable completion time so retries and delayed Host
        # admission cannot turn an old report into a new cooldown/evidence event.
        deferred = self._debate_deferred(run_id, facts, observed_at=source[0]["ended_at"])
        contract = self.host._research_contract(run_id).to_dict()
        grounding = self.host._expert_success(AuxiliaryTask(kind=AuxiliaryRequestKind.GROUNDING, run_id=run_id))
        if grounding is None:
            raise ValueError("Observer requires the actual completed Step-1 background")
        config = {"execution": dict(self.host._run_config(run_id)).get("execution", {}), "grounding_scope": "role-neutral",
            "components": contract["components"], "protocol_catalog": contract["protocols"], "goal": contract["goal"]}
        apply_profile_language(config, contract, task, observer_source_role=source[1]["role"])
        latest = {}
        for report in reports:
            key = (report[1]["role"], report[1]["component_id"])
            if key not in latest or report[1]["task_context"]["key"]["cycle"] > latest[key][1]["task_context"]["key"]["cycle"]:
                latest[key] = report
        included = {item[0]["attempt_id"]: item for item in (*latest.values(), source)}
        qualifications = self._facts(run_id, facts).leader_qualifications()
        hypotheses = {item.hypothesis_id for item in qualifications}
        for component in self.host._research_contract(run_id).components:
            history = self.host._batch_history(run_id, str(component.component_id), component.owner_role)
            if history.latest is not None:
                hypotheses.update(history.latest.members.values())
        cited = [ref for report in included.values() for ref in report[2]["response"]["typed_candidate"].get("supporting_refs", ())]
        facts = select_interaction_facts(facts, hypothesis_ids=hypotheses, reference_ids=cited)
        minimum = self._settings(run_id).get("leader", {}).get("min_rounds_before_leader", 1)
        eligible = sorted({str(item.component_id) for item in self._facts(run_id, facts).leader_qualifications()
            if any(report[1]["role"] == item.role and report[1]["component_id"] == item.component_id
                and report[1]["task_context"]["key"]["cycle"] >= minimum
                and self._closed(run_id, item.role, item.component_id, report[1]["task_context"]["key"]["cycle"])
                for report in reports)})
        eligible_contributions = self._eligible_contributions(run_id, facts, reports, minimum) if self._version(run_id) >= 21 else []
        if self._version(run_id) >= 21:
            eligible = sorted({str(item.component_id) for item in eligible_contributions})
        allowed = ["none"]
        if eligible and self._settings(run_id).get("leader", {}).get("enabled", False) and not self._closing(run_id):
            allowed.append("leader")
        debating = self.debate.eligible_participants(run_id) if self._settings(run_id).get("debate", {}).get("enabled", False) else []
        if len({item["scope"]["role"] for item in debating}) >= 2 and not self._closing(run_id) and deferred is None:
            allowed.append("debate")
        context = {"facts": [{name: row[name] for name in ("fact_id", "fact_type", "run_id", "payload_json")} for row in facts],
            "source_report_ref": source[0]["attempt_id"] + "--response", "allowed_actions": allowed,
            "eligible_leader_components": eligible, "debate_participants": debating}
        if self._version(run_id) >= 21:
            context["eligible_leader_hypotheses"] = sorted(item.hypothesis_id for item in eligible_contributions)
        if deferred is not None:
            context["debate_deferred"] = deferred
        maximum = config.get("node_runtime", {}).get("memory", {}).get("max_plugin_bytes", 1024 * 1024)
        plugin = self.host._plugin_binding(run_id, task, "grounding", "Observe actual expert records; select qualified interactions.", max_bytes=maximum, owner_kind="grounding")
        result = [EmbeddedInput.from_bytes(task.command_id + "--grounding-config", "application/json", canonical_json(config)),
            EmbeddedInput.from_bytes(task.command_id + "--interaction-context", "application/json", canonical_json(context)),
            EmbeddedInput.from_bytes(task.command_id + "--grounding-resources", "application/json", canonical_json({"plugin": plugin.to_dict()}))]
        observation = json.loads(grounding["observation_json"])
        result.append(EmbeddedInput.from_bytes(grounding["attempt_id"] + "--response", "application/json", canonical_json(observation["response"])))
        for identity, report in included.items():
            result.append(EmbeddedInput.from_bytes(identity + "--response", "application/json", canonical_json(report[2]["response"])))
        return tuple(result)

    def _append(self, run_id, command_id, event, *, pauses=(), resume=False):
        payload = {"expert_interaction": dict(event)}
        command = Command.RESUME_PARTICIPANT if resume else Command.ACCEPT_EXPERT_INTERACTION
        reason = "leader:" + event["source_attempt_id"]
        if resume:
            payload["participant_control"] = {"action": "resume", "scope": asdict(ParticipantScope()), "reason": reason}
        def operation(tx):
            head = tx.latest_transition(run_id)
            aborting = resume and event.get("action") == "leader_aborted" and head is not None and head["state"] == "STOPPING"
            if head is None or head["state"] != "ACTIVE" and not aborting:
                raise ValueError("interaction controls require an ACTIVE run")
            record = self.host._transition_record(tx, run_id, run_id, "RUN", command, RunState(head["state"]), command_id=command_id, payload=payload)
            tx.append_transition(record)
            for scope in pauses:
                control = {"action": "pause", "scope": asdict(scope), "reason": reason}
                tx.append_transition(self.host._transition_record(tx, run_id, run_id, "RUN", Command.PAUSE_PARTICIPANT, RunState.ACTIVE,
                    payload={"participant_control": control, "interaction_source_attempt_id": event["source_attempt_id"]}))
            return {"status": "accepted", "revision": record["revision"], "result": {"action": event["action"]}}
        self.store.execute_command({"command_id": command_id, "run_id": run_id, "workflow_id": run_id,
            "expected_revision": None, "command_type": command.value, "request": payload}, operation)

    def _accept(self, run_id, task, row):
        observation = json.loads(row["observation_json"])
        request, response = observation["request"], observation["response"]
        facts = self._facts(run_id, self.store.list_facts(run_id))
        # Fake adapters are explicit test transports, not evidence of CC sessions.
        if request.get("required_adapter_version") == "fake-agent/4":
            proposal = admit_observer_proposal(response["typed_candidate"], facts=facts)
        else:
            proposal = observer_from_attempt(row, facts=facts)
        from .agent_aci import EmbeddedInput
        context = json.loads(next(EmbeddedInput.from_dict(item).content_bytes() for item in request["input_artifacts"] if item["ref"].endswith("--interaction-context")))
        if proposal.action not in context["allowed_actions"]:
            raise ValueError("Observer action is disabled in its actual request")
        admit_observer_proposal(proposal.to_dict(), facts=self._facts(run_id, context["facts"]))
        if proposal.debate is not None:
            self.debate.accept_observer(run_id, task, row, proposal, context)
            return
        source = row["attempt_id"]
        event = {**proposal.control_payload(source)["expert_interaction"], "node_command_id": task.command_id,
            "agent_output_digest": response["output_digest"], "source_report_ref": context["source_report_ref"]}
        pauses = []
        if proposal.leader is not None:
            component_id = cited_leader_component(proposal.to_dict(), facts=facts)
            if component_id not in context["eligible_leader_components"]:
                raise ValueError("Leader component has not reached its admitted minimum round")
            if self._version(run_id) >= 21 and proposal.leader.contributions[0].hypothesis_id not in context["eligible_leader_hypotheses"]:
                raise ValueError("Leader contribution cycle has not closed or lacks research budget")
            history = self.host._batch_history(run_id, component_id, proposal.leader.role)
            if history.latest is None:
                raise ValueError("qualified Leader has no accepted component batch")
            event["leader"] = {"role": proposal.leader.role, "component_id": component_id, "after_cycle": history.latest.cycle}
            pauses = [ParticipantScope(item.owner_role, str(item.component_id)) for item in facts.contract.components
                if (item.owner_role, str(item.component_id)) != (proposal.leader.role, component_id)]
        self._append(run_id, source + "--interaction", event, pauses=pauses)

    def _drained(self, run_id, leader):
        peers = {(item.owner_role, str(item.component_id)) for item in self.host._research_contract(run_id).components
            if (item.owner_role, str(item.component_id)) != (leader["role"], leader["component_id"])}
        for row in self.store.list_attempts(run_id=run_id, state="OPEN"):
            if row["kind"] == "AGENT":
                request = json.loads(row["observation_json"])["request"]
                if (request["role"], request["component_id"]) in peers:
                    return False
            elif row.get("experiment_id"):
                creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (row["experiment_id"],))
                if creation and (lambda value: (value.get("role"), value.get("component_id")) in peers)(json.loads(creation[0]["payload_json"])):
                    return False
        for row in self.store.list_workflow_heads(run_id, "EXPERIMENT"):
            if row["state"] == "TERMINAL":
                continue
            creation = self.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND revision=1", (row["workflow_id"],))
            if creation and (lambda value: (value.get("role"), value.get("component_id")) in peers)(json.loads(creation[0]["payload_json"])):
                return False
        return True

    def leader_round(self, run_id: str, component_id: str) -> Mapping[str, Any] | None:
        active = self._active(run_id)
        if active is None or active["leader"]["component_id"] != component_id:
            return None
        return next((event["leader"] for _, event in reversed(self._events(run_id)) if event.get("action") == "leader_round_started"
            and event["source_attempt_id"] == active["source_attempt_id"]), None)

    def _admitted_cycle(self, run_id, leader, *, before_revision=None):
        cycles = [0]
        # Request intents are small, durable and already carry the admitted key.
        for row in self._rows(run_id):
            if before_revision is not None and row["revision"] >= before_revision: continue
            if row["command_type"] != "REQUEST_RESEARCH_NODE": continue
            key = json.loads(row["payload_json"]).get("task_context", {}).get("key", {})
            if (key.get("expert_slot"), key.get("component_id")) == (leader["role"], leader["component_id"]):
                cycles.append(key["cycle"])
        return max(cycles)

    def _selection_revision(self, run_id, active):
        return next(row["revision"] for row, event in self._events(run_id)
            if event["action"] in {"leader_selected", "debate_proposed"}
            and event["source_attempt_id"] == active["source_attempt_id"])

    def _round_failure(self, run_id, leader):
        for failure in self.host._expert_final_failures(run_id):
            attempt = self.store.get_attempt(failure["attempt_id"])
            request = json.loads(attempt["observation_json"])["request"]
            key = request.get("task_context", {}).get("key", {})
            if ((request["role"], request["component_id"], key.get("cycle")) ==
                    (leader["role"], leader["component_id"], leader["cycle"])
                    and key.get("chain") is None and key.get("node_kind") in {
                        "LITERATURE_REVIEW", "HYPOTHESIS_PROPOSAL", "ADJUDICATE", "HYPOTHESIS_UPDATE", "CYCLE_REPORT"}):
                return failure
        return None

    def _advance(self, run_id, active):
        leader, source = active["leader"], active["source_attempt_id"]
        started = self.leader_round(run_id, leader["component_id"])
        if started is not None:
            failure = self._round_failure(run_id, started)
            if self._closed(run_id, started["role"], started["component_id"], started["cycle"]) or failure is not None:
                terminal = {"terminal_reason": "ROUND_CLOSED"} if failure is None else {
                    "terminal_reason": "AGENT_FINAL_FAILURE", "failed_task_command_id": failure["failed_task_command_id"], "attempt_id": failure["attempt_id"]}
                self._append(run_id, source + "--leader-finished", {"action": "leader_round_finished", "source_attempt_id": source,
                    "leader": started, **terminal}, resume=True)
            return
        history = self.host._batch_history(run_id, leader["component_id"], leader["role"])
        latest = history.latest
        admitted = self._admitted_cycle(run_id, leader, before_revision=self._selection_revision(run_id, active) if self._version(run_id) >= 21 else None)
        if self._version(run_id) >= 21 and self._round_failure(run_id, {**leader, "cycle": admitted}) is not None:
            self._append(run_id, source + "--leader-not-started", {"action": "leader_not_started", "source_attempt_id": source,
                "leader": leader, "reason": "source_cycle_failed"}, resume=True)
            return
        if latest is None or latest.cycle < admitted or not self._closed(run_id, leader["role"], leader["component_id"], latest.cycle) or not self._drained(run_id, leader):
            return
        cycle = latest.cycle + 1
        parent = None if history.selected_tip is None else history.selected_tip["node_id"]
        if self._version(run_id) >= 21:
            selected = admit_observer_proposal(active["proposal"], facts=self._facts(run_id, self.store.list_facts(run_id)))
            parent = selected.leader.contributions[0].hypothesis_id
        unavailable = ("round_budget_exhausted" if cycle > batch_limits(self.host._research_contract(run_id).document, leader["role"])["rounds"]
            else "experiment_budget_exhausted" if not self.host._batch_creation_available(run_id, leader["role"])
            else "no_selected_parent" if parent is None
            else "agent_budget_unavailable" if self._version(run_id) >= 21 and not self.host._agent_budget_available(run_id, leader["role"]) else None)
        if unavailable is not None:
            self._append(run_id, source + "--leader-not-started", {"action": "leader_not_started", "source_attempt_id": source,
                "leader": leader, "reason": unavailable}, resume=True)
            return
        memory = ExpertMemory(self.host.state_root)
        peers = {role: memory.ensure(run_id, role) for role in self.host._research_contract(run_id).role_ids if role != leader["role"]}
        designated = {"role": leader["role"], "component_id": leader["component_id"], "cycle": cycle,
            "parent_hypothesis_id": parent, "peer_memory_commits": peers}
        self._append(run_id, source + "--leader-started", {"action": "leader_round_started", "source_attempt_id": source, "leader": designated})

    def drive(self, run_id: str) -> None:
        if not self._enabled(run_id) or self._closing(run_id):
            return
        active = self._active(run_id)
        if active is not None:
            self._advance(run_id, active)
            return
        if self.debate.pending(run_id):
            self.debate.drive(run_id)
            return
        for task, _ in self._triggers(run_id):
            source = self.host._expert_success(task)
            if source is not None:
                self._accept(run_id, task, source)
            elif self.host._expert_task_exhausted(run_id, task):
                self.host._record_expert_final_failure(run_id, "grounding", "grounding", task, "observer_exhausted")
            else:
                self.host._expert_agent_step(run_id, run_id, task, "grounding", "grounding")
            return

    def stop_for_goal(self, run_id: str, verdict_id: str) -> None:
        leader = self._active(run_id)
        if leader is not None:
            self._append(run_id, leader["source_attempt_id"] + "--leader-aborted",
                {"action": "leader_aborted", "source_attempt_id": leader["source_attempt_id"],
                 "leader": leader["leader"], "goal_verdict_id": verdict_id}, resume=True)
        debate = self.debate._active(run_id)
        if debate is not None:
            self.debate._append(run_id, debate["debate_id"] + "--debate-aborted",
                {"action": "debate_aborted", "source_attempt_id": debate["source_attempt_id"],
                 "debate_id": debate["debate_id"], "goal_verdict_id": verdict_id}, release=True)

    def pending(self, run_id: str) -> bool:
        return self._enabled(run_id) and not self._closing(run_id) and (self._active(run_id) is not None or self.debate.pending(run_id) or bool(self._triggers(run_id)))

    def blocks_next_cycle(self, run_id: str, component_id: str, cycle: int) -> bool:
        if not self._enabled(run_id):
            return False
        if self.debate.blocks_next_cycle(run_id, component_id, cycle):
            return True
        restart = self.debate.designated_round(run_id, component_id)
        if restart is not None and restart["component_id"] == component_id and restart["cycle"] == cycle:
            return False  # This accepted round must drain before deferred Observers can run.
        active = self._active(run_id)
        if active is not None and active["leader"]["component_id"] == component_id:
            started = self.leader_round(run_id, component_id)
            return cycle > (self._admitted_cycle(run_id, active["leader"], before_revision=self._selection_revision(run_id, active) if self._version(run_id) >= 21 else None) if started is None else started["cycle"])
        return any(report[1]["component_id"] == component_id and task.cycle < cycle for task, report in self._triggers(run_id))

    def priority_roles(self, run_id: str) -> tuple[str, ...]:
        active = self._active(run_id)
        return () if active is None else (active["leader"]["role"],)

    def leader_inputs(self, run_id: str, task: ResearchNodeTask) -> tuple[EmbeddedInput, ...]:
        key = task.key
        if key.node_kind is not ResearchNodeKind.HYPOTHESIS_PROPOSAL or key.subkind is not ResearchNodeSubkind.SYNTHESIS:
            return ()
        designated = self.leader_round(run_id, key.component_id)
        if designated is None or (designated["role"], designated["component_id"], designated["cycle"]) != (key.expert_slot, key.component_id, key.cycle):
            return ()
        active = self._active(run_id)
        resources = {"source_attempt_id": active["source_attempt_id"], **{name: designated[name]
            for name in ("role", "component_id", "cycle", "peer_memory_commits")}}
        return (EmbeddedInput.from_bytes(task.command_id + "--leader-resources", "application/json", canonical_json(resources)),)


__all__ = ["InteractionHost"]
