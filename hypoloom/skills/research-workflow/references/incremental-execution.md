# Staged experiments and completed-work reuse

Use this for expensive, independently repeatable computations. Keep the actual
task's final acceptance, sharing and budget requirements. A deterministic solver
need not invent seeds; a noisy experiment may need more than one development
repetition. Choose bounded exploration, optional confirmation and frozen final
evaluation in admitted project source. Do not force full final coverage on every
idea. Single-seed screening is permitted only when the task permits it; it is not
formal multi-seed acceptance. Final test observations never select the recipe.

The finite-study helper also accepts `--stages stages.json`. This is an ordered
array of `{name, basis, plan, trials, selection?, evaluation?}` using the existing
study formats. Later stages retain the implementation, data, metric, sharing and
final protocol, can add screening seeds, and narrow to candidates with complete
prior validation observations. Configurations cannot change silently. Use a new
declared study for revised science. A stage after final evaluation is rejected.
This remains a read-only relationship check, not a scheduler or scientific verdict.

## Design and submission

Review `prior_executions` in the supplied expert configuration. State the next
uncertainty and what new observation would resolve it: changed mechanism,
additional repetition/data, operational repair, or deliberate replication. A
completed negative result does not justify an unchanged technical retry.
The submit reply and subsequent result-analysis inputs include a bounded
source/command comparison when prior sources exist. This identifies changed
files; it cannot prove scientific equivalence.

ACI 32 supports `submit_experiment(..., reuse_experiments=[experiment_id, ...])`.
Choose up to 16 terminal published experiments from this same run/component/owner.
The Host verifies their existing publications and mounts them read-only at
`/hypoloom-reuse/N`. It freezes their experiment/outcome/artifact references in
`HYPOLOOM_REUSE_INPUTS`, a JSON array with those IDs and each mounted `path`.
Integrity-rejected outputs and arbitrary paths are not eligible. This permits
inspection of partial technical results; it does not make them scientific facts.
Use the ordinary `replaces_experiment_id` only for an eligible technical repair.

The bound standalone helper is in
`/expert-plugin/skills/research-workflow/scripts/work_units.py`. Include it as
ordinary submitted source (e.g. `/input/work_units.py`) or include its source in
the admitted Python entrypoint where the older workspace shape permits only one
script. It is not automatically mounted from the authoring plugin into a Process.

```python
from work_units import WorkUnits

work = WorkUnits("/output")  # reads Host-bound HYPOLOOM_REUSE_INPUTS
completed = work.run(
    "candidate-small-repeat0",
    argv=["/runtime/bin/python", "/input/train.py", "--out", "model.pt",
          "--metrics", "validation.json", "--seed", "0"],
    context={
        "implementation": "the admitted implementation reference",
        "data": {"split": "the actual immutable split reference"},
        "configuration": {"epochs": 20, "batch_size": 32},
        "repetition": {"seed": 0},
        "protocol": "train-and-validation-only-v1",
    },
    outputs=["model.pt", "validation.json"],
    timeout_seconds=600, stage="screen", purpose="exploration",
    basis="Prespecified paired comparison before expanding repetitions",
)
checkpoint = completed["files"]["model.pt"]
# Build and validate the task's actual result from these files.
```

The numbers above are examples, not defaults. Use the actual interpreter,
entrypoint, full resolved settings, input/source references, random conditions
and protocol. `repetition` can be null for deterministic work. The helper checks
declared equality, not hidden dependencies or the truth of self-reported settings.
Expose dependencies in the context and use project tests/evaluators to check them.
Children run in their own writable output directory and inherit the granted
environment; use absolute source/input paths. Preserve accepted scientific data
order and budget. Outputs must be explicit regular files relative to that child.

The helper reuses only a completed record with the same ID, argv, context and
declared outputs, and with every declared output present. It copies those outputs
into this Process's unit directory and retains the original publication reference.
Matching within the current Process is also reused. Changed work needs a new ID
within one Process; across Processes an incompatible context causes execution.
Use `replication_reason="..."` for intentional fresh replication. Do not count a
reused checkpoint as a new independent sample.

## Failure and partial results

Completed siblings remain under `/output/work_units/<id>/`; the Host retains
raw output through failure and publishes it using the existing artifact path.
A killed/running/failed unit, missing output or incomplete completion record
cannot be reused. A replacement explicitly imports the prior experiment and runs
the same plan: compatible complete units are copied; the rest execute. Existing
Process timeout and repair budgets remain binding. The helper never automatically
retries or extends them. A new invocation of the entire Process is required after
technical failure; do not try to mutate an old published directory.

Raw work-unit completion is technical evidence. The parent must validate actual
checkpoint loading, measurement coverage and LOCAL/GOAL output. Parser/publication
failures use their existing recovery stage; retained computation can be used in
a repair when its context still matches. A scientific negative is a completed
measurement, not a failed child. Mid-training resume requires the project's own
optimizer/scheduler/RNG/data-position handling; weights alone are insufficient.

## Performance probes

Use a small unit with `purpose="performance"` through the actual admitted
entrypoint when current workload costs are uncertain. It requires a nonempty
`context.runtime` recording relevant software, device and workload conditions.
Keep its own bounded command/outputs and reuse applicable measured results.
Run scientific work in separate children so probe RNG/model state is not carried
into training. Keep data inputs immutable; output measurements to the unit's own
directory. A warmup/measurement mode still needs project-specific implementation.

The helper's elapsed time measures the child (or current copying cost), not GPU
kernel time or scientific performance. Retain representative phase measurements
in declared outputs. Check the same objective, outputs, gradients and artifact
path before accepting an implementation optimization, then measure ordinary
end-to-end execution with profiling disabled. No probe or microbenchmark alone
establishes a GPU speedup. Ordinary short work needs no repeated measurement.
