<p align="center">
  <img src="docs/assets/hypoloom_vector.svg" alt="Hypoloom — an open-source engine for autonomous research">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python 3.12">
  <img src="https://img.shields.io/badge/platform-linux-blue.svg" alt="Platform: Linux">
  <img src="https://img.shields.io/badge/requires-Claude%20Code-blue.svg" alt="Requires: Claude Code">
</p>

<p align="center">
  English · <a href="README.zh-CN.md">简体中文</a>
</p>

Hypoloom is an open-source framework for autonomous research, from literature
review and hypothesis generation to experiments and analysis. AI research experts
work together on a research goal.

A background workflow service (**Workflow Host**, or **Host**) schedules tasks,
allocates resources and records progress. An independent validator (**Oracle**)
checks execution records and experiment artifacts for consistency. Scientific
conclusions still require review of the experiment design and results.

- **Analyze.** Experts search OpenAlex and read available PDFs, using your
  feedback to clarify the research goal and evaluation criteria.
- **Hypothesize.** Experts explore hypotheses in parallel, record experiment
  designs before execution, and plan the resources each experiment needs.
- **Experiment.** Experiments run in isolated Git worktrees using your selected
  CPU/GPU environment. GPUs require explicit authorization; an optional shared
  pool lets experiments share a device when capacity permits.
- **Evolve.** Experts analyze measurements, debate competing interpretations,
  refine hypotheses and improve reusable research skills.

## 🧩 What it provides

The workflow below connects expert collaboration, experiment execution and
independent checks. You set the research goal, budgets and resource permissions.

<p align="center">
  <img src="docs/assets/hypoloom_overview.svg" alt="Research workflow: goal clarification feeds parallel expert cycles of literature, hypotheses, design, execution, analysis and update, scheduled by the Host with independent Oracle checks">
</p>

Goal clarification (**Grounding**) helps define the research requirements.
A coordinating expert (**Leader**) shares advice, while structured debates
(**Debate**) examine competing interpretations. See the
[research workflow](docs/guides/research-workflow.md) for details.

A **Profile** is a set of six YAML files describing your topic: its goal, experts,
runtime, budgets and permissions. Start with an English or Chinese
[Profile template](profiles/README.md) and configure it for your code and data.

