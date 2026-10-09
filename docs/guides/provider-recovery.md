# API availability and recovery

English · [简体中文](provider-recovery.zh-CN.md)

Context capacity affects an individual task and does not trigger shared API
cooldown. See [context recovery](context-management.md) for fresh-session
continuation, usage display and the distinction from scientific termination.

The Host derives API availability from existing Agent observations and operator
control records. Web and TUI show API availability separately from the durable
run state. An ACTIVE run can be waiting
for API access. Missing live Host status means availability is unknown.

| API state | Meaning | Operator action |
|---|---|---|
| `ready` | API dispatch is allowed, subject to task and budget limits. This is not a balance check. | Monitor normal progress. |
| `cooldown` | New API work waits until the displayed retry time. | Wait, or explicitly retry/switch after restoring access. |
| `waiting_operator` | Authentication recovery is exhausted, explicit quota exhaustion has no retry time, or the selected connection file is unavailable. | Restore access or fix/select a connection file, then request a retry. |
| `recovering` | One pending task may try the API; recovery is not yet confirmed. | Wait for its response; repeated clicks do not prove recovery. |
| Unknown / stale | The interface lacks a fresh Host observation. | Check Host liveness; Web recovery controls remain disabled. |

Ordinary 401/403 means authentication was rejected. 429 alone does not establish quota
exhaustion. Explicit quota error types are classified separately. A valid
Retry-After is a time to try again, not proof of a quota reset. Unknown reset
times and remaining balances are not inferred.

Adapter 77 recognizes the observed 403 `permission_error` that explicitly says
an hourly usage window is exhausted and quota resets when that same window ends.
It becomes `usage_window_exhausted` and uses the existing automatic
60/120/240/480/900-second backoff, then one retry every 15 minutes; longer valid
Retry-After wins. "5-hour" describes the window, not five more hours to wait.
Generic permission failures, HTTP 401, and quota exhaustion without this renewal
evidence retain their existing authentication/operator rules. No balance query or
undocumented provider reset header is assumed. Existing runs retain their frozen
classification and recovery policy.

TUI API and service retry times use the TUI process's local timezone with an
explicit offset, for example `2026-10-09 22:05:20+08:00`. Web uses the browser's
local timezone. Updating the observer changes presentation; stored timestamps and
Host scheduling do not change. The displayed time is the next attempt, not a
claim about the provider's actual reset time.

The existing authentication delay remains `budget.provider_auth_cooldown_seconds`
(18000 seconds by default). Zero removes that timed delay; it does not disable
bounded authentication recovery. Ordinary 429 automatically backs off for
60/120/240/480/900 seconds, then keeps retrying every 15 minutes. Longer
Retry-After wins. Transient rate limits do not require manual unlocking after
three waves. Authentication recovery still uses the existing infrastructure
attempt allowance. Concurrent failures count as one wave.
Explicit quota exhaustion without a retry time waits for an operator immediately.

New Hosts also classify explicit upstream HTTP 500/502/503/504 as
`service_unavailable`. These failures use the same automatic backoff and
Retry-After handling as transient 429, without exhausting individual research
nodes. Authentication and quota rules remain separate. Local transport/cleanup
failures are not inferred to be remote service outages. A zero-usage terminal
stream with no model or content cannot confirm API recovery.

Provider availability failures retain closed Attempts and their actual/unknown
accounting, but do not exhaust each research node. Cost, token, wallclock and
cancellation limits still apply. Submitted CPU/GPU Processes continue through
collection. Final-report attempts during stopping retain their bounded failure
path. Recovery first admits one ordinary pending invocation; an observed model
response confirms availability independently of scientific output validation.

## Run RPM and request concurrency

New Profile admission supplies these defaults; override them in
`methodology.yaml` to allocate this run a different share:

```yaml
budget:
  provider_rpm_limit: 60
  max_active_agents: 12
  # provider_max_in_flight: 4  # Optional independent override.
```

