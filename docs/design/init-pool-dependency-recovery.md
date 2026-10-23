# Init pool dependency recovery (2026-10-24)

Implemented with focused validation; see the
[results and limits](../qualification.md#init-pool-dependency-recovery-2026-10-24).

## Problem and scope

The October 23 init selected the managed GPU pool. All 510 historical pool
requests were RELEASED, but two retained runs were ACTIVE and one retained OPEN
Agents after their Hosts disappeared. The user selected ending those runs; init
had no executable closure path and eventually recommended nonpooled execution.
Released pool requests do not establish terminal run state. Preserve that check.

Implement a reviewed, bounded path in the same init: inspect dependencies, name
the exact run/revision, request cancellation, recheck actual closure, prepare the
pool, then create the new run. Inspection and Prepare only remain read-only.
Compatible live pools still reuse without interrupting existing runs. No silent
nonpooled fallback and no mutation of retained production runs during development.

## Source of truth and recovery boundary

Reuse CREATED bindings, CANCEL commands, Attempt recovery, FINISH_STOP, controller
locks, pool request rows and the original frozen runtime. No migration, SQL state
repair, new registry, digest, receipt or permanent retirement exemption.

Recovery needs the original Host launch inputs. A collected transient systemd
unit loses ExecStart; the existing configuration digest cannot reconstruct model,
adapter or credential-path choices. This reproducible loss crosses the transient
supervisor / durable run boundary and prevents verified cancellation startup.
Git and version hashes preserve code, not invocation inputs; a transaction cannot
recover inputs that were never stored. The smallest addition is optional launch
metadata in the existing Host-authored CREATED payload, containing validated
arguments and paths, never credential values. Ordinary transactions and the
existing runtime/configuration validator remain sufficient. Maintenance cost is
CLI argument validation and compatibility tests; merge this metadata with a
future canonical Host configuration if one is introduced. No separate file or
authority is added. Old bundles/records lacking this capability remain explicitly
unsupported rather than being patched or run through current code.

Only a sole-run root with absent Host/process ownership and no unreleased pool
request is eligible for automatic cancellation startup. The original bundle and
interpreter must exist; environment-only credentials cannot be reconstructed.
The Host rechecks the exact revision under its controller lock, verifies its
normal pinned configuration, persists CANCEL before abandoned-Attempt recovery,
and starts no scientific nodes or experiments. The existing final-report path
may use the run's original API budget; this is disclosed in review. Cancellation
does not pretend a missing report succeeded. Released pool records are inspected
read-only; cancellation does not require restarting the pool or sending releases
for records already proved released. A later maintenance recheck still requires
terminal runs, no OPEN Attempts and no Host/process ownership.

The selected run/revision travels in ordinary init arguments. Start now covers
the displayed cancellations and pool operation once. Partial/pending cleanup is
reported honestly and rechecked using the original command identity. A changed
revision, missing bundle/configuration, live process or unknown ownership blocks
automatic recovery; do not expand the selected set or restart research.

## Concurrency review

Project the existing max_active_processes and authorized UUID count into final
review. Explain that the cap applies to the entire run and is independent of pool
capacity. A cap below device count merits a single-GPU-workload suggestion, not
an automatic Profile rewrite or a promise that every device is currently free.
Capacity waits still obey memory, multi-GPU and sharing requirements.

## Verification

Focused tests cover read-only previews, exact authorization, stale selections,
live ownership, missing/legacy bundles, released versus outstanding pool records,
no scientific dispatch before cancellation, lost Attempt closure, repeated
requests, pool restoration after closure, and bilingual concurrency review.
Use disposable real Host/SQLite/socket/CPU flows and independent Oracle/residue
checks. No retained run, production service or paid/GPU experiment is changed.
Full candidate promotion remains separate from these focused checks.
