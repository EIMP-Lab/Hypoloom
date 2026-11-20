"""Real CPU subprocess effects distinguish reuse from an unchanged rerun."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hypoloom.work_units import WorkUnitError, WorkUnits
from hypoloom.process_observation import observed_work_units


def unit(tmp_path, **changes):
    trace = tmp_path / "effects.jsonl"
    script = tmp_path / "calculate.py"
    if not script.exists():
        script.write_text('''import json, sys
from pathlib import Path
with Path(sys.argv[1]).open('a') as stream: stream.write('ran\\n')
if Path(sys.argv[2]).exists(): raise SystemExit(7)
Path('value.json').write_text(json.dumps({'value': 42}))
''')
    result = dict(argv=[sys.executable, str(script), str(trace), str(tmp_path / "fail")],
                  context={"implementation": "git:one", "data": "input:one", "configuration": {"steps": 2},
                           "repetition": None, "protocol": "validation-v1"}, outputs=["value.json"],
                  timeout_seconds=5, stage="pilot", purpose="exploration", basis="bounded deterministic pilot")
    result.update(changes)
    return result


def effects(tmp_path):
    path = tmp_path / "effects.jsonl"
    return path.read_text().splitlines() if path.exists() else []


def test_status_reads_bounded_live_work_and_skips_unsafe_or_partial_records(tmp_path):
    work = tmp_path / "work"
    units = WorkUnits(work / "attempt" / "output")
    units.run("one", **unit(tmp_path))
    assert observed_work_units(work, "attempt") == {"completed": 1, "running": 0, "failed": 0, "stage": "pilot", "partial": False}
    second = units.units / "two"
    second.mkdir()
    (second / "record.json").write_text(json.dumps({"unit_id": "two", "stage": "validation", "schema_version": 1, "status": "running"}))
    for name in ("large", "fifo", "hardlink", "broken"): (units.units / name).mkdir()
    (units.units / "large" / "record.json").write_bytes(b"x" * 32769)
    os.mkfifo(units.units / "fifo" / "record.json")
    (units.units / "broken" / "record.json").write_bytes(b"[")
    os.link(second / "record.json", units.units / "hardlink" / "record.json")
    (units.units / "link").symlink_to(second, target_is_directory=True)
    assert observed_work_units(work, "attempt")["running"] == 0
    (units.units / "hardlink" / "record.json").unlink()
    assert observed_work_units(work, "attempt") == {"completed": 1, "running": 1, "failed": 0, "stage": "validation", "partial": True}
    (work / "linked-attempt").symlink_to(work / "attempt", target_is_directory=True)
    assert observed_work_units(work, "linked-attempt") is None and observed_work_units(work, "../attempt") is None
    for number in range(80): (units.units / f"extra-{number}").mkdir()
    sample = observed_work_units(work, "attempt")
    assert sample is None or sample["partial"]


def test_partial_failure_reuses_completed_sibling_and_runs_only_failed_unit(tmp_path):
    definition = unit(tmp_path)
    first = WorkUnits(tmp_path / "first")
    first.run("a", **definition)
    (tmp_path / "fail").touch()
    with pytest.raises(WorkUnitError, match="exited with 7"):
        first.run("b", **definition)
    (tmp_path / "fail").unlink()
    second = WorkUnits(tmp_path / "second", previous=[{"path": str(first.root), "artifact_id": "published:first"}])
    a = second.run("a", **definition)
    b = second.run("b", **definition)
    assert len(effects(tmp_path)) == 3  # a once; b failed once and succeeded once
    assert a["reused_from"]["artifact_id"] == "published:first" and b["reused_from"] is None
    assert json.loads(Path(a["files"]["value.json"]).read_text()) == {"value": 42}
    assert json.loads((first.units / "b/record.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("field,value", [("implementation", "git:two"), ("data", "input:two"),
    ("configuration", {"steps": 3}), ("configuration", {"steps": True}),
    ("repetition", 1), ("protocol", "test-v1")])
def test_changed_context_runs_again(tmp_path, field, value):
    definition = unit(tmp_path)
    first = WorkUnits(tmp_path / "first"); first.run("a", **definition)
    changed = deepcopy(definition); changed["context"][field] = value
    second = WorkUnits(tmp_path / "second", previous=[{"path": str(first.root)}])
    assert second.run("a", **changed)["reused_from"] is None
    assert len(effects(tmp_path)) == 2


def test_same_process_reuse_and_explicit_replication(tmp_path):
    definition = unit(tmp_path)
    first = WorkUnits(tmp_path / "first"); first.run("a", **definition)
    first.run("a", **definition)
    assert len(effects(tmp_path)) == 1
    second = WorkUnits(tmp_path / "second", previous=[{"path": str(first.root)}])
    assert second.run("a", **definition, replication_reason="measure nondeterminism")["reused_from"] is None
    assert len(effects(tmp_path)) == 2
    with pytest.raises(WorkUnitError, match="replication_reason"):
        second.run("b", **definition, replication_reason="")


@pytest.mark.parametrize("damage", ["missing", "truncated", "symlink", "running"])
def test_incomplete_outputs_never_skip_actual_computation(tmp_path, damage):
    definition = unit(tmp_path)
    first = WorkUnits(tmp_path / "first"); first.run("a", **definition)
    value = first.units / "a/value.json"
    record = first.units / "a/record.json"
    if damage == "missing": value.unlink()
    if damage == "truncated": record.write_text('{"schema_version":')
    if damage == "symlink": value.unlink(); value.symlink_to(tmp_path / "effects.jsonl")
    if damage == "running":
        content = json.loads(record.read_text()); content["status"] = "running"; record.write_text(json.dumps(content))
    second = WorkUnits(tmp_path / "second", previous=[{"path": str(first.root)}])
    assert second.run("a", **definition)["reused_from"] is None
    assert len(effects(tmp_path)) == 2


def test_raw_publication_probe_context_and_frozen_parent_state(tmp_path, monkeypatch):
    definition = unit(tmp_path, purpose="performance")
    first = WorkUnits(tmp_path / "publication/raw")
    with pytest.raises(WorkUnitError, match="runtime"):
        first.run("probe", **definition)
    definition["context"]["runtime"] = {"device": "cpu", "threads": 1, "workload": "two steps"}
    first.run("probe", **definition)
    monkeypatch.setenv("HYPOLOOM_REUSE_INPUTS", json.dumps([{"path": str(tmp_path / "publication")}]))
    second = WorkUnits(tmp_path / "next")
    result = second.run("probe", **definition)
    assert result["reused_from"] and len(effects(tmp_path)) == 1
    changed = deepcopy(definition); changed["context"]["runtime"]["threads"] = 2
    assert second.run("probe-other-runtime", **changed)["reused_from"] is None


def test_timeout_is_not_completed_work_and_paths_cannot_escape(tmp_path):
    definition = unit(tmp_path)
    first = WorkUnits(tmp_path / "first")
    with pytest.raises(WorkUnitError, match="safe relative"):
        first.run("escape", **{**definition, "outputs": ["../elsewhere"]})
    with pytest.raises(subprocess.TimeoutExpired):
        first.run("slow", **{**definition, "argv": [sys.executable, "-c", "import time; time.sleep(5)"],
                             "timeout_seconds": 0.03})
    assert json.loads((first.units / "slow/record.json").read_text())["status"] == "failed"
