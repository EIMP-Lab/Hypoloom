from __future__ import annotations

import ast
import copy
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from hypoloom.cli import main as cli_main
from hypoloom.domain import ResearchContract
from hypoloom.profile_admission import (
    PROFILE_FILES,
    PROFILE_V3_FILES,
    admit_profile,
    compile_profile,
)
from hypoloom.profile_language import (
    resolve_profile_language,
)


_REPOSITORY = Path(__file__).resolve().parents[2]
_EXPERT_LOOP_PILOT = (
    _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot"
)
_EXPERT_LOOP_PILOT_V3 = (
    _REPOSITORY / "tests" / "fixtures" / "profiles" / "expert-loop-pilot-v3"
)


def _git(target: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _materialize_expert_loop_pilot(root: Path) -> tuple[Path, Path, Path]:
    target = root / "target"
    target.mkdir()
    (target / "train.py").write_text(
        "# Bound sample entrypoint; profile admission does not execute it.\n",
        encoding="utf-8",
    )
    _git(target, "init", "--quiet")
    _git(target, "add", "train.py")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Tests",
        "-c",
        "user.email=tests@invalid.example",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    read_root = root / "data"
    read_root.mkdir()
    profile = root / "profile"
    profile.mkdir()
    replacements = {
        "/REPLACE_ME/expert-loop-pilot-target": str(target.resolve()),
        "/REPLACE_ME/expert-loop-pilot-data": str(read_root.resolve()),
    }
    for name in PROFILE_FILES:
        text = (_EXPERT_LOOP_PILOT / name).read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        (profile / name).write_text(text, encoding="utf-8")
    return profile, target.resolve(), read_root.resolve()


def _materialize_expert_loop_pilot_v3(root: Path) -> tuple[Path, Path, Path]:
    target = root / "target-v3"
    target.mkdir()
    (target / "train.py").write_text("# profile-v3 target\n", encoding="utf-8")
    _git(target, "init", "--quiet")
    _git(target, "add", "train.py")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Tests",
        "-c",
        "user.email=tests@invalid.example",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    read_root = root / "data-v3"
    read_root.mkdir()
    profile = root / "profile-v3"
    profile.mkdir()
    replacements = {
        "/REPLACE_ME/expert-loop-pilot-target": str(target.resolve()),
        "/REPLACE_ME/expert-loop-pilot-data": str(read_root.resolve()),
    }
    for name in PROFILE_V3_FILES:
        text = (_EXPERT_LOOP_PILOT_V3 / name).read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        (profile / name).write_text(text, encoding="utf-8")
    return profile, target.resolve(), read_root.resolve()


def test_profile_v3_compiles_six_yaml_sources_to_the_existing_contract(
    tmp_path: Path,
) -> None:
    profile, target, read_root = _materialize_expert_loop_pilot_v3(tmp_path)

    report = admit_profile(profile, run_id="profile-v3")

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    assert {path.name for path in profile.iterdir()} == set(PROFILE_V3_FILES)
    contract = report.create_run_payload["research_contract"]
    assert contract["schema_version"] == 1
    assert contract["goal"]["objective"].startswith("Image classification\n\n")
    assert contract["goal"]["language"] == "en"
    assert contract["components"][0]["id"] == "primary.validation_accuracy"
    assert contract["components"][0]["owner"] == "ml-researcher"
    assert contract["components"][0]["proposers"] == ["ml-researcher"]
    assert contract["roles"][0]["focus"].startswith("Background:\nMachine learning")
    assert contract["budget"]["agent_invocation"]["token_enforcement_mode"] == "observe"
    assert {"run_tokens", "per_expert_tokens", "max_turns"}.isdisjoint(
        contract["budget"]
    )
    assert {"per_expert_tokens", "max_turns"}.isdisjoint(
        contract["roles"][0]["budget"]
    )
    assert contract["budget"]["agent_invocation"]["max_turns"] == 24
    assert contract["methodology"]["controls"] == {
        "domain_grounding": "required",
        "experiment_isolation": "git_authoring_worktree",
        "observer": "disabled",
        "research_loop": "expert-six-node/1",
        "language_routes": {"overrides": {}},
    }
    assert report.create_run_payload["execution"]["target"]["checkout"] == str(target)
    assert report.create_run_payload["execution"]["read_roots"] == [str(read_root)]


@pytest.mark.parametrize("destination,admitted", [("train.py", True), ("../data", False)])
def test_admission_checks_exact_committed_tree_without_changing_source(tmp_path, monkeypatch, capsys, destination, admitted):
    import tempfile
    profile, target, read_root = _materialize_expert_loop_pilot_v3(tmp_path)
    (target / "resource-link").symlink_to(destination)
    _git(target, "add", "resource-link")
    _git(target, "-c", "user.name=Tests", "-c", "user.email=tests@invalid.example",
         "commit", "--quiet", "-m", "committed link")
    before = (target / ".git/index").read_bytes()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    report = admit_profile(profile, run_id="tree-admission")
    assert report.admitted is admitted, report.to_dict()
    assert (target / ".git/index").read_bytes() == before
    assert (target / "resource-link").readlink().as_posix() == destination
    assert list(scratch.iterdir()) == []
    if admitted:
        assert report.to_dict()["resource_visibility"]["read_roots"] == [
            {"host": str(read_root), "sandbox": str(read_root)}]
    else:
        assert report.create_run_payload is None
        assert any(item.code == "target_binding_failed" and "resource-link" in item.message
                   and "escapes" in item.message for item in report.blockers)
        monkeypatch.setattr("hypoloom.cli._submit", lambda *args, **kwargs: pytest.fail("incompatible target reached Host"))
        assert cli_main(["profile-check", profile.name, "--profiles-root", str(tmp_path),
                         "--run-id", "tree-admission", "--cpu-only"]) == 2
        assert any(item["code"] == "target_binding_failed" for item in json.loads(capsys.readouterr().err)["blockers"])
        assert list(scratch.iterdir()) == []


def test_profile_v3_folds_background_and_principles_into_role_focus(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    experts = yaml.safe_load((profile / "experts.yaml").read_text(encoding="utf-8"))

    report = admit_profile(profile, run_id="profile-v3-focus")

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    roles = report.create_run_payload["research_contract"]["roles"]
    by_id = {role["id"]: role for role in roles}
    assert set(by_id) == {role["id"] for role in experts["roles"]}
    for source in experts["roles"]:
        focus = by_id[source["id"]]["focus"]
        assert source["background"].strip() in focus
        for principle in source["reasoning_principles"]:
            assert f"- {principle}" in focus


def test_profile_v3_balances_required_completion_signals_across_experts(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    experts_path = profile / "experts.yaml"
    experts = yaml.safe_load(experts_path.read_text(encoding="utf-8"))
    experts["roles"] = [
        {
            "id": role_id,
            "background": f"General research background for {role_id}.",
            "reasoning_principles": ["Test falsifiable claims with recorded evidence."],
        }
        for role_id in ("role-a", "role-b", "role-c")
    ]
    experts_path.write_text(yaml.safe_dump(experts, sort_keys=False), encoding="utf-8")

    methodology_path = profile / "methodology.yaml"
    methodology = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    role_budget = methodology["budget"]["roles"].pop("ml-researcher")
    methodology["budget"]["roles"] = {
        role_id: copy.deepcopy(role_budget)
        for role_id in ("role-a", "role-b", "role-c")
    }
    methodology_path.write_text(
        yaml.safe_dump(methodology, sort_keys=False), encoding="utf-8"
    )

    goal_path = profile / "goal.yaml"
    goal = yaml.safe_load(goal_path.read_text(encoding="utf-8"))
    prototype = goal["completion"]["signals"][0]
    goal["completion"]["signals"] = []
    for signal_id, tier in (
        ("required_result_a", "must"),
        ("primary_result", "primary"),
        ("required_result_b", "must"),
    ):
        signal = copy.deepcopy(prototype)
        signal["id"] = signal_id
        signal["tier"] = tier
        goal["completion"]["signals"].append(signal)
    goal_path.write_text(yaml.safe_dump(goal, sort_keys=False), encoding="utf-8")
    before = {
        name: (profile / name).read_bytes()
        for name in PROFILE_V3_FILES
    }

    report = admit_profile(profile, run_id="profile-v3-balanced")

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    components = report.create_run_payload["research_contract"]["components"]
    assert [(item["id"], item["owner"], item["proposers"]) for item in components] == [
        ("primary.primary_result", "role-b", ["role-b"]),
        ("must.required_result_a", "role-a", ["role-a"]),
        ("must.required_result_b", "role-c", ["role-c"]),
    ]
    assert before == {name: (profile / name).read_bytes() for name in PROFILE_V3_FILES}


@pytest.mark.parametrize("schema_version", (1, 2, 5, 3.0, True))
def test_profile_v3_rejects_non_v3_schema_versions(
    tmp_path: Path,
    schema_version: object,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    path = profile / "methodology.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["schema_version"] = schema_version
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-version")

    assert not report.admitted
    assert (
        "profile_contract_version_unsupported",
        "methodology.yaml.schema_version",
    ) in {(blocker.code, blocker.location) for blocker in report.blockers}


def test_profile_v3_rejects_mixed_legacy_and_yaml_layouts(tmp_path: Path) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    (profile / "goal.md").write_text("legacy duplicate\n", encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-mixed-layout")

    assert not report.admitted
    assert ("unexpected_profile_file", "goal.md") in {
        (blocker.code, blocker.location) for blocker in report.blockers
    }


def test_profile_v3_reports_compiler_errors_at_the_yaml_source_path(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    path = profile / "goal.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["completion"]["signals"][0]["result"]["direction"] = "sideways"
    document["completion"]["evaluation_protocols"][
        "image-classification-validation"
    ]["requirements"]["unknown_requirement"] = True
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-source-location")

    assert not report.admitted
    blockers = {(item.code, item.location) for item in report.blockers}
    assert (
        "contract_invalid_result",
        "goal.yaml.completion.signals[0]",
    ) in blockers
    assert (
        "contract_unknown_field",
        "goal.yaml.completion.evaluation_protocols."
        "image-classification-validation.requirements.unknown_requirement",
    ) in blockers


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value", "code", "location_suffix"),
    (
        (
            "result_kind",
            [],
            "contract_invalid_protocol",
            "result_kind",
        ),
        (
            "required_metrics",
            None,
            "contract_invalid_value",
            "requirements.required_metrics",
        ),
        (
            "required_artifacts",
            None,
            "contract_invalid_value",
            "requirements.required_artifacts",
        ),
        (
            "required_artifacts",
            [[]],
            "contract_invalid_value",
            "requirements.required_artifacts",
        ),
    ),
)
def test_profile_v3_protocol_type_errors_fail_closed_without_crashing(
    tmp_path: Path,
    invalid_field: str,
    invalid_value: object,
    code: str,
    location_suffix: str,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    path = profile / "goal.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    protocol = document["completion"]["evaluation_protocols"][
        "image-classification-validation"
    ]
    if invalid_field == "result_kind":
        protocol[invalid_field] = invalid_value
    else:
        protocol["requirements"][invalid_field] = invalid_value
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-invalid-protocol-type")

    assert not report.admitted
    expected_location = (
        "goal.yaml.completion.evaluation_protocols."
        f"image-classification-validation.{location_suffix}"
    )
    assert (code, expected_location) in {
        (item.code, item.location) for item in report.blockers
    }


def test_legacy_language_text_is_never_guessed_as_a_v3_route_policy() -> None:
    collision = 'profile-language-routes/1\n{"default":"zh-CN","overrides":{}}'

    assert resolve_profile_language(collision, None, "grounding") is None


def test_legacy_profile_cannot_author_the_v3_typed_language_control(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    path = profile / "methodology.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["hg"] = {"language_routes": {"overrides": {"grounding": "en"}}}
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="legacy-typed-language-control")

    assert not report.admitted
    assert (
        "profile_contract_version_unsupported",
        "methodology.yaml.hg.language_routes",
    ) in {(item.code, item.location) for item in report.blockers}


def test_research_contract_rejects_malformed_typed_language_routes(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    report = admit_profile(profile, run_id="profile-v3-language-contract")
    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    contract = copy.deepcopy(report.create_run_payload["research_contract"])
    contract["methodology"]["controls"]["language_routes"] = {
        "overrides": {"grounding": 3}
    }

    with pytest.raises(
        ValueError,
        match="research_contract.methodology.controls.language_routes is invalid",
    ):
        ResearchContract.from_mapping(contract)


@pytest.mark.parametrize(
    ("file_name", "field", "code"),
    (
        ("language.yaml", "terminology", "profile_v3_unknown_field"),
        ("experts.yaml", "budgets", "profile_v3_unknown_field"),
        ("methodology.yaml", "success_criteria", "profile_v3_unknown_field"),
    ),
)
def test_profile_v3_keeps_file_responsibilities_closed(
    tmp_path: Path,
    file_name: str,
    field: str,
    code: str,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    path = profile / file_name
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document[field] = {}
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-closed-fields")

    assert not report.admitted
    assert (code, f"{file_name}.{field}") in {
        (blocker.code, blocker.location) for blocker in report.blockers
    }


def test_profile_v3_rejects_non_agent_language_routes(tmp_path: Path) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    path = profile / "language.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["overrides"]["unknown_agent_node"] = "en"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-language-route")

    assert not report.admitted
    assert (
        "profile_v3_unknown_route",
        "language.yaml.overrides.unknown_agent_node",
    ) in {(blocker.code, blocker.location) for blocker in report.blockers}


def test_profile_v3_invalid_default_language_has_one_accurate_diagnostic(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    path = profile / "language.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["default"] = "not valid language"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-invalid-default-language")

    assert not report.admitted
    language_blockers = [
        (item.code, item.location)
        for item in report.blockers
        if item.location.startswith("language.yaml")
    ]
    assert language_blockers == [
        ("profile_v3_invalid_language", "language.yaml.default")
    ]


@pytest.mark.parametrize("invalid_tier", ([], {}))
def test_profile_v3_reports_unhashable_signal_tiers_as_blockers(
    tmp_path: Path,
    invalid_tier: object,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    path = profile / "goal.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["completion"]["signals"][0]["tier"] = invalid_tier
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-invalid-tier")

    assert not report.admitted
    assert (
        "profile_v3_invalid_value",
        "goal.yaml.completion.signals[0].tier",
    ) in {(item.code, item.location) for item in report.blockers}


def test_malformed_v3_yaml_does_not_emit_a_legacy_version_diagnostic(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    (profile / "experts.yaml").write_text("[]\n", encoding="utf-8")

    report = admit_profile(profile, run_id="profile-v3-malformed-root")

    assert not report.admitted
    blockers = {(item.code, item.location) for item in report.blockers}
    assert ("invalid_yaml_root", "experts.yaml") in blockers
    assert not any(
        item.code == "profile_contract_version_unsupported"
        for item in report.blockers
    )


def test_profile_v3_rejects_profile_file_symlinks(tmp_path: Path) -> None:
    profile, _, _ = _materialize_expert_loop_pilot_v3(tmp_path)
    language = profile / "language.yaml"
    target = tmp_path / "language-target.yaml"
    language.rename(target)
    language.symlink_to(target)

    report = admit_profile(profile, run_id="profile-v3-symlink")

    assert not report.admitted
    assert ("profile_file_not_regular", "language.yaml") in {
        (blocker.code, blocker.location) for blocker in report.blockers
    }


def _write_minimal_demo(root: Path) -> Path:
    profile = root / "component-demo"
    profile.mkdir()
    (profile / "goal.md").write_text(
        "Reach a deterministic scalar score without external inputs.\n",
        encoding="utf-8",
    )
    (profile / "experts.yaml").write_text(
        "enabled: [analyst, critic]\n",
        encoding="utf-8",
    )
    (profile / "methodology.yaml").write_text(
        """type: hg
success_criteria:
  primary:
    metric: score
    direction: maximize
    target: 1.25
budget:
  agent_invocation:
    max_turns: 2
    max_tokens: 512
    max_output_bytes: 16384
    wall_seconds: 5
    max_cost: "0"
""",
        encoding="utf-8",
    )
    (profile / "language.md").write_text("", encoding="utf-8")
    (profile / "notify.yaml").write_text("{}\n", encoding="utf-8")
    (profile / "safety.yaml").write_text("{}\n", encoding="utf-8")
    assert {path.name for path in profile.iterdir()} == set(PROFILE_FILES)
    return profile


def test_legacy_profile_file_symlink_keeps_the_pre_v3_admission_behavior(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    language = profile / "language.md"
    target = tmp_path / "legacy-language.md"
    language.rename(target)
    language.symlink_to(target)

    report = admit_profile(profile, run_id="legacy-symlink-compatible")

    assert report.admitted, report.to_dict()


def test_minimal_hypoloom_demo_compiles_to_exact_create_run_payload(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_demo(tmp_path)

    report = admit_profile(
        profile,
        run_id="demo-001",
        delay_seconds=0.125,
    )

    assert report.admitted
    assert report.blockers == ()
    assert report.create_run_payload is not None
    assert set(report.create_run_payload) == {
        "run_id",
        "research_contract",
        "execution",
    }
    assert report.create_run_payload["run_id"] == "demo-001"
    assert report.create_run_payload["execution"] == {
        "allow_gpu": False,
        "delay_seconds": 0.125,
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
    assert contract["goal"] == {
        "objective": "Reach a deterministic scalar score without external inputs.",
        "language": "",
        "verdict_rule": "required_conjunction",
        "summary_metric": {
            "name": "score",
            "direction": "MAXIMIZE",
            "target": "1.25",
            "display_only": True,
        },
    }
    assert contract["components"] == [
        {
            "id": "primary.score",
            "tier": "primary",
            "name": "score",
            "owner": "analyst",
            "proposers": ["analyst"],
            "dependencies": [],
            "required_inputs": [],
            "protocol": None,
            "result": {
                "kind": "metric",
                "metric": "score",
                "direction": "MAXIMIZE",
                "target": "1.25",
            },
            "seeds": [],
            "required_artifacts": [],
        }
    ]
    assert (
        compile_profile(
            profile,
            run_id="demo-001",
            delay_seconds=0.125,
        )
        == report.create_run_payload
    )


def test_compiled_payload_has_no_parallel_scalar_contract(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    payload = compile_profile(profile, run_id="host-demo")
    assert set(payload) == {"run_id", "research_contract", "execution"}
    assert payload["research_contract"]["goal"]["summary_metric"] == {
        "name": "score",
        "direction": "MAXIMIZE",
        "target": "1.25",
        "display_only": True,
    }
    assert payload["research_contract"]["components"][0]["result"] == {
        "kind": "metric",
        "metric": "score",
        "direction": "MAXIMIZE",
        "target": "1.25",
    }
    assert payload["execution"] == {
        "allow_gpu": False,
        "delay_seconds": 0.0,
        "target": None,
        "runtime_prefix": None,
        "read_roots": [],
    }


def test_tracked_hypoloom_demo_is_admitted_without_profile_semantic_loss() -> None:
    root = Path(__file__).resolve().parents[2]

    report = admit_profile(
        root / "tests" / "fixtures" / "profiles" / "component-demo",
        run_id="tracked-component-demo",
    )

    assert report.admitted
    assert report.blockers == ()
    assert report.create_run_payload is not None
    assert set(report.create_run_payload) == {
        "run_id",
        "research_contract",
        "execution",
    }
    contract = report.create_run_payload["research_contract"]
    assert [role["id"] for role in contract["roles"]] == [
        "scientist-a",
        "scientist-b",
        "scientist-c",
    ]
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
    assert report.create_run_payload["execution"] == {
        "allow_gpu": False,
        "delay_seconds": 0.0,
        "target": None,
        "runtime_prefix": None,
        "read_roots": [],
    }


def test_unbound_expert_loop_pilot_exposes_only_path_binding_blockers() -> None:
    report = admit_profile(
        _EXPERT_LOOP_PILOT,
        run_id="unbound-expert-loop-pilot",
    )

    assert not report.admitted
    assert report.create_run_payload is None
    assert {blocker.code for blocker in report.blockers} == {
        "target_binding_failed",
        "read_root_binding_failed",
    }


def test_bound_expert_loop_pilot_admits_exact_successor_contract(
    tmp_path: Path,
) -> None:
    profile, target, read_root = _materialize_expert_loop_pilot(tmp_path)

    report = admit_profile(profile, run_id="expert-loop-pilot")

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    payload = report.create_run_payload
    contract = payload["research_contract"]
    assert [role["id"] for role in contract["roles"]] == ["ml-researcher"]
    assert len(contract["components"]) == 1
    component = contract["components"][0]
    assert component["id"] == "primary.validation_accuracy"
    assert component["result"] == {
        "kind": "metric",
        "metric": "accuracy",
        "direction": "MAXIMIZE",
        "target": "0.55",
        "dataset": "IMAGE_CLASSIFICATION_PILOT",
        "setting": "frozen_validation_split",
        "baseline": "0.5",
    }
    assert contract["methodology"]["controls"] == {
        "domain_grounding": "required",
        "experiment_isolation": "git_authoring_worktree",
        "observer": "disabled",
        "research_loop": "expert-six-node/1",
    }
    assert {
        name: contract["budget"][name]
        for name in (
            "max_retries",
            "max_infrastructure_retries",
            "max_process_lost_retries",
        )
    } == {
        "max_retries": 5,
        "max_infrastructure_retries": 2,
        "max_process_lost_retries": 2,
    }
    assert payload["execution"]["target"] == {
        "checkout": str(target),
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip(),
    }
    assert payload["execution"]["read_roots"] == [str(read_root)]


@pytest.mark.parametrize(
    "field",
    ("research_loop", "domain_grounding", "experiment_isolation", "observer"),
)
def test_expert_loop_profile_v2_requires_every_successor_control(
    tmp_path: Path,
    field: str,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot(tmp_path)
    methodology_path = profile / "methodology.yaml"
    document = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    del document["hg"][field]
    methodology_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    report = admit_profile(profile, run_id=f"missing-{field}")

    assert not report.admitted
    assert report.create_run_payload is None
    assert (
        "successor_profile_contract_invalid",
        f"methodology.yaml.hg.{field}",
    ) in {(blocker.code, blocker.location) for blocker in report.blockers}


def _add_enabled_reviewer(profile: Path) -> None:
    experts_path = profile / "experts.yaml"
    experts = yaml.safe_load(experts_path.read_text(encoding="utf-8"))
    experts["enabled"].append("reviewer")
    experts["focus"]["reviewer"] = "Own one complete expert loop."
    experts["autonomy"]["reviewer"] = "evidence_first"
    experts["budgets"]["reviewer"] = {
        "per_expert_tokens": 20000,
        "max_experiments": 2,
        "max_papers": 4,
    }
    experts_path.write_text(
        yaml.safe_dump(experts, sort_keys=False),
        encoding="utf-8",
    )


def test_expert_loop_v2_requires_owner_as_the_only_required_component_proposer(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot(tmp_path)
    _add_enabled_reviewer(profile)
    methodology_path = profile / "methodology.yaml"
    methodology = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    methodology["component_proposers"]["primary.validation_accuracy"] = [
        "reviewer"
    ]
    methodology_path.write_text(
        yaml.safe_dump(methodology, sort_keys=False),
        encoding="utf-8",
    )

    report = admit_profile(profile, run_id="owner-proposer-mismatch")

    assert not report.admitted
    assert (
        "expert_loop_owner_proposer_mismatch",
        "methodology.yaml.component_proposers.primary.validation_accuracy",
    ) in {(blocker.code, blocker.location) for blocker in report.blockers}


def test_expert_loop_v2_requires_every_enabled_role_to_own_required_work(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot(tmp_path)
    _add_enabled_reviewer(profile)

    report = admit_profile(profile, run_id="reviewer-without-component")

    assert not report.admitted
    assert "expert_loop_role_without_required_component" in {
        blocker.code for blocker in report.blockers
    }
    assert "expert_loop_owner_proposer_mismatch" not in {
        blocker.code for blocker in report.blockers
    }


def test_expert_loop_v2_reserves_one_paper_for_each_required_stratum(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot(tmp_path)
    experts_path = profile / "experts.yaml"
    experts = yaml.safe_load(experts_path.read_text(encoding="utf-8"))
    experts["budgets"]["ml-researcher"]["max_papers"] = 2
    experts_path.write_text(
        yaml.safe_dump(experts, sort_keys=False), encoding="utf-8",
    )

    report = admit_profile(profile, run_id="literature-budget-too-small")

    assert not report.admitted
    assert (
        "expert_loop_literature_budget_too_small",
        "experts.yaml.budgets.ml-researcher.max_papers",
    ) in {(blocker.code, blocker.location) for blocker in report.blockers}


def test_expert_loop_v2_grounding_budget_is_support_only_not_an_enabled_role(
    tmp_path: Path,
) -> None:
    profile, _, _ = _materialize_expert_loop_pilot(tmp_path)
    experts_path = profile / "experts.yaml"
    experts = yaml.safe_load(experts_path.read_text(encoding="utf-8"))
    experts["budgets"]["grounding-agent"] = {
        "per_expert_tokens": 20000,
        "max_experiments": 1,
        "max_papers": 4,
    }
    experts_path.write_text(
        yaml.safe_dump(experts, sort_keys=False),
        encoding="utf-8",
    )

    report = admit_profile(profile, run_id="grounding-support-budget")

    assert report.admitted, report.to_dict()


def test_git_authoring_worktree_requires_profile_contract_v2(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    methodology_path = profile / "methodology.yaml"
    document = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    document["hg"] = {"experiment_isolation": "git_authoring_worktree"}
    methodology_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    report = admit_profile(profile, run_id="v1-successor-isolation")

    assert not report.admitted
    assert (
        "successor_profile_version_required",
        "methodology.yaml.schema_version",
    ) in {(blocker.code, blocker.location) for blocker in report.blockers}


def test_expert_loop_profile_requires_clean_git_target(tmp_path: Path) -> None:
    profile, target, _ = _materialize_expert_loop_pilot(tmp_path)
    (target / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    report = admit_profile(profile, run_id="dirty-expert-loop-target")

    assert not report.admitted
    assert (
        "target_binding_failed",
        "methodology.yaml.runtime.target_repo",
    ) in {(blocker.code, blocker.location) for blocker in report.blockers}


def test_complex_profile_fails_closed_when_required_contract_fields_are_absent(
    tmp_path: Path,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    (profile / "methodology.yaml").write_text(
        """type: hg
success_criteria:
  primary:
    - {metric: meld_wf1, direction: maximize, target: 0.65}
    - {metric: iemocap_wf1, direction: maximize, target: 0.67}
  must:
    - {name: paired_screen, required_artifacts: [checkpoints]}
component_proposers:
  primary.meld_wf1: [analyst, critic]
dependencies:
  primary.meld_wf1: [must.paired_screen]
experiments:
  protocols: {paired: {result_kind: metric}}
delivery_contracts:
  enabled: true
runtime:
  mode: reference
budget:
  max_experiments: 36
  agent_invocation:
    max_turns: 2
    max_tokens: 512
    max_output_bytes: 16384
    wall_seconds: 5
    max_cost: "0"
""",
        encoding="utf-8",
    )

    report = admit_profile(profile, run_id="complex")
    codes = {blocker.code for blocker in report.blockers}
    assert {
        "contract_invalid_result",
        "contract_missing_owner",
    } <= codes
    assert report.create_run_payload is None


@pytest.mark.parametrize(
    ("agent_invocation", "expected_location"),
    [
        (None, "methodology.yaml.budget.agent_invocation"),
        (
            {
                "max_turns": 2,
                "max_tokens": 512,
                "max_output_bytes": 16_384,
                "wall_seconds": 5,
            },
            "methodology.yaml.budget.agent_invocation.max_cost",
        ),
        (
            {
                "max_turns": 2,
                "max_tokens": 512,
                "max_output_bytes": 16_384,
                "wall_seconds": 5,
                "max_cost": "0",
                "max_input_bytes": 65_536,
            },
            "methodology.yaml.budget.agent_invocation.max_input_bytes",
        ),
        (
            {
                "max_turns": 2,
                "max_tokens": 512,
                "max_output_bytes": 16_384,
                "wall_seconds": 5,
                "max_cost": 0,
            },
            "methodology.yaml.budget.agent_invocation.max_cost",
        ),
        (
            {
                "max_turns": 2,
                "max_tokens": 512,
                "max_output_bytes": 16_384,
                "wall_seconds": 5,
                "max_cost": "0.00",
            },
            "methodology.yaml.budget.agent_invocation.max_cost",
        ),
    ],
)
def test_agent_invocation_budget_is_required_exact_and_canonical(
    tmp_path: Path,
    agent_invocation: object,
    expected_location: str,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    methodology_path = profile / "methodology.yaml"
    document = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    if agent_invocation is None:
        del document["budget"]["agent_invocation"]
    else:
        document["budget"]["agent_invocation"] = agent_invocation
    methodology_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    report = admit_profile(profile, run_id="invalid-agent-budget")

    assert not report.admitted
    assert report.create_run_payload is None
    assert expected_location in {blocker.location for blocker in report.blockers}


@pytest.mark.parametrize("mode", ("enforce", "observe"))
def test_agent_token_enforcement_mode_is_profile_owned_and_frozen(
    tmp_path: Path,
    mode: str,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    methodology_path = profile / "methodology.yaml"
    document = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    document["budget"]["agent_invocation"]["token_enforcement_mode"] = mode
    methodology_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    report = admit_profile(profile, run_id=f"token-mode-{mode}")

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    assert (
        report.create_run_payload["research_contract"]["budget"]
        ["agent_invocation"]["token_enforcement_mode"]
        == mode
    )


def test_unknown_agent_token_enforcement_mode_is_rejected(tmp_path: Path) -> None:
    profile = _write_minimal_demo(tmp_path)
    methodology_path = profile / "methodology.yaml"
    document = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    document["budget"]["agent_invocation"]["token_enforcement_mode"] = "ignore"
    methodology_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    report = admit_profile(profile, run_id="invalid-token-mode")

    assert not report.admitted
    assert "methodology.yaml.budget.agent_invocation.token_enforcement_mode" in {
        blocker.location for blocker in report.blockers
    }


@pytest.mark.parametrize(
    "field",
    ("max_retries", "max_infrastructure_retries", "max_process_lost_retries"),
)
def test_retry_budgets_are_frozen_nonnegative_integers(
    tmp_path: Path,
    field: str,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    methodology_path = profile / "methodology.yaml"
    document = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    document["budget"][field] = 2
    methodology_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    report = admit_profile(profile, run_id=f"retry-budget-{field}")

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    assert report.create_run_payload["research_contract"]["budget"][field] == 2

    document["budget"][field] = -1
    methodology_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    invalid = admit_profile(profile, run_id=f"invalid-retry-budget-{field}")
    assert not invalid.admitted
    assert f"methodology.yaml.budget.{field}" in {
        blocker.location for blocker in invalid.blockers
    }


def test_role_cumulative_cap_cannot_be_below_one_invocation(tmp_path: Path) -> None:
    profile = _write_minimal_demo(tmp_path)
    (profile / "experts.yaml").write_text(
        """enabled: [analyst, critic]
budgets:
  analyst:
    per_expert_tokens: 256
    max_turns: 1
""",
        encoding="utf-8",
    )

    report = admit_profile(profile, run_id="invalid-role-budget")

    assert not report.admitted
    locations = {blocker.location for blocker in report.blockers}
    assert "experts.yaml.budgets.analyst.per_expert_tokens" in locations
    assert "experts.yaml.budgets.analyst.max_turns" in locations


def test_unknown_or_duplicate_profile_semantics_fail_closed(tmp_path: Path) -> None:
    profile = _write_minimal_demo(tmp_path)
    (profile / "experts.yaml").write_text(
        """enabled: [analyst]
enabled: [critic]
""",
        encoding="utf-8",
    )
    (profile / "extra-policy.yaml").write_text("authority: hidden\n", encoding="utf-8")

    report = admit_profile(profile, run_id="duplicate")

    assert {blocker.code for blocker in report.blockers} >= {
        "invalid_yaml",
        "unexpected_profile_file",
    }
    assert report.create_run_payload is None


def test_adapter_imports_no_legacy_hypoloom_runtime() -> None:
    source_path = (
        Path(__file__).resolve().parents[2]
        / "hypoloom"
        / "profile_admission.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "hypoloom" not in imported
    assert not any(name.startswith("hypoloom.") for name in imported)


@pytest.mark.parametrize("command", ["profile-check", "start"])
@pytest.mark.parametrize("options", [[], ["--cpu-only", "--allow-gpu"]])
def test_profile_cli_requires_exactly_one_resource_authorization(tmp_path, capsys, monkeypatch, command, options):
    def forbidden(*args, **kwargs):
        raise AssertionError("missing or conflicting resource choice must fail before admission or Host contact")

    monkeypatch.setattr("hypoloom.profile_admission.admit_profile", forbidden)
    monkeypatch.setattr("hypoloom.cli._submit", forbidden)
    with pytest.raises(SystemExit) as raised:
        cli_main([command, "profile", "--profiles-root", str(tmp_path), "--run-id", "explicit-mode", *options])
    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "--cpu-only" in error and "--allow-gpu" in error
    assert list(tmp_path.iterdir()) == []


def test_profile_check_cli_is_read_only_and_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _write_minimal_demo(tmp_path)

    def forbidden_submit(*args: object, **kwargs: object) -> int:
        raise AssertionError("profile-check must not submit to the Host")

    monkeypatch.setattr("hypoloom.cli._submit", forbidden_submit)
    assert (
        cli_main(
            [
                "profile-check",
                profile.name,
                "--profiles-root",
                str(tmp_path),
                "--run-id",
                "profile-check-001",
                "--cpu-only",
                "--process-delay",
                "0.125",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["admitted"] is True
    payload = report["create_run_payload"]
    assert set(payload) == {"run_id", "research_contract", "execution"}
    assert payload["run_id"] == "profile-check-001"
    assert payload["execution"] == {
        "allow_gpu": False,
        "delay_seconds": 0.125,
        "target": None,
        "runtime_prefix": None,
        "read_roots": [],
    }
    assert report["literature_preflight"] == {
        "adapter": "offline",
        "qualified": True,
        "network_probe_performed": False,
    }


def test_profile_check_openalex_preflight_reads_explicit_key_and_tools_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    key_path = tmp_path / "openalex.key"
    key_path.write_text("profile-check-secret\n", encoding="utf-8")
    key_path.chmod(0o600)
    pdfinfo = tmp_path / "pdfinfo"
    pdftotext = tmp_path / "pdftotext"
    for tool in (pdfinfo, pdftotext):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o700)

    def forbidden_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("profile-check must not make a network request")

    monkeypatch.setattr(
        "hypoloom.literature.StdlibOpenAlexTransport.get",
        forbidden_network,
    )

    assert (
        cli_main(
            [
                "profile-check",
                profile.name,
                "--profiles-root",
                str(tmp_path),
                "--run-id",
                "openalex-profile-check",
                "--cpu-only",
                "--literature-adapter",
                "openalex",
                "--openalex-api-key-file",
                str(key_path),
                "--literature-pdfinfo",
                str(pdfinfo),
                "--literature-pdftotext",
                str(pdftotext),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["admitted"] is True
    assert report["literature_preflight"] == {
        "adapter": "openalex",
        "credential_source": "file",
            "host": "api.openalex.org",
            "max_response_bytes": 2 * 1024 * 1024,
            "max_retries": 2,
            "retry_backoff_seconds": 0.25,
            "search_wall_seconds": 60.0,
            "network_probe_performed": False,
        "poppler": {
            "max_pdf_bytes": 16 * 1024 * 1024,
            "max_text_bytes": 2 * 1024 * 1024,
            "pdfinfo": str(pdfinfo),
            "pdftotext": str(pdftotext),
            "timeout_seconds": 15.0,
        },
        "qualified": True,
        "timeout_seconds": 15.0,
        "pdf_fallback": {
            "adapter": "semantic-scholar-doi",
            "credential_source": "none",
            "host": "api.semanticscholar.org",
            "max_response_bytes": 64 * 1024,
            "network_probe_performed": False,
            "timeout_seconds": 5.0,
        },
    }


def test_start_openalex_preflight_failure_blocks_submission(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _write_minimal_demo(tmp_path)

    def forbidden_submit(*args: object, **kwargs: object) -> int:
        raise AssertionError("failed literature preflight must not submit")

    monkeypatch.setattr("hypoloom.cli._submit", forbidden_submit)
    assert (
        cli_main(
            [
                "start",
                profile.name,
                "--profiles-root",
                str(tmp_path),
                "--state-root",
                str(tmp_path / "state"),
                "--run-id",
                "openalex-start-blocked",
                "--cpu-only",
                "--literature-adapter",
                "openalex",
            ]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().err)
    assert report["admitted"] is False
    assert report["literature_preflight"]["qualified"] is False
    assert report["literature_preflight"]["network_probe_performed"] is False
    assert "literature_preflight_failed" in {
        blocker["code"] for blocker in report["blockers"]
    }


@pytest.mark.parametrize("option,allow_gpu", [("--cpu-only", False), ("--allow-gpu", True)])
def test_start_submits_only_an_admitted_exact_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    allow_gpu: bool,
) -> None:
    profile = _write_minimal_demo(tmp_path)
    submitted: list[tuple[str, dict[str, object]]] = []

    def recording_submit(args: object, command: str, payload: dict[str, object]) -> int:
        submitted.append((command, payload))
        return 0

    monkeypatch.setattr("hypoloom.cli._submit", recording_submit)
    assert (
        cli_main(
            [
                "start",
                "--state-root",
                str(tmp_path / "unused-state"),
                "--request-id",
                "start-profile-001",
                profile.name,
                "--profiles-root",
                str(tmp_path),
                "--run-id",
                "profile-start-001",
                option,
            ]
        )
        == 0
    )
    assert submitted == [
        (
            "CREATE_RUN",
            compile_profile(
                profile,
                run_id="profile-start-001",
                allow_gpu=allow_gpu,
            ),
        )
    ]
    assert submitted[0][1]["execution"] == {
        "allow_gpu": allow_gpu,
        "delay_seconds": 0.0,
        "target": None,
        "runtime_prefix": None,
        "read_roots": [],
    }


def test_unbound_component_batch_template_is_not_directly_admissible() -> None:
    repository = Path(__file__).resolve().parents[2]

    report = admit_profile(
        repository / "tests" / "fixtures" / "profiles" / "component-batch",
        run_id="unbound-component-batch",
        allow_gpu=True,
    )

    assert not report.admitted
    assert report.create_run_payload is None
    assert {blocker.code for blocker in report.blockers} == {
        "target_binding_failed",
        "runtime_binding_failed",
        "read_root_binding_failed",
    }


def test_bound_component_batch_profile_uses_only_disposable_paths(
    component_batch_profile: Path,
) -> None:
    report = admit_profile(
        component_batch_profile,
        run_id="bound-component-batch",
        allow_gpu=True,
    )

    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    execution = report.create_run_payload["execution"]
    disposable_root = component_batch_profile.parent.resolve()
    assert Path(execution["target"]["checkout"]).is_relative_to(disposable_root)
    assert Path(execution["runtime_prefix"]).is_relative_to(disposable_root)
    assert execution["read_roots"]
    assert all(
        Path(root).is_relative_to(disposable_root) for root in execution["read_roots"]
    )


def test_start_submits_representable_component_batch_contract(
    component_batch_profile: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[tuple[str, dict[str, object]]] = []

    def recording_submit(args: object, command: str, payload: dict[str, object]) -> int:
        submitted.append((command, payload))
        return 0

    monkeypatch.setattr("hypoloom.cli._submit", recording_submit)
    assert (
        cli_main(
            [
                "start",
                "--state-root",
                str(tmp_path / "unused-state"),
                component_batch_profile.name,
                "--profiles-root",
                str(component_batch_profile.parent),
                "--run-id",
                "template-hypoloom-component",
                "--allow-gpu",
            ]
        )
        == 0
    )
    assert submitted == [
        (
            "CREATE_RUN",
            compile_profile(
                component_batch_profile,
                run_id="template-hypoloom-component",
                allow_gpu=True,
            ),
        )
    ]
