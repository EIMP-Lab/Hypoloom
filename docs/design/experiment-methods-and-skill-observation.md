# Experiment methods and skill observation

Status: implemented, 2026-10-09. Focused validation passes; full G1 remains
environment-limited. See [qualification](../qualification.md#experiment-methods-and-expert-skill-library-2026-10-09).

## Scope and reproduced evidence

The retained October 8 MERC run contains two otherwise complete native calls
whose repeated Stop messages changed only the candidate summary. The first
accepted message and later provider result consequently diverged. Existing
`native-output` diagnostics are sanitized, best-effort evidence, so they must
not become an authoritative result transport.

The same run has phase/seed/epoch logs but generally lacks intermediate timing.
Its experiments have different implementations and runtimes. A cross-run average
cannot supply a reliable timeout. Existing GPU observations include memory and
utilization, but not board power. Existing research methods already belong to
owner-local, evolvable Git plugins.

Web already exposes a skill catalog, plugin bindings and entrypoint diffs. It
does not aggregate native Skill calls. One accepted ML evolution changed only
`research-workflow/references/{evidence,execution}.md`; entrypoint-only diffs miss
that change. A second changed `resource-planning/SKILL.md`.

This work covers output handoff, current-experiment performance methods, optional
device observations, and the expert skill library. It does not mutate retained
runs, migrate their runtime, launch paid experiments or change scientific goals.

## One output through validation and delivery

Use the existing private invocation directory and Stop validator. Store the exact
accepted output atomically in an invocation-local file, replacing the in-memory
accepted text. This is the result being delivered, not another acceptance record.
Keep sanitized diagnostic copies non-authoritative. The adapter reads that output
for source capture and response construction; the successful CLI envelope must
agree with it. Provider accounting, cancellation, timeouts, failed Stop and source
checks remain binding.

Repeated Stop may propose changed text, including a revised summary, only through
the same complete schema, reference, source, parser and completion validators.
There is no summary exemption. Failed revalidation cannot reuse earlier acceptance.
Missing/corrupt output or CLI disagreement is a technical failure. The file has the
existing private-directory lifetime; no resume or success can be inferred from it.

Ordinary atomic writes, existing Attempt/session ownership, the existing validator
and focused regressions suffice. No new digest, schema, gate, receipt or parallel
acceptance authority is introduced.

## Evolvable methods and ordinary observations

Extend research-workflow with a conditional performance reference, linked from
implementation, execution, analysis and evolution. Work units and timing belong to
the task: training batches, files, simulations or evaluations. Reuse existing logs;
add bounded, flushed phase/progress/duration messages in the task implementation
where necessary. Separate preparation/warmup, steady work, evaluation and artifact
checks. Unknown future search or convergence remains unknown. Use current-design
pilot measurements for initial budgets and current-experiment progress for live
estimates. Runtime estimates do not extend accepted deadlines.

Use Host-owned probes and existing handoff. Never keep an Agent polling while a
Process runs. Correlate throughput/data wait and CPU/I/O with optional GPU samples;
device utilization/power are shared observations, not owned process measurements.
Tune only a demonstrated bottleneck with bounded comparisons. Scientific settings,
accepted trial coverage and evaluator obligations remain unchanged. LOCAL/GOAL
entrypoint checks include the actual selected output layout before costly work.

Extend the existing GPU observation with optional power draw/limit, temperature
and SM clock when the driver supports them. Missing optional telemetry does not
block scheduling or alter memory policies. Keep latest observations in the existing
resource diagnostic path; no new sampler, scheduler or time-series service.

CYCLE_REPORT retains relevant original evidence and the estimate/actual discrepancy.
SELF_EVOLVE or GRAPH_EVOLVE may improve the owner's methods/references through the
existing Git path. No-change remains valid; Host structural acceptance does not
prove a faster method. Existing version selection, pins and old bindings remain.
Private lessons do not automatically propagate across runs or users.

## Expert skill library

Add an expert-card shortcut and a global entry to the existing skills workbench.
Use one view with expert/stage/time filters, usage distribution and a time trend,
plus an evolution timeline. Skill detail retains content and supporting files,
extends diffs to the whole skill subtree, and links to invocation evidence.

Count actual native `Skill` tool uses from current-invocation logs. Reuse the existing
invocation parser, primary tool IDs, ownership/binding records and redaction. Exclude
replayed history and duplicate events. Separate successful tool results, failed
results and unanswered calls. Catalog availability, request binding, direct file
reads and prompt mentions are not calls. Loading does not prove faithful execution
or scientific benefit. Reference files belong to their skill, not separate skills.

Aggregate by owner, skill and bound version. Timestamps support time bins; missing
timestamps remain untimed. Missing/truncated/live capture produces observed lower
bounds, never a false zero. Use bounded reads and disposable caches, independent of
whether the browser was open. Do not replicate transcripts or create a second
durable statistics ledger. Deleted evidence cannot be reconstructed.

Evolution uses accepted Attempt `base_commit`/`commit` and actual skill-subtree Git
differences. Show changed skills/files, no-change and failed attempts, and distinguish
accepted versions from later binding and observed calls. Preserve concurrent versions.
All views remain authenticated, run-bound and read-only; follow existing export and
credential-redaction boundaries.

## Verification

Focused regressions cover repeated valid/invalid output changes and durable handoff;
unknown device telemetry; duplicated/replayed/incomplete Skill traces; owner/version
association; supporting-file-only evolution; no-change/failed evolution; and browser
filtering, drill-down and safe rendering. Exercise the real Host/native CPU path for
skill evolution and later binding, including independent terminal/ownership/residue
counts. Check packaging of the new reference and source/bundle parity at freeze.
Record environmental qualification failures rather than weakening assertions.

## Computation efficiency guidance (2026-10-13)

Read-only GPU observations and training-source inspection are retained at
`.local/analysis/gpu-utilization-20261013-212232/`. They distinguish low device
activity with live work from memory-constrained queueing. The inspected loops
and transfers are candidate hotspots; no profile attributes their cost and no
speedup has been measured. The existing shared skill covers timing and data
loading but lacks concrete guidance for compute rewrites and their equivalence.

Extend its existing performance and PyTorch references, retaining one conditional
entrypoint. Cover bounded phase profiling, independent-loop/pairwise computation,
unnecessary synchronization, compilation tradeoffs, task-specific output/gradient
checks, and normal-run time/memory comparisons. Preserve dependencies, the actual
objective and scientific settings. No-change is valid; no universal utilization
target, worker count, model-specific patch or mandatory optimization phase is added.

Use existing tests, logs, reports, Host-owned resource work and owner-local plugin
Git evolution. No new executable helper, schema, validator, telemetry service or
acceptance authority is needed. Scheduling, resource grants and current Processes
are unchanged. Existing package identity covers these resources; new plugins get
the seed, while retained plugins and already-bound requests keep their versions.
Verification checks seed delivery, version isolation, native Skill loading and
packaging; it does not establish future model behavior or actual training speedup.

## Optional GPU telemetry audit repair (2026-10-16)

Read-only audit of `MERC-causal-202610131405` reproduces 44
`process_resource_observation_mismatch` findings. The tracker already records
optional power draw/limit, temperature and SM clock, but the Oracle still requires
the older exact field set. All 44 result artifacts validate; the mismatch is at
the device-observation reader boundary, not evidence of changed experiment results.

Extend the existing Oracle's current prefixed observation shape with exactly these
four optional fields. A present value must be a nonnegative integer, excluding
booleans; unavailable values remain absent, as emitted by the tracker. Unknown
fields, required memory fields, GPU grant identity, pressure causes and attributable
ownership remain independently checked. Multiple devices use the same per-device
check. Historical unprefixed observations retain their existing interpretation.

No new schema, version marker, digest or validator is needed: the existing stored
observations, producer types and focused regressions identify the mismatch. Tests
must include actual tracker output, malformed observations, owned-memory evidence
and Host collection/publication, rather than dropping telemetry to make audit pass.
The corrected independent reader may inspect the retained run; its frozen bundle,
database, artifacts, scientific verdict and lifecycle state stay unchanged.
Network failure handling and restart recovery are outside this user-selected repair.

See [qualification](../qualification.md#optional-gpu-telemetry-audit-2026-10-16)
for before/after audit and validation scope.
