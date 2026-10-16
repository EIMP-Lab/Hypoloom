# Profile 模板

[English](README.md) · 简体中文

一个 Profile 用六个 YAML 文件描述一个研究课题。先将模板复制到 `profiles/local/`，
配置私有副本，再通过 `/autoresearch init` 选择它。公开模板使用 `schema_version: 4`。
复制命令与完整字段说明见[个人配置指南](../config/README.zh-CN.md)。

| 模板 | 语言 | 专家视角 |
| --- | --- | --- |
| [multi-expert-zh](templates/multi-expert-zh/) | 简体中文 | 优化、架构和有效性评估 |
| [multi-expert-en](templates/multi-expert-en/) | 英文 | 优化、架构和有效性评估 |

## 文件职责

| 文件 | 内容 |
| --- | --- |
| `goal.yaml` | 研究目标、约束与完成标准 |
| `experts.yaml` | 专家职责、模型覆盖与分支上限 |
| `methodology.yaml` | 运行环境、文献、研究策略与预算 |
| `language.yaml` | 提示、报告与代码注释语言 |
| `notify.yaml` | 通知渠道、接收者与凭据 |
| `safety.yaml` | 数据访问、GPU 选择与沙箱设置 |

目录只保留这六个普通、非符号链接的 UTF-8 文件。未知字段、重复 YAML 键及未填写的
运行路径无法通过检查，说明文档应放在其他目录。

## 创建本地课题

按[中文个人配置指南](../config/README.zh-CN.md)复制并修改中文模板，
将以下占位路径替换为真实绝对路径：

- `methodology.runtime.target_repo`：存放研究代码的干净 Git 仓库。
- `methodology.runtime.conda_env`：完整的实验环境。
- `safety.allow_read_paths`：所需数据及其他获准只读输入。

若已有适合课题的基准评测（benchmark）项目，建议通过
`methodology.runtime.target_repo` 将其作为实验工作区的代码基础。
优先选择包含数据加载、基线实现、实验入口、评测脚本和依赖说明的仓库，
确认基线可在 `methodology.runtime.conda_env` 指定的环境中运行，并在 `goal.yaml`
中说明基线、数据集、评估指标和预期改进。复用这些代码可减少准备工作；
比较基线与改进方案时，应保持评测协议一致。初始化前，提交需要使用的源码修改，
确保 Git 工作区没有未提交的改动。

然后在 `/autoresearch init` 中选择准备好的 Profile。init 也支持引导创建新的本地配置，
不会覆盖已有文件。实验使用私有工作树，原始仓库和获准数据保持只读。
两份模板均继承初始化选择的 Host 模型；需要覆盖时才设置 `experts.defaults.model`
或角色模型。交互会话的 `/model` 选择须明确传给 Host。
详见[初始化指南](../docs/guides/initialization.zh-CN.md)。

## 私有凭据

凭据只填写在被 Git 忽略的本地副本中。文献凭据放在 `methodology.yaml.literature`，
通知令牌与接收者放在 `notify.yaml`。字符串需加引号，包括数字聊天 ID。
文件权限保持 0600，目录权限为 0700；模型认证单独配置。

Host 读取原私有文件，凭据明文不会进入 Agent 输入。不要通过 `--set`、命令参数、
对话、公开模板或 Issue 附件传递秘密。保留已有运行仍引用的文件。
字段与操作见[个人配置指南](../config/README.zh-CN.md)。

## 预算与 GPU

资源、时间和费用限制统一放在 `methodology.yaml.budget`：

| 设置 | 含义 |
| --- | --- |
| `total_budget_usd`、`per_expert_budget_usd` | 运行与专家费用限制 |
| `wallclock_hours` | 运行时限，单位为小时 |
| `experiment_timeout` | 单次实验时限，单位为秒 |
| `agent_invocation` | 单次模型调用限制 |
| `max_active_agents`、`max_active_processes`、`max_active_reviews` | 各类任务的独立并发上限 |
| `provider_rpm_limit`、`provider_max_in_flight` | 按模型分配的请求速率与并发上限 |

