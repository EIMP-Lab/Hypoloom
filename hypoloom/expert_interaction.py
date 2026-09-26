"""Pure interaction proposals over accepted facts and existing Agent Attempts.

These readers do not dispatch work or accept science. Host owns the transaction
that records a selection/control, and the independent Oracle audits its effects.
"""
from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping
from uuid import UUID

from .expert_loop import (
    AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeTask, _chain, _exact,
    _freeze_json, _text, _thaw_json, _unique, compared_tip, pruning_relation,
    task_context_from_dict,
)
from .participant_controls import ParticipantScope


_FACT_ENTITY_FIELDS = {
    "GoalDefined": ("goal_id",), "HypothesisProposed": ("hypothesis_id",),
    "ExperimentPreregistered": ("experiment_id",),
    "CanonicalOutcomeAccepted": ("outcome_id", "artifact_id"),
    "EvidenceLinked": ("evidence_id",), "HypothesisAssessed": ("assessment_id",),
    "GoalVerdictRecorded": ("verdict_id",),
}


def expand_fact_references(payloads: Iterable[Mapping[str, Any]], references: Iterable[str]) -> frozenset[str]:
    """Interpret a fact citation as its own entities, never its foreign keys.

    This is a read-only interpretation of accepted payloads. Keep the original
    references in the proposal and durable event so the Agent's citation is exact.
    """
    refs = frozenset(references)
    return refs | frozenset(payload[field] for payload in payloads if payload["fact_id"] in refs
        for field in _FACT_ENTITY_FIELDS.get(payload["fact_type"], ()) if payload.get(field) is not None)


