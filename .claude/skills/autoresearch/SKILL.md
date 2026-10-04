---
name: autoresearch
description: Bootstrap local Hypoloom paths or operate the independent Workflow Host. Routes /autoresearch to CC-only init, read-only component-profile admission, exact run creation, status, monitor, explicitly authorized notifications, retained module debugging, oracle, cancellation, backup, or offline restore. Never falls back to the retained legacy runtime.
argument-hint: "[init|monitor|status|oracle|cancel] [run-id]"
---

# /autoresearch — bounded Hypoloom operations

Use this skill only for the independent `hypoloom` runtime. The user
invokes `/autoresearch <subcommand> [args]`. If the subcommand or an exact
target is missing, show the supported operations and stop, except that `monitor`
uses the read-only chooser below; do not infer a run
from a legacy active pointer. `init` is Claude Code bootstrap orchestration;
all other subcommands route to the existing Hypoloom CLI.

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
ask using `choices.institutional_browser_mode`, translating its descriptions for the
user. The code supplies off/local/client/server/existing; do not infer off from an
omitted answer or choose a placement from the server OS. If needed, split the UI
question into whether to enable access and where the browser runs, then return one
explicit mode to inspect. Preserve previously supplied choices and the same launch
identity. Only use the returned `launch_argv` once missing choices are resolved.
Explicit off keeps public/OA retrieval and disables institutional browser acquisition. A supplied
legacy browser URL already answers this choice and retains its unmanaged behavior.

**Optional institutional preparation.** Use the enabled preview's `institutional_preparation`.
Offer defaults (IEEE, ScienceDirect, Springer), optional ACM (public-first), custom platforms or skip. For an explicit unattended-stability priority, use `--institutional-prepare-failure-mode public_only` and the failure policy in [details](references/init-details.md).
Reuse choices; repeated `--institutional-prepare-provider` replaces the list, and `none` skips
preparation without limiting research. Re-inspect with the same identity. Run `institutional_preparation_argv` with
`--agent-session` in the background; run its flushed `instructions_argv` and show
the returned instructions/code block verbatim. Use `status_argv` for the current
`reply_argv`; append `--text` with the exact offered user reply, and `--sample` only
for sample verification. Connection checks advance automatically; retries never extend deadlines.
Immediately arm one generated `watch_argv` in the background after connection instructions and
platform replies; its completion supplies current choices. Do not wait for "connected", overlap
watches or infer answers. Ending preparation cancels the batch; it is not needed before testing.
For ended batches see [details](references/init-details.md); never invent commands or use stdin.
Only an explicit preparation choice opens the browser; it does not authorize a
Host/run, Profile writes or model calls. SSO/MFA stays in the browser, never chat.
Show `preparation_summary`: no sample means not_checked; sample_verified proves
only that paper's readability, not login or subscription. Missing access never blocks research.
Report public PDF, browser PDF or HTML. Fixed-sample challenges fail and advance; never require per-paper solving or claim HTML is PDF. See details.
Merge returned `provider_modes` by platform into launch choices, replacing old values,
then re-inspect and review. Retain completed/skipped preparation in this interaction;
do not rerun a batch merely because its command still appears. Review auto/public_only
and finite 120-second acquisition / 600-second manual defaults. During research,
observe Host status in the read-only TUI; optional manual controls use authorized Web, not another standalone browser manager.

The enabled `--institutional-browser-mode client|local|existing|server` selects
run-owned connections. Windows `client` uses the program's `Connect` code block:
download the local entry and start discovery, then return to this same CC session.
Never substitute `Init` while init is already open. First local execution and SSH
authentication remain necessary. Missing connections do not prove an absent helper.
`local` uses the actual Linux user's visible browser over a process pipe, without
SSH or Docker. Check the current X11/Wayland session. `existing` wraps an explicitly
managed external endpoint. `server` uses the repository image/environment with
private per-run container relays, not Compose's fixed published ports.

Preview exposes policy, a read-only `institutional_connection_argv` and separate
preparation command. It starts no resources. Host allocates ports using port 0 after
run creation and releases owned connections on run termination or Host exit;
pausing or closing a monitor does not end the run. Profiles stay browser-owned.
The helper discovers active runs and cannot reconnect terminated runs. Same-version
recovery rebuilds ephemeral addresses without changing frozen configuration.
Local/server profiles cannot be shared by simultaneous browser instances. Do not
install dependencies, pull images, replace external browsers or change placements
silently. Missing connections use existing bounded acquisition/OA fallback.
Browser connectivity does not prove GUI visibility, school login or subscription.
Endpoint-only and explicit manual `institutional connect` commands retain their
old operator-owned behavior; never invoke detached manual startup for a new managed
run. See the institutional guide for commands and machine-specific checks.

Ask only about returned missing fields or explicit changes the user requested;
group related choices. Proposed model/auth values must be shown as defaults,
not claimed to be inherited from the interactive `/model`. Explain any actual
Profile model/effort overrides in `effective_configuration`.

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

Use one final `AskUserQuestion` for the concrete effective configuration,
bootstrap writes, pool operation and research launch:

- **Start now (Recommended)** when all prerequisites either pass or have the
  displayed executable managed-pool plan. Execute only the reviewed actions.
- **Prepare only** — only reviewed Profile/settings preparation; no pool
  creation, stop or upgrade, no Host, state root, run or provider call.

Rejection or cancellation stops with zero bootstrap writes before execution.
After an authorized operation has partly executed, report actual changes;
never claim it made no writes. Existing settings stay byte-for-byte unchanged.

### 3. Execute the generated arguments

For a new Profile, first perform the reviewed `bootstrap prepare --write`, then
inspect the written Profile without `--template` or `--set`. An actual mismatch
requires review. For adoption, there is no preparation write.

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

For `monitor`, including a missing run ID or state root, read the focused
[monitor shortcut](../autoresearch-monitor/SKILL.md) and use its existing
read-only discovery/launcher flow. `/autoresearch monitor` remains supported.

For other non-init operations, read [Operations reference](references/operations.md)
before routing the exact user request. It defines status, monitoring, controls,
notifications, retained debugging, cancellation and backup boundaries. Never
infer an existing run or mutate one merely because init is being performed.
