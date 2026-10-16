"""Opt-in real-model resource-path G5, not common scientific-goal qualification.

Only the explicitly selected live test may start native CC, the private pool or
CUDA. All preparation/projection tests are offline. No scripted model adapter or
FakeAgent is a substitute for this test's actual WorkflowHost/CC boundary.

Live selection requires REAL_CLAUDE, REAL_GPU, REAL_NATIVE_GPU and
REAL_SHARED_RESOURCE_G5=1 (all prefixed HYPOLOOM_), plus the existing
CLAUDE_EXECUTABLE, CLAUDE_SETTINGS, CLAUDE_MODEL, REAL_GPU_UUID and REAL_GPU_RUNTIME
variables. RESOURCE_G5_ALLOCATION_USD must explicitly supply the approved fresh
allowance; it does not settle previous bills. Select only test_real_model_shared_gpu_resource_path.
Run from the intentionally qualified candidate, with a fresh pytest basetemp.
"""

from __future__ import annotations

import builtins
import copy
import difflib
import io
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, replace
from decimal import Decimal

import pytest
import yaml
from jsonschema import Draft202012Validator

from hypoloom.agent_aci import CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION, AgentBudget, EmbeddedInput, WorkspaceArtifact
from hypoloom.agent_budget import (
    charged_cost,
    charged_totals,
    fold_agent_attempts,
)
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION,
    ClaudeAgentHost,
    process_group_exists,
)
from hypoloom.compute_resources import (
    LocalComputeResources,
    observe_authorized_gpu_memory,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import ResourceEstimate
from hypoloom.execution_workspace import (
    ExecutionWorkspace,
    bind_clean_git_target,
)
from hypoloom.gpu_attribution import GpuAttributionSampler
from hypoloom.gpu_pool import CapacitySample, GpuPool
from hypoloom.gpu_pool_client import PoolClient
from hypoloom.gpu_pool_server import PoolServer
from hypoloom.host_tools_server import _RESOURCE_TOOLS
from hypoloom.literature import FixedLiteratureIO
from hypoloom.oracle import Oracle, audit_gpu_pool
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost

from test_claude_measurement_g3 import _NoPublicWeb
from test_profile_v4 import documents
from test_real_current_host_g5 import (
    _NODE_BUDGET,
    _assert_current_agent_identity,
    _assert_launch_thresholds,
    _canary_accounting,
    _canary_launch,
    _drive_canary,
    _git,
    _native_tools,
    _positive_allocation,
)
from test_real_expert_gpu_qualification import _configured_gpu
from test_real_expert_loop_qualification import _DeterministicOpenAlexFixture


_RUN_ID = "real-resource-path-g5"
_NAMESPACE = "resource-g5"
_WORKLOAD = {"program": "bounded-cuda-checksum", "tensor_mib": "64", "dtype": "uint8"}
_NODE = replace(_NODE_BUDGET, max_turns=32)
_WALL_SECONDS = 1200
_RUN_TURNS = 384
_FLOOR_MIB = 1024
_INITIAL_MIB = 2048
_RESERVE_MIB = 512
_GPU_SECONDS = 30
_FLAG = "HYPOLOOM_REAL_SHARED_RESOURCE_G5"
_REQUIRED_FLAGS = (
    _FLAG,
    "HYPOLOOM_REAL_CLAUDE",
    "HYPOLOOM_REAL_GPU",
    "HYPOLOOM_REAL_NATIVE_GPU",
)
_FIXTURE_UUID = "GPU-00000000-0000-0000-0000-000000000001"


def _enabled(environment):
    return all(environment.get(name) == "1" for name in _REQUIRED_FLAGS)


def _prepare_profile(
    root, mount, model, uuid, runtime, *, allocation="6", node_budget=_NODE
):
    """Fresh fixture/Profile admission only; never inspect hardware or auth."""
    allocation = _positive_allocation(allocation)
    assert isinstance(node_budget, AgentBudget)
    root, mount, runtime = Path(root), Path(mount), Path(runtime).resolve()
    assert runtime.is_dir() and (runtime / "bin" / "python").is_file()
    root.mkdir(parents=True, exist_ok=False)
    mount.mkdir(parents=True, exist_ok=True)
    assert not list(mount.iterdir()), "qualification mount must be fresh and empty"
    target, data, profile = mount / "target", mount / "data", root / "profile"
    for path in (target, data, profile):
        path.mkdir()
    objective = (
        "Qualify only native resource-planning compatibility, not a new scientific result. "
        "One expert, one hypothesis, one CUDA-only checksum experiment. Read the admitted "
        "cuda_probe.py and validation.json. Keep the supplied CUDA probe intact; its run_probe() "
        "holds a 64MiB uint8 tensor, calculates sum([1,2,3]) on CUDA and returns checksum 6. "
        "In DESIGN invoke the native resource-planning Skill, call mcp__host-tools__get_resources, "
        "and run python cuda_probe.py --local-check (CPU-only validation). Implement experiment.py "
        "which at actual runtime adds HYPOLOOM_TARGET_ROOT to sys.path, imports cuda_probe, "
        "calls run_probe() exactly once and writes the returned, computed checksum through the "
        "exact rendered result contract at HYPOLOOM_RESULT_PATH. Do not hardcode a metric, "
        "copy the probe, change its bounds, or attempt CUDA from the Agent sandbox. "
        "The wrapper must branch on --local-check before GPU work: validate its own configuration "
        "and result-writing code using the probe's CPU local_check(), without importing torch or calling run_probe. "
        "In DESIGN actually run python experiment.py --local-check with Bash and inspect its successful output. "
        "Use exactly this wrapper-test command as its own Bash call:\n"
        "HYPOLOOM_TARGET_ROOT=/workspace HYPOLOOM_RESULT_PATH=/tmp/test_result.json python /workspace/experiment.py --local-check\n"
        "Use native Read in a later call to inspect /tmp/test_result.json. "
        "If an earlier check left an output file, remove it in a separate Bash call before this check. "
        "Do not prefix the wrapper-test call with cd or rm, or append echo, cat, assertions or cleanup. "
        "Keep setup and cleanup in separate calls even after repairing a failed local check. "
        "Do not treat the probe-only check as a check of your wrapper. Host's candidate validation compiles "
        "the source without executing it; --local-check is this canary's explicit Agent-side CPU test, "
        "not an automatically injected Host argument. Only normal Host Process execution calls run_probe(). "
        "EXECUTION: invoke resource-planning, query get_resources again, choose GPU_REQUIRED and "
        "gpus=1, and pass resource_estimate to submit_experiment with workload labels "
        + json.dumps(_WORKLOAD, sort_keys=True)
        + ". Estimate a conservative per-device peak including CUDA context and uncertainty "
        "headroom; explain the basis, distinguish it from device reserve and whole-device pressure. "
        "This fresh cohort has no prior comparable measurements: measurement_attempt_ids must be []. "
        "Never submit UUIDs, reserve changes or runtime overrides; Host chooses the authorized card. "
        "Use wait_experiment for the actual terminal result; after the terminal wait call "
        "get_resources again and read your own exact Process Attempt ID, measured peak/unknown "
        "status and admitted budget. Do not fabricate a measured value or another run's evidence. "
        "The tiny explicit checksum >= 6 goal ends the run after one accepted experiment. "
        "Use the native final-report Skill for a concise report citing actual supplied references "
        "and limitations; this is not Grounding-authored common scientific acceptance. "
        "No source repair, training, downloads, installs, public web, notifications or deliberate "
        "OOM. The supplied literature is an explicitly non-authoritative qualification fixture. "
        "At every node, use tools as needed but return the final response as one JSON object without prose or Markdown fences, "
        "matching that node's supplied schema; do not return an explanatory preamble or schema keywords. "
        "Fixture landing/source URLs are catalog metadata, not retrieved web sources. "
        "This canary disables public web retrieval: web_sources must be omitted or [] according to the node schema "
        "(use [] when required). Cite actual supplied packet IDs in supporting_refs and report the evidence gaps; "
        "do not convert a metadata URL or local file read into a claim of web retrieval. "
        "This Profile's declared metric is already the experiment evaluator; common acceptance was not commissioned. "
        "GROUNDING must return acceptance: null and supply the observed background, not declare or author another evaluator. "
        "The shared_goal completion mode does not change that commission boundary."
    )
    (target / "cuda_probe.py").write_text(_probe_source())
    (target / "README.md").write_text(objective + "\n")
    (data / "validation.json").write_text(
        json.dumps({"values": [1, 2, 3], "checksum": 6})
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Canary",
        "-c",
        "user.email=canary@example.invalid",
        "commit",
        "-qm",
        "Disposable resource qualification target",
    )
    source = documents()
    source["goal"] = {
        "title": "Bounded resource-path compatibility",
        "description": objective,
        "metric": {"name": "checksum", "direction": "maximize", "target": 6},
        "constraints": [
            "One authorized GPU; never operate foreign workloads",
            "Keep probe and data unchanged",
        ],
        "stop_conditions": {
            "max_rounds_per_expert": 1,
            "max_wall_clock_hours": _WALL_SECONDS / 3600,
        },
    }
    source["experts"] = {
        "defaults": {
            "model": model,
            "effort": "medium",
            "max_forks": 1,
            "max_hypotheses_per_round": 1,
        },
        "experts": [
            {
                "name": "researcher",
                "role": "CUDA resource compatibility researcher",
                "persona": "Measure honestly; distinguish own use from shared pressure.",
                "focus": [objective],
            }
        ],
    }
    source["methodology"] = {
        "schema_version": 4,
        "output_correction": {"max_failed_validations": 3},
        "runtime": {"target_repo": str(target), "conda_env": str(runtime)},
        "budget": {
            "max_active_hypotheses": 1,
            "max_active_agents": 1,
            "max_active_processes": 1,
            "max_active_reviews": 1,
            "max_experiments": 1,
            "max_retries": 1,
            "max_infrastructure_retries": 0,
            "max_process_lost_retries": 0,
            "max_turns": _RUN_TURNS,
            "min_vram_per_experiment": _FLOOR_MIB,
            "initial_vram_per_experiment": _INITIAL_MIB,
            "gpu_vram_reserve_mb": _RESERVE_MIB,
            "agent_invocation": node_budget.to_dict(),
        },
        "literature": {"enabled": False, "tools": [], "max_sources": 3},
        "inner_loop": {"max_iterations_per_hypothesis": 1, "repair_attempts": 1},
        "interaction": {
            "leader": {"enabled": False},
            "debate": {"enabled": False, "rounds": 3},
        },
        "graph_evolution": {"enabled": False, "stagnation_rounds": 1},
    }
    source["language"] = {
        key: "en"
        for key in ("prompt_language", "report_language", "code_comment_language")
    }
    source["notify"] = {"sinks": {}, "events": {}}
    source["safety"] = {
        "allow_read_paths": [str(data)],
        "sandbox_os": "linux_bwrap",
        "gpus": [uuid],
        "max_gpus_per_experiment": 1,
        "experiment_timeout_minutes": _GPU_SECONDS / 60,
        "total_budget_usd": float(allocation),
        "per_expert_budget_usd": float(allocation),
        "bash": {"deny_patterns": [], "network_allowed": False},
        "filesystem": {
            "write_only_within_worktree": True,
            "protected_paths": [".git/"],
        },
    }
    for name, value in source.items():
        (profile / (name + ".yaml")).write_text(yaml.safe_dump(value))
    admission = admit_profile(profile, run_id=_RUN_ID, allow_gpu=True)
    assert admission.admitted and admission.create_run_payload is not None, (
        admission.to_dict()
    )
    (root / "profile-admission.json").write_text(
        json.dumps(admission.to_dict(), indent=2)
    )
    (root / "fixture-paths.json").write_text(
        json.dumps(
            {"target": str(target), "data": str(data), "profile": str(profile)},
            indent=2,
        )
    )
    return profile, admission.create_run_payload


def _probe_source():
    return '''"""Bounded CUDA-only experiment; --local-check never imports torch."""
import json
import os
import time


def local_check():
    return sum([1, 2, 3])


def run_probe():
    import torch
    assert torch.cuda.is_available() and torch.cuda.device_count() == 1
    total = torch.cuda.get_device_properties(0).total_memory
    torch.cuda.set_per_process_memory_fraction(256 * 1024**2 / total, 0)
    held = torch.ones(64 * 1024**2, dtype=torch.uint8, device="cuda:0")
    values = torch.tensor([1, 2, 3], device="cuda:0")
    checksum = int(values.sum().item())
    torch.cuda.synchronize(0)
    assert checksum == local_check() and held.numel() == 64 * 1024**2
    reserved = torch.cuda.max_memory_reserved(0)
    assert reserved <= 256 * 1024**2
    print(json.dumps({"resource_g5_cuda": True, "visible_uuids": os.environ["CUDA_VISIBLE_DEVICES"].split(","),
        "cuda_device_count": torch.cuda.device_count(), "checksum": checksum, "held_tensor_mib": 64,
        "allocator_reserved_bytes": reserved, "cuda_runtime": torch.version.cuda}), flush=True)
    time.sleep(5)
    return checksum


if __name__ == "__main__":
    import sys
    assert sys.argv[1:] == ["--local-check"], "CUDA is only called by the Host-owned experiment"
    print(local_check())
'''


def _resource_trace(content, uuid, plugin, *, schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION):
    uses, results = _native_tools(content)
    successful = {row["tool_use_id"]: row for row in results if not row.get("is_error")}
    assert any(
        row["name"] == "Skill"
        and row["input"].get("skill") == plugin + ":resource-planning"
        and row["id"] in successful
        for row in uses
    ), "missing successful native resource Skill call"
    bodies = [
        block.get("text", "")
        for line in content.splitlines()
        for block in json.loads(line).get("message", {}).get("content", ())
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    assert any(
        "Base directory for this skill: /expert-plugin/skills/resource-planning" in body
        and "Device used/free memory is" in body
        for body in bodies
    ), "Skill acknowledgment is not its loaded body"
    queries = []
    for use in uses:
        if (
            use["name"] != "mcp__host-tools__get_resources"
            or use["id"] not in successful
        ):
            continue
        if schema_version < 27:
            assert use["input"] == {}
        else:
            assert not list(Draft202012Validator(_RESOURCE_TOOLS[0]["inputSchema"]).iter_errors(use["input"]))
            assert use["input"].get("gpus", 1) == 1
        content = successful[use["id"]]["content"]
        if isinstance(content, list):
            content = "\n".join(
                item["text"] for item in content if item.get("type") == "text"
            )
        value = json.loads(content)
        assert value["mode"] == "shared-pool" and value["status"] == "ok"
        assert value["measurement_scope"] == "current_run_role_component_family"
        assert {item["gpu_uuid"] for item in value["devices"]} == {uuid}
        queries.append(value)
    assert queries, "no successful actual resource MCP result"
    return queries


def _assert_submit_estimate(arguments):
    assert arguments.get("candidate", {}).get("resource_preference") == "GPU_REQUIRED"
    assert type(arguments.get("gpus")) is int and arguments["gpus"] == 1
    assert not {
        "gpu_uuid",
        "gpu_uuids",
        "allowed_gpu_uuids",
        "reserve_memory_mib",
        "runtime_prefix",
        "runtime",
        "mounts",
        "resource_preference",
    } & set(arguments)
    estimate = ResourceEstimate.from_dict(arguments.get("resource_estimate"))
    assert estimate.workload == _WORKLOAD and estimate.measurement_attempt_ids == ()
    return estimate


def _assert_accepted_probe_unchanged(arguments, target):
    """Inspect the accepted overlay through the existing Git/workspace path."""
    patches = [
        WorkspaceArtifact.from_dict(item).content_bytes()
        for item in arguments["workspace_files"]
        if item["path"] == "implementation.patch"
    ]
    assert len(patches) == 1
    with tempfile.TemporaryDirectory(prefix="resource-g5-patch-check-") as directory:
        state = Path(directory)
        artifacts = state / "artifacts"
        artifacts.mkdir()
        workspace = ExecutionWorkspace(artifacts, state_root=state)
        with workspace.verified_detached_target_tree(
            bind_clean_git_target(target), patch=patches[0] or None
        ) as tree:
            probe = tree / "cuda_probe.py"
            assert probe.is_file() and not probe.is_symlink(), (
                "accepted patch changed the supplied probe"
            )
            assert probe.read_bytes() == _probe_source().encode(), (
                "accepted patch changed the supplied probe"
            )


def _has_wrapper_local_check(uses, results):
    successful = {item["tool_use_id"] for item in results if not item.get("is_error")}
    for use in uses:
        if use.get("name") != "Bash" or use.get("id") not in successful:
            continue
        try:
            command = use["input"]["command"].strip()
            # Recognize only the observed optional cwd, bounded temporary-dir
            # preparation and read-only display, not arbitrary shell programs.
            # A successful all-&& chain preserves every prerequisite and the
            # wrapper exit status before any permitted result display.
            cwd = None
            prepared = re.match(
                r"mkdir[ \t]+-p[ \t]+(?P<created>/tmp/[A-Za-z0-9_][A-Za-z0-9._-]*)[ \t]+&&[ \t]+"
                r"cd[ \t]+(?P<cwd>/tmp/[A-Za-z0-9_][A-Za-z0-9._-]*)[ \t]+&&[ \t]+",
                command,
            )
            if prepared:
                if prepared["created"] != prepared["cwd"]:
                    continue
                cwd = prepared["cwd"]
                command = command[prepared.end():]
            else:
                prefix = re.match(r"cd[ \t]+(/workspace|/tmp)[ \t]+&&[ \t]+", command)
                if prefix:
                    cwd = prefix[1]
                    command = command[prefix.end():]
            command = re.sub(r"[ \t]+&&[ \t]+cat[ \t]+/tmp/test_result\.json[ \t]*$", "", command)
            if re.search(r"[$`;&|<>\r\n]", command):
                continue
            words = shlex.split(command)
        except (KeyError, ValueError):
            continue
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./:+-]*", words[0]):
            words = words[1:]
        if (
            not words
            or re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", Path(words[0]).name) is None
        ):
            continue
        arguments = words[1:]
        while arguments and arguments[0] in {"-B", "-u", "-I"}:
            arguments = arguments[1:]
        if (
            len(arguments) == 2
            and arguments[0] in {"experiment.py", "./experiment.py", "/workspace/experiment.py"}
            and (not cwd or not cwd.startswith("/tmp") or arguments[0] == "/workspace/experiment.py")
            and arguments[1] == "--local-check"
        ):
            return True
    return False


def _pool_rows(pool):
    with sqlite3.connect(pool.database_path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute("SELECT * FROM requests ORDER BY request_seq")
        ]


def _plugin_name(request):
    resource = next(
        EmbeddedInput.from_dict(item)
        for item in request["input_artifacts"]
        if item["ref"].endswith("--expert-resources")
    )
    return json.loads(resource.content_bytes())["plugin"]["name"]


def _assert_measured_history(measured, attempt_id, admitted, peak_bytes):
    assert measured and {item["attempt_id"] for item in measured} == {attempt_id}
    assert all(
        item["workload"] == _WORKLOAD
        and item["measurement_status"] == "complete"
        and item["sample_peak_bytes"] == peak_bytes
        and item["admitted_workload_memory_mib"] == admitted
        and item["oom"] is False
        for item in measured
    )


def _close_live_effects(host, server):
    """Stop only this cohort's effects; failed OPEN/BOUND records stay intact."""
    errors = []
    if host is not None:
        try:
            host.close()
        except Exception as exc:
            errors.append("Host cleanup: " + str(exc))
    try:
        server.close()
    except Exception as exc:
        errors.append("pool service cleanup: " + str(exc))
    return errors


def _assert_resource_closure(host, pool, uuid, model, allocation, *, node_budget=_NODE):
    closure = _assert_resource_evidence(host, pool, uuid, model, allocation, node_budget=node_budget)
    assert not host.running and not host.running_agents and not host.projection_errors
    return closure


def _assert_resource_evidence(host, pool, uuid, model, allocation, *, node_budget=_NODE):
    """Read the actual Host source, native tools, Attempt binding and Oracle."""
    assert host.store.latest_transition(_RUN_ID)["state"] == "COMPLETED"
    rows = host.store.list_attempts()
    assert rows and all(row["state"] == "CLOSED" for row in rows)
    processes = [row for row in rows if row["kind"] == "PROCESS"]
    assert len(processes) == 1 and processes[0]["terminal_reason"] == "SUCCEEDED"
    process = processes[0]
    observation = json.loads(process["observation_json"])
    assert not observation.get("synthetic_before_spawn") and not observation.get(
        "resource_failure"
    )
    assert observation["returncode"] == 0
    spec = host.store.find_fact(
        _RUN_ID, "ExperimentPreregistered", process["workflow_id"]
    )
    contract = spec["payload"]["execution_contract"]
    assert "resource_grant" not in contract
    source_command, _, source, _ = host._submission_source(
        contract["source_submit_command_id"]
    )
    raw_arguments = json.loads(source_command["request_json"])["arguments"]
    estimate = _assert_submit_estimate(raw_arguments)
    _assert_accepted_probe_unchanged(
        raw_arguments,
        Path(host._research_contract(_RUN_ID).document["runtime"]["target_repo"]),
    )
    resolved = ResourceEstimate.from_dict(source["arguments"]["resource_estimate"])
    assert resolved == estimate, (
        "first fresh submit must not invent a measurement source"
    )
    admitted = max(_FLOOR_MIB, _INITIAL_MIB, estimate.peak_memory_mib)
    intent = contract["resource_intent"]
    assert intent == {
        "kind": "GPU",
        "allowed_gpu_uuids": [uuid],
        "gpu_count": 1,
        "workload_memory_mib": admitted,
        "reserve_memory_mib": _RESERVE_MIB,
        "sharing": "exclusive",
    }
    dispatches = [
        json.loads(row["payload_json"])
        for row in host.store.inspect(
            "SELECT payload_json FROM workflow_transitions WHERE run_id=? AND workflow_kind='EXPERIMENT' AND state='EXECUTING'",
            (_RUN_ID,),
        )
    ]
    assert len(dispatches) == 1 and dispatches[0]["attempt_id"] == process["attempt_id"]
    dispatch = dispatches[0]
    key = {
        "namespace": _NAMESPACE,
        "run_id": _RUN_ID,
        "execution_id": process["execution_id"],
        "attempt_id": process["attempt_id"],
    }
    assert dispatch["gpu_pool_key"] == key
    grant = dispatch["resource_grant"]
    assert grant.get("gpu_uuids", [grant.get("gpu_uuid")]) == [uuid]
    reservations = _pool_rows(pool)
    assert len(reservations) == 1
    reservation = reservations[0]
    assert {field: reservation[field] for field in key} == key
    assert reservation["uid"] == os.getuid() and reservation["state"] == "RELEASED"
    assert reservation["process_group_id"] == process["process_group_id"]
    argv = observation["process_request"]["argv"]
    triples = [
        tuple(argv[index : index + 3])
        for index, item in enumerate(argv)
        if item in {"--setenv", "--dev-bind"}
    ]
    assert ("--setenv", "CUDA_VISIBLE_DEVICES", uuid) in triples
    assert ("--setenv", "CUDA_DEVICE_ORDER", "PCI_BUS_ID") in triples
    device = next(
        gpu for gpu in host.compute_resources.snapshot().gpus if gpu.uuid == uuid
    )
    assert {item[1] for item in triples if item[0] == "--dev-bind"} == {
        f"/dev/nvidia{device.index}",
        "/dev/nvidiactl",
        "/dev/nvidia-uvm",
    }
    usage = observation["resource_usage"]
    usage = [usage] if isinstance(usage, dict) else usage
    assert len(usage) == 1 and usage[0]["gpu_uuid"] == uuid
    owned = usage[0]["owned_memory"]
    assert owned["sample_count"] > 0 and owned["unknown_sample_count"] == 0
    peak = owned["peak"]
    assert peak["complete"] is True and peak["gpu_uuid"] == uuid
    assert peak["root_process_group_id"] == process["process_group_id"]
    assert 64 * 1024**2 <= peak["resident_bytes"] <= admitted * 1024**2
    stdout = (
        host.publisher.root
        / "result-staging"
        / process["attempt_id"]
        / "logs"
        / "stdout.log"
    )
    cuda = [
        json.loads(line)
        for line in stdout.read_text().splitlines()
        if line.startswith('{"resource_g5_cuda":')
    ]
    assert len(cuda) == 1
    assert cuda[0]["visible_uuids"] == [uuid] and cuda[0]["cuda_device_count"] == 1
    assert cuda[0]["checksum"] == 6 and cuda[0]["held_tensor_mib"] == 64
    assert 0 < cuda[0]["allocator_reserved_bytes"] <= 256 * 1024**2
    assert cuda[0]["cuda_runtime"]
    agent_rows = [row for row in rows if row["kind"] == "AGENT"]
    traces, accepted, tools = {}, [], []
    for row in agent_rows:
        item = json.loads(row["observation_json"])
        assert item["request"]["schema_version"] == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
        assert item["session"]["transcript_complete"] is True
        archive = host.publisher.root / item["session"]["transcript"]["path"]
        content = (archive / "transcript.jsonl").read_bytes()
        uses, results = _native_tools(content)
        tools.extend(uses)
        if row["terminal_reason"] != "SUCCEEDED":
            continue
        _assert_current_agent_identity(item, model)
        accepted.append(item)
        command = item["request"]["command"]
        if command in {"EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION"}:
            assert item["request"]["role"] == "researcher"
            traces[command] = _resource_trace(
                content, uuid, _plugin_name(item["request"]),
                schema_version=item["request"]["schema_version"],
            )
        if command == "EXPERIMENT_DESIGN_IMPLEMENT":
            assert _has_wrapper_local_check(uses, results), (
                "the real model did not successfully CPU-check its wrapper"
            )
        if command == "FINAL_REPORT":
            assert any(
                use["name"] == "Skill"
                and use["input"]["skill"].endswith(":final-report")
                for use in uses
            )
            files = item["response"]["proposed_workspace_artifacts"]
            assert len(files) == 1
            assert (
                archive / "FINAL_REPORT.md"
            ).read_bytes() == WorkspaceArtifact.from_dict(files[0]).content_bytes()
    assert len(traces) == 2
    assert any(
        query["measurements"] == [] for query in traces["EXPERIMENT_DESIGN_IMPLEMENT"]
    )
    execution_queries = traces["EXPERIMENT_EXECUTION"]
    assert execution_queries[0]["measurements"] == []
    measured = [item for query in execution_queries for item in query["measurements"]]
    _assert_measured_history(
        measured, process["attempt_id"], admitted, peak["resident_bytes"]
    )
    assert {
        "mcp__host-tools__submit_experiment",
        "mcp__host-tools__wait_experiment",
    } <= {item["name"] for item in tools}
    commands = [item["request"]["command"] for item in accepted]
    assert commands.count("FINAL_REPORT") == 1 and not {"DEBATE", "OBSERVER"} & set(
        commands
    )
    outcomes = host.store.list_facts(_RUN_ID, "CanonicalOutcomeAccepted")
    assert len(outcomes) == 1 and outcomes[0]["experiment_id"] == process["workflow_id"]
    assert outcomes[0]["payload"]["outcome"] == "SUCCESS"
    assert Decimal(outcomes[0]["payload"]["metric_value"]) == 6
    charges = fold_agent_attempts(
        sorted(rows, key=lambda row: (row["started_at"], row["attempt_id"]))
    )
    assert all(charge.source != "declared" for charge in charges), charges
    _assert_launch_thresholds(charges, allocation, node_budget)
    assert charged_totals(charges)[1] <= _RUN_TURNS
    audit = Oracle(host.store.database_path, host.publisher.root).audit(_RUN_ID)
    assert audit.ok, audit.to_dict()
    assert audit.canonical_outcome_count == 1 and audit.artifact_counts[
        "TRANSCRIPT"
    ] == len(agent_rows)
    assert audit.open_attempt_count == audit.pending_effect_count == 0
    assert not audit.owned_process_residue
    assert all(
        not process_group_exists(row["process_group_id"])
        for row in rows
        if row["process_group_id"] is not None
    )
    assert audit_gpu_pool(pool.root)["ok"]
    return {
        "process_attempt_id": process["attempt_id"],
        "pool_key": key,
        "estimate": estimate.to_dict(),
        "admitted_memory_mib": admitted,
        "own_peak_bytes": peak["resident_bytes"],
        "resource_queries": traces,
        "oracle": audit.to_dict(),
    }


def _run_resource_canary(
    root,
    *,
    adapter,
    model,
    uuid,
    runtime,
    fresh_allocation_usd="6",
    node_budget=_NODE,
    progress_callback=None,
):
    """Explicit live entry; real adapter only, and no private substitute provider."""
    assert isinstance(adapter, ClaudeAgentHost)
    adapter, model, prior, allocation = _canary_launch(
        adapter, model, fresh_allocation_usd
    )
    assert prior is None
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    assert not list(root.iterdir()), "live cohort root must be fresh and empty"
    initial = observe_authorized_gpu_memory((uuid,))
    assert len(initial) == 1 and initial[0].uuid == uuid
    assert initial[0].free_memory_mib >= 4096, (
        "wait for sufficient authorized capacity; no whole-device idle requirement"
    )
    mount = Path(tempfile.mkdtemp(prefix="prom-resource-g5-", dir="/var/tmp"))
    profile, payload = _prepare_profile(
        root / "qualification",
        mount,
        model,
        uuid,
        runtime,
        allocation=allocation,
        node_budget=node_budget,
    )
    source_yaml = {path.name: path.read_bytes() for path in profile.glob("*.yaml")}
    attribution = GpuAttributionSampler()
    pool = GpuPool(
        root / "pool",
        members={os.getuid(): (uuid,)},
        reserve_memory_mib=_RESERVE_MIB,
        sampler=lambda: CapacitySample(observe_authorized_gpu_memory((uuid,))),
        attribution_sampler=attribution.pool_residents,
    )
    socket = root / "pool.sock"
    server = PoolServer(pool, socket)
    host = None
    rows, snapshots = [], []
    failure = closure = None
    cleanup_errors = []
    started = time.monotonic()
    try:
        server.start()
        client = PoolClient(socket, expected_server_uid=os.getuid())
        resources = LocalComputeResources(
            allowed_gpu_uuids=(uuid,), gpu_runtime_prefix=Path(runtime)
        )
        host = WorkflowHost(
            root / "state",
            claude_agent_host=adapter,
            claude_model=model,
            compute_resources=resources,
            gpu_pool_socket=socket,
            gpu_pool_namespace=_NAMESPACE,
            gpu_pool_server_uid=os.getuid(),
            literature_io=FixedLiteratureIO(_DeterministicOpenAlexFixture()),
            grounding_web=_NoPublicWeb(),
        )
        host.initialize()
        assert host.version.research_contract_version == "hypoloom-research-v12"
        created = host.handle(
            ControlRequest(1, "create-resource-g5", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created
        last_snapshot = float("-inf")

        def progress(current, run_id):
            nonlocal last_snapshot
            now = time.monotonic()
            if now - last_snapshot >= 1:
                snapshots.append({"at": time.time(), **client.request("snapshot", {})})
                last_snapshot = now
            if progress_callback is not None:
                progress_callback(current, run_id)

        _drive_canary(host, _RUN_ID, _WALL_SECONDS, progress)
        assert source_yaml == {
            path.name: path.read_bytes() for path in profile.glob("*.yaml")
        }
        assert (mount / "target" / "cuda_probe.py").read_text() == _probe_source()
        assert (mount / "data" / "validation.json").read_text() == json.dumps(
            {"values": [1, 2, 3], "checksum": 6}
        )
        closure = _assert_resource_closure(
            host, pool, uuid, model, allocation, node_budget=node_budget
        )
        assert all(
            device["active_commitments"] == 0
            for device in client.request("snapshot", {})["devices"]
        )
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        # Preserve exact failed/OPEN durable rows and their conservative bills.
        # Cleanup never performs a paid retry or manufactures successful closure.
        head = audit = None
        if host is not None:
            rows = host.store.list_attempts()
            head = host.store.latest_transition(_RUN_ID)
            try:
                audit = (
                    Oracle(host.store.database_path, host.publisher.root)
                    .audit(_RUN_ID)
                    .to_dict()
                )
            except Exception as exc:
                cleanup_errors.append("pre-cleanup Oracle: " + str(exc))
        cleanup_errors.extend(_close_live_effects(host, server))
        groups = {
            row["process_group_id"]
            for row in rows
            if row["process_group_id"] is not None
        }
        residue = sorted(group for group in groups if process_group_exists(group))
        charges = fold_agent_attempts(rows)
        reported = sum(
            (
                charge.cost
                for charge in charges
                if charge.source not in {"declared", "rejected"}
                and charge.cost is not None
            ),
            Decimal("0"),
        )
        conservative = charged_cost(charges)
        evidence = {
            "qualification_scope": "current real-model resource compatibility, not scientific common-acceptance G5",
            "run_id": _RUN_ID,
            "model": model,
            "adapter": CLAUDE_AGENT_HOST_VERSION,
            "run_state": None if head is None else head["state"],
            "canary_failure": failure,
            "elapsed_seconds": time.monotonic() - started,
            "fixture_paths": json.loads(
                (profile.parent / "fixture-paths.json").read_text()
            ),
            "limits": {
                "allocation_usd": str(allocation),
                "run_wall_seconds": _WALL_SECONDS,
                "run_turns": _RUN_TURNS,
                "node": node_budget.to_dict(),
                "gpu_timeout_seconds": _GPU_SECONDS,
                "tensor_mib": 64,
                "allocator_limit_mib": 256,
                "allocator_excludes_cuda_context": True,
            },
            "charges": [asdict(charge) for charge in charges],
            "charged_usd": str(conservative),
            "reported_usd": str(reported),
            "unreported_attempt_ids": [
                charge.attempt_id for charge in charges if charge.source == "declared"
            ],
            **_canary_accounting(None, reported),
            "remaining_conservative_usd": str(
                max(Decimal("0"), allocation - conservative)
            ),
            "reported_threshold_exceeded": reported > allocation,
            "accounted_tokens_and_turns": charged_totals(charges),
            "accounting_scope": "existing CLI/Host-reported usage, not an invoice; an in-flight response can overshoot, never funding a further launch",
            "closure": closure,
            "oracle_before_cleanup": audit,
            "attempts": rows,
            "pool_requests_after_cleanup": _pool_rows(pool),
            "pool_audit_after_cleanup": audit_gpu_pool(pool.root),
            "pool_snapshots": snapshots,
            "cleanup_errors": cleanup_errors,
            "owned_process_residue": residue,
        }
        (root / "resource-g5-evidence.json").write_text(
            json.dumps(evidence, indent=2, default=str)
        )
        if failure is None:
            assert not residue and not cleanup_errors
            assert not socket.exists() and (
                server.thread is None or not server.thread.is_alive()
            )


@pytest.mark.skipif(
    not _enabled(os.environ),
    reason="requires explicit paid resource G5/native GPU opt-ins and approved auth/model/UUID/runtime",
)
def test_real_model_shared_gpu_resource_path(tmp_path):
    uuid, runtime = _configured_gpu()
    adapter = ClaudeAgentHost(
        claude_executable=Path(os.environ["HYPOLOOM_CLAUDE_EXECUTABLE"]),
        settings_path=Path(os.environ["HYPOLOOM_CLAUDE_SETTINGS"]),
    )
    _run_resource_canary(
        tmp_path / "trial",
        adapter=adapter,
        model=os.environ["HYPOLOOM_CLAUDE_MODEL"],
        uuid=uuid,
        runtime=runtime,
        fresh_allocation_usd=os.environ["HYPOLOOM_RESOURCE_G5_ALLOCATION_USD"],
    )


def _flags():
    return {name: "1" for name in _REQUIRED_FLAGS}


@pytest.mark.parametrize("missing", tuple(_flags()))
def test_resource_g5_requires_every_explicit_live_opt_in(missing):
    environment = _flags()
    assert _enabled(environment)
    environment.pop(missing)
    assert not _enabled(environment)


@pytest.mark.parametrize(
    "old_flag",
    [
        "HYPOLOOM_REAL_CURRENT_G5",
        "HYPOLOOM_REAL_CURRENT_COMMON_G5",
        "HYPOLOOM_REAL_SHARED_GPU",
    ],
)
def test_resource_g5_old_gpu_or_paid_flags_never_enable_new_canary(old_flag):
    assert not _enabled({old_flag: "1", "HYPOLOOM_REAL_CLAUDE": "1"})


def test_resource_g5_preparation_is_offline_and_preserves_real_runtime_limits(
    tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail(
            "offline preparation attempted to start a provider or inspect a GPU"
        )

    monkeypatch.setattr(ClaudeAgentHost, "preflight", forbidden)
    monkeypatch.setattr(ClaudeAgentHost, "start", forbidden)
    monkeypatch.setattr(PoolServer, "start", forbidden)
    monkeypatch.setattr(LocalComputeResources, "snapshot", forbidden)
    profile, payload = _prepare_profile(
        tmp_path / "qualification",
        tmp_path / "mount",
        "mimo-v2.5-pro",
        _FIXTURE_UUID,
        sys.prefix,
    )
    contract = payload["research_contract"]
    budget = contract["budget"]
    assert payload["execution"]["allow_gpu"] is True
    assert payload["execution"]["runtime_prefix"] == sys.prefix
    assert budget["experiment_timeout"] == _GPU_SECONDS
    assert budget["min_vram_per_experiment"] == _FLOOR_MIB
    assert budget["initial_vram_per_experiment"] == _INITIAL_MIB
    assert budget["gpu_vram_reserve_mb"] == _RESERVE_MIB
    assert budget["max_active_agents"] == budget["max_active_processes"] == 1
    assert budget["max_experiments"] == 1
    assert Decimal(str(budget["total_budget_usd"])) == 6
    assert AgentBudget.from_dict(budget["agent_invocation"]) == _NODE
    assert _NODE.wall_seconds == 300 and _NODE.max_turns == 32
    assert len(contract["roles"]) == 1
    product = contract["methodology"]["controls"]["product"]
    assert product["methodology"]["output_correction"] == {"max_failed_validations": 3}
    assert budget["max_retries"] == 1 and budget["max_infrastructure_retries"] == 0
    assert product["safety"]["gpus"] == [_FIXTURE_UUID]
    assert product["notify"] == {"sinks": {}, "events": {}}
    assert product["goal"].get("evaluation") != "grounding"
    objective = yaml.safe_load((profile / "goal.yaml").read_text())["description"]
    assert "resource-planning" in objective
    assert "after the terminal wait" in objective
    assert len(list(profile.glob("*.yaml"))) == 6
    assert not (tmp_path / "qualification" / "state").exists()


@pytest.mark.parametrize("allocation", ["0", "-1", "NaN", "Infinity", "bad"])
def test_resource_g5_rejects_invalid_fresh_allocation_before_preparation(
    tmp_path, allocation
):
    with pytest.raises(ValueError):
        _prepare_profile(
            tmp_path / "qualification",
            tmp_path / "mount",
            "mimo-v2.5-pro",
            _FIXTURE_UUID,
            sys.prefix,
            allocation=allocation,
        )
    assert not (tmp_path / "qualification").exists()


def test_resource_g5_probe_local_check_never_imports_torch_or_allocates(
    tmp_path, monkeypatch
):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name != "torch", "offline local check imported CUDA runtime"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    namespace = {"__name__": "offline_probe"}
    exec(compile(_probe_source(), "cuda_probe.py", "exec"), namespace)
    assert namespace["local_check"]() == 6
    assert "torch" not in namespace


def _trace_fixture():
    def use(identity, name, arguments):
        return {"type": "tool_use", "id": identity, "name": name, "input": arguments}

    def result(identity, value):
        return {"type": "tool_result", "tool_use_id": identity, "content": value}

    query = {
        "mode": "shared-pool",
        "status": "ok",
        "measurement_scope": "current_run_role_component_family",
        "devices": [{"gpu_uuid": _FIXTURE_UUID}],
        "measurements": [],
    }
    return [
        {
            "message": {
                "content": [
                    use("skill", "Skill", {"skill": "expert:resource-planning"})
                ]
            }
        },
        {
            "message": {
                "content": [result("skill", "Launching skill: resource-planning")]
            }
        },
        {
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "Base directory for this skill: /expert-plugin/skills/resource-planning\n"
                        "Device used/free memory is shared pressure, never this experiment's own use.",
                    }
                ]
            }
        },
        {"message": {"content": [use("query", "mcp__host-tools__get_resources", {})]}},
        {"message": {"content": [result("query", json.dumps(query))]}},
    ]


def _trace_bytes(events):
    return b"\n".join(json.dumps(event).encode() for event in events)


def test_resource_g5_reads_successful_native_skill_and_actual_mcp_result():
    traces = _resource_trace(_trace_bytes(_trace_fixture()), _FIXTURE_UUID, "expert")
    assert traces[0]["measurements"] == []


@pytest.mark.parametrize("schema_version", [26, 27, CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION])
@pytest.mark.parametrize("arguments", [{}, {"gpus": 1}, {"estimate": True}, {"gpus": 1, "estimate": True}])
def test_resource_g5_query_arguments_follow_advertised_version(schema_version, arguments):
    arguments = dict(arguments)
    if arguments.pop("estimate", False):
        arguments["resource_estimate"] = _estimate_arguments()["resource_estimate"]
    events = _trace_fixture()
    events[-2]["message"]["content"][0]["input"] = arguments
    if schema_version < 27 and arguments:
        with pytest.raises(AssertionError):
            _resource_trace(_trace_bytes(events), _FIXTURE_UUID, "expert", schema_version=schema_version)
    else:
        assert _resource_trace(_trace_bytes(events), _FIXTURE_UUID, "expert", schema_version=schema_version)


@pytest.mark.parametrize("arguments", [
    {"gpus": 0}, {"gpus": True}, {"gpus": 2}, {"gpu_uuid": "GPU-foreign"},
    {"runtime": "/foreign"}, {"reserve_memory_mib": 0},
    {"resource_estimate": {}}, {"resource_estimate": None}, [],
])
def test_resource_g5_rejects_invalid_or_unauthorized_query_arguments(arguments):
    events = _trace_fixture()
    events[-2]["message"]["content"][0]["input"] = arguments
    with pytest.raises(AssertionError):
        _resource_trace(_trace_bytes(events), _FIXTURE_UUID, "expert")


@pytest.mark.parametrize(
    "corruption",
    [
        "skill_ack_only",
        "wrong_plugin",
        "query_error",
        "wrong_device",
        "unscoped_history",
        "prompt_mentions_only",
    ],
)
def test_resource_g5_transcript_mentions_are_not_qualification(corruption):
    events = _trace_fixture()
    if corruption == "skill_ack_only":
        events.pop(2)
    elif corruption == "wrong_plugin":
        events[0]["message"]["content"][0]["input"]["skill"] = "other:resource-planning"
    elif corruption == "query_error":
        events[-1]["message"]["content"][0]["is_error"] = True
    elif corruption in {"wrong_device", "unscoped_history"}:
        result = events[-1]["message"]["content"][0]
        query = json.loads(result["content"])
        if corruption == "wrong_device":
            query["devices"][0]["gpu_uuid"] = "GPU-foreign"
        else:
            query["measurement_scope"] = "all_roots"
        result["content"] = json.dumps(query)
    else:
        events = [
            {
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Please invoke Skill expert:resource-planning and mcp__host-tools__get_resources",
                        }
                    ]
                }
            }
        ]
    with pytest.raises(AssertionError):
        _resource_trace(_trace_bytes(events), _FIXTURE_UUID, "expert")


