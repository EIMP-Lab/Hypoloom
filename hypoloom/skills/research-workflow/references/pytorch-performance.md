# PyTorch data-loading observations

Use only when the actual implementation uses PyTorch and measurements suggest
data-loading or transfer overhead. Inspect the installed API and current code.
Do not force these options onto another framework or a task without training.

Measure representative end-to-end steps after warmup, including data wait and
transfers. GPU operations can be asynchronous: CPU launch time alone is not GPU
execution time. Use appropriate events or synchronize at bounded measurement
boundaries; synchronizing every step can itself distort production throughput.

For DataLoader, compare a small range of `num_workers` permitted by CPU, RAM,
shared memory and concurrent workload limits. Zero workers can be appropriate
for small in-memory data. More workers are not automatically better. Consider
`persistent_workers`/`prefetch_factor` only when workers are enabled and their
lifetime/memory cost is justified. Check worker seeding and data order when
comparing results. Do not hardcode one best configuration for every workload.

If host-to-device copies dominate, inspect `pin_memory` and transfer behavior
before testing pinned memory or `non_blocking` transfers. Verify correctness,
memory use and actual overlap/throughput. These settings do not fix expensive
model compute, frequent synchronization or disk bottlenecks by themselves.
Batch size, mixed precision and compilation need their own protocol/correctness
assessment; do not silently add them as operational repairs.

References: [PyTorch tuning guide](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide)
and [pinned/nonblocking transfers](https://docs.pytorch.org/tutorials/intermediate/pinmem_nonblock.html).
