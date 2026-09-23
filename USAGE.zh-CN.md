# 运行、监控与停止 / Usage

[English](USAGE.md) · 简体中文

首次配置见 [SETUP](SETUP.zh-CN.md)。在项目目录启动 Claude Code 后使用 `/autoresearch init`，
选择 Profile 和语言，审阅真实路径、模型、预算、通知对象及 CPU/GPU，然后选择
`Start now` 或 `Prepare only`。默认 Run ID 为 `<profile>-<YYYYMMDDHHMM>`，Request ID
为同一标识加 `-create-1`；所有命令都针对明确的状态目录和 Run ID。

## 启动与状态

托管启动由 `.claude/skills/autoresearch/scripts/manage_host.py` 使用 user-systemd
完成。它先验证新根和 `control.sock` 的只读 STATUS，再发送明确的创建请求。
若回复丢失，需要查询并核对原 Request ID；不能用新 ID 重复创建运行。
已有运行属于只读对象，修复工作树不会修改它保存的运行代码。

以下示例中的路径和 ID 必须替换为实际已审阅值：

```bash
hypoloom status --state-root /absolute/state/run --run-id RUN_ID
hypoloom monitor --state-root /absolute/state/run --run-id RUN_ID
hypoloom monitor --state-root /absolute/state/run --run-id RUN_ID --json
hypoloom oracle --state-root /absolute/state/run --run-id RUN_ID
```

`status` 查询 Host；`oracle` 是离线只读审计。状态文件可能保留历史 ACTIVE，不能据此
断言 Host 仍在运行。诊断时结合持久状态、Host 服务状态及进程检查。

真实 Agent 的 environment auth 来自已批准的 `ANTHROPIC_API_KEY`；只有明确选择已有
合格设置文件时才使用 `--claude-settings`，该文件必须为 mode-0600。`apiKeyHelper`
不能通过资格检查，交互式 `/login` OAuth 也不等于隔离会话可用；不要自行切换 `--bare`。
Host 的六项调用预算来自 `create_run_payload.research_contract.budget.agent_invocation`，
包括 `token_enforcement_mode`，不是可独立调整的第二预算来源。

## TUI、Web 与人工决策

- **q / Leave monitor**：退出当前监控窗口，后台 Host 和任务继续。
- **x / End run**：打开明确的取消确认；等待任务清理、最终报告和托管 Host 退出。
- **? / Help**：窗口与可靠性指标简介。
- **b / Report Issue**：预览、导出故障支持包。
- **h**：查看并答复待处理的 Grounding 人工问题。

Web / local access 位于独立的连接信息区域。启动 Web observer：

```bash
hypoloom web --state-root /absolute/state/run --run-id RUN_ID
```

TUI Web、独立 Web 和 Profile dashboard 默认使用端口 `0`，由系统在启动监听时
分配空闲端口，支持同一服务器上的多个实例。实际地址与 SSH 命令见 TUI 的
**Web / local access** 面板；独立 Web 和 Host 的启动日志也会输出各自地址。
重启后端口和令牌可能变化，请使用新地址。
需要固定端口时，可指定 `monitor --web-port 18080`、`web --port 18081`，或设置
`notify.yaml.sinks.dashboard.port`。固定端口被占用时会报告冲突，不会自动换端口，
TUI 仍可使用。`monitor --no-web` 仅关闭该 TUI 自己的 Web 监听。

默认仅监听 loopback，并使用 bearer 链接。普通分享链接只读。
`--allow-human-answers` 明确创建可提交人工答复的操作者链接；不要公开该链接。
`WAITING_HUMAN` 需要用户真实答复；建议选项不能被自动提交。

## 研究闭环与完成标准

Grounding 读取目标和数据，建立研究约束。专家按 LITERATURE_REVIEW、HYPOTHESIS_PROPOSAL、
EXPERIMENT_DESIGN_IMPLEMENT、EXPERIMENT_EXECUTION、RESULT_ANALYSIS、HYPOTHESIS_UPDATE
推进，并使用 Host-owned 链工作树。原生工具、Bash、MCP 与网络访问受每次请求权限约束。
OpenAlex 检索和可用 PDF 组成带来源的文献证据。

研究 Observer、Leader、Debate/Judge 与界面的 Monitor/Web observer 是不同职责。
交互受间隔、新增结果门槛及上下文预算约束。执行节点只有在已接收的实验真正结束并完成
结果提交后才能结束；调度暂停或仍有排队实验不代表完成。

模板默认采用 shared goal：被接受的 SUPPORTED 结果可能触发整个运行停止。
需要统一跨专家验收时，在 `goal.yaml` 设置 `evaluation: grounding`。此时只有一个 GOAL
候选满足全部公共验收规则才能完成目标，LOCAL 支持和不同专家的部分分数不能拼成成功。
成功后进入 STOPPING、清理任务并生成最终报告，不继续 Leader/Debate 或寻优。

## GPU 调度

同卡共享需要显式连接 GPU pool。资源池的成员 UUID、Host allowlist、实际可用容量、
reserve、工作负载估算和运行并发预算共同决定调度；专家没有永久绑定一张卡。
非池化路径在同一个状态根内按 UUID 串行化，也不协调其他独立 Host。

