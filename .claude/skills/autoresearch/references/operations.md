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
Temporary catalog failures reuse the original query and recover automatically:
30/60/120/240/300-second backoff, then 15-minute checks from the sixth failed
wave, respecting a longer Retry-After. Authentication failures require repairing
the selected credential source. Do not restart init or repeatedly submit manual
retry controls for ordinary cooldown. Deferred query-coverage validation shares
the run's catalog wait and keeps its original model response; it still occupies
the ordinary task slot. Stop and wallclock limits prevent further reads.
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
`experiment-resource-planning/6` compatibility guidance (not a callable native
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
without changing the Profile minimum or reserve policy. With a positive
`gpu_vram_reserve_percent`, the Host computes device reserve from the final
admitted workload, at least 512 MiB and the configured absolute floor, rounded
up to 64 MiB. New templates use 5%; omitted/zero retains a fixed reserve. Actual
OOM growth raises the comparable workload and proportional reserve; protection
cancellation, external pressure and script errors are not OOM. Keep same-run
FIFO and use existing wait/handoff rather than resubmitting to jump the queue.
Missing estimates use
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
Use [Architecture](../../../../docs/architecture.md) and `docs/qualification.md`
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

`/autoresearch-monitor` is the slash-menu shortcut. `/autoresearch monitor`
uses the same [focused entry](../../autoresearch-monitor/SKILL.md). Missing
root/ID choices are reconstructed by `hypoloom.monitor_launch --inspect` from
existing project state; do not infer a root from a directory name, a stale
environment variable or an active-run pointer. Multiple matches require selection.
This read-only discovery does not establish Host liveness or authorize recovery.

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

### GPU infrastructure recovery (ACI 28)

A Host-observed pre-submit pool fault blocks only its source research node and
technically closes the current Agent with normal archive/accounting. Never
respond by submitting CPU work or `gpus: 0`, fabricating an experiment or clearing
API cooldown. Diagnose the bound service, then use TUI `g`, Web **Recheck GPU
resources**, or `hypoloom resource-recheck --state-root <root> --run-id <run>`.
This does not restart/reconfigure the service and retains existing budgets.
Managed init replaces a project pool only through a reviewed quiescent maintenance action; inspect
run dependencies and actual service/process ownership before separately
reviewing any replacement. See `docs/guides/runtime-recovery.md`.
