---
name: autoresearch
description: Bootstrap local Hypoloom paths or operate the independent Workflow Host. Routes /autoresearch to CC-only init, read-only component-profile admission, exact run creation, status, monitor, explicitly authorized notifications, retained module debugging, oracle, cancellation, backup, or offline restore. Never falls back to the retained legacy runtime.
argument-hint: "[init|monitor|status|oracle|cancel] [run-id]"
---

# /autoresearch — bounded Hypoloom operations

Use this skill only for the independent `hypoloom` runtime. The user
invokes `/autoresearch <subcommand> [args]`. If the subcommand or an exact
target is missing, show the supported operations and stop, except that `monitor`
uses the read-only chooser below; do not infer a run from a legacy active pointer. `init` is Claude Code bootstrap orchestration; all other subcommands route to the existing Hypoloom CLI.

## Runtime boundary

ACI16 Grounding can persist a question in WAITING_HUMAN without an open model
invocation. The existing status/monitor JSON exposes `grounding_dialogue` with
exact decision ID, question and answer history. Obtain a real user answer; never
infer it from an expert note or silently accept an offered option. The new
`hypoloom.cli answer` command requires the selected state root, run ID,
decision ID, expected run revision and the exact free-text answer (see USAGE).
It uses the existing Host socket, never direct SQLite writes. A slash-command
alias is not provided by this notice. With v4 `goal.evaluation: grounding`,
Grounding writes/tests common acceptance in private `/acceptance`; expert nodes
read the same accepted source without write permission. LOCAL support never
completes this goal. One GOAL candidate must pass the common checks, independently
replayed by Oracle, without combining experts' partial scores. Admission or a
completed dialogue is not scientific success or permission to spend; see STATUS.

`HYPOLOOM_RUNTIME_FAMILY` is a fail-closed routing assertion, not a session
bootstrap requirement for this Hypoloom-only skill. At the beginning of every
invocation, inspect the inherited value without changing it. An absent value is
allowed for every operation in this skill. A nonempty value other than exactly
`hypoloom` stops every operation, including `init`; never reinterpret or override
a conflicting family.

Set `HYPOLOOM_RUNTIME_FAMILY=hypoloom` explicitly in the environment of every
Python helper, hook-independent check, and `hypoloom.cli` subprocess
started by this skill. Also pass the selected control Python, profiles root,
and state root explicitly to each command that needs them. A child process
cannot update the already-running Claude parent, but it does not need to: init
and the automatic flow complete in this same Claude interaction. Do not force
a relaunch merely to export runtime variables.

Use the qualified project interpreter, not an ambient base Python whose SQLite
may be too old. For a real local topic, the profiles root is `profiles/local`,
not the tracked template root:

```bash
CONTROL_PYTHON="$REPOSITORY/.conda/envs/hypoloom/bin/python"
PROFILES_ROOT="$REPOSITORY/profiles/local"
STATE_ROOT="$REPOSITORY/state/$RUN_ID"
test -x "$CONTROL_PYTHON"
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$CONTROL_PYTHON" -c \
  'import sqlite3; assert sqlite3.sqlite_version_info >= (3, 51, 3)'
```

Never fall back to another runtime or an old research repository. An unsupported
profile is a stopped Hypoloom admission. No legacy runtime bridge is provided.

## Bootstrap init — aggregate checks, one reviewed launch

`/autoresearch init` has no corresponding `hypoloom.cli init` subcommand.
Use the deterministic bootstrap interface below. Do not rediscover its checks
with grep, hand-written Python, repeated shell probes or source inspection.
Read [Initialization reference](references/init-details.md) only for a specific
compatibility blocker, custom source or new Profile authoring.

Inspection performs **zero writes**. Explicit institutional preparation may create
its temporary connection and control directory; research launch remains separate.
No Host, Agent, service, scope, or run exists because of inspection. Do not contact a model
provider, install dependencies, send notifications, or change existing Profiles,
research runs, credentials or project settings. Preserve an inherited nonempty
runtime-family conflict; never override it. Every Python invocation uses
`PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom`.

### 1. Inspect once

Resolve the repository with Git, then use its qualified control Python:

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$CONTROL_PYTHON" -B -m hypoloom.bootstrap inspect \
  --repository "$REPOSITORY"
