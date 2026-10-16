"""Opt-in paid current-ACI Host canary; helper tests never invoke a provider.

This is one expert/chain with real model decisions and real CPU execution,
not the retained component/rendezvous canary. Public research, GPU, interactions
and external notifications are outside this deliberately bounded sample. The
user-selected shared-goal success stops its cycle before batch/report/SELF nodes;
explicitly budgeted earlier cycles may revise.
The tiny accuracy profile retains explicit-metric semantics; it does not opt in
to Grounding-authored common acceptance when the current ACI advances.
The separate COMMON_G5 opt-in reuses the same bounded shell to qualify common
acceptance. Selecting both paid modes fails closed; old flags never enable it.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
import yaml

from hypoloom.agent_aci import (
    CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
    AgentBudget,
    EmbeddedInput,
    WorkspaceArtifact,
)
from hypoloom.agent_budget import (
    AgentAttemptCharge,
    charged_cost,
    charged_totals,
    fold_agent_attempts,
)
from hypoloom.candidate_validation import component_result_contract
from hypoloom.claude_agent_host import (
    CLAUDE_AGENT_HOST_VERSION,
    ClaudeAgentHost,
    _output_schema,
    _prompt,
    validate_claude_model_selector,
)
from hypoloom.compute_resources import (
    ComputeCapabilities,
    LocalComputeResources,
)
from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import AgentCommand, ResearchContract
from hypoloom.fake_agent import default_request
from hypoloom.literature import FixedLiteratureIO
from hypoloom.oracle import Oracle
from hypoloom.outcome_adapter import OUTCOME_ADAPTER_VERSION
from hypoloom.profile_admission import admit_profile
from hypoloom.workflow_host import WorkflowHost

from test_profile_v4 import documents
from test_real_expert_loop_qualification import _DeterministicOpenAlexFixture


_RUN_ID = f"current-aci{CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION}-paid-canary"
_WALL_SECONDS = 900
_CUMULATIVE_USD = Decimal("20")
# A new cohort starts at zero only when the operator explicitly declares zero.
# Prior charged usage must include unknown reservations; it is never inferred.
_PRIOR_CHARGED_USD = Decimal("0")
_TURN_LIMIT = 240
_NODE_BUDGET = AgentBudget(
    max_turns=24,
    max_tokens=100_000,
    max_output_bytes=131_072,
    wall_seconds=300,
    max_cost="2",
    token_enforcement_mode="observe",
)
_REQUIRED = {
    "GROUNDING",
    "LITERATURE_REVIEW",
    "HYPOTHESIS_PROPOSAL",
    "EXPERIMENT_DESIGN_IMPLEMENT",
    "EXPERIMENT_EXECUTION",
    "RESULT_ANALYSIS",
    "FINAL_REPORT",
}


def _paid_canary_enabled(common_acceptance: bool, environment=None) -> bool:
    values = os.environ if environment is None else environment
    old = values.get("HYPOLOOM_REAL_CURRENT_G5") == "1"
    common = values.get("HYPOLOOM_REAL_CURRENT_COMMON_G5") == "1"
    return values.get("HYPOLOOM_REAL_CLAUDE") == "1" and old != common and (common if common_acceptance else old)


_PAID = pytest.mark.skipif(
    not _paid_canary_enabled(False),
    reason="select only REAL_CURRENT_G5 plus REAL_CLAUDE, explicit prior spend and approved auth/model; both G5 modes together are rejected",
)
_COMMON_PAID = pytest.mark.skipif(
    not _paid_canary_enabled(True),
    reason="select only REAL_CURRENT_COMMON_G5 plus REAL_CLAUDE, explicit prior spend and approved auth/model; this is a separate cumulative $20 opt-in",
)


def _approved_prior_charged_usd() -> Decimal:
    limit = _approved_cumulative_limit_usd()
    try:
        prior = Decimal(os.environ["HYPOLOOM_G5_PRIOR_CHARGED_USD"])
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError(
            "Set explicit prior charged USD before any G5 preflight"
        ) from exc
    if not prior.is_finite() or not _PRIOR_CHARGED_USD <= prior < limit:
        raise ValueError(
            "prior charged USD must include unknown reservations and leave positive allowance"
        )
    return prior


def _approved_cumulative_limit_usd() -> Decimal:
    try:
        limit = _positive_allocation(os.environ["HYPOLOOM_G5_CUMULATIVE_LIMIT_USD"])
    except KeyError as exc:
        raise ValueError("Set the explicit G5 cumulative limit before preflight") from exc
    if limit > _CUMULATIVE_USD:
        raise ValueError("G5 cumulative limit exceeds the bounded $20 canary ceiling")
    return limit


def _preflight() -> tuple[ClaudeAgentHost, str, Decimal]:
    prior = _approved_prior_charged_usd()
    executable = Path(os.environ["HYPOLOOM_CLAUDE_EXECUTABLE"])
    settings = Path(os.environ["HYPOLOOM_CLAUDE_SETTINGS"])
    if not (
        executable.is_absolute()
        and executable.is_file()
        and not executable.is_symlink()
        and os.access(executable, os.X_OK)
    ):
        raise ValueError("Use the absolute resolved executable path before G5")
    if not (
        settings.is_absolute()
        and settings.is_file()
        and not settings.is_symlink()
        and stat.S_IMODE(settings.stat().st_mode) == 0o600
    ):
        raise ValueError("Do not bypass or change the credential permission guard")
    model = validate_claude_model_selector(os.environ["HYPOLOOM_CLAUDE_MODEL"])
    adapter = ClaudeAgentHost(claude_executable=executable, settings_path=settings)
    adapter.preflight(model=model)  # Read-only; no model process or request.
    return adapter, model, prior


def _positive_allocation(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("allocation USD must be finite and positive") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("allocation USD must be finite and positive")
    return amount


def _canary_launch(adapter, model, fresh_allocation_usd):
    """Explicit caller allocation is separate from the retained cumulative opt-in."""
    if adapter is model is fresh_allocation_usd is None:
        adapter, model, prior = _preflight()
        return adapter, model, prior, _approved_cumulative_limit_usd() - prior
    if adapter is None or model is None or fresh_allocation_usd is None:
        raise ValueError("provide adapter, model and fresh_allocation_usd together")
    allocation = _positive_allocation(fresh_allocation_usd)
    model = validate_claude_model_selector(model)
    if adapter.settings_path is None:
        raise ValueError("explicit G5 adapter must retain the settings permission guard")
    # This validates the absolute nonsymlink executable, 0600 settings, sandbox
    # and model without launching a provider. Do not bypass it for injected CC.
    adapter.preflight(model=model)
    return adapter, model, None, allocation


def _canary_accounting(prior, reported):
    if prior is not None:
        return {"budget_scope": "cohort_cumulative",
                "prior_charged_usd": str(prior),
                "cohort_charged_lower_bound_usd": str(prior + reported)}
    return {"budget_scope": "fresh_allocation", "prior_charged_usd": None,
            "cohort_charged_lower_bound_usd": None}


def _assert_launch_thresholds(charges, remaining: Decimal, node_budget: AgentBudget = _NODE_BUDGET) -> None:
    """Check serial launch ceilings, allowing an already billed final response overshoot."""
    charged = Decimal("0")
    for item in charges:
        if item.source == "rejected":
            continue  # Host budget rejection did not launch a CLI process.
        assert item.budget is not None and item.cost is not None
        ceiling = Decimal(item.budget["max_cost"])
        assert 0 < ceiling <= min(Decimal(node_budget.max_cost), remaining - charged)
        charged += item.cost


def _assert_current_agent_identity(observation: dict, model: str) -> None:
    assert observation["request"]["schema_version"] == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION
    assert observation["response"]["model_identity"] == model
    assert observation["response"]["adapter_version"] == CLAUDE_AGENT_HOST_VERSION


@pytest.mark.parametrize("version_offset", [-1, 0, 1])
def test_current_g5_exact_write_identity_requires_current_version(version_offset):
    model = "claude-fable-5-1[1m]"
    observation = {
        "request": {"schema_version": CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION + version_offset},
        "response": {"model_identity": model, "adapter_version": CLAUDE_AGENT_HOST_VERSION},
    }
    if version_offset == 0:
        _assert_current_agent_identity(observation, model)
    else:
        with pytest.raises(AssertionError):
            _assert_current_agent_identity(observation, model)


@pytest.mark.parametrize("field", ["model_identity", "adapter_version"])
def test_current_g5_current_write_identity_keeps_exact_model_and_adapter(field):
    model = "claude-fable-5-1[1m]"
    observation = {
        "request": {"schema_version": CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION},
        "response": {"model_identity": model, "adapter_version": CLAUDE_AGENT_HOST_VERSION},
    }
    observation["response"][field] += "-other"
    with pytest.raises(AssertionError):
        _assert_current_agent_identity(observation, model)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, timeout=10
    )


def _profile(
    root: Path, model: str, prior_charged_usd: Decimal = _PRIOR_CHARGED_USD,
    *, common_acceptance: bool = False,
    fresh_allocation_usd=None, node_budget: AgentBudget = _NODE_BUDGET,
    run_wall_seconds: float = _WALL_SECONDS, run_turns: int = _TURN_LIMIT,
    max_rounds_per_expert: int = 1, max_experiments: int = 2,
    reading_compatibility: bool = False,
) -> tuple[Path, dict]:
    """Create only a fresh fixture Git target and six admitted source YAMLs."""
    allocation = _positive_allocation(
        _CUMULATIVE_USD - prior_charged_usd if fresh_allocation_usd is None else fresh_allocation_usd)
    if (isinstance(run_wall_seconds, bool) or not isinstance(run_wall_seconds, (int, float))
            or not 0 < run_wall_seconds < float("inf")):
        raise ValueError("run_wall_seconds must be finite and positive")
    if isinstance(run_turns, bool) or not isinstance(run_turns, int) or run_turns <= 0:
        raise ValueError("run_turns must be a positive integer")
    for name, value in (("max_rounds_per_expert", max_rounds_per_expert), ("max_experiments", max_experiments)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if not isinstance(node_budget, AgentBudget) or Decimal(node_budget.max_cost) <= 0:
        raise ValueError("node_budget must retain a positive cost ceiling")
    root.mkdir(parents=True, exist_ok=True)
    # Runner reserves /tmp for scratch. Sources under pytest's /tmp directory
    # would be rejected/hidden by that production mount, even after admission.
    mount = Path(tempfile.mkdtemp(prefix="prom-current-g5-", dir="/var/tmp"))
    target, data, profile = mount / "target", mount / "data", root / "profile"
    for path in (target, data, profile):
        path.mkdir()
    (root / "fixture-paths.json").write_text(
        json.dumps(
            {
                "target": str(target),
                "data": str(data),
                "profile": str(profile),
            },
            indent=2,
        )
    )
    (data / "validation.json").write_text(
        json.dumps(
            {
                "labels": [0, 1, 1, 0, 1, 0, 1, 0],
                "predictions": [0, 1, 1, 0, 1, 0, 1, 1],
            }
        )
    )
    (target / "accuracy.py").write_text(
        "def accuracy(labels, predictions):\n    return 0.0  # Qualification fixture: repair this incorrect implementation.\n"
    )
    (target / "test_accuracy.py").write_text(
        "import unittest\nfrom accuracy import accuracy\n\n"
        "class AccuracyTest(unittest.TestCase):\n"
        "    def test_known_validation_pairs(self):\n"
        "        self.assertEqual(accuracy([0, 1, 1, 0, 1, 0, 1, 0], [0, 1, 1, 0, 1, 0, 1, 1]), 0.875)\n"
    )
    objective = (
        "Qualify the complete current CPU research workflow on a tiny deterministic accuracy task. "
        "Read the actual accuracy.py/test_accuracy.py and validation.json. In DESIGN, "
        "invoke native Read with file_path=/workspace/accuracy.py before implementing the experiment. "
        "Then first run "
        "python -m unittest -v test_accuracy.py to observe the seeded failure, fix the implementation, "
        "and rerun the test successfully in the same session. Implement experiment.py that reads "
        "validation.json from the admitted HYPOLOOM_READ_ROOTS and calls the repaired accuracy "
        "function to calculate, never hardcode, the fraction of equal labels/predictions. At experiment "
        "runtime, add HYPOLOOM_TARGET_ROOT to sys.path and import accuracy from that read-only "
        "target: the Host applies this submitted candidate's target patch on the current submitted-target path. "
        "Do not copy the function into a standalone workaround. "
        + ("Use GOAL evaluation_scope and the Grounding-defined raw output contract; keep local measurement optional. "
           if common_acceptance else
           "Use the exact rendered result contract and HYPOLOOM_RESULT_PATH. "
           "This Profile's declared metric already defines evaluation; common acceptance was not commissioned. "
           "GROUNDING must return acceptance: null and observed background, without authoring another evaluator. "
           "The shared_goal completion mode does not change that commission boundary. ")
        + "Choose CPU and one hypothesis; no training, downloads, package installs or public web are needed. "
        "The supplied literature is explicitly a qualification fixture, not new scientific evidence. "
        "Host ends this shared-goal run once one accepted experiment satisfies the frozen criterion. "
        "The final node must invoke the native final-report Skill and report actual failures and limitations."
    )
    if reading_compatibility:
        objective += (
            " In the Literature MAIN node, fetch both supplied synthetic method documents with native "
            "web_fetch mode=document: " + ", ".join(_READING_DOCUMENTS) + ". "
            "Submit two independent questions together using read_papers action=submit, one per returned "
            "source_ref: identify the method's coefficient or window length, quote the exact source passage "
            "with page/line location, and state its limitation. Use reading_id method-a and method-b. "
            "Each reader must use native Read on its mounted /papers source text before answering. "
            "Collect both answers using read_papers action=wait, then synthesize the two observed details "
            "in your Literature summary and cite their sources. These are synthetic compatibility fixtures; "
            "do not claim scientific validation. Do not fetch any other document or perform public search. "
            "After reading, continue the existing accuracy experiment and final-report workflow."
        )
    if common_acceptance:
        objective += (
            " The common goal is accuracy >= 0.875 on all eight supplied validation pairs, not local hypothesis support. "
            "Grounding's final answer must be one JSON object with exactly the top-level keys typed_candidate "
            "and declared_effects. Put task_kind, supporting_refs, background, web_sources, clarification and "
            "acceptance inside typed_candidate; do not move these six fields to the outer object or add prose. "
            "Grounding: read the real project and data without fixing the expert's target. Write /acceptance/evaluate.py "
            "and /acceptance/test_evaluate.py; run that unittest suite using Bash in /acceptance before "
            "returning completed background and acceptance. Test all-correct, all-wrong and mixed pairs; never hardcode "
            "a measured score. Keep the standard-library tests compact and table-driven. For invalid evidence, "
            "assert the relevant validity check is false and outputs remain finite; do not demand a meaningful "
            "accuracy from invalid input. After the tests and complete smoke fixture pass, return the final JSON "
            "promptly; no extra test framework, documentation or work notes are needed for this tiny fixture. "
            "Declare the one numeric criterion accuracy GE 0.875, ALL rule, validity checks "
            "complete_pairs and computed_accuracy_matches, parser_files containing evaluate.py and raw_artifacts "
            "containing predictions.json. The expert's raw file must carry labels, predictions and computed_accuracy, "
            "with computed_accuracy obtained by calling the repaired target function. The common parser independently "
            "recomputes accuracy from these arrays, checks all eight original pairs are present unchanged, and checks "
            "that computed_accuracy matches that calculation. Its exact output has measurements {accuracy: decimal} "
            "and checks {complete_pairs: boolean, computed_accuracy_matches: boolean}. Experts must use "
            "Read or Bash cat /acceptance/evaluate.py to read the actual mounted file, not an embedded source literal. "
            "In DESIGN, invoke native Read with file_path=/acceptance/evaluate.py before implementing the experiment. "
            "If using Bash cat instead, its tool response must display the source contents. Redirecting cat to "
            "/dev/null, echoing a readability check, or only executing the parser does not satisfy this read. "
            "Preserve its read-only source, implement the real target repair and submit GOAL. "
            "Use the supplied unambiguous numeric goal; ask the user only if a consequential ambiguity genuinely remains."
        )
    (target / "README.md").write_text(objective + "\n")
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Hypoloom Canary",
        "-c",
        "user.email=canary@example.invalid",
        "commit",
        "-qm",
        "Disposable paid qualification target",
    )
    source = documents()
    source["goal"] = {
        "title": "Bounded current Host accuracy canary",
        "description": objective,
        **({"evaluation": "grounding"} if common_acceptance else {
            "metric": {"name": "accuracy", "direction": "maximize", "target": 0.875}}),
        "constraints": [
            "CPU only; no provider except the approved model endpoint",
            "Never modify admitted data",
        ],
        "stop_conditions": {
            "max_rounds_per_expert": max_rounds_per_expert,
            "max_wall_clock_hours": run_wall_seconds / 3600,
        },
    }
    source["experts"] = {
        "defaults": {
            "model": model,
            "effort": "medium",
            "max_forks": 1,
            "max_hypotheses_per_round": 1,
        },
        "experts": [
            {
                "name": "researcher",
                "role": "CPU validation researcher",
                "persona": "Measure honestly and repair ordinary implementation errors.",
                "focus": [objective],
            }
        ],
    }
    source["methodology"] = {
        "schema_version": 4,
        # Both parser modes use the existing correction allowance of the
        # resource canary and current real-topic Profile.
        "output_correction": {"max_failed_validations": 3},
        "runtime": {"target_repo": str(target), "conda_env": sys.prefix},
        "budget": {
            "max_active_hypotheses": 1,
            "max_active_agents": 3 if reading_compatibility else 1,
            "max_active_processes": 1,
            "max_active_reviews": 1,
            "max_experiments": max_experiments,
            "max_retries": 1,
            "max_infrastructure_retries": 0,
            "max_process_lost_retries": 0,
            "max_turns": run_turns,
            "agent_invocation": node_budget.to_dict(),
        },
        "literature": {"enabled": reading_compatibility,
                       "tools": ["web_fetch", "read_papers"] if reading_compatibility else [],
                       "max_sources": 3},
        "inner_loop": {"max_iterations_per_hypothesis": 1, "repair_attempts": 1},
        "interaction": {
            "leader": {"enabled": False},
            "debate": {"enabled": False, "rounds": 3},
        },
        "graph_evolution": {"enabled": False, "stagnation_rounds": 1},
    }
    source["language"] = {
        key: "en"
        for key in ("prompt_language", "report_language", "code_comment_language")
    }
    source["notify"] = {"sinks": {}, "events": {}}
    source["safety"] = {
        "allow_read_paths": [str(data)],
        "sandbox_os": "linux_bwrap",
        "gpus": [],
        "max_gpus_per_experiment": 1,
        "experiment_timeout_minutes": 0.5,
        "total_budget_usd": float(allocation),
        "per_expert_budget_usd": float(allocation),
        "bash": {"deny_patterns": [], "network_allowed": False},
        "filesystem": {
            "write_only_within_worktree": True,
            "protected_paths": [".git/"],
        },
    }
    for name, value in source.items():
        (profile / (name + ".yaml")).write_text(yaml.safe_dump(value), encoding="utf-8")
    admission = admit_profile(profile, run_id=_RUN_ID + ("-common" if common_acceptance else ""), allow_gpu=False)
    assert admission.admitted and admission.create_run_payload is not None, (
        admission.to_dict()
    )
    (root / "profile-admission.json").write_text(
        json.dumps(admission.to_dict(), indent=2)
    )
    return profile, admission.create_run_payload


class _NoPublicWeb:
    def web_search(self, *_args, **_kwargs):
        raise ValueError(
            "Public web is outside this bounded G5; use the explicitly supplied fixture and report this limitation."
        )

    web_fetch = web_search


_READING_DOCUMENTS = {
    "https://example.org/qualification/method-a": (
        b"<title>Synthetic method A</title><h2>Method</h2>"
        b"<p>The prefix-only weighting coefficient is 0.83.</p>"
        b"<p>Limitation: fixed synthetic validation pairs; no evidence for unseen speakers.</p>"
    ),
    "https://example.org/qualification/method-b": (
        b"<title>Synthetic method B</title><h2>Method</h2>"
        b"<p>The trailing context window length is 5 turns.</p>"
        b"<p>Limitation: truncated context omits dependencies beyond that window.</p>"
    ),
}


def _reading_fixture_web():
    from hypoloom.grounding_web import GroundingWeb
    from test_grounding_web import Transport, _response

    # Only document transport is a fixture. The explicitly injected model and
    # native tools remain real; this does not claim public-network coverage.
    return GroundingWeb(Transport({url: _response(url, body)
                                  for url, body in _READING_DOCUMENTS.items()}))


def _native_result_text(result):
    content = result.get("content", "")
    return content if isinstance(content, str) else "\n".join(
        item["text"] for item in content if isinstance(item, dict) and item.get("type") == "text")


def _assert_reading_canary_evidence(host):
    readers, collected, summaries = {}, set(), []
    for row in host.store.list_attempts():
        if row["kind"] != "AGENT" or row["terminal_reason"] != "SUCCEEDED":
            continue
        observation = json.loads(row["observation_json"])
        archive = host.publisher.root / observation["session"]["transcript"]["path"]
        uses, results = _native_tools((archive / "transcript.jsonl").read_bytes())
        if observation["request"]["command"] == "PAPER_READ":
            assert {u["name"] for u in uses} <= {"Read", "Glob", "Grep"}
            sources = observation["session"]["paper_sources"]
            assert len(sources) == 1
            source = sources[0]
            assert source["url"] in _READING_DOCUMENTS
            from hypoloom.paper_sources import source_paths
            original, pages, _index = source_paths(source)
            assert (archive / original).read_bytes() == _READING_DOCUMENTS[source["url"]]
            read_ids = {u["id"] for u in uses if u["name"] == "Read"
                        and u["input"].get("file_path") == "/" + pages}
            detail = "0.83" if source["url"].endswith("method-a") else "5 turns"
            assert any(r.get("tool_use_id") in read_ids and not r.get("is_error")
                       and detail in _native_result_text(r) for r in results)
            answer = observation["response"]["typed_candidate"]["summary"]
            assert detail in answer
            readers[source["url"]] = {"attempt_id": row["attempt_id"], "detail": detail}
        if (observation["request"]["command"] == "LITERATURE_REVIEW"
                and observation["request"].get("task_context", {}).get("key", {}).get("subkind") == "MAIN"):
            wait_ids = {u["id"] for u in uses if u["name"] == "mcp__host-tools__read_papers"
                        and u["input"].get("action") == "wait"}
            for result in results:
                if result.get("tool_use_id") not in wait_ids or result.get("is_error"):
                    continue
                reply = json.loads(_native_result_text(result))
                if reply.get("terminal"):
                    collected.update(item["source_archive_attempt_id"] for item in reply["readings"]
                                     if item["status"] == "completed")
            summaries.append(observation["response"]["typed_candidate"]["summary"])
    assert set(readers) == set(_READING_DOCUMENTS)
    assert {r["attempt_id"] for r in readers.values()} <= collected
    assert any(all(r["detail"] in summary for r in readers.values()) for summary in summaries)
    return {"reader_sources": readers, "collected_attempt_ids": sorted(collected),
            "scope": "real-model native reading/collection of two synthetic documents; not scientific quality or throughput"}


def test_current_g5_reading_fixture_uses_existing_host_and_bounded_admission(tmp_path):
    profile, payload = _profile(tmp_path, "fixture-pinned-model", fresh_allocation_usd="4",
                                node_budget=replace(_NODE_BUDGET, max_cost="1"), reading_compatibility=True)
    contract = payload["research_contract"]
    assert contract["budget"]["max_active_agents"] == 3
    assert contract["budget"]["total_budget_usd"] == "4"
    assert contract["budget"]["agent_invocation"]["max_cost"] == "1"
    objective = yaml.safe_load((profile / "goal.yaml").read_text())["description"]
    assert "GROUNDING must return acceptance: null" in objective
    assert "common acceptance was not commissioned" in objective
    web = _reading_fixture_web()
    for url, body in _READING_DOCUMENTS.items():
        result = web.document(url)
        assert result["url"] == url
    with pytest.raises(KeyError):
        web.document("https://example.org/outside-fixture")


def test_reading_checker_ignores_query_plan_but_still_requires_readers(tmp_path):
    from types import SimpleNamespace

    (tmp_path / "transcript.jsonl").write_text("")
    observation = {
        "request": {"command": "LITERATURE_REVIEW",
                    "task_context": {"kind": "RESEARCH_NODE", "key": {"subkind": "QUERY_PLAN"}}},
        "response": {"typed_candidate": {"query": "classification accuracy"}},
        "session": {"transcript": {"path": "."}},
    }
    row = {"kind": "AGENT", "terminal_reason": "SUCCEEDED",
           "observation_json": json.dumps(observation)}
    host = SimpleNamespace(store=SimpleNamespace(list_attempts=lambda: [row]),
                           publisher=SimpleNamespace(root=tmp_path))
    # Query plans have no synthesis summary. Ignoring that phase must still
    # reject a sample with no actual readers, native reads or parent collection.
    with pytest.raises(AssertionError):
        _assert_reading_canary_evidence(host)


def _native_tools(content: bytes) -> tuple[list[dict], list[dict]]:
    uses, results = [], []
    for line in content.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message", {})
        if not isinstance(message, dict):
            continue
        blocks = message.get("content", ())
        if not isinstance(blocks, list):
            continue
        uses.extend(
            item
            for item in blocks
            if isinstance(item, dict) and item.get("type") == "tool_use"
        )
        results.extend(
            item
            for item in blocks
            if isinstance(item, dict) and item.get("type") == "tool_result"
        )
    return uses, results


def _has_common_source_read(uses, results, source: bytes) -> bool:
    read_ids = {item["id"] for item in uses if item["name"] == "Read"
                and item["input"].get("file_path") == "/acceptance/evaluate.py"}
    cat_ids = {item["id"] for item in uses if item["name"] == "Bash"
               and re.fullmatch(r'cat /acceptance/evaluate\.py(?: 2>/dev/null)?'
                                r'(?: \|\| echo "File not found")?',
                                item["input"].get("command", "").strip())}
    for result in results:
        if result.get("is_error"):
            continue
        if result.get("tool_use_id") in read_ids:
            return True
        if result.get("tool_use_id") not in cat_ids:
            continue
        content = result.get("content")
        if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") == "text"
                and isinstance(item.get("text"), str) for item in content):
            content = "\n".join(item["text"] for item in content)
        if isinstance(content, str) and content.removeprefix("/bin/bash: warning: setlocale: LC_ALL: cannot change locale (C.UTF-8)\n").rstrip("\n") == source.decode("utf-8").rstrip("\n"):
            return True
    return False


def _assert_common_goal_outcome(payload: dict, source_attempt_id: str) -> None:
    assert payload.get("goal_evaluation") == {
        "source_attempt_id": source_attempt_id,
        "measurements": {"accuracy": "0.875"},
        "checks": {"complete_pairs": True, "computed_accuracy_matches": True},
    }


def _assert_canary_outcome_hypotheses(outcomes, preregistrations, batches):
    hypotheses = {item["experiment_id"]: item["hypothesis_id"] for item in preregistrations}
    experiments = [item["experiment_id"] for item in outcomes]
    assert len(hypotheses) == len(preregistrations)
    assert len(set(experiments)) == len(experiments)
    assert set(experiments) == set(hypotheses)
    assert {hypotheses[experiment] for experiment in experiments} == {
        hypothesis for batch in batches for hypothesis in batch["chains"].values()}


def _assert_canary_research_cycles(accepted, batches, *, max_rounds_per_expert=1):
    """Check actual accepted batches against this one-chain sample's node work."""
    assert 1 <= len(batches) <= max_rounds_per_expert
    assert [batch["cycle"] for batch in batches] == list(range(1, len(batches) + 1))
    inner_commands = ("EXPERIMENT_DESIGN_IMPLEMENT", "EXPERIMENT_EXECUTION", "RESULT_ANALYSIS")
    controls = {"ADJUDICATE", "HYPOTHESIS_UPDATE", "CYCLE_REPORT", "SELF_EVOLVE"}
    cycle_commands = {"LITERATURE_REVIEW", "HYPOTHESIS_PROPOSAL", *inner_commands, *controls}
    research = [item for item in accepted if item["request"]["command"] in cycle_commands
        and item["request"]["task_context"]["kind"] == "RESEARCH_NODE"]
    assert {item["request"]["task_context"]["key"]["cycle"] for item in research} == {batch["cycle"] for batch in batches}
    for batch in batches:
        cycle, chains = batch["cycle"], batch["chains"]
        assert len(chains) == 1, "the canary profile admits one chain per cycle"
        current = [item for item in research if item["request"]["task_context"]["key"]["cycle"] == cycle]
        shared = [item for item in current if item["request"]["command"] not in inner_commands]
        assert all(item["request"]["task_context"]["key"].get("chain") is None for item in shared)
        for command, subkinds in (("LITERATURE_REVIEW", ("QUERY_PLAN", "MAIN")),
                                  ("HYPOTHESIS_PROPOSAL", ("FORWARD", "BACKWARD", "SYNTHESIS"))):
            assert Counter(item["request"]["task_context"]["key"]["subkind"] for item in current
                if item["request"]["command"] == command) == Counter(subkinds)
        inner = [item for item in current if item["request"]["command"] in inner_commands]
        assert {item["request"]["task_context"]["key"].get("chain") for item in inner} == set(chains)
        for chain in chains:
            nodes = [item for item in inner if item["request"]["task_context"]["key"].get("chain") == chain]
            assert [item["request"]["command"] for item in nodes] == list(inner_commands)
            session_id = nodes[0]["session"]["session_id"]
            assert session_id and all(item["session"]["session_id"] == session_id for item in nodes)
            assert all(item["session"]["launch_arguments"] == ["--resume", session_id] for item in nodes[1:])
        closed = [item for item in current if item["request"]["command"] in controls]
        if cycle < len(batches):
            assert Counter(item["request"]["command"] for item in closed) == Counter(controls)
            update = next(item for item in closed if item["request"]["command"] == "HYPOTHESIS_UPDATE")
            assert update["response"]["typed_candidate"]["update_disposition"] == "REVISE_NEXT_CYCLE"
        else:
            assert not closed, "shared success stops its cycle before batch controls"


