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

Before the user's final confirmation, perform **zero writes**. No Host, Agent,
service, scope, or run exists because of inspection. Do not contact a model
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

**Optional institutional preparation.** With a selected `--institutional-browser-url`,
the preview exposes `institutional_preparation_argv`. Offer the optional standalone
`python -m hypoloom institutional prepare` before research launch, adding the user's
selected `--provider` values and optional sample DOI/URL `--sample` values. Ordinary
inspection remains read-only. Only an explicit choice to prepare authorizes opening
the dedicated browser; it does not authorize creating a Host/run, changing Profiles,
or calling a model. The user enters SSO/MFA in the browser, never in chat. Preparation
does not need pending papers and can be skipped without blocking launch. Validate
sample full text separately from login; one sample never proves platform-wide access.
Review `auto`/`public_only` provider choices and finite 120-second acquisition /
600-second manual defaults with the launch configuration. During research use the
Host's TUI/Web controls, rather than a second standalone browser manager.

The optional `--institutional-browser-mode local|existing|server` also selects
connection preparation (default CDP `http://127.0.0.1:9222`). `local` means a native
Linux user desktop with a dedicated Chrome/Chromium profile and user systemd;
it requires neither SSH nor Docker. Check current X11/Wayland access, not merely
the installed OS. `existing` checks the selected endpoint, including a remote
client's tunnel. The shipped `connection_assets/institutional-connect.ps1` runs
on Windows to discover Edge/Chrome, use existing SSH keys and optionally install
a current-user logon task; server init cannot launch an unconfigured client PC.
`server` explicitly uses the repository Compose service and a locally available
image. Do not silently install dependencies, pull images, switch placements or
reuse a personal browser profile. See the institutional guide for setup.

Preview exposes `institutional_connection` and, for an explicit placement,
`institutional_connection_argv`. It starts no resources. After the user's selected
preparation or Start now action, the helper starts/reuses the connection before
creating a Host. On `institutional_connection_failed`, report the reason and offer
retry, revised connection or an explicit public-only launch (remove institutional
connection options). Do not silently change that choice. The Host never owns the
browser/tunnel lifetime. Browser connectivity does not prove visible GUI, school
login or subscription rights; ask the operator to confirm the visible window.

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
