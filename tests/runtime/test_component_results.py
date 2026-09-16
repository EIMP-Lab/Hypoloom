from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from hypoloom.oracle import _validate_component_artifact_bytes
from hypoloom.outcome_adapter import (
    ComponentResultValidationError,
    OUTCOME_ADAPTER_VERSION,
    repairable_component_result_error,
    validate_component_result,
)
from hypoloom.candidate_validation import component_result_contract


def _metric_component(
    *,
    metric: str = "score",
    direction: str = "maximize",
    target: float = 1.0,
    seeds: list[int] | None = None,
    artifacts: list[str] | dict[str, object] | None = None,
) -> dict[str, Any]:
    return {
        "id": "primary.score",
        "tier": "primary",
        "name": "score",
        "result": {
            "kind": "metric",
            "metric": metric,
            "direction": direction,
            "target": target,
        },
        "seeds": [] if seeds is None else seeds,
        "required_artifacts": [] if artifacts is None else artifacts,
    }


def _artifact_component(
    artifacts: list[str] | dict[str, object],
) -> dict[str, Any]:
    return {
        "id": "must.report",
        "tier": "must",
        "name": "report",
        "result": {"kind": "artifact", "evaluation_kind": "artifact_check"},
        "seeds": [],
        "required_artifacts": artifacts,
    }


def _protocol(
    kind: str,
    *,
    metrics: list[str] | None = None,
    artifacts: list[str] | dict[str, object] | None = None,
    validators: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "result_kind": kind,
        "requirements": {
            "required_metrics": [] if metrics is None else metrics,
            "required_artifacts": [] if artifacts is None else artifacts,
            "artifact_validators": [] if validators is None else validators,
        },
    }


def _write_result(root: Path, result: dict[str, Any], files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    (root / "result.json").write_text(
        json.dumps(result, sort_keys=True), encoding="utf-8"
    )


def _single_trial(value: object = 1.0) -> list[dict[str, object]]:
    return [{"seed": "only", "metrics": {"score": value}, "clean": True}]


def _assert_code(error: pytest.ExceptionInfo[ComponentResultValidationError], code: str) -> None:
    assert error.value.code == code, str(error.value)


def test_paired_n3_recomputes_mean_delta_and_checks_declared_identity(
    tmp_path: Path,
) -> None:
    implementation = b"shared implementation bytes\n"
    digest = hashlib.sha256(implementation).hexdigest()
    trials: list[dict[str, object]] = []
    for seed, control, treatment in (
        (0, 0.50, 0.60),
        (1, 0.40, 0.60),
        (2, 0.30, 0.60),
    ):
        trials.extend(
            [
                {
                    "seed": seed,
                    "arm": "control",
                    "metrics": {"weighted_f1": control},
                    "clean": True,
                },
                {
                    "seed": seed,
                    "arm": "treatment",
                    "metrics": {"weighted_f1": treatment},
                    "clean": True,
                },
            ]
        )
    _write_result(
        tmp_path,
        {
            "trials": trials,
            "artifacts": {"per_trial_metrics": "trials.json"},
            "identity": {
                "method_family_id": "cpeta_multiemo",
                "implementation_sha256": digest,
            },
        },
        {"trials.json": "{}\n"},
    )
    component = _metric_component(
        metric="paired_weighted_f1_gain_vs_multiemo",
        target=0.19,
        seeds=[0, 1, 2],
        artifacts=["per_trial_metrics"],
    )
    protocol = _protocol(
        "metric",
        metrics=["paired_delta", "paired_weighted_f1_gain_vs_multiemo"],
        artifacts=["per_trial_metrics"],
    )

    result = validate_component_result(
        component,
        protocol,
        tmp_path,
        implementation_bytes=implementation,
        implementation_digest_check=lambda supplied: supplied == digest,
    )

    assert result == {
        "component_id": "primary.score",
        "result_kind": "metric",
        "metric_name": "paired_weighted_f1_gain_vs_multiemo",
        "metric_value": "0.2",
        "criterion_met": True,
        "logical_artifacts": {"per_trial_metrics": ("trials.json",)},
        "method_family_id": "cpeta_multiemo",
        "implementation_sha256": digest,
        "core_config_sha256": None,
    }


def test_n5_requires_exact_seeds_recomputes_mean_and_checkpoint_count(
    tmp_path: Path,
) -> None:
    checkpoint_paths = [f"checkpoints/seed-{seed}.pt" for seed in range(5)]
    files = {path: f"checkpoint {index}\n" for index, path in enumerate(checkpoint_paths)}
    files["metrics.json"] = "{}\n"
    _write_result(
        tmp_path,
        {
            "trials": [
                {
                    "seed": seed,
                    "metrics": {
                        "score": seed + 1,
                        "n_successful_seeds": 5,
                    },
                    "clean": True,
                }
                for seed in range(5)
            ],
            "artifacts": {
                "per_trial_metrics": "metrics.json",
                "checkpoints": checkpoint_paths,
            },
        },
        files,
    )
    component = _metric_component(
        target=3,
        seeds=[0, 1, 2, 3, 4],
        artifacts=["per_trial_metrics", "checkpoints"],
    )

    result = validate_component_result(
        component,
        _protocol(
            "metric",
            metrics=["score", "n_successful_seeds"],
            artifacts=["per_trial_metrics", "checkpoints"],
        ),
        tmp_path,
    )

    assert result["metric_value"] == "3"
    assert result["criterion_met"] is True
    assert result["logical_artifacts"]["checkpoints"] == tuple(checkpoint_paths)


def test_causal_metric_uses_maximum_not_supplied_aggregate(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        {
            "trials": [
                {
                    "seed": name,
                    "metrics": {"max_future_perturbation_logit_diff": value},
                    "clean": True,
                }
                for name, value in (("feature", 0.001), ("speaker", 0.009), ("set", 0.004))
            ],
            "artifacts": {"causal_audit": "causal.json"},
        },
        {"causal.json": '{"dataset":"MELD"}\n'},
    )
    component = _metric_component(
        metric="max_future_perturbation_logit_diff",
        direction="minimize",
        target=0.01,
        artifacts=["causal_audit"],
    )

    result = validate_component_result(
        component,
        _protocol(
            "metric",
            metrics=["max_future_perturbation_logit_diff"],
            artifacts=["causal_audit"],
        ),
        tmp_path,
    )

    assert result["metric_value"] == "0.009"
    assert result["criterion_met"] is True


def test_artifact_criterion_validates_json_text_and_injected_unified_diff(
    tmp_path: Path,
) -> None:
    patch = "--- a/model.py\n+++ b/model.py\n@@ -1 +1 @@\n-old\n+new\n"
    implementation_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    design = "MELD and IEMOCAP use one shared mechanism with an adapter."
    manifest = {
        "method_family_id": "shared",
        "candidates": {
            "MELD": {"method_family_id": "shared"},
            "IEMOCAP": {"method_family_id": "shared"},
        },
        "datasets": {"MELD": 1, "IEMOCAP": 2},
        "status": "validated",
    }
    _write_result(
        tmp_path,
        {
            "artifacts": {
                "candidate_manifest": "manifest.json",
                "dual_dataset_design": "design.md",
                "implementation_patch": "implementation.patch",
            },
            "identity": {
                "method_family_id": "shared",
                "implementation_sha256": implementation_digest,
                "core_config_sha256": "b" * 64,
            },
        },
        {
            "manifest.json": json.dumps(manifest),
            "design.md": design,
            "implementation.patch": patch,
        },
    )
    artifacts = ["candidate_manifest", "dual_dataset_design", "implementation_patch"]
    seen: list[bytes] = []

    def apply_check(payload: bytes) -> bool:
        seen.append(payload)
        return True

    result = validate_component_result(
        _artifact_component(artifacts),
        _protocol(
            "artifact",
            artifacts=artifacts,
            validators=[
                {
                    "type": "json_contract",
                    "artifact": "candidate_manifest",
                    "required_paths": ["method_family_id", "candidates.MELD.method_family_id"],
                    "equal_groups": [
                        [
                            "method_family_id",
                            "candidates.MELD.method_family_id",
                            "candidates.IEMOCAP.method_family_id",
                        ]
                    ],
                    "exact_key_sets": {"datasets": ["MELD", "IEMOCAP"]},
                    "allowed_values": {"status": ["validated"]},
                },
                {
                    "type": "text_contract",
                    "artifact": "dual_dataset_design",
                    "min_chars": len(design),
                    "required_terms": ["MELD", "IEMOCAP", "shared mechanism", "adapter"],
                    "forbidden_terms": ["dataset-name-selected mechanism"],
                },
                {
                    "type": "unified_diff",
                    "artifact": "implementation_patch",
                    "apply_check": True,
                },
            ],
        ),
        tmp_path,
        git_apply_check=apply_check,
    )

    assert result["criterion_met"] is True
    assert result["metric_value"] is None
    assert result["core_config_sha256"] == "b" * 64
    assert seen == [patch.encode()]


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"metric_value": 999}, "invalid_result_shape"),
        ({"aggregate": {"score": 999}}, "invalid_result_shape"),
        ({"criterion_met": True}, "invalid_result_shape"),
    ],
)
def test_result_json_rejects_supplied_aggregate_or_unknown_fields(
    tmp_path: Path, extra: dict[str, object], code: str
) -> None:
    _write_result(
        tmp_path,
        {"trials": _single_trial(), "artifacts": {}, **extra},
        {},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _metric_component(), _protocol("metric", metrics=["score"]), tmp_path
        )
    _assert_code(error, code)


