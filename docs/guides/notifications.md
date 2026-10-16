# Notifications

English · [简体中文](notifications.zh-CN.md)

## Configure delivery

Public Profiles disable Telegram. Notifications send information outside the
machine and require explicit approval of the transport, recipient and content.
Read-only diagnosis or opening the monitor does not authorize delivery.

In a local v4 Profile, configure `notify.yaml.sinks.telegram.enabled` and one
credential source. Fill `sinks.telegram.bot_token` and `sinks.telegram.chat_id`
directly in the private local `notify.yaml`, quoting both strings. Keep the file
mode 0600. The public template has empty fields and disabled Telegram; no env
file is required. The Host reads these values from the original Profile after
admission removes them from the contract. Never pass them through `--set` or chat.
Legacy `credentials_file` and `bot_token_env` / `chat_id_env` remain alternatives;
remove those fields when using inline credentials. Use exactly one source.
The [configuration tutorial](../../config/README.md) covers preparation and editing.

`events.telegram` chooses event types. Include `grounding_question` to receive
pending-human reminders and `grounding_answered` for answer acknowledgment.
The operator still answers through TUI/Web or the exact Host `answer` command;
receiving a message is not an answer. Include `provider_waiting` for authentication,
rate-limit or quota waits, `provider_retry_requested` for an operator request,
and `provider_recovered` for an observed successful API response. Legacy
`provider_auth_cooldown` / `provider_auth_resumed` selections map to these events.
See [API recovery](provider-recovery.md) for TUI/Web/CLI controls. Other
supported template events include new results, interaction starts, round completion, errors and run completion.

Telegram event templates follow the admitted Profile's `language.yaml.report_language`:
`zh` / `zh-*` use Chinese, other languages use English. The legacy fallback is
`goal.language`. Each entry is separated by a blank line, with a few event icons.
The footer shows elapsed wall time since run creation (including waits/offline
time, frozen at terminal transition), or unknown when unavailable. It does not
include Dashboard addresses. Obtain the authenticated Web address locally.

```bash
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID \
  --output-root /absolute/separate-notification-output --once
```

The delivery cursor belongs in the explicit output directory, separate from
the read-only source run. Set `notify.yaml.proxy_url` when a notification proxy
is needed; legacy `--proxy-env` remains an alternative and cannot override that
Profile value. Do not infer another service's proxy configuration. A loopback
test proves local request behavior, not actual delivery to a public recipient.

For managed `/autoresearch init`, the caller's notification environment is not
inherited. Inline Profile fields work without an additional credential option.
Webhooks use `sinks.webhook.url` and optional `token`; the URL still requires
HTTPS except for a local loopback receiver. `--notification-output-root` selects
a separate absolute delivery directory. Preflight validates enabled sinks without
sending or opening a dashboard. Missing input blocks launch. Optional old
`--notification-credentials-file` and environment inputs remain compatibility
paths only; model authentication remains separate. Preserve credential files
still referenced by existing runs. See [initialization](initialization.md).

## Experiment metrics and GOAL verdicts

`new_best_result` remains the subscription name, with two distinct titles:
**First valid measurement** when a comparison group has no prior accepted value,
and **Comparable metric improved** for a strict improvement over that group's best.
Ties and worse values do not trigger it. Accepted metric outcomes can be `SUCCESS`
or `CLEAN_REFUTATION`; a useful measurement need not meet its experiment threshold.
Groups use the same component, metric/target, measurement declaration, frozen parser
bytes, trial plan, upstream outcomes and evaluator version. A changed group starts
its own first measurement; there is no cross-component or overall GOAL ranking.

Each notice shows the component, declared experiment scope (`LOCAL`, `GOAL`, or
unknown for older records), metric/comparison form/direction, previous and current
values, and whether the experiment metric reaches its threshold. This threshold
uses the existing metric evaluator, not the overall GOAL verdict. A `GOAL` experiment
scope likewise does not establish that the whole goal has been met.

Subscribe separately to `goal_verdict_recorded` for **Overall GOAL met**, **Overall
GOAL not met**, or **Overall GOAL inconclusive**, taken only from the recorded GOAL
verdict. Both public templates include this event with Telegram still disabled.
For future runs, include both events in your private Profile before init. Existing
admitted subscriptions and retained runtime bundles do not change automatically.
Metric labels and values are included; research bodies and parser code are omitted.

