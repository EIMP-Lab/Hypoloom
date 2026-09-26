# Profile templates / 双语模板

Public templates use schema v4 and the multi-expert research workflow.
`schema_version` numbers the Profile authoring format: v4 is the format of
this open-source release, and earlier versions predate it, surviving only as
parser test fixtures — there is nothing older to seek or choose. Choose one
complete English or Chinese set:

中文:`schema_version` 是创作格式的版本号;v4 是本次开源发布的格式,更早的版本
早于公开发布,仅以解析器测试夹具形式保留,无需查找或选择。

| Template | Language | Experts | Purpose |
|---|---|---:|---|
| [multi-expert-en](templates/multi-expert-en/goal.yaml) | English | 3 | Optimization, architecture and evaluation perspectives |
| [multi-expert-zh](templates/multi-expert-zh/goal.yaml) | 中文 | 3 | 优化、架构和有效性三个互补视角 |

Each Profile contains **exactly six** regular, non-symlink UTF-8 YAML files.
Put documentation outside the Profile directory. Unknown fields, duplicate
keys, mixed layouts and unbound runtime paths fail admission.

## Private credentials / 私有凭据

Fill keys directly in the ignored local Profile, after preparing the six files.
No separate env is required. In `methodology.yaml`, use
`literature.openalex_api_key` and optional `literature.unpaywall_email`.
In `notify.yaml`, use `sinks.telegram.bot_token` / `chat_id`, optional
`sinks.webhook.url` / `token` and `proxy_url`. Quote all credential strings,
including chat IDs. Keep private files mode 0600 and never populate public templates.

Admission removes values and derives references to the original YAML files;
only Host consumers read them. Existing dotenv/environment sources remain
compatible, but cannot be mixed with inline credentials for the same service.
No automatic migration of existing Profiles or runs is performed.
Model authentication continues to use the selected CC settings/environment.

中文：文献密钥和 Telegram token 直接填写本地 Profile，无需维护独立 env。
准入会剥离明文，Host 复用既有私有读取器读取原 YAML，不生成第二份凭据文件。
保留 0600 权限、Git 忽略和运行/Agent 数据隔离；不要通过 `--set`、聊天或 Issue
传递真实值。详细步骤、字段表和兼容说明见[个人配置教程](../config/README.md)。

| File | Meaning / 职责 |
|---|---|
| `goal.yaml` | Goal, constraints, completion choice and stopping limits / 目标与完成要求 |
| `experts.yaml` | Stable expert names, persona, model and branching / 专家背景及模型配置 |
| `methodology.yaml` | `schema_version: 4`, runtime paths, research controls and all time/financial/invocation budgets |
| `language.yaml` | `prompt_language`, `report_language`, `code_comment_language` |
| `notify.yaml` | Notification sinks/events and private local credentials / 通知及本地私有凭据 |
| `safety.yaml` | Read roots, GPU permission and sandbox / 数据访问与执行权限 |

## Dashboard port / 看板端口

Both templates set `notify.yaml.sinks.dashboard.port: 0`. Zero (or an omitted
port) asks the OS to assign a free loopback port when the Host starts the
dashboard. Its startup log prints the actual bearer URL; keep it private and
use the new URL after restart. Set an integer from 1 to 65535 only when a fixed
port is needed. A fixed-port conflict is reported and retried on that same
port. Profile edits apply to newly created runs; existing runs keep their
admitted settings. A TUI owns a separate Web listener, configured by
`monitor --web-port` (also defaults to `0`).

中文：两套模板均使用 `notify.yaml.sinks.dashboard.port: 0`。填写 `0` 或省略端口时，
系统在 Host 启动看板时分配空闲 loopback 端口，启动日志输出实际 bearer 地址；
请保密，并在重启后使用新地址。需要固定端口时填写 1–65535，被占用时会报错并在
原端口重试。Profile 修改仅影响新建运行，既有运行保留已接纳的设置。
TUI 自己的 Web 监听由 `monitor --web-port` 配置，也默认使用 `0`。

## Create a local topic / 创建本地课题

Use `/autoresearch init` to preview a filled template, then write its six files
into a fresh ignored `profiles/local/<name>` directory after confirmation.
It rejects an existing destination. Adopt an existing local Profile unchanged when
its admitted semantics already match the request.

Replace `REPLACE_WITH_PINNED_MODEL` with the exact model selector supported by
the chosen provider. Replace `/REPLACE_ME/research-target`,
`/REPLACE_ME/project-runtime` and `/REPLACE_ME/research-data` with existing,
reviewed paths. The target is a clean Git repository; original target/data paths
stay read-only to experiments. Runtime/model placeholders prevent a template
from silently selecting a machine, account or research project.

