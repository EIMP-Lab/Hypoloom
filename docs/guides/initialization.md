# Initialization and effective configuration

English · [简体中文](initialization.zh-CN.md)

See [runtime recovery](runtime-recovery.md) for ACI 28 pool preflight, node-scoped GPU recovery and context/progress observations.

`/autoresearch init` reviews a fresh run. `bootstrap prepare` fills and admits a
prospective Profile in memory; `bootstrap launch` produces commands and an
`effective_configuration` explanation. Neither preview starts a Host, contacts
a provider, sends a notification or edits an existing Profile/run. Review the
result before the existing Start now / Prepare only decision.

Both `bootstrap inspect` and the retained `bootstrap paths` include a bounded
[version reminder](version-check.md) against the configured remote branch.
It is advisory and does not block initialization; `--offline` skips remote lookup.

## Aggregate inspection and one launch

The normal init path uses one compact, read-only inspection:

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
  .conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap inspect my-topic --cpu-only
```

Omit the Profile or execution mode to discover choices. For shared GPU scheduling,
replace `--cpu-only` with `--allow-gpu --managed-pool` and repeat
`--gpu-device-uuid` for the explicitly approved UUIDs. `--nonpooled` is an explicit
alternative, subject to the existing Profile budget rules. Model/auth defaults
are proposals from the selected CC source; review Profile overrides and supply
`--claude-model` when another selector was chosen.

Inspection returns missing choices, blockers, effective configuration, phase
timings and, for a prepared Profile, a `launch_argv` array. Run inspection with
the selected control Python so its dependency check describes that interpreter.
Keep its run ID, state root and request ID on
subsequent checks. Review the array as arguments, never eval JSON or paste secret
values into it. Only after Start now approval append `--start` to that same
launch command. Bootstrap generates the Host/CLI arguments and performs the
reviewed pool preparation before creating the research run. Prepare only never
starts, upgrades or stops a pool. New Profiles still use the reviewed
`bootstrap prepare --write`, followed by inspection of the written Profile.

`prepare --summary` and `launch --summary` omit raw candidate files, the complete
admission payload and repeated command vectors. Full output remains available
without that flag. Local validation does not establish provider availability.
Stage timings measure helper work; tool approval delays, user waits and model
responses are separate and are unknown unless recorded by the client.
Repeated CC auto-mode classifier timeouts should be reported as tool-admission
failures, not retried through increasingly complex shell commands.

The compact skill loads detailed compatibility/operation references only when
needed. It retains the existing validators and final pre-start checks; no saved
init manifest or second configuration source is introduced.

After a confirmed `launch --start`, both full and compact results include
`monitor.cc_command` and `monitor.terminal_command`. Init displays these complete
observer commands for reuse; it does not automatically open a TUI. Prepare only,
a failed launch or uncertain creation must not claim a run was started or return
these post-start entries. After restarting CC, use `/autoresearch-monitor` to
select from existing stores; see [monitoring](../../USAGE.md#tui-web-and-human-decisions).

Institutional access is an explicit init choice. When omitted, inspection returns
`institutional_browser_mode` in `missing` and descriptions in
`choices.institutional_browser_mode`. The conversation asks for that choice before
inspection provides launch arguments. Previously supplied choices are reused:

| Choice | Value | Behavior |
| --- | --- | --- |
| No institutional browser | `off` | Keep public/OA retrieval; disable institutional-panel operations. |
| Native Linux desktop | `local` | Open a dedicated browser on the project's Linux desktop; no SSH tunnel. |
| Windows PC | `client` | Use local Edge/Chrome and the helper's run-scoped SSH forwards. |
| Server container | `server` | Use an installed image and a private per-run GUI forward. |
| Existing external connection | `existing` | Use an explicitly supplied external CDP endpoint. |

Pass the answer through `--institutional-browser-mode`, for example
`--institutional-browser-mode off`. Omission does not count as choosing off.
A legacy URL-only input already answers the choice and retains its unmanaged
behavior. Existing direct launch/serve scripts may still omit the mode. Off rejects
conflicting connection or institutional-specific options; remove those options
when changing the selection.

Enabled reviews expose `institutional_preparation`, with IEEE, ACM, Elsevier /
ScienceDirect and Springer first. The default batch prepares IEEE, ScienceDirect
and Springer; ACM is public-first with optional login. Choose the default batch,
a custom list or skip, without entering each publisher URL. Repeated
`--institutional-prepare-provider <platform>` values replace the default list;
`none` skips preparation. `Elsevier` aliases `ScienceDirect`. Choose the browser
and optional interactive library URL once. Reinspection preserves selections;
the preparation list does not restrict research. Merge public-only choices from
the preparation result back into platform arguments and re-review. Unverified
or unavailable platforms do not prevent research startup.

Inspect and launch preview share the existing validators without contacting a
browser or allocating ports. After Start now, the Host allocates resources for
the run; missing connectivity/login uses bounded acquisition and OA fallback.
An explicit standalone preparation choice can handle logins first; preparation
does not save the mode for future runs. Init uses a background `--agent-session`:
connection readiness precedes platforms, and finish/verify/skip answers return
through the init conversation, without background stdin. Windows terminal users
can use the unified local `Init` entry to start discovery and remote init together;
standard paths are derived and no separate helper terminal is needed. First local
entry setup and SSH authentication remain necessary; server-only init cannot start
an unconnected PC. See the
[institutional guide](institutional-access.md#prepare-the-connection-during-init)
for commands, stopping and qualification limits. SSO/MFA remains human-operated.

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

The English and Chinese templates omit `experts.yaml`'s `defaults.model`.
Without explicit model overrides, all Agent routes use the Host model selected
at initialization. To use the interactive session's model, pass its reviewed
selector as `--claude-model` when preparing the launch and check the effective
routes. Add a Profile model only when an override is intended.

Existing local Profiles are not rewritten. Before creating a new run from an
older Profile, remove `defaults.model` if it should follow the Host selection,
and review any expert, stage or shared Grounding overrides. Do not replace the
field with an empty string or `null`; omit it. Existing runs keep their admitted
configuration.

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


Notification preflight validates local configuration only. An enabled Telegram sink
is **delivery unverified** until an actual response is observed. See [notification
checks and recovery](notifications.md#delivery-health-and-recovery--投递状态与恢复)
for explicit access/message tests and live Web/TUI status.
