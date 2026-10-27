# Observer closing, final-report consistency and skill follow-up (2026-10-26)

Status: phases 1–3 implemented; their full offline G1 and package validation are
complete. The October 27 framework follow-up below is implemented and verified
within the offline/CPU scope; commit and push to dev are authorized. Its validation
is separate from that earlier result. Retained research runs, original reports, private
plugins, Profiles and services remain read-only.

## Reproduced problems

The retained run `MERC-causal-advanced-20261017-202610242203` provides three
independent problems:

1. Two Observer Attempts report `interaction_source_invalid`. The producer uses
   `SharedAcceptanceHost.assessment_qualifies()` for shared-goal closing, while
   the independent Oracle used any `SUPPORTED` assessment plus one finished
   Leader/Debate as a permanent closing condition. The two frozen Observer inputs
   have no linked `goal_evaluation`, so the production closing predicate is false.
2. Component 2 outcome-8 is `CANCELLED/-15` in the Process observation and both
   result files, but the final report calls it `FAILED / rc=70 /
   raw_execution_failed`. The aggregate table has the correct six rc=70 rows;
   narrative and summary still disagree with actual Process records.
3. Three experts made accepted skill changes with later native calls, but the
   follow-up covered only terminal Agent failures. The engineering target is
   first completion rejection and the ML target includes Process failures, so a
   top-level `observations_available` did not validate their actual targets.

## Closing must use actual shared acceptance

`interaction_oracle.py` now follows the same boundary as the existing producer:
shared-goal completion mode is required; local assessment alone decides only
when no Grounding common evaluator is enabled. With a common evaluator, the
audit follows assessment evidence to the actual outcome and evaluates its
`goal_evaluation` using the independent `interpret_shared_acceptance()` reader.
`grounding_source()` checks the accepted source when needed.

LOCAL-only evidence, equality with a strict threshold and invalid evidence do
not close interaction. A real common pass closes it even when the local
assessment is REFUTED. A previous Leader or Debate does not itself create a
permanent closing condition. Source and observation mismatches remain errors;
the production Host is not imported to make the audit's decision.

## One Process account in report and summary

`final_report.py` reuses `process_attempt_details` and
`process_attempts_by_returncode`, which already come from durable Attempts and
accepted outcome relations. Narrow consistency checks compare explicit return
codes, terminal reasons and validation codes in `FINAL_REPORT.md` and the final
response summary. `final_report_oracle.py` applies the same pure check to
independent raw rows and published bytes.

ID matching respects exact token boundaries, including nested outcome IDs.
Lines with several different expected values remain ambiguous rather than
being assigned one status. Global aggregate counts are checked separately from
per-outcome status, so a best candidate is not blamed for a run-wide failure
count. Canonical `EXECUTION_FAILURE` is not rewritten as Process `FAILED`:
`CANCELLED` and an execution-failure outcome can legitimately coexist.
The October 27 review reproduces and fixes a false rejection for a correct
`rc=70` table cell: compare the captured numeric value, preserving the original
check for an actual contradiction.

This is not a general natural-language truth validator or a new report schema.
It checks only explicit Process fields. Validation and Stop correction enforce
per-outcome status and aggregate counts from the same existing inputs. No new
prompt prose is required; the existing process-detail instruction remains, and
its byte/continuation headroom is preserved.

## Separate skill evidence by lifecycle and ownership

`skill_observations.py` now distinguishes:

- terminal Agent failures, using the existing reliability and final-failure
  reducers;
- first completion rejections, using existing
  `structured_output_correction.rounds.validation_error`, with unknowns when
  those diagnostics are absent;
- Process terminal outcomes, linked through ExperimentPreregistered, experiment
  creation and source-Agent binding, with terminal reason, return code and
  validation code.

Repeated rejection rounds in one Attempt do not inflate Attempt counts. Process
cancellation is reported separately and is not a finished validation sample or
a skill failure. Process outcomes do not enter Agent first-pass rates, and
scientific SUCCESS or CLEAN_REFUTATION is not relabelled as a skill outcome.