def _has_seeded_repair(uses, results):
    positions = {item["id"]: index for index, item in enumerate(uses) if item["name"] == "Bash"}
    # In the observed native trace echo succeeds after printing unittest's
    # nonzero status. Preserve the actual test outcome, not echo's shell status.
    # Both interpreter names and exit-label cases occur in native traces.
    # Inspection commands after this immediate echo do not alter the test's
    # already printed status; do not require echo to end the whole shell call.
    printed_exit = {item["id"] for item in uses if item["name"] == "Bash"
        # The retained DESIGN call first displays its admitted validation file,
        # then changes to the same workspace. Neither changes the seeded test.
        and re.match(r'(?:cat /var/tmp/prom-current-g5-[A-Za-z0-9_-]+/data/validation\.json; echo "---"; )?'
                     r'(?:cd /workspace && )?'
                     r'python3? -m unittest -v test_accuracy\.py(?: 2>&1)?; echo "(?:exit|EXIT)=\$\?"(?:;|$)',
                     item.get("input", {}).get("command", "").strip())}
    # Native traces pipe unittest through tail or follow it with ls. The full
    # test summary still proves failure when that final command exits zero.
    summary_test = {item["id"] for item in uses if item["name"] == "Bash"
        and re.fullmatch(r'python -m unittest -v test_accuracy\.py 2>&1'
                         r'(?: \| tail -[1-9][0-9]*|; ls (?:/workspace|\.))',
                         item.get("input", {}).get("command", "").strip())}
    # This observed wrapper copies the known, unchanged fixture before testing.
    # Its short tail loses the method name but retains the full failure summary.
    copied_seeded_test = {item["id"] for item in uses if item["name"] == "Bash"
        and re.fullmatch(r'cd /tmp && mkdir -p (?P<scratch>[A-Za-z0-9][A-Za-z0-9_-]*)'
                         r' && cp /workspace/accuracy\.py /workspace/test_accuracy\.py (?P=scratch)/'
                         r' && cd (?P=scratch) && python -m unittest -v test_accuracy\.py 2>&1 \| tail -[1-9][0-9]*',
                         item.get("input", {}).get("command", "").strip())}
    summary_test.update(copied_seeded_test)
    failures = [positions[item["tool_use_id"]] for item in results if item.get("tool_use_id") in positions
        and (item.get("is_error") or (item.get("tool_use_id") in printed_exit
            and isinstance(item.get("content"), str)
            and re.search(r"(?m)^Ran 1 test in [0-9.]+s$", item["content"])
            and re.search(r"(?m)^FAILED \(failures=1\)\n(?:exit|EXIT)=[1-9][0-9]*$", item["content"]))
            or (item.get("tool_use_id") in summary_test and isinstance(item.get("content"), str)
                and re.search(r"(?m)^Ran 1 test in [0-9.]+s$", item["content"])
                and re.search(r"(?m)^FAILED \(failures=1\)$", item["content"])))
        and ("test_known_validation_pairs" in json.dumps(item.get("content")) or item.get("tool_use_id") in copied_seeded_test)
        and "FAILED" in json.dumps(item.get("content"))]
    successes = [positions[item["tool_use_id"]] for item in results if item.get("tool_use_id") in positions
        and not item.get("is_error") and "Ran 1 test" in json.dumps(item.get("content"))
        and "OK" in json.dumps(item.get("content"))]
    edits = [index for index, item in enumerate(uses) if item["name"] in {"Write", "Edit"}]
    return any(failed < edited < passed for failed in failures for edited in edits for passed in successes)


