import json
from dataclasses import replace
from uuid import UUID

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.agent_sessions import AgentSessions
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import AuxiliaryTask, AuxiliaryRequestKind, ResearchNodeSubkind
from hypoloom.fake_agent import default_request
from test_agent_sessions import _manager, _seed, _chain_request, _transcript
from test_git_authoring_workspace import make_target


def _source_row(workspace, metadata):
    request = workspace.request
    return {"run_id": request.run_id, "attempt_id": request.attempt_id, "kind": "AGENT", "state": "CLOSED",
        "terminal_reason": "SUCCEEDED", "command_id": request.command_id, "attempt_ordinal": request.attempt_ordinal,
        "observation_json": json.dumps({"request": request.to_dict(), "session_binding": workspace.binding, "session": metadata})}


def _setup(tmp_path, *, with_seed=False, version=14):
    target, target_binding = make_target(tmp_path)
    manager = _manager(tmp_path); seed = _seed(manager, target_binding)
    request = _chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "original-design", "chain-a")
    hypothesis = EmbeddedInput.from_bytes("source-hypothesis", "application/json", json.dumps({
        "fact_type": "HypothesisProposed", "hypothesis_id": "source-hypothesis",
        "component_id": request.component_id, "proposer_role": request.role}).encode())
    request = replace(request, schema_version=version, input_artifacts=(hypothesis,), immutable_context_refs=(hypothesis.ref,))
    original = manager.prepare(request, target_binding)
    (original.worktree_root / "experiment.py").write_text("print('original-debate-source')\n")
    _transcript(original, "Actual original expert conversation.")
    original_metadata = original.finish_node()
    context = {"debate_id": "debate-17", "round": 1,
        "participant": {"scope": {"role": request.role, "component_id": request.component_id, "chain": "chain-a"}, "hypothesis_id": "source-hypothesis"},
        "original_attempt_id": request.attempt_id, "original_session_id": original.binding["session_id"],
        "session_id": str(UUID(int=1701)), "previous_opponent_attempt_id": None}
    result = manager, target_binding, original, _source_row(original, original_metadata), context
    return (*result, seed) if with_seed else result


def _request(original, source, context, number):
    request = original.request
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE, run_id=request.run_id, expert_slot=request.role,
        component_id=request.component_id, cycle=1, ordinal=17 * 3 + number)
    context = {**context, "round": number, "previous_opponent_attempt_id": None if number == 1 else f"peer-round-{number-1}"}
    inputs = (EmbeddedInput.from_bytes(context["debate_id"] + "--debate-context", "application/json", json.dumps(context).encode()),
        EmbeddedInput.from_bytes(source["attempt_id"], "application/json", json.dumps(source).encode()))
    return replace(default_request(AgentCommand.DEBATE), schema_version=request.schema_version, role=request.role, component_id=request.component_id,
        task_context=task, command_id=task.command_id, attempt_id=f"debate-round-{number}", input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs), required_output_schema=required_output_schema(AgentCommand.DEBATE, task, schema_version=14))


@pytest.mark.parametrize("version", [14, 24])
def test_three_debate_rounds_fork_actual_archived_session_and_resume_after_host_reconstruction(tmp_path, version):
    manager, binding, original, source, context = _setup(tmp_path, version=version)
    first = manager.prepare(_request(original, source, context, 1), binding)
    try:
        assert first.binding["session_id"] == context["session_id"]
        assert first.binding["parent_session_id"] == original.binding["session_id"]
        assert first.session_arguments == ("--resume", original.binding["session_id"], "--fork-session", "--session-id", context["session_id"])
        assert not first.writable and first.worktree_root != original.worktree_root
        assert b"original-debate-source" in (first.worktree_root / "experiment.py").read_bytes()
        assert (first.session_root / (original.binding["session_id"] + ".jsonl")).read_bytes() == (
            manager.publisher.root / source["run_id"] / "agent" / source["attempt_id"] / "transcript/transcript.jsonl").read_bytes()
        _transcript(first, "Actual first debate round."); first.finish_node()
    finally: first.cleanup()
    for number in (2, 3):
        restarted = AgentSessions(manager.state_root, manager.publisher)
        current = restarted.prepare(_request(original, source, context, number), binding)
        try:
            assert current.session_arguments == ("--resume", context["session_id"])
            assert current.scope_root == first.scope_root
            _transcript(current, f"Actual round {number}.", append=True); current.finish_node()
        finally: current.cleanup()
    assert b"Actual original expert conversation" in (original.session_root / (original.binding["session_id"] + ".jsonl")).read_bytes()