## Twelve-hour run briefings

Add `run_briefing` to `events.telegram` in the private Profile **before init**;
both public templates include it, while Telegram remains disabled by default.
The existing observer sends the latest briefing at each 12-hour elapsed boundary,
using the observation timestamp, independently of the browser. The Host observer
or standalone notification process must be running. The first briefing is due at
12 hours, not on startup. After downtime, only the latest due window is sent;
terminal runs stop briefings. `run_finished` remains the completion notification.
Retry/backoff and the existing per-sink cursor apply: acknowledgment advances the
window, so ordinary restart does not repeat it. Delivery remains at-least-once;
a crash after remote success but before saving the cursor can duplicate a message.
Briefings make no periodic model requests and use no additional Agent slots.
Log/webhook subscriptions can select the same summary event.

The summary shows **cumulative** GOAL verdict and assessed/required components,
hypothesis graph node/relation/state counts, experiment states, and tokens.
Assessed components are not a claim of success. Reported, observed-unsettled and
reserved tokens remain separate; reservations are not usage, failed reported
usage is already included in reported tokens, and unknown counts can overlap.
It does not transmit research prose, question/answer bodies or operator notes.

The optional PNG uses the existing Web trend projection for the **last 12 hours**:
total and expert RPM (at most the 8 busiest experts, with all included in total),
Grounding/other activity, and recorded Agent/experiment occupancy and bucket peaks.
RPM includes retries, uses explicit bucket averages and leaves missing capture
as gaps with a known lower bound. Occupancy is recorded bound Attempts, not an OS
liveness check. UTC timestamps and bucket width are printed on the chart. Summary
and trend reads use the same cutoff time but separate read-only transactions;
ongoing activity can settle between those reads.

Install chart dependencies in the control environment:

```bash
python -m pip install -e '.[monitor,notify]'
```