```

Supply an already selected Profile and choices instead of asking again. For
example, append the Profile name, `--allow-gpu --managed-pool`, each approved
`--gpu-device-uuid`, and a previously selected `--claude-model`. Use `--cpu-only`
for an explicit CPU choice; `--nonpooled` selects nonpooled GPU scheduling.
Explicit external endpoints use the existing `--gpu-pool-socket`, namespace
and server-UID options. None of these modes is a silent fallback.

Inspection discovers paths, authentication/model defaults, the project pool,
GPU inventory and supervisor readiness. It admits the selected Profile and
validates complete launch choices with the existing validators. It performs
no paid probe. Output is a compact summary, missing choices, blockers, phase
timings and, for complete choices, a `launch_argv` array. Do not print whole
populated Profiles, settings, token values or raw command environments.

`--offline` skips the advisory remote version reminder. Remote comparison is
not an execution blocker. Tool admission/auto-mode classifier timeouts occur
before the helper runs; report them separately from helper phase timings.
After a repeated identical admission timeout, retain the current choices and
explain the unavailable tool approval service. Do not disguise the command,
disable a Hook, expand permissions or loop through equivalent shell forms.

### 2. Fill only missing choices and review

**Institutional access choice.** If `missing` includes `institutional_browser_mode`,
ask using `choices.institutional_browser_mode`, translating descriptions for the user.
Do not infer off from silence or placement from the server OS; if needed, ask enablement and placement separately.
Return one explicit mode and retain prior choices/identity. Off keeps public/OA retrieval, disabling institutional acquisition.
A supplied legacy URL answers placement and retains unmanaged behavior. Use `launch_argv` only after all missing choices resolve.

**Publisher selection requires an interactive answer.** When `missing` includes `institutional_prepare_provider`,
use `AskUserQuestion` with `choices.institutional_prepare_provider`; show IEEE, ACM, ScienceDirect and Springer explicitly.
Allow multiple publishers/custom catalog choices or skip. Recommendations are suggestions, never an implicit answer.
Choosing client mode, adopting a Profile or using a previous run's list does not confirm the current list.
Carry an already explicit answer through repeated `--institutional-prepare-provider`; `none` means skip. Do not ask again.
The program withholds preparation/launch commands until the list is supplied. Never fill it from defaults or bypass inspect.
Unselected institutional routes stay disabled; public retrieval is independent. For unattended stability, use
`--institutional-prepare-failure-mode public_only`; see [details](references/init-details.md).

Re-inspect with the same identity. Run returned `init_session_argv` unchanged in the background;
it owns client preparation, policy merging, final review and explicit launch. Otherwise use
`institutional_preparation_argv` with `--agent-session`. Relay `instructions_argv` output verbatim.
Use `status_argv` for current `reply_argv`; append the exact offered reply with `--text`,
and `--sample` only for sample verification. Never use background stdin.
Connection checks advance automatically; retries never extend deadlines. Arm one generated `watch_argv`
after connection instructions/platform replies; never overlap observers or infer answers.
At `status.state=awaiting_launch`, preparation is complete. With `launch_review`, relay current instructions
and immediately ask fixed Start now / Prepare only choices (plus retry if offered). Do not write a scratch reader,
reread notification files, merge modes, re-inspect or compose a second review. Submit the user's exact `reply_argv --text`;
the program launches only after Start now. Do not also run `launch --start`. Read background `bootstrap_result`;
`launching` is processing, not a question. Rejected/expired replies authorize neither a separate launch nor a new identity.
Without `launch_review`, read `summary` and enter the legacy separate review path.
Never wait for process exit before asking; the review retains the connection at most 600 seconds.
On an offered retry, execute `remaining_preparation_argv` unchanged: same-root `preparation retry`,
preserving identity/results. Prepare only/cancel must execute returned `finish_argv`; expiry never starts research.
Explicit finish/skip during preparation releases scope directly; read its final background result.
Only explicit preparation opens the browser; it authorizes no Host/run, Profile writes or model calls. SSO/MFA stays in the browser.
Show `preparation_summary`: no sample means not_checked; sample_verified proves only paper readability, not login/subscription.
Report public PDF, browser PDF or HTML accurately.
Fixed-sample challenges fail and advance; never require per-paper solving or call HTML a PDF.
Only for the separate preparation path, merge `provider_modes` by platform, replacing old values; re-inspect and review without replaying
completed/skipped preparation. Missing access never blocks research. Review auto/public_only/disabled
and finite 120-second acquisition / 600-second manual defaults. TUI observes; optional manual controls use authorized Web.

Enabled `--institutional-browser-mode client|local|existing|server` selects run-owned connections.
For Windows client mode, show the exact program-generated Setup/Connect entry for this init.
Transfer the original script through SFTP/SCP; never reproduce its body. Preserve `-StateRoot`,
`-RunId`, `-RequestId` and supplied `-PreparationRoot`. The user executes it once on the chosen PC,
then returns here. No logon trigger, task restart or future-run discovery; no terminal must stay open
except foreground Run diagnostics. Never substitute Init inside an existing init. Local execution
and SSH authentication remain prerequisites; waiting_for_client does not prove an absent helper.
The helper follows only this preparation/run, closes its own SSH/browser at STOPPING/termination,
and preserves login data. Continuous lost contact for 300 seconds ends it locally; wake checks expiry
before reconnecting. A new init needs new authorization; old tasks/browsers cannot adopt another run.
`local` uses the Linux user's visible browser over a pipe; check X11/Wayland. `existing` wraps
an explicit external endpoint; `server` uses the installed image with private per-run relays.
Preview exposes policy, read-only `institutional_connection_argv` and a separate preparation command;
it starts no resources. Host allocates ports after creation and releases owned connections on exit.
Closing CC/interactive SSH or monitors does not stop managed research; missing client access uses
bounded acquisition/OA fallback. Same-version recovery preserves frozen configuration; no run migration.
Do not share active local/server profiles, install dependencies, pull images or replace external browsers silently.
Connectivity proves neither visible GUI nor subscription. Legacy endpoint/manual commands remain
operator-owned; do not use detached manual startup for a new managed run. See details and the institutional guide.

Ask only about returned missing fields or requested changes; group related choices. Show proposed model/auth as defaults,
not inherited from interactive `/model`; explain actual Profile model/effort overrides in `effective_configuration`.

Capture the proposed run ID/state root/request ID once from `identity`; pass
those same values on subsequent inspection. Never recapture a timestamp to
bypass a conflict. A previous GPU selection is only a suggestion; the current
review must explicitly authorize the UUID subset. An inventory grants no access.

Adoption keeps the existing local Profile untouched. For a new Profile use
`bootstrap prepare --template ... --set ... --summary` and the existing six-file
format. Public templates contain no secrets. The user fills credentials locally.
Never put them in `--set`, arguments or chat. Read the detailed authoring
reference for this branch; do not migrate a retained Profile or run.

**Shared pool.** `--managed-pool` selects the project's canonical
`.local/gpu-pools/shared/control.sock`. A compatible pool is reused even when
its members exceed this run's subset. Busy devices are a capacity wait.
A missing or outdated pool can be prepared in this init. Review the exact
`--pool-action` (create/reuse/restore/upgrade/reconfigure), full
`--pool-device-uuid` membership and `--pool-reserve-mib` returned by inspection.
Upgrades preserve existing configuration; membership changes require explicit
reconfiguration. Paused, CPU, Grounding and API-waiting runs remain dependencies.
Unknown ownership blocks automatic replacement. External pools are only checked
and reused. Different private pools must not coordinate the same shared device
set independently. The canonical managed pool requires repository-local state
roots; external roots use an explicitly configured external pool.

Distinguish `start_ready` from `prepare_available`: a safe creation/upgrade plan
can offer Start now before the service is ready. For blockers, keep this init
open: recheck, revise affected choices, Prepare only or cancel. Preserve prior
answers and do not ask the user to start the whole conversation again.

Use the session's program-rendered review when available; otherwise use one final `AskUserQuestion` for the concrete effective configuration,
bootstrap writes, pool operation and research launch:

- **Start now (Recommended)** when all prerequisites either pass or have the
  displayed executable managed-pool plan. Execute only the reviewed actions.
- **Prepare only** — only reviewed Profile/settings preparation; no pool
  creation, stop or upgrade, no Host, state root, run or provider call.

Rejection/cancellation before execution makes zero bootstrap writes; release preparation via `finish_argv`.
After an authorized operation has partly executed, report actual changes;
never claim it made no writes. Existing settings stay byte-for-byte unchanged.

### 3. Execute the generated arguments

For a new Profile, first perform the reviewed `bootstrap prepare --write`, then
inspect the written Profile without `--template` or `--set`. An actual mismatch
requires review. For adoption, there is no preparation write.

For an active `init_session_argv` session, use its live fixed replies and read
`bootstrap_result`; never execute an additional launch command. The following
direct launch steps apply only when there is no active program-owned session.
Use the returned `launch_argv` values without reconstructing Host commands.
They are an argv array, not executable shell text; if invoking through Bash,
quote each argument normally. Never write a scratch launcher, manifest, receipt
or second configuration file. Do not use eval or substitute secret values.
Only after Start now approval append `--start` to that exact bootstrap launch.
Do not separately start a GPU service or Host.

Bootstrap rechecks admission, performs the reviewed pool action, verifies the
service, starts the managed Host, rechecks the Profile and creates the run with
its stable request ID. The Host repeats pool verification under admission
coordination. Report the returned status and phase timings. `started` is the
only successful research-start result; preparation alone is not a research run.
After `started`, always include the returned `monitor.cc_command` and complete
`monitor.terminal_command`. These are optional observer entry points; do not
launch a monitor automatically. Explain that `/autoresearch-monitor` can find
existing runs again after CC restarts. Do not promise gray input suggestions.

A preparation failure stays in this init and uses returned `next_actions`.
A healthy shared pool is retained if the subsequent Host launch fails.
`creation_uncertain` requires the existing STATUS reconciliation; never invent
another request ID or automatically rerun `--start` against an existing root.
Existing managed cleanup applies only to effects this launch owns.

For normal existing Profiles, aim for one aggregate inspection, at most one
combined missing-choice question, one final review and one launch call. The
performance target is at most six non-question tool calls/eight model responses,
not permission to omit checks or necessary user decisions.

## Supported routing

For `monitor`, including a missing run ID or state root, read the focused [monitor shortcut](../autoresearch-monitor/SKILL.md)
and use its existing read-only discovery/launcher flow. `/autoresearch monitor` remains supported.

For other non-init operations, read [Operations reference](references/operations.md)
before routing the exact user request. It defines status, monitoring, controls, notifications,
retained debugging, cancellation and backup boundaries. Never infer an existing run or mutate one merely because init is being performed.
