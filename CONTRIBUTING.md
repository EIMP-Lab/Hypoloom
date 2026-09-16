# Contributing

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
