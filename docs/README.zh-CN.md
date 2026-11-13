# Hypoloom 文档

[English](README.md) · 简体中文

首次安装请阅读[安装指南](../SETUP.zh-CN.md)，日常操作请参考[使用指南](../USAGE.zh-CN.md)。
dev 分支同时保留开发约定与验证历史。

## 使用指南

| 指南 | 内容 |
| --- | --- |
| [初始化](guides/initialization.zh-CN.md) | 模型选择、预算和启动检查 |
| [个人配置](../config/README.zh-CN.md) | 六个 YAML 文件、字段说明与中文模板示例 |
| [Profile 模板](../profiles/README.zh-CN.md) | 起始模板、配置流程与运行限制 |
| [操作员指南](operator-guide.zh-CN.md) | 日常操作与结果解读 |
| [研究工作流](guides/research-workflow.zh-CN.md) | 专家阶段、实验与技能演化 |
| [阅读与提案](guides/reading-and-proposals.zh-CN.md) | 文献发现、精读和假设提出 |
| [机构全文访问](guides/institutional-access.zh-CN.md) | 浏览器连接、机构登录与 PDF 获取 |
| [GPU 资源](guides/gpu-resource-planning.zh-CN.md) | 显存估算、共享与资源等待 |
| [上下文与记忆](guides/context-management.zh-CN.md) | 工作容量、会话与证据检索 |
| [API 恢复](guides/provider-recovery.zh-CN.md) | 服务限流、连接切换与定时重试 |
| [运行恢复](guides/runtime-recovery.zh-CN.md) | 阻塞任务、观察与恢复边界 |
| [通知](guides/notifications.zh-CN.md) | 外部通知与投递排障 |
| [版本提醒](guides/version-check.zh-CN.md) | 版本比较与新运行的更新方式 |
| [架构概览](architecture.zh-CN.md) | 执行归属、证据与独立校验 |

每篇指南均有独立中英文版本，顶部提供语言切换链接。
本索引链接中文版，[英文索引](README.md)链接对应英文版。

## 开发与验证

| 指南 | 内容 |
| --- | --- |
| [贡献指南](../CONTRIBUTING.zh-CN.md) | 开发环境、聚焦测试与文档维护 |
| [资格检验](qualification.zh-CN.md) | G0–G5 流程、候选证据、失败与适用范围 |
| [本地工作目录](guides/local-workspace.zh-CN.md) | 私有证据、当前工作与原路径保留 |

以上指南也提供英文版。以下维护资料保留原文语言：

- [设计记录](design/)：实现决策与修复依据。
- [AGENTS](../AGENTS.md)：仓库规范与授权边界，英文。
- [当前开发状态](codex-memory/CURRENT.md)与[开发记忆索引](codex-memory/INDEX.md)：
  当前决策和历史支撑材料，英文。
- [CLAUDE](../CLAUDE.md) 与[运行时技能](../.claude/skills/)：
  研究运行时指令，与 Codex 维护上下文分开，英文。

资格结果集中记录于资格检验文档，当前指南通过链接引用；历史证据保留原路径。
说明文字与实现冲突时，以源代码、类型校验和可观察测试结果核对事实。
开发记录不作为研究运行时输入。
