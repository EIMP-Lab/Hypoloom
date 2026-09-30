"""Independent raw-row observations of Observer and source-bound Leader controls.

Qualification and control audits do not call the interaction producer. Only
reference ID interpretation is shared; Host and state writers are not imported.
Existing raw facts, ordinary transitions/Attempts and readable Git commits are
the evidence; this module cannot accept a scientific or control effect.
"""
from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .batch_oracle import _debate_literature_source, _input, _need, _payload, _time, shared_goal_stop
from .expert_context_oracle import _commit_files, _component, _git
from .expert_interaction import expand_fact_references

InteractionIssue = tuple[str, str, tuple[str, ...]]


class _InteractionAudit:
    def __init__(self, root, attempts, observations, facts, payloads, transitions, workflow_version=None):
        self.root = Path(root)
        self.attempts = {str(row["attempt_id"]): dict(row) for row in attempts}
        self.observations = observations
        self.facts = payloads
        self.fact_rows = facts
        self.transitions = [(dict(row), _payload(row)) for row in transitions]
        self.events = [(row, value["expert_interaction"]) for row, value in self.transitions if "expert_interaction" in value]
        goals = [payloads[str(row["fact_id"])] for row in facts if row["fact_type"] == "GoalDefined"]
        _need(len(goals) == 1, "interaction requires its one accepted shared goal")
        self.run_id, self.goal_id = _component(goals[0]["run_id"]), goals[0]["goal_id"]
        self.contract = goals[0]["research_contract"]
        self.components = {item["id"]: item for item in self.contract["components"]}
        self.roles = {item["id"] for item in self.contract["roles"]}
        self.product = self.contract.get("methodology", {}).get("controls", {}).get("product", {})
        self.settings = self.product.get("methodology", {}).get("interaction", {})
        self.enabled = any(self.settings.get(kind, {}).get("enabled", False) for kind in ("leader", "debate"))
        self.success_stop = shared_goal_stop(facts, payloads, transitions, workflow_version)

    def aborted(self, row, event):
        _need(self.success_stop is not None, "interaction abortion lacks its accepted successful shared stop")
        stop, verdict = self.success_stop
        _need(event["goal_verdict_id"] == verdict["verdict_id"] and row["revision"] > stop["revision"]
              and row["state"] == row["previous_state"] == "STOPPING", "interaction abortion changes its goal verdict or stop ordering")

    def source(self, identity, command, *, closed=True):
        row = self.attempts[identity]
        observation = self.observations[identity]
        request = observation["request"]
        _need(row["kind"] == "AGENT" and row["run_id"] == request["run_id"] == self.run_id
              and row["command_id"] == request["command_id"] and request["attempt_id"] == identity
              and request["command"] == command and request["schema_version"] >= 14,
              "interaction source differs from its actual Attempt/run/command")
        if closed:
            _need(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED", "interaction source is not successful and closed")
        return row, request, observation

    def completed(self, role, component, cycle, command, before=float("inf")):
        return [(row, self.observations[identity]) for identity, row in self.attempts.items()
            if row["kind"] == "AGENT" and row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
            and row["ended_at"] <= before
            and (lambda req: (req.get("role"), req.get("component_id"), req.get("command"), req.get("task_context", {}).get("key", {}).get("cycle"))
                == (role, component, command, cycle))(self.observations[identity].get("request", {}))]

    def final_failure(self, command_id, before=float("inf")):
        matches = [(row, value) for row, value in self.transitions if value.get("agent_final_failure") is True
            and value.get("failed_task_command_id") == command_id and _time(row["created_at"]) <= before]
        if not matches:
            return None
        _need(len(matches) == 1, "interaction node has duplicate final failures")
        _, value = matches[0]
        source = self.attempts[value["attempt_id"]]
        _need(source["command_id"] == command_id and source["state"] == "CLOSED" and source["terminal_reason"] != "SUCCEEDED",
              "interaction final failure does not bind a real failed Attempt")
        return value

    def closed_round(self, role, component, cycle, before=float("inf")):
        reports = self.completed(role, component, cycle, "CYCLE_REPORT", before)
        evolves = self.completed(role, component, cycle, "SELF_EVOLVE", before)
        failed = any(self.final_failure(request["command_id"], before) is not None for request in
            (value.get("request", {}) for value in self.observations.values())
            if (request.get("role"), request.get("component_id"), request.get("command"), request.get("task_context", {}).get("key", {}).get("cycle"))
                == (role, component, "SELF_EVOLVE", cycle))
        return bool(reports and (evolves or failed))

    def qualifications(self, facts, version=14):
        by_kind = {}
        for fact in facts:
            by_kind.setdefault(fact["fact_type"], []).append(fact)
        hypotheses = {row["hypothesis_id"]: row for row in by_kind.get("HypothesisProposed", ())}
        specs = {row["experiment_id"]: row for row in by_kind.get("ExperimentPreregistered", ())}
        outcomes = {row["outcome_id"]: row for row in by_kind.get("CanonicalOutcomeAccepted", ())}
        evidence = {row["evidence_id"]: row for row in by_kind.get("EvidenceLinked", ())}
        result = {}
        for hyp in hypotheses.values():
            component = self.components[hyp["component_id"]]
            _need(hyp["goal_id"] == self.goal_id and hyp["proposer_role"] == component["owner"], "qualified hypothesis has another goal/owner")
            refs = set()
            own = {identity for identity, out in outcomes.items() if out["experiment_id"] in specs
                and specs[out["experiment_id"]]["hypothesis_id"] == hyp["hypothesis_id"]
                and out["component_id"] == hyp["component_id"] and out["method_family_id"] == hyp["method_family_id"]}
            if version < 21: refs.update(identity for identity in own if outcomes[identity]["outcome"] == "SUCCESS")
            for assessment in by_kind.get("HypothesisAssessed", ()):
                if assessment["hypothesis_id"] != hyp["hypothesis_id"] or assessment["assessment"] != "SUPPORTED":
                    continue
                links = [evidence.get(identity) for identity in assessment["evidence_ids"]]
                _need(links and all(link and link["hypothesis_id"] == hyp["hypothesis_id"] and link["outcome_id"] in own
                    and outcomes[link["outcome_id"]]["outcome"] in {"SUCCESS", "CLEAN_REFUTATION"}
                    and link["artifact_id"] == outcomes[link["outcome_id"]]["artifact_id"] for link in links),
                    "SUPPORTED Leader qualification lacks its accepted comparable evidence")
                refs.add(assessment["assessment_id"])
            if version >= 21:
                for link in evidence.values():
                    outcome = outcomes.get(link.get("outcome_id"), {})
                    if (link.get("outcome_id") not in own or outcome.get("outcome") not in {"SUCCESS", "CLEAN_REFUTATION"}
                            or not outcome.get("artifact_id") or link.get("artifact_id") != outcome["artifact_id"]
                            or any(link.get(field) != hyp[field] for field in ("hypothesis_id", "component_id", "method_family_id"))):
                        continue
                    for assessment in by_kind.get("HypothesisAssessed", ()):
                        if (assessment.get("assessment") == ("SUPPORTED" if outcome["outcome"] == "SUCCESS" else "REFUTED") and link["evidence_id"] in assessment.get("evidence_ids", ())
                                and all(assessment.get(field) == hyp[field] for field in ("hypothesis_id", "component_id", "method_family_id"))):
                            refs.update((link["outcome_id"], link["evidence_id"], assessment["assessment_id"]))
            if refs:
                result.setdefault((component["owner"], component["id"]), set()).update(refs)
        return result

    def observer(self, identity):
        row, request, observation = self.source(identity, "OBSERVER", closed=False)
        task = request["task_context"]
        _need(self.enabled and request["role"] == request["component_id"] == "grounding"
              and task["kind"] == "AUXILIARY" and task["request_kind"] == "OBSERVER" and task["run_id"] == self.run_id
              and task.get("component_id") is None and task.get("expert_slot") is None, "Observer is not enabled role-neutral Grounding")
        started = min(item["started_at"] for item in self.attempts.values() if item["command_id"] == request["command_id"])
        context = _input(request, "--interaction-context")
        frozen = []
        for item in context["facts"]:
            fact = json.loads(item["payload_json"]) if "payload_json" in item else item.get("payload", item)
            _need(self.facts.get(fact["fact_id"]) == fact and fact["run_id"] == self.run_id
                and all(item[name] == fact[name] for name in ("fact_id", "fact_type", "run_id")), "Observer frozen fact is not its actual accepted record")
            frozen.append(fact)
        _need(len({item["fact_id"] for item in frozen}) == len(frozen), "Observer duplicates a frozen fact")
        ref = context["source_report_ref"]
        _need(isinstance(ref, str) and ref.endswith("--response"), "Observer trigger lacks its actual report reference")
        report_id = ref.removesuffix("--response")
        report, report_request, report_observation = self.source(report_id, "CYCLE_REPORT")
        key = report_request["task_context"]["key"]
        triggers = [effect for effect, value in self.transitions if effect["command_type"] == "REQUEST_RESEARCH_NODE"
            and effect["workflow_id"] == self.run_id and effect["revision"] == task["ordinal"]
            and effect["command_id"] == report_request["command_id"] and value.get("task_context") == report_request["task_context"]]
        _need(len(triggers) == 1 and task["cycle"] == key["cycle"] and report["ended_at"] <= started
              and self.closed_round(report_request["role"], report_request["component_id"], key["cycle"], started)
              and _input(request, ref) == report_observation["response"], "Observer preceded or changed its actual closed expert round")
        grounds = [(item, self.observations[item["attempt_id"]]) for item in self.attempts.values() if item["kind"] == "AGENT"
            and self.observations[item["attempt_id"]].get("request", {}).get("command") == "GROUNDING"
            and item["state"] == "CLOSED" and item["terminal_reason"] == "SUCCEEDED" and item["ended_at"] <= started]
        # Clarification rounds need not be Observer inputs. Select its actual
        # background reference before strictly decoding and checking admission.
        refs = {item["ref"] for item in request.get("input_artifacts", ())}
        grounds = [(item, value) for item, value in grounds
            if item["attempt_id"] + "--response" in refs
            and value.get("response", {}).get("typed_candidate", {}).get("clarification") is None]
        _need(len(grounds) == 1, "Observer lacks one actual Step-1 background")
        ground, background = grounds[0]
        _need(_input(request, ground["attempt_id"] + "--response") == background["response"], "Observer changed actual Step-1 background")
        qualifications = self.qualifications(frozen, request["schema_version"])
        minimum = self.settings.get("leader", {}).get("min_rounds_before_leader", 1)
        eligible = sorted({component for role, component in qualifications if any(
            req.get("command") == "CYCLE_REPORT" and req.get("role") == role and req.get("component_id") == component
            and req["task_context"]["key"]["cycle"] >= minimum
            and self.closed_round(role, component, req["task_context"]["key"]["cycle"], started)
            for req in (value.get("request", {}) for value in self.observations.values()))})
        eligible_hypotheses = []
        if request["schema_version"] >= 21:
            from .agent_budget import profile_role_cap
            cap = self.product.get("goal", {}).get("stop_conditions", {}).get("max_rounds_per_expert", self.contract["budget"]["max_experiments"])
            creations = [value for effect, value in self.transitions if effect["workflow_kind"] == "EXPERIMENT"
                and effect["revision"] == 1 and _time(effect["created_at"]) <= started]
            for hyp in (item for item in frozen if item["fact_type"] == "HypothesisProposed"):
                own_facts = [item for item in frozen if item["fact_type"] != "HypothesisProposed" or item == hyp]
                if not self.qualifications(own_facts, request["schema_version"]): continue
                role, component = hyp["proposer_role"], hyp["component_id"]
                batches = [value["hypothesis_batch"] for effect, value in self.transitions
                    if value.get("role") == role and value.get("component_id") == component
                    and "hypothesis_batch" in value and _time(effect["created_at"]) <= started]
                cycles = [batch["cycle"] for batch in batches if hyp["hypothesis_id"] in batch["chains"].values()]
                role_cap = profile_role_cap(self.contract, role, "max_experiments")
                if (len(cycles) == 1 and cycles[0] >= minimum and self.closed_round(role, component, cycles[0], started)
                        and (request["schema_version"] >= 23 or (
                            max(batch["cycle"] for batch in batches) < cap
                            and len(creations) < self.contract["budget"]["max_experiments"]
                            and (role_cap is None or sum(value.get("role") == role for value in creations) < role_cap)))):
                    eligible_hypotheses.append(hyp["hypothesis_id"])
            eligible = sorted({item["component_id"] for item in frozen if item.get("hypothesis_id") in eligible_hypotheses})
            _need(context["eligible_leader_hypotheses"] == sorted(eligible_hypotheses),
                "Observer contribution eligibility differs from its own closed cycle and budget")
        _need(context["eligible_leader_components"] == eligible, "Observer minimum-round eligible components differ from actual closed records")
        closing = (self.product.get("goal", {}).get("completion_mode") == "shared_goal"
            and any(item["fact_type"] == "HypothesisAssessed" and item["assessment"] == "SUPPORTED" for item in frozen)
            and any(event["action"] in {"leader_round_finished", "debate_restarts_finished"} and _time(effect["created_at"]) <= started for effect, event in self.events))
        expected = ["none", "leader"] if eligible and self.settings.get("leader", {}).get("enabled", False) and not closing else ["none"]
        if request["schema_version"] >= 23 and "leader" in expected:
            prior = [(effect, event) for effect, event in self.events if event["action"] == "leader_selected"
                and _time(effect["created_at"]) <= started]
            if prior:
                previous = self.observations[prior[-1][1]["source_attempt_id"]]["request"]
                previous_context = _input(previous, "--interaction-context")
                report_refs = {item["ref"] for item in request["input_artifacts"] if item["ref"].endswith("--response")}
                if ({fact["fact_id"] for fact in frozen} <= {fact["fact_id"] for fact in previous_context["facts"]}
                        and report_refs <= {item["ref"] for item in previous["input_artifacts"]}):
                    expected.remove("leader")
        permitted = context.get("debate_participants", [])
        _need(isinstance(permitted, list), "Observer Debate participants are not an array")
        for participant in permitted:
            scope = participant["scope"]
            _need(self.components[scope["component_id"]]["owner"] == scope["role"]
                  and any(item["fact_type"] == "HypothesisProposed" and item["hypothesis_id"] == participant["hypothesis_id"]
                    and item["component_id"] == scope["component_id"] and item["proposer_role"] == scope["role"] for item in frozen),
                  "Observer Debate scope does not own its accepted hypothesis")
            _need(any(self.original_matches(value.get("request", {}), participant) and item["state"] == "CLOSED"
                and item["terminal_reason"] == "SUCCEEDED" and item["ended_at"] <= started and "session" in value
                for other, item in self.attempts.items() for value in [self.observations[other]] if item["kind"] == "AGENT"),
                "Observer Debate participant has no actual closed original session")
        if len({item["scope"]["role"] for item in permitted}) >= 2 and self.settings.get("debate", {}).get("enabled", False) and not closing:
            settings = self.settings.get("debate", {})
            prior = [(effect, event) for effect, event in self.events
                if event["action"] in {"debate_restarts_finished", "debate_failed", "debate_aborted"}
                and _time(effect["created_at"]) <= started]
            available = True
            if prior:
                effect, event = prior[-1]
                interval = settings.get("min_interval_seconds", 0)
                available = not interval or report["ended_at"] >= _time(effect["created_at"]) + interval
                minimum = settings.get("min_new_outcomes", 0)
                if minimum:
                    new = {item["fact_id"] for item in self.fact_rows if item["fact_type"] == "CanonicalOutcomeAccepted"
                        and _time(effect["created_at"]) < _time(item["created_at"]) <= report["ended_at"]}
                    available = available and len(new) >= minimum
            if available:
                expected.append("debate")
        _need(context["allowed_actions"] == expected, "Observer enabled/closing actions differ from its actual controls")
        resources = _input(request, "--grounding-resources")
        plugin = resources["plugin"]
        _need(set(resources) == {"plugin"} and (plugin["run_id"], plugin["role"], plugin["owner_kind"]) == (self.run_id, "grounding", "grounding"), "Observer plugin namespace differs")
        files = _commit_files(self.root / self.run_id / "grounding" / "plugin", plugin["commit"], allow_executable=True)
        _need(json.loads(files[".claude-plugin/plugin.json"])["name"] == plugin["name"] == "hypoloom-grounding"
              and files["skills/leader-trigger/SKILL.md"].strip(), "Observer Grounding plugin manifest/trigger is missing")
        if request.get("required_adapter_version") != "fake-agent/4":
            session, binding = observation.get("session", {}), observation.get("session_binding", {})
            _need(binding.get("parent_session_id") is None, "Observer admitted a resumed/forked prior conversation")
            if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
                _need(session.get("parent_session_id") is None and session.get("session_id") == binding.get("session_id")
                    and session.get("launch_arguments") == ["--session-id", binding.get("session_id")], "Observer did not start its fresh admitted session")
        effects = [(effect, event) for effect, event in self.events if event.get("node_command_id") == request["command_id"]]
        if row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED":
            _need(not any(event["source_attempt_id"] == identity for _, event in effects), "failed/open Observer became an accepted interaction")
            return
        _need(len(effects) == 1, "successful Observer lacks exactly one accepted interaction")
        effect, event = effects[0]
        response = observation["response"]
        proposal = response["typed_candidate"]
        domain = {value[field] for value in frozen for field in ("fact_id", "goal_id", "hypothesis_id", "experiment_id", "outcome_id", "artifact_id", "evidence_id", "assessment_id") if value.get(field)}
        _need(proposal["action"] in expected and set(proposal["supporting_refs"]) <= domain
              and event["source_attempt_id"] == identity and event["proposal"] == proposal
              and event["agent_output_digest"] == response["output_digest"] and event["source_report_ref"] == ref
              and effect["command_type"] == "ACCEPT_EXPERT_INTERACTION", "accepted interaction differs from its actual Observer output/refs")
        if proposal["action"] == "leader":
            cited = expand_fact_references(frozen, proposal["supporting_refs"])
            components = {component for (role, component), refs in qualifications.items() if role == proposal["leader"] and refs.intersection(cited)}
            if request["schema_version"] >= 21:
                components = {item["component_id"] for item in frozen if item["fact_type"] == "HypothesisProposed"
                    and item["proposer_role"] == proposal["leader"] and item["hypothesis_id"] in cited}
            _need(len(components) == 1 and components <= set(eligible) and event["action"] == "leader_selected"
                  and event["leader"]["role"] == proposal["leader"] and event["leader"]["component_id"] in components,
                  "Leader does not select its one exact qualified component")
            if request["schema_version"] >= 21:
                parents = [item for item in frozen if item["fact_type"] == "HypothesisProposed"
                    and item["proposer_role"] == proposal["leader"] and item["hypothesis_id"] in cited]
                _need(len(parents) == 1 and parents[0]["hypothesis_id"] in eligible_hypotheses,
                    "Leader must select exactly one eligible own contribution parent")
                refs = self.qualifications([item for item in frozen if item["fact_type"] != "HypothesisProposed" or item == parents[0]], request["schema_version"])
                _need(any(value.intersection(cited) for value in refs.values()),
                    "Leader parent lacks its own cited qualification")
        elif proposal["action"] == "debate":
            participants = proposal["debate"]["participants"]
            _need(event["action"] == "debate_proposed" and len(participants) == 2
                  and len({item["scope"]["role"] for item in participants}) == 2
                  and all(item in permitted for item in participants), "Debate proposal changes its frozen eligible source scopes")
        else:
            _need(event["action"] == "observed" and proposal["leader"] is None and proposal["debate"] is None, "none Observer manufactured an interaction")

    @staticmethod
    def original_matches(request, participant):
        scope = participant["scope"]
        key = request.get("task_context", {}).get("key", {})
        if (request.get("role"), request.get("component_id"), key.get("chain")) != (scope["role"], scope["component_id"], scope["chain"]):
            return False
        if request.get("schema_version", 0) < 14:
            return participant["hypothesis_id"] in request.get("immutable_context_refs", ())
        inputs = request.get("input_artifacts", ())
        if any(item["ref"].endswith("--batch-context") for item in inputs):
            if _input(request, "--batch-context").get("chains", {}).get(scope["chain"]) == participant["hypothesis_id"]:
                return True
        for item in inputs:
            if item.get("media_type") != "application/json":
                continue
            value = _input(request, item["ref"])
            value = value.get("payload", value)
            if value.get("fact_type") == "HypothesisProposed" and (value.get("hypothesis_id"), value.get("component_id"), value.get("proposer_role")) == (
                    participant["hypothesis_id"], scope["component_id"], scope["role"]):
                return True
        return False

    def leader(self, source):
        if self.observations[source]["request"]["schema_version"] >= 23:
            return self.leader_guidance(source)
        events = [(row, event) for row, event in self.events if event.get("source_attempt_id") == source]
        selected = [(row, event) for row, event in events if event["action"] == "leader_selected"]
        _need(len(selected) == 1, "Leader has no unique accepted Observer parent")
        selection_row, selection = selected[0]
        leader = selection["leader"]
        expected_peers = {(item["owner"], item["id"]) for item in self.components.values()} - {(leader["role"], leader["component_id"])}
        pauses = [(row, value["participant_control"]) for row, value in self.transitions if value.get("interaction_source_attempt_id") == source]
        _need(len(pauses) == len(expected_peers) and {(control["scope"]["role"], control["scope"]["component_id"]) for _, control in pauses} == expected_peers
              and all(row["command_type"] == "PAUSE_PARTICIPANT" and row["revision"] > selection_row["revision"]
                and control["action"] == "pause" and control["reason"] == "leader:" + source and control["scope"]["chain"] is None for row, control in pauses),
              "Leader pause scopes/reason differ from its exact peers")
        starts = [(row, event) for row, event in events if event["action"] == "leader_round_started"]
        finishes = [(row, event) for row, event in events if event["action"] in {"leader_round_finished", "leader_not_started", "leader_aborted"}]
        _need(len(starts) <= 1 and len(finishes) <= 1, "Leader round was started or released more than once")
        if finishes:
            effect, event = finishes[0]
            control = _payload(effect)["participant_control"]
            _need(effect["command_type"] == "RESUME_PARTICIPANT" and effect["revision"] > selection_row["revision"]
                  and control == {"action": "resume", "scope": {"role": None, "component_id": None, "chain": None}, "reason": "leader:" + source},
                  "Leader release removes another participant reason or has no accepted parent")
            if event["action"] == "leader_aborted":
                self.aborted(effect, event)
                _need(event["leader"] == leader, "Leader abortion changes its original selected participant")
            elif event["action"] == "leader_round_finished":
                _need(len(starts) == 1 and effect["revision"] > starts[0][0]["revision"], "Leader finished without its designated round")
                plan = starts[0][1]["leader"]
                _need(event["leader"] == plan, "Leader completion names another designated round")
                if event.get("terminal_reason") == "AGENT_FINAL_FAILURE":
                    failure = self.final_failure(event["failed_task_command_id"])
                    _need(failure is not None and failure["attempt_id"] == event["attempt_id"], "Leader release has a fabricated final failure")
                else:
                    _need(self.closed_round(plan["role"], plan["component_id"], plan["cycle"]), "Leader released peers before its designated round closed")
        if not starts:
            return
        effect, event = starts[0]
        plan = event["leader"]
        _need(effect["command_type"] == "ACCEPT_EXPERT_INTERACTION" and effect["revision"] > selection_row["revision"]
              and (plan["role"], plan["component_id"]) == (leader["role"], leader["component_id"]), "Leader round is not its Observer's selected component")
        prior = [(row, value) for row, value in self.transitions if row["revision"] < effect["revision"] and row["workflow_kind"] == "RUN"
            and value.get("component_id") == plan["component_id"] and value.get("role") == plan["role"] and "adjudication" in value]
        _need(prior, "Leader round has no actual accepted parent selection")
        _, parent = prior[-1]
        version = self.observations[source]["request"]["schema_version"]
        expected_parent = parent["adjudication"]["selected_tip"]["node_id"] if version < 21 else plan["parent_hypothesis_id"]
        if version >= 21:
            context = _input(self.observations[source]["request"], "--interaction-context")
            frozen = [json.loads(item["payload_json"]) if "payload_json" in item else item.get("payload", item)
                for item in context["facts"]]
            cited = expand_fact_references(frozen, selection["proposal"]["supporting_refs"])
            _need(expected_parent in cited
                  and plan["cycle"] == self.admitted_cycle(plan["role"], plan["component_id"], selection_row["revision"]) + 1
                  and self.closed_round(plan["role"], plan["component_id"], plan["cycle"] - 1, _time(effect["created_at"])),
                  "Leader bypassed its admitted source cycle or cited contribution")
        _need(plan["cycle"] == parent["adjudication"]["cycle"] + 1
              and plan["parent_hypothesis_id"] == expected_parent, "Leader next cycle changes its actual authorized parent")
        cap = self.product.get("goal", {}).get("stop_conditions", {}).get("max_rounds_per_expert", self.contract["budget"]["max_experiments"])
        _need(plan["cycle"] <= cap, "Leader overrode the admitted round budget")
        peers = plan["peer_memory_commits"]
        _need(set(peers) == self.roles - {plan["role"]}, "Leader memory includes itself or omits/adds a peer owner")
        for role, commit in peers.items():
            repository = self.root / self.run_id / "experts" / _component(role) / "memory"
            files = _commit_files(repository, commit)
            _need(files.get("MEMORY.md", b"").strip(), "Leader peer memory Git commit is unavailable/empty")
            _git(repository, "merge-base", "--is-ancestor", commit, "HEAD")
        for identity, observation in self.observations.items():
            request = observation.get("request", {})
            inputs = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith("--leader-resources")]
            key = request.get("task_context", {}).get("key", {})
            target = (request.get("role"), request.get("component_id"), key.get("cycle"), key.get("node_kind"), key.get("subkind")) == (
                plan["role"], plan["component_id"], plan["cycle"], "HYPOTHESIS_PROPOSAL", "SYNTHESIS")
            if not target and not inputs:
                continue
            resources = _input(request, "--leader-resources")
            if resources.get("source_attempt_id") != source:
                _need(not target, "Leader designated SYNTHESIS names another Observer")
                continue
            _need(target and resources == {"source_attempt_id": source, **{name: plan[name] for name in ("role", "component_id", "cycle", "peer_memory_commits")}}
                  and self.attempts[identity]["started_at"] >= _time(effect["created_at"]), "Leader peer memory escaped or changed its designated SYNTHESIS")
            session = observation.get("session")
            if session is not None:
                _need(session.get("leader_resources") == resources, "observed session mounted another Leader resource binding")

    def leader_guidance(self, source):
        from .expert_loop import AuxiliaryRequestKind, AuxiliaryTask
        events = [(row, event) for row, event in self.events if event.get("source_attempt_id") == source]
        selected = [(row, event) for row, event in events if event["action"] == "leader_selected"]
        _need(len(selected) == 1, "Leader guidance lacks one accepted selection")
        selection_row, selection = selected[0]
        leader = selection["leader"]
        task = AuxiliaryTask(kind=AuxiliaryRequestKind.LEADER, run_id=self.run_id,
            component_id=leader["component_id"], expert_slot=leader["role"],
            cycle=leader["after_cycle"], ordinal=selection_row["revision"])
        _need(not any(value.get("interaction_source_attempt_id") == source or
            value.get("participant_control", {}).get("reason") == "leader:" + source
            for _, value in self.transitions), "Leader advice paused or resumed participants")
        starts = [(row, event) for row, event in events if event["action"] == "leader_round_started"]
        finishes = [(row, event) for row, event in events if event["action"] in
            {"leader_round_finished", "leader_not_started", "leader_aborted"}]
        _need(len(starts) <= 1 and len(finishes) <= 1, "Leader advice has duplicate start/terminal effects")
        for effect, event in starts + finishes:
            expected_command = "CLEANUP_OBSERVED" if event["action"] == "leader_aborted" else "ACCEPT_EXPERT_INTERACTION"
            _need(effect["command_type"] == expected_command
                and effect["revision"] > selection_row["revision"] and event["leader"] == leader,
                "Leader advice changed its author, selection or scheduling authority")
        if starts:
            _need(starts[0][1]["task_command_id"] == task.command_id
                and set(leader) == {"role", "component_id", "after_cycle"}, "Leader advice designated an extra research round")
        original = self.observations[source]["request"]
        context = _input(original, "--interaction-context")
        bound = [(identity, row, self.observations[identity]) for identity, row in self.attempts.items()
            if row["kind"] == "AGENT" and row["command_id"] == task.command_id]
        first_inputs = None
        for identity, row, observed in sorted(bound, key=lambda item: item[1]["attempt_ordinal"]):
            _, request, _ = self.source(identity, "LEADER", closed=False)
            _need(starts and row["started_at"] >= _time(starts[0][0]["created_at"])
                and request["task_context"] == task.to_dict()
                and (request["role"], request["component_id"]) == (leader["role"], leader["component_id"]),
                "Leader advice task differs from selected author or precedes its start")
            _need(_input(request, "--leader-context") == {"source_observer_attempt_id": source,
                "leader": leader, "proposal": selection["proposal"], "facts": context["facts"]},
                "Leader advice changed the accepted selection/evidence")
            expected = {item["ref"]: item for item in original["input_artifacts"] if item["ref"].endswith("--response")}
            actual = {item["ref"]: item for item in request["input_artifacts"] if item["ref"].endswith("--response")}
            _need(actual == expected, "Leader advice changed actual source reports")
            immutable = [item for item in request["input_artifacts"] if item["ref"] in request["immutable_context_refs"]]
            if first_inputs is None: first_inputs = immutable
            _need(immutable == first_inputs, "Leader retry changed its original evidence")
            session = observed.get("session", {})
            _need(not session.get("leader_resources") and not session.get("parent_session_id"),
                "Leader advice acquired peer memories or resumed another task's session")
        if finishes:
            effect, event = finishes[0]
            if event["action"] == "leader_aborted":
                if event.get("reason") == "RUN_STOPPING":
                    _need(effect["state"] == effect["previous_state"] == "STOPPING"
                        and any(row["workflow_kind"] == "RUN" and row["state"] == "STOPPING"
                            and row["previous_state"] != "STOPPING" and row["revision"] < effect["revision"]
                            for row, _ in self.transitions), "Leader cancellation lacks the actual Run stop")
                    _need("goal_verdict_id" not in event, "operator cancellation invented a scientific verdict")
                else:
                    self.aborted(effect, event)
            elif event["action"] == "leader_round_finished":
                _need(starts and effect["revision"] > starts[0][0]["revision"]
                    and event["task_command_id"] == task.command_id, "Leader guidance finished without its task start")
                if event["terminal_reason"] == "GUIDANCE_ACCEPTED":
                    row, request, _ = self.source(event["guidance_attempt_id"], "LEADER")
                    _need(row["command_id"] == task.command_id and row["ended_at"] <= _time(effect["created_at"]),
                        "Leader guidance publication names another or unfinished task")
                else:
                    _need(event["terminal_reason"] == "AGENT_FINAL_FAILURE"
                        and event["failed_task_command_id"] == task.command_id, "Leader guidance has an unknown failure")
                    failure = self.final_failure(task.command_id, _time(effect["created_at"]))
                    _need(failure is not None and failure["attempt_id"] == event["attempt_id"],
                        "Leader guidance failure lacks its actual exhausted Attempt")
            else:
                _need(not bound and event["reason"] == "agent_budget_unavailable", "Leader not-started contradicts actual Attempts")

    def guidance_consumers(self):
        for identity, observed in self.observations.items():
            request = observed.get("request", {})
            if request.get("schema_version", 0) < 23: continue
            inputs = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith("--leader-guidance")]
            _need(not any(item["ref"].endswith("--leader-resources") for item in request.get("input_artifacts", ())),
                "new advice semantics mounted legacy peer memory")
            if request.get("command") == "LEADER":
                context = _input(request, "--leader-context")
                _need(any(event["action"] == "leader_round_started" and event.get("task_command_id") == request["command_id"]
                    and event["source_attempt_id"] == context["source_observer_attempt_id"] for _, event in self.events),
                    "orphan Leader guidance task")
            if request.get("command") != "HYPOTHESIS_PROPOSAL":
                _need(not inputs, "Leader advice escaped proposal scope")
                continue
            key = request["task_context"]["key"]
            forwards = [(row, value["request"]) for other, row in self.attempts.items()
                for value in [self.observations.get(other, {})]
                if value.get("request", {}).get("command") == "HYPOTHESIS_PROPOSAL"
                and value["request"]["task_context"]["key"] == {**key, "subkind": "FORWARD"}]
            _need(forwards, "Leader advice consumer has no actual first-wing request")
            first, first_request = min(forwards, key=lambda pair: pair[0]["started_at"])
            canonical = [item for item in first_request["input_artifacts"] if item["ref"].endswith("--leader-guidance")]
            _need(inputs == canonical and len(inputs) <= 1, "proposal wings/retries changed the original advice set")
            publications = [(effect, event) for effect, event in self.events
                if event.get("terminal_reason") == "GUIDANCE_ACCEPTED" and _time(effect["created_at"]) <= first["started_at"]]
            _need(bool(inputs) == bool(publications), "proposal omitted available advice or consumed unpublished advice")
            if not inputs: continue
            effect, event = publications[-1]
            row, _, source = self.source(event["guidance_attempt_id"], "LEADER")
            body = _input(request, "--leader-guidance")
            _need(inputs[0]["ref"] == row["attempt_id"] + "--leader-guidance"
                and body["source_attempt_id"] == row["attempt_id"]
                and body["source_observer_attempt_id"] == event["source_attempt_id"]
                and body["author"] == event["leader"]["role"] and body["published_at"] == effect["created_at"]
                and body["guidance"] == source["response"]["typed_candidate"]
                and body["facts"] == _input(source["request"], "--leader-context")["facts"],
                "proposal advice changed its latest accepted source, author or evidence")

    @staticmethod
    def debate_source_matches(supplied, source):
        observed = json.loads(source["observation_json"])
        if "observation_json" in supplied:
            return supplied["observation_json"] == source["observation_json"]
        request = observed["request"]
        return (set(supplied) == {"attempt_id", "command_id", "role", "command", "task_context", "typed_candidate"}
                and supplied["attempt_id"] == source["attempt_id"] and supplied["command_id"] == source["command_id"]
                and all(supplied[key] == request[key] for key in ("role", "command", "task_context"))
                and supplied["typed_candidate"] == observed["response"]["typed_candidate"])

    def debate(self, debate_id):
        events = [(row, event) for row, event in self.events if event.get("debate_id") == debate_id]
        def event(action, *, required=False):
            matches = [(row, value) for row, value in events if value["action"] == action]
            _need(len(matches) <= 1 and (matches or not required), "Debate has a missing/duplicate " + action)
            return matches[0] if matches else (None, None)
        proposed_row, proposed = event("debate_proposed", required=True)
        observer, observer_request, _ = self.source(proposed["source_attempt_id"], "OBSERVER")
        triggers = [row for row, _ in self.transitions if row["command_id"] == observer_request["command_id"] and row["command_type"] == "REQUEST_AUXILIARY"]
        _need(len(triggers) == 1 and proposed["source_revision"] == triggers[0]["revision"], "Debate logical trigger differs from its Observer request revision")
        participants = proposed["proposal"]["debate"]["participants"]
        scopes = [item["scope"] for item in participants]
        pauses = [(row, value["participant_control"]) for row, value in self.transitions if value.get("debate_id") == debate_id and "participant_control" in value]
        _need(len(pauses) == 2 and sorted((control["scope"] for _, control in pauses), key=str) == sorted(scopes, key=str)
              and all(row["command_type"] == "PAUSE_PARTICIPANT" and row["revision"] > proposed_row["revision"]
                  and control["action"] == "pause" and control["reason"] == "debate:" + debate_id for row, control in pauses),
              "Debate pause reasons/scopes are not its two exact participants")
        start_row, started = event("debate_started")
        failure_row, failure = event("debate_failed")
        verdict_row, verdict = event("debate_verdict")
        abort_row, aborted = event("debate_aborted")
        if aborted is not None:
            self.aborted(abort_row, aborted)
            _need(failure is None and aborted["source_attempt_id"] == observer["attempt_id"]
                  and event("debate_restarts_finished")[1] is None, "Debate abortion changes its source or aborts an already closed interaction")
            _need(abort_row["command_type"] == "RESUME_PARTICIPANT" and _payload(abort_row)["participant_control"] == {
                "action": "resume", "scope": {"role": None, "component_id": None, "chain": None}, "reason": "debate:" + debate_id},
                "Debate abortion released another pause reason")
            _need(verdict_row is None or verdict_row["revision"] < abort_row["revision"], "Debate was judged after its abortion")
        if failure is not None:
            _need(verdict is None and self.final_failure(failure["failed_task_command_id"]) is not None
                  and self.final_failure(failure["failed_task_command_id"])["attempt_id"] == failure["attempt_id"], "Debate failure manufactured a verdict or failed source")
        release_row = failure_row if failure is not None else verdict_row
        if release_row is not None:
            _need(release_row["command_type"] == "RESUME_PARTICIPANT" and _payload(release_row)["participant_control"] == {
                "action": "resume", "scope": {"role": None, "component_id": None, "chain": None}, "reason": "debate:" + debate_id},
                "Debate release removed another interaction/operator reason")
        if started is None:
            _need(verdict is None, "Debate was judged without original fork sources")
            return
        _need(start_row["revision"] > proposed_row["revision"] and started["source_attempt_id"] == observer["attempt_id"]
              and sorted((item["participant"] for item in started["participants"]), key=str) == sorted(participants, key=str),
              "Debate forks differ from the actual Observer selection")
        forks = {item["participant"]["scope"]["role"]: item for item in started["participants"]}
        _need(len(forks) == 2 and len({item["session_id"] for item in forks.values()}) == 2, "Debate duplicates participant forks")
        for role, fork in forks.items():
            source, request, value = self.source(fork["original_attempt_id"], self.observations[fork["original_attempt_id"]]["request"]["command"])
            _need(self.original_matches(request, fork["participant"]) and value["session"]["session_id"] == fork["original_session_id"]
                  and source["ended_at"] <= _time(start_row["created_at"]) + 0.001, "Debate original does not own the actual hypothesis/chain/session")
        rounds = {}
        judges = []
        for identity, value in self.observations.items():
            request = value.get("request", {})
            row = self.attempts[identity]
            if request.get("command") == "DEBATE_JUDGE" and any(item["ref"].endswith("--debate-judge-context") for item in request["input_artifacts"]):
                if _input(request, "--debate-judge-context")["debate_id"] == debate_id:
                    judges.append((row, request, value))
            if request.get("command") != "DEBATE":
                continue
            context = _input(request, "--debate-context")
            if context["debate_id"] != debate_id:
                continue
            number, role = context["round"], request["role"]
            _need(type(number) is int and number in {1, 2, 3} and role in forks, "Debate round is outside its actual two participants/three rounds")
            fork = forks[role]
            original = self.attempts[fork["original_attempt_id"]]
            original_request = self.observations[fork["original_attempt_id"]]["request"]
            task = request["task_context"]
            _need(task["request_kind"] == "DEBATE" and task["ordinal"] == proposed["source_revision"] * 3 + number
                  and task["cycle"] == original_request["task_context"]["key"]["cycle"]
                  and (task["expert_slot"], task["component_id"]) == (role, fork["participant"]["scope"]["component_id"])
                  and all(context[name] == fork[name] for name in ("participant", "original_attempt_id", "original_session_id", "session_id"))
                  and _input(request, fork["original_attempt_id"])["observation_json"] == original["observation_json"],
                  "Debate round changed its admitted original/cycle/fork source")
            if request["schema_version"] >= 29:
                brief = _input(request, "--interaction-context")
                _need(brief.get("topic") == proposed["proposal"]["debate"]["topic"]
                      and self.debate_source_matches(brief.get("original_statement"), original),
                      "bounded Debate changed its accepted topic or original statement")
            original_resources = [item for item in original_request["input_artifacts"] if item["ref"].endswith("--expert-resources")]
            if original_resources or original_request.get("required_adapter_version") != "fake-agent/4":
                _need(_input(request, "--expert-resources") == _input(original_request, "--expert-resources"),
                      "Debate replaced its original own-role plugin/memory/private-view binding")
                if value.get("session") is not None and request.get("required_adapter_version") != "fake-agent/4":
                    _need(value["session"].get("expert_resources") == _input(request, "--expert-resources"),
                          "Debate session did not mount its admitted original resources")
            if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
                from uuid import UUID, uuid5
                expected_session = str(uuid5(UUID(fork["session_id"]), identity)) if request["schema_version"] >= 29 else fork["session_id"]
                parent = None if request["schema_version"] >= 29 else fork["original_session_id"]
                _need((value["session"]["session_id"], value["session"]["parent_session_id"]) == (expected_session, parent),
                      "Debate changed its actual invocation session or original fork")
                _need((number, role) not in rounds, "Debate has duplicate successful outputs for one logical round")
                rounds[number, role] = (row, request, value)
        for (number, role), (row, request, _) in rounds.items():
            context = _input(request, "--debate-context")
            if number == 1:
                _need(context["previous_opponent_attempt_id"] is None, "first Debate round invented a preceding opponent")
            else:
                peer = next(item for item in forks if item != role)
                previous = rounds.get((number - 1, peer))
                _need(previous is not None and context["previous_opponent_attempt_id"] == previous[0]["attempt_id"]
                      and self.debate_source_matches(_input(request, previous[0]["attempt_id"]), previous[0])
                      and previous[0]["ended_at"] <= row["started_at"], "Debate omitted or changed its actual previous opponent round")
                if request["schema_version"] >= 29:
                    own = rounds.get((number - 1, role))
                    _need(own is not None and self.debate_source_matches(_input(request, own[0]["attempt_id"]), own[0])
                          and own[0]["ended_at"] <= row["started_at"], "Debate omitted or changed its preceding own statement")
        successful = [(row, request, value) for row, request, value in judges if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"]
        if verdict is None:
            _need(not successful, "successful Debate judge has no accepted verdict")
            return
        _need(len(rounds) == 6 and len(successful) == 1, "Debate verdict requires all six real participant outputs and one actual judge")
        judge, request, value = successful[0]
        self.source(judge["attempt_id"], "DEBATE_JUDGE")
        task = request["task_context"]
        _need(request["role"] == request["component_id"] == "grounding" and task["kind"] == "AUXILIARY"
              and task["request_kind"] == "DEBATE_JUDGE" and task["ordinal"] == proposed["source_revision"]
              and task["cycle"] == observer_request["task_context"]["cycle"]
              and task.get("expert_slot") is None and task.get("component_id") is None,
              "Debate judge belongs to another source trigger or Grounding scope")
        context = _input(request, "--debate-judge-context")
        _need(("rounds" in context) != ("round_attempts" in context), "judge has ambiguous round representations")
        if "rounds" in context:
            supplied = context["rounds"]
            expected = [{"number": number, "participant": forks[role]["participant"],
                "source_attempt_id": row["attempt_id"], "session_id": observed["session"]["session_id"],
                "statement": observed["response"]["typed_candidate"]}
                for (number, role), (row, _, observed) in rounds.items()]
            _need(sorted(supplied, key=str) == sorted(expected, key=str), "judge changed a canonical statement, source or fork")
        else:
            supplied = context["round_attempts"]
            _need(len(supplied) == 6 and {item["attempt_id"] for item in supplied} == {item[0]["attempt_id"] for item in rounds.values()}
                  and all(item["observation_json"] == self.attempts[item["attempt_id"]]["observation_json"] for item in supplied),
                  "judge changed canonical six-output records")
        _need(context["participants"] == started["participants"]
              and max(item[0]["ended_at"] for item in rounds.values()) <= judge["started_at"], "judge saw a partial/changed six-output record set")
        candidate = value["response"]["typed_candidate"]
        for name in ("winner", "scores", "keep", "prune", "rationale", "supporting_refs"):
            expected = candidate[name]
            _need(verdict[name] == expected or isinstance(expected, list) and sorted(verdict[name], key=str) == sorted(expected, key=str), "accepted Debate verdict differs from its actual judge output")
        _need(verdict["source_attempt_id"] == judge["attempt_id"] and verdict["node_command_id"] == request["command_id"]
              and verdict["agent_output_digest"] == value["response"]["output_digest"]
              and verdict["parent_observer_attempt_id"] == observer["attempt_id"], "Debate verdict lost its original Observer/judge causation")
        roles = set(forks)
        scores = {item["role"]: item for item in candidate["scores"]}
        _need(len(candidate["scores"]) == 2 and set(scores) == roles, "Debate judge score owners differ")
        for role, score in scores.items():
            _need(Decimal(score["score"]).is_finite() and set(score["round_refs"]) == {item[0]["attempt_id"] for (number, owner), item in rounds.items() if owner == role}, "judge score does not cite its three actual own outputs")
        keep, prune, winner = set(candidate["keep"]), set(candidate["prune"]), candidate["winner"]
        _need(not keep.intersection(prune) and keep | prune == roles and (winner is None or winner in keep)
              and (not prune or winner is not None) and (winner is None or all(Decimal(scores[winner]["score"]) >= Decimal(item["score"]) for item in scores.values())),
              "Debate keep/prune/winner contradicts its exact participants or declared scores")
        pruned = [{"loser_chain": forks[role]["participant"]["scope"]["chain"], "loser_hypothesis_id": forks[role]["participant"]["hypothesis_id"],
            "winner_chain": forks[winner]["participant"]["scope"]["chain"], "winner_hypothesis_id": forks[winner]["participant"]["hypothesis_id"],
            "source_attempt_id": judge["attempt_id"], "debate_id": debate_id, "score_refs": scores[role]["round_refs"]} for role in sorted(prune)]
        _need(verdict["pruned_by"] == pruned and {item[0]["attempt_id"] for item in rounds.values()} <= set(candidate["supporting_refs"]), "Debate pruning invented scientific/source relationships")
        self.debate_restarts(events, verdict, forks, rounds)

    def admitted_cycle(self, role, component, revision):
        keys = [value.get("task_context", {}).get("key", {}) for row, value in self.transitions
            if row["workflow_kind"] == "RUN" and row["command_type"] == "REQUEST_RESEARCH_NODE" and row["revision"] < revision]
        return max((key["cycle"] for key in keys if (key.get("expert_slot"), key.get("component_id")) == (role, component)), default=0)

    def debate_restarts(self, events, verdict, forks, rounds):
        selected = [(row, value) for row, value in events if value["action"] == "debate_restarts_designated"]
        version = self.observations[verdict["source_attempt_id"]]["request"]["schema_version"]
        _need(version >= 21 or len(selected) <= 1, "Debate designated its restarted scientific rounds twice")
        if not selected: return
        plans = [plan for _, designated in selected for plan in designated["plans"]]
        skipped = [item for _, designated in selected for item in designated["not_started"]]
        coverage = [item["role"] for item in (*plans, *skipped)]
        _need(all(designated["source_attempt_id"] == verdict["source_attempt_id"] for _, designated in selected)
              and len(coverage) == len(set(coverage)) and set(coverage) <= set(forks)
              and (version >= 21 or set(coverage) == set(forks)), "Debate restart changes or duplicates participants")
        proposed = next((row, event) for row, event in events if event["action"] == "debate_proposed")
        finishes = [(row, event) for row, event in events if event["action"] == "debate_restarts_finished"]
        if version >= 21 and finishes:
            _need(len(finishes) == 1 and set(coverage) == set(forks) and finishes[0][1]["plans"] == plans
                  and all(row["revision"] < finishes[0][0]["revision"] for row, _ in selected),
                  "Debate finished before participant coverage or changed its accepted plans")
            for plan in plans:
                failed = any(self.final_failure(req["command_id"], _time(finishes[0][0]["created_at"])) is not None
                    for req in (value.get("request", {}) for value in self.observations.values())
                    if (req.get("role"), req.get("component_id"), req.get("task_context", {}).get("key", {}).get("cycle")) ==
                        (plan["role"], plan["component_id"], plan["cycle"]))
                _need(self.closed_round(plan["role"], plan["component_id"], plan["cycle"], _time(finishes[0][0]["created_at"])) or failed,
                    "Debate finished before actual directed research closure")
        for plan in plans:
            effect = next(row for row, event in selected if plan in event["plans"])
            role, component, cycle = plan["role"], plan["component_id"], plan["cycle"]
            _need(component == forks[role]["participant"]["scope"]["component_id"] and plan["restart_from_hypothesis"] is True,
                  "Debate restart switched component or did not restart at hypothesis proposal")
            parent = [(row, value["adjudication"]) for row, value in self.transitions if row["workflow_kind"] == "RUN"
                and row["revision"] < effect["revision"] and value.get("component_id") == component and value.get("role") == role and "adjudication" in value]
            expected_parent = (forks[role]["participant"]["hypothesis_id"] if version >= 21
                else parent[-1][1]["selected_tip"]["node_id"])
            _need(parent and plan["source_cycle"] == parent[-1][1]["cycle"] == cycle - 1
                  and plan["parent_hypothesis_id"] == expected_parent
                  and self.closed_round(role, component, cycle - 1, _time(effect["created_at"])),
                  "Debate restarted without its authorized parent and closed source cycle")
            if version >= 21:
                _need(cycle == self.admitted_cycle(role, component, proposed[0]["revision"]) + 1,
                    "Debate target drifted past its admitted participant cycle")
            _debate_literature_source(plan, effect, self.transitions, self.attempts, self.observations, self.run_id)
            cap = self.product.get("goal", {}).get("stop_conditions", {}).get("max_rounds_per_expert", self.contract["budget"]["max_experiments"])
            _need(cycle <= cap, "Debate restart bypassed the admitted round budget")
            for identity, value in self.observations.items():
                request = value.get("request", {})
                key = request.get("task_context", {}).get("key", {})
                target = (request.get("role"), request.get("component_id"), key.get("cycle")) == (role, component, cycle)
                inner = key.get("node_kind") in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS"}
                synthesis = key.get("node_kind") == "HYPOTHESIS_PROPOSAL" and key.get("subkind") == "SYNTHESIS"
                inputs = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith("--session-promotion")]
                if not target:
                    if inputs:
                        _need(_input(request, "--session-promotion").get("source_judge_attempt_id") != verdict["source_attempt_id"]
                              or (request.get("role"), request.get("component_id"), key.get("cycle")) in {
                                  (item["role"], item["component_id"], item["cycle"]) for item in plans},
                              "Debate promotion escaped its designated cycle/participant")
                    continue
                if role != verdict["winner"] or not (inner or synthesis):
                    _need(not inputs, "losing or unrelated node received the winning Debate session")
                    continue
                source = rounds[3, role][0]
                expected = {"debate_id": verdict["debate_id"], "source_judge_attempt_id": verdict["source_attempt_id"],
                    "role": role, "component_id": component, "cycle": cycle, "original_session_id": forks[role]["original_session_id"],
                    "session_id": self.observations[source["attempt_id"]]["session"]["session_id"], "source_attempt_id": source["attempt_id"]}
                _need(_input(request, "--session-promotion") == expected
                      and _input(request, source["attempt_id"])["observation_json"] == source["observation_json"],
                      "winning SYNTHESIS/inner session has no exact accepted judge/round-three authorization")