def _references(value: Any, allowed: Iterable[str], field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result = _unique(value, field)
    if not set(result) <= set(allowed):
        raise ValueError(f"{field} cites outside the accepted reference domain")
    return tuple(result)


@dataclass(frozen=True)
class LeaderContribution:
    role: str
    component_id: str
    tier: str
    hypothesis_id: str
    qualification_refs: tuple[str, ...]


@dataclass(frozen=True)
class InteractionFacts:
    run_id: str
    goal_id: str
    contract: Any
    rows: tuple[Mapping[str, Any], ...]
    agent_aci_version: int = 14

    @classmethod
    def from_rows(cls, run_id: str, rows: Iterable[Mapping[str, Any]], *, agent_aci_version: int = 14) -> InteractionFacts:
        from .domain import ResearchContract, ResearchFactType

        _text(run_id, "run_id")
        payloads = []
        for row in rows:
            payload = json.loads(row["payload_json"]) if "payload_json" in row else row.get("payload", row)
            if not isinstance(payload, Mapping) or payload.get("run_id") != run_id:
                raise ValueError("interaction fact belongs to another run")
            for field in ("fact_id", "fact_type", "run_id"):
                if row.get(field) != _text(payload.get(field), field):
                    raise ValueError("accepted fact row and payload differ")
            ResearchFactType(payload["fact_type"])
            payloads.append(_freeze_json(json.loads(json.dumps(payload))))
        _unique((item["fact_id"] for item in payloads), "accepted fact identities")
        goals = [item for item in payloads if item["fact_type"] == "GoalDefined"]
        if len(goals) != 1:
            raise ValueError("interaction requires one accepted shared goal")
        return cls(run_id, goals[0]["goal_id"], ResearchContract.from_mapping(_thaw_json(goals[0]["research_contract"])), tuple(payloads), agent_aci_version)

    def index(self, kind: str, field: str) -> dict[str, Mapping[str, Any]]:
        rows = [item for item in self.rows if item["fact_type"] == kind]
        _unique((item.get(field) for item in rows), f"accepted {kind} identities")
        return {item[field]: item for item in rows}

    @property
    def reference_ids(self) -> frozenset[str]:
        fields = ("fact_id", "goal_id", "hypothesis_id", "experiment_id", "outcome_id", "artifact_id", "evidence_id", "assessment_id", "verdict_id")
        return frozenset(item[field] for item in self.rows for field in fields if item.get(field) is not None)

    def hypothesis(self, identity: str, scope: ParticipantScope | None = None) -> Mapping[str, Any]:
        _text(identity, "hypothesis reference")
        hypothesis = self.index("HypothesisProposed", "hypothesis_id").get(identity)
        if hypothesis is None or hypothesis.get("goal_id") != self.goal_id:
            raise ValueError("interaction hypothesis is outside the accepted shared goal")
        component = self.contract.component(hypothesis["component_id"])
        if component is None or component.owner_role != hypothesis.get("proposer_role"):
            raise ValueError("interaction hypothesis is not owned by its declared component owner")
        if scope is not None and (scope.role, scope.component_id) != (component.owner_role, component.component_id):
            raise ValueError("participant does not own the exact accepted hypothesis")
        return hypothesis

    def leader_qualifications(self) -> tuple[LeaderContribution, ...]:
        outcomes = self.index("CanonicalOutcomeAccepted", "outcome_id")
        specs = self.index("ExperimentPreregistered", "experiment_id")
        evidence = self.index("EvidenceLinked", "evidence_id")
        assessments = self.index("HypothesisAssessed", "assessment_id")
        qualified = []
        for hypothesis_id in sorted(self.index("HypothesisProposed", "hypothesis_id")):
            hypothesis = self.hypothesis(hypothesis_id)
            component = self.contract.component(hypothesis["component_id"])
            tips = {}
            for outcome_id, outcome in outcomes.items():
                spec = specs.get(outcome["experiment_id"])
                if spec is not None and spec["hypothesis_id"] == hypothesis_id:
                    tips[outcome_id] = compared_tip("accepted", hypothesis=hypothesis, preregistration=spec, outcome=outcome)
            refs = {identity for identity, tip in tips.items() if tip.outcome == "SUCCESS"} if self.agent_aci_version < 21 else set()
            for assessment_id, assessment in assessments.items():
                if assessment["hypothesis_id"] != hypothesis_id or assessment.get("assessment") != "SUPPORTED":
                    continue
                links = assessment.get("evidence_ids", ())
                if not links:
                    raise ValueError("SUPPORTED qualification requires actual accepted evidence")
                for row in (assessment, *(evidence.get(identity, {}) for identity in links)):
                    if any(row.get(field) != hypothesis[field] for field in ("hypothesis_id", "component_id", "method_family_id")):
                        raise ValueError("SUPPORTED qualification has missing or foreign evidence")
                    if row is assessment:
                        continue
                    tip = tips.get(row.get("outcome_id"))
                    if tip is None or not tip.comparable or row.get("artifact_id") != tip.artifact_id:
                        raise ValueError("SUPPORTED evidence requires its actual comparable accepted outcome")
                refs.add(assessment_id)
            if self.agent_aci_version >= 21:
                for evidence_id, link in evidence.items():
                    tip = tips.get(link.get("outcome_id"))
                    if (tip is None or not tip.comparable or tip.outcome not in {"SUCCESS", "CLEAN_REFUTATION"}
                            or link.get("artifact_id") != tip.artifact_id
                            or any(link.get(field) != hypothesis[field] for field in
                                ("hypothesis_id", "component_id", "method_family_id"))):
                        continue
                    matched = [identity for identity, assessment in assessments.items()
                        if assessment.get("assessment") == ("SUPPORTED" if tip.outcome == "SUCCESS" else "REFUTED") and evidence_id in assessment.get("evidence_ids", ())
                        and all(assessment.get(field) == hypothesis[field] for field in
                            ("hypothesis_id", "component_id", "method_family_id"))]
                    if matched:
                        refs.update((evidence_id, link["outcome_id"], *matched))
            if refs:
                qualified.append(LeaderContribution(component.owner_role, component.component_id, component.tier.value, hypothesis_id, tuple(sorted(refs))))
        return tuple(qualified)


@dataclass(frozen=True)
class LeaderSelection:
    role: str
    contributions: tuple[LeaderContribution, ...]


def select_interaction_facts(rows: Iterable[Mapping[str, Any]], *, hypothesis_ids: Iterable[str],
                             reference_ids: Iterable[str] = ()) -> list[Mapping[str, Any]]:
    """Keep exact current/cited facts and their dependencies, not old payloads.

    Keep the immediate parents' evidence for baseline comparisons. Older
    hypotheses retain lineage definitions and explicitly cited evidence; existing
    cycle reports supply the historical narrative.
    This disposable view never changes, summarizes or accepts a scientific fact.
    """
    rows = list(rows)
    payloads = {row["fact_id"]: json.loads(row["payload_json"]) if "payload_json" in row
        else row.get("payload", row) for row in rows}
    index = {identity: {identity} for identity in payloads}
    for identity, payload in payloads.items():
        for field in _FACT_ENTITY_FIELDS.get(payload["fact_type"], ()):
            if isinstance(payload.get(field), str):
                index.setdefault(payload[field], set()).add(identity)
    current = set(hypothesis_ids)
    hypotheses = set(current)
    hypotheses.update(p["parent_hypothesis_id"] for p in payloads.values()
        if p["fact_type"] == "HypothesisProposed" and p.get("hypothesis_id") in current
        and p.get("parent_hypothesis_id"))
    experiments = {p["experiment_id"] for p in payloads.values()
        if p["fact_type"] == "ExperimentPreregistered" and p.get("hypothesis_id") in hypotheses}
    selected = {identity for identity, payload in payloads.items()
        if payload["fact_type"] == "GoalDefined" or payload.get("hypothesis_id") in hypotheses
        or payload.get("experiment_id") in experiments}
    for ref in reference_ids:
        selected.update(index.get(ref, ()))
    pending = list(selected)
    while pending:
        payload = payloads[pending.pop()]
        if payload["fact_type"] == "GoalDefined":
            continue
        refs = [payload.get(field) for field in ("hypothesis_id", "parent_hypothesis_id", "experiment_id", "outcome_id", "artifact_id")]
        refs.extend(payload.get("evidence_ids", ()))
        refs.extend(payload.get("supporting_refs", ()))
        for ref in refs:
            if not isinstance(ref, str): continue
            for identity in index.get(ref, set()) - selected:
                selected.add(identity)
                pending.append(identity)
    return [row for row in rows if row["fact_id"] in selected]


@dataclass(frozen=True)
class DebateParticipant:
    scope: ParticipantScope
    hypothesis_id: str


@dataclass(frozen=True)
class DebateProposal:
    participants: tuple[DebateParticipant, ...]
    topic: str


@dataclass(frozen=True)
class ObserverProposal:
    action: str
    rationale: str
    supporting_refs: tuple[str, ...]
    leader: LeaderSelection | None
    debate: DebateProposal | None

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "rationale": self.rationale, "supporting_refs": list(self.supporting_refs),
                "leader": None if self.leader is None else self.leader.role,
                "debate": None if self.debate is None else {"participants": [asdict(item) for item in self.debate.participants], "topic": self.debate.topic}}

    def control_payload(self, source_attempt_id: str) -> dict[str, Any]:
        return {"expert_interaction": {"action": {"none": "observed", "leader": "leader_selected", "debate": "debate_proposed"}[self.action],
                "source_attempt_id": _text(source_attempt_id, "source Attempt"), "proposal": self.to_dict()}}


