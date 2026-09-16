from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hypoloom.agent_aci import (
    AgentCommand,
    AgentResponse,
    WorkspaceArtifact,
    with_output_digest,
)
from hypoloom.artifacts import ArtifactKey
from hypoloom.control_protocol import ControlRequest
from hypoloom.compute_resources import LocalComputeResources
from hypoloom.fake_agent import FakeAgent
from hypoloom.oracle import Oracle
from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
from hypoloom.profile_admission import admit_profile
from hypoloom.runner import ProcessRequest
from hypoloom.workflow_host import WorkflowHost


REPOSITORY = Path(__file__).resolve().parents[2]
FAMILY = "fake-family-v1"
IMPLEMENTATION = "must.implementation"
PAIRED = "must.paired_screen"
SEEDED = "primary.seeded_confirmation"
AUDIT = "must.checkpoint_audit"
PRODUCTION_COMPONENTS = (PAIRED, SEEDED, AUDIT)
REAL_GPU_ENABLE = "HYPOLOOM_REAL_GPU"
REAL_GPU_UUID = "HYPOLOOM_REAL_GPU_UUID"
REAL_GPU_RUNTIME = "HYPOLOOM_REAL_GPU_RUNTIME"


@pytest.fixture
def mount_root() -> Path:
    # bwrap owns a private /tmp, so target mounts must originate elsewhere.
    with tempfile.TemporaryDirectory(
        prefix="hypoloom-trial-driver-g3-", dir="/var/tmp"
    ) as value:
        yield Path(value)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _clean_target(root: Path) -> tuple[Path, str, dict[str, bytes]]:
    target = root / "target"
    package = target / "src/merc"
    (package / "causal").mkdir(parents=True)
    (package / "models").mkdir()
    (target / "configs/model").mkdir(parents=True)
    (target / "data").mkdir()
    for relative in (
        "src/merc/__init__.py",
        "src/merc/causal/__init__.py",
        "src/merc/models/__init__.py",
    ):
        (target / relative).write_text("", encoding="utf-8")
    (target / "configs/model/multiemo_fixture.yaml").write_text(
        "model: multiemo\n", encoding="utf-8"
    )
    (target / "configs/model/multiemo_causal_candidate_fixture.yaml").write_text(
        "model: multiemo_causal_candidate\n", encoding="utf-8"
    )
    (target / "data/fixture.txt").write_text(
        "immutable fixture\n", encoding="utf-8"
    )
    (package / "causal/train_fixture.py").write_text(
        '''import argparse
import importlib.util
import json
import os
from pathlib import Path

TARGET_MARKER = "REAL_TARGET_CHILD"
parser = argparse.ArgumentParser()
parser.add_argument("--dataset", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--fixture", required=True)
parser.add_argument("--causal-mode", required=True)
parser.add_argument("--seed", required=True, type=int)
parser.add_argument("--out", required=True)
parser.add_argument("--config", required=True)
parser.add_argument("--device", required=True)
parser.add_argument("--save-ckpt")
args = parser.parse_args()
assert args.dataset == "FIXTURE"
assert args.causal_mode == "train"
assert args.device in {"cpu", "cuda"}
assert Path(args.fixture).read_text(encoding="utf-8") == "immutable fixture\\n"
assert f"model: {args.model}" in Path(args.config).read_text(encoding="utf-8")
patched = args.model == "multiemo_causal_candidate"
if patched:
    from merc.models.multiemo_causal_candidate import METHOD_FAMILY_ID
    assert METHOD_FAMILY_ID == "fake-family-v1"
else:
    assert args.model == "multiemo"
    assert importlib.util.find_spec("merc.models.multiemo_causal_candidate") is None
score = ((80 if patched else 50) + args.seed) / 100
if args.device == "cuda":
    import torch
    assert torch.cuda.is_available()
    assert torch.cuda.device_count() == 1
    assert torch.tensor([args.seed + 1], device="cuda").cpu().item() == args.seed + 1
    cuda_evidence = {
        "available": True,
        "count": torch.cuda.device_count(),
        "name": torch.cuda.get_device_name(0),
        "runtime": torch.version.cuda,
    }
else:
    cuda_evidence = {"available": False, "count": 0}
output = Path(args.out)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({
    "dataset": args.dataset,
    "model": args.model,
    "seed": args.seed,
    "causal_mode": args.causal_mode,
    "test": {"weighted_f1": score},
    "target_marker": TARGET_MARKER,
    "patched_overlay": patched,
    "child_pid": os.getpid(),
    "cuda_evidence": cuda_evidence,
}, sort_keys=True), encoding="utf-8")
if args.save_ckpt:
    checkpoint = Path(args.save_ckpt)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    if args.device == "cuda":
        torch.save({"seed": args.seed}, checkpoint)
    else:
        checkpoint.write_bytes(f"checkpoint-{args.seed}".encode())
''',
        encoding="utf-8",
    )
    (package / "causal/audit_fixture.py").write_text(
        '''import argparse
import json
from pathlib import Path

AUDIT_MARKER = "REAL_AUDIT_CHILD"
parser = argparse.ArgumentParser()
parser.add_argument("--checkpoint", required=True)
parser.add_argument("--dataset", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--fixture", required=True)
parser.add_argument("--causal-mode", required=True)
parser.add_argument("--seed", required=True, type=int)
parser.add_argument("--out", required=True)
parser.add_argument("--device", required=True)
args = parser.parse_args()
assert args.dataset == "FIXTURE"
assert args.model == "multiemo_causal_candidate"
assert args.causal_mode == "audit"
assert args.device in {"cpu", "cuda"}
assert Path(args.fixture).read_text(encoding="utf-8") == "immutable fixture\\n"
if args.device == "cuda":
    import torch
    assert torch.load(
        args.checkpoint, map_location="cpu", weights_only=True
    ) == {"seed": args.seed}
else:
    assert Path(args.checkpoint).read_bytes() == f"checkpoint-{args.seed}".encode()
from merc.models.multiemo_causal_candidate import METHOD_FAMILY_ID
assert METHOD_FAMILY_ID == "fake-family-v1"
value = (args.seed + 1) / 100_000_000
if args.device == "cuda":
    import torch
    assert torch.cuda.is_available()
    assert torch.cuda.device_count() == 1
    assert torch.tensor([args.seed + 1], device="cuda").cpu().item() == args.seed + 1
output = Path(args.out)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({
    "dataset": args.dataset,
    "model": args.model,
    "seed": args.seed,
    "causal_mode": args.causal_mode,
    "max_future_perturbation_logit_diff": value,
    "audit": {
        "future_feature": value,
        "future_speaker": value,
        "future_participant_set": value,
    },
    "target_marker": AUDIT_MARKER,
}, sort_keys=True), encoding="utf-8")
''',
        encoding="utf-8",
    )
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Test",
        "-c",
        "user.email=hypoloom-test@example.invalid",
        "commit",
        "-qm",
        "trial driver production target",
    )
    commit = _git(target, "rev-parse", "HEAD")
    tracked = {
        relative: (target / relative).read_bytes()
        for relative in _git(target, "ls-files").splitlines()
    }
    return target.resolve(), commit, tracked


