from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from hypoloom.control_protocol import ControlRequest
from hypoloom.domain import FACT_SCHEMA_VERSION
from hypoloom.mcp_server import handle_request as mcp_handle_request
from hypoloom.monitor import (
    HypothesisView,
    MonitorError,
    _hypothesis_views,
    build_monitor_snapshot,
)
from hypoloom.profile_admission import admit_profile
from hypoloom.state_store import StateStore, StateTransaction
from hypoloom.workflow_host import WorkflowHost


_REPOSITORY = Path(__file__).resolve().parents[2]


def _complete_fake_run(state_root: Path, run_id: str) -> None:
    admission = admit_profile(
        _REPOSITORY / "tests/fixtures/profiles/component-demo",
        run_id=run_id,
        delay_seconds=0,
    )
    assert admission.admitted and admission.create_run_payload is not None
    host = WorkflowHost(state_root)
    try:
        host.initialize()
        created = host.handle(ControlRequest(
            schema_version=1,
            request_id=f"create-{run_id}",
            expected_revision=0,
            command="CREATE_RUN",
            payload=admission.create_run_payload,
        ))
        assert created.status == "accepted"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            host.tick()
            head = host.store.latest_transition(run_id)
            if head is not None and head["state"] == "COMPLETED":
                return
            time.sleep(0.001)
        raise AssertionError("fresh fake run did not close")
    finally:
        host.close()


def _proposed_row(
    hypothesis_id: str,
    *,
    parent_hypothesis_id: str | None = None,
    relation: str | None = None,
) -> dict[str, object]:
    fact_id = f"fact-{hypothesis_id}"
    payload = {
        "fact_type": "HypothesisProposed",
        "fact_id": fact_id,
        "run_id": "lineage-run",
        "schema_version": FACT_SCHEMA_VERSION,
        "hypothesis_id": hypothesis_id,
        "goal_id": "goal-lineage",
        "component_id": "primary.score",
        "proposer_role": "scientist-a",
        "method_family_id": "family-a",
        "claim": f"claim for {hypothesis_id}",
        "assumptions": [] if parent_hypothesis_id is None else ["bounded input"],
        "parent_hypothesis_id": parent_hypothesis_id,
        "relation": relation,
    }
    return {
        "fact_id": fact_id,
        "run_id": "lineage-run",
        "fact_type": "HypothesisProposed",
        "schema_version": FACT_SCHEMA_VERSION,
        "subject_id": hypothesis_id,
        "goal_id": "goal-lineage",
        "hypothesis_id": hypothesis_id,
        "payload": payload,
    }


def _assessed_row(
    hypothesis_id: str,
    *,
    component_id: str = "primary.score",
    method_family_id: str = "family-a",
) -> dict[str, object]:
    assessment_id = f"assessment-{hypothesis_id}"
    payload = {
        "fact_type": "HypothesisAssessed",
        "fact_id": f"fact-{assessment_id}",
        "run_id": "lineage-run",
        "schema_version": FACT_SCHEMA_VERSION,
        "assessment_id": assessment_id,
        "hypothesis_id": hypothesis_id,
        "component_id": component_id,
        "method_family_id": method_family_id,
        "evidence_ids": [f"evidence-{hypothesis_id}"],
        "assessment": "SUPPORTED",
    }
    return {
        "fact_id": f"fact-{assessment_id}",
        "run_id": "lineage-run",
        "fact_type": "HypothesisAssessed",
        "schema_version": FACT_SCHEMA_VERSION,
        "subject_id": assessment_id,
        "hypothesis_id": hypothesis_id,
        "payload": payload,
    }


def _row_census(database: Path) -> dict[str, int]:
    with sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True,
    ) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: int(connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0])
            for table in tables
        }


def test_hypothesis_projection_maps_parent_and_assessment_fact_fields() -> None:
    parent = _proposed_row("hypothesis-parent")
    child = _proposed_row(
        "hypothesis-child",
        parent_hypothesis_id="hypothesis-parent",
        relation="refines",
    )

    assert _hypothesis_views((parent, child), (_assessed_row("hypothesis-child"),)) == (
        HypothesisView(
            hypothesis_id="hypothesis-parent",
            component_id="primary.score",
            proposer_role="scientist-a",
            method_family_id="family-a",
            claim="claim for hypothesis-parent",
            assumptions=(),
            parent_hypothesis_id=None,
            relation=None,
            assessment_id=None,
            evidence_ids=(),
            assessment=None,
        ),
        HypothesisView(
            hypothesis_id="hypothesis-child",
            component_id="primary.score",
            proposer_role="scientist-a",
            method_family_id="family-a",
            claim="claim for hypothesis-child",
            assumptions=("bounded input",),
            parent_hypothesis_id="hypothesis-parent",
            relation="refines",
            assessment_id="assessment-hypothesis-child",
            evidence_ids=("evidence-hypothesis-child",),
            assessment="SUPPORTED",
        ),
    )


