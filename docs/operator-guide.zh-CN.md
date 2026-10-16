# 操作员指南

[English](operator-guide.md) · 简体中文

本指南用于启动审阅与结果解读。安装命令见[安装配置](../SETUP.zh-CN.md)，
监控和恢复命令见[使用指南](../USAGE.zh-CN.md)。

## 启动前审阅

| 项目 | 确认内容 |
| --- | --- |
| 研究 | 目标、完成标准、数据与专家职责符合预期。 |
| 代码与数据 | 目标 Git 仓库干净，所需路径存在；原始资源保持只读。 |
| 模型 | 确切模型和所选认证来源适用于隔离研究会话。 |
| 预算 | 运行、专家、调用和实验限制为分析与报告留有空间。 |
| 资源 | 明确选择 CPU/CUDA 环境、GPU UUID、显存预留及共享池。 |
| 外部访问 | 审阅文献权限、浏览器会话和通知接收者。 |

本地配置检查验证启动输入，不证明模型可用或全文可访问。
公开[模板](../profiles/README.zh-CN.md)的路径与凭据需要在私有副本中填写。

实验环境应完整，解释器实际路径须位于所选环境内。
指向环境外解释器的 venv 符号链接不满足默认沙箱要求。

## 托管启动

`/autoresearch init` 最后提供 **Start now** 与 **Prepare only**。
后者保存已批准配置，不操作 Host 或 GPU 服务。

**Start now** 通过 user-systemd 运行 `manage_host.py`，检查 Host 配置与
`control.sock` 就绪后提交已审阅的创建请求。就绪等待默认 30 秒，范围为大于零且
不超过 300 秒。每个新运行使用新状态目录；回复不明确时按原 Request ID 核对。

正常的临时服务可在失败后重启，scope 后备方式不具备自动重启能力。
`Linger=no` 可能影响最后一个登录会话退出后的持续运行，初始化不会修改它。
观察界面启动失败不取消已正常运行的 Host。

## 凭据与预算

模型认证与项目交互配置、Profile 中的文献及通知凭据分开管理。
使用已批准的环境变量，或权限为 0600 的普通非符号链接设置文件。
交互式 OAuth 或 `apiKeyHelper` 不能证明隔离会话可以认证。
不要用 `--bare` 改变启动方式，也不要向研究 Agent 暴露设置文件。

公开 Profile 的预算集中在 `methodology.yaml.budget`：

| 字段 | 作用范围 |
| --- | --- |
| `total_budget_usd`、`per_expert_budget_usd` | 运行与专家费用限制 |
| `wallclock_hours`、`experiment_timeout` | 运行时限（小时）、单个实验时限（秒） |
| `agent_invocation` | 单次调用的轮数、token、输出字节、时间、费用及 token 执行模式 |
| `max_active_agents`、`max_active_processes`、`max_active_reviews` | 相互独立的并发上限 |
| `provider_rpm_limit`、`provider_max_in_flight` | 本运行按模型分配的请求速率与并发 |

费用限制约束派发，在途响应仍可能超过剩余额度。未知费用保留预留。
token 观测模式不放宽费用、时间或资源限制；延长实验时限不延长 Agent 截止时间。

OpenAlex 与通知凭据填写在权限为 0600 的私有 Profile 中，通知接收者须明确批准。
详见[个人配置](../config/README.zh-CN.md)与[通知指南](guides/notifications.zh-CN.md)。

## 资源与恢复

模板默认使用 CPU。GPU 需要明确设备授权。初始化检查托管池，并展示创建、复用或
维护方案供审阅；外部服务只检查和复用。其他用户的设备活动仍影响可用容量。

同卡共享采用保守策略，根据可比的成功测量调整显存余量。
资源估算不能覆盖工作负载下限、OOM 反馈或预留。详见 [GPU 资源](guides/gpu-resource-planning.zh-CN.md)。

按原因选择 API 重试、OpenAlex 重试、Telegram 重试或 GPU 资源重查，操作之间
不相互清除等待。提交恢复请求不代表恢复成功，请检查后续观测。
详见 [API 恢复](guides/provider-recovery.zh-CN.md)与[运行恢复](guides/runtime-recovery.zh-CN.md)。

## 解读结果

- 已记录的 `ACTIVE` 不能证明 Host 或工作进程存活。
- Process 成功退出不直接建立科学结论。
- `COMPLETED` 仍需结合目标结论和证据审阅。
- 共同验收需要一个完整 GOAL 候选，不能拼接不同实验的 LOCAL 结果。
- 剪枝、Leader 建议和执行修复是控制或科研动作，不是独立科学验证。
- 已报告用量、预留和未知费用不同；GPU 授权不等于实际 GPU 计算。

检查获准测量和最终报告中的证据缺口及失败工作。扩大运行规模前，先完成一个有明确边界的小课题。

## 停止、保存与更新

**q / Leave monitor** 退出监控，后台工作继续；**x / End run** 请求取消并等待清理。
核对超时请求时保留原 Request ID，不要通过修改 SQLite 强制推进。

已有运行保留原运行时和配置。备份与同版本恢复需明确源和目标，且不得覆盖已有目录。
不支持跨版本运行迁移。重新打开观察界面可更新显示，不替换研究 Host。
