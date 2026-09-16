from __future__ import annotations

import json
from pathlib import Path

import yaml

from hypoloom.component_contract import component_execution_plan
from hypoloom.profile_admission import PROFILE_FILES, admit_profile


REPOSITORY = Path(__file__).resolve().parents[2]


def _copy_profile(source: Path, destination: Path) -> Path:
    destination.mkdir()
    for name in PROFILE_FILES:
        (destination / name).write_bytes((source / name).read_bytes())
    return destination


def _snapshot(profile: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(profile.iterdir())}


def _mutate_methodology(profile: Path, mutate: object) -> None:
    path = profile / "methodology.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(document)  # type: ignore[operator]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_initial_memory_setting_roundtrips_and_invalid_values_fail_profile_admission(tmp_path):
    profile = _copy_profile(REPOSITORY / "tests/fixtures/profiles/component-demo", tmp_path / "initial-memory")
    _mutate_methodology(profile, lambda doc: doc["budget"].update(
        min_vram_per_experiment=1024, initial_vram_per_experiment=8192, gpu_vram_reserve_mb=4096))
    before = _snapshot(profile)
    report = admit_profile(profile, run_id="memory-profile")
    assert report.admitted, report.to_dict()
    assert report.create_run_payload["research_contract"]["budget"]["initial_vram_per_experiment"] == 8192
    assert _snapshot(profile) == before
    for invalid in (None, True, 0, 1023, 1.5, 2**53):
        _mutate_methodology(profile, lambda doc: doc["budget"].update(initial_vram_per_experiment=invalid))
        rejected = admit_profile(profile, run_id="memory-profile")
        assert not rejected.admitted
        assert any(item.location == "methodology.yaml.budget.initial_vram_per_experiment" for item in rejected.blockers)


def test_hypoloom_demo_normalizes_to_one_component_contract() -> None:
    report = admit_profile(
        REPOSITORY / "tests" / "fixtures" / "profiles" / "component-demo",
        run_id="component-demo",
    )

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    assert set(report.create_run_payload) == {
        "run_id",
        "research_contract",
        "execution",
    }
    assert report.create_run_payload["execution"] == {
        "allow_gpu": False,
        "delay_seconds": 0.0,
        "target": None,
        "runtime_prefix": None,
        "read_roots": [],
    }
    contract = report.create_run_payload["research_contract"]
    assert contract["budget"] == {
        "agent_invocation": {
            "max_turns": 2,
            "max_tokens": 512,
            "max_output_bytes": 16_384,
            "wall_seconds": 5,
            "max_cost": "0",
            "token_enforcement_mode": "enforce",
        }
    }
    assert contract["schema_version"] == 1
    assert contract["goal"] == {
        "objective": (
            "Demonstrate one bounded three-expert Hypoloom closure with "
            "deterministic local measurements."
        ),
        "language": "",
        "verdict_rule": "required_conjunction",
        "summary_metric": {
            "name": "score",
            "direction": "MAXIMIZE",
            "target": "3",
            "display_only": True,
        },
    }
    assert [role["id"] for role in contract["roles"]] == [
        "scientist-a",
        "scientist-b",
        "scientist-c",
    ]
    assert contract["components"] == [
        {
            "id": "primary.score",
            "tier": "primary",
            "name": "score",
            "owner": "scientist-a",
            "proposers": ["scientist-a"],
            "dependencies": [],
            "required_inputs": [],
            "protocol": None,
            "result": {
                "kind": "metric",
                "metric": "score",
                "direction": "MAXIMIZE",
                "target": "3",
            },
            "seeds": [],
            "required_artifacts": [],
        }
    ]
    assert contract["protocols"] == {}
    assert contract["safety"] == {"read_roots": [], "sandbox": None}
    assert contract["observer"] == {
        "notify": {"channels": [], "digest": False, "digest_model": None}
    }


def test_component_batch_template_admits_six_frozen_production_batches(
    component_batch_profile: Path,
) -> None:
    report = admit_profile(
        component_batch_profile,
        run_id="template-component-contract",
        allow_gpu=True,
    )

    assert report.admitted, report.to_dict()
    assert report.blockers == ()
    assert report.create_run_payload is not None
    contract = report.create_run_payload["research_contract"]
    assert contract["methodology"]["controls"]["experiment_isolation"] == (
        "attempt_overlay"
    )
    assert contract["budget"]["agent_invocation"] == {
            "max_turns": 4,
        "max_tokens": 20_000,
        "max_output_bytes": 65_536,
        "wall_seconds": 180,
        "max_cost": "0.25",
        "token_enforcement_mode": "enforce",
    }
    plans = {
        component["id"]: component_execution_plan(contract, component["id"])
        for component in contract["components"]
    }
    assert {
        component_id: (
            plan["driver_plan"]["shape"],
            plan["driver_plan"]["dataset"],
            len(plan["driver_plan"]["trials"]),
        )
        for component_id, plan in plans.items()
        if plan.get("batch") is True
    } == {
        "primary.dataset_alpha_final_score": (
            "seeded_trials",
            "DATASET_ALPHA",
            5,
        ),
        "primary.dataset_beta_final_score": (
            "seeded_trials",
            "DATASET_BETA",
            5,
        ),
        "must.dataset_alpha_audit": (
            "checkpoint_audit",
            "DATASET_ALPHA",
            5,
        ),
        "must.dataset_beta_audit": (
            "checkpoint_audit",
            "DATASET_BETA",
            5,
        ),
        "must.dataset_alpha_screen_completed": (
            "paired_control_treatment",
            "DATASET_ALPHA",
            6,
        ),
        "must.dataset_beta_screen_completed": (
            "paired_control_treatment",
            "DATASET_BETA",
            6,
        ),
    }


