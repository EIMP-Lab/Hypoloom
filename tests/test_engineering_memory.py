from copy import deepcopy
import json

from hypoloom.agent_aci import canonical_json
from hypoloom.engineering_memory import MAX_MEMORY_BYTES, memory_document, verified_entry


def test_memory_retains_whole_evidence_with_bounded_bytes_and_distinct_probes():
    entries = [{"source_output_digest": "a" * 64, "probe_attempt_id": str(i), "evidence": "condition " * 60}
        for i in range(8)]
    document = memory_document("run", "expert", "component", [None, entries[0], *entries])
    assert len(canonical_json(document)) <= MAX_MEMORY_BYTES
    assert document["entries"] == entries[:4] and document["omitted_entries"] == 4
    assert memory_document("run", "expert", "component", entries) == document
    huge = {**entries[0], "evidence": "x" * MAX_MEMORY_BYTES}
    bounded = memory_document("run", "expert", "component", [huge, entries[1]])
    assert bounded["entries"] == [entries[1]] and bounded["omitted_entries"] == 1
    assert memory_document("run", "expert", "component", [huge]) is None
    assert memory_document("run", "expert", "component", entries, max_bytes=1) is None


def test_model_claim_without_process_and_review_is_never_a_verified_entry():
    claimed = {"observation_json": json.dumps({"response": {"typed_candidate": {"verdict": "passed"}}})}
    assert verified_entry(run_id="run", role="expert", component_id="component", commission={},
        review=claimed, plan_commission={}, plan_attempt=deepcopy(claimed), probe=claimed, execution={}) is None
