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

Hypoloom 是一个开源的自主研究框架，支持文献调研、假设提出、实验执行和结果分析，
让多个 AI 研究专家围绕同一课题协作。

**工作流调度服务**（Workflow Host，简称 Host）在后台安排任务、分配资源并保存研究进度。
**独立校验器**（Oracle）核对运行记录与实验产物的一致性。科学结论仍需结合实验设计和
实际结果判断。

- **分析**：通过 OpenAlex 检索学术文献，阅读可获取的 PDF 全文，结合你的反馈明确
  研究目标和评估标准。
- **假设**：并行探索不同假设，在执行前记录实验设计，并规划各项实验的资源需求。
- **实验**：在隔离的 Git 工作树中，使用选定的 CPU/GPU 环境运行代码。GPU 设备需明确
  授权；容量允许时，可通过共享 GPU 池让多个实验共用显卡。
- **演化**：分析测量结果，讨论不同解释，修订研究假设，并改进可复用的研究技能。

## 🧩 功能总览

下图展示专家协作、实验执行与独立校验之间的关系。
研究目标、预算和资源权限由你设定。

<p align="center">
  <img src="docs/assets/hypoloom_overview.svg" alt="研究流程：明确目标后，多位专家开展文献调研、假设提出、设计、执行、分析和更新，由 Host 调度并由 Oracle 独立校验记录">
</p>

**目标澄清**（Grounding）帮助明确研究要求，**协调专家**（Leader）提供研究建议，
**结构化辩论**（Debate）用于讨论不同解释。具体流程见
[研究工作流指南](docs/guides/research-workflow.zh-CN.md)。

**课题配置**（Profile）由六个 YAML 文件组成，用于定义研究目标、专家、运行环境、
预算与权限。可从[中英文模板](profiles/README.zh-CN.md)开始，按自己的代码和数据修改。

## 🚀 快速开始

需要 Linux、Conda、Git、bubblewrap、可用的用户命名空间和 Claude Code。
后台运行还需要 user-systemd。依赖说明见[安装指南](SETUP.zh-CN.md)。

### 1. 安装 Hypoloom

```bash
git clone --branch main https://github.com/EIMP-Lab/Hypoloom.git
cd Hypoloom
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[monitor]'
```

以上命令安装控制环境与终端监控界面。GPU 实验使用另行选择的 CUDA 环境。

### 2. 配置项目与模型访问

按[安装指南](SETUP.zh-CN.md)启用项目配置并设置模型 API 认证。
文献与通知凭据在下一步填写到本地 Profile 中。

### 3. 准备课题配置

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

**建议**使用已有、可运行的基准评测（benchmark）项目作为实验工作区（workspace）的
代码基础，在 `methodology.yaml` 的 `runtime.target_repo` 中填写其本地 Git 仓库绝对路径。
复用数据加载、基线实现和评测脚本，可减少重复搭建工作，让后续实验更集中于假设验证。
Hypoloom 会从该仓库创建私有实验工作树，原始代码保持只读。

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
选择 **Prepare only**（仅准备）保存配置，或选择 **Start now**（立即启动）开始研究。
配置检查本身不会发起付费模型调用。

### 5. 查看进展

在 Claude Code 中输入：

```text
/autoresearch-monitor
```

选择要查看的研究运行，也可使用初始化返回的终端或 Web 命令。
**q / Leave monitor** 只退出监控，研究继续；**x / End run** 请求取消并等待清理。
人工答复与恢复操作见[使用指南](USAGE.zh-CN.md)。

## 🔍 运行实况

使用 `hypoloom web` 在浏览器中查看研究进展，或通过 `hypoloom monitor` 打开终端界面。
两者均显示 Host 保存的运行记录。回答问题、切换 API 连接和结束运行均需明确操作。
Web 界面默认仅允许本机连接，通过包含访问令牌的链接控制访问；请妥善保管该链接。

### 📊 运行总览

**运行总览**（Operations）显示课题状态、最近一次 Oracle 校验结果，以及假设、实验、
文献和技能的进展。执行可靠性统计帮助你了解输出校验、重试、恢复和任务完成情况。

每条**执行尝试**（Attempt）记录一次具体任务的执行，例如一次模型任务调用或实验运行。
可筛选这些记录，查看失败原因和重试情况。

