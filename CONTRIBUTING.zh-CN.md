# 反馈与贡献

[English](CONTRIBUTING.md) · 简体中文

通过 [GitHub Issues](https://github.com/EIMP-Lab/Hypoloom/issues) 报告可复现问题或提出改进建议。
欢迎文档修正与 pull request；修改指南或模板时，请同步维护中英文版本。

## 描述问题

请提供 Hypoloom 版本或提交号、操作系统、预期行为、实际结果和最小复现步骤。
运行相关问题还需说明运行是否结束，以及使用了哪个界面。
可附截图或最小示例，但应先移除私有信息。

不要包含 API 密钥、真实 Profile、私有数据集或原始模型转录。
私人路径和研究内容使用占位值。

## 收集诊断材料

先在本地检查，再预览支持包：

```bash
hypoloom debug inspect --state-root /absolute/state/run --run-id RUN_ID
hypoloom debug export --state-root /absolute/state/run --run-id RUN_ID \
  --support-bundle --preview
```

在[安装指南](SETUP.zh-CN.md)的控制环境中执行，并替换为自己的路径与 Run ID。
Web 的 **Report issue** 和 TUI 的 **b** 提供相同导出入口。

| 材料 | 分享范围 |
| --- | --- |
| 本地诊断 | 留在本地；`--include-payloads` 可能显示精确研究输入。 |
| 默认支持包 | 检查实际压缩包后才可用于公开 Issue；过滤研究正文、本地路径和自由文本错误，并对运行／Attempt 标识作假名化。 |
| 带 `--include-private-data` 的导出 | 包含额外研究内容，如脱敏提示和转录；仅用于与维护者约定的私有渠道。 |

确认后，将 `--preview` 替换为 `--archive --output /absolute/new-support.zip` 保存压缩包。
分享前打开检查。用户填写的问题描述会进入公开导出；导出不自动上传，过滤不能替代人工审阅。

需要私有支持时，先与维护者约定渠道。收到的材料仅用于诊断，仅向参与处理的维护者开放，
在问题解决后或应要求删除。私有支持包不得进入公开 Issue。
若导出失败，请说明失败情况，不要因此附上未过滤的原始文件。

导出范围见[使用指南](USAGE.zh-CN.md)，连接问题见 [API 恢复](docs/guides/provider-recovery.zh-CN.md)。
收集诊断时不要修改已有运行。

## 提出或贡献改动

说明用户需求与预期行为，保持改动聚焦，并说明验证方式。
示例使用临时数据，不包含凭据或私人科研内容，也不连接其他用户正在运行的 Host 或 GPU 池。
