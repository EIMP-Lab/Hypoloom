# Notifications / 通知

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
supported template events include
new results, interaction starts, round completion, errors and run completion.

The Profile dashboard uses an OS-selected port by default (`port: 0`). When
the Host owns both services, Telegram notices use the dashboard's actual bound
port without sending its bearer token. If its listener is unavailable, or an
independent `notify` process cannot know the dynamic port, the notice directs
you to TUI **Web / local access** or the Host startup log. Standalone delivery
may display an explicitly configured fixed port, labeled as configured; that
is not a liveness check. Use the locally displayed token URL to authenticate.

```bash
hypoloom notify --state-root /absolute/state/run --run-id RUN_ID \
  --output-root /absolute/separate-notification-output --once
```

The delivery cursor belongs in the explicit output directory, separate from
the read-only source run. Set `notify.yaml.proxy_url` when a notification proxy
is needed; legacy `--proxy-env` remains an alternative and cannot override that
Profile value. Do not infer another service's proxy configuration. A loopback
test proves local request behavior, not actual delivery to a public recipient.

中文：在私有 `notify.yaml` 中填写 `sinks.telegram.bot_token` 和 `chat_id`，
两个值均加引号、文件保持 0600，确认接收对象后开启通知，无需单独 env。
准入剥离明文，Host 从原 Profile 读取；旧文件/环境方式仍兼容，但同一服务不能混用。
秘密不能写进公开模板、`--set`、对话或 Issue。提醒只是返回 TUI/Web
操作的入口，不会自动接受建议或恢复调度。

`events.telegram` 中的 `provider_waiting`、`provider_retry_requested`、
`provider_recovered` 分别表示 API 等待、操作者请求重试、已观察到实际恢复。
旧 `provider_auth_cooldown` / `provider_auth_resumed` 订阅仍映射到新事件，
但请求重试不会被当作已经恢复。通知按状态或原因变化发送，不逐次失败刷屏；
切换步骤见[API 状态与恢复](provider-recovery.zh-CN.md)。公开模板仍默认关闭 Telegram。

Dashboard 默认使用动态端口（`port: 0`）。Host 同时管理看板和通知时，Telegram
提醒使用实际监听端口，不发送 bearer 令牌。监听不可用，或独立 `notify` 无法获知
动态端口时，提醒会引导查看 TUI 的 **Web / local access** 或 Host 启动日志。
独立通知可展示明确配置的固定端口，并标为 configured；这不代表已验证服务存活。
认证请使用本地展示的令牌地址。


For managed `/autoresearch init`, the caller's notification environment is not
inherited. Inline Profile fields work without an additional credential option.
Webhooks use `sinks.webhook.url` and optional `token`; the URL still requires
HTTPS except for a local loopback receiver. `--notification-output-root` selects
a separate absolute delivery directory. Preflight validates enabled sinks without
sending or opening a dashboard. Missing input blocks launch. Optional old
`--notification-credentials-file` and environment inputs remain compatibility
paths only; model authentication remains separate.

中文：托管 init 不继承交互式通知环境；内联 Profile 无需额外凭据参数。
webhook 使用 `sinks.webhook.url`／`token`，代理使用 `proxy_url`，输出目录可由
`--notification-output-root` 指定。预览不会发送通知；凭据缺失会在启动前报错。
旧 `--notification-credentials-file` 和环境方式仅作兼容。已有运行仍引用原凭据
源，不自动迁移；不要删除其依赖文件。详情见[初始化指南](initialization.zh-CN.md)。

## Server-side Clash proxy / 服务器 Clash 代理

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

