# Bounded engineering optimization

Apply only to the actual implementation and workload. This short reference is
inlined for DESIGN, EXECUTION and ANALYSIS; it needs no duplicate Read. Read
[performance](performance.md) or [PyTorch](pytorch-performance.md) for applicable
details. New algorithms need their own justified invariants, not forced equality
to an unrelated baseline.

1. Trace the actual submitted entrypoint and configuration to its dataset/loader,
model/loss and training loop before spending the full trial budget. Inspect supplied
optimization patches and measured evidence against this captured revision. Adopt
compatible changes in the executed path, run bounded checks, or explain a concrete
reason to defer them in the existing implementation description. Reading a bundle
or adding an unused helper is not adoption. Identify repeated data preparation and
compute. In DESIGN use bounded CPU checks;
resource/GPU work belongs to EXECUTION's Host submission.
2. For fixed features read repeatedly, compare deterministic raw-data caching
under an explicit byte cap. Copy mutable returns, run random transforms on every
access, preserve order and Dataset interfaces. Reopen HDF5 handles per process;
do not pickle live handles. More workers are not automatically faster.
3. Skip zero-weight auxiliary calculations only while preserving required zero
versus None gradients and optimizer behavior. For pairwise losses consider
normalized matrix multiplication, masks and bounded blocks. Preserve self-pair
exclusion, softmax axes, weights, positive means and the all-sample denominator.
Mask unused log inputs before taking logs; avoid N by N by D intermediates.
4. Determine state dependencies before batching history reads. If a write uses
the current read, retain the recurrence. Cache differentiable K/V at write time
within this forward only; do not detach or reuse a previous forward's graph.
5. Check applicable values, input/parameter gradients, optimizer updates, masks,
empty cases, causality and sample order. Then measure the same work after warmup,
including backward where relevant. Synchronize CUDA at timing boundaries. Record
workload, variation and memory limits; local ratios are not whole-training gains.
6. Land one checkable edit, run its bounded check and repair demonstrated errors.
Record which active file/call/config uses the optimization, the before/after workload
and measurement, and remaining unmeasured paths in the existing handoff. If a patch
does not apply, inspect the actual source before adapting it; do not silently skip
it or treat an unmeasured adaptation as the original verified result.
Do not spend the invocation repeatedly rebuilding the same test suite. Submit
captured code through the existing permitted handoff, then release capacity while
the Host verifies it. Do not wait for a long prose report before submitting.
7. Use supplied verified-engineering-memory as conditional evidence: check its
   source digest, workload and uncovered conditions. If a reading_view is advertised,
   start at its index_path and overview; pages preserve the original evidence refs.
   Otherwise read the exact /inputs path. An omission count is a limitation, not
   evidence that no prior checks exist. A historical pass or an author's claimed passed report cannot approve
changed code. Only actual Host observations can support new performance claims.
8. Bound all child joins and queue waits by the remaining check budget. Spawn
tests belong in an importable file with a main guard, not Python stdin. On child
failure report its exit code instead of waiting for a result it cannot produce.

Keep scientific settings, precision, data coverage and evaluation obligations
unchanged unless the existing research revision process authorizes a change.
Check that the actual entrypoint uses the optimized loader/loss. Distinguish
loader-only, forward-only, training-step and full-epoch timings. For model-owned
losses, inspect evaluation-mode returns and the scheduler's monitored metric:
a zero training placeholder is not a measured validation objective.
When measurement is inappropriate or unavailable, state the concrete limitation;
do not invent timings or turn missing performance evidence into scientific failure.
