# Context, memory and recovery

English · [简体中文](context-management.zh-CN.md)

New ACI-24 runs preserve research identity, Git worktrees, accepted results and
memory across short model sessions. Every ordinary invocation starts a fresh
session, including design, execution, analysis, graph management and retries.
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
Unwritten reasoning cannot be recovered.

## Working limits

The renderer and existing relay share one policy. The relay checks complete
requests, including tool schemas, results and thinking, without rewriting signed
blocks or tool pairs. Effective capacity is the minimum of known deployment,
runtime and working ceilings, less output, tool-growth and estimation reserves.

The fallback working ceiling is **262,144 estimated tokens**, using conservative
UTF-8 byte size, with **49,152 inline input bytes**. This is an application
allowance, not a detected provider window or universal tokenizer guarantee.
Retries halve inline allowance up to a fourfold reduction; sources remain
readable. Oversized essential material can still end in an explicit capacity
failure. Do not omit constraints to make it fit.

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

Web/TUI separate last input and its source, working ceiling and cumulative usage.
Provider usage can be final or partial; transcript readings are labeled separately.
A working allowance does not make an unknown provider window known. Cached input
still occupies context.

## Recovery

Capacity reconstruction spends `budget.max_infrastructure_retries` in
`methodology.yaml`. **Zero disables automatic reconstruction.** `max_retries`
is the separate model-output correction allowance; increasing it does not enable
infrastructure recovery. Invocation/run budgets and stop conditions still apply.

Web shows both provider-measured input and the conservative byte estimate, with
output/tool/margin reservations. Only an explicit CLI `compacting` status means
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

When the complete initial request is locally refused at minimum inline load and
no generation request was forwarded, unchanged recovery ends early. New-run
input/runtime configuration is needed. Missing capture or a capacity failure
after model/tool progress retains ordinary bounded recovery.

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