Version follow-ups retain `evidence_kind`, exact source links, comparable
stage/component/model contexts, actual later commits and `awaiting_samples`.
Top-level `observations_available` means only that later evidence exists; it is
not a quality score or automatic selection signal. Runtime feedback carries
bounded output-rejection and Process groups with explicit history truncation,
while preserving existing byte limits and avoiding transcript/Git reads.

The Web view labels these evidence types and links the original Attempts. It
also reports later Process and cancellation counts without summing shared costs.

## Retained evidence and limits

After the closing repair, a complete read-only Oracle replay of the retained
run is clean, with 30 VALID result artifacts and no owned-process residue. This
is a correction of the independent audit, not a rewrite of the run.

The same retained report must fail the new Process consistency check: its
component 2 outcome-8 text is contradicted by the original `CANCELLED` record.
The aggregate summary must report six rc=70 Processes, not seven. The original
report is intentionally unchanged. The final full audit has no Observer issues,
but remains non-clean with exactly the intended `agent_postflight_invalid` and
`final_report_source_or_publication_invalid` findings for this report
inconsistency. Final replay of the other 14 retained roots has 13 clean roots;
`MERC-causal-202610120121` retains only its documented 11 historical parser
findings, with no new Observer or report inconsistency.

The skill replay preserves different conclusions per owner. ML's device failure
recurs in a later bound/called version and is then repaired by ordinary execution;
its pressure-related resume has no comparable later sample. Engineering's
EXECUTION first-pass rate does not improve. Affective's ADJUDICATE citation
target still has no later sample. No skill-content improvement is inferred.

## Authorized framework follow-up (October 27)

The read-only review of the private framework-followup draft accepts P0/P1 with
a narrower implementation, accepts P2 as consolidation of existing methods,
and defers P3. It replaces the earlier specialist-skill candidates above; no
private plugin is edited and no additional evolution cycle is triggered.

### Reproduced evidence

- All nine updated engineering EXECUTION first completions contain prose before
  JSON. The original messages fail at line 1, column 1; the JSON suffixes pass
  the existing schema generated from their retained requests. Suffix extraction
  is an audit experiment, never a proposed acceptance rule. Why the model keeps
  adding prose is not established, and a new schema-preflight interface is not
  justified by these samples.
- ML experiment-7 explicitly replaces experiment-6. Existing submission-source
  validation binds both frozen workspaces to their original Agents and
  preregistrations. The actual entrypoint diff adds two `.to(device)` calls;
  parser bytes remain equal, and the later Process succeeds under the same
  plugin version. This is ordinary repair evidence, not causal skill benefit.
- Each of the three evolution summaries is unconditionally cut at 1,000
  characters. Engineering loses its actual change and expected observation;
  other owners lose uncertainty and follow-up. Whole-payload trimming already
  removes history groups before updates. In-memory replay fits each full
  summary under 24 KiB by removing one more history group.
- Current follow-up compares every baseline problem with later non-base
  versions. It does not identify revision targets. The affective target Attempt
  contains both a JSON rejection and a terminal reference-domain failure, so an
  Attempt ID alone would not disambiguate the intended evidence.

### Small implementation slices

1. Preserve complete sanitized evolution summaries under the existing feedback
   byte budget. Prefer recent updates and exact evidence links over background
   groups. If a summary itself is too large, retain a bounded excerpt and its
   original Attempt ID with explicit truncation. Do not increase prompt limits
   or inject all history into EXECUTION.
2. Retain the actual native validation stage in the existing rejected-round
   observation: JSON decoding, schema, candidate, report, execution completion
   or parser smoke. Preserve original errors, correction counts and acceptance.
   Validation unavailability remains fail-closed infrastructure failure, not a
   rejected model completion. Old observations without stage metadata remain
   unknown; never infer stages by matching diagnostic prose.
   The existing public debug projection retains the optional field with its
   normal type, length and credential-redaction rules; raw and projected native
   correction traces must still agree.
