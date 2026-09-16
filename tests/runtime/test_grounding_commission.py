"""Read-only admission checks; these do not authorize or launch research."""

from __future__ import annotations

from pathlib import Path

import yaml

from hypoloom.profile_admission import PROFILE_V3_FILES, admit_profile
from test_profile_admission import _materialize_expert_loop_pilot_v3
from test_profile_v4 import documents


def _admit_isolated_goal(tmp_path: Path, goal: dict) -> dict:
    profile, target, data = _materialize_expert_loop_pilot_v3(tmp_path)
    source = documents()
    source["goal"] = goal
    source["methodology"]["runtime"]["target_repo"] = str(target)
    source["safety"]["allow_read_paths"] = [str(data)]
    for name, document in source.items():
        (profile / f"{name}.yaml").write_text(
            yaml.safe_dump(document, allow_unicode=True), encoding="utf-8"
        )
    before = {name: (profile / name).read_bytes() for name in PROFILE_V3_FILES}
    report = admit_profile(profile, run_id="commission-admission-only", allow_gpu=False)
    assert report.admitted, report.to_dict()
    assert report.create_run_payload is not None
    assert before == {name: (profile / name).read_bytes() for name in PROFILE_V3_FILES}
    assert not list(tmp_path.rglob("state.sqlite"))
    return report.create_run_payload["research_contract"]


def test_freeform_shared_commission_preserves_user_text_without_a_metric_dsl(
    tmp_path: Path,
) -> None:
    goal = {
        "title": "Improve two existing evaluation suites",
        "description": "Use one candidate; ask the user about decisive ambiguity.",
        "evaluation": "grounding",
        "constraints": ["Keep the supplied splits unchanged.", "No latency objective."],
        "stop_conditions": {"max_wall_clock_hours": 2},
    }
    contract = _admit_isolated_goal(tmp_path, goal)
    assert contract["goal"]["objective"].startswith(
        goal["title"] + "\n\n" + goal["description"].strip()
    )
    product = contract["methodology"]["controls"]["product"]
    assert product["goal"]["evaluation"] == "grounding"
    assert product["goal"]["completion_mode"] == "shared_goal"
    assert product["goal"]["constraints"] == goal["constraints"]


