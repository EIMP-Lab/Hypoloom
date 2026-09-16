"""Fresh Host controls select native Git plugins without rewriting old cycles."""
import copy
import json
import time
from dataclasses import replace

import pytest

from hypoloom.agent_aci import EmbeddedInput, required_output_schema
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, AttemptTerminalReason
from hypoloom.expert_loop import AuxiliaryRequestKind, AuxiliaryTask, ResearchNodeKey, ResearchNodeKind, ResearchNodeSubkind, ResearchNodeTask
from hypoloom.expert_plugin import ExpertPluginStore
from hypoloom.fake_agent import default_request
from hypoloom.lifecycle import Command, RunState
from hypoloom.workflow_host import WorkflowHost
from test_expert_loop_host import _pilot_payload
from test_expert_loop_host import mount_root as mount_root

RUN, ROLE, LIMIT = "plugin-host", "ml-researcher", 1024 * 1024


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    target = tmp_path / "target"
    target.mkdir()
    payload = _pilot_payload(tmp_path, target, RUN)
    extra = copy.deepcopy(payload["research_contract"]["components"][0])
    extra.update(id="must.secondary", tier="must", name="secondary")
    payload["research_contract"]["components"].append(extra)
    payload["research_contract"]["protocols"]["shared-accuracy"] = {"result_kind": "metric", "requirements": {"required_metrics": ["accuracy"]}}
    for component in payload["research_contract"]["components"]:
        component["protocol"] = "shared-accuracy"
    payload["research_contract"]["roles"][0]["protocols"] = {"default": "shared-accuracy", "allowed": ["shared-accuracy"]}
    payload["research_contract"]["goal"]["summary_metric"] = {"name": "required_component_completion", "direction": "MAXIMIZE", "target": "2", "display_only": True}
    instance = WorkflowHost(tmp_path / "state")
    instance.initialize()
    response = instance.handle(ControlRequest(schema_version=1, request_id="create-plugin-host", expected_revision=0,
        command="CREATE_RUN", payload=payload))
    assert response.status == "accepted", response
    try:
        yield instance
    finally:
        instance.close()