def admit_observer_proposal(value: Mapping[str, Any], *, facts: InteractionFacts) -> ObserverProposal:
    _exact(value, {"action", "rationale", "supporting_refs", "leader", "debate"}, "Observer proposal")
    action = _text(value["action"], "Observer action")
    if action not in {"none", "leader", "debate"}:
        raise ValueError("unsupported Observer action")
    refs = _references(value["supporting_refs"], facts.reference_ids, "Observer references")
    leader, debate = None, None
    if action == "leader":
        cited = expand_fact_references(facts.rows, refs)
        role = _text(value["leader"], "Leader role")
        contributions = tuple(item for item in facts.leader_qualifications() if item.role == role)
        if not contributions or not cited.intersection(ref for item in contributions for ref in item.qualification_refs):
            raise ValueError("Leader selection must cite its actual own verified contribution")
        cited_components = {item.component_id for item in contributions if cited.intersection(item.qualification_refs)}
        if facts.agent_aci_version < 21 and len(cited_components) != 1:
            raise ValueError("Leader references must select exactly one qualified component")
        if facts.agent_aci_version >= 21:
            cited_hypotheses = [identity for identity, hypothesis in facts.index("HypothesisProposed", "hypothesis_id").items()
                if identity in cited and hypothesis["proposer_role"] == role]
            parents = [item for item in contributions if item.hypothesis_id in cited_hypotheses]
            if len(cited_hypotheses) != 1 or len(parents) != 1 or not cited.intersection(parents[0].qualification_refs):
                raise ValueError("Leader must cite exactly one own hypothesis and its verified contribution")
            contributions = tuple(parents)
        leader = LeaderSelection(role, contributions)
    elif value["leader"] is not None:
        raise ValueError("only Leader selection can name a Leader")
    if action == "debate":
        raw = value["debate"]
        _exact(raw, {"participants", "topic"}, "Debate proposal")
        if not isinstance(raw["participants"], list) or len(raw["participants"]) != 2:
            raise ValueError("Debate requires exactly two experts")
        participants = []
        for item in raw["participants"]:
            _exact(item, {"scope", "hypothesis_id"}, "Debate participant")
            scope = ParticipantScope.from_dict(item["scope"])
            facts.hypothesis(item["hypothesis_id"], scope)
            participants.append(DebateParticipant(scope, item["hypothesis_id"]))
        _unique((item.scope.role for item in participants), "Debate experts")
        debate = DebateProposal(tuple(participants), _text(raw["topic"], "Debate topic"))
    elif value["debate"] is not None:
        raise ValueError("only Debate selection can name debaters")
    return ObserverProposal(action, _text(value["rationale"], "Observer rationale"), refs, leader, debate)


