import json
from pathlib import Path
import re

import pytest

from hypoloom.expert_memory import ExpertMemory, MemoryError, validate_report
from hypoloom.expert_plugin import ExpertPluginStore


def _report(fact_id: str = "assessment-1", assessment: str = "SUPPORTED") -> bytes:
    return ("---\nassessments:\n  - fact_id: " + fact_id +
            "\n    hypothesis_id: hyp-1\n    assessment: " + assessment +
            "\n---\n# Round 1\n\nTested the proposed experiment.\n").encode()


def _facts():
    return [{"fact_id": "assessment-1", "fact_type": "HypothesisAssessed",
             "hypothesis_id": "hyp-1", "payload": {"assessment": "SUPPORTED"}}]


def test_report_requires_exact_accepted_assessment_not_an_agent_verdict():
    files = {"MEMORY.md": b"# Memory\n\nSee rounds/1.md.\n", "rounds/1.md": _report()}
    validate_report(files, cycle=1, facts=_facts(), max_bytes=8192)
    for invalid in (_report("made-up"), _report(assessment="REFUTED")):
        with pytest.raises(MemoryError):
            validate_report({**files, "rounds/1.md": invalid}, cycle=1, facts=_facts(), max_bytes=8192)
    with pytest.raises(MemoryError):
        validate_report({**files, "rounds/2.md": b"wrong round"}, cycle=1, facts=_facts(), max_bytes=8192)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_seed_report_example_projects_only_current_assessment_references(tmp_path, count):
    store = ExpertPluginStore(tmp_path)
    binding = store.ensure("run", "expert", "Test current-cycle reporting")
    view = store.read("run", "expert", commit=binding.commit,
                      attempt_id="report", max_bytes=131072)
    try:
        skill = (view.root / "skills/experiment-report/SKILL.md").read_text()
    finally:
        view.close()
    examples = re.findall(r"```python\n(.*?)\n```", skill, re.DOTALL)
    assert len(examples) == 1, "the report Skill must demonstrate the input-to-reference projection"
    current = [
        {"fact_id": "assessment-current", "hypothesis_id": "hyp-current",
         "fact_type": "HypothesisAssessed", "payload": {"assessment": "SUPPORTED"}},
        {"fact_id": "assessment-other", "hypothesis_id": "hyp-other",
         "fact_type": "HypothesisAssessed", "payload": {"assessment": "REFUTED"}},
    ][:count]
    namespace = {"cycle_assessments": {"cycle": 2, "assessments": current},
                 "prior_assessments": _facts()}
    exec(compile(examples[0], "experiment-report/SKILL.md", "exec"), namespace)
    references = namespace["assessments"]
    assert references == [
        {"fact_id": "assessment-current", "hypothesis_id": "hyp-current", "assessment": "SUPPORTED"},
        {"fact_id": "assessment-other", "hypothesis_id": "hyp-other", "assessment": "REFUTED"},
    ][:count]
    report = ("---\n" + json.dumps({"assessments": references}) +
              "\n---\n# Round 2\nCurrent cycle observations.\n").encode()
    validate_report({"MEMORY.md": b"# Index\n", "rounds/2.md": report},
                    cycle=2, facts=current, max_bytes=8192)


@pytest.mark.parametrize("current", [[], [
    {"fact_id": "assessment-current", "hypothesis_id": "hyp-current",
     "fact_type": "HypothesisAssessed", "payload": {"assessment": "SUPPORTED"}},
]])
def test_report_rejects_prior_cycle_fact_even_with_the_same_scientific_verdict(current):
    with pytest.raises(MemoryError, match=rf"cycle=2, allowed_count={len(current)}"):
        validate_report({"MEMORY.md": b"# Index\n", "rounds/2.md": _report()},
                        cycle=2, facts=current, max_bytes=8192)


@pytest.mark.parametrize("references, error", [
    (_facts(), "invalid assessment reference"),
    ([], "report must cite every assessment"),
    ([{"fact_id": "assessment-1", "hypothesis_id": "hyp-1", "assessment": "SUPPORTED"}] * 2,
     "accepted-assessment list"),
])
def test_report_rejects_full_records_omissions_and_duplicates(references, error):
    report = ("---\n" + json.dumps({"assessments": references}) +
              "\n---\n# Round 1\nObserved results.\n").encode()
    with pytest.raises(MemoryError, match=error):
        validate_report({"MEMORY.md": b"# Index\n", "rounds/1.md": report},
                        cycle=1, facts=_facts(), max_bytes=8192)


