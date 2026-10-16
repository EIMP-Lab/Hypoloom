# Personal Profile configuration

English · [简体中文](README.zh-CN.md)

A Profile describes one research topic in six YAML files. This guide explains
all fields in the [English template](../profiles/templates/multi-expert-en/),
including its commented options, and the additional settings commonly needed.
Prepare your local copy before running `/autoresearch init`.

## Prepare a private Profile

From the Hypoloom repository root, copy the template into a new directory:

```bash
mkdir -p profiles/local
(umask 077 && mkdir profiles/local/my-topic && \
  cp profiles/templates/multi-expert-en/*.yaml profiles/local/my-topic/)
```

This creates a directory with mode 0700 and files with mode 0600. If `my-topic`
already exists, the copy stops; edit that Profile or choose a different name.
Edit only `profiles/local/my-topic/`, which Git ignores. Keep exactly the six
regular, non-symlink UTF-8 YAML files below; put notes elsewhere.

| File | Purpose | First changes |
| --- | --- | --- |
| [goal.yaml](#goalyaml) | Research goal and completion criteria | Describe your task, constraints and expected evidence. |
| [experts.yaml](#expertsyaml) | Expert roles and model preferences | Adapt expertise and research-chain limits. |
| [methodology.yaml](#methodologyyaml) | Runtime, literature, research controls and budgets | Replace paths, fill literature credentials and set affordable limits. |
| [language.yaml](#languageyaml) | Research output languages | Keep `en` or select the required language tags. |
| [notify.yaml](#notifyyaml) | Local logs, dashboard and external notifications | Enable external delivery only after reviewing recipients. |
| [safety.yaml](#safetyyaml) | Data access, GPUs and sandbox restrictions | Replace data paths; keep `gpus: []` for CPU work. |

Replace every `/REPLACE_ME/` path with an existing absolute path. Model API
authentication is configured separately in [Setup](../SETUP.md). The examples
below reproduce the English template; credentials are deliberately blank.
Numbers are example limits, not recommended spending commitments. Unknown fields,
duplicate YAML keys and unresolved runtime paths are rejected.

## goal.yaml

Define what research should establish and when it should stop.

```yaml
title: Improve a measured research system
description: >-
  Investigate the target project from three complementary perspectives. Each
  expert defines a reproducible experiment and explicit measurement/parser;
  accepted results must support a concrete improvement over a stated baseline.
metric: null
constraints:
  - Keep the original repository and datasets read-only.
  - Explain uncertainty and preserve failed experiments as evidence.
stop_conditions:
  max_rounds_per_expert: 4
```

| Field | Meaning and accepted values |
| --- | --- |
| `title` | Nonempty topic title, displayed in run summaries. |
| `description` | Nonempty research objective. State the baseline, expected improvement, measurement procedure and required evidence. `>-` folds the following lines into one string. |
| `metric` | `null` lets research establish the metric. To specify one shared metric, use the object described below. |
| `constraints` | List of research requirements and exclusions. These instructions do not replace sandbox permissions. |
| `stop_conditions.max_rounds_per_expert` | Positive integer ceiling on completed research cycles per expert; `4` is not an experiment count. |

With the template's default shared-goal policy, an accepted `SUPPORTED` result
can finish the run. `metric: null` does not itself require a common evaluator.
For a fixed metric, replace only the `metric` entry, for example:

```yaml
metric:
  name: accuracy
  direction: maximize
  target: 0.90
```

| Optional field | Meaning |
| --- | --- |
| `metric.name` | Name of the measured metric used in the evaluation. |
| `metric.direction` | `maximize` or `minimize`. |
| `metric.target` | Finite numeric threshold, expressed in the metric's units. |
| `evaluation` | Set to `grounding` when experts must share an acceptance procedure established during Grounding. Describe the complete requirements in `description`. |

For `evaluation: grounding`, keep `metric: null` and omit `completion` and
`stop_conditions.target_metric`. One complete `GOAL` candidate must satisfy all
accepted requirements; partial `LOCAL` results cannot be combined to claim success.
See [research workflow](../docs/guides/research-workflow.md). Put time and cost
limits in `methodology.yaml.budget`.

## experts.yaml

Define a roster and the defaults inherited by its members.

```yaml
defaults:
  # Omit model to use the Host model selected during initialization.
  effort: high
  max_forks: 2
  max_hypotheses_per_round: 2
# Optional shared Step-1 query-plan/research override; otherwise use defaults.
# grounding:
#   model: REPLACE_WITH_PINNED_GROUNDING_MODEL
#   effort: medium
experts:
  - name: optimizer
    role: Optimization researcher
    persona: Separate algorithmic improvements from tuning noise.
    focus: [optimization, convergence, controlled comparisons]
  - name: architect
    role: Systems and architecture researcher
    persona: Explain performance in terms of mechanisms and bottlenecks.
    focus: [architecture, resource efficiency, reproducibility]
  - name: evaluator
    role: Measurement and validity researcher
    persona: Challenge leakage, confounding and unstable evaluation rules.
    focus: [measurement, statistical validity, robustness]
```

| Field | Meaning and accepted values |
| --- | --- |
| `defaults.effort` | Requested reasoning effort: `low`, `medium`, `high`, `xhigh` or `max`. Actual support depends on the selected model/runtime. |
| `defaults.max_forks` | Positive integer ceiling on active research chains per expert. |
| `defaults.max_hypotheses_per_round` | Positive integer ceiling on hypotheses proposed in one round. This does not require every slot to be filled. |
| `experts` | Nonempty list of experts. Each entry uses the fields below. |
| `experts[].name` | Unique, stable ID, 1–128 characters: start with a letter or digit, then use letters, digits, `_`, `.` or `-`. Keep IDs stable when editing descriptions. |
| `experts[].role` | Human-readable specialty or responsibility. |
| `experts[].persona` | Instructions for the expert's research approach and judgment. |
| `experts[].focus` | Nonempty list of distinct focus areas or reasoning principles. |

The template omits model IDs so all stages inherit the Host model reviewed during
init. An interactive `/model` choice must be passed to that Host explicitly.
Use overrides only when you want a different model or effort:

| Optional field | Meaning |
| --- | --- |
| `defaults.model` | Exact provider-supported model ID for the Profile's default model. |
| `grounding.model`, `grounding.effort` | Shared Grounding model/effort; omitted fields inherit `defaults`, then the Host selection. This applies before individual expert rounds. |
| `experts[].model`, `experts[].effort` | Override the corresponding default for one expert. |
| `experts[].max_forks`, `experts[].max_hypotheses_per_round` | Override the corresponding default ceiling for one expert. |
| `experts[].stage_overrides` | Map from a stage name to `model` and/or `effort`. A stage override takes precedence over expert settings. |

For example, add this fragment inside an existing expert entry to change its
analysis effort:

```yaml
stage_overrides:
  inner_loop.analysis:
    effort: high
```

Stage names include `literature_review`, `hypothesis_proposal`,
`hypothesis_proposal.forward`, `hypothesis_proposal.backward`,
`hypothesis_proposal.synthesis`, `inner_loop.code`, `inner_loop.run`,
`inner_loop.analysis`, `hypothesis_update`, `cycle_report`, `self_evolve`,
`observer`, `debate` and `leader`. A specific proposal substage overrides the
broader `hypothesis_proposal` setting. Personal `stage_overrides.grounding` does
not take effect; use the shared `grounding` block. Init shows effective choices
and their sources; see [initialization](../docs/guides/initialization.md).

## methodology.yaml

This file owns runtime paths, literature access, research controls and budgets.

```yaml
schema_version: 4
runtime:
  target_repo: /REPLACE_ME/research-target
  conda_env: /REPLACE_ME/project-runtime
literature:
  enabled: true
  tools: [web_search, web_fetch]
  max_sources: 8
  default_route: direct  # No proxy required; optionally auto or proxy_only.
  # Set proxy_url in your private Profile when needed; auto needs no domain list.
  # proxy_url: "http://127.0.0.1:7890"
  # proxy_domains: []  # Exact-host proxy-only overrides.
  # direct_domains: [] # Exact-host direct-only overrides; no overlap allowed.
  # Fill only in your ignored local Profile (mode 0600), never in this template.
  # Host-only values: omitted from admitted contracts and Agent inputs.
  openalex_api_key: ""
  unpaywall_email: ""  # Required only for the Unpaywall PDF fallback.
inner_loop:
  max_iterations_per_hypothesis: 4
  repair_attempts: 2
output_correction:
  max_failed_validations: 3
interaction:
  observer:
    coalesce_reports: true
  leader:
    enabled: true
    check_after: expert_round
    min_rounds_before_leader: 1
  debate:
    enabled: true
    rounds: 3
    min_interval_seconds: 1800
    min_new_outcomes: 2
graph_evolution:
  enabled: true
  stagnation_rounds: 2
memory:
  retention: retain
  max_transcript_bytes: 134217728
  max_memory_bytes: 8388608
  max_plugin_bytes: 1048576
budget:
  gpu_vram_reserve_mb: 512  # Absolute per-device floor; GPU use also needs a workload floor.
  gpu_vram_reserve_percent: 5  # Percentage of the admitted workload, rounded up to 64 MiB.
  total_budget_usd: 45
  per_expert_budget_usd: 15
  wallclock_hours: 6
  experiment_timeout: 1800  # seconds
  max_experiments: 96
  max_active_hypotheses: 6
  max_active_agents: 3
  provider_rpm_limit: 60  # This run's allocation per model; not the account's observed limit.
  # provider_max_in_flight: 3  # Optional override; otherwise follows max_active_agents.
  max_active_processes: 2
  max_active_reviews: 3
  max_retries: 2
  max_infrastructure_retries: 2
  max_process_lost_retries: 2
  agent_invocation:
    max_turns: 40
    max_tokens: 80000
    max_output_bytes: 262144
    wall_seconds: 600
    max_cost: "1"
    token_enforcement_mode: enforce
```

### Runtime and literature

| Field | Meaning and accepted values |
| --- | --- |
| `schema_version` | Keep `4`, the format used by these six-file Profiles. |
| `runtime.target_repo` | Absolute path to a clean Git repository containing the research code. Experiments use private worktrees; the original repository remains read-only. |
| `runtime.conda_env` | Absolute prefix of the complete experiment environment, including its Python interpreter and dependencies. For GPU experiments, select a compatible CUDA environment. |
| `literature.enabled` | Enable the configured literature tools. |
| `literature.tools` | Allowed tools: `web_search`, `web_fetch` and optionally `read_papers`. The template enables search/fetch only. Readers share Agent slots and budgets. |
| `literature.max_sources` | Positive integer limit used for each expert's paper budget; `8` in the template. |
| `literature.default_route` | `direct`: direct requests; `auto`: initially direct, with fallback for eligible connection failures and a short preference for a successful proxy; `proxy_only`: proxy route. `auto` and `proxy_only` require `proxy_url`. Domain overrides below take precedence. |
| `literature.openalex_api_key` | OpenAlex key for the literature search service. Fill the quoted string only in the private local file before launch. |
| `literature.unpaywall_email` | Contact email required when using the Unpaywall PDF fallback; otherwise leave empty. |

| Optional literature field | Meaning |
| --- | --- |
| `literature.proxy_url` | Server-side HTTP CONNECT proxy, such as `http://127.0.0.1:7890`. Configure it together with `auto`, `proxy_only` or a nonempty `proxy_domains` list. It is separate from model and notification proxies. |
| `literature.proxy_domains` | Exact public HTTPS hostnames that must use the proxy. No schemes, paths, ports or wildcards. |
| `literature.direct_domains` | Exact public HTTPS hostnames that must connect directly; must not overlap `proxy_domains`. |
| `literature.semantic_scholar_api_key` | Optional key for the default Semantic Scholar DOI fallback. Empty/omitted at startup uses anonymous lookup. |
| `literature.elsevier_api_key` | Optional official Elsevier article API key; enables that path for new Hosts. |
| `literature.elsevier_insttoken` | Institution token, only if issued by Elsevier for the selected API access. |

Add optional fields under the existing `literature` mapping; do not duplicate it.
API keys do not establish publisher subscription rights. A configured Semantic
Scholar key is reread for each admitted lookup; removing it or making its file
unreadable fails that lookup instead of silently switching to anonymous access.
See [institutional full text](../docs/guides/institutional-access.md) for routing,
access checks, browser login and PDF import.

### Research controls and memory

| Field | Meaning and accepted values |
| --- | --- |
| `inner_loop.max_iterations_per_hypothesis` | Positive integer limit on scientific iterations of a hypothesis chain. |
| `inner_loop.repair_attempts` | Nonnegative limit on execution-repair attempts. Repairs are distinct from scientific revisions and model-call retries. |
| `output_correction.max_failed_validations` | Positive limit on rejected structured submissions within one model invocation. Reaching the limit ends that correction opportunity; outer retries still use their own budgets. |
| `interaction.observer.coalesce_reports` | Allow a successful Observer result to cover eligible peer reports it already reviewed, reducing redundant observations. |
| `interaction.leader.enabled` | Allow evidence-based leader selection and advice. |
| `interaction.leader.check_after` | Keep `expert_round`; `cycle_report` is an alias. Checks follow completed expert rounds, including self-evolution completion or formal exhaustion. Other values do not activate a different trigger. |
| `interaction.leader.min_rounds_before_leader` | Positive minimum number of completed expert rounds before leader selection is eligible; not a count of experiments. |
| `interaction.debate.enabled` | Allow debate when coordination conditions warrant it. |
| `interaction.debate.rounds` | Must be `3`; the supported debate has exactly three rounds. |
| `interaction.debate.min_interval_seconds` | Nonnegative minimum interval between debates, in seconds. |
| `interaction.debate.min_new_outcomes` | Nonnegative minimum number of new outcomes required for another debate. |
| `graph_evolution.enabled` | Enable shared research-graph management and evolution. |
| `graph_evolution.stagnation_rounds` | Positive advisory window of completed rounds for reviewing progress. It does not force a graph change or a scientific verdict. |
| `memory.retention` | `retain` preserves session files; `archive_on_close` archives eligible session files after the run is terminal and no effects remain open. |
| `memory.max_transcript_bytes` | Maximum retained session transcript size; template: 134217728 bytes (128 MiB). |
| `memory.max_memory_bytes` | Maximum expert memory content size; template: 8388608 bytes (8 MiB). |
| `memory.max_plugin_bytes` | Maximum skill-plugin content size; template: 1048576 bytes (1 MiB). |

All three byte limits are positive integers. Optional context controls belong in
the same `memory` mapping:

| Optional field | Meaning |
| --- | --- |
| `memory.working_context_tokens` | Positive explicit working ceiling; omit to follow the resolved model window. |
| `memory.context_window_tokens` | Positive confirmed deployment context window; omit when unknown. |
| `memory.runtime_context_tokens` | Positive confirmed runtime ceiling; omit when unknown. |
| `memory.max_inline_input_bytes` | Positive total inline input allowance; default 49152 bytes. Full sources can remain available through file references. |
| `memory.input_usage_semantics` | `separate_cache`, `includes_cache` or `unknown`, according to the provider's input-token accounting. |
| `memory.context_metadata` | Boolean permitting bounded read-only model metadata queries when needed; default `true`. |

These context settings do not replace invocation token or output limits.
See [context and memory](../docs/guides/context-management.md).

### Run budgets

All fields below are under `budget`. Cost and time values must be positive;
counts are positive integers except retry allowances, which may be zero.
GPU reserves may also be zero.

| Field | Meaning and units |
| --- | --- |
| `budget.total_budget_usd` | Run-wide model spending allowance in USD; template: 45. |
| `budget.per_expert_budget_usd` | Model spending allowance per expert in USD; template: 15. Shared work also consumes the run-wide allowance. |
| `budget.wallclock_hours` | Whole-run wall-clock allowance, in hours; template: 6. |
| `budget.experiment_timeout` | Maximum duration of one experiment Process, in seconds; template: 1800. |
| `budget.max_experiments` | Run-wide experiment-count ceiling. |
| `budget.max_active_hypotheses` | Concurrent active hypothesis ceiling. |
| `budget.max_active_agents` | Concurrent Agent ceiling, including proposal arguments and reading assistants. Waiting parent Agents still occupy slots. |
| `budget.provider_rpm_limit` | This run's request allocation per model, in requests per minute. It is not a discovered account quota. |
| `budget.provider_max_in_flight` | Optional simultaneous provider-request limit; omitted in the template, so it follows `max_active_agents`. |
| `budget.max_active_processes` | Concurrent experiment Process ceiling. |
| `budget.max_active_reviews` | Concurrent Review ceiling. |
| `budget.max_retries` | Additional attempts after eligible model/output failures; `2` permits up to two retries beyond the initial attempt. |
| `budget.max_infrastructure_retries` | Separate allowance for infrastructure retries, subject to the remaining run and invocation budgets. |
| `budget.max_process_lost_retries` | Additional attempts for lost experiment Processes; not permission to rerun every failed scientific result. |
| `budget.gpu_vram_reserve_mb` | Absolute per-device reserve floor in MiB; template: 512. The field name uses `mb`. |
| `budget.gpu_vram_reserve_percent` | Integer percentage from 0 to 100; template: 5. A positive value takes the maximum of that workload percentage, the absolute floor and 512 MiB, then rounds up to 64 MiB. Zero uses only the absolute floor. |

Cost limits govern further dispatch. Responses already in flight can report
charges above the remaining allowance; unknown charges remain accounted for
separately. Leave room for retries, analysis and the final report. Concurrent
limits are ceilings, not requested utilization.

The template is CPU-only. Before enabling GPU execution, provide a measured
workload floor with `budget.min_vram_per_experiment` (MiB) and review explicit
Host device authorization. `budget.initial_vram_per_experiment` is an optional
initial working-set estimate for a configured shared pool. The initial estimate
must respect the floor. See [GPU resource planning](../docs/guides/gpu-resource-planning.md)
for reserve calculations, fixed grants and shared pools.

### Per-invocation budgets

Each Agent invocation also has independent limits. They do not replace the
whole-run or per-expert budgets.

| Field | Meaning and units |
| --- | --- |
| `budget.agent_invocation.max_turns` | Positive maximum number of model turns in one invocation. |
| `budget.agent_invocation.max_tokens` | Positive total token allowance for one invocation, not the model's context-window size. |
| `budget.agent_invocation.max_output_bytes` | Positive maximum size of the returned output, in bytes; template: 262144 (256 KiB). |
| `budget.agent_invocation.wall_seconds` | Positive per-invocation deadline, in seconds; template: 600. |
| `budget.agent_invocation.max_cost` | Nonnegative canonical decimal string in USD, such as `"1"` or `"0.5"`. Use a positive allowance for paid work; `"0"` is not unlimited. This limit is separate from total spending. |
| `budget.agent_invocation.token_enforcement_mode` | `enforce` applies the token allowance; `observe` records token usage without enforcing that token ceiling. Other limits remain active. Keep `enforce` unless observation-only accounting is intentional. |

## language.yaml

Choose language tags for research prompts and outputs.

```yaml
prompt_language: en
report_language: en
code_comment_language: en
```

| Field | Meaning |
| --- | --- |
| `prompt_language` | Language used for research instructions. |
| `report_language` | Requested language for research reports. |
| `code_comment_language` | Requested language for generated code comments. |

The English template uses `en`; the Chinese template uses `zh-CN`. These fields
control research language, not the monitor's interface language. Keep YAML keys,
model IDs, tool names and expert IDs unchanged when translating prose.

## notify.yaml

The template logs locally and enables the loopback dashboard. External Telegram
delivery stays disabled until you configure and approve it.

```yaml
sinks:
  log:
    level: info
    path: events.jsonl
  telegram:
    enabled: false
    # Fill only in your local Profile, keeping this file mode 0600; no env needed.
    # Quote both values. Never commit them or paste them into a conversation.
    bot_token: ""
    chat_id: ""
  dashboard:
    enabled: true
    host: 127.0.0.1
    port: 0  # OS-selected free port; use 1–65535 to pin a fixed port.
events:
  # new_best_result: first/group-best experiment measurement; goal_verdict_recorded: overall GOAL judgment.
  telegram: [grounding_question, grounding_answered, new_best_result, goal_verdict_recorded, leader_started, debate_started, expert_round_done, provider_waiting, provider_retry_requested, provider_recovered, literature_waiting, literature_retry_requested, literature_recovered, error, run_finished, run_briefing]
```

| Field | Meaning and accepted values |
| --- | --- |
| `sinks.log.level` | Minimum log level: `debug`, `info`, `warning` (`warn`), `error` or `critical`. |
| `sinks.log.path` | Path relative to this run's notification output directory. Keep `events.jsonl`, or use a relative path without `..` and outside `cursors/`. |
| `sinks.telegram.enabled` | Enable Telegram delivery only after checking the token and recipient. |
| `sinks.telegram.bot_token` | Private bot token string; fill locally and keep quoted. |
| `sinks.telegram.chat_id` | Approved recipient chat ID as a quoted string, including numeric IDs. |
| `sinks.dashboard.enabled` | Enable the Host-managed read-only Web dashboard. |
| `sinks.dashboard.host` | Loopback bind address; keep `127.0.0.1`. Public interface addresses are rejected. |
| `sinks.dashboard.port` | `0` selects a free port; 1–65535 fixes a port. The startup output gives the actual private bearer URL. |
| `events.telegram` | Event names to deliver to Telegram when that sink is enabled. The template selection is explained below. |

| Event selection | Meaning |
| --- | --- |
| `grounding_question`, `grounding_answered` | A pending research question and its recorded answer. Keep `grounding_question` when you need notification of required input. |
| `new_best_result` | First measurement or an improved measurement in a comparable experiment group; not a declaration that the whole goal is met. |
| `goal_verdict_recorded` | Recorded judgment of the overall research goal. |
| `leader_started`, `debate_started` | Coordination activity begins. |
| `expert_round_done` | An expert research round completes. |
| `provider_waiting`, `provider_retry_requested`, `provider_recovered` | Model access is waiting, a retry is requested, or access recovers. |
| `literature_waiting`, `literature_retry_requested`, `literature_recovered` | Literature access is waiting, a retry is requested, or access recovers. |
| `error` | An error requiring attention. |
| `run_finished` | The run ends. |
| `run_briefing` | A consolidated run briefing. |

Optional notification settings:

| Field | Meaning |
| --- | --- |
| `proxy_url` | Top-level server HTTP CONNECT proxy for notifications only, such as `http://127.0.0.1:7891`. Configure before init. |
| `sinks.webhook.enabled` | Enable an explicitly approved webhook destination. |
| `sinks.webhook.url` | Private destination URL: HTTPS, or HTTP only for a loopback receiver; omit embedded credentials and fragments. |
| `sinks.webhook.token` | Optional bearer token for that destination. |
| `events.webhook` | Event selection for the enabled webhook; use supported event names. |
| `events.log` | Optional local-log selection; omission selects all supported notification event types, subject to log level. |

The notification proxy is independent of model/literature routing. Loopback means
the Linux server, not a desktop client. Existing runs and **Retry Telegram delivery**
do not hot-load proxy changes. Keep dashboard bearer URLs private and use the new
URL after restart. See [notifications](../docs/guides/notifications.md) for delivery,
server proxy setup and troubleshooting.

## safety.yaml

Limit data access, compute resources and shell/filesystem behavior.

```yaml
allow_read_paths: [/REPLACE_ME/research-data]
sandbox_os: linux_bwrap
# [] is a CPU-only starter. GPU use needs exact Host UUID authorization too.
gpus: []
max_gpus_per_experiment: 1
bash:
  network_allowed: false
  deny_patterns: ["rm -rf /", "git push", "sudo", "pip uninstall"]
filesystem:
  write_only_within_worktree: true
  # Add existing project-relative protected paths (for example data/) as needed.
  protected_paths: [.git/]
```

| Field | Meaning and accepted values |
| --- | --- |
| `allow_read_paths` | List of existing absolute paths to data and other approved read-only inputs. Use an empty list when no additional data is needed. Never include credential directories. |
| `sandbox_os` | Keep `linux_bwrap` for the supported Linux bubblewrap sandbox. |
| `gpus` | `[]` means CPU-only. `auto` uses the Host-authorized inventory; a list narrows it to GPU UUIDs or nonnegative integer indices. Prefer stable UUIDs. This field never grants devices by itself. |
| `max_gpus_per_experiment` | Positive per-experiment GPU-count ceiling; `1` does not enable GPUs when `gpus: []`. |
| `bash.network_allowed` | Boolean controlling network access for the research shell. `false` keeps it disabled; Host-managed literature access is separate. |
| `bash.deny_patterns` | List of valid regular expressions used to reject shell commands. Retain the template restrictions and add task-specific rules as needed. |
| `filesystem.write_only_within_worktree` | Keep `true` to express worktree-only writes. Setting `false` does not grant additional writable sandbox paths. |
| `filesystem.protected_paths` | Existing project-relative paths that must remain protected, such as `.git/` and optionally `data/`. The repository metadata remains protected. |

GPU use also requires an appropriate runtime, workload memory settings and
explicit device approval during init. Data and source-repository read access
does not authorize writes to their originals.

## Credentials and local checks

Fill secrets in a local editor, only in your ignored Profile. Keep files as
regular non-symlinks with mode 0600, and the directory mode 0700. Store them outside
Agent/experiment data and runtime mounts. Do not pass secret values through
`--set`, shell arguments, conversations, public templates or issue attachments.
Share a blank template when reporting configuration problems.

The Host reads literature/notification credentials from the original private YAML;
secret values are omitted from admitted research contracts and Agent inputs.
Keep referenced files readable for delivery and recovery. Existing
`credentials_file`, dotenv and explicit notification `*_env` sources remain
supported: use one credential source per service, removing old source declarations
when adding inline values. The [dotenv example](hypoloom.env.example) is for that
compatibility path. Preserve files still referenced by existing runs.

Model authentication uses the selected Claude settings or environment, not these
six files. See [Setup](../SETUP.md) and [API recovery](../docs/guides/provider-recovery.md).
Optional institutional browser sessions use a private Docker volume; see the
[server browser configuration](institutional-browser.compose.yaml) and
[institutional access guide](../docs/guides/institutional-access.md).

To inspect locations and permissions without printing secret values:

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap paths --profile my-topic
```

`profile_credentials` identifies the YAML locations. The `credentials` and
`credentials_template` entries refer to the older dotenv locations.

## Initialize the prepared Profile

Start `claude` from the repository root, then enter:

```text
/autoresearch init
```

Select the existing `my-topic` Profile. Review its effective models, paths,
credentials, CPU/GPU allocation, time/cost limits and notification recipients.
Choose **Prepare only** to save/review configuration, or **Start now** to launch.
Configuration checks do not make a paid model-generation call. Init also supports
guided creation of a new local Profile; it does not overwrite an existing one.

Profile edits configure new runs. They do not replace the configuration already
accepted by a running research task. See [initialization](../docs/guides/initialization.md)
and [Usage](../USAGE.md) for launch review and operator actions.