def _closed(row: Mapping[str, Any], run_id: str) -> tuple[Mapping[str, Any], Any]:
    if (row.get("run_id"), row.get("kind"), row.get("state"), row.get("terminal_reason")) != (run_id, "AGENT", "CLOSED", "SUCCEEDED"):
        raise ValueError("interaction source requires its CLOSED SUCCEEDED Agent Attempt")
    observation = json.loads(row["observation_json"])
    request = observation["request"]
    task = task_context_from_dict(request["task_context"])
    if request.get("attempt_id") != row["attempt_id"] or request.get("run_id") != run_id or task.run_id != run_id or task.command_id != request.get("command_id"):
        raise ValueError("interaction request differs from its source Attempt/task")
    if isinstance(task, AuxiliaryTask):
        kind, role, component = task.kind.value, task.expert_slot, task.component_id
    else:
        kind, role, component = task.key.node_kind.value, task.key.expert_slot, task.key.component_id
    if request.get("command") != kind or role not in {None, request.get("role")} or component not in {None, request.get("component_id")}:
        raise ValueError("interaction command or participant differs from its task")
    return observation, task


def _session(observation: Mapping[str, Any]) -> tuple[str, str | None]:
    from .agent_sessions import observed_session_binding
    return observed_session_binding(observation)


def _observer(row: Mapping[str, Any], facts: InteractionFacts, *, kind=AuxiliaryRequestKind.OBSERVER) -> Mapping[str, Any]:
    observation, task = _closed(row, facts.run_id)
    if not isinstance(task, AuxiliaryTask) or task.kind is not kind:
        raise ValueError("interaction judge must use an Observer task")
    session_id, parent = _session(observation)
    if parent is not None or observation["session"].get("launch_arguments") != ["--session-id", session_id]:
        raise ValueError("Observer must start a fresh stateless session")
    return observation


def observer_from_attempt(row: Mapping[str, Any], *, facts: InteractionFacts) -> ObserverProposal:
    observation = _observer(row, facts)
    proposal = admit_observer_proposal(observation["response"]["typed_candidate"], facts=facts)
    request = observation["request"]
    if type(request.get("schema_version")) is int and request["schema_version"] >= 14:
        inputs = [item for item in request.get("input_artifacts", ()) if item["ref"].endswith("--interaction-context")]
        if len(inputs) != 1 or inputs[0]["ref"] not in request["immutable_context_refs"]:
            raise ValueError("Observer requires its one admitted interaction context")
        context = json.loads(base64.b64decode(inputs[0]["content_base64"], validate=True))
        frozen = InteractionFacts.from_rows(facts.run_id, context["facts"], agent_aci_version=observation["request"]["schema_version"])
        admit_observer_proposal(observation["response"]["typed_candidate"], facts=frozen)
        allowed = frozen.reference_ids
    else:
        allowed = request["immutable_context_refs"]
    _references(list(proposal.supporting_refs), allowed, "Observer input references")
    return proposal


@dataclass(frozen=True)
class DebateFork:
    participant: DebateParticipant
    original_attempt_id: str
    original_session_id: str
    session_id: str


@dataclass(frozen=True)
class DebateBinding:
    debate_id: str
    run_id: str
    participants: tuple[DebateFork, ...]

    @property
    def input_ref(self) -> str:
        return f"{self.debate_id}--debate-context"

    def round_context(self, role: str, number: int, previous_opponent_attempt_id: str | None) -> dict[str, Any]:
        fork = next((item for item in self.participants if item.participant.scope.role == role), None)
        if fork is None or type(number) is not int or number not in {1, 2, 3}:
            raise ValueError("unknown debater or round outside the three-round debate")
        if (number == 1) != (previous_opponent_attempt_id is None):
            raise ValueError("Debate round requires the exact preceding opponent output")
        return {"debate_id": self.debate_id, "round": number, **asdict(fork), "previous_opponent_attempt_id": previous_opponent_attempt_id}


