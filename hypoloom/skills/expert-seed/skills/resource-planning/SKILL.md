---
name: resource-planning
description: Estimate an experiment's per-device GPU memory from comparable workload measurements, query live authorized capacity, and learn resource-planning lessons through cycle reports and SELF_EVOLVE.
---

Expert: {{ROLE}}
Focus: {{FOCUS}}

Use this skill for DESIGN or EXECUTION resource decisions, and when reporting or
evolving a resource lesson. These instructions are stable strategy; injected
resource experience is scoped evidence, and the initial capability snapshot is
historical. When available, `mcp__host-tools__get_resources` returns timestamped
authorized capacity, wait/unknown reasons and this expert's bounded history.
DESIGN has read-only resource access, never submit or cancel permission.

Choose `CPU` when GPU acceleration is immaterial, `AUTO` when CPU is scientifically
valid, or `GPU_REQUIRED` for an evidenced GPU requirement. Do not change that
scientific requirement merely because capacity is temporarily unavailable.

For ACI18 EXECUTION on an explicitly pooled run, pass an optional
`resource_estimate` to `submit_experiment`:

- `workload`: 1–16 string-to-string configuration labels, with keys up to 64 and
  values up to 256 characters. Distinguish model/method, batch, precision and
  shapes when relevant; non-ML workloads need their own comparable labels.
- `peak_memory_mib`: positive per-device workload peak including uncertainty
  headroom, separate from the operator's device reserve.
- `basis`: evidence, scaling assumptions and uncertainty, up to 1024 characters.
- `measurement_attempt_ids`: up to 32 exact comparable CLOSED Process Attempt
  IDs from this run/role/component/method-family; omit or use `[]` without any.

Prefer same-workload attributed peaks, then explained scaling from nearby
measurements, then conservative workload accounting. Device used/free memory is
shared pressure, never this experiment's own use. Unknown or partial own-use
samples remain unknown. Pressure cancellation alone is not an observed OOM.
History includes observed_peak_lower_bound_bytes when an attributed sample
exists, even if another sample/device was unknown. This is a diagnostic lower
bound, not a complete maximum or proof of workload comparability.
Unknown coverage keeps sample_peak_bytes=null even when a lower bound is known.
Use these observations to investigate an undersized estimate and explain your
next workload estimate in basis. A successful admission is not a measurement:
do not reuse its budget as evidence that actual demand fits it. Review lower
bounds from nearby workloads when scaling, and use measurement_attempt_ids only
for exact comparable labels. Do not relabel sources or use partial coverage to
reduce margins or enable sharing. An omitted estimate cannot learn comparable bounds.
Do not invent a precise estimate; an omitted estimate keeps the Profile initial
workload budget (or its floor when no initial value is configured) and is
explicitly unestimated. A bounded measurement is useful when feasible.

For pooled runs, the Host resolves matching measured history only at first
submission and freezes those source references. Admission uses the maximum of
the Profile floor, submitted estimate and compatible measured/OOM lower bounds
with Host headroom. The pool chooses actual authorized devices for each Attempt;
retries may use another card. Live observations cannot rewrite accepted sources.
An optional operator `initial_vram_per_experiment` separates a conservative
starting estimate from `min_vram_per_experiment`, the hard workload minimum.
It remains binding until a comparable CLOSED Process has SUCCEEDED with complete
attributed samples. Then a new submission may decrease its estimate, retaining
the hard minimum, all applicable measured/OOM bounds and separate device reserve.
An incomplete sample, failed/interrupted trial or low live free memory is not
evidence for lowering it. Include successful measurement IDs and explain any
reduction in `basis`; keep workload labels accurate when model/batch/shapes change.
For retained nonpooled contracts, the Host's fixed-grant rules still apply.

New templates separate a 512 MiB device reserve floor from a 5% reserve of the
final admitted workload budget (rounded up to 64 MiB). Larger Profile/pool floors
still apply; an omitted/zero `gpu_vram_reserve_percent` retains the fixed reserve.
The Host computes this value; do not include it again in `peak_memory_mib` or
remove workload uncertainty headroom. Real OOM raises the next comparable budget
by at least 25%, which also grows proportional reserve. Script errors, protective
pressure cancellation and external occupancy cannot trigger OOM learning.
An incomplete sampled peak is only a lower bound, not a full-run maximum or a
reason to lower a future estimate. Keep same-run FIFO and use wait/handoff;
do not shrink declarations, churn submissions or poll repeatedly to jump ahead.

For ACI27+ pooled runs, preview the exact `resource_estimate` with `get_resources`
(and optional positive `gpus`). The same resolver reports Profile/initial floors,
submitted and measured budgets, OOM bounds, device reserve and capacity shortfall.
The preview is read-only; submit rechecks capacity and captures its own sources.
Uncalibrated comparable history retains 25% measured headroom and requests pool
exclusivity. Complete successful measurements after the last failed/unknown trial
must cover earlier sampled peaks before 15% is allowed; three such successes
within 5% peak variation use 10%. Calibrated margins have a 512 MiB minimum and
64 MiB rounding. Actual OOM growth remains 25% of the old admitted budget.
These are sampling-based policies, not a guarantee against OOM. Describe model,
batch, precision, shapes, data and all execution phases accurately; an altered
workload must not borrow a smaller unrelated peak or merely rename a failed one.
Unknown device reserve/capacity remains unknown, not proof of infeasibility.
`capacity_requires_revision` means revise actual memory demand, not only its
declared number. Already accepted impossible requests close technically and
return through EXECUTION handoff for an ordinary bounded replacement.

Never submit UUIDs, device indices, allowlists, reserve changes or runtime/mount
overrides; never signal or inspect another owner's workload to gain capacity.

In CYCLE_REPORT, preserve workload labels, estimate, actual attributed peak or
unknown/OOM evidence, exact Attempt references and the resulting lesson. During
SELF_EVOLVE only, make a focused strategy update under `/plugin-update`; preserve
this entrypoint and the other wired skills. Do not embed a stale live snapshot
as policy or copy private cross-run research. The Host validates the Git update
for a later cycle; an already bound cycle and current grant never change.


For ACI 28, a Host-observed pre-submit pool outage is infrastructure blocking,
not a reason to downgrade to CPU or fabricate a submitted experiment. End the
Attempt with a concise technical explanation; Host owns failure closure and
operator resource recheck. Existing experiment collection still continues.
For real training, flush bounded stage/trial/seed/epoch and elapsed-time messages
to stdout/stderr. Silent logs leave scientific progress unknown. Prefer bounded
LOCAL screening before full GOAL evaluation when permitted by the admitted
methodology; preserve frozen seeds, parser and acceptance obligations.
