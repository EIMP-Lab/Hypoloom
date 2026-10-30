# Running, monitoring and stopping

English · [简体中文](USAGE.zh-CN.md)

Complete [Setup](SETUP.md) first. Run the shell commands below in the control
environment, replacing `/absolute/state/run` and `RUN_ID` with the values returned
by initialization.

## Start and status

In Claude Code at the repository root, enter `/autoresearch init`. Review the
Profile, model, paths, budgets, resources and notification recipients. Choose
**Prepare only** to save the configuration or **Start now** to launch it.

Managed start uses `manage_host.py` and user-systemd. The helper checks
`control.sock` readiness before submitting the reviewed request. Each new run
uses a fresh state directory. If a reply is lost, check the original request
and run status before taking further action; do not create a duplicate run.

```bash
hypoloom status --state-root /absolute/state/run --run-id RUN_ID
hypoloom oracle --state-root /absolute/state/run --run-id RUN_ID
```

`status` queries the Host. `oracle` audits the recorded results and lifecycle
without changing research state. A stored `ACTIVE` status alone does not prove
that the Host is alive; check current Host and process observations as well.

## TUI, Web and human decisions

Use this Claude Code shortcut to select an existing run:

```text
/autoresearch-monitor
```

For an external state directory, specify it explicitly:

```text
/autoresearch-monitor RUN_ID --state-root /absolute/state/run
```

Inside tmux, the shortcut opens a TUI window. Otherwise it returns a command for
an interactive terminal. Multiple matching runs require your selection. To start
an observer directly:

```bash
hypoloom monitor --state-root /absolute/state/run --run-id RUN_ID
hypoloom web --state-root /absolute/state/run --run-id RUN_ID
```

Use `monitor --json` for a JSON snapshot or `monitor --no-web` to disable that
TUI's Web listener. Initialization returns reusable commands but does not open
the observer automatically.

| TUI action | Effect |
| --- | --- |
| **q / Leave monitor** | Close the observer; research continues. |
| **x / End run** | Confirm cancellation and wait for cleanup and the final-report attempt. |
| **h** | Read and answer pending Grounding questions. |
| **p / API recovery** | Inspect provider waits, retry or select a compatible connection. |
| **g / GPU resource recheck** | Recheck resource blocks after repairing the bound GPU pool. |
| **l / Literature retry** | Retry OpenAlex after resolving its access problem. |
| **n / Telegram retry** | Retry notification delivery with the current settings. |
| **b / Report Issue** | Preview and export a support bundle. |
| **? / Help** | View interface help and metric definitions. |

Use **CN / EN** in TUI or **中文 / English** in Web to change the interface language.
Research text, logs and identifiers retain their original language. The TUI
selection lasts for that session; Web remembers it in browser storage when available.

### Browser access

Web listens on loopback and selects a free port by default. Copy the current URL
and SSH command from **Web / local access** or the standalone startup log. Ports
and tokens can change after restart. Fixed ports are optional: use
`monitor --web-port 18080`, `web --port 18081`, or the Profile dashboard's `port`.
An occupied fixed port reports a conflict.

A Web listener opened by TUI stops when that TUI closes; the research Host continues.
Reopen the monitor for the same run to obtain a new URL. Ordinary share links are
read-only. Keep bearer tokens private.

`WAITING_HUMAN` requires a real user answer. `web --allow-human-answers` creates an
operator link for those answers; do not publish it. API recovery and GPU recheck
need a separate operator grant: TUI **p → Browser**, or `web --allow-provider-control`.
Human-answer permission alone does not authorize provider changes.

## Read research progress

The expert cards show current stages, hypotheses, experiments and research
coordination. **Expert details** separates proposal arguments, reading assistants
and experiment work; **Details & history** provides recorded evidence. See
[reading and proposals](docs/guides/reading-and-proposals.md).

In **Hypothesis lineage**, parent links describe revisions and pruning links
record control decisions. Pruning is not scientific refutation. In **Metric trends**,
GOAL measurements use the common acceptance criteria; LOCAL results retain their
own screening definitions. Missing measurements remain unknown. Scores from
different LOCAL experiments cannot be combined to satisfy a GOAL candidate.

For common acceptance, set `evaluation: grounding` in `goal.yaml` before starting.
One complete GOAL candidate must pass all accepted requirements. Without common
acceptance, the default shared goal can finish on an accepted SUPPORTED result.
After goal completion, the Host stops new research, cleans up work and prepares
the final report.

