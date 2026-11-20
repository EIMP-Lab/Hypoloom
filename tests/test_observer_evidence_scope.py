import base64
import json

import pytest

from hypoloom.interaction_oracle import _InteractionAudit
from hypoloom.oracle import _audit_owned_workspace_residue


def embedded(ref, value):
    return {"ref": ref, "media_type": "application/json", "content_base64": base64.b64encode(json.dumps(value).encode()).decode()}


@pytest.mark.parametrize("mode,expected", [("batch", True), ("fact", True), ("diagnostic", False),
    ("missing", False), ("wrong_chain", False), ("wrong_owner", False), ("wrong_ref", False), ("forged_fact", False),
    ("conflicting_batch", False)])
def test_only_exact_admitted_chain_or_hypothesis_binds_an_original_session(mode, expected):
    participant = {"scope": {"component_id": "primary.score", "role": "one", "chain": "L"}, "hypothesis_id": "hyp"}
    scope = participant["scope"]
    fact = {"fact_type": "HypothesisProposed", "fact_id": "fact-1", "hypothesis_id": "hyp",
        "component_id": scope["component_id"], "proposer_role": scope["role"]}
    inputs = [embedded("retry--candidate-validation-error", {"validation_error": fact}), embedded("comparison", [1, 2])]
    if mode in {"batch", "wrong_chain", "conflicting_batch"}:
        inputs.append(embedded("delivery--batch-context", {"chains": {"L": "hyp" if mode == "batch" else "other"}}))
    if mode == "forged_fact": fact["fact_id"] = "forged"
    if mode in {"fact", "missing", "wrong_owner", "wrong_ref", "forged_fact", "conflicting_batch"}:
        inputs.append(embedded("wrong" if mode == "wrong_ref" else fact["fact_id"], {"fact_type": fact["fact_type"], "payload": fact}))
    request = {"schema_version": 41, "command_id": "delivery", "role": "one", "component_id": scope["component_id"],
        "task_context": {"key": {"chain": "L", "component_id": scope["component_id"], "expert_slot": "other" if mode == "wrong_owner" else "one"}},
        "input_artifacts": inputs, "immutable_context_refs": [i["ref"] for i in inputs if mode != "missing" or i["ref"] != "fact-1"]}
    assert _InteractionAudit.original_matches(request, participant, "fact-1") is expected


def test_pending_collection_exempts_only_its_owned_process_and_post_publication_residue_is_visible(tmp_path):
    closed = {"attempt_id": "chosen", "workflow_id": "experiment", "state": "CLOSED", "kind": "PROCESS", "terminal_reason": "SUCCEEDED"}
    other = {**closed, "attempt_id": "other"}
    for attempt in (closed, other):
        for category in ("work", "result-staging"):
            path = tmp_path / category / attempt["attempt_id"]
            path.mkdir(parents=True)
            (path / "raw.json").write_text("42")
    issues = []
    _audit_owned_workspace_residue(tmp_path, [closed, other], issues, collecting={("experiment", "chosen")})
    assert {i.code for i in issues} == {"owned_work_residue", "owned_staging_residue"}
    assert all(i.identifiers[0] == "other" for i in issues)
    issues.clear()
    _audit_owned_workspace_residue(tmp_path, [closed], issues)
    assert {i.code for i in issues} == {"owned_work_residue", "owned_staging_residue"}