def _assert_common_canary_evidence(host, run_id, accepted, outcomes) -> dict:
    grounding = [item for item in accepted if item["request"]["command"] == "GROUNDING"
                 and item["response"]["typed_candidate"].get("acceptance") is not None]
    assert len(grounding) == 1
    observed = grounding[0]
    source_attempt_id = observed["request"]["attempt_id"]
    declaration = observed["response"]["typed_candidate"]["acceptance"]
    assert declaration["rule"] == "ALL"
    assert declaration["criteria"] == [{"name": "accuracy", "operator": "GE", "target": "0.875"}]
    assert set(declaration["validity_checks"]) == {"complete_pairs", "computed_accuracy_matches"}
    assert "evaluate.py" in declaration["parser_files"]
    assert declaration["raw_artifacts"] == ["predictions.json"]
    source = {item.path: item.content_bytes() for raw in observed["response"]["proposed_workspace_artifacts"]
              if (item := WorkspaceArtifact.from_dict(raw)).path in declaration["parser_files"]}
    archive = host.publisher.root / observed["session"]["transcript"]["path"]
    assert set(source) == set(declaration["parser_files"])
    assert all(content and (archive / "evaluator" / path).read_bytes() == content for path, content in source.items())
    uses, results = _native_tools((archive / "transcript.jsonl").read_bytes())
    authored = [index for index, item in enumerate(uses) if item["name"] in {"Write", "Edit"}
                and item["input"].get("file_path") == "/acceptance/evaluate.py"]
    # Observe the authored evaluator's real suite, not an arbitrary test-method
    # name. unittest accepts the exact file or module spelling; a matching
    # tool result must prove nonzero tests actually ran.
    tested = {item["id"]: index for index, item in enumerate(uses) if item["name"] == "Bash"
              and all(part in item["input"].get("command", "")
                      for part in ("unittest", "/acceptance"))
              and re.search(r"(?:^|[\s'\"])(?:/acceptance/)?test_evaluate(?:\.py)?(?=$|[\s'\";&|])",
                            item["input"].get("command", ""))}
    successful = [tested[item["tool_use_id"]] for item in results if item.get("tool_use_id") in tested
                  and not item.get("is_error") and re.search(r"Ran [1-9][0-9]* tests? in ", json.dumps(item.get("content")))
                  and "OK" in json.dumps(item.get("content"))]
    assert any(written < passed for written in authored for passed in successful), "Grounding must actually author and test its evaluator"
    designs = [item for item in accepted if item["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT"]
    assert designs
    for design in designs:
        assert design["response"]["typed_candidate"]["evaluation_scope"] == "GOAL"
        shared = [json.loads(EmbeddedInput.from_dict(item).content_bytes())
                  for item in design["request"]["input_artifacts"] if item["ref"].endswith("--shared-acceptance")]
        assert len(shared) == 1 and shared[0]["source_attempt_id"] == source_attempt_id
        assert shared[0]["acceptance"] == declaration
        assert {item.path: item.content_bytes() for raw in shared[0]["files"] if (item := WorkspaceArtifact.from_dict(raw))} == source
        design_archive = host.publisher.root / design["session"]["transcript"]["path"]
        design_uses, design_results = _native_tools((design_archive / "transcript.jsonl").read_bytes())
        assert _has_common_source_read(design_uses, design_results, source["evaluate.py"])
    preregistrations = host.store.list_facts(run_id, "ExperimentPreregistered")
    assert len(preregistrations) == len(outcomes)
    assert all(item["payload"]["goal_evaluation"] == {"scope": "GOAL", "source_attempt_id": source_attempt_id,
        "acceptance": declaration} for item in preregistrations)
    _assert_common_goal_outcome(outcomes[-1]["payload"], source_attempt_id)
    return {"source_attempt_id": source_attempt_id, "source_archive": str(archive / "evaluator"),
        "acceptance": declaration, "goal_evaluation": outcomes[-1]["payload"]["goal_evaluation"]}


def test_current_g5_profile_is_admitted_with_finite_cpu_only_budgets(tmp_path):
    profile, payload = _profile(tmp_path / "qualification", "claude-fable-5-1[1m]")
    assert len(tuple(profile.glob("*.yaml"))) == 6
    assert payload["run_id"] == _RUN_ID == f"current-aci{CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION}-paid-canary"
    goal = yaml.safe_load((profile / "goal.yaml").read_text(encoding="utf-8"))
    assert goal["metric"] == {"name": "accuracy", "direction": "maximize", "target": 0.875}
    assert "evaluation" not in goal
    contract = ResearchContract.from_mapping(payload["research_contract"])
    budget = contract.document["budget"]
    assert (
        budget["total_budget_usd"] == "20"
        and budget["per_expert_budget_usd"] == "20"
    )
    assert budget["agent_invocation"] == _NODE_BUDGET.to_dict()
    assert budget["agent_invocation"] == {
        "max_turns": 24,
        "max_tokens": 100_000,
        "max_output_bytes": 131_072,
        "wall_seconds": 300,
        "max_cost": "2",
        "token_enforcement_mode": "observe",
    }
    assert budget["max_turns"] == _TURN_LIMIT == 240 and budget["experiment_timeout"] == 30
    assert budget["wallclock_hours"] == 0.25 and budget["max_experiments"] == 2
    assert budget["max_retries"] == 1 and budget["max_infrastructure_retries"] == 0
    assert len(contract.components) == len(contract.role_ids) == 1
    assert contract.document["methodology"]["controls"]["product"]["goal"]["completion_mode"] == "shared_goal"
    assert not payload["execution"]["allow_gpu"]
    fixture_paths = json.loads((profile.parent / "fixture-paths.json").read_text())
    assert Path(fixture_paths["target"]).is_relative_to("/var/tmp")
    assert Path(fixture_paths["data"]).is_relative_to("/var/tmp")
    assert not list(tmp_path.rglob("state.sqlite"))


@pytest.mark.parametrize("prior", [None, "-1", "-0.01", "NaN", "Infinity", "20", "21"])
def test_current_g5_requires_explicit_unspent_cumulative_allowance(monkeypatch, prior):
    monkeypatch.setenv("HYPOLOOM_G5_CUMULATIVE_LIMIT_USD", "20")
    monkeypatch.delenv("HYPOLOOM_G5_PRIOR_CHARGED_USD", raising=False)
    if prior is not None:
        monkeypatch.setenv("HYPOLOOM_G5_PRIOR_CHARGED_USD", prior)
    with pytest.raises(ValueError, match="prior charged USD"):
        _approved_prior_charged_usd()


def test_current_g5_later_spend_reduces_the_new_run_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPOLOOM_G5_CUMULATIVE_LIMIT_USD", "20")
    monkeypatch.setenv("HYPOLOOM_G5_PRIOR_CHARGED_USD", "4.25")
    prior = _approved_prior_charged_usd()
    assert prior == Decimal("4.25")
    _, payload = _profile(tmp_path / "qualification", "claude-fable-5-1[1m]", prior)
    budget = payload["research_contract"]["budget"]
    assert budget["total_budget_usd"] == budget["per_expert_budget_usd"] == "15.75"


@pytest.mark.parametrize("limit", [None, "0", "-1", "NaN", "Infinity", "20.01"])
def test_current_g5_requires_an_explicit_bounded_cohort_limit(monkeypatch, limit):
    monkeypatch.delenv("HYPOLOOM_G5_CUMULATIVE_LIMIT_USD", raising=False)
    if limit is not None:
        monkeypatch.setenv("HYPOLOOM_G5_CUMULATIVE_LIMIT_USD", limit)
    with pytest.raises(ValueError):
        _approved_cumulative_limit_usd()


def test_current_g5_new_cohort_can_explicitly_declare_zero_spend(monkeypatch):
    monkeypatch.setenv("HYPOLOOM_G5_CUMULATIVE_LIMIT_USD", "5")
    monkeypatch.setenv("HYPOLOOM_G5_PRIOR_CHARGED_USD", "0")
    assert _approved_prior_charged_usd() == 0
    monkeypatch.setattr(sys.modules[__name__], "_preflight", lambda: ("adapter", "model", Decimal("1.25")))
    assert _canary_launch(None, None, None) == ("adapter", "model", Decimal("1.25"), Decimal("3.75"))


def test_current_g5_final_response_may_overshoot_but_cannot_fund_another_launch():
    def charge(identity, ceiling, actual):
        return AgentAttemptCharge(
            identity,
            "researcher",
            "CLOSED",
            "response",
            1,
            1,
            Decimal(actual),
            {"max_cost": ceiling},
        )

    charges = [charge("first", "2", "2.4"), charge("last", "0.6", "0.9")]
    _assert_launch_thresholds(charges, Decimal("3"))
    assert charged_cost(charges) == Decimal("3.3")
    with pytest.raises(AssertionError):
        _assert_launch_thresholds(
            [*charges, charge("unauthorized", "0.1", "0.1")], Decimal("3")
        )
    with pytest.raises(AssertionError):
        _assert_launch_thresholds(
            [charge("excessive-invocation", "2.01", "0.1")], Decimal("3")
        )


def test_current_g5_accounted_usage_includes_unknown_declared_reservations():
    reported = AgentAttemptCharge(
        "reported",
        "researcher",
        "CLOSED",
        "response",
        120,
        2,
        Decimal("0.5"),
        _NODE_BUDGET.to_dict(),
    )
    unknown = AgentAttemptCharge(
        "timeout",
        "researcher",
        "CLOSED",
        "declared",
        100_000,
        24,
        Decimal("2"),
        _NODE_BUDGET.to_dict(),
    )
    # The existing fold counts unknown usage conservatively; this is not a
    # reported/provider total, just as declared USD is not an upstream bill.
    assert charged_totals([reported]) == (120, 2)
    assert charged_totals([reported, unknown]) == (100_120, 26)


@pytest.mark.parametrize(
    "failure", ["symlink-executable", "settings-mode", "model-selector"]
)
def test_current_g5_preflight_rejects_before_provider_or_run(
    tmp_path, monkeypatch, failure
):
    executable = tmp_path / "claude"
    if failure == "symlink-executable":
        executable.symlink_to(Path("/usr/bin/true").resolve())
    else:
        executable.write_text("#!/bin/sh\nexit 99\n")
        executable.chmod(0o700)
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n")
    settings.chmod(0o644 if failure == "settings-mode" else 0o600)
    monkeypatch.setenv("HYPOLOOM_G5_PRIOR_CHARGED_USD", str(_PRIOR_CHARGED_USD))
    monkeypatch.setenv("HYPOLOOM_G5_CUMULATIVE_LIMIT_USD", "20")
    monkeypatch.setenv("HYPOLOOM_CLAUDE_EXECUTABLE", str(executable))
    monkeypatch.setenv("HYPOLOOM_CLAUDE_SETTINGS", str(settings))
    monkeypatch.setenv(
        "HYPOLOOM_CLAUDE_MODEL",
        "claude-fable-5-1[1M][1m]"
        if failure == "model-selector"
        else "claude-fable-5-1[1m]",
    )

    def must_not_construct(**_kwargs):
        raise AssertionError("Invalid preflight reached the provider adapter")

    monkeypatch.setitem(_preflight.__globals__, "ClaudeAgentHost", must_not_construct)
    with pytest.raises(ValueError):
        _preflight()
    assert not list(tmp_path.rglob("state.sqlite"))
    assert not (tmp_path / "qualification").exists()


def test_current_g5_prompt_and_schema_sizes_are_observed_without_provider(tmp_path):
    _, payload = _profile(tmp_path / "qualification", "claude-fable-5-1[1m]")
    document = payload["research_contract"]
    component = document["components"][0]
    protocol = document["protocols"][component["protocol"]]
    metric = ResearchContract.from_mapping(document).components[0].metric
    assert metric is not None
    # Representative current rendered config plus the complete admitted profile;
    # future actual model/fact/session inputs cannot be measured before the run.
    config = EmbeddedInput.from_bytes(
        "canary--expert-config",
        "application/json",
        json.dumps(
            {
                "execution": payload["execution"],
                "required_experiment_spec": {
                    "metric": {
                        "name": metric.name,
                        "direction": metric.direction.value,
                        "target": metric.target,
                    },
                    "evaluator_version": OUTCOME_ADAPTER_VERSION,
                    "artifact_requirements": ["result.json"],
                    "result_contract": component_result_contract(component, protocol),
                },
            }
        ).encode(),
    )
    embedded = EmbeddedInput.from_bytes(
        "canary-profile", "application/json", json.dumps(payload).encode()
    )
    measurements = {}
    for command in (
        AgentCommand.EXPERIMENT_DESIGN_IMPLEMENT,
        AgentCommand.EXPERIMENT_EXECUTION,
    ):
        request = replace(
            default_request(command),
            schema_version=CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION,
            budget=_NODE_BUDGET,
        )
        request = replace(
            request, input_artifacts=(*request.input_artifacts, config, embedded)
        )
        prompt = _prompt(request, tool_workspace=True, git_authoring=True)
        schema = _output_schema(request)
        measurements[command.value] = {
            "aci_schema_version": request.schema_version,
            "prompt_bytes": len(prompt),
            "schema_bytes": len(json.dumps(schema).encode()),
        }
        assert request.schema_version == CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION and b"Bash" in prompt
    (tmp_path / "prompt-size-observations.json").write_text(
        json.dumps(measurements, indent=2)
    )
    assert all(0 < item["prompt_bytes"] < 100_000 for item in measurements.values())


@_PAID
def test_real_current_host_closes_tools_cpu_sessions_and_reports(tmp_path):
    _run_current_host_canary(tmp_path)


@_COMMON_PAID
def test_real_current_common_host_closes_grounding_source_goal_and_reports(tmp_path):
    _run_current_host_canary(tmp_path, common_acceptance=True)


def _canary_host_stop(host):
    if (halt := getattr(host, "operational_halt", None)) is not None:
        return f"operational_halt: {halt}"
    if getattr(host, "stop_requested", False) and host._backup_busy_reason() is None:
        return "stop_requested with no active effect"
    return None


def _drive_canary(host, run_id, run_wall_seconds, progress_callback):
    deadline = time.monotonic() + run_wall_seconds + 30
    while time.monotonic() < deadline:
        halt = _canary_host_stop(host)
        if halt is None:
            host.tick()
        if progress_callback is not None:
            progress_callback(host, run_id)
        state = host.store.latest_transition(run_id)["state"]
        if state == "WAITING_HUMAN":
            raise RuntimeError("WAITING_HUMAN: canary dispatch paused; an actual user answer is required")
        halt = halt or _canary_host_stop(host)
        if halt is not None:
            raise RuntimeError(f"canary stopped while durable run_state={state}: {halt}")
        if state in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)


