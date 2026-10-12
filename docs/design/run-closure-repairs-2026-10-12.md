# Research execution and closure repairs (2026-10-12)

## Evidence and scope

Read-only diagnosis of retained run `MERC-causal-202610120121` found three
completed LOCAL experiments returning `-1` because parser argv used relative
raw paths from `/parsed`. All required files exist. Recalculation gives
`-0.01981210486021116`, `-0.004226645912406772` and
`-0.0025344263617077165`; the accepted historical values remain unchanged.
One native smoke already returned `-1` without an execution/schema error.

Other observed failures: 51 locally deferred calls following seven upstream
429s; nine DESIGN deadlines; nine raw execution errors (including imports from
the reference checkout and mixed devices); seven pressure cancellations; an
End-run dialog `ScreenStackError`; and inconsistent final-report counts and
unsupported explanations of missing measurements. The run closed CANCELLED,
with 15 successful, 16 failed and six cancelled Process records, all resources
released. Four cancelled records were synthetic, before process launch.

Existing runs, accepted parsers/results, private Profiles and shared services
remain read-only. Implementation and qualification use new disposable runs.

## Changes

1. **Parser path transport.** Extend the existing argv projection: relative
   arguments naming declared raw files or their non-root parent directories
   resolve under `/raw`, just as declared parser files resolve under `/input`.
   Preserve explicit absolute paths, undeclared strings, output cwd `/parsed`,
   read-only mounts and the result validator. Test both positional and
   `--option=value` forms, source precedence, missing inputs and legitimate
   negative measurements. Replay the three retained parsers in disposable CPU
   sandboxes against copied declared inputs. No task-independent rejection of
   `-1`: a finite negative value can be a legitimate scientific measurement.
   Authoring guidance requires ordinary semantic assertions over known fixture
   values and explicit errors for unreadable/invalid evidence, not sentinels.

2. **Entrypoints and bounded DESIGN.** Preserve the existing source/data mount
   boundary. `HYPOLOOM_TARGET_ROOT` is already the submitted source location;
   never remap it through the data-only `HYPOLOOM_READ_ROOT_MAP`. Exercise a
   patched import and a child import in the real sandbox with an overlapping
   reference checkout. Strengthen the existing implementation/repair guidance:
   build one small runnable change, inspect its actual module paths, reuse
   retained work, and put device smoke before training in the granted Process.
   Give DESIGN its existing last-20% reminder up to ten minutes before expiry
   (Grounding retains five). Do not add a universal ML validator or extend the
   50-minute invocation limit.

3. **Short provider waits.** Reuse the traffic condition/queue and its existing
   300-second maximum. Wait in-place for transient rate/overload/unavailable
   cooldowns only when the cooldown fits both that queue window and the request
   deadline. Check cancellation and appointments throughout. Authentication,
   operator-required and long waits still defer to Host; no upstream call or RPM
   credit is consumed while waiting. Maintain FIFO, concurrency and retry rules.

4. **Resource feedback.** Reuse attributed samples and admitted budgets to show
   underestimation and pressure explicitly in the existing bounded history.
   Distinguish a measured lower bound from complete calibration. Explain that
   `self` pressure attribution means release can restore reserve, not exclusive
   causation. Preserve admission arithmetic, exact-source comparability,
   exclusivity on unknown coverage and protective cancellation. Guidance must
   consult these observations before resubmission and distinguish workload
   reduction from merely lowering the estimate. No automatic cross-workload
   learning or live pool reconfiguration.

5. **Report accuracy.** Derive status, return-code, cancellation and raw-omission
   counts in the existing report context from the existing Attempt/fact rows.
   Include bounded provider failure details in recovered failures as well as
   exhausted ones. Instruct the report to render tables programmatically from
   these counts, keep source scope explicit, and never infer missing evidence
   or scientific refutation from a sentinel alone. Preserve citation validation
   and accepted science. Ordinary tests check these projections; arbitrary
   scientific prose remains subject to source review, not a new prose validator.

6. **End-run UI.** Dismiss only the active mounted dialog, so duplicate/late
   close events cannot pop the monitor or another modal. Preserve pending
   command handling. Use existing live attempt information to distinguish task
   cleanup from final-report work where that information is available.

## Assurance and validation

No new digest, frozen contract, schema, golden snapshot, gate, checkpoint,
receipt or second authority is required: these failures are addressable through
existing path projection, condition variables, immutable requests, derived
views and ordinary tests. Keep scientific freezing, source isolation, resource
protection and append-only ownership unchanged.

Run focused regressions during development, then real Host/broker/CC/CPU
publication and stop/report closure with independent Attempt/process census.
Freeze the final candidate before the full offline selection and package/bundle
checks. Record pre-existing failures separately; do not weaken checks or claim
physical GPU, paid-provider or scientific-quality qualification from scripted
CPU tests. See the corresponding section in `../qualification.md` for results.