中文：服务器无法直连 Telegram 时，在**新 init 前**配置专用通知代理。
Clash/Mihomo 应运行在 Linux 服务器上，并保持后台可用；这里的 `127.0.0.1`
指 Hypoloom 所在服务器，不是 Windows 客户端。通知不会自动采用模型、浏览器
的代理，也不会自动读取 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`。

程序生成的 init 审核会明确显示 **Telegram route: direct**（直连）或
**explicit HTTP proxy**（显式 HTTP 代理）。Clash 已运行但审核仍显示 direct，
说明通知服务尚未采用它；应在 init 前填写私有 Profile 的 `proxy_url`。
安装 Clash 或更新教程不会修改该 Profile；显示已配置也不代表网络已验证。

Check Clash's `port` (HTTP) or `mixed-port` (HTTP supported), then confirm the
listener. A `socks-port` alone is not supported by this notification transport.
The example below uses HTTP port **7891**; replace it with your actual port.
The proxy URL must be an HTTP origin without credentials, path or query.

中文：先确认 Clash 的 `port`（HTTP）或支持 HTTP 的 `mixed-port`，再检查监听。
通知传输不支持仅提供 SOCKS 的 `socks-port`。以下以 **7891** 为例，必须替换为
自己的实际 HTTP 端口；这不是项目固定端口。代理 URL 不携带账号、密码、路径或查询参数。

```bash
ss -ltnp 'sport = :7891'
```

Run this optional connection check on the server in the configured Python
environment. It opens an HTTP CONNECT tunnel and verifies Telegram's TLS
certificate; it uses no Bot credentials and sends no Telegram message.
A listening local port alone does not establish that Telegram is reachable.

中文：可在服务器的项目 Python 环境执行以下连接检查。它验证代理到 Telegram
的隧道和 TLS 证书，不使用 Bot 凭据、不发送消息；仅看到本地端口监听不足以证明连通。

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

中文：在现有私有 `notify.yaml` 的**顶层**添加，与 `sinks`、`events` 同级；
保留原有通知配置和 0600 权限：

```yaml
proxy_url: "http://127.0.0.1:7891"
```

Start a new init after saving. Preflight validates this configuration without
connecting; it still reports delivery as unverified. The connection check above
proves network access only. `--test` below separately checks Bot authentication
and recipient access; `--send-test` additionally sends a message.

中文：保存后再开始新 init。预检只验证配置，不实际连接，因此仍显示投递未验证。
上面的检查只证明网络可达；下节的 `--test` 另行验证 Bot 认证与接收对象，
`--send-test` 才会额外发送测试消息。

**Existing runs:** the notification service caches its proxy when it starts.
TUI **n**, Web **Retry Telegram delivery** and `--retry` clear delivery backoff;
they do not reload or change the proxy. Adding `proxy_url` after a run was created
without it also does not add that binding to the admitted run configuration.
Restore the original network route for automatic recovery, or arrange an explicit
same-version Host recovery with the required proxy startup option and the same
notification output directory. Do not create a second sender to work around this:
concurrent senders can duplicate messages. Preserve the existing delivery cursors.

中文：**已有运行**的通知服务在启动时缓存代理。TUI **n**、Web 的重试按钮及
`--retry` 只解除投递退避，不重载或切换代理。若创建运行时未配置代理，事后新增
`proxy_url` 也不会改变已准入的配置绑定。恢复原网络路径后可自动重试；需要更换
代理时，应单独安排使用原运行版本、指定代理启动参数并保留通知输出目录的 Host
恢复操作。不要另开第二个发送进程来绕过这一限制，以免重复消息；保留原投递游标。

For an existing run **without a Profile proxy binding**, an explicitly requested
access check can use a one-command environment variable. This tests the selected
proxy without sending a message or installing it in the running Host:

中文：对于**尚未绑定 Profile 代理**的已有运行，可主动执行以下独立认证检查。
变量只用于这一次命令；不会发送消息，也不会给正在运行的 Host 安装代理。
已绑定 Profile 代理时，使用下节不带 `--proxy-env` 的检查，不能混用两种来源。

```bash
HYPOLOOM_NOTIFY_PROXY=http://127.0.0.1:7891 hypoloom notify \
  --state-root /absolute/state/run --run-id RUN_ID \
  --proxy-env HYPOLOOM_NOTIFY_PROXY --test
```


## Delivery health and recovery / 投递状态与恢复

An enabled sink is configured, not verified. Init preflight reports
`delivery_status: unverified`; it reads credentials but sends no message. After
explicit notification authorization, verify access or send one labelled test:

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
cursor replacement can still duplicate delivery. There is no second queue or
persistent health store: restart reconstructs pending events from existing facts
and cursors, and starts current-lifetime delivery health as unverified.

Literature service events are `literature_waiting`, `literature_retry_requested`
and `literature_recovered`. Existing `error` subscriptions also select these
operational notices. Repeated failures in one wait state do not each send a new
notice; changing cause, requiring operator intervention, requesting retry and
observing actual retrieval success do. See [API recovery](provider-recovery.md).

中文：**开启通知不代表已验证投递成功**。init 只检查配置，明确显示未验证；
`--test` 检查 Bot 认证和接收对象，`--send-test` 另发一条标注测试用途的消息。
这两项不补发历史通知、不改投递游标；接口接受消息也不代表用户已经阅读。
修复已绑定的凭据来源或网络路径后，可执行 `--retry`，或使用 TUI **n** 和 Web 的
**Retry Telegram delivery**；这些操作不重载代理配置，见上节。以上模式无需输出目录；
普通独立投递仍要求指定目录。

若失败阶段为 `connect`，错误为 `timeout` 或 `network_unreachable` 且没有 HTTP
状态码，先排查服务器网络路径和选定代理。这时尚未收到 Telegram API 响应，不能
据此判断 Bot token 或接收对象无效；TLS 检查通过后，仍需单独验证认证和接收权限。

Web/TUI 显示待投递数量、本次 Host 生命周期内最近成功时间、失败类别/阶段和
下次重试时间。Host 不可达时状态为未知。暂时性错误按 30/60/120/240/300 秒退避，
遵循更长的服务端 Retry-After；认证、接收对象、权限及证书错误等待人工处理。
代理仍显式写入 Profile，保持 TLS、重定向和私密凭据边界。通知故障不暂停研究。

Telegram 状态同时显示已绑定的“直连／HTTP 代理”，不显示端点或凭据。
“连接方式未知”表示 Host 未提供该信息（包括旧版本），不能从事后编辑的 Profile
推断当前发送线路；点击重试保留原有线路和投递游标。

至少五条连续积压的普通 Telegram 事件会汇总；人工问题、错误和服务状态变化单独发送，
每轮最多五条远程消息。成功确认并写入既有游标后，才记录摘要覆盖事件的进度。
崩溃仍可能导致重复投递，不承诺恰好一次；不新增持久队列或健康状态文件，重启后
由事实和游标重建待投递项，当前投递状态重新从未验证开始。

文献通知包含等待、操作者请求重试、实际恢复三个事件，旧 `error` 订阅同样覆盖。
同一等待状态不会逐次失败刷屏；原因变化、转入人工处理以及实际恢复会产生提醒。
详情见 [API 状态与恢复](provider-recovery.zh-CN.md)。
