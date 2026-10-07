from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hypoloom.outcome_adapter import validate_component_result


REPOSITORY = Path(__file__).resolve().parents[2]
IMPLEMENTATION = "a" * 64
CORE_CONFIG = "c" * 64
FAMILY = "frozen-family-v1"
ARMS = ("control", "treatment")
AUDIT_ARMS = ("future_feature", "future_speaker", "future_participant_set")


def _target(root: Path) -> Path:
    source = root / "target/src"
    package = source / "fake_target"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "runner.py").write_text(
        """import argparse, json, os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--dataset", required=True)
parser.add_argument("--model")
parser.add_argument("--seed", required=True, type=int)
parser.add_argument("--causal-mode", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--checkpoint")
parser.add_argument("--value", required=True, type=float)
parser.add_argument("--fail", action="store_true")
parser.add_argument("--reported-seed", type=int)
parser.add_argument("--audit-high", action="store_true")
parser.add_argument("--audit-missing")
parser.add_argument("--valid-checkpoint", action="store_true")
args = parser.parse_args()
trace = Path(os.environ["TRIAL_TRACE"])
with trace.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"seed": args.seed, "model": args.model, "checkpoint": args.checkpoint}) + "\\n")
if args.fail:
    raise SystemExit(9)
audit = {
    "future_feature": 0.001,
    "future_speaker": 0.5 if args.audit_high else 0.002,
    "future_participant_set": 0.003,
}
if args.audit_missing:
    audit.pop(args.audit_missing)
raw = {
    "dataset": args.dataset,
    "model": args.model,
    "seed": args.reported_seed if args.reported_seed is not None else args.seed,
    "causal_mode": args.causal_mode,
    "test": {
        "weighted_f1": args.value,
        "per_class": {"neutral": args.value, "joy": args.value + 0.01},
    },
    "audit": audit,
}
out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
if args.checkpoint:
    checkpoint = Path(args.checkpoint)
    if not checkpoint.exists():
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(
            b"TORCH-STUB\\0" + str(args.seed).encode()
            if args.valid_checkpoint
            else f"checkpoint-{args.seed}".encode()
        )
""",
        encoding="utf-8",
    )
    return source.resolve()


def _manifest(root: Path, *, implementation: str = IMPLEMENTATION) -> Path:
    path = root / "inputs/candidate-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            "{\n"
            f'  "core_config_sha256": "{CORE_CONFIG}",\n'
            f'  "implementation_sha256": "{implementation}",\n'
            f'  "method_family_id": "{FAMILY}",\n'
            '  "schema_version": 1\n'
            "}\n"
        ).encode()
    )
    return path.resolve()


def _trial(
    pythonpath: Path,
    *,
    seed: int,
    value: float,
    arm: str | None = None,
    checkpoint: str | None = None,
    extra: list[str] | None = None,
) -> dict[str, Any]:
    model = None if arm is None else ("baseline" if arm == "control" else "candidate-v1")
    argv = [
        "--dataset", "MELD", "--seed", str(seed), "--causal-mode", "train",
        "--out", "{raw_result}", "--value", str(value),
    ]
    if model is not None:
        argv.extend(("--model", model))
    if checkpoint is not None:
        argv.extend(("--checkpoint", "{checkpoint}"))
    argv.extend(extra or [])
    result: dict[str, Any] = {
        "module": "fake_target.runner",
        "pythonpath": str(pythonpath),
        "seed": seed,
        "target_argv": argv,
        "raw_result": f"raw/{arm + '-' if arm else ''}{seed}.json",
    }
    if arm is not None:
        result.update(arm=arm, model=model)
    if checkpoint is not None:
        result["checkpoint"] = checkpoint
    return result


def _plan(shape: str, trials: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "shape": shape,
        "identity": {
            "method_family_id": FAMILY,
            "implementation_sha256": IMPLEMENTATION,
        },
        "dataset": "MELD",
        "metric_paths": {"test.weighted_f1": "weighted_f1"},
        "candidate_manifest_resource": "candidate-manifest",
        "data_checks": [],
        "trials": trials,
    }