def _assert_current_canary_closure(
    host, run_id, *, model, remaining, node_budget, run_turns,
    max_rounds_per_expert, max_experiments, common_acceptance,
):
    """Use the same read-only outcome checks for live and retained canaries."""
    assert host.store.latest_transition(run_id)["state"] == "COMPLETED"
    common_evidence = None
    rows = host.store.list_attempts()
    observations = {
        r["attempt_id"]: json.loads(r["observation_json"]) for r in rows
    }
    assert all(r["state"] == "CLOSED" for r in rows)
    accepted = [
        observations[r["attempt_id"]]
        for r in sorted(rows, key=lambda row: (row["started_at"], row["attempt_id"]))
        if r["kind"] == "AGENT" and r["terminal_reason"] == "SUCCEEDED"
    ]
    counts = Counter(o["request"]["command"] for o in accepted)
    assert _REQUIRED <= counts.keys(), counts
    assert counts["FINAL_REPORT"] == 1
    assert not {"OBSERVER", "DEBATE"} & counts.keys()
    batches = [transition["hypothesis_batch"] for row in host.store.inspect(
        "SELECT t.payload_json FROM workflow_transitions t JOIN workflow_commands c USING(command_id) "
        "WHERE t.workflow_id=? AND c.command_type='ACCEPT_HYPOTHESIS' ORDER BY t.revision", (run_id,))
        if (transition := json.loads(row["payload_json"])).get("hypothesis_batch") is not None]
    _assert_canary_research_cycles(accepted, batches, max_rounds_per_expert=max_rounds_per_expert)
    stop = host.store.inspect("SELECT payload_json FROM workflow_transitions WHERE workflow_id=? AND state='STOPPING' AND fact_id IS NOT NULL", (run_id,))
    assert len(stop) == 1 and json.loads(stop[0]["payload_json"])["stop_disposition"] == "COMPLETED"
    assert host.store.list_facts(run_id, "GoalVerdictRecorded")[0]["payload"]["verdict"] == "GOAL_MET"
    for observation in accepted:
        _assert_current_agent_identity(observation, model)
    uses = []
    seeded_repair = False
    for observation in accepted:
        directory = (
            host.publisher.root / observation["session"]["transcript"]["path"]
        )
        assert observation["session"]["transcript_complete"] is True
        current, results = _native_tools(
            (directory / "transcript.jsonl").read_bytes()
        )
        uses.extend(current)
        if observation["request"]["command"] == "EXPERIMENT_DESIGN_IMPLEMENT":
            seeded_repair |= _has_seeded_repair(current, results)
    assert seeded_repair, "The seeded test must fail, be repaired, and pass in at least one actual DESIGN session"
    assert {
        "Read",
        "Bash",
        "Skill",
        "mcp__host-tools__submit_experiment",
        "mcp__host-tools__wait_experiment",
    } <= {u["name"] for u in uses}
    for skill in ("final-report",):
        assert any(
            u["name"] == "Skill" and u["input"]["skill"].endswith(":" + skill)
            for u in uses
        )
    final = next(o for o in accepted if o["request"]["command"] == "FINAL_REPORT")
    final_archive = host.publisher.root / final["session"]["transcript"]["path"]
    final_files = final["response"]["proposed_workspace_artifacts"]
    assert len(final_files) == 1
    assert (
        final_archive / "FINAL_REPORT.md"
    ).read_bytes() == WorkspaceArtifact.from_dict(final_files[0]).content_bytes()
    outcomes = host.store.list_facts(run_id, "CanonicalOutcomeAccepted")
    assert 1 <= len(outcomes) <= max_experiments
    _assert_canary_outcome_hypotheses(outcomes, host.store.list_facts(run_id, "ExperimentPreregistered"), batches)
    if common_acceptance:
        common_evidence = _assert_common_canary_evidence(host, run_id, accepted, outcomes)
    else:
        assert outcomes[-1]["payload"]["outcome"] == "SUCCESS" and Decimal(
            outcomes[-1]["payload"]["metric_value"]
        ) == Decimal("0.875")
    processes = [r for r in rows if r["kind"] == "PROCESS"]
    assert len(processes) == len(outcomes) and any(
        r["terminal_reason"] == "SUCCEEDED" for r in processes
    )
    assert all(
        json.loads(r["observation_json"]).get("synthetic_before_spawn") is not True
        for r in processes
    )
    creations = host.store.inspect(
        "SELECT payload_json FROM workflow_transitions WHERE workflow_kind='EXPERIMENT' AND revision=1"
    )
    assert len(
        {
            json.loads(r["payload_json"])["source_submit_command_id"]
            for r in creations
        }
    ) == len(processes)
    charges = fold_agent_attempts(
        sorted(rows, key=lambda row: (row["started_at"], row["attempt_id"]))
    )
    assert all(c.source != "declared" for c in charges), charges
    _assert_launch_thresholds(charges, remaining, node_budget)
    assert charged_totals(charges)[1] <= run_turns
    audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
    assert audit.ok, audit.to_dict()
    assert audit.artifact_counts["TRANSCRIPT"] == sum(
        r["kind"] == "AGENT" for r in rows
    )
    assert audit.open_attempt_count == audit.pending_effect_count == 0
    assert not audit.owned_process_residue
    return common_evidence


