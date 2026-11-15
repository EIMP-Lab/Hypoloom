"""Bounded, host-derived engineering evidence from existing immutable records.

No model-authored report is promoted into memory without its exact successful
Process and accepted independent review. This is an input projection, not a new
scientific fact, pass cache, shared private notebook or performance guarantee.
"""
from __future__ import annotations

import hashlib
import json

from .agent_aci import canonical_json
from .verification_probe import validate_result

LEGACY_MAX_MEMORY_BYTES = 6 * 1024
MAX_MEMORY_BYTES = 64 * 1024
MAX_MEMORY_ENTRIES = 4
SUFFIX = "--verified-engineering-memory"
SCOPE = ("Engineering evidence for this run, expert and component only. Conditions and reference bases are "
    "declarations reviewed against the named Process, not universal lessons. Recheck applicability and changed "
    "code; a historical pass never authorizes a new candidate. CPU/local timings do not imply CUDA/full-training "
    "speedup or scientific success. Read these records instead of trusting an author's claimed passed report.")


def _observation(attempt):
    return json.loads(attempt["observation_json"] or "{}")


def verified_entry(*, run_id, role, component_id, commission, review, plan_commission, plan_attempt, probe, execution):
    """Fail closed on stale/mismatched evidence; return only checked conditions."""
    try:
        keys = ("experiment_id", "subject_expert", "component_id", "source_submit_command_id", "source_output_digest")
        if (commission["phase"] != "review" or commission["subject_expert"] != role
                or commission["component_id"] != component_id
                or any(commission[k] != plan_commission[k] for k in keys)
                or commission["source_output_digest"] != execution["source_agent_output_digest"]
                or commission["source_submit_command_id"] != execution["source_submit_command_id"]
                or commission["probe_attempt_id"] != probe["attempt_id"]
                or commission["probe_report_ref"] != plan_attempt["attempt_id"]):
            return None
        for attempt, kind in ((review, "AGENT"), (plan_attempt, "AGENT"), (probe, "PROCESS")):
            if (attempt["run_id"] != run_id or attempt["kind"] != kind or attempt["state"] != "CLOSED"
                    or attempt["terminal_reason"] != "SUCCEEDED"):
                return None
        if (probe["experiment_id"] != commission["experiment_id"]
                or probe["execution_id"] != commission["experiment_id"] + "--implementation-probe"
                or not plan_attempt["ended_at"] <= probe["started_at"] <= probe["ended_at"] <= review["started_at"]):
            return None
        reviewed, planned, observed = map(_observation, (review, plan_attempt, probe))
        if any(o["request"]["command"] != "VERIFY_IMPLEMENTATION" or o["request"]["role"] != "verifier"
               for o in (reviewed, planned)):
            return None
        report = reviewed["response"]["typed_candidate"]
        plan_report = planned["response"]["typed_candidate"]
        if report["verdict"] != "passed" or plan_report["verdict"] != "probe_required": return None
        if (observed.get("implementation_probe") is not True or observed.get("synthetic_before_spawn")
                or observed.get("check_results_error") or observed.get("returncode") != 0):
            return None
        checks = plan_report["probe"]["checks"]
        actual = validate_result(observed.get("check_results"), checks,
            attempt_id=probe["attempt_id"], report_ref=plan_attempt["attempt_id"])
        coverage = report["coverage"]
        covered = {i for c in coverage["covered"] for i in c["check_ids"]}
        successful = {c["id"] for c in actual["checks"] if c["status"] in {"PASS", "MEASURED"}}
        if (not actual["passed"] or not {c["id"] for c in checks if c["required"]} <= covered <= successful
                or any(c["blocking"] for c in coverage["uncovered"])
                or any(f["severity"] == "blocker" for f in report["findings"])):
            return None
        declared = {c["id"]: c for c in checks}
        return {"experiment_id": commission["experiment_id"], "source_submit_command_id": commission["source_submit_command_id"],
            "source_output_digest": commission["source_output_digest"], "review_attempt_id": review["attempt_id"],
            "probe_attempt_id": probe["attempt_id"], "probe_report_ref": plan_attempt["attempt_id"],
            "covered": coverage["covered"], "uncovered": coverage["uncovered"],
            "checks": [{"id": c["id"], "kind": c["kind"], "purpose": declared[c["id"]]["purpose"],
                "basis": declared[c["id"]]["basis"], "status": c["status"], "measurement": c["measurement"]}
                for c in actual["checks"] if c["id"] in covered]}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def memory_document(run_id, role, component_id, entries, *, max_bytes=MAX_MEMORY_BYTES, include_omissions=True):
    """Newest whole records first; bound bytes without truncating their evidence."""
    document = {"schema_version": 1, "run_id": run_id, "expert": role, "component_id": component_id,
        "scope": SCOPE, "entries": [], "omitted_entries": 0}
    seen = set()
    for entry in entries:
        if entry is None: continue
        # Repeated references to one proof are duplicates. Different devices or
        # workloads on the same code remain distinct evidence.
        identity = (entry["source_output_digest"], entry["probe_attempt_id"])
        if identity in seen: continue
        seen.add(identity)
        document["entries"].append(entry)
        candidate = {**document, "version_id": "0" * 64}
        if len(document["entries"]) > MAX_MEMORY_ENTRIES or len(canonical_json(candidate)) > max_bytes - 32:
            document["entries"].pop()
            document["omitted_entries"] += 1
    if not document["entries"] and (not include_omissions or not document["omitted_entries"]): return None
    document["version_id"] = hashlib.sha256(canonical_json(document)).hexdigest()
    return document if len(canonical_json(document)) <= max_bytes else None