def test_protocol_required_metric_must_be_present_on_every_trial(
    tmp_path: Path,
) -> None:
    _write_result(
        tmp_path,
        {
            "trials": [
                {
                    "seed": seed,
                    "metrics": {
                        "score": seed + 1,
                        **({"calibration_error": 0.1} if seed < 4 else {}),
                    },
                    "clean": True,
                }
                for seed in range(5)
            ],
            "artifacts": {},
        },
        {},
    )

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _metric_component(seeds=[0, 1, 2, 3, 4]),
            _protocol("metric", metrics=["score", "calibration_error"]),
            tmp_path,
        )

    _assert_code(error, "required_metric_mismatch")


def test_missing_decision_metric_does_not_fall_back_to_unrelated_metric(
    tmp_path: Path,
) -> None:
    _write_result(
        tmp_path,
        {
            "trials": [
                {
                    "seed": "only",
                    "metrics": {"unrelated_metric": 99},
                    "clean": True,
                }
            ],
            "artifacts": {},
        },
        {},
    )

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _metric_component(),
            _protocol("metric", metrics=["score"]),
            tmp_path,
        )

    _assert_code(error, "missing_trial_metric")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda trials: trials.pop(),
        lambda trials: trials.__setitem__(0, {**trials[0], "arm": "treatment"}),
        lambda trials: trials.append(dict(trials[0])),
        lambda trials: trials.__setitem__(0, {**trials[0], "clean": False}),
        lambda trials: trials.__setitem__(0, {**trials[0], "metrics": {"score": True}}),
    ],
)
def test_paired_trials_fail_closed_on_incomplete_duplicate_unclean_or_non_numeric(
    tmp_path: Path, mutate: Any
) -> None:
    trials = [
        {"seed": seed, "arm": arm, "metrics": {"score": seed + offset}, "clean": True}
        for seed in (0, 1, 2)
        for arm, offset in (("control", 0.0), ("treatment", 0.5))
    ]
    mutate(trials)
    _write_result(tmp_path, {"trials": trials, "artifacts": {}}, {})
    component = _metric_component(
        metric="paired_score", target=0.1, seeds=[0, 1, 2]
    )
    with pytest.raises(ComponentResultValidationError):
        validate_component_result(
            component,
            _protocol("metric", metrics=["paired_score", "paired_delta"]),
            tmp_path,
        )


