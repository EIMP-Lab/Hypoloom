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
generic schema validator. Scientific tests are still necessary: qualifying and
violating cases, strict-boundary cases, missing evidence and mandatory validity.
A passing smoke test proves that the parser interface works, not goal success.
Accepted shared evaluators remain read-only after Grounding.

After retries, clarification or context recovery, use the current request,
accepted sources and retained work. Optional `/acceptance/WORK_NOTES.md` is draft
material, not scientific evidence or a declared parser source. This skill does
not change context limits, add a paid summarizer or introduce RAG storage.

## Before costly experiments

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
conditions. No finer timing instrumentation is introduced. Exact reread counts
do not cover all Bash reads; unknown CLI pricing is not verified billing.
See [qualification](../qualification.md) for actual coverage and limitations and
the [design](../design/research-workflow-skill.md) for implementation rationale.