<p align="center">
  <img src="docs/assets/web-overview.png" alt="Web 运行总览：课题状态、最近一次 Oracle 校验结果、研究活动、执行可靠性与共同研究目标">
</p>

### 🧑‍🔬 专家卡片

专家的研究循环包含文献调研、假设提出、实验设计、执行、分析和更新六个阶段。
**研究智能体**（Agent）通过模型和工具完成其中的具体任务。专家卡片显示当前阶段、
正在工作的智能体、上下文用量和 token 消耗；token 是模型处理文本的计量单位。

协调状态会提示与辩论相关的调度暂停，皇冠标识当前的协调专家。

<p align="center">
  <img src="docs/assets/web-expert.png" alt="专家卡片：当前研究阶段、活跃智能体与实验、上下文用量、执行可靠性，以及辩论相关的调度暂停提示">
</p>

### 🧬 假设谱系

查看研究假设如何分支和演化。颜色区分负责各项假设的专家，连线区分执行修复、
实验方案修订和科学假设调整。停止探索某条分支属于流程控制；科学评估结果单独记录。

<p align="center">
  <img src="docs/assets/Hypo.png" alt="假设谱系图：按专家区分假设，展示评估状态、执行修复、方案修订，以及已停止探索的分支">
</p>

### 📈 指标趋势

对照实验执行前设定的目标，查看各次实验的测量结果。图表按初始假设链分组，
帮助你跟踪不同研究路线的进展。

<p align="center">
  <img src="docs/assets/web-metrics.png" alt="指标趋势图：按初始假设链展示实验测量值、目标线和可点击的图例">
</p>

### 💻 终端界面

**终端用户界面**（TUI）在命令行中显示相同的研究记录，包括目标、结果、预算、
专家活动和假设历史。你可以在这里回答问题、处理 API 恢复、查看诊断信息和结束运行。

<p align="center">
  <img src="docs/assets/TUI.png" alt="终端界面：运行状态、预算、专家活动、假设历史，以及人工答复、诊断和结束运行的操作入口">
</p>

Web 与终端命令、只读访问分享及操作方式见[使用指南](USAGE.zh-CN.md)。

## 📚 文档

| 指南 | 内容 |
| --- | --- |
| [安装与配置](SETUP.zh-CN.md) | 准备依赖、模型认证与首次启动 |
| [使用指南](USAGE.zh-CN.md) | 监控、人工答复、停止与恢复 |
| [操作员指南](docs/operator-guide.zh-CN.md) | 启动审阅、预算、资源与结果解读 |
| [课题配置](config/README.zh-CN.md) | 六个 Profile 文件、字段说明与中文示例 |
| [Profile 模板](profiles/README.zh-CN.md) | 起始模板、配置流程与运行限制 |
| [架构概览](docs/architecture.zh-CN.md) | 组件职责、执行边界与证据 |
| [文档索引](docs/README.zh-CN.md) | GPU、文献、通知等专题指南 |
| [反馈与贡献](CONTRIBUTING.zh-CN.md) | 问题反馈与诊断材料的安全分享 |

每篇指南均有独立中英文版本，顶部提供语言切换链接。
[English homepage](README.md) 直接链接英文指南。

## 🗺️ 后续计划

当前方向，不分先后——是计划而非承诺；具体安排取决于实现与验证情况。

- **更多智能体运行时**：在现有 Claude Code 支持的基础上，计划接入 Codex、
  Kimi Code、OpenCode 等运行时，继续由 Host 调度研究任务，并分别验证兼容性。
- **研究基准评测**：探索将 Hypoloom 适配 AutoResearch 相关基准任务，并设计比较
  实验，评估研究过程与产出质量。

## 🚧 当前适用范围

Hypoloom 仍在持续开发。建议先在自己的模型和环境中运行一个有明确时间与费用上限的
小课题，再安排长实验。执行成功本身不能证明科学结论，需要检查实验记录、测量结果和
最终报告。公开模板是配置示例，不是科学基准。

已有运行保留自己的运行时包和配置，更新本仓库不会升级该运行；不支持跨版本运行迁移。
操作限制见[运行恢复指南](docs/guides/runtime-recovery.zh-CN.md)。

## ⚖️ 许可证

[MIT](LICENSE)，版权 © 2026 EIMP Lab and the Hypoloom Authors。