@pytest.mark.parametrize("metric", ["score", "max_score"])
@pytest.mark.parametrize("seeds", [[], [0, 1, 2, 3, 4]])
def test_unpaired_results_reject_arm_without_requiring_declared_seeds(tmp_path, metric, seeds):
    trials = [{"seed": seed, "metrics": {metric: 6}, "clean": True}
              for seed in seeds or [0]]
    trials[0]["arm"] = "bounded-cuda-checksum"
    _write_result(tmp_path, {"trials": trials, "artifacts": {}}, {})
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(_metric_component(metric=metric, seeds=seeds),
                                  _protocol("metric", metrics=[metric]), tmp_path)
    _assert_code(error, "trial_completeness")


@pytest.mark.parametrize("metric,metrics,expected", [
    ("score", ["score"], {"const": None}),
    ("max_score", ["max_score"], {"const": None}),
    ("paired_score", ["paired_score"], {"enum": ["control", "treatment"]}),
    ("score", ["score", "paired_delta"], {"enum": ["control", "treatment"]}),
])
def test_rendered_trial_arm_rules_follow_the_metric_mode(metric, metrics, expected):
    contract = component_result_contract(_metric_component(metric=metric),
                                         _protocol("metric", metrics=metrics))
    assert contract["trial_item"]["arm"] == expected


def test_n5_rejects_a_substitute_seed_even_when_count_matches(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        {
            "trials": [
                {"seed": seed, "metrics": {"score": 1}, "clean": True}
                for seed in (0, 1, 2, 3, 99)
            ],
            "artifacts": {},
        },
        {},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _metric_component(seeds=[0, 1, 2, 3, 4]),
            _protocol("metric", metrics=["score"]),
            tmp_path,
        )
    _assert_code(error, "trial_completeness")


def test_required_artifact_min_count_is_enforced(tmp_path: Path) -> None:
    required = {"logs": {"min_count": 2}}
    _write_result(
        tmp_path,
        {"artifacts": {"logs": ["one.log"]}},
        {"one.log": "one\n"},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _artifact_component(required),
            _protocol("artifact", artifacts=required),
            tmp_path,
        )
    _assert_code(error, "artifact_count_mismatch")


@pytest.mark.parametrize("reference", ["../outside.txt", "/tmp/outside.txt", "a\\b.txt"])
def test_artifact_paths_must_be_safe_relative_paths(
    tmp_path: Path, reference: str
) -> None:
    _write_result(tmp_path, {"artifacts": {"report": reference}}, {})
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _artifact_component(["report"]),
            _protocol("artifact", artifacts=["report"]),
            tmp_path,
        )
    _assert_code(error, "unsafe_artifact_path")


@pytest.mark.parametrize(
    ("artifacts", "expected_code"),
    [
        ({"first": [], "second": "../outside.txt"}, "invalid_artifact_map"),
        ({"second": "../outside.txt", "first": []}, "unsafe_artifact_path"),
    ],
)
def test_repairability_cannot_be_enabled_by_ordering_an_empty_before_unsafe_path(
    tmp_path: Path,
    artifacts: dict[str, object],
    expected_code: str,
) -> None:
    component = _artifact_component(["first", "second"])
    protocol = _protocol("artifact", artifacts=["first", "second"])
    (tmp_path / "result.json").write_text(
        json.dumps({"artifacts": artifacts}), encoding="utf-8"
    )

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)

    _assert_code(error, expected_code)
    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )


def test_repairability_accepts_inline_shape_but_not_wrong_artifact_inventory(
    tmp_path: Path,
) -> None:
    component = _artifact_component(["first", "second"])
    protocol = _protocol("artifact", artifacts=["first", "second"])
    _write_result(tmp_path, {"artifacts": {"first": {}, "second": {}}}, {})
    with pytest.raises(ComponentResultValidationError) as inline:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(inline, "invalid_artifact_map")
    assert repairable_component_result_error(
        component, protocol, tmp_path, inline.value
    )

    _write_result(tmp_path, {"artifacts": {"wrong": {}}}, {})
    with pytest.raises(ComponentResultValidationError) as inventory:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(inventory, "artifact_inventory_mismatch")
    assert not repairable_component_result_error(
        component, protocol, tmp_path, inventory.value
    )


def test_artifact_paths_cannot_alias_by_reference_hardlink_or_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "reference"
    _write_result(
        root,
        {"artifacts": {"first": "same.txt", "second": "same.txt"}},
        {"same.txt": "value\n"},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _artifact_component(["first", "second"]),
            _protocol("artifact", artifacts=["first", "second"]),
            root,
        )
    _assert_code(error, "artifact_path_alias")

    root = tmp_path / "hardlink"
    _write_result(
        root,
        {"artifacts": {"first": "one.txt", "second": "two.txt"}},
        {"one.txt": "value\n"},
    )
    os.link(root / "one.txt", root / "two.txt")
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _artifact_component(["first", "second"]),
            _protocol("artifact", artifacts=["first", "second"]),
            root,
        )
    _assert_code(error, "artifact_path_alias")

    root = tmp_path / "symlink"
    _write_result(root, {"artifacts": {"report": "link.txt"}}, {})
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _artifact_component(["report"]),
            _protocol("artifact", artifacts=["report"]),
            root,
        )
    _assert_code(error, "unsafe_artifact_path")


def test_undeclared_files_are_rejected_from_exact_inventory(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        {"artifacts": {"report": "report.txt"}},
        {"report.txt": "ok\n", "undeclared.log": "not mapped\n"},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            _artifact_component(["report"]),
            _protocol("artifact", artifacts=["report"]),
            tmp_path,
        )
    _assert_code(error, "artifact_inventory_mismatch")


