<p align="center">
  <img src="docs/assets/hypoloom_vector.svg" alt="Hypoloom — 开源自主研究引擎">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="许可证: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python 3.12">
</p>

<p align="center">
  <a href="README.md">English</a> · 简体中文
</p>

Hypoloom 是一个开源的自主研究框架,覆盖从假设生成、实验执行到证据驱动迭代的完整闭环。
常驻 Workflow Host 协调持久研究专家:Host 拥有执行、资源分配与持久状态;独立的 Oracle
校验已记录的结果与生命周期一致性。

- **分析(Analyze)**：专家通过 OpenAlex 与 PDF 提取检索并阅读文献,再经显式人工澄清
  与公共验收准则接地研究目标。
- **假设(Hypothesize)**：并行专家链提出假设,预注册实验设计并规划资源。
- **实验(Experiment)**：真实 CPU/GPU 执行——私有 Git 工作树、选定运行时、显式 GPU
  授权,以及容量允许时在同一张卡上容纳多个实验的可选共享 GPU 池。
- **演化(Evolve)**：专家解释测量结果,经辩论与裁决修正研究路线,并演化可复用的
  研究技能。

## 功能总览

一个常驻 Host 协调整个闭环;操作员保留显式授权,独立的 Oracle 校验记录。

<p align="center">
  <img src="docs/assets/hypoloom_overview.svg" alt="Hypoloom 总览:多个领域专家并行运行六节点研究闭环(文献、假设及其 fork 链、设计、执行、分析、演进),由运行级 Grounding 提供证据,通过带评判的 debate 与 Observer 选出的 leader 协调,各角色技能插件自进化,由常驻 Workflow Host 在 Agent、Process 与共享 GPU 池上执行,操作员保留授权,独立 Oracle 校验记录">
</p>

[Profile 模板](profiles/README.md) 提供中英文版本,维护者使用
[G0–G5 资格工作流](docs/qualification.md)。

## 快速开始

需要 Linux、bubblewrap、Git 与当前的 Claude Code CLI。固定的
[environment.yml](environment.yml) 提供 Python 3.12 与合适的 SQLite;GPU 实验使用
单独选择的 CUDA 环境。

```bash
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[monitor,dev]'
./.conda/envs/hypoloom/bin/hypoloom --help
```

在本检出中打开 Claude Code 并使用 `/autoresearch init`。选择本地 Profile 或双语
模板,审查目标/数据路径、确切模型、凭据、预算与 CPU/GPU 分配,然后选择
**Prepare only** 或 **Start now**。后者使用受跟踪的 `manage_host.py` 助手与
user-systemd 启动 Host、验证 `control.sock`、创建已审查的运行,并在同一会话中返回
状态,无需 CLI `init` 子命令。

模板路径是占位符,准入前必须绑定。默认 Run ID 为 `<profile>-<YYYYMMDDHHMM>`,
Request ID 为该标识加 `-create-1`;状态根唯一。模型认证需要固定的精确选择器,以及
经批准的环境凭据或符合条件的现有 0600 权限设置文件。使用真实模型或资源前请阅读
[SETUP](SETUP.md)。

## 文档

- [安装与配置 / Setup](SETUP.md)
- [运行、监控与停止 / Usage](USAGE.md)
- [English operator guide](docs/operator-guide.md)
- [中文与英文 Profile 模板](profiles/README.md)
- [架构(英文)](docs/architecture.md)
- [资格与当前验证状态](docs/qualification.md)
- [文档索引](docs/README.md)
- [贡献指南](CONTRIBUTING.md)、[Codex 指令](AGENTS.md) 与
  [Claude Code 运行时指令](CLAUDE.md)

## 开发状态

本项目是开发框架。公开模板用于演示配置,并非科学基准。资格结论仅适用于被测候选与
环境,不构成对科学主张的证明。当前验证范围记录于
[qualification](docs/qualification.md)。保留的运行使用其自带运行时包;不提供跨版本
运行迁移。

## 许可证

[MIT](LICENSE),版权 © 2026 Hui Li。
