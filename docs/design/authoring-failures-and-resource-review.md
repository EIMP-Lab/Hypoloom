# Authoring failures and resource review

Status: implemented in adapter 81, 2026-10-10. Focused validation and two native
Host/CPU recovery closures pass. See [qualification](../qualification.md#authoring-recovery-and-resource-review-2026-10-10).

## 1. GPU reserve configuration

A retained run and its private Profile both specify a fixed 4096 MiB reserve,
without a percentage. Its shared pool also has a 4096 MiB floor. The implemented
5% rule therefore was never selected. Preserve the existing calculation and
explicit configuration authority. Init must explain the request reserve, the
pool floor and the resulting initial admission reserve in readable language,
including when a fixed floor dominates the percentage. Unknown pool floors stay
unknown; active requests can impose a higher shared reserve, so the preview is
a minimum, not a promise of immediately available capacity. Update the selected
private Profile to 512 MiB plus 5% for future runs;
do not rewrite the current run or reconfigure a pool serving existing runs.

## 2. Design-time smoke tests and deadlines

A design node spent two 3000-second invocations implementing and waiting for CPU
training checks. Between them a resumed session hit its local context ceiling;
these three failures exhausted the existing two infrastructure retries. The last
invocation wrote a draft but never submitted accepted output. This is distinct
from GPU memory-pressure termination and the parser error below.

Keep CPU checks for parsing, configuration, paths and bounded implementation
semantics. Model forward/backward, save/reload and device compatibility should use
the intended admitted device. DESIGN has no GPU grant: author a bounded check in
the actual experiment entrypoint, then let EXECUTION submit it through the existing
Host/Process/pool path. Run that check before expensive expansion within the same
Process; fail normally on an error. Preserve frozen trial/metric obligations and
do not count smoke results as scientific outcomes. Reuse applicable successful
checks; do not repeat slow baselines before exercising the changed arm.

Give DESIGN the existing PreToolUse deadline advisory, including GOAL-only designs
without a local parser. Explain the deadline and bounded CPU/GPU handoff directly
in its runtime instructions and evolvable methods. Preserve drafts in the retained
worktree, reserve time for Stop correction, and avoid long sleep/poll loops. Keep
the admitted timeout and retry counts. An advisory is not proof of compliance or
a guarantee that arbitrary scientific code will finish in time.

## 3. Preserve parser validation failures

A local parser returned a numeric `value` and an object in optional `baseline`.
The validator reported only “metric value must be numeric”. After rejected Stop,
postflight reparsed the candidate without captured sources and replaced the useful
error with a missing `implementation.patch` complaint. Name the invalid field,
preserve the existing native rejection in retry diagnostics, and extract provider
accounting without revalidating a rejected candidate with missing workspace data.
Continue rejecting invalid output and preserve usage accounting and correction caps.

## 4. Separate live run state from terminal node failures

Web currently appends every exhausted node to DURABLE STATE and assigns the whole
state the blocked style, even while unrelated Processes run. Keep durable state
and actual Host/dispatch problems there. Show terminal node failures separately,
with a count and expandable role/stage/chain/time/reason/Attempt links. Explain that
these nodes will not automatically retry and do not imply a stopped run. TUI uses
the same distinction; historical failures remain visible after run completion.

## Boundaries and validation

All repairs reuse existing configuration, validators, native hooks, Attempt records
and Host-owned resource lifecycle. No new gate, digest, ledger or acceptance
authority is needed. Existing runs remain read-only and retain their bundle.

Focused regressions must reproduce field ambiguity/error masking, real native Stop
rejection and recovery, design deadline notices without acceptance, fixed/proportional
and higher-pool reserve reviews, and bilingual browser state/failure separation.
Exercise a real Host/native-CLI/CPU closure and independently check Attempt terminal
effects, outcomes, ownership and residue. Physical GPU timing is separate evidence;
do not claim it from scripted responses or device grants.

## 2026-10-13: Version-aware execution repair observations

Retained run `MERC-causal-202610121755` has 15/11/12 experiment creations across
four initial chains per expert. The old component-wide count subtracts one and
reports 14/10/11 revisions, warning on two experts without distinguishing science
from repairs. Accepted creation identities instead show 8/6/2 scientific iterations
and 3/1/6 operational repairs; no execution node has more than 2/1/2 repairs, against
each role's admitted allowance of nine. Repairs can inherit a scientific predecessor,
so testing only `iteration_of_experiment_id` double-counts them.

All 195 package files and `environment.yml` in this retained bundle match Git
`0ed0bc6`; current development baseline is `f1b43c9`. Their monitor, Policy, batch,
TUI and Web counting/rendering sources are identical. Matching adapter `/84` alone
would not establish package equality. The correction belongs in current source;
the original runtime, database, Profile and final-report lifecycle remain untouched.

Replace the existing monitor counter with a disposable projection over the same
accepted transitions/outcomes and `batch_limits`. Group modern repairs by component,
role, cycle, chain, scientific iteration and execution-node command. Count accepted
repair predecessors before scientific-iteration predecessors. The latest accepted
batch resets current-cycle statistics even before it creates an experiment. Legacy
single-chain runs preserve their original thresholds, including >30 rather than >=30.
Incomplete modern identities yield unknown counts. Existing Oracle validation
continues to own causal integrity; this display does not introduce another validator,
persisted counter, schema, gate or run-version migration.

The existing `cycle_revisions` observation now means operational repairs, with
nullable unknown and an additive `revision_activity` detail. Web and TUI consume
that same projection. Budget observations require a scoped repairable failure;
pending work is not failure, recovered work retains history, and older iterations
or stopped runs show historical notices. Remove claims about scientific convergence
and unverified causes. Existing Host safeguards and budgets remain unchanged.

Validation combines retained read-only replay, parallel/iterated/budget-boundary
fixtures, legacy thresholds, incomplete identities, bilingual browser interactions,
and actual Host/native-CLI/CPU publication and repair closure. Full candidate and
package validation are recorded in the qualification ledger, with process ownership
and remaining evidence limits independently counted.
