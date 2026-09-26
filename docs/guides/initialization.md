# Initialization and effective configuration

English · [简体中文](initialization.zh-CN.md)

`/autoresearch init` reviews a fresh run. `bootstrap prepare` fills and admits a
prospective Profile in memory; `bootstrap launch` produces commands and an
`effective_configuration` explanation. Neither preview starts a Host, contacts
a provider, sends a notification or edits an existing Profile/run. Review the
result before the existing Start now / Prepare only decision.

The initial `bootstrap paths` also includes a bounded
[version reminder](version-check.md) against the configured remote branch.
It is advisory and does not block initialization; `--offline` skips remote lookup.

## One place for budgets

Keep the six Profile files. New templates put financial and time limits in
`methodology.yaml.budget`, alongside invocation, retry and concurrency limits:

```yaml
budget:
  total_budget_usd: 45
  per_expert_budget_usd: 15
  wallclock_hours: 6
  experiment_timeout: 1800  # seconds
  agent_invocation:
    max_turns: 40
    max_tokens: 80000
    max_output_bytes: 262144
    wall_seconds: 600
    max_cost: "1"
    token_enforcement_mode: enforce
```

This is an excerpt, not a complete Profile. `goal.yaml` owns the scientific
target, acceptance and round limits; `safety.yaml` owns permissions. The compiled
runtime budget and its existing enforcement remain authoritative. Per-role
budgets may intentionally narrow or override their shared per-role default;
the total-run cap remains independent.

Older `safety.total_budget_usd`, `safety.per_expert_budget_usd`,
`safety.experiment_timeout_minutes` and `goal.stop_conditions.max_wall_clock_hours`
still work when the corresponding budget field is absent, with a migration
warning. Declaring a bound twice is rejected even when equal. For prospective
Profiles, move each old value to the budget field and remove its old declaration;
multiply timeout minutes by 60. Init does not rewrite files or migrate old runs.

## Actual Agent models

Agent stages resolve model and effort through the same function used by the
preview: stage override, expert override, Profile defaults, then the Host model
fallback. Shared Grounding and Observer have their existing role-specific rules.
`effective_configuration.models.routes` shows each possible route, its value
and source, including conditional roles. It is not a schedule of future calls.
`profile_models_overriding_host_default` highlights models that differ from the
Host selection. An absent effort uses the native CLI default, not an interactive
session setting.

The model selected in an interactive Claude session is independent. A settings
file's configured default does not prove that session's current `/model` choice.
To use one model everywhere in a new Profile, review its defaults and all explicit
overrides together. To use multiple models, retain the intended overrides and
review the resulting routes. Each actual Agent pins its resolved model.

## Local launch checks

The preview checks the API URL, authentication shape and supported relay mode
with the relay's own local validator. No paid probe is performed; a passing
configuration does not prove model availability, remaining quota or connectivity.

GPU launch requires the Host runtime to match the admitted Profile runtime.
The selected UUIDs must exist in the read-only inventory; Profile GPU selectors
must resolve inside that authorized set. A GPU launch with `safety.gpus: []` is
rejected. Shared-pool budget rules use the Host's existing validator. These
checks neither allocate GPUs nor infer permission from free capacity. Temporary
resource contention remains a runtime wait, not permission to switch devices.

## Managed notifications

Fill the private Profile's `notify.yaml` directly; no separate env is required:

```yaml
sinks:
  telegram:
    enabled: false  # Enable after reviewing the recipient.
    bot_token: ""   # Fill locally; quote both values.
    chat_id: ""
```

Optional fields are `sinks.webhook.url` / `token` and `proxy_url`.
Keep populated files mode 0600 and outside Agent/experiment mounts. Admission
strips the values and derives references to the original YAML; the Host reads
that file with the existing private-file validator. No copied secrets or extra
credential file is generated. Fill literature keys in `methodology.yaml` as
shown in [Personal configuration](../../config/README.md). Model auth remains
the selected CC configuration. Prepare first, edit locally, then repeat init.
Never use `--set`, command arguments, chat or public templates for actual values.

The managed Host does not inherit interactive notification secrets. Old
`credentials_file`/environment inputs remain compatible, including the optional
`--notification-credentials-file`; these are unnecessary for an inline Profile.
Do not mix sources for one service. `--notification-output-root` still selects
an absolute separate delivery directory. Direct serve/notify retain explicit
environment compatibility. Existing runs and their source files are not migrated.

Before launch, enabled sinks reuse their runtime credential and URL validation;
the proxy, output directory isolation, log path and dashboard loopback address
are also checked without delivery or listening. Missing input blocks launch.
An occupied fixed dashboard port is still a dynamic runtime error; prefer port
0 unless a fixed port is required. See [Notifications](notifications.md).

The explanation is derived output, not a saved launch manifest or a new source
of authority. The six Profile files, ordinary Host options and existing readers
remain the inputs. Existing runtime protections remain in place after preflight.
