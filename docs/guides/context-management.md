# Context, memory and recovery

English · [简体中文](context-management.zh-CN.md)

See [runtime recovery](runtime-recovery.md) for ACI 28 pool preflight, node-scoped GPU recovery and context/progress observations.

New ACI-25 runs preserve research identity, Git worktrees, accepted results and
memory across bounded model sessions. New tasks and capacity reconstruction use
fresh sessions. An eligible correction or transport retry of the same frozen
task can resume its complete archived session when observed capacity leaves
room for the retry. Missing evidence uses a fresh session. ACI-24 runs retain
their original per-Attempt reconstruction behavior.
Native compaction is optional; no extra paid summarizer is required. Three-round
Debate retains its existing bounded fork protocol; a capacity failure there
ends the auxiliary task instead of replaying the oversized conversation.

## Inputs and memory

The current request supplies exact task identities, constraints and source
references. A total inline budget selects small current inputs; complete frozen
sources remain at the advertised `/inputs` paths. Models must read all applicable
goal constraints and specifications before acting, including offloaded sources.
`R1` aliases and numbered paths belong only to the current request; reread the
mapping after a session change or native compaction.

Existing summaries, cycle reports, expert memory, research views and paper
sources support retrieval by ID, path and keyword. Summaries retain decisions,
qualified negative results, unresolved errors and next steps. They cannot change
facts or grant permission. Only CYCLE_REPORT writes MEMORY.md. There is no vector
database or second fact authority.

Infrastructure retries retain identical frozen inputs. Bounded operational
observations identify the previous Attempt and owned submitted experiments;
these are not scientific citations. Inspect and test retained unaccepted code.
ACI-25 includes bounded tool arguments, paths/ranges, checks and errors instead
of only the last three results. Grounding sees retained `/acceptance` drafts and
the same task's previously authorized paper sources. Optional
`/acceptance/WORK_NOTES.md` can record checked interfaces and unfinished work;
it is working material, not accepted parser source. Unwritten reasoning cannot
be recovered. Ordinary nodes still cannot write expert MEMORY.md.

## Working limits

The renderer and existing relay share one policy. The relay checks complete
requests, including tool schemas, results and thinking, without rewriting signed
blocks or tool pairs. Effective capacity is the minimum of known deployment,
runtime and working ceilings, less output, tool-growth and estimation reserves.

The fallback working ceiling remains **262,144 tokens**, with **49,152 inline
input bytes**. This is an application allowance, not a detected provider window.
ACI-25 keeps inline allowance stable across retries. Capacity measurement uses:

1. An already observed successful provider count for the exact current input,
   when available. The framework does not add count API requests.
2. Complete same-relay provider usage for a verified unchanged prefix, plus a
   conservative byte estimate of appended text and retained reserves.
3. An explicitly labeled UTF-8 byte estimate when neither is usable. A refusal
   on this fallback means capacity could not be confirmed, not that the provider
   reported an overflow.

These are not universal tokenizer guarantees. Changed history, model/protocol
or tool definitions invalidate an anchor; new multimodal input and unknown
cache semantics cannot silently inherit one. Counting recognizes the CLI's
equivalent single text block/string representation and movement of known
`ephemeral` cache annotations on text/tool-result blocks. It preserves content,
order, tool identities and signed thinking; provider requests are not rewritten. Anchors are disposable per-invocation observations;
a resumed invocation may still need fresh reconstruction if its first request
cannot be reliably measured. That reconstruction uses the existing infrastructure
retry allowance; with zero retries it stops and reports the reason. Byte, request,
time, total-token and cost limits remain independent. Do not omit constraints to make an input fit.

Optional `methodology.yaml` → `memory` controls for new runs:

| Field | Meaning |
|---|---|
| `working_context_tokens` | Framework ceiling; default 262144. |
| `context_window_tokens` | Confirmed deployment window; omit when unknown. |
| `runtime_context_tokens` | Confirmed runtime ceiling; omit when unknown. |
| `max_inline_input_bytes` | Total rendered inline allowance; default 49152. |
| `input_usage_semantics` | `separate_cache` for Anthropic-style separate counts; `includes_cache` when input already includes cache; `unknown` to withhold an unsupported input total. |

Limits are positive integers. Configure smaller confirmed ceilings explicitly.
A `[1m]` model suffix and invocation token budget do not establish capacity.
A missing count endpoint does not disable local checks. Configuration is frozen
for the run; switching API connections does not change it.

