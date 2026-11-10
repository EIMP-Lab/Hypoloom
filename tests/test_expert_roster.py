"""Roster cardinality is independent of finite execution capacity."""
from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from hypoloom.domain import ResearchContract
from hypoloom.oracle import _read_goal_component_contract
from hypoloom.policy import ComponentSnapshot, PolicySnapshot, ProposalSnapshot, ResearchPolicy
from hypoloom.profile_admission import admit_profile
from hypoloom.profile_v4 import project_profile_v4


ROOT = Path(__file__).resolve().parents[1]


def roster_source(count, language="en"):
    source = {path.stem: yaml.safe_load(path.read_text()) for path in
              (ROOT / "profiles/templates" / ("multi-expert-" + language)).glob("*.yaml")}
    expert = source["experts"]["experts"][0]
    source["experts"]["experts"] = [
        {**copy.deepcopy(expert), "name": f"researcher-{i + 1}"} for i in range(count)]
    source["methodology"]["budget"].update(max_active_agents=1, max_active_processes=1,
        max_active_reviews=1, provider_max_in_flight=1)
    return source


@pytest.fixture(scope="module")
def bindings(tmp_path_factory):
    root = tmp_path_factory.mktemp("roster-profile")
    target, data = root / "target", root / "data"
    target.mkdir(); data.mkdir()
    (target / "README.md").write_text("Disposable roster admission target.\n")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Hypoloom Tests",
        "-c", "user.email=tests@invalid.example", "commit", "-qm", "fixture")):
        subprocess.run(["git", *args], cwd=target, check=True, capture_output=True)
    return root, target, data


def preview(source, bindings):
    root, target, data = bindings
    source = copy.deepcopy(source)
    source["methodology"]["runtime"].update(target_repo=str(target), conda_env=sys.prefix)
    source["safety"]["allow_read_paths"] = [str(data)]
    return admit_profile(root / "preview", run_id="roster-preview", source_texts={
        name + ".yaml": yaml.safe_dump(value, allow_unicode=True) for name, value in source.items()})


def audit_contract(document):
    issues = []
    value = _read_goal_component_contract(
        [{"fact_id": "goal", "goal_id": "goal", "fact_type": "GoalDefined"}],
        {"goal": {"research_contract": document}}, "component-evaluator/1", issues)
    return value, issues


@pytest.mark.parametrize("count", [1, 3, 4, 16, 64])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_admission_domain_policy_and_oracle_accept_nonempty_rosters(bindings, count, language):
    report = preview(roster_source(count, language), bindings)
    assert report.admitted, report.blockers
    contract = ResearchContract.from_mapping(report.create_run_payload["research_contract"])
    expected = tuple(f"researcher-{i + 1}" for i in range(count))
    assert contract.role_ids == expected
    assert tuple(item.owner_role for item in contract.components) == expected
    assert len(contract.components) == count
    assert contract.document["budget"]["max_active_agents"] == 1
    assert contract.document["budget"]["max_active_processes"] == 1
    assert contract.document["budget"]["provider_max_in_flight"] == 1
    view = PolicySnapshot(run_id="roster-preview", run_state="ACTIVE", run_revision=1,
        roster=expected, live_effect_limit=1, components=tuple(
            ComponentSnapshot(component_id=str(item.component_id), tier=item.tier.value,
                owner_role=item.owner_role, proposals=(ProposalSnapshot(role=item.owner_role),))
            for item in contract.components))
    assert ResearchPolicy().next_command(view).payload["role"] == expected[0]
    assert ResearchPolicy().next_command(replace(view, live_effect_count=1)) is None
    value, issues = audit_contract(contract.to_dict())
    assert value is not None and not issues, issues
    assert not list(bindings[0].rglob("state.sqlite"))


@pytest.mark.parametrize("invalid", ["empty", "duplicate", "blank", "wrong_type"])
def test_invalid_rosters_remain_rejected(bindings, invalid):
    source = roster_source(3)
    if invalid == "empty": source["experts"]["experts"] = []
    elif invalid == "duplicate": source["experts"]["experts"][1]["name"] = "researcher-1"
    elif invalid == "blank": source["experts"]["experts"][1]["name"] = " "
    else: source["experts"]["experts"] = "researcher-1"
    report = preview(source, bindings)
    assert not report.admitted and report.create_run_payload is None
    assert any("experts.yaml" in item.location for item in report.blockers)


@pytest.mark.parametrize("invalid", ["empty", "duplicate", "blank"])
def test_domain_policy_and_independent_audit_keep_identity_checks(bindings, invalid):
    report = preview(roster_source(3), bindings)
    assert report.admitted, report.blockers
    document = copy.deepcopy(report.create_run_payload["research_contract"])
    if invalid == "empty": document["roles"] = []
    elif invalid == "duplicate": document["roles"][1]["id"] = document["roles"][0]["id"]
    else: document["roles"][1]["id"] = " "
    with pytest.raises(ValueError): ResearchContract.from_mapping(document)
    value, issues = audit_contract(document)
    assert value is None and any(item.code == "component_contract_invalid" for item in issues)
    with pytest.raises(ValueError):
        PolicySnapshot(run_id="invalid-roster", run_state="ACTIVE", run_revision=1,
            roster=tuple(item["id"] for item in document["roles"]), components=(
                ComponentSnapshot(component_id="primary.goal", tier="primary", owner_role="researcher-1",
                    proposals=(ProposalSnapshot(role="researcher-1"),)),))


def test_omitted_capacity_defaults_keep_existing_fork_based_semantics():
    source = roster_source(16)
    budget = source["methodology"]["budget"]
    for field in ("max_active_agents", "max_active_processes", "max_active_reviews", "provider_max_in_flight"):
        budget.pop(field, None)
    result = project_profile_v4(**source)
    assert not result.issues, result.issues
    expected = 16 * source["experts"]["defaults"]["max_forks"]
    actual = result.projection.methodology["budget"]
    assert all(actual[field] == expected for field in (
        "max_active_agents", "max_active_processes", "max_active_reviews", "provider_max_in_flight"))
