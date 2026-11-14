"""Implementation review contracts; no scientific acceptance or static check list."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

MAX_PROBE_SECONDS = 120
MAX_PROBE_BYTES = 256 * 1024


def context(request):
    return json.loads(next(item for item in request.input_artifacts
                          if item.ref.endswith("--verification-context")).content_bytes())


def validate_review(request, raw):
    """The same validator serves native correction and Host admission."""
    expected = {"task_kind", "summary", "supporting_refs", "verdict", "findings", "probe", "resolved_refs"}
    if not isinstance(raw, dict) or set(raw) != expected or raw["task_kind"] != "VERIFY_IMPLEMENTATION":
        raise ValueError("implementation review requires its exact fields and task kind")
    ctx = context(request)
    refs = set(request.immutable_context_refs)
    if not isinstance(raw["summary"], str) or not raw["summary"].strip():
        raise ValueError("review summary is required")
    if not isinstance(raw["supporting_refs"], list) or not raw["supporting_refs"] or not set(raw["supporting_refs"]) <= refs:
        raise ValueError("review must cite supplied implementation/evidence inputs")
    verdict = raw["verdict"]
    if verdict not in {"probe_required", "passed", "changes_required", "inconclusive"}:
        raise ValueError("invalid implementation verdict")
    if not isinstance(raw["findings"], list) or len(raw["findings"]) > 32:
        raise ValueError("findings must be an array of at most 32 items")
    seen = set()
    previous = set(ctx.get("finding_refs", ()))
    for item in raw["findings"]:
        if not isinstance(item, dict) or set(item) != {"id", "severity", "message", "evidence_refs", "resolution_condition", "followup_of"}:
            raise ValueError("finding requires id, severity, message, evidence_refs, resolution_condition and followup_of")
        key = item["id"]
        if not isinstance(key, str) or not key or len(key) > 64 or key in seen or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in key):
            raise ValueError("finding id must be unique short ASCII identifier")
        seen.add(key)
        if item["severity"] not in {"blocker", "advisory"}:
            raise ValueError("finding severity must be blocker or advisory")
        if any(not isinstance(item[k], str) or not item[k].strip() for k in ("message", "resolution_condition")):
            raise ValueError("finding must explain the defect and observable resolution condition")
        if not isinstance(item["evidence_refs"], list) or not item["evidence_refs"] or not set(item["evidence_refs"]) <= refs:
            raise ValueError("finding evidence must cite supplied inputs")
        if item["followup_of"] is not None and item["followup_of"] not in previous:
            raise ValueError("followup_of must name a supplied report finding")
    if not isinstance(raw["resolved_refs"], list) or not set(raw["resolved_refs"]) <= previous:
        raise ValueError("resolved_refs must name supplied prior findings")
    blockers = any(item["severity"] == "blocker" for item in raw["findings"])
    if verdict == "passed" and blockers or verdict == "changes_required" and not blockers:
        raise ValueError("changes_required needs a sourced blocker; passed cannot retain blockers")
    probe = raw["probe"]
    if (probe is not None) != (verdict == "probe_required"):
        raise ValueError("only probe_required supplies a probe")
    if ctx["phase"] == "inspect" and verdict == "passed":
        raise ValueError("initial inspection cannot pass without Host Process evidence")
    if ctx["phase"] == "review" and verdict == "probe_required":
        raise ValueError("bounded review must decide from evidence or report inconclusive")
    if verdict == "passed" and not (ctx.get("probe_evidence") or {}).get("usable"):
        raise ValueError("a pass requires successful current Host Process evidence")
    if verdict == "passed" and not set(ctx.get("open_blocker_refs", ())) <= set(raw["resolved_refs"]):
        raise ValueError("a pass must explicitly resolve supplied prior blockers using current evidence")
    if probe is not None:
        if not isinstance(probe, dict) or set(probe) != {"files", "argv", "timeout_seconds"}:
            raise ValueError("probe requires files, explicit Python argv and timeout_seconds")
        files = probe["files"]
        if not isinstance(files, dict) or not 1 <= len(files) <= 8:
            raise ValueError("probe requires 1..8 independent source files")
        for path, source in files.items():
            parts = PurePosixPath(path).parts
            if not path.startswith("verification/") or ".." in parts or str(PurePosixPath(path)) != path or not isinstance(source, str) or not source.strip():
                raise ValueError("probe sources must be nonempty text within verification/")
            if path in ctx["workspace_paths"]:
                raise ValueError("probe cannot overwrite candidate files")
        if sum(len(s.encode()) for s in files.values()) > MAX_PROBE_BYTES:
            raise ValueError("probe source exceeds bounded output size")
        argv = probe["argv"]
        if not isinstance(argv, list) or len(argv) < 2 or argv[0] != "python" or argv[1] not in files or any(not isinstance(a, str) or "\x00" in a for a in argv):
            raise ValueError("probe argv must directly invoke its Python source")
        if type(probe["timeout_seconds"]) not in {int, float} or not 0 < probe["timeout_seconds"] <= ctx["max_probe_seconds"]:
            raise ValueError("probe timeout exceeds Host allowance")
    return raw


def review_schema(request):
    from copy import deepcopy
    text = {"type": "string"}
    refs = {"type": "array", "items": {"type": "string", "enum": list(request.immutable_context_refs)}}
    finding = {"type": "object", "additionalProperties": False, "properties": {
        "id": text, "severity": {"enum": ["blocker", "advisory"]}, "message": text,
        "evidence_refs": deepcopy(refs), "resolution_condition": text, "followup_of": {"type": ["string", "null"]}}}
    finding["required"] = list(finding["properties"])
    probe = {"type": "object", "additionalProperties": False, "properties": {
        "files": {"type": "object", "additionalProperties": text},
        "argv": {"type": "array", "items": text}, "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_PROBE_SECONDS}},
        "required": ["files", "argv", "timeout_seconds"]}
    result = {"type": "object", "additionalProperties": False, "properties": {
        "task_kind": {"const": "VERIFY_IMPLEMENTATION"}, "summary": text, "supporting_refs": refs,
        "verdict": {"enum": ["probe_required", "passed", "changes_required", "inconclusive"]},
        "findings": {"type": "array", "items": finding},
        "probe": {"anyOf": [probe, {"type": "null"}]}, "resolved_refs": {"type": "array", "items": text}}}
    result["required"] = list(result["properties"])
    return result


INSTRUCTIONS = """You are the independent implementation verifier, not a research expert.
Inspect only correctness, engineering reasonableness and computational efficiency of
the captured implementation. Do not judge hypotheses, novelty or scientific success.
Do not assume a previous algorithm is the correctness reference for a new algorithm.
Read actual code, interfaces and configuration; check dispatch, tensor/batch behavior,
state, gradients, numerical edge cases, data flow and scaling where applicable.
Select checks from this implementation, never from a fixed research-method checklist.
You cannot edit candidate code. Return a small independent Python probe in probe.files;
the Host executes it with the captured candidate read-only under /input, patched target
read-only at the supplied mount, and fresh writable /output and /tmp. Import captured
files from /input explicitly. Print bounded, concrete results and timings to stdout.
Do not start full training. No network, subprocess daemons, installation or nested agents.
Probe failures are engineering evidence, not scientific refutation. Review actual Host
logs before passing; successful exit alone is not proof. Cite inputs and exact code
locations in findings. Block only demonstrated correctness defects or infeasible resource
use; preferences and justified slower tradeoffs are advisories. If evidence is inadequate,
return inconclusive. Report observable resolution conditions and explicit prior finding
references. A prior report delivered to its author is not proof that it was understood
or fixed. Never resolve a finding without evidence from the changed implementation.
"""