def audit_interactions(state_root: Path, attempts: Sequence[Mapping[str, Any]], attempt_payloads: Mapping[str, Mapping[str, Any]],
                       facts: Sequence[Mapping[str, Any]], fact_payloads: Mapping[str, Mapping[str, Any]],
                       transitions: Sequence[Mapping[str, Any]], *, workflow_version=None) -> list[InteractionIssue]:
    """Read ACI14 Observer/Leader ownership without invoking its producer."""
    ids = [str(row["attempt_id"]) for row in attempts if row["kind"] == "AGENT"
        and attempt_payloads.get(str(row["attempt_id"]), {}).get("request", {}).get("command") == "OBSERVER"]
    events = [(_payload(row).get("expert_interaction")) for row in transitions if _payload(row).get("expert_interaction")]
    if not ids and not events:
        return []
    try:
        audit = _InteractionAudit(state_root, attempts, attempt_payloads, facts, fact_payloads, transitions, workflow_version)
    except (KeyError, TypeError, ValueError) as exc:
        return [("interaction_source_invalid", str(exc), ())]
    issues = []
    for identity in set(ids) | {event.get("source_attempt_id") for event in events if event["action"] in {"observed", "leader_selected", "leader_round_started", "leader_round_finished", "leader_not_started", "leader_aborted", "debate_proposed"}}:
        try:
            audit.observer(identity)
            if any(event.get("source_attempt_id") == identity and event["action"].startswith("leader_") for event in events):
                audit.leader(identity)
        except (KeyError, TypeError, ValueError, AttributeError, StopIteration, OSError, subprocess.SubprocessError) as exc:
            issues.append(("interaction_source_invalid", str(exc), (str(identity),)))
    try:
        audit.guidance_consumers()
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration) as exc:
        issues.append(("leader_guidance_invalid", str(exc), ()))
    for debate_id in {event["debate_id"] for event in events if "debate_id" in event}:
        try:
            audit.debate(debate_id)
        except (KeyError, TypeError, ValueError, AttributeError, StopIteration, OSError, subprocess.SubprocessError) as exc:
            issues.append(("debate_source_invalid", str(exc), (str(debate_id),)))
    if audit.success_stop is not None and any(row["workflow_kind"] == "RUN" and row.get("state") in {"COMPLETED", "FAILED", "CANCELLED"} for row, _ in audit.transitions):
        for _, event in audit.events:
            if event["action"] == "leader_selected" and not any(other.get("source_attempt_id") == event["source_attempt_id"]
                    and other["action"] in {"leader_round_finished", "leader_not_started", "leader_aborted"} for _, other in audit.events):
                issues.append(("interaction_source_invalid", "terminal shared stop leaves its Leader active", (event["source_attempt_id"],)))
            if event["action"] == "debate_proposed" and not any(other.get("debate_id") == event["debate_id"]
                    and other["action"] in {"debate_failed", "debate_restarts_finished", "debate_aborted"} for _, other in audit.events):
                issues.append(("debate_source_invalid", "terminal shared stop leaves its Debate active", (event["debate_id"],)))
    return issues


__all__ = ["audit_interactions"]