This is the **dev** branch. It includes the development guides, qualification
records and maintainer context. For the public version, see
[main](https://github.com/EIMP-Lab/Hypoloom/tree/main).

## 🚀 Quick start

You need Linux, Conda, Git, bubblewrap, working user namespaces and Claude Code.
Background runs require user-systemd. See [Setup](SETUP.md) for prerequisites.

### 1. Install Hypoloom

```bash
git clone --branch dev https://github.com/EIMP-Lab/Hypoloom.git
cd Hypoloom
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[dev,monitor,notify]'
```

This installs the control environment, terminal monitor, notification charts
and development tools. GPU experiments use a separately selected CUDA environment.

### 2. Configure project and model access

Follow [Setup](SETUP.md) to enable the project settings and configure model API
authentication. You will add literature and notification credentials to your
local Profile in the next step.

### 3. Prepare a research Profile

Copy the English template into a new, Git-ignored directory:

```bash
mkdir -p profiles/local
(umask 077 && mkdir profiles/local/my-topic && \
  cp profiles/templates/multi-expert-en/*.yaml profiles/local/my-topic/)
```

If `my-topic` already exists, edit it or choose a new name. The command does not
overwrite an existing Profile. Edit the local copy using the
[English configuration guide](config/README.md):

| File | Purpose |
| --- | --- |
| `goal.yaml` | Research goal, constraints and completion criteria |
| `experts.yaml` | Expert roles, model choices and research-chain limits |
| `methodology.yaml` | Experiment environment, literature access, research controls and budgets |
| `language.yaml` | Languages for prompts, reports and code comments |
| `notify.yaml` | Local logs, dashboard and optional external notifications |
| `safety.yaml` | Readable data paths, GPU selection and sandbox restrictions |

Replace the goal and `/REPLACE_ME/` paths, fill the required literature credentials,
and review budgets. Keep the directory mode 0700 and YAML files mode 0600, as
created above. The template starts with CPU execution and Telegram disabled.

### 4. Initialize, review and start

Start Claude Code in the repository:

```bash
claude
```

Then enter the skill command:

```text
/autoresearch init
```

Select the existing `my-topic` Profile. Confirm the model, credentials, time and
cost limits, CPU/GPU allocation and notification recipients. Choose **Prepare only**
to save the configuration, or **Start now** to begin research. The setup checks
do not make a paid model call.

### 5. Monitor progress

In Claude Code, enter:

```text
/autoresearch-monitor
```

Select your run, or use the terminal/Web commands returned by initialization.
**q / Leave monitor** keeps research running. **x / End run** requests cancellation
and waits for cleanup. See [Usage](USAGE.md) for human answers and recovery.

## 🔍 Inside a run

Use `hypoloom web` to follow research in a browser, or `hypoloom monitor` for
the terminal interface. Both display records maintained by the Host. Answering
questions, changing API connections and ending a run require explicit action.
By default, the Web interface accepts connections only from the same machine.
Access uses a link containing a token; keep that link private.

### 📊 Run overview

The **Operations** view shows run status, the latest Oracle check, and progress
across hypotheses, experiments, literature and skills. Reliability summaries
track output validation, retries, recovery and completed work.

Filter execution attempts (**Attempts**) to inspect a failure or retry. Each
attempt records one task execution, such as a model invocation or an experiment.

<p align="center">
  <img src="docs/assets/web-overview.png" alt="Web run overview showing status, the latest Oracle check, research activity, execution reliability and the shared research goal">
</p>

### 🧑‍🔬 Expert cards

Each expert follows six research stages: literature, hypotheses, design,
execution, analysis and update. Its card shows the current stage and active
research agents (**Agents**): workers that use models and tools to complete
specific tasks. Context and token usage help you track each agent's workload.

Coordination indicators show debate-related scheduling pauses. A crown marks
the selected Leader.

<p align="center">
  <img src="docs/assets/web-expert.png" alt="Expert cards showing research stages, active agents and experiments, context usage, execution reliability and a debate-related scheduling pause">
</p>

### 🧬 Hypothesis lineage

Follow how hypotheses branch and change over time. Colors identify the expert
responsible for each hypothesis; connections distinguish execution repairs,
experiment revisions and changes to the scientific hypothesis. Stopping a branch
is a workflow decision; scientific assessments are recorded separately.

<p align="center">
  <img src="docs/assets/Hypo.png" alt="Hypothesis lineage grouped by expert, with assessment states and connections showing repairs, revisions and branches no longer being pursued">
</p>

### 📈 Metric trends

Track experiment measurements against targets defined before execution. Charts
group results by the initial hypothesis so you can follow how each line of
research progresses.

<p align="center">
  <img src="docs/assets/web-metrics.png" alt="Metric charts showing recorded measurements for each initial hypothesis chain, target lines and a clickable legend">
</p>

### 💻 Terminal UI

The terminal user interface (**TUI**) shows the same research records in your
shell: goals, results, budgets, expert activity and hypothesis history. Use it
to answer questions, manage API recovery, inspect diagnostics and end a run.

<p align="center">
  <img src="docs/assets/TUI.png" alt="Terminal interface showing run status, budgets, expert activity, hypothesis history and controls for human answers, diagnostics and ending the run">
</p>

See [Usage](USAGE.md) for Web and terminal commands, read-only access sharing
and operator controls.

## 📚 Documentation

| Guide | What it covers |
| --- | --- |
| [Setup](SETUP.md) | Dependencies, model access and your first launch |
| [Usage](USAGE.md) | Monitoring, human answers, stopping and recovery |
| [Operator guide](docs/operator-guide.md) | Launch review, budgets, resources and result interpretation |
| [Profile configuration](config/README.md) | All six Profile files, field descriptions and English examples |
| [Profile templates](profiles/README.md) | Starting templates, configuration workflow and operating limits |
| [Architecture](docs/architecture.md) | Components, execution boundaries and evidence |
| [Documentation index](docs/README.md) | Detailed guides for GPUs, literature, notifications and more |
| [Contributing](CONTRIBUTING.md) | Development setup, validation, documentation and issue reporting |

Each guide links its Chinese translation at the top. The
[中文主页](README.zh-CN.md) links directly to Chinese guides.

### Development and validation

| Reference | What it covers |
| --- | --- |
| [Qualification](docs/qualification.md) | G0–G5 procedures, tested candidates, results and limits |
| [Local workspace](docs/guides/local-workspace.md) | Private evidence, current work and original-path preservation |
| [Design records](docs/design/) | Engineering decisions and repair rationale |
| [Codex instructions](AGENTS.md) | Repository rules and authorization boundaries |
| [Current development state](docs/codex-memory/CURRENT.md) | Current decisions and evidence pointers |
| [Development memory index](docs/codex-memory/INDEX.md) | Earlier decisions and supporting records |
| [Claude Code instructions](CLAUDE.md) | Research runtime configuration, separate from maintenance context |

Design and maintainer records retain their original language. During development,
the managed entry remains `manage_host.py` with user-systemd and the run's
`control.sock`; use [Setup](SETUP.md) and [Usage](USAGE.md) for lifecycle operations.
Read [Contributing](CONTRIBUTING.md) before changing code or running qualification.

## 🗺️ Roadmap

Current directions, in no fixed order — plans, not commitments; sequencing
depends on implementation and qualification results.

- **More agent runtimes.** Extend the current Claude Code integration to
  runtimes such as Codex, Kimi Code and OpenCode, keeping the Host responsible
  for research scheduling and checking compatibility with each runtime.
- **Research benchmarks.** Explore adapting Hypoloom to AutoResearch benchmark
  tasks and designing comparative studies of research processes and outcomes.

## 🚧 Development status

The dev branch is under active development: interfaces, Profile schemas and
internal contracts can change between candidates. Qualification applies only
to the exact tested candidate and environment; model/provider compatibility is
established per sampled binding. Read the [qualification record](docs/qualification.md)
for completed checks, failures and limitations.

Begin with a small topic and explicit time and cost limits. Public templates are
configuration examples, not scientific benchmarks. Successful execution or
qualification does not prove a scientific claim; inspect the experiment records,
measurements and final report.

Preserved runs use their own runtime bundle and configuration. Updating this
checkout does not upgrade them; cross-version run migration is unsupported.
See [runtime recovery](docs/guides/runtime-recovery.md).

## ⚖️ License

[MIT](LICENSE), copyright © 2026 EIMP Lab and the Hypoloom Authors.
