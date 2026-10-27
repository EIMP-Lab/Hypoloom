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
Exercise meaningful behavior, using bounded negative or boundary cases where
relevant to show the check detects the original fault. Scratch-only success does
not validate submitted code. Use the resource-planning and repair skills for their
scoped duties; local exploration does not grant direct GPU experiment authority.

For a declared local measurement, follow the runtime's parser and smoke contract.
The Host owns generic output validation and isolated preflight; keep your tests
focused on task semantics and actual changes. A GOAL-only measurement=null does
not need a new local parser. Record unresolved problems honestly in the existing
output/notes instead of adding alternate validators or lowering the criterion.

Split authoring checks from device execution. In DESIGN, use short, explicitly
bounded CPU checks for parser semantics, paths, configuration and changed code.
Do not treat CPU training as cheap merely because the input has few examples:
long sequences, model setup and audits may dominate. Inspect the remaining Agent
deadline before a check, leave time for final JSON and Stop corrections, and stop
an overlong local check instead of repeatedly sleeping/polling. Retain drafts and
tests under /workspace so a failed invocation can reuse them.

For a GPU workload, implement its bounded device smoke at the beginning of the
actual experiment entrypoint. EXECUTION submits that entrypoint through Host; its
Process owns the GPU grant, timeout, logs and cleanup. Exercise the changed arm
first, using a tiny forward/backward/save/reload/evaluate path on the granted
device, and fail before expensive expansion when it fails. CPU-only checks do
not establish CUDA compatibility. Record this planned but unrun device check in
the DESIGN handoff; do not wait for it inside DESIGN or claim it already passed.
Preserve accepted full trial counts and metrics after smoke, separate temporary
smoke outputs, and do not turn smoke data into scientific outcomes.

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

Keep submitted code and reference data distinct. Use HYPOLOOM_TARGET_ROOT
directly for target imports; never translate it with HYPOLOOM_READ_ROOT_MAP.
The latter resolves reference data and can select the unpatched checkout.
Inspect the changed module's __file__ under the submitted root, and propagate
the same import path to child processes. Check new modules and registrations,
not only imports already present in the baseline.

For parser tests, assert hand-computed values for small positive and negative
fixtures. Missing or malformed evidence must raise a clear error, not return a
finite sentinel such as -1. A real negative measurement remains valid. Resolve
raw inputs from HYPOLOOM_MEASUREMENT_RAW_ROOT; parser cwd is /parsed, not /raw.
If a frozen parser needs correction, propose the existing revised hypothesis
or new cycle; do not repeatedly rerun training under a known broken parser.

Deliver the smallest runnable change first. Reuse the retained entrypoint and
tests, then spend remaining time on the changed mechanism. Budget implementation,
checks and final submission together; a long retry must inspect retained work
before repeating it. Do not extend the Agent deadline or duplicate slow checks.
