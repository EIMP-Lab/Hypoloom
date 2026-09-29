"""The common-goal display projects facts without becoming another judge."""

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypoloom.monitor import build_status_projection
from tests.runtime.test_shared_acceptance_oracle import acceptance


def recorded_evidence():
    declaration = acceptance()
    candidate = {"acceptance": declaration, "clarification": None, "background": {}}
    attempts = [
        {
            "attempt_id": "final-grounding",
            "run_id": "run",
            "kind": "AGENT",
            "state": "CLOSED",
            "terminal_reason": "SUCCEEDED",
            "observation_json": json.dumps(
                {
                    "request": {"command": "GROUNDING"},
                    "response": {"typed_candidate": candidate},
                }
            ),
        }
    ]
    facts = [
        {
            "fact_type": "GoalDefined",
            "payload": {
                "research_contract": {
                    "schema_version": 3,
                    "goal": {"evaluation": {"mode": "grounding"}},
                }
            },
        }
    ]
    for index, scope in enumerate(("LOCAL", "GOAL", "GOAL"), 1):
        experiment_id = f"experiment-{index}"
        vector = (
            None
            if scope == "LOCAL"
            else {
                "source_attempt_id": "final-grounding",
                "measurements": {
                    "left": "0.6674" if index == 2 else "0.8",
                    "right": "0.7305000000000001",
                },
                "checks": {"valid": index == 2},
            }
        )
        facts.extend(
            [
                {
                    "fact_type": "ExperimentPreregistered",
                    "experiment_id": experiment_id,
                    "payload": {
                        "measurement": {"metric": {"name": "screening", "direction": "MAXIMIZE", "target": "0"},
                            "comparison": "DELTA_FROM_BASELINE", "parse_rule": "Paired validation delta"},
                        "goal_evaluation": {
                            "scope": scope,
                            "source_attempt_id": "final-grounding",
                            "acceptance": declaration,
                        }
                    },
                },
                {
                    "fact_type": "CanonicalOutcomeAccepted",
                    "experiment_id": experiment_id,
                    "payload": {
                        "outcome_id": f"outcome-{index}",
                        "artifact_id": f"artifact-{index}",
                        "component_id": f"component-{index}",
                        "method_family_id": f"candidate-{index}",
                        "outcome": "SUCCESS",
                        "metric_value": "0",
                        "goal_evaluation": vector,
                    },
                },
            ]
        )
    return attempts, facts


def status(tmp_path, attempts, facts):
    database = tmp_path / "display.sqlite"
    if not database.exists():
        database.write_bytes(b"display-only test fixture")
    reads = []

    def inspect(query, parameters):
        reads.append((query, parameters))
        if query.startswith("SELECT payload_json FROM workflow_transitions"):
            assert parameters == ("run", "run")
        elif query.startswith("SELECT c.command_type,t.payload_json"):
            assert parameters == ("run", "RESOURCE_BLOCKED", "RESOURCE_RECHECK")
        else:
            assert query.startswith("SELECT * FROM workflow_human_decisions")
        return []

    # No writer or fact/Attempt reader is exposed. Both views must be reused.
    store = SimpleNamespace(database_path=database, inspect=inspect)
    result = build_status_projection(
        store,
        "run",
        quiescent_for_backup=None,
        attempts=attempts,
        facts=facts,
        heads=[{"workflow_kind": "RUN", "state": "ACTIVE", "revision": 7}],
    )
    # Dialogue, stored pool binding and resource controls; common evidence reuses inputs.
    assert len(reads) == 3
    assert result["gpu_pool"] is None
    return result


def test_status_exposes_one_raw_source_and_each_scope_without_combining_scores(
    tmp_path,
):
    attempts, facts = recorded_evidence()
    before = deepcopy((attempts, facts))
    first = status(tmp_path, attempts, facts)
    common = first["shared_acceptance"]
    assert common["source_attempt_id"] == "final-grounding"
    assert common["acceptance"] == acceptance()
    assert [row["scope"] for row in common["outcomes"]] == ["LOCAL", "GOAL", "GOAL"]
    assert common["outcomes"][0]["goal_evaluation"] is None
    assert common["outcomes"][0]["local_measurement"]["comparison"] == "DELTA_FROM_BASELINE"
    assert common["outcomes"][1]["goal_evaluation"]["measurements"]["left"] == "0.6674"
    assert common["outcomes"][2]["goal_evaluation"]["checks"]["valid"] is False
    assert all(
        row["local_metric_value"] == "0" and row["local_outcome"] == "SUCCESS"
        for row in common["outcomes"]
    )
    assert all("goal_met" not in row for row in common["outcomes"])
    assert first["goal_verdict"] is None
    assert (attempts, facts) == before
    common["outcomes"][2]["goal_evaluation"]["checks"]["valid"] = True
    common["acceptance"]["criteria"][0]["operator"] = "GE"
    common["outcomes"][0]["local_measurement"]["metric"]["name"] = "changed"
    assert (attempts, facts) == before
    assert (
        status(tmp_path, attempts, facts)["shared_acceptance"]["acceptance"]
        == acceptance()
    )
    assert (tmp_path / "display.sqlite").read_bytes() == b"display-only test fixture"


