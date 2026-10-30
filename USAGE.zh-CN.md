# 运行、监控与停止

[English](USAGE.md) · 简体中文

先完成[安装配置](SETUP.zh-CN.md)。以下 Shell 命令在控制环境中执行；
将 `/absolute/state/run` 和 `RUN_ID` 替换为初始化返回的实际值。

## 启动与状态

在仓库目录的 Claude Code 中输入 `/autoresearch init`，审阅 Profile、模型、路径、
预算、资源和通知接收者。选择 **Prepare only** 保存配置，或 **Start now** 启动研究。

托管启动使用 `manage_host.py` 和 user-systemd，检查 `control.sock` 就绪后才提交运行。
每个新运行使用新的状态目录。若启动回复丢失，先核对原请求与运行状态，不要重复创建。

```bash
hypoloom status --state-root /absolute/state/run --run-id RUN_ID
hypoloom oracle --state-root /absolute/state/run --run-id RUN_ID
```

`status` 查询 Host；`oracle` 审计已记录的结果与生命周期，不修改科研状态。
数据库中的 `ACTIVE` 本身不能证明 Host 存活，还需检查当前 Host 和进程观测。

## TUI、Web 与人工决策

在 Claude Code 中使用以下入口选择已有运行：

```text
/autoresearch-monitor
```

状态目录不在仓库内时，明确指定路径：

```text
/autoresearch-monitor RUN_ID --state-root /absolute/state/run
```

在 tmux 内，入口会打开新的 TUI 窗口；其他情况下返回可在交互终端执行的命令。
存在多个运行时需要选择。也可直接启动观察界面：

```bash
hypoloom monitor --state-root /absolute/state/run --run-id RUN_ID
hypoloom web --state-root /absolute/state/run --run-id RUN_ID
```

`monitor --json` 输出 JSON 快照，`monitor --no-web` 关闭该 TUI 的 Web 监听。
初始化会返回可复用命令，不自动打开观察界面。

| TUI 操作 | 效果 |
| --- | --- |
| **q / Leave monitor** | 退出观察界面，研究继续。 |
| **x / End run** | 确认取消，等待清理及最终报告尝试。 |
| **h** | 查看并回答待处理的 Grounding 问题。 |
| **p / API recovery** | 检查供应商等待、重试或选择兼容连接。 |
| **g / GPU resource recheck** | 修复绑定的 GPU 池后，重新检查资源阻塞。 |
| **l / Literature retry** | 解决访问问题后重试 OpenAlex。 |
| **n / Telegram retry** | 使用当前配置重试通知投递。 |
| **b / Report Issue** | 预览并导出支持包。 |
| **? / Help** | 查看界面帮助与指标定义。 |

TUI 使用 **CN / EN**、Web 使用 **中文 / English** 切换界面语言。
研究正文、日志和标识保持原样。TUI 选择仅在当前会话有效；Web 在浏览器存储可用时记住选择。

### 浏览器访问

Web 默认仅监听本机回环地址，并自动选择空闲端口。从 **Web / local access** 面板
或独立 Web 启动日志复制当前 URL 与 SSH 命令。端口和令牌可能在重启后变化。
需要固定端口时，可用 `monitor --web-port 18080`、`web --port 18081`，或配置
Profile 看板的 `port`；端口占用会报告冲突。

由 TUI 打开的 Web 服务随 TUI 退出而关闭，研究 Host 继续运行。
重新打开同一运行的监控可获取新 URL。普通分享链接只读，访问令牌应妥善保管。

`WAITING_HUMAN` 需要真实用户答复。`web --allow-human-answers` 生成可提交答复的
操作员链接，不应公开。API 恢复和 GPU 重查需要独立授权：使用 TUI **p → Browser**，
或 `web --allow-provider-control`。人工答复权限不包含切换供应商的权限。

## 解读研究进展

专家卡片显示当前阶段、假设、实验与协作状态。**Expert details** 区分提案论证、
阅读助手和实验工作；**Details & history** 提供已记录证据。
详见[阅读与提案](docs/guides/reading-and-proposals.zh-CN.md)。

**Hypothesis lineage** 中的父子连线表示修订关系，剪枝连线记录控制决策；
剪枝不等于科学否定。**Metric trends** 中 GOAL 测量使用共同验收标准，LOCAL 结果
保留各自的筛选定义。缺失测量保持未知，不能拼接多个 LOCAL 分数来满足一个 GOAL 候选。

需要共同验收时，在启动前为 `goal.yaml` 设置 `evaluation: grounding`。
必须由一个完整 GOAL 候选通过全部获准要求。未启用共同验收时，默认共享目标可由
获准的 SUPPORTED 结果结束。目标完成后，Host 停止派发新研究、清理任务并准备最终报告。

Process 执行完成不等于科研结果获准，`COMPLETED` 也不直接表示目标达成。
请检查目标结论、测量和最终报告中的失败及证据缺口。获准使用 GPU 不代表实验实际执行了 GPU 计算。

