# Profile templates

English · [简体中文](README.zh-CN.md)

A Profile defines a research topic in six YAML files. Copy a template into
`profiles/local/`, configure that private copy, then select it in `/autoresearch init`.
The public templates use `schema_version: 4`. See the
[personal configuration guide](../config/README.md) for copy commands and every field.

| Template | Language | Expert perspectives |
| --- | --- | --- |
| [multi-expert-en](templates/multi-expert-en/) | English | Optimization, architecture and evaluation |
| [multi-expert-zh](templates/multi-expert-zh/) | Simplified Chinese | Optimization, architecture and evaluation |

## Files

| File | Contents |
| --- | --- |
| `goal.yaml` | Research goal, constraints and completion criteria |
| `experts.yaml` | Expert roles, model overrides and branching limits |
| `methodology.yaml` | Runtime paths, literature, research controls and budgets |
| `language.yaml` | Prompt, report and code-comment languages |
| `notify.yaml` | Notification channels, recipients and credentials |
| `safety.yaml` | Allowed data paths, GPU selection and sandbox settings |

Keep exactly these six regular, non-symlink UTF-8 files in the Profile directory.
Unknown fields, duplicate YAML keys and unresolved runtime paths fail validation.
Store explanatory documents elsewhere.

## Create a local topic

Use the [English configuration guide](../config/README.md) to copy and edit the
English template. Replace these placeholders with real absolute paths:

- `methodology.runtime.target_repo`: a clean Git repository containing the research code.
- `methodology.runtime.conda_env`: a complete experiment environment.
- `safety.allow_read_paths`: the required data and other read-only inputs.

When a suitable benchmark project already exists, use it as the codebase for
the experiment workspace through `methodology.runtime.target_repo`. Prefer a
repository with data loaders, baseline implementations, experiment entry points,
evaluation scripts and dependency instructions. Confirm the baseline runs in the
environment selected by `methodology.runtime.conda_env`, and describe the baseline,
dataset, metrics and intended improvements in `goal.yaml`. Reusing this setup
reduces preparation work; keep the evaluation protocol consistent when comparing
baseline and revised results. Commit the intended source changes and leave the
Git working tree clean before initialization.

Then select the prepared Profile in `/autoresearch init`. Init can also guide
creation of a new local Profile; it does not overwrite an existing one.
Experiments use private worktrees; the original repository and approved data remain
read-only. Both templates inherit the Host model chosen during init. Set
`experts.defaults.model` or a role-specific model only when you intend an override.
The interactive session's `/model` selection must be passed to the Host explicitly.
See [initialization](../docs/guides/initialization.md).

## Private credentials

Fill credentials only in your ignored local copy. Literature credentials belong
in `methodology.yaml.literature`; notification tokens and recipients belong in
`notify.yaml`. Quote credential strings, including numeric chat IDs. Keep files
mode 0600 and the directory mode 0700. Model authentication is separate.

The Host reads credentials from their original private files; secret values are
omitted from Agent inputs. Do not pass secrets through `--set`, shell arguments,
chat, public templates or issue attachments. Retain files still referenced by
existing runs. See [personal configuration](../config/README.md).

## Budgets and GPU use

Configure resource, time and cost limits under `methodology.yaml.budget`:

| Setting | Meaning |
| --- | --- |
| `total_budget_usd`, `per_expert_budget_usd` | Run and expert cost limits |
| `wallclock_hours` | Run time limit in hours |
| `experiment_timeout` | Per-experiment time limit in seconds |
| `agent_invocation` | Per-call limits |
| `max_active_agents`, `max_active_processes`, `max_active_reviews` | Independent concurrency ceilings |
| `provider_rpm_limit`, `provider_max_in_flight` | Request rate and concurrency per model |

The templates have three experts, a 45 USD run limit, 15 USD per expert,
1 USD per invocation, at most four research rounds per expert and a six-hour run
limit. These are editable thresholds, not expected costs or permission to start.
In-flight responses can exceed the remaining allowance. Reserve room for analysis,
reports and retries. Agent and experiment deadlines are independent.

Templates default to CPU with `gpus: []` and Telegram disabled. GPU use requires
both a local Profile change and explicit device authorization. Shared pools also
apply workload floors, live capacity checks and reserves. Configuration changes
apply to new runs. See [GPU resources](../docs/guides/gpu-resource-planning.md).

## Research controls

Limits are ceilings, not target counts. `max_hypotheses_per_round` bounds
candidates, `max_forks` bounds active research chains, and `max_active_agents`
also covers proposal arguments and readers. Parallel FORWARD/BACKWARD arguments
support one proposal; they are not two hypotheses.

Leader shares evidence-based advice while peers continue independently.
`interaction.leader.min_rounds_before_leader` counts completed expert cycles,
not experiments. Advice does not grant extra cycles or resource access.
Execution can return to Host waiting after submission and continue after results
arrive; both model invocations count toward the normal budget.

See [research workflow](../docs/guides/research-workflow.md) for the stages and
[context and memory](../docs/guides/context-management.md) for session limits.

## Selective paper reading

Experts can read retained paper sources and extracted text. To allow independent
reading assistants, add `read_papers` to the existing tools list before starting:

```yaml
literature:
  enabled: true
  tools: [web_search, web_fetch, read_papers]
  max_sources: 8
```

This is a fragment; preserve the other fields and credentials. Readers share Agent
slots and research budgets. A waiting parent still occupies its slot, so capacity
may require serial reading. The expert remains responsible for checking and
integrating findings. Public templates leave this option disabled. See
[reading and proposals](../docs/guides/reading-and-proposals.md).

## Goal completion

The default `metric: null` lets research establish a metric. Under the default
shared-goal policy, an accepted `SUPPORTED` result can finish the run. For a common
evaluator across experts, set `evaluation: grounding` in `goal.yaml` and describe
the full requirements. One complete `GOAL` candidate must satisfy them; partial
`LOCAL` results cannot be combined to declare success.

## Dashboard port

The templates use `notify.yaml.sinks.dashboard.port: 0`, which selects a free
loopback port. The startup log shows the actual bearer URL. Keep the token private
and use the new URL after restart. Set 1–65535 only when a fixed port is required;
a conflict is reported and retried on that port. The TUI has its own listener,
configured with `monitor --web-port`.

## Command-line preparation

Use the control environment's bootstrap helper: `paths` displays locations and
permissions, `prepare` previews a new Profile, and `launch` previews its launch
arguments. `prepare --write` creates a new private Profile; `launch --start`
performs the reviewed managed launch. Neither overwrites an existing Profile or
starts a run from a preview alone.

This example only previews configuration; replace the paths:

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap prepare my-topic \
  --template multi-expert-en --run-id my-topic-preview --cpu-only \
  --set 'methodology.runtime.target_repo=/absolute/research-target' \
  --set 'methodology.runtime.conda_env=/absolute/research-runtime' \
  --set 'safety.allow_read_paths=[/absolute/research-data]'
```

Do not include credentials in `--set`. Resolve reported blockers and review the
complete configuration before launch. Local validation does not prove model
availability. Use `/autoresearch init` for guided setup, then choose
**Prepare only** to retain the configuration or **Start now** to launch.