def test_v5_repairs_retained_extra_source_as_the_safe_first_failure(
    tmp_path: Path,
) -> None:
    patch = "--- /dev/null\n+++ b/model.py\n@@ -0,0 +1 @@\n+candidate = True\n"
    extra_source = "candidate = True\n"
    digest = hashlib.sha256(extra_source.encode()).hexdigest()
    artifacts = [
        "implementation_patch",
        "runner_config",
        "integration_notes",
        "candidate_manifest",
        "dual_dataset_design",
    ]
    component = _artifact_component(artifacts)
    protocol = _protocol(
        "artifact",
        artifacts=artifacts,
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            },
            {
                "type": "json_contract",
                "artifact": "runner_config",
                "required_paths": ["schema_version", "method_family_id"],
            },
            {
                "type": "json_contract",
                "artifact": "candidate_manifest",
                "required_paths": ["method_family_id"],
            },
            {
                "type": "text_contract",
                "artifact": "integration_notes",
                "required_terms": ["implementation_patch", "runner"],
            },
            {
                "type": "text_contract",
                "artifact": "dual_dataset_design",
                "required_terms": ["MELD", "IEMOCAP", "shared mechanism"],
            },
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {
                "implementation_patch": "implementation_patch.diff",
                "runner_config": "runner_config.json",
                "integration_notes": "integration_notes.md",
                "candidate_manifest": "candidate_manifest.json",
                "dual_dataset_design": "dual_dataset_design.md",
            },
            "identity": {
                "method_family_id": "relative_position_speaker_memory_modality_reliability",
                "implementation_sha256": digest,
            },
        },
        {
            "implementation_patch.diff": patch,
            "runner_config.json": json.dumps(
                {
                    "schema_version": 1,
                    "method_family_id": "relative_position_speaker_memory_modality_reliability",
                }
            ),
            "integration_notes.md": "implementation_patch is consumed by the runner.",
            "candidate_manifest.json": json.dumps(
                {
                    "method_family_id": "relative_position_speaker_memory_modality_reliability"
                }
            ),
            "dual_dataset_design.md": "MELD and IEMOCAP share one shared mechanism.",
            "multiemo_causal_candidate.py": extra_source,
        },
    )

    apply_calls = 0

    def apply_check(_payload: bytes) -> bool:
        nonlocal apply_calls
        apply_calls += 1
        return False

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            git_apply_check=apply_check,
        )

    _assert_code(error, "artifact_inventory_mismatch")
    assert error.value.location == "artifact_dir"
    assert error.value.message == (
        "undeclared=['multiemo_causal_candidate.py'], missing=[]"
    )
    assert repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id=(
            "relative_position_speaker_memory_modality_reliability"
        ),
        git_apply_check=apply_check,
    )
    assert apply_calls == 0


def test_v5_repairs_standalone_candidate_patch_digest_mismatch(tmp_path: Path) -> None:
    patch = "--- /dev/null\n+++ b/model.py\n@@ -0,0 +1 @@\n+candidate = True\n"
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": False,
            }
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"implementation_patch": "implementation_patch.diff"},
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": "f" * 64,
            },
        },
        {"implementation_patch.diff": patch},
    )

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
        )

    _assert_code(error, "implementation_digest_mismatch")
    assert error.value.location == "implementation_sha256"
    assert error.value.message == "bytes do not match"
    assert repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
    )


def test_v5_digest_repair_does_not_trust_target_apply_callback(tmp_path: Path) -> None:
    patch = "--- /dev/null\n+++ b/model.py\n@@ -0,0 +1 @@\n+candidate = True\n"
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            }
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"implementation_patch": "implementation_patch.diff"},
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": "f" * 64,
            },
        },
        {"implementation_patch.diff": patch},
    )
    apply_calls = 0

    def apply_check(_payload: bytes) -> bool:
        nonlocal apply_calls
        apply_calls += 1
        return True

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            git_apply_check=apply_check,
        )
    _assert_code(error, "implementation_digest_mismatch")
    assert apply_calls == 1
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=apply_check,
    )
    assert apply_calls == 1


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "malformed",
        "wrong_family",
        "explicit_bytes",
        "callback_present",
        "callback_error",
    ],
)
def test_v5_standalone_digest_repair_fails_closed_on_non_candidate_errors(
    tmp_path: Path, case: str
) -> None:
    patch = "--- /dev/null\n+++ b/model.py\n@@ -0,0 +1 @@\n+candidate = True\n"
    patch_digest = hashlib.sha256(patch.encode()).hexdigest()
    identity: dict[str, object] = {
        "method_family_id": "family-b" if case == "wrong_family" else "family-a",
        "implementation_sha256": patch_digest if case == "callback_error" else "f" * 64,
    }
    if case == "missing":
        identity.pop("implementation_sha256")
    elif case == "malformed":
        identity["implementation_sha256"] = "SHA256:not-exact"
    implementation_bytes = b"external implementation" if case == "explicit_bytes" else None
    digest_check = (
        (lambda _supplied: "verifier unavailable")
        if case == "callback_error"
        else (lambda _supplied: True) if case == "callback_present" else None
    )
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol("artifact", artifacts=["implementation_patch"])
    _write_result(
        tmp_path,
        {
            "artifacts": {"implementation_patch": "implementation_patch.diff"},
            "identity": identity,
        },
        {"implementation_patch.diff": patch},
    )

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            implementation_bytes=implementation_bytes,
            implementation_digest_check=digest_check,
        )

    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        implementation_bytes=implementation_bytes,
        implementation_digest_check=digest_check,
    )


