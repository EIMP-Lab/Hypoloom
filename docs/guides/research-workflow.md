# Research workflow and evolving methods

English · [简体中文](research-workflow.zh-CN.md)

New runs seed `research-workflow` into the Grounding manager's plugin and each
expert's plugin from one public source. The framework loads it using the actual
bound plugin name and the current stage. It is a research method, not a new
scientific evaluator or additional permission system. No Profile changes are
required.

## During a run

| Stage | Method |
|---|---|
| Grounding | Reuse supplied context and retained drafts, investigate necessary gaps and test the commissioned evaluator. Background-only tasks do not invent one. |
| Literature and hypotheses | Resolve unanswered questions using original sources and propose discriminating experiments. |
| Implementation | Reuse applicable code, preserve accepted interfaces and test actual changes. |
| Execution and repair | Act on current Host state and original errors through existing submission and handoff tools. |
| Analysis and reports | Check raw evidence, comparison scope and uncertainty before making conclusions. |
| Evolution | Improve a specific method from observed evidence, or leave it unchanged. |

The short entry points to stage references, loaded only when relevant. Existing
literature, resource, repair, reporting and interaction skills retain their
specialist responsibilities. QUERY_PLAN and bounded paper readers keep their
focused instructions. Auxiliary roles without a plugin binding likewise keep
their existing instructions; the runtime never invents a binding or grants them
plugin access simply to load guidance.

Initial Grounding reads the shared plugin while retaining its selected execution
role, model, effort and budget. It cannot write `/plugin-update`. Research nodes
continue using their existing expert resources even when graph evolution is
disabled. Cycle-report resource availability is a runtime-version condition, not
a new user switch. Existing runs and retained Git plugins are not backfilled.

## Task-specific science

The skill contains no dataset names, project directories, seed counts or fixed
performance thresholds. Classification, numerical optimization and reproduction
tasks retain their own metrics, checks and parser code. Reuse an implementation
only after checking its assumptions against the current goal.

The native Stop hook already validates final JSON and performs isolated parser
smoke tests. Agents should use the resulting feedback instead of writing another
generic schema validator. `/runtime/output-schema.json` exposes the identical
schema directly. Scientific tests still cover raw calculations, task-specific
boundaries, missing evidence and every mandatory validity condition. For each
validity mutation, assert its individual check changes and unrelated checks stay
as expected; a low overall score alone is insufficient. The Host already tests
strict numeric equality, comparison operators and ALL/ANY: do not construct
complex raw samples just to repeat those framework comparisons.
A passing smoke test proves that the parser interface works, not goal success.
Accepted shared evaluators remain read-only after Grounding.

After retries, clarification or context recovery, use the current request,
accepted sources and retained work. Optional `/acceptance/WORK_NOTES.md` is draft
material, not scientific evidence or a declared parser source. Keep reusable
fixture generators, test helpers and candidate drafts under `/acceptance/work/`;
`/tmp` disappears between invocations. Recheck current inputs before reuse and
omit helpers/notes/drafts from `parser_files`. Once scientific checks and smoke
inputs are ready, submit the complete final JSON; writing it to a file alone is
not submission. Grounding tool calls can receive an advisory during the last
20% of the remaining invocation budget, capped at five minutes, to leave time
for Stop corrections. It cannot interrupt a long model response or extend the
deadline. This skill does not change context limits or add paid summarization.

When evidence contains both details and an aggregate, independently recompute
using the goal's population and weights. If both representations are required,
test their agreement: reported 0.70 with all contributing observations 0.10 must
not qualify. Mutate each side separately and keep consistent positive controls;
assert the relevant check or recomputed measurement changes. Field presence and
positive fixtures alone are insufficient. Keep executable tests in
`/acceptance/work/` and actual commands/relationships in existing work notes.
This is an evolvable Grounding method, not a universal metric format or a promise
that structural smoke validation proves scientific correctness.

## Incomplete component comparisons

For Profile-owned component-result protocols, an optional condition can distinguish
incomplete scientific coverage from a valid negative comparison. Declare it under
`methodology.yaml.experiments.protocols.<name>.requirements`, for example:

```yaml
inconclusive_if:
  artifact: evaluation_summary
  path: coverage.full_matrix_complete
  equals: false
```