def _input(
    resource: str, producer: str, artifact: str, count: int = 1
) -> dict[str, object]:
    return {
        "resource": resource,
        "produced_by": producer,
        "artifact": artifact,
        "min_count": count,
    }


def _component(
    component_id: str,
    *,
    tier: str,
    dependencies: list[str],
    required_inputs: list[dict[str, object]],
    protocol: str,
    result: dict[str, object],
    seeds: list[int],
    artifacts: list[str],
) -> dict[str, object]:
    return {
        "id": component_id,
        "tier": tier,
        "name": component_id.split(".", 1)[1],
        "owner": "scientist-a",
        "proposers": ["scientist-a"],
        "dependencies": dependencies,
        "required_inputs": required_inputs,
        "protocol": protocol,
        "result": result,
        "seeds": seeds,
        "required_artifacts": artifacts,
    }


def _production_requirements(
    *, module_path: str, artifacts: list[str], metrics: list[str], shape: str
) -> dict[str, object]:
    flags = [
        "--dataset",
        "--model",
        "--fixture",
        "--causal-mode",
        "--seed",
        "--out",
    ]
    if shape == "checkpoint_audit":
        flags.insert(0, "--checkpoint")
    if shape == "seeded_trials":
        flags.append("--save-ckpt")
    return {
        "required_metrics": metrics,
        "required_artifacts": artifacts,
        "required_paths": [
            {"scope": "target", "path": module_path, "kind": "file"},
            {
                "scope": "target",
                "path": "src/merc/models/multiemo_causal_candidate.py",
                "kind": "file",
            },
            {"scope": "target", "path": "data/fixture.txt", "kind": "file"},
        ],
        "data_checks": [],
        "required_cli_flags": flags,
        "required_source_literals": [
            "REAL_AUDIT_CHILD" if shape == "checkpoint_audit" else "REAL_TARGET_CHILD"
        ],
        "argv_bindings": [
            {
                "flag": "--fixture",
                "role": "fixture_data",
                "scope": "target",
                "path": "data/fixture.txt",
                "kind": "file",
                "absolute": True,
                "overlay_policy": "immutable_data",
            },
            {
                "flag": "--causal-mode",
                "role": "causal_mode",
                "value": "audit" if shape == "checkpoint_audit" else "train",
            },
        ],
        "artifact_validators": [],
    }


