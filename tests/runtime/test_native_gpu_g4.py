"""Opt-in current native CLI/MCP G4; real authorized CUDA, local model only.

Run from one frozen ordinary source copy. Both REAL_GPU and REAL_NATIVE_GPU
must be 1. REAL_GPU_UUID selects the single card; the plural case additionally
requires REAL_GPU_SECOND_UUID. All names have the HYPOLOOM_ prefix.
No inventory/Runner/Host/scientific acceptance is mocked in these two cases.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import (
    CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, WorkspaceArtifact, with_output_digest,
)
from hypoloom.claude_agent_host import ClaudeAgentHost, process_group_exists
from hypoloom.compute_resources import LocalComputeResources
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.expert_loop import ResearchNodeSubkind, ResearchNodeTask
from hypoloom.fake_agent import FakeAgent, default_request
from hypoloom.literature import FixedLiteratureIO, OpenAlexLiteratureFetcher
from hypoloom.oracle import Oracle
from hypoloom.workflow_host import WorkflowHost

from test_claude_file_tool_cli import _installed_claude
from test_claude_final_report_g3 import _FinalReportHostScenario
from test_claude_measurement_g3 import _NoPublicWeb
from test_claude_multichain_g3 import _INNER, _payload
from test_full_cc_host_g3 import (
    _CatalogTransport, _MODEL, _local_experiment_command, _request_from_provider, _start_provider,
)
from test_real_expert_gpu_qualification import _assert_gpu_idle, _configured_gpu


_NATIVE_GPU = pytest.mark.skipif(
    os.environ.get("HYPOLOOM_REAL_GPU") != "1"
    or os.environ.get("HYPOLOOM_REAL_NATIVE_GPU") != "1",
    reason="requires explicit REAL_GPU and REAL_NATIVE_GPU opt-in and exact UUIDs",
)


def _cuda_source(original: bytes, *, uuids: tuple[str, ...], hold_seconds: int) -> bytes:
    # Reuse the candidate's actual result declaration. Only the computation
    # prelude changes; metric/result parsing still belongs to the production ACI.
    source = original.decode()
    assert "\nresult = " in source
    result_writer = "result = " + source.split("\nresult = ", 1)[1]
    return (
        "import json, os, sys, time\nfrom pathlib import Path\n"
        "if '--local-check' not in sys.argv:\n"
        "    import torch\n"
        "    assert torch.cuda.is_available()\n"
        f"    assert torch.cuda.device_count() == {len(uuids)}\n"
        "    visible = os.environ['CUDA_VISIBLE_DEVICES'].split(',')\n"
        f"    assert set(visible) == set({uuids!r}) and len(visible) == {len(uuids)}\n"
        "    assert os.environ['CUDA_DEVICE_ORDER'] == 'PCI_BUS_ID'\n"
        "    sums = []\n"
        "    for index in range(torch.cuda.device_count()):\n"
        "        values = torch.tensor([1, 2, 3], device=f'cuda:{index}')\n"
        "        sums.append(int(values.sum().item()))\n"
        "        torch.cuda.synchronize(index)\n"
        f"    assert sums == {[6] * len(uuids)!r}\n"
        "    print(json.dumps({'native_g4_cuda': True, 'visible_uuids': visible,\n"
        "        'cuda_device_count': torch.cuda.device_count(), 'sums': sums,\n"
        "        'device_names': [torch.cuda.get_device_name(i) for i in range(len(visible))],\n"
        "        'cuda_runtime': torch.version.cuda}), flush=True)\n"
        # Finite, low-power context ownership allows a real READY queue to form.
        # There is no busy loop, training workload, large tensor or external PID.
        f"    time.sleep({hold_seconds})\n"
        + result_writer
    ).encode()


class _GpuBatchAgent(FakeAgent):
    """Produces local scripted provider responses, never replaces real Agents."""

    def __init__(self, chains: tuple[str, ...], uuids: tuple[str, ...], *, aci_version: int = CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION):
        super().__init__()
        self.chains = chains
        self.uuids = uuids
        self.aci_version = aci_version

    def invoke(self, request):
        response = super().invoke(request)
        assert request.schema_version == self.aci_version
        task = request.task_context
        if (request.command is AgentCommand.HYPOTHESIS_PROPOSAL
                and isinstance(task, ResearchNodeTask)
                and task.key.subkind is ResearchNodeSubkind.SYNTHESIS):
            candidate = response.typed_candidate["hypotheses"][0]["candidate"]
            return with_output_digest(replace(response, typed_candidate={
                "schema_version": 1, "hypotheses": [
                    {"chain_id": chain, "candidate": {
                        **candidate, "claim": "Bounded CUDA mechanism " + chain}}
                    for chain in self.chains]}))
        if request.command is AgentCommand.RESULT_ANALYSIS:
            again = task.key.chain == self.chains[0] and task.key.experiment_ordinal == 1
            return with_output_digest(replace(response, typed_candidate={
                **response.typed_candidate,
                "iteration_disposition": "ITERATE" if again else "COMPLETE"}))
        if request.command is AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            hold = 45 if len(self.chains) == 3 and task.key.experiment_ordinal == 1 else 1
            artifacts = tuple(
                WorkspaceArtifact.from_bytes(item.path, _cuda_source(
                    item.content_bytes(), uuids=self.uuids, hold_seconds=hold))
                if item.path == "experiment.py" else item
                for item in response.proposed_workspace_artifacts)
            return with_output_digest(replace(response,
                typed_candidate={**response.typed_candidate, "resource_preference": "GPU_REQUIRED"},
                proposed_workspace_artifacts=artifacts))
        return response


class _GpuScenario(_FinalReportHostScenario):
    def __init__(self, root: Path, chains: tuple[str, ...], uuids: tuple[str, ...], *, aci_version: int = CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION):
        super().__init__(root)
        self.reviser = _GpuBatchAgent(chains, uuids, aci_version=aci_version)
        self.chain_nonces = {chain: "native-g4-private-" + secrets.token_hex(12) for chain in chains}
        self.gpu_count = len(uuids)

    def record(self, document):
        number = super().record(document)
        request = _request_from_provider(document)
        if request.command is AgentCommand.FINAL_REPORT:
            report = self.reports[request.attempt_id]
            report.report = report.report.replace(
                "The compatibility provider was scripted; no paid model or GPU was used.",
                "The provider was scripted locally; no paid model was used. "
                "The accepted experiments used real authorized CUDA devices.")
        return number

    def _unscoped_tool_calls(self, number):
        calls = super()._unscoped_tool_calls(number)
        revised = []
        for identity, name, arguments in calls:
            if name == "mcp__host-tools__submit_experiment":
                arguments = {**arguments, "gpus": self.gpu_count}
            if name == "Bash" and identity in {"test_failure", "test_success"}:
                # Ordinary local Write/Bash failure/Edit/Bash pass still run in
                # the real Agent sandbox, which has no GPU device access.
                arguments = {**arguments, "command": _local_experiment_command("--local-check")}
            revised.append((identity, name, arguments))
        return tuple(revised)


def _gpu_payload(root, run_id, uuids, runtime, chains):
    payload = _payload(root, run_id)
    contract = payload["research_contract"]
    product = contract["methodology"]["controls"]["product"]
    product["experts"]["defaults"].update(max_hypotheses_per_round=len(chains), max_forks=6)
    product["safety"].update(gpus=list(uuids), max_gpus_per_experiment=len(uuids),
                             experiment_timeout_minutes=2)
    count = len(chains) + 1  # Exactly one accepted scientific iteration.
    contract["budget"].update(max_experiments=count, max_active_agents=6,
        max_active_processes=3, max_active_reviews=3, experiment_timeout=120,
        min_vram_per_experiment=512, gpu_vram_reserve_mb=512)
    contract["roles"][0]["budget"]["max_experiments"] = count
    contract["runtime"]["conda_env"] = str(runtime)
    payload["execution"].update(allow_gpu=True, runtime_prefix=str(runtime))
    return payload


def _gpu_host(root, executable, base_url, key, resources):
    # Same production adapter/literature helpers as the existing native Host G3;
    # pass actual resources at construction so the RunVersion binds them.
    return WorkflowHost(root / "state",
        claude_agent_host=ClaudeAgentHost(claude_executable=executable,
                                         settings_path=root / "settings.json"),
        claude_model=_MODEL,
        provider_environment={"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": base_url},
        compute_resources=resources,
        literature_io=FixedLiteratureIO(OpenAlexLiteratureFetcher(
            api_key="local-literature-fixture-key", transport=_CatalogTransport(),
            resolver=lambda _hostname: ("8.8.8.8",))),
        grounding_web=_NoPublicWeb())


def _snapshot_queue(host, run_id):
    return {
        "at": time.time(),
        "ready": [row["workflow_id"] for row in host.store.list_workflow_heads(run_id, "EXPERIMENT")
                  if row["state"] == "READY"],
        "open": [row["workflow_id"] for row in host.store.list_attempts()
                 if row["kind"] == "PROCESS" and row["state"] == "OPEN"],
        "waiting": sorted(host.resource_waits),
    }


def _archived_cuda_stdout(artifact_root, attempt_id):
    # ExecutionWorkspace cleanup removes ProcessRequest.cwd. Its ordinary saved
    # result-staging logs survive and are the source already used by retained G4.
    path = artifact_root / "result-staging" / attempt_id / "logs" / "stdout.log"
    text = path.read_text()
    records = [json.loads(line) for line in text.splitlines() if line.startswith('{"native_g4_cuda":')]
    assert len(records) == 1, text
    return path, records[0]


def _assert_closure(host, run_id, scenario, uuids, chains, queue, root, host_key):
    expected = {(chain, 1) for chain in chains} | {(chains[0], 2)}
    assert scenario.local_tests == set(scenario.execution_results) == expected
    attempts = host.store.list_attempts()
    assert all(row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED" for row in attempts)
    processes = [row for row in attempts if row["kind"] == "PROCESS"]
    assert len(processes) == len(expected)
    observations = [json.loads(row["observation_json"]) for row in attempts if row["kind"] == "AGENT"]
    assert all(row["request"]["schema_version"] == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION and "session" in row for row in observations)
    assert all(row["request"]["required_adapter_version"].startswith("claude-code-bwrap/")
               for row in observations)
    for command in ("ADJUDICATE", "CYCLE_REPORT", "SELF_EVOLVE", "FINAL_REPORT"):
        assert sum(row["request"]["command"] == command for row in observations) == 1
    evolved = next(row for row in observations if row["request"]["command"] == "SELF_EVOLVE")
    assert evolved["plugin_update"]["base_commit"] != evolved["plugin_update"]["commit"]
    assert {row["session"]["expert_resources"]["plugin"]["commit"] for row in observations
            if "expert_resources" in row["session"]} == {evolved["plugin_update"]["base_commit"]}
    sessions = {}
    fresh_sessions = set()
    for chain in chains:
        nodes = [row for row in observations if row["request"]["command"] in {item.value for item in _INNER}
                 and row["request"]["task_context"]["key"]["chain"] == chain]
        assert len(nodes) == (6 if chain == chains[0] else 3)
        sessions[chain] = nodes[0]["session"]["session_id"]
        if nodes[0]["request"]["schema_version"] >= 24:
            ids = {row["session"]["session_id"] for row in nodes}
            assert len(ids) == len(nodes) and not ids & fresh_sessions
            fresh_sessions.update(ids)
            assert all(row["session"]["parent_session_id"] is None and
                       row["session"]["launch_arguments"] == ["--session-id", row["session"]["session_id"]]
                       for row in nodes)
        else:
            assert {row["session"]["session_id"] for row in nodes} == {sessions[chain]}
            assert "--fork-session" in nodes[0]["session"]["launch_arguments"]
            assert all(row["session"]["launch_arguments"] == ["--resume", sessions[chain]] for row in nodes[1:])
    assert len(set(sessions.values())) == len(chains)
    assert len(host.store.list_facts(run_id, "HypothesisProposed")) == len(chains)
    outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
    assert len(outcomes) == len(expected)
    assert {row["payload"]["outcome"] for row in outcomes} == {"SUCCESS"}
    creation_rows = host.store.inspect(
        "SELECT workflow_id,payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND revision=1 ORDER BY rowid", (run_id,))
    creations = [json.loads(row["payload_json"]) for row in creation_rows]
    assert {(row["chain"], row["chain_iteration"]) for row in creations} == expected
    second = next(row for row in creations if row["chain_iteration"] == 2)
    assert second["analysis_attempt_id"] and second["iteration_of_experiment_id"]
    assert second["revision_of_outcome_id"] is None
    by_experiment = {row["experiment_id"]: row for row in processes}
    specs = host.store.list_facts(run_id, "ExperimentPreregistered")
    cuda_records = []
    for spec in specs:
        contract = spec["payload"]["execution_contract"]
        grant = contract["resource_grant"]
        selected = grant.get("gpu_uuids", [grant.get("gpu_uuid")])
        assert grant["kind"] == "GPU" and set(selected) == set(uuids)
        assert len(selected) == len(uuids)
        submit_id = contract["source_submit_command_id"]
        commands = host.store.inspect("SELECT * FROM workflow_commands WHERE command_id=?", (submit_id,))
        assert len(commands) == 1 and commands[0]["response_status"] == "accepted"
        submitted = json.loads(commands[0]["request_json"])
        assert submitted["tool"] == "submit_experiment"
        assert submitted["arguments"]["gpus"] == len(uuids)
        assert submitted["source_request"]["attempt_id"] == contract["source_agent_attempt_id"]
        process = by_experiment[spec["experiment_id"]]
        observation = json.loads(process["observation_json"])
        request = observation["process_request"]
        argv = request["argv"]
        assert Path(argv[0]).name == "bwrap"
        triples = [tuple(argv[i:i + 3]) for i, item in enumerate(argv) if item in {"--setenv", "--dev-bind"}]
        assert ("--setenv", "CUDA_VISIBLE_DEVICES", ",".join(selected)) in triples
        assert ("--setenv", "CUDA_DEVICE_ORDER", "PCI_BUS_ID") in triples
        assert len([item for item in triples if item[0] == "--dev-bind"]) == len(uuids) + 2
        usage = observation["resource_usage"]
        usage = [usage] if isinstance(usage, dict) else usage
        assert {row["gpu_uuid"] for row in usage} == set(uuids)
        stdout_path, record = _archived_cuda_stdout(host.publisher.root, process["attempt_id"])
        assert set(record["visible_uuids"]) == set(uuids)
        assert record["cuda_device_count"] == len(uuids) and record["sums"] == [6] * len(uuids)
        assert len(record["device_names"]) == len(uuids) and record["cuda_runtime"]
        cuda_records.append({"attempt_id": process["attempt_id"], "stdout_path": str(stdout_path), **record})
    # Existing ordinary row order and Attempt timestamps establish queue order
    # and actual resource exclusion; no new scheduling marker or fake clock.
    process_order = sorted(processes, key=lambda row: row["started_at"])
    for first, second_process in zip(process_order, process_order[1:]):
        assert first["ended_at"] <= second_process["started_at"]
    assert [row["workflow_id"] for row in process_order] == [row["workflow_id"] for row in creation_rows]
    if len(chains) == 3:
        # Policy can withhold EXECUTE before _start_process populates its
        # diagnostic resource_waits. The real READY heads plus the exact common
        # grant above establish contention without requiring that cache entry.
        assert any(len(row["open"]) == 1 and len(row["ready"]) == 2 for row in queue), queue
        assert all(len(row["open"]) <= 1 for row in queue)
    for row in observations:
        archive = host.publisher.root / row["session"]["transcript"]["path"]
        transcript = (archive / "transcript.jsonl").read_bytes()
        assert row["session"]["transcript_complete"] and host_key.encode() not in transcript
        if row["request"]["command"] == "EXPERIMENT_EXECUTION":
            assert b"mcp__host-tools__submit_experiment" in transcript
            assert b"mcp__host-tools__wait_experiment" in transcript
        if row["request"]["command"] == "CYCLE_REPORT":
            assert scenario.nonce in (archive / "memory/MEMORY.md").read_text()
            assert scenario.nonce in (archive / "memory/rounds/1.md").read_text()
        if row["request"]["command"] == "FINAL_REPORT":
            report = scenario.reports[row["request"]["attempt_id"]]
            assert len(report.requests) == 5 and len(report.evidence) == 4
            files = row["response"]["proposed_workspace_artifacts"]
            assert len(files) == 1 and files[0]["path"] == "FINAL_REPORT.md"
            assert WorkspaceArtifact.from_dict(files[0]).content_bytes() == report.report.encode()
            assert (archive / "FINAL_REPORT.md").read_bytes() == report.report.encode()
    audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
    (root / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
    assert audit.ok, audit.to_dict()
    assert audit.canonical_outcome_count == len(expected)
    assert audit.artifact_counts["TRANSCRIPT"] == len(observations)
    assert audit.open_attempt_count == audit.pending_effect_count == 0
    assert not host.running and not host.running_agents and not audit.owned_process_residue
    assert all(not process_group_exists(row["process_group_id"]) for row in attempts if row["process_group_id"] is not None)
    (root / "closure.json").write_text(json.dumps({
        "run_id": run_id, "schema_version": CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, "allowed_gpu_uuids": uuids,
        "chain_sessions": sessions, "agent_count": len(observations),
        "process_count": len(processes), "cuda": cuda_records,
        "native_provider": "local-scripted-only", "oracle_ok": audit.ok}, indent=2))


def _run_native_gpu(root: Path, *, plural: bool):
    first, runtime = _configured_gpu()
    uuids = (first,)
    if plural:
        second = os.environ.get("HYPOLOOM_REAL_GPU_SECOND_UUID")
        assert second and second == second.strip() and second.startswith("GPU-") and second != first
        uuids += (second,)
    for uuid in uuids:
        _assert_gpu_idle(uuid)
    executable = _installed_claude()
    chains = ("chain-a",) if plural else ("chain-a", "chain-b", "chain-c")
    scenario = _GpuScenario(root, chains, uuids)
    key = "host-only-native-g4-" + secrets.token_hex(16)
    settings = root / "settings.json"
    settings.write_text("{}\n")
    settings.chmod(0o600)
    resources = LocalComputeResources(allowed_gpu_uuids=uuids, gpu_runtime_prefix=runtime)
    assert {gpu.uuid for gpu in resources.snapshot().gpus} == set(uuids)
    provider, thread = _start_provider(scenario, key)
    host = None
    queue = []
    run_id = "native-g4-plural" if plural else "native-g4-queue"
    try:
        host = _gpu_host(root, executable, f"http://127.0.0.1:{provider.server_port}", key, resources)
        payload = _gpu_payload(root, run_id, uuids, runtime, chains)
        host.initialize()
        created = host.handle(ControlRequest(schema_version=1, request_id="create-" + run_id,
            expected_revision=0, command="CREATE_RUN", payload=payload))
        assert created.status == "accepted", created
        assert host._effect_capacity(run_id, "PROCESS") == 3
        deadline = time.monotonic() + 540
        while time.monotonic() < deadline:
            host.tick()
            sample = _snapshot_queue(host, run_id)
            # Save only changes, retaining actual observation time and identities.
            if not queue or any(sample[key] != queue[-1][key] for key in ("ready", "open", "waiting")):
                queue.append(sample)
            assert not scenario.errors, scenario.errors
            if host.store.latest_transition(run_id)["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.02)
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED", host.store.list_attempts()
        _assert_closure(host, run_id, scenario, uuids, chains, queue, root, key)
    finally:
        (root / "queue-observations.json").write_text(json.dumps(queue, indent=2))
        (root / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
        (root / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
        if host is not None:
            host.close()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    for uuid in uuids:
        _assert_gpu_idle(uuid)


@_NATIVE_GPU
def test_native_three_chains_queue_on_one_gpu_and_scientifically_iterate(tmp_path):
    _run_native_gpu(tmp_path, plural=False)


@_NATIVE_GPU
def test_native_mcp_exact_two_gpu_cuda_grant_and_iteration(tmp_path):
    _run_native_gpu(tmp_path, plural=True)


@pytest.mark.parametrize("count", [1, 2])
def test_local_check_executes_result_writer_without_importing_cuda(tmp_path, monkeypatch, count):
    original = b'import json, os\nfrom pathlib import Path\nresult = {"score": 3}\nPath(os.environ["HYPOLOOM_RESULT_PATH"]).write_text(json.dumps(result))\n'
    source = _cuda_source(original, uuids=tuple(f"GPU-{index}" for index in range(count)), hold_seconds=45)
    result = tmp_path / "result.json"
    monkeypatch.setenv("HYPOLOOM_RESULT_PATH", str(result))
    monkeypatch.setattr(sys, "argv", ["experiment.py", "--local-check"])
    namespace = {}
    exec(compile(source, "experiment.py", "exec"), namespace)
    assert json.loads(result.read_text()) == {"score": 3}
    assert "torch" not in namespace
    assert f"torch.cuda.device_count() == {count}" in source.decode()


def test_default_native_gpu_scenario_accepts_current_synthesis(tmp_path):
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    task = replace(request.task_context,
                   key=replace(request.task_context.key, subkind=ResearchNodeSubkind.SYNTHESIS))
    request = replace(request, schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
                      task_context=task, command_id=task.command_id,
                      required_output_schema="HypothesisSet/v1")
    scenario = _GpuScenario(tmp_path, ("chain-a", "chain-b", "chain-c"), ("GPU-selected",))
    response = scenario.reviser.invoke(request)
    assert [item["chain_id"] for item in response.typed_candidate["hypotheses"]] == list(scenario.reviser.chains)


@pytest.mark.parametrize("aci_version", [14, 16, 17])
def test_scripted_candidate_requires_current_protocol_and_exact_chain_set(aci_version):
    request = default_request(AgentCommand.HYPOTHESIS_PROPOSAL)
    task = replace(request.task_context,
                   key=replace(request.task_context.key, subkind=ResearchNodeSubkind.SYNTHESIS))
    request = replace(request, schema_version=aci_version, task_context=task, command_id=task.command_id,
                      required_output_schema="HypothesisSet/v1")
    assert request.task_context.key.subkind is ResearchNodeSubkind.SYNTHESIS
    agent = _GpuBatchAgent(("chain-a", "chain-b", "chain-c"), ("GPU-selected",), aci_version=aci_version)
    response = agent.invoke(request)
    assert [item["chain_id"] for item in response.typed_candidate["hypotheses"]] == list(agent.chains)
    with pytest.raises(AssertionError):
        agent.invoke(replace(request, schema_version=13, required_output_schema="HypothesisCandidate/v2"))


@pytest.mark.parametrize("count", [1, 2])
def test_native_tools_keep_gpu_request_explicit_and_local_check_gpu_free(tmp_path, count):
    uuids = tuple("GPU-authorized-" + str(index) for index in range(count))
    scenario = _GpuScenario(tmp_path, ("chain-a",), uuids, aci_version=14)
    request = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    task = replace(request.task_context, key=replace(request.task_context.key, chain="chain-a"))
    request = replace(request, schema_version=14, task_context=task, command_id=task.command_id)
    response = scenario.reviser.invoke(request)
    assert response.typed_candidate["resource_preference"] == "GPU_REQUIRED"
    source = next(item.content_bytes() for item in response.proposed_workspace_artifacts if item.path == "experiment.py")
    assert f"torch.cuda.device_count() == {count}" in source.decode()
    scenario.current = request
    scenario.responses[request.attempt_id] = response
    scenario.steps[request.attempt_id] = 2
    calls = scenario._unscoped_tool_calls(1)
    assert len(calls) == 1 and calls[0][1] == "Bash"
    assert calls[0][2]["command"] == _local_experiment_command("--local-check")
    execution = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    execution_task = replace(execution.task_context, key=replace(execution.task_context.key, chain="chain-a"))
    execution = replace(execution, schema_version=14, task_context=execution_task, command_id=execution_task.command_id)
    scenario.current = execution
    scenario.steps[execution.attempt_id] = 1
    scenario.designs[("chain-a", 1)] = response.typed_candidate
    calls = scenario._unscoped_tool_calls(2)
    assert len(calls) == 1 and calls[0][1] == "mcp__host-tools__submit_experiment"
    assert calls[0][2]["gpus"] == count


def test_native_final_report_runs_file_tools_before_structured_completion(tmp_path):
    from test_final_report import report_request

    request = report_request()
    scenario = _GpuScenario(tmp_path, ("chain-a",), ("GPU-selected",), aci_version=14)
    prompt = json.dumps({"agent_request_metadata": request.to_dict(), "rendered_inputs": [
        {"ref": item.ref, "media_type": item.media_type, "content_utf8": item.content_bytes().decode()}
        for item in request.input_artifacts]})
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    results = (
        "The report is a narrative artifact derived from accepted records",
        request.input_artifacts[0].content_bytes().decode(),
        "Wrote /report/FINAL_REPORT.md",
        "FINAL_REPORT_FILE_AND_BOUNDARIES",
    )
    names = ("Skill", "Read", "Write", "Bash", "StructuredOutput")
    for index, name in enumerate(names):
        number = scenario.record({"messages": messages})
        assert not scenario.errors, scenario.errors
        calls = scenario.tool_calls(number)
        assert len(calls) == 1 and calls[0][1] == name
        identity, _, arguments = calls[0]
        if name == "Write":
            assert arguments["file_path"] == "/report/FINAL_REPORT.md"
            assert "outcome-accepted" in arguments["content"]
            assert "no paid model or GPU was used" not in arguments["content"]
        if index < len(results):
            messages = [*messages, {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": identity, "content": results[index]}]}]
    assert len(scenario.reports[request.attempt_id].evidence) == 4


def test_cuda_evidence_reads_saved_log_without_requiring_removed_process_workdir(tmp_path):
    attempt_id = "fresh-experiment--process-1"
    artifact_root = tmp_path / "artifacts"
    path = artifact_root / "result-staging" / attempt_id / "logs" / "stdout.log"
    path.parent.mkdir(parents=True)
    record = {"native_g4_cuda": True, "visible_uuids": ["GPU-selected"],
              "cuda_device_count": 1, "sums": [6]}
    path.write_text(json.dumps(record) + "\n")
    assert not (artifact_root / "work" / attempt_id).exists()
    assert _archived_cuda_stdout(artifact_root, attempt_id) == (path, record)
