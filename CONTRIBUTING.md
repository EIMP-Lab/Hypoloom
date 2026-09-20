# Contributing

English · [简体中文](CONTRIBUTING.zh-CN.md)

Use the pinned [environment](environment.yml), then install `.[dev,monitor]`.
Read [AGENTS](AGENTS.md) for repository conventions and [architecture](docs/architecture.md)
for runtime ownership. Claude Code instructions are in [CLAUDE](CLAUDE.md).

Run focused tests while making changes. Before promoting a candidate, follow
[qualification](docs/qualification.md): full offline checks, retained controls,
bounded production-process closure, applicable CPU/GPU work and conditional
real-model compatibility. GPU and paid tests are opt-in; collection must not
start them. A passing repair test alone does not qualify a release.

Preserve tests that reproduce real failures. Current parser-compatibility
templates live under `tests/fixtures/profiles/`; public authoring examples use
v4 under `profiles/templates/`. Update both language variants when changing a
public template and check semantic controls remain equivalent.

Keep existing runs read-only unless an exact operation has been authorized.
Use disposable test roots; never attach a development test to a research Host
or a shared resource pool implicitly. Keep secrets, real Profiles, datasets,
logs and runtime environments outside version control.

Prefer existing validators, durable identities, transactions and ordinary
tests. New hashes, authority files, gates or custom process protocols require a
concrete failure justification; avoid duplicating the production reducer in a
test oracle. Preserve authentication, authorization and irreversible-operation
protections. Record test scope honestly, including skips and unknown costs.

## Reporting issues

GitHub issues are public. Diagnose first with the read-only local tools, then
attach only material whose disclosure level you have reviewed:

| Level | Produced by | Contents | Where it may go |
| --- | --- | --- | --- |
| Local diagnosis | `hypoloom debug inspect` | Status summary on your terminal; `--include-payloads` adds exact research inputs, still local only | Nowhere — it never leaves your machine |
| Default support bundle | Web **Report issue**, TUI **b**, or `hypoloom debug export --support-bundle [--archive]` | Diagnostics with run/Attempt IDs pseudonymized; research text, free-form errors and local paths filtered out; attachment names reduced | A public GitHub issue, after you review the actual package |
| Private support bundle | add `--include-private-data` | Additionally sanitized prompts, transcripts and research attachments — your research content is inside; known credentials and raw environment variables are still excluded | Only a private channel agreed with the maintainers, never a public issue |

A bundle that must include sensitive content may instead be sent privately to
the project authors for support. The authors use submitted material only to
diagnose the reported issue, restrict it to the maintainers involved, never
redistribute it, and delete it once the issue is resolved or on your request —
we are jointly committed to keeping your personal information safe.

Use `--preview` to inspect a report before creating any file, and open the
exported package itself before attaching it. Exports are never uploaded
automatically; sharing is always your separate, explicit action. Never post
API keys, credentials files or private datasets anywhere. If the export
itself fails, say so in the issue text — that is reportable too.
