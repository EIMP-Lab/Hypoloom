"""ACI17 revision ancestry is not a scientific batch selection."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from hypoloom.agent_aci import (
    AgentACIError, AgentRequest, EmbeddedInput, canonical_json,
    validate_postflight,
)
from hypoloom.claude_agent_host import _output_schema, _prompt
from hypoloom.expert_batch import BatchHistory
from hypoloom.expert_loop import ResearchNodeTask
from hypoloom.fake_agent import FakeAgent
from tests.runtime.test_agent_aci import _hypothesis_fact
from tests.runtime.test_agent_aci14 import _request, _response
from tests.runtime.test_expert_batch import _batch, _fact, _selection


def _revision_raw(*, version=17):
    base = _request(cycle=2)
    previous = ResearchNodeTask(key=replace(base.task_context.key, cycle=1))
    lineage = next(item for item in base.input_artifacts if item.ref.endswith("--lineage-context"))
    inputs = [item for item in base.input_artifacts if item is not lineage]
    inputs.append(EmbeddedInput.from_bytes(lineage.ref, "application/json",
        canonical_json({"cycle": 2, "parent_hypothesis_id": None})))
    inputs.extend(_hypothesis_fact(parent, parent_hypothesis_id=None,
        method_family_id="family-current") for parent in ("parent-b", "parent-a"))
    inputs.append(EmbeddedInput.from_bytes(base.command_id + "--batch-context", "application/json",
        canonical_json({"batch_command_id": previous.command_id, "cycle": 1,
            "chains": {"chain-b": "parent-b", "chain-a": "parent-a"},
            "terminal_tips": {"chain-b": None, "chain-a": None}, "selected_tip": None})))
    raw = base.to_dict()
    raw.update(schema_version=version, input_artifacts=[item.to_dict() for item in inputs],
        immutable_context_refs=list(dict.fromkeys((*base.immutable_context_refs, *(item.ref for item in inputs)))))
    return raw


def _edit_input(raw, suffix, edit):
    index = next(index for index, item in enumerate(raw["input_artifacts"]) if item["ref"].endswith(suffix))
    item = EmbeddedInput.from_dict(raw["input_artifacts"][index])
    content = json.loads(item.content_bytes())
    edit(content)
    raw["input_artifacts"][index] = EmbeddedInput.from_bytes(item.ref, item.media_type, canonical_json(content)).to_dict()


def _candidate(request, parents=("parent-b", "parent-a")):
    template = FakeAgent().invoke(request).typed_candidate["hypotheses"][0]["candidate"]
    return {"schema_version": 1, "hypotheses": [{"chain_id": f"new-{index}",
        "candidate": {**template, "parent_hypothesis_id": parent, "relation": "revises failed parser"}}
        for index, parent in enumerate(parents)]}


def test_unselected_successor_admits_each_candidates_own_prior_batch_parent():
    request = AgentRequest.from_dict(_revision_raw())
    accepted = validate_postflight(request, _response(request, _candidate(request)))
    assert [member.candidate.parent_hypothesis_id for member in accepted.candidate.hypotheses] == ["parent-b", "parent-a"]
    assert AgentRequest.from_dict(request.to_dict()) == request
    validate_postflight(request, FakeAgent().invoke(request))


def test_assembled_synthesis_enumerates_revision_parents_without_a_winner():
    request = AgentRequest.from_dict(_revision_raw())
    candidate = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]["properties"]["hypotheses"]["items"]["properties"]["candidate"]
    assert candidate["properties"]["parent_hypothesis_id"] == {"enum": ["parent-a", "parent-b"]}
    assert candidate["properties"]["relation"] == {"type": "string", "minLength": 1}
    instruction = json.loads(_prompt(request, tool_workspace=True))["instruction"]
    assert "Each member may choose a different parent" in instruction
    assert "revision provenance, not scientific selection" in instruction
    assert "set both parent_hypothesis_id and relation to null" not in instruction


def test_single_failed_tip_remains_an_enum_revision_source_not_a_selection():
    raw = _revision_raw()
    def single_failed(value):
        value["chains"] = {"chain-a": "parent-a"}
        value["terminal_tips"] = {"chain-a": {
            "run_id": raw["run_id"], "component_id": raw["component_id"], "expert_slot": raw["role"],
            "chain_id": "chain-a", "node_id": "parent-a", "method_family_id": "family-current",
            "outcome": "EXECUTION_FAILURE", "metric_value": None}}
    _edit_input(raw, "--batch-context", single_failed)
    request = AgentRequest.from_dict(raw)
    candidate = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]["properties"]["hypotheses"]["items"]["properties"]["candidate"]
    assert candidate["properties"]["parent_hypothesis_id"] == {"enum": ["parent-a"]}
    validate_postflight(request, FakeAgent().invoke(request))
    context = json.loads(next(item for item in request.input_artifacts if item.ref.endswith("--batch-context")).content_bytes())
    assert context["selected_tip"] is None


def test_aci17_cycle_one_keeps_null_parent_and_relation():
    request = replace(_request(cycle=1), schema_version=17)
    candidate = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]["properties"]["hypotheses"]["items"]["properties"]["candidate"]
    assert candidate["properties"]["parent_hypothesis_id"] == {"const": None}
    assert candidate["properties"]["relation"] == {"const": None}
    validate_postflight(request, FakeAgent().invoke(request))


@pytest.mark.parametrize("parent", [None, "foreign-parent", "older-parent"])
def test_postflight_rejects_null_foreign_or_older_revision_parent(parent):
    request = AgentRequest.from_dict(_revision_raw())
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, _candidate(request, (parent,))))


@pytest.mark.parametrize("relation", [None, ""])
def test_postflight_requires_revision_relation(relation):
    request = AgentRequest.from_dict(_revision_raw())
    candidate = _candidate(request)
    candidate["hypotheses"][0]["candidate"]["relation"] = relation
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, candidate))


@pytest.mark.parametrize("mutation", [
    "missing_batch", "unbound_batch", "wrong_batch_ref", "wrong_batch_command", "wrong_cycle",
    "selected_tip", "empty_chains", "duplicate_members", "unsafe_chain", "missing_tip",
    "foreign_tip", "missing_fact", "unbound_fact", "foreign_run", "foreign_component",
    "foreign_role", "duplicate_fact", "unbound_lineage", "extra_field",
])
def test_revision_context_rejects_missing_or_foreign_sources(mutation):
    raw = _revision_raw()
    batch_ref = raw["command_id"] + "--batch-context"
    if mutation == "missing_batch":
        raw["input_artifacts"] = [item for item in raw["input_artifacts"] if item["ref"] != batch_ref]
    elif mutation in {"unbound_batch", "unbound_fact", "unbound_lineage"}:
        ref = batch_ref if mutation == "unbound_batch" else "fact-parent-a" if mutation == "unbound_fact" else next(item["ref"] for item in raw["input_artifacts"] if item["ref"].endswith("--lineage-context"))
        raw["immutable_context_refs"].remove(ref)
    elif mutation == "wrong_batch_ref":
        item = next(item for item in raw["input_artifacts"] if item["ref"] == batch_ref)
        item["ref"] = "foreign--batch-context"
        raw["immutable_context_refs"].append(item["ref"])
    elif mutation == "missing_fact":
        raw["input_artifacts"] = [item for item in raw["input_artifacts"] if item["ref"] != "fact-parent-a"]
    elif mutation.startswith("foreign_") and mutation != "foreign_tip":
        field = {"foreign_run": "run_id", "foreign_component": "component_id", "foreign_role": "proposer_role"}[mutation]
        _edit_input(raw, "fact-parent-a", lambda value: value["payload"].update({field: "foreign"}))
    elif mutation == "duplicate_fact":
        duplicate = deepcopy(next(item for item in raw["input_artifacts"] if item["ref"] == "fact-parent-a"))
        duplicate["ref"] = "duplicate-parent-fact"
        raw["input_artifacts"].append(duplicate)
        raw["immutable_context_refs"].append(duplicate["ref"])
        _edit_input(raw, duplicate["ref"], lambda value: (value.update(fact_id=duplicate["ref"]), value["payload"].update(fact_id=duplicate["ref"])))
    else:
        def edit(value):
            if mutation == "wrong_batch_command": value["batch_command_id"] = "foreign-command"
            elif mutation == "wrong_cycle": value["cycle"] = 2
            elif mutation == "selected_tip": value["selected_tip"] = {"node_id": "parent-a"}
            elif mutation == "empty_chains": value["chains"] = {}
            elif mutation == "duplicate_members": value["chains"]["chain-b"] = "parent-a"
            elif mutation == "unsafe_chain": value["chains"]["../foreign"] = value["chains"].pop("chain-a")
            elif mutation == "missing_tip": value["terminal_tips"].pop("chain-a")
            elif mutation == "foreign_tip": value["terminal_tips"]["chain-a"] = {"node_id": "foreign"}
            elif mutation == "extra_field": value["revision_parent"] = "parent-a"
        _edit_input(raw, "--batch-context", edit)
    with pytest.raises(AgentACIError):
        AgentRequest.from_dict(raw)


@pytest.mark.parametrize("version", [14, 15, 16])
def test_retained_successor_still_rejects_null_fixed_parent(version):
    with pytest.raises(AgentACIError):
        AgentRequest.from_dict(_revision_raw(version=version))


@pytest.mark.parametrize("version", [14, 16, 17])
def test_selected_successor_keeps_exact_parent_schema_and_postflight(version):
    request = replace(_request(cycle=2), schema_version=version)
    schema = _output_schema(request, tool_workspace=True)["properties"]["typed_candidate"]["properties"]["hypotheses"]["items"]["properties"]["candidate"]
    assert schema["properties"]["parent_hypothesis_id"] == {"const": "parent-1"}
    validate_postflight(request, FakeAgent().invoke(request))
    with pytest.raises(AgentACIError):
        validate_postflight(request, _response(request, _candidate(request, ("parent-a",))))


def _history_rows():
    selection = _selection(1, "a", "ha")
    payload = json.loads(selection["payload_json"])
    payload["adjudication"]["selected_tip"] = None
    selection["payload_json"] = json.dumps(payload)
    rows = [_batch(1, {"a": "ha", "b": "hb"}), selection, _batch(2, {"a": "h2a", "b": "h2b"})]
    facts = [_fact("ha"), _fact("hb"), _fact("h2a", "hb"), _fact("h2b", "ha")]
    for fact in facts:
        fact["payload"]["relation"] = "revises" if fact["payload"]["parent_hypothesis_id"] is not None else None
    return rows, facts


def test_batch_history_accepts_distinct_revision_parents_without_selecting_one():
    rows, facts = _history_rows()
    history = BatchHistory.from_rows("run", "c", "r", rows, facts, agent_aci_version=17)
    assert len(history.batches) == 2 and history.selected_tip is None
    assert history.hypothesis(2, "a")["payload"]["parent_hypothesis_id"] == "hb"
    assert history.hypothesis(2, "b")["payload"]["parent_hypothesis_id"] == "ha"


def test_batch_history_default_retains_no_selection_terminal_semantics():
    rows, facts = _history_rows()
    with pytest.raises(ValueError):
        BatchHistory.from_rows("run", "c", "r", rows, facts)


@pytest.mark.parametrize("mutation", ["foreign_parent", "null_parent", "missing_relation", "empty_relation", "missing_selection"])
def test_batch_history_rejects_unbound_revision_ancestry(mutation):
    rows, facts = _history_rows()
    if mutation == "foreign_parent": facts[-1]["payload"]["parent_hypothesis_id"] = "older-or-foreign"
    elif mutation == "null_parent": facts[-1]["payload"]["parent_hypothesis_id"] = None
    elif mutation == "missing_relation": facts[-1]["payload"].pop("relation")
    elif mutation == "empty_relation": facts[-1]["payload"]["relation"] = ""
    else: rows.pop(1)
    with pytest.raises(ValueError):
        BatchHistory.from_rows("run", "c", "r", rows, facts, agent_aci_version=17)