中文：通过 `/autoresearch init` 选择模板并审阅复制目标。中文模板不仅翻译本说明，也翻译
目标、专家背景、研究原则及注释。两种语言的字段名、专家 ID、预算、权限和数值控制保持
一致；`language.yaml` 分别选择 `en` 或 `zh-CN`。实际模型、数据和运行环境必须由你填写。

## Deterministic preparation / 确定性准备

The interactive entry stays `/autoresearch init`. Its tracked implementation
uses `python -B -m hypoloom.bootstrap` with the qualified control Python:

- `paths [--profile <name>]`: show the default and selected credential paths,
  existence, permissions and template locations without displaying values.
- `prepare <name> --template <template> --set section.field=YAML ...`: fill
  the prospective six files in memory and run ordinary Profile admission.
  Add `--write` only after approving those contents. It creates a new private
  directory and never edits the source or an existing Profile.
- `launch <name> ...`: derive the managed Host and client arguments. Add
  `--start` only for an authorized fresh run, after the local Profile exists.
  It checks the actual Profile before/after Host readiness and reconciles an
  ambiguous create response by querying the exact run, without duplicate creation.

For example, this command previews only; replace paths and the exact model:

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap prepare my-topic \
  --template multi-expert-en --run-id my-topic-202609221010 --cpu-only \
  --set 'experts.defaults.model=your-exact-provider-model' \
  --set 'methodology.runtime.target_repo=/absolute/research-target' \
  --set 'methodology.runtime.conda_env=/absolute/research-runtime' \
  --set 'safety.allow_read_paths=[/absolute/research-data]'
```

`--set` uses the existing section/field names and YAML values; replace an entire
list when necessary. Budgets, research policy and scientific text remain explicit
Profile choices. An unbound starter can be written for later editing, but exit 2
and admission blockers never authorize a launch. For real work, include the
reviewed `--literature-adapter openalex` options; inline Profile credentials
derive the existing Host file argument automatically. Conflicting CLI sources
are refused. Configuration checks do not contact providers. The helper neither
creates credentials nor deploys a GPU pool; existing pool selection and consent
remain in the interactive flow.

中文：`paths` 让隐藏的个人配置位置可见；`prepare` 在内存中填写并校验新模板，
默认不写文件，确认后加 `--write` 才创建新目录。已有 Profile 只读复用，拒绝用
`--set` 或 `--write` 覆盖。`launch` 默认只展示由同一参数源生成的命令；已授权真实
启动时才加 `--start`，并在 Host 就绪前后复查实际配置。请求结果不明确时先查状态，
不重复创建。课题目标、预算、并发与交互策略仍由 Profile 决定；模型可用性和科学
质量需要单独验证。真实凭据由用户在私有 Profile 中填写，不能放入 `--set`。

## Budgets and GPU use / 预算与 GPU

New v4 templates keep resource, time and financial limits together under
`methodology.yaml.budget`: `total_budget_usd`, `per_expert_budget_usd`,
`wallclock_hours`, `experiment_timeout` (seconds), plus invocation/retry/concurrency
limits. Goal retains scientific completion and round limits; safety retains
permissions. The six-file layout and admitted runtime budget do not change.
Legacy safety USD/timeout and goal wall-clock fields are accepted individually
with a migration warning; declaring the same bound in both places is an error,
even when values match. Existing Profiles and runs are never rewritten automatically.

中文：新模板将费用、时限、单次调用、重试及并发限制统一放在
`methodology.yaml.budget`，实验超时 `experiment_timeout` 的单位是秒。
旧 `safety` 费用/分钟超时、`goal.stop_conditions.max_wall_clock_hours`
单独声明时仍兼容并提示迁移；与 budget 重复声明会报错，不再静默覆盖。
无需增加文件或升级已有运行。完整生效配置与通知设置见
[初始化指南](../docs/guides/initialization.zh-CN.md)。

The multi-expert pair starts with three experts, a total USD ceiling of 45,
15 per expert, at most four rounds and a six-hour run limit. Both languages use
a per-invocation ceiling of 1. These are configurable limits, **not cost
estimates or permission to launch**. Inspect the admitted payload before Start.

The templates use `gpus: []`, disable Telegram and keep the original repository
and data read-only. GPU starts require both an updated local Profile and exact
Host UUID authorization. Shared-pool estimates live in `methodology.yaml.budget`;
the workload floor and reserve remain enforced after measurement-based learning.

## Research controls and framework boundaries / 研究控制与框架边界

Profile controls express research and resource policy. For example,
`interaction.leader.min_rounds_before_leader` counts the contributing expert's
closed research cycles, not its experiment count. The templates use `1`; a
local topic may deliberately choose a larger value or disable Leader/Debate.
Leader can coordinate from verified intermediate contributions before the final
goal. The Host still validates evidence and eligibility; skills explain the
scientific rationale using the admitted evaluation protocol. From ACI 23, Leader
is an advice author: peers continue independently, with no extra cycle or parent
authorization. Advice uses ordinary Agent budgets and the existing `leader`
model/language route; no new Profile setting is required. Experts decide how to
use it in a later proposal. Debate controls retain their existing meaning.

Agent, Process and review capacities are separate upper bounds. Raising them
does not automatically parallelize seeds or variants inside an experiment;
that partitioning belongs in the target implementation and must preserve its
evaluation protocol. GPU availability is live Host information, not permanent
skill memory. Public templates and local topics keep their own budget choices.

In ACI 22 and later, EXECUTION can hand a submitted experiment to Host waiting,
then resume the same node after collection. Both invocations consume
normal budgets; waiting does not extend an invocation deadline. An expected
short experiment may finish after one bounded wait in the same invocation.
Agent and experiment timeouts remain independent. Framework ownership, publication,
archival validation and resource safeguards cannot be disabled by a skill.

中文：Profile 表达研究策略和资源政策。Leader 的 `min_rounds_before_leader` 按贡献
专家已收尾的研究周期计数，不按实验数量；模板采用 `1`，本地课题可选择更大值或
关闭交互。Leader 可依据最终目标达成前的已验证贡献协调研究，Host 仍验证资格与
证据，skill 按实际评测协议解释科学判断。从 ACI 23 起，Leader 是建议作者，同行
独立推进，不获得额外周期或父假设授权。建议使用普通 Agent 预算和既有 `leader`
模型／语言路由，无需新增 Profile 设置；接收者自行决定后续提案如何使用。Debate
控制保持原义。Agent／Process／review 并发是各自上限，
不会自动拆分单个实验的 seed 或变体；拆分应在目标实现中保持评测协议。GPU 空闲
情况由 Host 实时观察，不能固化为 skill 记忆。ACI 22 及后续版本的运行可提交后交还 Host
等待，结果收集后继续同一节点；前后调用均计入预算，独立时限不延长。
框架的所有权、发布、归档验证与资源保护不由 skill 覆盖。

## Selective paper reading / 选择性精读

ACI 24 rebuilds ordinary model sessions while retaining research workspaces.
Optional `methodology.memory` capacity and usage-adapter controls are described
in [context management](../docs/guides/context-management.md).
ACI 24 重建普通模型会话并保留科研工作区；`methodology.memory` 的可选容量和用量
适配配置见[上下文管理](../docs/guides/context-management.zh-CN.md)。

Literature MAIN can use retained originals and bounded text under `/papers`.
`web_search(scope="academic")` uses the Host-selected OpenAlex client directly;
`web_fetch(mode="document", url=...)` prepares exact sources, and `paper_ref`
reuses an admitted source. Literature credentials stay in private
`methodology.yaml`; only the derived file reference enters the admitted contract.
The Host reads the original file, and readers never receive the secret bytes.

To opt into Host-managed independent readers in a fresh ACI-20 run, add
`read_papers` to `methodology.yaml`'s existing list:

```yaml
literature:
  enabled: true
  tools: [web_search, web_fetch, read_papers]
  max_sources: 8