@pytest.mark.parametrize(
    "case", ["legacy", "no-final", "question", "open", "failed", "foreign", "ambiguous"]
)
def test_status_does_not_invent_accepted_source(tmp_path, case):
    attempts, facts = recorded_evidence()
    if case == "legacy":
        facts[0]["payload"]["research_contract"]["schema_version"] = 2
    if case == "no-final":
        attempts.clear()
    if case == "question":
        observation = json.loads(attempts[0]["observation_json"])
        observation["response"]["typed_candidate"].update(
            background=None, clarification={"question": "Unanswered?"}
        )
        attempts[0]["observation_json"] = json.dumps(observation)
    if case == "open":
        attempts[0]["state"] = "OPEN"
    if case == "failed":
        attempts[0]["terminal_reason"] = "FAILED"
    if case == "foreign":
        attempts[0]["run_id"] = "another-run"
    if case == "ambiguous":
        attempts.append({**attempts[0], "attempt_id": "second-final"})
    assert status(tmp_path, attempts, facts)["shared_acceptance"] is None


def test_accepted_source_before_experiments_has_an_empty_outcome_list(tmp_path):
    attempts, facts = recorded_evidence()
    assert status(tmp_path, attempts, facts[:1])["shared_acceptance"]["outcomes"] == []


def test_display_retains_only_the_recorded_goal_verdict(tmp_path):
    attempts, facts = recorded_evidence()
    facts.append(
        {"fact_type": "GoalVerdictRecorded", "payload": {"verdict": "INCONCLUSIVE"}}
    )
    projected = status(tmp_path, attempts, facts)
    assert projected["goal_verdict"] == "INCONCLUSIVE"
    assert projected["shared_acceptance"]["outcomes"][0]["local_outcome"] == "SUCCESS"


def test_web_common_panel_renders_raw_values_and_reuses_its_read_only_node(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the browser display function")
    script = (
        Path(__file__).resolve().parents[2]
        / "hypoloom/web_observer_assets/app.js"
    ).read_text()

    def extract(start, end):
        return script[script.index(start) : script.index(end)]

    renderer = extract(
        "  function renderSharedAcceptance(", "  function renderRuntime("
    )
    helpers = "".join(
        [
            extract("  function element(", "  function svgElement("),
            extract("  function svgElement(", "  function setText("),
            extract("  function object(", "  function sharedGoal("),
            extract("  function expertClass(", "  function appendDefinition("),
        ]
    )
    attempts, facts = recorded_evidence()
    payload = status(tmp_path, attempts, facts)
    snapshot = {"status": payload, "hypotheses": [], "components": [], "experts": []}
    harness = r"""
const registry = new Map();
let insertions = 0;
const SVG_NS = "http://www.w3.org/2000/svg";
class Node {
  constructor() { this.children = []; this.textContent = ""; this.hidden = false; this.dataset = {}; }
  set id(value) { registry.set(value, this); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  after(_item) { insertions += 1; }
  setAttribute(name, value) { (this.attributes || (this.attributes = {}))[name] = String(value); }
  addEventListener() {}
}
const document = {createElement: () => new Node(), createElementNS: () => new Node()};
const byId = (id) => registry.get(id);
const list = (value) => Array.isArray(value) ? value : [];
registry.set("grounding-dialogue-panel", new Node());
const content = (node) => [node.textContent, ...node.children.map(content)].join("\n");
"""
    tail = """
renderSharedAcceptance(snapshot);
const first = byId("shared-acceptance-panel");
const rendered = content(first);
renderSharedAcceptance(snapshot);
const reused = first === byId("shared-acceptance-panel");
renderSharedAcceptance({status: {shared_acceptance: null}});
process.stdout.write(JSON.stringify({rendered, reused, insertions, hidden: first.hidden, remaining: first.children.length}));
"""
    observed = json.loads(
        subprocess.run(
            [
                node,
                "-e",
                harness
                + helpers
                + renderer
                + "const snapshot = "
                + json.dumps(snapshot)
                + ";"
                + tail,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    )
    for value in (
        "final-grounding",
        "0.6674",
        "0.7305000000000001",
        '"valid": false',
        '"operator": "GT"',
        "LOCAL: not a common-goal evaluation",
        "Local outcome: SUCCESS · local metric: 0",
        "candidate-3",
        "artifact-3",
    ):
        assert value in observed["rendered"]
    assert "GOAL_MET" not in observed["rendered"]
    assert observed == {
        **observed,
        "reused": True,
        "insertions": 1,
        "hidden": True,
        "remaining": 0,
    }
    assert "innerHTML" not in renderer and "fetch(" not in renderer