def _delivery(
    *,
    shape: str,
    module: str,
    module_path: str,
    normalizer: dict[str, object],
) -> dict[str, object]:
    capabilities: dict[str, object] = {
        "entrypoint": {
            "role": "entrypoint",
            "path": module_path,
            "module": module,
            "required_literals": [
                "REAL_AUDIT_CHILD"
                if shape == "checkpoint_audit"
                else "REAL_TARGET_CHILD"
            ],
        },
        "selection": {
            "role": "registry",
            "selection": {
                "argv_flag": "--model",
                "expected_values": ["multiemo_causal_candidate"],
                **(
                    {"control_value": "multiemo"}
                    if shape == "paired_control_treatment"
                    else {}
                ),
            },
        },
        "output": {"role": "output", "normalizer": normalizer},
    }
    if shape != "checkpoint_audit":
        capabilities["config"] = {
            "role": "config",
            "path_template": "configs/model/{model}_{dataset_lower}.yaml",
            "path_flag": "--config",
        }
    return {
        "production": True,
        "execution_shape": shape,
        "capabilities": capabilities,
        "proof_obligations": [],
    }


def _payload(
    run_id: str,
    target: Path,
    commit: str,
    *,
    allow_gpu: bool = False,
    runtime: Path | None = None,
) -> dict[str, object]:
    admitted = admit_profile(REPOSITORY / "tests/fixtures/profiles/component-demo", run_id=run_id)
    assert admitted.create_run_payload is not None
    payload = copy.deepcopy(admitted.create_run_payload)
    contract = payload["research_contract"]
    implementation_artifacts = [
        "implementation_patch",
        "runner_config",
        "candidate_manifest",
    ]
    paired_artifacts = [
        "per_trial_metrics",
        "resolved_config",
        "baseline_control",
        "provenance",
        "candidate_manifest",
    ]
    seeded_artifacts = [
        "per_trial_metrics",
        "aggregate_results",
        "resolved_config",
        "checkpoints",
        "provenance",
        "candidate_manifest",
    ]
    audit_artifacts = [
        "resolved_config",
        "provenance",
        "candidate_manifest",
        "causal_audit",
    ]
    contract["goal"] = {
        "objective": "Close one production-shaped Host-owned batch chain.",
        "language": "",
        "verdict_rule": "required_conjunction",
        "summary_metric": {
            "name": "required_component_completion",
            "direction": "MAXIMIZE",
            "target": "4",
            "display_only": True,
        },
    }
    contract["components"] = [
        _component(
            IMPLEMENTATION,
            tier="must",
            dependencies=[],
            required_inputs=[],
            protocol="implementation",
            result={"kind": "artifact", "evaluation_kind": "artifact_check"},
            seeds=[],
            artifacts=implementation_artifacts,
        ),
        _component(
            PAIRED,
            tier="must",
            dependencies=[IMPLEMENTATION],
            required_inputs=[
                _input("candidate-manifest", IMPLEMENTATION, "candidate_manifest")
            ],
            protocol="paired",
            result={
                "kind": "metric",
                "metric": "paired_weighted_f1_gain_vs_multiemo",
                "direction": "MAXIMIZE",
                "target": "0.3",
                "dataset": "FIXTURE",
                "target_overlay_policy": "latest",
            },
            seeds=[0, 1, 2],
            artifacts=paired_artifacts,
        ),
        _component(
            SEEDED,
            tier="primary",
            dependencies=[IMPLEMENTATION, PAIRED],
            required_inputs=[
                _input("candidate-manifest", IMPLEMENTATION, "candidate_manifest")
            ],
            protocol="seeded",
            result={
                "kind": "metric",
                "metric": "weighted_f1",
                "direction": "MAXIMIZE",
                "target": "0.8",
                "dataset": "FIXTURE",
                "target_overlay_policy": "latest",
            },
            seeds=[0, 1, 2, 3, 4],
            artifacts=seeded_artifacts,
        ),
        _component(
            AUDIT,
            tier="must",
            dependencies=[IMPLEMENTATION, SEEDED],
            required_inputs=[
                _input("candidate-manifest", IMPLEMENTATION, "candidate_manifest"),
                _input("final-checkpoints", SEEDED, "checkpoints", 5),
            ],
            protocol="audit",
            result={
                "kind": "metric",
                "metric": "max_future_perturbation_logit_diff",
                "direction": "MINIMIZE",
                "target": "0.000001",
                "dataset": "FIXTURE",
                "target_overlay_policy": "latest",
            },
            seeds=[0, 1, 2, 3, 4],
            artifacts=audit_artifacts,
        ),
    ]
    contract["protocols"] = {
        "implementation": {
            "result_kind": "artifact",
            "requirements": {
                "required_artifacts": implementation_artifacts,
                "artifact_validators": [
                    {
                        "artifact": "implementation_patch",
                        "type": "unified_diff",
                        "apply_check": True,
                    },
                    {
                        "artifact": "runner_config",
                        "type": "json_contract",
                        "required_paths": ["method_family_id", "datasets"],
                    },
                    {
                        "artifact": "candidate_manifest",
                        "type": "json_contract",
                        "required_paths": [
                            "method_family_id",
                            "implementation_sha256",
                        ],
                    },
                ],
            },
        },
        "paired": {
            "result_kind": "metric",
            "requirements": _production_requirements(
                module_path="src/merc/causal/train_fixture.py",
                artifacts=paired_artifacts,
                metrics=[
                    "paired_delta",
                    "paired_weighted_f1_gain_vs_multiemo",
                ],
                shape="paired_control_treatment",
            ),
        },
        "seeded": {
            "result_kind": "metric",
            "requirements": _production_requirements(
                module_path="src/merc/causal/train_fixture.py",
                artifacts=seeded_artifacts,
                metrics=["weighted_f1", "n_successful_seeds"],
                shape="seeded_trials",
            ),
        },
        "audit": {
            "result_kind": "metric",
            "requirements": _production_requirements(
                module_path="src/merc/causal/audit_fixture.py",
                artifacts=audit_artifacts,
                metrics=["max_future_perturbation_logit_diff"],
                shape="checkpoint_audit",
            ),
        },
    }
    contract["delivery_contracts"] = {
        "enabled": True,
        "schema_version": 1,
        "protocols": {
            "paired": _delivery(
                shape="paired_control_treatment",
                module="merc.causal.train_fixture",
                module_path="src/merc/causal/train_fixture.py",
                normalizer={
                    "metric_paths": {"test.weighted_f1": "weighted_f1"},
                    "candidate_manifest_resource": "candidate-manifest",
                },
            ),
            "seeded": _delivery(
                shape="seeded_trials",
                module="merc.causal.train_fixture",
                module_path="src/merc/causal/train_fixture.py",
                normalizer={
                    "metric_paths": {"test.weighted_f1": "weighted_f1"},
                    "candidate_manifest_resource": "candidate-manifest",
                    **({"checkpoint_torch_load": True} if allow_gpu else {}),
                },
            ),
            "audit": _delivery(
                shape="checkpoint_audit",
                module="merc.causal.audit_fixture",
                module_path="src/merc/causal/audit_fixture.py",
                normalizer={
                    "metric_paths": {
                        "max_future_perturbation_logit_diff": (
                            "max_future_perturbation_logit_diff"
                        )
                    },
                    "audit_path": "audit",
                    "audit_max": 0.000001,
                    "audit_required_arms": [
                        "future_feature",
                        "future_speaker",
                        "future_participant_set",
                    ],
                    "candidate_manifest_resource": "candidate-manifest",
                    "checkpoint_resource": "final-checkpoints",
                },
            ),
        },
    }
    runtime = (Path(sys.prefix) if runtime is None else runtime).resolve()
    contract["runtime"] = {
        "target_repo": str(target),
        "conda_env": str(runtime),
    }
    contract["budget"].update({"max_experiments": 4, "experiment_timeout": 30})
    contract["safety"] = {"read_roots": [], "sandbox": "bwrap"}
    payload["execution"] = {
        "allow_gpu": allow_gpu,
        "delay_seconds": 0.0,
        "target": {"checkout": str(target), "commit": commit},
        "runtime_prefix": str(runtime),
        "read_roots": [],
    }
    return payload


