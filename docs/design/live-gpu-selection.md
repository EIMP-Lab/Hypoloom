# Live GPU selection and automatic pool initialization

## Scope (2026-11-08)

The user requests adding, removing or replacing GPUs while a project runs, then
selects option 1: new GPU initialization manages all accessible local GPUs;
operators adjust each run's selection in the TUI. Dev commit/push and selective
main synchronization, including a bilingual README feature-update section, are
explicitly authorized. Existing research runs and production pools are unchanged.

## Existing authority and execution boundaries

Previously the Host's launch allowlist fixed every later allocation. Removing a
busy card by editing that list could invalidate observation of a live Process;
restarting or rebuilding a shared pool could disturb other runs. Pool membership
and the current run selection are therefore distinct.

Lifecycle v12 adds RESOURCE_SELECT and the read-only RESOURCE_OPTIONS operation.
Selection changes use existing controller revisions, command identities and
append-only workflow transitions. There is no new state database, authority file,
hash, checkpoint or service. Ordinary transaction/idempotency handling protects
retries and stale revisions; startup replays the same transition history. Each
new dispatch refers to its selection revision, which the existing Oracle checks
independently. The original Profile, run configuration and experiment intent stay
immutable. Historical grants remain observable after a card is deselected.

An operator may select devices authorized by the bound pool and usable by the
Host. The pool advertises resource_selection/1. Pending or reserved requests can
be reselected; launching and running Processes retain their exact grants until
cleanup. Count, workload, sharing and memory budgets remain unchanged. Clearing
the selection holds new GPU planning/admission without spending further Agent
attempts. CPU-only runs do not acquire GPU authorization through this operation.

TUI uppercase G, the Web operator control and CLI share the same Host operation.
Web uses its existing operator scope and same-origin checks. A lost response is
retried with the original command identity; restart retries pool reconciliation
from durable history. Adding devices outside the pool remains pool maintenance,
not a live-selection operation.

## Initialization defaults

GPU init inventories NVML devices and checks device-node access without creating
a CUDA context. All usable devices form the default managed membership, including
busy cards; membership itself reserves no memory. Profile selectors or explicit
CLI flags determine the run's initial subset. CPU-only and explicit external or
nonpooled modes remain available. External membership is never expanded.

Final review distinguishes membership and initial selection and pins their UUIDs
in ordinary launch arguments. Later inventory growth does not expand that plan.
An old subset pool requires the existing reviewed reconfiguration; an old service
without resource_selection/1 requires a reviewed upgrade. Both retain existing
dependency checks and never cancel research implicitly. Inspection and Prepare
only do not start or replace services.

## Validation

Portable tests exercise selection replay, stale revisions, delayed pool cleanup,
pending reservation changes, active-job drain, independent audit and operator
interfaces. Initialization checks cover automatic membership, Profile subsets,
device permissions, pinned plans, external authorization and effect-free previews.
Publication checks and their limits are recorded in the
[qualification record](../qualification.md#live-gpu-selection-and-automatic-initialization-2026-11-08).
