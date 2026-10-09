# Implementation

For costly or timing-sensitive computation, use [Progress and performance](performance.md)
to make the current implementation observable and plan any necessary small probe.

Read the accepted hypothesis, supplied interface declarations, actual evaluator
source and relevant existing implementation. Reuse components whose assumptions
match; investigate remaining integration gaps. Accepted shared acceptance is
read-only. Preserve the exact measurement already declared by the hypothesis.

For later iterations, begin with the retained implementation and accepted analysis.
Identify the scientific change, edit that path and reuse still-applicable parser,
data-loading and integration work. Do not rebuild a working harness or repeat a
literature review without an unresolved question. In existing implementation
notes, identify the entrypoint, selected variants, checks already run and exact
remaining execution work so EXECUTION can submit without rediscovering the design.

Reuse a baseline or completed trial only when its source, data/splits, seed,
training/evaluation protocol and required artifacts match the current comparison
and existing submission rules permit access. Otherwise recompute it. Never merge
partial trials into a complete claim or reuse a checkpoint as an independent run.
Use the selected configuration to report the planned trial count and bounded
phase/trial timing in ordinary logs. For unexpectedly low GPU utilization, measure
data preparation, training, evaluation and artifact checks separately before
changing batch sizes or parallelism; preserve scientific settings and Host grants.

Change actual target files and test those changes in the selected environment.
Exercise meaningful scientific and integration behavior, including invalid or
boundary inputs where relevant. Scratch-only success does not validate the
submitted implementation. Use the resource-planning and repair skills for their
scoped duties; local exploration does not grant direct GPU experiment authority.

For a declared local measurement, follow the runtime's parser and smoke contract.
The Host owns generic output validation and isolated preflight; keep your tests
focused on task semantics and actual changes. A GOAL-only measurement=null does
not need a new local parser. Record unresolved problems honestly in the existing
output/notes instead of adding alternate validators or lowering the criterion.

Before an expensive experiment, exercise a small meaningful instance through the
actual changed implementation, selected environment and evaluator: the applicable
update/solve path, saved artifact, reload, evaluation and metric parser. For a
training task this can be a tiny train/save/load/evaluate cycle; a solver or data
pipeline should use its own equivalent. Include relevant device placement,
shapes, units and boundary cases. Mock result JSON alone only checks a parser.
Invoke the actual experiment entrypoint with every selected arm or variant,
including new registrations, import/dispatch branches and data/cache lineage.
Prioritize the changed candidate path before spending a long baseline run. A
successful existing arm does not establish that a newly selected arm can start.
Use existing authorized CPU checks or Host-submitted resource work for GPU needs;
this guidance never authorizes direct GPU launch or a competing scheduler.

Reuse a still-applicable successful check when code, evaluator and environment
are unchanged. After a failure, exercise the failing path and its correction
before committing the remaining full-experiment budget. Record commands, observed
results and unresolved coverage in existing implementation notes. The task owns
these checks; do not add a universal framework-specific ML validator or a second
acceptance gate, and do not call a smoke result scientific superiority.
