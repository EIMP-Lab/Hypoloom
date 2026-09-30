# API 状态与恢复

[English](provider-recovery.md) · 简体中文

上下文容量问题只影响相关任务，不触发全局 API 冷却。短会话续跑、用量显示及
技术阻塞与科研终止的区别见[上下文恢复](context-management.zh-CN.md)。

Host 从已有 Agent 调用观察和操作者控制记录推导 API 状态。Web、TUI 显示正常、
冷却、等待操作者或正在尝试恢复；它们与运行的持久化状态分开。ACTIVE 不代表
Agent 一定在工作。无法获得实时 Host 状态时，API 可用性显示未知。

| API 状态 | 含义 | 操作建议 |
|---|---|---|
| `ready` | 允许调用 API，仍受任务与预算限制；不代表已查询余额。 | 观察正常任务进展。 |
| `cooldown` | 等待显示的下次重试时间，暂停新的 API 调用。 | 等待；恢复访问后也可手动重试或切换。 |
| `waiting_operator` | 自动恢复次数已用完、明确额度耗尽但无重试时间，或连接文件不可用。 | 恢复访问或修正、选择连接文件，再请求重试。 |
| `recovering` | 允许一个待办任务尝试 API，尚未确认恢复。 | 等待响应；重复点击不能证明恢复。 |
| 未知 / stale | 界面没有新鲜的 Host 观察。 | 检查 Host 是否存活；Web 恢复按钮保持禁用。 |

401/403 表示鉴权被拒绝；单独一个 429 不能证明额度耗尽。供应商明确返回额度
错误类型时才单独分类。Retry-After 表示下次尝试时间，不等于额度重置时间；
未知的重置时间和余额不做猜测。

认证冷却仍使用 `budget.provider_auth_cooldown_seconds`，默认 18000 秒。
设为 0 仅取消这段定时等待，不取消有界恢复。普通 429 没有 Retry-After 时
等待 60 秒。连续恢复轮次达到既有基础设施尝试上限后，等待操作者处理；
并发失败属于同一轮。明确额度耗尽、又没有重试时间时，直接等待操作者。

API 不可用的调用正常关闭并记账，但不会逐个耗尽科研节点的重试次数。
费用、token、总运行时间和取消限制仍有效。已提交 CPU/GPU 实验继续执行和
收集；停止时最终报告保留有界失败收尾。恢复先放行一个正常待办调用，实际
收到模型响应后确认连接恢复；科研输出是否通过校验是另一项判断。

## 重试与切换

这些操作要求 Host 存活，且运行处于 CREATED 或 ACTIVE；不会启动已经退出的 Host。
请将下方示例路径和 Run ID 替换为该运行的实际值；CLI 命令在 Host 所在机器上、
使用[安装教程](../../SETUP.zh-CN.md)中的控制环境执行。

TUI 按 **p**，选择 **Try current API**；或者输入 Host 上连接文件的绝对路径，
选择 **Switch API**。**Keep waiting** 不发送控制。**Browser** 提供单独的
Web API 操作链接。

普通 Web 观察链接和人工回答链接不能切换 API。通过 TUI 获得操作链接，或显式启动：

```bash
hypoloom web --state-root /absolute/state/run --run-id RUN_ID --allow-provider-control
```

请妥善保管生成的操作者链接。在 API 面板点击 **Try current API** 使用当前选定的
连接；需要切换时，在 **Host-side connection file** 中填写连接文件的绝对路径，
点击 **Switch API and try**。即使浏览器在另一台电脑，文件也必须位于 Host 机器上。

此操作者恢复链接也允许对 Host 已确认的池故障执行 **Recheck GPU resources**。
API 重试与资源复查是两个独立操作，不会互相解除对方的等待；详见
[运行恢复](runtime-recovery.zh-CN.md)。

CLI 使用当前连接重试：

```bash
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-api-retry-1
```

切换步骤：

1. 选择支持当前固定模型和协议的服务。
2. 在 Host 上创建普通、非符号链接的 JSON 文件，例如
   `<checkout>/.local/credentials/provider-connection.json`。使用私有编辑器，
   先将权限设为 0600 再填写密钥；文件保留在 Git 忽略目录中。
3. 按下方格式填写，通过 TUI、Web 或 CLI 提交绝对路径。确认操作被接受后，
   等待实际模型响应。

JSON 顶层仅允许 `env`；
其中仅允许 `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN` 和 `ANTHROPIC_BASE_URL`。
key 与 token 必须恰有一个非空。省略 base URL 时使用 `https://api.anthropic.com`。

```json
{"env":{"ANTHROPIC_API_KEY":"YOUR_NEW_KEY","ANTHROPIC_BASE_URL":"https://your-compatible-provider.example"}}
```

