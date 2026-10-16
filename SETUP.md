# Installation and configuration

English · [简体中文](SETUP.zh-CN.md)

Use `/autoresearch init` in Claude Code to prepare a topic and review its launch.
Complete the following setup before your first run.

## 1. Install the control environment

The server needs Linux, Conda, Git, bubblewrap (`bwrap`), working user namespaces
and the Claude Code CLI. Managed background runs also require user-systemd.
The supplied environment provides Python 3.12 and SQLite compatible with the
runtime requirement of 3.51.3 or later.

```bash
git clone --branch main https://github.com/EIMP-Lab/Hypoloom.git
cd Hypoloom
conda env create --prefix ./.conda/envs/hypoloom --file environment.yml
conda run --prefix ./.conda/envs/hypoloom python -m pip install -e '.[monitor]'
./.conda/envs/hypoloom/bin/hypoloom --help
```

Use this environment for subsequent `hypoloom` commands:

```bash
conda activate ./.conda/envs/hypoloom
python -c 'import sqlite3; print(sqlite3.sqlite_version); assert sqlite3.sqlite_version_info >= (3, 51, 3)'
```

The `monitor` extra installs the terminal interface. Add `notify` for notification
charts or `institutional` for browser integration when needed. PDF extraction
requires Poppler's `pdfinfo` and `pdftotext`; see [institutional access](docs/guides/institutional-access.md)
for browser requirements.

Experiments may use a separate CPU/CUDA environment. Its interpreter must resolve
inside the selected runtime prefix; a venv symlink pointing outside that prefix
does not satisfy the default sandbox requirements. Review the experiment
runtime separately from the control environment.

## 2. Enable the project settings

Review [.claude/settings.example.json](.claude/settings.example.json). For a new
checkout without `.claude/settings.json`, copy it once:

```bash
(umask 077; set -C; cat .claude/settings.example.json > .claude/settings.json)
```

The command refuses to overwrite an existing file. If you already have project
settings, merge the required hooks and MCP configuration manually. Restart Claude
Code after changing hook registration. These settings protect framework files
and expose the research tools; they are separate from model authentication.

## 3. Configure model and literature access

Select the exact model identifier supported by your provider. Initialization
shows the effective model and authentication source without displaying secrets.

| Model authentication | Requirements |
| --- | --- |
| Environment | An approved `ANTHROPIC_API_KEY`; set `ANTHROPIC_BASE_URL` when required by the provider. |
| Existing settings file | Select an absolute path to a regular, non-symlink file with mode 0600. Supported fields include `env.ANTHROPIC_API_KEY` and `env.ANTHROPIC_AUTH_TOKEN`. |

Interactive `/login` OAuth and `apiKeyHelper` do not establish authentication for
isolated research sessions. Do not select this repository's interaction settings
as the research authentication file or change the launch mode with `--bare`.
Local configuration checks do not prove that the provider will accept a request.

Literature and notification credentials belong in the local Profile, not in
public templates or command arguments:

| File | Fields |
| --- | --- |
| `methodology.yaml` | `literature.openalex_api_key`; optional Semantic Scholar, Unpaywall and Elsevier settings |
| `notify.yaml` | Approved notification recipients, tokens and optional `proxy_url` |

OpenAlex is the research literature provider. The default DOI fallback is Semantic
Scholar; Unpaywall requires a contact email when selected. Publisher API keys and
browser login do not guarantee access to every article. See [personal configuration](config/README.md)
for fields, permissions and editing steps.

Keep private YAML files mode 0600 and outside experiment data/runtime mounts.
Model authentication remains separate. Do not put credentials, real Profiles,
research data or run records in Git.

## 4. Prepare and review a topic

First copy the [English template](profiles/templates/multi-expert-en/) into a
new private Profile and configure its six YAML files as described in
[personal configuration](config/README.md). Replace the target, data and runtime
placeholders with real absolute paths. The target repository must be clean;
experiments use private worktrees and read the original repository and approved
data without modifying them.

Then start Claude Code in this checkout and enter:

```text
/autoresearch init
```

Select the local Profile you prepared. Init can also guide creation of a new
Profile from the [English or Chinese templates](profiles/README.md); it does not
overwrite existing local configuration.

Review these items before starting:

- Research goal, completion criteria and expert roles.
- Effective models, authentication and literature access.
- Time, cost and concurrency limits in `methodology.yaml.budget`.
- CPU/GPU permissions, runtime paths and any shared GPU pool.
- Notification destinations and any institutional browser connection.

Cost limits stop further dispatch when exhausted. In-flight responses can report
costs above the remaining allowance; leave room for retries, analysis and the final
report. Unknown charges are retained separately. Agent and experiment deadlines
are independent. See [initialization](docs/guides/initialization.md) for the full review.

Templates default to CPU. GPU work requires explicit device UUIDs and a suitable
CUDA environment. Initialization reviews any managed pool creation, reuse or
maintenance before it becomes part of **Start now**. **Prepare only** performs no
service action. See [GPU resources](docs/guides/gpu-resource-planning.md).

## 5. Start and monitor

Choose **Prepare only** to save the approved configuration, or **Start now** to
launch it. The `manage_host.py` helper starts the Host through user-systemd,
checks `control.sock` readiness and submits the reviewed run. No separate Host
terminal is required. Readiness defaults to 30 seconds; allowed values are
more than zero and at most 300 seconds.

Initialization returns the Run ID, state directory and monitoring commands.
Use `/autoresearch-monitor` to reopen an observer. Leaving the monitor keeps
research running; End run requests cancellation and waits for cleanup.
See [Usage](USAGE.md).

If user-systemd reports `Linger=no`, background work may stop after the final
login session ends. Initialization does not change this setting. Connection or
provider failures during a run are handled through [API recovery](docs/guides/provider-recovery.md),
not by editing the run database or replacing its runtime.

## Optional configuration

- **Notifications:** configure a server-side HTTP CONNECT proxy in `notify.yaml`
  before initialization if required. It is independent of model/literature proxies.
  See [notifications](docs/guides/notifications.md).
- **Institutional access:** choose a browser mode during init. A Windows client
  requires user-level SSH keys and noninteractive login before its generated Setup
  command. See [institutional access](docs/guides/institutional-access.md).
- **Version reminders:** `hypoloom version-check` checks the configured branch;
  `bootstrap paths --offline` skips its remote lookup. It never installs updates.
  See [version reminders](docs/guides/version-check.md).
- **Custom control Python:** validate Python, SQLite and dependencies, then set
  the paths before starting Claude Code:

```bash
export HYPOLOOM_RUNTIME_FAMILY=hypoloom
export HYPOLOOM_PYTHON=/absolute/control-environment/bin/python
export HYPOLOOM_HOOK_PYTHON="$HYPOLOOM_PYTHON"
claude
```

Existing runs keep their original runtime and configuration. Updating the checkout
applies to new runs; cross-version run migration is not supported.