init 会只读探测本项目约定位置（`.local/gpu-pools/shared/control.sock`）并召回历史运行
存储的池绑定作为候选；需要池化而没有存活池时，最终审查列出精确池计划，Start now 批准后
由 `manage_gpu_pool.py ensure` 在托管 Host 启动前创建瞬态用户服务。外部项目的池端点仍需
显式提供；池的停止、替换与成员变更始终是独立的操作员动作。

池化 Profile 可以分别设置 `budget.min_vram_per_experiment` 与
`budget.initial_vram_per_experiment`。初始估算只能在获得完整、成功且可比较的实验测量后
下调；工作负载下限、OOM 反馈和 reserve 仍然有效。旧运行不会随模板更新自动改变预算。

## 结束运行

优先使用 TUI 的 End run。CLI 需要先取得当前 revision，再提交取消：

```bash
hypoloom cancel --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-cancel-1 --expected-revision REVISION
```

请求超时表示回复未知。先查询同一请求和运行状态，保留原 Request ID；不要重复提交新身份。
被接受的取消与 Host 已退出是两个观察点。托管 Host 使用 `--exit-when-terminal` 等待终态
及副作用清理后退出；手工启动的常驻 `serve` 可以继续等待其他显式请求。

`pause`/`resume` 是参与者控制，不是退出服务或跨版本恢复命令。
DISPATCH PAUSED 时使用只读诊断确认原因，不能通过重写 SQLite 强行推进状态。
当前候选在运行级调度异常后，会结束该运行中仍在执行的 Agent 调用，以
`dispatch_paused` 记录失败并保留会话和费用观察，避免反复等待消耗模型调用。
已提交的实验仍按原执行路径结束；暂停期间尚未收集的结果不会被宣称为成功或丢弃。
这不等于 End run，需按实际诊断决定后续操作。源码修复仅用于新的运行；既有 bundle
不会自动更新，也不支持把旧运行切换到新版本恢复。

Provider 鉴权被拒绝（HTTP 401/403；五小时滚动额度与密钥失效无法区分）时，
新的 Agent 调度会进入冷却而不是立刻烧掉节点重试额度：运行保持 ACTIVE，
在途调用正常结束，冷却到期（`budget.provider_auth_cooldown_seconds`，默认
18000 秒，0 表示关闭）后自动恢复。冷却状态由持久化失败记录推导，重启 Host
不会清除或重置。通知事件 `provider_auth_cooldown`（每个冷却窗口一条，不逐次
刷屏）与 `provider_auth_resumed` 经既有 Telegram/log 渠道投递，TUI 摘要区显示
PROVIDER COOLDOWN 横幅。手动更换密钥后提前恢复：TUI 按 `p` 确认，或执行
`hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID`；
若凭证仍不可用，下一次失败会重新进入冷却。

## 故障反馈与模块排障

```bash
hypoloom debug inspect --state-root /absolute/state/run --run-id RUN_ID
hypoloom debug export --state-root /absolute/state/run --run-id RUN_ID \
  --support-bundle --preview
```

确认范围后，将 `--preview` 替换为 `--archive --output /absolute/new-support.zip` 导出支持包。
默认过滤研究正文、自由文本错误及本地路径；私有内容需要显式 `--include-private-data`。
导出不会自动上传。

`--recent-failures` 按实际失败时间选取最近三个失败命令、重试及直接相关证据。
包内分别列出全运行概览、选中详情、直接来源和省略记录；`--attempt-id` 将详情限制在
指定 Attempt 及其直接来源。源记录存在不等于导出内容完整：脱敏或省略后的正文不能当作
精确回放夹具。README 和 report.json 展示观察时的运行状态、revision、失败分组与导出环境；
实际 Attempt 的记录版本单独列出，无法取得时保持未知。下载会重新读取证据，活动运行可能
在预览后产生新记录。

浏览器支持填写可选的问题描述；CLI 对应 `--issue-actual`、`--issue-expected`、
`--issue-steps` 和 `--issue-occurred-at`（请注明时区）。这些字段也会进入公开包，分享前
请检查是否包含私密信息；每个字段最多 2,000 字符。

新运行的 Profile 准入会在一次性临时目录中，使用执行阶段的同一验证器检查绑定的确切
Git 提交。树外链接等不兼容问题会在研究启动前报出。准入报告的 `resource_visibility`
展示显式授权目录在通用执行中的挂载投影，但不保证具体课题所需的数据和模型已经齐全。

`debug rehearse-chain` 会在全新状态根运行后继模块，属于需要明确授权的
实验操作；源运行始终只读。参数见 `hypoloom debug rehearse-chain --help`。

## 备份与同版本恢复

`backup` 通过 Host 取得一致快照；`restore` 离线写入尚不存在的新根，保留运行原有版本边界。
恢复所需的私有模型设置单独提供，不进入备份。不要将恢复解释为当前工作树接管旧运行，
也不要覆盖已有状态根。具体参数见 `hypoloom backup --help` 和 `hypoloom restore --help`。

通知配置见 [notifications](docs/guides/notifications.md)(英文)。新的昂贵课题运行前，核对
[资格状态与验收规则](docs/qualification.zh-CN.md)。