```bash
chmod 600 /absolute/private/connection.json
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-api-switch-1 --settings /absolute/private/connection.json
```

`YOUR_NEW_KEY` 和示例地址均为占位值。使用 token 认证时，将 `ANTHROPIC_API_KEY`
替换为 `ANTHROPIC_AUTH_TOKEN`。该连接 JSON 与 OpenAlex/Telegram 使用的
`hypoloom.env` 分开，不接受模型映射或其他 Claude 设置。base URL 不能包含
用户名密码、查询参数或片段。

每个新的主动操作使用新的 Request ID。CLI 回复丢失或超时时，使用相同 Request ID
和参数重跑原命令，查询已记录结果。TUI/Web 会保留未确认请求；出现
**Retry same request** 时先重试原请求，再提交其他连接选择。

界面只提交文件路径，请勿在路径框中输入 key。新服务必须兼容当前固定模型和
协议；此操作不更换模型、goal、skill 或运行环境。原始 Host 私有 settings
副本和既有运行版本漂移检查保持有效，不要直接修改该副本。

所选私有文件持续作为凭据来源。Host 在接受新的恢复操作和重启后加载所选文件；
单独编辑文件不会热更新正在使用的连接，编辑后需要再次提交重试。请保留所选路径。
重启或备份恢复后，如果文件缺失或无效，新的 Agent 调度等待人工处理，
不会静默退回旧凭据。备份不会额外收集这个外部凭据文件；恢复时需单独提供，
原始私有 settings 的备份恢复要求仍然保留。

切换只影响后续调用。在途调用使用旧连接结束，其迟到失败不会重新阻塞新连接。
回复丢失时使用相同请求 ID 重试。操作被接受只说明请求恢复，实际可用性仍待
模型响应确认。配置校验失败会拒绝切换；所选文件不可用会明确显示等待。
已有实验、失败历史和费用记录保留；此操作不重开已经记录为最终失败的节点。

## 恢复未推进时的排查

| 现象 | 检查方式 |
|---|---|
| 文件校验拒绝操作 | 核对 Host 上的绝对路径、普通非符号链接文件、0600 权限和上方连接专用 JSON 格式；修正后发起新请求。 |
| 重启后显示 `connection_configuration` | 恢复所选文件，或选择另一个有效文件。**Try current API** 使用上次选定的连接，不会退回启动时的凭据。 |
| 操作被接受，仍为 `recovering` | 等待可运行任务及实际 API 响应；任务、资源和预算限制仍然有效。 |
| 再次出现 401/403/429 或额度等待 | 向服务商核对访问情况，选择可用连接；反复清除等待不会补充额度。 |
| API 正常，但研究没有推进 | 检查任务状态、人工问题、资源等待和预算。API 恢复不会绕过这些条件，也不重开最终失败节点。 |
| 状态未知、stale 或无法连接 Host | 按[启动与状态](../../USAGE.zh-CN.md#启动与状态)检查；历史 ACTIVE 记录不能证明 Host 存活。 |

## 通知与版本范围

已启用的通知渠道可选择 `provider_waiting`、`provider_retry_requested` 和
`provider_recovered`，分别表示等待、操作者请求重试、实际连接恢复。
按有意义的状态或原因变化通知，不逐次刷屏。原有 `provider_auth_cooldown` /
`provider_auth_resumed` 订阅也映射到对应新事件；请求重试不再被描述为已恢复。

新的调度和连接控制用于本实现创建的新运行。旧 bundle 保持固定，不自动重启、
重写历史失败，也不支持跨版本迁移。


## 文献服务恢复

OpenAlex 的认证、限流、超时和暂时性 HTTP 故障独立进入等待，由已有检索 Attempt
观测和操作者命令记录推导。它影响 Grounding/专家 MAIN 的文献前置检索；不依赖
该服务的模型调用和已提交实验继续。查询校验、年代覆盖、私有地址拒绝及 PDF
规则不变。QUERY_PLAN 仍可把服务异常推迟到 MAIN，因此计划接受不证明服务可用。

已分类的服务故障不耗尽模型/节点重试次数。最多六轮自动失败后等待人工处理，
退避间隔为 30/60/120/240/300 秒，并遵循更长的 Retry-After；认证失败立即等待人工。
运行总时限和取消继续生效。并发失败只算一轮，较早请求的迟到失败不会覆盖较新
检索的成功。成功检索到文献包即确认服务恢复，不取决于后续模型任务是否成功。

修复所选连接或原 Profile 中的凭据后，使用 Web **Retry literature service**、
TUI **l**，或：

```bash
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID --service openalex
```

搜索前重新读取原凭据来源。此操作不切换模型 API、不改变模型、不自动换文献供应商。
“请求重试”与“实际恢复”分开显示，并可通过现有 Telegram 事件推送。
代码更新不迁移、不重开已冻结的旧运行。