3. Project ordinary Process repair links from existing accepted outcome and
   experiment creation identities. Keep source-Agent/submission references and
   predecessor/result statuses; do not infer links from time or equal return
   codes. Expose these associations in bounded runtime feedback and Observer
   views without reading complete logs or Git in the input-generation path.
4. Label follow-up as owner background, retain each diagnostic's conclusion and
   split later observations by their actual plugin commit. Keep missing evidence
   explicit. Do not manufacture automatic target status from the summary or
   treat matching stage/model as proof that the failing path was exercised.
5. Consolidate the public implementation method around meaningful bounded
   positive/negative checks on the actual changed path. Negative controls are
   risk-triggered; no mandatory extra training, GPU authority in DESIGN, new
   scientific criterion or framework-specific ML validator is introduced.

### Minimal-mechanism reasoning

Native Stop currently collapses typed schema errors and distinct validation
boundaries into `str(exc)`. This reproducibly loses the source stage between
validation and durable correction observations, obstructing reliable routing.
Git, stable Attempt/round identities, atomic writes and transactions preserve
the stored text but cannot recover discarded boundary information; tests can
detect the loss but cannot reconstruct it. The smallest addition is optional
stage metadata on the existing round, produced at the original validator and
passed through its existing readers. It grants no acceptance authority, needs
no database migration or separate schema, and costs only maintaining the labels
at the existing boundaries. Merge it into an existing general diagnostic code
if that code later conveys the same source information; old records stay valid.

The other slices use existing primary keys, source validation, Git versions,
byte bounds and ordinary tests. No target-reference field is added: automatic
target tracking would need a separately justified relation distinguishing
terminal, round and Process evidence. Supporting references and free prose are
not silently reinterpreted as that relation. No new ledger, scheduler, scoring,
automatic rollback, digest or parallel validator is required.

### Falsifiable completion criteria

- The three retained summaries survive intact within 24 KiB. Oversized Unicode
  summaries and multiple updates remain bounded, explicitly truncated and
  linked to their original source; credentials remain sanitized.
- Malformed JSON, valid-JSON schema errors and candidate errors have distinct
  producer stages without changing decisions. Unavailable validation consumes
  no correction round. Legacy missing metadata is unknown.
- The retained ML repair links experiment-6 to experiment-7. An adjacent success
  without a predecessor relation, a foreign owner or a missing source must not
  become a repair link. Cancellation is not a completed validation sample.
- Later versions remain separate, missing diagnostics cannot prove recurrence
  or resolution, and affective ADJUDICATE still has no later sample. Background
  failures never establish a revision target or a skill effect.
- Focused counterexamples precede candidate freeze. Real native Host/CPU flows,
  independent ownership/effect/residue counts, retained read-only Oracles, fresh
  full offline G1 and isolated package/install checks validate the final scope.
  The original two report findings and other retained audit results remain.

## Mechanism and validation boundary

Existing primary IDs, Git versions, transactions, ordinary validators and
bounded projections are sufficient. No database table, persistent score ledger,
new scheduler, automatic version selection, extra evolution trigger or second
scientific authority is added. The implementation does not justify or require a
new hash, digest, checkpoint, receipt or migration.

Focused tests cover the closing matrix and tampering, Process status/summary
mismatches, nested IDs, ambiguous multi-outcome lines, first-rejection and
Process recurrence, missing diagnostics, cancellation, ownership and bounded
runtime feedback. Native Host/CPU flows check final reports and two-cycle
learning with independent terminal/ownership/residue counts. Retained-run
replay is read-only. Paid providers, physical GPUs and production operations
are outside this change's default validation scope.

Final validation totals and original failure evidence are recorded in
`docs/qualification.md`. Specialist-skill changes, automatic target tracking,
new preflight interfaces, paid/GPU work and main publication are outside this
authorized implementation.