Web/TUI separate the current request estimate, last successful measured input
and their exchange numbers, working ceiling and cumulative usage. A refused
request cannot borrow the preceding request's usage for its occupancy bar.
Provider usage can be final or partial; transcript readings are labeled separately.
A working allowance does not make an unknown provider window known. Cached input
still occupies context.

## Recovery

Capacity reconstruction spends `budget.max_infrastructure_retries` in
`methodology.yaml`. **Zero disables automatic reconstruction.** `max_retries`
is the separate model-output correction allowance; increasing it does not enable
infrastructure recovery. Invocation/run budgets and stop conditions still apply.

Web identifies provider count, measured-prefix estimate or byte fallback, with
output/tool/margin reservations. Near 80% of the available input allowance,
ACI-25 gives a bounded advisory through existing native tool hooks to finish
work and preserve progress. It does not grant tool permission, accept output,
force a summary API call or guarantee that the model follows the advice.
Only an explicit CLI `compacting` status means
compaction is ongoing. Native `compact_boundary` events supply completed counts,
before/after input and duration; absent fields stay unknown.

`context_observation` counts successful native Read calls and repeated exact
ranges of the same frozen input. Different ranges, mutable code and shell reads
are excluded. Live bounded tails report `partial`; missing evidence reports
`unavailable`, not zero. `input_material` reports source/unique/duplicate bytes and
offloaded source counts; these are not billed token counts.
Logical-node material totals sum per-prompt observations; “unique” does not mean
global deduplication across invocations.

`context_recovery` uses existing charges to separate the initial context failure
from subsequent recovery Attempts, including successful ones. Logical-node,
expert and run totals retain unknown billing instead of treating reservations as
spend. Command detail includes cache/material observations; expert cards show
reported recovery costs and unknown counts across all cycles. Cross-Attempt
rereads compare exact frozen refs within one logical node. These costs do not
establish savings versus a counterfactual run.
Amounts remain runtime/CLI reports, not reconciled vendor invoices. A CLI can
report fallback dollar estimates for an unknown model price. Compare requests,
tokens and cache distribution directly; do not claim actual billing savings from
those estimates. An unsettled invocation's reservation is not actual spend.
Web/TUI and support metadata expose the CLI session's unknown-pricing flag;
missing evidence is separately unavailable, not a verified price. This observation
does not rewrite recorded charges or budgets.

When a **fresh ACI-25 session's** complete initial request is locally refused and
no generation request was forwarded, unchanged recovery ends early. New-run
input/runtime configuration is needed. A resumed session, missing capture or
a capacity failure after model/tool progress retains bounded reconstruction.
ACI-24 retains its old minimum-inline rule. An operator API connection reload
or switch makes subsequent ordinary retries use fresh sessions; it does not
reinterpret already OPEN session bindings.

| Observation | Behavior |
|---|---|
| Local capacity, explicit oversized input or failed native summary | Close/charge/archive the invocation, capture code, then reconstruct within existing infrastructure retry and total budgets. |
| HTTP 200, zero output, `max_tokens` | Record an anomaly; infer capacity only with a configured window and near-window usage. Normal nonzero output truncation is separate. |
| Authentication, rate or quota failure | Existing shared [API recovery](provider-recovery.md); submitted experiments continue. |
| Recovery exhausted | Show node and reason. API retry cannot reopen a finalized node or bypass required scientific acceptance. |

The next invocation queries owned experiments before submission. Replaying a
committed submit retains its original identity, without duplicate experiments
or reset costs. Archive failures retain existing fail-closed handling.
A technical analysis failure alone no longer cancels a legal next-cycle revision;
accepted adjudication, update, report, budget and termination controls still apply.
API errors are not scientific refutations. If an exhausted execution controller
owned experiments that have closed with valid results, accepted analysis may
still request the next experiment within budget. Archive failure or unfinished
experiments do not qualify. Promoted source code continues into new research
chains independently of model-session inheritance. Old runs retain their original
bundles; cross-version migration is unsupported.

## Measurement and citations

For `DELTA_FROM_BASELINE`, parser `value=0.72`, `baseline=0.68` produce `0.04`.
A parser already returning the intended difference must use `VALUE`. Relative
delta also divides by the absolute baseline. Native local parser diagnostics
show these intermediate values; test a nonzero baseline. The framework does not
guess scientific intent from signs or names.

Native Stop checks web_sources against delivered retrievals and prior same-node
retrieval evidence using the final Host validator. A URL in a paper packet or
historical prose does not establish a web retrieval. Errors are returned within
the invocation's existing correction/time budget; final Host acceptance remains.

Research methods across Grounding and later stages are described in the
[workflow guide](research-workflow.md); skill evolution reuses the existing Git
plugins and does not add another memory store.
