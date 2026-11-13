# Execution requirement checks

Status: implemented and qualified, 2026-11-14. The user requested design,
implementation and verification after the retained training-efficiency diagnosis.
See the [qualification record](../qualification.md#execution-requirement-checks-2026-11-14).

Explicit engineering requirements must be checked against the submitted workload
before expensive execution. Scientific measurements and success thresholds remain
separate. The Host supplies the admitted checker, runs it inside the experiment's
existing resource grant and sandbox, and starts the workload only after its exact
required checks pass.

## Failure and scope

In the retained November 11 MERC run, all 347 inspected design, execution and
analysis calls received performance guidance. An early design call also read the
specific instruction to integrate equivalent SWFC vectorization and inspect the
actual callable. Nevertheless, the available source snapshots retained the
per-anchor base implementation. Later notes treated inherited runtime settings as
part of a frozen recipe. Submitted scientific parsers checked F1, structure and
causality, without checking the loss implementation.

The failed boundary is between an explicitly required implementation property and
the decision to start the submitted workload. Git and artifact binding establish
which bytes run; they cannot establish what those bytes do. Type checks establish
the shape of declared criteria, not coverage of natural-language instructions.
Existing performance guidance and work-unit probes are optional methods. Their
successful delivery cannot authorize a claim that the requirement was satisfied.

Evidence remains in the original ignored analysis directory:
`/data/Gray/Project/Hypoloom/.local/analysis/training-efficiency-20261113/`.
The source, trace and attribution reports are `source_audit.json`,
`vectorization_trace_index.json` and `vectorization_root_cause.json`.

## Smallest additional mechanism

Add an optional task-owned `goal.execution_checks` declaration to Profile v4.
It contains the original engineering requirements, executable checker sources,
an explicit command and a bounded timeout. Freeze this declaration in the
existing GoalDefined product controls. Experiment Agents can read it but cannot
replace it through their candidate, parser, patch or submission arguments.

The extra execution condition is necessary because the existing validators never
run a task-specific implementation check before the workload. Reuse the existing
Process intent, source materialization, resource grant, LocalRunner, publication,
technical-failure recovery and scientific parser sequence. Do not introduce a
new workflow, approval round, database table, digest or independent verdict.
Maintenance is one declaration validator and one optional phase in the existing
execution adapter. Remove that phase if the ordinary task execution driver later
provides the same mandatory prerequisite behavior and source binding.

The initial interface deliberately requires an explicit task declaration. The
framework does not infer complete executable obligations from arbitrary prose.
An omitted declaration retains the old behavior and must never be reported as
verified compliance. Known project requirements should be supplied with the task;
model-authored checkers still need project review and meaningful negative cases.

## Declaration and authority

The declaration uses these fields:

| Field | Meaning |
| --- | --- |
| `requirements` | Nonempty mapping of stable check names to the original task requirements |
| `argv` | Explicit Python checker command referencing a declared source file |
| `files` | UTF-8 checker and support sources, stored with the admitted declaration |
| `timeout_seconds` | Positive bounded phase timeout, also limited by the outer Process deadline |

Sources are task configuration, independently fixed before experiment candidates.
No source keyword, function name or Agent-provided `vectorized=true` is itself
proof of implementation. The checker must exercise the relevant submitted code.
MERC-specific numerical logic belongs in a project checker, not in the generic
Host. Existing Git and artifact identities remain the source authority.

Design, execution and analysis receive a compact projection of the exact required
checks and their source. The projection distinguishes runtime configuration from
the scientific conditions that the task actually freezes. Full source remains
available through the existing immutable input mechanism.

## Execution

The ordinary sequence becomes:

1. Materialize the submitted target and the independently admitted checker.
2. Run the checker in a separate child using the experiment's pinned interpreter,
   candidate input files, patched target, read roots and granted device visibility.
3. Require a successful exit and a bounded regular JSON result containing exactly
   the declared check names with Boolean values. Every required value must be true.
4. Start the original workload in a fresh child, then run its original LOCAL and
   optional GOAL parsers.
5. Preserve checker source, result and logs with the ordinary published artifacts,
   including when checking or later execution fails.

The checker receives the actual workload command and target environment through
a Host-created read-only context file. It has a separate writable output
directory. Checking does not consume the training child's sampler, random state,
optimizer or model instance. The candidate cannot overwrite the admitted checker
or its output through its own writable output directory.

The checker may inspect and invoke the candidate's actual model factory or a
bounded entrypoint mode. A project adapter must cover the actual dataset, arm,
precision and loss variants that can change the selected implementation. A check
against an unrelated baseline model is insufficient. For properties that can
change after construction, the project's common training entrypoint must retain
a lightweight assertion on the real training instance.

## Failure and reuse

Missing checks, false values, malformed or oversized output, a failed exit and a
timeout all prevent workload launch. Diagnose the failed requirement through the
existing Process/publication and repair path. Do not produce a metric sentinel,
scientific refutation or fabricated successful training result. Stop and outer
timeout continue to terminate the entire owned process tree.

The first implementation runs the bounded prerequisite on each submitted
execution. This avoids an unverified global cache. Expensive compatible numerical
measurements may be reused within the project using existing completed-work and
publication inputs; a cheap actual-binding check still runs. Reuse must include
actual source, runtime, workload and configuration dependencies. A reused result
is not an independent scientific sample.

## MERC application

A project checker for the observed failure should exercise the actual loss used
by the candidate and matched baseline, including subclass and instance overrides.
It checks loss and full parameter-gradient equivalence to the reference on
representative masked batches and numerical edge cases. It records implementation
origin and effective loader/precision settings. A bounded training comparison can
measure improvement; it is separate from the prerequisite's correctness result.

Required negative cases include the old per-anchor implementation, a vectorized
function that exists but is never called, a subclass that restores the old
method, a changed loss formula and a stale result from another configuration.
The default scientific F1 thresholds and causal boundaries remain unchanged.
No universal worker count, model family, speedup threshold or GPU requirement is
introduced into the framework.

## Compatibility and verification

Declarations without execution checks retain their serialized controls and
execution behavior. Retained research roots and their bundles remain unchanged;
there is no cross-version migration. New runs capture new checker declarations
through normal admission. Generic and shared-goal execution must use the same
planner; replay and Oracle validation reconstruct the exact optional phase.

Verification must cover:

- Actual CPU subprocesses: old implementation and overridden methods fail before
  a workload marker is written; a compliant implementation executes normally.
- Malformed declarations and result objects, missing/extra check names, false or
  non-Boolean values, duplicate JSON keys, unsafe paths and source drift.
- Checker failure, timeout, outer cancellation, descendant cleanup, successful
  checking followed by workload/parser failure and retained diagnostic outputs.
- LOCAL and GOAL paths, scientific negative results, repair and unchanged legacy
  requests/publications. Failure is an engineering failure, not a scientific one.
- Exact prompt delivery and source binding; a candidate cannot downgrade or omit
  the admitted requirements. Normal source/workload changes re-run the prerequisite.
- Existing qualification lanes through actual Host submission, execution,
  collection, publication, recovery and next action, with independent ownership
  and residue accounting. Real-model compliance and GPU speedups require their
  own measured evidence and are not inferred from framework test passes.

## Implementation

`execution_checks.py` owns the one declaration validator, frozen-input prompt
projection, sandbox phase preparation and bounded evidence collection. Profile v4
captures it in the existing product controls. `measurement_driver.py` executes
the prerequisite before raw work, with exit 74 indicating a failed prerequisite.
`shared_acceptance_execution.py` handles checked static, local and shared-goal
sequences. The Host uses the same plan for launch and recovery, rechecks admitted
source and successful check output before publication acceptance, and records
`execution_requirements_failed` through the existing technical repair path.

Static scientific envelopes retain their exact logical artifact inventory. Only
the finite Host evidence paths derived from the admitted checker are exempt from
that inventory; undeclared files still fail, and check evidence cannot be claimed
as a scientific deliverable. The independent Oracle reconstructs sandbox mounts,
command, ownership, timeout, source/context and successful result evidence from
GoalDefined and the recorded Process. No checker-result cache is introduced.

The core authority budget remains 27,550 lines: 290 unused common-acceptance
lines transfer to the counted prerequisite helper (250) and Host (40). User
configuration and limits are documented in [the guide](../guides/execution-checks.md)
and its [Chinese version](../guides/execution-checks.zh-CN.md).