def _estimate_arguments():
    return {
        "candidate": {"resource_preference": "GPU_REQUIRED"},
        "gpus": 1,
        "resource_estimate": {
            "workload": dict(_WORKLOAD),
            "peak_memory_mib": 2048,
            "basis": "64MiB tensor plus uncertain CUDA context and conservative headroom.",
            "measurement_attempt_ids": [],
        },
    }


def test_resource_g5_accepts_typed_estimate_without_inventing_prior_measurements():
    estimate = _assert_submit_estimate(_estimate_arguments())
    assert estimate == ResourceEstimate.from_dict(
        _estimate_arguments()["resource_estimate"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate", {"resource_preference": "CPU"}),
        ("gpus", 2),
        ("resource_estimate", None),
        ("gpu_uuid", _FIXTURE_UUID),
        ("reserve_memory_mib", 0),
    ],
)
def test_resource_g5_rejects_wrong_resource_submission(field, value):
    arguments = copy.deepcopy(_estimate_arguments())
    arguments[field] = value
    with pytest.raises((AssertionError, ValueError)):
        _assert_submit_estimate(arguments)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workload", {"program": "other"}),
        ("peak_memory_mib", True),
        ("measurement_attempt_ids", ["invented-process"]),
        ("basis", ""),
    ],
)
def test_resource_g5_rejects_unproven_estimate_fields(field, value):
    arguments = _estimate_arguments()
    arguments["resource_estimate"][field] = value
    with pytest.raises((AssertionError, ValueError)):
        _assert_submit_estimate(arguments)


