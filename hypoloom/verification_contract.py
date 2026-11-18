"""Implementation review contracts; no scientific acceptance or static check list."""
from __future__ import annotations

import json
import math
from pathlib import PurePosixPath

MAX_PROBE_SECONDS = 120
MAX_PROBE_BYTES = 256 * 1024
MAX_PROBE_REPAIRS = 2
MAX_REVIEW_NARRATIVE_BYTES = 16 * 1024


def structured_submission(request):
    from .agent_aci import STRUCTURED_VERIFIER_OUTPUT_ACI_SCHEMA_VERSION
    return (request.schema_version >= STRUCTURED_VERIFIER_OUTPUT_ACI_SCHEMA_VERSION
            and request.command.value == "VERIFY_IMPLEMENTATION")


def compact_review(request):
    from .agent_aci import STAGE_EVIDENCE_ACI_SCHEMA_VERSION
    return request.command.value == "VERIFY_IMPLEMENTATION" and request.schema_version >= STAGE_EVIDENCE_ACI_SCHEMA_VERSION


def _narrative_fields(raw):
    """Explanatory text only; source, identifiers and evidence refs keep their limits."""
    yield "summary", raw.get("summary"), 2048
    for index, finding in enumerate(raw.get("findings", ())):
        if isinstance(finding, dict):
            for field in ("message", "resolution_condition"):
                yield f"findings/{index}/{field}", finding.get(field), 2048
    probe = raw.get("probe")
    if isinstance(probe, dict):
        for index, check in enumerate(probe.get("checks", ())):
            if isinstance(check, dict):
                for field in ("purpose", "basis"):
                    yield f"probe/checks/{index}/{field}", check.get(field), 1024
    coverage = raw.get("coverage")
    if isinstance(coverage, dict):
        for category in ("covered", "uncovered"):
            for index, row in enumerate(coverage.get(category, ())):
                if isinstance(row, dict):
                    for field in ("condition", "reason"):
                        yield f"coverage/{category}/{index}/{field}", row.get(field), 2048


def _validate_narrative(raw):
    size = 0
    for path, value, limit in _narrative_fields(raw):
        if not isinstance(value, str):
            continue  # The ordinary validator owns required fields and types.
        if len(value) > limit:
            raise ValueError(f"/{path}: explanatory text exceeds {limit} characters; summarize and cite code locations")
        size += len(value.encode("utf-8"))
    if size > MAX_REVIEW_NARRATIVE_BYTES:
        raise ValueError(f"review explanatory text exceeds {MAX_REVIEW_NARRATIVE_BYTES} bytes; remove repeated source and findings")


def structured_probe(request):
    from .agent_aci import PROBE_EVIDENCE_ACI_SCHEMA_VERSION
    return request.schema_version >= PROBE_EVIDENCE_ACI_SCHEMA_VERSION


def context(request):
    return json.loads(next(item for item in request.input_artifacts
                          if item.ref.endswith("--verification-context")).content_bytes())


def can_repair_probe(request, ctx):
    from .agent_aci import PROBE_RECOVERY_ACI_SCHEMA_VERSION
    remaining = ctx.get("probe_repairs_remaining", 0)
    return (request.schema_version >= PROBE_RECOVERY_ACI_SCHEMA_VERSION
            and ctx["phase"] == "review" and type(remaining) is int and remaining > 0)


