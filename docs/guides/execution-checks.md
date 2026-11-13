# Execution checks

English · [简体中文](execution-checks.zh-CN.md)

Use `goal.yaml.execution_checks` when an engineering requirement must hold before
an expensive experiment runs. The task supplies a small checker independently of
candidate code. Every execution, including LOCAL experiments and repairs, runs it
inside the existing resource grant before starting the workload. Failure becomes
`EXECUTION_FAILURE`, with no scientific metric or refutation.

| Field | Accepted value |
| --- | --- |
| `requirements` | 1–64 names mapped to nonempty original requirement descriptions. Names match `[a-z][a-z0-9_]{0,63}`; descriptions total at most 16 KiB. |
| `argv` | A list starting with `python` and a declared `.py` filename, followed by optional arguments; at most 8 KiB. |
| `files` | 1–64 relative filenames mapped to UTF-8 source text, at most 512 KiB total. Paths cannot overlap; `context.json` is reserved. |
| `timeout_seconds` | A finite positive timeout, limited further by the experiment's remaining outer deadline. |

Checker sources are captured in the admitted task. Experiment patches, commands
and measurement parsers cannot replace them. Omit this optional field to retain
the existing execution behavior. An already created run keeps its original
declaration and frozen runtime; editing its Profile does not upgrade it.

The checker runs with the workload's pinned Python runtime, read-only candidate
files at `/input`, patched target, read roots and granted devices. It inherits
the same environment, including `HYPOLOOM_TARGET_ROOT` when a target is present.
The read-only `/hypoloom-checks/context.json` contains `argv` (the submitted
logical workload command), `input_dir`, `output_dir` and `requirements`. The
checker has its own writable `/output` and private `/tmp`; its current directory
is `/output`. The actual training process starts separately, so a probe does not
consume training RNG or sampler state.

On success, exit zero and write exactly one output file, `result.json`:

```json
{"checks":{"actual_loss_dispatch":true,"loss_gradient_equivalence":true}}
```

The keys must exactly match `requirements`, and every value must be Boolean
`true`. False, omitted or extra keys, duplicate JSON keys, non-Boolean values,
nonzero exit, timeout, additional output files or a result over 64 KiB prevent
workload launch. Use stdout/stderr for diagnostics and `/tmp` for scratch files.
Published evidence includes admitted source/context, bounded checker logs and
the available result under `raw/execution-checks/`. Driver diagnostics appear in
the ordinary Process stderr. The existing finite repair budget applies.

For vectorization, the project checker should construct the actual model selected
by `context.json.argv` and exercise its real loss and backward path. Cover dataset,
arm, precision, subclass and instance overrides that affect dispatch. Compare
losses and full parameter gradients with the reference on masked and numerical
edge cases. A function merely existing, a `vectorized=true` flag, or a check on a
different baseline model does not establish compliance. If dispatch can change
after construction, retain an assertion in the common training entrypoint too.

Keep probes bounded; measure full throughput separately. Worker counts, precision
and other runtime settings remain tunable unless the scientific protocol freezes
them. These prerequisites do not change scientific success thresholds. The
framework enforces the supplied checker; it cannot prove that arbitrary prose
was translated into a complete checker. Review that coverage when authoring a task.
