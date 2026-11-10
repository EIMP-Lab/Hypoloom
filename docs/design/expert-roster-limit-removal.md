# Expert rosters without a fixed size ceiling

Implemented and qualified on November 10, 2026. See the
[validation record](../qualification.md#expert-roster-limit-removal-2026-11-10)
for original failures, final reconciliation, native closure and retained controls.

The framework must accept any nonempty list of uniquely identified experts.
Adding a fourth expert currently fails the v3 projection called by v4, the
component compiler, ResearchContract, PolicySnapshot and the independent Oracle.
Clearing the rejected list also produces misleading unknown-role/owner errors.
In-memory admission confirms that one to three experts pass and four, eight and
sixteen fail even with independent finite resource limits.

## Scope and implementation

Remove only the upper bound in those five existing validators. Keep nonempty
lists, unique trimmed identities, valid references, component ownership and
all other contract checks. The same representations already carry arbitrary
lists; no new schema, persisted state, version gate or parallel validator is
needed. Existing accepted one-to-three-expert contracts have identical bytes
and interpretation. Existing runs and frozen bundles remain read-only; the
change is used when preparing a new run.

Keep resource semantics unchanged: Agent, Process, review and provider capacity,
per-expert forks, experiment/cost/time limits, shared-goal stopping and explicit
completion signals remain independent of roster size. The current defaults
derive capacity from the sum of expert fork allowances when fields are omitted.
Document that behavior and recommend explicit capacities when expanding a
roster; silently replacing defaults would alter previously valid Profiles.
The existing rotating component cursor and per-role dispatch queues serve larger
rosters. Debate still selects two eligible participants for three rounds; plot
summaries may show only the eight busiest experts while totals include all.

## Recent repair compatibility

The baseline is dev bce08b8 / main 2bdb082. This change does not alter the bounded
STATUS socket thread, snapshot age/revision rules or transactional derived cache;
live GPU selection/replay and sharing; strict measurement operators and native
adjudication correction; task-scoped retry and workspace preparation; staged
evidence/completed-work reuse; or unlimited research rounds with finite budgets.
Run their existing regressions against this candidate, rather than duplicating
their mechanisms in the roster implementation.

## Validation and publication

Portable coverage exercises admission, domain construction, scheduling snapshots
and independent contract audit with one, three, four, sixteen and sixty-four
experts, including low concurrency and invalid empty/duplicate rosters. Host
coverage must show every expert progressing with fewer execution slots than
experts, real CPU publication, preserved completion semantics and clean Oracle
closure. Include the existing native recovery and interaction scenarios.

Before publication, freeze the candidate and run full offline G1, retained-run
read-only baseline/candidate comparison, native CPU closure and independent
Attempt/process-group census, and wheel/installation/fresh-bundle checks.
No real GPU or paid model qualification is implied by the CPU/scripted-provider
results. Record actual results and any failures in the qualification guide.
The user authorizes dev commit/push followed by selective main synchronization;
dev engineering history stays on dev and public tests/guides accompany main.
