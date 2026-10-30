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

## Run-local skill outcomes and follow-up (2026-10-24)

The retained affective-computing expert made three private plugin updates, but
the next two cycles each repeated six failed DESIGN Attempts and delivered no
design nodes. The third update has no later sample. Accepted Git changes and
successful Skill loads therefore cannot stand in for improved task outcomes.
Some failures originate in the previously accepted hypothesis measurement, a
boundary that design instructions cannot repair by changing frozen declarations.

Project disposable scorecards from the existing bound plugin commits, Attempts,
native Skill calls and final-failure transitions. Reuse the execution-reliability
reducer for first-pass validation, logical-node delivery/retry and reported retry
costs. Group by owner, commit, stage, component and model. Show bound-version
context separately from nodes with observed calls to a named skill; a repeated
call in one Attempt is one exposure. Missing capture, incomplete nodes and mixed
bindings remain explicit. Valid scientific refutations are not execution failures.

Trace failed Attempts back to the exact accepted hypothesis relation and proposal
Attempt when retained. Follow each changed skill subtree through later comparable
stage/component/model observations, retaining exact diagnostic recurrence and
the actual later commits. No samples means awaiting validation; absence of the
same diagnostic is not a resolution or causal quality verdict. Multiple skills
may share nodes and costs; their scorecards must not be summed.

Give CYCLE_REPORT, SELF_EVOLVE and subsequent proposal work a bounded projection
of their owner's durable failures and version outcomes. This path reads metadata,
not transcripts or Git trees, and carries original Attempt references. Existing
reports and evolution summaries retain the target problem, supporting evidence,
expected observable change and later follow-up. Rebuild observations from the
ordinary evidence on each new input; do not create a scoring or issue database.
Keep the existing cycle triggers, budgets, version freezing and pin/revert rules.
A single-skill reversion is an ordinary SELF_EVOLVE commit restoring that subtree,
not a whole-plugin pin mislabeled as a skill rollback.

Git, stable primary keys, existing acceptance and metric validators, atomic
publication and focused tests suffice. No new hash, schema, gate, authority or
persistent registry is introduced. The Web views are authenticated and read-only;
runtime inputs apply to new bundles without changing retained runs. Verification
covers recurrence, no-sample updates, incomplete evidence, retry identity, source
links, bounded feedback, browser drill-down and real Host/CPU closure.

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

## Expert cards and version trends (2026-10-30)

The shared skill workbench interleaves experts in one frequency list, timeline,
version catalog and outcome section. This makes ownership hard to follow, and
the run-wide capture flag makes a fully captured expert appear incomplete when
another expert's evidence is missing. The user requests separate expert cards
and line charts for changes during skill evolution.

Reuse `skill_activity`'s bounded native-call reads to aggregate capture coverage,
counts and time bins by bound plugin owner before truncating the latest-call
listing. Count accepted changes from the existing complete evolution projection.
No new read pass, persistent observation stream, schema, gate or quality score is
needed. Git versions, Attempt bindings and the existing outcome reducer remain
the sources of truth; these additions are disposable read-only projections.

Each responsive card contains its own invocation totals, skill-frequency list,
calls/failures line chart and version outcome chart. Existing skill details,
outcome/repair follow-up and evolution history remain available inside the card.
Catalog pagination remains bounded; name search narrows the catalog/frequency
list, while charts follow expert/stage/time selection. Coverage belongs to the
owner, even when another owner's evidence is unavailable. Unknown repair links
and other unattributable totals stay above the cards.

The version chart reuses first-pass validation, node delivery and node retry
metrics within an exact basis/skill, stage, component and model context. It
orders versions by retained binding/update times, marks accepted updates and
shows raw numerators, denominators, unknowns and exclusions for the selected
point. Missing samples break the line; unknown/mixed models never connect across
versions. Accepted changes and correlated rates do not establish improvement or
causality. Sample task and skill-version links open existing detail endpoints.
Selection and expanded details survive polling, with keyboard/touch interactions
and English/Chinese labels. No experiment, Host, private plugin, live run or
frozen bundle is modified by this UI change.

Focused verification covers owner isolation, more than 200 calls, incomplete
capture, filters and immutable database reads, then actual Chrome interaction,
version gaps, context separation, refresh retention and responsive layouts.
See [qualification](../qualification.md#expert-skill-cards-and-trends-2026-10-30)
for evidence and validation limits.

The pagination follow-up addresses the user's report that the bottom buttons
appear inactive: they replace the bounded version catalog inside collapsed
expert cards while the visible call statistics and charts keep their scope.
A retained-run read returned 55 catalog entries, so the next page contains five
entries. Label the controls as skill-version pagination, hide them for a single
page, and expand/focus the changed catalogs on successful user pagination.
Reuse the existing request identity and pending promise for loading feedback,
duplicate-click prevention and keeping the old offset after a failed load. A
late response from another category cannot move the current page. These are
ordinary browser presentation states; no backend or durable evidence changes.

The user's subsequent refinement moves pagination into each expert card. Retire
the shared skill pager and keep ten entries per owner page, with the controls
next to the catalog heading. Reuse the existing `skills` detail endpoint's role,
offset and limit selectors. Only expanded catalogs request their page; no new
endpoint, aggregate ledger or all-owner prefetch is required. The browser retains
one bounded page, pending request and error per owner, scoped by run/stage/window.
Changing a page patches only that catalog's DOM. Charts and other expert cards
retain their state. Main observation refresh reloads expanded catalogs at their
current offset; reopening a catalog also refreshes its current page. Owner focus
retains pages, while stage/time changes and explicit clearing reset them. A late
response from an old scope is discarded; local retry keeps other cards usable.
Search applies to loaded lists, and single-page controls remain hidden. Existing
global pagination continues to serve the other workbench categories.
