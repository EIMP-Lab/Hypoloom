# 阅读助手与并行假设论证

[English](reading-and-proposals.md) · 简体中文

新建的 ACI-26 运行允许同一专家的 FORWARD 与 BACKWARD 同时执行。
Host 先记录 FORWARD 请求，使两侧复用同一组已接纳的 Leader 建议；随后在普通
Agent 容量和预算允许时启动 BACKWARD。SYNTHESIS 必须等待两侧结果正式接纳。
只有一个可用名额时自然串行。既有运行保持原固定 bundle，不支持跨版本迁移。

两侧是各有独立会话与工作目录的固定任务，不代表已经生成两条假设。
综合阶段可以提出一条或多条可独立检验的机制。证据有限、实验有先后依赖、
或者差异只是普通超参数变化时，选择单条候选仍然合理；在候选已有的 `claim` 或
`assumptions` 中说明理由，不另加输出字段。

| 设置 | 含义 |
| --- | --- |
| `max_hypotheses_per_round` | 每位专家每轮候选数的配置上限 |
| 实际请求中的 `effective_max_forks` | 本次提案获准使用的候选上限 |
| `max_forks` | 专家活跃研究链的并发上限 |
| `max_active_agents` | 包括论证任务、阅读助手的共享 Agent 容量 |

一侧失败重试时保留已成功的另一侧；必需的一侧最终失败后，Host 取消并排空
仍在运行的另一侧，不伪造科学否定。停止、API 恢复和计费沿用已有机制。

## 准备文献并委派阅读

需要时在 Profile 的文献工具列表中启用 `read_papers`。阅读助手需要父 Attempt
已经接纳的文献来源。已有来源见 `/papers/INDEX.md`；新文献使用现有 document 工具：

```text
web_fetch(mode="document", url="https://example.org/paper")
  -> status="available", source_ref=...
read_papers(action="submit", readings=[{
  "reading_id": "method-check",
  "paper_ref": <返回的 source_ref>,
  "question": "文中报告的机制依赖哪些假设？"
}])
read_papers(action="wait", max_wait_seconds=10)
```

将 `source_ref` 原样填入 `paper_ref`。URL、DOI 不等于已接纳引用；工具不会
猜测文献或为错误引用隐式下载。submit 只接受 `action` 与 `readings`，查询、
等待和取消使用独立的 status、wait、cancel 调用。未知参数会提示字段名，不回显值。
根据反馈修正后再次提交。批量中任意一项无效时不创建任何阅读任务；相同 ID 和问题
的重复提交复用原命令，冲突复用仍报错。

阅读助手消耗普通 Agent 和费用预算，由父专家核查证据并综合结论。若没有助手能够
启动且 wait 提示容量或预算不足，可以取消未启动问题后自行阅读原文，或者说明限制。
不支持阅读助手递归委派。

## 理解专家卡片

卡片区分活跃研究链、本轮已接纳候选与请求上限、两侧论证及综合状态、阅读任务数。
阅读任务按已接纳 command ID 计数；重试增加 Attempt 数，不增加任务数。
被拒绝的阅读调用不算助手，也不虚构子 Agent 费用；实际调用和失败重试仍进入原有
Token 与费用汇总。

工具观测来自当前调用捕获的 tool_use/tool_result，以已有事件 ID 去重。
记录缺失或不完整时显示下界和未知覆盖，不将缺失当作零错误。历史等待原因可能已经
变化；缺少原因时不能断言具体阻塞位置。OPEN 表示持久化执行状态，不等于进程心跳。
CLI 与支持报告使用相同的只读汇总。

本改动不保证固定倍数加速或科研质量提升。实际验证范围和限制见
[qualification](../qualification.zh-CN.md)。
