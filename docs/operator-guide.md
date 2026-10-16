# Operator guide

English · [简体中文](operator-guide.zh-CN.md)

Use this guide to review a launch and interpret its results. Installation commands
are in [Setup](../SETUP.md); monitoring and recovery commands are in [Usage](../USAGE.md).

## Review before starting

| Item | Confirm |
| --- | --- |
| Research | Goal, completion criteria, data and expert roles match the intended study. |
| Code and data | The target Git repository is clean and all required paths exist. Original sources remain read-only. |
| Model | The exact model and selected authentication source are available to the isolated research session. |
| Budget | Run, expert, invocation and experiment limits leave room for analysis and reporting. |
| Resources | The CPU/CUDA environment, GPU UUIDs, memory reserve and any shared pool are explicitly selected. |
| External access | Literature access, browser sessions and notification recipients have been reviewed. |

A local configuration check validates launch inputs; it does not prove model
availability or full-text access. Public [templates](../profiles/README.md) contain
placeholder paths and blank credentials that must be filled in a private copy.

Use a complete experiment runtime with its interpreter inside the selected prefix.
A venv symlink outside that prefix does not meet the default sandbox requirements.

## Managed start

`/autoresearch init` ends with **Start now** or **Prepare only**. The latter saves
approved configuration without operating a Host or GPU service.

**Start now** uses `manage_host.py` through user-systemd. It checks Host settings
and `control.sock` readiness before submitting the reviewed creation request.
Readiness defaults to 30 seconds and must be greater than zero and at most
300 seconds. Each new run needs a fresh state root; an ambiguous reply is
reconciled with the original Request ID.

The normal transient service restarts on failure; the scope fallback does not.
`Linger=no` can prevent continuation after the last login session ends.
Initialization does not change linger. A failed observer launch does not cancel
an otherwise running Host.

## Credentials and budgets

Keep model authentication separate from project interaction settings and Profile
literature/notification credentials. Use the approved environment or a regular,
non-symlink settings file with mode 0600. Interactive OAuth or an `apiKeyHelper`
is not proof that isolated sessions can authenticate. Do not change the entry
mode with `--bare` or expose settings to research Agents.

For public Profiles, configure budgets under `methodology.yaml.budget`:

| Setting | Scope |
| --- | --- |
| `total_budget_usd`, `per_expert_budget_usd` | Run and expert cost limits |
| `wallclock_hours`, `experiment_timeout` | Run time in hours; individual experiment time in seconds |
| `agent_invocation` | Per-call turns, tokens, output bytes, time, cost and token enforcement |
| `max_active_agents`, `max_active_processes`, `max_active_reviews` | Independent concurrency limits |
| `provider_rpm_limit`, `provider_max_in_flight` | This run's request pacing per model |

Cost limits govern dispatch; responses already in flight can exceed the remaining
allowance. Unknown charges retain reservations. Token observation mode does not
relax cost, time or resource limits. Raising an experiment timeout does not extend
an Agent's deadline.

Store OpenAlex and notification credentials in the private Profile with mode
0600. Approve notification recipients explicitly. See [personal configuration](../config/README.md)
and [notifications](guides/notifications.md).

## Resources and recovery

CPU is the template default. GPU access requires explicit device authorization.
Initialization inspects the managed pool and presents any creation, reuse or
maintenance action for review; external services are only inspected and reused.
Other users' device activity still affects available capacity.

Sharing starts conservatively and uses comparable successful measurements to
adjust memory margins. A resource estimate cannot override workload floors,
OOM feedback or reserves. See [GPU resources](guides/gpu-resource-planning.md).

Use the recovery action matching the cause: API retry, OpenAlex retry, Telegram
retry or GPU resource recheck. One does not clear the others. A submitted recovery
request is not proof of success; inspect the subsequent observation. See
[API recovery](guides/provider-recovery.md) and [runtime recovery](guides/runtime-recovery.md).

## Interpret the result

- A stored `ACTIVE` status does not prove that the Host or workers are alive.
- A successful Process exit does not establish a scientific result.
- `COMPLETED` requires a separate goal verdict and evidence review.
- Common acceptance requires one complete GOAL candidate; partial LOCAL results
  from different experiments cannot be combined into success.
- Pruning, Leader advice and execution repairs are control or research actions,
  not independent scientific validation.
- Reported usage, reservations and unknown charges are distinct. GPU permission
  is not evidence of GPU computation.

Inspect accepted measurements and the final report, including missing evidence
and failed work. Begin with a bounded topic before scaling up.

## Stop, preserve and update

**q / Leave monitor** leaves background work running. **x / End run** requests
cancellation and waits for cleanup. Keep the original Request ID when reconciling
a timeout; do not force state forward by editing SQLite.

Existing runs retain their original runtime and configuration. Backup and
same-version restore require explicit source and destination selection and never
overwrite an existing root. Cross-version run migration is unsupported.
Reopening an observer can update its display without replacing the research Host.
