"""Public bilingual starters must preserve controls and admit real path bindings."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from hypoloom.profile_admission import admit_profile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "profiles/templates"
FILES = {f"{name}.yaml" for name in (
    "goal", "experts", "methodology", "language", "notify", "safety")}


def _documents(name):
    template = TEMPLATES / name
    assert {path.name for path in template.iterdir()} == FILES
    return {path.stem: yaml.safe_load(path.read_text()) for path in template.iterdir()}


def test_language_variants_preserve_scientific_and_operational_controls():
    english, chinese = _documents("multi-expert-en"), _documents("multi-expert-zh")
    for name in ("methodology", "safety", "notify"):
        assert english[name] == chinese[name]
    for language, source in (("en", english), ("zh-CN", chinese)):
        assert set(source["language"].values()) == {language}
        assert source["methodology"]["schema_version"] == 4
        assert source["safety"]["gpus"] == []
        assert source["notify"]["sinks"]["telegram"]["enabled"] is False
        assert source["notify"]["sinks"]["dashboard"]["port"] == 0
    assert english["goal"]["stop_conditions"] == chinese["goal"]["stop_conditions"]
    assert english["goal"]["metric"] is chinese["goal"]["metric"] is None
    assert english["experts"]["defaults"] == chinese["experts"]["defaults"]
    assert [v["name"] for v in english["experts"]["experts"]] == [
        v["name"] for v in chinese["experts"]["experts"]]


@pytest.mark.parametrize("name", ["multi-expert-en", "multi-expert-zh"])
def test_public_template_admits_bound_paths_without_starting_research(tmp_path, name):
    source = _documents(name)
    before = copy.deepcopy(source)
    target, data, profile = (tmp_path / part for part in ("target", "data", "profile"))
    for path in (target, data, profile):
        path.mkdir()
    (target / "README.md").write_text("Disposable public Profile admission target.\n")
    for args in (("init", "--quiet"), ("add", "README.md"),
                 ("-c", "user.name=Hypoloom Tests", "-c", "user.email=tests@invalid.example",
                  "commit", "--quiet", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=target, check=True, capture_output=True)
    source["methodology"]["runtime"].update(target_repo=str(target), conda_env=sys.prefix)
    source["safety"]["allow_read_paths"] = [str(data)]
    source["experts"]["defaults"]["model"] = "scripted-exact-model"
    for section, document in source.items():
        (profile / (section + ".yaml")).write_text(yaml.safe_dump(document, allow_unicode=True))
    report = admit_profile(profile, run_id="public-" + name, allow_gpu=False)
    assert report.admitted, report.to_dict()
    contract = report.create_run_payload["research_contract"]
    product = contract["methodology"]["controls"]["product"]
    assert product["language"] == source["language"]
    assert len(product["experts"]["experts"]) == 3
    assert not report.create_run_payload["execution"]["allow_gpu"]
    assert not list(tmp_path.rglob("state.sqlite"))
    assert _documents(name) == before