def validate_review(request, raw):
    """The same validator serves native correction and Host admission."""
    expected = {"task_kind", "summary", "supporting_refs", "verdict", "findings", "probe", "resolved_refs"}
    if structured_probe(request): expected.add("coverage")
    if not isinstance(raw, dict) or set(raw) != expected or raw["task_kind"] != "VERIFY_IMPLEMENTATION":
        raise ValueError("implementation review requires its exact fields and task kind")
    ctx = context(request)
    refs = set(request.immutable_context_refs)
    # Return related, independently actionable mistakes together. All checks
    # below still run through this one validator at correction and admission.
    previous = set(ctx.get("finding_refs", ()))
    errors = []
    resolved = raw["resolved_refs"]
    if not _references(resolved, previous):
        errors.append("resolved_refs must name supplied prior findings (report_ref#id), never input R aliases; "
            + ("no prior findings exist, use []" if not previous else "use verification-context.finding_refs"))
    if isinstance(raw["findings"], list):
        for i, item in enumerate(raw["findings"]):
            if isinstance(item, dict) and item.get("followup_of") is not None and (not isinstance(item.get("followup_of"), str) or item["followup_of"] not in previous):
                errors.append(f"findings[{i}].followup_of must name a supplied prior finding; use null for a new issue")
    if (raw["probe"] is not None) != (raw["verdict"] == "probe_required"):
        errors.append("only probe_required supplies a probe; all other verdicts require probe=null")
    if errors: raise ValueError("; ".join(errors[:8]))
    if not isinstance(raw["summary"], str) or not raw["summary"].strip():
        raise ValueError("review summary is required")
    if not raw["supporting_refs"] or not _references(raw["supporting_refs"], refs):
        raise ValueError("review must cite supplied implementation/evidence inputs")
    verdict = raw["verdict"]
    if not isinstance(verdict, str) or verdict not in {"probe_required", "passed", "changes_required", "inconclusive"}:
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
        if not item["evidence_refs"] or not _references(item["evidence_refs"], refs):
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
        if not can_repair_probe(request, ctx):
            raise ValueError("probe repair allowance exhausted or unsupported; decide from evidence or report inconclusive")
        if blockers:
            raise ValueError("candidate blockers require changes_required, not a probe repair")
        if not ctx.get("probe_evidence") or ctx["probe_evidence"].get("usable"):
            raise ValueError("probe repair requires unsuccessful current Host probe evidence")
        prior = ctx.get("probe_plan") or {}
        if not isinstance(probe, dict) or probe.get("checks") != prior.get("checks"):
            raise ValueError("probe repair must preserve every declared check, purpose, basis and required flag")
        if probe.get("files") == prior.get("files"):
            raise ValueError("probe repair must change the defective probe source")
    if verdict == "passed" and not (ctx.get("probe_evidence") or {}).get("usable"):
        raise ValueError("a pass requires successful current Host Process evidence")
    if verdict == "passed" and not set(ctx.get("open_blocker_refs", ())) <= set(raw["resolved_refs"]):
        raise ValueError("a pass must explicitly resolve supplied prior blockers using current evidence")
    if probe is not None:
        fields = {"files", "argv", "timeout_seconds"} | ({"checks"} if structured_probe(request) else set())
        if not isinstance(probe, dict) or set(probe) != fields:
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
            if structured_probe(request):
                from .verification_probe import RESERVED_PATHS
                if any(path == p or path.startswith(p + "/") or p.startswith(path + "/") for p in RESERVED_PATHS):
                    raise ValueError("probe source overlaps the Host runner")
                if request.schema_version >= 36 and path.endswith(".py"):
                    try: compile(source, path, "exec")
                    except (SyntaxError, ValueError) as exc:
                        raise ValueError(f"probe source does not compile: {path}: {exc}") from exc
        if sum(len(s.encode()) for s in files.values()) > MAX_PROBE_BYTES:
            raise ValueError("probe source exceeds bounded output size")
        argv = probe["argv"]
        if not isinstance(argv, list) or len(argv) < 2 or argv[0] != "python" or argv[1] not in files or any(not isinstance(a, str) or "\x00" in a for a in argv):
            raise ValueError("probe argv must directly invoke its Python source")
        if type(probe["timeout_seconds"]) not in {int, float} or not math.isfinite(probe["timeout_seconds"]) or not 0 < probe["timeout_seconds"] <= ctx["max_probe_seconds"]:
            raise ValueError("probe timeout exceeds Host allowance")
        if structured_probe(request):
            from .verification_probe import RESERVED_PATHS, validate_checks
            from .agent_aci import ENGINEERING_MEMORY_ACI_SCHEMA_VERSION
            validate_checks(probe["checks"], comparisons=request.schema_version >= ENGINEERING_MEMORY_ACI_SCHEMA_VERSION)
            if any(p == w or p.startswith(w + "/") or w.startswith(p + "/") for p in RESERVED_PATHS for w in ctx["workspace_paths"]):
                raise ValueError("candidate overlaps reserved Host probe paths; report inconclusive without a probe")
    if structured_probe(request): _validate_coverage(raw, ctx)
    if structured_submission(request): _validate_narrative(raw)
    return raw


def _references(value, allowed):
    return isinstance(value, list) and all(isinstance(v, str) and v in allowed for v in value) and len(value) == len(set(value))


