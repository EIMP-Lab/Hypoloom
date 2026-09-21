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

## Credential file / 凭据文件

Keep every local credential (OpenAlex key, Unpaywall contact, Telegram bot
token/chat ID) in **one dotenv file** in an ignored directory, for example the
project's `.local/credentials/hypoloom.env` (`/.local/` is Git-ignored):
`mkdir -m 700 -p .local/credentials`, copy
[templates/hypoloom.env.example](templates/hypoloom.env.example) there, keep
the file mode 0600, and fill in the values you use. The format is a strict
subset: `KEY=VALUE` lines and `#` comments. Profiles reference the file by
absolute path — never inline secret literals:

- `methodology.yaml` → `literature.credentials_file` (absolute path). The
  operator flow passes it to the Host as `--credentials-file`, providing
  `OPENALEX_API_KEY` and, for the Unpaywall PDF fallback, `UNPAYWALL_EMAIL`.
- `notify.yaml` → `sinks.telegram.credentials_file` (absolute path), providing
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; or the environment-variable
  alternative `bot_token_env` / `chat_id_env`. Use exactly one source.

中文：所有本地凭据（OpenAlex 密钥、Unpaywall 联系方式、Telegram bot
令牌/会话 ID）集中存放在一个 dotenv 文件中（如项目
`.local/credentials/hypoloom.env`，`/.local/` 已被 Git 忽略；目录 0700、
文件 0600）。复制 `templates/hypoloom.env.example` 后按需填写，格式为严格
子集（`KEY=VALUE` 行与 `#` 注释）。Profile 里只写绝对路径
（`literature.credentials_file`、`sinks.telegram.credentials_file`），不写
秘密明文；Telegram 也可用环境变量方式，两种方式二选一。

| File | Meaning / 职责 |
|---|---|
| `goal.yaml` | Goal, constraints, completion choice and stopping limits / 目标与完成要求 |
| `experts.yaml` | Stable expert names, persona, model and branching / 专家背景及模型配置 |
| `methodology.yaml` | `schema_version: 4`, runtime paths, research controls and invocation budgets |
| `language.yaml` | `prompt_language`, `report_language`, `code_comment_language` |
| `notify.yaml` | Notification sinks/events; secrets referenced by absolute file path or environment-variable name |
| `safety.yaml` | Read roots, GPU permission, sandbox, experiment timeout and USD ceilings |

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

Use `/autoresearch init` to copy one reviewed template into a fresh ignored
`profiles/local/<name>` directory. It copies the six files individually and
rejects an existing destination. Adopt an existing local Profile unchanged when
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

## Budgets and GPU use / 预算与 GPU

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
scientific rationale using the admitted evaluation protocol.

Agent, Process and review capacities are separate upper bounds. Raising them
does not automatically parallelize seeds or variants inside an experiment;
that partitioning belongs in the target implementation and must preserve its
evaluation protocol. GPU availability is live Host information, not permanent
skill memory. Public templates and local topics keep their own budget choices.

In fresh ACI-22 runs, EXECUTION can hand a submitted experiment to Host waiting,
then resume the same node/session after collection. Both invocations consume
normal budgets; waiting does not extend an invocation deadline. An expected
short experiment may finish after one bounded wait in the same invocation.
Agent and experiment timeouts remain independent. Framework ownership, publication,
archival validation and resource safeguards cannot be disabled by a skill.

中文：Profile 表达研究策略和资源政策。Leader 的 `min_rounds_before_leader` 按贡献
专家已收尾的研究周期计数，不按实验数量；模板采用 `1`，本地课题可选择更大值或
关闭交互。Leader 可依据最终目标达成前的已验证贡献协调研究，Host 仍验证资格与
证据，skill 按实际评测协议解释科学判断。Agent／Process／review 并发是各自上限，
不会自动拆分单个实验的 seed 或变体；拆分应在目标实现中保持评测协议。GPU 空闲
情况由 Host 实时观察，不能固化为 skill 记忆。新 ACI-22 运行可提交后交还 Host
等待，结果收集后继续同一节点／会话；前后调用均计入预算，独立时限不延长。
框架的所有权、发布、归档验证与资源保护不由 skill 覆盖。

## Selective paper reading / 选择性精读

Literature MAIN can use retained originals and bounded text under `/papers`.
`web_search(scope="academic")` uses the Host-selected OpenAlex client directly;
`web_fetch(mode="document", url=...)` prepares exact sources, and `paper_ref`
reuses an admitted source. The Profile declares the dotenv credentials file by
absolute path (`literature.credentials_file`); the secret bytes stay in the
ignored credential directory, outside the Profile, the contract and reader
prompts.

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