class _ImplementationThenForbiddenWrappers(FakeAgent):
    def invoke(self, request):
        response = super().invoke(request)
        if (
            request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC
            or request.component_id == IMPLEMENTATION
        ):
            return response
        assert request.component_id in PRODUCTION_COMPONENTS
        assert isinstance(response, AgentResponse)
        forbidden = WorkspaceArtifact.from_bytes(
            "experiment.py",
            b'raise AssertionError("Agent experiment.py executed")\n',
        )
        return with_output_digest(
            replace(
                response,
                proposed_workspace_artifacts=(forbidden,),
                output_digest="",
            )
        )


def _drive(host: WorkflowHost, run_id: str, *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        host.tick()
        head = host.store.latest_transition(run_id)
        if head is not None and head["state"] == "COMPLETED":
            return
        time.sleep(0.005)
    raise AssertionError(f"run did not complete: {host.store.latest_transition(run_id)}")


def _mount_pairs(request: ProcessRequest, option: str) -> set[tuple[str, str]]:
    return {
        (request.argv[index + 1], request.argv[index + 2])
        for index, value in enumerate(request.argv[:-2])
        if value == option
    }


def _publication_root(host: WorkflowHost, outcome: dict[str, object]) -> Path:
    rows = host.store.inspect(
        "SELECT * FROM execution_artifacts WHERE artifact_id=?",
        (outcome["artifact_id"],),
    )
    assert len(rows) == 1
    row = rows[0]
    assert host.publisher is not None
    return host.publisher.path_for(
        ArtifactKey(
            run_id=row["run_id"],
            experiment_id=row["experiment_id"],
            execution_id=row["execution_id"],
            attempt_id=row["attempt_id"],
            artifact_kind=row["artifact_kind"],
        )
    )


def _logical_path(
    host: WorkflowHost, outcome: dict[str, object], logical_name: str
) -> Path:
    logical = next(
        item
        for item in outcome["payload"]["logical_artifacts"]
        if item["logical_artifact"] == logical_name
    )
    assert len(logical["paths"]) == 1
    return _publication_root(host, outcome) / logical["paths"][0]


def test_host_closes_real_trial_driver_batch_chain_through_bwrap(
    tmp_path: Path, mount_root: Path
) -> None:
    run_id = "trial-driver-host-g3"
    target, commit, tracked_before = _clean_target(mount_root)
    state_root = tmp_path / "state"
    host = WorkflowHost(state_root)
    host.agent = _ImplementationThenForbiddenWrappers()
    child_pids: set[int] = set()
    host_process_pids: set[int] = set()
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=_payload(run_id, target, commit),
            )
        )
        assert created.status == "accepted", created
        _drive(host, run_id)

        outcomes = {
            row["payload"]["component_id"]: row
            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        }
        assert list(outcomes) == [IMPLEMENTATION, PAIRED, SEEDED, AUDIT]
        assert {row["payload"]["outcome"] for row in outcomes.values()} == {
            "SUCCESS"
        }
        assert outcomes[PAIRED]["payload"]["metric_value"] == "0.3"
        assert outcomes[SEEDED]["payload"]["metric_value"] == "0.82"
        assert outcomes[AUDIT]["payload"]["metric_value"] == "0.00000005"

        implementation_patch = _logical_path(
            host, outcomes[IMPLEMENTATION], "implementation_patch"
        ).read_bytes()
        implementation_sha = hashlib.sha256(implementation_patch).hexdigest()
        implementation_manifest = _logical_path(
            host, outcomes[IMPLEMENTATION], "candidate_manifest"
        ).read_bytes()
        manifest = json.loads(implementation_manifest)
        assert manifest["method_family_id"] == FAMILY
        assert manifest["implementation_sha256"] == implementation_sha
        core_config_sha = manifest["core_config_sha256"]

        expected_artifacts = {
            PAIRED: {
                "per_trial_metrics",
                "resolved_config",
                "baseline_control",
                "provenance",
                "candidate_manifest",
            },
            SEEDED: {
                "per_trial_metrics",
                "aggregate_results",
                "resolved_config",
                "checkpoints",
                "provenance",
                "candidate_manifest",
            },
            AUDIT: {
                "resolved_config",
                "provenance",
                "candidate_manifest",
                "causal_audit",
            },
        }
        for component_id in PRODUCTION_COMPONENTS:
            root = _publication_root(host, outcomes[component_id])
            result = json.loads((root / "result.json").read_text(encoding="utf-8"))
            assert set(result) == {"artifacts", "identity", "trials"}
            assert set(result["artifacts"]) == expected_artifacts[component_id]
            assert result["identity"] == {
                "method_family_id": FAMILY,
                "implementation_sha256": implementation_sha,
                "core_config_sha256": core_config_sha,
            }
            assert _logical_path(
                host, outcomes[component_id], "candidate_manifest"
            ).read_bytes() == implementation_manifest

        paired = json.loads(
            _logical_path(host, outcomes[PAIRED], "per_trial_metrics").read_text(
                encoding="utf-8"
            )
        )
        assert len(paired["trials"]) == len(paired["raw_outputs"]) == 6
        assert [
            (raw["seed"], raw["model"], raw["patched_overlay"])
            for raw in paired["raw_outputs"]
        ] == [
            (seed, model, patched)
            for seed in range(3)
            for model, patched in (
                ("multiemo", False),
                ("multiemo_causal_candidate", True),
            )
        ]
        assert {raw["target_marker"] for raw in paired["raw_outputs"]} == {
            "REAL_TARGET_CHILD"
        }
        child_pids.update(raw["child_pid"] for raw in paired["raw_outputs"])

        seeded = json.loads(
            _logical_path(host, outcomes[SEEDED], "per_trial_metrics").read_text(
                encoding="utf-8"
            )
        )
        assert len(seeded["trials"]) == len(seeded["raw_outputs"]) == 5
        assert [raw["seed"] for raw in seeded["raw_outputs"]] == list(range(5))
        assert all(raw["patched_overlay"] for raw in seeded["raw_outputs"])
        assert {raw["target_marker"] for raw in seeded["raw_outputs"]} == {
            "REAL_TARGET_CHILD"
        }
        child_pids.update(raw["child_pid"] for raw in seeded["raw_outputs"])
        checkpoint_paths = [
            _publication_root(host, outcomes[SEEDED]) / path
            for item in outcomes[SEEDED]["payload"]["logical_artifacts"]
            if item["logical_artifact"] == "checkpoints"
            for path in item["paths"]
        ]
        assert [path.read_bytes() for path in checkpoint_paths] == [
            f"checkpoint-{seed}".encode() for seed in range(5)
        ]

        audit = json.loads(
            _logical_path(host, outcomes[AUDIT], "causal_audit").read_text(
                encoding="utf-8"
            )
        )
        expected_audits = [
            {
                "future_feature": (seed + 1) / 100_000_000,
                "future_speaker": (seed + 1) / 100_000_000,
                "future_participant_set": (seed + 1) / 100_000_000,
            }
            for seed in range(5)
        ]
        assert audit["trials"] == expected_audits

        rows = host.store.inspect(
            "SELECT t.payload_json FROM workflow_transitions t JOIN workflow_commands c "
            "ON c.command_id=t.command_id WHERE t.run_id=? AND c.command_type='EXECUTE' "
            "AND json_type(t.payload_json,'$.process_request')='object' ORDER BY t.rowid",
            (run_id,),
        )
        process_requests: dict[str, ProcessRequest] = {}
        driver_plans: dict[str, dict[str, object]] = {}
        for row in rows:
            transition = json.loads(row["payload_json"])
            component_id = transition["component_id"]
            request = ProcessRequest.from_dict(transition["process_request"])
            process_requests[component_id] = request
            if component_id in PRODUCTION_COMPONENTS:
                command = request.argv[request.argv.index("--") + 1 :]
                assert command[1] == "/input/.hypoloom/trial_driver.py"
                assert "/input/experiment.py" not in command
                driver_plans[component_id] = json.loads(command[2])
                mounts = _mount_pairs(request, "--ro-bind")
                assert (
                    str(target / "data/fixture.txt"),
                    "/hypoloom-data/fixture_data",
                ) in mounts
        assert set(process_requests) == {
            IMPLEMENTATION,
            PAIRED,
            SEEDED,
            AUDIT,
        }
        assert {
            component_id: (plan["shape"], len(plan["trials"]))
            for component_id, plan in driver_plans.items()
        } == {
            PAIRED: ("paired_control_treatment", 6),
            SEEDED: ("seeded_trials", 5),
            AUDIT: ("checkpoint_audit", 5),
        }
        paired_mounts = _mount_pairs(process_requests[PAIRED], "--ro-bind")
        assert any(destination == "/hypoloom-control-target" for _, destination in paired_mounts)
        assert not any(
            destination == "/hypoloom-control-target"
            for component_id in (SEEDED, AUDIT)
            for _, destination in _mount_pairs(process_requests[component_id], "--ro-bind")
        )
        audit_mounts = _mount_pairs(process_requests[AUDIT], "--ro-bind")
        assert {
            source
            for source, destination in audit_mounts
            if destination.startswith("/hypoloom-inputs/2/")
        } == {str(path) for path in checkpoint_paths}

        specifications = host.store.list_facts(run_id, "ExperimentPreregistered")
        assert {
            row["payload"]["component_id"]: row["payload"]["evaluator_version"]
            for row in specifications
        } == {
            component_id: OUTCOME_ADAPTER_VERSION
            for component_id in (IMPLEMENTATION, PAIRED, SEEDED, AUDIT)
        }
        assert not host.running
        assert not host.running_agents
        assert not host.store.inspect(
            "SELECT 1 FROM execution_attempts WHERE run_id=? AND state='OPEN'",
            (run_id,),
        )
        for row in host.store.inspect(
            "SELECT observation_json FROM execution_attempts WHERE run_id=?",
            (run_id,),
        ):
            observation = json.loads(row["observation_json"])
            if isinstance(observation.get("pid"), int):
                host_process_pids.add(observation["pid"])
    finally:
        host.close()

    assert _git(target, "rev-parse", "HEAD") == commit
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not (target / "src/merc/models/multiemo_causal_candidate.py").exists()
    assert {
        relative: (target / relative).read_bytes() for relative in tracked_before
    } == tracked_before
    # Child-reported PIDs are inside bwrap's PID namespace; only Host-observed
    # process IDs have meaning in the outer /proc namespace.
    assert child_pids and all(pid > 0 for pid in child_pids)
    assert host_process_pids
    assert all(not Path(f"/proc/{pid}").exists() for pid in host_process_pids)
    work_root = state_root / "artifacts/work"
    assert work_root.is_dir() and not any(work_root.iterdir())
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
        run_id
    )
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 4
    assert report.open_attempt_count == 0
    assert report.owned_process_residue == ()