def _paired_plan(pythonpath: Path) -> dict[str, Any]:
    return _plan(
        "paired_control_treatment",
        [
            _trial(
                pythonpath,
                seed=seed,
                arm=arm,
                value=(0.50 + seed / 100 if arm == "control" else 0.60 + seed / 100),
            )
            for arm in ARMS
            for seed in range(3)
        ],
    )


def _seeded_plan(pythonpath: Path) -> dict[str, Any]:
    plan = _plan(
        "seeded_trials",
        [
            _trial(
                pythonpath,
                seed=seed,
                value=0.60 + seed / 100,
                checkpoint=f"raw-checkpoints/seed-{seed}.pt",
            )
            for seed in range(5)
        ],
    )
    plan.update(
        per_class_path="test.per_class",
        class_labels=["neutral", "joy"],
    )
    return plan


def _audit_plan(pythonpath: Path, count: int, *, extra: list[str] | None = None) -> dict[str, Any]:
    plan = _plan(
        "checkpoint_audit",
        [
            _trial(
                pythonpath,
                seed=seed,
                value=0.5,
                checkpoint=None,
                extra=["--checkpoint", "{checkpoint}", *(extra or [])],
            )
            for seed in range(count)
        ],
    )
    plan.update(
        audit_path="audit",
        audit_max=0.01,
        audit_required_arms=list(AUDIT_ARMS),
        checkpoint_resource="accepted-checkpoints",
    )
    return plan


