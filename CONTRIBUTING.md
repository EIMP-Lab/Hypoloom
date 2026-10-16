# Contributing

English · [简体中文](CONTRIBUTING.zh-CN.md)

Develop against the `dev` branch. Read [AGENTS](AGENTS.md), the
[current development state](docs/codex-memory/CURRENT.md) and its
[index](docs/codex-memory/INDEX.md) before changing code or diagnosing a run.
[Architecture](docs/architecture.md) describes runtime ownership; [CLAUDE](CLAUDE.md)
and `.claude/` configure the research runtime separately.

## Environment and focused checks

Follow [Setup](SETUP.md) to create the pinned [environment](environment.yml), then
install development dependencies:

```bash
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[dev,monitor,notify]'
```

For documentation and public Profile changes, run:

```bash
./.conda/envs/hypoloom/bin/python -m pytest -q \
  tests/test_documentation_structure.py tests/test_public_profiles.py
```

These checks cover local links, operation boundaries and production admission of
both templates without starting research, contacting a model or using a GPU.
For code changes, run the tests that exercise the affected behavior.

Before promoting a candidate, follow
[qualification](docs/qualification.md): full offline checks, retained controls,
bounded production-process closure, applicable CPU/GPU work and conditional
real-model compatibility. GPU and paid tests are opt-in; collection must not
start them. A passing repair test alone does not qualify a release.

Preserve tests that reproduce real failures. Current parser-compatibility
templates live under `tests/fixtures/profiles/`; public authoring examples use
v4 under `profiles/templates/`. Update both language variants when changing a
public template and check semantic controls remain equivalent.

## Runs, evidence and engineering boundaries

Keep existing runs read-only unless an exact operation has been authorized.
Use disposable test roots; never attach a development test to a research Host
or a shared resource pool implicitly. Keep secrets, real Profiles, datasets,
logs and runtime environments outside version control.

Use the [local workspace guide](docs/guides/local-workspace.md) to separate
current validation from private development history. Keep evidence and
path-dependent state at their original locations; the optional `current/` and
`history/` directories are browsing links, not runtime inputs or version proofs.

Prefer existing validators, durable identities, transactions and ordinary
tests. New hashes, authority files, gates or custom process protocols require a
concrete failure justification; avoid duplicating the production reducer in a
test oracle. Preserve authentication, authorization and irreversible-operation
protections. Record test scope honestly, including skips and unknown costs.

## Documentation maintenance

Use [the documentation index](docs/README.md) to find current guides. Update
English and Chinese user-facing guides together, link to the reader's language,
and keep examples consistent with the matching template. Configuration comes
before init: copy and edit the private Profile, then select it for launch review.

When adapting documentation from main, port the relevant content without merging
the independent branch histories. Keep dev's qualification procedures, tests and
fixtures, design records, development memory and detailed runtime boundaries.
Public-branch cleanup is not a reason to delete dev evidence. Preserve historical
records at their original paths and keep their incoming links usable.

Keep current decisions compact in CURRENT and route supporting records through
INDEX. Link to qualification evidence instead of duplicating results in guides;
documentation checks alone do not renew a candidate's qualification.

## Reporting issues

Treat material posted to a public issue as public. Diagnose first with the
read-only local tools, then attach only material whose disclosure level you have
reviewed:

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
