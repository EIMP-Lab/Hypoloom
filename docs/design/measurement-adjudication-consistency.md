# Measurement and adjudication consistency

## Failure and scope

A completed local experiment measured exactly zero improvement. Its prose required
strictly positive improvement, but its structured MAXIMIZE/target=0 declaration
used the legacy inclusive comparison. The Host accepted SUCCESS; the adjudicator
returned refuted, then inconclusive, and exhausted its task retries. Historical
outcome IDs also passed the output schema but failed the Host's narrower terminal
tip reference domain. Native Stop had accepted these candidates before the Host
rejected them, wasting recovery opportunities after the invocation closed.

The failed boundary is the translation from hypothesis intent to an executable
measurement, followed by candidate admission. Git, transactionality and immutable
facts correctly retain the disagreement but cannot express an absent comparison
operator. No additional ledger, checkpoint or recovery authority is needed.

## Repair

- ACI 33 adds an explicit `success_operator` to the existing scientific measurement
  declaration: GT/GE for MAXIMIZE, LT/LE for MINIMIZE. This reuses the shared-goal
  vocabulary. The transformed numeric measurement is compared to the target;
  parser values remain unchanged. New declarations must choose an operator.
  Existing declarations omit the field and retain their inclusive semantics and
  serialized bytes. The operator freezes with the existing declaration.
- The producer, independent measurement Oracle and observer interpret that same
  declaration. No epsilon, inferred prose rule or fabricated parser verdict is
  introduced. Shared-goal acceptance keeps its existing independent criteria.
- ACI 33 projects the Host's exact terminal-tip reference domain into the schema
  and reuses its adjudication validator for native Stop and postflight. Native
  validation reads only frozen request inputs; final Host admission still reads
  current authoritative state. Errors identify chain, accepted outcome, expected
  and actual status. Scientific limitations belong in the summary; a local gate
  result does not establish the overall research goal.
- Existing finite invocation correction and retry limits remain unchanged.
  No automatic verdict rewriting, replay of exhausted tasks, state migration or
  mutation of an existing research run is included. Normal resume clears pauses;
  it does not reopen a final-failed task. New runs receive the repaired bundle.

The only added contract field is maintained in the existing measurement type,
schema, comparator and independent audit. It can be removed only after that
type is replaced by an equally explicit compatible representation. Historical
records must never acquire new threshold semantics during a read or audit.

## Verification and publication

Use synthetic regressions for equality boundaries, transformed measurements,
invalid operators, frozen declarations, legacy round trips, exact references,
wrong verdict correction within one native invocation, and Host/CPU closure with
independent Oracle/residue checks. Run the existing offline qualification once
the candidate is stable, retain initial failures and affected rechecks, and verify
package/bundle parity. This repair changes no GPU execution or provider transport;
new GPU or paid-model qualification is not required for the scoped claim.

The user authorizes dev commit/push followed by corresponding public main changes.
Keep development records and fixtures in dev; publish runtime, portable regressions
and bilingual user documentation through the existing selective synchronization.