def _run(
    output: Path,
    plan: dict[str, Any],
    manifest: Path,
    trace: Path,
    *,
    checkpoints: list[Path] | None = None,
    extra_pythonpath: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    output.mkdir()
    resources = {"candidate-manifest": [str(manifest)]}
    if checkpoints is not None:
        resources["accepted-checkpoints"] = [str(path) for path in checkpoints]
    environment = os.environ.copy()
    pythonpath = [str(REPOSITORY)]
    if extra_pythonpath is not None:
        pythonpath.append(str(extra_pythonpath))
    environment.update(
        PYTHONPATH=os.pathsep.join(pythonpath),
        HYPOLOOM_REQUIRED_INPUTS=json.dumps(resources, sort_keys=True, separators=(",", ":")),
        TRIAL_TRACE=str(trace),
    )
    raw_plan = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return subprocess.run(
        [sys.executable, "-m", "hypoloom.trial_driver", raw_plan],
        cwd=output,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("complete, expected", [(False, None), (True, False)])
def test_protocol_owned_incomplete_coverage_is_inconclusive_not_refutation(
    tmp_path: Path, complete: bool, expected: bool | None,
) -> None:
    output = tmp_path / "inconclusive-output"
    output.mkdir()
    (output / "evaluation_summary.json").write_text(
        json.dumps({"coverage": {"full_matrix_complete": complete}}), encoding="utf-8"
    )
    (output / "resolved_config.json").write_text("{}", encoding="utf-8")
    patch_bytes = b"patch"
    (output / "implementation.patch").write_bytes(patch_bytes)
    (output / "result.json").write_text(json.dumps({
        "artifacts": {
            "evaluation_summary": "evaluation_summary.json",
            "resolved_config": "resolved_config.json",
            "implementation_patch": "implementation.patch",
        },
        "identity": {"implementation_sha256": hashlib.sha256(patch_bytes).hexdigest()},
        "trials": [{"seed": 0, "clean": True, "metrics": {"score": -100}}],
    }), encoding="utf-8")
    component = {
        "id": "primary.score", "result": {"kind": "metric", "metric": "score",
        "direction": "MAXIMIZE", "target": "0"}, "seeds": [],
        "required_artifacts": ["implementation_patch", "evaluation_summary", "resolved_config"],
    }
    protocol = {"result_kind": "metric", "requirements": {
        "required_metrics": ["score"], "required_artifacts": component["required_artifacts"],
        "inconclusive_if": {"artifact": "evaluation_summary", "path": "coverage.full_matrix_complete", "equals": False},
        "artifact_validators": [],
    }}
    validated = validate_component_result(component, protocol, output)
    assert validated["criterion_met"] is expected
    # Raw validation measurements remain available; the Host owns omission of
    # comparable metric/artifact claims from an accepted INCONCLUSIVE outcome.
    assert validated["metric_value"] == "-100"
    assert set(validated["logical_artifacts"]) == set(component["required_artifacts"])


def test_paired_two_by_three_runs_real_modules_and_normalizes_exact_inventory(
    tmp_path: Path,
) -> None:
    pythonpath = _target(tmp_path)
    manifest = _manifest(tmp_path)
    trace = tmp_path / "paired.trace"
    output = tmp_path / "paired-output"

    completed = _run(output, _paired_plan(pythonpath), manifest, trace)

    assert completed.returncode == 0, completed.stderr
    assert [(item["model"], item["seed"]) for item in _trace(trace)] == [
        ("baseline", 0), ("baseline", 1), ("baseline", 2),
        ("candidate-v1", 0), ("candidate-v1", 1), ("candidate-v1", 2),
    ]
    assert {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()} == {
        "result.json",
        "artifacts/per_trial_metrics.json",
        "artifacts/resolved_config.json",
        "artifacts/provenance.json",
        "artifacts/candidate_manifest.json",
        "artifacts/baseline_control.json",
    }
    assert (output / "artifacts/candidate_manifest.json").read_bytes() == manifest.read_bytes()
    component = {
        "id": "primary.score", "result": {"kind": "metric", "metric": "paired_weighted_f1_gain_vs_control",
        "direction": "MAXIMIZE", "target": "0.09"}, "seeds": [0, 1, 2],
        "required_artifacts": ["per_trial_metrics", "resolved_config", "baseline_control", "provenance", "candidate_manifest"],
    }
    protocol = {"result_kind": "metric", "requirements": {
        "required_metrics": ["paired_delta", "paired_weighted_f1_gain_vs_control"],
        "required_artifacts": component["required_artifacts"], "artifact_validators": [],
    }}
    validated = validate_component_result(component, protocol, output)
    assert validated["metric_value"] == "0.1"
    assert validated["criterion_met"] is True


def test_seeded_five_runs_emit_aggregate_per_class_and_real_checkpoints(
    tmp_path: Path,
) -> None:
    pythonpath = _target(tmp_path)
    manifest = _manifest(tmp_path)
    trace = tmp_path / "seeded.trace"
    output = tmp_path / "seeded-output"

    completed = _run(output, _seeded_plan(pythonpath), manifest, trace)

    assert completed.returncode == 0, completed.stderr
    assert [item["seed"] for item in _trace(trace)] == list(range(5))
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    checkpoints = result["artifacts"]["checkpoints"]
    assert len(checkpoints) == 5
    assert all((output / path).is_file() and (output / path).stat().st_size for path in checkpoints)
    per_class = json.loads((output / result["artifacts"]["per_class_metrics"]).read_text(encoding="utf-8"))
    assert set(per_class["datasets"]["MELD"]["per_class_f1"]) == {"neutral", "joy"}
    component = {
        "id": "primary.score", "result": {"kind": "metric", "metric": "weighted_f1",
        "direction": "MAXIMIZE", "target": "0.62"}, "seeds": list(range(5)),
        "required_artifacts": ["per_trial_metrics", "per_class_metrics", "aggregate_results", "resolved_config",
                               "checkpoints", "provenance", "candidate_manifest"],
    }
    protocol = {"result_kind": "metric", "requirements": {
        "required_metrics": ["weighted_f1", "n_successful_seeds"],
        "required_artifacts": component["required_artifacts"], "artifact_validators": [],
    }}
    validated = validate_component_result(component, protocol, output)
    assert validated["metric_value"] == "0.62"
    assert validated["criterion_met"] is True


@pytest.mark.parametrize("valid", [True, False])
def test_torch_checkpoint_validation_precedes_publication(
    tmp_path: Path, valid: bool,
) -> None:
    pythonpath = _target(tmp_path)
    manifest = _manifest(tmp_path)
    trace = tmp_path / "checkpoint.trace"
    output = tmp_path / "checkpoint-output"
    torch_stub = tmp_path / "torch-stub"
    torch_stub.mkdir()
    (torch_stub / "torch.py").write_text(
        """from pathlib import Path

def load(path, map_location=None, weights_only=None):
    raw = Path(path).read_bytes()
    if not raw.startswith(b"TORCH-STUB\\0"):
        raise ValueError("invalid torch checkpoint")
    return {"seed": int(raw.removeprefix(b"TORCH-STUB\\0"))}
""",
        encoding="utf-8",
    )
    plan = _seeded_plan(pythonpath)
    plan["checkpoint_torch_load"] = True
    if valid:
        for trial in plan["trials"]:
            trial["target_argv"].append("--valid-checkpoint")

    completed = _run(
        output,
        plan,
        manifest,
        trace,
        extra_pythonpath=torch_stub,
    )

    assert (completed.returncode == 0) is valid, completed.stderr
    assert (output / "result.json").exists() is valid
    if not valid:
        assert list(output.iterdir()) == []


def test_checkpoint_audit_expands_required_input_resource_and_passes_strict_arms(
    tmp_path: Path,
) -> None:
    pythonpath = _target(tmp_path)
    manifest = _manifest(tmp_path)
    checkpoints = []
    for seed in range(2):
        path = tmp_path / f"inputs/seed-{seed}.pt"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f"accepted-{seed}".encode())
        checkpoints.append(path.resolve())
    trace = tmp_path / "audit.trace"
    output = tmp_path / "audit-output"

    completed = _run(output, _audit_plan(pythonpath, 2), manifest, trace, checkpoints=checkpoints)

    assert completed.returncode == 0, completed.stderr
    assert [item["checkpoint"] for item in _trace(trace)] == [str(path) for path in checkpoints]
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    audit = json.loads((output / result["artifacts"]["causal_audit"]).read_text(encoding="utf-8"))
    assert audit["max_future_perturbation_logit_diff"] == 0.003
    assert audit["checks"] == {arm: True for arm in AUDIT_ARMS}
    assert "per_trial_metrics" not in result["artifacts"]


@pytest.mark.parametrize("failure", ["child", "raw", "audit", "manifest", "data"])
def test_batch_failures_leave_no_partial_result_or_output(
    tmp_path: Path, failure: str,
) -> None:
    case = tmp_path / failure
    case.mkdir()
    pythonpath = _target(case)
    manifest = _manifest(case, implementation="b" * 64 if failure == "manifest" else IMPLEMENTATION)
    trace = case / "failure.trace"
    checkpoints: list[Path] | None = None
    if failure == "audit":
        checkpoint = case / "inputs/checkpoint.pt"
        checkpoint.parent.mkdir(exist_ok=True)
        checkpoint.write_bytes(b"accepted")
        checkpoints = [checkpoint.resolve()]
        plan = _audit_plan(pythonpath, 1, extra=["--audit-missing", "future_speaker"])
    else:
        plan = _paired_plan(pythonpath)
        if failure == "child":
            plan["trials"][1]["target_argv"].append("--fail")
        elif failure == "raw":
            plan["trials"][1]["target_argv"].extend(("--reported-seed", "99"))
        elif failure == "data":
            plan["data_checks"] = [{
                "type": "hdf5_split_manifest", "dataset": "MELD",
                "cache": str((case / "inputs/missing.h5").resolve()),
                "manifest": str((case / "inputs/missing.json").resolve()),
            }]
    output = case / "output"

    completed = _run(output, plan, manifest, trace, checkpoints=checkpoints)

    assert completed.returncode != 0
    assert not (output / "result.json").exists()
    assert list(output.iterdir()) == []
