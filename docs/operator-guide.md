# Operator guide

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

A provider that rejects authentication (HTTP 401/403 — rolling quota windows
and broken keys look alike) pauses new Agent dispatch instead of burning retry
budgets: the run stays ACTIVE, in-flight work finishes, and dispatch resumes
on its own after `budget.provider_auth_cooldown_seconds` (default 18000, zero
disables). The gate is derived from durable failure rows, so a Host restart
neither clears nor resets it. Operators get `provider_auth_cooldown` /
`provider_auth_resumed` notifications, a TUI banner, and can resume early
after restoring access: TUI `p` → confirm, or
`hypoloom provider-retry-clear --run-id <id>`. A still-broken credential
simply re-enters cooldown on the next failure.

## Resources, observation and stopping

CPU-only is the public template default. GPU starts require exact device UUIDs,
a selected GPU runtime and matching Profile authorization. Init probes the
conventional project pool endpoint (`.local/gpu-pools/shared/control.sock`)
read-only and recalls prior runs' stored pool bindings as candidates; when
pooled GPU work is wanted and no live pool exists, the final review carries an
exact pool plan and the bounded `manage_gpu_pool.py ensure` helper deploys it
as a transient user-systemd service immediately before the managed Host start.
An external project's pool endpoint still enters only through explicit operator
supply. Pool status queries do not create services; replacing, stopping or
reconfiguring a pool remains a separate operator action. Shared capacity
supports concurrent experiments on one card; reserve, memory estimates and
concurrency limits remain enforced.

Use `hypoloom status`, `hypoloom monitor` and `hypoloom oracle` with an exact
`--state-root` and `--run-id`. A durable ACTIVE record does not establish process
liveness. `q / Leave monitor` leaves background work running. `x / End run`
confirms cancellation, followed by task cleanup and the managed Host's terminal
exit. Do not treat a timed-out reply as either a successful cancellation or an
authorization to send a new request identity.

The TUI's `h` action and the explicitly granted Web operator link submit real
human answers to pending Grounding decisions. Read-only share links cannot do so.
Help, Report Issue and Web access are separate UI purposes.

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