def _measurement_fixture():
    return {
        "attempt_id": "actual-process-1",
        "workload": dict(_WORKLOAD),
        "measurement_status": "complete",
        "sample_peak_bytes": 512 * 1024**2,
        "admitted_workload_memory_mib": 2048,
        "oom": False,
    }


def test_resource_g5_post_wait_history_matches_exact_own_closed_source():
    _assert_measured_history(
        [_measurement_fixture()], "actual-process-1", 2048, 512 * 1024**2
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt_id", "another-process"),
        ("workload", {"program": "other"}),
        ("measurement_status", "unknown"),
        ("sample_peak_bytes", None),
        ("sample_peak_bytes", 20_000 * 1024**2),
        ("admitted_workload_memory_mib", 1024),
        ("oom", True),
    ],
)
def test_resource_g5_device_pressure_or_wrong_source_is_not_own_measurement(
    field, value
):
    measured = _measurement_fixture()
    measured[field] = value
    with pytest.raises(AssertionError):
        _assert_measured_history([measured], "actual-process-1", 2048, 512 * 1024**2)


def test_resource_g5_explicit_allocation_is_not_an_old_cumulative_budget(tmp_path):
    node = replace(_NODE, max_cost="1.75")
    _, payload = _prepare_profile(
        tmp_path / "qualification",
        tmp_path / "mount",
        "mimo-v2.5-pro",
        _FIXTURE_UUID,
        sys.prefix,
        allocation="3.75",
        node_budget=node,
    )
    budget = payload["research_contract"]["budget"]
    assert Decimal(str(budget["total_budget_usd"])) == Decimal("3.75")
    assert Decimal(str(budget["per_expert_budget_usd"])) == Decimal("3.75")
    assert budget["agent_invocation"] == node.to_dict()