_INCOMPLETE_IMPLEMENTATION_PATCH = (
    b"diff --git a/src/merc/models/unrelated_marker.py "
    b"b/src/merc/models/unrelated_marker.py\n"
    b"new file mode 100644\n"
    b"--- /dev/null\n"
    b"+++ b/src/merc/models/unrelated_marker.py\n"
    b"@@ -0,0 +1,1 @@\n"
    b'+MARKER = "incomplete"\n'
)


class _IncompleteFirstPatchAgent(FakeAgent):
    """Omit the downstream-required candidate module from the first patch."""

    def __init__(self) -> None:
        super().__init__()
        self.implementation_designs = 0

    def invoke(self, request):
        response = super().invoke(request)
        if (
            request.command is not AgentCommand.DRAFT_EXPERIMENT_SPEC
            or request.component_id != IMPLEMENTATION
        ):
            return response
        self.implementation_designs += 1
        if self.implementation_designs != 1:
            return response
        assert isinstance(response, AgentResponse)
        artifacts = {
            item.path: item.content_bytes()
            for item in response.proposed_workspace_artifacts
        }
        source = artifacts["experiment.py"] + (
            b"\nimport hashlib\n"
            b"_result_path = Path(os.environ['HYPOLOOM_RESULT_PATH'])\n"
            b"_result = json.loads(_result_path.read_text(encoding='utf-8'))\n"
            b"_patch = "
            + repr(_INCOMPLETE_IMPLEMENTATION_PATCH).encode("ascii")
            + b"\n"
            b"_ref = _result['artifacts']['implementation_patch']\n"
            b"(_result_path.parent / _ref).write_bytes(_patch)\n"
            b"_result['identity']['implementation_sha256'] = "
            b"hashlib.sha256(_patch).hexdigest()\n"
            b"_result_path.write_text("
            b"json.dumps(_result, sort_keys=True), encoding='utf-8')\n"
        )
        rewritten = tuple(
            WorkspaceArtifact.from_bytes("experiment.py", source)
            if item.path == "experiment.py"
            else item
            for item in response.proposed_workspace_artifacts
        )
        return with_output_digest(
            replace(response, proposed_workspace_artifacts=rewritten)
        )