def test_hypothesis_projection_rejects_incomplete_or_mismatched_facts() -> None:
    incomplete = _proposed_row("hypothesis-child")
    assert isinstance(incomplete["payload"], dict)
    incomplete["payload"]["parent_hypothesis_id"] = "hypothesis-parent"
    with pytest.raises(MonitorError, match="incomplete parent_hypothesis_id/relation"):
        _hypothesis_views((incomplete,), ())

    proposal = _proposed_row("hypothesis-child")
    mismatched = _assessed_row("hypothesis-child", component_id="must.delivery")
    with pytest.raises(MonitorError, match="component/method-family lineage differs"):
        _hypothesis_views((proposal,), (mismatched,))

    missing_payload = _proposed_row("hypothesis-child")
    missing_payload["payload"] = None
    with pytest.raises(MonitorError, match="has no typed payload"):
        _hypothesis_views((missing_payload,), ())


def test_hypothesis_projection_leaves_duplicate_assessment_verdict_to_oracle() -> None:
    proposal = _proposed_row("hypothesis-child")
    first = _assessed_row("hypothesis-child")
    second = _assessed_row("hypothesis-child")
    assert isinstance(second["payload"], dict)
    second["subject_id"] = "assessment-hypothesis-child-second"
    second["payload"]["assessment_id"] = "assessment-hypothesis-child-second"
    second["payload"]["assessment"] = "REFUTED"

    view = _hypothesis_views((proposal,), (first, second))

    assert len(view) == 1
    assert view[0].assessment_id == "assessment-hypothesis-child"
    assert view[0].assessment == "SUPPORTED"


def test_snapshot_uses_one_fact_read_for_hypothesis_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "one-fact-read"
    run_id = "one-fact-read"
    _complete_fake_run(state_root, run_id)
    original = StateTransaction.list_facts
    fact_type_calls: list[object] = []

    def tracked_list_facts(
        transaction: StateTransaction,
        selected_run_id: str,
        fact_type: object = None,
    ):
        fact_type_calls.append(fact_type)
        if fact_type in {"HypothesisProposed", "HypothesisAssessed"}:
            raise AssertionError("hypothesis lineage must share one fact snapshot")
        return original(transaction, selected_run_id, fact_type)

    monkeypatch.setattr(StateTransaction, "list_facts", tracked_list_facts)

    snapshot = build_monitor_snapshot(state_root, run_id)

    assert snapshot.hypotheses
    assert fact_type_calls.count(None) == 1


def test_completed_fact_lineage_is_shared_with_mcp_without_durable_writes(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "hypothesis-lineage"
    run_id = "hypothesis-lineage"
    _complete_fake_run(state_root, run_id)
    store = StateStore(state_root)
    proposals = store.list_facts(run_id, "HypothesisProposed")
    assessments = store.list_facts(run_id, "HypothesisAssessed")
    assert len(proposals) == len(assessments) == 1
    proposal = proposals[0]["payload"]
    assessment = assessments[0]["payload"]
    before_rows = _row_census(state_root / "state.sqlite")
    before_artifacts = {
        path.relative_to(state_root): path.read_bytes()
        for path in sorted((state_root / "artifacts").rglob("*"))
        if path.is_file()
    }

    snapshot = build_monitor_snapshot(state_root, run_id)
    mcp_response = mcp_handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "hypoloom_inspect_run",
            "arguments": {"state_root": str(state_root), "run_id": run_id},
        },
    })

    assert snapshot.schema_version == 2
    assert snapshot.hypotheses == (HypothesisView(
        hypothesis_id=proposal["hypothesis_id"],
        component_id=proposal["component_id"],
        proposer_role=proposal["proposer_role"],
        method_family_id=proposal["method_family_id"],
        claim=proposal["claim"],
        assumptions=tuple(proposal["assumptions"]),
        parent_hypothesis_id=proposal["parent_hypothesis_id"],
        relation=proposal["relation"],
        assessment_id=assessment["assessment_id"],
        evidence_ids=tuple(assessment["evidence_ids"]),
        assessment=assessment["assessment"],
    ),)
    assert mcp_response is not None
    mcp_payload = json.loads(mcp_response["result"]["content"][0]["text"])
    assert mcp_payload["schema_version"] == 2
    assert mcp_payload["hypotheses"] == json.loads(snapshot.to_json())["hypotheses"]
    assert mcp_payload["rendezvous"] == json.loads(snapshot.to_json())["rendezvous"]
    assert _row_census(state_root / "state.sqlite") == before_rows
    assert {
        path.relative_to(state_root): path.read_bytes()
        for path in sorted((state_root / "artifacts").rglob("*"))
        if path.is_file()
    } == before_artifacts