def test_resource_g5_preparation_cannot_overwrite_existing_root(tmp_path):
    root = tmp_path / "already-existing"
    root.mkdir()
    original = root / "existing-data"
    original.write_text("preserve")
    with pytest.raises(FileExistsError):
        _prepare_profile(
            root, tmp_path / "mount", "mimo-v2.5-pro", _FIXTURE_UUID, sys.prefix
        )
    assert original.read_text() == "preserve"
    assert not (tmp_path / "mount").exists()


@pytest.mark.parametrize("host_close_error", [False, True])
def test_resource_g5_failed_cleanup_never_settles_open_pool_records(
    monkeypatch, host_close_error
):
    # Offline lifecycle spies, not a simulated provider/Host qualification.
    attempts = [{"attempt_id": "unfinished", "state": "OPEN"}]
    reservations = [{"attempt_id": "unfinished", "state": "BOUND"}]
    before = copy.deepcopy((attempts, reservations))
    closed = []

    def forbidden(*args, **kwargs):
        pytest.fail("teardown must not mutate the pool through its API")

    monkeypatch.setattr(PoolClient, "request", forbidden)

    class HostEffects:
        def close(self):
            closed.append("host")
            if host_close_error:
                raise RuntimeError("retain this cleanup failure")

    class Service:
        def close(self):
            closed.append("service")

    errors = _close_live_effects(HostEffects(), Service())
    assert closed == ["host", "service"]
    assert (attempts, reservations) == before
    assert bool(errors) == host_close_error