@pytest.mark.parametrize(
    "case",
    [
        "logical_id",
        "missing_declared",
        "symlink_extra",
        "alias_extra",
        "bad_identity",
        "wrong_family",
        "digest_callback",
    ],
)
def test_v5_compound_inventory_repair_fails_closed_on_other_defects(
    tmp_path: Path, case: str
) -> None:
    patch = "--- /dev/null\n+++ b/model.py\n@@ -0,0 +1 @@\n+candidate = True\n"
    extra_source = "candidate = True\n"
    artifacts = {
        "implementation_patch": "implementation_patch.diff",
        "report": "report.md",
    }
    if case == "logical_id":
        artifacts = {"wrong": "implementation_patch.diff", "report": "report.md"}
    identity = {
        "method_family_id": "family-b" if case == "wrong_family" else "family-a",
        "implementation_sha256": (
            "not-a-digest"
            if case == "bad_identity"
            else hashlib.sha256(extra_source.encode()).hexdigest()
        ),
    }
    files = {
        "implementation_patch.diff": patch,
        "report.md": "required phrase",
        "extra.py": extra_source,
    }
    if case == "missing_declared":
        files.pop("report.md")
    component = _artifact_component(["implementation_patch", "report"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch", "report"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            },
            {
                "type": "text_contract",
                "artifact": "report",
                "required_terms": ["required phrase"],
            },
        ],
    )
    _write_result(
        tmp_path,
        {"artifacts": artifacts, "identity": identity},
        files,
    )
    if case in {"symlink_extra", "alias_extra"}:
        (tmp_path / "extra.py").unlink()
        if case == "symlink_extra":
            (tmp_path / "extra.py").symlink_to(tmp_path / "report.md")
        else:
            os.link(tmp_path / "report.md", tmp_path / "extra.py")
    digest_check = (lambda _supplied: True) if case == "digest_callback" else None

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            git_apply_check=lambda _payload: True,
            implementation_digest_check=digest_check,
        )

    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=lambda _payload: True,
        implementation_digest_check=digest_check,
    )


@pytest.mark.parametrize(
    ("manifest", "validator"),
    [
        (
            {"status": "validated", "classes": {"a": 1}},
            {"required_paths": ["dataset"]},
        ),
        (
            {"dataset": "MELD", "classes": {"a": 1}},
            {"exact_key_sets": {"classes": ["a", "b"]}},
        ),
        (
            {"dataset": "MELD", "classes": {"a": 1}},
            {"allowed_values": {"dataset": ["IEMOCAP"]}},
        ),
    ],
)
def test_json_contract_rejects_missing_exact_key_and_allowed_value_failures(
    tmp_path: Path, manifest: dict[str, object], validator: dict[str, object]
) -> None:
    component = _artifact_component(["manifest"])
    protocol = _protocol(
        "artifact",
        artifacts=["manifest"],
        validators=[
            {"type": "json_contract", "artifact": "manifest", **validator}
        ],
    )
    _write_result(
        tmp_path,
        {"artifacts": {"manifest": "manifest.json"}},
        {"manifest.json": json.dumps(manifest)},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "json_contract_failed")
    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("short", "requires at least 20 characters"),
        (
            "MELD and IEMOCAP share a shared mechanism but omit the final term",
            "missing required text 'adapter'",
        ),
        (
            "MELD IEMOCAP shared mechanism adapter contains forbidden shortcut",
            "contains forbidden text 'forbidden shortcut'",
        ),
    ],
)
def test_text_contract_content_failures_are_repairable_for_v3(
    tmp_path: Path, payload: str, message: str
) -> None:
    component = _artifact_component(["report"])
    protocol = _protocol(
        "artifact",
        artifacts=["report"],
        validators=[
            {
                "type": "text_contract",
                "artifact": "report",
                "min_chars": 20,
                "required_terms": ["MELD", "IEMOCAP", "shared mechanism", "adapter"],
                "forbidden_terms": ["forbidden shortcut"],
            }
        ],
    )
    _write_result(
        tmp_path,
        {"artifacts": {"report": "report.md"}},
        {"report.md": payload},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "text_contract_failed")
    assert error.value.location == "report.md"
    assert error.value.message == message
    assert repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )


def test_text_contract_repairability_requires_exact_validator_error_shape(
    tmp_path: Path,
) -> None:
    component = _artifact_component(["report"])
    protocol = _protocol(
        "artifact",
        artifacts=["report"],
        validators=[
            {
                "type": "text_contract",
                "artifact": "report",
                "required_terms": ["required phrase"],
            }
        ],
    )
    _write_result(
        tmp_path,
        {"artifacts": {"report": "nested/report.md"}},
        {"nested/report.md": "candidate text"},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "text_contract_failed")

    wrong_location = ComponentResultValidationError(
        "text_contract_failed", "other.md", error.value.message
    )
    wrong_message = ComponentResultValidationError(
        "text_contract_failed", error.value.location, "missing required text 'other'"
    )
    assert not repairable_component_result_error(
        component, protocol, tmp_path, wrong_location
    )
    assert not repairable_component_result_error(
        component, protocol, tmp_path, wrong_message
    )
    assert not repairable_component_result_error(
        component,
        _protocol("artifact", artifacts=["report"]),
        tmp_path,
        error.value,
    )


def test_text_contract_repairability_replays_duplicate_basenames_in_order(
    tmp_path: Path,
) -> None:
    component = _artifact_component(["left", "right"])
    protocol = _protocol(
        "artifact",
        artifacts=["left", "right"],
        validators=[
            {
                "type": "text_contract",
                "artifact": "left",
                "required_terms": ["left phrase"],
            },
            {
                "type": "text_contract",
                "artifact": "right",
                "required_terms": ["right phrase"],
            },
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {
                "left": "left/report.md",
                "right": "right/report.md",
            }
        },
        {
            "left/report.md": "left phrase is present",
            "right/report.md": "candidate text omits the required words",
        },
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)

    assert error.value.location == "report.md"
    assert error.value.message == "missing required text 'right phrase'"
    assert repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )

    (tmp_path / "left" / "report.md").write_text(
        "candidate text also omits the required words", encoding="utf-8"
    )
    with pytest.raises(ComponentResultValidationError) as multiple:
        validate_component_result(component, protocol, tmp_path)
    assert multiple.value.message == "missing required text 'left phrase'"
    assert repairable_component_result_error(
        component, protocol, tmp_path, multiple.value
    )


