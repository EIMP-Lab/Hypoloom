# Implementation verification

English · [简体中文](implementation-verification.zh-CN.md)

## Project-specific engineering requirements

Starting with ACI 45, a v4 Profile can give the independent verifier explicit requirements in `goal.yaml`. Each ID uses 1–64 ASCII letters, digits, underscores or hyphens. The Host reserves `formal_entrypoint` for the command sample.

```yaml
implementation_requirements:
  artifact_reload: Exercise the submitted producer and load its artifact with the project's public reader. Compare outputs on the same small input.
```

Choose requirements that apply to every submission and describe the expected behavior when an existing artifact can be reused. Authors receive the requirements at admission. The verifier receives the same IDs and text, declares a required assertion for each ID, and cites its successful execution in review coverage. A failed or unexercised requirement prevents a passing verdict.

The Host also records who can act on a failed check. When the author's submitted command sample fails, the author receives its execution evidence. A verifier can repair a faulty paired input on the same captured code. If the revised check repeats the same fixture error, the reviewer can return `inconclusive` or identify an independently evidenced defect in the submitted code. These outcomes keep formal execution pending or closed according to the existing repair policy.

## Independent verification workflow

The Host commissions an independent implementation verifier when an expert submits
captured code. It checks correctness, engineering choices and computational efficiency.
It does not generate hypotheses or judge scientific merit. Checks come from the
current implementation, without a predefined research-method checklist.

Choose an exact provider model ID during `/autoresearch init`, independently of the
research models. Using the base model requires an explicit choice. Optional effort:
`low`, `medium`, `high`, `xhigh`, `max`. You can instead set `verifier.model` and
`verifier.effort` beside `grounding` in `experts.yaml`, or pass
`--verifier-model MODEL_ID --verifier-effort high` to bootstrap or direct
`hypoloom.cli start`. Flags override the Profile in memory through the same compiler;
init shows the effective model and source. Profile files stay unchanged. The verifier
is not an `experts[]` member and inherits no author's private memory or model overrides.

Inspection produces sourced findings and a small Python probe when appropriate.
The Host executes it against read-only candidate code in the normal sandbox, with
authorized resources and a maximum of 120 seconds. A separate session reviews actual
observations before formal execution. Probe records and retry ordinals are separate
from scientific executions. Failed checks and insufficient evidence return through
engineering repair. The author releases Agent capacity using execution handoff while waiting.

GPU-backed probes have their own pool reservations. In new runs, the Host
releases a closed probe's reservation after its process group is confirmed absent;
stopping an experiment or run also withdraws probes queued before launch.
An open probe or unknown process liveness retains its reservation. Existing frozen
Hosts require an explicitly scoped operational cleanup for previously leaked
reservations; updating the checkout or restarting the observer does not clear them.

New probes declare their own checks and use the Host's assertion/timing helper.
The Host records executed assertions, failures, exceptions and missing checks;
printing PASS or returning exit 0 is insufficient. Evidence review includes the
probe source and specifies tested conditions, untested conditions and blocking
gaps. A measurement records a workload's timing, not a universal speed guarantee.
Protocol mistakes are corrected within the same invocation and its original
budget, using `methodology.yaml.output_correction` (the template allows three
failed validations). New runs support bounded probe repair: when evidence review
identifies a probe defect without a demonstrated candidate blocker, the verifier can
submit corrected probe files while retaining every declared check. The Host reruns
them against the same read-only capture, then commissions an independent review.
At most two repairs are allowed, further limited by the configured model retry
allowance. They do not consume the author's code-repair submissions. Exhaustion
remains inconclusive and cannot authorize training. Retained runs keep their frozen rules.

Unchanged captures may reuse recent evidence within the same run. Code, entrypoint,
environment or resource changes require another inspection. There is no universal
vectorization threshold, mandatory baseline equivalence or cross-run pass cache.
A small probe samples behavior; it does not prove complete program correctness.

From ACI 40, `submit_review` accepts the report fields directly, including
verdict, findings, probe, coverage and evidence references. The Host supplies
the task discriminator and effect, and validates the complete response. The tool
describes the fields for the current review phase. ACI 39 accepts the full report as
structured arguments, checks it with the existing validator, and returns precise
field errors within the same correction budget. After successful preparation the
Verifier finishes with `{"review_submitted":true}`; Stop revalidates the prepared
report before Host admission. Preparation alone cannot approve training. Candidate
files remain read-only and probes still run separately. Explanatory text is limited
to 16 KiB in total, with 2048-character summary/finding/coverage fields and
1024-character check purpose/basis fields. Probe source retains its 256 KiB limit.
Older runs keep their frozen protocol; updating the checkout does not change them.

From ACI 41, supply a probe object to `submit_review` for `probe_required`.
For `passed`, `changes_required` and `inconclusive`, omit the field. The Host
fills in JSON null and validates the verdict against the evidence. When a review
exhausts its correction allowance or the validator becomes unavailable, the
Host closes that attempt and its process group at its next poll. The existing
attempt budget governs retries. The independent reviewer can read each planned
check's observed status in the evidence overview. A missing result appears as
null. The current probe is available through `probe_plan` and its report
reference. Full observations and earlier distinct plans remain available.