模板包含三位专家，运行费用限额为 45 美元、每位专家 15 美元、每次调用 1 美元，
每位专家最多四轮研究、运行时限六小时。这些是可修改的阈值，不是费用预测或启动授权；
在途响应可能超过剩余额度。请为分析、报告和重试留出预算。Agent 与实验时限相互独立。

模板默认使用 CPU，`gpus: []`，Telegram 关闭。GPU 任务需要修改本地配置并明确授权设备；
共享池仍受工作负载下限、实时容量和预留约束。配置变更适用于新运行。
详见 [GPU 资源](../docs/guides/gpu-resource-planning.zh-CN.md)。

## 研究控制

数量设置均为上限，不要求填满。`max_hypotheses_per_round` 限制候选数，`max_forks`
限制活跃研究链，`max_active_agents` 同时约束提案论证与阅读助手。FORWARD/BACKWARD
可并行支持一个提案，不代表两条假设。

Leader 提供有证据支持的建议，同行仍独立推进。
`interaction.leader.min_rounds_before_leader` 按已完成专家周期计数，不按实验数计数。
建议不增加周期或资源权限。实验提交后可交给 Host 等待，结果返回后继续；
前后模型调用均计入正常预算。

研究阶段见[研究工作流](../docs/guides/research-workflow.zh-CN.md)，
会话限制见[上下文与记忆](../docs/guides/context-management.zh-CN.md)。

## 选择性精读

专家可阅读保存的论文原文与提取文本。需要独立阅读助手时，在启动前将
`read_papers` 加入已有工具列表：

```yaml
literature:
  enabled: true
  tools: [web_search, web_fetch, read_papers]
  max_sources: 8
```

以上为配置片段，请保留其他字段与凭据。阅读助手共享 Agent 名额和研究预算，
等待中的父任务仍占名额；容量不足时应串行阅读。原专家负责核查与综合，
公开模板默认不开启该选项。详见[阅读与提案](../docs/guides/reading-and-proposals.zh-CN.md)。

## 完成标准

默认 `metric: null` 允许在研究中确定指标；默认共享目标可由获准的 `SUPPORTED` 结果结束。
需要专家共用评估器时，在 `goal.yaml` 设置 `evaluation: grounding`，并说明完整要求。
必须由一个完整的 `GOAL` 候选满足全部要求，不能拼接局部 `LOCAL` 结果宣布成功。

## 看板端口

模板使用 `notify.yaml.sinks.dashboard.port: 0`，由系统选择空闲回环端口；
实际 bearer URL 见启动日志。请保管访问令牌，重启后使用新地址。
需要固定端口时填写 1–65535；冲突会报告并在原端口重试。
TUI 的独立监听通过 `monitor --web-port` 配置。

## 命令行准备

使用控制环境中的 bootstrap：`paths` 显示位置与权限，`prepare` 预览新 Profile，
`launch` 预览启动参数。`prepare --write` 创建新的私有配置，`launch --start`
执行已审阅的托管启动；预览本身不启动运行，也不覆盖已有配置。

以下命令仅预览中文模板配置，须替换路径：

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap prepare my-topic \
  --template multi-expert-zh --run-id my-topic-preview --cpu-only \
  --set 'methodology.runtime.target_repo=/absolute/research-target' \
  --set 'methodology.runtime.conda_env=/absolute/research-runtime' \
  --set 'safety.allow_read_paths=[/absolute/research-data]'