def test_later_text_validator_non_text_failure_is_not_repairable(
    tmp_path: Path,
) -> None:
    component = _artifact_component(["first", "second"])
    protocol = _protocol(
        "artifact",
        artifacts=["first", "second"],
        validators=[
            {
                "type": "text_contract",
                "artifact": "first",
                "required_terms": ["required first"],
            },
            {
                "type": "text_contract",
                "artifact": "second",
                "required_terms": ["required second"],
            },
        ],
    )
    _write_result(
        tmp_path,
        {"artifacts": {"first": "first.md", "second": "second.md"}},
        {"first.md": "candidate first", "second.md": "placeholder"},
    )
    (tmp_path / "second.md").write_bytes(b"\xff\xfe")
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "text_contract_failed")

    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )


def test_text_contract_repairability_validates_optional_result_identity(
    tmp_path: Path,
) -> None:
    patch = b"--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"
    digest = hashlib.sha256(patch).hexdigest()
    component = _artifact_component(["implementation_patch", "report"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch", "report"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            },
            {
                "type": "text_contract",
                "artifact": "report",
                "required_terms": ["required phrase"],
            },
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {
                "implementation_patch": "change.patch",
                "report": "report.md",
            },
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": digest,
            },
        },
        {"change.patch": patch.decode(), "report.md": "candidate text"},
    )
    def apply_check(_payload: bytes) -> bool:
        return True

    def digest_check(supplied: str) -> bool:
        return supplied == digest
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component, protocol, tmp_path, git_apply_check=apply_check
        )
    _assert_code(error, "text_contract_failed")

    assert repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=apply_check,
        implementation_digest_check=digest_check,
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        git_apply_check=apply_check,
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-b",
        git_apply_check=apply_check,
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=apply_check,
        implementation_bytes=b"different implementation",
    )

    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["identity"] = {"implementation_sha256": digest}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        git_apply_check=apply_check,
    )

    result["identity"] = {"method_family_id": " family-a"}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=apply_check,
    )

    result.pop("identity")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=apply_check,
    )


def test_text_contract_failure_cannot_mask_identity_or_later_diff_integrity(
    tmp_path: Path,
) -> None:
    component = _artifact_component(["report", "patch"])
    text_validator = {
        "type": "text_contract",
        "artifact": "report",
        "required_terms": ["required phrase"],
    }
    protocol = _protocol(
        "artifact",
        artifacts=["report", "patch"],
        validators=[
            text_validator,
            {"type": "unified_diff", "artifact": "patch", "apply_check": True},
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"report": "report.md", "patch": "change.patch"},
            "identity": {"implementation_sha256": "not-a-digest"},
        },
        {"report.md": "candidate text", "change.patch": "not a patch\n"},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "text_contract_failed")
    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )

    payload = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    payload.pop("identity")
    (tmp_path / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "text_contract_failed")
    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )


def test_outcome_adapter_v9_retains_v7_repairability_semantics() -> None:
    assert OUTCOME_ADAPTER_VERSION == "component-result/9"


def test_v7_repairs_exact_candidate_authored_unified_diff_syntax(
    tmp_path: Path,
) -> None:
    patch = (
        "--- /dev/null+++ b/src/merc/models/multiemo_causal_candidate.py"
        "@@ -0,0 +1 @@+candidate = True\n"
    )
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            }
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"implementation_patch": "implementation.patch"},
            "identity": {
                "method_family_id": "causal-state-accumulator",
                "implementation_sha256": hashlib.sha256(patch.encode()).hexdigest(),
            },
        },
        {"implementation.patch": patch},
    )
    apply_calls = 0

    def apply_check(_payload: bytes) -> bool:
        nonlocal apply_calls
        apply_calls += 1
        return True

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            git_apply_check=apply_check,
        )

    _assert_code(error, "invalid_unified_diff")
    assert error.value.location == "implementation.patch"
    assert error.value.message == "must contain paired headers and a text hunk"
    assert repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="causal-state-accumulator",
        git_apply_check=apply_check,
    )
    assert apply_calls == 0


def test_v7_unified_diff_syntax_fails_closed_without_apply_check_or_binding(
    tmp_path: Path,
) -> None:
    patch = "not a patch\n"
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            }
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"implementation_patch": "implementation.patch"},
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": hashlib.sha256(patch.encode()).hexdigest(),
            },
        },
        {"implementation.patch": patch},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)

    _assert_code(error, "invalid_unified_diff")
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=lambda _payload: True,
        implementation_bytes=patch.encode(),
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=lambda _payload: True,
        implementation_digest_check=lambda _digest: True,
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-b",
        git_apply_check=lambda _payload: True,
    )

    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["identity"]["implementation_sha256"] = "f" * 64
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=lambda _payload: True,
    )


def test_v7_concatenated_unified_diff_cannot_hide_an_unsafe_path(
    tmp_path: Path,
) -> None:
    patch = "--- ../../outside.py+++ b/src/model.py@@ -1 +1 @@-old+new\n"
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[{
            "type": "unified_diff",
            "artifact": "implementation_patch",
            "apply_check": True,
        }],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"implementation_patch": "implementation.patch"},
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": hashlib.sha256(patch.encode()).hexdigest(),
            },
        },
        {"implementation.patch": patch},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            git_apply_check=lambda _payload: True,
        )

    _assert_code(error, "invalid_unified_diff")
    assert error.value.message == "must contain paired headers and a text hunk"
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=lambda _payload: True,
    )


