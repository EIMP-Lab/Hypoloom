# GPU sharing and execution recovery — 2026-11-04

The user ended the research run and authorized framework repairs, dev publication
and corresponding main updates. Retained research state and frozen bundles remain
read-only. This work changes new runs; it does not repair NVIDIA firmware or the
research project's algorithms.

## Reproduced problems

1. All 45 GPU requests used pool exclusivity. A request for 18 GiB waited despite
   sufficient allocatable memory on 80 GiB cards. The September 27 calibration
   rule, introduced after contention on 24 GiB devices, coupled measurement
   confidence to sharing permission. Actual device capacity was detected correctly.
2. A task using about 4 GiB against an 18 GiB budget was terminated when the device
   reserve fell below its threshold. The old `self` label only established that
   releasing the task could restore reserve; it did not establish a budget overrun.
3. A failed CUDA process coincided with an owned-process Xid 38. The Host retained
   the Python traceback but not the kernel event. Device failure therefore looked
   like another ordinary execution repair and exhausted the same allowance.
4. Agents could prepare replacements before discovering that their repair budget
   was exhausted. Round limits were available as static configuration, without a
   clear current-round/remaining-round explanation in their task input.

The kernel evidence contains eight Xid 38 events on one device in the current
boot. Other devices have Xid 43 history; this does not establish the same fault.
Private logs and research artifacts stay in their original ignored locations.

NVIDIA's [Ampere Xid catalog](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)
classifies Xid 38 as a FECS watchdog timeout / driver firmware error. It does not
establish a defective card. The catalog's immediate action is IGNORE and its
investigatory action is CONTACT_SUPPORT; this framework's node pause is an
execution-recovery policy after a failed, attributed Process, not a claim that
every such event requires a reset. Xid 43 has a different application-fault
description. [NVIDIA's diagnostic guide](https://docs.nvidia.com/deploy/xid-errors/working-with-xid-errors.html)
describes kernel evidence and PCI/GPU identity correlation.

## Design

Use ACI 30 to distinguish new policy from retained ACI 27–29 admission. Existing
versioned requests already provide this boundary; no new database, policy ledger
or migration is needed. Historical budget calculations must continue to audit.

- Default to budget-based pool sharing independently of calibration. Keep the
  existing initial/floor budget, measured margins, OOM growth, reserve accounting,
  atomic admission and pre-execution recheck. Explicit existing pool exclusivity
  remains enforceable; fresh research requests do not synthesize it from missing
  measurements.
  A comparable attributed partial peak can raise the budget with conservative
  headroom; it cannot calibrate a smaller margin or release the initial floor.
- For current pooled workloads with a complete live ownership sample, sustained
  pressure cancels an over-budget workload, while a workload within its admitted
  budget remains running and records `within_budget`. Unknown ownership retains
  the existing conservative protection. Legacy counterfactual attribution remains
  readable. Pressure alone never becomes OOM learning.
- On GPU failure, read Linux kernel Xids with bounded nonblocking reads. Associate
  only the granted UUID/PCI identity, the Process time window and already observed
  owned PIDs, including process birth time and a complete ownership observation
  at or after the event to exclude PID reuse. Preserve inaccessible/truncated
  evidence as unknown/partial. Never
  infer Xid 38 from an arbitrary CUDA traceback or treat Xid 43 as firmware failure.
- An owned-process Xid 38 uses the existing RESOURCE_BLOCKED/RESOURCE_RECHECK
  workflow. Record the block atomically with Process closure. Collection still
  publishes the actual technical failure; the affected execution node waits for
  operator resource recovery instead of attempting speculative code fixes. A
  recheck verifies resource availability, not the absence of a latent GPU defect.
  No reset, driver change, machine-wide quarantine or historical-run operation is
  performed automatically.
- After explicit resource recheck, replacements for confirmed device faults use a
  separate bounded allowance derived from the existing
  `budget.max_infrastructure_retries` setting. Ordinary execution repairs retain
  `inner_loop.repair_attempts`. Counts come from accepted replacement relations and
  closed Process evidence; there is no persisted counter. Global experiment, cost,
  wall-clock and invocation limits continue to apply.
- Expose initial submission, ordinary repairs, device recoveries and remaining
  allowances in experiment tools. Add current research round and remaining future
  rounds to the existing expert input. A round remains a full research cycle;
  neither a scientific iteration nor a repair starts another round.

The new diagnostic is evidence attached to an existing Process observation. Git
or transactions alone cannot capture a kernel event after the kernel log rotates;
bounded event fields are the minimal additional evidence. No raw kernel log or
unrelated process command line is retained. Diagnostics never prove hardware
causation, and missing ownership cannot earn a recovery-budget exemption.
Maintenance is limited to bounded Linux/NVIDIA parsing, ownership validation and
legacy readers. If the existing runner later supplies equivalent attributed
driver evidence, remove this acquisition helper while retaining historical
observation compatibility; do not add a second device-health database.

## Validation

Reproduce cold/partial-history sharing on synthetic 80 GiB cards and preserve
24 GiB capacity limits, committed reservations and explicit exclusivity. Exercise
pressure below/above budget and unknown ownership. Test kernel attribution against
wrong GPU/PID/time, permission errors, truncation and non-firmware Xids. Close
resource failure, blocked-node recovery and bounded replacement through the real
Host, then independently audit its durable relations. Verify current/legacy
budget policy, full offline checks, bounded native CPU closure and packaging.
Record actual commands, failures, passes and untested hardware boundaries in the
qualification record before publication.
