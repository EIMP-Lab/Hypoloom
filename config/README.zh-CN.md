# 个人 Profile 配置

[English](README.md) · 简体中文

一个 Profile 用六个 YAML 文件描述一个研究课题。本指南以
[中文模板](../profiles/templates/multi-expert-zh/)为例，解释其中的全部字段、
注释中的可选项及常用扩展配置。请先准备本地副本，再执行 `/autoresearch init`。

## 准备私有 Profile

在 Hypoloom 仓库根目录，将模板复制到新目录：

```bash
mkdir -p profiles/local
(umask 077 && mkdir profiles/local/my-topic && \
  cp profiles/templates/multi-expert-zh/*.yaml profiles/local/my-topic/)
```

该命令创建权限为 0700 的目录和权限为 0600 的文件。如果 `my-topic` 已存在，
复制操作会停止；请编辑已有 Profile 或换一个名称。只修改被 Git 忽略的
`profiles/local/my-topic/`。目录内只保留以下六个普通、非符号链接的 UTF-8 YAML 文件，
说明笔记放在其他位置。

| 文件 | 作用 | 首次配置重点 |
| --- | --- | --- |
| [goal.yaml](#goalyaml) | 研究目标与完成标准 | 写明课题、约束和预期证据。 |
| [experts.yaml](#expertsyaml) | 专家职责与模型偏好 | 调整专业分工和研究链上限。 |
| [methodology.yaml](#methodologyyaml) | 运行环境、文献、研究策略与预算 | 替换路径，填写文献凭据，设置可承受的限额。 |
| [language.yaml](#languageyaml) | 研究输出语言 | 保留 `zh-CN`，或设置需要的语言标签。 |
| [notify.yaml](#notifyyaml) | 本地日志、看板与外部通知 | 确认接收者后再启用外部通知。 |
| [safety.yaml](#safetyyaml) | 数据访问、GPU 与沙箱限制 | 替换数据路径；CPU 任务保留 `gpus: []`。 |

将所有 `/REPLACE_ME/` 占位路径替换为已经存在的绝对路径。模型 API 认证单独配置，
见[安装指南](../SETUP.zh-CN.md)。下方示例来自中文模板，凭据有意留空；数值是示例上限，
不代表建议消费金额。未知字段、重复 YAML 键及未填写的运行路径无法通过检查。

## goal.yaml

说明研究需要证明什么，以及何时结束。

```yaml
title: 改进一个可测量的研究系统
description: >-
  从三个互补视角研究目标项目。每位专家设计可复现的实验并明确测量方法与解析器；
  被接受的结果必须支持相对于明确基线的具体改进。
metric: null
constraints:
  - 保持原始仓库和数据集只读。
  - 说明不确定性，并保留失败实验作为证据。
stop_conditions:
  max_rounds_per_expert: 4
```

| 字段 | 含义与取值 |
| --- | --- |
| `title` | 非空课题标题，显示在运行摘要中。 |
| `description` | 非空研究目标。应说明基线、期望改进、测量方法和证据要求；`>-` 将后续多行合并为一个字符串。 |
| `metric` | `null` 表示允许研究过程确定指标。需要指定统一指标时，使用下方对象格式。 |
| `constraints` | 研究要求与排除事项的列表；这些文字要求不能替代沙箱权限。 |
| `stop_conditions.max_rounds_per_expert` | 每位专家完成研究周期的上限，正整数；`4` 不代表四次实验。 |

模板默认采用共享目标模式，一个获准的 `SUPPORTED` 结果即可使运行结束。
`metric: null` 本身不要求所有专家使用共同评估器。需要固定指标时，可仅替换
`metric` 项，例如：

```yaml
metric:
  name: accuracy
  direction: maximize
  target: 0.90
```

| 可选字段 | 含义 |
| --- | --- |
| `metric.name` | 评估中使用的指标名称；示例 `accuracy` 表示准确率。 |
| `metric.direction` | `maximize` 表示越大越好，`minimize` 表示越小越好。 |
| `metric.target` | 有限数值阈值，单位须与指标一致。 |
| `evaluation` | 设置为 `grounding`，要求专家共用在 Grounding 阶段建立的验收流程；在 `description` 中说明完整要求。 |

设置 `evaluation: grounding` 时，保留 `metric: null`，不填写 `completion` 或
`stop_conditions.target_metric`。必须由一个完整的 `GOAL` 候选满足全部已确认要求，
不能拼接多个局部 `LOCAL` 结果宣布成功。详见[研究工作流](../docs/guides/research-workflow.zh-CN.md)。
时间与费用上限统一放在 `methodology.yaml.budget`。

## experts.yaml

定义专家名单及各专家继承的默认设置。

```yaml
defaults:
  # 省略 model，使用初始化时为 Host 选择的模型。
  effort: high
  max_forks: 2
  max_hypotheses_per_round: 2
# 可选：单独配置共享 Grounding 查询与研究阶段；省略时使用 defaults。
# grounding:
#   model: REPLACE_WITH_PINNED_GROUNDING_MODEL
#   effort: medium
experts:
  - name: optimizer
    role: 优化研究专家
    persona: 区分算法改进与调参噪声。
    focus: [优化, 收敛, 受控比较]
  - name: architect
    role: 系统与架构研究专家
    persona: 从机制与瓶颈解释性能变化。
    focus: [架构, 资源效率, 可复现性]
  - name: evaluator
    role: 测量与有效性研究专家
    persona: 检查数据泄漏、混杂因素与不稳定的评估规则。
    focus: [测量, 统计有效性, 稳健性]
```

| 字段 | 含义与取值 |
| --- | --- |
| `defaults.effort` | 请求的推理强度：`low`、`medium`、`high`、`xhigh` 或 `max`；是否支持取决于所选模型与运行时。 |
| `defaults.max_forks` | 每位专家同时活跃的研究链上限，正整数。 |
| `defaults.max_hypotheses_per_round` | 每轮提出的假设数量上限，正整数；不要求填满名额。 |
| `experts` | 非空专家列表，每项使用以下字段。 |
| `experts[].name` | 唯一且稳定的标识，长度为 1–128；首字符为英文字母或数字，其余可使用英文字母、数字、`_`、`.`、`-`。修改角色说明时应保持标识稳定。 |
| `experts[].role` | 专业领域或职责说明。 |
| `experts[].persona` | 对研究方法和判断原则的具体要求。 |
| `experts[].focus` | 非空且不重复的关注方向或推理原则列表。 |

模板省略模型标识，各阶段继承初始化时确认的 Host 模型。交互会话的 `/model` 选择
需要明确传给 Host。确实需要不同模型或推理强度时，使用以下覆盖设置：

| 可选字段 | 含义 |
| --- | --- |
| `defaults.model` | Profile 默认模型，填写供应商支持的确切模型标识。 |
| `grounding.model`、`grounding.effort` | 共享 Grounding 的模型与推理强度；未填写的字段先继承 `defaults`，再使用 Host 选择。该阶段发生在各专家研究周期之前。 |
| `experts[].model`、`experts[].effort` | 覆盖某一专家的默认模型或推理强度。 |
| `experts[].max_forks`、`experts[].max_hypotheses_per_round` | 覆盖某一专家的对应数量上限。 |
| `experts[].stage_overrides` | 以阶段名称为键、以 `model` 和／或 `effort` 为值的映射；阶段设置优先于专家设置。 |

例如，在已有专家条目内部加入以下片段，单独设置分析阶段的推理强度：

```yaml
stage_overrides:
  inner_loop.analysis:
    effort: high
```

可用阶段名称包括 `literature_review`、`hypothesis_proposal`、
`hypothesis_proposal.forward`、`hypothesis_proposal.backward`、
`hypothesis_proposal.synthesis`、`inner_loop.code`、`inner_loop.run`、
`inner_loop.analysis`、`hypothesis_update`、`cycle_report`、`self_evolve`、
`observer`、`debate` 和 `leader`。具体提案子阶段的设置优先于通用
`hypothesis_proposal` 设置。个人 `stage_overrides.grounding` 不生效，应使用共享
`grounding` 块。初始化会展示生效值与来源，详见[初始化指南](../docs/guides/initialization.zh-CN.md)。

## methodology.yaml

统一配置运行路径、文献访问、研究策略和预算。

```yaml
schema_version: 4
runtime:
  target_repo: /REPLACE_ME/research-target
  conda_env: /REPLACE_ME/project-runtime
literature:
  enabled: true
  tools: [web_search, web_fetch]
  max_sources: 8
  default_route: direct  # 无需代理的用户保留此值；可选 auto 或 proxy_only。
  # 需要代理时，在本地 Profile 填 proxy_url；auto 无须枚举域名。
  # proxy_url: "http://127.0.0.1:7890"
  # proxy_domains: []  # 精确域名覆盖：仅代理。
  # direct_domains: [] # 精确域名覆盖：仅直连；不能与上项重叠。
  # 仅在 Git 忽略的本地 Profile（0600）中填写，勿修改公开模板。
  # 仅 Host 读取：不进入准入契约或 Agent 输入。
  openalex_api_key: ""
  unpaywall_email: ""  # 仅 Unpaywall PDF 后备需要。
inner_loop:
  max_iterations_per_hypothesis: 4
  repair_attempts: 2
output_correction:
  max_failed_validations: 3
interaction:
  observer:
    coalesce_reports: true
  leader:
    enabled: true
    check_after: expert_round
    min_rounds_before_leader: 1
  debate:
    enabled: true
    rounds: 3
    min_interval_seconds: 1800
    min_new_outcomes: 2
graph_evolution:
  enabled: true
  stagnation_rounds: 2
memory:
  retention: retain
  max_transcript_bytes: 134217728
  max_memory_bytes: 8388608
  max_plugin_bytes: 1048576
budget:
  gpu_vram_reserve_mb: 512  # 每卡绝对保留量下限；启用 GPU 时还须配置工作负载下限。
  gpu_vram_reserve_percent: 5  # 已核定工作负载的百分比，向上取整到 64 MiB。
  total_budget_usd: 45
  per_expert_budget_usd: 15
  wallclock_hours: 6
  experiment_timeout: 1800  # 秒
  max_experiments: 96
  max_active_hypotheses: 6
  max_active_agents: 3
  provider_rpm_limit: 60  # 本运行每个模型的请求份额，不代表已知的账号限额。
  # provider_max_in_flight: 3  # 可选覆盖；省略时跟随 max_active_agents。
  max_active_processes: 2
  max_active_reviews: 3
  max_retries: 2
  max_infrastructure_retries: 2
  max_process_lost_retries: 2
  agent_invocation:
    max_turns: 40
    max_tokens: 80000
    max_output_bytes: 262144
    wall_seconds: 600
    max_cost: "1"
    token_enforcement_mode: enforce
```

### 运行环境与文献

| 字段 | 含义与取值 |
| --- | --- |
| `schema_version` | 保留 `4`，对应当前六文件 Profile 格式。 |
| `runtime.target_repo` | 存放研究代码的干净 Git 仓库绝对路径。建议使用已有、可运行的 benchmark 项目，复用其基线实现与评测脚本。实验使用私有工作树，原始仓库保持只读。 |
| `runtime.conda_env` | 完整实验环境的绝对前缀路径，包含 Python 解释器与所需依赖；GPU 实验须选择兼容的 CUDA 环境。 |
| `literature.enabled` | 是否启用已配置的文献工具。 |
| `literature.tools` | 可用值为 `web_search`、`web_fetch` 和可选的 `read_papers`。模板只启用检索与获取；阅读助手共享 Agent 名额和预算。 |
| `literature.max_sources` | 每位专家文献数量预算所用的上限，正整数；模板为 `8`。 |
| `literature.default_route` | `direct` 为直连；`auto` 初始直连，在符合条件的连接失败后尝试后备路径，并短暂优先使用成功的代理；`proxy_only` 为代理路径。`auto` 和 `proxy_only` 必须同时配置 `proxy_url`；下方域名规则优先。 |
| `literature.openalex_api_key` | OpenAlex 文献检索密钥；启动前只在私有本地文件中填写带引号的字符串。 |
| `literature.unpaywall_email` | 使用 Unpaywall PDF 后备时需要的联系邮箱；其他情况可留空。 |

| 可选文献字段 | 含义 |
| --- | --- |
| `literature.proxy_url` | 服务器 HTTP CONNECT 代理，例如 `http://127.0.0.1:7890`；须同时设置 `auto`、`proxy_only` 或非空 `proxy_domains`，独立于模型与通知代理。 |
| `literature.proxy_domains` | 必须走代理的精确公共 HTTPS 主机名列表，不含协议、路径、端口或通配符。 |
| `literature.direct_domains` | 必须直连的精确公共 HTTPS 主机名列表，不得与 `proxy_domains` 重叠。 |
| `literature.semantic_scholar_api_key` | 默认 Semantic Scholar DOI 后备查询的可选密钥；启动时为空或省略则匿名查询。 |
| `literature.elsevier_api_key` | 可选的 Elsevier 官方文章接口密钥，为新 Host 启用该访问路径。 |
| `literature.elsevier_insttoken` | 机构令牌，仅在 Elsevier 为所选接口访问签发后填写。 |

可选字段加入已有 `literature` 块，不要重复声明该块。API 密钥不等于出版社订阅授权。
已经配置的 Semantic Scholar 密钥会在每次获准查询前重读；删除密钥或使文件不可读会
导致该次查询失败，不会静默改成匿名访问。路由、权限验证、浏览器登录和 PDF 导入见
[机构全文访问](../docs/guides/institutional-access.zh-CN.md)。

### 研究策略与记忆

| 字段 | 含义与取值 |
| --- | --- |
| `inner_loop.max_iterations_per_hypothesis` | 一条假设链的科学迭代次数上限，正整数。 |
| `inner_loop.repair_attempts` | 执行修复次数上限，非负整数；与科学修订、模型调用重试分别计数。 |
| `output_correction.max_failed_validations` | 单次模型调用中，结构化提交校验失败次数的上限，正整数。达到上限后结束本次纠错机会；外层重试仍受其独立预算约束。 |
| `interaction.observer.coalesce_reports` | 允许成功的 Observer 结果覆盖它已经审阅且符合条件的其他专家报告，减少重复观察。 |
| `interaction.leader.enabled` | 是否允许依据证据选择 Leader 并提出建议。 |
| `interaction.leader.check_after` | 保留 `expert_round`；`cycle_report` 是别名。在专家周期完成后检查，包括自演化成功或正式耗尽。其他值不会启用不同的触发时机。 |
| `interaction.leader.min_rounds_before_leader` | 具备 Leader 选择条件前，至少完成的专家周期数，正整数；不是实验次数。 |
| `interaction.debate.enabled` | 是否允许在满足协调条件时发起辩论。 |
| `interaction.debate.rounds` | 必须为 `3`，当前辩论流程固定三轮。 |
| `interaction.debate.min_interval_seconds` | 辩论之间的最短间隔，单位为秒，非负整数。 |
| `interaction.debate.min_new_outcomes` | 再次辩论前至少新增的结果数量，非负整数。 |
| `graph_evolution.enabled` | 是否启用共享研究图的管理与演化。 |
| `graph_evolution.stagnation_rounds` | 审阅研究进展时参考的已完成周期窗口，正整数；不会强制修改研究图或作出科学结论。 |
| `memory.retention` | `retain` 保留会话文件；`archive_on_close` 在运行终止且没有未结束执行任务后归档符合条件的会话文件。 |
| `memory.max_transcript_bytes` | 保存的会话记录大小上限；模板为 134217728 字节，即 128 MiB。 |
| `memory.max_memory_bytes` | 专家记忆内容大小上限；模板为 8388608 字节，即 8 MiB。 |
| `memory.max_plugin_bytes` | 技能插件内容大小上限；模板为 1048576 字节，即 1 MiB。 |

三个字节上限均为正整数。可选上下文设置放在同一个 `memory` 块中：

| 可选字段 | 含义 |
| --- | --- |
| `memory.working_context_tokens` | 显式工作容量上限，正整数；省略时跟随解析出的模型窗口。 |
| `memory.context_window_tokens` | 已确认的部署上下文窗口，正整数；未知时省略。 |
| `memory.runtime_context_tokens` | 已确认的运行时容量上限，正整数；未知时省略。 |
| `memory.max_inline_input_bytes` | 内联输入总字节上限，正整数，默认 49152；完整来源仍可通过文件引用提供。 |
| `memory.input_usage_semantics` | 根据供应商输入 token 口径选择：`separate_cache` 为缓存分开计数，`includes_cache` 为已含缓存，`unknown` 为未知。 |
| `memory.context_metadata` | 是否允许在必要时进行有界的模型元数据只读查询，布尔值，默认 `true`。 |

上下文设置不替代单次调用的 token 或输出限制。详见[上下文与记忆](../docs/guides/context-management.zh-CN.md)。

### 整组运行预算

以下字段均位于 `budget` 块中。费用与时间须为正数；数量须为正整数，重试次数允许为零。
GPU 预留量也允许为零。

| 字段 | 含义与单位 |
| --- | --- |
| `budget.total_budget_usd` | 整个运行的模型费用额度，单位为美元；模板为 45。 |
| `budget.per_expert_budget_usd` | 每位专家的模型费用额度，单位为美元；模板为 15。共享任务也计入整组额度。 |
| `budget.wallclock_hours` | 整个运行的墙钟时间额度，单位为小时；模板为 6。 |
| `budget.experiment_timeout` | 单次实验 Process 的最长运行时间，单位为秒；模板为 1800。 |
| `budget.max_experiments` | 整个运行的实验数量上限。 |
| `budget.max_active_hypotheses` | 同时活跃的假设数量上限。 |
| `budget.max_active_agents` | 并发 Agent 上限，包括提案论证任务和阅读助手；等待中的父 Agent 仍占名额。 |
| `budget.provider_rpm_limit` | 本运行分配给每个模型的每分钟请求份额，不是自动探测到的账号总限额。 |
| `budget.provider_max_in_flight` | 可选的供应商请求并发上限；模板省略时跟随 `max_active_agents`。 |
| `budget.max_active_processes` | 并发实验 Process 上限。 |
| `budget.max_active_reviews` | 并发 Review 上限。 |
| `budget.max_retries` | 可重试的模型／输出失败后的额外尝试次数；`2` 表示初次尝试之外最多重试两次。 |
| `budget.max_infrastructure_retries` | 基础设施故障的独立重试额度，同时受剩余运行与调用预算约束。 |
| `budget.max_process_lost_retries` | 实验 Process 丢失后的额外尝试次数，不代表所有失败的科学结果都可重新运行。 |
| `budget.gpu_vram_reserve_mb` | 每张 GPU 的绝对显存预留下限，单位为 MiB；模板为 512，字段名使用 `mb`。 |
| `budget.gpu_vram_reserve_percent` | 0–100 的整数百分比，模板为 5。正值时取工作负载百分比、绝对预留下限和 512 MiB 三者的最大值，再向上取整到 64 MiB；为零时仅使用绝对预留量。 |

费用上限约束后续派发，在途响应报告的费用可能超过剩余额度；未知费用单独保留。
请为重试、分析和最终报告预留空间。并发数是上限，不要求占满。

模板默认仅使用 CPU。启用 GPU 前，需要用 `budget.min_vram_per_experiment` 指定
有测量依据的工作负载下限，单位为 MiB，并确认 Host 的具体设备授权。
`budget.initial_vram_per_experiment` 是已配置共享池时可选的初始工作集估算，
不得低于工作负载下限。预留计算、固定分配与共享池配置见
[GPU 资源规划](../docs/guides/gpu-resource-planning.zh-CN.md)。

### 单次模型调用预算

每次 Agent 调用还有独立限制，不替代整组运行或每位专家的预算。

| 字段 | 含义与单位 |
| --- | --- |
| `budget.agent_invocation.max_turns` | 单次调用的模型交互轮数上限，正整数。 |
| `budget.agent_invocation.max_tokens` | 单次调用的 token 总额度，正整数；不是模型上下文窗口大小。 |
| `budget.agent_invocation.max_output_bytes` | 返回输出的字节上限，正整数；模板为 262144，即 256 KiB。 |
| `budget.agent_invocation.wall_seconds` | 单次调用的时限，单位为秒，正数；模板为 600。 |
| `budget.agent_invocation.max_cost` | 单次调用的美元费用上限，使用规范的非负十进制字符串，例如 `"1"` 或 `"0.5"`。付费任务应设置正额度，`"0"` 不表示无限；与整组费用分别约束。 |
| `budget.agent_invocation.token_enforcement_mode` | `enforce` 执行 token 限额；`observe` 只记录 token 用量，不执行这一 token 上限，其他限制继续有效。除非明确只需观察，否则保留 `enforce`。 |

## language.yaml

为研究提示和输出指定语言标签。

```yaml
prompt_language: zh-CN
report_language: zh-CN
code_comment_language: zh-CN
```

| 字段 | 含义 |
| --- | --- |
| `prompt_language` | 研究指令使用的语言。 |
| `report_language` | 研究报告要求使用的语言。 |
| `code_comment_language` | 生成代码时要求使用的注释语言。 |

中文模板使用 `zh-CN`，英文模板使用 `en`。这些字段控制研究语言，不改变监控界面的语言。
翻译文字时，保留 YAML 键名、模型标识、工具名称和专家标识。

## notify.yaml

模板记录本地日志并启用回环地址看板；Telegram 默认关闭，填写凭据并确认接收者后再开启。

```yaml
sinks:
  log:
    level: info
    path: events.jsonl
  telegram:
    enabled: false
    # 仅在本地 Profile 中填写，并保持本文件权限 0600；无需单独的 env。
    # token 和 chat ID 均须加引号，不能提交到 Git 或粘贴进对话。
    bot_token: ""
    chat_id: ""
  dashboard:
    enabled: true
    host: 127.0.0.1
    port: 0  # 由系统分配空闲端口；填写 1–65535 可固定端口。
events:
  # new_best_result：实验首次测量／同组指标刷新；goal_verdict_recorded：整体 GOAL 判定。
  telegram: [grounding_question, grounding_answered, new_best_result, goal_verdict_recorded, leader_started, debate_started, expert_round_done, provider_waiting, provider_retry_requested, provider_recovered, literature_waiting, literature_retry_requested, literature_recovered, error, run_finished, run_briefing]
```

| 字段 | 含义与取值 |
| --- | --- |
| `sinks.log.level` | 最低日志级别：`debug`、`info`、`warning`（或 `warn`）、`error`、`critical`。 |
| `sinks.log.path` | 相对于本运行通知输出目录的路径。可保留 `events.jsonl`；其他相对路径不得含 `..`，也不能进入 `cursors/`。 |
| `sinks.telegram.enabled` | 是否启用 Telegram；确认令牌与接收者后才设为 `true`。 |
| `sinks.telegram.bot_token` | 私有机器人令牌，作为带引号的字符串在本地填写。 |
| `sinks.telegram.chat_id` | 已确认的接收会话 ID，数字 ID 也须写为带引号的字符串。 |
| `sinks.dashboard.enabled` | 是否启用 Host 托管的只读 Web 看板。 |
| `sinks.dashboard.host` | 回环监听地址，保留 `127.0.0.1`；公共接口地址会被拒绝。 |
| `sinks.dashboard.port` | `0` 由系统分配空闲端口；1–65535 为固定端口。实际私有 bearer URL 见启动输出。 |
| `events.telegram` | Telegram 启用后投递的事件名称列表；模板选择的事件含义见下表。 |

| 事件 | 含义 |
| --- | --- |
| `grounding_question`、`grounding_answered` | 待回答的研究问题及其已记录答复；需要接收人工问题时，应保留 `grounding_question`。 |
| `new_best_result` | 某个可比较实验组的首次测量或更优测量，不代表整体目标已达成。 |
| `goal_verdict_recorded` | 已记录的整体研究目标判定。 |
| `leader_started`、`debate_started` | Leader 或辩论协调活动开始。 |
| `expert_round_done` | 一个专家研究周期完成。 |
| `provider_waiting`、`provider_retry_requested`、`provider_recovered` | 模型访问进入等待、请求重试或恢复。 |
| `literature_waiting`、`literature_retry_requested`、`literature_recovered` | 文献访问进入等待、请求重试或恢复。 |
| `error` | 需要关注的错误。 |
| `run_finished` | 运行结束。 |
| `run_briefing` | 运行综合简报。 |

可选通知设置：

| 字段 | 含义 |
| --- | --- |
| `proxy_url` | 顶层字段，指定仅供通知使用的服务器 HTTP CONNECT 代理，例如 `http://127.0.0.1:7891`；在 init 前配置。 |
| `sinks.webhook.enabled` | 是否启用已明确确认的 webhook 接收端。 |
| `sinks.webhook.url` | 私有接收地址：使用 HTTPS，仅回环接收端允许 HTTP；不得包含内嵌账号密码或 URL 片段。 |
| `sinks.webhook.token` | 接收端所需的可选 bearer 令牌。 |
| `events.webhook` | 已启用 webhook 的事件选择，使用受支持的事件名称。 |
| `events.log` | 可选的本地日志事件选择；省略时选择全部受支持的通知事件类型，再按日志级别过滤。 |

通知代理与模型、文献代理相互独立。回环地址指 Linux 服务器，而不是桌面客户端。
已有运行及 **Retry Telegram delivery** 不会热加载代理修改。看板 bearer URL 应保密，
重启后使用新地址。通知投递、服务器代理和排障见[通知指南](../docs/guides/notifications.zh-CN.md)。

## safety.yaml

限定数据访问、计算资源及 shell／文件系统操作。

```yaml
allow_read_paths: [/REPLACE_ME/research-data]
sandbox_os: linux_bwrap
# [] 表示仅使用 CPU；启用 GPU 还需要 Host 对精确 UUID 的授权。
gpus: []
max_gpus_per_experiment: 1
bash:
  network_allowed: false
  deny_patterns: ["rm -rf /", "git push", "sudo", "pip uninstall"]
filesystem:
  write_only_within_worktree: true
  # 按需添加已经存在的项目相对保护路径，例如 data/。
  protected_paths: [.git/]
```

| 字段 | 含义与取值 |
| --- | --- |
| `allow_read_paths` | 已存在的数据与其他获准只读输入的绝对路径列表；无需额外数据时可设为空列表。不得包含凭据目录。 |
| `sandbox_os` | 保留 `linux_bwrap`，使用受支持的 Linux bubblewrap 沙箱。 |
| `gpus` | `[]` 表示仅 CPU；`auto` 使用 Host 已授权设备范围；列表可用 GPU UUID 或非负整数索引进一步缩小范围，推荐稳定的 UUID。该字段本身不授予设备权限。 |
| `max_gpus_per_experiment` | 单次实验的 GPU 数量上限，正整数；`gpus: []` 时，填写 `1` 也不会启用 GPU。 |
| `bash.network_allowed` | 是否允许研究 shell 访问网络，布尔值；`false` 表示关闭，Host 管理的文献访问独立配置。 |
| `bash.deny_patterns` | 拒绝 shell 命令所用的有效正则表达式列表；保留模板限制，按课题需要添加规则。 |
| `filesystem.write_only_within_worktree` | 保留 `true`，表达仅在工作树内写入；设为 `false` 也不会增加沙箱可写路径。 |
| `filesystem.protected_paths` | 需要保护的已有项目相对路径，例如 `.git/`，也可加入 `data/`；仓库元数据始终受保护。 |

GPU 执行还需要合适的实验环境、工作负载显存配置，以及 init 时明确的设备授权。
获准读取数据和源码仓库，不代表获准修改它们的原件。

## 凭据与本地检查

只在本地编辑器中向被 Git 忽略的 Profile 填写秘密。文件须为普通非符号链接文件，
权限保持 0600，目录权限保持 0700；存放位置应在 Agent／实验的数据与运行环境挂载目录之外。
不要通过 `--set`、命令参数、对话、公开模板或 Issue 附件传递秘密值。
反馈配置问题时分享空白模板。

Host 从原始私有 YAML 读取文献与通知凭据，凭据明文不会进入已准入的研究契约或
Agent 输入。保留文件的可读性，以供投递和恢复使用。已有 `credentials_file`、dotenv
及显式通知 `*_env` 来源仍受支持；同一服务只选一种来源，填写内联值时删除旧来源声明。
[dotenv 示例](hypoloom.env.example)用于该兼容路径；已有运行仍引用的文件须保留。

模型认证使用选定的 Claude 配置或环境变量，不放在这六个文件中。详见
[安装指南](../SETUP.zh-CN.md)和 [API 恢复](../docs/guides/provider-recovery.zh-CN.md)。
可选机构浏览器的登录会话存放在私有 Docker 卷中，参见
[服务器浏览器配置](institutional-browser.compose.yaml)与[机构访问指南](../docs/guides/institutional-access.zh-CN.md)。

仅检查位置与权限、不输出秘密值：

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap paths --profile my-topic
```

`profile_credentials` 指出 YAML 位置；`credentials` 与 `credentials_template`
描述的是旧 dotenv 路径。

## 初始化已准备的 Profile

在仓库根目录启动 `claude`，然后输入：

```text
/autoresearch init
```

选择已有的 `my-topic` Profile，核对生效模型、路径、凭据、CPU/GPU 分配、时间与费用上限，
以及通知接收者。选择 **Prepare only** 保存／审阅配置，或选择 **Start now** 启动研究。
配置检查不会发起付费模型生成调用。init 也支持引导创建新的本地 Profile，不会覆盖已有配置。

Profile 修改用于新运行，不会替换正在运行的课题已经接受的配置。
启动审阅与操作员动作见[初始化指南](../docs/guides/initialization.zh-CN.md)和[使用指南](../USAGE.zh-CN.md)。
