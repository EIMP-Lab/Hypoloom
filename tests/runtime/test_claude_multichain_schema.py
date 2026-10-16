"""Native JSON-schema projection of the existing ACI14 candidate contracts."""

import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import AgentACIError, EmbeddedInput
from hypoloom.claude_agent_host import AgentHostError, _output_schema
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.fake_agent import default_request

from test_agent_aci import _pincer_request
from test_agent_aci14 import _request


@pytest.mark.parametrize("cycle", (1, 2))
def test_native_synthesis_schema_wraps_complete_bound_hypotheses_without_changing_legacy(cycle):
    successor = _request(cycle=cycle)
    schema = _output_schema(successor)["properties"]["typed_candidate"]
    assert set(schema["required"]) == {"schema_version", "hypotheses"}
    assert schema["properties"]["schema_version"] == {"const": 1}
    members = schema["properties"]["hypotheses"]
    assert "Nonempty" in members["description"]
    assert "minItems" not in members  # Existing provider-strict compatibility.
    assert set(members["items"]["required"]) == {"chain_id", "candidate"}
    hypothesis = members["items"]["properties"]["candidate"]
    legacy = _pincer_request(ResearchNodeSubkind.SYNTHESIS, cycle=cycle,
        parent_hypothesis_id="parent-1" if cycle == 2 else None)
    assert hypothesis == _output_schema(legacy)["properties"]["typed_candidate"]
    assert hypothesis["properties"]["parent_hypothesis_id"] == {"const": "parent-1" if cycle == 2 else None}
    assert members["items"]["additionalProperties"] is False
    assert schema["additionalProperties"] is False


def test_native_batch_measurement_schema_stays_inside_each_existing_hypothesis():
    request = _request()
    config = EmbeddedInput.from_bytes("node--expert-config", "application/json",
        json.dumps({"component": {"result": {"deferred": True}}}).encode())
    request = replace(request, input_artifacts=(*request.input_artifacts, config))
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert "measurement" not in schema["properties"]
    hypothesis = schema["properties"]["hypotheses"]["items"]["properties"]["candidate"]
    assert "measurement" in hypothesis["required"]
    assert {"metric", "comparison", "parse_rule", "parser_argv", "parser_files", "raw_artifacts"} == set(hypothesis["properties"]["measurement"]["required"])


def test_native_adjudication_schema_has_exact_verdict_and_selection_shape():
    request = _request(AgentCommand.ADJUDICATE)
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert set(schema["required"]) == {"schema_version", "verdicts", "keep", "prune", "best_chain", "summary"}
    assert set(schema["properties"]) == set(schema["required"])
    assert schema["additionalProperties"] is False
    verdict = schema["properties"]["verdicts"]["items"]
    assert set(verdict["required"]) == {"chain_id", "node_id", "status", "evidence"}
    assert verdict["properties"]["status"]["enum"] == ["verified", "refuted", "inconclusive"]
    assert set(schema["properties"]["best_chain"]["type"]) == {"string", "null"}
    assert schema["properties"]["keep"]["uniqueItems"] is True
    assert schema["properties"]["prune"]["uniqueItems"] is True


