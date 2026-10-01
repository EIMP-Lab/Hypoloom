# Personal configuration / 个人配置入口

Optional server Chromium: [institutional-browser.compose.yaml](institutional-browser.compose.yaml).
Deployment and first login: [English guide](../docs/guides/institutional-access.md#independent-server-browser-recommended) /
[服务器浏览器指南](../docs/guides/institutional-access.zh-CN.md#服务器独立运行推荐)。
Browser sessions live in a private Docker volume, not in this configuration directory.
机构登录状态保存在服务器浏览器的独立Docker卷，不写入此目录或Profile。

Literature and notification credentials live directly in your local Profile.
No separate `.env` is required. Start from the existing six-file
[Profile templates](../profiles/templates/), then fill only the ignored local copy.

文献密钥和 Telegram token 直接填写到本地 Profile，无需维护单独的 `.env`。
公开模板只保留空值；`bootstrap prepare --write` 创建的本地目录为 0700、文件为 0600。

| Setting / 内容 | Local Profile field / 本地字段 |
|---|---|
| OpenAlex API key / 文献密钥 | `methodology.yaml` → `literature.openalex_api_key` |
| Unpaywall contact / PDF 后备联系方式 | `methodology.yaml` → `literature.unpaywall_email` |
| Telegram bot token | `notify.yaml` → `sinks.telegram.bot_token` |
| Telegram recipient / 接收会话 | `notify.yaml` → `sinks.telegram.chat_id` |
| Optional webhook / 可选通知回调 | `notify.yaml` → `sinks.webhook.url` / `token` |
| Optional notification proxy / 可选通知代理 | `notify.yaml` → `proxy_url` |

These are YAML fragments; retain the other fields in each file:
以下是配置片段，保留文件中的其他字段：

```yaml
# profiles/local/YOUR_PROFILE/methodology.yaml
literature:
  enabled: true
  tools: [web_search, web_fetch]
  max_sources: 8
  openalex_api_key: ""  # Fill locally / 本地填写
  unpaywall_email: ""   # Only for Unpaywall / 仅 Unpaywall 需要
```

```yaml
# profiles/local/YOUR_PROFILE/notify.yaml
sinks:
  telegram:
    enabled: false      # Enable after reviewing recipient / 确认接收对象后开启
    bot_token: ""       # Fill locally / 本地填写
    chat_id: ""         # Quote numeric IDs too / 数字 ID 也须加引号
```

Prepare the Profile, edit these fields in a local editor, then repeat init or
`profile-check`. Keep private files mode 0600. For a manually copied Profile,
set permissions before validation (replace YOUR_PROFILE):
先准备 Profile，用本地编辑器填写，再重新执行 init／`profile-check`。
手工复制后可自行设置权限：

```bash
chmod 700 profiles/local/YOUR_PROFILE
chmod 600 profiles/local/YOUR_PROFILE/*.yaml
```

Never enter values through `--set`, shell arguments, chat, tracked templates or
issue attachments. The helper rejects populated credential fields in templates
and `--set`; it does not copy existing secrets into a new Profile.
Sharing a filled Profile shares its credentials: share a blank template instead.

秘密不通过 `--set`、命令行参数、对话、公开模板或 Issue 传递；分享配置时使用空白模板。
准入剥离这些字段的明文，只保存原 YAML 文件引用。Host 复用既有私有文件读取器，
不生成第二份凭据文件，不把明文放进 Agent 输入、运行契约或 issue 导出。
含凭据文件必须为普通非符号链接文件、权限 0600，不能放进 Agent/实验的数据或运行
环境挂载目录。保持文件在 Host 上可读，供通知和重启读取。

Legacy `credentials_file`, dotenv and explicit notification environment sources
remain compatible. Use one source per service: remove old file/environment
fields when filling inline values. The old [dotenv example](hypoloom.env.example)
is for compatibility only. Keep files still referenced by retained runs.

旧文件和环境变量方式保留兼容；同一服务填写内联值后，应删除旧
`credentials_file`／`*_env` 声明，冲突会报错。旧运行仍引用的凭据文件须保留，
本次不会迁移运行或自动改写已有 Profile。

Model authentication continues to use the selected existing Claude settings or
environment. Runtime API switching uses the existing optional private connection
JSON described in [API recovery](../docs/guides/provider-recovery.md).
模型 API 认证仍使用既有 CC 配置；运行中切换见
[API 状态与恢复](../docs/guides/provider-recovery.zh-CN.md)。

Inspect locations and permissions without values / 仅查看位置与权限：

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap paths --profile YOUR_PROFILE
```

`profile_credentials` lists the YAML files. The `credentials` and
`credentials_template` entries describe legacy dotenv locations only.
See [Setup](../SETUP.md), [安装配置](../SETUP.zh-CN.md) and
[Profile guide](../profiles/README.md).