def test_acceptance_rejects_patch_missing_downstream_module_then_revises(
    tmp_path: Path, mount_root: Path
) -> None:
    run_id = "trial-driver-downstream-contract-repair"
    target, commit, tracked_before = _clean_target(mount_root)
    state_root = tmp_path / "state"
    payload = _payload(run_id, target, commit)
    payload["research_contract"]["budget"]["max_experiments"] = 8
    agent = _IncompleteFirstPatchAgent()
    host = WorkflowHost(state_root)
    host.agent = agent
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=payload,
            )
        )
        assert created.status == "accepted", created
        _drive(host, run_id)

        experiments = host.store.list_facts(run_id, "ExperimentPreregistered")
        outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        assert [row["experiment_id"] for row in experiments[:2]] == [
            f"{run_id}--component-1--experiment",
            f"{run_id}--component-1--experiment-2",
        ]
        assert experiments[1]["payload"]["revision_of_outcome_id"] == (
            outcomes[0]["outcome_id"]
        )
        assert [row["payload"]["outcome"] for row in outcomes] == [
            "INTEGRITY_REJECTED",
            "SUCCESS",
            "SUCCESS",
            "SUCCESS",
            "SUCCESS",
        ]
        rejected = outcomes[0]["payload"]
        assert rejected["component_id"] == IMPLEMENTATION
        assert rejected["validation_code"] == "downstream_target_contract_failed"
        terminal = host.store.latest_transition(f"{run_id}--component-1--experiment")
        assert terminal is not None
        validation_error = json.loads(terminal["payload_json"])["outcome"][
            "validation_error"
        ]
        assert validation_error.startswith(
            "downstream_target_contract_failed@implementation.patch: "
        )
        assert PAIRED in validation_error
        assert "src/merc/models/multiemo_causal_candidate.py" in validation_error
        assert agent.implementation_designs == 2

        by_component = {
            row["payload"]["component_id"]: row
            for row in outcomes
            if row["payload"]["outcome"] == "SUCCESS"
        }
        assert set(by_component) == {IMPLEMENTATION, PAIRED, SEEDED, AUDIT}
        paired = json.loads(
            _logical_path(host, by_component[PAIRED], "per_trial_metrics").read_text(
                encoding="utf-8"
            )
        )
        assert {raw["target_marker"] for raw in paired["raw_outputs"]} == {
            "REAL_TARGET_CHILD"
        }
        assert any(
            raw["patched_overlay"] for raw in paired["raw_outputs"]
        )
        verdicts = host.store.list_facts(run_id, "GoalVerdictRecorded")
        assert len(verdicts) == 1
        assert verdicts[0]["payload"]["verdict"] == "GOAL_MET"
        assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    finally:
        host.close()

    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not (target / "src/merc/models/multiemo_causal_candidate.py").exists()
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
        run_id
    )
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 5
    assert report.open_attempt_count == 0
    assert report.owned_process_residue == ()