@pytest.mark.parametrize("change_probe", [False, True])
def test_resource_g5_accepted_patch_is_checked_not_only_original_target(
    tmp_path, change_probe
):
    _, payload = _prepare_profile(
        tmp_path / "qualification",
        tmp_path / "mount",
        "mimo-v2.5-pro",
        _FIXTURE_UUID,
        sys.prefix,
    )
    target = Path(payload["research_contract"]["runtime"]["target_repo"])
    if change_probe:
        original = _probe_source()
        changed = original.replace("torch.ones(64 *", "torch.ones(640 *")
        patch = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                changed.splitlines(keepends=True),
                fromfile="a/cuda_probe.py",
                tofile="b/cuda_probe.py",
            )
        ).encode()
    else:
        patch = b"diff --git a/note.txt b/note.txt\nnew file mode 100644\n--- /dev/null\n+++ b/note.txt\n@@ -0,0 +1 @@\n+Harmless wrapper note\n"
    arguments = {
        "workspace_files": [
            WorkspaceArtifact.from_bytes("implementation.patch", patch).to_dict()
        ]
    }
    if change_probe:
        with pytest.raises(AssertionError, match="accepted patch changed"):
            _assert_accepted_probe_unchanged(arguments, target)
    else:
        _assert_accepted_probe_unchanged(arguments, target)
    assert (target / "cuda_probe.py").read_text() == _probe_source()


