# Operator guide

English · [简体中文](operator-guide.zh-CN.md)

Install the pinned control environment using [SETUP](../SETUP.md). The normal
entry is `/autoresearch init` in Claude Code at the repository root. It checks
dependencies, Profile semantics, model/literature authentication, resource
bindings and user-systemd readiness before one final **Start now** or
**Prepare only** choice. Public [templates](../profiles/README.md) are complete
v4 English/Chinese pairs, with placeholder target/data/runtime paths.

Use a complete runtime prefix with its interpreter inside that prefix, as
provided by the pinned Conda environment. A conventional venv whose Python
symlink resolves outside the prefix does not satisfy the default CPU sandbox
binding; package import and `--help` alone do not validate that execution path.

## Managed start

The Run ID defaults to `<profile>-<YYYYMMDDHHMM>` and the Request ID to that
identity plus `-create-1`; use one timestamp and a fresh state root. Explicit
replacement identities are reviewed together. A fresh-root helper refuses that
existing root; it never takes over a retained run.

`manage_host.py` runs in the same Claude Code turn. It starts a transient user
service with `Restart=on-failure`, or the supported user-systemd scope fallback,
then checks the expected read-only STATUS through `control.sock`. Only after
the Host settings match does it send the exact admitted CREATE_RUN request.
The readiness timeout is finite, greater than zero and at most 300 seconds;
30 seconds is the default. Ambiguous CREATE replies retain the original request
identity and are reconciled by querying the Host.

The scope fallback cannot automatically restart a failed Host. `Linger=no`
limits continuation after the last login session ends; init never changes linger.
Textual TUI is an optional observer. A TUI launch failure does not cancel an
otherwise valid running Host. Prepare only creates only the explicitly approved
Profile/settings files and stops before lifecycle effects.

## Credentials and budgets

Use a pinned exact model selector. Prefer approved environment auth through
`ANTHROPIC_API_KEY`; otherwise select an existing eligible mode-0600 regular
settings file with `--claude-settings`. Validate its path and supported auth
fields without exposing contents. Interactive `/login` OAuth is not proof of
isolated Host authentication, and `apiKeyHelper` is not qualified by JSON
validity. Never pass project interaction settings to Agent sessions or improvise
`--bare` launch variants. The Host establishes the actual sandbox and tool scope.

The sole invocation-budget source is
`create_run_payload.research_contract.budget.agent_invocation`. Its five numeric
bounds and `token_enforcement_mode` must match the Host. Token observe mode
preserves cost/time/experiment limits. `max_cost` is per invocation; total and
per-expert USD caps are separate. Unknown charges retain reservations.

OpenAlex requires an explicitly selected existing mode-0600 key file. Review
HTTP(S) access, Poppler paths and response/PDF limits together. Notifications
require separate explicit recipient/transport approval and are disabled in
public templates. See [notifications](guides/notifications.md).

Provider availability is visible in Web/TUI and derived from existing Attempt
and control records. Bounded recovery pauses shared API work without exhausting
each node; submitted experiments continue. TUI **p** and CLI
`provider-retry-clear` request another attempt; optional `--settings` selects a
protected connection file while retaining the pinned model/runtime. An operator
request is not proof of recovery. See [API recovery](guides/provider-recovery.md)
for connection format, separate Web authorization and restart/restore behavior.

## Resources, observation and stopping

CPU-only is the public template default. GPU starts require exact device UUIDs,
a selected GPU runtime and matching Profile authorization. Aggregate
`bootstrap inspect` checks the project pool and returns a concrete preparation
plan. Start now can create, reuse, restore or upgrade it before the Host starts;
member/reserve changes require explicit reconfiguration. Maintenance verifies
project dependencies and coordinates with new admission. External services are
only inspected/reused. Prepare only performs no service action. Failures retain
the current init choices. See [initialization](guides/initialization.md) and
[runtime recovery](guides/runtime-recovery.md) for scope and concurrency limits.
Shared capacity, reserves, estimates and concurrency budgets remain enforced.

New ACI 27 runs use measured 25/15/10% margins and exclusive scheduling until
comparable successful evidence permits sharing. The resource query explains the
final budget, and impossible requests return for bounded technical repair.
See [GPU memory planning](guides/gpu-resource-planning.md), including pool
compatibility and the limits of sampled peaks.

Use `hypoloom status`, `hypoloom monitor` and `hypoloom oracle` with an exact
`--state-root` and `--run-id`. A durable ACTIVE record does not establish process
liveness. `q / Leave monitor` leaves background work running. `x / End run`
confirms cancellation, followed by task cleanup and the managed Host's terminal
exit. Do not treat a timed-out reply as either a successful cancellation or an
authorization to send a new request identity.

In a project CC session, `/autoresearch-monitor` offers a completable shortcut
and reads existing run IDs from the state stores, including after CC restarts.
Multiple matches require selection; external roots require `--state-root`.
See [monitor entry points](../USAGE.md#tui-web-and-human-decisions) for tmux,
terminal commands and JSON output. Confirmed init starts return both the CC
entry and the complete terminal command without opening an observer.

The TUI's `h` action and the explicitly granted Web operator link submit real
human answers to pending Grounding decisions. Read-only share links cannot do so.
Help, Report Issue and Web access are separate UI purposes.

In the Web **Hypothesis lineage** view, solid arrows connect parents to derived
hypotheses; dashed arrows connect winners to pruned hypotheses. **Hide pruning
links (N)** / **Show pruning links (N)** names the next display action; N counts
links. Hypothesis nodes and their statuses remain visible when links are hidden,
without moving nodes or changing their assessments. Dashed node borders mark
pruning, while status symbols retain the scientific assessment. The grouped
legend reflects only visible content.
Select a node or link for the recorded revision text and parent/child comparison;
the graph does not infer scientific change from keywords in that text.

## Research and recovery boundaries

The core expert loop uses private worktrees and persistent sessions for
literature, hypothesis, design, execution, analysis and update. Grounding,
research Observer, Leader and Debate/Judge have their own roles. If common
acceptance is selected, one GOAL candidate must satisfy the complete accepted
declaration; partial results from different experts cannot be combined.

Retained runs use their original preserved runtime. Worktree repairs are for
new runs. Backup and same-version restore require exact source/destination
selection and never overwrite an existing root. `debug rehearse-chain` creates
a new bounded test run; it needs explicit effect/resource authorization and
keeps its source read-only. Cross-version recovery is not provided.

Before a costly topic run, complete applicable [G0–G5 qualification](qualification.md).
Successful installation, Profile admission or a finished model call alone is
insufficient. Daily commands and support export examples are in [USAGE](../USAGE.md).
