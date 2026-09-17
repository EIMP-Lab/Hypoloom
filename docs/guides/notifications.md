# Notifications / 通知

Public Profiles disable Telegram. Notifications send information outside the
machine and require explicit approval of the transport, recipient and content.
Read-only diagnosis or opening the monitor does not authorize delivery.

In a local v4 Profile, configure `notify.yaml.sinks.telegram.enabled` and one
credential source. The standard source is `credentials_file`: an absolute path
to one dotenv file (template: `profiles/templates/hypoloom.env.example`)
holding `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The alternative is
`bot_token_env` and `chat_id_env`, whose values name environment variables,
never literal credentials. Use exactly one source, and supply either secret
only to the approved notification process.

`events.telegram` chooses event types. Include `grounding_question` to receive
pending-human reminders and `grounding_answered` for answer acknowledgment.
The operator still answers through TUI/Web or the exact Host `answer` command;
receiving a message is not an answer. Include `provider_auth_cooldown` to be
told when a provider 401/403 pauses new Agent dispatch until the cooldown ends
(default five hours) and `provider_auth_resumed` for the operator's confirmed
recovery; early resume is TUI `p` or `hypoloom provider-retry-clear`. Other
supported template events include
new results, interaction starts, round completion, errors and run completion.

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
`profiles/templates/hypoloom.env.example`），或改用环境变量方式（`bot_token_env` /
`chat_id_env`），二者选其一；秘密不能写进 YAML 或公开 Issue。提醒只是返回 TUI/Web
操作的入口，不会自动接受建议或恢复调度。