## Separate recovery follow-up found during qualification

Full G1 exposed a resource-outage cancellation path in which a partial native
session retains the old large request, then receives another full request on
retry without a usage anchor. The conservative input check rejects both the
candidate and unmodified baseline under that timing; no CPU Process is created.
Other baseline timings pass. The original run diagnosed above did not establish
this failure. Keep it separate from this repair: reproduce both archive shapes,
then reuse the existing context reconstruction/session policy to avoid repeating
large immutable inputs while preserving source access, billing and ownership.
Do not increase the input allowance or add a new recovery authority to make the
test pass. Commit `8e8caad` retained this limitation and had no G1 promotion;
the follow-up below repairs that boundary.

## Recovery and qualification follow-up

This repair addresses the remaining G1 failures on top of `8e8caad`.
The resource-cancel case admits 126,419 bytes on the first exchange, but resumes
that history with another full prompt (the preceding prompt alone was 70,434
bytes). The new relay has no prior usage anchor; its conservative 147,308-token
allowance correctly rejects the combined request. The failed boundary is retry
session selection, not provider capacity enforcement.

Adapter 84 reuses the existing context diagnostics and retry selection. A resume
must leave byte headroom for the preceding full prompt and the existing estimate
margin for unmeasured reply/continuation text, alongside the existing token/output
check. Missing sizes or insufficient headroom select the existing
fresh reconstruction, retaining Git work, archives and continuation observations.
No new schema, checkpoint, hash, state registry or authority is needed: existing
Attempt records, immutable archives, transactions and ordinary regression tests
already carry the required evidence. The relay remains the final request check.

Two further reproduced failures affect existing recovery evidence. Requiring the
accepted invocation file caused rejected Grounding parser sources to bypass
their existing forensic capture. Decode only a schema-valid rejected final
candidate for the bounded source capture; keep its failed outcome and never
create an accepted response. The existing archive Oracle recognizes the native
`invalid_candidate`/`provider_output` rejection with its validation trace, while
retaining path, ownership, digest and byte-budget checks. Missing rejection proof
and an exceeded source budget remain failures. Ordinary mapping/integer checks
make incomplete diagnostic fields a reported archive failure instead of an audit
exception; boolean counts cannot stand in for a positive rejection count.
This restores the original source
archive assertion rather than replacing it with transcript-only evidence.

Long traceback recovery notes lost the final exception under a 512-byte prefix.
Keep both ends within that same allowance for failed tools. Scan the full original
text for credentials before choosing excerpts, including the omitted middle.
Successful tool excerpts and the existing twelve-note selection remain unchanged.
Both repairs reuse the published archive and existing bounded projections; no
new evidence store, schema or acceptance authority is introduced.

Triage each remaining failure against the current validator before changing a
test. Preserve rejected-output accounting, exact scientific effects and native
Host/CPU closure. Update stale fixture interfaces and model-suffix expectations;
move detailed operator instructions into the existing reference and reduce Host
duplication within the current code allocation. Do not loosen input capacity,
acceptance, preflight, code-size or lifecycle assertions to obtain a green suite.
Run focused tests first, then freeze the candidate and repeat full offline G1 and
package parity. Retained research runs and shared installations remain unchanged.

The first full follow-up run also identifies two native checks that expected an
avoidable intermediate capacity rejection. Require direct reconstruction, the
same correction/source evidence and exactly one CPU outcome; the analysis case
disables infrastructure retries entirely. Three asynchronous test boundaries use
the existing completion signals: persist request-start metadata while the reply
is blocked, finish rejected-response capture/cooldown accounting before checking
the next admission, and render the scrolled TUI controls before actual clicks.
These retain strict request counts, pre-response visibility and lost-reply
replay/cancellation assertions without changing production timing or controls.

The next full run exposes two further shutdown races. Textual keeps a screen's
`is_mounted` flag after child teardown begins, so a late snapshot can query an
already removed End-run button and crash. Ignore completed refresh messages once
the application's existing `is_running` state is false; retain normal refresh,
explicit cancellation and reply handling. The regression delivers a real refresh
during teardown, after removing the dialog controls, without a new lifecycle flag.

A resident Host can commit COMPLETED and exit while its pending projection was
prepared during ACTIVE. Shutdown previously waited for that worker and correctly
discarded its stale output, but never prepared the final view. Drain terminal jobs
through the existing refresh/collection path before shutting down the executor,
then publish their replacements through the same owner-thread validator and atomic
directory exchange. SQLite and accepted artifacts remain authoritative; no new
revision stamp, receipt or state source is needed. A held ACTIVE preparation across
cancellation reproduces the missing final projection even with no subsequent tick.

