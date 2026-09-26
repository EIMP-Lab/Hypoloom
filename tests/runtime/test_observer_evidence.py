"""Current-invocation observations never turn missing evidence into blame."""
from __future__ import annotations

import base64
import json
import os

import pytest

from hypoloom.observer_evidence import read_attempt_evidence
from tests.runtime.test_observer_details import _database


SKILL = "hypoloom-researcher:resource-planning"
QUERY = "mcp__host-tools__get_resources"


def _row(**changes):
    request = {"run_id": "run", "attempt_id": "ours", "schema_version": 18,
        "role": "researcher", "command": "EXPERIMENT_DESIGN_IMPLEMENT",
        "input_artifacts": [{"ref": "ours--expert-resources", "media_type": "application/json",
            "content_base64": base64.b64encode(json.dumps({"plugin": {
                "name": "hypoloom-researcher", "run_id": "run", "role": "researcher"}}).encode()).decode()}]}
    request.update(changes)
    return {"attempt_id": "ours", "run_id": "run", "kind": "AGENT", "state": "CLOSED",
        "terminal_reason": "SUCCEEDED", "started_at": 1, "ended_at": 2,
        "attempt_ordinal": 1, "experiment_id": None,
        "observation": {"request": request, "session": {"session_id": "chain"}}}


def _init(**changes):
    return {"type": "system", "subtype": "init", "session_id": "chain",
        "tools": ["Skill", QUERY, "Read", "Edit"], "skills": [SKILL], **changes}


def _call(identity, name, inputs=None, **changes):
    return {"type": "assistant", "session_id": "chain", "message": {
        "id": "message-" + identity, "content": [{"type": "tool_use", "id": identity,
            "name": name, "input": inputs or {}}]}, **changes}


def _result(identity, *, error=False):
    return {"type": "user", "session_id": "chain", "uuid": "result-" + identity,
        "message": {"content": [{"type": "tool_result", "tool_use_id": identity,
            "is_error": error, "content": "PRIVATE_TOOL_BODY"}]}}


def _body(**changes):
    return {"type": "user", "session_id": "chain", "uuid": "skill-body",
        "message": {"content": [{"type": "text", "text":
            "Base directory for this skill: /expert-plugin/skills/resource-planning\n\n"
            "Inspect owned measurements. PRIVATE_SKILL_BODY"}]}, **changes}


def _terminal():
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": "chain"}


def _success():
    return [_init(), _call("skill", "Skill", {"skill": SKILL}), _result("skill"), _body(),
            _call("query", QUERY), _result("query"), _terminal()]


def _write(root, row, events, *, instruction=None):
    directory = root / "diagnostics/agents/run/ours"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "stdout.log").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    request = row["observation"]["request"]
    prompt = {"agent_request_metadata": {k: request[k] for k in ("run_id", "attempt_id", "role", "command")},
        "instruction": instruction if instruction is not None else
            f"Your bound native resource Skill is `{SKILL}`; "
            f"If `{SKILL}` is listed, you must invoke it now, "
            "read its loaded instructions, then query mcp__host-tools__get_resources. "}
    (directory / "prompt.txt").write_text(json.dumps(prompt))
    return directory


def test_complete_current_success_deduplicates_tools_and_omits_private_bodies(tmp_path):
    row = _row()
    events = _success()
    events[2:2] = [events[1]]
    events[4:4] = [events[3]]
    _write(tmp_path, row, events)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    summary = read_attempt_evidence(tmp_path, "run", row)
    assert summary["coverage"] == "complete"
    assert (summary["tool_calls"], summary["tool_errors"]) == (2, 0)
    assert summary["required_action"]["status"] == "met"
    assert summary["required_action"]["name"] == SKILL
    assert "PRIVATE" not in json.dumps(summary)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("events", [
    [_init(), _call("query", QUERY), _result("query"), _terminal()],
    [_init(), _call("skill", "Skill", {"skill": "resource-planning"}), _result("skill"),
     _body(), _call("query", QUERY), _result("query"), _terminal()],
    [_init(), _call("skill", "Skill", {"skill": SKILL}), _result("skill", error=True),
     _body(), _call("query", QUERY), _result("query"), _terminal()],
    [_init(), _call("skill", "Skill", {"skill": SKILL}), _result("skill"),
     _call("query", QUERY), _result("query"), _terminal()],
    [_init(), _call("query", QUERY), _result("query"), _call("skill", "Skill", {"skill": SKILL}),
     _result("skill"), _body(), _terminal()],
    [_init(), _call("skill", "Skill", {"skill": SKILL}), _result("skill"),
     _call("query", QUERY), _body(), _result("query"), _terminal()],
])
def test_closed_complete_stream_distinguishes_missing_or_wrong_action_order(tmp_path, events):
    row = _row()
    _write(tmp_path, row, events)
    summary = read_attempt_evidence(tmp_path, "run", row)
    assert summary["coverage"] == "complete"
    assert summary["required_action"]["status"] == "missed"


