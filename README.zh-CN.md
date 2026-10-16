<p align="center">
  <img src="docs/assets/hypoloom_vector.svg" alt="Hypoloom — 开源自主研究引擎">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="许可证: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python 3.12">
  <img src="https://img.shields.io/badge/platform-linux-blue.svg" alt="平台: Linux">
  <img src="https://img.shields.io/badge/requires-Claude%20Code-blue.svg" alt="依赖: Claude Code">
</p>

<p align="center">
  <a href="README.md">English</a> · 简体中文
</p>

Hypoloom 是一个开源的自主研究框架，覆盖从假设生成、实验执行到证据驱动迭代的完整闭环。
常驻 Workflow Host 协调持久研究专家：Host 拥有执行、资源分配与持久状态；独立的 Oracle
校验已记录的结果与生命周期一致性。

- **分析（Analyze）**：专家通过 OpenAlex 检索文献并提取 PDF，与用户澄清研究目标和评估标准。
- **假设（Hypothesize）**：并行专家链提出假设，预注册实验设计并规划资源。
- **实验（Experiment）**：真实 CPU/GPU 执行——私有 Git 工作树、选定运行时、显式 GPU
  授权，以及容量允许时在同一张卡上容纳多个实验的可选共享 GPU 池。
- **演化（Evolve）**：专家解释测量结果，经辩论与裁决修正研究路线，并演化可复用的
  研究技能。

## 🧩 功能总览

一个常驻 Host 协调整个闭环；操作员保留显式授权，独立的 Oracle 校验记录。

<p align="center">
  <img src="docs/assets/hypoloom_overview.svg" alt="Hypoloom 总览:多个领域专家并行运行六节点研究闭环(文献、假设及其 fork 链、设计、执行、分析、演进),由运行级 Grounding 提供证据,通过带评判的 debate 与 Observer 选出的 leader 协调,各角色技能插件自进化,由常驻 Workflow Host 在 Agent、Process 与共享 GPU 池上执行,操作员保留授权,独立 Oracle 校验记录">
</p>

[Profile 模板](profiles/README.zh-CN.md) 提供中英文起点，用于配置你自己的研究目标、
代码、数据和预算。