def _run_current_host_canary(
    tmp_path, *, common_acceptance=False, adapter=None, model=None,
    fresh_allocation_usd=None, node_budget: AgentBudget = _NODE_BUDGET,
    run_wall_seconds: float = _WALL_SECONDS, run_turns: int = _TURN_LIMIT,
    max_rounds_per_expert: int = 1, max_experiments: int = 2,
    progress_callback=None, reading_compatibility=False,
):
    """Run only when explicitly called; callbacks may stop dispatch by raising.

    The default uses the old $20 cumulative preflight. Injected CC requires its
    own explicit fresh allocation; it neither settles nor resets old invoices.
    """
    adapter, model, prior, remaining = _canary_launch(adapter, model, fresh_allocation_usd)
    # Retain fresh /var/tmp sources/data and their exact paths beside the run.
    profile, payload = _profile(tmp_path / "qualification", model, prior, common_acceptance=common_acceptance,
        fresh_allocation_usd=fresh_allocation_usd, node_budget=node_budget,
        run_wall_seconds=run_wall_seconds, run_turns=run_turns,
        max_rounds_per_expert=max_rounds_per_expert, max_experiments=max_experiments,
        reading_compatibility=reading_compatibility)
    run_id = payload["run_id"]
    common_evidence = reading_evidence = None
    canary_failure = None
    frozen_yaml = {p.name: p.read_bytes() for p in profile.glob("*.yaml")}
    host = WorkflowHost(
        tmp_path / "state",
        claude_agent_host=adapter,
        claude_model=model,
        compute_resources=LocalComputeResources(
            inventory_provider=lambda: ComputeCapabilities(gpus=())
        ),
        literature_io=FixedLiteratureIO(_DeterministicOpenAlexFixture()),
        grounding_web=_reading_fixture_web() if reading_compatibility else _NoPublicWeb(),
    )
    try:
        host.initialize()
        created = host.handle(
            ControlRequest(1, "create-current-paid", 0, "CREATE_RUN", payload)
        )
        assert created.status == "accepted", created
        assert host.store.get_run(run_id)["agent_aci_version"] == str(CURRENT_EXPERT_LOOP_ACI_SCHEMA_VERSION)
        _drive_canary(host, run_id, run_wall_seconds, progress_callback)
        assert {p.name: p.read_bytes() for p in profile.glob("*.yaml")} == frozen_yaml
        common_evidence = _assert_current_canary_closure(
            host, run_id, model=model, remaining=remaining, node_budget=node_budget,
            run_turns=run_turns, max_rounds_per_expert=max_rounds_per_expert,
            max_experiments=max_experiments, common_acceptance=common_acceptance,
        )
        if reading_compatibility:
            reading_evidence = _assert_reading_canary_evidence(host)
    except BaseException as exc:
        canary_failure = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        try:
            # Preserve failures and budget exhaustion as failures; no extra paid
            # recovery attempt, synthetic successful report, or relaxed Oracle.
            rows = host.store.list_attempts()
            charges = fold_agent_attempts(rows)
            # Unknown usage remains a conservative reservation, not invented
            # provider-reported spend. The parent must resolve it before reuse.
            reported = sum(
                (
                    item.cost
                    for item in charges
                    if item.source not in {"declared", "rejected"}
                    and item.cost is not None
                ),
                Decimal("0"),
            )
            head = host.store.latest_transition(run_id)
            audit = Oracle(host.store.database_path, host.publisher.root).audit(run_id)
            (tmp_path / "current-g5-evidence.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        **({"common_acceptance": common_evidence} if common_acceptance else {}),
                        **({"reading_compatibility": reading_evidence} if reading_compatibility else {}),
                        "model": model,
                        "adapter": CLAUDE_AGENT_HOST_VERSION,
                        "run_state": None if head is None else head["state"],
                        "canary_failure": canary_failure,
                        "fixture_paths": json.loads(
                            (profile.parent / "fixture-paths.json").read_text()
                        ),
                        "limits": {
                            "cohort_budget_ceiling_usd": str(remaining + prior) if prior is not None else None,
                            "prior_charged_usd": str(prior) if prior is not None else None,
                            "total_reported_usd": str(remaining),
                            "per_expert_reported_usd": str(remaining),
                            "run_turns": run_turns,
                            "run_wall_seconds": run_wall_seconds,
                            "max_rounds_per_expert": max_rounds_per_expert,
                            "max_experiments": max_experiments,
                            "node": node_budget.to_dict(),
                        },
                        "charges": [asdict(c) for c in charges],
                        "charged_usd": str(charged_cost(charges)),
                        "reported_usd": str(reported),
                        "unreported_attempt_ids": [
                            item.attempt_id
                            for item in charges
                            if item.source == "declared"
                        ],
                        **_canary_accounting(prior, reported),
                        "remaining_reported_usd": str(
                            max(Decimal("0"), remaining - reported)
                        ),
                        "remaining_conservative_usd": str(
                            max(Decimal("0"), remaining - charged_cost(charges))
                        ),
                        "reported_threshold_exceeded": reported > remaining,
                        # Includes declared reservations for missing usage.
                        "accounted_tokens_and_turns": charged_totals(charges),
                        "accounting_scope": "CLI/Host-reported USD, not an upstream invoice; post-response thresholds may overshoot and do not authorize a further launch",
                        "oracle": audit.to_dict(),
                        "attempts": rows,
                    },
                    indent=2,
                    default=str,
                )
            )
        finally:
            host.close()
