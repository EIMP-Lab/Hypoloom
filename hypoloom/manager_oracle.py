"""Independent read-only observations of accepted Grounding manager actions.

No manager admission, Host dispatch or plugin writer is imported. The existing
Oracle's raw Git reader is reused; accepted rows remain the only control source.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .batch_oracle import _input, _need, _payload, _time, shared_goal_stop
from .expert_context_oracle import _commit_files, _component, _git

ManagerIssue = tuple[str, str, tuple[str, ...]]
_SKILLS = {"leader-trigger", "debate-trigger", "debate-judge", "graph-evolve", "final-report", "self-evolve"}


class _ManagerAudit:
    def __init__(self, root, attempts, observations, facts, payloads, transitions, workflow_version=None):
        self.root = Path(root)
        self.attempts = {str(row["attempt_id"]): dict(row) for row in attempts}
        self.observations = observations
        self.transitions = [(dict(row), _payload(row)) for row in transitions]
        goals = [payloads[str(row["fact_id"])] for row in facts if row["fact_type"] == "GoalDefined"]
        _need(len(goals) == 1, "manager audit requires one accepted GoalDefined")
        self.run_id = _component(goals[0]["run_id"])
        self.contract = goals[0]["research_contract"]
        product = self.contract.get("methodology", {}).get("controls", {}).get("product", {})
        experts = product.get("experts", {})
        self.maximum = {}
        for role in self.contract["roles"]:
            settings = {**experts.get("defaults", {}), **next((item for item in experts.get("experts", ()) if item.get("name", item.get("id")) == role["id"]), {})}
            self.maximum[role["id"]] = settings.get("max_hypotheses_per_round", product.get("methodology", {}).get("inner_loop", {}).get("max_hypotheses_per_round", 1))
        self.enabled = bool(product) and product.get("methodology", {}).get("graph_evolution", {}).get("enabled", True)
        self.git_cache = {}
        self.success_stop = shared_goal_stop(facts, payloads, transitions, workflow_version)

    def first_started(self, request):
        return min(row["started_at"] for row in self.attempts.values() if row["command_id"] == request["command_id"])

    def effective(self, started):
        result = dict(self.maximum)
        for row, value in sorted(self.transitions, key=lambda item: item[0].get("raw_order", 0)):
            if row["command_type"] != "ACCEPT_GRAPH_EVOLUTION" or _time(row["created_at"]) > started:
                continue
            for proposal in value.get("graph_evolution", ()):
                result[proposal["role"]] = proposal["effective_max_forks"]
        return result

    def source(self, attempt_id, command, *, closed=True):
        row = self.attempts[attempt_id]
        observation = self.observations[attempt_id]
        request = observation["request"]
        _need(row["kind"] == "AGENT" and row["run_id"] == request["run_id"] == self.run_id
              and row["command_id"] == request["command_id"] and request["attempt_id"] == attempt_id
              and request["command"] == command and type(request["schema_version"]) is int and request["schema_version"] >= 14,
              "manager source Attempt/run/command differs")
        if closed:
            _need(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED", "manager source is not an actual successful closed Attempt")
        return row, request, observation

    def report(self, ref, request, started):
        _need(isinstance(ref, str) and ref.endswith("--response"), "manager report reference has no actual Attempt identity")
        source_id = ref.removesuffix("--response")
        row, report_request, observation = self.source(source_id, "CYCLE_REPORT")
        key = report_request["task_context"]["key"]
        component = next(item for item in self.contract["components"] if item["id"] == report_request["component_id"])
        _need(key["node_kind"] == "CYCLE_REPORT" and key["expert_slot"] == report_request["role"] == component["owner"]
              and key["component_id"] == component["id"] and key["run_id"] == self.run_id and row["ended_at"] <= started,
              "manager report source belongs to another participant or is not yet complete")
        _need(any(item["ref"] == ref for item in request["input_artifacts"]) and ref in request["immutable_context_refs"]
              and _input(request, ref) == observation["response"], "manager report input differs from the actual complete response")
        return report_request

    def files(self, repository, commit):
        identity = (repository, commit)
        if identity not in self.git_cache:
            self.git_cache[identity] = _commit_files(repository, commit, allow_executable=True)
        return self.git_cache[identity]

    def plugin(self, request, observation, *, accepted):
        resources = _input(request, "--grounding-resources")
        _need(set(resources) == {"plugin"}, "manager Grounding resource fields differ")
        plugin = resources["plugin"]
        _need(set(plugin) == {"run_id", "role", "owner_kind", "name", "commit"}
              and (plugin["run_id"], plugin["role"], plugin["owner_kind"]) == (self.run_id, "grounding", "grounding"),
              "manager plugin belongs to another owner namespace")
        repository = self.root / self.run_id / "grounding" / "plugin"
        base = self.files(repository, plugin["commit"])

        def content(files):
            _need(json.loads(files[".claude-plugin/plugin.json"])["name"] == plugin["name"] == "hypoloom-grounding",
                  "manager plugin manifest changes its owner identity")
            _need(all(files[f"skills/{skill}/SKILL.md"].strip() for skill in _SKILLS), "manager plugin omits a wired Grounding skill")

        content(base)
        if not accepted:
            _need("plugin_update" not in observation, "failed manager has an accepted plugin update")
            return None
        update = observation["plugin_update"]
        _need(set(update) == {"base_commit", "commit"} and update["base_commit"] == plugin["commit"],
              "accepted manager changed its selected Grounding plugin base")
        content(self.files(repository, update["commit"]))
        if update["commit"] != update["base_commit"]:
            parents = _git(repository, "rev-list", "--parents", "-n", "1", update["commit"]).decode().split()
            message = _git(repository, "log", "-1", "--format=%B", update["commit"]).decode().strip()
            _need(parents == [update["commit"], update["base_commit"]]
                  and message == "Hypoloom plugin update\n\nCommand: " + request["command_id"] + "\nBase: " + update["base_commit"],
                  "Grounding Git update has another parent or logical command cause")
        return update

    def manager(self, attempt_id):
        row, request, observation = self.source(attempt_id, "GRAPH_EVOLVE", closed=False)
        task = request["task_context"]
        _need(request["role"] == request["component_id"] == "grounding" and task["kind"] == "AUXILIARY"
              and task["request_kind"] == "GRAPH_EVOLVE" and task["run_id"] == self.run_id
              and task.get("component_id") is None and task.get("expert_slot") is None
              and type(task.get("cycle")) is int and task["cycle"] >= 1, "manager is not its run-scoped Grounding auxiliary node")
        context = _input(request, "--manager-context")
        for previous in self.attempts.values():
            if previous["command_id"] != request["command_id"] or previous["attempt_ordinal"] >= row["attempt_ordinal"]:
                continue
            old = self.observations[previous["attempt_id"]]
            if previous["terminal_reason"] == "LOST" or old.get("retry_class") == "INFRASTRUCTURE":
                _need(all(_input(old["request"], suffix) == _input(request, suffix)
                    for suffix in ("--manager-context", "--grounding-resources")),
                    "infrastructure retry changed its frozen manager context or Grounding plugin")
        started = self.first_started(request)
        _need(context["max_hypotheses_per_round"] == self.maximum and context["effective_max_forks"] == self.effective(started)
              and type(context["enabled"]) is bool and context["enabled"] == self.enabled,
              "manager frozen limits differ from the admitted Goal and prior accepted controls")
        refs = context["source_report_refs"]
        _need(isinstance(refs, list) and refs and all(isinstance(ref, str) for ref in refs) and len(set(refs)) == len(refs),
              "manager requires unique actual completed report references")
        reports = [self.report(ref, request, started) for ref in refs]
        triggers = [(effect, value) for effect, value in self.transitions if effect["command_type"] == "REQUEST_RESEARCH_NODE"
            and effect["workflow_kind"] == "RUN" and effect["workflow_id"] == self.run_id
            and effect["revision"] == task["ordinal"] and any(effect["command_id"] == report["command_id"]
                and value.get("task_context") == report["task_context"] and report["task_context"]["key"]["cycle"] == task["cycle"] for report in reports)]
        _need(len(triggers) == 1, "manager trigger is not its actual report's durable request revision/cycle")
        accepted = row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
        update = self.plugin(request, observation, accepted=accepted)
        effects = [(effect, value) for effect, value in self.transitions if effect["command_type"] == "ACCEPT_GRAPH_EVOLUTION"
            and (value.get("agent_attempt_id") == attempt_id or value.get("node_command_id") == request["command_id"])]
        if not accepted:
            _need(not any(value.get("agent_attempt_id") == attempt_id for _, value in effects), "failed/open manager was accepted as a control")
            return
        _need(len(effects) == 1, "successful manager has no unique accepted graph evolution")
        effect, value = effects[0]
        response = observation["response"]
        candidate = response["typed_candidate"]
        _need(candidate.get("task_kind") == "GRAPH_EVOLVE" and isinstance(candidate.get("summary"), str) and candidate["summary"].strip(), "manager response is not a graph evolution proposal")
        _need(set(candidate["supporting_refs"]) <= set(request["immutable_context_refs"]), "manager proposal cites a source outside its actual inputs")
        proposals = candidate["graph_evolution"]
        _need(isinstance(proposals, list) and (self.enabled or not proposals), "disabled manager accepted graph changes")
        seen = set()
        for proposal in proposals:
            _need(set(proposal) == {"role", "effective_max_forks", "reason"}, "graph evolution proposal fields differ")
            role, count = proposal["role"], proposal["effective_max_forks"]
            _need(role in self.maximum and role not in seen and type(count) is int and 1 <= count <= self.maximum[role]
                  and isinstance(proposal["reason"], str) and proposal["reason"].strip() == proposal["reason"] and proposal["reason"],
                  "graph evolution has an unknown/duplicate role, invalid bound or empty reason")
            seen.add(role)
        _need(effect["run_id"] == effect["workflow_id"] == self.run_id and effect["workflow_kind"] == "RUN"
              and value["agent_attempt_id"] == attempt_id and value["node_command_id"] == request["command_id"]
              and value["agent_output_digest"] == response["output_digest"] and value["graph_evolution"] == proposals
              and value["source_report_refs"] == refs and value["plugin_update"] == update
              and _time(effect["created_at"]) >= row["started_at"], "accepted manager control differs from its actual output/report/plugin sources")

    def batch(self, attempt_id):
        observation = self.observations[attempt_id]
        request = observation.get("request", {})
        key = (request.get("task_context") or {}).get("key", {})
        if key.get("node_kind") != "HYPOTHESIS_PROPOSAL" or key.get("subkind") != "SYNTHESIS" or request.get("schema_version", 0) < 14:
            return
        started = self.first_started(request)
        effects = [row for row, _ in self.transitions if row["command_type"] == "ACCEPT_GRAPH_EVOLUTION" and _time(row["created_at"]) <= started]
        if not effects:
            return  # This module does not reinterpret pre-manager batch admission.
        count = self.effective(started)[request["role"]]
        _need(_input(request, "--expert-config")["effective_max_forks"] == count, "future batch did not freeze its accepted manager fork count")
        row = self.attempts[attempt_id]
        if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED":
            hypotheses = observation["response"]["typed_candidate"]["hypotheses"]
            _need(1 <= len(hypotheses) <= count, "future batch exceeds the accepted effective fork count")

    def closure(self):
        if not self.enabled:
            return
        completed = [_time(row["created_at"]) for row, _ in self.transitions
            if row.get("workflow_kind") == "RUN" and row.get("state") in {"COMPLETING", "COMPLETED"} and self.success_stop is None]
        for identity, observation in self.observations.items():
            request = observation.get("request", {})
            row = self.attempts[identity]
            if (request.get("command") != "CYCLE_REPORT" or request.get("schema_version", 0) < 14
                    or row["state"] != "CLOSED" or row["terminal_reason"] != "SUCCEEDED"):
                continue
            key = request["task_context"]["key"]
            consumers = [item["started_at"] for other, item in self.attempts.items() if item["kind"] == "AGENT"
                if (lambda req: req.get("role") == request["role"] and req.get("component_id") == request["component_id"]
                    and (req.get("command") == "SELF_EVOLVE" and req.get("task_context", {}).get("key", {}).get("cycle") == key["cycle"]
                        or req.get("command") == "LITERATURE_REVIEW" and req.get("task_context", {}).get("key", {}).get("cycle", 0) > key["cycle"]))(
                            self.observations[other].get("request", {}))]
            deadlines = consumers + completed
            if not deadlines:
                continue  # The report can legitimately await its manager in an ACTIVE run.
            trigger = [effect for effect, _ in self.transitions if effect["command_type"] == "REQUEST_RESEARCH_NODE"
                and effect["workflow_id"] == self.run_id and effect["command_id"] == request["command_id"]]
            _need(len(trigger) == 1, "closed report has no unique manager trigger revision")
            sources = [(item, value) for other, item in self.attempts.items() if item["kind"] == "AGENT" for value in [self.observations[other]]
                if value.get("request", {}).get("command") == "GRAPH_EVOLVE"
                and value["request"]["task_context"].get("ordinal") == trigger[0]["revision"]
                and value["request"]["task_context"].get("cycle") == key["cycle"]]
            succeeded = [item["ended_at"] for item, _ in sources if item["state"] == "CLOSED" and item["terminal_reason"] == "SUCCEEDED"]
            failed = []
            for effect, payload in self.transitions:
                if payload.get("agent_final_failure") is not True:
                    continue
                matched = [(item, value) for item, value in sources if item["attempt_id"] == payload.get("attempt_id")
                    and item["command_id"] == payload.get("failed_task_command_id")]
                if matched:
                    _need(matched[0][0]["state"] == "CLOSED" and matched[0][0]["terminal_reason"] != "SUCCEEDED", "manager final failure has no failed source Attempt")
                    failed.append(_time(effect["created_at"]))
            _need(succeeded + failed and min(succeeded + failed) <= min(deadlines),
                  "expert Self Evolve/next round/completion preceded its required manager success or final failure")


def audit_manager(state_root: Path, attempts: Sequence[Mapping[str, Any]], attempt_payloads: Mapping[str, Mapping[str, Any]],
                  facts: Sequence[Mapping[str, Any]], fact_payloads: Mapping[str, Mapping[str, Any]],
                  transitions: Sequence[Mapping[str, Any]], *, workflow_version=None) -> list[ManagerIssue]:
    """Observe ACI14 manager/report/Git causation without changing run state."""
    manager_ids = [str(row["attempt_id"]) for row in attempts if row["kind"] == "AGENT"
        and attempt_payloads.get(str(row["attempt_id"]), {}).get("request", {}).get("command") == "GRAPH_EVOLVE"]
    controls = [dict(row) for row in transitions if dict(row).get("command_type") == "ACCEPT_GRAPH_EVOLUTION"]
    issues = []
    try:
        audit = _ManagerAudit(state_root, attempts, attempt_payloads, facts, fact_payloads, transitions, workflow_version)
    except (KeyError, TypeError, ValueError) as exc:
        return [("manager_source_invalid", str(exc), ())]
    for row in controls:
        source = _payload(row).get("agent_attempt_id")
        if source not in manager_ids:
            issues.append(("manager_source_invalid", "accepted graph evolution lacks its actual manager source", (str(source),)))
    for identity in audit.observations:
        try:
            if identity in manager_ids:
                audit.manager(identity)
            else:
                audit.batch(identity)
        except (KeyError, ValueError, TypeError, AttributeError, StopIteration, OSError, subprocess.SubprocessError) as exc:
            issues.append(("manager_source_invalid" if identity in manager_ids else "manager_batch_binding_invalid", str(exc), (str(identity),)))
    try:
        audit.closure()
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(("manager_closure_invalid", str(exc), (audit.run_id,)))
    return issues


__all__ = ["audit_manager"]
