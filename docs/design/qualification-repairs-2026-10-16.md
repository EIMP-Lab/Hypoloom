# Qualification repair plan, 2026-10-16

Base: `ed74d1e`; the unchanged runtime candidate `8579de8` failed the
[complete qualification](../qualification.md#full-dev-qualification-2026-10-16).
Preserve its original failures and charges in
`.local/qualification/full-20261016/`. New evidence belongs in
`.local/qualification/repairs-20261016/`.

## Reproduced problems and bounded changes

1. The GPU Host copies up to 512 characters from a recognized failed-workload
   exception. A cut at a space is legitimate raw evidence, but the Oracle requires
   trimmed text and rejects its dependent resource learning. Accept that bounded
   excerpt without changing bytes; retain type, length, exception prefix,
   control-character, failed exit, pressure and ownership checks.
2. Parser execution projects declared relative raw files/directories into `/raw`;
   the shared verifier reconstructs the older relative argv. Reuse the existing
   pure argument projection while preserving exact mounts, source, CPU limits and
   identities. Check historical producer compatibility explicitly. Do not change
   recorded measurements or erase the known three historical parser sentinels.
3. Shared G4 performs Skill/resource calls before its inherited logical first
   step. Assert an empty history at the actual first request, then allow only the
   two exact current-Attempt prelude IDs. The native G4 closure must assert distinct
   fresh sessions per node under current ACI, preserving older-version behavior.
   Keep negative checks for foreign tool history and incorrect session reuse.
4. The execution MCP advertises `candidate` as an arbitrary object while its final
   response schema describes a different object. Both paid execution samples
   repeatedly construct invalid candidates. Reuse the existing design-candidate
   projection in the existing `output-schema.json` definitions and MCP tools/list,
   and direct execution to the accepted design output; explain ordinary
   `command` versus declared-measurement `execution_argv`. Do not relax admission,
   infer scientific declarations, or silently repair model output.
5. ADJUDICATE uses the evidence reference domain for `node_id`, although Host
   admission requires the exact `chains[chain_id]` hypothesis. Project those
   existing batch identities into the existing output schema and instructions;
   keep evidence references separate and retain scientific verdict checks.
6. The common Grounding sample wrote and tested a parser but exhausted its node
   time during test repair. Its invalid-label test incorrectly expected meaningful
   accuracy from invalid evidence; the model repaired it and passed ten tests,
   then timed out after additional notes/checks. Clarify compact table-driven
   testing, validity assertions for invalid evidence and prompt completion after
   passing tests. Preserve the qualifying case, scientific validity mutations
   and real parser execution.
   A larger allowance alone is not a repair.

## Existing authority and scope

The failures cross producer/reader and rendered-input/Host-validation boundaries.
Existing Git versions, typed candidates, accepted batch keys and ordinary
regressions provide the required authority. This plan adds no digest, persisted
contract, schema authority, receipt, checkpoint or alternate reducer. Schema work
only corrects projections of the existing admitted inputs and validators. The
Oracle remains independent of production scientific comparisons and acceptance.

Historical runs and bundles stay read-only. Network-outage and machine-reboot
campaigns remain excluded. No shared installation/service replacement or migration
is included. GPU checks retain the previously authorized GPU 0/1 and CUDA runtime;
resource-model work uses GPU 1. Existing authentication/model selection persists.
Paid qualification requires a bounded reviewed allocation; the previous three
samples retain 10.6981888 USD accounted, including 6 USD unknown reservations.
No unbounded paid retry loop is implied by the completion objective.

## Focused evidence

Adapter `claude-code-bwrap/85` carries the repaired input projections. The
producer/reader regression selection passes 188 cases, including exact historical
adapter 82 versus 83/84 argv behavior and malformed OOM counterexamples. Interface
and Host-tool checks pass 459 cases with four opt-in GPU skips. The new MCP
tools/list check submits the accepted design through the real Host, waits for its
CPU Process and verifies the published outcome with the existing Oracle.

Offline replay of the original local/resource G5 requests admits both original
accepted design candidates and rejects all nine original malformed submissions
and four rejected adjudications at the projected schema boundary. The historical
records, rejected responses and accounted costs remain unchanged. These checks
do not constitute a real-model compatibility pass.

Two native CPU scenarios pass in 97.04 seconds: independent census finds 51
Agents and six Processes, all CLOSED/SUCCEEDED, six published outcomes and no
duplicate ownership or live members of the 57 recorded process groups. Read-only
re-audit clears all nine current-reader findings in `MERC-causal-202610121755`;
`MERC-causal-202610120121` retains exactly its 11 documented historical parser
findings. Both retained roots are unchanged. Full candidate checks remain pending.

### Follow-up from physical G4

Candidate `3725a3d` completes all eight CUDA Processes and 72 Agents, all closed,
with eight outcomes, clean independent run/pool audits and no live process groups.
Both live tests nevertheless fail the same obsolete budget assertion: the current
one-success adaptive policy resolves a measured 330 MiB peak to 896 MiB (15 percent
with a 512 MiB minimum margin, rounded to 64 MiB); the fixture expects the former
25-percent-only 448 MiB. Preserve this 16-pass/two-failure assessment.

The saved intervals also demonstrate the current initial-exclusive policy: first
trials serialize, and the old three-second follow-up closes before the peer's
calibrated trial starts. Align the fixture with the already-tested resource policy:
assert exclusive initial trials, exact adaptive budgets and shared second trials;
hold both bounded CUDA cycles for 30 seconds so measured second trials can overlap.
Require actual overlap and resident credit against the exact sum of admitted
calibrated budgets, preserving zero unknown attribution and terminal audits.
No resource policy, grant, memory limit or scientific acceptance rule changes.
This test-only follow-up is prepared in an isolated checkout while the original
frozen full offline selection finishes; refresh every changed-file check.

The first corrected physical cohort records 17 passes/one failure: ordinary
calibrated sharing passes with 23.53 seconds of measured overlap, while the initial
memory case ignores the pool's higher-priority `exclusive_wait` reason. Saved
pool requests and capacity samples show 16,384 MiB initial exclusive budgets,
insufficient remaining allocatable memory, then 896 MiB shared budgets. Observe
either existing capacity or exclusivity wait, still require zero pre-dispatch
Attempts, and additionally require a live capacity sample below the initial
budget. The queue requirement is retained and its actual memory condition is
now explicit. Preserve the failure and refresh the affected initial-memory case.

### Full-offline template fixture

The complete `3725a3d` G1 selection records 8,368 passes, 159 explicit skips and
one failure in 1,245.47 seconds. The tracked template reaches GOAL_MET with six
admitted hypotheses, five requested designs, five matching design Attempts and
four closed CPU Processes/outcomes; independent Oracle passes. Its assertion that
all six designs must start contradicts the template's existing immediate
shared-goal stop policy and its own timing-independent Process assertions.

Keep the six-hypothesis assertion and all post-goal admission prohibitions.
Replace the speculative scheduling count with an exact reconciliation of committed
REQUEST_RESEARCH_NODE identities against design Attempts, admitted batch ownership
and preregistered hypothesis identities. Missing, duplicate and cross-chain design
records must fail counterexamples. This changes only the fixture; goal termination,
dispatcher, collection and scientific acceptance remain unchanged.

The exact original five-design evidence passes this reconciliation; deleting a
design Attempt, duplicating one or changing its chain fails. The complete template
scenario then passes in 13.60 seconds, including its unchanged scientific outcome,
stop-order, collection and Oracle checks. Freeze this final test candidate before
the final complete offline selection; do not erase the first G1 failure.

### Procfs observation race in the second complete selection

The complete `8f68e67` selection records 8,368 passes, 159 skips and one failure in
1,258.42 seconds; the repaired template passes. The Git-timeout cleanup fixture
raises ESRCH while reading a terminated helper's `/proc/<pid>/stat`. Its cleanup
predicate already accepts ENOENT and zombie state, but misses this read-after-exit
variant. Add only `ProcessLookupError` to that fixture's disappearance handling;
permission errors and live helpers still fail, with the existing bounds unchanged.
Production Git termination and prompt suppression code stays unchanged. A bounded
probe converts a real absent helper's ENOENT into ESRCH to reproduce the failure
deterministically; a separate permission-error probe must remain rejected.

The ESRCH probe fails before this one-line fixture correction and passes after
it; the permission-error probe still fails as required. All 35 version-check
cases pass. Refresh this affected file against the complete second selection by
test identity, preserving both original full-run failures. No production bytes
change after `3725a3d`, and the final qualification report distinguishes complete
selection coverage plus this targeted refresh from an uninterrupted green run.

## Verification order

### Authorized common retry and qualification budget forwarding

The user explicitly approves one common-only retry, at most 8 USD from the
remaining cohort allowance, with a 1,200-second run and unchanged 300-second
nodes. Use the existing explicit-allocation runner; inspect the admitted Profile
before model work proceeds. The actual sample has 8 USD total/per-expert caps,
no GPU grant and the authorized time limits. Keep all prior charges, including
the 2 USD unknown report reservation.

Preflight inspection also reproduces a qualification-helper budget defect in the
default cumulative entry: `_canary_launch` resolves the reviewed allocation, but
the caller passes `None` to `_profile`, which recomputes `20 - prior` instead.
The preceding local/common Profiles therefore admitted 20/17.9355848 USD rather
than their reviewed 6/8 USD. Their actual accounted charges remained below the
reviewed allocations; retain both facts and do not rewrite those Profiles. The
current explicit 8 USD entry is unaffected.

Pass the already resolved `remaining` allocation into the existing Profile
builder for both entry paths. Four offline boundary cases reproduce the excess
before repair and inspect both admitted budget values and source safety YAML
afterward, stopping before any Host/model launch. This changes only the paid
qualification harness, not production budgeting, accounting, default ceilings,
unknown reservations or scientific validation. It reuses the preflight decision
as the sole budget source; no new schema, gate or accounting authority is added.
Prepare this isolated fixture repair while the authorized candidate remains
unchanged, then refresh the affected files before integrating it.

### Common G5: report deadline and authored test inputs

The repaired common sample completes Grounding, the seeded target repair, a real
CPU Process, independently parsed GOAL accuracy 0.875 with both validity checks
true, and RESULT_ANALYSIS. FINAL_REPORT has only 15.90 seconds before the original
900-second run deadline and times out. This is a real qualification failure;
clean Oracle and GOAL_MET do not replace the missing report. Preserve its
2.6025296 USD reported cost and 2 USD unknown reservation; do not rerun it without
bounded authorization.

Grounding takes 289.78 seconds. Its authored test helper writes the reference-only
`ORIGINAL` object into the smoke directory, removing `computed_accuracy` from its
own previously complete fixture. The resulting test failure incorrectly gets
described by the model as Host normalization. Replaying the exact original
helper on a temporary copy reproduces the field removal without a Host/model.
Other initial failures confuse pair completeness with aggregate validity and
guess the wrong score after dropping the only mismatched pair. All eight final
authored tests and the existing common scientific-evidence validator pass on
copies/read-only state; they do not make the failed full sample a pass.

Clarify only the common canary commission: reference pairs are not complete raw
results; construct a complete smoke input with a calculated aggregate, run each
test in its own temporary raw directory, mutate validity conditions separately
and calculate expectations from the actual mutated data. Ask for compact factual
node prose and a short final report; preserve all required nodes, native Skills,
source reads, tests, actual experiment, exact schemas and scientific thresholds.
No runtime, validator, budget or deadline changes are included. The existing
admission/fixture regressions validate this bounded wording change; improved
model timing requires a newly authorized paid common sample and is not claimed
from offline checks. A larger wall allowance, if later proposed, must be explicit
and accompanied by this concrete repair rather than silently replacing the
failed 900-second result.

### Paid recheck: session fixture compatibility

The user authorizes the fresh 20 USD local/common/resource cohort. Its local
sample on `7ec6f04` completes 11 Agents and one CPU Process, publishes one outcome
and finishes its final report. Every Attempt succeeds, the independent Oracle
passes and all 12 groups are absent. The qualifier nevertheless fails because
it still requires one resumed session across design/execution/analysis. Current
ACI 24+ uses fresh sessions for each node, already asserted by native G4.

Reproduce this exact failure from the retained sample with read-only StateStore
and the entire existing closure validator. Align only the fixture with the
existing version boundary: distinct nonempty sessions across nodes/cycles, no
parent, exact `--session-id`; retain old versions' resume checks. Reject session
reuse, foreign parents, resume arguments, empty IDs and mixed versions. All other
scientific, tool, cost, publication and Oracle checks remain unchanged. Replay
the same complete validator without launching a Host or model; preserve the
original paid failure and cost. No new evidence authority or runtime change is
needed, and a second paid local sample is not authorized or required for this
read-only fixture correction.

The original sample fails this read-only replay before correction and passes the
entire qualifier afterward, with unchanged database/artifact evidence. Both
affected test files pass 226 offline cases with two explicit paid skips; four
current-version cases previously reproduced the obsolete-session failure. No
additional local model call or charge is incurred.

### Resource G5: current query and first-trial policy

The resource sample completes all 11 Agents, one real CUDA Process, checksum 6
and FINAL_REPORT, with clean run/pool audits and no owned residue. The qualifier
first fails because its expected resource intent omits the current first-trial
`sharing: exclusive`. Retained replay then exposes another obsolete assertion:
resource queries must be empty, although ACI 27+ explicitly admits `gpus` and
`resource_estimate`. Both original failures remain in the evidence.

Require the exact exclusive intent and validate current query arguments with the
existing advertised MCP schema; preserve the one-authorized-GPU restriction and
older versions' empty-input behavior. Counterexamples reject missing/shared
intent, invalid estimates/counts and unauthorized selectors/runtime/reserve
fields. Extract the existing durable assertions for read-only reuse; the live
wrapper keeps its original running-Agent/Process and projection-error checks.
Do not invent historical in-memory maps for replay. Independently verify actual
Host/pool sockets, process groups, released pool rows and all 30 published
projection files against the existing renderer. Only observation time and
database/WAL byte counts differ after normal Host-close checkpointing; all
research content and terminal revision match. The retained database, artifacts
and pool bytes remain unchanged. This is repaired evidence plus independent
closure verification, not a second paid sample or an uninterrupted green test.

The three affected G5 files pass 315 offline cases with three explicit paid
skips. Current runtime bytes and resource policy are unchanged. Original local,
common and GPU samples account 8.9460224 USD in this newly authorized cohort,
including the common report's 2 USD unknown reservation. Only the common sample
still lacks its final report; its focused fixture repair is ready, but timing
improvement and full qualification remain unproven without another bounded
authorization.

Reproduce each defect with focused counterexamples, repair it, then run focused
Oracle, native-tool, schema and real Host/CPU closure checks. Replay the original
paid requests and failures offline. Freeze one candidate only after these pass.
Then run complete G1/package validation, all ten retained G2 audits with unchanged
data checks, G3 production closure, shared/native G4, and the three bounded G5
samples. Independently count ownership, actual Processes, published outcomes,
pool terminal effects and live residue; preserve failures and unknown billing.

The historical sentinel audit remains an explained expected finding under G2's
existing supported-cohort rule; it must not become a clean scientific result.
Any new unexplained finding or missing required closure blocks promotion. Record
final results and limits in both qualification guides and route compact decisions
through CURRENT/INDEX. Commit and push the completed changes to `dev` under the
user's existing instruction.
