import copy

import pytest
import yaml

from hypoloom.domain import ResearchContract
from hypoloom.profile_admission import admit_profile
from hypoloom.profile_v4 import project_profile_v4, validate_product_settings
from tests.runtime.test_profile_v4 import documents
from test_profile_admission import _materialize_expert_loop_pilot_v3


def commission():
    source = documents()
    source["goal"].pop("metric")
    source["goal"]["evaluation"] = "grounding"
    source["goal"]["description"] = "Preserve two strict held-out targets and causal constraints."
    return source


def test_commission_is_explicit_without_user_evaluation_dsl():
    source = commission()
    before = copy.deepcopy(source)
    projected = project_profile_v4(**source)
    assert not projected.issues
    assert projected.settings["goal"]["evaluation"] == "grounding"
    assert validate_product_settings(projected.settings) == projected.settings
    assert source == before


@pytest.mark.parametrize("change", [{"metric": {"name": "score", "direction": "maximize", "target": 1}},
    {"completion": {"rule": "required_conjunction"}}, {"evaluation": "automatic"},
    {"stop_conditions": {"target_metric": 1}}])
def test_commission_rejects_competing_or_unknown_acceptance_authorities(change):
    source = commission()
    source["goal"].update(change)
    assert project_profile_v4(**source).issues


def test_admission_persists_schema3_commission_without_mutating_old_contracts(tmp_path):
    profile, target, data = _materialize_expert_loop_pilot_v3(tmp_path)
    source = commission()
    source["methodology"]["runtime"]["target_repo"] = str(target)
    source["safety"]["allow_read_paths"] = [str(data)]
    source["safety"]["filesystem"]["protected_paths"] = [str(target)]
    source["safety"]["gpus"] = []
    source["notify"]["sinks"]["telegram"]["enabled"] = False
    for name, value in source.items():
        (profile / f"{name}.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
    report = admit_profile(profile, run_id="grounding-commission")
    assert report.admitted, report.to_dict()
    raw = report.create_run_payload["research_contract"]
    assert raw["schema_version"] == 3
    assert raw["goal"]["evaluation"] == {"mode": "grounding"}
    assert source["goal"]["description"] in raw["goal"]["objective"]
    assert source["goal"]["constraints"][0] in raw["goal"]["objective"]
    assert all(item["result"] == {"kind": "metric", "deferred": True} for item in raw["components"])
    ResearchContract.from_mapping(raw)
    with pytest.raises(ValueError):
        ResearchContract.from_mapping({**raw, "schema_version": 2})
