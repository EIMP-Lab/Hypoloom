# GPU memory planning

English · [简体中文](gpu-resource-planning.zh-CN.md)

See [runtime recovery](runtime-recovery.md) for ACI 28 pool preflight, node-scoped GPU recovery and context/progress observations.

New ACI 27 runs use evidence-based memory margins and conservative pool sharing.
Existing runs retain their accepted runtime and resource intent. Updating the
checkout does not change a running Host or upgrade its shared pool.

A subsequent init can prepare the project managed pool within its reviewed
Start now action. Creation, restoration and upgrades are separate from the
advisory repository version reminder; they preserve existing pool history and
require no unresolved dependencies. See [initialization](initialization.md).

## CUDA availability inside an experiment

GPU sandbox profile version 2 exposes only the granted NVIDIA device nodes and
read-only `/sys/module/nvidia/initstate` and `/sys/module/nvidia_uvm/initstate`.
These module-state files let the NVIDIA driver helper recognize loaded modules
when generic experiments expose `/usr/bin`; missing metadata can otherwise cause
CUDA error 304 inside the sandbox even while host-side CUDA works.

Before starting the experiment, an isolated check initializes CUDA, verifies the
visible GPU UUIDs and creates a context with a small allocation on each granted
device. It has a 30-second timeout. Failure exits with code 78 and logs
`HYPOLOOM_CUDA_UNAVAILABLE`; the experiment entrypoint is not executed and the
ordinary Process failure/cleanup path applies. No PyTorch dependency is added.

The experiment must also fail if its own framework cannot use the GPU grant;
`AUTO` allows the Host to choose CPU before dispatch, not a silent fallback after
GPU allocation. The driver check does not prove that later training uses CUDA or
replace the implementation's forward/backward/checkpoint smoke. CPU experiments
and measurement parsers retain their CPU isolation. Existing runs keep their
frozen runtime; applying this change requires a new run, not editing its bundle.

## What the budget means

The Host admits the maximum of the Profile floor, the Agent's estimate, the
comparable measured budget and any observed OOM budget bound. An optional
`initial_vram_per_experiment` remains until suitable successful measurements
exist. Device reserve is separate: the pool charges the maximum
required reserve once per device, not once per experiment. Agent estimates
already include their uncertainty; they are not multiplied again by the Host.

For comparable, attributed measurements:

| Evidence | Measured margin | Pool sharing |
|---|---|---|
| No qualifying completed success | 25% on any measured peak | Exclusive |
| Complete success after the last failed or unknown trial, covering earlier peaks | 15%, at least 512 MiB | Shared when capacity permits |
| At least three such successes, largest peak at most 105% of smallest | 10%, at least 512 MiB | Shared when capacity permits |

Budgets round up to 64 MiB. An actual reported OOM independently raises the old
admitted budget by 25%; reducing the measured margin does not remove this bound.
Failed/partial samples cannot establish a safe full-run maximum. Samples also
cannot guarantee continuous peak coverage. Matching workload descriptions must
cover model, data/shapes, precision, batch and execution phases. A successful
Process is evidence of execution, not proof that the scientific goal was met.
The percentages are engineering policy, not measured universal optima.

In the bounded `measurements` history, `observed_peak_lower_bound_bytes` retains the
largest known attributed per-device sample even when other samples/devices are
unknown. This diagnostic can reveal that the requested budget was below observed
use; it does not establish the full-run maximum or workload comparability.
Unknown coverage keeps `sample_peak_bytes: null`, cannot release the initial
floor, and cannot enable sharing. Exact `measurement_attempt_ids` still require
matching workload labels. Explain the next estimate using the actual workload;
the admission resolver and its complete-coverage requirements remain unchanged.
No process identity, foreign-owner history or device-wide usage becomes an owned
measurement. A successful admission does not prove that actual demand fit its
requested budget. Updating the checkout does not change retained runs.

### Device reserve

New templates use these settings under `methodology.yaml` → `budget`:

```yaml
gpu_vram_reserve_mb: 512
gpu_vram_reserve_percent: 5
```