def test_native_skill_reads_host_pinned_then_explicitly_unpinned_revision(host, tmp_path):
    import secrets
    from pathlib import Path
    from hypoloom.agent_sessions import AgentSessions
    from hypoloom.claude_agent_host import ClaudeAgentHost
    from hypoloom.execution_workspace import GitTargetBinding
    from hypoloom.expert_memory import ExpertMemory
    from test_agent_aci import _pincer_request
    from test_claude_file_tool_cli import _installed_claude, _tool_results, _result_text
    from test_full_cc_adapter import _invoke_adapter, _request as native_request
    from test_full_cc_host_g3 import _start_provider

    class Scenario:
        def __init__(self, expected):
            self.expected = expected
            self.requests, self.errors = [], []
            self.structured_output, self.stage_step = {}, 0
            self.evidence = []

        def record(self, document):
            self.requests.append(document)
            self.stage_step += 1
            try:
                rendered = json.dumps(document)
                if self.stage_step == 1:
                    assert marker not in rendered
                elif self.stage_step == 2:
                    assert not _tool_results(document)["native_plugin_skill"].get("is_error")
                    assert (marker in rendered) is self.expected
                elif self.stage_step == 3:
                    result = _tool_results(document)["native_plugin_read"]
                    assert not result.get("is_error"), result
                    self.evidence.append(_result_text(result))
                    assert "PIN_CONTROL_NATIVE_READ_OK" in self.evidence[-1]
            except Exception as exc:
                self.errors.append(f"{self.stage_step}: {type(exc).__name__}: {exc}")
            return len(self.requests)

        def tool_calls(self, _number):
            if self.errors:
                return ()
            if self.stage_step == 1:
                return (("native_plugin_skill", "Skill", {"skill": "hypoloom-ml-researcher:self-evolve"}),)
            if self.stage_step == 2:
                script = ("import os\nfrom pathlib import Path\n"
                    "p=Path('/expert-plugin/skills/self-evolve/SKILL.md')\n"
                    f"assert ({marker!r} in p.read_text()) is {self.expected!r}\n"
                    "assert os.statvfs(p).f_flag & os.ST_RDONLY\n"
                    "assert not Path('/plugin-update').exists() and not Path('/state').exists()\n"
                    "print('PIN_CONTROL_NATIVE_READ_OK')\n")
                return (("native_plugin_read", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 10000}),)
            return (("finish_plugin_control", "StructuredOutput", self.structured_output),)

    executable = _installed_claude()
    marker = "NATIVE_PLUGIN_CANDIDATE_" + secrets.token_hex(16)
    seed = _binding(host, _task(host, 1))
    candidate = _candidate(host, seed, marker)
    assert _control(host, "PIN", seed.commit).status == "accepted"
    manager = AgentSessions(host.state_root, host.publisher)
    target = host._run_config(RUN)["execution"]["target"]
    target = GitTargetBinding(checkout=Path(target["checkout"]), commit=target["commit"])
    memory = ExpertMemory(host.state_root).ensure(RUN, ROLE)
    evidence = []
    for cycle, expected in ((1, False), (2, True)):
        if expected:
            response = _control(host, "UNPIN", candidate.commit)
            assert response.status == "accepted", response
        task = _task(host, cycle, node=ResearchNodeKind.HYPOTHESIS_PROPOSAL)
        task = replace(task, key=replace(task.key, subkind=ResearchNodeSubkind.SYNTHESIS))
        plugin = _binding(host, task)
        assert plugin.commit == (candidate.commit if expected else seed.commit)
        base = _pincer_request(ResearchNodeSubkind.SYNTHESIS, cycle=cycle,
            parent_hypothesis_id="actual-fixture-parent" if cycle > 1 else None)
        resource = EmbeddedInput.from_bytes(task.command_id + "--expert-resources", "application/json",
            json.dumps({"plugin": plugin.to_dict(), "memory_commit": memory}).encode())
        inputs = (*base.input_artifacts, resource)
        request = native_request(replace(base, schema_version=14, run_id=RUN, owner_workflow_id=RUN, role=ROLE,
            component_id=task.key.component_id, task_context=task, command_id=task.command_id,
            input_artifacts=inputs, immutable_context_refs=tuple(item.ref for item in inputs),
            required_output_schema=required_output_schema(base.command, task, schema_version=14)),
            "plugin-unpinned" if expected else "plugin-pinned", None)
        scenario = Scenario(expected)
        key = "host-only-plugin-control-" + secrets.token_hex(12)
        provider, thread = _start_provider(scenario, key)
        try:
            workspace = manager.prepare(request, target)
            _, metadata = _invoke_adapter(ClaudeAgentHost(claude_executable=executable), request, workspace,
                scenario, {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"},
                tmp_path / ("native-unpinned" if expected else "native-pinned"))
            assert metadata["transcript_complete"] and not scenario.errors
            assert len(scenario.requests) == 3
            evidence.append({"request": request.to_dict(), "metadata": metadata, "tool_results": scenario.evidence,
                "provider_requests": scenario.requests, "provider_errors": scenario.errors})
        finally:
            (tmp_path / f"native-provider-{cycle}.json").write_text(json.dumps({"requests": scenario.requests, "errors": scenario.errors}, indent=2))
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=3)
    (tmp_path / "native-plugin-control-evidence.json").write_text(json.dumps(evidence, indent=2))


def _control(host, action, commit=None, *, identity=None, role=ROLE, owner_kind="expert", expected=None):
    return host.handle(ControlRequest(schema_version=1, request_id=identity or action.lower(), expected_revision=expected,
        command=action + "_PLUGIN", payload={"run_id": RUN, "role": role, "owner_kind": owner_kind, "commit": commit}))


def _task(host, cycle, *, component=None, node=ResearchNodeKind.LITERATURE_REVIEW):
    component = component or str(host._research_contract(RUN).components[0].component_id)
    return ResearchNodeTask(key=ResearchNodeKey(run_id=RUN, component_id=component, expert_slot=ROLE,
        cycle=cycle, node_kind=node, subkind=ResearchNodeSubkind.SYNTHESIS if node is ResearchNodeKind.HYPOTHESIS_PROPOSAL else ResearchNodeSubkind.MAIN))


def _binding(host, task, *, kind="expert"):
    return host._plugin_binding(RUN, task, "grounding" if kind == "grounding" else ROLE, "scope test",
        max_bytes=LIMIT, owner_kind=kind)


