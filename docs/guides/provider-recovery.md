# API availability and recovery

English · [简体中文](provider-recovery.zh-CN.md)

The Host derives API availability from existing Agent observations and operator
control records. Web and TUI show API availability separately from the durable
run state. An ACTIVE run can be waiting
for API access. Missing live Host status means availability is unknown.

| API state | Meaning | Operator action |
|---|---|---|
| `ready` | API dispatch is allowed, subject to task and budget limits. This is not a balance check. | Monitor normal progress. |
| `cooldown` | New API work waits until the displayed retry time. | Wait, or explicitly retry/switch after restoring access. |
| `waiting_operator` | Automatic recovery is exhausted, quota has no retry time, or the selected connection file is unavailable. | Restore access or fix/select a connection file, then request a retry. |
| `recovering` | One pending task may try the API; recovery is not yet confirmed. | Wait for its response; repeated clicks do not prove recovery. |
| Unknown / stale | The interface lacks a fresh Host observation. | Check Host liveness; Web recovery controls remain disabled. |

401/403 means authentication was rejected. 429 alone does not establish quota
exhaustion. Explicit quota error types are classified separately. A valid
Retry-After is a time to try again, not proof of a quota reset. Unknown reset
times and remaining balances are not inferred.

The existing authentication delay remains `budget.provider_auth_cooldown_seconds`
(18000 seconds by default). Zero removes that timed delay; it does not disable
bounded recovery. Ordinary 429 without Retry-After waits 60 seconds. After the
existing infrastructure attempt allowance is spent on consecutive recovery
waves, the Host waits for an operator. Concurrent failures count as one wave.
Explicit quota exhaustion without a retry time waits for an operator immediately.

Provider availability failures retain closed Attempts and their actual/unknown
accounting, but do not exhaust each research node. Cost, token, wallclock and
cancellation limits still apply. Submitted CPU/GPU Processes continue through
collection. Final-report attempts during stopping retain their bounded failure
path. Recovery first admits one ordinary pending invocation; an observed model
response confirms availability independently of scientific output validation.

## Retry or switch the connection

These controls require a live Host and a run in CREATED or ACTIVE state. They
do not start an exited Host. Replace the sample paths and Run ID below with the
values for that run; run CLI commands on the Host machine in the control
environment from [Setup](../../SETUP.md).

TUI: press **p**, then choose **Try current API** or enter an absolute Host-side
connection file path and choose **Switch API**. **Keep waiting** sends no control.
**Browser** opens a separate operator API link.

Web: ordinary observer and human-answer links cannot control APIs. Obtain the
operator API link through the TUI or start an observer explicitly:

```bash
hypoloom web --state-root /absolute/state/run --run-id RUN_ID --allow-provider-control
```

Keep the resulting operator link private. In its API panel, **Try current API**
uses the currently selected connection. To switch, enter the connection file's
absolute path in **Host-side connection file**, then choose **Switch API and try**.
The file must exist on the Host, even when the browser runs on another machine.

CLI retry with the current connection:

```bash
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-api-retry-1
```

To switch:

1. Choose a service that supports the run's pinned model and protocol.
2. Create a regular, non-symlink JSON file on the Host, for example
   `<checkout>/.local/credentials/provider-connection.json`. Use a private editor
   and set mode 0600 before entering credentials. Keep the file outside Git.
3. Fill it using the format below, then submit its absolute path through TUI,
   Web or CLI. Confirm the request was accepted and wait for an actual response.

Its only top-level field is `env`; allowed entries are `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL`. Supply exactly one nonempty key
or token. Omitted base URL selects `https://api.anthropic.com`.

```json
{"env":{"ANTHROPIC_API_KEY":"YOUR_NEW_KEY","ANTHROPIC_BASE_URL":"https://your-compatible-provider.example"}}
```

```bash
chmod 600 /absolute/private/connection.json
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-api-switch-1 --settings /absolute/private/connection.json
```

`YOUR_NEW_KEY` and the example endpoint are placeholders. For token-based auth,
replace `ANTHROPIC_API_KEY` with `ANTHROPIC_AUTH_TOKEN`. This connection JSON is
separate from the OpenAlex/Telegram `hypoloom.env` file and accepts no model
mapping or other Claude settings. The base URL must not contain credentials,
query parameters or a fragment.

For a new intentional operation, choose a new Request ID. If the CLI reply is
lost or times out, rerun the exact command with the same Request ID and arguments
to retrieve its recorded result. TUI/Web retain the pending request identity;
use **Retry same request** when shown before submitting a different selection.

Only the file path is submitted. Never put a credential in that path field.
The selected service must support the pinned model and protocol. Changing a
model, goal, skill or runtime is outside this operation. The original private
settings companion and its existing runtime drift checks remain unchanged.

The selected private file remains the credential source. The Host loads it when
applying a new recovery request and after a Host restart. Editing it does not
hot-reload an active connection:
submit another retry after editing. Keep this file available at its selected
path. If it is missing or invalid after restart/restore, new Agent dispatch
waits for an operator instead of silently falling back to old credentials.
Backups do not add this external credential file; supply it separately when
restoring, alongside the original private settings required by that backup.

Switching affects subsequent invocations. In-flight calls finish under their
original connection; their late failures cannot re-block the new selection.
A lost control reply is retried with the same request ID. Acceptance means a
recovery attempt was requested, not that the provider is healthy. Failed
configuration validation rejects the selection; unavailable selected input is
shown explicitly. Completed experiments and failure/accounting history remain.
This operation does not reopen nodes already recorded as final failures.

## If recovery does not progress

| Observation | What to check |
|---|---|
| File validation rejected | Use an absolute Host-side path, a regular non-symlink file, mode 0600 and the connection-only JSON above. Fix it before making a new request. |
| `connection_configuration` after restart | Restore the selected file or select another valid one. **Try current API** reuses the last selection; it does not revert to launch credentials. |
| Request accepted, still `recovering` | Wait for a runnable task and its actual API response. Admission is still subject to the run's task, resource and budget limits. |
| Another 401/403/429 or quota wait | Check access with the service and select a usable connection. Repeated clear requests do not replenish quota. |
| API is ready but research is idle | Inspect the task state, human questions, resource waits and budgets. API recovery does not bypass those requirements or reopen final failures. |
| Unknown/stale status or failed Host connection | Use the [status and diagnosis workflow](../../USAGE.md#start-and-status); a historical ACTIVE record does not prove a live Host. |

## Notifications and version scope

Opt-in notification channels can select `provider_waiting`,
`provider_retry_requested` and `provider_recovered`. Waiting messages are emitted
for meaningful state/reason changes, not every rejected call. Legacy
`provider_auth_cooldown` / `provider_auth_resumed` selections also subscribe to
the corresponding new events; retry requested never claims recovery.

These scheduling and connection controls apply to new runs created with this
implementation. Existing run bundles remain fixed; no cross-version migration,
automatic restart or rewrite of historical failures is performed.
