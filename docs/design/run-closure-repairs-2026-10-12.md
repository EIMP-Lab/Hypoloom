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
test pass. The current candidate retains this limitation and has no G1 promotion.
