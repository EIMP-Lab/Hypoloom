# Usage: running, monitoring and stopping

English · [简体中文](USAGE.zh-CN.md)

For new ACI-25 runs, see [context and memory](docs/guides/context-management.md):
current input estimate, last measured request and cumulative usage are separate. Capacity
recovery reconstructs a short session; API waits use the existing connection
controls. Exhausted nodes show their failure and cannot be reopened by API retry.

First-time configuration is in [SETUP](SETUP.md). After starting Claude Code
in the project directory, use `/autoresearch init`, choose a Profile and a
language, review the real paths, models, budgets, notification recipients and
CPU/GPU, then choose `Start now` or `Prepare only`. The default Run ID is
`<profile>-<YYYYMMDDHHMM>` and the Request ID is that identity plus
`-create-1`; every command targets an explicit state root and Run ID.

## Start and status

Managed start uses `.claude/skills/autoresearch/scripts/manage_host.py` with
user-systemd. It first verifies a read-only STATUS of the fresh root and
`control.sock`, then sends the explicit create request. If a reply is lost,
query and reconcile the original Request ID; never recreate a run with a new
ID. Existing runs are read-only objects; repairing the worktree does not
modify the runtime code a run preserved.

Replace the paths and IDs in these examples with the actual reviewed values:

```bash
hypoloom status --state-root /absolute/state/run --run-id RUN_ID
hypoloom monitor --state-root /absolute/state/run --run-id RUN_ID
hypoloom monitor --state-root /absolute/state/run --run-id RUN_ID --json
hypoloom oracle --state-root /absolute/state/run --run-id RUN_ID
```

`status` queries the Host; `oracle` is an offline read-only audit. A state
file may retain a historical ACTIVE record; that alone does not prove the
Host is still running. Diagnose from durable state, Host service state and
process checks together.

Real-Agent environment auth comes from the approved `ANTHROPIC_API_KEY`;
only an explicitly selected existing eligible settings file uses
`--claude-settings`, and that file must be mode-0600. An `apiKeyHelper`
cannot pass qualification, and interactive `/login` OAuth does not mean an
isolated session works; do not switch to `--bare` on your own. The Host's six
invocation budgets come from
`create_run_payload.research_contract.budget.agent_invocation`, including
`token_enforcement_mode`; there is no independently tunable second budget
source.

## TUI, Web and human decisions

In Claude Code opened in this repository, type `/autoresearch-monitor` and
select the skill from slash-command completion. It finds existing runs even
after CC restarts. Restart the CC session after updating if the new entry has
not loaded yet. The original `/autoresearch monitor` entry remains supported.

```text
/autoresearch-monitor
/autoresearch-monitor RUN_ID
/autoresearch-monitor RUN_ID --state-root /absolute/state/run
```

With no arguments it reads immediate `state/*/state.sqlite` stores. One valid
match from a complete scan can be selected automatically; multiple matches
require your choice. Use an explicit state root for external stores or duplicate
run IDs. The list shows durable state, not Host liveness. An unreadable store or
incomplete scan is reported rather than silently choosing another run.

Inside tmux the shortcut opens a new TUI window; outside tmux it returns the
complete command to paste into an interactive terminal. Add `--json` for the
existing JSON observer instead, or `--no-web` to disable the TUI's Web listener.
Init also returns a reusable CC entry and complete terminal command after a
confirmed Start now. Nothing starts the monitor automatically. Completion
covers the skill name, not dynamic run IDs or gray prompt suggestions; no CC
history/settings or separate active-run registry is modified.

- **q / Leave monitor**: leaves the current monitor window; the background
  Host and tasks continue.
- **x / End run**: opens an explicit cancellation confirmation; wait for task
  cleanup, the final report and the managed Host exit.
- **? / Help**: a brief introduction to the window and the reliability
  metrics.
- **b / Report Issue**: preview and export a support bundle.
- **h**: view and answer pending Grounding human questions.
- **p / API recovery**: view provider waiting, retry the current connection or
  select another compatible API using a private connection file.
- **g / GPU resource recheck**: after repairing the bound pool, recheck
  Host-observed resource blocks. See [runtime recovery](docs/guides/runtime-recovery.md).

Web / local access sits in its own connection-information area. Start the
Web observer:

```bash
hypoloom web --state-root /absolute/state/run --run-id RUN_ID
```

TUI Web, standalone Web and the Profile dashboard default to port `0`: the
OS assigns a free port when each listener starts, allowing multiple instances
on one server. Find the actual URL and SSH command in the TUI's
**Web / local access** panel, or the URL in the standalone/Host startup log.
The port and token may change after restart; use the new address.
For a fixed port, use `monitor --web-port 18080`, `web --port 18081`, or
`notify.yaml.sinks.dashboard.port`. An occupied fixed port reports a conflict
without switching ports; the TUI remains usable. `monitor --no-web` disables
only that TUI's Web listener.