def validate_original_debate_scope(request: Mapping[str, Any], participant: DebateParticipant) -> None:
    """Reuse the original admitted chain/hypothesis binding, not a new alias."""
    task = task_context_from_dict(request["task_context"])
    scope = participant.scope
    if (not isinstance(task, ResearchNodeTask) or (request["role"], request["component_id"], task.key.expert_slot,
            task.key.component_id, task.key.chain) != (scope.role, scope.component_id, scope.role, scope.component_id, scope.chain)):
        raise ValueError("Debate original Attempt is not bound to the exact hypothesis/chain")
    if request.get("schema_version", 0) < 14:
        bound = participant.hypothesis_id in request["immutable_context_refs"]
    else:
        bound = False
        for item in request.get("input_artifacts", ()):
            if item["ref"] not in request["immutable_context_refs"] or item.get("media_type") != "application/json":
                continue
            value = json.loads(base64.b64decode(item["content_base64"], validate=True))
            if not isinstance(value, Mapping):
                continue
            if item["ref"].endswith("--batch-context") and value.get("chains", {}).get(scope.chain) == participant.hypothesis_id:
                bound = True
            fact = value.get("payload", value)
            if (isinstance(fact, Mapping) and fact.get("fact_type", value.get("fact_type")) == "HypothesisProposed"
                    and (fact.get("hypothesis_id"), fact.get("component_id"), fact.get("proposer_role")) ==
                        (participant.hypothesis_id, scope.component_id, scope.role)):
                bound = True
    if not bound:
        raise ValueError("Debate original Attempt is not bound to the exact hypothesis/chain")


def bind_debate(debate_id: str, proposal: DebateProposal, *, facts: InteractionFacts,
                original_attempts: Iterable[Mapping[str, Any]], fork_session_ids: Mapping[str, str]) -> DebateBinding:
    _chain(debate_id)
    originals = {}
    for row in original_attempts:
        observation, task = _closed(row, facts.run_id)
        if not isinstance(task, ResearchNodeTask):
            raise ValueError("Debate must fork an actual expert research session")
        role = observation["request"]["role"]
        if role in originals:
            raise ValueError("ambiguous original expert Attempt")
        originals[role] = (row, observation, task)
    roles = {item.scope.role for item in proposal.participants}
    if len(roles) != 2 or set(originals) != roles or set(fork_session_ids) != roles:
        raise ValueError("Debate original sessions must cover exactly both participants")
    forks = []
    for participant in proposal.participants:
        facts.hypothesis(participant.hypothesis_id, participant.scope)
        row, observation, task = originals[participant.scope.role]
        validate_original_debate_scope(observation["request"], participant)
        original_id, _ = _session(observation)
        session_id = fork_session_ids[participant.scope.role]
        if not isinstance(session_id, str) or str(UUID(session_id)) != session_id:
            raise ValueError("Debate fork requires its stable actual session ID")
        forks.append(DebateFork(participant, row["attempt_id"], original_id, session_id))
    _unique((identity for fork in forks for identity in (fork.original_session_id, fork.session_id)), "Debate original/fork sessions")
    return DebateBinding(debate_id, facts.run_id, tuple(forks))


@dataclass(frozen=True)
class DebateStatement:
    position: str
    arguments: tuple[str, ...]
    evidence: tuple[str, ...]
    concessions: tuple[str, ...]
    web_sources: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"position": self.position, "arguments": list(self.arguments), "evidence": list(self.evidence),
            "concessions": list(self.concessions), **({"web_sources": list(self.web_sources)} if self.web_sources is not None else {})}


def admit_debate_statement(value: Mapping[str, Any], *, reference_ids: Iterable[str]) -> DebateStatement:
    expected = {"position", "arguments", "evidence", "concessions"}
    _exact(value, expected | ({"web_sources"} if "web_sources" in value else set()), "Debate statement")
    for field in ("arguments", "concessions"):
        if not isinstance(value[field], list) or (field == "arguments" and not value[field]):
            raise ValueError("Debate requires arguments and an array of concessions")
        for item in value[field]:
            _text(item, field)
    from .grounding_background import validate_web_sources
    sources = None if "web_sources" not in value else validate_web_sources(value["web_sources"], value["web_sources"])
    return DebateStatement(_text(value["position"], "Debate position"), tuple(value["arguments"]),
        _references(value["evidence"], reference_ids, "Debate evidence"), tuple(value["concessions"]), sources)