The component selects that protocol through `experiment_kind`. The artifact must
already belong to its required artifact inventory and resolve to one JSON file;
the dotted field must exist. Matching uses the scalar's type and value. Normal
execution, identity, artifact and trial checks still apply first: this condition
does not turn malformed output or a failed Process into valid science. It applies
to component-result evaluation, not deferred or shared measurement parsers.

A match produces INCONCLUSIVE. The accepted outcome carries no comparable metric
or logical artifact claims; published raw output remains available for analysis.
Analysis can finish with COMPLETE. Same-chain ITERATE still requires SUCCESS or
CLEAN_REFUTATION; an invalid choice uses the existing correction/retry allowance.
This change does not automatically rerun missing comparisons. Existing runs keep
their frozen runtime and admitted Profile.

## Before costly experiments

DESIGN uses bounded CPU checks for parsing, configuration, paths and small code
cases. It has no GPU grant. For a GPU model, author a small device smoke phase at
the beginning of the actual experiment entrypoint, then let EXECUTION submit it
through Host. Test the changed arm on the granted device before expensive work;
fail normally when the smoke fails. Preserve full trial/metric obligations and
keep smoke outputs separate from scientific measurements. Do not spend DESIGN's
deadline on CPU training or repeated polling. Its existing tool hook now reminds
it near the deadline to retain drafts/tests and leave time for final correction.

Use a small meaningful case through the actual implementation and evaluator,
including saved artifacts and reload where applicable. Training tasks can use a
tiny train/save/load/evaluate cycle; solvers and pipelines choose their equivalent.
Exercise relevant devices, dimensions, units and invalid/boundary cases. GPU
checks use existing Host-owned submission and resource scheduling. Record actual
checks and gaps in existing notes, and reuse still-applicable successful checks.
After a technical failure, test its corrected path before repeating the full run.
This is task-specific agent guidance, not an extra universal admission gate.

A LOCAL criterion should answer the hypothesis's screening question. Improvement
claims need an appropriate comparison or an explained absolute target; a zero
target is valid for many gains/errors. A positive raw score alone does not prove
improvement. Preserve separate goal targets and the accepted metric definition;
revised criteria belong to a new/revised hypothesis. After ablations, explain what
uncertainty another full evaluation resolves. Framework code does not choose the
scientific strategy or enforce dataset-specific thresholds.

## Reducing experiment turnaround

Later iterations begin from the retained implementation and accepted analysis.
Keep working loaders, parsers and still-applicable checks; record the exact
entrypoint, selected variants and remaining execution work in existing notes.
An accepted long-running submission uses the existing Host handoff to free its
Agent slot. Independent accepted chains have their own preparation tasks.

Before freezing a LOCAL measurement, choose a small informative comparison when
the methodology permits. Full GOAL evaluation retains every accepted seed, arm,
dataset and check. Reuse a baseline only when provenance, data/splits, seeds,
protocol and required artifacts match and existing submission access permits it.
Incomplete trials do not become a complete claim. Ordinary experiment logs should
show planned trial counts and bounded phase/trial timings, so low GPU utilization
can be investigated before changing scientific settings or resource parallelism.
These are Agent workflow improvements, not an automatic speedup guarantee.

## Evolution and version selection

Experts may update their own skill through `SELF_EVOLVE`; the manager may update
its copy through `GRAPH_EVOLVE`. The existing Host validates paths, frontmatter,
owner identity, byte budgets and preservation of wired entrypoints. Git remains
the version history and existing Attempt records select versions. Structural
acceptance does not prove a heuristic improves quality or cost.

An accepted update is eligible for later requests/cycles. Already-bound requests
retain their version; operators retain the existing `plugin-pin`, `plugin-revert`
and `plugin-unpin` controls described in [USAGE](../../USAGE.md). Owners do not
overwrite each other's plugins. An unchanged candidate is valid, and an API
outage alone does not justify a skill change.

Private revisions are not automatically shared across users or imported by init.
Public improvements require ordinary source review and multi-task validation,
then reach newly created plugins. Initial Grounding normally runs once; later
evolution cannot retroactively improve that completed call. Direct selection of
a private version for a fresh run is not part of this implementation.

## Expected benefit and verification

The intended benefit is less duplicate investigation, generic validation code
and avoidable history, plus clearer stage handoffs. Necessary scientific work,
provider latency and training time remain. Skill loading also has an instruction
cost; there is no guaranteed speedup.

Compare scientific correctness and accepted outcomes first, then existing total
time, tokens, tool calls and correction records under matched task/model/budget
conditions. Phase timing is authored by the task when applicable. Exact reread counts
do not cover all Bash reads; unknown CLI pricing is not verified billing.