def test_v7_unified_diff_logical_artifact_must_select_one_path(
    tmp_path: Path,
) -> None:
    malformed = "--- /dev/null+++ b/src/model.py@@ -0,0 +1 @@+candidate\n"
    valid = "--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-old\n+new\n"
    requirement = {"patches": {"min_count": 2}}
    component = _artifact_component(requirement)
    protocol = _protocol(
        "artifact",
        artifacts=requirement,
        validators=[{
            "type": "unified_diff",
            "artifact": "patches",
            "apply_check": False,
        }],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"patches": ["malformed.patch", "valid.patch"]},
            "identity": {"method_family_id": "family-a"},
        },
        {"malformed.patch": malformed, "valid.patch": valid},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)

    _assert_code(error, "invalid_unified_diff")
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
    )


@pytest.mark.parametrize(
    ("patch", "callback", "code"),
    [
        ("--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b\n", None, "apply_check_unavailable"),
        (
            "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b\n",
            lambda _payload: "does not apply",
            "apply_check_failed",
        ),
        (
            "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b\n",
            lambda _payload: (_ for _ in ()).throw(OSError("verifier unavailable")),
            "apply_check_failed",
        ),
    ],
)
def test_unified_diff_apply_check_failures_are_not_repairable(
    tmp_path: Path, patch: str, callback: Any, code: str
) -> None:
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            }
        ],
    )
    _write_result(
        tmp_path,
        {"artifacts": {"implementation_patch": "change.patch"}},
        {"change.patch": patch},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            git_apply_check=callback,
        )
    _assert_code(error, code)
    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )


@pytest.mark.parametrize(
    "patch",
    [
        "--- source.py\n+++ b/source.py\n@@ -1 +1 @@\n-old\n+new\n",
        "--- a/../source.py\n+++ b/../source.py\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_v7_unified_diff_path_errors_remain_nonrepairable(
    tmp_path: Path, patch: str
) -> None:
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": True,
            }
        ],
    )
    _write_result(
        tmp_path,
        {
            "artifacts": {"implementation_patch": "implementation.patch"},
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": hashlib.sha256(patch.encode()).hexdigest(),
            },
        },
        {"implementation.patch": patch},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            git_apply_check=lambda _payload: True,
        )

    _assert_code(error, "invalid_unified_diff")
    assert error.value.message != "must contain paired headers and a text hunk"
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
        git_apply_check=lambda _payload: True,
    )


def test_v7_unified_diff_decode_and_later_contract_errors_remain_nonrepairable(
    tmp_path: Path,
) -> None:
    component = _artifact_component(["implementation_patch", "report"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch", "report"],
        validators=[
            {
                "type": "unified_diff",
                "artifact": "implementation_patch",
                "apply_check": False,
            },
            {
                "type": "text_contract",
                "artifact": "report",
                "required_terms": ["approved"],
            },
        ],
    )
    invalid_utf8 = b"\xff\xfe"
    _write_result(
        tmp_path,
        {
            "artifacts": {
                "implementation_patch": "implementation.patch",
                "report": "report.md",
            },
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": hashlib.sha256(invalid_utf8).hexdigest(),
            },
        },
        {"implementation.patch": "placeholder", "report.md": "approved"},
    )
    (tmp_path / "implementation.patch").write_bytes(invalid_utf8)
    with pytest.raises(ComponentResultValidationError) as decode_error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(decode_error, "invalid_unified_diff")
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        decode_error.value,
        expected_method_family_id="family-a",
    )

    malformed = "not a patch\n"
    (tmp_path / "implementation.patch").write_text(malformed, encoding="utf-8")
    (tmp_path / "report.md").write_text("candidate only", encoding="utf-8")
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    result["identity"]["implementation_sha256"] = hashlib.sha256(
        malformed.encode()
    ).hexdigest()
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ComponentResultValidationError) as syntax_error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(syntax_error, "invalid_unified_diff")
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        syntax_error.value,
        expected_method_family_id="family-a",
    )

    forged = ComponentResultValidationError(
        "invalid_unified_diff",
        syntax_error.value.location,
        "unsafe patch path 'source.py'",
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        forged,
        expected_method_family_id="family-a",
    )


def test_unified_diff_requires_a_hunk_line_consistently_with_oracle(
    tmp_path: Path,
) -> None:
    patch = "--- a/a\n+++ b/a\n ordinary body mentions @@ without a hunk\n"
    validator = {
        "type": "unified_diff",
        "artifact": "implementation_patch",
        "apply_check": False,
    }
    component = _artifact_component(["implementation_patch"])
    protocol = _protocol(
        "artifact",
        artifacts=["implementation_patch"],
        validators=[validator],
    )
    _write_result(
        tmp_path,
        {"artifacts": {"implementation_patch": "change.patch"}},
        {"change.patch": patch},
    )

    with pytest.raises(ValueError, match="lacks paired headers and a text hunk"):
        _validate_component_artifact_bytes(tmp_path / "change.patch", validator)
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "invalid_unified_diff")
    assert error.value.location == "change.patch"
    assert error.value.message == "must contain paired headers and a text hunk"


def test_identity_digest_must_be_exact_and_match_optional_bytes(tmp_path: Path) -> None:
    component = _artifact_component(["report"])
    protocol = _protocol("artifact", artifacts=["report"])
    _write_result(
        tmp_path,
        {
            "artifacts": {"report": "report.txt"},
            "identity": {"implementation_sha256": "a" * 64},
        },
        {"report.txt": "ok\n"},
    )
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(
            component,
            protocol,
            tmp_path,
            implementation_bytes=b"different",
        )
    _assert_code(error, "implementation_digest_mismatch")
    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )

    payload = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    payload["identity"]["implementation_sha256"] = "SHA256:not-exact"
    (tmp_path / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(error, "invalid_identity")
    assert not repairable_component_result_error(
        component, protocol, tmp_path, error.value
    )


def test_unbound_artifact_placeholder_identity_can_open_one_revision(tmp_path: Path) -> None:
    component = _artifact_component(["report"])
    component["required_inputs"] = []
    protocol = _protocol("artifact", artifacts=["report"])
    _write_result(
        tmp_path,
        {
            "artifacts": {"report": "report.txt"},
            "identity": {
                "method_family_id": "family-a",
                "implementation_sha256": "pending_integration",
                "core_config_sha256": "pending_integration",
            },
        },
        {"report.txt": "complete artifact\n"},
    )

    with pytest.raises(ComponentResultValidationError) as error:
        validate_component_result(component, protocol, tmp_path)

    _assert_code(error, "invalid_identity")
    assert repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-b",
    )

    component["required_inputs"] = [{"resource": "bound-upstream"}]
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
    )
    component["required_inputs"] = []
    component["dependencies"] = ["must.upstream"]
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error.value,
        expected_method_family_id="family-a",
    )


