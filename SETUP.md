# Setup: installation and configuration

English · [简体中文](SETUP.zh-CN.md)

The recommended entry is `/autoresearch init` in Claude Code. It runs
read-only checks first, presents the complete launch plan, and finally lets
you choose `Start now` or `Prepare only`. Project interaction settings are
sourced from [settings.example.json](.claude/settings.example.json); existing
settings must be merged manually and are never overwritten. A normal
`Start now` does not require exporting the four variable groups first,
leaving Claude Code or reopening the session, and does not ask you to open
separate Host/monitor terminals.

Personal configuration is indexed in [config/README.md](config/README.md).
The public template is `config/hypoloom.env.example`; your real file remains
`.local/credentials/hypoloom.env` in a hidden, ignored directory. Use
`python -B -m hypoloom.bootstrap paths --profile <name>` with the control Python
to see defaults and Profile-declared paths, existence and permissions, without
showing values. Model authentication remains separate from this dotenv file.

Init uses `hypoloom.bootstrap prepare` to validate filled template values in
memory, with zero writes. Only the explicit `--write` preparation creates a
fresh six-file Profile. `bootstrap launch` previews arguments; `--start` invokes
the existing managed lifecycle after authorization, rereading the Profile
before and after Host readiness. Profile changes during preparation stop the
launch. A configuration check makes no provider request and does not certify
model availability or scientific quality. Exact helper usage is in
[the Profile guide](profiles/README.md#deterministic-preparation--确定性准备).

## 1. Control environment

The runtime topology targets Linux: Git, bubblewrap (`bwrap`), working user
namespaces and the Claude Code CLI are required. Managed start also needs an
accessible user-systemd. Control processes use Python 3.12 with SQLite at
least 3.51.3; the SQLite bundled with a system Python may not qualify.

```bash
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[monitor,dev]'
./.conda/envs/hypoloom/bin/python -c \
  'import sqlite3; print(sqlite3.sqlite_version); assert sqlite3.sqlite_version_info >= (3, 51, 3)'
./.conda/envs/hypoloom/bin/hypoloom --help
```

`monitor` installs Textual; `dev` installs test and packaging tools. CPU/GPU
experiments may use a separate research environment; do not automatically
treat the control Python as a project's CUDA environment. Runtime paths in
the public templates must be replaced. The default CPU sandbox requires the
interpreter to live inside the complete runtime environment. A conventional
venv's Python may be a symlink pointing outside the environment and cannot
substitute for the complete Conda environment; this project's installation
and process-closure validation uses the latter. Web browser tests also need
local Chrome/Chromium; `dev` already includes their Python driver dependency
`websockets`.

## 2. Model and literature authentication

Use a pinned exact selector actually supported by the provider; a marketing
alias cannot replace a validated model. Init resolves model options from the
currently selected auth source and displays the model identity, never the
key.

- **environment auth**: use the approved `ANTHROPIC_API_KEY`, plus
  `ANTHROPIC_BASE_URL` when needed. Optional model mappings likewise come
  from an explicitly selected source.
- **Existing settings file**: `--claude-settings` accepts only an eligible
  file that already exists, is an absolute path, a regular non-symlink file
  and mode-0600. Supported in-file auth includes `env.ANTHROPIC_API_KEY` or
  `env.ANTHROPIC_AUTH_TOKEN`. An `apiKeyHelper` cannot be judged usable auth
  from this.
- Interactive `/login` OAuth is not authentication for an isolated Host. The
  settings and tool boundaries of experiment sessions are constructed
  explicitly by the Host; do not hand interactive project settings to Agents
  and do not append `--bare` to change the entry point.

Literature retrieval uses OpenAlex. The complete native expert path needs an
approved dotenv credentials file (recommended
`.local/credentials/hypoloom.env`; template at
`config/hypoloom.env.example`) holding `OPENALEX_API_KEY` and,
when the Unpaywall PDF fallback is selected, `UNPAYWALL_EMAIL`; it must be an
existing, absolute-path, non-symlink, mode-0600 file. A local v4 Profile
declares this path in `literature.credentials_file`; the init/operator flow
passes it as `--credentials-file` to `serve`, `profile-check` and `start`.
The legacy single-key `--openalex-api-key-file` is still accepted; the two
are never mixed. The PDF path needs Poppler's `pdfinfo` and `pdftotext`.
Network timeouts, response/PDF sizes and page limits are reviewed together by
init. Retrieval preserves source URIs and extraction evidence; not obtaining
a PDF or having insufficient metadata must not be described as having read
the full text.

Native `web_search` reaches DuckDuckGo directly by default and does not read
HTTP(S)_PROXY. When that egress is unavailable, explicitly select
`--web-search-fallback openalex` in the Host `serve` arguments, reusing the
OpenAlex configuration above. Fallback results keep the original search error
and are labeled `academic_literature`; they provide academic metadata only
and imply neither general web-search recovery nor full-text access. This
option is not written into the Profile and is not passed to `profile-check`
or `start`. Verify direct search and the fallback path separately; absent
billing information cannot be counted as zero. An explicit
`web_search(..., scope="academic")` directly reuses the client configured by
`--literature-adapter openalex`, without enabling the web fallback. The two
choices appear in the run configuration as `academic_provider` and
`search_fallback` respectively; do not misread a disabled fallback as
OpenAlex being unconfigured. The default PDF fallback is Semantic Scholar;
Unpaywall can also be selected explicitly: add
`--literature-pdf-fallback unpaywall` to the same literature arguments passed
to `serve`, `profile-check` and `start`; the credentials file above must then
also provide `UNPAYWALL_EMAIL` (the legacy single-key
`--unpaywall-email-file` is still accepted and must not be mixed with the
credentials file). The email content is never written into the Profile or
Agent inputs. This option only looks up open full text by exact DOI; it does
not call Unpaywall title search and does not send the OpenAlex key to
Unpaywall. Returned addresses still pass the existing HTTPS/DNS, size, PDF
format and Poppler checks. After a 429, the fallback cools down for 60
seconds within one Host, shared across expert batches; direct open-PDF
downloads are not throttled by the fallback. Configuration checks send no
network requests; verify one actual DOI lookup and full-text fetch
separately.

Keys, real Profiles, the local `.claude/settings.json`, research data and run
records are never committed to Git.

## 3. Choose a bilingual Profile

See the [template guide](profiles/README.md). Public templates uniformly use
the multi-expert design and provide the current v4 `multi-expert-en` and
`multi-expert-zh`. Each set contains six YAML files; place them in a separate
`profiles/local/<profile>` and fill in the goal, models, runtime and data
paths. Init can perform a confirmed copy; it only copies the six files one by
one and refuses to overwrite an existing directory.

Budgets come from the admitted
`create_run_payload.research_contract.budget.agent_invocation`, including
`max_turns`, `max_tokens`, `max_output_bytes`, `wall_seconds`, `max_cost` and
`token_enforcement_mode`. Host arguments must match these values. `max_cost`
is the per-invocation cost threshold passed to the CLI; total and per-expert
thresholds live in `safety.yaml`. A response already sent may exceed the
remaining allowance when its cost is reported, so these parameters are not a
hard cap on the upstream bill. The Host honestly accounts the excess and
blocks later calls; budget planning needs headroom for retries, analysis,
reports and unknown charges. Observe mode only changes token thresholds; it
cannot lift cost, time or resource constraints.

The default Run ID is `<profile>-<YYYYMMDDHHMM>` and the Request ID is that
identity plus `-create-1`. The timestamp is taken once and the state root is
`state/<Run ID>`. On conflict, review the paths again; never reuse an old
root.

## 4. GPU and background Host

Templates default to CPU. GPU work requires filling in the Profile and
reviewing the exact UUIDs, GPU runtime prefix, free VRAM, reserve and
concurrency budget. A standalone resource pool must first be configured
explicitly by the operator; `gpu-pool-status` only queries and never creates
or replaces a service. A shared pool allows multiple experiments on one card;
actual capacity and concurrency limits still apply.

"Allow using cards other users are also using" and "join the shared pool" are
two independent choices. Pool binding enters through the Host's
`--gpu-pool-socket`, `--gpu-pool-namespace` and optional
`--gpu-pool-server-uid`; it is not stored in the six-file Profile. When
reusing an existing pool in a new CC session, supply the confirmed socket
path, choose an independent namespace for the new state root, and check the
actual `serve` arguments and UUIDs in the final review. Searching only `/tmp`
or `/run/user` cannot establish whether a pool already exists under the
project directory; there is no need to rebuild a pool or fall back to
nonpooled because of that. Joining a pool also does not automatically turn an
explicit `gpus: 0` submission or an already granted CPU grant into GPU.

`Start now` calls `.claude/skills/autoresearch/scripts/manage_host.py`,
starts the background Host through user-systemd and confirms `control.sock`
readiness, then submits the reviewed create request. The Host readiness
timeout defaults to 30 seconds and must be greater than 0 and at most 300
seconds. `Prepare only` completes the approved configuration preparation
without starting the Host. `Linger=no` means no promise of continued
background running after the last login session ends; init does not modify
linger.

## 5. Custom interpreter (optional fallback)

If you do not use the default control environment, validate your own
Python/SQLite and dependencies first, then supply the path explicitly. The
following is the optional form for a custom interactive session; a normal
managed init passes the path to the required child processes and does not
need a Claude Code restart.

```bash
export HYPOLOOM_RUNTIME_FAMILY=hypoloom
export HYPOLOOM_PYTHON=/absolute/qualified/environment/bin/python
export HYPOLOOM_HOOK_PYTHON="$HYPOLOOM_PYTHON"
claude
```

A successful install does not equal passed qualification. Next steps:
[daily operations](USAGE.md), the [operator guide](docs/operator-guide.md)
and [G0–G5](docs/qualification.md).
