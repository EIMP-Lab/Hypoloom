# Notifications / 通知

Public Profiles disable Telegram. Notifications send information outside the
machine and require explicit approval of the transport, recipient and content.
Read-only diagnosis or opening the monitor does not authorize delivery.

In a local v4 Profile, configure `notify.yaml.sinks.telegram.enabled` and one
credential source. The standard source is `credentials_file`: an absolute path
to one dotenv file (template: `config/hypoloom.env.example`)
holding `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The alternative is
`bot_token_env` and `chat_id_env`, whose values name environment variables,
never literal credentials. Use exactly one source, and supply either secret
only to the approved notification process.

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
the read-only source run. Proxy routing is explicit through `--proxy-env` when
needed; do not infer or change another service's proxy configuration. A loopback
test proves local request behavior, not actual delivery to a public recipient.

中文：请在本地 Profile 中开启通知并明确接收对象。密钥集中存放在一个 dotenv 凭据文件
（`sinks.telegram.credentials_file` 声明其绝对路径；模板见
`config/hypoloom.env.example`），或改用环境变量方式（`bot_token_env` /
`chat_id_env`），二者选其一；秘密不能写进 YAML 或公开 Issue。提醒只是返回 TUI/Web
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
