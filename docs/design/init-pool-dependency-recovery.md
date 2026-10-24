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
authority is added. Old bundles/records lacking this capability cannot use this
normal cancellation path; the separate administrative extension below handles
supported legacy bundles without patching them or resuming research.

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

## Legacy closure inside the same init (October 24 follow-up)

The afternoon init reproduces the remaining operational gap: both retained ACI 29
runs have no launch metadata or cancellation-first entry, and one original Claude
binary is no longer installed. The new run has not been created. All 510 pool
requests are released, but terminal run/Attempt ownership is still required.
Requiring the operator to repair these separately defeats init's reviewed flow.

Add an explicitly reviewed **offline closure** option for these stopped legacy
runs, distinct from ordinary frozen-Host cancellation. This authorizes ending the
exact run/revision without provider or GPU execution; it does not authorize
resumption with replacement settings or a different runtime. Keep the original
bundle, schema, version row, scientific history, pool requests and artifacts.
Use the bundled Host's CANCEL, abandoned-Attempt recovery, result collection,
failure publication and FINISH_STOP operations. A missing final report produces
an actual locally rejected report Attempt and the existing FINAL_REPORT_FAILED
transition, so the final run is FAILED, never falsely reported successful.

The failed asset is the executable recovery path across the supervisor/frozen
environment boundary, not the pool's ownership database. Git and the existing
version validator cannot recover a missing executable or invocation. They remain
mandatory for ordinary research startup; this separate administrative operation
has no scientific scheduler or provider adapter. Its process uses the original
bundle's types, storage and lifecycle validators, under the controller lock and
existing Linux filesystem/network isolation. Only the selected state root is
writable; the bundle and all other paths remain read-only. No migration, SQL
state rewrite, new schema, hash, registry, receipt or retirement exemption is
introduced. Compatibility code is limited to the observed bundled interfaces
and can be removed when these pre-recovery runs no longer require closure.

The original session preparation can reject its own auxiliary input directories
before archiving an interrupted invocation. Closure must preserve that diagnostic
and archive independently through the original session/Git/paper-source helpers;
it must not delete inputs, claim a successful invocation or weaken the Oracle.
Two disposable filesystem overlays of the actual retained roots demonstrate
ordinary collection/closure and complete session archives with clean bundled
Oracles. Source roots stay read-only and the overlays have no network or GPU.

Carry the separate choice through ordinary init arguments and the final review.
Prepare only stays read-only. After Start now, use a bounded managed worker;
persist the existing CANCEL command before recovery, retain that command across
rechecks, wait for closure in the same launch and prepare the same pool before
creating the already reviewed new-run identity. An ambiguous launch is observed,
not duplicated. A changed revision, live owner/process, unreleased request,
unsupported bundle or unreadable ownership state remains a specific blocker.