## Current-experiment timing and performance

For costly computation, the shared skill routes to a conditional performance
reference. The task chooses meaningful work units and reuses its logs; it may add
flushed stage/progress/duration messages and calculate estimates in its own code.
Initialization, warmup, steady work, evaluation and artifact checks are separate.
Current-design pilot measurements inform the initial timeout; current progress
informs remaining time. Unknown convergence or future search stays unknown.
Estimates never extend admitted deadlines or reduce required GOAL coverage.

GPU diagnostics optionally retain device power draw/limit (mW), temperature (°C)
and SM clock (MHz), alongside existing utilization/memory observations. Unsupported
fields are absent. These device-level readings are not attributable Process energy
or efficiency. Compare phase throughput/data wait before a bounded tuning probe;
PyTorch-specific advice is loaded only when applicable. Resource authorization,
scientific settings and the submit/handoff workflow remain unchanged.

The performance branch also covers computation: distinguish suspected hotspots
from measured costs, then compare one bounded implementation change. PyTorch
guidance includes independent-loop batching, pairwise-loss reductions, avoidable
synchronization and conditional compilation, alongside data loading. Check the
original and changed outputs, relevant gradients/updates and numerical boundaries
with task-specific tests. Compare normal end-to-end time and peak memory as well
as steady throughput; include startup costs and keep no-change when benefit is
unproven. Higher GPU utilization alone is not an improvement. Queueing and grants
stay Host-owned, and an already-running experiment is not rewritten or profiled
by an expert. New seed guidance does not replace a retained plugin or binding.

Reports retain workload conditions, original evidence and estimated/actual timing.
SELF_EVOLVE/GRAPH_EVOLVE can change these methods and references within the owner's
plugin. Accepted versions become selectable for later requests; existing bindings
remain. Private numerical lessons are conditional, not automatic cross-run policy.

## Expert skill library

Choose **Skill library** on an expert card, or **Expert skill library** in the
global summary. Both open the existing skills workbench. Filter by expert, research
stage and the entire run or its last 1/6/24 hours. For a finished run, the window
ends at its last recorded Agent activity. The stage filter applies to calls;
evolution history follows the expert/time filters.

The library shows observed native Skill calls, loaded/failed/unanswered results,
frequency distribution and an interactive time chart. Calls link to their original
invocation. Availability, prompt mentions, request bindings and reference-file reads
are not Skill calls. Loading does not prove that the method was followed or useful.
Replayed session history and duplicate tool IDs are excluded. Missing, bounded or
live capture gives lower bounds; untimed calls are excluded from time windows/charts.
Cold reads are bounded and cached, so coverage can improve on a later refresh.
Browser opening time does not control recording. Deleted logs cannot be recovered.

Evolution history separates accepted changes, no-change, failed and running
attempts. It identifies changed skills and files, including references/scripts,
and separates publication, later request binding and observed later calls. Open a
skill to inspect its content, supporting files, whole-directory diff and usage.
Concurrent bindings may use different versions. All views reuse existing logs,
Attempt records and Git objects; they do not modify research state or create a
second durable skills database. Existing research runs are not upgraded in place.


## Parser inputs and bounded design
The runtime resolves relative `parser_argv` arguments naming declared raw files
or their parent directories under `/raw`; declared parser files resolve under
`/input`. Explicit absolute paths are preserved. The parser still starts in
`/parsed`, writes only its result there, and sees only declared read-only inputs.
Inside parser code, resolve raw files from `HYPOLOOM_MEASUREMENT_RAW_ROOT`.
Test known positive/negative values with ordinary assertions. Missing or invalid
evidence must fail explicitly, not become a numeric sentinel. Structural smoke
validation alone cannot establish arbitrary scientific correctness.

Import patched experiment code directly from `HYPOLOOM_TARGET_ROOT`, including
in children. Never translate that code root through the data-only
`HYPOLOOM_READ_ROOT_MAP`. Inspect the changed module's actual import path.
DESIGN's existing deadline advisory now covers its last 20%, up to ten minutes;
Grounding retains five minutes. Deliver a small runnable change, reuse retained
checks and put device smoke at the beginning of the admitted Process. No deadline
or GPU permission is enlarged. Frozen parser changes still need a revised
hypothesis/new cycle; accepted runs retain their original bundle.