The literature responsiveness fixture separately used a 0.3-second client deadline
under whole-suite concurrency. Give its tool phase its own bounded deadline while
retaining the unreleased network-I/O gate, thread-ownership, cancellation, closure
and Oracle assertions. A synchronous I/O regression still fails before that gate
is released. The separate resident Host test retains its two-second maximum STATUS
latency assertion and actual CPU cycles.

One provider-rule fixture closed its relay as soon as the redacted HTTP error
arrived, cancelling asynchronous body capture before checking body-specific quota
codes. The retained failures contain zero received bytes and `relay_closed`.
Reuse the existing completed-exchange waiter before closing, and require a complete
body with the exact received byte count. Provider reduction and cancellation
semantics are unchanged; the test now establishes the evidence its assertion needs.

## Web activity observation during busy Host work

Read-only inspection of `MERC-causal-202610121755` finds real run traffic at
14 RPM while the managed Web view has no traffic and marks activity unconfirmed.
The user's port 46329 belongs to a separate TUI observer, using the same reader.
A direct STATUS call takes 4.145 seconds. Of six subsequent ordinary observer
reads, two time out at the existing three-second deadline and another is deferred
by its backoff; all three lose the live activity/RPM enrichment. The other three
respond and pass the existing snapshot matcher. No coherence mismatch appears
in this sample. Cold offline summary profiling also spends 7.128 seconds reading
history, including 5.105 seconds in reliability evidence; this is display work,
not a new run-state authority.

Give only the shared background reader a ten-second STATUS allowance through the
existing `host_timeout` parameter. Keep the synchronous default, bounded backoff,
strict snapshot matching and browser freshness limits unchanged. Its existing
single refresh worker coalesces clients; HTTP polls continue to return the last
observation with its original age. This changes no schema, persistent state,
validator or research operation, and needs no extra cache or health marker.

A real socket regression delays STATUS by four seconds: the old reader reports
unconfirmed activity, while the repaired reader preserves exact run/expert
traffic. Repeated HTTP requests remain nonblocking and share one STATUS call;
the fixture database is unchanged. Preserve a failed live validation sample too:
three STATUS calls exceeded even ten seconds, and two reads were deferred. A
later repeat returns three matching live observations at 10 RPM in 4.232–5.990
seconds. The change repairs premature rejection of ordinary busy replies; it is
not a claim that every Host operation now completes within ten seconds or that
unconfirmed activity should always be hidden. Longer Host work remains a separate
performance question. Reopen the observer to activate this fix; do not restart or
migrate the research Host or alter its frozen bundle.

## History growth and observation latency

The preceding profile identifies a reproducible display bottleneck: an ordinary
refresh cold-reads up to sixteen invocation logs, then decodes their JSON twice.
The 65-Agent sample spends 5.105 of 7.128 profiled seconds in reliability evidence.
This delays the subsequent STATUS probe. Host STATUS separately traverses
historical rows and shares its owner thread with tick work; a socket timeout
alone does not identify which Host operation blocked. Increasing the deadline
cannot establish a bound on either path.

The first implementation removes invocation-log reads from the shared polling
worker. Reuse the existing metadata-validated, 128-entry evidence summary cache:
polling may consume hits, while misses retain the existing deferred/unknown
coverage. One disposable background worker per observer warms at most sixteen
cold captures per pass, prioritizing recent Attempts, with a ten-second pause
after completion. It releases its SQLite read before reading files. It never
writes research state or publishes a separate observation. Changed files or
Attempt state still invalidate a cached summary, and direct detail/export reads
retain their ordinary synchronous behavior. Closing an observer prevents further
work and does not wait indefinitely for an already-running file read.

The native trace decoder will consume already-decoded events from the existing
invocation validator. Keep session/replay filtering, result and pair checks,
configuration-read suppression, file limits and partial-coverage semantics.
Nested evidence budgets must only reduce an outer allowance and charge reads
back to it. These changes reuse existing sources, metadata checks and validators;
they add no persisted cache, schema, health marker or acceptance authority.

Validation must block a real historical file read while proving that fresh
matching STATUS traffic still reaches HTTP, clients share one evidence worker,
and cached evidence becomes unknown after its source changes. Exercise history
beyond the existing cache capacity and count cold reads independently of timing.
Compare before/after display work against the same retained rows read-only;
separate profiler overhead, cold/warm evidence and socket waiting. Preserve both
successful and timed-out live samples.

Further Host work is a separate measured stage of this plan: instrument an
isolated real Host to distinguish queue/tick delay from STATUS calculation,
exercise increasing committed Attempt/fact counts, and identify the specific
long owner-thread operation before moving its preparation off-thread. Reuse the
existing committed charge/read caches and keep durable publication on the owner.
If row aggregation remains material, split historical view cadence from live
activity using the existing snapshot matcher and explicit per-view age; do not
combine stale history with a new timestamp or create a second lifecycle reducer.
This first implementation does not promise constant-time full summaries or
bounded Host ticks, and does not restart or migrate the active frozen run.
