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
alternative, subject to the existing Profile budget rules. Authentication and the
candidate model come from the selected CC source. Without an explicit
`--claude-model`, a fixed question offers that model or a freely entered exact
model ID supported by the provider. It does not silently accept the default.
An explicit selector is not asked again; settings files remain unchanged and
Profile role overrides remain effective. Final review lists literature routing
separately from Telegram and institutional-browser configuration.

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

After selecting devices, a differing shared-pool reserve prompts **Keep current
pool** or **Review reconfiguration**. Matching configuration adds no question.
Reconfiguration defaults to the full existing pool membership, independently of
this run's subset. Final review shows the pool-wide change and before/after floor;
the database and request history stay intact.

Missing-state owners are listed with UID, namespace, run ID, released/unreleased
request counts and process status. Only this account's absent runs with nonempty,
fully released request history and no recorded live processes can be reviewed.
The operator must also confirm retirement/deletion, no relocation and no future
resume; zero occupancy does not establish these facts. Exact
`--pool-retired-owner namespace/run_id` arguments carry that assertion for this
maintenance, subject to execution-time checks. They create no permanent exemption
and delete no history. Later maintenance needs its own review; ordinary compatible
reuse does not ask again. Retained runs, other UIDs, unreadable state and unreleased
requests remain protected. **Defer confirmation** retains `none` and blocks launch;
remove that deferred choice when reconsidering, preserving other init answers.

`pool_configuration_unknown` means the managed service's configuration could
not be read. Its `detail` identifies the failed stage; it does not establish
active dependencies or unreadable foreign GPU processes. The research shell
may deny the service's `/proc/<PID>/cwd`; inspection then checks that unit's
systemd working directory while retaining live process ownership/argument
checks. Recheck with `interaction.inspect_argv` to preserve the chosen reserve,
membership and run identity. Do not reset the pool choice unless revising it.
The existing question hook removes stray carriage returns from menu text;
already displayed menus refresh only when a new question is issued.

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

Init uses the fixed `interaction.questions` returned by inspect (`--ui-language en`
or `zh-CN`). CC relays these unchanged and saves the original AskUserQuestion
`answers` object in `.local/`. The generated `answer_argv` accepts that JSON path,
retains prior choices/identity, and performs the existing admission checks. Partial,
renamed, stale or contradictory answers fail without starting a run. Configuration
that is not a question is reported in `configuration_required`.

After enabling access, **confirm the publisher list in the interactive session**.
The catalog is shown in stable groups of up to three platforms, each with an
explicit “None in this group” option. All eight may be selected together; the program
processes them sequentially with at most eight owned browser tabs. Recommendations
never count as an answer; there is no model-designed batching step.
ACM is selectable even though its recommendation is public-first with optional login.
Choosing Windows client mode alone does not select publishers; no preparation or launch
command is generated until the list is supplied. Previously explicit answers are retained.
Repeated `--institutional-prepare-provider <platform>` values carry the exact list;
`none` skips preparation. Off needs no publisher question. Direct scripts and standalone
preparation retain their defaults. `Elsevier` aliases `ScienceDirect`. Choose the browser
and optional interactive library URL once. Reinspection preserves selections;
unselected institutional channels remain disabled, while public/OA retrieval stays available.
Windows client init merges public-only
choices into the launch arguments in its program-owned session. Standalone preparation
still requires carrying those choices into the separate launch review. Unverified
or unavailable platforms do not prevent research startup.

Inspect and launch preview share the existing validators without contacting a
browser or allocating ports. Start now allocates the run's resources; unavailable
institutional access uses bounded acquisition and public/OA fallback.
Init preparation uses a background `--agent-session`: the program checks readiness,
opens selected platforms and validates fixed replies for the current stage/provider.
It uses a generated `watch_argv`, not assistant-written polling or background stdin.