For a positive percentage, reserve is the greatest of 512 MiB, the configured
absolute floor and that percentage of the **admitted per-device workload**,
rounded up to 64 MiB. A 12,288 MiB workload reserves 640 MiB; a 19,456 MiB
workload reserves 1,024 MiB. GPU use also requires a positive
`min_vram_per_experiment` chosen for the workload. On nonpooled fixed-grant runs,
that floor is the workload budget. An omitted or zero percentage preserves the
old fixed reserve exactly. Adopting an existing Profile does not rewrite it.

Init explains request protection, the percentage basis, a dominating absolute
floor and the pool's initial admission reserve separately. Unknown pool floors
remain unconfirmed. Changing the percentage alone cannot remove an explicit
absolute floor; review both Profile fields and the pool configuration. Existing
runs keep their frozen budgets.

The reserve does not replace workload estimation headroom. The Host uses the
resolved reserve for preview, admission and sustained-pressure protection;
accepted requests keep their values. On pooled runs, after an actual OOM, the next comparable
submission's existing 25% budget increase also increases proportional reserve.
Script errors, protective cancellation and external occupancy are not OOM.
Experts retain scoped lessons through repair, CYCLE_REPORT and SELF_EVOLVE;
they cannot change the operator's reserve policy or accepted requests.

The pool's floor remains binding for admission. A reused pool with a 4 GiB floor
still reserves at least 4 GiB, even for a 5% request. Init shows the actual pool
floor alongside the starting budget; `get_resources` exposes request reserve
in `budget_preview` and effective admission reserve in `capacity_preview`.
Runtime pressure protection uses the accepted request's reserve; a larger pool
floor controls admission without rewriting that request. Change an idle pool
only through its reviewed reconfiguration flow. An active pool and old runs
are not silently updated.

## Preview and waiting

`get_resources` still accepts an empty object. In ACI 27 it also accepts an
optional `resource_estimate` in the same format as `submit_experiment`, and an
optional positive `gpus` count. The response includes `budget_preview` and
`capacity_preview`: submitted and measured amounts, selected margin, OOM bound,
sharing mode, per-device capacity and shortfall. This is read-only and does not
reserve capacity. Submission validates again and captures its own evidence.
Same-run FIFO is retained: a smaller request may wait behind a larger one even
when it would fit. Do not shrink declared requirements merely to jump the queue.

The Web/TUI resource panel distinguishes:

- Capacity/fairness wait: the request waits for free capacity or earlier work.
- Exclusive wait: an uncalibrated request cannot share with an active pool job;
  shared requests likewise cannot enter a device with an exclusive commitment.
- Capacity unknown: reliable usable-capacity information is incomplete.
- Revision required: even the authorized devices' known capacity upper bounds
  cannot satisfy the request. Low instantaneous free memory alone is insufficient
  to make this determination. Driver reserve, when available, is shown separately.

A known impossible new submission is rejected with capacity details. A queued
request discovered to be impossible is released and closed as a technical
execution failure, then returned through the normal EXECUTION handoff. After
evidence linking, the Agent may submit a `replaces_experiment_id` repair within
existing budgets. It must reduce actual demand while preserving scientific
constraints; lowering the declared estimate cannot override measured bounds.
An accepted `experiment_failed` event carries the resource reason through
already configured notification sinks. No extra notification authorization or
polling event store is introduced.

## Deployment and verification limits

New pooled runs require a pool advertising `resource_replanning/1`. An older
pool is reported incompatible; exclusivity is never silently omitted. Arrange
pool deployment separately from ongoing research. Do not migrate accepted runs.
Exclusive means exclusive among pool commitments; external GPU users remain
outside that scheduler, with the existing live-pressure protections retained.

This change does not alter existing reserves, enlarge GPU authorization, move live
processes, automatically change scientific hyperparameters or combine missing
seed results. See [qualification](../qualification.md) for actual test scope;
synthetic-device regressions do not establish CUDA OOM rates or model quality.

The bounded measurement history also exposes `observed_over_budget` (unknown
when there is no usable owned sample) and `pressure_exceeded`. These are derived
feedback, not new admission rules: unknown coverage still cannot calibrate or
enable sharing. A pressure attribution of `self` means releasing the workload
can restore reserve; it does not identify the sole cause of device occupancy.
Review underestimation and external pressure separately before resubmitting.
