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

Hypoloom is an open-source framework for autonomous research, from hypothesis
generation and experiment execution to evidence-driven iteration. Persistent
research experts work through a resident Workflow Host; the Host owns
execution, resource allocation and durable state, and an independent Oracle
checks recorded outcomes and lifecycle consistency.

- **Analyze.** Experts retrieve and read literature through OpenAlex and PDF
  extraction, then clarify the research goal and evaluation criteria with you.
- **Hypothesize.** Parallel expert chains propose hypotheses, preregister
  experiment designs, and plan their resources.
- **Experiment.** Real CPU/GPU execution: private Git worktrees, a selected
  runtime, explicit GPU authorization, and an optional shared GPU pool that
  packs several experiments onto one card when capacity permits.
- **Evolve.** Experts interpret measurements, debate and adjudicate outcomes,
  revise the line of inquiry, and evolve reusable research skills.

## 🧩 What it provides

One resident Host coordinates the whole loop; operators keep explicit
authority, and an independent Oracle checks the record.

<p align="center">
  <img src="docs/assets/hypoloom_overview.svg" alt="Hypoloom overview: parallel domain experts each run a six-node research cycle (literature, hypotheses with forked chains, design, execute, analyze, update), fed by run-scoped Grounding, coordinated through judged debates and an Observer-selected leader, with self-evolving per-role skill plugins, all executed by a resident Workflow Host on agents, processes, and a shared GPU pool, under operator authorization and independent Oracle checks">
</p>

[Profile templates](profiles/README.md) provide English and Chinese starting
points for your own research goal, code, data and budgets.

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

### 2. Configure access

Follow [Setup](SETUP.md) to enable the project settings and configure model API
authentication. You will add literature and notification credentials to your
local Profile in the next step.

### 3. Prepare your Profile

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
cost limits, CPU/GPU allocation and any
notification recipients. Choose **Prepare only** to save the configuration, or
**Start now** to begin research. The setup checks do not make a paid model call.

### 5. Monitor progress

In Claude Code, enter:

```text
/autoresearch-monitor
```

Select your run, or use the terminal/Web commands returned by initialization.
**q / Leave monitor** keeps research running. **x / End run** requests cancellation
and waits for cleanup. See [Usage](USAGE.md) for human answers and recovery.

## 🔍 Inside a run

Monitor research in the browser with `hypoloom web` or in the terminal with
`hypoloom monitor`. Both display the Host's recorded state. Human answers, API
recovery and End run are explicit operator actions. The Web observer listens
on loopback and uses a bearer link for access.

### 📊 Run overview

The Operations view tracks the whole run: durable state, revision and the
last Oracle verdict; counts of experiments, hypotheses, literature evidence,
evolved skills and Leader/Debate activity; cumulative execution reliability
(first output pass, correction recovery, nodes needing retry, final delivery,
required actions); and filters over every recorded attempt.

<p align="center">
  <img src="docs/assets/web-overview.png" alt="Web Research Observer operations overview of an ACTIVE run: header with run ID, durable state, pending scientific verdict, revision, observed time and last-clean Oracle status; counter tiles for experiments, hypotheses, literature, skills, effective evolutions, leader and debate; cumulative execution reliability tiles; observation filters and the shared research goal">
</p>

### 🧑‍🔬 Expert cards

Each expert runs its own six-node research cycle and gets a live card: its
current stage, the agents working for it with context used against budget,
observed tokens and per-expert reliability, and coordination states — a
proposed debate pauses dispatch for that expert, and a crown marks the
Observer-selected leader.

<p align="center">
  <img src="docs/assets/web-expert.png" alt="Expert cards in the Web observer: the ml-expert is in cycle 3 running two experiment processes with breathing-light agent tiles and per-expert reliability tiles, while the experiment-engineering expert shows an amber debate-proposed dispatch pause over its completed result analyses, with stage pipeline, token accounting and browse-history entry per card">
</p>

### 🧬 Hypothesis lineage

The lineage graph shows how the inquiry itself evolves. Every durable
hypothesis appears once, colored by its expert; edge styles distinguish
execution repair, experiment revision and scientific revision, and pruned
branches record control decisions — pruning is not a scientific verdict.

<p align="center">
  <img src="docs/assets/Hypo.png" alt="Hypothesis lineage topology of a run: twelve durable hypothesis nodes colored by owning expert and marked refuted, initial or pending, connected by edges distinguishing execution repair, experiment revision, scientific revision and unclear revision, with a legend noting that pruned-by edges are control decisions, not refutations">
</p>

### 📈 Metric trends

Metric trends connect each published measurement to its initial-hypothesis
chain in experiment order, against the target lines preregistered in the
research contract — progress toward the acceptance criteria is visible at a
glance.

<p align="center">
  <img src="docs/assets/web-metrics.png" alt="Metric trends in the Web observer: one chart per preregistered metric, plotting published observations per initial-hypothesis chain in experiment order against dashed target lines, with clickable legend entries to show or hide each chain">
</p>

### 💻 Terminal UI

The terminal UI presents the same durable snapshot in the shell: the run at a
glance (goal, verdict, budget and tokens, execution reliability), per-expert
lanes with their current work, the hypothesis chains, the next durable
commands, and the operator actions — refresh, Web/local access, human
decision, provider resume, diagnostics and End run.

<p align="center">
  <img src="docs/assets/TUI.png" alt="Terminal Research Operations UI of an ACTIVE read-only run: run-at-a-glance panel with goal, pending verdict, research activity counters, budget and token usage, execution reliability and run health; expert lanes with stage progress and open attempts; next durable commands; hypothesis chains tree; operator key hints including human decision, provider resume, diagnostics and end run">
</p>

[USAGE](USAGE.md) covers launching the Web observer and the TUI, sharing
read-only links, and the operator actions.

## 📚 Documentation

| Guide | What it covers |
| --- | --- |
| [Setup](SETUP.md) | Dependencies, model access and your first launch |
| [Usage](USAGE.md) | Monitoring, human answers, stopping and recovery |
| [Operator guide](docs/operator-guide.md) | Launch review, budgets, resources and result interpretation |
| [Personal configuration](config/README.md) | All six Profile files, field descriptions and English examples |
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

- **More agent runtimes.** The framework currently drives research experts
  through Claude Code. We plan to extend the same Host-owned research loop to
  other coding-agent runtimes — Codex, Kimi Code, OpenCode and similar —
  with compatibility checked for each supported runtime.
- **AutoResearch benchmarks.** We are considering academic follow-up around
  the emerging AutoResearch benchmark line: adapting the Hypoloom workflow to
  those tasks, then designing and running comparative evaluations of academic
  quality against them.

## 🚧 Development status

The dev branch is under active development: interfaces, Profile schemas and
internal contracts can change between candidates. Qualification applies only
to the exact tested candidate and environment; model/provider compatibility is
established per sampled binding. Read the [qualification record](docs/qualification.md)
for completed checks, failures and limitations.

Begin with a small topic and explicit time and cost limits. Public templates are
configuration examples, not scientific benchmarks. Successful execution or
qualification does not prove a scientific claim; inspect the accepted evidence,
measurements and final report.

Preserved runs use their own runtime bundle and configuration. Updating this
checkout does not upgrade them; cross-version run migration is unsupported.
See [runtime recovery](docs/guides/runtime-recovery.md).

## ⚖️ License

[MIT](LICENSE), copyright © 2026 EIMP Lab and the Hypoloom Authors.