def test_resource_g5_profile_requires_cpu_only_wrapper_local_check(tmp_path):
    profile, _ = _prepare_profile(
        tmp_path / "qualification",
        tmp_path / "mount",
        "mimo-v2.5-pro",
        _FIXTURE_UUID,
        sys.prefix,
    )
    objective = yaml.safe_load((profile / "goal.yaml").read_text())["description"]
    assert "python experiment.py --local-check" in objective
    assert "without importing torch or calling run_probe" in objective
    assert "Host's candidate validation compiles" in objective
    command = objective.split("as its own Bash call:\n", 1)[1].splitlines()[0]
    assert _has_wrapper_local_check(
        [{"id": "check", "name": "Bash", "input": {"command": command}}],
        [{"tool_use_id": "check", "content": "local check passed"}],
    )
    assert "remove it in a separate Bash call" in objective


def test_resource_g5_action_card_separates_catalog_refs_from_retrieved_web(tmp_path):
    profile, payload = _prepare_profile(
        tmp_path / "qualification",
        tmp_path / "mount",
        "mimo-v2.5-pro",
        _FIXTURE_UUID,
        sys.prefix,
    )
    objective = yaml.safe_load((profile / "goal.yaml").read_text())["description"]
    assert "one JSON object without prose or Markdown fences" in objective
    assert "Fixture landing/source URLs are catalog metadata, not retrieved web sources" in objective
    assert "web_sources must be omitted or []" in objective
    assert "supporting_refs" in objective and "report the evidence gaps" in objective
    assert "GROUNDING must return acceptance: null" in objective
    assert "declared metric is already the experiment evaluator" in objective
    budget = payload["research_contract"]["budget"]
    assert budget["max_retries"] == 1
    assert AgentBudget.from_dict(budget["agent_invocation"]) == _NODE