def test_profile_admission_normalizes_legacy_worktree_isolation(
    component_batch_profile: Path,
    tmp_path: Path,
) -> None:
    profile = _copy_profile(component_batch_profile, tmp_path / "legacy-worktree")

    def use_legacy_value(document: dict[str, object]) -> None:
        document["hg"]["experiment_isolation"] = "worktree"  # type: ignore[index]

    _mutate_methodology(profile, use_legacy_value)
    report = admit_profile(profile, run_id="legacy-worktree")

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    contract = report.create_run_payload["research_contract"]
    assert contract["methodology"]["controls"]["experiment_isolation"] == (
        "attempt_overlay"
    )


def test_profile_admission_rejects_unknown_experiment_isolation(
    component_batch_profile: Path,
    tmp_path: Path,
) -> None:
    profile = _copy_profile(component_batch_profile, tmp_path / "unknown-isolation")

    def use_unknown_value(document: dict[str, object]) -> None:
        document["hg"]["experiment_isolation"] = "container"  # type: ignore[index]

    _mutate_methodology(profile, use_unknown_value)
    report = admit_profile(profile, run_id="unknown-isolation")

    assert not report.admitted
    assert any(
        blocker.code == "contract_invalid_value"
        and blocker.location == "methodology.yaml.hg.experiment_isolation"
        for blocker in report.blockers
    )