def _record(host, task, plugin, *, update=None):
    """Actual SQLite command/Attempt fixture; not a claimed model invocation."""
    if host.store.latest_transition(RUN)["state"] == "CREATED":
        with host.store.transaction() as tx:
            tx.append_transition(host._transition_record(tx, RUN, RUN, "RUN", Command.ACTIVATE, RunState.ACTIVE))
    command = AgentCommand.GRAPH_EVOLVE if plugin.owner_kind == "grounding" else (
        AgentCommand.SELF_EVOLVE if task.key.node_kind is ResearchNodeKind.SELF_EVOLVE else AgentCommand.LITERATURE_REVIEW)
    suffix = "--grounding-resources" if plugin.owner_kind == "grounding" else "--expert-resources"
    resource = EmbeddedInput.from_bytes(task.command_id + suffix, "application/json", json.dumps({"plugin": plugin.to_dict()}).encode())
    base = default_request(command)
    request = replace(base, schema_version=14, run_id=RUN, owner_workflow_id=RUN, role=plugin.role,
        component_id="grounding" if plugin.owner_kind == "grounding" else task.key.component_id,
        task_context=task, command_id=task.command_id, attempt_id="attempt-" + str(len(host.store.list_attempts()) + 1),
        input_artifacts=(resource,), immutable_context_refs=(resource.ref,),
        required_output_schema=required_output_schema(command, task, schema_version=14))
    with host.store.transaction() as tx:
        row = host._transition_record(tx, RUN, RUN, "RUN", Command.REQUEST_AUXILIARY if plugin.owner_kind == "grounding" else Command.REQUEST_RESEARCH_NODE,
            RunState.ACTIVE, payload={"task_context": task.to_dict(), "role": plugin.role, "component_id": request.component_id})
        tx.append_transition(row)
        tx.open_attempt({"attempt_id": request.attempt_id, "run_id": RUN, "workflow_id": RUN, "command_id": row["command_id"],
            "kind": "AGENT", "attempt_ordinal": 1, "host_generation": host.generation, "started_at": time.time(),
            "observation": {"request": request.to_dict()}})
        tx.close_attempt(request.attempt_id, terminal_reason=AttemptTerminalReason.SUCCEEDED, ended_at=time.time(),
            payload={} if update is None else {"plugin_update": update})
    return request


def _candidate(host, base, name):
    store = ExpertPluginStore(host.state_root, owner_kind=base.owner_kind)
    prepared = store.prepare_update(RUN, base.role, commit=base.commit, attempt_id=name, command_id=name)
    path = prepared.root / "skills/self-evolve/SKILL.md"
    path.write_bytes(path.read_bytes() + ("\n" + name + "\n").encode())
    return store.finish_update(prepared, max_bytes=LIMIT, advance=False)


def test_created_pin_is_transactional_idempotent_and_activates_normally(host):
    seed = ExpertPluginStore(host.state_root).ensure(RUN, ROLE, "scope test")
    response = _control(host, "PIN", seed.commit, expected=1)
    assert response.status == "accepted", response
    assert _control(host, "PIN", seed.commit, expected=1) == response
    assert response.result["plugin_selection"] == {"action": "pin", "owner_kind": "expert", "role": ROLE,
        "requested_commit": seed.commit, "commit": seed.commit,
        "effective_cycles": {str(item.component_id): 1 for item in host._research_contract(RUN).components}}
    command, = host.store.inspect("SELECT * FROM workflow_commands WHERE command_type='SELECT_PLUGIN'")
    assert json.loads(command["request_json"])["command"] == "PIN_PLUGIN"
    assert _control(host, "PIN", "f" * 40, identity="pin", expected=1).status == "conflict"
    host.tick()
    assert host.store.latest_transition(RUN)["state"] == "ACTIVE"


