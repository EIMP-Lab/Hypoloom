# Incremental experiment execution

Status: implemented and verified, 2026-11-04. The user authorizes design,
implementation, dev publication and corresponding public main synchronization
for recommendations 1–5. Recommendation 6 (monitor/UI work) is excluded.

## Failure and boundary

The retained MERC run used three seeds for every screening comparison because
its task required them. Engineering experiments 13 and 14 then executed the
same training source/configuration again; six repeated displayed measurements
matched. Large scripts hide many independent computations inside one Process,
so a later technical failure can cause already completed work to run again.
Existing study/performance guidance alone does not supply execution reuse.

The failed boundary is the handoff between completed computation, published
outputs and a later submitted execution. Existing Git, submitted sources,
artifact publication/digests, ownership and atomic writes remain authoritative.
An output filename alone cannot establish compatible inputs/protocol; a small
work-unit record must retain the actual command, declared scientific context,
declared output paths and completion status. This record is ordinary experiment
output, not a scientific verdict, independent ledger or new integrity digest.
Remove the helper if the existing execution driver later supplies the same API.

## Implementation

- Keep scientific staging in admitted project source and task instructions:
  bounded exploration, optional confirmation, frozen final evaluation. No fixed
  seeds, universal significance threshold, extra expert-round ceiling or second
  scheduler. The finite configuration-study validator keeps validation selection
  and final evaluation separate.
- Add a standard-library work-unit helper to fresh plugins. A task includes the
  bound helper in its submitted source and calls it at its actual entrypoint.
  Independent subprocesses use separate output directories. Only complete,
  matching units with all declared regular outputs can be reused. Commands,
  inputs/source references, resolved configuration, repetition and protocol must
  match by typed value. Explicit replication requires a reason and executes
  again; failed/incomplete units are rerun, never relabeled as successes.
- ACI 32 permits explicit `reuse_experiments` at submit. The Host checks same
  run/component/owner, terminal publication and non-integrity-rejected outcome,
  verifies the existing publication, freezes references in the existing execution
  environment and mounts selected publications read-only. No state-root or
  arbitrary filesystem access, cross-run lookup, silent dependency selection or
  promotion of partial work into accepted scientific evidence.
- Supply bounded prior execution/source information at normal design/execution
  handoffs and the persisted comparison to result analysis, including technical
  failure versus completed scientific negative results. Reuse is an explicit
  submission choice. Source differences are factual
  feedback, not an automatic scientific-equivalence judgment.
- Use the same helper for bounded performance probes through the actual admitted
  entrypoint, with runtime/workload conditions in their context. Probe processes
  cannot consume a scientific child's RNG/model state. Existing stdout and
  published outputs retain timing/evidence; no monitor change or telemetry service.
  Pure algebra/vectorization changes remain project-owned and need numerical
  checks and representative end-to-end measurements.

`execution_reuse.py` remains within the counted core-authority allocation:
transfer 150 unused domain lines, 125 to this helper and 25 to the Host. The
27,550-line total and all previously counted files stay unchanged.

## Recovery and compatibility

New work-unit outputs survive technical failure through existing raw-output
collection. A replacement explicitly binds prior publication inputs and executes
only missing or incompatible units. Artifact publication/parser retries retain
their existing stage-specific recovery. Completing the computation with a low
score is scientific evidence, not a technical retry. Checkpoint training resume
is separate: this helper reuses completed work and never claims an arbitrary
weights file preserves optimizer/RNG/data-loader state.

All old ACI readers, old submitted sources, preset driver behavior and retained
runs remain unchanged. No local Profile is silently rewritten: its three-seed
policy is still a task requirement until explicitly revised. Default templates
and workflow guidance explain task-defined progressive evidence and reuse.

## Verification

Reproduce partial failure, repeated identical work, changed input/config/protocol,
explicit replication, incomplete/missing output, test/validation separation and
probe reuse in isolated CPU tests. Exercise Host submission, read-only mounts,
failure collection, replacement, publication and independent Oracle checks with
actual subprocesses. Check old ACI rejection, replay/restart identity, foreign
ownership and publication corruption. Qualify the frozen candidate with the
offline suite, retained readers, installed package/bundle and native CPU flows.
No paid model calls or GPU speedup claims are necessary for this change.

The final candidate is ACI 32 / adapter 90. Full offline qualification plus
157 affected rechecks reconciles 8,709 passes and 239 explicit skips, with no
unresolved case failures. Ten final CPU/native roots close 206 Agents and 20
Processes with clean independent Oracles and no members of 180 recorded process
groups. Five retained roots and 3,072 artifact files remain unchanged; 209 runtime
files match the wheel, isolated installation and fresh bundle. See the
[qualification record](../qualification.md#incremental-experiment-execution-2026-11-04)
for original failures and limits.
