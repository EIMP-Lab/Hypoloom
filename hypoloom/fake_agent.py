"""Deterministic Hypoloom Agent and adversarial contract fixtures."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

from .agent_aci import (
    ACI_SCHEMA_VERSION,
    EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AgentACIError,
    AgentBudget,
    AgentCommand,
    AgentRequest,
    AgentResponse,
    AgentStatus,
    DeclaredEffect,
    EmbeddedInput,
    WorkspaceArtifact,
    canonical_json,
    request_digest,
    required_effect,
    required_output_schema,
    with_output_digest,
    _synthesis_parent_hypothesis_ids,
)
from .collaboration import ExpertSignalKind, RendezvousDisposition, RendezvousReply, RendezvousRequest
from .expert_loop import (
    AuxiliaryRequestKind,
    AuxiliaryTask,
    HypothesisUpdateDisposition,
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeSubkind,
    ResearchNodeTask,
)
from .literature import LiteratureQuery
from .outcome_adapter import OUTCOME_ADAPTER_VERSION


FAKE_MODEL_IDENTITY = "fake:model-v1"
FAKE_ADAPTER_VERSION = "fake-agent/4"


class FakeAgentMode(str, Enum):
    VALID = "VALID"
    MALFORMED_JSON = "MALFORMED_JSON"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    WRONG_SCHEMA = "WRONG_SCHEMA"
    WRONG_COMMAND = "WRONG_COMMAND"
    WRONG_COMMAND_ID = "WRONG_COMMAND_ID"
    WRONG_ATTEMPT_ID = "WRONG_ATTEMPT_ID"
    WRONG_ATTEMPT_ORDINAL = "WRONG_ATTEMPT_ORDINAL"
    MISSING_EFFECT = "MISSING_EFFECT"
    WRONG_EFFECT_IDENTITY = "WRONG_EFFECT_IDENTITY"
    DUPLICATE_EFFECT = "DUPLICATE_EFFECT"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    FREE_TEXT_ONLY = "FREE_TEXT_ONLY"
    TIMEOUT = "TIMEOUT"
    NONZERO_EXIT = "NONZERO_EXIT"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    STALE_RESPONSE = "STALE_RESPONSE"
    MISSING_METADATA = "MISSING_METADATA"
    WRONG_INPUT_DIGEST = "WRONG_INPUT_DIGEST"
    WRONG_OUTPUT_DIGEST = "WRONG_OUTPUT_DIGEST"
    OVERSIZED_OUTPUT = "OVERSIZED_OUTPUT"
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"


class FakeAgent:
    """Return a repeatable response shape without a model/provider call."""

    model_identity = FAKE_MODEL_IDENTITY
    adapter_version = FAKE_ADAPTER_VERSION
    permission_profile = "phase2-fake-no-network"

    def __init__(self, mode: FakeAgentMode | str = FakeAgentMode.VALID) -> None:
        self.mode = FakeAgentMode(mode)

    def invoke(self, request: AgentRequest) -> AgentResponse | bytes:
        valid = _valid_response(request)
        mode = self.mode
        if mode is FakeAgentMode.VALID:
            return valid
        if mode is FakeAgentMode.MALFORMED_JSON:
            return b'{"status":'
        if mode is FakeAgentMode.UNKNOWN_FIELD:
            return _mutated_bytes(valid, unexpected=True)
        if mode is FakeAgentMode.WRONG_SCHEMA:
            return _redigest(replace(valid, schema_version=EXPERT_LOOP_ACI_SCHEMA_VERSION + 1))
        if mode is FakeAgentMode.WRONG_COMMAND:
            other = (
                AgentCommand.DRAFT_EXPERIMENT_SPEC
                if request.command is AgentCommand.PROPOSE_HYPOTHESIS
                else AgentCommand.PROPOSE_HYPOTHESIS
            )
            return _redigest(replace(valid, command=other))
        if mode is FakeAgentMode.WRONG_COMMAND_ID:
            return _redigest(replace(valid, command_id=f"stale-{request.command_id}"))
        if mode in {FakeAgentMode.WRONG_ATTEMPT_ID, FakeAgentMode.STALE_RESPONSE}:
            return _redigest(replace(valid, attempt_id=f"stale-{request.attempt_id}"))
        if mode is FakeAgentMode.WRONG_ATTEMPT_ORDINAL:
            return _redigest(replace(valid, attempt_ordinal=request.attempt_ordinal + 1))
        if mode is FakeAgentMode.MISSING_EFFECT:
            return _redigest(replace(valid, declared_effects=()))
        if mode is FakeAgentMode.WRONG_EFFECT_IDENTITY:
            wrong = replace(valid.declared_effects[0], attempt_id=f"wrong-{request.attempt_id}")
            return _redigest(replace(valid, declared_effects=(wrong,)))
        if mode is FakeAgentMode.DUPLICATE_EFFECT:
            effect = valid.declared_effects[0]
            return _redigest(replace(valid, declared_effects=(effect, effect)))
        if mode is FakeAgentMode.DUPLICATE_CANDIDATE:
            return _mutated_bytes(valid, typed_candidate=[valid.typed_candidate, valid.typed_candidate])
        if mode is FakeAgentMode.FREE_TEXT_ONLY:
            return _redigest(
                replace(
                    valid,
                    typed_candidate=None,
                    declared_effects=(),
                    proposed_workspace_artifacts=(),
                    diagnostics={"free_text": "I completed the task successfully."},
                )
            )
        if mode is FakeAgentMode.TIMEOUT:
            return _redigest(
                replace(
                    valid,
                    status=AgentStatus.TIMED_OUT,
                    typed_candidate=None,
                    proposed_workspace_artifacts=(),
                    declared_effects=(),
                    diagnostics={"reason": "fake_deadline_exceeded"},
                )
            )
        if mode is FakeAgentMode.NONZERO_EXIT:
            return _redigest(
                replace(
                    valid,
                    status=AgentStatus.FAILED,
                    typed_candidate=None,
                    proposed_workspace_artifacts=(),
                    declared_effects=(),
                    diagnostics={"reason": "fake_nonzero_exit", "exit_code": 17},
                )
            )
        if mode is FakeAgentMode.PATH_TRAVERSAL:
            raw = valid.to_dict()
            raw["proposed_workspace_artifacts"] = [
                {"path": "../../state.sqlite", "content_base64": "eA=="}
            ]
            return canonical_json(raw)
        if mode is FakeAgentMode.MISSING_METADATA:
            raw = valid.to_dict()
            del raw["model_identity"]
            return canonical_json(raw)
        if mode is FakeAgentMode.WRONG_INPUT_DIGEST:
            return _redigest(replace(valid, input_digest="0" * 64))
        if mode is FakeAgentMode.WRONG_OUTPUT_DIGEST:
            return replace(valid, output_digest="0" * 64)
        if mode is FakeAgentMode.OVERSIZED_OUTPUT:
            artifact = WorkspaceArtifact.from_bytes("experiment.py", b"x" * (request.budget.max_output_bytes + 1))
            return _redigest(replace(valid, proposed_workspace_artifacts=(artifact,)))
        if mode is FakeAgentMode.CONFLICTING_DUPLICATE:
            candidate = dict(valid.typed_candidate or {})
            candidate["claim"] = "conflicting replay bytes"
            return _redigest(replace(valid, typed_candidate=candidate))
        raise AssertionError(f"unhandled fake mode: {mode}")

    def deliveries(self, request: AgentRequest, *, conflict: bool = False) -> tuple[AgentResponse | bytes, ...]:
        """Return an exact replay pair or an explicit conflicting second delivery."""

        first = _valid_response(request)
        if not conflict:
            return first, first
        second = FakeAgent(FakeAgentMode.CONFLICTING_DUPLICATE).invoke(request)
        return first, second


def _default_task_context(command: AgentCommand) -> ResearchNodeTask | AuxiliaryTask | None:
    node = {
        AgentCommand.LITERATURE_REVIEW: (ResearchNodeKind.LITERATURE_REVIEW, ResearchNodeSubkind.MAIN),
        AgentCommand.HYPOTHESIS_PROPOSAL: (ResearchNodeKind.HYPOTHESIS_PROPOSAL, ResearchNodeSubkind.FORWARD),
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT: (ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT, ResearchNodeSubkind.MAIN),
        AgentCommand.EXPERIMENT_EXECUTION: (ResearchNodeKind.EXPERIMENT_EXECUTION, ResearchNodeSubkind.MAIN),
        AgentCommand.RESULT_ANALYSIS: (ResearchNodeKind.RESULT_ANALYSIS, ResearchNodeSubkind.MAIN),
        AgentCommand.HYPOTHESIS_UPDATE: (ResearchNodeKind.HYPOTHESIS_UPDATE, ResearchNodeSubkind.MAIN),
        AgentCommand.CYCLE_REPORT: (ResearchNodeKind.CYCLE_REPORT, ResearchNodeSubkind.MAIN),
        AgentCommand.ADJUDICATE: (ResearchNodeKind.ADJUDICATE, ResearchNodeSubkind.MAIN),
        AgentCommand.SELF_EVOLVE: (ResearchNodeKind.SELF_EVOLVE, ResearchNodeSubkind.MAIN),
    }.get(command)
    if node is not None:
        experiment_bound = node[0] in {
            ResearchNodeKind.EXPERIMENT_DESIGN_IMPLEMENT,
            ResearchNodeKind.EXPERIMENT_EXECUTION,
            ResearchNodeKind.RESULT_ANALYSIS,
        }
        return ResearchNodeTask(key=ResearchNodeKey(
            run_id="run-fake", component_id="primary.score", expert_slot="scientist",
            cycle=1, node_kind=node[0], subkind=node[1],
            experiment_ordinal=1 if experiment_bound else None,
        ))
    auxiliary = {
        AgentCommand.GROUNDING: AuxiliaryRequestKind.GROUNDING,
        AgentCommand.OBSERVER: AuxiliaryRequestKind.OBSERVER,
        AgentCommand.DEBATE: AuxiliaryRequestKind.DEBATE,
        AgentCommand.LEADER: AuxiliaryRequestKind.LEADER,
        AgentCommand.COMPACT_CONTEXT: AuxiliaryRequestKind.CONTEXT_COMPACTION,
        AgentCommand.GRAPH_EVOLVE: AuxiliaryRequestKind.GRAPH_EVOLVE,
        AgentCommand.FINAL_REPORT: AuxiliaryRequestKind.FINAL_REPORT,
        AgentCommand.DEBATE_JUDGE: AuxiliaryRequestKind.DEBATE_JUDGE,
        AgentCommand.PAPER_READ: AuxiliaryRequestKind.PAPER_READ,
    }.get(command)
    if auxiliary is None:
        return None
    scoped = auxiliary not in {AuxiliaryRequestKind.GROUNDING, AuxiliaryRequestKind.GRAPH_EVOLVE, AuxiliaryRequestKind.FINAL_REPORT, AuxiliaryRequestKind.DEBATE_JUDGE}
    return AuxiliaryTask(
        kind=auxiliary, run_id="run-fake",
        component_id="primary.score" if scoped else None,
        expert_slot="scientist" if scoped else None, cycle=1 if scoped or auxiliary is AuxiliaryRequestKind.GRAPH_EVOLVE else None,
    )


def default_request(command: AgentCommand = AgentCommand.PROPOSE_HYPOTHESIS) -> AgentRequest:
    """Small deterministic request used by focused tests and the walking skeleton."""
    inputs: tuple[EmbeddedInput, ...] = ()
    if command is AgentCommand.ANSWER_RENDEZVOUS:
        rendezvous = RendezvousRequest(schema_version=1, rendezvous_id="rendezvous-attempt-1",
            run_id="run-fake", requester_role="analyst", requester_slot=1, target_role="scientist",
            source_attempt_id="source-attempt-1", kind=ExpertSignalKind.HELP,
            question="Which assumption needs review?", context_refs=("goal-1",), round=1, max_rounds=1)
        inputs = (EmbeddedInput.from_bytes("rendezvous-attempt-1--rendezvous", "application/json",
                                          canonical_json(rendezvous.to_dict())),)
    task_context = _default_task_context(command)
    if command is AgentCommand.CYCLE_REPORT:
        inputs = (EmbeddedInput.from_bytes("cycle-1--cycle-assessments", "application/json",
            canonical_json({"cycle": 1, "assessments": []})),)
    schema_version = (
        EXPERT_LOOP_ACI_SCHEMA_VERSION
        if task_context is not None
        else ACI_SCHEMA_VERSION
    )
    if command in {AgentCommand.ADJUDICATE, AgentCommand.SELF_EVOLVE, AgentCommand.GRAPH_EVOLVE, AgentCommand.FINAL_REPORT, AgentCommand.DEBATE_JUDGE}: schema_version = 14
    if command is AgentCommand.PAPER_READ: schema_version = 20
    if command is AgentCommand.FINAL_REPORT:
        inputs = (EmbeddedInput.from_bytes("final--final-report-context", "application/json",
            canonical_json({"facts": [], "stop_reason": "Deterministic fixture without an accepted verdict."})),)
    if command is AgentCommand.GRAPH_EVOLVE:
        inputs = (EmbeddedInput.from_bytes("graph--manager-context", "application/json", canonical_json({
            "max_hypotheses_per_round": {"scientist": 3}, "effective_max_forks": {"scientist": 1},
            "source_report_refs": [], "enabled": True})),)
    return AgentRequest(
        schema_version=schema_version,
        run_id="run-fake",
        owner_workflow_id="workflow-fake",
        command=command,
        command_id=(f"command-{command.value.lower()}" if task_context is None else task_context.command_id),
        attempt_id="attempt-1",
        attempt_ordinal=1,
        role="grounding" if command in {AgentCommand.GRAPH_EVOLVE, AgentCommand.FINAL_REPORT, AgentCommand.DEBATE_JUDGE} else "scientist",
        component_id="grounding" if command in {AgentCommand.GRAPH_EVOLVE, AgentCommand.FINAL_REPORT, AgentCommand.DEBATE_JUDGE} else "primary.score",
        method_family_id=(
            _fake_method_family_id()
            if command in {
                AgentCommand.DRAFT_EXPERIMENT_SPEC,
                AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
            }
            else None
        ),
        immutable_context_refs=("goal-1",),
        input_artifacts=inputs,
        required_output_schema=required_output_schema(
            command, task_context, schema_version=schema_version,
        ),
        effect_requirements=(required_effect(command),),
        tool_permission_profile="phase2-fake",
        budget=AgentBudget(
            max_turns=2,
            max_tokens=512,
            max_output_bytes=16_384,
            wall_seconds=5,
            max_cost="0",
        ),
        deadline="2030-01-01T00:00:00Z",
        max_input_bytes=65_536,
        required_model_identity=FAKE_MODEL_IDENTITY,
        required_adapter_version=FAKE_ADAPTER_VERSION,
        task_context=task_context,
    )


def _valid_response(request: AgentRequest) -> AgentResponse:
    query_plan = (
        isinstance(request.task_context, ResearchNodeTask)
        and request.task_context.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW
        and request.task_context.key.subkind is ResearchNodeSubkind.QUERY_PLAN
        or isinstance(request.task_context, AuxiliaryTask)
        and request.task_context.kind is AuxiliaryRequestKind.GROUNDING_QUERY_PLAN
    )
    if query_plan:
        templates = tuple(item for item in request.input_artifacts
            if item.ref.endswith("--literature-query-template"))
        if len(templates) != 1 or templates[0].media_type != "application/json":
            raise AgentACIError(
                "invalid_embedded_input",
                "query plan requires exactly one JSON literature query template",
            )
        try:
            query = LiteratureQuery.from_dict(json.loads(templates[0].content_bytes()))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AgentACIError("invalid_embedded_input", str(exc)) from exc
        expected_version = 1 if request.schema_version == 8 else 2
        if query.schema_version != expected_version:
            raise AgentACIError(
                "invalid_embedded_input",
                f"ACI v{request.schema_version} requires LiteratureQuery/v{expected_version}",
            )
        candidate = query.to_dict()
        artifacts = ()
    elif request.command is AgentCommand.PROPOSE_HYPOTHESIS:
        candidate: dict[str, Any] = {
            "component_id": request.component_id,
            "proposer_role": request.role,
            "method_family_id": _fake_method_family_id(),
            "claim": "The deterministic score will meet the preregistered threshold.",
            "assumptions": ["the local deterministic evaluator is available"],
        }
        artifacts: tuple[WorkspaceArtifact, ...] = ()
    elif (
        request.command is AgentCommand.HYPOTHESIS_PROPOSAL
        and isinstance(request.task_context, ResearchNodeTask)
        and request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS
    ):
        # Deterministic fixture choice is ancestry only, never adjudication.
        parent_hypothesis_id = _synthesis_parent_hypothesis_ids(request)[0]
        candidate = {
            "component_id": request.component_id,
            "proposer_role": request.role,
            "method_family_id": _fake_method_family_id(),
            "claim": "The synthesized deterministic mechanism will meet the goal.",
            "assumptions": ["both bounded pincer arguments are available"],
            "parent_hypothesis_id": parent_hypothesis_id,
            "relation": None if parent_hypothesis_id is None else "revises",
        }
        if request.schema_version >= 14:
            candidate = {"schema_version": 1, "hypotheses": [{"chain_id": "1", "candidate": candidate}]}
        artifacts = ()
    elif request.command in {
        AgentCommand.DRAFT_EXPERIMENT_SPEC,
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    }:
        component, protocol, contract = _component_contract(request)
        result_kind = str(contract.get("result_kind", "metric"))
        metric = contract.get("metric")
        if result_kind not in {"metric", "artifact"}:
            raise ValueError(f"unknown fake component result kind {result_kind!r}")
        if result_kind == "metric" and not isinstance(metric, dict):
            raise ValueError("fake metric component lacks its decision metric")
        gpu = _has_gpu_capability(request)
        criteria = contract.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            if result_kind == "metric":
                assert isinstance(metric, dict)
                direction = str(metric["direction"])
                operator = ">=" if direction == "MAXIMIZE" else "<="
                criteria = [f"{metric['name']} {operator} {metric['target']}"]
            else:
                criteria = ["all required logical artifacts pass deterministic validation"]
        logical_requirements = contract.get("required_artifacts", [])
        if not isinstance(logical_requirements, list):
            raise ValueError("fake component required_artifacts must be a list")
        artifact_requirements = contract.get(
            "artifact_requirements", ["result.json"]
        )
        if artifact_requirements != ["result.json"]:
            raise ValueError(
                "fake experiment artifact_requirements must equal ['result.json']"
            )
        candidate = {
            "component_id": request.component_id,
            "owner_role": request.role,
            "method_family_id": request.method_family_id,
            "method": (
                f"Run bounded {'GPU' if gpu else 'CPU'} component "
                f"{request.component_id} for family {request.method_family_id}."
            ),
            "metrics": [] if result_kind == "artifact" else [metric],
            "acceptance_criteria": criteria,
            "evaluator_version": OUTCOME_ADAPTER_VERSION,
            "artifact_requirements": artifact_requirements,
            "resource_preference": "AUTO" if gpu else "CPU",
        }
        result, logical_files = _fake_component_result(
            request=request,
            component=component,
            protocol=protocol,
            required_spec=contract,
        )
        setup = (
            "import torch\n"
            "assert torch.cuda.is_available()\n"
            "assert torch.cuda.device_count() == 1\n"
            "resource_evidence = {\n"
            "    'cuda_available': True,\n"
            "    'cuda_device_count': torch.cuda.device_count(),\n"
            "    'cuda_device_name': torch.cuda.get_device_name(0),\n"
            "    'cuda_runtime': torch.version.cuda,\n"
            "}\n"
            "values = torch.tensor([1, 1, 1], device='cuda')\n"
            "measurements = [int(value) for value in values.cpu().tolist()]\n"
            "print(json.dumps(resource_evidence, sort_keys=True), flush=True)\n"
            if gpu else
            "checksum = sum((index * index) % 97 for index in range(200_000))\n"
            "assert checksum > 0\n"
        )
        result_json = json.dumps(
            result, ensure_ascii=True, allow_nan=False, sort_keys=True
        )
        logical_files_json = json.dumps(
            logical_files, ensure_ascii=True, sort_keys=True
        )
        source = (
            "import json, os, time\n"
            "from pathlib import Path\n"
            + setup +
            "time.sleep(float(os.environ.get('HYPOLOOM_DELAY', '0')))\n"
            f"result = json.loads({result_json!r})\n"
            f"logical_files = json.loads({logical_files_json!r})\n"
            "result_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
            "result_path.parent.mkdir(parents=True, exist_ok=True)\n"
            "for relative, payload in logical_files.items():\n"
            "    path = result_path.parent / relative\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_bytes(payload.encode('utf-8'))\n"
            "result_path.write_text(json.dumps(result, sort_keys=True), encoding='utf-8')\n"
        ).encode()
        artifacts = (WorkspaceArtifact.from_bytes("experiment.py", source),)
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            artifacts += (
                WorkspaceArtifact.from_bytes(
                    "implementation.patch", _new_file_patch("experiment.py", source)
                ),
            )
    elif request.command is AgentCommand.ANSWER_RENDEZVOUS:
        rendezvous = next(
            RendezvousRequest.from_dict(json.loads(item.content_bytes()))
            for item in request.input_artifacts
            if item.ref.endswith("--rendezvous")
        )
        candidate = RendezvousReply(schema_version=1, rendezvous_id=rendezvous.rendezvous_id,
            kind=rendezvous.kind, disposition=RendezvousDisposition.ANSWERED,
            responder_role=request.role, summary="Challenge the cited assumption before freezing the method.",
            supporting_refs=rendezvous.context_refs).to_dict()
        artifacts = ()
    elif request.command is AgentCommand.OBSERVER and request.schema_version >= 14:
        candidate = {"action": "none", "rationale": "No interaction is needed for this scripted report.",
            "supporting_refs": [], "leader": None, "debate": None}
        artifacts = ()
    elif request.command is AgentCommand.DEBATE and request.schema_version >= 14:
        candidate = {"position": "Evaluate the recorded evidence against the shared goal.",
            "arguments": ["Only accepted outcomes establish scientific progress."],
            "evidence": [], "concessions": [], "web_sources": []}
        artifacts = ()
    elif request.command is AgentCommand.DEBATE_JUDGE:
        from .agent_aci import debate_judge_inputs
        _, _, rows = debate_judge_inputs(request)
        roles = sorted({row.participant.scope.role for row in rows})
        candidate = {"winner": roles[0], "scores": [{"role": role, "score": "1", "round_refs": [row.source_attempt_id
            for row in rows if row.participant.scope.role == role]} for role in roles],
            "keep": roles[:1], "prune": roles[1:], "rationale": "Scripted equal scores with a stable participant-order tie break.",
            "supporting_refs": [row.source_attempt_id for row in rows]}
        artifacts = ()
    elif request.command is AgentCommand.GRAPH_EVOLVE:
        candidate = {"task_kind": "GRAPH_EVOLVE", "summary": "Retain the current graph limits and trigger skills.",
            "supporting_refs": list(request.immutable_context_refs), "graph_evolution": []}
        artifacts = ()
    elif request.command is AgentCommand.ADJUDICATE:
        context = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--batch-context")).content_bytes())
        tips = context["terminal_tips"]
        comparable = [chain for chain, tip in tips.items() if tip is not None and tip["outcome"] in {"SUCCESS", "CLEAN_REFUTATION"}]
        best = min(comparable, key=lambda chain: (tips[chain]["outcome"] != "SUCCESS", chain), default=None)
        candidate = {"schema_version": 1, "verdicts": [{"chain_id": chain, "node_id": hypothesis_id,
            "status": "inconclusive" if tips[chain] is None or tips[chain]["outcome"] not in {"SUCCESS", "CLEAN_REFUTATION"} else "verified" if tips[chain]["outcome"] == "SUCCESS" else "refuted",
            "evidence": [] if tips[chain] is None else [tips[chain]["outcome_id"]]} for chain, hypothesis_id in context["chains"].items()],
            "keep": list(context["chains"]), "prune": [], "best_chain": best, "summary": "Select the stable first eligible successful result, otherwise the stable first clean refutation."}
        artifacts = ()
    else:
        assert request.task_context is not None
        task_kind = (
            request.task_context.key.node_kind.value
            if isinstance(request.task_context, ResearchNodeTask)
            else request.task_context.kind.value
        )
        literature_main = (
            request.schema_version >= 8
            and isinstance(request.task_context, ResearchNodeTask)
            and request.task_context.key.node_kind is ResearchNodeKind.LITERATURE_REVIEW
            and request.task_context.key.subkind is ResearchNodeSubkind.MAIN
        )
        supporting_refs = (
            [item.ref for item in request.input_artifacts
             if item.ref.endswith("--literature-packet")]
            if literature_main else list(request.immutable_context_refs)
        )
        candidate = {
            "task_kind": task_kind,
            "summary": f"Deterministic Stage-1 result for {request.command.value}.",
            "supporting_refs": supporting_refs,
        }
        if request.command is AgentCommand.GROUNDING and request.schema_version >= 13:
            candidate = {"task_kind": "GROUNDING", "supporting_refs": supporting_refs,
                "background": {"project_summary": candidate["summary"], "codebase_map": [],
                    "entrypoints": [], "how_to_run_experiment": "Use the admitted execution environment and inspect the repository before authoring an experiment.",
                    "metrics_available": [], "risks": ["Fake adapter supplies no web or repository observations."], "open_questions": []},
                "web_sources": []}
            if request.schema_version >= 15: candidate["clarification"] = None
            if request.schema_version >= 16: candidate["acceptance"] = None
        if request.command is AgentCommand.HYPOTHESIS_UPDATE:
            candidate["update_disposition"] = HypothesisUpdateDisposition.TERMINATE_LINEAGE.value
        if request.command is AgentCommand.RESULT_ANALYSIS and request.schema_version >= 14:
            candidate["iteration_disposition"] = "COMPLETE"
        artifacts = ()
        if request.command is AgentCommand.FINAL_REPORT:
            from .final_report import report_sources
            verdict, outcomes, reason = report_sources(request)
            report = "# Final report\n\n" + reason + "\n\n" + "\n".join(
                f"{identity}: {value or 'No scientific verdict was accepted.'}" for identity, value in (verdict, *outcomes)) + "\n"
            artifacts = (WorkspaceArtifact.from_bytes("FINAL_REPORT.md", report.encode()),)
        if request.command is AgentCommand.CYCLE_REPORT:
            supplied = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--cycle-assessments")).content_bytes())
            citations = [{"fact_id": str(row["fact_id"]), "hypothesis_id": str(row["hypothesis_id"]),
                "assessment": row["payload"]["assessment"]} for row in supplied["assessments"]]
            report = "---\n" + json.dumps({"assessments": citations}) + "\n---\n# Cycle report\n\nCompleted the recorded experiment.\n"
            from .expert_memory import report_path, request_report_component
            relative = report_path(supplied["cycle"], request_report_component(request))
            artifacts = (WorkspaceArtifact.from_bytes("MEMORY.md", f"# Expert memory\n\nSee {relative}.\n".encode()),
                WorkspaceArtifact.from_bytes(relative, report.encode()))
    effect = DeclaredEffect(
        effect=required_effect(request.command),
        command_id=request.command_id,
        attempt_id=request.attempt_id,
    )
    response = AgentResponse(
        schema_version=request.schema_version,
        command=request.command,
        command_id=request.command_id,
        attempt_id=request.attempt_id,
        attempt_ordinal=request.attempt_ordinal,
        status=AgentStatus.SUCCEEDED,
        typed_candidate=candidate,
        proposed_workspace_artifacts=artifacts,
        declared_effects=(effect,),
        diagnostics={"fake_mode": "VALID"},
        model_identity=FAKE_MODEL_IDENTITY,
        adapter_version=FAKE_ADAPTER_VERSION,
        token_usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        turn_count=1,
        cost="0",
        cost_currency="USD",
        input_digest=request_digest(request),
        output_digest="",
    )
    return with_output_digest(response)


def _new_file_patch(path: str, content: bytes) -> bytes:
    lines = content.decode("utf-8").splitlines(keepends=True)
    body = b"".join(b"+" + line.encode("utf-8") for line in lines)
    header = (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
    ).encode("utf-8")
    return header + body


def _component_contract(
    request: AgentRequest,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for item in reversed(request.input_artifacts):
        try:
            value = json.loads(item.content_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        component = value.get("component")
        protocol = value.get("protocol")
        contract = value.get("required_experiment_spec")
        if (
            isinstance(component, dict)
            and component.get("id") == request.component_id
            and isinstance(protocol, dict)
            and isinstance(contract, dict)
        ):
            return component, protocol, contract
    metric = {"name": "score", "direction": "MAXIMIZE", "target": "1"}
    component = {
        "id": request.component_id,
        "result": {
            "kind": "metric",
            "metric": metric["name"],
            "direction": metric["direction"],
            "target": 1,
        },
        "seeds": [],
        "required_artifacts": [],
    }
    protocol = {
        "result_kind": "metric",
        "requirements": {
            "required_metrics": [metric["name"]],
            "required_artifacts": [],
            "artifact_validators": [],
        },
    }
    contract = {
        "result_kind": "metric",
        "metric": metric,
        "trial_plan": {"seeds": [], "arms": [], "trial_ids": []},
        "required_artifacts": [],
        "artifact_requirements": ["result.json"],
    }
    return component, protocol, contract


def _fake_component_result(
    *,
    request: AgentRequest,
    component: Mapping[str, Any],
    protocol: Mapping[str, Any],
    required_spec: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    result_kind = required_spec.get("result_kind")
    if result_kind not in {"metric", "artifact"}:
        raise ValueError("fake component result_kind must be metric or artifact")
    if component.get("id") != request.component_id:
        raise ValueError("fake component id differs from its Agent request")
    if protocol.get("result_kind") != result_kind:
        raise ValueError("fake protocol result kind differs from its component")

    requirements = protocol.get("requirements", {})
    if not isinstance(requirements, Mapping):
        raise ValueError("fake protocol requirements must be an object")
    artifact_ids, artifact_counts = _logical_artifact_plan(
        component, protocol, required_spec
    )
    validators = _artifact_validators(requirements, artifact_ids)
    family = request.method_family_id
    if not isinstance(family, str) or not family:
        raise ValueError("fake component result requires a method family")
    trials = (
        _fake_metric_trials(component, required_spec, requirements)
        if result_kind == "metric"
        else []
    )
    implementation_patch = _fake_implementation_patch(family)
    runner_config = _fake_runner_config(family)
    implementation_digest = hashlib.sha256(implementation_patch.encode()).hexdigest()
    config_digest = hashlib.sha256(runner_config.encode()).hexdigest()

    artifacts: dict[str, str | list[str]] = {}
    logical_files: dict[str, str] = {}
    for artifact_id in artifact_ids:
        artifact_validators = validators.get(artifact_id, ())
        suffix = _artifact_suffix(artifact_id, artifact_validators)
        paths = [
            f"artifacts/{artifact_id}-{index + 1}{suffix}"
            for index in range(artifact_counts[artifact_id])
        ]
        artifacts[artifact_id] = paths[0] if len(paths) == 1 else paths
        for index, relative in enumerate(paths, start=1):
            logical_files[relative] = _fake_artifact_payload(
                artifact_id=artifact_id,
                item_ordinal=index,
                validators=artifact_validators,
                component=component,
                requirements=requirements,
                family=family,
                implementation_patch=implementation_patch,
                runner_config=runner_config,
                implementation_digest=implementation_digest,
                config_digest=config_digest,
                trials=trials,
            )

    identity: dict[str, str] = {"method_family_id": family}
    result_spec = component.get("result", {})
    if ({"implementation_patch", "runner_config"} <= set(artifact_ids)
            or isinstance(result_spec, Mapping) and result_spec.get("target_overlay_policy") == "latest"):
        identity.update(
            implementation_sha256=implementation_digest,
            core_config_sha256=config_digest,
        )
    result: dict[str, Any] = {
        "artifacts": artifacts,
        "identity": identity,
    }
    if result_kind == "metric":
        result["trials"] = trials
    return result, logical_files


def _fake_metric_trials(
    component: Mapping[str, Any],
    required_spec: Mapping[str, Any],
    requirements: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metric = required_spec.get("metric")
    if not isinstance(metric, Mapping):
        raise ValueError("fake metric component lacks its decision metric")
    metric_name = metric.get("name")
    if not isinstance(metric_name, str) or not metric_name.strip():
        raise ValueError("fake metric name must be non-empty text")
    value = _metric_target(metric.get("target"))
    trial_plan = required_spec.get("trial_plan", {})
    if not isinstance(trial_plan, Mapping):
        raise ValueError("fake trial_plan must be an object")
    raw_seeds = trial_plan.get("seeds", [])
    raw_arms = trial_plan.get("arms", [])
    if not isinstance(raw_seeds, list) or not isinstance(raw_arms, list):
        raise ValueError("fake trial_plan seeds and arms must be arrays")
    if any(not isinstance(arm, str) or not arm for arm in raw_arms):
        raise ValueError("fake trial_plan arms must contain non-empty text")

    if raw_arms:
        trials: list[dict[str, Any]] = []
        raw_metric = _paired_raw_metric(metric_name)
        for seed in raw_seeds:
            for arm in raw_arms:
                arm_value: int | float = 0 if arm == "control" else value
                trials.append(
                    {
                        "seed": seed,
                        "arm": arm,
                        "metrics": {raw_metric: arm_value},
                        "clean": True,
                    }
                )
        return trials

    seeds: list[object] = list(raw_seeds)
    causal_max = not seeds and metric_name.casefold().startswith("max_")
    if causal_max:
        seeds = list(range(_causal_trial_count(component)))
    if not seeds:
        seeds = ["only"]
    required_metrics = requirements.get("required_metrics", [])
    include_success_count = (
        isinstance(required_metrics, (list, tuple))
        and "n_successful_seeds" in required_metrics
    )
    trials = []
    for index, seed in enumerate(seeds):
        trial_value = value
        if causal_max and len(seeds) > 1:
            trial_value = (
                value * (index + 1) / len(seeds)
                if value >= 0
                else value - (len(seeds) - index - 1)
            )
        metrics = {metric_name: trial_value}
        if include_success_count:
            metrics["n_successful_seeds"] = len(seeds)
        trials.append({"seed": seed, "metrics": metrics, "clean": True})
    return trials


def _paired_raw_metric(metric_name: str) -> str:
    prefix = "paired_"
    separator = "_gain_vs_"
    if metric_name.startswith(prefix) and separator in metric_name:
        return metric_name[len(prefix) :].split(separator, 1)[0]
    return metric_name


def _causal_trial_count(component: Mapping[str, Any]) -> int:
    counts = []
    raw_inputs = component.get("required_inputs", [])
    if isinstance(raw_inputs, list):
        for item in raw_inputs:
            if not isinstance(item, Mapping) or item.get("artifact") != "checkpoints":
                continue
            count = item.get("min_count", 1)
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                counts.append(count)
    return max(counts, default=1)


def _artifact_validators(
    requirements: Mapping[str, Any], artifact_ids: tuple[str, ...]
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    raw = requirements.get("artifact_validators", [])
    if not isinstance(raw, list):
        raise ValueError("fake artifact_validators must be an array")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("fake artifact validator must be an object")
        artifact_id = item.get("artifact")
        if not isinstance(artifact_id, str) or artifact_id not in artifact_ids:
            raise ValueError("fake artifact validator names an undeclared artifact")
        grouped.setdefault(artifact_id, []).append(item)
    return {key: tuple(value) for key, value in grouped.items()}


_JSON_ARTIFACTS = {
    "aggregate_results",
    "baseline_control",
    "candidate_manifest",
    "causal_audit",
    "per_class_metrics",
    "per_trial_metrics",
    "provenance",
    "resolved_config",
    "runner_config",
}


def _artifact_suffix(
    artifact_id: str, validators: tuple[Mapping[str, Any], ...]
) -> str:
    kinds = {item.get("type") for item in validators}
    if "unified_diff" in kinds or artifact_id == "implementation_patch":
        return ".patch"
    if "json_contract" in kinds or artifact_id in _JSON_ARTIFACTS:
        return ".json"
    if artifact_id == "checkpoints":
        return ".ckpt"
    if "text_contract" in kinds:
        return ".md"
    return ".txt"


def _fake_artifact_payload(
    *,
    artifact_id: str,
    item_ordinal: int,
    validators: tuple[Mapping[str, Any], ...],
    component: Mapping[str, Any],
    requirements: Mapping[str, Any],
    family: str,
    implementation_patch: str,
    runner_config: str,
    implementation_digest: str,
    config_digest: str,
    trials: list[dict[str, Any]],
) -> str:
    kinds = {item.get("type") for item in validators}
    supported = {"json_contract", "text_contract", "unified_diff"}
    if not kinds <= supported:
        raise ValueError("fake artifact validator has an unsupported type")
    if len(kinds) > 1:
        raise ValueError("fake artifact cannot synthesize conflicting validator types")
    if "unified_diff" in kinds or artifact_id == "implementation_patch":
        return implementation_patch
    if artifact_id == "runner_config":
        return runner_config
    if "json_contract" in kinds or artifact_id in _JSON_ARTIFACTS:
        return _fake_json_artifact(
            artifact_id=artifact_id,
            item_ordinal=item_ordinal,
            validators=validators,
            component=component,
            requirements=requirements,
            family=family,
            implementation_digest=implementation_digest,
            config_digest=config_digest,
            trials=trials,
        )
    if "text_contract" in kinds:
        return _fake_text_artifact(artifact_id, validators)
    return (
        f"deterministic fake artifact {artifact_id} item {item_ordinal} "
        f"for method family {family}\n"
    )


def _fake_implementation_patch(family: str) -> str:
    return (
        "diff --git a/src/merc/models/multiemo_causal_candidate.py "
        "b/src/merc/models/multiemo_causal_candidate.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/merc/models/multiemo_causal_candidate.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+\"\"\"Deterministic fake topology component; not a research result.\"\"\"\n"
        f"+METHOD_FAMILY_ID = {family!r}\n"
        "+MODEL_KEY = \"multiemo_causal_candidate\"\n"
    )


def _fake_runner_config(family: str) -> str:
    return _json_text(
        {
            "schema_version": 1,
            "method_family_id": family,
            "datasets": {
                "MELD": {
                    "cache": "cache/meld_C_multiemo",
                    "split_manifest": "splits/meld_C_multiemo_split_manifest.json",
                },
                "IEMOCAP": {
                    "cache": "cache/iemocap_C_multiemo",
                    "split_manifest": "splits/iemocap6_split_manifest.json",
                },
            },
        }
    )


def _fake_json_artifact(
    *,
    artifact_id: str,
    item_ordinal: int,
    validators: tuple[Mapping[str, Any], ...],
    component: Mapping[str, Any],
    requirements: Mapping[str, Any],
    family: str,
    implementation_digest: str,
    config_digest: str,
    trials: list[dict[str, Any]],
) -> str:
    result_spec = component.get("result", {})
    dataset = result_spec.get("dataset") if isinstance(result_spec, Mapping) else None
    metric = result_spec.get("metric") if isinstance(result_spec, Mapping) else None
    target = result_spec.get("target") if isinstance(result_spec, Mapping) else None
    value: dict[str, Any] = {
        "schema_version": 1,
        "artifact": artifact_id,
        "item_ordinal": item_ordinal,
        "component_id": component.get("id"),
        "method_family_id": family,
    }
    if artifact_id == "candidate_manifest":
        value.update(
            {
                "implementation_sha256": implementation_digest,
                "core_config_sha256": config_digest,
                "candidates": {
                    "MELD": {
                        "candidate_id": "fake-meld-candidate-v1",
                        "method_family_id": family,
                    },
                    "IEMOCAP": {
                        "candidate_id": "fake-iemocap-candidate-v1",
                        "method_family_id": family,
                    },
                },
            }
        )
    elif artifact_id == "resolved_config":
        value.update(
            {
                "required_paths": requirements.get("required_paths", []),
                "data_checks": requirements.get("data_checks", []),
            }
        )
    elif artifact_id == "causal_audit":
        value.update(
            {
                "dataset": dataset,
                "max_future_perturbation_logit_diff": target,
                "checks": {
                    "future_feature": True,
                    "future_speaker": True,
                    "future_participant_set": True,
                },
            }
        )
    elif artifact_id == "per_trial_metrics":
        value["trials"] = trials
    elif artifact_id == "aggregate_results":
        value.update(
            {
                "dataset": dataset,
                "metric": metric,
                "mean": target,
                "n_successful_seeds": len(trials),
            }
        )
    elif artifact_id == "baseline_control":
        value["controls"] = [
            trial for trial in trials if trial.get("arm") == "control"
        ]
    elif artifact_id == "provenance":
        value.update({"dataset": dataset, "source": "deterministic-fake-topology"})

    for validator in validators:
        if validator.get("type") != "json_contract":
            continue
        _apply_fake_json_contract(
            value,
            validator,
            family=family,
            component=component,
            implementation_digest=implementation_digest,
            config_digest=config_digest,
        )
    return _json_text(value)


def _apply_fake_json_contract(
    value: dict[str, Any],
    validator: Mapping[str, Any],
    *,
    family: str,
    component: Mapping[str, Any],
    implementation_digest: str,
    config_digest: str,
) -> None:
    exact = validator.get("exact_key_sets", {})
    allowed = validator.get("allowed_values", {})
    if not isinstance(exact, Mapping) or not isinstance(allowed, Mapping):
        raise ValueError("fake JSON validator mappings are invalid")
    for path, raw_keys in exact.items():
        if not isinstance(path, str) or not isinstance(raw_keys, list):
            raise ValueError("fake exact_key_sets entry is invalid")
        _set_json_path(value, path, {str(key): 0 for key in raw_keys})
    for path, choices in allowed.items():
        if (
            not isinstance(path, str)
            or not isinstance(choices, list)
            or not choices
        ):
            raise ValueError("fake allowed_values entry is invalid")
        _set_json_path(value, path, choices[0])
    raw_required = validator.get("required_paths", [])
    if not isinstance(raw_required, list):
        raise ValueError("fake JSON required_paths must be an array")
    for path in raw_required:
        if not isinstance(path, str) or not path:
            raise ValueError("fake JSON required path must be text")
        if _get_json_path(value, path) is _FAKE_MISSING:
            _set_json_path(
                value,
                path,
                _fake_json_value(
                    path,
                    family=family,
                    component=component,
                    implementation_digest=implementation_digest,
                    config_digest=config_digest,
                ),
            )
    groups = validator.get("equal_groups", [])
    if not isinstance(groups, list):
        raise ValueError("fake JSON equal_groups must be an array")
    for group in groups:
        if not isinstance(group, list) or len(group) < 2:
            raise ValueError("fake JSON equal group must contain at least two paths")
        shared = _get_json_path(value, str(group[0]))
        if shared is _FAKE_MISSING:
            shared = family
        for path in group:
            if not isinstance(path, str) or not path:
                raise ValueError("fake JSON equal path must be text")
            _set_json_path(value, path, shared)


_FAKE_MISSING = object()


def _get_json_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for token in path.split("."):
        if not isinstance(current, Mapping) or token not in current:
            return _FAKE_MISSING
        current = current[token]
    return current


def _set_json_path(value: dict[str, Any], path: str, leaf: Any) -> None:
    tokens = path.split(".")
    if any(not token for token in tokens):
        raise ValueError("fake JSON path has an empty token")
    current = value
    for token in tokens[:-1]:
        child = current.get(token)
        if child is None:
            child = {}
            current[token] = child
        if not isinstance(child, dict):
            raise ValueError("fake JSON contract paths conflict")
        current = child
    current[tokens[-1]] = leaf


def _fake_json_value(
    path: str,
    *,
    family: str,
    component: Mapping[str, Any],
    implementation_digest: str,
    config_digest: str,
) -> Any:
    leaf = path.rsplit(".", 1)[-1]
    if leaf == "schema_version":
        return 1
    if leaf == "method_family_id":
        return family
    if leaf == "implementation_sha256":
        return implementation_digest
    if leaf == "core_config_sha256":
        return config_digest
    if leaf == "candidate_id":
        dataset = "meld" if ".MELD." in f".{path}." else "iemocap"
        return f"fake-{dataset}-candidate-v1"
    result = component.get("result", {})
    if leaf == "dataset" and isinstance(result, Mapping):
        return result.get("dataset", "MELD")
    if leaf == "max_future_perturbation_logit_diff" and isinstance(result, Mapping):
        return result.get("target", 0)
    if leaf.startswith("future_"):
        return True
    return f"deterministic-fake-{leaf.replace('_', '-')}"


def _fake_text_artifact(
    artifact_id: str, validators: tuple[Mapping[str, Any], ...]
) -> str:
    validator = validators[0]
    required: list[str] = []
    for key in ("required_sections", "required_terms"):
        values = validator.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"fake text validator {key} must be a text array")
        required.extend(values)
    forbidden = validator.get("forbidden_terms", [])
    if not isinstance(forbidden, list) or any(
        not isinstance(item, str) for item in forbidden
    ):
        raise ValueError("fake text validator forbidden_terms must be a text array")
    lines = [
        f"Deterministic fake topology artifact: {artifact_id}.",
        "This fixture exercises contract closure and does not claim a real scientific result.",
    ]
    lines.extend(f"Required contract term: {term}." for term in required)
    text = "\n".join(lines) + "\n"
    minimum = validator.get("min_chars", 1)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError("fake text validator min_chars must be positive")
    filler = "Bounded deterministic evidence remains synthetic and reproducible.\n"
    while len(text.strip()) < minimum:
        text += filler
    folded = text.casefold()
    if any(term.casefold() in folded for term in forbidden):
        raise ValueError("fake text contract has contradictory required/forbidden terms")
    return text


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ) + "\n"


def _logical_artifact_plan(
    component: Mapping[str, Any],
    protocol: Mapping[str, Any],
    required_spec: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, int]]:
    required = required_spec.get("required_artifacts", [])
    if not isinstance(required, list) or any(
        not isinstance(item, str) or not item.strip() for item in required
    ):
        raise ValueError("fake logical artifact requirements must be text arrays")
    component_required = component.get("required_artifacts", [])
    if component_required != required:
        raise ValueError(
            "fake required_experiment_spec logical artifacts differ from component"
        )
    counts = {artifact_id: 1 for artifact_id in required}
    requirements = protocol.get("requirements", {})
    if not isinstance(requirements, Mapping):
        raise ValueError("fake protocol requirements must be an object")
    protocol_required = requirements.get("required_artifacts", [])
    protocol_ids: list[str] = []
    if isinstance(protocol_required, Mapping):
        declarations = protocol_required.items()
    elif isinstance(protocol_required, list):
        declarations = []
        for item in protocol_required:
            if isinstance(item, str):
                declarations.append((item, 1))
            elif isinstance(item, Mapping):
                declarations.append(
                    (item.get("artifact", item.get("id")), item.get("min_count", 1))
                )
            else:
                raise ValueError("fake protocol artifact declaration is invalid")
    else:
        raise ValueError("fake protocol required_artifacts must be an array or object")
    for artifact_id, count in declarations:
        if isinstance(count, Mapping):
            count = count.get("min_count")
        if (
            not isinstance(artifact_id, str)
            or artifact_id not in counts
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            raise ValueError("fake protocol artifact inventory differs from component")
        protocol_ids.append(artifact_id)
        counts[artifact_id] = max(counts[artifact_id], count)
    if protocol_ids and set(protocol_ids) != set(required):
        raise ValueError("fake protocol artifact inventory differs from component")
    seeds = component.get("seeds", [])
    if "checkpoints" in counts and isinstance(seeds, list) and seeds:
        counts["checkpoints"] = max(counts["checkpoints"], len(seeds))
    return tuple(required), counts


def _metric_target(value: Any) -> int | float:
    try:
        target = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("fake metric target must be numeric") from exc
    if not target.is_finite():
        raise ValueError("fake metric target must be finite")
    if target == target.to_integral_value():
        return int(target)
    result = float(target)
    if not math.isfinite(result):
        raise ValueError("fake metric target is outside the JSON number range")
    return result


def _has_gpu_capability(request: AgentRequest) -> bool:
    for item in reversed(request.input_artifacts):
        if (
            not item.ref.endswith("--compute-capabilities")
            or item.media_type != "application/json"
        ):
            continue
        try:
            value = json.loads(item.content_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("gpus"), list):
            return bool(value["gpus"])
    return False


def _fake_method_family_id() -> str:
    # One stable family deliberately spans every component in the fake workflow.
    return "fake-family-v1"


def _redigest(response: AgentResponse) -> AgentResponse:
    return with_output_digest(replace(response, output_digest=""))


def _mutated_bytes(response: AgentResponse, **changes: Any) -> bytes:
    value = response.to_dict()
    value.update(changes)
    return canonical_json(value)


__all__ = [
    "FAKE_ADAPTER_VERSION",
    "FAKE_MODEL_IDENTITY",
    "FakeAgent",
    "FakeAgentMode",
    "default_request",
]