When omitted, `provider_max_in_flight` follows the admitted `max_active_agents`
(12 in this example). An explicit value overrides that default. A lower value
queues model calls without reducing the number of research chains; `max_forks`
and hypothesis counts remain independent. These are local limits per model,
not discovered account limits. Experts,
Grounding and reading assistants share admission. The rolling 60-second count
includes outbound attempts, retries, remote token-count calls and connection
failures. Locally queued or locally rejected requests do not count. Requests
are spaced before forwarding; an in-flight slot remains occupied through the
end of a streaming response. `max_active_agents` independently limits Agent
tasks. Queue time consumes their existing Agent/run deadlines.

Other CC sessions, machines and independent Hosts are not visible to this
coordinator. Allocate separate shares when they use the same account. A low
local RPM and sufficient balance do not rule out TPM, other account traffic or
provider overload. TPM limiting is not implemented; 429 does not reveal an
unknown provider limit.

Web displays total run RPM in the header, each expert's RPM on its card, and
Grounding RPM on its independent run-level Agent card in both Web and TUI. Grounding includes its query planning
and readers; it is not also charged to the expert serving as its transport
role. Other global tasks contribute to the total separately. Hover over a value
for in-flight and queued counts. Grounding retains tasks, token accounting,
active reader counts and result-sharing evidence after completion; Web details
can be collapsed. Other global task summaries exclude Grounding.
Grounding shows the same cumulative execution reliability metrics as experts:
first output pass (FP), correction recovery (CR), nodes needing retry (RT),
final delivery (DL) and required actions (RA). Its scope includes Grounding query
planning and readers, excluding other global tasks and expert work. Web metric
buttons open evidence filtered to that scope. Denominators, unknowns and excluded
records remain visible; no samples is not a 100% success rate. These are execution
observations, not scientific quality scores.

Web Agent child tiles use RPM for their exact execution Attempt. Their trailing
robot-head icon shows the current RPM, in-flight and queued counts on hover; an
experiment tile ends with a clock and keeps elapsed time in its capsule. Both
icons use matching SVG outlines and the same dimensions, independent of emoji
fonts. The accessible Agent label includes the request-rate observation.
An expert or Grounding main light uses its aggregate RPM; these totals are never added again
to the run total. For positive RPM, child period is `0.8 + 1.2 / sqrt(RPM)` seconds; main
period is 1.35 times that curve. At equal RPM the main light is slower; differing
aggregate and child rates need not preserve that ordering. Process tiles use
static status, and TUI uses status labels rather than continuous animation.
At 1 RPM the child/main periods are 2/2.7 seconds; at 4 RPM they are 1.4/1.89
seconds. At higher rates the curve approaches 0.8/1.08 seconds without a hard
cutoff. Lights retain their compact size and soft halo, without scaling.
Refreshes preserve the animation; RPM changes retain its current phase.
This frequency cue is not a heartbeat, token speed or research progress signal.
Zero RPM, missing/stale data and reduced-motion preferences stop animation;
unavailable values display `—`.

A complete model response from 429 recovery can release dispatch before the
whole Agent finishes. Repeated same-connection retries during recovery reuse
that recovery rather than admitting another task. Explicit connection switches
and the first explicit reload retain the existing fresh-session boundary.
Known service cooldowns and overly long local
queue waits return to Host recovery before the bridge read timeout. A local
queue wait exceeding 300 seconds is `local_request_queue`, not an upstream 429:
it requeues that task without global API cooldown or scientific retry exhaustion.
Final-report closure, Agent deadlines and run time/token/cost bounds still apply. Cancellation
cleans queued requests and in-flight slots; waiting is not model success.

After a Host restart with prior Host calls, admission waits out a 60-second
window and reports RPM as unknown during that interval. No additional durable
request ledger is created. Existing frozen Hosts do not acquire these changes;
missing traffic observations remain unknown.

## Local provider rules and scheduled retries