def test_ordinary_tool_error_is_observed_without_overriding_required_action(tmp_path):
    row = _row()
    _write(tmp_path, row, _success()[:-1] + [_call("read", "Read", {"file_path": ".env"}),
                                         _result("read", error=True), _terminal()])
    result = read_attempt_evidence(tmp_path, "run", row)
    assert (result["tool_calls"], result["tool_errors"]) == (3, 1)
    assert result["required_action"]["status"] == "met"
    assert "PRIVATE_TOOL_BODY" not in json.dumps(result)


def test_rejected_reading_calls_are_counted_once_without_inventing_readers(tmp_path):
    row = _row(command="LITERATURE_REVIEW")
    call = _call("bad-ref", "mcp__host-tools__read_papers", {"paper_ref": "PRIVATE_REFERENCE"})
    reply = _result("bad-ref", error=True)
    _write(tmp_path, row, [_init(), call, reply, call, reply,
        _call("pending", "mcp__host-tools__read_papers"), _terminal()])
    result = read_attempt_evidence(tmp_path, "run", row)
    assert result["reading_calls"] == {"calls": 2, "rejected": 1, "unanswered": 1, "last_wait_reason": None}
    assert result["coverage"] == "partial"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("change", ["open", "no_terminal", "unpaired", "malformed", "truncated", "capture_error", "foreign_session"])
def test_incomplete_evidence_never_reports_met_or_missed(tmp_path, monkeypatch, change):
    from hypoloom import observer_details
    row, events = _row(), _success()
    if change == "open":
        row["state"] = "OPEN"
    if change == "no_terminal":
        events.pop()
    if change == "unpaired":
        events.pop(-2)
    if change == "capture_error":
        row["observation"]["diagnostics"] = {"runtime_capture": {"capture_errors": ["interrupted"]}}
    if change == "foreign_session":
        events[1]["session_id"] = "other-session"
    directory = _write(tmp_path, row, events)
    if change == "malformed":
        with (directory / "stdout.log").open("a") as stream:
            stream.write('{"type":')
    if change == "truncated":
        monkeypatch.setattr(observer_details, "_MAX_TRACE_BYTES", 100)
    summary = read_attempt_evidence(tmp_path, "run", row)
    assert summary["coverage"] == "partial"
    assert summary["required_action"]["status"] == "unknown"


@pytest.mark.parametrize("change", ["missing", "bad_prompt_scope", "missing_requirement", "prompt_truncated"])
def test_current_prompt_must_supply_the_exact_requirement(tmp_path, monkeypatch, change):
    from hypoloom import observer_details
    row = _row()
    directory = _write(tmp_path, row, _success())
    prompt = directory / "prompt.txt"
    if change == "missing":
        prompt.unlink()
    elif change == "bad_prompt_scope":
        document = json.loads(prompt.read_text())
        document["agent_request_metadata"]["attempt_id"] = "previous"
        prompt.write_text(json.dumps(document))
    elif change == "missing_requirement":
        _write(tmp_path, row, _success(), instruction="Optional resource advice")
    else:
        prompt.write_text(prompt.read_text() + " " * observer_details._MAX_TRACE_BYTES)
    assert read_attempt_evidence(tmp_path, "run", row)["required_action"]["status"] == "unknown"


def test_missing_current_stdout_never_counts_a_resumed_archive(tmp_path):
    row = _row()
    archive = tmp_path / "artifacts/run/agent/ours/transcript/transcript.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text("\n".join(json.dumps(e) for e in _success()))
    row["observation"]["session"]["transcript"] = {"path": "run/agent/ours/transcript"}
    summary = read_attempt_evidence(tmp_path, "run", row)
    assert summary["coverage"] == "unavailable"
    assert summary["required_action"]["status"] == "unknown"