@pytest.mark.parametrize("mutation", (None, "fact-id", "input-ref", "swapped-node", "foreign-chain", "best-without-tip"))
def test_adjudication_projects_batch_pairs_and_absent_terminal_tips(mutation):
    from hypoloom.native_output import decode_output
    request = _request(AgentCommand.ADJUDICATE)
    context = EmbeddedInput.from_bytes(request.command_id + "--batch-context", "application/json",
        json.dumps({"chains": {"chain-a": "hyp-a", "chain-b": "hyp-b"},
                    "terminal_tips": {"chain-a": None, "chain-b": None}}).encode())
    request = replace(request, input_artifacts=(*request.input_artifacts, context),
        immutable_context_refs=(*request.immutable_context_refs, context.ref))
    schema = _output_schema(request)["properties"]["typed_candidate"]
    candidate = {"schema_version": 1,
        "verdicts": [{"chain_id": chain, "node_id": node, "status": "inconclusive", "evidence": [context.ref]}
                     for chain, node in (("chain-a", "hyp-a"), ("chain-b", "hyp-b"))],
        "keep": ["chain-a", "chain-b"], "prune": [], "best_chain": None,
        "summary": "No accepted comparable terminal tip."}
    if mutation == "fact-id":
        candidate["verdicts"][0]["node_id"] = "fact-123"
    elif mutation == "input-ref":
        candidate["verdicts"][0]["node_id"] = context.ref
    elif mutation == "swapped-node":
        candidate["verdicts"][0]["node_id"] = "hyp-b"
    elif mutation == "foreign-chain":
        candidate["verdicts"][0]["chain_id"] = "hyp-a"
    elif mutation == "best-without-tip":
        candidate["best_chain"] = "chain-a"
    if mutation is None:
        assert decode_output(json.dumps(candidate), schema) == candidate
    else:
        with pytest.raises(ValueError):
            decode_output(json.dumps(candidate), schema)


@pytest.mark.parametrize("version", (13, 14))
def test_native_result_analysis_iteration_is_required_only_on_successor(version):
    request = replace(default_request(AgentCommand.RESULT_ANALYSIS), schema_version=version)
    schema = _output_schema(request)["properties"]["typed_candidate"]
    if version == 14:
        assert "iteration_disposition" in schema["required"]
        assert schema["properties"]["iteration_disposition"]["enum"] == ["ITERATE", "COMPLETE"]
    else:
        assert "iteration_disposition" not in schema["properties"]


def test_native_manager_schema_preserves_exact_roles_per_role_limits_and_refs():
    from test_grounding_manager import _request as manager_request
    request = manager_request()
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert set(schema["properties"]) == set(schema["required"]) == {
        "task_kind", "summary", "supporting_refs", "graph_evolution"}
    assert schema["properties"]["task_kind"] == {"const": "GRAPH_EVOLVE"}
    assert schema["properties"]["supporting_refs"]["items"]["enum"] == list(request.immutable_context_refs)
    changes = schema["properties"]["graph_evolution"]
    assert "minItems" not in changes
    item = changes["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"]) == {"role", "effective_max_forks", "reason"}
    assert item["properties"]["role"]["enum"] == ["peer", "scientist"]
    assert item["properties"]["effective_max_forks"] == {"type": "integer", "minimum": 1}
    assert {branch["properties"]["role"]["const"]: branch["properties"]["effective_max_forks"]["maximum"]
            for branch in item["oneOf"]} == {"peer": 2, "scientist": 3}


def test_native_disabled_manager_schema_requires_an_empty_graph_proposal():
    from test_grounding_manager import _request as manager_request
    request = manager_request()
    item = request.input_artifacts[0]
    context = json.loads(item.content_bytes())
    context["enabled"] = False
    request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(item.ref,
        item.media_type, json.dumps(context).encode()),))
    schema = _output_schema(request)["properties"]["typed_candidate"]
    assert schema["properties"]["graph_evolution"] == {"const": []}


@pytest.mark.parametrize("invalid", ("missing", "duplicate", "bool-limit", "old-version"))
def test_native_manager_schema_reuses_the_existing_context_validator(invalid):
    from test_grounding_manager import _request as manager_request
    request = manager_request()
    if invalid == "missing":
        request = replace(request, input_artifacts=())
    elif invalid == "duplicate":
        item = request.input_artifacts[0]
        request = replace(request, input_artifacts=(*request.input_artifacts,
            EmbeddedInput.from_bytes("other--manager-context", item.media_type, item.content_bytes())))
    elif invalid == "old-version":
        with pytest.raises(AgentACIError, match="unknown_command"):
            replace(request, schema_version=13)
        return
    else:
        item = request.input_artifacts[0]
        context = json.loads(item.content_bytes())
        context["max_hypotheses_per_round"]["peer"] = True
        request = replace(request, input_artifacts=(EmbeddedInput.from_bytes(item.ref,
            item.media_type, json.dumps(context).encode()),))
    with pytest.raises(AgentHostError):
        _output_schema(request)
