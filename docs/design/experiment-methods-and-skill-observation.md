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
