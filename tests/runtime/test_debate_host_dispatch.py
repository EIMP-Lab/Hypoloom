"""Host dispatch seams; these do not impersonate actual Debate sessions."""
from types import SimpleNamespace

import pytest

from hypoloom.agent_aci import EmbeddedInput
from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeKind, ResearchNodeSubkind
from hypoloom.workflow_host import WorkflowHost
from tests.runtime.test_expert_batch_host import batch_host as _batch_host

batch_host = _batch_host


@pytest.mark.parametrize("kind", [AuxiliaryRequestKind.DEBATE, AuxiliaryRequestKind.DEBATE_JUDGE])
def test_host_dispatches_exact_debate_command_and_its_delegated_context(batch_host, monkeypatch, kind):
    import json
    host = batch_host; host._activate("batch-host")
    neutral = kind is AuxiliaryRequestKind.DEBATE_JUDGE
    task = AuxiliaryTask(kind=kind, run_id="batch-host", cycle=1, ordinal=22,
        component_id=None if neutral else "primary.validation_accuracy",
        expert_slot=None if neutral else "ml-researcher")
    role = "grounding" if neutral else "ml-researcher"
    component = "grounding" if neutral else "primary.validation_accuracy"
    supplied = (EmbeddedInput.from_bytes("dispatch-seam", "application/json", b'{}'),)
    monkeypatch.setattr(host.interaction.debate, "inputs", lambda run, selected: supplied if selected == task else ())
    monkeypatch.setattr(host.agent, "invoke", lambda request: b"")
    monkeypatch.setattr(host, "_finish_fake_agent", lambda request, response: None)
    host._expert_agent_step("batch-host", "batch-host", task, role, component)
    row = host._expert_attempts(task)[0]
    request = json.loads(row["observation_json"])["request"]
    assert request["command"] == kind.value
    assert request["input_artifacts"] == [item.to_dict() for item in supplied]


@pytest.mark.parametrize("version", [25, 26])
def test_authorized_debate_restart_starts_forward_without_a_synthetic_literature_node(version):
    dispatched = []
    plan = {"role": "researcher", "component_id": "component", "cycle": 2,
        "parent_hypothesis_id": "accepted-parent", "source_cycle": 1,
        "source_literature_attempt_id": "actual-literature", "source_literature_command_id": "actual-command",
        "restart_from_hypothesis": True}
    host = SimpleNamespace(
        store=SimpleNamespace(get_run=lambda run: {"agent_aci_version": str(version)}),
        _batch_enabled=lambda _run: False,
        _research_contract=lambda run: SimpleNamespace(role_ids=("researcher",), components=()),
        _expert_hypothesis_chain=lambda *args: ({"hypothesis_id": "accepted-parent"},),
        _expert_success=lambda task: {} if isinstance(task, AuxiliaryTask) else None,
        _expert_task_exhausted=lambda *args: False,
        _expert_agent_step=lambda *args: dispatched.append(args),
        _expert_attempts=lambda task: (),
        interaction=SimpleNamespace(debate=SimpleNamespace(designated_round=lambda *args: plan)))
    WorkflowHost._drive_expert_proposal(host, "run", "component", "researcher", cycle=2,
        parent_hypothesis_id="accepted-parent", restart_from_hypothesis=True)
    assert len(dispatched) == 1
    task = dispatched[0][2]
    assert task.key.node_kind is ResearchNodeKind.HYPOTHESIS_PROPOSAL
    assert task.key.subkind is ResearchNodeSubkind.FORWARD


@pytest.mark.parametrize("version,irreducible,expected", [(28, False, None), (29, False, "INFRASTRUCTURE"), (29, True, None)])
def test_debate_context_failure_uses_existing_bounded_attempt_budget(batch_host, monkeypatch, version, irreducible, expected):
    import json
    from dataclasses import replace
    from hypoloom.domain import AttemptTerminalReason
    from hypoloom.agent_aci import AgentRequest
    host = batch_host
    host._activate("batch-host")
    task = AuxiliaryTask(kind=AuxiliaryRequestKind.DEBATE, run_id="batch-host", cycle=1,
        ordinal=22, component_id="primary.validation_accuracy", expert_slot="ml-researcher")
    monkeypatch.setattr(host.interaction.debate, "inputs", lambda *args: ())
    monkeypatch.setattr(host.agent, "invoke", lambda request: b"")
    monkeypatch.setattr(host, "_finish_fake_agent", lambda *args: None)
    host._expert_agent_step("batch-host", "batch-host", task, task.expert_slot, task.component_id)
    row = host._expert_attempts(task)[0]
    request = replace(AgentRequest.from_dict(json.loads(row["observation_json"])["request"]), schema_version=version)
    failure = {"code": "context_capacity", "source": "local_context_limit"}
    if irreducible: failure["recovery_action"] = "input_reconfiguration_required"
    host._agent_failure(request, AttemptTerminalReason.FAILED, "nonzero_exit", retryable=True,
        details={"diagnostics": {"provider_failure": failure}})
    observed = json.loads(host.store.get_attempt(request.attempt_id)["observation_json"])
    assert observed["retry_class"] == expected
    assert observed["failure_stage"] == "context_reconstruction"
    assert host._expert_task_exhausted("batch-host", task) is (expected is None)