A completed Process is not automatically an accepted scientific result.
Likewise, `COMPLETED` does not by itself mean the goal was achieved. Review the
goal verdict, measurements and final report, including failures and missing evidence.
GPU authorization alone does not prove an experiment used a GPU.

### Usage and activity

Web and TUI distinguish reported tokens, observed unsettled usage and reservations.
The Web header's `≥` marks a known lower bound; missing usage is not zero.
Reservations contribute to budget limits but are not consumed tokens. Current
context usage and cumulative run usage are separate; see
[context and memory](docs/guides/context-management.md).

**Run trends** shows recorded concurrency and request rates. The **Time window**
navigator below the charts displays the full-run RPM overview. Drag either edge
to resize the interval, or drag its middle to move the window. Both charts and
local start/end inputs update together. Touch and arrow keys are supported;
Shift moves ten buckets, and **View entire run** resets the window. Presets and
precise date inputs with **Apply interval** remain available. The page shows the
timezone, recorded resolution and actual displayed range; complete overlapping
buckets are retained. Invalid intervals leave the previous view intact, and
empty ranges show a message. Hover or touch a chart to inspect a point, or use
arrow keys while it is focused. Trend refresh preserves the interval, inspected time
and unfinished input edits. Fixed metric cells prevent movement when RPM digits
or unknown states change. Reload the page to load these frontend updates.
Solid occupancy
lines show bucket-end counts; dashed lines show peaks. Request rates include
retries. Missing observations remain gaps, and historical activity does not
establish current process liveness. **Tokens by stage** shows usage and diagnostic
subsets; overlapping subsets should not be added together.

## GPU scheduling

GPU use requires approved UUIDs, a compatible runtime and matching Profile
permissions. Shared scheduling also requires a pool binding. Its members, live
capacity, reserve and workload estimates determine admission; an expert is not
permanently assigned to one card.

Initialization reviews any managed pool creation or maintenance before **Start now**.
**Prepare only** does not operate services. External pools are explicitly selected
and are not taken over. Changing Profile values does not change existing runs.
See [GPU resources](docs/guides/gpu-resource-planning.md) and
[runtime recovery](docs/guides/runtime-recovery.md).

## Ending a run

Prefer **x / End run** in TUI. For the CLI, read the current revision with `status`,
then submit a uniquely identified request:

```bash
hypoloom cancel --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-cancel-1 --expected-revision REVISION
```

An accepted cancellation and an exited Host are separate observations. Wait for
task cleanup and terminal state. A managed Host exits after terminal cleanup;
a manually started resident Host may remain available.

A timeout means the reply is unknown. Reconcile the original Request ID before
retrying; do not submit a second identity. `pause` and `resume` do not stop the
service or migrate a run. For `DISPATCH PAUSED`, inspect the cause: submitted
experiments may still be finishing. Do not edit SQLite to force progress.

## API waiting and recovery

Provider failures appear as a cooldown or an operator wait. Submitted experiments
can continue while model work waits. After resolving the cause, use TUI **p** or:

```bash
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID
```

Add `--settings /absolute/private/connection.json` to select a compatible API
connection while retaining the run's model. A retry request does not prove
recovery; an actual response does. See [API recovery](docs/guides/provider-recovery.md)
for the private file format, scheduled retries and authorization.

Literature and Telegram retries are separate actions. Notification retries do
not reload proxy settings; see [notifications](docs/guides/notifications.md).

## Report an issue

Preview local diagnostics and the scope of a support export:

```bash
hypoloom debug inspect --state-root /absolute/state/run --run-id RUN_ID
hypoloom debug export --state-root /absolute/state/run --run-id RUN_ID \
  --support-bundle --preview
```

To create an archive after review, replace `--preview` with
`--archive --output /absolute/new-support.zip`. Default exports filter research
text, local paths and free-form errors. `--include-private-data` includes additional
research content and is for an agreed private support channel only.

Inspect the actual archive before sharing. Issue descriptions are included in
public exports, and active-run evidence may change between preview and download.
Exports never upload automatically. Use `--recent-failures` or `--attempt-id` to
narrow details; `report.json` records missing or size-limited material.
See [feedback and diagnostics](CONTRIBUTING.md).

## Backup and same-version restore

`hypoloom backup` creates a consistent snapshot through the Host. `hypoloom restore`
writes into a new, absent directory while preserving the original runtime version.
Private model settings are supplied separately and are not included in the backup.
Use each command's `--help` for its required arguments.

Never overwrite an existing state directory. Updating the repository does not
upgrade an existing run, and cross-version migration is not supported.