def test_unbound_identity_revision_fails_closed_on_every_binding_or_other_error(
    tmp_path: Path,
) -> None:
    component = _artifact_component(["report"])
    component.update({"dependencies": [], "required_inputs": []})
    protocol = _protocol("artifact", artifacts=["report"])
    result = {
        "artifacts": {"report": "report.txt"},
        "identity": {
            "method_family_id": "family-a",
            "implementation_sha256": "pending_integration",
        },
    }
    _write_result(tmp_path, result, {"report.txt": "complete artifact\n"})
    with pytest.raises(ComponentResultValidationError) as captured:
        validate_component_result(component, protocol, tmp_path)
    _assert_code(captured, "invalid_identity")
    error = captured.value

    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error,
        expected_method_family_id="family-a",
        implementation_bytes=b"externally bound implementation",
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        error,
        expected_method_family_id="family-a",
        implementation_digest_check=lambda _value: True,
    )
    forged = ComponentResultValidationError(
        "invalid_identity", "result.json.identity", "forged diagnostic"
    )
    assert not repairable_component_result_error(
        component,
        protocol,
        tmp_path,
        forged,
        expected_method_family_id="family-a",
    )

    patch_component = _artifact_component(["implementation_patch"])
    patch_component.update({"dependencies": [], "required_inputs": []})
    patch_protocol = _protocol(
        "artifact", artifacts=["implementation_patch"]
    )
    patch_root = tmp_path / "patch"
    _write_result(
        patch_root,
        {
            "artifacts": {"implementation_patch": "candidate.patch"},
            "identity": result["identity"],
        },
        {"candidate.patch": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b\n"},
    )
    with pytest.raises(ComponentResultValidationError) as patch_error:
        validate_component_result(patch_component, patch_protocol, patch_root)
    _assert_code(patch_error, "invalid_identity")
    assert not repairable_component_result_error(
        patch_component,
        patch_protocol,
        patch_root,
        patch_error.value,
        expected_method_family_id="family-a",
    )

    diff_protocol = _protocol(
        "artifact",
        artifacts=["report"],
        validators=[{
            "artifact": "report",
            "type": "unified_diff",
            "apply_check": False,
        }],
    )
    diff_root = tmp_path / "diff"
    _write_result(
        diff_root,
        result,
        {"report.txt": "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b\n"},
    )
    with pytest.raises(ComponentResultValidationError) as diff_error:
        validate_component_result(component, diff_protocol, diff_root)
    _assert_code(diff_error, "invalid_identity")
    assert not repairable_component_result_error(
        component,
        diff_protocol,
        diff_root,
        diff_error.value,
        expected_method_family_id="family-a",
    )

    bad_text_protocol = _protocol(
        "artifact",
        artifacts=["report"],
        validators=[{
            "artifact": "report",
            "type": "text_contract",
            "required_terms": ["required words"],
        }],
    )
    bad_text_root = tmp_path / "bad-text"
    _write_result(bad_text_root, result, {"report.txt": "wrong content\n"})
    assert not repairable_component_result_error(
        component,
        bad_text_protocol,
        bad_text_root,
        error,
        expected_method_family_id="family-a",
    )
    missing_root = tmp_path / "missing"
    _write_result(missing_root, result, {"report.txt": "complete artifact\n"})
    (missing_root / "report.txt").unlink()
    assert not repairable_component_result_error(
        component,
        protocol,
        missing_root,
        error,
        expected_method_family_id="family-a",
    )


def test_result_contract_makes_optional_identity_constraints_explicit() -> None:
    contract = component_result_contract(
        _artifact_component(["report"]),
        _protocol("artifact", artifacts=["report"]),
    )

    identity = contract["optional_identity"]
    assert identity["omit_when_unavailable"] is True
    assert identity["placeholder_values_allowed"] is False
    assert identity["fields"]["method_family_id"]["must_equal"] == (
        "required_experiment_spec.method_family_id"
    )
    assert identity["fields"]["implementation_sha256"]["pattern"] == (
        "^[0-9a-f]{64}$"
    )
    assert identity["fields"]["core_config_sha256"]["pattern"] == (
        "^[0-9a-f]{64}$"
    )


def test_result_contract_echoes_validators_and_digest_basis() -> None:
    validators = [
        {
            "type": "unified_diff",
            "artifact": "implementation_patch",
            "apply_check": True,
            "base_scope": "target",
        },
    ]
    contract = component_result_contract(
        _artifact_component(["implementation_patch"]),
        _protocol("artifact", artifacts=["implementation_patch"], validators=validators),
    )

    assert contract["artifact_validators"] == validators
    identity = contract["optional_identity"]
    assert identity["implementation_sha256_required_when"] == (
        "artifacts.implementation_patch is declared"
    )
    assert "implementation.patch" in identity["implementation_sha256_digest_basis"]

def test_module_has_no_legacy_import_and_never_invokes_a_shell() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "hypoloom"
        / "outcome_adapter.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name == "hypoloom" or name.startswith("hypoloom.") for name in imported)
    assert "subprocess" not in imported
    assert "os.system" not in source
