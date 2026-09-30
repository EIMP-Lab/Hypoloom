import base64
import copy
import json
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.oracle import _raw_artifact_digest
from hypoloom.session_oracle import audit_sessions
from test_agent_aci import _pincer_request
from test_agent_sessions import _chain_request, _transcript
from test_debate_sessions import _setup, _request, _source_row


@pytest.fixture(params=[14, 29])
def history(tmp_path, request):
    version = request.param
    manager, target, original, source, context, seed = _setup(tmp_path, with_seed=True, version=version)
    rows, payloads = [], {}
    def record(workspace):
        workspace.request = replace(workspace.request, required_adapter_version="claude-code-bwrap/36")
        row = _source_row(workspace, workspace.finish_node())
        row.update(started_at=len(rows) * 2, ended_at=len(rows) * 2 + 1, process_group_id=12345)
        rows.append(row); payloads[row["attempt_id"]] = json.loads(row["observation_json"])
        return row
    record(seed); source = record(original)
    for number in (1, 2, 3):
        request = replace(_request(original, source, context, number), required_adapter_version="claude-code-bwrap/36")
        workspace = manager.prepare(request, target)
        _transcript(workspace, f"Accepted round {number}.", append=number > 1 and version < 29)
        winner = record(workspace); workspace.cleanup()
    promotion = {"debate_id": context["debate_id"], "source_judge_attempt_id": "actual-judge", "role": original.request.role,
        "component_id": original.request.component_id, "cycle": 2, "original_session_id": context["original_session_id"],
        "session_id": json.loads(winner["observation_json"])["session"]["session_id"], "source_attempt_id": winner["attempt_id"]}
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS, cycle=2, parent_hypothesis_id="source-hypothesis")
    inputs = (*request.input_artifacts,
        EmbeddedInput.from_bytes("winner--session-promotion", "application/json", json.dumps(promotion).encode()),
        EmbeddedInput.from_bytes(winner["attempt_id"], "application/json", json.dumps(winner).encode()))
    request = replace(request, schema_version=version, required_adapter_version="claude-code-bwrap/36", attempt_id="promoted-synthesis",
        input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(request.command, request.task_context, schema_version=14))
    workspace = manager.prepare(request, target); _transcript(workspace, "Promoted winning context.", append=version < 24)
    record(workspace); workspace.cleanup()
    request = _chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "next-inner", "new-chain")
    task = replace(request.task_context, key=replace(request.task_context.key, cycle=2))
    request = replace(request, schema_version=version, required_adapter_version="claude-code-bwrap/36", task_context=task, command_id=task.command_id,
        input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs))
    workspace = manager.prepare(request, target); _transcript(workspace, "New scientific inner chain.")
    record(workspace); workspace.cleanup()
    return manager, rows, payloads


def test_independent_session_oracle_accepts_three_rounds_promotion_and_new_chain_parent(history):
    manager, rows, payloads = history
    assert audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest) == ([], 7)


@pytest.mark.parametrize("failure", ["copied_source", "source_not_closed", "wrong_fork", "promotion_cycle", "promotion_source", "promotion_forks_again"])
def test_debate_session_oracle_rejects_forged_source_and_promotion(history, failure):
    manager, original_rows, original_payloads = history
    rows, payloads = copy.deepcopy(original_rows), copy.deepcopy(original_payloads)
    if failure == "source_not_closed": rows[1]["state"] = "OPEN"
    elif failure == "wrong_fork": payloads["debate-round-2"]["session_binding"]["parent_session_id"] = "00000000-0000-0000-0000-000000000001"
    elif failure == "promotion_forks_again":
        current = payloads["promoted-synthesis"]
        current["session"]["launch_arguments"] = ["--resume", current["session_binding"]["parent_session_id"], "--fork-session", "--session-id", current["session_binding"]["session_id"]]
    else:
        request = payloads["debate-round-1" if failure == "copied_source" else "promoted-synthesis"]["request"]
        source = next(item for item in request["input_artifacts"] if item["ref"] == "original-design") if failure == "copied_source" else next(item for item in request["input_artifacts"] if item["ref"].endswith("--session-promotion"))
        value = json.loads(base64.b64decode(source["content_base64"]))
        if failure == "copied_source": value["observation_json"] = "{}"
        elif failure == "promotion_cycle": value["cycle"] = 3
        else: value["source_attempt_id"] = "debate-round-2"
        source["content_base64"] = base64.b64encode(json.dumps(value).encode()).decode()
    issues, _ = audit_sessions(manager.publisher.root, rows, payloads, _raw_artifact_digest)
    assert {item[0] for item in issues} & {"session_transfer_source_invalid", "session_binding_invalid", "session_launch_mismatch"}
