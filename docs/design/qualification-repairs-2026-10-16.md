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
