# Configuration studies

Use the accepted task's sharing, budget and measurement rules. The supplied
`configuration_guidance` is this bound reference; rereading it is unnecessary.
No parameter search is required for tasks without a meaningful configuration
choice. A borrowed baseline setting is a starting point, not evidence of optimality.

## Design and handoff

Separate architecture (layers, widths, heads, fusion/memory structure), training
(learning rate, batch size, regularization, objective coefficients, stopping),
necessary dataset adaptations (label/output cardinality, input dimensions), and
runtime settings (workers, threads, transfers). The task determines which must
be shared. Never label a changed network depth as a dataset adaptation. When a
shared recipe matters, compare that recipe across the required datasets first;
record any later dataset-specific tuning separately. Shared structure across
datasets does not forbid comparing structural variants during development.

Use paper/code/default provenance and the actual data regime to justify the
initial values and a small bounded comparison. Record batch unit, dataset size,
steps per epoch, effective batch after accumulation, scheduler step unit and
training/stopping budget. Batch size can change in-batch objectives: gradient
accumulation need not reproduce the same negatives or batch statistics. Compare
cost and scientific settings explicitly instead of assuming a larger batch or
more workers is better; use the performance guidance for measured worker choices.

Keep one accepted mechanism per hypothesis; parameter variants normally belong
inside its study. Declare candidates, search seeds, validation metric/direction,
dataset weights, trial ceiling including retries, selection rule and final seeds
before execution, in existing admitted source/notes. Use logarithmic ranges when
appropriate, a bounded pilot, then confirm promising choices with matched seeds
and compute. Do not replace required task seeds with this heuristic or add an
arbitrary significance gate. Leave room in the existing deadline for selection,
final evaluation and artifact checks. If settings are inherited without tuning,
say why; do not invent a search or imply they generalize.

## Execution and selection

Have the actual training entrypoint emit resolved configurations after defaults
and dataset overrides, implementation/data references, seed, attempt, validation
measurement, duration when available, and original artifact/log references.
Retain failed and interrupted attempts and unrun candidates. Use existing Host
submission and artifacts, with ordinary source/Git versions for plan and selected
configuration. A revised search uses the existing revised-design/hypothesis path;
do not shrink the original plan after a timeout to claim complete coverage.

For a finite comparison, the bound standard-library helper is available at:

```bash
python3 /expert-plugin/skills/research-workflow/scripts/configuration_study.py \
  --plan plan.json --trials trials.json --selection selected.json
```

The optional selection file contains the chosen candidate ID and its exact
configs. With final observations, add `--evaluation evaluation.json`. The format
and a small example are in [the input reference](configuration-study-format.md).
The helper reads files and prints a report; it does not run trials, choose a new
budget, persist a selection or grant scientific acceptance. Use bounded CPU
file checks only within the current node's permissions. If a frozen measurement
parser needs it, include this script in the ordinary admitted parser source;
the execution sandbox does not automatically mount the expert plugin.

Only complete candidates enter the validation ranking; ties use plan order.
Partial searches yield provisional choices among completed candidates. No
complete candidate means no recommendation. Final observations never influence
the ranking. Keep test data out of tuning, early stopping and recipe selection;
checking correspondence does not license test-driven redesign.

## Analysis and transfer

Report planned/attempted/completed coverage, failures, per-dataset effects and
seed variation alongside any aggregate. An average gain can hide a required
dataset's regression. Preserve the accepted per-goal criteria. A selected recipe
is best only within its stated completed comparison, not globally optimal.
Verify final observations use that exact recipe; incomplete final coverage is
not a complete evaluation. An emitted configuration is evidence to check against
training code and artifacts, not independent proof that the code used it.

To study transfer, select on the declared source validation data, freeze, then
evaluate on held-out datasets or splits without retuning. Tuning jointly on all
datasets demonstrates a shared fitted recipe, not unseen-dataset generalization.
Combine experts' mechanisms only after compatible implementations, common
controls and single/combined ablations; separate gains need not add together.
Retain these lessons through existing reports and owner-local skill evolution.
