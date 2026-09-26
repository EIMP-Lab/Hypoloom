---
name: autoresearch
description: Bootstrap local Hypoloom paths or operate the independent Workflow Host. Routes /autoresearch to CC-only init, read-only component-profile admission, exact run creation, status, monitor, explicitly authorized notifications, retained module debugging, oracle, cancellation, backup, or offline restore. Never falls back to the retained legacy runtime.
---

# /autoresearch — bounded Hypoloom operations

Use this skill only for the independent `hypoloom` runtime. The user
invokes `/autoresearch <subcommand> [args]`. If the subcommand or an exact
target is missing, show the supported operations and stop; do not infer a run
from a legacy active pointer. `init` is Claude Code bootstrap orchestration;
all other subcommands route to the existing Hypoloom CLI.

## Runtime boundary

ACI16 Grounding can persist a question in WAITING_HUMAN without an open model
invocation. The existing status/monitor JSON exposes `grounding_dialogue` with
exact decision ID, question and answer history. Obtain a real user answer; never
infer it from an expert note or silently accept an offered option. The new
`hypoloom.cli answer` command requires the selected state root, run ID,
decision ID, expected run revision and the exact free-text answer (see USAGE).
It uses the existing Host socket, never direct SQLite writes. A slash-command
alias is not provided by this notice. With v4 `goal.evaluation: grounding`,
Grounding writes/tests common acceptance in private `/acceptance`; expert nodes
read the same accepted source without write permission. LOCAL support never
completes this goal. One GOAL candidate must pass the common checks, independently
replayed by Oracle, without combining experts' partial scores. Admission or a
completed dialogue is not scientific success or permission to spend; see STATUS.

`HYPOLOOM_RUNTIME_FAMILY` is a fail-closed routing assertion, not a session
bootstrap requirement for this Hypoloom-only skill. At the beginning of every
invocation, inspect the inherited value without changing it. An absent value is
allowed for every operation in this skill. A nonempty value other than exactly
`hypoloom` stops every operation, including `init`; never reinterpret or override
a conflicting family.

Set `HYPOLOOM_RUNTIME_FAMILY=hypoloom` explicitly in the environment of every
Python helper, hook-independent check, and `hypoloom.cli` subprocess
started by this skill. Also pass the selected control Python, profiles root,
and state root explicitly to each command that needs them. A child process
cannot update the already-running Claude parent, but it does not need to: init
and the automatic flow complete in this same Claude interaction. Do not force
a relaunch merely to export runtime variables.

Use the qualified project interpreter, not an ambient base Python whose SQLite
may be too old. For a real local topic, the profiles root is `profiles/local`,
not the tracked template root:

```bash
CONTROL_PYTHON="$REPOSITORY/.conda/envs/hypoloom/bin/python"
PROFILES_ROOT="$REPOSITORY/profiles/local"
STATE_ROOT="$REPOSITORY/state/$PROFILE_NAME"
test -x "$CONTROL_PYTHON"
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$CONTROL_PYTHON" -c \
  'import sqlite3; assert sqlite3.sqlite_version_info >= (3, 51, 3)'
```

Never fall back to another runtime or an old research repository. An unsupported
profile is a stopped Hypoloom admission. No legacy runtime bridge is provided.

## Bootstrap init — Claude Code only

`/autoresearch init` has no corresponding `hypoloom.cli init`
subcommand. Implement it with Claude Code's read-only inspection,
`AskUserQuestion`, `python -B -m hypoloom.bootstrap`, the existing read-only
`profile-check`, and the existing Host/CLI lifecycle. It is the
single interactive flow for preparing a local profile and, only after one final
authorization, optionally starting a fresh run. It adds no configuration or
workflow authority.

The suggested repository-local layout is:

```text
<repo>/.conda/envs/hypoloom/bin/python
<repo>/profiles/templates/<template-safe-name>   # tracked copy source
<repo>/profiles/local/<profile-safe-name>        # ignored real profile
<repo>/profiles/local/<profile-safe-name>/methodology.yaml # private literature keys, 0600
<repo>/profiles/local/<profile-safe-name>/notify.yaml      # private notification tokens, 0600
<repo>/state/<profile-safe-name>-<YYYYMMDDHHMM> # ignored future state root
```

Start by running `python -B -m hypoloom.bootstrap paths --repository <repo>`;
add `--profile <name>` when selected. Show `profile_credentials` paths and
permissions and the local Profile/template directories, never values. Literature
and notification credentials are filled directly in these private YAML files;
no separate env is required. Public templates contain blank fields. The helper's
`credentials`/`credentials_template` entries describe legacy dotenv locations
only. Model authentication remains the selected existing environment/settings.

Show the helper's `version_check` result once in the user's language, including
the comparison remote/branch, local/remote revisions and working-tree status.
`remote_differs` means a different remote tip with unverified ordering; do not
call it proven behind. `unavailable`/`offline` mean unknown freshness, not latest.
Forward `--offline` when the user requests offline init. This is a bounded,
advisory check: it never blocks the launch decision, prompts for Git login or
updates code. Do not repeat it after selecting the Profile, poll it in the Host,
run fetch/pull automatically or migrate existing runs. To inspect paths again,
use `--offline` and retain the first check's result in the review. See the
[version reminder guide](../../../docs/guides/version-check.md).

Use the deterministic helper for prospective Profile validation and launch
assembly. It reuses admission, credentials, model-source inspection and the
existing managed lifecycle. It creates no launch manifest. Models choose and
explain user policy; they must not manually reimplement these checks or invent
command arguments. A passing configuration is not provider availability or a
scientific-quality qualification; preview performs no provider probe.

### Read-only decision and qualification phase

Before the user's final confirmation, perform **zero writes**: do not create a
directory, copy or chmod a file, create the state root, install a package,
contact a model provider, create a user unit, or start a process that can write
workflow state. No Host, Agent, service, scope, or run exists because of this
flow before that confirmation.
Resolve the repository root read-only (prefer
`git -C "$CLAUDE_PROJECT_DIR" rev-parse --show-toplevel`) and inspect the
proposed paths. Never read, resume, migrate, or modify an existing run as part
of init. Every Python process launched by init—including dependency checks,
Agent preflight, compute snapshot, and both profile admissions—must set
`PYTHONDONTWRITEBYTECODE=1`; do not leave import caches in the checkout.

Use `AskUserQuestion` for the decisions below. Ask only questions whose answers
were not supplied by the invocation, group closely related choices when the UI
permits, and show concrete detected values so that the user does not have to
reconstruct commands manually. Propose a consolidated default set where safe:
the requested existing local profile, the qualified project control Python, an
absent timestamped state leaf, the same timestamped identity as the run ID,
that identity suffixed with `-create-1` as the request ID, and process delay
zero. After the exact profile safe name is selected, obtain one server-local
minute with the read-only command `date '+%Y%m%d%H%M'`; accept only exactly 12
ASCII digits. Define the single launch identity as
`<profile-safe-name>-<YYYYMMDDHHMM>`, preserving the profile name's exact case
and bytes. For example, profile `my-topic` at 2026-08-28 11:23 produces
`my-topic-202608281123`. Use that exact identity for both
the state-root leaf and run ID, and use
`<profile-safe-name>-<YYYYMMDDHHMM>-create-1` for the request ID. Capture the
timestamp once for the prospective run and never recompute it independently
between those three values. Do not offer a date-only name, an `-r2`/`-r3`
fallback, or a timestamp containing an internal separator. Ask the user to
accept or explicitly replace the concrete identity and paths in one
consolidated choice; do not ask them to invent every path and identifier from
scratch.

1. **Profile source.** Enumerate safe immediate children of both
   `<repo>/profiles/local/` and `<repo>/profiles/templates/`. Offer either
   adoption of one existing ignored local profile with no copy, or copying one
   tracked template to a new ignored local profile. When the requested local
   profile already exists, adoption is the normal path only when its admitted
   contract already matches the requested runtime; never replace its files or
   force the user back through template selection.

   Read the admitted profile contract version before presenting its runtime.
   Treat `methodology.yaml.schema_version: 1` as compatible only with the
   legacy three-command Agent path (`PROPOSE_HYPOTHESIS`,
   `DRAFT_EXPERIMENT_SPEC`, and `ANSWER_RENDEZVOUS`). Schema v2 remains a
   compatible legacy six-file expert-loop source. Product development should use
   schema v4 and the bilingual `profiles/templates/multi-expert-en` /
   `profiles/templates/multi-expert-zh` templates.
   Review the chosen language and its explicit prompt/report settings.
   Older parser examples under `tests/fixtures/profiles/` are test inputs only. Schema
   v3 requires the exact admitted `mode.research_loop: expert-six-node/1`
   controls and the YAML-only layout described below. Never relabel a v1/v2
   admission in chat or synthesize v3 controls outside the six profile files.
   V4 uses the same exact six YAML filenames but different authoring fields:
   shared goal or explicit completion signals; expert persona/focus, model/effort
   and fork/batch limits; methodology literature/inner-loop/interaction/manager/
   memory; language; safety and notify. Review them as written, including actual
   source paths, budgets, HTTP(S)/GPU permissions and delivery recipients.
   V4 admission is not an implicit ACI promotion; inspect the current default
   identity and STATUS rather than forcing a new protocol through chat or argv.
   Its real literature path also requires the reviewed OpenAlex adapter/key-file
   and bounded query/PDF configuration described below. Never infer v4 shared-goal
   ownership or migrate an existing profile from old Attempt ordering.

   For legacy `shared_goal` without `goal.evaluation`, explain that one expert's actual accepted
   SUPPORTED assessment under canonical experiment evidence, the frozen
   criterion and Policy stops the whole run. With `evaluation: grounding`, only
   one candidate passing the complete common evaluation qualifies, not local
   support. Do not promise ADJUDICATE, a full
   round, continued optimization or Leader/Debate after success. The Host
   atomically records GOAL_MET and enters STOPPING with disposition COMPLETED,
   cancels other research and collects real terminal observations, aborts active
   interactions without inventing completion/refutation, then generates
   FINAL_REPORT and FINISH_STOP COMPLETED. Report/integrity failure remains
   FAILED. Cancellation before success remains CANCELLED; repeating CANCEL
   after successful stop preserves COMPLETED, with FAILED taking precedence.
   Explicit required-conjunction signals keep their existing rule. This is a
   runtime behavior explanation, not permission to start or modify a run.

   For a new v4 topic, preserve an existing local profile unchanged. If its
   admitted semantics do not match the requested topic, use a new ignored
   `profiles/local/<new-safe-name>` destination from a public v4 template.
   Review the exact topic, objective, completion signals, roles, runtime,
   budgets and permissions. Never modify the source profile in place or infer
   missing scientific choices from old Attempts. Show the proposed field-level
   changes before final confirmation; withhold `Start now` until the existing
   admission validator accepts the in-memory candidate. After writing,
   `profile-check` must admit the actual new destination before Host launch. Do not offer automatic format migration.

   A schema-v2, schema-v3 or schema-v4 real `expert-six-node/1` run requires the explicit `openalex`
   literature adapter. New v4 Profiles store `literature.openalex_api_key` and
   optional `literature.unpaywall_email` directly in private `methodology.yaml`.
   Prepare the blank six-file Profile first; the user fills secrets with a local
   editor, then init rereads it. Never request secrets in chat or pass them through
   `--set`, argv or public templates. Keep the file regular, non-symlink and mode
   0600. Admission strips literals and derives the original YAML path for the
   existing reader; no copied credential file is created. Older Profile-declared
   dotenv and single-key sources remain compatible alternatives, never conflicting
   overrides. Do not read/print whole populated Profiles for review: use admission
   and the safe helper output. Do not copy, chmod or edit existing credential values.
   Resolve and review one in-memory
   literature argument vector containing all of these exact flags and values:

   ```text
   --literature-adapter openalex
   --credentials-file <derived-private-methodology.yaml-or-legacy-0600-path>
   --literature-pdfinfo <absolute-qualified-path>
   --literature-pdftotext <absolute-qualified-path>
   --literature-timeout <reviewed-positive-seconds>
   --literature-max-response-bytes <reviewed-positive-bytes>
   --literature-max-pdf-bytes <reviewed-positive-bytes>
   --literature-max-pdf-pages <reviewed-positive-pages>
   ```

   Automatically pass the same exact reviewed literature argument vector to
   `serve`, both `profile-check` calls, and `start`; do not reconstruct or
   omit values between phases. Do not fall back to `offline` when OpenAlex
   qualification fails. The admitted profile, the reviewed existing
   credentials path, and the existing CLI preflight remain the only sources
   of truth; do not persist another literature config, marker, or credential
   receipt. The legacy single-secret flags `--openalex-api-key-file` and
   `--unpaywall-email-file` remain accepted instead of `--credentials-file`,
   never mixed with it.
   The DOI-only PDF fallback defaults to Semantic Scholar. If the operator
   selects Unpaywall, append `--literature-pdf-fallback unpaywall` to that
   same
   literature vector for serve, profile-check and start; the declared
   credentials file must then also provide `UNPAYWALL_EMAIL`. Read the file
   without displaying its contents or changing it;
   do not use an OpenAlex key as the contact email. This selection performs
   exact DOI lookup after direct PDF retrieval is unavailable; it does not
   enable Unpaywall title search or a second concurrent fallback chain. The
   existing PDF verification and cross-batch 429 cooldown remain in effect.
   Preflight checks configuration only; actual DOI/full-text availability needs
   a bounded online observation with the approved contact source.
   Review native web search separately: it uses direct DuckDuckGo HTTPS and
   does not inherit HTTP(S)_PROXY. When that route is unavailable, offer the
   explicit Host-only `--web-search-fallback openalex` option with the existing
   reviewed OpenAlex key and bounds. It returns academic metadata, not general
   web coverage or downloaded full text; replies retain the primary failure.
   Preserve the accepted option in `serve` argv; do not pass it to profile-check
   or start, add it to Profile YAML, or infer approval from a listening proxy.
   Describe direct-search and fallback health separately. One bounded live
   request is evidence; a configured flag or CLI help is not. Do not reset or
   assume zero provider charges when a fallback is used.
