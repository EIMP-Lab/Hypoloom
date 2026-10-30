"""Skill activity is observed use, distinct from binding and accepted evolution."""
import json
import shutil

import pytest

from hypoloom.observer_details import read_observer_detail
from tests.runtime.test_observer_details import _attempt, _binding, _database, _git, _plugin
from tests.runtime.test_observer_evidence import _call, _init, _result, _terminal


NAME = "hypoloom-researcher:resource-planning"


def _bound(identity, commit, *, start=1, command="EXPERIMENT_DESIGN_IMPLEMENT", state="CLOSED", update=None):
    return _attempt(identity, command=command, started_at=start, state=state,
        observation={"session": {"session_id": "chain", "expert_resources": _binding(commit)},
                     **({"plugin_update": update} if update else {})})


def _capture(root, row, events):
    path = root / "diagnostics/agents/run" / row["attempt_id"]
    path.mkdir(parents=True)
    (path / "stdout.log").write_text("\n".join(json.dumps(event) for event in events) + "\n")


def _calls(*, timestamp=1.5):
    return [_init(), _call("use", "Skill", {"skill": NAME}, timestamp=timestamp), _result("use"), _terminal()]


def test_library_counts_current_calls_once_and_separates_results(tmp_path):
    _, _, commit = _plugin(tmp_path)
    row = _bound("ours", commit)
    _database(tmp_path, [row])
    use = _call("use", "Skill", {"skill": NAME}, timestamp=1.5)
    _capture(tmp_path, row, [_call("old", "Skill", {"skill": NAME}), _init(), use, use,
        _result("use"), _result("use"), _call("replay", "Skill", {"skill": NAME}, isReplay=True),
        _call("child", "Skill", {"skill": NAME}, parent_tool_use_id="parent"),
        _call("bad", "Skill", {"skill": NAME}, timestamp=1.6), _result("bad", error=True),
        _call("pending", "Skill", {"skill": NAME}, timestamp=1.7), _terminal()])
    before = (tmp_path / "state.sqlite").read_bytes()
    data = read_observer_detail(tmp_path, "run", "skills")
    usage = data["skills"][0]["usage"]
    assert (usage["calls"], usage["succeeded"], usage["failed"], usage["unanswered"]) == (3, 1, 1, 1)
    assert usage["coverage"] == "partial"
    assert data["activity"]["observed_calls"] == 3
    assert sum(bin["calls"] for bin in data["activity"]["timeline"]) == 3
    assert "PRIVATE_TOOL_BODY" not in json.dumps(data)
    assert (tmp_path / "state.sqlite").read_bytes() == before


