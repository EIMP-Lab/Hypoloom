# Recovered experiment evidence in Leader qualification

## Reproduced failure

The October 17 read-only diagnosis found a valid `SUPPORTED` assessment citing
two accepted results for the same hypothesis: an execution failure followed by a
successful repair. `WorkflowHost._record_assessment` intentionally includes the
hypothesis's historical evidence. `InteractionFacts.leader_qualifications` instead
required every cited result to be comparable. Observer input construction raised
`ValueError`, quarantining dispatch; two already running Processes subsequently
finished without outcome acceptance or workspace cleanup. The independent Oracle
reported two owned-work residues. Those residue checks and the dispatch quarantine
are valid. The disposable Host regression exposed the same all-comparable
assumption in the independent interaction audit after dispatch was repaired.

## Minimal repair

Keep the existing accepted facts, writer and `compared_tip` validator. Validate
the hypothesis/component/method-family, outcome and artifact binding of **every**
cited evidence record, including failures. Require at least one cited comparable
outcome before admitting the accepted `SUPPORTED` assessment as a contribution.
An uncited success cannot satisfy that requirement. Non-comparable historical
results remain context and never become qualifying outcome/evidence references.

Preserve existing assessment semantics: a comparable result may be `SUCCESS` or
`CLEAN_REFUTATION`; this reader does not replace an accepted scientific assessment
with a new numerical verdict. Keep ACI 21+ matching rules for individual positive
and negative contribution references and earlier-version compatibility.

The independent Oracle checks raw evidence bindings separately and requires at
least one comparable linked outcome. It does not call the production qualification
reader. Keep its ownership, publication and workspace-residue checks unchanged.

This is a reader/writer interpretation mismatch, not a failed persistence or
trust mechanism. No new schema, version, authority, hash, receipt or gate is needed.
Do not trim historical facts, rewrite the producer, suppress arbitrary reader
errors, clear quarantine, or exempt failures from Oracle binding/residue checks.

## Validation and publication boundaries

- Reproduce mixed failure/success histories and reversed evidence ordering across
  supported ACI generations. Reject failure-only support, absent/foreign links,
  missing outcomes, wrong artifacts and failure-only Leader citations.
- Exercise failure, repair, cumulative assessment, Observer dispatch, result
  publication and cleanup through the real Host and CPU Process path in disposable
  roots. Count terminal Attempts/outcomes and check the existing Oracle.
- Replay the retained facts read-only with the candidate reader; do not start or
  attach another Host to the real run. Run full offline candidate checks and the
  existing bounded CPU closure scenarios; retain original failures separately.
- Publish the runtime repair to both branches. Dev retains tests and this record;
  main receives only the runtime change, amended into its sole `v0.0.1` root.
  The branches' independent histories and public documentation remain intact.

Existing run bundles stay frozen. This change does not resume, upgrade, settle or
delete any retained research run. GPU/paid compatibility is outside this repair's
execution scope; prior qualification is historical evidence, not a new G4/G5 run.

Validation results: [qualification record](../qualification.md#recovered-evidence-reader-repair-2026-10-17).

## Failures found by the full offline check

The first complete selection passes 8,442 tests, skips 159 opt-in cases and fails
two unrelated assertions. Preserve that invocation rather than relabeling it.

The native context-recovery flow completes its real CPU science and Oracle, but
reports partial transcript coverage. Its saved transcript includes valid
`api-request-blob` records containing cached tool messages without event times.
The existing telemetry reader mistakes them for live tool events. Two minimal
regressions reproduce false partial coverage and, when a timestamp is supplied,
an invented extra read. Ignore that specific history-record type in the existing
reader. Actual tool events with missing timestamps and malformed/truncated data
must still report partial coverage. This is an observed interpretation failure;
no new event schema, hash or observation authority is needed.

The documentation check still requires README to duplicate detailed Run/Request
ID rules after the earlier user-requested quick-start simplification. Check the
README's init command and links to Setup/Usage, and retain the exact naming-rule
assertions in the three detailed operation guides. Do not reintroduce development
or implementation detail into the public quick start.

These repairs accompany focused retests before freezing the final candidate.
The context reader is the third public runtime change; the documentation test
remains dev-only. No real-run diagnostics are rewritten to claim complete coverage.