The engineering brief asks authors to trace the actual entrypoint to data loading
and training, adopt compatible supplied optimizations or explain deferrals in the
existing implementation description, and record bounded equivalence/timing evidence.
New Verifier guidance checks reachable calls and configuration using existing
probe checks and coverage. Reading a reference or timing an unused helper does not
demonstrate adoption. Missing optional optimizations are advisory; neither low GPU
utilization nor a microbenchmark establishes whole-training performance.

ACI 37 adds a shorter, version-bound engineering reference for design, execution
and analysis. It covers repeated data reads, byte-bounded raw-feature caches,
worker-safe file handles, gradient-preserving loss implementations and causal
history reuse. The detailed references remain available in the same expert plugin.
A retained plugin without the brief uses its own full reference; current seed
content is never inserted into an older plugin version.

For a justified reference/candidate pair, a verifier can declare a `comparison`
check and call `hypoloom_probe.compare(id, reference, candidate, workload=...,
device="cpu", warmup=2, repeats=5)`. It alternates execution order and records
individual samples, derived medians and their ratio. CUDA comparisons require
`device="cuda", synchronize=torch.cuda.synchronize`. Correctness needs separate
assertions; a measured ratio below one is valid evidence, not an automatic
scientific failure. Changed algorithms need their own invariants when baseline
equivalence is inappropriate. Probe observations are saved atomically before and
after each check, so a killed check remains unfinished and cannot pass.

After an actual successful probe and independent passing review, the Host can
supply `--verified-engineering-memory` to later engineering nodes of the same run,
expert and component. For ACI 38 and later, this read-only input holds at most
four complete records within 64 KiB, selected from the most recent 16 review
commissions. The inline reading budget remains 6 KiB; larger documents are read
through the advertised pages. ACI 37 retains its original 6 KiB storage policy. Each record
names the code digest, capture, probe, review, covered and uncovered conditions,
and actual measurements. Oversized records are omitted whole; new requests
report the omission count even when none fit. The independent
Oracle reconciles these references and timings with the durable records. This
is conditional evidence recall, not model-weight training or a substitute for
checking a new candidate. No memory is produced from an author's claimed pass,
an unfinished Process or an inconclusive review.

New native requests advertise a `reading_view.index_path` beside verification
context, submitted source and engineering memory. Start with that index and its
overview. Source, probe scripts, logs and history are readable as text pages of
at most 12,000 bytes, with original source labels and line offsets. Each field's
pages concatenate exactly. `/inputs/NNNN.txt` still contains the unchanged
authoritative input; views use the same read-only mount and evidence references.
This avoids whole-file Read limits on escaped single-line JSON without removing
checks, rewriting evidence or expanding the verifier's decision authority.

Native CC engineering nodes also bound each foreground Bash invocation to at
most 60 seconds and the remaining invocation budget, reserving up to 15 seconds
(10% of the original budget) for handoff. The hook changes neither commands nor
existing permission rules. Spawn checks must use an importable file with a main
guard and bounded joins/queue waits. An already captured submission continues
through Host verification even if its author later times out; the Agent timeout
stays recorded, and an unsubmitted draft is never promoted automatically.
Provider thinking defaults are unchanged.

TUI shows a compact **Verifier** card beside Grounding with the model,
current phases and per-expert blocker/advisory counts. Follow-ups of the same finding
are counted once in the current summary. Press **v** for paginated reports, individual
probe checks, coverage and invocation diagnostics. The Web card shows per-expert
deliveries, triggers, reuse, completed first-pass results, blockers and advisories.
Report generation, delivery in an expert task input, response, repair submission and
independently verified resolution are distinct. Delivery does not establish reading
or understanding. Finding resolution requires an explicit prior report/finding reference.
Verifier accounting is global; subject attribution does not bill the work twice.
Use **Verification history** in Web for paginated evidence.
Stale Host observations remain unconfirmed; runs predating verification show unsupported status.

Grounding, Verifier and expert cards have distinct icons and show their recorded
base-model names. Multiple names indicate different invocations; unavailable
names remain `—`. Active status lights breathe with fresh run-scoped RPM while
the Host is confirmed, and stop animating when that observation expires or the
Host is unconfirmed. They are request-rate indicators, not process heartbeats.
Web respects reduced-motion preferences. Display updates can read compatible
retained runs without changing their frozen execution code.

Verification details also separate inconclusive verification and invocation failures from
author code defects, and shows phase time, output corrections, check results,
coverage and available wire/response model observations. An omitted thinking
parameter does not establish disabled reasoning. Delivery evidence reads a
bounded recent window; older unconfirmed deliveries are labelled partial rather
than counted as proven undelivered. Aggregate delivery and first-pass counts
still use the complete verification history.

This applies to new runs. Existing frozen runs are not hot-upgraded or migrated.

## Paired perturbation probes

In ACI 43, call `paired_inputs` inside a probe check to draw one sample and compare two copies. The perturbation edits its copy in place. The selectors identify all inputs that must stay fixed and the region that must change.

```python
from hypoloom_probe import check, paired_inputs

with check("prefix_invariance") as c:
    a, b = paired_inputs(draw_sample, change_suffix,
                         control=lambda row: row["prefix"], target=lambda row: row["suffix"])
    c.equal(candidate(a), candidate(b))
```

A changed control or an unchanged target produces `ERROR`. A failed output assertion produces `FAIL`. For arrays or tensors supply an appropriate `clone` and scalar-valued `equal` function, and select values that support a snapshot, such as detached tensors.
