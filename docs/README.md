# Hypoloom documentation

English · [简体中文](README.zh-CN.md)

Use [Setup](../SETUP.md) for your first installation and [Usage](../USAGE.md)
for daily operation. The dev branch also retains development instructions and validation history.

## User guides

| Guide | What it covers |
| --- | --- |
| [Initialization](guides/initialization.md) | Model selection, budgets and startup checks |
| [Personal configuration](../config/README.md) | Six YAML files, field descriptions and English template examples |
| [Profile templates](../profiles/README.md) | Starting templates, configuration workflow and operating limits |
| [Operator guide](operator-guide.md) | Daily operation and result interpretation |
| [Research workflow](guides/research-workflow.md) | Expert stages, experiments and evolving skills |
| [Reading and proposals](guides/reading-and-proposals.md) | Paper discovery, reading and hypothesis proposals |
| [Institutional full text](guides/institutional-access.md) | Browser connections, institution login and PDF access |
| [GPU resources](guides/gpu-resource-planning.md) | Memory estimates, sharing and resource waits |
| [Context and memory](guides/context-management.md) | Working limits, sessions and evidence retrieval |
| [API recovery](guides/provider-recovery.md) | Provider limits, connection changes and scheduled retries |
| [Runtime recovery](guides/runtime-recovery.md) | Blocked work, observation and recovery boundaries |
| [Notifications](guides/notifications.md) | External notifications and delivery troubleshooting |
| [Version reminders](guides/version-check.md) | Comparing versions and updating for new runs |
| [Architecture](architecture.md) | Runtime ownership, evidence and independent verification |

Each guide has separate English and Chinese versions, linked at the top.
This index links to English guides; the [Chinese index](README.zh-CN.md) links
to their Chinese counterparts.

## Development and validation

| Guide | What it covers |
| --- | --- |
| [Contributing](../CONTRIBUTING.md) | Development setup, focused tests and documentation maintenance |
| [Qualification](qualification.md) | G0–G5 procedures, candidate evidence, failures and limits |
| [Local workspace](guides/local-workspace.md) | Private evidence, current work and original-path preservation |

These guides also have Chinese counterparts. The following references retain
their original language:

- [Design records](design/): implementation decisions and repair rationale.
- [AGENTS](../AGENTS.md): repository rules and authorization boundaries.
- [Current development state](codex-memory/CURRENT.md) and
  [memory index](codex-memory/INDEX.md): compact decisions and supporting history.
- [CLAUDE](../CLAUDE.md) and [runtime skills](../.claude/skills/):
  research runtime instructions, separate from Codex maintenance context.

Keep qualification results in the qualification record and link to them from
current guides. Preserve historical evidence at its original path. Source code,
typed validation and observable test results resolve conflicts in explanatory
prose; development notes do not become runtime inputs.