def test_resource_g5_native_wrapper_local_check_must_succeed():
    use = {
        "id": "wrapper-check",
        "name": "Bash",
        "input": {"command": "python experiment.py --local-check"},
    }
    result = {"tool_use_id": "wrapper-check", "content": "local check passed"}
    assert _has_wrapper_local_check([use], [result])
    assert not _has_wrapper_local_check([use], [{**result, "is_error": True}])
    assert not _has_wrapper_local_check(
        [{**use, "input": {"command": "python cuda_probe.py --local-check"}}], [result]
    )


@pytest.mark.parametrize(
    "command",
    [
        "python experiment.py --local-check || true",
        "python experiment.py --local-check -B",
        "cd /tmp && HYPOLOOM_TARGET_ROOT=/workspace python /workspace/experiment.py --local-check && cat /tmp/test_result.json || true",
        "python /workspace/experiment.py --local-check ; cat /tmp/test_result.json",
        "python /workspace/experiment.py --local-check | cat /tmp/test_result.json",
        "python /workspace/experiment.py --local-check && echo 'local check passed'",
        "python /workspace/experiment.py --local-check && printf 'local check passed' > /tmp/test_result.json",
        "echo 'local check passed' && python /workspace/experiment.py --local-check",
        "python /tmp/experiment.py --local-check",
        "python /workspace/cuda_probe.py --local-check && cat /tmp/test_result.json",
        "python /workspace/experiment.py --local-check '&&' cat /tmp/test_result.json",
        "cd /tmp && python experiment.py --local-check && cat /tmp/test_result.json",
        "HYPOLOOM_TARGET_ROOT=$(echo /workspace) python experiment.py --local-check",
        "cd /tmp && rm -f /tmp/test_result.json && HYPOLOOM_TARGET_ROOT=/workspace HYPOLOOM_RESULT_PATH=/tmp/test_result.json python3 /workspace/experiment.py --local-check",
        "rm -rf /tmp/hypo_out && mkdir -p /tmp/hypo_out && cd /tmp/hypo_out && HYPOLOOM_TARGET_ROOT=/workspace HYPOLOOM_RESULT_PATH=result.json python3 /workspace/experiment.py --local-check && echo '--- result.json in cwd ---' && cat /tmp/hypo_out/result.json",
    ],
)
def test_resource_g5_wrapper_masked_failure_is_not_a_successful_local_check(command):
    use = {"id": "wrapper-check", "name": "Bash", "input": {"command": command}}
    result = {"tool_use_id": "wrapper-check", "content": "", "is_error": False}
    assert not _has_wrapper_local_check([use], [result])