def _validate_coverage(raw, ctx):
    coverage = raw["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != {"covered", "uncovered"}:
        raise ValueError("coverage requires covered and uncovered conditions")
    plan = raw["probe"] if raw["probe"] is not None else ctx.get("probe_plan")
    checks = {c["id"]: c for c in (plan or {}).get("checks", ())}
    covered = set()
    for field in ("covered", "uncovered"):
        rows = coverage[field]
        if not isinstance(rows, list) or len(rows) > 32: raise ValueError("coverage arrays are bounded to 32 conditions")
        for row in rows:
            keys = {"condition", "check_ids"} if field == "covered" else {"condition", "reason", "blocking"}
            if not isinstance(row, dict) or set(row) != keys or not isinstance(row["condition"], str) or not row["condition"].strip() or len(row["condition"]) > 2048:
                raise ValueError("coverage conditions require their exact bounded fields")
            if field == "covered":
                if not row["check_ids"] or not _references(row["check_ids"], checks):
                    raise ValueError("coverage.check_ids must name checks in the current probe plan")
                covered.update(row["check_ids"])
            elif type(row["blocking"]) is not bool or not isinstance(row["reason"], str) or not row["reason"].strip() or len(row["reason"]) > 2048:
                raise ValueError("uncovered conditions need a reason and boolean blocking")
    if ctx["phase"] == "inspect" and coverage["covered"]:
        raise ValueError("inspect coverage.covered must be []; proposed checks have not run")
    if raw["verdict"] == "passed":
        from .verification_probe import validate_result
        evidence = ctx.get("probe_evidence") or {}
        try:
            assessment = validate_result(evidence["observation"]["check_results"], list(checks.values()),
                attempt_id=evidence["attempt_id"], report_ref=ctx["probe_report_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("passed requires complete current structured probe evidence") from exc
        if not assessment["passed"]: raise ValueError("passed cannot ignore failed, erroneous or unexecuted required checks")
        if not {key for key, c in checks.items() if c["required"]} <= covered:
            raise ValueError("passed coverage must account for every required check")
        successful = {r["id"] for r in assessment["checks"] if r["status"] in {"PASS", "MEASURED"}}
        if not covered <= successful: raise ValueError("covered checks must actually have succeeded")
        if any(c["blocking"] for c in coverage["uncovered"]): raise ValueError("passed cannot retain a blocking coverage gap")


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
    if structured_probe(request):
        ctx = context(request)
        prior = list(ctx.get("finding_refs", ()))
        finding["properties"]["followup_of"] = {"enum": [None, *prior]}
        # An empty enum is invalid JSON Schema; false items admits only [].
        result["properties"]["resolved_refs"]["items"] = {"type": "string", "enum": prior} if prior else False
        verdicts = ["probe_required", "changes_required", "inconclusive"] if ctx["phase"] == "inspect" else ["passed", "changes_required", "inconclusive"]
        if can_repair_probe(request, ctx): verdicts.append("probe_required")
        result["properties"]["verdict"] = {"enum": verdicts}
        probe["properties"]["timeout_seconds"]["maximum"] = ctx["max_probe_seconds"]
        def obj(properties):
            return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}
        from .agent_aci import ENGINEERING_MEMORY_ACI_SCHEMA_VERSION
        kinds = ["assertion", "measurement"]
        if request.schema_version >= ENGINEERING_MEMORY_ACI_SCHEMA_VERSION: kinds.append("comparison")
        probe["properties"]["checks"] = {"type": "array", "items": obj({"id": text, "purpose": text, "basis": text,
            "kind": {"enum": kinds}, "required": {"type": "boolean"}})}
        probe["required"].append("checks")
        result["properties"]["coverage"] = obj({
            "covered": {"type": "array", "items": obj({"condition": text, "check_ids": {"type": "array", "items": text}})},
            "uncovered": {"type": "array", "items": obj({"condition": text, "reason": text, "blocking": {"type": "boolean"}})}})
        result["required"].append("coverage")
    if structured_submission(request):
        result["properties"]["summary"] = {"type": "string", "minLength": 1, "maxLength": 2048}
        for field in ("message", "resolution_condition"):
            finding["properties"][field] = {"type": "string", "minLength": 1, "maxLength": 2048}
        for field in ("purpose", "basis"):
            probe["properties"]["checks"]["items"]["properties"][field] = {"type": "string", "minLength": 1, "maxLength": 1024}
        for category in ("covered", "uncovered"):
            rows = result["properties"]["coverage"]["properties"][category]
            for field in ("condition", "reason"):
                if field in rows["items"]["properties"]:
                    rows["items"]["properties"][field] = {"type": "string", "minLength": 1, "maxLength": 2048}
    return result


def instructions(request):
    ctx = context(request)
    phase = ctx["phase"]
    result = INSTRUCTIONS + "\n" + (
        "INSPECT: read the captured code and propose checks, or report demonstrated blockers/inconclusive. You cannot pass yet.\n"
        if phase == "inspect" else (
            "REVIEW: independently read the candidate, probe source and actual observations. "
            "If the probe itself is defective and no candidate blocker is demonstrated, return probe_required with corrected probe files. "
            "Explain the observed probe error and its correction in summary. Preserve the complete checks array exactly: "
            "do not drop, rename or weaken required checks. The Host reruns the corrected probe on the same captured candidate "
            "and commissions another independent review. Remaining probe repairs: " + str(ctx["probe_repairs_remaining"]) + ". "
            "Otherwise decide passed/changes_required/inconclusive.\n"
            if can_repair_probe(request, ctx) else
            "REVIEW: independently read the candidate, probe source and actual observations. Decide passed/changes_required/inconclusive; do not request another probe.\n"))
    result += (
        "Your current filesystem is the review session. Source JSON is inline or at its exact advertised /inputs/NNNN.txt path. "
        "These JSON containers are not Python modules. /input exists ONLY in the later Process sandbox; do not search for it here. "
        "Use supporting_refs AND findings[].evidence_refs aliases from this request. resolved_refs and followup_of instead use "
        "only supplied prior report_ref#finding_id values, never input R aliases or current finding ids. "
        + ("No prior findings exist: resolved_refs=[] and followup_of=null. " if not ctx.get("finding_refs") else "See verification-context.finding_refs for prior findings. ")
        + "changes_required requires a sourced blocker and probe=null. Only probe_required supplies probe files and argv=['python','verification/probe.py',...].\n")
    result += ("Keep summaries and findings concise. Cite source locations without repeating source code or prior reports. "
        "Use short, self-contained probes with explicit imports and public training initialization APIs. "
        "Do not spend the invocation "
        "searching for files whose contents are already supplied in the captured source.\n")
    if structured_probe(request):
        result += PROBE_INSTRUCTIONS
    from .agent_aci import ENGINEERING_MEMORY_ACI_SCHEMA_VERSION
    if request.schema_version >= ENGINEERING_MEMORY_ACI_SCHEMA_VERSION:
        result += COMPARISON_INSTRUCTIONS
    if structured_submission(request):
        result += ADOPTION_INSTRUCTIONS + (COMPACT_REVIEW_INSTRUCTIONS if compact_review(request) else STRUCTURED_OUTPUT_INSTRUCTIONS)
    else:
        result += "Before returning JSON, check quoting/escaping of embedded Python source.\n"
    return result


def system_instructions(request):
    if structured_submission(request):
        return SYSTEM_INSTRUCTIONS.replace(
            "Return the complete requested AgentResponse JSON; the native Stop hook\nmay ask you to correct it within this invocation.",
            ("Submit the report fields via submit_review" if compact_review(request) else
             "Submit the complete requested AgentResponse via submit_review") +
            ", then finish\nwith {\"review_submitted\":true}. The native Stop hook revalidates it and may ask\nfor corrections within this invocation.")
    return SYSTEM_INSTRUCTIONS


SYSTEM_INSTRUCTIONS = """You are Hypoloom's independent code implementation verifier.
Follow the supplied phase, captured inputs and output contract. Inspect implementation
correctness and efficiency only. Use Read/Glob/Grep only when an advertised input needs
reading. Candidate code is read-only. Probes run later under the Host, not in this
session. Return the complete requested AgentResponse JSON; the native Stop hook
may ask you to correct it within this invocation. Do not invent scientific judgments.
"""

PROBE_INSTRUCTIONS = """
probe.checks declares 1..32 checks: {id,purpose,basis,kind:'assertion'|'measurement',required:boolean}.
At least one required assertion must exercise the implementation. Dynamically choose
inputs, references, tolerances and workload from this code, not a fixed method checklist.
The Host injects a read-only runner. In probe files use:
    from hypoloom_probe import check, measure
    with check('your_declared_id') as c:
        c.equal(actual, expected)
        c.close(actual_numbers_or_lists, expected, rtol=1e-7, atol=1e-9)
        c.true(gradient is not None, 'gradient exists')
    measure('your_timing_id', callable_workload, workload='shape/dtype/device and timed operations', warmup=2, repeats=10, synchronize=None)
Use explicit /input imports for captured candidate modules. Convert tensors to numbers
or lists for c.close, and use a GPU synchronization callback when needed. Only assert
properties justified by the implementation contract. Each assertion check must call
an assertion helper; print-only checks are not evidence. The runner records PASS/FAIL/
ERROR/NOT_RUN or MEASURED, timings and exceptions. Do not catch and conceal failures,
call sys.exit, overwrite runner/results or claim that a measurement proves correctness.
Reserved files: verification/_hypoloom_runner.py and verification/_hypoloom_plan.json.
coverage={covered:[{condition,check_ids}],uncovered:[{condition,reason,blocking}]}.
During inspect, covered=[] because nothing has executed. During review, cite actual
successful check ids, account for every required check before passed, and list material
untested conditions. Failed/missing required checks or blocking gaps prevent passed.
Probe defects are not automatic author defects. Use the current phase's bounded repair
allowance when offered; otherwise report inconclusive for inadequate evidence.
Check the probe itself: correct references, meaningful assertions and actual timed work.
Do not generalize small CPU evidence to untested CUDA, scale or combinations of cases.
"""


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


COMPARISON_INSTRUCTIONS = """
For an applicable implementation optimization, declare kind='comparison' and use:
    from hypoloom_probe import compare
    compare('timing_id', reference_work, candidate_work,
            workload='same shape/dtype/device, forward/backward and state reset conditions',
            device='cpu', warmup=2, repeats=5)
For CUDA set device='cuda' and supply synchronize=torch.cuda.synchronize. The Host
records paired samples, alternating order, medians and their ratio. Both callables
must perform equivalent work and reset mutable state/RNG as required; separately
assert the relevant output, gradient, data-order or state equivalence. Do not invent
a reference for a genuinely different method or impose universal speed thresholds.
Only a successful Process plus accepted review contributes bounded, source-bound
engineering memory to later author sessions. Unsupported speed claims, CPU-to-CUDA
extrapolations and author self-reports never become verified performance evidence.
Keep probes bounded: use importable files and a main guard for spawn; bound child
join/queue waits, inspect exit codes and stop children on failure. Avoid repeated
benchmark loops once the declared checks have completed. The Host checkpoints each
check so a timeout retains diagnostics, but incomplete/failed Processes cannot pass.
"""


REVIEW_COMPLETION = """\
Repair the specific reported fields and resubmit within the existing correction
budget. After success finish with exactly {"review_submitted":true}; do not repeat
the report. The Host Stop hook still revalidates it. Ordinary complete AgentResponse
JSON is also supported, but hand-writing long embedded source risks serialization
errors. Aim for a short paragraph summary, one finding per distinct defect, and
a small probe (usually below 12 KiB). Explanatory text totals at most 16 KiB;
summary/finding/coverage fields at most 2048 characters, check purpose/basis 1024.
Use source locations and supplied evidence refs instead of copying code/logs.
"""

COMPACT_REVIEW_INSTRUCTIONS = """
Call mcp__verifier-output__submit_review with the report fields at the top level.
The Host supplies task_kind and declared_effects. Pass probe source as string values.
""" + REVIEW_COMPLETION

STRUCTURED_OUTPUT_INSTRUCTIONS = """
Use mcp__verifier-output__submit_review with the complete AgentResponse envelope,
including typed_candidate and declared_effects. Its input schema is the output
schema; pass probe source as string values, letting the tool serialize JSON.
""" + REVIEW_COMPLETION


ADOPTION_INSTRUCTIONS = """
For relevant efficiency claims, trace the submitted argv/configuration to the
actual dataset, loader, model/loss and training/evaluation calls in captured code.
A supplied optimization helper or a read reference is not evidence that the active
entrypoint uses it. Probe the reachable path with a bounded representative workload,
not a disconnected reimplementation. Cover applicable data-order/mutation/RNG,
gradient and optimizer invariants; compare equivalent work when justified. State
adopted changes and remaining unmeasured paths in existing summary/coverage fields.
Distinguish loader-only, training-step and epoch results; low GPU utilization alone
does not identify a bottleneck. Missing optional optimizations are advisories, not
correctness blockers. For allocation/dispatch claims verify runtime behavior:
for example sys.getsizeof may include an owned array's buffer but not a view's.
If a small probe cannot establish a claim, record uncertainty rather than an
invented defect, guaranteed speedup or universal worker/cache recommendation.
"""
