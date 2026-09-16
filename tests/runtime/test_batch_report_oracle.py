from __future__ import annotations

import json

import pytest
import yaml

from hypoloom.agent_aci import EmbeddedInput, canonical_json
from hypoloom.domain import AgentCommand, HypothesisAssessment
from hypoloom.expert_context_oracle import _report_assessments
from hypoloom.fake_agent import default_request
from test_agent_aci import _assessment_fact, _hypothesis_fact


def _report():
    request = default_request(AgentCommand.CYCLE_REPORT).to_dict()
    request["schema_version"] = 14
    entries = [
        item
        for hypothesis in ("hyp-a", "hyp-b")
        for item in (
            _hypothesis_fact(
                hypothesis, parent_hypothesis_id=None, method_family_id="family-1"
            ),
            _assessment_fact(
                hypothesis,
                method_family_id="family-1",
                assessment=HypothesisAssessment.INCONCLUSIVE,
            ),
        )
    ]
    payloads = {
        item.ref: json.loads(item.content_bytes())["payload"] for item in entries
    }
    facts = [
        {
            "fact_id": identity,
            "fact_type": payload["fact_type"],
            "hypothesis_id": payload["hypothesis_id"],
        }
        for identity, payload in payloads.items()
    ]
    context = {
        "batch_command_id": "synthesis-1",
        "cycle": 1,
        "chains": {"chain-a": "hyp-a", "chain-b": "hyp-b"},
        "terminal_tips": {},
        "selected_tip": None,
    }
    request["input_artifacts"] = [
        EmbeddedInput.from_bytes(
            request["command_id"] + "--batch-context",
            "application/json",
            canonical_json(context),
        ).to_dict()
    ]
    transition = {
        "run_id": request["run_id"],
        "workflow_kind": "RUN",
        "workflow_id": request["run_id"],
        "command_type": "ACCEPT_HYPOTHESIS",
        "payload_json": json.dumps(
            {
                "role": request["role"],
                "component_id": request["component_id"],
                "node_command_id": "synthesis-1",
                "hypothesis_batch": {"cycle": 1, "chains": context["chains"]},
            }
        ),
    }
    report = {
        "assessments": [
            {
                "fact_id": row["fact_id"],
                "hypothesis_id": row["hypothesis_id"],
                "assessment": "INCONCLUSIVE",
            }
            for row in facts
            if row["fact_type"] == "HypothesisAssessed"
        ]
    }
    return request, facts, payloads, [transition], report


def _content(report):
    return (
        "---\n"
        + yaml.safe_dump(report)
        + "---\n\nBoth accepted chains remain inconclusive.\n"
    ).encode()


def test_multichain_report_uses_exact_accepted_batch_not_parent_depth():
    request, facts, payloads, transitions, report = _report()
    _report_assessments(
        _content(report), request, facts, payloads, batch_transitions=transitions
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "no_batch",
        "duplicate_batch",
        "foreign_owner",
        "foreign_node",
        "stale_batch",
        "missing_assessment",
        "fake_verdict",
    ],
)
def test_multichain_report_cannot_replace_or_invent_its_actual_batch(tamper):
    request, facts, payloads, transitions, report = _report()
    if tamper == "no_batch":
        transitions = []
    elif tamper == "duplicate_batch":
        transitions *= 2
    elif tamper == "foreign_owner":
        next(
            payload
            for payload in payloads.values()
            if payload["fact_type"] == "HypothesisProposed"
        )["proposer_role"] = "foreign"
    elif tamper in {"foreign_node", "stale_batch"}:
        value = json.loads(transitions[0]["payload_json"])
        if tamper == "foreign_node":
            value["hypothesis_batch"]["chains"]["chain-a"] = "foreign-hypothesis"
        else:
            value["node_command_id"] = "another-synthesis"
        transitions[0]["payload_json"] = json.dumps(value)
    elif tamper == "missing_assessment":
        report["assessments"].pop()
    else:
        report["assessments"][0]["assessment"] = "REFUTED"
    with pytest.raises(ValueError):
        _report_assessments(
            _content(report), request, facts, payloads, batch_transitions=transitions
        )


def test_legacy_report_still_requires_its_single_owned_hypothesis():
    request, facts, payloads, _, report = _report()
    request["schema_version"] = 13
    with pytest.raises(ValueError):
        _report_assessments(_content(report), request, facts, payloads)


@pytest.mark.parametrize("version", [13, 14])
def test_empty_assessments_are_exact_only_for_an_owned_batch(version):
    request, facts, payloads, transitions, _ = _report()
    request["schema_version"] = version
    facts = [row for row in facts if row["fact_type"] != "HypothesisAssessed"]
    content = b"---\nassessments: []\n---\n\nNo scientific assessment was accepted.\n"
    if version == 14:
        # The independent batch Oracle checks each chain's actual final failure;
        # the report checker must neither invent assessments nor reject this set.
        _report_assessments(content, request, facts, payloads, batch_transitions=transitions)
    else:
        with pytest.raises(ValueError):
            _report_assessments(content, request, facts, payloads)