def test_memory_commits_report_and_old_snapshot_does_not_follow_head(tmp_path: Path):
    store = ExpertMemory(tmp_path)
    first = store.ensure("run-1", "expert-a")
    files = {"MEMORY.md": b"# Memory\n\nSee rounds/1.md.\n", "rounds/1.md": _report()}
    new = store.commit_report("run-1", "expert-a", first, "node-1", files,
                              cycle=1, facts=_facts(), max_bytes=8192)
    assert first != new
    assert store.files("run-1", "expert-a", first) == {"MEMORY.md": b"# Expert memory\n\nNo completed cycles yet.\n"}
    assert store.files("run-1", "expert-a", new) == files
    assert store.commit_report("run-1", "expert-a", first, "node-1", files,
                               cycle=1, facts=_facts(), max_bytes=8192) == new
    with pytest.raises(MemoryError):
        store.commit_report("run-1", "expert-a", first, "different-node", files,
                            cycle=1, facts=_facts(), max_bytes=8192)


def test_role_memory_is_not_readable_using_another_roles_commit(tmp_path: Path):
    store = ExpertMemory(tmp_path)
    original = store.ensure("run-1", "expert-a")
    store.ensure("run-1", "expert-b")
    commit = store.commit_report("run-1", "expert-a", original, "node-1",
                                {"MEMORY.md": b"# Private A\n", "rounds/1.md": _report()},
                                cycle=1, facts=_facts(), max_bytes=8192)
    with pytest.raises(MemoryError):
        store.files("run-1", "expert-b", commit)


def test_same_expert_component_cycle_reports_do_not_collide_or_overwrite(tmp_path: Path):
    store = ExpertMemory(tmp_path)
    base = store.ensure("run-1", "expert-a")
    first = {"MEMORY.md": b"# Memory\nFirst component complete.\n", "rounds/primary.score/1.md": _report()}
    commit = store.commit_report("run-1", "expert-a", base, "component-1-report", first,
        cycle=1, component_id="primary.score", facts=_facts(), max_bytes=8192)
    second = {"MEMORY.md": b"# Memory\nBoth components complete.\n", "rounds/must.delivery/1.md": _report()}
    new = store.commit_report("run-1", "expert-a", commit, "component-2-report", second,
        cycle=1, component_id="must.delivery", facts=_facts(), max_bytes=8192)
    assert store.files("run-1", "expert-a", new) == {**first, **second}
    assert store.files("run-1", "expert-a", commit) == first
    with pytest.raises(MemoryError, match="cannot be overwritten"):
        store.commit_report("run-1", "expert-a", new, "overwrite-component-1", first,
            cycle=1, component_id="primary.score", facts=_facts(), max_bytes=8192)
    with pytest.raises(MemoryError, match="exact round"):
        store.commit_report("run-1", "expert-a", new, "foreign-component", first,
            cycle=1, component_id="must.delivery", facts=_facts(), max_bytes=8192)


def test_complete_memory_budget_is_independent_and_does_not_truncate(tmp_path: Path):
    store = ExpertMemory(tmp_path)
    original = store.ensure("run-1", "expert-a")
    files = {"MEMORY.md": b"# Memory\n", "rounds/1.md": _report()}
    total = sum(map(len, files.values()))
    with pytest.raises(MemoryError, match="complete memory snapshot"):
        store.commit_report("run-1", "expert-a", original, "node-1", files,
                            cycle=1, facts=_facts(), max_bytes=8192,
                            max_memory_bytes=total - 1)
    assert store.ensure("run-1", "expert-a") == original
    assert not (store._repository("run-1", "expert-a") / "rounds/1.md").exists()
    committed = store.commit_report("run-1", "expert-a", original, "node-1", files,
                                    cycle=1, facts=_facts(), max_bytes=8192,
                                    max_memory_bytes=total)
    assert store.files("run-1", "expert-a", committed, max_bytes=total) == files
    destination = tmp_path / "run-1/sessions/expert-a/probe/memory"
    with pytest.raises(MemoryError, match="complete memory snapshot"):
        store.prepare("run-1", "expert-a", committed, destination,
                      writable=False, max_bytes=total - 1)
    assert not destination.exists()
    store.prepare("run-1", "expert-a", committed, destination,
                  writable=False, max_bytes=total)
    with pytest.raises(MemoryError, match="complete memory snapshot"):
        store.capture("run-1", "expert-a", committed, destination,
                      cycle=1, max_bytes=8192, max_memory_bytes=total - 1)
    assert (destination / "rounds/1.md").read_bytes() == files["rounds/1.md"]


@pytest.mark.parametrize("path", ["../outside", "/tmp/memory", "a/b", ".", ".."])
def test_memory_rejects_unsafe_owner_paths(tmp_path: Path, path: str):
    with pytest.raises(MemoryError):
        ExpertMemory(tmp_path).ensure("run-1", path)