def test_pin_preserves_frozen_component_cycles_candidates_and_unpin_after_restart(host):
    seed = _binding(host, _task(host, 1))
    early = _candidate(host, seed, "early-candidate")
    _record(host, _task(host, 1, node=ResearchNodeKind.SELF_EVOLVE), seed,
        update={"base_commit": seed.commit, "commit": early.commit})
    _record(host, _task(host, 3), early)
    _record(host, _task(host, 1, component="must.secondary"), seed)
    pin = _control(host, "PIN", seed.commit)
    assert pin.status == "accepted", pin
    assert pin.result["plugin_selection"]["effective_cycles"] == {str(host._research_contract(RUN).components[0].component_id): 4, "must.secondary": 2}
    assert _binding(host, _task(host, 3)) == early
    assert _binding(host, _task(host, 1, component="must.secondary")) == seed
    assert _binding(host, _task(host, 4)) == seed
    proposal = _candidate(host, seed, "candidate-written-while-pinned")
    _record(host, _task(host, 4, node=ResearchNodeKind.SELF_EVOLVE), seed,
        update={"base_commit": seed.commit, "commit": proposal.commit})
    assert _binding(host, _task(host, 5)) == seed
    assert ExpertPluginStore(host.state_root).ensure(RUN, ROLE, "ignored").commit == seed.commit
    unpin = _control(host, "UNPIN")
    assert unpin.status == "accepted" and unpin.result["plugin_selection"]["commit"] == proposal.commit
    assert _binding(host, _task(host, 4)) == seed
    host.close()
    replacement = WorkflowHost(host.state_root)
    replacement.initialize()
    try:
        assert _binding(replacement, _task(replacement, 5)) == proposal
        assert _control(replacement, "UNPIN") == unpin
    finally:
        replacement.close()


def test_grounding_pin_applies_only_to_next_unfrozen_command(host):
    first = AuxiliaryTask(kind=AuxiliaryRequestKind.GRAPH_EVOLVE, run_id=RUN, cycle=1, ordinal=10)
    seed = _binding(host, first, kind="grounding")
    candidate = _candidate(host, seed, "grounding-candidate")
    _record(host, first, seed, update={"base_commit": seed.commit, "commit": candidate.commit})
    second = replace(first, ordinal=20)
    assert _binding(host, second, kind="grounding") == candidate
    pin = _control(host, "PIN", seed.commit, role="grounding", owner_kind="grounding")
    assert pin.status == "accepted" and pin.result["plugin_selection"]["effective_cycles"] == {}
    assert _binding(host, first, kind="grounding") == seed
    assert _binding(host, second, kind="grounding") == seed
    for kind in (AuxiliaryRequestKind.OBSERVER, AuxiliaryRequestKind.DEBATE_JUDGE, AuxiliaryRequestKind.FINAL_REPORT):
        task = AuxiliaryTask(kind=kind, run_id=RUN) if kind is AuxiliaryRequestKind.FINAL_REPORT else replace(second, kind=kind)
        assert _binding(host, task, kind="grounding") == seed


def test_revert_is_an_idempotent_git_child_not_a_reset_and_remains_pinned(host):
    seed = _binding(host, _task(host, 1))
    proposal = _candidate(host, seed, "proposed-update")
    response = _control(host, "REVERT", proposal.commit)
    assert response.status == "accepted", response
    selected = response.result["plugin_selection"]["commit"]
    store = ExpertPluginStore(host.state_root)
    repository = store._repository(RUN, ROLE)
    assert selected not in {seed.commit, proposal.commit}
    assert store._git(repository, "rev-list", "--parents", "-n", "1", selected).decode().split() == [selected, seed.commit]
    assert store._git(repository, "rev-parse", selected + "^{tree}") == store._git(repository, "rev-parse", proposal.commit + "^{tree}")
    before = store._git(repository, "rev-list", "--all")
    assert _control(host, "REVERT", proposal.commit) == response
    assert store._git(repository, "rev-list", "--all") == before
    assert _binding(host, _task(host, 1)).commit == selected


@pytest.mark.parametrize("changes", [{"role": "unknown"}, {"owner_kind": "invalid"},
    {"role": ROLE, "owner_kind": "grounding"}, {"commit": "HEAD"}, {"commit": None}])
def test_invalid_owner_or_commit_controls_have_no_git_or_sql_effect(host, changes):
    def plugin_files():
        # An ancestor temp-directory name must not include SQLite in this check.
        return {str(path): path.read_bytes() for path in host.state_root.rglob("*")
                if path.is_file() and "/plugin" in "/" + path.relative_to(host.state_root).as_posix()}

    original = plugin_files()
    response = _control(host, "PIN", **{"commit": "a" * 40, **changes})
    assert response.status == "rejected", response
    assert not host.store.inspect("SELECT 1 FROM workflow_commands WHERE command_type='SELECT_PLUGIN'")
    assert plugin_files() == original


