# Hypoloom

An open-source framework for autonomous research, from hypothesis generation
and experiment execution to evidence-driven iteration.

Hypoloom coordinates persistent research experts through a resident Workflow
Host. Experts investigate a goal, propose hypotheses, implement experiments,
interpret measurements, and revise their work. The Host owns execution,
resource allocation and durable state; an independent Oracle checks recorded
outcomes and lifecycle consistency.

## What it provides

- Grounding with explicit human clarification and common acceptance criteria.
- Parallel expert chains, literature retrieval through OpenAlex, private Git
  worktrees, revision graphs, debate, adjudication and evolving research skills.
- Real CPU/GPU experiments, explicit GPU authorization, and an optional shared
  GPU pool supporting several experiments on one card when capacity permits.
- Bounded interaction context and cadence, model/experiment budgets, execution
  accounting, crash recovery using each run's own preserved runtime, and
  explicit cancellation and cleanup.
- A Textual TUI and local Web observer for progress, evidence and human answers.
  The TUI's **q / Leave monitor** leaves the Host running; **x / End run** opens
  the cancellation confirmation. Help and Report Issue are separate actions.
- English and Chinese [Profile templates](profiles/README.md), plus the
  [G0–G5 qualification workflow](docs/qualification.md) for maintainers.

## Start here

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

Template paths are placeholders and must be bound before admission. The
default Run ID is `<profile>-<YYYYMMDDHHMM>` and Request ID is that identity
plus `-create-1`; state roots are unique. Model authentication requires a pinned
exact selector and either approved environment credentials or an eligible
existing mode-0600 settings file. Read [SETUP](SETUP.md) before using real
models or resources.

## Documentation

- [安装与配置 / Setup](SETUP.md)
- [运行、监控与停止 / Usage](USAGE.md)
- [English operator guide](docs/operator-guide.md)
- [中文与英文 Profile 模板](profiles/README.md)
- [Architecture](docs/architecture.md)
- [Qualification and current validation status](docs/qualification.md)
- [Documentation index](docs/README.md)
- [Contributing](CONTRIBUTING.md), [Codex instructions](AGENTS.md), and
  [Claude Code runtime instructions](CLAUDE.md)

## Development status

This is a development framework. Public templates demonstrate configuration;
they are not scientific benchmarks. Qualification applies to the tested
candidate and environment, and does not prove a scientific claim. The current
Hypoloom validation scope is recorded in [qualification](docs/qualification.md).
Preserved runs use their own runtime bundle; cross-version run migration is
not provided.

## License

[MIT](LICENSE), copyright © 2026 Hui Li.