The Web server opened by the TUI lives in that TUI process. Leaving or crashing
the TUI closes its Web listener; the independent research Host keeps running.
For an unexpected exit, inspect `<state-root>/diagnostics/host.jsonl` for
`phase: "tui"` exceptions, then reopen the monitor for the same run. After
reopening, obtain the current Web URL and SSH port from **Web / local access**.

By default it listens on loopback only and uses a bearer link. Ordinary share
links are read-only. `--allow-human-answers` explicitly creates an operator
link that can submit human answers; never publish that link. `WAITING_HUMAN`
needs a real user answer; suggested options cannot be submitted
automatically.

API recovery uses a separate operator grant: TUI **p → Browser**, or standalone
Web with `--allow-provider-control`. Human-answer permission alone does not
permit API switching. See [API recovery](docs/guides/provider-recovery.md).
The same operator recovery grant permits GPU resource recheck. API retry and
GPU recheck are separate actions and do not clear each other's wait.


Web/TUI report usage in **tokens**, separating reported usage, reservations and
unknown records. Model-price availability is not a runtime health signal.
The Web **Metric trends** section keeps a waiting message until published GOAL
measurements arrive. LOCAL screening charts appear separately, grouped by one
hypothesis and its recorded measurement definition; they never inherit GOAL
thresholds or combine different screening baselines. Running experiments appear
as scores only after their outcomes are published.

## Research loop and completion

For new ACI-26 runs, the Host can run an expert's FORWARD/BACKWARD arguments
concurrently and joins their accepted outputs before SYNTHESIS. Choose **Expert details** on an expert card to distinguish proposal
tasks, hypotheses and reading assistants, including rejected reading calls and
incomplete observation coverage. The overview keeps current work visible; the
details drawer also links to that expert's session history. See
[reading and proposals](docs/guides/reading-and-proposals.md) for tool examples,
capacity/branch limits and retry behavior.

Grounding reads the goal and data and establishes the research constraints.
Experts progress through LITERATURE_REVIEW, HYPOTHESIS_PROPOSAL,
EXPERIMENT_DESIGN_IMPLEMENT, EXPERIMENT_EXECUTION, RESULT_ANALYSIS and
HYPOTHESIS_UPDATE, using Host-owned chain worktrees. Native tools, Bash, MCP
and network access are bounded by per-request permissions. OpenAlex retrieval
and available PDFs form the literature evidence with sources.

The research Observer, Leader, Debate/Judge and the interface Monitor/Web
observer are different responsibilities. Interactions are bounded by
intervals, new-outcome thresholds and context budgets. An execution node may
only finish after the experiments it accepted have genuinely ended and
submitted their results; a dispatch pause or still-queued experiments do not
count as completion.

In new ACI-23 runs, Leader shares evidence-backed advice while peers continue
independent research. It grants no extra experiment, scheduling priority or
hypothesis parent link. The next proposal cycle may adopt, adapt or reject the
advice. Web **Leader & Debate** shows its author, accepted text, source Attempts
and the proposal requests that received it; inclusion does not prove adoption.
The crown indicates active coordination. Older pinned runs retain their original
exclusive-round behavior; this update does not migrate an existing run.

Templates default to a shared goal: an accepted SUPPORTED result can stop the
whole run. For unified cross-expert acceptance, set `evaluation: grounding`
in `goal.yaml`. Then only one GOAL candidate satisfying all common acceptance
rules completes the goal; LOCAL support and partial scores from different
experts cannot be assembled into a success. On success the run enters
STOPPING, cleans up tasks and produces the final report, without continuing
Leader/Debate or further optimization.

The final report distinguishes a completed raw execution from an accepted
scientific result. A retry's accepted result belongs to the Attempt that
published it; earlier lost attempts are not extra successful replications.
GPU device monitoring and permission to use a GPU do not prove this task ran
GPU computations. Task memory observations retain unknown sampling coverage.
Model usage is reported in tokens, separating reported usage, reservations and
unknown records. The frozen summary precedes the first final-report attempt and
therefore excludes the report calls themselves. `reliability-compare` also uses
tokens and tokens per delivered node in its comparison table.

## GPU scheduling

Same-card sharing requires explicitly connecting a GPU pool. The pool's
member UUIDs, the Host allowlist, actual free capacity, reserve, workload
estimates and the run's concurrency budget together determine scheduling; an
expert is never permanently bound to one card. The nonpooled path serializes
by UUID within one state root and does not coordinate other independent
Hosts either.

