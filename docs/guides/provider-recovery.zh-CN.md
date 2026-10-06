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
| `waiting_operator` | 认证恢复次数已用完、明确额度耗尽但无重试时间，或连接文件不可用。 | 恢复访问或修正、选择连接文件，再请求重试。 |
| `recovering` | 允许一个待办任务尝试 API，尚未确认恢复。 | 等待响应；重复点击不能证明恢复。 |
| 未知 / stale | 界面没有新鲜的 Host 观察。 | 检查 Host 是否存活；Web 恢复按钮保持禁用。 |

401/403 表示鉴权被拒绝；单独一个 429 不能证明额度耗尽。供应商明确返回额度
错误类型时才单独分类。Retry-After 表示下次尝试时间，不等于额度重置时间；
未知的重置时间和余额不做猜测。

认证冷却仍使用 `budget.provider_auth_cooldown_seconds`，默认 18000 秒。
设为 0 仅取消这段定时等待，不取消认证恢复次数上限。普通 429 自动按
60/120/240/480/900 秒退避，此后保持每 15 分钟重试；更长的 Retry-After 优先。
并发失败属于同一轮。临时限流不会因三轮失败而永久等待人工操作；认证失败达到
既有基础设施尝试上限、或明确额度耗尽又没有重试时间时，仍等待操作者。

API 不可用的调用正常关闭并记账，但不会逐个耗尽科研节点的重试次数。
费用、token、总运行时间和取消限制仍有效。已提交 CPU/GPU 实验继续执行和
收集；停止时最终报告保留有界失败收尾。恢复先放行一个正常待办调用，实际
收到模型响应后确认连接恢复；科研输出是否通过校验是另一项判断。

## 本运行的 RPM 与并发控制

新 Profile 准入默认使用以下配置，也可在 `methodology.yaml` 中显式调整：

```yaml
budget:
  provider_rpm_limit: 60
  max_active_agents: 12
  # provider_max_in_flight: 4  # 可选的独立覆盖值。
```

省略 `provider_max_in_flight` 时，采用本次准入的 `max_active_agents`（本例为 12）；
显式填写则保留该值。较低的 API 并发会使模型请求排队，但不会减少研究链数量；
`max_forks` 和假设数量仍独立控制。

这是本运行分配给每个模型的请求份额，不是自动查询到的账号限额。所有专家、
Grounding 和阅读助手共享限速；每个实际出站尝试（包括重试、远端 token 计数和
连接失败）计入最近 60 秒窗口。尚在本地排队或被本地校验拒绝的请求不计入 RPM。
发送前按窗口和间隔排队；在途名额直到响应结束才释放，流式响应也占用名额。
Agent 数量仍由 `max_active_agents` 独立限制；排队时间计入原有 Agent/运行时限。

其他 CC 会话、其他机器和独立 Host 的请求无法自动合并；多个客户端共用账号时，
应分别分配份额。余额充足、本运行 RPM 较低也不能排除 TPM、账号其他流量或服务端
过载。此实现未启用 TPM 限速，不从 429 猜测服务商的真实限额。

Web 顶部显示本运行总 RPM，专家卡片显示各自 RPM，TUI 和 Web 都通过独立的运行级
Grounding 卡片显示其 RPM（含查询规划和阅读助手）、token、活跃阅读助手及结果分享
证据。完成后保留卡片，Web 详情可折叠；其他全局任务的摘要不再包含 Grounding。Grounding 不重复计入其执行角色的专家卡片；
其他公共任务计入总量，因此总量不一定等于专家与 Grounding 之和。悬停数值可查看
在途和排队数。

Grounding 同步显示与专家一致的累计执行稳定性指标：首次输出通过率（FP）、纠错
恢复率（CR）、需重试节点比例（RT）、最终交付率（DL）和必需动作完成率（RA）。
仅统计其查询规划与阅读助手，不混入其他全局任务或专家工作。Web 点击指标后按此
范围查看证据；分母、未知和排除项仍保留，无样本不显示为 100% 成功。这些指标
描述执行情况，不代表科研质量。