2. **Control interpreter and paths.** Accept or replace the suggested qualified
   control interpreter at
   `<repo>/.conda/envs/hypoloom/bin/python`. Select the absolute
   `<repo>/profiles/local` profiles root and propose the fresh future state root
   at `<repo>/state/<profile-safe-name>-<YYYYMMDDHHMM>`. The operator may
   explicitly replace it with another absent absolute state-root path. Keep the
   control Python distinct from the topic's admitted experiment runtime prefix;
   a CUDA/topic environment is not automatically the Hypoloom control interpreter.
3. **Prospective request.** Present the shared launch identity as the future run
   ID and that identity plus `-create-1` as the stable request ID for the initial
   `CREATE_RUN` and its exact transport retries; do not ask separate naming
   questions unless the operator explicitly chooses replacement. Ask for a
   finite non-negative process delay and whether this is a deterministic
   fake-Agent demo or a real Claude-Agent topic. Run and
   request IDs must both use
   `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. Because the request suffix and timestamp
   are fixed, the canonical default is available only when the selected profile
   safe name is at most 106 characters. For a longer profile, report that the
   default is unavailable and require explicit grammar-valid replacement IDs;
   never truncate the profile name automatically. These canonical values are
   defaults, not forced identities: an explicit replacement state-root path,
   run ID, and request ID remain valid when they satisfy their existing
   validators. The identifiers remain
   prospective until an explicitly selected `Start now` action persists the
   exact request.
4. **Interactive project settings.** If
   `<repo>/.claude/settings.json` does not exist, separately ask whether to copy
   the tracked `<repo>/.claude/settings.example.json`. If it exists, report
   that it will be left byte-for-byte and mode-for-mode untouched; never offer
   to overwrite, chmod, or merge it automatically.
5. **GPU UUID allowlist.** Ask whether the prospective run is CPU-only or may
   use GPU. CPU-only means no Host GPU flags and explicit `--cpu-only` on both
   admission checks and `start`. GPU authorization requires `--allow-gpu` on
   those same three client invocations. These choices are mutually exclusive
   and required; omission is an error. For GPU, run
   `nvidia-smi` read-only and show, for every detected card, its index, stable
   UUID, model, total and free memory, utilization, and current compute
   occupancy. Alongside the inventory show the admitted profile's exact
   `min_vram_per_experiment`, `gpu_vram_reserve_mb`, and their sum. If configured,
   also show `initial_vram_per_experiment` and its sum with the reserve; this is
   the pre-measurement dispatch threshold. Distinguish the hard workload floor
   from a conservative starting guess. For a new explicitly pooled run, the
   operator may configure a lower hard floor and a separate initial estimate;
   reuse existing in-scope approval, otherwise include the proposed values and
   their measurement basis in the existing final launch review. Never lower a
   floor or reserve automatically because a card is occupied. Require the
   operator to approve the exact UUID subset and whether occupied UUIDs may be
   shared. Reuse an already explicit in-scope sharing decision; otherwise ask.
   A shared and occupied
   card may be allow-listed only by that explicit answer; do not reject all
   occupied cards automatically. Conversely, free memory, utilization, an
   empty process list, and compute occupancy are capacity/risk observations,
   never authorization; there is no automatic inference of GPU permission
   from any of them. Never kill, signal, reset, suspend, or otherwise
   control an external process to obtain capacity. Require the admitted
   profile's absolute runtime prefix with a real executable `bin/python`.
   Agents choose CPU/AUTO/GPU_REQUIRED and may submit an exact GPU count under
   the admitted per-experiment cap. The omitted-count compatibility path chooses
   at most one GPU; a positive explicit count requires that complete count,
   with per-device wait/retry/pressure checks and no silent reduction. After selection, reuse
   `LocalComputeResources` with that exact allowlist/runtime and call its
   read-only `snapshot`; require the returned UUID set to equal the accepted
   set before calling the GPU **configuration** eligible. UUID equality does
   not prove current dispatch capacity. If every authorized card is below the
   profile threshold, report **"launch configured but currently waiting
   capacity"** rather than claiming that GPU work can dispatch immediately;
   explain that retained nonpooled `AUTO` may choose CPU and `GPU_REQUIRED`
   freezes a UUID and waits. Explicitly pooled requests instead preserve the
   accepted GPU count and workload intent, selecting actual UUIDs per Attempt;
   capacity wait before dispatch opens and consumes no PROCESS Attempt.

   **Shared pool.** For coordinated sharing within or across runs, review a
   pool binding: an absolute socket path, a stable namespace unique to this
   state root, and an optional expected server OS UID. This does not replace
   the exact GPU allowlist/runtime or per-run `--allow-gpu` decision. Sharing
   an occupied card is not the same choice as joining a shared pool.

   **Detection.** Probe once per init before concluding anything about pool
   availability:

   ```bash
   "$CONTROL_PYTHON" -B \
     "$REPOSITORY/.claude/skills/autoresearch/scripts/manage_gpu_pool.py" \
     status --repository "$REPOSITORY"
   ```

   This probe is read-only: it never creates a pool, service, directory or
   reservation. It reports each conventional project pool endpoint under
   `$REPOSITORY/.local/gpu-pools/<name>/control.sock` as live, stale,
   unauthorized or absent with its served UUIDs, and recalls the pool bindings
   stored in prior runs' durable CREATED records (newest first, with current
   liveness) as candidate values. Only an endpoint beneath this repository's
   `.local/gpu-pools/` is this project's pool; any other endpoint, however
   healthy, is external and enters this review solely through the operator's
   explicit supply, labeled as external. When the operator refers to a
   previously confirmed pool, carry its supplied endpoint into this review.
   If the endpoint is missing from this CC session, ask for it or use the
   read-only recall above; Codex maintenance memory is not automatically CC
   runtime context. A search limited to `/run/user` or `/tmp` does not
   establish that no pool exists: sockets may be beneath a project directory.
   Do not recommend "nonpooled, as before" merely because that search found
   nothing, and never infer a prior run's participation from socket absence:
   its stored binding decides what it was. Historical bindings provide
   candidate values, not authorization to reuse an old state-root namespace
   or silently override a newer explicit GPU/pool choice.
   When the requested behavior includes choosing whichever authorized GPU has
   capacity after a queue wait, or learning per-experiment memory requirements
   through resource Skill updates, recommend this pooled path explicitly.
   `initial_vram_per_experiment` requires the pooled path for GPU-enabled runs;
   nonpooled GPU starts reject that setting. Omission preserves existing fixed
   thresholds. Only a complete successful comparable Process measurement allows
   a later estimate below the initial value; the hard floor and device reserve
   still apply. Update only a prospective profile under the existing launch
   review, never an existing run or its frozen configuration. An
   allowlist with several GPUs alone does not provide deferred allocation:
   nonpooled requests can remain pinned to a low-capacity GPU while another
   authorized GPU becomes available. If no pool is configured and the operator
   declines the reviewed creation below, explain that limitation in the final
   review rather than presenting the nonpooled launch as satisfying
   dynamic/shared scheduling. If the operator explicitly chooses nonpooled
   after that explanation, retain the choice in the final review. Explicit
   `gpus: 0` submissions remain CPU work even with a pool; joining a pool does
   not migrate committed CPU grants.

   **Live pool preflight.** For any reviewed live pool (project, recalled or
   external), use the control Python for the read-only preflight below,
   passing `--expected-server-uid` only when the same UID was reviewed:

   ```bash
   "$CONTROL_PYTHON" -B -m hypoloom.cli gpu-pool-status \
     --socket-path "$GPU_POOL_SOCKET" --expected-server-uid "$GPU_POOL_SERVER_UID"
   ```

   Record exit status and the authorized capacity response; require the approved
   UUID subset to be present. An unavailable pool blocks `Start now`, but not
   preparation; a reachable pool with insufficient headroom is a disclosed
   capacity wait. Never substitute a private allocator or silently remove the
   pool configuration.

   **Pool creation.** When the operator wants pooled GPU work and no live
   project pool or explicitly supplied external pool exists, include the exact
   pool plan in the final launch review: pool root
   `$REPOSITORY/.local/gpu-pools/shared`, socket `control.sock` inside it, the
   derived `hypoloom-gpu-pool@<escaped-pool-root>.service` unit, the single
   `--member <this-uid>=<approved UUID subset>` line, and `--reserve-mib`
   equal to the admitted profile's `gpu_vram_reserve_mb`. Creation runs only
   after **Start now** approval, immediately before the managed Host start,
   through the bounded helper, never a hand-rolled service:

   ```bash
   "$CONTROL_PYTHON" -B \
     "$REPOSITORY/.claude/skills/autoresearch/scripts/manage_gpu_pool.py" \
     ensure --repository "$REPOSITORY" \
     --pool-root "$REPOSITORY/.local/gpu-pools/shared" \
     --socket-path "$REPOSITORY/.local/gpu-pools/shared/control.sock" \
     --member "$(id -u)=GPU-...,GPU-..." --reserve-mib "$GPU_VRAM_RESERVE_MB"
   ```

   Require exit 0 with status `ready` or `reused`; verify the returned socket
   and devices against the review and reuse that socket as `$GPU_POOL_SOCKET`.
   Any refusal (`foreign_pool_root`, `member_uid_mismatch`, `service_busy`,
   `pool_conflict`, `pool_ready_timeout`, `pool_verification_failed`) blocks
   `Start now` and is reported verbatim; never fall back to nonpooled
   silently. The helper deploys only beneath this repository's
   `.local/gpu-pools/`, only for the invoking UID, never against a live or
   conflicting service, and never forwards an ambient API key. A stale socket
   from a dead pool is cleared by the pool service itself on start. Do not run
   `gpu-pool-serve` directly, install a service by hand, change
   socket/directory permissions or groups, or enable linger as part of init.
   Pool stop, replacement and member reconfiguration are separate operator
   actions outside init. Different private pools must not coordinate the same
   shared device set independently.

   Show Profile workload floor, per-device reserve, known submitted/measured
   workload estimates and current effective capacity separately. Before any
   experiment estimate exists, say unestimated rather than inventing one.
   Device occupancy is not experiment usage; partial own-use samples remain unknown.
   An explicit OOM and pressure cancellation are different observations.
   The final review must state whether this run participates in the shared pool;
   a nonparticipating Host has no cross-root capacity coordination.
   Reuse a pool binding already confirmed in this session; do not silently fall
   back to a local Host when reconstructing the launch command. Keep its socket,
   namespace, server UID and reviewed GPU UUIDs in the same concrete `serve`
   argument vector used by the managed helper.
6. **real-Agent readiness.** Skip this item for a fake demo. For real work,
   resolve candidates such as `command -v claude` through `readlink -f`, then
   ask the operator to accept or replace these exact launch files:

   - an absolute executable regular file that is not a symlink;
   - an absolute executable bubblewrap file, normally `/usr/bin/bwrap`.

   Select one authentication transport before selecting a model, without
   contacting the provider:

   - First test only whether the inherited `ANTHROPIC_API_KEY` is nonempty. Do
     not print, return, log, persist, summarize, measure, hash, or otherwise
     inspect its value, and never ask the user to paste it into chat. When it is
     nonempty, select **environment auth** by default. Do not require, create,
     or configure a dedicated Agent settings file. The managed Host must use
     the helper's systemd scope transport, inherit the nonempty
     `ANTHROPIC_API_KEY` without putting it in an argument or unit property, and
     omit `--claude-settings`. The real allowlisted key remains in the Host's
     provider relay outside the sandbox; the full-CC Agent receives only the
     local bridge's dummy key. Never forward a real provider key or auth token
     into Agent environment, mounts, arguments or transcripts.
   - When the key is absent, report that environment auth is unavailable, then
     inspect an existing **user-level Claude settings** candidate before asking
     for another file. Resolve `${CLAUDE_CONFIG_DIR}/settings.json` when
     `CLAUDE_CONFIG_DIR` names an absolute directory; otherwise resolve
     `${HOME}/.claude/settings.json`. This user-level path is distinct from the
     repository's interactive project settings. It is eligible only when it is
     an absolute regular, non-symlink mode-`0600` file containing one JSON
     object and one of these directly available noninteractive auth
     configurations:

     - nonempty `env.ANTHROPIC_API_KEY`;
     - both nonempty `env.ANTHROPIC_AUTH_TOKEN` and
       `env.ANTHROPIC_BASE_URL` for an explicit gateway.

     Do not qualify `apiKeyHelper`: the isolated Agent sandbox does not mount or
     validate a helper executable and its dependencies.

     Check only those boolean/type/length facts. Never print, return, log,
     persist, summarize, measure, hash, or otherwise expose any value. If the
     user-level candidate qualifies, propose its exact absolute path as the
     default existing-settings auth choice; do not offer an empty JSON file in
     preference to it. Require operator acceptance of the path and detected
     auth kind, not of any secret value.
   - If that candidate does not qualify, the optional compatibility path is
     another operator-selected, already-existing settings file with the same
     real-file, mode, JSON-object and recognized-auth checks. Never treat JSON
     validity alone as authentication readiness. The resolved repository files
     `<repo>/.claude/settings.json`, `<repo>/.claude/settings.local.json`, and
     `<repo>/.claude/settings.example.json` remain forbidden as real-Agent auth
     because they configure the interactive project session. Only an eligible
     existing-settings path adds `--claude-settings` to the Host command, and
     selecting it prevents the CLI from forwarding an ambient
     `ANTHROPIC_API_KEY`. Never create or edit the file from init, and never
     combine the transports.

   Explain in the final review: after Start now, the new Host copies the selected
   settings under its existing controller lock to
   `<state-root>/host/claude-settings.json` (directory 0700, file 0600).
   Restarts reuse this private input instead of the interactive source. Init
   still never edits the user's settings. Never modify the private copy or
   recreate a missing copy in an existing run. Its bytes remain pinned by the
   existing runtime version. Configuration incompatibility exits 78; newly
   launched managed services use `RestartPreventExitStatus=2 78`. Both CLI error
   exits (2) and configuration incompatibility (78) require operator review.

   After the authentication source is fixed, resolve model choices from that
   same source with the repository helper and the qualified control Python:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 "$CONTROL_PYTHON" \
     "$REPOSITORY/.claude/skills/autoresearch/scripts/resolve_model_options.py" \
     --environment

   PYTHONDONTWRITEBYTECODE=1 "$CONTROL_PYTHON" \
     "$REPOSITORY/.claude/skills/autoresearch/scripts/resolve_model_options.py" \
     --settings "$AUTH_SETTINGS"
   ```

   Run exactly one form. Environment auth may use only the inherited mapping;
   it must not borrow values from a user settings file. Existing-settings auth
   may use only the selected settings object; ambient mapping must not override
   it. The helper validates the same recognized-auth boundary and returns only
   allowlisted non-secret facts. Never print or independently parse the source
   settings. In particular, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
   `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, and the
   compatibility `ANTHROPIC_DEFAULT_FABLE_MODEL` are model mappings, while
   names ending in `_MODEL_NAME` are display labels and never model identities.

   Dynamically offer the helper's available logical tiers; do not invent dated
   Anthropic model IDs. Show each choice as
   `logical_selector -> claude_model`. The first value is the operator-facing
   tier; the second is the pinned Claude Code launch selector and the exact
   identity required from `modelUsage`. Prefer the mapped tier named by the
   selected source's current model setting when one is available. If the helper
   reports a distinct valid
   `configured_default`, also offer it explicitly as the source's configured
   default without pretending that it is a logical tier. A trailing `[1m]` is
   retained in this single exact identity: observed Claude Code 2.1.232
   `modelUsage` preserves it even when Claude Code may transform the model name
   on its downstream provider request. Hypoloom does not independently
   observe or claim that downstream wire identity. If
   `configured_default_error` is non-null, explain that
   the current default is unresolved or invalid and require a mapped choice or
   valid custom selector; never pass that default to the Host.
   Offer `fable` only when the selected source explicitly maps it.

   If no suitable mapped tier exists, the operator may enter one custom pinned
   non-alias Claude selector. Validate it with
   `hypoloom.claude_agent_host.validate_claude_model_selector` under the
   control Python, then show the exact selector for confirmation. Reject
   unresolved aliases such as `sonnet`, `opus`, `haiku`, and `fable`,
   whitespace, and malformed `[1m]` suffixes. Pass only the
   accepted Claude selector to `--claude-model`; the Host freezes that one value
   as its fallback model. Profile model/effort overrides take precedence per
   Agent route. Inspect `effective_configuration.models` from `bootstrap launch`:
   show the effective model/effort and source for shared Grounding, each expert's
   stages, Leader, Observer and other auxiliary roles. These are possible routes,
   not a promise that every role will execute. Explicitly highlight
   `profile_models_overriding_host_default`; do not present the Host fallback as
   a global override. For a new Profile, apply the user's reviewed model policy
   through its existing defaults/role/stage fields. An adopted Profile remains
   unchanged. The settings configured default is not the live interactive
   session's `/model` choice; do not claim to inherit that session implicitly.
   Each Agent pins its resolved model and requires its exact `modelUsage`
   identity. Init does not prove that a mapped model is available.
   Changing providers, settings, mappings, or the chosen selector requires a
   new init, new Host, and new run; never alter a frozen existing run.

   An ordinary interactive Claude `/login` OAuth session is not reusable by
   this adapter: every command runs with an isolated HOME and Claude config
   directory. ACI 11 keeps provider credentials in the external relay and does
   not use `--bare` or `--safe-mode`; those flags belong to predecessor paths.
   Do not call `/login` state authentication readiness. Reuse
   `ClaudeAgentHost.preflight` with `PYTHONDONTWRITEBYTECODE=1` to validate the
   selected launch boundary without executing Claude or contacting the
   provider. For environment auth, construct the preflight with
   `settings_path=None` and do not pass the key value as an argument or report
   field; the separate nonempty-presence check is sufficient. For the
   compatibility path, construct it with the validated settings path. Passing
   either local preflight proves only the launch inputs, not provider
   authentication or model availability. The real-provider G5 evidence covers
   only the mode-`0600` existing-settings gateway path. Environment
   `ANTHROPIC_API_KEY` currently has deterministic real-subprocess-stub launch
   wiring and secret-suppression coverage, not provider qualification.
   If it is not ready, report one blocker and disable automatic start instead
   of creating a secret file or silently using interactive settings.
7. **Budgets.** Read the complete per-invocation budget only from the admitted
   `create_run_payload.research_contract.budget.agent_invocation`. It contains
   five boundary values—`max_turns`, `max_tokens`, `max_output_bytes`,
   `wall_seconds`, and `max_cost`—plus exactly one
   `token_enforcement_mode`, either `enforce` or `observe`. Show all six exact
   profile-derived values together with the
   admitted run-token, wall-clock, experiment and per-expert limits. Never
   offer independent Agent-budget defaults, ask the operator to replace these
   five values or the mode during init, or silently substitute the
   Host's legacy fallback. `max_cost: null` explicitly removes the single-call
   USD cap; configured cumulative USD caps and turn/wall/output limits still
   apply. Review that exposure; do not turn missing accounting into zero.
   A source profile may omit the mode and let admission
   normalize it to `enforce`, but init must read the explicit admitted mode and
   must never guess that default. `enforce` makes per-call, run and role token
   thresholds hard limits. `observe` treats those token thresholds only as
   recorded references: actual usage remains durable accounting, while turn,
   cost, wall, output, experiment and every other existing limit remains hard.
   To change a bound, the operator edits the prospective local profile and runs
   admission again. State that `max_cost` is per invocation, not a total-run
   price. Separately read the admitted research-contract budget's
   `total_budget_usd` and effective `per_expert_budget_usd` for each role
   (`budget.roles.<role>.per_expert_budget_usd`, then the shared per-expert
   value). When absent, explicitly
   display "No total-run USD ceiling" or "No USD ceiling for this expert";
   never infer a total from `max_cost`, token references, or experiment counts.
   New v4 Profiles put these limits in `methodology.yaml.budget`, together with
   `wallclock_hours` and `experiment_timeout` (seconds). The six-file layout is
   unchanged: goal owns the scientific target; safety owns permissions; budget
   owns resource/time/financial limits. Older safety USD/timeout and goal wall
   fields remain accepted only when their canonical budget field is absent and
   produce a migration warning. Duplicate declarations are rejected, even when
   equal. Never silently keep the larger value. Changing a limit requires editing
   the prospective profile and repeating admission, not migrating a retained run.
   For ACI 22 or later, explain that EXECUTION may hand a submitted experiment
   to Host waiting and resume the same node/session after terminal collection.
   Both invocations use these admitted budgets; the handoff does not grant an
   extra allowance or extend a wall deadline. Read the actual runtime version,
   preserving terminal-only completion for earlier bundles.
   Also compare invocation `wall_seconds` with admitted `experiment_timeout`:
   these independently limit the controlling CC invocation and the scientific
   Process. A long experiment timeout does not keep its CC controller alive;
   show `research_contract.budget.max_infrastructure_retries` and the
   source-specific controller-timeout qualification or limitation recorded in
   STATUS, without silently changing either
   duration. The one final Start-now decision authorizes the displayed profile
   budget and cost scope; admission alone never authorizes spending.
   For every real profile that admits a file-tool node, compare the admitted
   `max_turns` with the qualified exchange. The legacy schema-v1 single-file
   DRAFT canary requires `max_turns >= 4`: sequential target Read, candidate
   Write and structured completion consumed four Claude Code turns. The
   schema-v2/v3 multi-file Git-worktree design canary requires `max_turns >= 24`:
   the completed bounded design response reported nineteen turns after its
   file-tool exchanges; the qualified bounded profile therefore reserves
   twenty-four. If `max_turns` is below the applicable bound,
   do not alter it or offer another init-time value;
   report one adapter/profile compatibility blocker, withhold `Start now`, and
   tell the operator to edit the prospective local profile and repeat
   admission. Fake demos and profiles with no file-tool node do not inherit
   this check.
8. **User-systemd readiness.** Before the final confirmation, use only
   read-only manager queries to establish that the current user's systemd
   manager is reachable; for example, query
   `systemctl --user show --property=Version --value`. Do not create a probe
   service or scope. Also read
   `loginctl show-user "$(id -u)" --property=Linger --value`. An unavailable
   user manager blocks `Start now` but not `Prepare only`. `Linger=no` does not
   block same-window operation while the login session is active; report it as
   a limitation on unattended continuation after the user's last logout. Never
   enable linger or otherwise change an OS or systemd setting from this skill.
   Resolve a finite Host readiness timeout for the helper; offer 30 seconds and
   accept a replacement only when it is greater than zero and no more than 300
   seconds. Include the exact value in the final review.

A profile/template name must match the CLI's safe-name grammar exactly:
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. It names one immediate child, never a path.
An adopted local profile must resolve to an immediate real directory under the
local root and contain the six required files for its admitted version. A
schema-v3 or schema-v4 template or local profile must contain exactly
these six regular files and no other entry:

```text
goal.yaml
experts.yaml
methodology.yaml
language.yaml
notify.yaml
safety.yaml
```

A retained schema-v1/v2 local profile or explicitly selected legacy template
keeps the old exact layout:

```text
goal.md
experts.yaml
methodology.yaml
language.md
notify.yaml
safety.yaml
```

Reject a mixed layout, both goal/language variants, any extra entry or a
directory in place of a required file. For schema v3/v4 also reject every file
symlink; schema v1/v2 retains its existing admission behavior. Confirm read-only
that all six template files are tracked by Git and, in copy
mode, that the local destination does not exist. The proposed state-root leaf
must not exist in either mode. If the default launch-identity leaf already
exists, do not recapture or silently replace the timestamp inside the current
review. Mark the default unavailable and ask the operator either to provide an
explicit absent replacement or to stop and invoke init again after the minute
changes; never append a sequence suffix or reuse the existing root.
Check that the control interpreter is an
executable regular file, that its SQLite version is at least 3.51.3, and that
it can import `hypoloom`, `yaml`, and `filelock` from the repository
root with `PYTHONDONTWRITEBYTECODE=1`; otherwise stop and point to `SETUP.md`
without creating an environment. Resolve every selected path to an absolute
path and reject a local profile outside `<repo>/profiles/local/` or a
repository-local state path outside `<repo>/state/`.

The local profile must pass
Git's own ignore matcher, including when a copy destination does not exist:

```bash
git -C "$REPOSITORY" check-ignore --no-index -q -- "$PROFILE_DESTINATION"
```

If the chosen state root is inside the repository, require it to be immediately
below `<repo>/state/` with a safe-name leaf and run the same ignore check:

```bash
git -C "$REPOSITORY" check-ignore --no-index -q -- "$STATE_ROOT"
```

If the state root is an absolute path outside this repository, do not run the
repository's ignore matcher against it: that Git worktree cannot track the
path. If a required `git check-ignore --no-index` command fails, stop with zero
writes. Do not edit `.gitignore` from this skill and do not treat `git status`
as a substitute.

Run `python -B -m hypoloom.bootstrap prepare <profile> --repository <repo>
--run-id <reviewed-id>` with exactly one of `--allow-gpu` or `--cpu-only` and the
reviewed literature arguments. For adoption, omit `--template` and `--set`:
the existing local Profile is read-only. For a new destination, select
`--template multi-expert-en` or `--template multi-expert-zh`; apply each reviewed
field with `--set 'section.field=YAML-value'`, for example
`--set 'experts.defaults.model=exact-provider-model'`. This is the existing
six-file Profile syntax, not a second configuration schema. Whole lists may be
replaced with a YAML list; never pass secret literals.

Without `--write` this is a zero-write preview: the helper applies edits in memory
and runs the same admission validator on the **filled candidate**, not on the
unbound template. Inspect `candidate_files`, `admission` and `literature_preflight`.
Use the actual admitted payload for budgets. Retain these exact in-memory inputs
and outputs for the current review. A blocked starter may be prepared for later
editing, but cannot be started. Never treat exit 2 as ready.

Run `bootstrap launch` with the same profile/template/field/run/GPU/literature
choices, the reviewed `--state-root`, `--control-python`, `--request-id`,
`--auth-transport` and selected Host options. This read-only command derives the
exact `profile_check`, `host_start`, `start`, `status` and `host_stop` arrays.
It propagates one literature vector and the admitted invocation budget, including
literal `--agent-max-cost-usd null` when applicable. A declared
inline literature credentials derive the original private `methodology.yaml` as
the Host source. Legacy `literature.credentials_file` remains supported; conflicting
CLI sources are rejected.
Host-only `--gpu-pool-socket`, `--gpu-pool-namespace`, `--gpu-pool-server-uid`
and `--web-search-fallback` never enter client commands; CPU/GPU authorization
never enters `serve`. Host GPU inputs are `--gpu-device-uuid` and
`--gpu-runtime-prefix`. The helper derives `--agent-max-turns`,
`--agent-max-tokens`, `--agent-max-output-bytes`, `--agent-wall-seconds` and
`--agent-max-cost-usd` from admission. There is no token-mode CLI flag:
`token_enforcement_mode` stays in the admitted Profile. Do not assemble those arrays in chat. The helper also checks the observed Host GPU inventory against Profile selectors,
requires its runtime to equal the Profile runtime, and validates pool-specific
budget controls. A busy GPU is a capacity wait, not permission to substitute
another GPU. Pool inventory,
sharing approval, systemd readiness and the final user choice below still apply.

Review `effective_configuration` alongside admission and command arrays. For
managed notifications, use private `notify.yaml`: `sinks.telegram.bot_token` /
`chat_id`, optional `sinks.webhook.url` / `token`, and optional `proxy_url`.
The user fills these locally; keep the file mode 0600. Admission removes values
and derives the source file reference. No separate env or notification credential
flag is needed. Preserve `--notification-output-root` when selected. Old explicit
file/environment inputs remain compatible, including `--notification-proxy-env`
and `--notification-credentials-file`; do not mix sources for one service.
Managed init does not rely on interactive or systemd-manager notification values.
Never copy secrets or put them in argv, unit properties, review JSON or chat.
The helper runs the runtime's local credential,
notification-output isolation, log-path, proxy and loopback-address validators
without sending notifications or binding listeners. Missing credentials or
incompatible settings block launch; occupied fixed ports remain dynamic runtime
availability failures. Provider URL and unsupported transport modes are likewise
checked locally without contacting a provider. Keep the runtime checks active.

Present one final review containing the profile version/mode and its exact six files;
profile, profiles, state, and interpreter paths; run/request IDs and delay; the
captured server-local `YYYYMMDDHHMM` timestamp, canonical launch identity, and
any explicit identity/path replacements; the
full blockers or admitted target, commit, runtime, and read roots; adapter;
Agent/model logical tier and pinned exact Claude selector/modelUsage identity,
auth mode/settings-path-if-applicable and all six profile-derived invocation
budget values, including `token_enforcement_mode`, when real;
the remaining profile research budgets; and
the CPU/GPU decision with exact UUIDs/count cap, Profile workload floor/reserve
and optional initial workload estimate with its pre-measurement threshold,
estimate availability versus timestamped capacity, and shared pool
socket/namespace/peer UID, participation and wait/blocker status, plus the
exact pool plan (pool root, socket, derived unit, member line, reserve MiB)
when pool creation is approved; Profile-enabled notification
recipients/environment and dashboard host/port; retention choice; user-systemd readiness and linger
limitation; and the exact bootstrap writes that each action would permit. For
Profile v4, notification authority is
`research_contract.methodology.controls.product.notify`, compiled from
`notify.yaml`; legacy `research_contract.observer.notify.channels` is not the
active sink selection. Display the enabled sinks and their actual event lists.
For interactive Grounding, explain that a pending question pauses the whole
run in `WAITING_HUMAN` until an explicit answer, with waiting time included in
the run wall-clock budget. This wait makes no model calls, but it does not pause
the run clock. Telegram delivers questions only if enabled and subscribed to
`grounding_question`; `grounding_answered` reports the response. Telegram is
outbound-only, not an answer channel. Missing question delivery requires an
explicit alternative monitoring plan or a prospective profile edit, not an
assumption that the operator will be notified. For
schema v2/v3/v4 also show the reviewed OpenAlex key path, complete literature
argument vector, selected PDF fallback/contact-file path, and the separate
Host-only web-search fallback choice. Explicit academic search uses configured
OpenAlex even when general-web fallback is off. Render the complete literal
`serve` argv in this review, including the exact model selector (which overrides
settings model defaults). Reuse that same vector for the managed launch; do not
reconstruct it later from a shortened example or drop a previously chosen option.
Only paths and non-secret options belong here, never credential/contact contents.
State the full-CC boundary exactly: `agent-full-cc/1` exposes
`Read/Write/Edit/Glob/Grep/Bash`, local tests and environment exploration inside
bwrap. Design/execution may edit the Host-owned chain worktree; analysis is
read-only. Inner nodes fork SYNTHESIS then resume their persistent chain
session; Host commits and archives at node closure. Execution Agents submit,
wait, inspect logs, cancel and list only through their scoped Host MCP endpoint.
ACI18 DESIGN and EXECUTION additionally expose read-only `get_resources`; DESIGN
never gains submit/cancel tools. New native expert plugins include
`resource-planning`; typed estimates on explicitly pooled runs and scoped measured
history feed later admission, while SELF_EVOLVE updates strategy only for a later
cycle. Unknown own use is not replaced with device used memory.
Host still validates/admits candidates, grants resources and executes scientific
Processes. Provider credentials remain outside the sandbox. WP-1 adds native
per-expert Git plugins, actual `CYCLE_REPORT` memory files and complete read-only
role views; requests do not expose the whole run. Unrestricted General Agent Web,
ambient marketplace installation and nested Agents are not permitted by the current capability policy.
Grounding actively uses Host web tools and
source-bound structured background, while existing literature pre-scan remains.
ACI 14 adds multi-chain ADJUDICATE, SELF_EVOLVE, persistent manager, fresh Observer,
Leader and three-round Debate/Judge with original-session fork/winner promotion.
Read the default ACI and adapter identities for new writes from the actual selected
Hypoloom runtime bundle and display them in the final review; do not invent an
init/CLI version switch or migrate an existing run. Use STATUS for actual native
slice and whole-candidate qualification.
Each full-CC request read-only mounts only the admitted run's `runtime_prefix`
and `read_roots`, at their original absolute paths. Bash PATH prefers that
Profile runtime; system Python serves bridge/MCP. This is not GPU authorization
or permission for arbitrary long experiments. Old Python/trial-driver contracts
remain; C5 development additionally admits expert execution argv, parser files
and preregistered comparison rules through Host. Current WP-1/C5 CPU slice
evidence is not G4/G5 qualification or an all-green combined regression. Consult
STATUS; never use historical provider/GPU success to authorize the current bytes.

An existing target repo is the versioned code base; ignored/untracked caches
and splits require explicit `safety.allow_read_paths`. Show those original
authorized paths in the final review. Formal C5/GOAL Processes import candidate
code only through `HYPOLOOM_TARGET_ROOT`. Host maps target-overlapping data
roots to read-only `/hypoloom-read-roots/<one-based-original-root-ordinal>`
aliases, merging contained roots under their outer authorized root.
`HYPOLOOM_READ_ROOTS` lists actual minimal sandbox roots; when aliases are
needed, `HYPOLOOM_READ_ROOT_MAP` maps original paths to sandbox paths,
including contained roots. Use that JSON map for data, never to import candidate
code from the original repository. Agent tools keep admitted original paths;
request execution `read_root_map` is only an advisory preview of formal Process
paths, not extra authorization. Existing production-driver data bindings remain.

Use one final `AskUserQuestion` as the sole authorization for both the reviewed
bootstrap writes and the selected lifecycle action. When every readiness item
passed and the state-root leaf is absent, offer exactly:

- **Start now (Recommended)** — perform only the reviewed bootstrap writes,
  start and supervise the Host automatically, create the one reviewed fresh
  run, and return its status in this interaction.
- **Prepare only** — perform only the reviewed bootstrap writes, create no
  state root/unit/run, and leave execution for later.

Do not label `Start now` recommended when readiness is incomplete. In that
case, show the exact blockers and allow only preparation or cancellation; do
not turn a blocked choice into partial automatic execution. Rejection or
cancellation stops with zero bootstrap writes and no unit, state, provider, or
run effect.

### Confirmed bootstrap writes

After final confirmation, recheck all no-overwrite preconditions. Adoption
mode makes no profile write. For a new Profile, run the exact reviewed
`bootstrap prepare` command with `--write`. The helper creates the new directory
exclusively and writes the six individually named files at mode 0600; it never
overwrites or merges an existing destination. Never use a recursive or wildcard copy.
Its emitted candidate and admitted payload must match the reviewed preview.
If writing fails, report the partial result and stop; never overwrite to repair it.
Public source templates and existing local Profiles stay unchanged.

Only when project settings was absent and the user separately opted in may the
skill copy `.claude/settings.example.json` to `.claude/settings.json`. Use an
exclusive/no-clobber create under `umask 077`, set the completed file to mode
`0600`, and fail if the destination appeared; never alter pre-existing
settings.

```bash
(
  umask 077
  set -o noclobber
  cat -- "$REPOSITORY/.claude/settings.example.json" \
    > "$REPOSITORY/.claude/settings.json"
) && chmod 0600 -- "$REPOSITORY/.claude/settings.json"
```

Do not create `.env`, a launcher, manifest, config, marker, receipt, digest, or
any other convenience file. Do not add a bootstrap sidecar. The only bootstrap file content
init may create is the reviewed six-file profile destination and, after its
separate opt-in, the one
interactive project settings copy. `Prepare only` stops after these writes.
`Start now` may proceed to managed lifecycle effects only as described next.
In particular, init must never generate a scratch Python or shell launcher,
preflight helper, summary helper, or diagnostic script under the tracked
`.claude/skills/autoresearch/` tree. Reuse the reviewed tracked helpers and run
the remaining bounded checks directly; do not turn an interaction transcript
into repository-local executable files.

### Start now — managed in this interaction

Automatic mode is only for a fresh run: immediately before starting the Host,
recheck that the exact state-root leaf is still absent and that all copy
destinations and user-systemd readiness remain valid. Repeat the reviewed
read-only pool status check when participating; endpoint/peer failure stops
before creating a Host/root. Never attach this flow to
an existing state root or infer an existing run. Existing roots and runs remain
read-only unless a later operation receives its own exact authorization.

There is no migration mode. The helper rereads and admits the actual local
Profile before any Host launch, including freshly written v4 Profiles.

Run `bootstrap launch` once without `--start` against the prepared local Profile
(omit `--template` and `--set`). Compare its admitted `create_run_payload` and
command arrays with the approved preview. A mismatch requires a new review,
never silently substitute defaults. When an approved new pool is needed, first
use `manage_gpu_pool.py ensure` as reviewed and retain its exact binding;
a refusal blocks launch, without a nonpooled fallback.

Then run those same bootstrap arguments with `--start`. This flag is the explicit
execution choice, available only after the user's Start-now authorization.
The helper performs the existing lifecycle in order: read-only `profile-check`,
optional pool status, `manage_host.py` start, repeated admission and payload
comparison, one `hypoloom.cli start` with the stable request ID, then `status`.
A failed pre-create check stops only the newly created managed Host. No state
root or log is deleted. A managed launch failure remains a failure; never fall
back to an ad-hoc `systemd-run`, shell background process or foreground Host.

On ambiguous creation, the helper queries STATUS immediately. If the run exists,
it does not retry or stop it. Only a conclusive `not_found` allows cleanup;
otherwise it reports `creation_uncertain`, preserves the unit, and requires
operator reconciliation. Never invent another request ID or automatically rerun
`--start`; an existing state root is rejected. Before creation, a failed initial
check opens no Host. After creation, a failed status read does not undo the run.
Every helper and CLI subprocess uses the selected repository as its working
directory, including when init was invoked from a different directory.

`--auth-transport environment` uses the managed scope with the inherited key,
never in argv, unit properties, JSON, logs, or chat. `--auth-transport settings`
(and fake `none`) uses the transient user systemd service without forwarding an
ambient API key. The helper keeps secrets out of argv,
reports and unit properties. Existing managed readiness cleanup,
`RestartPreventExitStatus=2 78`, finite 30-second window and burst of three remain;
do not replace that interval with `infinity`. No additional launch config or
receipt is persisted. `Prepare only` never passes `--start`.

Once creation is confirmed, fetch `status` for the exact run and report the
Host unit/supervisor, run ID, revision, lifecycle state, and any resource waits
inline in this same Claude interaction. Confirm the returned supervisor is
`user-systemd` and the unit is a `.service` for none/settings or a `.scope` for
environment auth. Inspect the actual non-secret `serve` arguments and compare
the existing STATUS `gpu_pool` binding and stored `grounding_web`/`literature`
configuration with the reviewed choices. Socket readiness alone does not prove
pool participation. Report any discrepancy without claiming readiness or
automatically stopping an already-created run; never dump the unit environment.
Do not print a multi-terminal handoff
or require the TUI. If mentioning the optional later Textual view, describe the
actual `/autoresearch monitor <run-id>` behavior precisely: it opens a new
window when this Claude Code session is already inside tmux, and otherwise
returns one complete command for an external interactive terminal. It never
embeds Textual in Claude Code's captured command output. While that TUI is
mounted it also owns one loopback-only Web observer; `w` or the footer's
`Web / local access` entry shows the token URL and workstation SSH-tunnel command.
The top `Help` button separately explains the windows and execution reliability.
The URL-copy button sends only an explicit, unacknowledged clipboard request;
under tmux the TUI uses a one-shot passthrough without changing global tmux
settings. Describe the toast as request dispatch, tell the user to paste to
verify, and retain client-owned `Shift`-selection as the workstation fallback.
If mentioning tmux copy-mode, say that it guarantees only a tmux paste buffer;
reaching the client clipboard still requires pasting and using client-owned
selection/Copy.

The managed Host must remain online when its fresh root contains no run. After
at least one run exists, `--exit-when-terminal` makes it exit normally only
when every run in that root is terminal and no in-memory or durable Attempt is
unfinished. Exit status 0 does not trigger `Restart=on-failure`, and
`--collect` lets systemd remove the transient service. This lifecycle applies
only to the fresh root authorized by the current Start-now choice; it is not
authorization to enumerate, stop, restart, or otherwise mutate pre-existing
terminal runs or units.

### Prepare only — compact fallback

After the approved bootstrap writes, do not call `manage_host.py`, create the
state root, start a unit, run `manage_gpu_pool.py ensure`, submit `start`, or
contact a provider. Print no old
Host/check/start/status/monitor command bundle. If project settings was newly
copied or the user wants a resumable fallback, print at most one compact,
fully resolved and shell-quoted Claude relaunch command containing
`HYPOLOOM_RUNTIME_FAMILY=hypoloom`, `HYPOLOOM_HOOK_PYTHON=`,
`HYPOLOOM_PYTHON=`, `HYPOLOOM_PROFILES_ROOT=`, and
`HYPOLOOM_STATE_ROOT=`, but never an API-key assignment. Its only
purpose is to reload the newly copied project settings and resume
`/autoresearch init` later; it is not required by `Start now` and it must not
include a Host or run command.

## Supported routing

| Subcommand | Meaning |
| --- | --- |
| `init` | Qualify one fresh launch, then either prepare only or automatically supervise and start it after one final authorization |
| `profile-check <profile> --run-id <id>` | Read and compile the six-file profile without contacting the Host |
| `start <profile> --run-id <id>` | Repeat admission, then submit its exact `CREATE_RUN` payload |
| `status <run-id>` | Request the current projection from the resident Host |
| `pause <run-id> [--expert <role>] [--component <id>] [--chain <id>]` | Explicitly authorized participant pause through the resident Host; admitted work may finish |
| `resume <run-id> [--expert <role>] [--component <id>] [--chain <id>]` | Remove only the operator pause reason; never start/resume a stopped Host implicitly |
| `inject <run-id> --expert <role> "text"` | Submit one operator note for that expert's next eligible node |
| `provider-retry-clear <run-id>` | Confirm credential recovery after a provider 401/403; clears the dispatch cooldown immediately, never edits attempts or resumes a stopped Host |
| `plugin-pin/revert/unpin --run-id <id> --expert <role> [--owner-kind grounding] [--commit <full-sha>]` | Explicit Host control for future-cycle plugin selection; pin/revert lock until unpin, existing requests stay frozen |
| `session-archive --run-id <id> [--member <path>] [--max-bytes <n>]` | Read-only standard-tar inventory or bounded base64 member; never trigger archive or restore |
| `session-restore --run-id <id>` | Explicit local write restoring terminal hot session trees; never resume run or overwrite changed context |
| `graph <run-id> [--export <new-file.html\|new-file.json>]` | Read an offline graph or write a new, private, non-authoritative export outside state-root |
| `monitor <run-id> [--json] [--interval <seconds>] [--web-port <port>] [--no-web]` | Emit one unchanged JSON snapshot, or open the actual Textual TUI plus its lifecycle-bound loopback Web observer in an external PTY |
| `notify --state-root <absolute-root> --run-id <id> --output-root <disjoint-root> [--once] [--poll-interval <seconds>]` | Explicitly authorized real delivery to admitted log/Telegram/webhook sinks; source run stays read-only, progress lives only in the separate output root |
| `debug inspect --state-root <absolute-root> --run-id <id> [--attempt-id <id>] [--include-payloads]` | Derive one metadata-only module diagnosis unless exact payload disclosure is explicit |
| `debug export --state-root <absolute-root> --run-id <id> --attempt-id <id> --output <new-private-file>` | Create one exact private mode-0600 fixture outside the source root |
| `debug validate (--fixture <file> \| --state-root <absolute-root> --run-id <id> --attempt-id <id>) (--source-exact \| --candidate)` | Run pure production parsers without contacting the Host or starting effects |
| `debug rehearse-chain --source-state-root <absolute-root> --source-run-id <id> --through-attempt-id <id> <profile> --profiles-root <absolute-root> --state-root <absent-absolute-root> --run-id <new-id> --allow-agent --allow-process [reviewed GPU options]` | Rebind one eligible successful, validated Agent output once in a fresh local/offline non-authoritative Host run |
| `oracle <run-id>` | Run the independent, read-only closure oracle |
| `cancel <run-id> --expected-revision <n>` | Persist one exact cancellation request through the Host |
| `backup --backup-root <dir> --backup-id <id>` | Ask the Host for one synchronous quiesced backup |
| `restore --backup-path <dir> --state-root <new-dir>` | Restore offline into a nonexistent fresh state root |

There is no separate `drive` command. `serve` is the sole resident Workflow
Host and owns automatic advancement. Do not create a second scheduler,
background it with `nohup`/`&`, or invent a PID/heartbeat file. `init`'s
authorized `Start now` path delegates one fresh Host to `manage_host.py` and
user systemd with `--exit-when-terminal`. Outside that path, the user or a
standard service supervisor must own a directly launched foreground Host
explicitly. Direct `serve` omits `--exit-when-terminal` by default and remains
resident:

```bash
HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli serve \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --agent-adapter fake
```

The fake adapter is for deterministic bounded demonstrations. A real Claude
Host additionally requires operator-supplied values for
`--claude-executable` and a pinned non-alias `--claude-model` selector with
`--agent-adapter claude`, plus either inherited environment auth or the optional
existing-settings compatibility path; never invent those values or silently
use the fake adapter for a requested real run. GPU service configuration is
separate from a run's `--allow-gpu` authorization.

ACI schema 11 is the WP-0 full-CC foundation, not a pin for current new writes;
exact adapter/evaluator/lifecycle identities and qualification are recorded only
in `docs/qualification.md`. Profile schema v1
still selects the legacy three-command vocabulary; ACI schema 6 is its
retained-read wire boundary. Profile v2/v3 `expert-six-node/1` uses Host-owned
persistent chain sessions/worktrees. Pre-ACI-11 zero-tool, single-file and
Attempt-private execution rules apply only to their frozen predecessor versions.
See `docs/architecture.md` for the current
capability/authority boundary. Existing runs remain read-only.

For full-CC expert loops, admitted `max_active_agents`, `max_active_processes`
and `max_active_reviews` bound the three capacity classes independently;
`max_active_hypotheses`/role count is only their missing-field compatibility fallback. A waiting execution Agent may
coexist with its own Process; reviewer roles remain serialized. Legacy paths
retain the root-global total-three bound. The Agent-side experiment MCP is not
the separate read-only operator MCP used by this skill.

For schema v3, `language.yaml` owns only BCP47-style natural-language tags:
one `default` and optional overrides for these exact Agent-backed routes.
Admission projects the default to the existing
`research_contract.goal.language` and the overrides to the existing typed
`research_contract.methodology.controls.language_routes.overrides`. Never add
a second default or encode a magic prefix in goal language:

```text
grounding
literature_review.query_plan
literature_review.main
hypothesis_proposal.forward
hypothesis_proposal.backward
hypothesis_proposal.synthesis
experiment_design_implement
experiment_execution
result_analysis
hypothesis_update
```

Do not accept `observer`, `report`, `code`, `query` or
arbitrary stage names. The Host resolves the exact route only when that typed
v3 control exists, using the existing goal language as default, and gives the
Agent its `route` and `output_language`; the adapter applies that language
to natural-language candidate fields while preserving schema literals,
identifiers, code, paths and verbatim citations. Legacy `language.md` prose is
not parsed into this route map.

Every schema-v1
DRAFT gets a fresh request-bound snapshot whose canonical
`busy_gpu_uuids` records same-root OPEN Process occupancy. `AUTO` considers
only non-busy capacity-qualified devices; `GPU_REQUIRED` prefers non-busy
devices but, when every authorized UUID is busy, freezes the best UUID and
waits in `READY`. ACI 11 uses full CC with explicit settings and scoped MCP;
it does not use `--safe-mode` or `--bare`, which disable required capabilities.
It loads no ambient settings/hooks/plugins. The old safe-mode, tool-less proposal
and single-file DRAFT behavior belongs only to pre-ACI-11 frozen adapters. For
schema-v2/v3 Grounding, the Host first renders exactly one packaged
`openalex-query-planning/6` instruction into the role-neutral
`GROUNDING_QUERY_PLAN` request with the frozen goal and bounded repository/data
context. The provider may change only `query` and optional
`preferred_venues`; a missing literature stratum closes that Attempt as
`invalid_candidate`, and the next bounded Attempt receives the exact validation
diagnostic so it can broaden the query. Only one successful admitted plan is
retrieved and bound by exact query ref/bytes to the following role-neutral
Grounding request. After Grounding succeeds, each expert independently receives
the same packaged instruction in its own `LITERATURE_REVIEW/QUERY_PLAN`, together
with the frozen goal, Grounding result, role `focus`, component and current evidence. The
legacy v2 role's `experts.yaml.focus`, or the deterministic v3 projection of
`roles[].background` plus `roles[].reasoning_principles`, is the sole
background/responsibility source; there is no venue-ranking file. The provider returns
only `query` and optional bounded `preferred_venues`; the Host materializes the
complete `LiteratureQuery/v2` from its frozen template, then validates the safe Boolean subset and all frozen
retrieval-policy fields, then combines preferred and broad OpenAlex lanes. The
planning instruction requires discriminative field-specific concept groups rather
than a flat bag of generic terms. An unresolved public landing/source hostname is
metadata-only: the candidate remains eligible for another-host PDF and otherwise
becomes `PDF_UNAVAILABLE`; private/non-global addresses still fail closed. A bad
PDF is isolated to that paper; after bounded OpenAlex OA locations, an exact-DOI
Semantic Scholar `openAccessPdf` lookup may supply one final URI through the same
download and Poppler checks. A 429 suppresses remaining DOI fallback lookups
and starts a 60-second cooldown shared by later batches in the same Host;
direct OA retrieval remains eligible. Skipped lookups are not new HTTP 429s.
The pre-ACI-14 `MAIN` is packet-only under
`fixed-literature-io/2`. ACI 14 literature and Debate additionally use the frozen
Profile's Host web-tool subset and source-bound output; disabled/empty tools
must not be mounted. These consumers never receive the API key or ambient network.
Bash `safety.bash.network_allowed` separately defaults false; true admits only
public HTTP:80/CONNECT:443 via the bounded per-Attempt proxy, not private/loopback
access, arbitrary socket networking, TLS interception or provider credentials. The old zero-plugin
description applies only to frozen pre-WP-1 requests; current expert nodes may
load their explicitly bound native role plugin, not ambient plugins. Query validation failure
may use the existing bounded Agent retry with its exact diagnostic; a Host
`QUERY_INVALID`/HTTP 400 does not repeat the same admitted plan. Existing Agent
Attempt timing includes the Host retrieval interval and records
`literature_retrieval_seconds` for every successful or failed retrieval.
Role-neutral means no expert persona in the prompt. The existing ACI/budget
ledger still requires a declared role/component accounting key, so both shared
prelude calls remain charged to the Run and the scheduler-selected role; this
does not grant that role Grounding ownership or scientific authority.
At `HYPOTHESIS_UPDATE`, decide only from the current component's current-tip
assessment and local evidence. Outside `evaluation: grounding`, a latest `SUPPORTED` assessment must terminate
that lineage; downstream or whole-goal incompleteness is never a reason to keep
an upstream component open. In Grounding common-acceptance mode local support
does not require termination or prove goal success; continue useful local work
or submit a complete GOAL experiment against the accepted common evaluator.
For
`DRAFT_EXPERIMENT_SPEC`, the Host renders one packaged, role-parameterized
`experiment-resource-planning/5` compatibility guidance (not a callable native
Skill; use the request-bound plugin's advertised entrypoint) and one bounded
`resource-experience/1` summary as advisory inputs. For that component's bounded
preflight `ANSWER_RENDEZVOUS`, the Host performs allow-listed deterministic
context projection/dedup over the frozen DRAFT inputs. Keep the scientific goal,
current component/protocol/execution/result/delivery contract, typed candidate,
exactly one plaintext `experiment.py`, compile observation, hypotheses and
required resource context; omit duplicate full catalogs, response metadata and
embedded workspace. This is not a compression Agent or compression skill, does
not call another provider and creates no new authority. Bind projected bytes
through the original `context_refs` and request digest, and never expose the
packet to unrelated Agent calls. Resource inputs grant no device or process authority.
Retained nonpooled contracts keep fixed grants and compatibility history rules.
Cross-run source Attempts on that compatibility path
are eligible only when the entire
`ResearchContract.document` and every `RunVersion` field exactly match the
current run; role, component or method-family equality alone is insufficient.
Current pooled DESIGN/EXECUTION receive the native `resource-planning` Skill,
fresh scoped history and the read-only live `get_resources` MCP tool. Initial
inputs stay frozen historical bytes. CYCLE_REPORT and SELF_EVOLVE also receive
scoped measured history, but no resource-query or experiment mutation tools.
ACI18 EXECUTION may submit a typed `resource_estimate` (flat string workload map,
positive per-device `peak_memory_mib`, evidence `basis`, optional exact
`measurement_attempt_ids`). The Host resolves same-run/role/component/method-family
and exact-workload measurements at first acceptance only, freezes those sources,
and uses the maximum of Profile floor, estimate and measured/OOM lower bound with
headroom for real admission. An optional Profile `initial_vram_per_experiment`
also remains binding until a comparable CLOSED Process has SUCCEEDED with
complete attributed samples. The next submission may then decrease its estimate
without changing the Profile minimum or device reserve. Missing estimates use
the initial value (or floor when absent) and stay unestimated; partial samples
stay unknown. Never use another run's private research or device deltas as own use.
Only SELF_EVOLVE's writable native plugin can update stable resource strategy;
the accepted Git revision is selected for a later cycle, not an already bound
request. Preserve the original four skills and resource-planning when present.
No skill update changes the Profile, reserve, allowlist or current allocation.
Pure documentation/report/checklist work represented by a non-production
artifact component is CPU-only: `CPU`/`AUTO` freeze CPU, while `GPU_REQUIRED`
is returned to the same Agent as a bounded resource correction before
preregistration or Process intent. Production artifacts and metrics retain the
normal GPU selection path.
A hypothesis proposal must pair `parent_hypothesis_id` with `relation`; only a
same-component hypothesis may be its parent. Current schema-v2/v3 profiles should
make retry admission explicit: `budget.max_retries` counts model/candidate redrafts,
`budget.max_infrastructure_retries` independently counts Agent
transport/workspace/Host failures, and `budget.max_process_lost_retries` counts
only recovery-proven Process `LOST` retries. Each value is the retries after the
first Attempt. A legacy profile that omits one of these fields uses the
bundle-frozen total-Attempt fallback for that class (model 2, infrastructure 3,
Process `LOST` 2); that fallback is not an additional-retry count. An
`invalid_candidate` failure supplies its latest bounded
diagnostic to the next model retry. Current Profile v4 can set
`methodology.yaml.output_correction.max_failed_validations: 3`, allowing native
CC to correct two invalid final JSON objects inside one invocation; a valid third
object can pass, while a third failed validation returns to the Host. Omission preserves the old default one;
the current template explicitly selects three. This admitted request setting
does not expand invocation budgets, change outer `budget.max_retries`, or allow
ambient settings to override it. Provider transport retries remain zero.
Current full CC emits one complete JSON object as ordinary final text, not a
native StructuredOutput tool call. The Host Stop hook validates the same full
schema supplied in the prompt and returns precise correction feedback within
that invocation. At the cap, allow normal native completion to drain accounting;
unchanged Host postflight still rejects the invalid object. Exit zero is not
acceptance. Missing/failed/session-mismatched hook validation fails closed.
Keep each rejected validation round observable even after correction succeeds, and
request a complete corrected object rather than only changed fields. Never
merge partial candidates, relax acceptance, classify two format failures as
proof of backend/model incapability, or label unknown failed charges as zero.
The complete native terminal result is the sole reported usage/cost source;
count each failed Attempt's explicit fee once. Raw tokens and Stop diagnostics
cannot price a missing bill; unknown charges retain reservations, which are not
actual spend or an independently verified upstream invoice.
Use [Architecture](../../../docs/architecture.md) and `docs/qualification.md`
for the current boundaries and source-specific validation status. This output path is
not permission to start, resume or mutate an existing run.

For current output diagnosis, compare the full launch/prompt schema, actual
`last_assistant_message` and validation error in private `native-output/*.json`,
and `stdout.log`'s terminal result event `result` text. Check exchange/capture
completeness before comparing matching provider SSE text blocks; exclude
thinking, tool arguments and other rounds. Follow existing private-data
redaction and explicit-export rules. `structured_output_correction` uses
`scope=native_stop_validation`, actual round ordinals/errors/counts/status, not
fabricated tool IDs. Retained legacy StructuredOutput evidence still uses only
actual matched failed tool calls/results; never apply that tool-trace inference
to ordinary final text.

Use the existing `Attempt + EmbeddedInput` correction path. A retryable
MODEL/output failure opens a new Attempt for the same stable Command/logical
node and introduces one advisory input from that immediately preceding failed
Attempt's bounded code, stage and safe diagnostic. An INFRASTRUCTURE retry
opens a new Attempt, creates no new correction, and exactly replays the prior
request inputs; it therefore preserves any unresolved correction already in
that request. A literature-retrieval retry reuses the same pre-retrieval inputs
and invokes retrieval again. A later MODEL failure replaces, rather than
accumulates, the earlier correction. Design repair may receive the direct prior source, validation
observation, optional patch and preflight reply; a canonical
`EXECUTION_FAILURE` successor may receive the direct predecessor spec,
outcome, evaluation, Process observation, bounded stdout/stderr, source, patch
and review replies. Never accumulate older lineage. Correction refs are
excluded from `immutable_context_refs`; never present them as `supporting_refs`
or scientific evidence. Do not create a retry schema, fact, ledger, receipt or
gate. Rejected bytes remain non-authoritative Attempt evidence.
Any final-JSON predecessor candidate retains the existing 64-KiB limit and is
omitted wholesale if credential-shaped; no malformed-object reconstruction or
full stdout/transcript/thinking is added to correction inputs. This is separate
from the existing private diagnostic archive, not a new acceptance source.

For `RESULT_ANALYSIS`, use only the Host-projected exact current Experiment:
preregistration, canonical outcome, terminal evaluator candidate, selected
Process observation, source `experiment.py`, published `result.json` and
bounded stdout/stderr tails. Component, role, cycle, experiment ordinal,
execution, source Attempt and publication mismatch must fail closed. These are
operational inputs, not authority to rewrite the deterministic outcome or
assessment.

Current evaluator `component-result/7` retains predecessor `/6` and treats only
the exactly reconstructed candidate-authored unified-diff paired-header/text-
hunk shape error as `EXECUTION_FAILURE` eligible for the existing
same-hypothesis linear successor. Unsafe path/decode, apply-check,
identity/implementation binding, publication and other integrity failures stay
`INTEGRITY_REJECTED`.

At `HYPOTHESIS_UPDATE`, `TERMINATE_LINEAGE` only ends the exact local lineage;
it does not itself release a dependent. Policy still requires the ordinary
current `SUPPORTED` assessment or the applicable required-input
outcome/artifact/family conditions. A current `SUPPORTED` tip must terminate;
`INCONCLUSIVE`/`REFUTED` keep the local choice. Do not turn operational
diagnostics into scientific evidence or describe `REVISE_NEXT_CYCLE` as a
guaranteed budget/lifecycle admission.
A stable packaged-skill revision requires human review and applies only to
newly frozen runs.

For resource-wait interpretation, distinguish the durable boundary precisely.
A failed capacity check before intent leaves the Experiment `READY` and opens
no PROCESS Attempt. If intent and the Attempt were already committed, failure
of the final pre-spawn capacity/identity check closes that real Attempt
`FAILED` with `synthetic_before_spawn=true`; do not describe it as a no-Attempt
wait.

## 1. Profile check — always before a start

Use the prospective run ID and the intended delay and GPU authorization. The
component graph and experiment bound come from the admitted profile. The check performs no allocation, provider call, socket
request, or run write:

```bash
RUN_RESOURCE_ARGS=(--cpu-only)   # Only for an explicitly CPU-only run.
# RUN_RESOURCE_ARGS=(--allow-gpu) # Use the reviewed GPU authorization instead.

HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli profile-check "$PROFILE" \
  --profiles-root "$HYPOLOOM_PROFILES_ROOT" \
  --run-id "$RUN_ID" \
  --process-delay "$PROCESS_DELAY" \
  "${RUN_RESOURCE_ARGS[@]}"
```

Select exactly one of `--allow-gpu` or `--cpu-only` from the explicit review.
Reuse this same argument array for both admission checks and run creation.
Missing or conflicting choices exit 2 before admission and before any writes.
Exit 0 returns an admitted report and the exact prospective `CREATE_RUN`
payload. Admission blockers exit 2 with `create_run_payload: null`.
Do not edit the profile, drop blockers, or hand-build a smaller payload to make
it pass.

The public `multi-expert-en` / `multi-expert-zh` templates are bounded configuration
starters, not scientific evidence or preconfigured real user profiles. Real topics are copied and reviewed
under the ignored `profiles/local/` tree, and commands use that directory as
their profiles root. A real start still requires exact target/data/model/cost/
GPU review. Any local profile that fails admission remains a hard stop and
never falls back to legacy.

## 2. Start — exact admitted payload only

Confirm that the intended Host is already running at the explicit state root.
Choose one request ID and reuse it for an exact transport retry:

```bash
HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli start "$PROFILE" \
  --profiles-root "$HYPOLOOM_PROFILES_ROOT" \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --run-id "$RUN_ID" \
  --request-id "$REQUEST_ID" \
  --process-delay "$PROCESS_DELAY" \
  "${RUN_RESOURCE_ARGS[@]}"
```

Use the same required `--allow-gpu` or `--cpu-only` choice as profile-check. `start` repeats
admission and submits only the adapter's exact payload. A blocker or absent
Host is a hard stop; never spawn an implicit Host or translate the request to
legacy.

## 3. Status, monitor, and oracle

Host status is the authority for current revision and Host-only facts such as
backup quiescence. Its `resource_waits` list gives the online reason why a
configured GPU experiment remains in `READY`:

```bash
HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli status \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --run-id "$RUN_ID"
```

The monitor and oracle are read-only views. They do not prove Host liveness and
must not repair state. Both monitor routes consume the same existing observer
snapshot. Its durable projection can display minimum/latest free VRAM, latest
utilization and pressure flag only after those values exist in a terminal
PROCESS observation. One compatible resident-Host STATUS response may enrich
that exact poll with current `resource_waits`; without it, Host-only fields
remain unknown. Neither case proves continuous liveness. A natural terminal
that wins a pressure-cancellation race remains natural with
`pressure_exceeded=false` and no `resource_pressure`; the Oracle rejects
inconsistent pressure/terminal combinations:

```bash
HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli monitor \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --run-id "$RUN_ID" --json

HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli oracle \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --run-id "$RUN_ID"
```

For `/autoresearch monitor <run-id> --json`, invoke the existing CLI command
above directly and relay its one snapshot unchanged. Do not parse it into a
Markdown table, summarize it, or pass it through another renderer. An optional
positive finite `--interval` has no effect on this one-shot JSON result. JSON
mode never binds a Web port; `--web-port` and `--no-web` do not change that.

Without `--json`, do **not** invoke the Textual CLI in Claude Code's captured
subprocess or try to reproduce its panels in chat. The slash invocation itself
authorizes starting only this read-only observer; it authorizes no run write or
lifecycle action. Resolve `INTERVAL` to `2.0` when the flag is omitted or to
the exact user-supplied positive finite value. Accept `WEB_PORT` only as an
integer from 0 through 65535; omission uses 0, asking the OS for a free
ephemeral port. Preserve an exact `--no-web` choice. Then route the exact run
to the bounded launcher:

The supported TUI-only controls are `--web-port <port>` and `--no-web`.

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" \
  "$REPOSITORY/.claude/skills/autoresearch/scripts/launch_monitor.py" \
  --python "$HYPOLOOM_PYTHON" \
  --working-directory "$REPOSITORY" \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --run-id "$RUN_ID" \
  --interval "$INTERVAL"
```

Append `--web-port "$WEB_PORT"` only when the user supplied it, and append
`--no-web` only when the user supplied that flag. Do not invent either flag.

When the current Claude Code process has a nonempty `TMUX`, the launcher puts
the existing `hypoloom.cli monitor` command in a new tmux window with a
real PTY. Before opening that window it uses the same control Python to verify
the TUI import and one existing read-only `monitor --json` snapshot; a failed
preflight opens no window and points the user to `--json`. It has no renderer or
snapshot builder of its own. The user can quit with `q`; the command then exits
and the window closes naturally. The same TUI process binds its Web observer
only to `127.0.0.1`, retains the ephemeral bearer token only in memory, and
stops the server when the TUI unmounts. `w` or the footer's `Web / local access` entry
shows the actual token URL, the exact remote port and
`ssh -N -L <port>:127.0.0.1:<port> <user>@<server>` workstation template. A
clicked URL-copy action uses a one-shot tmux passthrough without changing
server-wide tmux settings and cannot confirm workstation delivery; the user
must paste to verify, with client-owned `Shift`-selection as fallback. tmux
copy-mode guarantees only the tmux buffer; describe the extra paste plus
client-selection step when workstation clipboard delivery matters. A
port collision degrades only Web: the TUI stays usable and its Web access screen
shows the error and `--web-port 0` recovery. `--no-web` starts no
listener. When `TMUX` is absent, the launcher starts nothing and prints exactly
one complete, shell-quoted command; return that command to the user for an
external interactive terminal and do not execute it inside Claude Code.
If Textual is unavailable, report the existing optional installation hint and
offer the direct `--json` path; never replace it with a legacy monitor.

The same Web observer also exposes authenticated `/api/graph`, `/api/experts`,
`/api/experiments`, `/api/events` and aggregate `/api/activity`. Its Experiments
& logs view shows actual experiment state/events and bounded stdout/stderr,
including explicitly unavailable logs. Log HTTP and WebSocket routes are scoped
to an existing experiment in the selected run. Browser WebSocket authentication
uses offered subprotocols, not a URL query token. These are read-only product
observations, not Leader decisions, scheduling authority or run controls.

## 3a. Participant controls and graph

The fidelity-first candidate adds `pause`, `resume` and `inject` to the existing
Host socket. These are state-changing operations: require the exact run and
scope from the user, never infer them from a monitor or stale active pointer.
`resume` here removes an operator pause; it does not restart a Host, migrate a
retained run, reopen terminal state or clear a Leader/Debate pause reason.
This development slice's exact Host/recovery verification is tracked in STATUS;
the presence of these verbs alone is not a qualification claim.

For `/autoresearch pause|resume <run-id>`, route the same verb to
`hypoloom.cli` with explicit `--state-root` and `--run-id`. Forward
optional `--expert`, `--component` and `--chain` without inventing a participant.
A component requires an expert; a chain also requires a component. Obtain a
fresh Host status and forward its run revision as `--expected-revision`.
Keep one request ID across a transport retry; a revision conflict requires
fresh status and user confirmation of the changed target state, not automatic
rewriting of the request. Do not fall back to cancellation or HumanDecision.

For `/autoresearch inject <run-id> --expert <role> "text"`, forward the exact
text as one argv item, not shell code, to CLI `inject`. The note is additional
operator context for a future node, not a scientific fact or a way to overwrite
an OPEN Attempt's frozen input. Do not redact or reinterpret the user's meaning.

`/autoresearch graph <run-id>` is read-only and needs no Host. Optional `--export`
writes only a previously absent `.html` or `.json` file outside state-root,
without overwriting an existing export. Relay the result and path. The HTML is
self-contained: no external scripts or model/provider access.

## 3a. Plugin selection and terminal archives

Use literal exact root/run/owner and current Host revision for plugin controls.
Require full Git commit for pin/revert; unpin may select an explicit commit or
the latest accepted evolution candidate when omitted. For Grounding require
`--owner-kind grounding --expert grounding`. These commands write via the Host:
get explicit authorization, preserve stable request IDs on ambiguous retry and
never edit Git refs or SQLite directly. Pin and revert remain locked until unpin;
revert makes a child commit with the selected tree, preserving history. SELF
candidates remain on independent Git branches while pinned. Selection changes
only future cycles, never an OPEN request or its infrastructure retry.
Provider errors do not authorize automatic rollback or discard.

Default memory retention is retain. Explicit archive_on_close means Run terminal,
not closure of an Attempt, chain or round that a later Debate/fork may need.
Host publishes standard tar before removing fixed hot session/worktree trees;
canonical transcript and plugin/memory Git remain. Do not clean old terminal
runs when inspecting or starting another authorized Host lifetime.
`session-archive` is read-only, with bounded member output. `session-restore`
requires separate exact write authorization and a terminal run with no OPEN
Attempt; it preserves the tar, rejects changed context, does not write scientific
state and never resumes Host or run. Prepared CLI examples are in USAGE.md.

## 3b. Notifications — explicit external effect

For `/autoresearch notify`, require literal exact `--state-root`, `--run-id` and
`--output-root`; do not infer a recipient, run or output directory. Read the
admitted Profile-v4 `controls.product.notify` settings without changing them.
Explain that log/Telegram/webhook sinks are independent and that `--once` still
sends real selected messages. A request to inspect or monitor does not authorize
delivery; never start an explicit notifier from status, monitor or Oracle.
An authorized Start-now/serve may automatically own Profile-enabled services;
review sink recipients, secret environment names and dashboard host/port as part
of that launch. Prepare-only starts no service.

Automatic serve services are ordinary lifecycle-owned readers, not a second
Host: start only after the sole lock/initialize/socket, use the configured
loopback dashboard port (0 or omitted means an OS-selected free port) and
ephemeral Bearer. Explicit fixed ports fail visibly on conflict and retry the
same port. Startup logs and owned notifications use the actual bound port;
notifications never include the Bearer. Without a known dynamic port, direct
the operator to local access information rather than inventing a URL.
Delivery defaults to the disjoint sibling
`<state-root-name>-notifications`; review any `--notification-output-root`
override. Exclude runs already terminal before this Host lifetime; newly created
or previously active runs retain final polling/dashboard until serve exits.
Restart rotates token, preserves cursor and does not duplicate a listener.
An explicit notifier can deliberately catch up a terminal run; do not reset
cursor as automatic repair. FINAL_REPORT still follows accepted verdict, so do
not call goal_verdict_recorded the run's completion event.

After the user authorizes the exact delivery scope, forward to the existing CLI
with `HYPOLOOM_RUNTIME_FAMILY=hypoloom` and the qualified Python:

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli notify \
  --state-root "$STATE_ROOT" --run-id "$RUN_ID" \
  --output-root "$NOTIFICATION_OUTPUT_ROOT" --once
```

Preserve the user's actual `--once`/`--poll-interval` choices; the example is not
an implicit default or permission to send. For continuous delivery, return the
exact foreground command for an operator-owned terminal rather than hiding a
long-running process in captured Bash. Use the same output root after restart.
Stopping only the notifier does not stop or modify the run.

Secrets are environment values, not profile literals or command arguments. Do
not ask the user to paste them into chat, print them, source an unreviewed `.env`,
or inject them into Agent settings. The output root must be disjoint from the
entire state root. Per-sink `cursors/<sink>.json` stores ordinary source/revision
progress after success; failures remain visible and retryable. At-least-once
delivery may duplicate after remote success before cursor persistence. Never
delete/reset cursors as an automatic repair, add a delivery ledger, or invent
events without an actual accepted source. Leader/Debate/manager events now come
from accepted interaction/evolution controls; run_finished only from actual RUN
terminal, not the earlier goal verdict. Missing event selections do not send.
Relay `delivered`, `errors`, `warnings` and the CLI exit status honestly. See
`docs/guides/notifications.md` for the current configuration. Loopback tests do not qualify real Telegram
or public webhook delivery.

## Retained Hypoloom module debug

`/autoresearch debug` is a developer surface over explicitly named Hypoloom
records. For inspect/export/validate, require a literal absolute `--state-root`
and exact run ID; never infer either from an active pointer, latest timestamp,
environment-only default, monitor result, service name, or legacy root. Those
three actions must not contact or start a Host, call `status`, or run any
lifecycle helper.

For inspect, preserve the user's exact optional Attempt and disclosure choice:

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli debug inspect \
  --state-root "$EXACT_ABSOLUTE_STATE_ROOT" \
  --run-id "$RUN_ID"
```

Append `--attempt-id "$ATTEMPT_ID"` only when supplied. Append
`--include-payloads` only when the user explicitly writes that flag; it places
exact private research inputs in Claude Code's captured output and session.
Default inspect is metadata-only. Do not automatically select the reported
first or unrecovered Attempt for a later operation.

Export is the only debug route that writes a file, and the invocation
authorizes only the exact named output:

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli debug export \
  --state-root "$EXACT_ABSOLUTE_STATE_ROOT" \
  --run-id "$RUN_ID" \
  --attempt-id "$ATTEMPT_ID" \
  --output "$EXACT_NEW_PRIVATE_FILE"
```

Require an absolute output whose parent already exists, whose final target is
absent, and whose resolved location is outside the source state root. Never
rename it, add a suffix, overwrite it, copy SQLite/WAL/SHM, or read and rewrite
a rejected fixture as a purported sanitized version. The CLI creates a regular
mode-0600 file with exclusive create and refuses detected credential-shaped
content. A refusal is terminal for that invocation.

Validate in exactly one of the two mutually exclusive input forms:

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli debug validate \
  --fixture "$EXACT_PRIVATE_FIXTURE" --candidate

PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli debug validate \
  --state-root "$EXACT_ABSOLUTE_STATE_ROOT" \
  --run-id "$RUN_ID" \
  --attempt-id "$ATTEMPT_ID" --source-exact
```

The first form accepts only `--fixture`; the second requires the exact
state/run/Attempt triple and no fixture. Require exactly one of
`--source-exact` or `--candidate`. Never retry a failed source-exact validation
as candidate automatically: source-exact may truthfully return
`NOT_RECONSTRUCTABLE` when the complete source launch configuration/runtime
files are unavailable, while candidate is an explicit counterfactual and
cannot relabel the source run.

For inspect, export, and validate, relay stdout as one verbatim JSON block and
report stderr plus the real exit status separately. Do not parse, summarize,
translate, reorder, reserialize, or turn the result into a Markdown table. In
particular, validation failure may intentionally produce stdout JSON and exit
2; preserve both. If Claude Code capture truncates output, state that it was
truncated and stop. Never invent a complete JSON result; prefer exact export
for a large private payload.

These routes acquire no writer capability and do not change the source main
database, Commands, Attempts, facts, artifacts, or run state. Ordinary SQLite
access to a live WAL database may maintain WAL/SHM sidecars, so do not claim
directory-wide byte-for-byte or mtime invariance.

`/autoresearch debug rehearse-chain` is the currently implemented public
effectful debug action. Its retained schema-v2 handler evidence does not by
itself qualify a schema-v3 or adapter-`/29` candidate; use the current
`docs/qualification.md` evidence before a promoted run. Execute
it only after the user explicitly authorizes the exact source root/run/Attempt,
local profile, new run ID, previously absent absolute destination root, Agent
and process effects, and (when requested) exact GPU UUID/runtime. Never choose
or infer any of those values. The destination parent must already exist; source
and destination roots must be disjoint. The current bounded handler accepts
only a successful ACI schema 10/profile-schema-v2-or-v3 cycle-one expert
`QUERY_PLAN` for the bounded pilot metric fixture, or a cycle-one,
experiment-ordinal-1 `EXPERIMENT_DESIGN_IMPLEMENT` Agent Attempt whose exact
rebound produces a non-`SUPPORTED` local assessment that legitimately permits
revision. A first-cycle `SUPPORTED` result terminates locally and cannot be
forced into a two-cycle rehearsal.
It re-runs the current request/response parser and ACI postflight before it
atomically claims the destination. The local profile must admit and bind the
same component/role:

```bash
PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli debug rehearse-chain \
  --source-state-root "$EXACT_ABSOLUTE_SOURCE_ROOT" \
  --source-run-id "$SOURCE_RUN_ID" \
  --through-attempt-id "$SOURCE_ATTEMPT_ID" \
  "$LOCAL_PROFILE" \
  --profiles-root "$EXACT_PROFILES_ROOT" \
  --state-root "$EXACT_ABSENT_ABSOLUTE_DESTINATION_ROOT" \
  --run-id "$NEW_RUN_ID" \
  --process-delay 0 \
  --wall-seconds 3600 \
  --allow-agent --allow-process
```

For an explicitly authorized GPU rehearsal append all three forms: `--allow-gpu`,
one or more exact `--gpu-device-uuid "$GPU_UUID"`, and
`--gpu-runtime-prefix "$EXACT_GPU_RUNTIME"`. Do not add a GPU selector merely
because the profile permits GPU. Do not add Claude executable/settings/model,
OpenAlex credentials, or network arguments: this handler uses an explicitly
non-authoritative local deterministic Agent plus offline literature while the
ordinary Host/Runner/Artifact/Outcome/Kernel/Oracle path owns new effects. A
GPU rehearsal is real subprocess/CUDA framework evidence, not real-provider or
scientific-continuation evidence.

Relay successful stdout as one verbatim JSON block. On failure, report stderr
and the real exit status; the fresh root is retained for inspection. A success
must report `non_authoritative=true`, a fresh `COMPLETED` Host run,
`source_output_rebound_once=true`, deterministic first-cycle revise followed by
second-cycle termination, clean Oracle, no residue and `closure.complete=true`.
Source authority rows and canonical artifacts remain unchanged; do not claim
the whole source directory or WAL/SHM mtimes are byte-invariant. Never
retry automatically with a different root, Attempt, profile, GPU or effect
scope. Never contact the source Host, run lifecycle helpers against the source,
resume/import its history, or substitute arbitrary shell replay. Unsupported
nodes fail closed and require a separately implemented and qualified handler.

## 4. Cancel

Cancellation is a state change. Require the exact run ID and explicit user
authorization. Read a fresh Host `status`, take its `result.run_revision`, then
submit:

```bash
HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli cancel \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --run-id "$RUN_ID" \
  --expected-revision "$RUN_REVISION" \
  --cause operator_request
```

Do not derive the revision from a stale file or offline monitor. On a revision
conflict, fetch status again and ask the user whether the changed run should
still be cancelled; do not force the request.

## 5. Backup and restore

Backup is Host-mediated and covers the explicit Hypoloom state root. The Host
checks quiescence; a refusal is a stopped operation, not a reason to copy live
files manually:

```bash
HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli backup \
  --state-root "$HYPOLOOM_STATE_ROOT" \
  --backup-root "$BACKUP_ROOT" \
  --backup-id "$BACKUP_ID"
```

Restore is offline and destructive if aimed at the wrong place. Require the
exact published backup path and an explicitly authorized target that does not
exist. Ensure no Host owns the target, then run:

```bash
HYPOLOOM_RUNTIME_FAMILY=hypoloom \
"$HYPOLOOM_PYTHON" -m hypoloom.cli restore \
  --backup-path "$BACKUP_ROOT/$BACKUP_ID" \
  --state-root "$NEW_HYPOLOOM_STATE_ROOT"
```

Restore audits every copied run before publishing the new root. Never restore
over an existing root, repoint a legacy run, or create an extra marker, digest,
receipt, or gate. Start a Host for the restored root only as a later explicit
operation.

Backups and support bundles do not contain the Host-private settings. For a
separately authorized fresh-root restore, the operator may provide the original
private bytes with `restore --claude-settings /absolute/original-settings.json`.
This is a secret input, not an export or permission to resume; subsequent Host
version verification rejects incorrect bytes before recovery. Missing original
credentials must not be replaced with current interactive settings or a rewritten
runtime hash. Without them the research backup remains usable read-only.

## Iron rules

- Existing runs are read-only unless the user authorizes the exact state
  change; inspection does not authorize start, cancel, backup, or restore.
- Use only the existing Host command, profile admission report, state store,
  monitor/debug projections, production parsers, oracle, and backup/restore
  implementation.
- Report command exit status and JSON honestly. Never reinterpret a blocked,
  cancelled, or nonterminal state as scientific success.
- Do not equate local profile admission with real-topic scientific
  qualification; preserve its exact target, data, model, cost and GPU review.