def test_foreign_namespace_commit_and_over_budget_tree_are_rejected(host, monkeypatch):
    expert = _binding(host, _task(host, 1))
    grounding = ExpertPluginStore(host.state_root, owner_kind="grounding").ensure(RUN, "grounding", "grounding")
    response = _control(host, "PIN", grounding.commit)
    assert response.status == "rejected", response
    assert _control(host, "PIN", expert.commit, identity="wrong-namespace", role="grounding", owner_kind="grounding").status == "rejected"
    monkeypatch.setattr("hypoloom.profile_v4.resolve_node_runtime", lambda *_args: {"memory": {"max_plugin_bytes": 1}})
    response = _control(host, "PIN", expert.commit, identity="over-budget")
    assert response.status == "rejected" and "budget" in response.error["message"], response
    assert not host.store.inspect("SELECT 1 FROM workflow_commands WHERE command_type='SELECT_PLUGIN'")


def test_terminal_control_does_not_even_initialize_a_plugin_repository(host):
    response = host.handle(ControlRequest(schema_version=1, request_id="cancel-plugin-test", expected_revision=None,
        command="CANCEL", payload={"run_id": RUN, "cause": "fresh fixture done"}))
    assert response.status == "accepted", response
    # This fixture only needs the existing terminal transition, not a science run.
    with host.store.transaction() as tx:
        tx.append_transition(host._transition_record(tx, RUN, RUN, "RUN", Command.FINISH_STOP, RunState.CANCELLED))
    before = tuple(host.state_root.rglob("plugin"))
    response = _control(host, "REVERT", "a" * 40)
    assert response.status == "rejected" and "CREATED or ACTIVE" in response.error["message"]
    assert tuple(host.state_root.rglob("plugin")) == before == ()


def test_explicit_unpin_selects_the_requested_commit_and_null_without_candidates_uses_seed(host):
    seed = _binding(host, _task(host, 1))
    candidate = _candidate(host, seed, "explicit-unpin-candidate")
    selected = _control(host, "UNPIN", candidate.commit)
    assert selected.status == "accepted", selected
    assert _binding(host, _task(host, 1)) == candidate
    reset = _control(host, "UNPIN", identity="unpin-latest-without-success")
    assert reset.status == "accepted" and reset.result["plugin_selection"]["commit"] == seed.commit


def test_legacy13_rejects_controls_and_keeps_role_cycle_binding(host, monkeypatch):
    seed = _binding(host, _task(host, 1))
    candidate = _candidate(host, seed, "another-component-version")
    _record(host, _task(host, 1), candidate)
    original = host.store.get_run
    monkeypatch.setattr(host.store, "get_run", lambda run_id: {**original(run_id), "agent_aci_version": "13"})
    assert _control(host, "PIN", seed.commit).status == "rejected"
    assert _binding(host, _task(host, 1, component="must.secondary")) == candidate


