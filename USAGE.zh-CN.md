# 运行、监控与停止 / Usage

[English](USAGE.md) · 简体中文

新建 ACI-25 运行的[上下文与记忆说明](docs/guides/context-management.zh-CN.md)解释了
当前请求估算、上次实测与累计用量的区别。容量恢复重建短会话，API 等待使用既有连接控制；
恢复耗尽的节点显示失败原因，不能通过 API 重试重新打开。

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

在本仓库目录启动的 Claude Code 中输入 `/autoresearch-monitor`，即可从斜杠命令
补全中选择该入口；重开 CC 后也能发现已有运行。更新后若新入口尚未加载，请重启
CC 会话。原来的 `/autoresearch monitor` 仍可使用。

```text
/autoresearch-monitor
/autoresearch-monitor RUN_ID
/autoresearch-monitor RUN_ID --state-root /absolute/state/run
```

无参数时，只读取 `state/*/state.sqlite` 中的直接子目录。完整扫描后只有一个有效
匹配时可自动选中；多个匹配时由你选择。外部状态目录或重复的 run ID 需显式指定
`--state-root`。列表显示持久化状态，不代表 Host 在线；状态库不可读或扫描不完整
时会报告原因，不会静默选择另一个运行。

在 tmux 内会打开新的 TUI 窗口；不在 tmux 内则返回可直接粘贴到交互终端的完整
命令。加 `--json` 可改用已有 JSON observer，加 `--no-web` 可关闭 TUI 的 Web
监听。init 确认 Start now 成功后也会给出可复用的 CC 入口和完整终端命令，不会
自动启动监控。补全支持技能名称，不承诺动态 run ID 或灰色输入建议；不修改
CC 的输入历史、设置，也不维护额外的“当前运行”登记文件。

- **q / Leave monitor**：退出当前监控窗口，后台 Host 和任务继续。
- **x / End run**：打开明确的取消确认；等待任务清理、最终报告和托管 Host 退出。
- **? / Help**：窗口与可靠性指标简介。
- **b / Report Issue**：预览、导出故障支持包。
- **h**：查看并答复待处理的 Grounding 人工问题。
- **p / API recovery**：查看 API 等待状态、重试当前连接，或通过私有连接文件选择兼容 API。
- **g / GPU resource recheck**：修复绑定的池后，复查 Host 已确认的资源阻断；详见
  [运行恢复](docs/guides/runtime-recovery.zh-CN.md)。

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

TUI 打开的 Web 服务与该 TUI 属于同一进程。TUI 退出或崩溃会关闭这个 Web 监听，
独立运行的科研 Host 继续工作。遇到意外退出，可检查
`<state-root>/diagnostics/host.jsonl` 中 `phase: "tui"` 的异常，然后重新打开
同一个 run 的监控。重开后从 **Web / local access** 获取当前 Web 地址和 SSH 端口。

默认仅监听 loopback，并使用 bearer 链接。普通分享链接只读。
`--allow-human-answers` 明确创建可提交人工答复的操作者链接；不要公开该链接。
`WAITING_HUMAN` 需要用户真实答复；建议选项不能被自动提交。

API 恢复使用独立操作权限：TUI **p → Browser**，或启动独立 Web 时添加
`--allow-provider-control`。仅有人工回答权限不能切换 API，具体步骤见
[API 状态与恢复](docs/guides/provider-recovery.zh-CN.md)。
同一操作者恢复权限也允许 GPU 资源复查；API 重试与 GPU 复查是两个独立操作，
不会互相解除对方的等待。


Web/TUI 统一以 **token** 展示用量，区分已报告、预留与未知记录；模型价格是否
可用不代表运行健康状态。Web 的 **Metric trends** 在正式 GOAL 测量发布前显示
等待说明，LOCAL 筛选指标单独按假设与已记录的测量定义绘图，不套用 GOAL 门槛，
不混合不同筛选基线。进行中的实验要等结果正式发布后才显示指标。

## 研究闭环与完成标准