@pytest.mark.parametrize(
    "command",
    [
        "cd /workspace && PYTHONDONTWRITEBYTECODE=1 python -B -u /workspace/experiment.py --local-check",
        "PYTHONDONTWRITEBYTECODE=1 /runtime/bin/python3.11 -I experiment.py --local-check",
    ],
)
def test_resource_g5_wrapper_check_keeps_supported_cd_environment_and_python_flags(
    command,
):
    use = {"id": "wrapper-check", "name": "Bash", "input": {"command": command}}
    result = {"tool_use_id": "wrapper-check", "content": "passed"}
    assert _has_wrapper_local_check([use], [result])


def test_resource_g5_observed_wrapper_check_may_display_its_result_after_success():
    command = (
        "cd /tmp && HYPOLOOM_TARGET_ROOT=/workspace python /workspace/experiment.py "
        "--local-check && cat /tmp/test_result.json"
    )
    use = {"id": "wrapper-check", "name": "Bash", "input": {"command": command}}
    result = {"tool_use_id": "wrapper-check", "content": '{"checksum": 6}', "is_error": False}
    assert _has_wrapper_local_check([use], [result])
    assert not _has_wrapper_local_check([use], [{**result, "is_error": True}])


def test_resource_g5_wrapper_check_accepts_matching_bounded_temp_preparation_only():
    command = (
        "mkdir -p /tmp/test_exp && cd /tmp/test_exp && "
        "HYPOLOOM_TARGET_ROOT=/workspace HYPOLOOM_RESULT_PATH=result.json "
        "python /workspace/experiment.py --local-check"
    )
    use = {"id": "wrapper-check", "name": "Bash", "input": {"command": command}}
    result = {"tool_use_id": "wrapper-check", "content": "passed", "is_error": False}
    assert _has_wrapper_local_check([use], [result])
    mismatch = command.replace("cd /tmp/test_exp", "cd /tmp/other")
    assert not _has_wrapper_local_check(
        [{**use, "input": {"command": mismatch}}], [result]
    )
    assert not _has_wrapper_local_check([use], [{**result, "is_error": True}])


def test_resource_g5_existing_host_validation_compiles_without_executing_cuda(
    tmp_path, monkeypatch
):
    from hypoloom import candidate_validation

    class PlannedWorkspace:
        def __init__(self, *args, **kwargs):
            pass

        def plan(self, **kwargs):
            return kwargs

    class CPUResources:
        def workspace_kwargs(self, grant, capabilities):
            assert grant.kind.value == "CPU" and capabilities.gpus == ()
            return {}

    monkeypatch.setattr(candidate_validation, "ExecutionWorkspace", PlannedWorkspace)
    prepared, _ = candidate_validation.prepare_candidate_validation(
        tmp_path / "artifacts",
        tmp_path,
        CPUResources(),
        agent_request={},
        agent_response={},
        attempt_id="offline-check",
        attempt_ordinal=1,
        execution_id="offline-execution",
        timeout_seconds=10,
        require_result_reference=True,
    )
    command = prepared["execution_argv"]
    assert command[:2] == ("python", "-c") and "--local-check" not in command
    assert prepared["target_env"]["HYPOLOOM_REQUIRE_RESULT_REFERENCE"] == "1"
    source = b"import torch\nraise AssertionError('CPU validator executed the wrapper')\n# HYPOLOOM_RESULT_PATH\n"
    original_import = builtins.__import__

    def no_cuda(name, *args, **kwargs):
        assert name != "torch"
        return original_import(name, *args, **kwargs)

    opened = []

    def supplied_source(path, mode):
        assert path == "/input/experiment.py" and mode == "rb"
        opened.append(path)
        return io.BytesIO(source)

    monkeypatch.setattr(builtins, "open", supplied_source)
    monkeypatch.setattr(builtins, "__import__", no_cuda)
    monkeypatch.setenv("HYPOLOOM_REQUIRE_RESULT_REFERENCE", "1")
    exec(command[2], {})
    assert opened == ["/input/experiment.py"]