```

不要在 `--set` 中包含凭据。启动前解决报告的问题并审阅完整配置；本地检查不证明模型可用。
需要引导式设置时使用 `/autoresearch init`，再选择 **Prepare only** 保留配置，
或选择 **Start now** 启动研究。

## 开发说明

### 准备流程与兼容性

bootstrap 复用现有 Profile 准入校验。`--set` 接受已有字段名和 YAML 值，列表需整体
替换。未绑定真实路径的初始配置可先写入以便继续编辑，但退出码 2 和准入阻塞项都不表示
允许启动。已有 Profile 直接复用，不接受 `--set` 或 `--write` 覆盖。
`launch --start` 在 Host 就绪前后重读配置；创建响应不明确时先查询该运行，核实后再操作。
GPU 池的选择与生命周期操作仍须纳入启动审阅。

真实文献访问使用已审阅的 `--literature-adapter openalex` 参数。内联凭据自动派生既有
Host 文件参数；冲突的凭据来源会被拒绝。配置校验不访问模型服务。

当前模板将费用和时限放在 `methodology.yaml.budget`。旧 safety 费用／超时字段和
goal 总时限字段仍可单独使用，并提示迁移；两处重复声明同一限制会报错，即使数值相同。
已有 Profile 和运行不自动改写。`provider_rpm_limit` 默认 60，
`provider_max_in_flight` 省略时跟随 `max_active_agents`。这些值限制本运行每模型
的请求，不代表已查询服务提供方的账户额度。

### 运行时边界

以下 ACI 变更用于解释兼容性，不是新课题需要选择的 Profile 版本：

- **ACI 20**：`read_papers` 仅向 Literature MAIN 提供提交、查询、等待和取消工具。
  读者继承其文献模型，仅有 Read/Glob/Grep 工具，共享 Agent 槽位与专家／运行预算；
  等待中的父任务仍占槽位。没有独立的读者数量或模型字段。PDF I/O 继续共享最多两路请求
  的限制。无读者可启动时，可取消排队并串行阅读；原专家仍负责综合结论和既有报告。
- **ACI 22**：EXECUTION 提交实验后可交还 Host 等待，收集结果后继续同一节点。
  前后调用均计入普通预算，等待不延长单次调用期限。
- **ACI 23**：Leader 撰写建议，同行独立推进。Host 核验依据与资格；建议沿用
  `leader` 模型／语言路由和普通预算，不授予额外周期或父假设权限。
  专家自行决定是否在后续提案中采用。
- **ACI 24**：重建普通模型会话，保留研究工作区。可选记忆限制和用量适配器见
  [上下文管理](../docs/guides/context-management.zh-CN.md)。
- **ACI 26**：FORWARD/BACKWARD 论证服务同一提案，计入 Agent 容量，不产生两个假设。

Agent、Process 和评审并发分别限制各自任务，不会自动并行单个实验中的随机种子或变体；
这类并行由目标代码实现，并须保持评测协议。GPU 可用性由 Host 实时观察。
技能不能绕过所有权、发布、归档校验或资源保护。

Literature MAIN 可读取 `/papers` 下保留的原文和有界文本。
`web_search(scope="academic")` 使用 Host 选定的 OpenAlex 客户端；
`web_fetch(mode="document", url=...)` 获取精确来源，`paper_ref` 复用已准入来源。
读者不会收到凭据明文。详见[阅读与提案](../docs/guides/reading-and-proposals.zh-CN.md)。

显式指标和完成条件继续受现有解析器支持。不得从课题标题臆造阈值，也不得通过翻译标识
改变指标含义。所有权和验收细节见[架构](../docs/architecture.zh-CN.md)。

### 检查与兼容性夹具

在仓库根目录执行：

```bash
./.conda/envs/hypoloom/bin/python -m pytest -q tests/test_public_profiles.py
```

该检查通过生产准入验证两种语言，不启动 Host、模型或 GPU。修改模板时保持两种语言的
控制语义等价。旧解析器夹具保留在 [tests/fixtures/profiles](../tests/fixtures/profiles/)；
新课题使用 v4。解析兼容不代表支持跨版本运行恢复。完整候选验证遵循
[G0–G5](../docs/qualification.zh-CN.md)。

密钥、真实 Profile、本地 `.claude/settings.json`、研究数据和运行记录不提交到 Git。