def test_real_cpu_self_evolve_while_pinned_then_unpin_next_cycle_and_restart(tmp_path, monkeypatch, mount_root):
    from hypoloom.fake_agent import FakeAgent
    from hypoloom.oracle import Oracle
    from hypoloom.profile_v4 import project_profile_v4
    from test_expert_loop_host import _ReviseOnceAgent
    from test_profile_v4 import documents
    monkeypatch.setattr("hypoloom.workflow_host.EXPERT_LOOP_ACI_SCHEMA_VERSION", 14)
    payload = _pilot_payload(tmp_path, mount_root, RUN)
    settings = project_profile_v4(**documents()).settings
    settings["experts"]["experts"][0]["name"] = ROLE
    settings["experts"]["defaults"]["max_hypotheses_per_round"] = 1
    settings["experts"]["defaults"]["max_forks"] = 1
    settings["goal"]["completion_mode"] = "explicit_signals"
    settings["goal"]["stop_conditions"]["max_rounds_per_expert"] = 2
    settings["methodology"]["inner_loop"] = {"max_iterations_per_hypothesis": 1, "repair_attempts": 1}
    settings["methodology"]["interaction"] = {"leader": {"enabled": False, "check_after": "cycle_report", "min_rounds_before_leader": 1}, "debate": {"enabled": False, "rounds": 3}}
    settings["methodology"]["graph_evolution"]["enabled"] = False
    payload["research_contract"]["methodology"]["controls"]["product"] = settings
    payload["research_contract"]["budget"]["max_experiments"] = 4
    payload["research_contract"]["roles"][0]["budget"]["max_experiments"] = 4

    class EvolvingAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.delegate = _ReviseOnceAgent()
            self.reads = []

        def invoke(self, request):
            resources = next((item for item in request.input_artifacts if item.ref.endswith("--expert-resources")), None)
            if resources is not None:
                plugin = json.loads(resources.content_bytes())["plugin"]
                store = ExpertPluginStore(tmp_path / "state")
                view = store.read(RUN, ROLE, commit=plugin["commit"], attempt_id=request.attempt_id, max_bytes=LIMIT)
                try:
                    text = (view.root / "skills/self-evolve/SKILL.md").read_text()
                    self.reads.append({"command": request.command.value, "cycle": request.task_context.key.cycle,
                        "commit": plugin["commit"], "candidate_seen": "PINNED_CANDIDATE_CYCLE_1" in text})
                finally:
                    view.close()
                if request.command is AgentCommand.SELF_EVOLVE:
                    prepared = store.prepare_update(RUN, ROLE, commit=plugin["commit"], attempt_id=request.attempt_id, command_id=request.command_id)
                    path = prepared.root / "skills/self-evolve/SKILL.md"
                    path.write_text(path.read_text() + f"\nPINNED_CANDIDATE_CYCLE_{request.task_context.key.cycle}\n")
            return self.delegate.invoke(request)

    # Ordinary dispatch errors are quarantined; this boundary models Host exit.
    class BetweenCycles(SystemExit):
        pass

    agent = EvolvingAgent()
    instance = WorkflowHost(tmp_path / "state", local_agent=agent)
    instance.initialize()
    def completed_evolution():
        return [row for row in instance.store.list_attempts() if row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
            and json.loads(row["observation_json"] or "{}").get("request", {}).get("command") == "SELF_EVOLVE"]
    def boundary(point):
        if point == "after_agent_terminal_commit" and completed_evolution():
            raise BetweenCycles()
    try:
        accepted = instance.handle(ControlRequest(schema_version=1, request_id="create-real-plugin", expected_revision=0, command="CREATE_RUN", payload=payload))
        assert accepted.status == "accepted", accepted
        seed = ExpertPluginStore(instance.state_root).ensure(RUN, ROLE, "real CPU plugin test")
        pin = _control(instance, "PIN", seed.commit)
        assert pin.status == "accepted", pin
        monkeypatch.setattr(instance, "_crash", boundary)
        deadline = time.monotonic() + 40
        with pytest.raises(BetweenCycles):
            while time.monotonic() < deadline:
                instance.tick()
                time.sleep(.005)
        monkeypatch.setattr(instance, "_crash", lambda _point: None)
        evolved, = completed_evolution()
        update = json.loads(evolved["observation_json"])["plugin_update"]
        assert update["base_commit"] == seed.commit and update["commit"] != seed.commit
        assert _binding(instance, _task(instance, 2)) == seed
        assert ExpertPluginStore(instance.state_root).ensure(RUN, ROLE, "ignored") == seed
        unpin = _control(instance, "UNPIN")
        assert unpin.status == "accepted" and unpin.result["plugin_selection"]["commit"] == update["commit"]
        instance.close()
        instance = WorkflowHost(tmp_path / "state", local_agent=agent)
        instance.initialize()
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and instance.store.latest_transition(RUN)["state"] != "COMPLETED":
            instance.tick()
            time.sleep(.005)
        assert instance.store.latest_transition(RUN)["state"] == "COMPLETED"
        assert len(instance.store.list_facts(RUN, "CanonicalOutcomeAccepted")) == 2
        processes = [row for row in instance.store.list_attempts() if row["kind"] == "PROCESS"]
        assert len(processes) == 2 and all(row["terminal_reason"] == "SUCCEEDED" for row in processes)
        assert all(not item["candidate_seen"] for item in agent.reads if item["cycle"] == 1)
        assert all(item["candidate_seen"] for item in agent.reads if item["cycle"] == 2)
        assert len(completed_evolution()) == 2
        report = Oracle(instance.store.database_path, instance.publisher.root).audit(RUN)
        (tmp_path / "plugin-consumer-reads.json").write_text(json.dumps(agent.reads, indent=2))
        (tmp_path / "oracle.json").write_text(report.to_json())
        assert report.ok, report.to_dict()
    finally:
        instance.close()