Transports themselves use the standard library. For Chinese images, install a system CJK font such as
Noto Sans CJK (`fonts-noto-cjk` on Debian/Ubuntu). Plotting runs in a disposable
subprocess with a 15-second timeout, following bounded diagnostic inspection.
Missing dependency/font, timeout or drawing failure falls back to one text
briefing. A PNG and caption use one acknowledged
[Telegram sendPhoto](https://core.telegram.org/bots/api#sendphoto) request; if the
caption exceeds its 1,024-character limit, send the full text summary instead.
A remote photo-delivery failure follows normal retries, without sending another
text message that could duplicate an ambiguously acknowledged photo. These
settings apply to new runs; updating the checkout does not change the subscriptions
or runtime of an existing run. Cross-version run migration is unsupported.

## Server-side Clash proxy

When the Linux server cannot connect directly to Telegram, configure an explicit
notification proxy **before a new init**. Clash/Mihomo must run on the server
under an appropriate account and stay available for unattended notifications.
Here `127.0.0.1` means the server running Hypoloom, not the Windows client.
Notifications do not inherit model/browser proxy settings or automatically use
`HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY`.

The fixed init review explicitly shows **Telegram route: direct** or
**explicit HTTP proxy**. If Clash is running but the review says direct,
the notification service has not selected it. Add `proxy_url` to the private
Profile before init; installing Clash or updating this tutorial does not edit
that Profile. A configured proxy still needs a connectivity check.

Check Clash's `port` (HTTP) or `mixed-port` (HTTP supported), then confirm the
listener. A `socks-port` alone is not supported by this notification transport.
The example below uses HTTP port **7891**; replace it with your actual port.
The proxy URL must be an HTTP origin without credentials, path or query.

```bash
ss -ltnp 'sport = :7891'
```

Run this optional connection check on the server in the configured Python
environment. It opens an HTTP CONNECT tunnel and verifies Telegram's TLS
certificate; it uses no Bot credentials and sends no Telegram message.
A listening local port alone does not establish that Telegram is reachable.

```bash
python - <<'PY'
import http.client

connection = http.client.HTTPSConnection("127.0.0.1", 7891, timeout=8)
connection.set_tunnel("api.telegram.org", 443)
try:
    connection.connect()
    print("Telegram CONNECT + verified TLS succeeded; no message sent")
finally:
    connection.close()
PY
```

Add this **top-level** field to the existing private `notify.yaml`, alongside
`sinks` and `events`; preserve those sections and keep file permissions at 0600:

```yaml
proxy_url: "http://127.0.0.1:7891"
```

Start a new init after saving. Preflight validates this configuration without
connecting; it still reports delivery as unverified. The connection check above
proves network access only. `--test` below separately checks Bot authentication
and recipient access; `--send-test` additionally sends a message.

**Existing runs:** the notification service caches its proxy when it starts.
TUI **n**, Web **Retry Telegram delivery** and `--retry` clear delivery backoff;
they do not reload or change the proxy. Adding `proxy_url` after a run was created
without it also does not add that binding to the admitted run configuration.
Restore the original network route for automatic recovery, or arrange an explicit
same-version Host recovery with the required proxy startup option and the same
notification output directory. Do not create a second sender to work around this:
concurrent senders can duplicate messages. Preserve the existing delivery cursors.

For an existing run **without a Profile proxy binding**, an explicitly requested
access check can use a one-command environment variable. This tests the selected
proxy without sending a message or installing it in the running Host. For a run
with a Profile proxy binding, use the check below without `--proxy-env`; do not
combine the two sources:

```bash
HYPOLOOM_NOTIFY_PROXY=http://127.0.0.1:7891 hypoloom notify \
  --state-root /absolute/state/run --run-id RUN_ID \
  --proxy-env HYPOLOOM_NOTIFY_PROXY --test
```


## Delivery health and recovery

An enabled sink is configured, not verified. Init preflight reports
`delivery_status: unverified`; it reads credentials but sends no message. After
explicit notification authorization, choose the required check, test or retry;
these commands are alternatives, not a sequence to run in full:

```bash
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID --test
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID --send-test
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID --retry
```

`--test` checks Telegram Bot authentication and recipient access. `--send-test`
additionally sends one message; API acceptance does not prove the human read it.
Neither operation replays research events or changes a delivery cursor. `--retry`
asks the live managed Host to retry Telegram after repairing its already-bound
credential source or network route. It does not reload proxy settings (see above).
These modes need no `--output-root`. Ordinary
standalone delivery still requires a separate explicit output directory.

Web and TUI show sink state, pending event count, last successful delivery in this
Host lifetime, safe failure class/phase and next retry time. Unknown/unavailable
Host contact is never a healthy channel. TUI **n** opens the Telegram retry form;
the scoped Web operator link includes **Retry Telegram delivery**. Notification
failures stay separate from scientific acceptance and model API recovery.

Telegram health also shows its bound **Direct / HTTP proxy** route, without
exposing the endpoint or credentials. **Unknown route** means the observed Host
did not provide it (including older versions); viewers do not infer it from an
edited Profile. Retry keeps that bound route and the existing delivery cursors.

If the failure phase is `connect` with `timeout` or `network_unreachable` and
no HTTP status, first check the server's network route and selected proxy.
No Telegram API response has been received, so this is not evidence of an invalid
Bot token or recipient. A successful TLS check still needs the separate access
check before authentication or recipient permissions can be considered verified.

Temporary errors back off 30, 60, 120, 240, then at most 300 seconds. A longer
server Retry-After is respected. Authentication, recipient/permission and TLS
certificate errors wait for operator repair and retry. The transport never
follows redirects, disables TLS verification or guesses an ambient proxy.
Only fixed classifications, numeric status/errno and safe phases are reported.
No Bot URL, token, recipient or raw response body is exposed in health details.

At least five consecutive pending routine Telegram events are summarized into one message;
human questions, failures and service-state changes retain separate messages.
Each poll sends at most five remote messages. Existing per-source delivery
cursors advance only after API acknowledgment and durable cursor replacement,
including every source covered by a summary. A crash between acknowledgment and
cursor replacement can still duplicate delivery. Restart reconstructs pending
events from recorded research events and delivery cursors, and starts
current-lifetime delivery health as unverified.

Literature service events are `literature_waiting`, `literature_retry_requested`
and `literature_recovered`. Existing `error` subscriptions also select these
operational notices. Repeated failures in one wait state do not each send a new
notice; changing cause, requiring operator intervention, requesting retry and
observing actual retrieval success do. See [API recovery](provider-recovery.md).