### 用量与活动

Web 和 TUI 区分已报告 token、尚未结算的观测用量与预留。
Web 顶部的 `≥` 表示已知下界，缺失用量不按零计算。预留参与预算限制，不等于已消耗 token。
当前上下文用量与运行累计用量不同，详见[上下文与记忆](docs/guides/context-management.zh-CN.md)。

**运行趋势（Run trends）** 展示已记录的并发与请求速率。下方 **时间窗口** 显示全程
RPM 缩略图：拖动选框两端调整范围，拖动中间整体平移，两张曲线与本地起止输入同步
更新。支持触摸和方向键，按住 Shift 可移动十个时间段，点击 **查看全程** 可复位。
也可选择预设范围，或输入精确起止时间后点击 **应用时间段**。页面标明时区、原始
分辨率与实际显示范围，保留完整重叠时间桶；无效输入保留原视图，无记录范围明确
提示。在曲线上悬停、触摸，或聚焦曲线后用方向键查看单点。趋势刷新保留选区、查看位置及尚未
应用的输入。下方指标使用固定布局，RPM 位数或未知状态变化不会导致位置跳动；
刷新页面即可加载此次前端更新。实线表示时间段结束时的占用，虚线表示峰值；
请求速率包含重试。缺失观测保留为空白，历史活动不能证明进程当前存活。
**Tokens by stage** 展示用量及诊断子集，重叠子集不能直接相加。

## GPU 调度

GPU 使用需要获准 UUID、兼容环境及匹配的 Profile 权限；共享调度还需绑定资源池。
池成员、实时容量、预留和工作负载估算共同决定准入，专家不永久绑定某张卡。

初始化在 **Start now** 前审阅托管池的创建或维护；**Prepare only** 不操作服务。
外部池需要明确选择，不会被接管。修改 Profile 不改变已有运行。
详见 [GPU 资源](docs/guides/gpu-resource-planning.zh-CN.md)与
[运行恢复](docs/guides/runtime-recovery.zh-CN.md)。

## 结束运行

优先使用 TUI 的 **x / End run**。使用 CLI 时，先通过 `status` 获取当前修订号，
再提交具有唯一请求标识的取消命令：

```bash
hypoloom cancel --state-root /absolute/state/run --run-id RUN_ID \
  --request-id RUN_ID-cancel-1 --expected-revision REVISION
```

取消被接纳与 Host 退出是两个状态。请等待任务清理和运行进入终态；托管 Host
在终态清理后退出，手动启动的常驻 Host 可能继续等待请求。

超时表示回复未知。重试前核对原 Request ID，不要换新标识重复提交。
`pause`、`resume` 不用于停止服务或迁移运行。遇到 `DISPATCH PAUSED` 时先诊断原因，
已提交实验可能仍在收尾。不要通过修改 SQLite 强制推进。

## API 等待与恢复

供应商故障显示为冷却或操作员等待；模型调用等待期间，已提交实验可以继续。
解决原因后，使用 TUI **p** 或：

```bash
hypoloom provider-retry-clear --state-root /absolute/state/run --run-id RUN_ID
```

添加 `--settings /absolute/private/connection.json` 可选择兼容 API 连接，运行模型保持固定。
发起重试不等于恢复，实际响应才提供恢复证据。私有文件格式、定时重试和授权见
[API 恢复](docs/guides/provider-recovery.zh-CN.md)。

文献与 Telegram 重试是独立操作。通知重试不会重新加载代理配置，详见[通知指南](docs/guides/notifications.zh-CN.md)。

## 反馈问题

先查看本地诊断及支持包范围：

```bash
hypoloom debug inspect --state-root /absolute/state/run --run-id RUN_ID
hypoloom debug export --state-root /absolute/state/run --run-id RUN_ID \
  --support-bundle --preview
```

确认后，将 `--preview` 替换为 `--archive --output /absolute/new-support.zip` 创建压缩包。
默认导出过滤研究正文、本地路径和自由文本错误。`--include-private-data` 会增加研究内容，
仅用于事先约定的私有支持渠道。

分享前检查实际压缩包。问题描述会进入公开导出；活动运行的证据也可能在预览后变化。
导出不自动上传。可用 `--recent-failures` 或 `--attempt-id` 缩小细节范围；
`report.json` 说明缺失或受大小限制的材料。详见[反馈与诊断](CONTRIBUTING.zh-CN.md)。

## 备份与同版本恢复

`hypoloom backup` 通过 Host 创建一致快照；`hypoloom restore` 写入尚不存在的新目录，
保留原运行版本。恢复所需的私有模型配置单独提供，不包含在备份中。
必填参数见各命令的 `--help`。

不要覆盖已有状态目录。更新仓库不升级已有运行，也不支持跨版本迁移。
