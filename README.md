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
  extraction, then ground the goal through explicit human clarification with
  common acceptance criteria.
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

[Profile templates](profiles/README.md) ship in English and Chinese, and
maintainers get the [G0–G5 qualification workflow](docs/qualification.md).

## 🔍 Inside a run

A running research loop is observable at two surfaces, both read-only
projections of the Host's durable state: a browser-based Research Observer
(`hypoloom web`, loopback-only behind a bearer link) and a terminal Research
Operations UI (`hypoloom monitor`). Neither mutates the run. Operator
interventions — answering a Grounding question, resuming after a provider
outage, ending the run — are explicit, recorded commands.

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

## 🚀 Start here

Use Linux with bubblewrap, Git and a current Claude Code CLI. The pinned
[environment.yml](environment.yml) supplies Python 3.12 and a suitable SQLite;
GPU experiments use a separately selected CUDA environment.

```bash
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[monitor,dev]'
./.conda/envs/hypoloom/bin/hypoloom --help
```

Open Claude Code in this checkout and use `/autoresearch init`. Select a local
Profile or a bilingual template, review the target/data paths, exact model,
credentials, budgets and CPU/GPU allocation, then choose **Prepare only** or
**Start now**. The latter uses the tracked `manage_host.py` helper and
user-systemd to start a Host, verify `control.sock`, create the reviewed run and
return status in the same interaction. No CLI `init` subcommand is required.

A Profile is six YAML files (goal, experts, methodology, language, notify and
safety); init copies one reviewed template set into `profiles/local/<name>`
and refuses to overwrite, and you bind its placeholders — the model selector
and the target/data/runtime paths. Secrets never enter the Profile: a single
mode-0600 dotenv file in an ignored directory (template
[profiles/templates/hypoloom.env.example](profiles/templates/hypoloom.env.example))
holds keys such as `OPENALEX_API_KEY`, and the Profile references it by
absolute path. The full construction rules are in
[profiles/README](profiles/README.md) and [SETUP](SETUP.md).

The default Run ID is `<profile>-<YYYYMMDDHHMM>` and Request ID is that identity
plus `-create-1`; state roots are unique. Model authentication requires a pinned
exact selector and either approved environment credentials or an eligible
existing mode-0600 settings file. Read [SETUP](SETUP.md) before using real
models or resources.

## 📚 Documentation

- [Setup and configuration](SETUP.md)
- [Usage: monitoring, human answers, cancellation and recovery](USAGE.md)
- [Operator guide](docs/operator-guide.md)
- [Profile templates](profiles/README.md) (bilingual English/Chinese)
- [Architecture](docs/architecture.md)
- [Qualification and current validation status](docs/qualification.md)
- [Documentation index](docs/README.md)
- [Contributing](CONTRIBUTING.md), [Codex instructions](AGENTS.md) and
  [Claude Code runtime instructions](CLAUDE.md) (maintainer/runtime
  references, English only)

Each guide above links its 简体中文 version at the top; the
[中文主页](README.zh-CN.md) links the Chinese versions directly.

## 🗺️ Roadmap

Current directions, in no fixed order — plans, not commitments; sequencing
follows qualification results.

- **More agent runtimes.** The framework currently drives research experts
  through Claude Code. We plan to extend the same Host-owned research loop to
  other coding-agent runtimes — Codex, Kimi Code, OpenCode and similar —
  behind the existing per-binding qualification path.
- **AutoResearch benchmarks.** We are considering academic follow-up around
  the emerging AutoResearch benchmark line: adapting the Hypoloom workflow to
  those tasks, then designing and running comparative evaluations of academic
  quality against them.

## 🚧 Development status

Hypoloom is a development framework with no frozen release: interfaces,
Profile schemas and internal contracts can change between candidates.
Qualification applies to one exact tested candidate and environment; model
and provider compatibility is established per sampled binding, not in
general. Public templates demonstrate configuration; they are not scientific
benchmarks, and no qualification result proves a scientific claim. The
current validation scope is recorded in [qualification](docs/qualification.md).
Preserved runs use their own runtime bundle; cross-version run migration is
not provided.

## ⚖️ License

[MIT](LICENSE), copyright © 2026 EIMP Lab and the Hypoloom Authors.
