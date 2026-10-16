# PyTorch computation and data loading

Use when the actual implementation uses PyTorch and evidence indicates a
throughput problem. Follow [Progress and performance](performance.md) for budget,
Host submission, scientific scope and comparison rules. Inspect the installed
API and submitted code; options below are candidates, not a tuning checklist.

## Locate the cost

Measure representative end-to-end steps after warmup, including data wait and
transfers. GPU operations can be asynchronous: CPU launch time alone is not GPU
execution time. Use appropriate events or synchronize at bounded measurement
boundaries; synchronizing every step can itself distort production throughput.

If phase timings are insufficient, use a short `torch.profiler` window in the
authorized experiment. Inspect CPU/CUDA time, operation counts and allocations
for forward, loss, backward and update separately. Enable shapes or stacks only
when needed; profiler overhead and first-use work can skew results. Recheck the
candidate with normal execution, not just a profiled microbenchmark.

## Improve the measured computation

For many small operations launched from Python, inspect per-sample, per-pair and
per-time-step loops. Batch independent operations or use bounded blocks when a
full pairwise tensor would exceed memory. Preserve recurrent state and causal
dependencies; a sequential model cannot be replaced with an independent map.
For contrastive or pairwise losses, preserve masks, self-pair exclusion, weights,
normalization axes, reduction denominators and empty-positive behavior. A faster
surrogate objective is a scientific change, not an equivalent implementation.

Check whether a reduction computes large intermediates that its final output
does not need. An algebraic simplification must preserve the actual implemented
formula, including gradient paths and numerical stability. Do not silently
"correct" the objective to match a different mathematical convention.

When host/device synchronization dominates, inspect scalar extraction (`item`,
`float`), CPU/NumPy conversions, GPU-dependent Python branches and repeated
dynamic indexing in the hot loop. Retain required decisions and logging, but
batch or defer diagnostic transfers where valid. Keep accumulators bounded and
detach diagnostic values only; retain autograd connections used by the loss.

Consider compilation/fusion only for an evidenced launch or operator overhead
and supported installed runtime. Check graph breaks, varying shapes, extra memory
and compile/recompile time against the planned workload. A setting that helps
steady batches may lose on short or variable trials; preserve the eager baseline.
Do not install or replace a shared runtime to enable an optimization.

## Data preparation and transfers

For DataLoader, compare a small range of `num_workers` permitted by CPU, RAM,
shared memory and concurrent workload limits. Zero workers can be appropriate
for small in-memory data. More workers are not automatically better. Consider
`persistent_workers`/`prefetch_factor` only when workers are enabled and their
lifetime/memory cost is justified. Check worker seeding and data order when
comparing results. Check reader-handle isolation and compatibility with the
runtime's multiprocessing start method before adding workers. Do not hardcode
one best configuration for every workload.

If host-to-device copies dominate, inspect `pin_memory` and transfer behavior
before testing pinned memory or `non_blocking` transfers. Verify correctness,
memory use and actual overlap/throughput. These settings do not fix expensive
model compute, frequent synchronization or disk bottlenecks by themselves.

## Check equivalence and useful speed

Compare original and candidate on the same inputs, parameters, optimizer state
and relevant random state. For a loss/compute rewrite, check forward values,
parameter/input gradients where applicable and an optimizer step, using justified
tolerances. Cover relevant masks, variable lengths, singleton or empty groups,
and the actual training/evaluation modes. A finite loss or successful backward
alone does not establish equivalence. Reuse the task's small end-to-end check for
saved artifacts and the evaluator; CPU checks do not establish CUDA equivalence.

Then compare bounded normal-run step/phase time and peak memory at representative
shapes, including backward. Report startup costs and variation using the common
performance guidance. Preserve required deterministic behavior, batch size,
precision, data order, loss, optimizer, stopping and evaluation coverage; changes
to scientific settings follow the existing revised-hypothesis route. Do not use
AMP, lower precision or fewer trials merely to raise utilization.

References: [PyTorch tuning guide](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide.html),
[profiler](https://docs.pytorch.org/tutorials/recipes/recipes/profiler_recipe.html),
and [pinned/nonblocking transfers](https://docs.pytorch.org/tutorials/intermediate/pinmem_nonblock.html).