新 ACI-26 运行允许 Host 并行执行专家的 FORWARD/BACKWARD，在两侧结果正式
接纳后才进行综合。点击专家卡片的 **Expert details** 查看论证任务、科学假设、
阅读助手、阅读调用拒绝及观测不完整情况；概览保留当前工作，详情另提供该专家
的会话历史入口。工具示例、容量/分支上限与重试行为见
[阅读与论证教程](docs/guides/reading-and-proposals.zh-CN.md)。

Grounding 读取目标和数据，建立研究约束。专家按 LITERATURE_REVIEW、HYPOTHESIS_PROPOSAL、
EXPERIMENT_DESIGN_IMPLEMENT、EXPERIMENT_EXECUTION、RESULT_ANALYSIS、HYPOTHESIS_UPDATE
推进，并使用 Host-owned 链工作树。原生工具、Bash、MCP 与网络访问受每次请求权限约束。
OpenAlex 检索和可用 PDF 组成带来源的文献证据。

研究 Observer、Leader、Debate/Judge 与界面的 Monitor/Web observer 是不同职责。
交互受间隔、新增结果门槛及上下文预算约束。执行节点只有在已接收的实验真正结束并完成
结果提交后才能结束；调度暂停或仍有排队实验不代表完成。

新 ACI-23 运行中，Leader 分享有证据的建议，其他专家独立继续研究；任命不增加实验、
调度优先或父假设关联。后续提案可以采纳、改造或拒绝。Web 的 **Leader & Debate**
展示作者、已接受建议、来源 Attempt，以及实际收到建议的提案请求；输入包含不代表
已经采纳。皇冠表示协调正在进行。旧运行保留固定版本的独占轮次行为，本次更新不迁移
已有运行。

模板默认采用 shared goal：被接受的 SUPPORTED 结果可能触发整个运行停止。
需要统一跨专家验收时，在 `goal.yaml` 设置 `evaluation: grounding`。此时只有一个 GOAL
候选满足全部公共验收规则才能完成目标，LOCAL 支持和不同专家的部分分数不能拼成成功。
成功后进入 STOPPING、清理任务并生成最终报告，不继续 Leader/Debate 或寻优。

最终报告区分已完成的原始执行和已接受的科研结果。重试成功的结果只归属于实际发布
产物的那次 Attempt，之前 LOST 的尝试不算额外的成功重复实验。设备监控和 GPU
使用权限不能证明该任务运行过 GPU 计算；本任务显存观察保留采样未知状态。
模型用量统一使用 token，区分已报告用量、预留和未知记录；该汇总固定于首次最终
报告尝试之前，不包含最终报告调用本身。`reliability-compare` 的对比表也使用 token
及每个成功交付节点的 token 用量。

## GPU 调度

同卡共享需要显式连接 GPU pool。资源池的成员 UUID、Host allowlist、实际可用容量、
reserve、工作负载估算和运行并发预算共同决定调度；专家没有永久绑定一张卡。
非池化路径在同一个状态根内按 UUID 串行化，也不协调其他独立 Host。

init 检查项目端点（`.local/gpu-pools/shared/control.sock`），并审阅具体池操作。
Start now 可以在 Host 启动前创建、复用、恢复或升级托管池，但须通过依赖与所有权检查。
修改全池成员或 reserve 属于明确重配。外部池必须显式提供，不会被自动接管。
失败时保留当前 init 选择，允许重新检查或调整。详见
[初始化](docs/guides/initialization.zh-CN.md)与[运行恢复](docs/guides/runtime-recovery.zh-CN.md)。

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

## API 等待与恢复

API 不可用时，Web/TUI 明确显示冷却或等待人工处理。已提交实验继续执行，
持续连接故障不会逐个耗尽科研节点。恢复操作先放行一个待办调用，实际收到
API 响应后才确认恢复。TUI 按 **p**，或执行
`hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID`。
增加 `--settings /absolute/private/connection.json` 可选择兼容的 API 连接，
模型和原始运行配置仍固定。文件格式、Web 操作权限、有界重试和重启行为见
[API 状态与恢复](docs/guides/provider-recovery.zh-CN.md)。

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