@dataclass(frozen=True)
class DebateRound:
    number: int
    participant: DebateParticipant
    source_attempt_id: str
    session_id: str
    statement: DebateStatement

    def to_dict(self) -> dict[str, Any]:
        return {"number": self.number, "participant": asdict(self.participant),
            "source_attempt_id": self.source_attempt_id, "session_id": self.session_id,
            "statement": self.statement.to_dict()}


def read_judge_rounds(context: Mapping[str, Any], *, binding: DebateBinding,
                      facts: InteractionFacts) -> tuple[DebateRound, ...]:
    """Read the existing round projection; retained requests carry full rows."""
    if ("rounds" in context) == ("round_attempts" in context):
        raise ValueError("judge requires exactly one six-output representation")
    if "round_attempts" in context:
        return read_debate_rounds(context["round_attempts"], binding=binding, facts=facts)
    values = context["rounds"]
    if not isinstance(values, list) or len(values) != 6 or facts.run_id != binding.run_id:
        raise ValueError("judge requires six outputs from its own run")
    forks = {item.participant.scope.role: item for item in binding.participants}
    refs = set(_unique((_text(item["source_attempt_id"], "round source") for item in values), "round sources"))
    allowed = facts.reference_ids | refs | {item.original_attempt_id for item in binding.participants}
    result = []
    for item in values:
        _exact(item, {"number", "participant", "source_attempt_id", "session_id", "statement"}, "judge round")
        role = item["participant"]["scope"]["role"]
        fork = forks.get(role)
        if (type(item["number"]) is not int or item["number"] not in {1, 2, 3} or fork is None
                or item["participant"] != asdict(fork.participant) or item["session_id"] != fork.session_id):
            raise ValueError("judge round changed its number, participant or fork")
        result.append(DebateRound(item["number"], fork.participant, item["source_attempt_id"], item["session_id"],
            admit_debate_statement(item["statement"], reference_ids=allowed)))
    if {(item.number, item.participant.scope.role) for item in result} != {(n, role) for n in (1, 2, 3) for role in forks}:
        raise ValueError("judge requires exactly three rounds for both participants")
    return tuple(sorted(result, key=lambda item: (item.number, item.participant.scope.role)))