def test_component_batch_compiler_emits_canonical_trial_driver_plans(
    component_batch_profile: Path,
) -> None:
    report = admit_profile(
        component_batch_profile,
        run_id="template-driver-contract",
        allow_gpu=True,
    )
    assert report.create_run_payload is not None
    contract = report.create_run_payload["research_contract"]
    implementation = "a" * 64
    family = "example-method-family"
    expected = {
        "primary.dataset_alpha_final_score": (
            "seeded_trials",
            "DATASET_ALPHA",
        ),
        "primary.dataset_beta_final_score": (
            "seeded_trials",
            "DATASET_BETA",
        ),
        "must.dataset_alpha_audit": ("checkpoint_audit", "DATASET_ALPHA"),
        "must.dataset_beta_audit": ("checkpoint_audit", "DATASET_BETA"),
        "must.dataset_alpha_screen_completed": (
            "paired_control_treatment",
            "DATASET_ALPHA",
        ),
        "must.dataset_beta_screen_completed": (
            "paired_control_treatment",
            "DATASET_BETA",
        ),
    }

    compiled: dict[str, dict[str, object]] = {}
    for component_id, (shape, dataset) in expected.items():
        execution = component_execution_plan(
            contract,
            component_id,
            method_family_id=family,
            implementation_sha256=implementation,
            device="cuda",
        )
        assert execution["argv"][:2] == ("python", ".hypoloom/trial_driver.py")
        encoded = execution["argv"][2]
        assert isinstance(encoded, str)
        driver = json.loads(encoded)
        assert encoded == json.dumps(
            driver, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        assert driver == execution["driver_plan"]
        assert driver["schema_version"] == 1
        assert driver["shape"] == shape
        assert driver["dataset"] == dataset
        assert driver["identity"] == {
            "method_family_id": family,
            "implementation_sha256": implementation,
        }
        compiled[component_id] = driver

    for driver in compiled.values():
        shape = driver["shape"]
        trials = driver["trials"]
        if shape == "paired_control_treatment":
            assert [(trial["arm"], trial["seed"]) for trial in trials] == [
                (arm, seed) for seed in range(3) for arm in ("control", "treatment")
            ]
            assert driver["metric_paths"] == {"metrics.quality_score": "quality_score"}
        else:
            assert [trial["seed"] for trial in trials] == list(range(5))
        if shape == "checkpoint_audit":
            assert driver["metric_paths"] == {
                "audit.max_integrity_violation": "max_integrity_violation"
            }
            assert driver["audit_path"] == "audit"
            assert driver["checkpoint_resource"].endswith("-final-checkpoints")
        elif shape == "seeded_trials":
            assert driver["metric_paths"] == {"metrics.quality_score": "quality_score"}
            assert driver["per_class_path"] == "metrics.per_class"
            assert driver["checkpoint_torch_load"] is True

        for trial in trials:
            argv = trial["target_argv"]
            flags = argv[::2]
            tokens = dict(zip(flags, argv[1::2], strict=True))
            assert len(flags) == len(set(flags))
            assert trial["module"] == (
                "research.audit" if shape == "checkpoint_audit" else "research.train"
            )
            assert tokens["--dataset"] == driver["dataset"]
            assert tokens["--model"] == trial["model"]
            assert tokens["--seed"] == str(trial["seed"])
            assert tokens["--mode"] == (
                "audit" if shape == "checkpoint_audit" else "train"
            )
            assert tokens["--out"] == "{raw_result}"
            assert tokens["--device"] == "cuda"
            if shape == "checkpoint_audit":
                assert tokens["--checkpoint"] == "{checkpoint}"
                assert trial["checkpoint"].startswith("/hypoloom-inputs/")
            elif shape == "seeded_trials":
                assert tokens["--save-ckpt"] == "{checkpoint}"
                assert trial["checkpoint"].startswith("raw-checkpoints/")
            else:
                expected_model = (
                    "baseline_model" if trial["arm"] == "control" else "candidate_model"
                )
                assert trial["model"] == expected_model
                assert tokens["--config"].endswith(
                    f"{expected_model}_{str(driver['dataset']).lower()}.yaml"
                )
            if shape != "checkpoint_audit":
                assert tokens["--cache"].startswith("/hypoloom-data/")
                assert tokens["--split-manifest"].startswith("/hypoloom-data/")


def test_admission_does_not_modify_the_six_profile_files(
    component_batch_profile: Path,
    tmp_path: Path,
) -> None:
    profile = _copy_profile(component_batch_profile, tmp_path / "batch")
    before = _snapshot(profile)

    report = admit_profile(profile, run_id="read-only")

    assert report.admitted, report.to_dict()
    assert _snapshot(profile) == before
    assert {path.name for path in profile.iterdir()} == set(PROFILE_FILES)


def test_contract_rejects_duplicate_component_names(
    component_batch_profile: Path,
    tmp_path: Path,
) -> None:
    profile = _copy_profile(component_batch_profile, tmp_path / "batch")

    def mutate(document: dict[str, object]) -> None:
        primary = document["success_criteria"]["primary"]  # type: ignore[index]
        primary[1]["name"] = primary[0]["name"]

    _mutate_methodology(profile, mutate)
    report = admit_profile(profile, run_id="duplicate-component")

    assert "contract_duplicate_component" in {b.code for b in report.blockers}


def test_contract_rejects_unknown_fields_and_references(
    component_batch_profile: Path,
    tmp_path: Path,
) -> None:
    source = component_batch_profile
    cases: list[tuple[str, object, str]] = []

    def unknown_field(document: dict[str, object]) -> None:
        document["success_criteria"]["primary"][0]["free_text_lifecycle"] = "ready"  # type: ignore[index]

    cases.append(("unknown-field", unknown_field, "contract_unknown_field"))

    def unknown_owner(document: dict[str, object]) -> None:
        document["component_owners"]["primary.dataset_alpha_final_score"] = "ghost"  # type: ignore[index]

    cases.append(("unknown-owner", unknown_owner, "contract_unknown_owner"))

    def unknown_protocol(document: dict[str, object]) -> None:
        document["success_criteria"]["primary"][0]["experiment_kind"] = "ghost"  # type: ignore[index]

    cases.append(("unknown-protocol", unknown_protocol, "contract_unknown_protocol"))

    def unknown_input(document: dict[str, object]) -> None:
        document["success_criteria"]["must"][2]["required_inputs"][0]["produced_by"] = (
            "primary.ghost"  # type: ignore[index]
        )

    cases.append(("unknown-input", unknown_input, "contract_unknown_input_producer"))

    for directory, mutation, expected in cases:
        profile = _copy_profile(source, tmp_path / directory)
        _mutate_methodology(profile, mutation)
        report = admit_profile(profile, run_id=directory)
        assert expected in {b.code for b in report.blockers}, report.to_dict()


def test_contract_rejects_dependency_cycles_and_missing_producer_artifacts(
    component_batch_profile: Path,
    tmp_path: Path,
) -> None:
    source = component_batch_profile
    cyclic = _copy_profile(source, tmp_path / "cycle")

    def add_cycle(document: dict[str, object]) -> None:
        document["dependencies"]["must.mechanism_search_recorded"] = [  # type: ignore[index]
            "must.method_implemented"
        ]

    _mutate_methodology(cyclic, add_cycle)
    cycle_report = admit_profile(cyclic, run_id="cycle")
    assert "contract_dependency_cycle" in {b.code for b in cycle_report.blockers}

    missing = _copy_profile(source, tmp_path / "artifact")

    def missing_artifact(document: dict[str, object]) -> None:
        document["success_criteria"]["must"][2]["required_inputs"][0]["artifact"] = (
            "ghost"  # type: ignore[index]
        )

    _mutate_methodology(missing, missing_artifact)
    missing_report = admit_profile(missing, run_id="artifact")
    assert "contract_input_artifact_missing" in {
        b.code for b in missing_report.blockers
    }
