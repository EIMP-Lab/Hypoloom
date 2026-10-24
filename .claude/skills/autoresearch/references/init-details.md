# Initialization reference

Consult this reference for new Profile authoring, compatibility or a specific
blocker. The main SKILL uses aggregate inspection; do not execute every example
or repeat checks already reported by bootstrap. Code validators own admission.

## Interaction budget

For normal existing Profiles, aim for one aggregate inspection, at most one
combined missing-choice question, one final review and one launch call. The
performance target is at most six non-question tool calls/eight model responses,
not permission to omit checks or necessary user decisions.

## Pool dependency handling

After device selection, inspect asks about a differing pool reserve: keep the
current pool or review reconfiguration. Preserve the returned full pool membership;
this run's device subset does not shrink the pool. Follow the generated questions
and answer argv, rather than inventing a rebuild command. If missing-state owners
are eligible for retirement review, ask the exact displayed identities. Never
infer retirement from RELEASED requests or answer on the operator's behalf.
`--pool-retired-owner namespace/run_id` records the operator's assertion for this
maintenance only: the run was retired/deleted, not relocated, and will not resume.
It does not delete history or authorize other owners. `none` defers and stays
blocked; remove that deferred choice only when the operator elects to reconsider.
Final Start now covers the displayed action, configuration and retirement scope.
Inspect/answers/Prepare only never perform maintenance. Do not delete a pool
database, clear requests or start another pool on the same devices to evade a block.

For retained nonterminal runs, use the generated `pool_cancel_run` question when
available. `--pool-cancel-run RUN_ID:REVISION` selects exact cancellations for the
final review; it is not a retirement assertion. Start now uses each run's original
frozen Host, commits CANCEL before recovery, and permits only closure plus its
original budgeted final report. Include this possible API work in final review.
No Host/process may still own the run, and all its pool requests must be released.
For supported legacy bundles without normal cancellation recovery, inspect offers
the separate `pool_retire_run` question. `--pool-retire-run RUN_ID:REVISION` reviews
offline closure with the original frozen lifecycle, retaining history and making
no provider/GPU calls. An unavailable final report is recorded as failure; the
old run may end FAILED. Disclose this in final review, and never infer this choice
from ordinary cancellation or missing-state retirement authorization. Unsupported
bundles remain blocked; never patch an old bundle, hand-edit SQLite or start an
ordinary Host to obtain a socket.
`cancellation.prepare_after_cleanup` is an executable plan, not proof of closure.
After Start now, bootstrap waits up to five minutes for cleanup and automatically
continues pool preparation and the same reviewed new-run identity. If it returns
`dependency_cleanup_pending`, preserve the exact selected revisions and recheck
with `interaction.inspect_argv`; do not repeatedly launch recovery or recommend
nonpooled merely because cleanup is still pending. A changed selection requires
review of the affected item. Already accepted cancellations retain their identity.
When cleanup finishes and the remaining actions stay within the previous Start
now authorization, continue that launch without asking for the same approval again.

## Detailed configuration boundaries

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

Use `bootstrap inspect` from the main skill first. For additional credential-path
diagnostics only, use `python -B -m hypoloom.bootstrap paths --repository <repo>`;
add `--profile <name>` when selected. Show `profile_credentials` paths and
permissions and the local Profile/template directories, never values. Literature
and notification credentials are filled directly in these private YAML files;
no separate env is required. Public templates contain blank fields. The helper's
`credentials`/`credentials_template` entries describe legacy dotenv locations
only. Model authentication remains the selected existing environment/settings.

