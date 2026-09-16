from dataclasses import replace
import json
from pathlib import Path

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.agent_sessions import AgentSessions
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind
from hypoloom.expert_memory import ExpertMemory
from hypoloom.fake_agent import default_request
from hypoloom.full_cc_runtime import full_cc_argv
from hypoloom.git_authoring_workspace import GitAuthoringIntegrityError
from test_agent_sessions import _manager


def _leader_request(manager):
    base = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    task = replace(base.task_context, key=replace(base.task_context.key, subkind=ResearchNodeSubkind.SYNTHESIS))
    memory = ExpertMemory(manager.state_root)
    peer = memory.ensure(base.run_id, "peer")
    data = {"source_attempt_id": "observer-accepted", "role": base.role, "component_id": base.component_id,
        "cycle": 1, "peer_memory_commits": {"peer": peer}}
    item = EmbeddedInput.from_bytes("node--leader-resources", "application/json", json.dumps(data).encode())
    request = replace(base, schema_version=14, task_context=task, command_id=task.command_id,
        required_output_schema=required_output_schema(base.command, task, schema_version=14),
        input_artifacts=(item,), immutable_context_refs=(item.ref,))
    return request, data


def test_only_designated_leader_synthesis_reads_exact_frozen_peer_memory(tmp_path):
    manager = _manager(tmp_path); request, data = _leader_request(manager)
    prepared = manager.prepare(request, None)
    try:
        assert prepared.peer_memory_root is not None
        expected = ExpertMemory(manager.state_root).files(request.run_id, "peer", data["peer_memory_commits"]["peer"])
        assert {path.relative_to(prepared.peer_memory_root / "peer").as_posix(): path.read_bytes()
            for path in (prepared.peer_memory_root / "peer").rglob("*") if path.is_file()} == expected
        argv = full_cc_argv(bwrap=Path("/usr/bin/bwrap"), executable=tmp_path / "claude", request=request,
            model="exact-model", workspace=prepared, provider_socket=tmp_path / "provider.sock", host_socket=None,
            output_schema={}, extra_paths=())
        triples = set(zip(argv, argv[1:], argv[2:]))
        assert ("--ro-bind", str(prepared.peer_memory_root), "/peer-memory") in triples
        assert ("--bind", str(prepared.peer_memory_root), "/peer-memory") not in triples
        retry = replace(request, attempt_id="retry", attempt_ordinal=2)
        replay = AgentSessions(manager.state_root, manager.publisher).prepare(retry, None)
        assert (replay.peer_memory_root / "peer/MEMORY.md").read_bytes() == expected["MEMORY.md"]
        replay.cleanup()
    finally: prepared.cleanup()


@pytest.mark.parametrize("field,value", [("role", "other"), ("component_id", "other"), ("cycle", 2),
    ("source_attempt_id", "../other"), ("peer_memory_commits", {"scientist": "a" * 40})])
def test_leader_memory_binding_cannot_change_scope_or_claim_own_memory_as_peer(tmp_path, field, value):
    manager = _manager(tmp_path); request, data = _leader_request(manager)
    data[field] = value
    item = EmbeddedInput.from_bytes("node--leader-resources", "application/json", json.dumps(data).encode())
    with pytest.raises((GitAuthoringIntegrityError, ValueError)):
        manager.prepare(replace(request, input_artifacts=(item,)), None)


def test_leader_memory_is_not_granted_to_an_ordinary_proposal_substage(tmp_path):
    manager = _manager(tmp_path); request, _ = _leader_request(manager)
    task = replace(request.task_context, key=replace(request.task_context.key, subkind=ResearchNodeSubkind.FORWARD))
    request = replace(request, task_context=task, command_id=task.command_id,
        required_output_schema=required_output_schema(request.command, task, schema_version=14))
    with pytest.raises(GitAuthoringIntegrityError, match="SYNTHESIS"):
        manager.prepare(request, None)
