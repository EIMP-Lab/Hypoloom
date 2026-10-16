# 贡献指南

[English](CONTRIBUTING.md) · 简体中文

开发以 `dev` 分支为基线。修改代码或诊断运行前，阅读 [AGENTS](AGENTS.md)、
[当前开发状态](docs/codex-memory/CURRENT.md)和[开发记忆索引](docs/codex-memory/INDEX.md)
（维护资料为英文）。[架构](docs/architecture.zh-CN.md)说明运行时所有权；
[CLAUDE](CLAUDE.md) 和 `.claude/` 单独配置研究运行时。

## 环境与聚焦检查

按[安装指南](SETUP.zh-CN.md)创建固定的[环境](environment.yml)，再安装开发依赖：

```bash
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[dev,monitor,notify]'
```

修改文档或公开 Profile 时执行：

```bash
./.conda/envs/hypoloom/bin/python -m pytest -q \
  tests/test_documentation_structure.py tests/test_public_profiles.py
```

这些检查验证本地链接、操作边界和双语模板的生产准入，不启动研究、访问模型或使用 GPU。
修改代码时，另行运行覆盖相关行为的测试。

晋升候选前遵循[资格检验](docs/qualification.zh-CN.md)：完整
离线检查、保留对照、有界的生产进程闭环、适用的 CPU/GPU 工作和有条件的真实
模型兼容性。GPU 和付费测试须明确启用；收集测试项时不得启动它们。
单项修复测试通过不足以证明候选具备发布资格。

保留复现真实失败的测试。当前解析器兼容模板位于 `tests/fixtures/profiles/`；
公开配置示例使用 `profiles/templates/` 下的 v4。修改公开模板时同时更新两种
语言版本，并检查控制语义保持等价。

## 运行、证据与工程边界

既有运行保持只读，除非确切操作已获授权。使用一次性测试根目录，不将开发测试
隐式挂到研究 Host 或共享资源池上。密钥、真实 Profile、数据集、日志和运行时
环境保持在版本控制之外。

按[本地工作目录指南](docs/guides/local-workspace.zh-CN.md)区分当前验证与私人开发
历史。证据和依赖路径的状态保留原位；可选的 `current/`、`history/` 目录只是浏览
链接，不是运行输入，也不能证明版本一致。

优先使用现有校验器、持久标识、事务和普通测试。新的哈希、权威文件、门控或
自定义进程协议需要具体的失败论证；避免在测试 Oracle 中复制生产状态归约逻辑。
保留认证、授权和不可逆操作保护。如实记录测试范围，包括跳过项和未知费用。

## 文档维护

通过[文档索引](docs/README.zh-CN.md)查找当前指南。面向用户的文档同步维护中英文，
链接指向对应语言，示例与该语言模板一致。先复制并编辑私有 Profile，再执行 init
并选择该配置进行启动审阅。

参考 main 优化文档时，按内容移植，不合并两条独立分支的历史。保留 dev 的资格流程、
测试与夹具、设计记录、开发记忆和详细运行时边界。公开分支的清理规则不用于删除
dev 的开发证据；历史记录保留原路径，已有引用继续可用。

CURRENT 只记录简洁的当前决策，支撑资料通过 INDEX 索引。指南链接到资格证据，
不重复维护结果表；文档检查通过不代表重新取得候选资格。

## 报告 Issue

发布到公开 issue 的材料应按公开信息处理。先用只读本地工具诊断，再附上已核对披露级别的
材料：

| 级别 | 产生方式 | 内容 | 可发布范围 |
| --- | --- | --- | --- |
| 本地诊断 | `hypoloom debug inspect` | 终端状态摘要；`--include-payloads` 会加入精确研究输入，仍只在本地 | 仅本机查看 |
| 默认支持包 | Web 的 **Report issue**、TUI 的 **b**，或 `hypoloom debug export --support-bundle [--archive]` | 运行／Attempt 标识假名化；过滤研究正文、自由文本错误和本地路径；简化附件名 | 检查实际包内容后，可附于公开 issue |
| 私有支持包 | 加 `--include-private-data` | 额外包含脱敏后的提示、转录和研究附件，仍包含研究内容；排除已知凭据和原始环境变量 | 仅限与维护者约定的私有渠道，不得附于公开 issue |

必须包含敏感内容的支持包，可以私下发送给项目作者获取支持。作者承诺：收到的材料
仅用于诊断所报告的问题，仅限参与处理的维护者可见，不再转发，并在问题解决后
或应你的要求删除。

写文件前先用 `--preview` 预览，发送附件前打开导出的包检查。导出不会自动上传；
分享需要另行明确操作。任何场合都不要发布 API 密钥、凭据文件或私有数据集。
如果导出本身失败，也请在 issue 正文中说明。
