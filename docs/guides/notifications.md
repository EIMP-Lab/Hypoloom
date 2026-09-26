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