@pytest.mark.parametrize("failure", ["foreign_role", "foreign_chain", "foreign_session", "unaccepted_source"])
def test_debate_fork_rejects_foreign_or_unaccepted_source(tmp_path, failure):
    manager, binding, original, source, context = _setup(tmp_path)
    if failure == "foreign_role": context["participant"]["scope"]["role"] = "other"
    elif failure == "foreign_chain": context["participant"]["scope"]["chain"] = "other-chain"
    elif failure == "foreign_session": context["original_session_id"] = str(UUID(int=999))
    else: source["state"] = "OPEN"
    with pytest.raises(ValueError): manager.prepare(_request(original, source, context, 1), binding)


@pytest.mark.parametrize("version", [14, 24])
def test_winning_fork_is_the_next_synthesis_session_and_the_parent_of_new_inner_chains(tmp_path, version):
    from test_agent_aci import _pincer_request
    manager, binding, original, source, context = _setup(tmp_path, version=version)
    for number in (1, 2, 3):
        current = manager.prepare(_request(original, source, context, number), binding)
        _transcript(current, f"Accepted debate round {number}.", append=number > 1)
        metadata = current.finish_node(); current.cleanup()
    winner = _source_row(current, metadata)
    promotion = {"debate_id": context["debate_id"], "source_judge_attempt_id": "actual-judge",
        "role": original.request.role, "component_id": original.request.component_id, "cycle": 2,
        "original_session_id": context["original_session_id"], "session_id": context["session_id"],
        "source_attempt_id": winner["attempt_id"]}
    inputs = (EmbeddedInput.from_bytes("winner--session-promotion", "application/json", json.dumps(promotion).encode()),
        EmbeddedInput.from_bytes(winner["attempt_id"], "application/json", json.dumps(winner).encode()),
        EmbeddedInput.from_bytes("winner--lineage-context", "application/json", json.dumps({"cycle": 2, "parent_hypothesis_id": "source-hypothesis"}).encode()))
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    task = replace(request.task_context, key=replace(request.task_context.key, cycle=2))
    request = replace(request, schema_version=version, task_context=task, command_id=task.command_id, attempt_id="promoted-synthesis",
        input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(request.command, task, schema_version=14))
    synthesis = AgentSessions(manager.state_root, manager.publisher).prepare(request, binding)
    try:
        assert synthesis.binding["scope"] == "synthesis"
        if version < 24:
            assert synthesis.binding["session_id"] == context["session_id"]
            assert synthesis.session_arguments == ("--resume", context["session_id"])
            assert b"Accepted debate round 3" in (synthesis.session_root / (context["session_id"] + ".jsonl")).read_bytes()
        else:
            assert synthesis.binding["session_id"] != context["session_id"]
            assert synthesis.binding["parent_session_id"] is None
            assert synthesis.session_arguments == ("--session-id", synthesis.binding["session_id"])
            assert not (synthesis.session_root / (synthesis.binding["session_id"] + ".jsonl")).exists()
            assert b"original-debate-source" in (synthesis.worktree_root / "experiment.py").read_bytes()
        _transcript(synthesis, "Proposed a new hypothesis from the winning discussion.", append=version < 24)
        synthesis.finish_node()
        inner = _chain_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT, "new-inner", "new-chain")
        task = replace(inner.task_context, key=replace(inner.task_context.key, cycle=2))
        inner = replace(inner, schema_version=version, task_context=task, command_id=task.command_id,
            input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs))
        prepared = manager.prepare(inner, binding)
        try:
            if version < 24:
                assert prepared.binding["parent_session_id"] == synthesis.binding["session_id"]
                assert prepared.session_arguments == ("--resume", context["session_id"], "--fork-session", "--session-id", prepared.binding["session_id"])
            else:
                assert prepared.binding["parent_session_id"] is None
                assert prepared.session_arguments == ("--session-id", prepared.binding["session_id"])
                assert b"original-debate-source" in (prepared.worktree_root / "experiment.py").read_bytes()
        finally: prepared.cleanup()
    finally: synthesis.cleanup()