当前为 **dev** 分支，包含开发指南、资格检验记录和维护上下文。
公开版本见 [main 分支](https://github.com/EIMP-Lab/Hypoloom/tree/main)。

## 🚀 快速开始

需要 Linux、Conda、Git、bubblewrap、可用的用户命名空间和 Claude Code。
后台运行还需要 user-systemd。依赖说明见[安装指南](SETUP.zh-CN.md)。

### 1. 安装 Hypoloom

```bash
git clone --branch dev https://github.com/EIMP-Lab/Hypoloom.git
cd Hypoloom
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[dev,monitor,notify]'
```

上述命令安装控制环境、终端监控界面、通知图表和开发工具。GPU 实验另行选择 CUDA 环境。

### 2. 配置访问权限

按[安装指南](SETUP.zh-CN.md)启用项目配置并设置模型 API 认证。
文献与通知凭据在下一步填写到本地 Profile 中。

### 3. 准备个人 Profile

将中文模板复制到新的、被 Git 忽略的目录：

```bash
mkdir -p profiles/local
(umask 077 && mkdir profiles/local/my-topic && \
  cp profiles/templates/multi-expert-zh/*.yaml profiles/local/my-topic/)
```

若 `my-topic` 已存在，请编辑已有配置或换一个名称；以上命令不会覆盖已有 Profile。
按[中文个人配置指南](config/README.zh-CN.md)修改本地副本：

| 文件 | 作用 |
| --- | --- |
| `goal.yaml` | 研究目标、约束与完成标准 |
| `experts.yaml` | 专家职责、模型选择与研究链上限 |
| `methodology.yaml` | 实验环境、文献访问、研究策略与预算 |
| `language.yaml` | 提示、报告与代码注释的语言 |
| `notify.yaml` | 本地日志、看板与可选外部通知 |
| `safety.yaml` | 数据读取路径、GPU 选择与沙箱限制 |

修改研究目标与 `/REPLACE_ME/` 占位路径，填写必要的文献凭据，并核对预算。
保持上述命令创建的目录权限 0700、YAML 文件权限 0600。模板默认使用 CPU，关闭 Telegram。

### 4. 初始化、确认并启动

在仓库目录启动 Claude Code：

```bash
claude
```

然后输入技能命令：

```text
/autoresearch init
```

选择已有的 `my-topic` Profile，核对模型、凭据、时间与费用限制、CPU/GPU 分配以及通知接收者。
选择 **Prepare only** 保存配置，或选择 **Start now** 开始研究。
配置检查本身不会发起付费模型调用。

### 5. 查看进展

在 Claude Code 中输入：

```text
/autoresearch-monitor
```

选择运行，也可使用初始化返回的终端或 Web 命令。
**q / Leave monitor** 只退出监控，研究继续；**x / End run** 请求取消并等待清理。
人工答复与恢复操作见[使用指南](USAGE.zh-CN.md)。

## 🔍 运行实况

使用 `hypoloom web` 在浏览器中查看研究，或使用 `hypoloom monitor` 打开终端界面。
两者都显示 Host 的已记录状态；人工答复、API 恢复与 End run 需要明确的操作员动作。
Web 默认仅监听回环地址，并通过 bearer 链接控制访问。

### 📊 运行总览

Operations 总览页跟踪整个运行：持久状态、修订号与最近一次 Oracle 结论；实验、
假设、文献证据、演化技能与 Leader/Debate 活动的计数；累计执行可靠性(首轮输出
通过、纠错恢复、需要重试的节点、最终交付、必要动作)；以及对全部已记录 Attempt
的过滤器。

<p align="center">
  <img src="docs/assets/web-overview.png" alt="Web Research Observer 的运行总览:头部显示 Run ID、持久状态 ACTIVE、待定的科学结论、修订号、观测时间与 Oracle 最近清洁状态;实验、假设、文献、技能、有效演化、Leader 与 Debate 的计数卡片;累计执行可靠性卡片;观测过滤器与共享研究目标">
</p>

### 🧑‍🔬 专家卡片

每位专家各自运行六节点研究闭环，并有一张实时卡片：当前所处阶段、正在工作的
Agent 及其上下文相对预算的用量、观测 token 与该专家的可靠性，以及协调状态——
被提议的 debate 会暂停该专家的派发，皇冠标记 Observer 选出的 leader。

<p align="center">
  <img src="docs/assets/web-expert.png" alt="Web observer 的专家卡片:ml-expert 处于第 3 轮并运行两个实验进程,Agent 瓦片以呼吸灯和上下文用量胶囊显示,另有该专家的可靠性卡片;experiment-engineering-expert 显示琥珀色的 debate 提议暂停提示,覆盖其已完成的结果分析;每张卡片含阶段流水线、token 账目与历史浏览入口">
</p>

### 🧬 假设谱系

谱系图展示研究路线本身的演化。每个持久假设只出现一次，按所属专家着色；边的
样式区分执行修复、实验修订与科学修订；被剪枝的分支记录的是控制决策——剪枝
不等于科学否定。

<p align="center">
  <img src="docs/assets/Hypo.png" alt="假设谱系拓扑图:十二个持久假设节点按所属专家着色并标记为已否定、初始或待评估,连线区分执行修复、实验修订、科学修订与不明确修订;图例说明 pruned_by 边是控制决策而非科学否定">
</p>

### 📈 指标趋势

指标趋势把每条已发表的测量按实验顺序连接到其初始假设链，并对照研究合同中
预注册的目标线——通向验收准则的进展一目了然。

<p align="center">
  <img src="docs/assets/web-metrics.png" alt="Web observer 的指标趋势:每个预注册指标一张图,按实验顺序把已发表观测连接到各自的初始假设链,并对照虚线目标线;图例可点击以显示或隐藏对应链">
</p>

### 💻 终端 UI

终端界面在 shell 中呈现同一份持久快照：运行一览(目标、结论、预算与 token、
执行可靠性)、各专家泳道及其当前工作、假设链、下一条持久命令，以及操作员
动作——刷新、Web/本地访问、人工决策、供应方恢复、诊断与 End run。

<p align="center">
  <img src="docs/assets/TUI.png" alt="终端 Research Operations 界面:只读 ACTIVE 运行的快照,包含运行一览(目标、待定结论、研究活动计数、预算与 token、执行可靠性、运行健康)、专家泳道及其进行中的 Attempt、下一条持久命令、假设链树,以及人工决策、供应方恢复、诊断和结束运行等操作员按键提示">
</p>

[USAGE](USAGE.zh-CN.md) 介绍如何启动 Web observer 与 TUI、分享只读链接，以及各操作员
动作。

## 📚 文档

| 指南 | 内容 |
| --- | --- |
| [安装与配置](SETUP.zh-CN.md) | 准备依赖、模型认证与首次启动 |
| [使用指南](USAGE.zh-CN.md) | 监控、人工答复、停止与恢复 |
| [操作员指南](docs/operator-guide.zh-CN.md) | 启动审阅、预算、资源与结果解读 |
| [个人配置](config/README.zh-CN.md) | 六个 Profile 文件、字段说明与中文示例 |
| [Profile 模板](profiles/README.zh-CN.md) | 起始模板、配置流程与运行限制 |
| [架构概览](docs/architecture.zh-CN.md) | 组件职责、执行边界与证据 |
| [文档索引](docs/README.zh-CN.md) | GPU、文献、通知等专题指南 |
| [贡献指南](CONTRIBUTING.zh-CN.md) | 开发环境、验证、文档维护与问题反馈 |

每篇指南均有独立中英文版本，顶部提供语言切换链接。
[English homepage](README.md) 直接链接英文指南。

### 开发与验证

| 参考资料 | 内容 |
| --- | --- |
| [资格检验](docs/qualification.zh-CN.md) | G0–G5 流程、被测候选、结果与适用范围 |
| [本地工作目录](docs/guides/local-workspace.zh-CN.md) | 私有证据、当前工作与原路径保留 |
| [设计记录](docs/design/) | 工程决策与修复依据，保留原文语言 |
| [Codex 开发约定](AGENTS.md) | 仓库规范与授权边界，英文 |
| [当前开发状态](docs/codex-memory/CURRENT.md) | 当前决策与证据入口，英文 |
| [开发记忆索引](docs/codex-memory/INDEX.md) | 历史决策与支撑记录，英文 |
| [Claude Code 指令](CLAUDE.md) | 研究运行时配置，与维护上下文分开，英文 |

开发时仍通过 `manage_host.py`、user-systemd 和运行的 `control.sock` 管理生命周期；
具体操作见[安装指南](SETUP.zh-CN.md)与[使用指南](USAGE.zh-CN.md)。
修改代码或执行资格检验前，请阅读[贡献指南](CONTRIBUTING.zh-CN.md)。

## 🗺️ 后续计划

当前方向，不分先后——是计划而非承诺；具体安排取决于实现与资格检验结果。

- **更多 Agent 运行时**：框架目前通过 Claude Code 驱动研究专家。后续计划把同一
  Host 拥有的研究闭环扩展到其他编程 Agent 运行时——Codex、Kimi Code、OpenCode
  等——并分别验证各运行时的兼容性。
- **AutoResearch 基准**：我们正考虑围绕业界新兴的 AutoResearch benchmark 开展
  学术性质的后续工作：让 Hypoloom 工作流适配这些任务，进而设计并运行具有学术
  质量的对比评测。

## 🚧 开发状态

dev 分支持续开发，接口、Profile 结构和内部契约可能随候选版本变化。
资格结论仅适用于被测候选及其环境；模型与服务提供方的兼容性按实际采样的绑定确认。
已完成的检查、失败与限制见[资格检验记录](docs/qualification.zh-CN.md)。

建议先运行一个有明确时间和费用上限的小课题。公开模板是配置示例，不是科学基准。
执行成功或资格检验通过都不能证明科学结论；仍需检查获准证据、测量结果和最终报告。

保留的运行使用自己的运行时包和配置，更新本仓库不会升级它们；不支持跨版本运行迁移。
操作限制见[运行恢复指南](docs/guides/runtime-recovery.zh-CN.md)。

## ⚖️ 许可证

[MIT](LICENSE)，版权 © 2026 EIMP Lab and the Hypoloom Authors。