Adapter 78 selects shipped error rules from the connection URL and actual error.
Init adds no provider questions or identity probes. Endpoint families include
Kimi Code, MiMo API/Token Plan and Anthropic; unknown gateways keep compatible
protocol/HTTP fallbacks. Model names do not identify gateways. The rules live in
`provider_observation.py`, with official sources in the
[design](../design/context-management-and-recovery.md#2026-10-10本地错误规则与一次性定时重试).
Updates ship with the application, not through model-generated answers.

Kimi's explicit weekly/monthly exhaustion becomes quota exhaustion without an
inferred reset instant. MiMo 402 means insufficient balance; 429 alone cannot
distinguish request limits from plan exhaustion. Anthropic 529 and compatible
overload types use temporary-service recovery. Authentication checks remain.
Diagnostics retain the selected rule family without endpoint/credential exposure.
Coverage is bounded, not a complete vendor catalog.

Press **p**, enter a future local time, then choose **Schedule retry**. Examples:
`2026-10-10 22:30`, today's `22:30`, `30m`, `2h`, or `30 分钟后`. The preview and
appointment show the UTC offset and countdown. Past times are rejected, not moved
to tomorrow. Daylight-saving folds/gaps require an explicit offset, for example
`2026-11-01 01:30-04:00`.

Scheduling pauses automatic API recovery and new provider requests until the
Host consumes the appointment. In-flight responses and submitted experiments
continue; research/Agent time limits remain. The appointment uses no Agent slot.
At the due time one pending task may try; only a response confirms recovery.
Failure resumes ordinary handling. No additional probing request is sent.

A new time replaces the appointment. **Cancel schedule** restores ordinary
recovery. **Try current API** and successful **Switch API** operations supersede
it. An uncertain reply is retried with the same request ID, including scheduling
and cancellation. The existing Host control journal preserves the appointment
when the TUI closes. A normal Host restart consumes an overdue appointment once
if the run remains eligible. It does not start an exited Host or retry a stopped,
ended or wallclock-expired run. Web also displays the appointment.

Scheduling requires a new adapter-78 run; an observer attached to an older frozen
Host hides these controls. Existing runs are not migrated.

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

This operator recovery link also permits **Recheck GPU resources** for a
Host-observed pool fault. API retry and resource recheck are separate actions;
neither clears the other's wait. See [runtime recovery](runtime-recovery.md).

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


## Literature service recovery

New online Hosts first try [Crossref keyword backup](institutional-access.md#search-backup-and-optional-server-proxy)
after transient primary failures. A valid backup packet permits work to proceed
without claiming OpenAlex recovered. The waits below apply when no backup packet
is available. Authentication failures still require repair.

OpenAlex authentication, rate limiting, timeouts and temporary HTTP failures have
independent availability recovery. The Host derives it from existing retrieval
Attempt observations and the existing operator-command journal. It affects
Grounding/expert MAIN retrievals; model calls and submitted experiments that do
not need this service continue. Query validation, scientific coverage, private
address rejection and PDF rules remain unchanged. QUERY_PLAN can still defer a
service failure to MAIN; accepting a plan does not certify provider availability.

Known service failures do not exhaust model/node retry slots. Transient failures
use 30/60/120/240/300-second delays, then one automatic recovery attempt every
15 minutes after the sixth failed wave. A longer Retry-After takes precedence.
Authentication failures wait for credential repair immediately.
Run wall-clock/cancellation limits still apply. Concurrent failures count as one
wave; an older in-flight failure cannot overwrite newer successful retrieval.

The original pending query performs recovery before any model call; no model is
launched just to check connectivity. Coverage prechecks share the same cooldown
and per-run serialization. Already running experiments and independent work retain
their ordinary scheduling; a deferred query-plan Attempt still occupies its
existing task slot. If required evidence is unavailable, dependent work waits:
timeouts, empty search results or missing PDF text are never scientific success.

TUI/Web show the latest reason/HTTP status, next automatic retry and affected
roles in the selected language. Repeated unchanged failures do not generate a
new alert every 15 minutes. Terminal runs retain failure history without promising
future retries. No proxy is required for these recovery rules; a permanently
unreachable search provider still needs a usable network connection.

Web **Retry literature service**, TUI **l**, or the command below requests a
bounded recovery attempt after the operator repairs the connection/credential:

```bash
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID --service openalex
```

OpenAlex reloads the selected original credential source before search. This
command does not switch the model API or change the configured search policy.
Requesting retry is not recovery; a successfully retrieved literature packet
establishes recovery even if the subsequent model task fails. Telegram can report
waiting, operator retry and recovery using the same product-event projection.
Existing frozen runs are not migrated or reopened by deploying these changes.
