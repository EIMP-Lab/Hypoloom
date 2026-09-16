from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from hypoloom.agent_context import _analysis_experiment_id
from hypoloom.expert_loop import (
    ResearchNodeKey,
    ResearchNodeKind,
    ResearchNodeTask,
)


def _selection():
    key = ResearchNodeKey(
        run_id="run-1",
        component_id="primary.score",
        expert_slot="researcher",
        cycle=1,
        chain="chain-a",
        node_kind=ResearchNodeKind.RESULT_ANALYSIS,
        experiment_ordinal=2,
    )
    task = ResearchNodeTask(key=key)
    command = replace(key, node_kind=ResearchNodeKind.EXPERIMENT_EXECUTION).command_id
    base = {
        "role": key.expert_slot,
        "component_id": key.component_id,
        "expert_cycle": 1,
        "chain": key.chain,
        "chain_iteration": 2,
        "execution_node_command_id": command,
    }
    creations = [
        ("preflight", {**base, "experiment_ordinal": 6}),
        (
            "failed",
            {
                **base,
                "experiment_ordinal": 8,
                "preflight_repair_of_experiment_id": "preflight",
            },
        ),
        (
            "success",
            {**base, "experiment_ordinal": 11, "revision_of_outcome_id": "out-failed"},
        ),
        ("other-chain", {**base, "chain": "chain-b", "experiment_ordinal": 2}),
        ("other-iteration", {**base, "chain_iteration": 1, "experiment_ordinal": 2}),
        (
            "other-command",
            {
                **base,
                "execution_node_command_id": "foreign-source",
                "experiment_ordinal": 2,
            },
        ),
    ]
    outcomes = [
        {"outcome_id": "out-failed", "experiment_id": "failed"},
        {"outcome_id": "out-success", "experiment_id": "success"},
    ]
    return task, creations, outcomes


def _store(creations, outcomes):
    return SimpleNamespace(
        inspect=lambda *_: [
            {"workflow_id": identity, "payload_json": json.dumps(payload)}
            for identity, payload in creations
        ],
        list_facts=lambda *_: outcomes,
    )


def test_chain_iteration_is_not_global_ordinal_and_repair_tip_is_causal():
    task, creations, outcomes = _selection()
    before = copy.deepcopy((creations, outcomes))
    assert (
        _analysis_experiment_id(
            _store(list(reversed(creations)), outcomes), task.key.run_id, task
        )
        == "success"
    )
    assert (creations, outcomes) == before


@pytest.mark.parametrize(
    "tamper",
    [
        "unrelated",
        "foreign_preflight",
        "foreign_outcome",
        "duplicate_successor",
        "cycle",
        "missing",
    ],
)
def test_analysis_rejects_ambiguous_or_foreign_repair_sources(tamper):
    task, creations, outcomes = _selection()
    if tamper == "unrelated":
        creations[2][1].pop("revision_of_outcome_id")
    elif tamper == "foreign_preflight":
        creations[1][1]["preflight_repair_of_experiment_id"] = "other-chain"
    elif tamper == "foreign_outcome":
        outcomes[0]["experiment_id"] = "other-command"
    elif tamper == "duplicate_successor":
        creations.append(("second-success", copy.deepcopy(creations[2][1])))
    elif tamper == "cycle":
        creations[0][1]["preflight_repair_of_experiment_id"] = "success"
    else:
        creations = creations[3:]
    with pytest.raises(ValueError):
        _analysis_experiment_id(_store(creations, outcomes), task.key.run_id, task)


def test_legacy_none_chain_keeps_exact_global_ordinal_selection():
    task, _, _ = _selection()
    task = ResearchNodeTask(key=replace(task.key, chain=None))
    creations = [
        (
            "legacy-2",
            {
                "component_id": task.key.component_id,
                "role": task.key.expert_slot,
                "expert_cycle": 1,
                "experiment_ordinal": 2,
            },
        )
    ]
    assert (
        _analysis_experiment_id(_store(creations, []), task.key.run_id, task)
        == "legacy-2"
    )
