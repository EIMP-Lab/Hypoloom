# GPU memory planning

English · [简体中文](gpu-resource-planning.zh-CN.md)

See [runtime recovery](runtime-recovery.md) for ACI 28 pool preflight, node-scoped GPU recovery and context/progress observations.

New ACI 27 runs use evidence-based memory margins and conservative pool sharing.
Existing runs retain their accepted runtime and resource intent. Updating the
checkout does not change a running Host or upgrade its shared pool.

## What the budget means

The Host admits the maximum of the Profile floor, the Agent's estimate, the
comparable measured budget and any observed OOM budget bound. An optional
`initial_vram_per_experiment` remains until suitable successful measurements
exist. Device `gpu_vram_reserve_mb` is separate: the pool charges the maximum
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

## Preview and waiting

`get_resources` still accepts an empty object. In ACI 27 it also accepts an
optional `resource_estimate` in the same format as `submit_experiment`, and an
optional positive `gpus` count. The response includes `budget_preview` and
`capacity_preview`: submitted and measured amounts, selected margin, OOM bound,
sharing mode, per-device capacity and shortfall. This is read-only and does not
reserve capacity. Submission validates again and captures its own evidence.

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

This change does not lower device reserves, enlarge GPU authorization, move live
processes, automatically change scientific hyperparameters or combine missing
seed results. See [qualification](../qualification.md) for actual test scope;
synthetic-device regressions do not establish CUDA OOM rates or model quality.