def _assert_selected_gpu_idle(gpu_uuid: str) -> None:
    inventory = subprocess.run(
        (
            "nvidia-smi",
            "--query-gpu=uuid,utilization.gpu,memory.used,temperature.gpu",
            "--format=csv,noheader,nounits",
        ),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    matches = [line for line in inventory if line.split(",", 1)[0].strip() == gpu_uuid]
    assert len(matches) == 1, (gpu_uuid, inventory)
    _, utilization, memory_used, temperature = [
        value.strip() for value in matches[0].split(",")
    ]
    applications = subprocess.run(
        (
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid",
            "--format=csv,noheader,nounits",
        ),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    selected = [
        line for line in applications if line.split(",", 1)[0].strip() == gpu_uuid
    ]
    assert int(utilization) <= 10
    assert int(memory_used) <= 512
    assert int(temperature) <= 60
    assert selected == []


@pytest.mark.skipif(
    os.environ.get(REAL_GPU_ENABLE) != "1",
    reason=f"set {REAL_GPU_ENABLE}=1 with exact UUID/runtime",
)
def test_host_closes_batch_chain_on_one_real_gpu(
    tmp_path: Path, mount_root: Path
) -> None:
    gpu_uuid = os.environ.get(REAL_GPU_UUID)
    runtime_raw = os.environ.get(REAL_GPU_RUNTIME)
    assert gpu_uuid and gpu_uuid == gpu_uuid.strip()
    assert runtime_raw and runtime_raw == runtime_raw.strip()
    runtime = Path(runtime_raw)
    assert runtime.is_absolute() and not runtime.is_symlink()
    runtime = runtime.resolve()
    assert (runtime / "bin/python").is_file()
    _assert_selected_gpu_idle(gpu_uuid)

    run_id = "trial-driver-host-real-gpu"
    target, commit, tracked_before = _clean_target(mount_root)
    state_root = tmp_path / "state"
    resources = LocalComputeResources(
        allowed_gpu_uuids=(gpu_uuid,),
        gpu_runtime_prefix=runtime,
    )
    host = WorkflowHost(state_root, compute_resources=resources)
    host.agent = _ImplementationThenForbiddenWrappers()
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(
                schema_version=1,
                request_id=f"create-{run_id}",
                expected_revision=0,
                command="CREATE_RUN",
                payload=_payload(
                    run_id,
                    target,
                    commit,
                    allow_gpu=True,
                    runtime=runtime,
                ),
            )
        )
        assert created.status == "accepted", created
        _drive(host, run_id, timeout=180)
        outcomes = {
            row["payload"]["component_id"]: row
            for row in host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
        }
        assert list(outcomes) == [IMPLEMENTATION, PAIRED, SEEDED, AUDIT]
        assert {row["payload"]["outcome"] for row in outcomes.values()} == {
            "SUCCESS"
        }
        specs = host.store.list_facts(run_id, "ExperimentPreregistered")
        assert len(specs) == 4
        assert all(
            row["payload"]["execution_contract"]["resource_grant"]
            == {"kind": "GPU", "gpu_uuid": gpu_uuid}
            for row in specs
        )

        transitions = host.store.inspect(
            "SELECT t.payload_json FROM workflow_transitions t JOIN workflow_commands c "
            "ON c.command_id=t.command_id WHERE t.run_id=? AND c.command_type='EXECUTE' "
            "AND json_type(t.payload_json,'$.process_request')='object' ORDER BY t.rowid",
            (run_id,),
        )
        production_plans: dict[str, dict[str, object]] = {}
        for row in transitions:
            payload = json.loads(row["payload_json"])
            request = ProcessRequest.from_dict(payload["process_request"])
            assert gpu_uuid in request.argv
            assert "--dev-bind" in request.argv
            component_id = payload["component_id"]
            if component_id in PRODUCTION_COMPONENTS:
                command = request.argv[request.argv.index("--") + 1 :]
                plan = json.loads(command[2])
                production_plans[component_id] = plan
                for trial in plan["trials"]:
                    argv = trial["target_argv"]
                    assert argv[argv.index("--device") + 1] == "cuda"
        assert {
            component_id: len(plan["trials"])
            for component_id, plan in production_plans.items()
        } == {PAIRED: 6, SEEDED: 5, AUDIT: 5}
        assert production_plans[SEEDED]["checkpoint_torch_load"] is True

        observed_cuda_children = 0
        for component_id in (PAIRED, SEEDED):
            normalized = json.loads(
                _logical_path(
                    host, outcomes[component_id], "per_trial_metrics"
                ).read_text(encoding="utf-8")
            )
            for raw in normalized["raw_outputs"]:
                evidence = raw["cuda_evidence"]
                assert evidence["available"] is True
                assert evidence["count"] == 1
                assert evidence["runtime"] == "12.8"
                assert "RTX 3090" in evidence["name"]
                observed_cuda_children += 1
        assert observed_cuda_children == 11
        assert not host.running and not host.running_agents
    finally:
        host.close()

    assert _git(target, "rev-parse", "HEAD") == commit
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert {
        relative: (target / relative).read_bytes() for relative in tracked_before
    } == tracked_before
    report = Oracle(state_root / "state.sqlite", state_root / "artifacts").audit(
        run_id
    )
    assert report.ok, report.issues
    assert report.canonical_outcome_count == 4
    assert report.open_attempt_count == 0
    assert report.owned_process_residue == ()
    _assert_selected_gpu_idle(gpu_uuid)