Show `inspect.checks.version` (or the paths helper's `version_check`) once in the user's language, including
the comparison remote/branch, local/remote revisions and working-tree status.
`remote_differs` means a different remote tip with unverified ordering; do not
call it proven behind. `unavailable`/`offline` mean unknown freshness, not latest.
Forward `--offline` when the user requests offline init. This is a bounded,
advisory check: it never blocks the launch decision, prompts for Git login or
updates code. Do not repeat it after selecting the Profile, poll it in the Host,
run fetch/pull automatically or migrate existing runs. To inspect paths again,
use `--offline` and retain the first check's result in the review. See the
[version reminder guide](../../../../docs/guides/version-check.md).

For institutional access, use the main skill's missing-choice flow. If inspect
returns `institutional_browser_mode` in `missing`, show the descriptions in
`choices.institutional_browser_mode` and collect off/local/client/server/existing.
Do not infer off from silence, choose a mode from the host OS or replace a supplied
legacy URL. Repeat inspect with the explicit answer and the same launch identity;
do not hand-build launch arguments to bypass an unanswered choice. Off leaves
public/OA retrieval available and disables institutional-panel operations. Preview starts no browser; standalone
preparation and final run startup retain their separate explicit choices.
When access is enabled, inspect requires a separate `institutional_prepare_provider`
answer and supplies `choices.institutional_prepare_provider` from the existing catalog.
Ask interactively, explicitly displaying IEEE, ACM, ScienceDirect and Springer, with
multiple/custom selections and skip. Recommended IEEE/ScienceDirect/Springer are only
suggestions; ACM's public-first recommendation does not authorize omitting it from the
question. Browser placement, Profile adoption and old-run defaults are not list answers.
Carry an already explicit answer without asking again; use repeated
`--institutional-prepare-provider` arguments, or `none` to skip. No executable preparation,
launch or session command is returned until this missing choice is resolved.
`bootstrap session` also rejects an omitted list. Do not reconstruct a direct launch
command to bypass the question. Standalone preparation/direct-launch defaults remain
compatible; they do not answer an interactive init question.
Use the existing single library URL and browser profile for the batch.
The program carries selected platforms as `auto` unless an explicit `public_only` or
`disabled` mode overrides them. All other publishers default to disabled institutional
access, even though the catalog lists them. Public/OA retrieval remains independent.
Use generated provider modes and argv unchanged; do not enable the rest of the catalog.
When the user explicitly prioritizes unattended stability, carry
`--institutional-prepare-failure-mode public_only` through inspection and launch.
Known failed/challenge-dependent platforms can use the existing repeated
`--institutional-provider-mode PROVIDER=public_only` from the outset; retain the
user's platform choices instead of imposing a global publisher blacklist.
This policy converts failed/unvisited preparation platforms to public-only, including
connection failure or interrupted preparation. Completed/skipped platform choices
are retained. Review is still the default for users who have not chosen this policy.
For Windows client init, prefer the returned `init_session_argv`: one background
`bootstrap session` uses the same preparation socket, merges accepted publisher
modes, rechecks the original admitted Profile and renders the complete final review
before starting its 600-second answer clock. At `awaiting_launch`, relay the live
instructions and immediately ask the offered fixed choice. Send Start now or Prepare
only via the existing `reply_argv --text`. Start now is consumed once and calls the
existing managed launch; watch `bootstrap_result`, not a second launch invocation.
Do not rerun inspect, build a notification-reading script, manually merge policies
or rewrite the program's review. These operations are already complete. Notification
review includes actual event subscriptions and private source paths; do not extract
recipient/token values. A blocked review offers no Start now. Expiry or a rejected
reply requires a fresh explicit decision; never reuse the old review as approval.
An accepted Start now changes to `launching`: bounded existing launch phases retain
the connection until actual Host CREATE handoff or launch failure. The helper's
300-second lost-contact bound still applies. No status read renews a review.
The separate preparation/direct-launch guidance below applies when the generated
session command is absent, or when deliberately diagnosing standalone preparation.

Standalone preparation checks supplied samples first and returns current results and
platform modes. Merge modes by platform, re-inspect, and retain the same run identity.
Never turn not_checked, an opened page or a previous sample into permanent login proof.
Ask for one optional interactive library entry only if needed and not already supplied;
never substitute SAML metadata. Without one, use the catalog's publisher entry pages.
Use the complete returned preparation command without appending a second platform list.
Optional sample DOI/URLs check the current session before a manual prompt; all samples
readable means no login prompt. The command is absent when preparation is skipped.
Show the final preparation_summary without claiming that public access proves login.
The generated command uses `--agent-session`. Run it once in the background and
read the first flushed JSON record for `state_root`, `status_argv`, `instructions_argv`, `watch_argv`.
The control root is temporary preparation state, never the proposed research root.
Query `status_argv` when checking progress; do not repeatedly start preparation.
`checking_connection` has the configured connection timeout (default 30 seconds).
Run `instructions_argv` and relay its output, including the Windows file location,
fixed Setup entry, input values and reply choices. Do not hand-write client commands,
translate/reword the fixed replies, infer an SSH alias or calculate another deadline.
`status_argv` returns the live stage, `remaining_seconds` and complete `reply_argv`.
Connection checks are owned by the preparation program. After an unsuccessful
initial check it automatically retries at a bounded cadence within the same manual
window, using the existing single browser worker. A late client needs no agent reply
to advance. `继续连接` only expedites a check; it never extends the deadline or stacks
concurrent checks. Render the latest `connection.reason_code` when present; never
infer that the browser opened from a socket, task start or an expired observation.
Immediately after relaying connection instructions, start exactly one `watch_argv`
in the background, including when the client has not attached yet. Do not end the
turn waiting for the user to say "connected" with no observer running: connection
checks can advance and open a platform without a reply. The generated command adds
`--wait-seconds 3600`; it exits early at a user-action stage, awaiting_launch or batch end and prints
current instructions. This is an observation limit, not a new preparation deadline.
On its background completion, read current instructions and offer the current
platform's fixed replies promptly. At `status.state=awaiting_launch`, read `summary`
and enter review immediately; the live background process is only retaining the
connection for handoff. If the process ended, read its final background summary.
After submitting a platform reply, re-arm one background watch for the next stage.
If an observer reaches its limit while preparation is still progressing, relay its
current result and re-arm one observer; never overlap them or repeat the same user
question on every notification. An already waiting_for_user stage needs a user
answer, not another watch. The observer never sends replies. Do not invent a shell
loop, ask for reinstall solely because a check failed, or claim a cached connection
result is still current. A direct watch without the option remains a short 30-second
diagnostic; it is not a substitute for the generated background observer.
After an actual user reply, append `--text` containing that exact offered reply;
for 验证样本 append `--sample` with the supplied DOI/URL. Invalid/stale replies are
rejected by the process. A tool/background completion is never a user reply.
`waiting_for_connection` offers 继续连接 and 跳过准备. Its manual deadline starts
when connection waiting begins, even if no publisher page has opened. Platform
answer waiting has its own deadline; checks, retry replies and polling do not extend
the connection deadline. Expiry concludes preparation with the latest connection
result; a bound init may retain its control root in awaiting_launch for review.
No publisher action has occurred at this point. Do not claim a local helper is
absent solely from waiting_for_client, or promise a browser popup before `opened`.
Execute `watch_argv` unchanged in background mode, with no shorter external timeout.
It prints at a user-action stage, awaiting_launch, batch end or its observation limit; it
is not an event stream. A foreground/tool timeout must not kill it before that
result. If an external timeout does kill it, empty output is not evidence of
unchanged state. Read status/instructions and restore one observer if needed.
`waiting_for_user` supplies the current provider in its generated reply command.
Keep it unchanged. Ask within this init conversation using the offered fixed choices.
If the user has already logged in while connection checks advanced, explain that
they can reply 完成 immediately; do not ask them to log in again. A connection reply
may be stale by then: re-read instructions, never submit the old reply to a new stage
and never translate connection confirmation into platform completion on their behalf.
If its `reason_code` is `verification_required`, relay the generated challenge
instructions. This records detection when the page opened, not a connection failure
or proof that verification passed. Keep the same page and answer deadline; never
solve challenges, refresh/reopen automatically, or claim that reinstalling the
client will resolve a website challenge. The user may skip this platform.
For IEEE, ScienceDirect, Springer and optional ACM, the program supplies fixed
public DOI probes in `completion_samples`. After `完成`, it automatically fetches
and validates that sample through the shared public-PDF path, then the same browser
if no public PDF is available. Do not invent another DOI or
ask the user to find one. Explicit `--sample` or a `验证样本` reply supplies the user's
own article instead. Defaults run after the manual answer, never instead of the
requested login opportunity. `sample_verified` proves only that article's current
readability; `sample_scope: public_full_text` cannot prove institutional login or
subscription. Retain `verification_scope` in the review. Failures remain failures
with their generated retry command; completing the manual step is not test success.
Report the returned `media_type`, `acquisition_source`, `source_url` and `page_count`
when present. A public PDF verifies public acquisition, not browser login; HTML full
text is readable content, not a downloaded PDF. Old results without these fields
do not establish PDF format. Fixed catalog samples stop on a challenge/sign-in page
and proceed to the next platform, retaining failure rather than requesting manual
per-paper downloading. Do not retry those samples automatically or promise that
repeating init clears a website restriction. For a different explicit sample only,
if checking reaches a challenge/sign-in page, the program offers another
`waiting_for_user` with `sample_url` and the remaining original acquisition time.
Relay those instructions: the user handles that sample page and replies 完成, skips,
chooses public-only or ends preparation. Do not call the initial login incomplete.
Completion resumes the same acquisition and tab, without restarting the deadline
or generating another prepare/verify command. Use its current fixed choices; this
stage does not accept a replacement sample. Expiry is still failure, never success.
For other platforms or older sessions without a sample, completion stays
not_checked. Use each live process's generated instructions. Answer waiting
uses the existing manual timeout. Scoped Windows init completion/non-manual timeout enters
`awaiting_launch`: read status's `summary` without waiting for process exit. The same
control root retains the helper for at most 600 seconds, then releases it without
starting research. Explicit finish/skip during preparation releases it immediately;
read that command's final background summary instead. Merge modes even on failure.
On background exit, read the separate `preparation_lifecycle` event: its reason is
`run_handoff`, `launch_review_expired` or `operator_finished`; the sample summary
does not establish controller/helper liveness. Preserve both; this exit observation
never authorizes launch or changes the sample result.
The command's nonzero exit is a preparation result, not a failed research run.
Relay the program's final human-readable preparation results verbatim before
offering retry/continue; the returned summary remains the source of states and commands.
`identity_unconfirmed` concerns the requested paper, never the user's institutional
account identity. It may occur before any PDF fetch. Use `reason_code` to distinguish
`article_identity_unconfirmed` (landing identity), `pdf_target_unconfirmed` (download
target) and `document_validation_failed` (obtained content validation). Older results
without those reasons do not establish which stage failed or that a PDF was fetched.
Do not treat logging in again as a proven repair or public/OA fallback as guaranteed
success. A user's explicit fresh-init acceptance test is different from recovery:
honor their fresh-session scope rather than steering them back to the old batch.
Only a sample_verified result supports a readability claim. acquisition_timeout,
browser_unavailable or any other failure proves neither readability nor login.
For a failed platform, report its intermediate safe `reason_code` from the task
output when available. `browser_unavailable` alone does not establish that Edge,
SSH or the client task exited; page operations can fail with a live connection.
A platform that never opened was not verified for login or subscription.
If `preparation_failure_mode` is `public_only`, name each failure and its returned
public-only mode, merge `provider_modes` and re-inspect without asking for another
retry/accept decision. This was already authorized by the policy; an empty
`remaining_preparation_argv` means the failures have a selected fallback, not success.
Do not regenerate preparation for those platforms. Still stop at final launch review;
the policy does not authorize Start now. Keep the real failed sample states and
nonzero preparation exit visible. Public-only preserves public search/OA retrieval
and disables automatic institutional browser acquisition for that platform.
Otherwise, if any selected platform failed, name the failed platforms and offer `重新准备`
(retry only the failed/unfinished platforms) or `接受未验证状态，继续检查启动配置`
(continue to launch review). Do not interpret timeout as completion or advance to
Start now on the user's behalf. This is an optional preparation choice, not a
research admission blocker; an explicit decision to finish/skip already permits review.
Never put credentials in answers or use a second interactive manager concurrently.
While `awaiting_launch`, the user's fixed 重新准备 executes the summary's generated
`remaining_preparation_argv` unchanged. It invokes `preparation retry` on that same
control root, retaining failed/unfinished platforms, completed results, connection
options, modes, samples and launch identity. Never open another preparation root
or replay successful/manual-complete platforms. An empty list is not proof of login
or success. Once finished/expired, an old retry vector cannot revive the controller;
follow the current generated instructions, never reconstruct a restart from bootstrap.
Do not restart CC or create a run merely to recover a preparation check.
Do not launch custom polling tasks or repeatedly ask the same question on background
notifications; use one program-generated observer and relay the current instructions.
Never recommend 结束准备 merely because the client connected or the user started a
fresh init to test the feature. It cancels the batch and does not mean "connected"
or "continue". Honor an explicit finish answer; restart only on the user's retry.

For Windows client mode, `institutional_client_entry` supplies `script_source`,
`setup_inputs` and a single fixed file-launch command in `powershell`. The user
transfers the original script from that server to Windows through SFTP/SCP and runs
`-Action Setup` in its downloaded directory. Show the source path, launch command and
input values; never reproduce, rewrite or generate the installer body, nor use a
chat-generated script file as the download. Keep installation source on the
file-transfer path, outside model-generated text.
Every init requires the user to execute its exact scoped entry once on the chosen
Windows PC. Preserve `-StateRoot`, `-RunId`, `-RequestId` and supplied
`-PreparationRoot`; these are the existing launch identity and current temporary
control root, never values to invent. Setup accepts them as arguments or asks for
the displayed values, then checks SSH/remote Python, installs the original file at
the stable local path and invokes Connect. No Windows checkout or second CC session
is needed. Never substitute `-Action Init` inside this init. Use the guide's original
file-transfer flow and SSH key/agent steps. Loopback refers to Windows; password
login is not BatchMode readiness. For an old command stuck at `>>`, Ctrl+C first.

Setup/Connect prepares the owned profile's private permissions and starts a hidden
one-shot task without a logon trigger or task restart. It returns, so the terminal
may close; only foreground `-Action Run` diagnostics require keeping it open and
must carry the same scope. A started task/waiting status does not prove connectivity.
The same helper follows preparation into the exact authorized run; it does not scan
all runs or stay available for later init sessions. New Setup checks ownership before
removing a matching old logon/discovery task. Never recommend wildcard task deletion
or adopting an old dedicated browser; close that old window while preserving its
profile. The guide covers migration of the local helper, not frozen research Hosts.

At `awaiting_launch` with `launch_review`, use the fixed session procedure above.
For standalone preparation, show the summary and perform launch review while the controller
retains the connection. **Prepare only or cancel must execute returned `finish_argv`**
to revoke that connection; Start now still requires separate explicit authorization.
If the handoff expires, report lost optional access without inferring failure of
research admission or automatically renewing the authorization. An explicit
finish/skip during preparation also revokes scope; later browser use requires an
explicit entry for the intended run. Never keep an observer waiting for this live
controller to exit before asking the final launch choice.

On STOPPING/termination the helper closes its owned SSH/browser, preserving login
data. Short disconnection only recovers to the same run; 300 seconds of continuous
lost contact causes local exit. After sleep it checks expiry before reconnecting.
Closing CC or interactive SSH does not stop started managed research; preparation
and pre-launch waiting remain bounded. Missing client access uses public/OA fallback
and does not block unrelated work. Old unscoped helpers cannot discover new runs.
A server-only invocation cannot start programs on an unconnected Windows PC. Do not
claim Windows GUI/sleep acceptance from automated tests or server-side readiness.
Merge provider_modes by platform into launch choices (replace, never append conflicting
values), then re-inspect. Results are transient, not saved credentials or authority.
Failures and skipped/unverified platforms do not block research startup. Preserve the
interaction's completed/skipped decision; reinspection alone never repeats preparation.

Use the deterministic helper for prospective Profile validation and launch
assembly. It reuses admission, credentials, model-source inspection and the
existing managed lifecycle. It creates no launch manifest. Models choose and
explain user policy; they must not manually reimplement these checks or invent
command arguments. A passing configuration is not provider availability or a
scientific-quality qualification; preview performs no provider probe.

### Read-only decision and qualification phase

Inspection performs **zero writes**. Explicitly selected institutional preparation
may create its temporary connection/control resources as described above. Otherwise do not create a
directory, copy or chmod a file, create the state root, install a package,
contact a model provider, create a user unit, or start a process that can write
workflow state. No Host, Agent, service, scope, or run exists because of this
flow before that confirmation.
Resolve the repository root read-only (prefer
`git -C "$CLAUDE_PROJECT_DIR" rev-parse --show-toplevel`) and inspect the
proposed paths. Read only minimal existing bindings and ownership when needed
for pool maintenance; never resume, migrate, or modify an existing run in init.
Every Python process launched by init—including dependency checks,
Agent preflight, compute snapshot, and both profile admissions—must set
`PYTHONDONTWRITEBYTECODE=1`; do not leave import caches in the checkout.

Use `AskUserQuestion` for the decisions below. Ask only questions whose answers
were not supplied by the invocation, group closely related choices when the UI
permits, and show concrete detected values so that the user does not have to
reconstruct commands manually. Propose a consolidated default set where safe:
the requested existing local profile, the qualified project control Python, an
absent timestamped state leaf, the same timestamped identity as the run ID,
that identity suffixed with `-create-1` as the request ID, and process delay
zero. After the exact profile safe name is selected, use the single server-local minute supplied by `bootstrap inspect`, equivalent
to `date '+%Y%m%d%H%M'`; accept only exactly 12 ASCII digits. Define the single launch identity as
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
   compatible legacy six-file expert-loop source. New topics should use
   schema v4 and the bilingual `profiles/templates/multi-expert-en` /
   `profiles/templates/multi-expert-zh` templates.
   Review the chosen language and its explicit prompt/report settings.
   Schema v3 requires the exact admitted `mode.research_loop: expert-six-node/1`
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
   Optional `literature.elsevier_api_key` and `literature.elsevier_insttoken` use
   the same private source. The official Elsevier article path is independent of
   the browser. Respect program-selected default preparation: with a key it omits
   ScienceDirect, retaining other platforms. Configuration is not full-text access;
   do not re-add ScienceDirect, request keys in chat, or fall back to CAPTCHA retries
   after API denial. An explicitly requested browser preparation remains possible.
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
   The DOI-only PDF fallback defaults to Semantic Scholar. An optional
   `literature.semantic_scholar_api_key` in the private methodology file (legacy
   dotenv: `SEMANTIC_SCHOLAR_API_KEY`) is read by the Host only; never ask for its
   value in chat or put it in argv. No key is required. Generated preparation and
   retry arguments preserve the existing credential path and selected fallback;
   forward them unchanged. Rate limits and publisher verification leave bounded
   full-text gaps; do not promise that a key or another browser retry resolves them.
   If the operator
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
   `min_vram_per_experiment`, `gpu_vram_reserve_mb` and optional
   `gpu_vram_reserve_percent`. New templates use 512 MiB and 5%; adopting an
   existing Profile preserves its values. A missing/zero percentage means the
   fixed reserve; a positive percentage means the greater of 512 MiB, the fixed
   floor and that percentage of the admitted per-device workload, rounded up to
   64 MiB. GPU use with a percentage requires a positive workload floor.
   Show bootstrap's `gpu.memory_budget` computed starting workload, reserve and
   required free memory; initial workload includes `initial_vram_per_experiment`
   when configured. Later measured/estimated workloads can require more. Show
   the pool's actual reserve too: a reused 4096 MiB pool floor still applies even
   when this Profile requests 5%. Unknown pool reserve stays unknown. Distinguish the hard workload floor
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

   Refresh the device snapshot and confirm this init's UUID subset every time.
   A prior run's selection is only a suggested default; never silently inherit it.

   **Detection.** The aggregate inspection already observes the project pool.
   For a specific historical-binding diagnostic, use this read-only helper;
   do not repeat it as another mandatory init step. Probe before concluding anything about pool
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
   UUID subset to be present and the live capability `resource_replanning/1`.
   The current Host repeats these checks before creating a GPU run.
   An unavailable or incompatible external pool blocks direct launch. A project
   pool with a safe reviewed preparation plan permits Start now; a reachable pool with insufficient headroom is a disclosed
   capacity wait. Never substitute a private allocator or silently remove the
   pool configuration.

   **Pool creation.** When the operator wants pooled GPU work and no live
   project pool or explicitly supplied external pool exists, include the exact
   pool plan in the final launch review: pool root
   `$REPOSITORY/.local/gpu-pools/shared`, socket `control.sock` inside it, the
   derived `hypoloom-gpu-pool@<escaped-pool-root>.service` unit, the single
   `--member <this-uid>=<approved UUID subset>` line, and `--reserve-mib`
   equal to the computed absolute reserve floor (at least 512 MiB and rounded up
   to 64 MiB with a positive percentage; otherwise `gpu_vram_reserve_mb`, zero
   when absent). Use the returned plan when reusing/restoring a pool; do not
   silently replace its larger floor. Creation runs only
   after **Start now** approval, immediately before the managed Host start,
   through bootstrap's generated `pool_prepare` vector to the bounded
   `manage_gpu_pool.py`/`hypoloom.managed_gpu_pool` helper. Do not invoke a second
   service-start command separately from bootstrap.

   Require exit 0 with status `ready`, `reused`, `restored`, `upgraded` or `reconfigured`; verify the returned socket
   and required capability against the review. Served devices may be a superset
   of the reviewed per-run UUID subset. Reuse that socket as `$GPU_POOL_SOCKET`.
   Any refusal (`foreign_pool_root`, `member_uid_mismatch`, `service_busy`,
   `pool_conflict`, `pool_ready_timeout`, `pool_verification_failed`) blocks
   this launch attempt and is reported verbatim. Keep the current init open
   for recheck or revised choices; never fall back to nonpooled
   silently. The helper deploys only beneath this repository's
   `.local/gpu-pools/`, only for the invoking UID, never against a conflicting service or a service with unresolved dependencies, and never forwards an ambient API key. A stale socket
   from a dead pool is cleared by the pool service itself on start. Do not run
   `gpu-pool-serve` directly, install a service by hand, change
   socket/directory permissions or groups, or enable linger as part of init.
   Project pool replacement and member reconfiguration are reviewed init actions,
   executed by bootstrap with dependency and concurrency checks. External pools
   are not automatically taken over. Different private pools must not coordinate the same
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
   authentication or model availability. Do not infer that one authentication
   route or provider works because a different route succeeded previously.
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
Use the inspected notification `proxy` field and the program's fixed Telegram
route notice: a running Clash/browser proxy does not imply that notifications
use it. When the user has selected a notification proxy, ensure its explicit
private Profile or CLI binding is present before starting the session. Preserve
direct routing when no proxy was selected; never guess a local port. Runtime
retry does not reload a missing or changed proxy binding.
Never copy secrets or put them in argv, unit properties, review JSON or chat.
The helper runs the runtime's local credential,
notification-output isolation, log-path, proxy and loopback-address validators
without sending notifications or binding listeners. Missing credentials or
incompatible settings block launch; occupied fixed ports remain dynamic runtime
availability failures. Provider URL and unsupported transport modes are likewise
checked locally without contacting a provider. Keep the runtime checks active.
Enabled notifications must be reviewed as configuration-valid but delivery-unverified.
Do not describe credential preflight as a successful Telegram test. After creation,
`hypoloom notify --state-root ROOT --run-id RUN_ID --test` checks access without
sending; `--send-test` sends one explicitly requested message. A failed optional
notification check is visible and does not silently cancel research launch.

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
recipient source paths/environment names (not their contents) and dashboard host/port; retention choice; user-systemd readiness and linger
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
OpenAlex even when general-web fallback is off. Review the generated
`launch_argv` and effective configuration, including the exact model selector
(which overrides settings model defaults). Bootstrap derives the complete
`serve` argv internally; do not reconstruct it in chat or drop a chosen option.
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
Processes. Provider credentials remain outside the sandbox. Native expert requests use
per-expert Git plugins, actual `CYCLE_REPORT` memory files and complete read-only
role views; requests do not expose the whole run. Unrestricted General Agent Web,
ambient marketplace installation and nested Agents are not permitted by the current capability policy.
Grounding actively uses Host web tools and
source-bound structured background, while existing literature pre-scan remains.
ACI 14 adds multi-chain ADJUDICATE, SELF_EVOLVE, persistent manager, fresh Observer,
Leader and three-round Debate/Judge with original-session fork/winner promotion.
Read the default ACI and adapter identities for new writes from the actual selected
Hypoloom runtime bundle and display them in the final review; do not invent an
init/CLI version switch or migrate an existing run. Use STATUS for the actual
runtime identity, admitted limits and observed execution state.
Each full-CC request read-only mounts only the admitted run's `runtime_prefix`
and `read_roots`, at their original absolute paths. Bash PATH prefers that
Profile runtime; system Python serves bridge/MCP. This is not GPU authorization
or permission for arbitrary long experiments. Old Python/trial-driver contracts
remain; current execution admits expert execution argv, parser files and
preregistered comparison rules through Host. Consult STATUS; never use historical
provider/GPU success to authorize the current bytes or an unreviewed experiment.

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

Use one final `AskUserQuestion` as described in the main skill for authorization of both the reviewed
bootstrap writes and the selected lifecycle action. When every readiness item
passed and the state-root leaf is absent, offer exactly:

- **Start now (Recommended)** — perform only the reviewed bootstrap writes,
  start and supervise the Host automatically, create the one reviewed fresh
  run, and return its status in this interaction.
- **Prepare only** — perform only the reviewed bootstrap writes, create no
  state root/unit/run, and leave execution for later.

A repairable project pool permits Start now with its concrete preparation action.
For other blockers retain this init and offer recheck, revised choices, preparation
or cancellation; do not execute unreviewed partial changes. Rejection or
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
destinations and user-systemd readiness remain valid. Execute the reviewed pool preparation and repeat live verification before
creating a Host/root; a preparation failure returns to this init. Never attach this flow to
an existing state root or infer an existing run. Existing roots and runs remain
read-only unless a later operation receives its own exact authorization.

There is no migration mode. The helper rereads and admits the actual local
Profile before any Host launch, including freshly written v4 Profiles.

Run `bootstrap launch` once without `--start` against the prepared local Profile
(omit `--template` and `--set`). Compare its admitted `create_run_payload` and
command arrays with the approved preview. A mismatch requires a new review,
never silently substitute defaults. For managed pools include `--managed-pool`, the exact `--pool-action` and
reviewed full membership/reserve in bootstrap arguments. Bootstrap prepares the
pool before the Host; retain its exact binding, without a nonpooled fallback.

Then run those same bootstrap arguments with `--start`. This flag is the explicit
execution choice, available only after the user's Start-now authorization.
The helper performs the existing lifecycle in order: read-only `profile-check`,
reviewed pool preparation and status, `manage_host.py` start, repeated admission and payload
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
Do not print a multi-terminal handoff for starting research.
Always include bootstrap's `monitor.cc_command` and complete
`monitor.terminal_command` after confirmed creation. They are optional
read-only observer entry points, not a multi-terminal research-start handoff.
Do not require or automatically open the TUI. Describe the
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

If preparation is awaiting_launch, execute its returned `finish_argv` to release
the helper authorization before returning. Do the same on cancellation; this is
cleanup of explicitly selected preparation, not authorization to stop a run.

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

## Connection lifecycle

Enabled `--institutional-browser-mode client|local|existing|server` selects run-owned connections.
For Windows client mode, show the exact program-generated Setup/Connect entry for this init.
Transfer the original script through SFTP/SCP; never reproduce its body. Preserve `-StateRoot`,
`-RunId`, `-RequestId` and supplied `-PreparationRoot`. The user executes it once on the chosen PC,
then returns here. No logon trigger, task restart or future-run discovery; no terminal must stay open
except foreground Run diagnostics. Never substitute Init inside an existing init. Local execution
and SSH authentication remain prerequisites; waiting_for_client does not prove an absent helper.
The helper follows only this preparation/run, closes its own SSH/browser at STOPPING/termination,
and preserves login data. Continuous lost contact for 300 seconds ends it locally; wake checks expiry
before reconnecting. A new init needs new authorization; old tasks/browsers cannot adopt another run.
`local` uses the Linux user's visible browser over a pipe; check X11/Wayland. `existing` wraps
an explicit external endpoint; `server` uses the installed image with private per-run relays.
Preview exposes policy, read-only `institutional_connection_argv` and a separate preparation command;
it starts no resources. Host allocates ports after creation and releases owned connections on exit.
Closing CC/interactive SSH or monitors does not stop managed research; missing client access uses
bounded acquisition/OA fallback. Same-version recovery preserves frozen configuration; no run migration.
Do not share active local/server profiles, install dependencies, pull images or replace external browsers silently.
Connectivity proves neither visible GUI nor subscription. Legacy endpoint/manual commands remain
operator-owned; do not use detached manual startup for a new managed run. See details and the institutional guide.


## Grounding and shared acceptance

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
