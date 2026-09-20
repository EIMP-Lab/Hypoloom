# 贡献指南

[English](CONTRIBUTING.md) · 简体中文

使用固定的[环境](environment.yml),然后安装 `.[dev,monitor]`。
仓库约定见 [AGENTS](AGENTS.md)(英文),运行时所有权见
[架构](docs/architecture.zh-CN.md)。Claude Code 指令在 [CLAUDE](CLAUDE.md)(英文)。

修改时运行聚焦测试。晋升候选前遵循[资格](docs/qualification.zh-CN.md):完整
离线检查、保留对照、有界的生产进程闭环、适用的 CPU/GPU 工作和有条件的真实
模型兼容性。GPU 和付费测试是选择加入的;收集不得启动它们。仅一个修复测试
通过不能使发布合格。

保留复现真实失败的测试。当前解析器兼容模板位于 `tests/fixtures/profiles/`;
公开创作示例使用 `profiles/templates/` 下的 v4。修改公开模板时同时更新两种
语言变体,并检查语义控制保持等价。

既有运行保持只读,除非确切操作已被授权。使用一次性测试根;绝不把开发测试
隐式挂到研究 Host 或共享资源池上。密钥、真实 Profile、数据集、日志和运行时
环境保持在版本控制之外。

优先使用现有校验器、持久标识、事务和普通测试。新的哈希、权威文件、门控或
自定义进程协议需要具体的失败论证;避免在测试 oracle 中复制生产 reducer。
保留认证、授权和不可逆操作保护。诚实记录测试范围,包括跳过和未知成本。