Init inspects the project endpoint (`.local/gpu-pools/shared/control.sock`) and
reviews the required pool action. Start now can create, reuse, restore or upgrade
a managed pool before the Host starts, subject to dependency and ownership
checks. Changing pool members or reserve is explicit reconfiguration. External
pools must be supplied explicitly and are not taken over. Failure keeps the
current init choices available for recheck or revision. See
[initialization](docs/guides/initialization.md) and
[runtime recovery](docs/guides/runtime-recovery.md).

A pooled Profile can set `budget.min_vram_per_experiment` and
`budget.initial_vram_per_experiment` separately. An initial estimate may only
be lowered after complete, successful and comparable experiment measurements;
the workload floor, OOM feedback and reserve stay in effect. Old runs do not
change budgets automatically with template updates.

## Ending a run

Prefer the TUI's End run. The CLI needs the current revision before
submitting a cancellation:

```bash
hypoloom cancel --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-cancel-1 --expected-revision REVISION
```

A timed-out request means the reply is unknown. Query that same request and
the run state first, keeping the original Request ID; do not resubmit with a
new identity. An accepted cancellation and an exited Host are two separate
observation points. A managed Host uses `--exit-when-terminal` to wait for
the terminal state and side-effect cleanup before exiting; a manually started
resident `serve` can keep waiting for other explicit requests.

`pause`/`resume` are participant controls, not service-exit or cross-version
recovery commands. Under DISPATCH PAUSED, use read-only diagnosis to confirm
the cause; never force state forward by rewriting SQLite. After a run-wide
dispatch exception, the current candidate ends the Agent calls still running
in that run, records them as failed with `dispatch_paused`, and preserves
session and cost observations, avoiding repeated waits that burn model calls.
Submitted experiments still finish through their original execution path;
results not yet collected during the pause are neither declared successful
nor discarded. This is not the same as End run; decide follow-up from the
actual diagnosis. Source repairs only apply to new runs; existing bundles are
not updated automatically, and switching an old run to a new version for
recovery is not supported.

## API waiting and recovery

API failures now produce a visible cooldown or operator wait in Web/TUI.
Submitted experiments continue; consecutive connection failures do not exhaust
individual research nodes. A recovery request admits one pending invocation;
only an actual API response confirms recovery. Use TUI **p**, or
`hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID`.
Add `--settings /absolute/private/connection.json` to select another compatible
API connection without changing the pinned model or original runtime settings.
See [API availability and recovery](docs/guides/provider-recovery.md) for the
private file format, Web operator access, bounded retries and restart behavior.

## Issue reporting and module diagnosis

```bash
hypoloom debug inspect --state-root /absolute/state/run --run-id RUN_ID
hypoloom debug export --state-root /absolute/state/run --run-id RUN_ID \
  --support-bundle --preview
```

After confirming the scope, replace `--preview` with `--archive --output /absolute/new-support.zip`
to export a support bundle. Research text, free-form errors and local paths
are filtered by default; private content requires an explicit
`--include-private-data`. Exports are never uploaded automatically.

`--recent-failures` selects the last three failing commands by failure time,
their retries and directly related evidence. The package distinguishes the
run-wide summary, selected details and supporting sources; missing or trimmed
records are listed explicitly. `--attempt-id` restricts details to that Attempt
and its direct sources. Source availability is separate from exported fidelity:
sanitized or omitted text is not an exact replay fixture. README and report.json
show the observed run state/revision, failure groups and exporter environment;
recorded Attempt versions are separate and may be unknown. Downloads read fresh
evidence and can differ from the preview of an active run.

The browser also accepts an optional description. CLI equivalents are
`--issue-actual`, `--issue-expected`, `--issue-steps` and `--issue-occurred-at`
(include a timezone). These fields are included even in public packages; review
them before sharing. Each field accepts at most 2,000 characters.

Before a new run, Profile admission materializes the exact target Git commit in
a disposable directory using the execution validator. Escaping symlinks and
other incompatible trees fail before research starts. `resource_visibility` in
the admission report shows the generic execution mount projection for declared
roots; it does not establish that every required dataset/model is present.

`debug rehearse-chain` runs subsequent modules in a fresh state root; it is
an experimental operation that needs explicit authorization, and the source
run stays read-only. See `hypoloom debug rehearse-chain --help` for
parameters.

## Backup and same-version restore

`backup` takes a consistent snapshot through the Host; `restore` writes
offline into a new root that does not exist yet, preserving the run's
original version boundary. The private model settings needed for recovery are
supplied separately and never enter the backup. Do not interpret a restore as
the current worktree taking over an old run, and never overwrite an existing
state root. See `hypoloom backup --help` and `hypoloom restore --help` for
parameters.

Notification configuration is in [notifications](docs/guides/notifications.md).
Before a new expensive topic run, check the [qualification status and
acceptance rules](docs/qualification.md).