```

This grants only Literature MAIN the submit/status/wait/cancel tool. Each reader
inherits the literature model, has Read/Glob/Grep only, and shares normal Agent
slots and the expert/run budget; the waiting parent still occupies its slot.
There is no separate reader-count or model setting. Public defaults leave the
option off. Validate the complete six-file Profile through `profile-check`.

中文：原文目录支持按问题选读方法、实验细节和附录；不是要求读完全部论文。
需要并行精读时，在已有 tools 列表加入 `read_papers`。模型读者没有固定两路上限，
实际数量取决于 Agent 空位、剩余预算、父任务期限和服务端退避；PDF I/O 仍共享最多
两路的现有限额。没有读者能启动时，专家可取消排队项并串行阅读。原专家负责核查、
综合和写入原有摘要、MEMORY.md 与轮次报告。默认模板及已有本地 Profile 不自动开启。

## Goal completion / 完成标准

The default `metric: null` defers an explicit metric to research and uses the
current shared-goal semantics. An accepted SUPPORTED experiment can stop the
whole run. For Grounding-authored common acceptance, add `evaluation: grounding`
to `goal.yaml` and describe the full requirements in its description/constraints.
One GOAL candidate must then satisfy the whole accepted declaration; individual
LOCAL results and combined partial scores cannot close it.

Explicit metric/completion forms remain supported by the existing parser. Do
not invent thresholds from a title or translate an identifier into a different
metric. See [architecture](../docs/architecture.md) and [usage](../USAGE.md).

## Checks and compatibility fixtures

`tests/test_public_profiles.py` checks both languages through production
admission without launching a Host, model or GPU. Examples for older supported
parsers live under `tests/fixtures/profiles/` and remain test inputs; new public
topics use v4. Profile parser compatibility does not imply cross-version run
recovery. Qualification is described in [G0–G5](../docs/qualification.md).
