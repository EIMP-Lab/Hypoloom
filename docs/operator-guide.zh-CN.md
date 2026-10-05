# 操作员指南

[English](operator-guide.md) · 简体中文

按照 [SETUP](../SETUP.zh-CN.md) 安装固定的控制环境。正常入口是在仓库根目录的
Claude Code 中使用 `/autoresearch init`。它会检查依赖、Profile 语义、模型/文献
认证、资源绑定与 user-systemd 就绪情况,最后由你一次性选择 **Start now** 或
**Prepare only**。公开[模板](../profiles/README.md)是完整的 v4 中英文成对版本,
目标/数据/运行时路径均为占位符。

使用解释器位于前缀内部的完整运行时前缀,即固定 Conda 环境所提供的形式。Python
符号链接解析到前缀之外的普通 venv 不满足默认 CPU 沙箱绑定;仅靠包导入和 `--help`
不能验证该执行路径。

## 托管启动

默认 Run ID 为 `<profile>-<YYYYMMDDHHMM>`,Request ID 为该标识加 `-create-1`;
时间戳只取一次,状态根必须全新。显式替换标识会一并审阅。全新根助手拒绝已存在的
根;它从不接管保留的运行。

`manage_host.py` 在同一个 Claude Code 会话中运行。它以 `Restart=on-failure` 启动
瞬态用户服务,或使用受支持的 user-systemd scope 回退,然后通过 `control.sock`
检查期望的只读 STATUS。只有当 Host 设置匹配时,才发送已准入的精确 CREATE_RUN
请求。就绪超时是有限的,必须大于 0 且不超过 300 秒;默认 30 秒。含义不明的
CREATE 回复保留原请求标识,并通过查询 Host 来对账。

scope 回退无法自动重启失败的 Host。`Linger=no` 限制最后一个登录会话结束后的
持续运行;init 从不修改 linger。Textual TUI 是可选的观察器;TUI 启动失败不会
取消一个本来有效的运行中 Host。Prepare only 只创建被显式批准的 Profile/设置
文件,并在任何生命周期效果之前停止。

## 凭据与预算

使用固定的精确模型选择器。优先使用经批准的 environment auth,即
`ANTHROPIC_API_KEY`;否则用 `--claude-settings` 选择已有的合格 mode-0600 普通
设置文件。校验其路径和支持的认证字段,不暴露内容。交互式 `/login` OAuth 不是
隔离 Host 认证的证明,`apiKeyHelper` 也不能凭 JSON 合法性通过资格。不要把项目
交互设置交给 Agent 会话,也不要临时拼凑 `--bare` 启动变体。Host 负责建立实际的
沙箱与工具范围。

唯一的调用预算来源是
`create_run_payload.research_contract.budget.agent_invocation`。它的五个数值
上限和 `token_enforcement_mode` 必须与 Host 匹配。token 观察模式不改变
费用/时间/实验限制。`max_cost` 是单次调用额度;总额与每专家 USD 上限是独立的。
未知费用保留其预留额。

OpenAlex 需要一个显式选择的已有 mode-0600 密钥文件。HTTP(S) 访问、Poppler 路径
和响应/PDF 上限一并审阅。通知需要单独显式批准接收方/传输方式,公开模板默认
关闭。见 [notifications](guides/notifications.md)(英文)。

Web/TUI 显示由既有调用和控制记录推导的 API 状态。有界恢复暂停共用 API
的 Agent 工作，保留节点和已提交实验。TUI **p** 或 CLI `provider-retry-clear`
请求重试；增加 `--settings` 选择受保护连接文件，模型和原始运行配置保持固定。
请求被接受不代表连接已恢复。文件格式、独立 Web 操作授权和重启/备份恢复说明见
[API 状态与恢复](guides/provider-recovery.zh-CN.md)。

## 资源、观察与停止

公开模板默认仅 CPU。GPU 启动需要精确的设备 UUID、选定的 GPU 运行时和匹配的
Profile 授权。init 会只读探测项目约定的池端点
(`.local/gpu-pools/shared/control.sock`),并召回历史运行存储的池绑定作为候选;
当需要池化 GPU 工作而没有存活池时,最终审查携带精确的池计划,由有界的
`manage_gpu_pool.py ensure` 助手在托管 Host 启动前一刻将其部署为瞬态
user-systemd 服务。外部项目的池端点仍只能由操作员显式提供。池状态查询不会创建
服务;替换、停止或重新配置池始终是独立的操作员动作。共享容量支持同卡并发
实验;reserve、显存估算和并发上限仍然执行。

新建 ACI 27 运行根据实测采用 25%／15%／10% 余量；获得可比成功证据前默认
池内独占。资源查询解释最终预算，无法满足的申请交还 Agent 进行有界技术修订。
共享池兼容要求与采样局限见 [GPU 显存规划](guides/gpu-resource-planning.zh-CN.md)。

使用 `hypoloom status`、`hypoloom monitor` 和 `hypoloom oracle`,并给出精确的
`--state-root` 与 `--run-id`。持久的 ACTIVE 记录不能证明进程存活。
`q / Leave monitor` 让后台工作继续运行。`x / End run` 确认取消,随后是任务清理
和托管 Host 的终态退出。不要把超时回复当作取消成功,也不要据此用新的请求标识
再次发送。

TUI 的 `h` 动作和显式授予的 Web 操作员链接会向待处理的 Grounding 决策提交真实
人工答复。只读分享链接不能提交。Help、Report Issue 与 Web 访问是不同的界面
用途。

TUI 工具栏的 **CN / EN** 与 Web 的 **中文 / English** 可切换界面语言，
首次默认英文；Web 记住当前浏览器的选择。切换不改变研究数据或操作权限。
详见[监控界面语言说明](../USAGE.zh-CN.md#tuiweb-与人工决策)。

Web 的 **Hypothesis lineage** 中，实线箭头表示父假设到派生假设，虚线箭头表示
胜出假设到被剪枝假设。**Hide pruning links (N)** / **Show pruning links (N)**
表示点击后隐藏或显示剪枝连线，N 为连线数量。隐藏连线后，假设节点及其状态仍然
显示，节点位置和评估保持不变。节点虚线边框表示剪枝，状态符号保留科学评估；
分组图例只解释当前可见内容。
点击节点或连线可查看修订原文和父子假设对照，图谱不依据原文关键词推断科学变化。

## 研究与恢复边界

核心专家闭环使用私有工作树和持久会话完成文献、假设、设计、执行、分析与演进。
Grounding、研究 Observer、Leader 与 Debate/Judge 各有自己的角色。若选择公共
验收,一个 GOAL 候选必须满足完整、被接受的声明;不同专家的部分结果不能拼成
成功。

保留的运行使用其原始保留的运行时。工作树修复只面向新运行。备份与同版本恢复
需要精确的源/目标选择,从不覆盖已有根。`debug rehearse-chain` 创建新的有界
测试运行;它需要显式的效果/资源授权,并保持其来源只读。不提供跨版本恢复。

在昂贵课题运行前,完成适用的 [G0–G5 资格](qualification.zh-CN.md)(框架与摘要;
完整台账为[英文版](qualification.md))。仅有安装成功、Profile 准入或一次完成的
模型调用是不够的。日常命令与支持包导出示例见 [USAGE](../USAGE.zh-CN.md)。