def read_debate_rounds(rows: Iterable[Mapping[str, Any]], *, binding: DebateBinding, facts: InteractionFacts) -> tuple[DebateRound, ...]:
    selected, trigger_revisions = {}, set()
    if facts.run_id != binding.run_id:
        raise ValueError("Debate facts belong to another run")
    for row in rows:
        observation, task = _closed(row, binding.run_id)
        if not isinstance(task, AuxiliaryTask) or task.kind is not AuxiliaryRequestKind.DEBATE:
            raise ValueError("Debate round requires a DEBATE auxiliary task")
        request = observation["request"]
        number = task.ordinal
        if request.get("schema_version", 0) >= 14:
            inputs = [item for item in request["input_artifacts"] if item["ref"] == binding.input_ref]
            if len(inputs) != 1:
                raise ValueError("Debate requires its exact round context")
            number = json.loads(base64.b64decode(inputs[0]["content_base64"], validate=True))["round"]
            if type(number) is not int or number not in {1, 2, 3} or task.ordinal <= number or (task.ordinal - number) % 3:
                raise ValueError("Debate ordinal does not name its Observer revision and round")
            trigger_revisions.add((task.ordinal - number) // 3)
        identity = (number, request["role"])
        if identity in selected:
            raise ValueError("duplicate Debate round participant")
        selected[identity] = (row, observation)
    roles = {item.participant.scope.role for item in binding.participants}
    if len(trigger_revisions) > 1 or set(selected) != {(number, role) for number in range(1, 4) for role in roles}:
        raise ValueError("Debate requires exactly three rounds and six participant outputs")
    _unique((row["attempt_id"] for row, _ in selected.values()), "Debate source Attempts")
    results = []
    for (number, role), (row, observation) in sorted(selected.items()):
        fork = next(item for item in binding.participants if item.participant.scope.role == role)
        request = observation["request"]
        peer = next(item for item in roles if item != role)
        previous = None if number == 1 else selected[(number - 1, peer)][0]["attempt_id"]
        context = binding.round_context(role, number, previous)
        inputs = [item for item in request["input_artifacts"] if item["ref"] == binding.input_ref]
        if len(inputs) != 1 or json.loads(base64.b64decode(inputs[0]["content_base64"], validate=True)) != context:
            raise ValueError("Debate request does not bind its exact source/fork/previous round")
        modern = request.get("schema_version", 0) >= 14
        required = {fork.original_attempt_id} | (set() if modern else {fork.participant.hypothesis_id}) | ({previous} if previous else set())
        if not required <= set(request["immutable_context_refs"]) or request["component_id"] != fork.participant.scope.component_id:
            raise ValueError("Debate input lacks its own source or preceding opponent output")
        if _session(observation) != (fork.session_id, fork.original_session_id):
            raise ValueError("Debate output belongs to another fork or original session")
        launches = [["--resume", fork.session_id]]
        if number == 1:
            launches.append(["--resume", fork.original_session_id, "--fork-session", "--session-id", fork.session_id])
        if observation["session"].get("launch_arguments") not in launches:
            raise ValueError("Debate must continue its own fork between rounds")
        known = facts.reference_ids | {item.original_attempt_id for item in binding.participants} | {item.source_attempt_id for item in results}
        allowed = known.intersection(request["immutable_context_refs"])
        if modern:
            contexts = [item for item in request["input_artifacts"] if item["ref"].endswith("--interaction-context")]
            if len(contexts) != 1 or contexts[0]["ref"] not in request["immutable_context_refs"]:
                raise ValueError("Debate requires its frozen accepted scientific context")
            frozen = InteractionFacts.from_rows(binding.run_id, json.loads(base64.b64decode(contexts[0]["content_base64"], validate=True))["facts"])
            frozen.hypothesis(fork.participant.hypothesis_id, fork.participant.scope)
            allowed |= frozen.reference_ids.intersection(facts.reference_ids)
        statement = admit_debate_statement(observation["response"]["typed_candidate"], reference_ids=allowed)
        results.append(DebateRound(number, fork.participant, row["attempt_id"], fork.session_id, statement))
    return tuple(results)


@dataclass(frozen=True)
class DebateJudgment:
    binding: DebateBinding
    source_attempt_id: str
    winner: str | None
    scores: tuple[Mapping[str, Any], ...]
    keep: tuple[str, ...]
    prune: tuple[str, ...]
    rationale: str
    supporting_refs: tuple[str, ...]
    pruned_by: tuple[Mapping[str, Any], ...]

    def control_payload(self) -> dict[str, Any]:
        promoted = next((item for item in self.binding.participants if item.participant.scope.role == self.winner), None)
        return {"expert_interaction": {"action": "debate_verdict", "debate_id": self.binding.debate_id,
            "source_attempt_id": self.source_attempt_id, "participants": [asdict(item) for item in self.binding.participants],
            "winner": self.winner, "scores": _thaw_json(self.scores), "keep": list(self.keep), "prune": list(self.prune),
            "rationale": self.rationale, "supporting_refs": list(self.supporting_refs), "pruned_by": _thaw_json(self.pruned_by),
            "promote_session": None if promoted is None else {"role": self.winner,
                "original_session_id": promoted.original_session_id, "session_id": promoted.session_id}}}


def admit_debate_judgment(value: Mapping[str, Any], *, binding: DebateBinding, rounds: Iterable[DebateRound],
                          facts: InteractionFacts, source_attempt_id: str) -> DebateJudgment:
    _exact(value, {"winner", "scores", "keep", "prune", "rationale", "supporting_refs"}, "Debate judgment")
    _text(source_attempt_id, "judge source Attempt")
    roles = {item.participant.scope.role for item in binding.participants}
    outputs = tuple(rounds)
    if facts.run_id != binding.run_id or len(outputs) != 6 or {(item.number, item.participant.scope.role) for item in outputs} != {(number, role) for number in range(1, 4) for role in roles}:
        raise ValueError("judge requires the complete exact six-round output set")
    for item in outputs:
        fork = next(participant for participant in binding.participants if participant.participant.scope.role == item.participant.scope.role)
        if item.participant != fork.participant or item.session_id != fork.session_id:
            raise ValueError("judge round refers to another participant/fork")
    round_refs = set(_unique((item.source_attempt_id for item in outputs), "judged round Attempts"))
    refs = _references(value["supporting_refs"], facts.reference_ids | round_refs, "judge references")
    if not round_refs <= set(refs):
        raise ValueError("judge must cite all six actual round outputs")
    scores = {}
    if not isinstance(value["scores"], list):
        raise ValueError("judge scores must be an array")
    for score in value["scores"]:
        _exact(score, {"role", "score", "round_refs"}, "Debate score")
        role = _text(score["role"], "scored expert")
        if role not in roles or role in scores:
            raise ValueError("judge scores must cover exactly both experts")
        try:
            number = Decimal(_text(score["score"], "judge score"))
        except InvalidOperation as exc:
            raise ValueError("judge score must be finite decimal text") from exc
        if not number.is_finite():
            raise ValueError("judge score must be finite decimal text")
        own = {item.source_attempt_id for item in outputs if item.participant.scope.role == role}
        if set(_references(score["round_refs"], own, "score references")) != own:
            raise ValueError("each score must cite its three exact participant outputs")
        scores[role] = _freeze_json(score)
    if set(scores) != roles:
        raise ValueError("judge scores must cover both participants")
    keep = set(_references(value["keep"], roles, "kept experts"))
    prune = set(_references(value["prune"], roles, "pruned experts"))
    winner = value["winner"]
    if winner is not None:
        _text(winner, "winning expert")
    if keep & prune or keep | prune != roles or (winner is not None and winner not in keep) or (prune and winner is None):
        raise ValueError("judge keep/prune must partition the exact participants and keep its winner")
    if winner is not None and any(Decimal(scores[winner]["score"]) < Decimal(item["score"]) for item in scores.values()):
        raise ValueError("judge winner contradicts its own declared debate scores")
    participants = {item.participant.scope.role: item.participant for item in binding.participants}
    pruned = tuple(_freeze_json(pruning_relation(loser_chain=participants[role].scope.chain,
        loser_hypothesis_id=participants[role].hypothesis_id, winner_chain=participants[winner].scope.chain,
        winner_hypothesis_id=participants[winner].hypothesis_id, source_attempt_id=source_attempt_id,
        score_refs=scores[role]["round_refs"], debate_id=binding.debate_id)) for role in sorted(prune))
    return DebateJudgment(binding, source_attempt_id, winner, tuple(scores[role] for role in sorted(scores)), tuple(sorted(keep)),
        tuple(sorted(prune)), _text(value["rationale"], "judge rationale"), refs, pruned)


def debate_judgment_from_attempt(row: Mapping[str, Any], *, binding: DebateBinding,
                                  round_attempts: Iterable[Mapping[str, Any]], facts: InteractionFacts) -> DebateJudgment:
    modern = json.loads(row["observation_json"])["request"].get("schema_version", 0) >= 14
    observation = _observer(row, facts, kind=AuxiliaryRequestKind.DEBATE_JUDGE if modern else AuxiliaryRequestKind.OBSERVER)
    rounds = read_debate_rounds(round_attempts, binding=binding, facts=facts)
    judgment = admit_debate_judgment(observation["response"]["typed_candidate"], binding=binding,
        rounds=rounds, facts=facts, source_attempt_id=row["attempt_id"])
    allowed = set(observation["request"]["immutable_context_refs"])
    if modern:
        contexts = [item for item in observation["request"]["input_artifacts"] if item["ref"].endswith("--debate-judge-context")]
        if len(contexts) != 1:
            raise ValueError("judge requires its exact frozen six-output context")
        context = json.loads(base64.b64decode(contexts[0]["content_base64"], validate=True))
        frozen = InteractionFacts.from_rows(binding.run_id, context["facts"])
        if (context["debate_id"] != binding.debate_id or context["participants"] != [asdict(item) for item in binding.participants]
                or read_judge_rounds(context, binding=binding, facts=frozen) != rounds):
            raise ValueError("judge input differs from the canonical six outputs")
        canonical = {item["fact_id"]: item for item in facts.rows}
        if any(item != canonical.get(item["fact_id"]) for item in frozen.rows):
            raise ValueError("judge input changed an accepted scientific fact")
        allowed |= frozen.reference_ids | {item.source_attempt_id for item in rounds}
    _references(list(judgment.supporting_refs), allowed, "judge input references")
    return judgment
