"""Independent read-only batch/iteration checks over ordinary durable rows.

No Host, Policy, ACI validator, batch reducer or comparison helper is imported.
These observations extend the existing Oracle; they never accept scientific facts.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from .measurement_contract import ScientificMeasurement
from .expert_interaction import expand_fact_references

BatchIssue = tuple[str, str, tuple[str, ...]]
_COMPARABLE = {"SUCCESS", "CLEAN_REFUTATION"}
_REPAIRABLE_INTEGRITY = {"apply_check_failed", "invalid_unified_diff", "missing_implementation_digest", "downstream_target_contract_failed"}


def _need(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _identity(row: Mapping[str, Any]) -> str:
    return f"{row['workflow_id']}@{row['revision']}"


def _time(value: Any) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if isinstance(value, str) else float(value)


def supports_successful_stop(version: str | None) -> bool:
    prefix = "hypoloom-lifecycle-v"
    return isinstance(version, str) and version.startswith(prefix) and version[len(prefix):].isdigit() and int(version[len(prefix):]) >= 9


def shared_goal_stop(facts, payloads, transitions, workflow_version=None):
    """Read the existing atomic verdict/stop and its prior scientific identities.

    Numerical/artifact validity remains the main Oracle's independent result
    derivation; this shared reader never invokes a production reducer.
    """
    if not supports_successful_stop(workflow_version): return None
    ordered = sorted(enumerate(transitions), key=lambda item: dict(item[1]).get("raw_order", item[0]))
    positions = {str(dict(row)["fact_id"]): index for index, (_, row) in enumerate(ordered) if dict(row).get("fact_id") is not None}
    stops = [(index, dict(row)) for index, (_, row) in enumerate(ordered)
        if dict(row).get("command_type") == "RECORD_GOAL_VERDICT"
        and (dict(row).get("state") == "STOPPING" or _payload(row).get("stop_disposition") == "COMPLETED")]
    if not stops: return None
    _need(len(stops) == 1, "shared goal has duplicate successful stop commands")
    position, stop = stops[0]
    goals = [payloads[str(row["fact_id"])] for row in facts if row["fact_type"] == "GoalDefined"]
    _need(len(goals) == 1, "successful stop requires its accepted goal")
    goal = goals[0]
    common = (goal["research_contract"].get("schema_version") == 3
              and goal["research_contract"].get("goal", {}).get("evaluation") == {"mode": "grounding"})
    _need(common or goal["research_contract"].get("methodology", {}).get("controls", {}).get("product", {}).get("goal", {}).get("completion_mode") == "shared_goal",
          "successful stop is not an admitted shared goal")
    verdict = payloads[stop["fact_id"]]
    _need(stop["workflow_kind"] == "RUN" and stop["workflow_id"] == stop["run_id"] == goal["run_id"]
          and stop.get("previous_state") == "ACTIVE" and stop["state"] == "STOPPING"
          and _payload(stop).get("stop_disposition") == "COMPLETED"
          and verdict["fact_type"] == "GoalVerdictRecorded" and verdict["verdict"] == "GOAL_MET"
          and verdict["run_id"] == goal["run_id"] and verdict["goal_id"] == goal["goal_id"], "successful stop differs from its actual GOAL_MET fact")
    ids = verdict["assessment_ids"]
    _need(isinstance(ids, list) and len(ids) == 1, "successful stop requires one winning assessment")
    def source(kind, field, identity):
        matches = [value for value in payloads.values() if value.get("fact_type") == kind and value.get(field) == identity]
        _need(len(matches) == 1 and matches[0].get("run_id") == goal["run_id"]
              and positions.get(str(matches[0]["fact_id"]), position) < position, "successful stop source is missing, foreign or later")
        return matches[0]
    assessment = source("HypothesisAssessed", "assessment_id", ids[0])
    hypothesis = source("HypothesisProposed", "hypothesis_id", assessment["hypothesis_id"])
    component, family = assessment["component_id"], assessment["method_family_id"]
    _need((common or assessment["assessment"] == "SUPPORTED") and verdict["method_family_id"] == family
          and hypothesis["component_id"] == component and hypothesis["method_family_id"] == family,
          "successful stop has no exact supported hypothesis/family")
    _need(assessment["evidence_ids"], "successful stop lacks canonical evidence")
    qualified = False
    for identity in assessment["evidence_ids"]:
        evidence = source("EvidenceLinked", "evidence_id", identity)
        outcome = source("CanonicalOutcomeAccepted", "outcome_id", evidence["outcome_id"])
        spec = source("ExperimentPreregistered", "experiment_id", outcome["experiment_id"])
        _need(all(value["component_id"] == component and value["method_family_id"] == family for value in (evidence, outcome, spec))
              and evidence["hypothesis_id"] == spec["hypothesis_id"] == hypothesis["hypothesis_id"]
              and evidence["artifact_id"] == outcome["artifact_id"] and spec["execution_id"] == outcome["execution_id"],
              "successful stop evidence changes its preregistered experiment identity")
        if common and outcome.get("goal_evaluation") is not None:
            from .shared_acceptance_oracle import interpret_shared_acceptance
            binding, observed = spec.get("goal_evaluation", {}), outcome["goal_evaluation"]
            _need(binding.get("scope") == "GOAL" and binding.get("source_attempt_id") == observed.get("source_attempt_id"),
                  "common successful stop changes its exact evaluator source or scope")
            qualified = qualified or interpret_shared_acceptance(binding["acceptance"],
                {name: observed[name] for name in ("measurements", "checks")}).goal_met
    _need(not common or qualified, "local support alone cannot establish common goal success")
    return stop, verdict


def _payload(row: Mapping[str, Any], parsed: Mapping[str, Mapping[str, Any]] | None = None) -> Mapping[str, Any]:
    row = dict(row)
    value = None if parsed is None else parsed.get(_identity(row) if "workflow_id" in row else str(row.get("fact_id")))
    if value is None:
        value = row.get("payload")
    if value is None:
        value = json.loads(row["payload_json"])
    _need(isinstance(value, Mapping), "durable row payload is not an object")
    return value


def _input(request: Mapping[str, Any], suffix: str) -> Mapping[str, Any]:
    items = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith(suffix)]
    _need(len(items) == 1 and items[0]["media_type"] == "application/json", "exact frozen input is unavailable")
    _need(items[0]["ref"] in request.get("immutable_context_refs", ()), "frozen input is not an admitted context reference")
    value = json.loads(base64.b64decode(items[0]["content_base64"], validate=True))
    _need(isinstance(value, Mapping), "frozen input is not an object")
    return value


def _debate_literature_source(plan, effect, transitions, attempts, observations, run_id):
    """Independently follow accepted HP-only ancestry to the original LR row."""
    role, component = plan["role"], plan["component_id"]
    source = attempts[plan["source_literature_attempt_id"]]
    request = observations[source["attempt_id"]]["request"]
    key = request["task_context"]["key"]
    _need(source["kind"] == "AGENT" and source["run_id"] == request["run_id"] == key["run_id"] == run_id
          and source["attempt_id"] == request["attempt_id"] and request["schema_version"] >= 14
          and source["state"] == "CLOSED" and source["terminal_reason"] == "SUCCEEDED"
          and source["command_id"] == request["command_id"] == plan["source_literature_command_id"]
          and request["command"] == key["node_kind"] == "LITERATURE_REVIEW" and key["subkind"] == "MAIN"
          and (request["role"], request["component_id"], key["expert_slot"], key["component_id"]) == (role, component, role, component),
          "Debate restart lacks its actual owner-bound literature source")
    cycle = plan["source_cycle"]
    _need(1 <= key["cycle"] <= cycle, "Debate literature source is from a later cycle")
    while cycle > key["cycle"]:
        prior = [(row, item) for row, payload in transitions if row["workflow_kind"] == "RUN"
            and row["run_id"] == run_id and row["revision"] < effect["revision"]
            and row.get("command_type") == "ACCEPT_EXPERT_INTERACTION"
            and payload.get("expert_interaction", {}).get("action") == "debate_restarts_designated"
            for item in payload["expert_interaction"]["plans"]
            if (item["role"], item["component_id"], item["cycle"]) == (role, component, cycle)]
        _need(len(prior) == 1, "old Debate literature lacks its unique accepted HP-restart ancestor")
        effect, inherited = prior[0]
        _need(inherited["source_cycle"] == cycle - 1 and inherited["restart_from_hypothesis"] is True
              and inherited["source_literature_attempt_id"] == source["attempt_id"]
              and inherited["source_literature_command_id"] == source["command_id"],
              "Debate literature ancestry changed its actual source or parent cycle")
        cycle -= 1
    _need(_time(source["ended_at"]) <= _time(effect["created_at"]), "Debate literature was not closed before its original restart")


def _candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    _need(set(value) == {"schema_version", "verdicts", "keep", "prune", "best_chain", "summary"}
          and type(value["schema_version"]) is int and value["schema_version"] == 1, "adjudication fields/version differ")
    verdicts = value["verdicts"]
    _need(isinstance(verdicts, list) and all(isinstance(item, Mapping) for item in verdicts), "verdicts are not an array")
    _need(len({item["chain_id"] for item in verdicts}) == len(verdicts), "duplicate chain verdict")
    for item in verdicts:
        _need(set(item) == {"chain_id", "node_id", "status", "evidence"} and isinstance(item["evidence"], list)
              and len(set(item["evidence"])) == len(item["evidence"]), "verdict fields or references differ")
    for name in ("keep", "prune"):
        _need(isinstance(value[name], list) and len(set(value[name])) == len(value[name]), "keep/prune are not unique arrays")
    return {**value, "keep": sorted(value["keep"]), "prune": sorted(value["prune"]),
            "verdicts": sorted(({**item, "evidence": sorted(item["evidence"])} for item in verdicts), key=lambda item: item["chain_id"])}


def selected_fact_tips(facts: Sequence[Mapping[str, Any]], payloads: Mapping[str, Mapping[str, Any]],
                       transitions: Sequence[Mapping[str, Any]], transition_payloads: Mapping[str, Mapping[str, Any]]) -> tuple[dict, dict, dict]:
    """Project exact accepted selections; later unselected outcomes do not win."""
    selections = {}
    for _, raw in sorted(enumerate(transitions), key=lambda item: dict(item[1]).get("raw_order", item[0])):
        row = dict(raw)
        if row.get("command_type") != "ACCEPT_ADJUDICATION":
            continue
        value = _payload(row, transition_payloads)
        selections[value["component_id"]] = value["adjudication"].get("selected_tip")
    specs, outcomes, evidence = {}, {}, {}
    for component, tip in selections.items():
        if not isinstance(tip, Mapping):
            continue
        selected = {}
        for kind, identity, field in (("ExperimentPreregistered", tip.get("experiment_id"), "experiment_id"),
                                      ("CanonicalOutcomeAccepted", tip.get("outcome_id"), "outcome_id")):
            matches = [row for row in facts if row["fact_type"] == kind and payloads.get(str(row["fact_id"]), {}).get(field) == identity]
            if len(matches) == 1:
                selected[kind] = matches[0]
        if len(selected) != 2:
            continue  # audit_batches reports the invalid control; projection never invents a replacement.
        spec, outcome = selected["ExperimentPreregistered"], selected["CanonicalOutcomeAccepted"]
        sp, out = payloads[str(spec["fact_id"])], payloads[str(outcome["fact_id"])]
        if (sp.get("hypothesis_id"), sp.get("component_id"), out.get("experiment_id"), out.get("component_id")) != (
                tip.get("node_id"), component, tip.get("experiment_id"), component):
            continue
        specs[component], outcomes[component] = spec, outcome
        matches = [row for row in facts if row["fact_type"] == "EvidenceLinked" and all(
            payloads[str(row["fact_id"])].get(field) == tip.get(target) for field, target in (
                ("hypothesis_id", "node_id"), ("outcome_id", "outcome_id"), ("artifact_id", "artifact_id"), ("component_id", "component_id")))]
        if matches:
            evidence[component] = min(matches, key=lambda row: str(row["fact_id"]))
    return specs, outcomes, evidence


class _Audit:
    def __init__(self, run_id, attempts, attempt_payloads, facts, fact_payloads, transitions, commands):
        self.run_id = run_id
        self.attempts = {str(row["attempt_id"]): dict(row) for row in attempts}
        self.observations = attempt_payloads
        self.facts = fact_payloads
        self.rows = {str(row["fact_id"]): dict(row) for row in facts}
        self.transitions = [(dict(row), _payload(row)) for row in transitions]
        self.order = {_identity(row): int(row.get("raw_order", index)) for index, (row, _) in enumerate(self.transitions)}
        self.fact_order = {str(row["fact_id"]): self.rank(row) for row, _ in self.transitions if row.get("fact_id") is not None}
        self.commands = {str(row["command_id"]): dict(row) for row in commands}
        goals = [self.facts[str(row["fact_id"])] for row in facts if row["fact_type"] == "GoalDefined"]
        _need(len(goals) == 1, "batch audit requires its one accepted GoalDefined")
        self.contract = goals[0]["research_contract"]
        self.components = {item["id"]: item for item in self.contract["components"]}
        self.batches, self.selections = {}, {}

    def rank(self, row):
        return self.order[_identity(row)]

    def limits(self, role):
        product = self.contract.get("methodology", {}).get("controls", {}).get("product", {})
        inner = product.get("methodology", {}).get("inner_loop", {})
        expert = product.get("experts", {})
        settings = {**expert.get("defaults", {}), **next((item for item in expert.get("experts", ()) if item.get("name", item.get("id")) == role), {})}
        return (settings.get("max_hypotheses_per_round", inner.get("max_hypotheses_per_round", 1)),
                inner.get("max_iterations_per_hypothesis", 1),
                product.get("goal", {}).get("stop_conditions", {}).get("max_rounds_per_expert", self.contract.get("budget", {}).get("max_experiments", 1)))

    def source(self, identity, kind, *, closed=True):
        row = self.attempts[identity]
        observation = self.observations[identity]
        request = observation["request"]
        key = request["task_context"]["key"]
        _need(row["run_id"] == request["run_id"] == key["run_id"] == self.run_id and row["kind"] == "AGENT", "source Attempt belongs to another run/kind")
        _need(request["attempt_id"] == identity and request["command_id"] == row["command_id"] and request["command"] == key["node_kind"] == kind,
              "source Attempt command identity differs")
        _need((key["component_id"], key["expert_slot"]) == (request["component_id"], request["role"]), "source request participant differs from its node")
        _need(type(request["schema_version"]) is int and request["schema_version"] >= 14, "batch source is not ACI14")
        if closed:
            _need(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED", "accepted source is not a successful closed Attempt")
        return row, request, observation.get("response", {}), key

    def fact(self, kind, field, identity):
        rows = [value for value in self.facts.values() if value["fact_type"] == kind and value.get(field) == identity]
        _need(len(rows) == 1 and rows[0]["run_id"] == self.run_id, "canonical fact is missing, ambiguous or foreign")
        return rows[0]

    def owner(self, payload, request):
        _need((payload["component_id"], payload["role"]) == (request["component_id"], request["role"]), "accepted transition has another participant")
        _need(self.components[request["component_id"]]["owner"] == request["role"], "source is not the component owner")

    def exhausted(self, role, before):
        creations = [(r, p) for r, p in self.transitions if r["command_type"] == "CREATE_EXPERIMENT" and self.rank(r) < before]
        budget = self.contract.get("budget", {})
        role_budget = next(item for item in self.contract["roles"] if item["id"] == role).get("budget", {})
        role_cap = role_budget.get("max_experiments", budget.get("max_experiments"))
        return (len(creations) >= budget.get("max_experiments", float("inf"))
                or role_cap is not None and sum(p.get("role") == role for _, p in creations) >= role_cap)

    def creation(self, row, payload):
        source, request, _, key = self.source(payload["source_agent_attempt_id"], "EXPERIMENT_EXECUTION", closed=False)
        self.owner(payload, request)
        identity = (request["component_id"], request["role"], key["cycle"])
        batch_row, batch = self.batches[identity]
        iteration, chain = payload["chain_iteration"], payload["chain"]
        _need(row["run_id"] == self.run_id and row["workflow_kind"] == "EXPERIMENT"
              and payload["execution_node_command_id"] == request["command_id"] and payload["expert_cycle"] == key["cycle"]
              and iteration == key["experiment_ordinal"] and chain == key.get("chain"), "experiment node/chain identity differs")
        _need(type(iteration) is int and 1 <= iteration <= self.limits(request["role"])[1]
              and batch["hypothesis_batch"]["chains"][chain] == payload["hypothesis_id"]
              and self.rank(batch_row) < self.rank(row), "experiment is outside its accepted batch/iteration bound")
        hypothesis = self.fact("HypothesisProposed", "hypothesis_id", payload["hypothesis_id"])
        _need(payload["method_family_id"] == hypothesis["method_family_id"] == request["method_family_id"], "experiment method family differs from its hypothesis")
        submit = self.commands[payload["source_submit_command_id"]]
        submitted = json.loads(submit["request_json"])
        _need(submit["run_id"] == self.run_id and submit["workflow_id"] == row["workflow_id"]
              and submit["command_type"] == "CREATE_EXPERIMENT" and submit.get("response_status") == "accepted"
              and submitted["tool"] == "submit_experiment" and submitted["source_request"] == request
              and submitted["execution_node_command_id"] == request["command_id"], "creation lacks its exact accepted source-owned submit")
        siblings = [(r, p) for r, p in self.transitions if r["command_type"] == "CREATE_EXPERIMENT"
            and p.get("hypothesis_id") == payload["hypothesis_id"] and p.get("chain_iteration") == iteration and self.rank(r) < self.rank(row)]
        repair = payload.get("preflight_repair_of_experiment_id")
        if payload.get("revision_of_outcome_id") is not None:
            prior_outcome = self.fact("CanonicalOutcomeAccepted", "outcome_id", payload["revision_of_outcome_id"])
            _need(repair is None and (prior_outcome["outcome"] == "EXECUTION_FAILURE" or prior_outcome["outcome"] == "INTEGRITY_REJECTED"
                  and prior_outcome.get("validation_code") in _REPAIRABLE_INTEGRITY), "operational repair cannot rewrite a non-repairable result")
            repair = prior_outcome["experiment_id"]
        if siblings:
            previous = max(siblings, key=lambda item: self.rank(item[0]))
            _need(repair == previous[0]["workflow_id"] and payload["execution_node_command_id"] == previous[1]["execution_node_command_id"],
                  "same-iteration submission is not a causal operational repair")
        else:
            _need(repair is None, "operational repair has no preceding experiment in its exact science iteration")
        if iteration == 1:
            _need(payload.get("iteration_of_experiment_id") is None and payload.get("analysis_attempt_id") is None, "first iteration invents a scientific predecessor")
            return
        origin = {name: payload[name] for name in ("iteration_of_experiment_id", "analysis_attempt_id")}
        _need(_input(request, "--iteration-context") == origin, "scientific predecessor differs from the frozen request")
        prior_creation = [(r, p) for r, p in self.transitions if r["command_type"] == "CREATE_EXPERIMENT"
            and r["workflow_id"] == origin["iteration_of_experiment_id"]]
        _need(len(prior_creation) == 1 and self.rank(prior_creation[0][0]) < self.rank(row)
              and all(prior_creation[0][1].get(field) == payload[field] for field in ("hypothesis_id", "component_id", "role", "chain", "expert_cycle"))
              and prior_creation[0][1]["chain_iteration"] == iteration - 1, "scientific predecessor is not the preceding iteration of this hypothesis")
        analysis, analysis_request, response, analysis_key = self.source(origin["analysis_attempt_id"], "RESULT_ANALYSIS")
        _need(all(analysis_key.get(field) == key.get(field) for field in ("component_id", "expert_slot", "cycle", "chain"))
              and analysis_key["experiment_ordinal"] == iteration - 1 and analysis["ended_at"] <= source["started_at"]
              and response["typed_candidate"].get("iteration_disposition") == "ITERATE", "scientific iteration lacks its actual prior ITERATE analysis")
        observed = _input(analysis_request, "--current-outcome")["payload"]
        outcome = self.fact("CanonicalOutcomeAccepted", "experiment_id", origin["iteration_of_experiment_id"])
        _need(observed == outcome and outcome["outcome"] in _COMPARABLE and self.fact_order[outcome["fact_id"]] < self.rank(row),
              "scientific iteration does not follow an accepted comparable outcome")

    def nodes(self, identity, kind):
        return [self.source(attempt_id, kind) for attempt_id, observation in self.observations.items()
            if (lambda key: (key.get("component_id"), key.get("expert_slot"), key.get("cycle")) == identity
                and key.get("node_kind") == kind and key.get("chain") is None)((observation.get("request", {}).get("task_context") or {}).get("key", {}))
            and self.attempts[attempt_id]["state"] == "CLOSED" and self.attempts[attempt_id]["terminal_reason"] == "SUCCEEDED"]

    def final_failures(self, identity, *, kind=None, chain=None, before=float("inf")):
        found = []
        for row, payload in self.transitions:
            if row["command_type"] != "AGENT_ATTEMPT_FINAL_FAILURE" or payload.get("agent_final_failure") is not True or self.rank(row) >= before:
                continue
            attempt_id = payload.get("attempt_id")
            observation = self.observations.get(attempt_id, {})
            request = observation.get("request", {})
            key = (request.get("task_context") or {}).get("key", {})
            if (key.get("component_id"), key.get("expert_slot"), key.get("cycle")) != identity or key.get("chain") != chain or kind is not None and key.get("node_kind") != kind:
                continue
            attempt, _, _, _ = self.source(attempt_id, key["node_kind"], closed=False)
            _need(attempt["state"] == "CLOSED" and attempt["terminal_reason"] not in (None, "SUCCEEDED")
                  and payload["failed_task_command_id"] == request["command_id"] and attempt["ended_at"] <= _time(row["created_at"]),
                  "final failure does not bind its actual closed failed source")
            found.append((row, attempt, request))
        return found

    def cycle_close(self, identity, *, before=float("inf"), terminal=False):
        if identity not in self.selections:
            failures = self.final_failures(identity, kind="ADJUDICATE", before=before)
            _need(terminal and len(failures) == 1 and not self.nodes(identity, "ADJUDICATE"),
                  "unselected batch lacks its unique final adjudication failure")
            return _time(failures[0][0]["created_at"])
        selected_row, selected = self.selections[identity]
        accepted = _payload(selected_row)
        previous = self.source(accepted["agent_attempt_id"], "ADJUDICATE")
        update = None
        failed_update = None
        failed_report = None
        failed_evolve = False
        def predecessor(request, started, kind):
            failed_previous = failed_update if kind == "CYCLE_REPORT" else failed_report if kind == "SELF_EVOLVE" else None
            if failed_previous is not None:
                failed_row, failed = failed_previous
                _need(started >= _time(failed_row["created_at"])
                      and _input(request, failed["attempt_id"] + "--final-failure")
                      == {"attempt": failed, "control": _payload(failed_row)}, "cycle node lacks its actual failed predecessor source")
            else:
                _need(started >= previous[0]["ended_at"]
                      and _input(request, previous[0]["attempt_id"] + "--response") == previous[2], "cycle node lacks its actual completed predecessor response")
        for kind in ("HYPOTHESIS_UPDATE", "CYCLE_REPORT", "SELF_EVOLVE"):
            sources = self.nodes(identity, kind)
            if not sources and kind == "HYPOTHESIS_UPDATE":
                failures = self.final_failures(identity, kind=kind, before=before)
                _need(len(failures) == 1, "cycle lacks successful or actual final-failed update")
                failed_row, failed, request = failures[0]
                _need(failed["started_at"] >= _time(selected_row["created_at"])
                      and _input(request, previous[0]["attempt_id"] + "--response") == previous[2],
                      "failed update lacks its actual adjudication predecessor")
                failed_update = (failed_row, failed)
                continue
            if not sources and kind == "CYCLE_REPORT":
                failures = self.final_failures(identity, kind=kind, before=before)
                _need(len(failures) == 1, "cycle lacks successful or actual final-failed report")
                failed_row, failed, request = failures[0]
                predecessor(request, failed["started_at"], kind)
                failed_report = (failed_row, failed)
                continue
            if not sources and kind == "SELF_EVOLVE":
                failures = self.final_failures(identity, kind=kind, before=before)
                _need(len(failures) == 1, "cycle lacks successful or actual final-failed Self Evolve")
                failed_row, failed, request = failures[0]
                predecessor(request, failed["started_at"], kind)
                ended = _time(failed_row["created_at"])
                failed_evolve = True
                break
            _need(len(sources) == 1, "cycle lacks its unique successful " + kind)
            source = sources[0]
            predecessor(source[1], source[0]["started_at"], kind)
            if kind == "HYPOTHESIS_UPDATE":
                _need(source[0]["started_at"] >= _time(selected_row["created_at"]), "update was dispatched before accepted adjudication")
                update = source[2]["typed_candidate"]
            previous, ended = source, source[0]["ended_at"]
        if terminal:
            analysis_failed = any(self.final_failures(identity, kind="RESULT_ANALYSIS", chain=chain, before=before)
                for chain in self.batches[identity][1]["hypothesis_batch"]["chains"])
            successor_failures = [item for item in self.final_failures((*identity[:2], identity[2] + 1), before=before)
                                  if item[2]["command"] in {"LITERATURE_REVIEW", "HYPOTHESIS_PROPOSAL"}]
            for failed_row, failed, request in successor_failures:
                if request["schema_version"] >= 17 and selected["selected_tip"] is None:
                    self.unselected_successor(failed["attempt_id"])
                    continue
                _need(failed["started_at"] >= ended and update is not None
                      and update.get("update_disposition") == "REVISE_NEXT_CYCLE"
                      and _input(request, "--lineage-context")["parent_hypothesis_id"] == selected["selected_tip"]["node_id"],
                      "successor failure does not follow its actual closed parent cycle")
            _need(analysis_failed or failed_update is not None or failed_report is not None or successor_failures
                  or failed_evolve and selected["selected_tip"] is None and previous[1]["schema_version"] >= 17
                  or update is not None and update.get("update_disposition") == "TERMINATE_LINEAGE"
                  or identity[2] >= self.limits(identity[1])[2]
                  or selected["selected_tip"] is None and self.source(accepted["agent_attempt_id"], "ADJUDICATE")[1]["schema_version"] < 17
                  or self.exhausted(identity[1], before), "completed lineage still requests a budgeted next cycle")
        return ended

    def unselected_successor(self, attempt_id):
        """ACI17 provenance is a prior member, never an invented selected tip."""
        request = self.observations[attempt_id].get("request", {})
        key = (request.get("task_context") or {}).get("key", {})
        if request.get("schema_version", 0) < 17 or key.get("cycle", 1) <= 1:
            return
        identity = (key["component_id"], key["expert_slot"], key["cycle"])
        prior_identity = (*identity[:2], identity[2] - 1)
        selected_row, selected = self.selections[prior_identity]
        _, request, _, _ = self.source(attempt_id, key["node_kind"], closed=False)
        started = min(row["started_at"] for row in self.attempts.values() if row["command_id"] == request["command_id"])
        proposal = key["node_kind"] in {"LITERATURE_REVIEW", "HYPOTHESIS_PROPOSAL"} and key.get("chain") is None
        admitted_at = started
        if not proposal:
            batch_row, batch = self.batches[identity]
            _need(_time(batch_row["created_at"]) <= started, "successor node precedes its accepted current batch")
            admitted_at = self.attempts[batch["agent_attempt_id"]]["started_at"]
        before = 1 + max((self.rank(row) for row, _ in self.transitions if _time(row["created_at"]) <= admitted_at), default=-1)
        directed = self.directed_parent(request, before)
        if directed is not None:
            _need(self.cycle_close(prior_identity, before=before) <= started
                  and _input(request, "--lineage-context") == {"cycle": identity[2], "parent_hypothesis_id": directed}
                  and identity[2] <= self.limits(identity[1])[2] and not self.exhausted(identity[1], before),
                  "directed successor bypasses closure, budget or authorized lineage")
            return
        if selected["selected_tip"] is not None:
            return
        _need(_time(selected_row["created_at"]) <= started
              and all(len(self.nodes(prior_identity, kind)) == 1 for kind in ("HYPOTHESIS_UPDATE", "CYCLE_REPORT", "SELF_EVOLVE"))
              and self.cycle_close(prior_identity, before=before) <= started,
              "unselected successor began before successful prior cycle controls closed")
        update = self.nodes(prior_identity, "HYPOTHESIS_UPDATE")[0][2]["typed_candidate"]
        _need(update.get("update_disposition") == "REVISE_NEXT_CYCLE"
              and identity[2] <= self.limits(identity[1])[2] and not self.exhausted(identity[1], before),
              "unselected successor lacks explicit budgeted revision")
        parent = None
        if key.get("chain") is not None:
            batch_row, batch = self.batches[identity]
            _need(_time(batch_row["created_at"]) <= started, "chain node precedes its accepted current batch")
            member = batch["hypothesis_batch"]["chains"][key["chain"]]
            parent = self.fact("HypothesisProposed", "hypothesis_id", member)["parent_hypothesis_id"]
        _need(_input(request, "--lineage-context") == {"cycle": identity[2], "parent_hypothesis_id": parent},
              "unselected successor lineage context differs from its actual member parent")
        if proposal:
            _, batch = self.batches[prior_identity]
            judge = self.source(_payload(selected_row)["agent_attempt_id"], "ADJUDICATE")[1]
            expected = {"batch_command_id": batch["node_command_id"], "cycle": identity[2] - 1,
                "chains": batch["hypothesis_batch"]["chains"],
                "terminal_tips": _input(judge, "--batch-context")["terminal_tips"], "selected_tip": None}
            _need(_input(request, request["command_id"] + "--batch-context") == expected,
                  "unselected proposal does not carry its exact immediately preceding batch")
            hypotheses = []
            for item in request.get("input_artifacts", ()):
                if item.get("media_type") != "application/json":
                    continue
                value = json.loads(base64.b64decode(item["content_base64"], validate=True))
                if isinstance(value, Mapping) and value.get("fact_type") == "HypothesisProposed" and isinstance(value.get("payload"), Mapping):
                    hypotheses.append((item, value))
            for parent_id in expected["chains"].values():
                fact = self.fact("HypothesisProposed", "hypothesis_id", parent_id)
                matches = [(item, value) for item, value in hypotheses if value["payload"].get("hypothesis_id") == parent_id]
                _need(len(matches) == 1 and matches[0][0]["ref"] == fact["fact_id"]
                      and fact["fact_id"] in request.get("immutable_context_refs", ())
                      and matches[0][1] == {"fact_id": fact["fact_id"], "fact_type": "HypothesisProposed", "payload": fact},
                      "unselected proposal parent is not its exact immutable accepted hypothesis fact")

    def dependencies(self, attempt_id):
        request = self.observations[attempt_id].get("request", {})
        key = (request.get("task_context") or {}).get("key", {})
        component = self.components.get(request.get("component_id"), {})
        if not key or not component.get("dependencies"):
            return
        _, request, _, _ = self.source(attempt_id, key["node_kind"], closed=False)
        # Infrastructure retries keep the first logical request's admitted inputs.
        started = min(row["started_at"] for row in self.attempts.values() if row["command_id"] == request["command_id"])
        before = 1 + max((self.rank(row) for row, _ in self.transitions if _time(row["created_at"]) <= started), default=-1)
        selected = {}
        for producer in component["dependencies"]:
            choices = [(row, value) for row, value in self.transitions if row["command_type"] == "ACCEPT_ADJUDICATION"
                and value.get("component_id") == producer and _time(row["created_at"]) <= started]
            _need(choices, "consumer was dispatched without its dependency's accepted adjudication")
            row, value = max(choices, key=lambda item: self.rank(item[0]))
            cycle = value["adjudication"]["cycle"]
            identity = (producer, self.components[producer]["owner"], cycle)
            _need(identity in self.selections and self.selections[identity][0] == row,
                  "consumer cites an invalid or unaccepted adjudication source")
            _need(not any(p.get("component_id") == producer and "hypothesis_batch" in p
                and self.rank(r) > self.rank(row) and _time(r["created_at"]) <= started for r, p in self.transitions),
                "consumer used a previous selection while a newer batch was unfinished")
            tip = value["adjudication"]["selected_tip"]
            _need(tip is not None and tip["outcome"] in _COMPARABLE, "consumer dependency has no comparable selected tip")
            _need(self.cycle_close(identity, before=before, terminal=True) <= started, "consumer ran before the producer lineage actually closed")
            source_id = value["agent_attempt_id"]
            _need(_input(request, source_id + "--response") == self.observations[source_id]["response"],
                  "consumer does not contain the exact selected adjudication response")
            selected[producer] = tip
        requirements = [{"resource": item["resource"], "produced_by_component": item["produced_by"],
                         "logical_artifact": item["artifact"], "min_count": item.get("min_count", 1)}
                        for item in component.get("required_inputs", ())]
        if not requirements:
            return
        families = {selected[item["produced_by_component"]]["method_family_id"] for item in requirements}
        _need(len(families) == 1 and (request["method_family_id"] is None or request["method_family_id"] in families)
              and _input(request, "--expert-config")["required_method_family_id"] in families,
              "consumer request/config does not continue its selected producer family")
        source = self.attempts[attempt_id]
        if key["node_kind"] == "HYPOTHESIS_PROPOSAL" and key["subkind"] == "SYNTHESIS" and source["state"] == "CLOSED" and source["terminal_reason"] == "SUCCEEDED":
            members = self.observations[attempt_id]["response"]["typed_candidate"]["hypotheses"]
            _need(all(member["candidate"]["method_family_id"] in families for member in members), "consumer hypothesis set changes the selected producer family")
        expected = [{**requirement, "outcome_id": selected[requirement["produced_by_component"]]["outcome_id"],
            "artifact_id": selected[requirement["produced_by_component"]]["artifact_id"]} for requirement in requirements]
        for fact in self.facts.values():
            if fact["fact_type"] != "ExperimentPreregistered" or fact["component_id"] != request["component_id"]:
                continue
            source_id = fact.get("execution_contract", {}).get("source_agent_attempt_id")
            if source_id != attempt_id:
                continue
            _need(fact["method_family_id"] == request["method_family_id"] and sorted(fact["upstream_outcomes"], key=lambda item: item["resource"])
                  == sorted(expected, key=lambda item: item["resource"]), "ProducerOutcomeBinding differs from the exact frozen selected outcome/artifact")

    def directed_parent(self, request, before):
        if request["schema_version"] < 21: return None
        key = request["task_context"]["key"]
        matches = []
        for row, value in self.transitions:
            if self.rank(row) >= before: continue
            event = value.get("expert_interaction", {})
            plans = [event["leader"]] if event.get("action") == "leader_round_started" and "task_command_id" not in event else (
                event.get("plans", ()) if event.get("action") == "debate_restarts_designated" else ())
            for plan in plans:
                if (plan["role"], plan["component_id"], plan["cycle"]) == (request["role"], request["component_id"], key["cycle"]):
                    matches.append((row, event, plan))
        if not matches: return None
        _need(len(matches) == 1, "directed research has duplicate accepted plans")
        row, event, plan = matches[0]
        parent = plan["parent_hypothesis_id"]
        hypothesis = self.fact("HypothesisProposed", "hypothesis_id", parent)
        _need(row["command_type"] == "ACCEPT_EXPERT_INTERACTION" and hypothesis["proposer_role"] == request["role"]
              and hypothesis["component_id"] == request["component_id"]
              and any(parent in batch[1]["hypothesis_batch"]["chains"].values() for identity, batch in self.batches.items()
                  if identity[:2] == (request["component_id"], request["role"]) and identity[2] < key["cycle"])
              and _time(row["created_at"]) <= self.attempts[request["attempt_id"]]["started_at"],
              "directed research parent is not an authorized earlier own hypothesis")
        if event["action"] == "debate_restarts_designated":
            _need(self.debate_reopen(request, parent, before), "directed Debate source is missing")
            proposals = [value["expert_interaction"] for prior, value in self.transitions if self.rank(prior) < self.rank(row)
                and value.get("expert_interaction", {}).get("action") == "debate_proposed"
                and value["expert_interaction"].get("debate_id") == event["debate_id"]]
            _need(len(proposals) == 1 and any(item["hypothesis_id"] == parent
                and item["scope"]["role"] == request["role"] and item["scope"]["component_id"] == request["component_id"]
                for item in proposals[0]["proposal"]["debate"]["participants"]), "Debate parent differs from its accepted participant")
            if key["node_kind"] == "HYPOTHESIS_PROPOSAL":
                guidance = _input(request, "--debate-guidance")
                source = self.observations[event["source_attempt_id"]]
                _need(guidance["debate_id"] == event["debate_id"] and guidance["source_judge_attempt_id"] == event["source_attempt_id"]
                      and guidance["judgment"] == source["response"]["typed_candidate"]
                      and guidance["facts"] == _input(source["request"], "--debate-judge-context")["facts"],
                      "directed planning changed its public judgment or accepted scientific sources")
        else:
            proposals = [value["expert_interaction"] for prior, value in self.transitions if self.rank(prior) < self.rank(row)
                and value.get("expert_interaction", {}).get("action") == "leader_selected"
                and value["expert_interaction"].get("source_attempt_id") == event["source_attempt_id"]]
            source = self.observations[event["source_attempt_id"]]
            context = _input(source["request"], "--interaction-context")
            frozen = [json.loads(item["payload_json"]) if "payload_json" in item else item.get("payload", item)
                for item in context["facts"]]
            _need(len(proposals) == 1 and parent in expand_fact_references(frozen, proposals[0]["proposal"]["supporting_refs"])
                  and proposals[0]["proposal"] == source["response"]["typed_candidate"],
                  "Leader parent differs from its actual Observer proposal")
        return parent

    def leader_reopen(self, request, parent, before):
        bindings = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith("--leader-resources")]
        if not bindings:
            return False
        resources = _input(request, "--leader-resources")
        source = resources["source_attempt_id"]
        observer = self.attempts[source]
        observed = self.observations[source]
        _need(observer["run_id"] == self.run_id and observer["kind"] == "AGENT" and observer["state"] == "CLOSED"
              and observer["terminal_reason"] == "SUCCEEDED" and observed["request"]["command"] == "OBSERVER",
              "Leader reopening lacks its real successful Observer source")
        selected = [(row, value["expert_interaction"]) for row, value in self.transitions if self.rank(row) < before
            and value.get("expert_interaction", {}).get("source_attempt_id") == source
            and value["expert_interaction"].get("action") == "leader_selected"]
        starts = [(row, value["expert_interaction"]) for row, value in self.transitions if self.rank(row) < before
            and value.get("expert_interaction", {}).get("source_attempt_id") == source
            and value["expert_interaction"].get("action") == "leader_round_started"]
        _need(len(selected) == len(starts) == 1 and self.rank(selected[0][0]) < self.rank(starts[0][0]),
              "Leader reopening lacks its unique source-bound selection/designation")
        selection, plan = selected[0][1], starts[0][1]["leader"]
        _need(selection["proposal"] == observed["response"]["typed_candidate"] and selection["proposal"]["action"] == "leader"
              and selection["leader"]["role"] == plan["role"] == request["role"]
              and selection["leader"]["component_id"] == plan["component_id"] == request["component_id"]
              and plan["cycle"] == request["task_context"]["key"]["cycle"] and plan["parent_hypothesis_id"] == parent
              and resources == {"source_attempt_id": source, **{name: plan[name] for name in ("role", "component_id", "cycle", "peer_memory_commits")}}
              and _time(starts[0][0]["created_at"]) <= self.attempts[request["attempt_id"]]["started_at"],
              "Leader reopening changed its frozen target, parent or memory source")
        return True

    def debate_reopen(self, request, parent, before):
        identity = (request["role"], request["component_id"], request["task_context"]["key"]["cycle"])
        plans = [(row, value["expert_interaction"], plan) for row, value in self.transitions if self.rank(row) < before
            and value.get("expert_interaction", {}).get("action") == "debate_restarts_designated"
            for plan in value["expert_interaction"]["plans"] if (plan["role"], plan["component_id"], plan["cycle"]) == identity]
        if not plans:
            return False
        _need(len(plans) == 1, "Debate restarted the same expert cycle more than once")
        row, event, plan = plans[0]
        source = event["source_attempt_id"]
        judge = self.attempts[source]
        observed = self.observations[source]
        verdicts = [(effect, payload["expert_interaction"]) for effect, payload in self.transitions if self.rank(effect) < self.rank(row)
            and payload.get("expert_interaction", {}).get("action") == "debate_verdict"
            and payload["expert_interaction"].get("debate_id") == event["debate_id"]]
        _need(len(verdicts) == 1 and verdicts[0][1]["source_attempt_id"] == source
              and judge["kind"] == "AGENT" and judge["run_id"] == self.run_id and judge["state"] == "CLOSED"
              and judge["terminal_reason"] == "SUCCEEDED" and observed["request"]["command"] == "DEBATE_JUDGE"
              and verdicts[0][1]["agent_output_digest"] == observed["response"]["output_digest"],
              "Debate reopening lacks its unique actual accepted judge source")
        _need(plan["parent_hypothesis_id"] == parent and plan["source_cycle"] == identity[2] - 1
              and plan["restart_from_hypothesis"] is True and _time(row["created_at"]) <= self.attempts[request["attempt_id"]]["started_at"],
              "Debate reopening changed its accepted hypothesis-stage cycle/parent")
        _debate_literature_source(plan, row, self.transitions, self.attempts, self.observations, self.run_id)
        return True

    def batch(self, row, payload):
        _need(row["command_type"] == "ACCEPT_HYPOTHESIS" and row["workflow_kind"] == "RUN"
              and row["workflow_id"] == row["run_id"] == self.run_id, "batch marker is not a run-level accepted hypothesis")
        source_id = payload["agent_attempt_id"]
        _, request, response, key = self.source(source_id, "HYPOTHESIS_PROPOSAL")
        self.owner(payload, request)
        _need(key["subkind"] == "SYNTHESIS" and key.get("chain") is None, "batch source is not the shared SYNTHESIS node")
        _need(payload["node_command_id"] == request["command_id"] and payload["agent_output_digest"] == response["output_digest"], "accepted batch source bytes differ")
        cycle, chains = payload["hypothesis_batch"]["cycle"], payload["hypothesis_batch"]["chains"]
        identity = (request["component_id"], request["role"], cycle)
        _need(identity not in self.batches and cycle == key["cycle"] and isinstance(chains, Mapping) and chains,
              "batch cycle or membership is invalid/duplicated")
        _need(all(isinstance(chain, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", chain) for chain in chains)
              and len(set(chains.values())) == len(chains), "batch chains or canonical hypotheses are not unique safe identities")
        raw = response["typed_candidate"]
        _need(set(raw) == {"schema_version", "hypotheses"} and raw["schema_version"] == 1 and isinstance(raw["hypotheses"], list), "source is not a HypothesisSet")
        members = {item["chain_id"]: item["candidate"] for item in raw["hypotheses"]}
        _need(len(members) == len(raw["hypotheses"]) and set(members) == set(chains) and len(chains) <= self.limits(request["role"])[0], "accepted batch differs from its complete bounded source set")
        accepts = [(record, value) for record, value in self.transitions if record["command_type"] == "ACCEPT_HYPOTHESIS" and value.get("agent_attempt_id") == source_id]
        _need(len(accepts) == len(chains) and sum("hypothesis_batch" in value for _, value in accepts) == 1
              and self.rank(row) == max(self.rank(record) for record, _ in accepts), "batch is partial or marker does not close its accepted source")
        prior = self.selections.get((identity[0], identity[1], cycle - 1))
        parent = None if cycle == 1 else None if prior is None else prior[1]["selected_tip"]
        directed = self.directed_parent(request, self.rank(row))
        unselected = directed is None and cycle > 1 and request["schema_version"] >= 17 and prior is not None and parent is None
        if cycle > 1:
            _need((parent is not None or unselected or directed is not None) and self.rank(prior[0]) < min(self.rank(record) for record, _ in accepts), "new batch lacks its preceding accepted selection")
            parent = directed if directed is not None else None if unselected else parent["node_id"]
            if unselected:
                self.unselected_successor(source_id)
            _need(self.cycle_close((identity[0], identity[1], cycle - 1), before=self.rank(row))
                  <= self.attempts[source_id]["started_at"], "new cycle began before report and Self Evolve completed")
            update = self.nodes((identity[0], identity[1], cycle - 1), "HYPOTHESIS_UPDATE")[0]
            _need((directed is not None or update[2]["typed_candidate"].get("update_disposition") == "REVISE_NEXT_CYCLE"
                   or not unselected and (self.leader_reopen(request, parent, self.rank(row))
                   or self.debate_reopen(request, parent, self.rank(row)))) and cycle <= self.limits(request["role"])[2]
                  and not self.exhausted(request["role"], self.rank(row)), "new batch follows a terminated or exhausted lineage")
        _need(cycle == 1 or (identity[0], identity[1], cycle - 1) in self.batches, "accepted batch cycles are not contiguous")
        for record, accepted in accepts:
            chain = accepted["chain"]
            _need(chain in chains and accepted["hypothesis_id"] == chains[chain] and accepted["node_command_id"] == request["command_id"]
                  and accepted["agent_output_digest"] == response["output_digest"], "member differs from the shared accepted source")
            fact = self.fact("HypothesisProposed", "hypothesis_id", chains[chain])
            _need(record["fact_id"] == fact["fact_id"] and fact["component_id"] == request["component_id"] and fact["proposer_role"] == request["role"], "batch member fact ownership differs")
            if unselected:
                parents = self.batches[(*identity[:2], cycle - 1)][1]["hypothesis_batch"]["chains"].values()
                _need(fact.get("parent_hypothesis_id") in parents and isinstance(fact.get("relation"), str)
                      and bool(fact["relation"].strip()), "revision member lacks its immediately preceding batch parent/relation")
            else:
                _need(fact.get("parent_hypothesis_id") == parent and (fact.get("relation") is None) == (cycle == 1), "member parent differs from the prior selected hypothesis")
            for field in ("component_id", "proposer_role", "method_family_id", "claim", "assumptions", "parent_hypothesis_id", "relation", "measurement"):
                actual, submitted = fact.get(field), members[chain].get(field)
                if field == "measurement":
                    actual = None if actual is None else ScientificMeasurement.from_dict(actual).to_dict()
                    submitted = None if submitted is None else ScientificMeasurement.from_dict(submitted).to_dict()
                _need(actual == submitted, "accepted hypothesis differs from its actual submitted candidate")
        self.batches[identity] = (row, payload)

    def tip(self, value, chain, hypothesis_id):
        hypothesis = self.fact("HypothesisProposed", "hypothesis_id", hypothesis_id)
        outcome = self.fact("CanonicalOutcomeAccepted", "outcome_id", value["outcome_id"])
        spec = self.fact("ExperimentPreregistered", "experiment_id", value["experiment_id"])
        _need(spec["hypothesis_id"] == hypothesis_id and outcome["experiment_id"] == spec["experiment_id"]
              and outcome["execution_id"] == spec["execution_id"], "selected tip causal facts differ")
        _need(len({fact["component_id"] for fact in (hypothesis, spec, outcome)}) == 1
              and len({fact["method_family_id"] for fact in (hypothesis, spec, outcome)}) == 1, "selected tip component/family differs")
        metric = spec.get("measurement") or ({"metric": spec["metric"], "comparison": "VALUE"} if spec["result_kind"] == "metric" else None)
        expected = {"run_id": self.run_id, "component_id": hypothesis["component_id"], "expert_slot": hypothesis["proposer_role"],
            "chain_id": chain, "node_id": hypothesis_id, "experiment_id": spec["experiment_id"], "outcome_id": outcome["outcome_id"],
            "outcome_fact_id": outcome["fact_id"], "artifact_id": outcome["artifact_id"], "method_family_id": hypothesis["method_family_id"],
            "outcome": outcome["outcome"], "result_kind": outcome["result_kind"], "metric_value": outcome.get("metric_value"), "measurement": metric}
        _need(dict(value) == expected, "stored compared tip does not equal its exact raw accepted facts")
        return outcome

    def analyses(self, component, role, cycle, chain, iteration):
        found = []
        for identity, observation in self.observations.items():
            request = observation.get("request", {})
            key = (request.get("task_context") or {}).get("key", {})
            if (key.get("component_id"), key.get("expert_slot"), key.get("cycle"), key.get("chain"), key.get("experiment_ordinal"), key.get("node_kind")) == (
                    component, role, cycle, chain, iteration, "RESULT_ANALYSIS"):
                row = self.attempts[identity]
                if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
                    found.append(self.source(identity, "RESULT_ANALYSIS"))
        return found

    def adjudication(self, row, payload):
        _need(row["command_type"] == "ACCEPT_ADJUDICATION" and row["workflow_kind"] == "RUN"
              and row["workflow_id"] == row["run_id"] == self.run_id, "adjudication is not a run-level accepted control")
        _, request, response, key = self.source(payload["agent_attempt_id"], "ADJUDICATE")
        self.owner(payload, request)
        accepted = payload["adjudication"]
        identity = (request["component_id"], request["role"], key["cycle"])
        _need(identity not in self.selections and identity in self.batches, "adjudication has no unique accepted batch")
        batch_row, batch = self.batches[identity]
        chains = batch["hypothesis_batch"]["chains"]
        _need(payload["node_command_id"] == request["command_id"] and payload["agent_output_digest"] == response["output_digest"]
              and accepted["cycle"] == key["cycle"] and accepted["batch_command_id"] == batch["node_command_id"], "adjudication source or batch identity differs")
        value = _candidate(accepted["candidate"])
        _need(value == _candidate(response["typed_candidate"]), "accepted adjudication differs from actual Agent output")
        context = _input(request, "--batch-context")
        tips = context["terminal_tips"]
        _need(context["chains"] == chains and context["cycle"] == key["cycle"] and context["batch_command_id"] == batch["node_command_id"]
              and set(tips) == set(chains), "adjudication input does not contain the exact accepted batch")
        verdicts = {item["chain_id"]: item for item in value["verdicts"]}
        _need(set(verdicts) == set(chains), "adjudication omits or adds a compared chain")
        refs = set(request.get("immutable_context_refs", ())) | set(chains.values())
        for chain, tip in tips.items():
            if tip is None:
                _need(self.final_failures(identity, chain=chain, before=self.rank(row)), "null tip lacks its own accepted final failure")
                continue
            self.tip(tip, chain, chains[chain])
            refs.update((tip["experiment_id"], tip["outcome_id"], tip["outcome_fact_id"], tip["artifact_id"]))
            creation = [(r, p) for r, p in self.transitions if r["command_type"] == "CREATE_EXPERIMENT" and r["workflow_id"] == tip["experiment_id"]]
            _need(len(creation) == 1 and self.rank(creation[0][0]) < self.rank(row), "tip experiment was not accepted before adjudication")
            iteration = creation[0][1]["chain_iteration"]
            own_creations = [(r, p) for r, p in self.transitions if r["command_type"] == "CREATE_EXPERIMENT"
                and p.get("hypothesis_id") == chains[chain] and self.rank(r) < self.rank(row)]
            _need(max(own_creations, key=lambda item: self.rank(item[0]))[0]["workflow_id"] == tip["experiment_id"], "adjudication uses a superseded scientific/repair tip")
            analyses = self.analyses(*identity, chain, iteration)
            if not analyses:
                failed = [item for item in self.final_failures(identity, kind="RESULT_ANALYSIS", chain=chain, before=self.rank(row))
                          if item[2]["task_context"]["key"].get("experiment_ordinal") == iteration]
                _need(len(failed) == 1, "terminal chain lacks successful or actual final-failed Result Analysis")
                _, source, analysis_request = failed[0]
                analysis_response = None
            else:
                _need(len(analyses) == 1, "terminal chain lacks its unique successful Result Analysis")
                source, analysis_request, analysis_response, _ = analyses[0]
            observed = _input(analysis_request, "--current-outcome")["payload"]
            _need(observed["outcome_id"] == tip["outcome_id"] and source["ended_at"] <= self.attempts[payload["agent_attempt_id"]]["started_at"], "terminal analysis refers to another outcome or occurred after the judge")
            successor_failures = [item for item in self.final_failures(identity, chain=chain, before=self.rank(row))
                if item[2]["command"] in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"}
                and item[2]["task_context"]["key"].get("experiment_ordinal") == iteration + 1
                and item[1]["started_at"] >= source["ended_at"]
                and _input(item[2], "--iteration-context") == {
                    "iteration_of_experiment_id": tip["experiment_id"], "analysis_attempt_id": source["attempt_id"]}]
            _need(analysis_response is None or analysis_response["typed_candidate"].get("iteration_disposition") == "COMPLETE" or iteration >= self.limits(request["role"])[1]
                  or self.exhausted(request["role"], self.rank(row)) or bool(successor_failures), "chain is not complete and still has scientific iterations")
            _need(any(fact["fact_type"] == "HypothesisAssessed" and fact["hypothesis_id"] == chains[chain]
                      and self.fact_order.get(fact["fact_id"], float("inf")) < self.rank(row) for fact in self.facts.values()), "terminal chain lacks an accepted hypothesis assessment")
        for chain, verdict in verdicts.items():
            tip = tips[chain]
            expected = {"SUCCESS": "verified", "CLEAN_REFUTATION": "refuted"}.get(None if tip is None else tip["outcome"], "inconclusive")
            _need(verdict["node_id"] == chains[chain] and verdict["status"] == expected, "verdict changes accepted science; operational failure is not refutation")
            _need(set(verdict["evidence"]) <= refs and (tip is None or tip["outcome"] not in _COMPARABLE or tip["outcome_id"] in verdict["evidence"]), "verdict cites an unknown or different canonical outcome")
        keep, prune = set(value["keep"]), set(value["prune"])
        best = value["best_chain"]
        _need(not keep.intersection(prune) and keep | prune == set(chains) and (best is None or best in keep), "keep/prune/best do not cover the exact batch")
        chosen = None if best is None else tips[best]
        _need((not prune or chosen is not None) and (chosen is None or chosen["outcome"] in _COMPARABLE), "selected winner is not an accepted comparable tip")
        _need(accepted["selected_tip"] == chosen, "stored selection differs from the declared best chain")
        pruned = [{"loser_chain": chain, "loser_hypothesis_id": chains[chain], "winner_chain": best,
            "winner_hypothesis_id": chains[best], "source_attempt_id": payload["agent_attempt_id"], "debate_id": None,
            "score_refs": sorted(verdicts[chain]["evidence"])} for chain in sorted(prune)]
        _need(accepted["pruned_by"] == pruned, "pruning control has false winner/loser/source references")
        self.selections[identity] = (row, accepted)


def audit_batches(run_id: str, attempts: Sequence[Mapping[str, Any]], attempt_payloads: Mapping[str, Mapping[str, Any]],
                  facts: Sequence[Mapping[str, Any]], fact_payloads: Mapping[str, Mapping[str, Any]],
                  transitions: Sequence[Mapping[str, Any]], commands: Sequence[Mapping[str, Any]] = (), *, workflow_version=None) -> list[BatchIssue]:
    """Audit ACI14 batches; the main Oracle supplies existing raw SQL rows."""
    issues = []
    try:
        audit = _Audit(run_id, attempts, attempt_payloads, facts, fact_payloads, transitions, commands)
        success_stop = shared_goal_stop(facts, fact_payloads, transitions, workflow_version)
    except (KeyError, TypeError, ValueError) as exc:
        return [("batch_source_invalid", str(exc), (run_id,))]
    for row, payload in sorted(audit.transitions, key=lambda item: audit.rank(item[0])):
        code = "batch_source_invalid" if "hypothesis_batch" in payload else "batch_iteration_invalid" if row["command_type"] == "CREATE_EXPERIMENT" else "batch_adjudication_invalid"
        try:
            if "hypothesis_batch" in payload:
                audit.batch(row, payload)
            elif "adjudication" in payload:
                audit.adjudication(row, payload)
            elif row["command_type"] == "CREATE_EXPERIMENT":
                audit.creation(row, payload)
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            issues.append((code, str(exc), (_identity(row),)))
    for row, payload in audit.transitions:
        if row["command_type"] != "ACCEPT_HYPOTHESIS":
            continue
        source_id = payload.get("agent_attempt_id")
        request = audit.observations.get(source_id, {}).get("request", {})
        if request.get("schema_version", 0) >= 14 and not any(batch[1]["agent_attempt_id"] == source_id for batch in audit.batches.values()):
            issues.append(("batch_source_invalid", "accepted hypothesis has no complete accepted batch source", (str(row["fact_id"]),)))
    for attempt_id in audit.observations:
        try:
            audit.unselected_successor(attempt_id)
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            issues.append(("batch_source_invalid", str(exc), (str(attempt_id),)))
        try:
            audit.dependencies(attempt_id)
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            issues.append(("batch_dependency_binding_invalid", str(exc), (str(attempt_id),)))
    run_rows = [row for row, _ in audit.transitions if row["workflow_kind"] == "RUN"]
    if run_rows and max(run_rows, key=audit.rank).get("state") == "COMPLETED" and success_stop is None:
        latest = {(component, role): max(key[2] for key in audit.batches if key[:2] == (component, role)) for component, role, _ in audit.batches}
        for (component, role), cycle in latest.items():
            try:
                head = max(run_rows, key=audit.rank)
                ended = audit.cycle_close((component, role, cycle), before=audit.rank(head), terminal=True)
                _need(ended <= _time(head["created_at"]), "run completed before its latest expert cycle closed")
            except (KeyError, TypeError, ValueError, StopIteration) as exc:
                issues.append(("batch_closure_invalid", str(exc), (component, role, str(cycle))))
    return issues