def test_binding_and_prompt_mentions_are_not_skill_calls(tmp_path):
    _, _, commit = _plugin(tmp_path)
    row = _bound("ours", commit)
    _database(tmp_path, [row])
    _capture(tmp_path, row, [_init(), {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Use " + NAME}]}}, _terminal()])
    data = read_observer_detail(tmp_path, "run", "skills")
    assert data["activity"]["coverage"] == "complete"
    assert data["activity"]["observed_calls"] == 0
    assert data["skills"][0]["bindings"]


def test_expert_cards_keep_full_counts_and_separate_capture_coverage(tmp_path):
    repo, _, commit = _plugin(tmp_path)
    shutil.copytree(repo, tmp_path / "run/experts/peer/plugin")
    ours = _bound("ours", commit)
    peer = _attempt("peer", role="peer", command="RESULT_ANALYSIS", started_at=5,
        observation={"session": {"session_id": "peer-chain", "expert_resources": {
            "plugin": {"run_id": "run", "role": "peer", "name": "hypoloom-peer", "commit": commit}}}})
    _database(tmp_path, [ours, peer])
    events = [_init()]
    for index in range(230):
        events.extend([_call(str(index), "Skill", {"skill": NAME}, timestamp=1 + index / 1000), _result(str(index))])
    _capture(tmp_path, ours, [*events, _terminal()])
    _capture(tmp_path, peer, [_init(), _call("pending", "Skill", {"skill": "hypoloom-peer:resource-planning"}, timestamp=5.5), _terminal()])
    before = (tmp_path / "state.sqlite").read_bytes()
    result = read_observer_detail(tmp_path, "run", "skills")
    activity = result["activity"]
    experts = {item["role"]: item for item in activity["experts"]}
    assert len(activity["calls"]) == 200 and activity["calls_truncated"]
    assert experts["researcher"]["observed_calls"] == experts["researcher"]["succeeded"] == 230
    assert sum(item["calls"] for item in experts["researcher"]["timeline"]) == 230
    assert experts["researcher"]["coverage"] == "complete"
    assert experts["peer"]["observed_calls"] == experts["peer"]["unanswered"] == 1
    assert experts["peer"]["coverage"] == "partial"
    assert sum(item["observed_calls"] for item in experts.values()) == activity["observed_calls"]
    assert next(item for item in result["skills"] if item["role"] == "researcher")["usage"]["coverage"] == "complete"
    selected = read_observer_detail(tmp_path, "run", "skills", role="peer")["activity"]
    assert [item["role"] for item in selected["experts"]] == ["peer"]
    assert selected["observed_calls"] == 1
    stage = read_observer_detail(tmp_path, "run", "skills", command="RESULT_ANALYSIS")["activity"]
    assert next(item for item in stage["experts"] if item["role"] == "researcher")["attempts"] == 0
    assert (tmp_path / "state.sqlite").read_bytes() == before


def test_version_time_and_stage_filters_preserve_actual_binding(tmp_path):
    _, base, commit = _plugin(tmp_path)
    old = _bound("old", base)
    current = _bound("new", commit, start=10000, command="EXPERIMENT_EXECUTION")
    _database(tmp_path, [old, current])
    _capture(tmp_path, old, _calls())
    _capture(tmp_path, current, _calls(timestamp=10000.5))
    all_data = read_observer_detail(tmp_path, "run", "skills")
    assert all_data["activity"]["observed_calls"] == 2
    assert {item["commit"]: item["usage"]["calls"] for item in all_data["skills"]} == {base: 1, commit: 1}
    recent = read_observer_detail(tmp_path, "run", "skills", window="3600")
    assert recent["activity"]["observed_calls"] == 1
    assert recent["activity"]["calls"][0]["commit"] == commit
    stage = read_observer_detail(tmp_path, "run", "skills", command="EXPERIMENT_DESIGN_IMPLEMENT")
    assert stage["activity"]["observed_calls"] == 1
    assert stage["activity"]["calls"][0]["commit"] == base


@pytest.mark.parametrize("window", [None, "3600"])
def test_missing_timestamps_are_not_invented(tmp_path, window):
    _, _, commit = _plugin(tmp_path)
    row = _bound("ours", commit)
    _database(tmp_path, [row])
    _capture(tmp_path, row, _calls(timestamp=None))
    result = read_observer_detail(tmp_path, "run", "skills", **({"window": window} if window else {}))["activity"]
    assert result["untimed_calls"] == 1
    assert sum(item["calls"] for item in result["timeline"]) == 0
    assert result["observed_calls"] == (0 if window else 1)
    if window:
        assert result["coverage"] == "partial"


def test_missing_capture_and_foreign_plugin_do_not_become_zero_or_owned_calls(tmp_path):
    _, _, commit = _plugin(tmp_path)
    missing = _bound("missing", commit)
    foreign = _bound("foreign", commit)
    _database(tmp_path, [missing, foreign])
    events = _calls()
    events[1]["message"]["content"][0]["input"]["skill"] = "another-owner:resource-planning"
    _capture(tmp_path, foreign, events)
    result = read_observer_detail(tmp_path, "run", "skills")["activity"]
    assert result["coverage"] == "partial"
    assert result["observed_calls"] == 0
    assert result["unmatched_calls"] == 1
    assert result["capture"]["unavailable"] == 1


def test_reference_only_evolution_and_actual_later_calls(tmp_path):
    repo, _, base = _plugin(tmp_path)
    (repo / "skills/resource-planning/reference.md").write_text("Measure steady phase separately from warmup.\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "reference only")
    commit = _git(repo, "rev-parse", "HEAD")
    update = _bound("update", base, command="SELF_EVOLVE", update={"base_commit": base, "commit": commit})
    next_row = _bound("next", commit, start=5)
    noop = _bound("noop", commit, start=7, command="SELF_EVOLVE", update={"base_commit": commit, "commit": commit})
    failed = _bound("failed", commit, start=9, command="SELF_EVOLVE")
    failed["terminal_reason"] = "FAILED"
    _database(tmp_path, [update, next_row, noop, failed])
    _capture(tmp_path, next_row, _calls(timestamp=5.5))
    detail = read_observer_detail(tmp_path, "run", "skill", role="researcher", name="resource-planning", commit=commit)
    assert "reference.md" in detail["diff"]["text"]
    assert "+Measure steady phase" in detail["diff"]["text"]
    assert "SKILL.md" not in detail["diff"]["text"]
    next_binding = next(item for item in detail["adoption"] if item["attempt_id"] == "next")
    assert next_binding["native_loaded"] is True and next_binding["observed_native_calls"] == 1
    events = read_observer_detail(tmp_path, "run", "skills")["activity"]["evolutions"]
    assert [item["status"] for item in events] == ["failed", "unchanged", "accepted_change"]
    assert events[-1]["skills"][0]["files"] == [{"path": "skills/resource-planning/reference.md", "change": "modified"}]
    assert events[-1]["observed_later_calls"] == 1


def test_removed_custom_skill_retains_accepted_deletion_diff(tmp_path):
    repo, _, _ = _plugin(tmp_path)
    custom = repo / "skills/custom-method/SKILL.md"
    custom.parent.mkdir()
    custom.write_text("---\nname: custom-method\ndescription: A scoped method\n---\nOld method.\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "custom method")
    base = _git(repo, "rev-parse", "HEAD")
    custom.unlink()
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "remove method")
    commit = _git(repo, "rev-parse", "HEAD")
    update = _bound("update", base, command="SELF_EVOLVE", update={"base_commit": base, "commit": commit})
    _database(tmp_path, [update])
    detail = read_observer_detail(tmp_path, "run", "skill", role="researcher", name="custom-method", commit=commit)
    assert detail["removed"] and detail["body"] is None
    assert "-Old method." in detail["diff"]["text"]
    assert detail["diff"]["files"] == [{"path": "skills/custom-method/SKILL.md", "change": "deleted"}]


@pytest.mark.parametrize("window", ["0", "-1", "nan", "9999999999999999999"])
def test_invalid_windows_rejected_before_read(tmp_path, window):
    with pytest.raises(ValueError):
        read_observer_detail(tmp_path, "run", "skills", window=window)