def test_prefix_history_replay_and_nested_agent_do_not_satisfy_this_invocation(tmp_path):
    row = _row()
    history = _success()[1:-1]
    replay = [{**event, "isReplay": True} for event in history]
    nested = [{**event, "parent_tool_use_id": "child"} for event in history]
    events = history + [_init()] + replay + nested + [_call("current", QUERY), _result("current"), _terminal()]
    _write(tmp_path, row, events)
    summary = read_attempt_evidence(tmp_path, "run", row)
    assert summary["coverage"] == "complete"
    assert summary["tool_calls"] == 1
    assert summary["required_action"]["status"] == "missed"


@pytest.mark.parametrize("change", ["legacy", "other_command", "no_binding", "skill_absent"])
def test_requirement_is_not_invented_for_legacy_or_unavailable_plugin(tmp_path, change):
    row, events = _row(), _success()
    if change == "legacy":
        row["observation"]["request"]["schema_version"] = 17
    elif change == "other_command":
        row["observation"]["request"]["command"] = "FINAL_REPORT"
    elif change == "no_binding":
        row["observation"]["request"]["input_artifacts"] = []
    else:
        events = [_init(skills=[]), _terminal()]
    _write(tmp_path, row, events)
    assert read_attempt_evidence(tmp_path, "run", row)["required_action"]["status"] == "not_applicable"


@pytest.mark.parametrize("change", ["row_run", "request_run", "request_attempt", "plugin_run"])
def test_cross_scope_rows_or_bindings_never_read_another_attempt(tmp_path, monkeypatch, change):
    from hypoloom import observer_details
    row = _row()
    _write(tmp_path, row, _success())
    if change == "row_run":
        row["run_id"] = "other"
    elif change == "request_run":
        row["observation"]["request"]["run_id"] = "other"
    elif change == "request_attempt":
        row["observation"]["request"]["attempt_id"] = "other"
    else:
        item = row["observation"]["request"]["input_artifacts"][0]
        item["content_base64"] = base64.b64encode(json.dumps({"plugin": {
            "name": "hypoloom-researcher", "run_id": "other", "role": "researcher"}}).encode()).decode()
    monkeypatch.setattr(observer_details, "_read", lambda *args: pytest.fail("cross-scope evidence read"))
    result = read_attempt_evidence(tmp_path, "run", row)
    assert result["coverage"] == "unavailable"
    assert result["required_action"]["status"] == "unknown"


@pytest.mark.parametrize("change", ["symlink", "parent_symlink", "fifo", "read_error"])
def test_unsafe_or_unreadable_evidence_stays_unknown_without_private_content(tmp_path, monkeypatch, change):
    from hypoloom import observer_details
    row = _row()
    directory = _write(tmp_path, row, _success())
    path = directory / "stdout.log"
    if change == "read_error":
        def denied(*args):
            raise PermissionError("PRIVATE_ERROR_TEXT")
        monkeypatch.setattr(observer_details, "_read", denied)
    elif change == "parent_symlink":
        directory.rename(directory.with_name("elsewhere"))
        directory.symlink_to(directory.with_name("elsewhere"), target_is_directory=True)
    else:
        path.unlink()
        if change == "fifo":
            os.mkfifo(path)
        else:
            path.symlink_to(directory / "prompt.txt")
    summary = read_attempt_evidence(tmp_path, "run", row)
    assert summary["required_action"]["status"] == "unknown"
    assert "PRIVATE" not in json.dumps(summary)