For Windows client preparation, inspect returns `init_session_argv`. CC starts that
single background program and relays its fixed instructions. After the last platform,
the program merges publisher results, checks the Profile and produces the full launch
review, including notification events and credential source paths. No notification
test is sent and private recipient/token values remain undisplayed. CC asks the fixed
**Start now / Prepare only** choice immediately; no extra inspect, custom reader or
manual policy merge is needed. The reply goes through the existing preparation
channel. Only an accepted **Start now** invokes managed launch; `bootstrap_result`
reports the result and monitor commands. Never also execute a separate `launch --start`.
Retries preserve completed publishers. A blocked or expired review cannot start a run.
Once Start now is accepted, the bounded launch phases retain the connection through
Host handoff; the 300-second lost-contact bound remains unchanged.

For each init in Windows client mode, run the exact generated Setup/Connect entry
once on the PC you choose. Transfer the original script through SFTP/SCP on first
use or upgrade; never execute assistant-transcribed installation source. The entry
carries this init's `-StateRoot`, `-RunId`, `-RequestId` and optional
`-PreparationRoot`. It starts a hidden one-shot task, with no logon trigger or
future-run discovery. Setup/Connect returns, so its terminal may close; only a
foreground Run diagnostic needs an open terminal. Return to the same CC init.

`status.state: awaiting_launch` means preparation is complete: read `summary` and
review immediately, without waiting for the background process to exit. The same
control root/helper waits at most 600 seconds for this run. Failed platforms use the
returned `remaining_preparation_argv` to retry in that root; successful results are
retained. Prepare only/cancel executes `finish_argv`; Start now still needs explicit
authorization. Expiry never starts research. Explicit finish/skip during preparation
exits directly and leaves its result in the background output. The final
`preparation_lifecycle` event records handoff, review expiry or explicit finish
separately from publisher sample results; a summary is not proof of helper liveness.
If the Windows helper exits, use its [local Status check](institutional-access.md#manual-connection-diagnostics)
first. It reads safe reasons without SSH, restarting the browser or foreground replay;
300-second control-check expiry does not itself mean the publisher page failed.

The helper closes its own SSH/browser on run termination or `STOPPING`, preserving
login data. Short disconnection can recover only to the same run; continuous lost
contact for 300 seconds causes local exit. After sleep it checks expiry before
reconnecting. Closing CC/interactive SSH does not stop already started research;
a missing Windows client does not block public fallback or other work. A new init
requires its own entry. See the [institutional guide](institutional-access.md#prepare-the-connection-during-init)
for upgrade/old-task cleanup, scope values and Windows acceptance limits. Server-only
init cannot start an unconnected PC; SSO/MFA remains human-operated.

On first use or after changing Windows PC/account, follow the
[SSH setup guide](institutional-access.md#first-setup-or-a-new-windows-pcaccount)
and verify that `BatchMode=yes` login returns `0`; password login alone does not
establish background helper access.

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

The final review also lists resolved context windows per model. New adapter-80
Agents import the selected settings' non-secret CC window/compaction controls;
an omitted Profile working limit follows the resolved window. If local window
information is insufficient, bounded read-only model metadata queries may run;
the review reports their count separately from generation probes. Missing
metadata falls back to labeled CC assumptions. See [context management](context-management.md)
for limits, sources and the `memory.context_metadata: false` opt-out.

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
If server-side Telegram delivery needs Clash, set top-level
`proxy_url: "http://127.0.0.1:7891"` in `notify.yaml` **before a new init**,
using the actual HTTP CONNECT port (an HTTP-capable `mixed-port` also works).
Here loopback means the Linux server, not your Windows PC. This setting is
independent of model and literature proxies. Existing runs and **Retry Telegram
delivery** do not hot-load a newly added or changed proxy. See the
[server-side Clash guide](notifications.md#server-side-clash-proxy--服务器-clash-代理).

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

Research sessions cannot edit framework source through file tools or ordinary shell
writes. The existing project hook wraps Bash/Monitor in Linux bubblewrap, mounting
the repository read-only except `.local/`, `profiles/local/` and `state/`. Direct
Write/Edit/NotebookEdit to state remain blocked. This does not auto-approve commands
or replace Host authority. A missing/unavailable bubblewrap is an explicit error,
with no unsandboxed retry. Use a separate authorized development session for repairs.
Restart CC after updating hook registration; stopped/historical runs are unchanged.
