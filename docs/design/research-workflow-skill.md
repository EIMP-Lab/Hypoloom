# Shared research workflow skill

English · [简体中文](research-workflow-skill.zh-CN.md)

Status: implemented, 2026-09-28; qualification is recorded in
[the results](../qualification.md#current-development--research-workflow-skill-2026-09-28).
The user authorized implementation and submission to dev. Finer time
instrumentation remains excluded; existing research runs remain unchanged.

## Experiment supply and memory feedback (2026-10-07)

A retained-run read-only sample covered 21.2 hours, 21 submitted experiments and
seven accepted single-member batches. Mean GPU Process concurrency was 1.48;
this is process occupancy, not whole-device utilization on shared GPUs. Design
invocations had a 36.7-minute median. All 21 submissions included estimates, but
17 of 18 closed Processes had unknown coverage despite retaining attributed peak
samples. Later estimates repeatedly treated the old admitted budget as sufficient
because the resource projection hid these known samples. Three reserve-pressure
interruptions and two script failures also extended experiment turnaround.

Reuse the current batch reader to give proposal and manager requests actual
accepted width, three recent batches and OPEN chain counts. Keep these observations
advisory. Strengthen both proposal wings and synthesis to develop independent,
evidence-backed mechanisms within existing bounds; informative refutations remain
scientific progress. Do not force candidates, reserve idle slots, bypass dependencies
or add a queue. The existing Host already interleaves independent chains.

Extend the existing resource-history projection with an attributed observed peak
lower bound. Complete calibration still requires the original full-coverage checks;
unknown samples remain unknown and cannot enable sharing or release initial floors.
The resolver continues to consume the original calibrated field and exact workload
references. Agents see enough evidence to revise undersized estimates, without
silently treating nearby workloads as identical or changing accepted requests.
Unestimated-workload history expansion was considered, then excluded after checking
the actual source submissions: it was not the failure in this run.

Stage guidance reuses retained implementations, comparison-compatible baselines
and successful checks, hands off precise execution work, and chooses informative
LOCAL scope before freezing it. GOAL coverage stays complete. Trial/phase timing
uses ordinary experiment logs; no telemetry service or profiling of retained
processes is introduced. Existing raw rows, batch validation, Git plugin versions
and tests suffice: no additional assurance mechanism, persistent state or authority
is needed. Changes reach new runs/plugins through the existing runtime bundle.

Validation and limits: [qualification](../qualification.md#experiment-supply-and-memory-feedback-2026-10-07).
Scripted/native closure checks establish integration, not a measured improvement
in real-model hypothesis quality or CUDA throughput.

## 1. Problem and scope

The retained MERC-causal-202609281629 Grounding succeeded, including native
compaction and first-pass final validation. It nevertheless repeated repository
discovery and wrote a separate final-output schema checker despite the existing
Host validator. Its task-specific acceptance parser cannot be declared redundant
from its 726-line size: scientific constraints still require real computation
and meaningful tests. Original evidence remains in
`.local/analysis/grounding-review-20260928/`.

Introduce one `research-workflow` skill for evidence reuse, focused investigation,
implementation and verification across applicable stages. Preserve task-specific
science, specialist skills and all existing runtime authority. It should reduce
avoidable work without requiring every node to run an investigation/test checklist.
No promised percentage speedup follows from one successful run.

## 2. Responsibilities and source of truth

| Layer | Responsibility | Owner |
|---|---|---|
| Framework | Admitted permissions, resource ownership, schema validation, parser isolation, accepted criteria and publication | Existing Host code |
| Shared method | Reuse supplied evidence, investigate gaps, reuse applicable code, make and verify the relevant change | `research-workflow` skill |
| Specialist method | Literature reading, GPU planning, experiment repair, reports | Existing specialist skills |
| Scientific implementation | Metrics, aggregation, validity requirements and executable parser | Current task's authorized author |

Reuse `grounding_context.py`, `native_output.py`, `shared_acceptance.py`, existing
Git plugins, Attempt bindings and memory/report paths. Do not add a second
validator, schema, registry, evidence ledger, mandatory checklist artifact or
scoring/acceptance authority. Update the existing runtime identity mechanism to
cover the shared source; do not create another digest/version system.

Keep a single tracked skill source, proposed under
`hypoloom/skills/research-workflow/`, with a short `SKILL.md` and optional stage
references. Materialize that source into newly created Grounding and expert
plugins through the existing seed construction. These run-owned Git copies are
already supported lifecycle versions, not independently maintained public copies.
Existing plugin validation, byte budgets, packaging and runtime preservation must
include the skill and references. Never backfill a retained run's plugin.

## 3. Expected working mode

The shared entry asks the Agent to identify its current obligation, use the
supplied context/index/accepted evidence, inspect only unresolved prerequisites,
reuse suitable code, and verify the outcome appropriate to this stage. Missing,
truncated or stale evidence still warrants fresh investigation. Batch independent
reads when useful; do not limit tool counts or impose a code-line target.

| Stage | Relevant behavior | Boundary |
|---|---|---|
| Grounding | Inspect actual data/interfaces; author and test common acceptance when commissioned | Keep exact goal and confirmed answers; ordinary background-only Grounding does not invent an evaluator |
| Literature and proposal | Trace existing sources, resolve relevant gaps, explain a discriminating hypothesis | Use existing literature/proposal methods; do not demand code or parser fixtures |
| Design/implementation | Read accepted interfaces, reuse code, test changed behavior and a declared local measurement | Shared evaluator remains read-only; do not invent a local measurement for a GOAL-only task |
| Execution/repair | Inspect actual Host state, validate prerequisites, use authorized submit/replacement/handoff paths | No direct GPU launch or repeated polling outside existing ownership |
| Analysis/report | Recompute or inspect relevant raw evidence, check comparability and explain limits | A local test or an Agent narrative is not an accepted scientific outcome |
| Self-evolution | Identify an evidenced weakness and make a focused skill change, or leave it unchanged | Existing owner, budget and version selection rules apply |

Use a short stage-specific routing instruction with the actual bound plugin
name. Load the common entry and relevant reference, not every stage's guidance.
Move advisory method text out of duplicated runtime/specialist instructions;
keep authoritative permissions, output contracts and execution constraints in
framework code. Do not add a new gate requiring a particular tool-call count or
a proof that the Agent invoked Skill. Verify actual skill use in qualification.

Implementation details:

- Initial Grounding now receives a narrowly scoped read-only binding through
  the existing resource mechanism. `agent_sessions.py` retains management/Observer
  checks and adds the exact initial Grounding task. Its execution role, model and
  budget are unchanged; it never receives `/plugin-update`.
- Inspection clarified that `_cycle_reports_enabled` is a runtime-version
  condition, not a current Profile switch. Current scientific stages already
  retain their resources when graph evolution is disabled; preserve this path
  and test it. Limited paper readers, QUERY_PLAN and auxiliary roles without
  a plugin binding retain their focused instructions. No new resource descriptor
  or scientific cycle is needed.

## 4. Validation and task diversity

Agents continue to create task-specific parsers through the existing executable
parser interface. A classification task may calculate F1; an optimization task
may compute an objective and feasibility; a reproduction task may compare declared
protocol/results. None inherits ERC datasets, seed counts, filenames or thresholds.

Use the existing Host Stop validator for the final structured response and its
existing parser smoke execution. The skill explains these facilities so Agents
do not recreate a generic schema checker in their workspace. Keep task-specific
positive/negative/boundary tests: smoke execution proves interface compatibility,
not scientific correctness. Reuse an existing scientific implementation only
after checking its assumptions and input semantics against the current goal.
The parser computes measurements and checks; the Host retains goal evaluation.

Do not impose one universal raw-evidence layout or a new user-authored DSL.
Ambiguous scientific intent still follows the existing human-clarification path.

## 5. Self-evolution and sharing

All owners start with the same public method. Experts update their own plugin
through SELF_EVOLVE; the manager updates its own plugin through the existing
GRAPH_EVOLVE path. No extra evolution Agent or per-call reflection loop is added.
Use scoped, closed Attempt evidence, accepted reports and original failure traces;
do not insert live log ages or entire transcripts into frozen scientific inputs.
An API outage alone is not evidence that a skill needs changing.

Host structural/ownership validation and Git commits make a candidate selectable
under existing future-request/cycle rules. Existing bound requests keep their
revision. Pin/revert/unpin remains the recovery mechanism. Structural acceptance
does not prove improved reasoning, lower cost or better scientific quality.
Updates cannot change accepted criteria, thresholds, permissions or framework
validation, and an owner cannot overwrite a peer's plugin. Lessons may be shared
through existing accepted summaries; they do not silently replace peer versions.

Initial Grounding generally occurs once, before useful evolution evidence exists.
Later evolution cannot accelerate that past call. Public improvements reach fresh
runs through ordinary reviewed source updates and multi-task regression. Private
versions are not automatically published, merged across users or imported by init.
Direct private-version import into a fresh run requires a separate explicit
selection feature; it is an optional follow-up, not a first-version promise.
No old evaluator or old scientific conclusion is inherited as new-task acceptance.

## 6. Development sequence and verification

| Step | Deliverable | Relevant verification |
|---|---|---|
| D1 | One shared source, common entry and focused references; remove duplicate advisory text | Packaging, prompt size, unchanged specialist entrypoints and authority instructions |
| D2 | Initial Grounding and scientific-stage loading through existing plugin resources | Real request-to-workspace-to-CC path; exact role/model/budget; disabled evolution; restricted readers; retry/clarification/compaction continuity |
| D3 | Existing self-evolution can maintain the new skill | Own-plugin update, unchanged candidate, accepted Git selection, later-cycle use, pin/revert, concurrent owner isolation and interrupted update recovery |
| D4 | Bilingual guidance and finite quality/efficiency evaluation | Existing G0–G5 promotion model, focused regressions during work and full relevant qualification at candidate freeze |

Exercise classification, numerical optimization and a non-neural reproduction or
artifact task with differing layouts and metric directions. Check actual parser
execution, correct measurements and meaningful violating inputs, then relevant
CPU/GPU experiment collection/publication through the real Host. Do not replace
scientific checks with file-existence assertions or count permission-denied native
tests as passing. Keep existing runs read-only and create isolated test roots.

For a finite model comparison, hold the task, initial state, model and existing
invocation budgets fixed between baseline and skill arms. Use already recorded
total wall time, token usage, tool calls, validation corrections and outcomes;
no new fine-grained timing instrumentation is planned. Distinguish fewer repeated
investigations/duplicate validators from useful scientific tests. Existing exact
reread metrics miss Bash reads, so inspect scoped traces where necessary. Report
missing coverage and unknown CLI pricing, and do not declare savings from one
sample. Validation quality has priority over a lower token or tool count.

## 7. Expected benefits and limits

Expected: fewer repeated discoveries, less model-generated generic validation,
shorter unnecessary history, clearer stage handoffs and evidence-based local
method improvements. Benefits apply across users because sources and science are
supplied by each request rather than embedded in the skill.

The skill adds some instruction cost and models may follow it imperfectly.
It cannot remove necessary scientific investigation, provider latency, training
time or task-specific evaluator work. Current evidence supports this direction,
not a numerical speed/cost claim. No broader timing dashboard, RAG service,
automatic cross-user learning or change to context limits is in scope.
