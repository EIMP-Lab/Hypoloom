# Progress, timing and performance

Use the current task's actual entrypoint, implementation and budget. Work units
may be batches, files, simulation intervals or evaluations; do not impose epochs
or a GPU on unrelated tasks. A framework or tool name alone does not identify a
bottleneck. For PyTorch compute, synchronization or data-loading issues, read
[PyTorch](pytorch-performance.md). Use the implementation's own tools for other
frameworks, numerical solvers and data pipelines.

## Measure the current design

Inspect existing logs first. Identify repeated phases, known work counts and
variable work. If needed, add small, flushed messages to the task's own code for
phase, completed/total units where known, monotonic elapsed time and recent rate.
Use existing stdout/stderr and working notes, not a second telemetry service,
mandatory raw-evidence layout or framework source edit. Avoid per-item log floods.

Separate preparation, compilation/warmup, steady work, evaluation, saving/reload
and final artifact checks. Measure relevant variants through the actual selected
entrypoint. A mock output only checks an interface; before costly expansion use
the admitted small end-to-end check to exercise the actual LOCAL/GOAL output layout
and parsers, preserving all accepted measurement and shared-goal obligations.

Use a current-design pilot for the initial full-experiment timeout when needed.
Only EXECUTION can submit resource work through Host; DESIGN may describe a probe
and use bounded CPU checks, but cannot launch GPU work directly. For GPU model
checks, place a bounded smoke phase at the start of the actual Host-submitted
Process on its granted device; do not emulate an expensive GPU workload on CPU
inside DESIGN. A few examples need not be fast. Stop overlong authoring checks,
retain the draft and pending checks, and leave time for Stop correction before
the invocation deadline. Do not increase frozen budgets or reduce trial coverage.
Reuse a still-applicable completed pilot rather than repeat it. Estimate the sum
of remaining phase work, include evaluation/I/O and explained uncertainty, and
keep the total run budget binding. Cross-run runtime averages are not a required
input.

During execution, ordinary task code may calculate rolling estimates from its
own progress. For stable units, remaining count times recent representative unit
duration is useful; keep an interval when rates vary. Exclude warmup from steady
rates and separate different data sizes/arms. Do not extrapolate one short/simple
batch to longer inputs without evidence. Reset estimates across phase changes or
resumption without double-counting completed work. Log buffering is not inactivity.
Adaptive search, early stopping or unknown convergence may allow only phase/rate
and budget-consumed reporting: remaining time stays unknown. Never manufacture a
precise percentage or silently extend a submitted timeout.

After submission use the existing Host handoff. Do not keep an Agent polling
logs or add a monitoring Agent. Read returned evidence at the normal pilot,
analysis or repair boundary; human-facing live observation remains read-only.

## Explain and test a bottleneck

Separate resource queue time from execution time before tuning code. A waiting
request has no training throughput; FIFO, grants and sharing remain Host-owned.
Source inspection suggests candidates, not measured causes. When existing timings
cannot locate the cost, plan a short representative profile within the admitted
Process, distinguishing data wait, host work, copies, compute and synchronization.
Profile only the uncertain phase; do not attach to or rewrite an already-running
experiment. Low GPU utilization alone does not require an optimization pass.

Align phase throughput and data-wait/copy/compute time with available CPU, I/O,
memory and device samples. Existing GPU diagnostics may include utilization,
power draw/limit, temperature and SM clock; missing fields are unknown. Board
power and utilization describe the whole device, especially on shared cards.
They cannot establish this Process's efficiency or another owner's behavior.
High utilization is not necessarily high throughput; low power alone is not a
stall. Never change device power/clock settings or signal other workloads.

Choose one evidenced bottleneck and the smallest relevant implementation change:
batch independent operations, remove repeated work or unnecessary synchronization,
or overlap preparation with compute where semantics permit. For data loading,
compare a few worker/prefetch settings within granted CPU, memory and shared
memory limits; for a solver, inspect its own repeated expensive work instead.
Preserve genuine dependencies and the accepted mathematical objective.

Reuse the task's tests to compare the original and changed implementations on
matched inputs and starting state. Check relevant outputs, masks, reductions,
gradients or solver updates, and the actual artifact/evaluation path. Include
boundary cases exposed by the change. Use justified numerical tolerances; an
algebraic identity does not imply identical floating-point behavior. Never weaken
an existing check or detach part of a computation to make the comparison pass.

Measure before/after with matched workload, software, device, data order and
scientific settings, noting shared-machine contention. Repeat a bounded sample
to expose variation; time normal execution with profiling disabled. Compare
end-to-end work as well as the changed phase, and report peak memory and any extra
CPU/I/O demand. Separate startup/compilation from steady rates and include their
cost over the planned trial length. A faster isolated operation is not yet a
faster experiment. Prefer elapsed time/throughput with preserved correctness;
maximum power or utilization is not the objective. Account for probe cost, stop
within the existing budget, and retain the original when benefit is unproven or
the change cannot fit the granted resources. No-change is a valid result.

Batch size, precision, data order, stopping and evaluation coverage can change
scientific meaning: preserve the accepted protocol and use the existing revised
hypothesis path when a scientific change is needed. Never tune against held-out
test outcomes.

## Report and evolve within this run

In existing notes/CYCLE_REPORT, retain the workload conditions, original log or
Attempt references, predicted versus observed phase time, the demonstrated
bottleneck, correctness comparison, measured speed/memory tradeoff and unresolved
limitations. Keep suspected causes and unmeasured gains explicitly provisional.
Completed/failed work can explain an engineering problem without becoming
accepted scientific evidence.
Do not turn an unmeasured suggestion into a successful optimization.

At SELF_EVOLVE or GRAPH_EVOLVE, check the premise against the source and improve
this owner's method/reference only when evidence warrants it. Prefer better
measurement/decision rules; numerical settings need explicit applicability.
No-change is valid. Use existing plugin Git/version selection and report paths;
do not create a cross-run timing database or import private lessons into public
seeds. Later measurements test the new advice; structural acceptance alone does
not establish improvement or change already-bound requests.
