"""Opt-in shared-load CUDA qualification through two native CC/Host roots.

Requires REAL_GPU, REAL_NATIVE_GPU and REAL_SHARED_GPU all equal to 1, two
explicit UUIDs and the existing GPU runtime setting (HYPOLOOM_ prefix).
The provider is scripted locally; inventory, NVML, pool, Host, CC and Runner are
real. Busy devices are allowed, but each must initially have at least 6GiB free.
Nothing in the CUDA branch runs during ordinary focused tests or collection.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sys
import time

import pytest

from hypoloom.agent_aci import (
    CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
    EmbeddedInput,
    WorkspaceArtifact,
    with_output_digest,
)
from hypoloom.claude_agent_host import ClaudeAgentHost, process_group_exists
from hypoloom.compute_resources import (
    LocalComputeResources,
    observe_authorized_gpu_memory,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand
from hypoloom.fake_agent import default_request
from hypoloom.gpu_attribution import GpuAttributionSampler
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.literature import FixedLiteratureIO, OpenAlexLiteratureFetcher
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.workflow_host import WorkflowHost

from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_claude_measurement_g3 import _NoPublicWeb
from test_expert_memory_g3 import _resources
from test_full_cc_host_g3 import (
    _CatalogTransport,
    _MODEL,
    _request_from_provider,
    _rows,
    _start_provider,
)
from test_native_gpu_g4 import _GpuBatchAgent, _GpuScenario, _gpu_payload
from test_real_expert_gpu_qualification import _configured_gpu


_SHARED_GPU = pytest.mark.skipif(
    any(
        os.environ.get("HYPOLOOM_" + flag) != "1"
        for flag in ("REAL_GPU", "REAL_NATIVE_GPU", "REAL_SHARED_GPU")
    ),
    reason="requires explicit shared/native/CUDA opt-in and two authorized UUIDs",
)
_RESOURCE_COMMANDS = {
    AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
    AgentCommand.EXPERIMENT_EXECUTION,
}
_HISTORY_COMMANDS = _RESOURCE_COMMANDS | {
    AgentCommand.CYCLE_REPORT,
    AgentCommand.SELF_EVOLVE,
}
_MIB = 1024**2
_RUN_ID = "native-shared-gpu"
_ESTIMATE = {
    "workload": {"program": "bounded-cuda-canary", "tensor_mib_per_process": "64"},
    "peak_memory_mib": 128,
    "basis": "Deliberate bounded learning probe; 64MiB tensor split across visible devices, plus unknown CUDA contexts. "
    "Harness requires 6GiB free per authorized GPU, never induces an OOM.",
    "measurement_attempt_ids": [],
}


def _shared_cuda_source(original, *, uuids, hold_seconds):
    assert len(uuids) in {1, 2}, "this bounded sample uses one or two GPUs per Process"
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
        f"    assert set(visible) == set({uuids!r})\n"
        "    assert os.environ['CUDA_DEVICE_ORDER'] == 'PCI_BUS_ID'\n"
        "    held, sums = [], []\n"
        f"    tensor_bytes = 64 * 1024**2 // {len(uuids)}\n"
        f"    allocator_limit = 256 * 1024**2 // {len(uuids)}\n"
        "    for index in range(torch.cuda.device_count()):\n"
        "        total = torch.cuda.get_device_properties(index).total_memory\n"
        "        torch.cuda.set_per_process_memory_fraction(allocator_limit / total, index)\n"
        "        held.append(torch.ones(tensor_bytes, dtype=torch.uint8, device=f'cuda:{index}'))\n"
        "        values = torch.tensor([1, 2, 3], device=f'cuda:{index}')\n"
        "        sums.append(int(values.sum().item()))\n"
        "        torch.cuda.synchronize(index)\n"
        "        assert torch.cuda.max_memory_reserved(index) <= allocator_limit\n"
        "    peaks = [torch.cuda.max_memory_reserved(i) for i in range(torch.cuda.device_count())]\n"
        "    assert sum(peaks) <= 256 * 1024**2\n"
        f"    assert sums == {[6] * len(uuids)!r}\n"
        "    print(json.dumps({'shared_g4_cuda': True, 'visible_uuids': visible,\n"
        "        'cuda_device_count': torch.cuda.device_count(), 'sums': sums,\n"
        "        'held_tensor_mib_per_process': 64, 'held_tensor_mib_per_device': tensor_bytes / 1024**2,\n"
        "        'allocator_limit_mib_per_process': 256, 'allocator_limit_mib_per_device': allocator_limit / 1024**2,\n"
        "        'allocator_peak_reserved_bytes_per_device': peaks, 'cuda_runtime': torch.version.cuda}), flush=True)\n"
        f"    time.sleep({hold_seconds})\n" + result_writer
    ).encode()


class _SharedGpuAgent(_GpuBatchAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if request.command is not AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT:
            return response
        first = request.task_context.key.experiment_ordinal == 1
        artifacts = tuple(
            WorkspaceArtifact.from_bytes(
                item.path,
                _shared_cuda_source(
                    item.content_bytes(),
                    uuids=self.uuids,
                    hold_seconds=30 if first else 3,
                ),
            )
            if item.path == "experiment.py"
            else item
            for item in response.proposed_workspace_artifacts
        )
        return with_output_digest(
            replace(response, proposed_workspace_artifacts=artifacts)
        )


class _SharedGpuScenario(_GpuScenario):
    def __init__(self, root, uuids):
        super().__init__(root, ("chain-a",), uuids)
        self.reviser = _SharedGpuAgent(("chain-a",), uuids)
        self.resource_steps = defaultdict(int)
        self.resource_queries = {}
        self.skill_results = {}
        self.history = {}

    def record(self, document):
        request = _request_from_provider(document)
        identity = request.attempt_id
        try:
            assert request.schema_version == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
            if request.command in _HISTORY_COMMANDS:
                item = next(
                    item
                    for item in request.input_artifacts
                    if item.ref.endswith("--resource-experience")
                )
                history = json.loads(item.content_bytes())
                assert history["mode"] == "shared-pool"
                assert self.history.setdefault(identity, history) == history
            if request.command in _RESOURCE_COMMANDS:
                phase = self.resource_steps[identity]
                if phase < 3:
                    phase += 1
                    self.resource_steps[identity] = phase
                    if phase > 1:
                        label = "resource_skill_" if phase == 2 else "resource_query_"
                        result = _tool_results(document)[label + identity]
                        assert not result.get("is_error"), result
                        text = _result_text(result)
                        if phase == 2:
                            # Native Skill acknowledges the call, then injects
                            # the actual body as a separate user text block.
                            bodies = [
                                block["text"]
                                for message in document["messages"]
                                for block in message.get("content", ())
                                if isinstance(block, dict)
                                and block.get("type") == "text"
                                and "Base directory for this skill: /expert-plugin/skills/resource-planning"
                                in block.get("text", "")
                            ]
                            assert bodies, (
                                "native Skill body was not injected after its acknowledgment"
                            )
                            text = bodies[-1]
                            assert "Device used/free memory is" in text
                            self.skill_results[identity] = text
                        else:
                            value = json.loads(text)
                            assert value["status"] == "ok"
                            assert (
                                value["measurement_scope"]
                                == "current_run_role_component_family"
                            )
                            assert {
                                item["gpu_uuid"] for item in value["devices"]
                            } == set(self.reviser.uuids)
                            self.resource_queries[identity] = value
                    if phase < 3:
                        self.requests.append(document)
                        self.current = request
                        self.agent_requests[identity] = request
                        return len(self.requests)
        except Exception as exc:
            self.errors.append(
                f"shared resource interface: {type(exc).__name__}: {exc}"
            )
        return super().record(document)

    def tool_calls(self, number):
        request = self.current
        if request is not None and request.command in _RESOURCE_COMMANDS:
            phase = self.resource_steps[request.attempt_id]
            if phase == 1:
                return (
                    (
                        "resource_skill_" + request.attempt_id,
                        "Skill",
                        {
                            "skill": _resources(request)["plugin"]["name"]
                            + ":resource-planning"
                        },
                    ),
                )
            if phase == 2:
                return (
                    (
                        "resource_query_" + request.attempt_id,
                        "mcp__host-tools__get_resources",
                        {},
                    ),
                )
        return super().tool_calls(number)

    def _unscoped_tool_calls(self, number):
        return tuple(
            (identity, name, {**arguments, "resource_estimate": dict(_ESTIMATE)})
            if name == "mcp__host-tools__submit_experiment"
            else (identity, name, arguments)
            for identity, name, arguments in super()._unscoped_tool_calls(number)
        )


def _new_shared_host(root, executable, provider, key, resources, socket, namespace):
    return WorkflowHost(
        root / "state",
        claude_agent_host=ClaudeAgentHost(
            claude_executable=executable, settings_path=root / "settings.json"
        ),
        claude_model=_MODEL,
        provider_environment={
            "ANTHROPIC_API_KEY": key,
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}",
        },
        compute_resources=resources,
        gpu_pool_socket=socket,
        gpu_pool_namespace=namespace,
        gpu_pool_server_uid=os.getuid(),
        literature_io=FixedLiteratureIO(
            OpenAlexLiteratureFetcher(
                api_key="local-literature-fixture-key",
                transport=_CatalogTransport(),
                resolver=lambda _hostname: ("8.8.8.8",),
            )
        ),
        grounding_web=_NoPublicWeb(),
    )


def _usage(observation):
    usage = observation["resource_usage"]
    return [usage] if isinstance(usage, dict) else usage


def _assert_host_closure(host, scenario, uuids, root, key, initial_memory_mib=None):
    attempts = host.store.list_attempts()
    assert all(
        row["state"] == "CLOSED" and row["terminal_reason"] == "SUCCEEDED"
        for row in attempts
    )
    processes = sorted(
        (row for row in attempts if row["kind"] == "PROCESS"),
        key=lambda row: row["started_at"],
    )
    assert len(processes) == 2
    assert (
        scenario.local_tests
        == set(scenario.execution_results)
        == {("chain-a", 1), ("chain-a", 2)}
    )
    assert len(scenario.resource_queries) == len(scenario.skill_results) == 4
    process_ids = {row["attempt_id"] for row in processes}
    for request in scenario.agent_requests.values():
        if request.command in _RESOURCE_COMMANDS:
            expected = (
                set()
                if request.task_context.key.experiment_ordinal == 1
                else {processes[0]["attempt_id"]}
            )
            query = scenario.resource_queries[request.attempt_id]
            assert {item["attempt_id"] for item in query["measurements"]} == expected
        if request.command in {AgentCommand.CYCLE_REPORT, AgentCommand.SELF_EVOLVE}:
            assert {
                item["attempt_id"]
                for item in scenario.history[request.attempt_id]["measurements"]
            } == process_ids
    dispatches = [
        json.loads(row["payload_json"])
        for row in host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND state='EXECUTING'",
            (_RUN_ID,),
        )
    ]
    assert len(dispatches) == 2
    peak_mib = None
    budgets = []
    for ordinal, process in enumerate(processes):
        spec = host.store.find_fact(
            _RUN_ID, "ExperimentPreregistered", process["workflow_id"]
        )
        contract = spec["payload"]["execution_contract"]
        assert "resource_grant" not in contract
        assert contract["resource_intent"]["allowed_gpu_uuids"] == sorted(uuids)
        assert contract["resource_intent"]["gpu_count"] == len(uuids)
        budget = contract["resource_intent"]["workload_memory_mib"]
        budgets.append(budget)
        command, _, source, _ = host._submission_source(
            contract["source_submit_command_id"]
        )
        assert (
            json.loads(command["request_json"])["arguments"]["resource_estimate"]
            == _ESTIMATE
        )
        expected_ids = [] if ordinal == 0 else [processes[0]["attempt_id"]]
        assert (
            source["arguments"]["resource_estimate"]["measurement_attempt_ids"]
            == expected_ids
        )
        expected_budget = (
            max(128, initial_memory_mib or 0) if ordinal == 0 else max(128, math.ceil(peak_mib * 1.25 / 64) * 64)
        )
        assert budget == expected_budget
        dispatch = next(
            item for item in dispatches if item["attempt_id"] == process["attempt_id"]
        )
        grant = dispatch["resource_grant"]
        selected = grant.get("gpu_uuids", [grant.get("gpu_uuid")])
        assert set(selected) == set(uuids)
        observation = json.loads(process["observation_json"])
        argv = observation["process_request"]["argv"]
        triples = [
            tuple(argv[index : index + 3])
            for index, item in enumerate(argv)
            if item in {"--setenv", "--dev-bind"}
        ]
        assert ("--setenv", "CUDA_VISIBLE_DEVICES", ",".join(selected)) in triples
        nodes = {
            f"/dev/nvidia{gpu.index}"
            for gpu in host.compute_resources.snapshot().gpus
            if gpu.uuid in uuids
        }
        assert {item[1] for item in triples if item[0] == "--dev-bind"} == nodes | {
            "/dev/nvidiactl",
            "/dev/nvidia-uvm",
        }
        usage = _usage(observation)
        assert {item["gpu_uuid"] for item in usage} == set(uuids)
        owned = [item["owned_memory"] for item in usage]
        assert all(
            item["sample_count"] > 0 and item["unknown_sample_count"] == 0
            for item in owned
        )
        assert all(0 < item["peak"]["resident_bytes"] <= 1024 * _MIB for item in owned)
        peak_mib = max(
            math.ceil(item["peak"]["resident_bytes"] / _MIB) for item in owned
        )
        stdout = (
            host.publisher.root
            / "result-staging"
            / process["attempt_id"]
            / "logs"
            / "stdout.log"
        )
        records = [
            json.loads(line)
            for line in stdout.read_text().splitlines()
            if line.startswith('{"shared_g4_cuda":')
        ]
        assert len(records) == 1
        assert set(records[0]["visible_uuids"]) == set(uuids)
        assert records[0]["sums"] == [6] * len(uuids)
        assert records[0]["held_tensor_mib_per_process"] == 64
        assert records[0]["held_tensor_mib_per_device"] == 64 / len(uuids)
        assert records[0]["allocator_limit_mib_per_process"] == 256
        assert records[0]["allocator_limit_mib_per_device"] == 256 / len(uuids)
        peaks = records[0]["allocator_peak_reserved_bytes_per_device"]
        assert len(peaks) == len(uuids) and sum(peaks) <= 256 * _MIB
    if initial_memory_mib is None:
        assert budgets[1] >= budgets[0]
    else:
        assert budgets[1] < budgets[0] == initial_memory_mib
    agents = [
        json.loads(row["observation_json"])
        for row in attempts
        if row["kind"] == "AGENT"
    ]
    for command in ("ADJUDICATE", "CYCLE_REPORT", "SELF_EVOLVE", "FINAL_REPORT"):
        assert sum(row["request"]["command"] == command for row in agents) == 1
    for observation in agents:
        assert observation["request"]["schema_version"] == 20
        session = observation["session"]
        assert session["transcript_complete"]
        transcript = (
            host.publisher.root / session["transcript"]["path"] / "transcript.jsonl"
        )
        raw = transcript.read_bytes()
        assert key.encode() not in raw
        if observation["request"]["command"] in {
            item.value for item in _RESOURCE_COMMANDS
        }:
            assert b"mcp__host-tools__get_resources" in raw
            assert b"resource-planning" in raw
    audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN_ID)
    (root / "oracle.json").write_text(json.dumps(audit.to_dict(), indent=2))
    assert audit.ok, audit.to_dict()
    assert audit.canonical_outcome_count == 2
    assert audit.open_attempt_count == audit.pending_effect_count == 0
    assert audit.artifact_counts["TRANSCRIPT"] == len(agents)
    assert not host.running and not host.running_agents and not host.projection_errors
    assert not audit.owned_process_residue
    assert all(
        not process_group_exists(row["process_group_id"])
        for row in attempts
        if row["process_group_id"] is not None
    )
    return processes, budgets


@_SHARED_GPU
def test_native_two_hosts_share_real_cuda_and_learn_from_owned_measurements(tmp_path):
    _exercise_shared_cuda(tmp_path)


@_SHARED_GPU
def test_native_initial_memory_waits_then_calibrates_into_same_gpu_overlap(tmp_path):
    _exercise_shared_cuda(tmp_path, initial_memory_mib=16384)


def _exercise_shared_cuda(tmp_path, *, initial_memory_mib=None):
    first, runtime = _configured_gpu()
    second = os.environ.get("HYPOLOOM_REAL_GPU_SECOND_UUID")
    assert (
        second
        and second == second.strip()
        and second.startswith("GPU-")
        and second != first
    )
    authorized = (first, second)
    initial = observe_authorized_gpu_memory(authorized)
    assert {item.uuid for item in initial} == set(authorized)
    assert all(item.free_memory_mib >= 6144 for item in initial), initial
    executable = _installed_claude()
    attribution = GpuAttributionSampler()
    pool = GpuPool(
        tmp_path / "pool",
        members={os.getuid(): authorized},
        reserve_memory_mib=512,
        sampler=lambda: CapacitySample(observe_authorized_gpu_memory(authorized)),
        attribution_sampler=attribution.pool_residents,
    )
    socket = tmp_path / "pool.sock"
    entries, snapshots, admitted_targets, capacity_waits = [], [], [], []
    cleanup = ExitStack()
    server = PoolServer(pool, socket)
    cleanup.callback(server.close)
    try:
        server.start()
        client = PoolClient(socket, expected_server_uid=os.getuid())
        for name, uuids in (("plural", authorized), ("single", (first,))):
            root = tmp_path / name
            root.mkdir()
            (root / "settings.json").write_text("{}\n")
            (root / "settings.json").chmod(0o600)
            scenario = _SharedGpuScenario(root, uuids)
            key = "local-shared-g4-" + secrets.token_hex(16)
            provider, thread = _start_provider(scenario, key)
            cleanup.callback(thread.join, timeout=5)
            cleanup.callback(provider.server_close)
            cleanup.callback(provider.shutdown)
            entry = {
                "root": root,
                "scenario": scenario,
                "provider": provider,
                "thread": thread,
                "key": key,
                "host": None,
                "uuids": uuids,
            }
            entries.append(entry)
            resources = LocalComputeResources(
                allowed_gpu_uuids=uuids, gpu_runtime_prefix=runtime
            )
            assert {item.uuid for item in resources.snapshot().gpus} == set(uuids)
            host = _new_shared_host(
                root, executable, provider, key, resources, socket, name
            )
            entry["host"] = host
            cleanup.callback(host.close)
            assert host.version.research_contract_version == "hypoloom-research-v12"
            payload = _gpu_payload(root, _RUN_ID, uuids, runtime, ("chain-a",))
            admitted_targets.append(
                Path((root / "admitted-target.txt").read_text().strip())
            )
            payload["research_contract"]["budget"]["min_vram_per_experiment"] = 64
            if initial_memory_mib is not None:
                payload["research_contract"]["budget"]["initial_vram_per_experiment"] = initial_memory_mib
            host.initialize()
            created = host.handle(
                ControlRequest(
                    schema_version=1,
                    request_id="create-shared-g4",
                    expected_revision=0,
                    command="CREATE_RUN",
                    payload=payload,
                )
            )
            assert created.status == "accepted", created
        deadline, next_sample = time.monotonic() + 540, 0.0
        while time.monotonic() < deadline:
            for entry in entries:
                entry["host"].tick()
                assert not entry["scenario"].errors, entry["scenario"].errors
                if initial_memory_mib is not None:
                    host = entry["host"]
                    for experiment_id, (_, reason) in host.resource_waits.items():
                        if reason == "capacity_unavailable":
                            assert not host.store.list_attempts(workflow_id=experiment_id)
                            observation = {"namespace": entry["root"].name,
                                "experiment_id": experiment_id, "reason": reason}
                            if observation not in capacity_waits:
                                capacity_waits.append(observation)
            if time.monotonic() >= next_sample:
                snapshots.append(client.request("snapshot", {}))
                next_sample = time.monotonic() + 0.5
            states = [
                entry["host"].store.latest_transition(_RUN_ID)["state"]
                for entry in entries
            ]
            assert not {"FAILED", "CANCELLED"} & set(states), states
            if states == ["COMPLETED", "COMPLETED"]:
                break
            time.sleep(0.02)
        assert states == ["COMPLETED", "COMPLETED"]
        closed = [
            _assert_host_closure(
                entry["host"],
                entry["scenario"],
                entry["uuids"],
                entry["root"],
                entry["key"],
                initial_memory_mib,
            )
            for entry in entries
        ]
        if initial_memory_mib is None:
            plural, single = (item[0][0] for item in closed)
            assert max(plural["started_at"], single["started_at"]) < min(
                plural["ended_at"], single["ended_at"])
        else:
            assert capacity_waits, "initial memory must cause a real pre-Process queue"
            assert any(max(plural["started_at"], single["started_at"]) < min(
                plural["ended_at"], single["ended_at"])
                for plural in closed[0][0] for single in closed[1][0]), "calibrated work never shared a GPU"
        assert any(
            item["gpu_uuid"] == first
            and item["active_commitments"] == 2
            and item["unknown_attribution_commitments"] == 0
            and item["outstanding_memory_mib"] < (initial_memory_mib or 256)
            for sample in snapshots
            for item in sample["devices"]
        ), "No actual shared resident credit was observed"
        rows = _rows(pool.database_path, "SELECT * FROM requests ORDER BY request_seq")
        assert len(rows) == 4 and {row["namespace"] for row in rows} == {
            "plural",
            "single",
        }
        assert {row["run_id"] for row in rows} == {_RUN_ID}
        assert len({row["attempt_id"] for row in rows}) == 2
        assert {row["state"] for row in rows} == {"RELEASED"}
        assert all(
            item["active_commitments"] == 0
            for item in client.request("snapshot", {})["devices"]
        )
        assert audit_gpu_pool(pool.root)["ok"]
        for entry, peer in ((entries[0], entries[1]), (entries[1], entries[0])):
            assert peer["scenario"].chain_nonces["chain-a"] not in json.dumps(
                entry["scenario"].requests
            )
    finally:
        try:
            owned_groups = {
                row["process_group_id"]
                for entry in entries
                if entry["host"] is not None
                for row in entry["host"].store.list_attempts()
                if row["process_group_id"] is not None
            }
        finally:
            cleanup.close()
        for entry in entries:
            scenario = entry["scenario"]
            (entry["root"] / "shared-native-evidence.json").write_text(
                json.dumps(
                    {
                        "errors": scenario.errors,
                        "requests": scenario.requests,
                        "resource_queries": scenario.resource_queries,
                        "history": scenario.history,
                        "skill_results": scenario.skill_results,
                    },
                    indent=2,
                )
            )
        (tmp_path / "pool-snapshots.json").write_text(json.dumps(snapshots, indent=2))
        if initial_memory_mib is not None:
            (tmp_path / "capacity-waits.json").write_text(json.dumps(capacity_waits, indent=2))
        for target in admitted_targets:
            assert target.parent == Path("/var/tmp") and target.name.startswith(
                "hypoloom-native-batch-target-"
            )
            shutil.rmtree(target)
        assert all(not process_group_exists(group) for group in owned_groups)
    assert not socket.exists() and not server.thread.is_alive()


@pytest.mark.parametrize("count", [1, 2])
def test_shared_canary_cuda_amounts_are_bounded_per_process(tmp_path, monkeypatch, capsys, count):
    from types import SimpleNamespace

    held, ceilings = [], []
    def allocate(size, *, dtype, device):
        held.append(size)
        return object()
    def limit(fraction, index):
        ceilings.append(fraction * 1024**3)
    cuda = SimpleNamespace(
        is_available=lambda: True, device_count=lambda: count,
        get_device_properties=lambda index: SimpleNamespace(total_memory=1024**3),
        set_per_process_memory_fraction=limit, synchronize=lambda index: None,
        max_memory_reserved=lambda index: held[index] + _MIB,
    )
    torch = SimpleNamespace(cuda=cuda, uint8="uint8", ones=allocate,
        tensor=lambda values, device: SimpleNamespace(sum=lambda: SimpleNamespace(item=lambda: sum(values))),
        version=SimpleNamespace(cuda="offline-double"))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    uuids = tuple(f"GPU-test-{i}" for i in range(count))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", ",".join(uuids))
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    monkeypatch.setattr(sys, "argv", ["experiment.py"])
    source = _shared_cuda_source(b'import json\nresult = {"score": 6}\n', uuids=uuids, hold_seconds=0)
    exec(compile(source, "experiment.py", "exec"), {})
    assert sum(held) == 64 * _MIB
    assert sum(ceilings) == 256 * _MIB
    record = json.loads(capsys.readouterr().out)
    assert record["held_tensor_mib_per_process"] == 64
    assert record["held_tensor_mib_per_device"] == 64 / count
    assert record["allocator_limit_mib_per_process"] == 256
    assert record["allocator_limit_mib_per_device"] == 256 / count
    assert record["allocator_peak_reserved_bytes_per_device"] == [64 * _MIB // count + _MIB] * count


@pytest.mark.parametrize("count", [1, 2])
def test_shared_canary_local_check_never_imports_cuda(tmp_path, monkeypatch, count):
    source = _shared_cuda_source(
        b'import json, os\nfrom pathlib import Path\nresult = {"score": 6}\nPath(os.environ["HYPOLOOM_RESULT_PATH"]).write_text(json.dumps(result))\n',
        uuids=tuple(f"GPU-test-{number}" for number in range(count)),
        hold_seconds=30,
    )
    result = tmp_path / "result.json"
    monkeypatch.setenv("HYPOLOOM_RESULT_PATH", str(result))
    monkeypatch.setattr(sys, "argv", ["experiment.py", "--local-check"])
    namespace = {}
    exec(compile(source, "experiment.py", "exec"), namespace)
    assert json.loads(result.read_text()) == {"score": 6}
    assert "torch" not in namespace


@pytest.mark.parametrize("count", [1, 2])
def test_shared_native_submit_keeps_explicit_count_and_unresolved_estimate(
    tmp_path, count
):
    scenario = _SharedGpuScenario(
        tmp_path, tuple(f"GPU-test-{number}" for number in range(count))
    )
    request = default_request(AgentCommand.EXPERIMENT_EXECUTION)
    task = replace(
        request.task_context, key=replace(request.task_context.key, chain="chain-a")
    )
    request = replace(
        request, schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
        task_context=task, command_id=task.command_id
    )
    scenario.current = request
    scenario.steps[request.attempt_id] = 1
    scenario.designs[("chain-a", 1)] = {"resource_preference": "GPU_REQUIRED"}
    calls = scenario._unscoped_tool_calls(1)
    assert len(calls) == 1 and calls[0][1] == "mcp__host-tools__submit_experiment"
    assert calls[0][2]["gpus"] == count
    assert calls[0][2]["resource_estimate"] == _ESTIMATE
    assert calls[0][2]["resource_estimate"]["measurement_attempt_ids"] == []


@pytest.mark.parametrize("body_present", [True, False])
def test_native_fixture_queries_resources_before_ordinary_design_steps(
    tmp_path, monkeypatch, body_present
):
    """Exercise only the scripted provider's new prefix, not production mocks."""
    request = default_request(AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT)
    request = replace(
        request,
        schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
        input_artifacts=(
            *request.input_artifacts,
            EmbeddedInput.from_bytes(
                "test--resource-experience",
                "application/json",
                b'{"mode":"shared-pool","measurements":[]}',
            ),
            EmbeddedInput.from_bytes(
                "test--expert-resources",
                "application/json",
                b'{"plugin":{"name":"test-expert"}}',
            ),
        ),
    )
    scenario = _SharedGpuScenario(tmp_path, ("GPU-test",))
    base_calls = []
    monkeypatch.setattr(
        sys.modules[__name__], "_request_from_provider", lambda _: request
    )
    monkeypatch.setattr(
        _GpuScenario, "record", lambda self, document: base_calls.append(document) or 3
    )
    assert scenario.record({"messages": []}) == 1
    assert scenario.tool_calls(1)[0][1:] == (
        "Skill",
        {"skill": "test-expert:resource-planning"},
    )

    def result(label, content):
        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": label + request.attempt_id,
                            "content": content,
                        }
                    ],
                }
            ]
        }

    body = (
        "Base directory for this skill: /expert-plugin/skills/resource-planning\n"
        "Device used/free memory is not owned use."
    )
    document = result(
        "resource_skill_",
        "Launching skill: test-expert:resource-planning" if body_present else body,
    )
    if body_present:
        document["messages"].append(
            {"role": "user", "content": [{"type": "text", "text": body}]}
        )
    scenario.record(document)
    if not body_present:
        assert any(
            "native Skill body was not injected" in error for error in scenario.errors
        )
        assert not scenario.skill_results
        return
    assert scenario.skill_results[request.attempt_id] == body
    assert scenario.tool_calls(2)[0][1:] == ("mcp__host-tools__get_resources", {})
    assert not base_calls
    query = {
        "status": "ok",
        "measurement_scope": "current_run_role_component_family",
        "devices": [{"gpu_uuid": "GPU-test"}],
        "measurements": [],
    }
    scenario.record(result("resource_query_", json.dumps(query)))
    assert len(base_calls) == 1 and not scenario.errors
    assert scenario.resource_queries[request.attempt_id] == query