Agent 子卡片显示其执行 Attempt 的独立 RPM，不从专家总量平均分配。
RPM 为正时，子灯周期为 `0.8 + 1.2 / √RPM` 秒，专家／Grounding 主灯使用汇总 RPM，并将
对应周期乘以 1.35。同等 RPM 下主灯稍慢；汇总与子任务 RPM 不同时，不保证主灯
始终更慢。实验进程只显示静态状态，TUI 不使用连续动画。这些灯不表示心跳、token
速度或科研进度，卡片分项也不会再次加进全局总量。
1 RPM 时子灯／主灯周期为 2／2.7 秒，4 RPM 时为 1.4／1.89 秒；高 RPM 时逐渐趋近
0.8／1.08 秒，不在某个速率处统一截断。灯体保持原来的小尺寸和柔和光晕，不做缩放。
页面刷新保留连续动画，RPM 变化时保留当前呼吸相位。
RPM 为零、数据缺失/过期或启用系统减少动画时停止动画；未知显示 `—`。

429 恢复调用取得完整有效的模型响应即可解除调度等待，无须等整个 Agent 结束。
恢复中重复提交同一连接的重试不会再次放行任务；显式切换连接仍使用原有控制。
本地排队遇到已知服务冷却会把等待交回 Host；过长的本地排队也在桥接读取超时前
交回现有恢复流程。超过 300 秒的本地排队记录为 `local_request_queue`，不会伪装成
供应商 429、触发全局 API 冷却或耗尽科研节点重试，只重新排队相关任务。最终报告
仍有界收尾，Agent 时限及运行时间／token／费用预算继续生效。取消会清理排队项和
在途名额，不把等待伪装成模型成功。

重启 Host 后，若有前一 Host 的调用，会先等待一个 60 秒窗口；期间 RPM 显示未知，
不创建额外请求账本。旧冻结 Host 不会自动获得这些功能，缺少流量观测时显示未知。

首次显式重载仍记录原有的新会话边界；之后对未变连接的重复重试才会合并，显式切换
连接继续保留原有控制。

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

已分类的服务故障不耗尽模型/节点重试次数。暂时故障先按 30/60/120/240/300 秒退避，
第六轮失败后改为每 15 分钟自动恢复一次；更长的 Retry-After 优先。认证失败则立即
等待修复凭据。
运行总时限和取消继续生效。并发失败只算一轮，较早请求的迟到失败不会覆盖较新
检索的成功。成功检索到文献包即确认服务恢复，不取决于后续模型任务是否成功。

恢复沿用原任务的真实查询，在模型调用前完成，不反复启动模型检查网络。覆盖预检查
也遵守同一冷却与单运行串行规则。已运行的实验和独立任务按原调度继续；等待覆盖
验证的原查询计划 Attempt 仍占用其现有任务槽。缺少必要证据的下游继续等待，不能
把超时、空检索结果或缺少 PDF 全文当作科研成功。

TUI／Web 按所选语言显示最近原因／HTTP 状态、下次自动重试和受影响角色。同一故障
不会每 15 分钟重复告警；运行结束后保留失败历史，但不再承诺未来重试。这些恢复规则
不要求配置代理；检索入口永久不可达时，仍需可用的网络连接。

修复所选连接或原 Profile 中的凭据后，使用 Web **Retry literature service**、
TUI **l**，或：

```bash
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID --service openalex
```

新 Host 会先在主源临时失败时尝试 [Crossref 关键词后备](institutional-access.zh-CN.md#检索后备与可选服务器代理)。
后备文献包通过现有校验即可继续工作，不代表 OpenAlex 已恢复；上述等待适用于
后备也未能提供文献包的情况。认证错误仍需修复。

搜索前重新读取原凭据来源。此操作不切换模型 API、不改变模型或已配置的检索策略。
“请求重试”与“实际恢复”分开显示，并可通过现有 Telegram 事件推送。
代码更新不迁移、不重开已冻结的旧运行。
