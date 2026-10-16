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