def test_small_summary_cache_reuses_read_and_invalidates_files_state_and_binding(tmp_path, monkeypatch):
    from hypoloom import observer_details
    row = _row()
    directory = _write(tmp_path, row, _success())
    reads, original = [], observer_details._read
    def counted(*args):
        reads.append(args[1])
        return original(*args)
    monkeypatch.setattr(observer_details, "_read", counted)
    first = read_attempt_evidence(tmp_path, "run", row)
    count = len(reads)
    first["required_action"]["status"] = "caller mutation"
    assert read_attempt_evidence(tmp_path, "run", row)["required_action"]["status"] == "met"
    assert len(reads) == count
    _write(tmp_path, row, [_init(), _terminal()])
    assert read_attempt_evidence(tmp_path, "run", row)["required_action"]["status"] == "missed"
    assert len(reads) > count
    row["state"] = "OPEN"
    assert read_attempt_evidence(tmp_path, "run", row)["required_action"]["status"] == "unknown"
    row["state"] = "CLOSED"
    prompt = json.loads((directory / "prompt.txt").read_text())
    prompt["instruction"] = "No requirement recorded"
    (directory / "prompt.txt").write_text(json.dumps(prompt))
    assert read_attempt_evidence(tmp_path, "run", row)["required_action"]["status"] == "unknown"


def test_attempt_detail_exposes_the_same_small_summary(tmp_path):
    from hypoloom.observer_details import read_observer_detail
    row = _row()
    _write(tmp_path, row, _success())
    stored = {**row, "observation_json": json.dumps(row["observation"])}
    _database(tmp_path, [stored])
    detail = read_observer_detail(tmp_path, "run", "attempt", attempt_id="ours")
    assert detail["trace_evidence"] == read_attempt_evidence(tmp_path, "run", row)


def test_refresh_read_budget_bounds_churn_beyond_cache_capacity(tmp_path, monkeypatch):
    from hypoloom import observer_details, observer_evidence
    monkeypatch.setattr(observer_evidence, "_CACHE_LIMIT", 2)
    roots = [tmp_path / str(index) for index in range(6)]
    row = _row()
    for root in roots:
        _write(root, row, _success())
    reads, original = [], observer_details._read
    def counted(*args):
        reads.append(args[1])
        return original(*args)
    monkeypatch.setattr(observer_details, "_read", counted)
    for _ in range(3):
        before = len(reads)
        with observer_evidence.evidence_read_budget(1):
            results = [read_attempt_evidence(root, "run", row) for root in roots]
        assert len(reads) - before <= 2  # At most stdout and prompt for one cold Attempt.
        assert any(r["required_action"]["reason"] == "evidence_read_deferred" for r in results)
    # Selected detail remains available even when a broad refresh was deferred.
    assert read_attempt_evidence(roots[-1], "run", row)["required_action"]["status"] == "met"


@pytest.mark.parametrize("change", ["assistant_body", "body_before_skill_result", "conflicting_result", "missing_init"])
def test_native_pair_and_body_provenance_are_not_inferred_from_text(tmp_path, change):
    row, events = _row(), _success()
    if change == "assistant_body":
        events[3]["type"] = "assistant"
    elif change == "body_before_skill_result":
        events[2], events[3] = events[3], events[2]
    elif change == "conflicting_result":
        events.insert(3, _result("skill", error=True))
    else:
        events.pop(0)
    _write(tmp_path, row, events)
    result = read_attempt_evidence(tmp_path, "run", row)
    assert result["required_action"]["status"] == (
        "unknown" if change in {"conflicting_result", "missing_init"} else "missed")


def test_missing_old_stdout_does_not_starve_later_evidence_and_creation_invalidates(tmp_path):
    from hypoloom.observer_evidence import evidence_read_budget
    row = _row()
    missing = [tmp_path / str(index) for index in range(16)]
    present = tmp_path / "present"
    _write(present, row, _success())
    observed = []
    for _ in range(2):
        with evidence_read_budget(16):
            for root in missing:
                assert read_attempt_evidence(root, "run", row)["coverage"] == "unavailable"
            observed.append(read_attempt_evidence(present, "run", row))
    assert observed[-1]["required_action"]["status"] == "met"
    _write(missing[0], row, _success())
    assert read_attempt_evidence(missing[0], "run", row)["required_action"]["status"] == "met"


@pytest.mark.parametrize("terminal", ["TIMED_OUT", "LOST", "CANCELLED", "FAILED"])
def test_interrupted_attempt_cannot_make_complete_native_result_conclusive(tmp_path, terminal):
    row = _row()
    row["terminal_reason"] = terminal
    _write(tmp_path, row, [_init(), _terminal()])
    result = read_attempt_evidence(tmp_path, "run", row)
    assert result["coverage"] == ("complete" if terminal == "FAILED" else "partial")
    assert result["required_action"]["status"] == ("missed" if terminal == "FAILED" else "unknown")
