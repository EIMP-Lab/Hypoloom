# Qualification: G0–G5

English · [简体中文](qualification.zh-CN.md)

Qualification belongs to an exact candidate and environment. Hypoloom retains
the existing G0–G5 promotion model, runtime oracles and regression fixtures.
The [test selector](../scripts/qualify.py) runs ordinary pytest selections; its
exit code is a test result, not an aggregate release decision. A skipped lane,
missing retained cohort or unfinished cleanup cannot be recorded as passed.

## Current development — prompt offload, termination honesty, tick cost, and attribution-aware GPU pressure, 2026-09-19

Candidate: uncommitted working tree on dev @ f2de65a carrying eight reviewed
repair directives plus their audit follow-ups (44+ files; adapter
`claude-code-bwrap/62`, compute `local-compute/6`, runner
`local-process-group/3`; all changes apply to new runs only through the
existing version pinning). Origin: read-only analysis of the terminated run
`MERC-causal-202609172306` (its own evidence is untouched), which found four
defect classes — monotonically growing prompts killing agent tasks (the
OBSERVER interaction trigger died every cycle; zero DEBATE/LEADER tasks in
the whole run), a TERMINATE_LINEAGE teach-vs-enforce semantic gap that
permanently collapsed three-way expert parallelism to one, a GPU-pressure
kill that discarded 4.2 h of completed scan work, and control-channel
starvation that made the operator's (durably accepted) End Run look failed.
Two local audit records drove the work: findings/fix plan and the directive
audit with execution order (both under `.local/`, git-ignored); a second
independent implementation audit (same place) adjudicated the eight
directives and set the must-dos below.

Changes. ①a: oversized prompt inputs (four context suffixes at any size, or
any input ≥ 64 KB) render as `{ref, media_type, path, size_bytes}`
advertisements under the existing DEBATE_JUDGE precedent; request bytes and
`input_digest` are unchanged, so every oracle is unaffected; the common
instruction teaches reading the advertised read-only `/inputs` source.
①b: CLI-side uncompactable-single-turn failures classify as
`input_too_long` only for the stateless commands (OBSERVER, DEBATE_JUDGE),
whose byte-identical retries are futile; stateful `--resume` retries (which
succeed through provider-side compaction) keep their existing
classification. ①c: attempt diagnostics gain `prompt_bytes`,
`rendered_input_bytes`, `offloaded_refs` (additive, not oracle-consumed).
②c: the TERMINATE_LINEAGE schema description and system prompt now state
the enforced semantics (the Host never schedules another cycle; only a
Debate/Leader-designated restart reopens; untested sibling chains are
silently abandoned); the SUPPORTED⇒TERMINATE ACI rule is unchanged.
①d: the per-tick adjudicated-worktree retirement is gated on the accepted
batch history actually moving (process-local memory, replayed once after a
Host restart), lists OPEN attempts only, and memorizes the
permanently-dirty retained outcome instead of re-running git every tick —
the audited run's ~20 s/tick (~170 MB full-table load plus repeated git
cleanup on three permanently-dirty worktrees) becomes flat ~5 ms.
②e: the End-Run modal's socket timeout is 30 s, and on timeout it
reconciles against the durable command row (idempotent request id) instead
of declaring failure; the monitor's STATUS polling backs off exponentially
instead of hammering a wedged socket.
③c/③d: the device-level GPU pressure backstop now consults the already
sampled pool ownership attribution — `free + owned ≥ reserve` means the
pressure is external and the process is spared; `owned_memory_mib_at_decision`
and `attribution` are recorded, and the payload is renamed with explicit
`gpu_` fields. The oracle (single- and multi-GPU branches) accepts exactly
the old or the new key set (version lives only in `configuration_id`, so
shape is the evidence, per the oracle's existing idiom); the real run's
old-shape payload re-audits clean. ③b: only the pressure-cancel path uses a
30 s terminate grace (recorded in the payload); operator/timeout cancels
keep 0.5 s. En route the real lane exposed that the grace loop never
reaped the SIGTERMed zombie root, so a 30 s cancel blocked the Host tick
for the full grace; one line (`runner.py` polls the child inside the loop)
makes the cancel return once the group actually exits.

Audit must-dos landed: ①d error-result memory (dirty worktrees no longer
re-run git per tick; the test that codified the repetition was rewritten),
③b applied grace in the payload (oracle both branches), ③c real-GPU
evidence, the CC-carryover `clamped` marker, autouse clearing of the
file-backed request registry, and direct attribute access with test
doubles declaring fields (no production getattr tolerance).

Evidence (archived under `.local/qualification/final-20260919/` unless
named). Replays of the terminated run's real requests: OBSERVER
775→93 KB, EXPERIMENT_EXECUTION 1766→345 KB, FINAL_REPORT 885→178 KB,
expert nodes ~610→~300 KB (`.local/replay_offload_sizes.py`); failure
classification replay: stateless → terminal, stateful → unchanged
(`.local/replay-20260919-cli-oversize-classification.py`). Tick timing:
549.6 ms → 19.9 ms full/OPEN at the audited 214-row shape, dirty case flat
~5 ms after the memory fix. G1 full offline: `g1.xml` 5,843 passed / 26
skipped with 3 native failures caused by a test-harness patch point lost
in the file-backed fixture (not production); fixed and refreshed as
`g1-refresh.xml` (49 passed including the three identities). G2 retained
cohort: 8/8 runs audited with the current candidate oracle — 7 clean plus
one verbatim-known historical finding — with database bytes proven
unchanged (`g2-retained.json` and per-run reports); the portable fixture
selection is 39/39 (independent audit run). G3 lane: the four scenarios
this round had not covered pass (three inside `g1.xml`, the native
learning scenario inside `g1-refresh.xml`). G4 shared CUDA on the two
authorized UUIDs: the pressure-attribution lane
(`.local/qualification/g4-pressure-20260919.xml` + retained roots) covers
unknown-kill (exclusive mode), external-spare and self-kill (pool mode)
on real workloads, with oracle validation of the new payload end-to-end;
the main lane (`g4.xml` + `g4-roots/`) is 9/10 — the two-host case failed
the closure's zero-unknown-tolerance owned-sample assertion, the same
pre-existing flake seen in the 20260917 batch at the same assertion (the
sampler records a bounded unknown when the device's CUDA process set
changes mid-scan, a teardown race under concurrent workloads; the
assertion is documented here as a known flaky surface and was not
weakened — the same test passes on targeted reruns, 2/2 today). G5:
one paid g5-local sample (`g5-local.xml` + `g5-local-roots/`, run root
archived) passed on first execution — the real run reached
COMPLETED/GOAL_MET (canonical SUCCESS, metric 0.875) on adapter /62
with CC 2.1.277 and glm-5.3-flash, charging 2.827766 USD exactly equal
to reported (zero unknown attempts; declared cohort 5 USD with zero
prior, 2.172234 USD remaining). The offload contract held end-to-end
with a real model: the FINAL_REPORT prompt advertises its 22,435-byte
`--final-report-context` as a 342-rendered-byte manifest
(`offloaded_refs` of record), the transcript shows the model issuing
`Read /inputs/0002.txt` and consuming the offloaded source, and every
attempt closed with exact accounting under the CC 2.1.277 carryover
repair.

AGENTS.md justification for the ③c mechanism (the round's one new
decision rule): (1) reproducible failure and impact — run
`MERC-causal-202609172306` attempt `…--component-3--experiment-10--process-1`:
device free pinned at 3,901 MiB against the 4,096 MiB reserve (sustained,
minimum == latest) while the process's own attributed resident peak was
552 MiB of 20,675 MiB used; the device-level rule cancelled it, freeing
almost nothing, and discarded 4.2 h of completed scan work
(`EXECUTION_FAILURE` of record). (2) Failed asset and boundary — the
Host-side pressure-cancel decision in `WorkflowHost._poll_processes`, at
the boundary between the device-level tracker (documented to never
signal or attribute) and the pool ownership sampler that already sampled
every poll for evidence but was never consulted. (3) Why existing
machinery is insufficient — the tracker deliberately never attributes;
pool admission accounts capacity only at dispatch time, so a foreign
tenant's in-run consumption is indistinguishable from self-consumption
at cancel time. (4) Smallest mechanism — reuse the existing `sample_owned`
accumulator: one read accessor and one pure decision rule
(`free + owned_latest ≥ reserve` spares), recorded per device with the
decision evidence in the renamed payload; no new sampler, no durable
state, no parallel authority; removal is reverting the decision line.
Old runs keep their frozen versions and audit against the old payload
shape; nothing is migrated.

CC version crossover of session transcripts (accepted risk). The
per-Attempt billing repair below keys off `cost-state` transcript records
that only CC ≥ 2.1.277 writes; session preparation records the newest
such record as the deduction baseline whenever one exists. If a
transcript carrying these records is later resumed or forked by an older
CLI (< 2.1.277) — a binary downgrade mid-run, which auto-update never
performs and only a manual version change can cause — the older CLI
ignores the records and reports a zero-based ledger while the Host still
deducts the recorded baseline: an over-deduction that under-accounts the
attempt, with underflowed fields flooring at zero and named in
`provider_session_carryover.clamped`. This direction stays fail-closed,
not silently under-billing: once a deducted input total falls below the
observed assistant/wire input floor, the existing lower-bound guard
withholds `provider_accounting` and the attempt closes as
`invalid_provider_metadata`. Upgrades are safe in both directions.

Known residuals (recorded, not blocking): pressure decisions made on a
single incomplete attribution sample stay conservative (a sustained-two-
poll or last-complete-sample refinement is a follow-up); a pressure
cancel blocks the serve tick only for the workload's actual remaining
life after the zombie fix (a non-blocking cancel is a separate optional
change); the twelve other unfiltered `list_attempts()` full-table scans
in interaction/debate/workflow hosts are filed as a follow-up; the
offload gate is the boolean `tool_workspace` (no legacy workspace is
paired with schema ≥ 14 in practice; tightening it to the session
workspace type is optional); the file-backed request registry clears
per-test in its own module (suite-wide bounding would move the fixture
to conftest); the G4 closure zero-unknown tolerance is a known flaky
surface under concurrent CUDA churn (see above).

## Current development — per-Attempt billing under CC 2.1.277 cumulative session ledgers, 2026-09-19

The local Claude Code auto-updated to 2.1.277 (`~/.local/bin/claude` replaced
2026-09-19 10:07; `~/.local/share/claude/versions/` shows daily builds
2.1.273 → 2.1.274 → 2.1.275 → 2.1.277). Its changelog entry "Fixed a headless
resume (`claude -p --resume`, the SDK, a VS Code extension window reload)
starting the session's cost and usage totals at zero; headless sessions now
save their totals at exit" changed the result-event contract Hypoloom bills
from: CC ≥ 2.1.277 appends a cumulative `cost-state` record
(`totalCostUSD` + per-model `modelUsage`) to the session transcript at each
headless exit, and a `--resume`/`--fork-session` launch seeds its reported
`modelUsage`/`total_cost_usd` from that record instead of starting at zero.
CC ≤ 2.1.275 writes no such record (verified: zero `cost-state` occurrences in
a 2.1.275 fixture's session files, 14 in the same fixture under 2.1.277).

Four native G3 tests failed on the unmodified tree under 2.1.277 and all
passed under 2.1.275 without any tree change, exonerating the five in-flight
workstreams:

- `test_model_usage_accounting_g3`: a same-session FINAL_REPORT retry
  reported cumulative modelUsage 35 (invocation 29 + failed attempt 6) and
  cumulative cost; `assert 35 == 29`.
- `test_partial_cli_accounting_g3`: the same mechanism recorded retry
  `token_usage.input_tokens` 11 (5 + 6); `assert 11 == 5`.
- `test_execution_completion_g3`: a chain fork inherited the synthesis
  parent's saved totals under the parent's different model identity, so the
  design attempt's ledger held two model keys and failed
  `multiple_model_identities`; the run COMPLETED with zero outcomes.
- `test_structured_output_recovery_g3` (3 cases, found during diagnosis):
  forked design accounting recorded cap+2 (cap+1 + seed 1).

Repair (no assertion weakened): `agent_sessions.prepare` records the newest
`cost-state` record of the exact transcript bytes it hands CC as
`PreparedSessionWorkspace.session_usage_baseline` (`_session_cost_state`;
absent for older CC/fresh sessions, so behavior there is unchanged).
`claude_agent_host` deducts that recorded carry from the result ledger
(`_session_usage_view`) before every per-Attempt accounting path; models the
invocation did not use drop out of the ledger, cost is deducted in Decimal,
and `provider_session_carryover` is recorded in diagnostics. The two
harnesses that assert on the raw native ledger now derive the same
invocation view from the archived prior transcript (which is byte-identical
to what preparation handed CC); every asserted quantity (29/11, the
17/3/5/7 fallback delta, cap+1, cost equality) is unchanged.

Focused evidence: 3 new unit cases (resumed cumulative ledger deducts carry
and drops the unused carried model, with and without structured-output
failure; resume/fork preparation records the newest cost-state, supersedes
older records, skips an unterminated partial tail, and records none for
pre-2.1.277 transcripts); 306 focused cases pass. Native evidence: all four
failing files plus `test_full_cc_host_g3.py` green under CC 2.1.277
(1+1+1+3+3) and under CC 2.1.275 (1+1+1+3+3). No paid, GPU or online lane
ran; existing runs, Profiles and credentials are unchanged.

## Current development — project GPU-pool detection and managed creation in init, 2026-09-17

Under the user's direction after a new run's init exited (the intake could
neither find this project's pool nor create one): the `/autoresearch init`
pool step now detects and — only through the reviewed plan — deploys the
node-local project pool instead of relying on an operator-supplied external
endpoint. The triggering mis-inference is preserved as context: the intake
labeled the wedged `MERC-causal-202609170117` nonpooled from socket absence,
while its durable CREATED payload carries the `gpu_pool` binding (socket under
the Prometheus project's `.local/gpu-pools/shared-3090x4/`, namespace = run
ID, server UID 1001). That pool is foreign-owned and stays untouched.

New bounded helper `.claude/skills/autoresearch/scripts/manage_gpu_pool.py`
mirrors `manage_host.py`'s user-systemd discipline. `status --repository`
(read-only) reports each conventional project endpoint under
`.local/gpu-pools/<name>/control.sock` (live/stale/unauthorized/absent with
served UUIDs) and recalls prior runs' stored pool bindings newest-first from
mode-ro state databases with current liveness probed; against this checkout
it reports the project pool absent and both September 16–17 pooled bindings
live. `ensure` deploys only beneath the repository's `.local/gpu-pools/` and
only for the invoking UID, deriving `hypoloom-gpu-pool@<escaped-root>.service`;
an active unit serving the exact reviewed device set is reused (`reused`),
while a live unmanaged socket, a conflicting unit and a foreign root are
refused (`service_busy`, `pool_conflict`, `foreign_pool_root`); a failed
readiness stops the new unit. There is no stop/replace subcommand: pool
teardown stays a separate operator action. The skill text keeps every existing
pool caution and states the new boundary: deployment only through the reviewed
plan and helper, after **Start now** approval, before the managed Host start;
**Prepare only** never runs it.

Focused evidence: 13 new `tests/runtime/test_manage_gpu_pool_script.py` cases
(FakeSystemd plus a real pool-protocol server on a short AF_UNIX path):
absent/live/stale detection, newest-first deduped recall with the source
database byte-identical, exact unit/argv/property creation with no ambient-key
leakage and a mode-0700 pool root, idempotent reuse, and foreign-root /
busy-socket / conflict / ready-timeout refusals with bounded unit cleanup.
Adjacent suites pass: 36 operator-skill + 23 manage-host + 4 documentation
+ 3 public-profile + 27 shared-GPU closure/integration cases. Default-ruff
findings on the two new files are the same classes the sibling helper already
carries; skill scripts are not held to default ruff. No GPU, paid or online
lane ran; the first real project-pool creation happens at the next actual
init as the operator-approved action. Existing runs, the Prometheus pool,
Profiles and credentials are unchanged.

## Current development — wire-final input floor for bill completeness, 2026-09-17

Implemented under the user's repair authorization after the read-only
adjudication recorded below: the k3 endpoint's stream-start usage runs a few
tokens above its final per-request usage, and CC records that start snapshot
in transcript assistant events, so the transcript-derived lower bound could
exceed the exact billing ledger and fail completed attempts as
`invalid_provider_metadata` (15 of 62 Agent attempts in the wedged run; both
9-17 paid samples). The guarded quantity was right; its proxy was not.

The repair reuses the existing exchange capture, no parallel authority:
`provider_observation.observed_input_floor` reduces the already-retained wire
exchanges to the per-request final input sum only when every `/v1/messages`
exchange is complete, measured and single-model (otherwise None).
`_provider_accounting` accepts an optional `observed_wire_input_floor`; when
present it replaces the transcript bound as the floor the CLI ledger must
cover (diagnostics label `provider_accounting_floor="wire_exchange"`), so the
start/final skew cancels while a ledger below the wire still fails closed.
The full-CC finalize path reads the capture once before response parsing and
reuses it for the existing observed-usage/failure diagnostics; without a
complete single-model capture the transcript guard is unchanged. The legacy
direct-provider branch has no capture and keeps the transcript guard. No
schema, identity or budget change.

Focused evidence: 14 new `tests/runtime/test_provider_wire_floor.py` cases —
floor reduction (complete/unmeasured/multi-model/non-generation), response
level (skew accepted, below-wire ledger still rejected, non-integral floor
keeps the transcript guard), and a real-CLI full-CC session whose scripted
upstream emits start=2/final=1 usage per request: the design attempt closed
SUCCEEDED with transcript bound 12 > ledger 6 == wire floor 6, while the same
captured stdout replayed without the floor still fails
`invalid_provider_metadata`. Adjacent suites pass: 165 (accounting guard
files) + 196 (full-CC adapter, file-tool CLI, session CLI, full CC host,
agent host). Full G1 after the repair: **5,760 passed / 25 opt-in skips / 0
failed**, 4,999.79 s (`g1-wirefloor.xml`; pre-checks compileall, ruff,
`git diff --check` pass). No paid/GPU lane was rerun; the k3 skew samples and
the foreign GPU tenancy remain as recorded below.

## Supplementary qualification — provider-auth-cooldown repair candidate, 2026-09-17

The user requested a complete G0–G5 batch for the uncommitted provider auth
cooldown repair, then a commit to dev. The offline lanes pass in full. The
GPU lane and both executed paid samples failed on independent environmental
and provider-side causes, preserved with their roots below; none involve the
repair. The third paid sample was not launched once two consecutive samples
established a systematic provider-metadata wall, so its allocation survives.
Detailed evidence stays in ignored `.local/qualification/full-20260917/`.

### Candidate and environment

HEAD `3f3bbd4a7f53b421dd6c118435ec2371ae4b9b08` (dev) plus the uncommitted
repair tree; runtime identity
`sha256:25f500a5090733520fe6e861bd3be98c73e4ca82005752430db5b36a88207303`,
ACI 20 (`candidate-run-version.json`). Control Python is the project Conda
3.12.14 / SQLite 3.53.4. Native CC pinned to the current
`/home/gray/.local/share/claude/versions/2.1.274` (upgraded the same morning);
model `k3-256k` through the existing mode-0600 settings file. Approved GPU
UUIDs `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` and
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`; CUDA runtime
`/home/gray/miniconda3/envs/erc-cu128`. Production `profile-check
MERC-causal` admits with the existing credentials file (exit 0). A fresh
cohort was declared: prior 0, ceiling 15 USD, 5 USD per paid sample, no
automatic paid retry (`cohort-accounting.json`).

### Executed evidence and limits

| Stage | Actual result | Evidence and boundary |
| --- | --- | --- |
| G0 | Ready | `g0-environment.json`, `g0-gpu-inventory.txt`, `merc-profile-check.json` |
| G1 | **5,746 passed / 25 opt-in skips / 0 failed**, 4,999.28 s | `g1.xml`; compileall, ruff F821/2/3 and `git diff --check` passed. An earlier invocation killed by a 3,600 s harness cap at ~61% is not evidence. The suite ran ~4× slower than the 9-16 invocation under foreign tenant load; per-test integrity is unaffected. The 25 skips are the documented paid/GPU/real-Telegram/tmpfs opt-ins. |
| G2 | 174/174 portable; retained cohort unchanged | `g2-retained.json`: six terminal roots plus the wedged run kept byte-identical databases, identical logical rows, artifact inventories and bundle readers across the lane. Readers: 0044's bundle keeps its two known archive errors; 161513 shows one pre-existing `session_archive_missing` (candidate and bundled readers agree; data-carried, unrelated to the repair). |
| G3 | 7/7, 470.30 s | `g3.xml` |
| G4 | 8/10, 680.18 s | `g4-environmental-analysis.json`: foreign tenant PID 2833485 held 15.2 GiB on GPU 0 and 254 MiB on GPU 3 throughout. `…_initial_memory_waits_then_calibrates…` could never grant 16,384 MiB per card (9,356 MiB free on GPU 0; pool requests stayed QUEUED `capacity_unavailable`/`fairness_wait` to the 540 s deadline). `…_two_hosts_share_real_cuda…` failed `unknown_sample_count == 0` with exactly one unattributed sample per card (1/41). Both failed roots show zero provider failures, zero `auth_failed` classifications and zero rate-limit gates, and the repair changes no GPU/pool/attribution bytes. Roots retained at `/tmp/pytest-of-gray/pytest-4511/`. Not rerun: the foreign allocation persists. |
| G5-local | **FAIL**, 774.17 s, 3.780730 USD known + 1 unknown attempt | `g5-local-result.json`. The run COMPLETED with GOAL_MET and the real CPU process succeeded, but the EXPERIMENT_EXECUTION agent attempt failed post-completion as `invalid_provider_metadata`: CC 2.1.274 reported `input_tokens` 415,342, five below the platform-observed assistant lower bound 415,347 (turn count also normalized 7→6). The strict accounting guard (`claude_agent_host.py:1768`) is untouched by the repair; the sample shows zero auth/rate-limit events. Closure requires a SUCCEEDED execution agent, so the sample stays failed. |
| G5-common | **FAIL**, 903.60 s, 7.302222 USD known + 3 unknown attempts | `g5-common-result.json`. Same signature struck twice (EXPERIMENT_DESIGN_IMPLEMENT, CYCLE_REPORT), SELF_EVOLVE timed out and FINAL_REPORT hit `profile_budget_exhausted`; the run FAILED. |
| G5-resource | Not executed | Two consecutive samples established the provider-metadata wall (4 events; the five 9-16 samples on CC 2.1.272/273 had none), and GPU 3 still carries foreign memory against the lane's identical `unknown_sample_count == 0` assertion. The 5 USD allocation survives. |

### Findings and smallest next steps

1. **k3 provider start/final usage skew trips the accounting guard —
   adjudicated after the batch (read-only, wire-level).** Four agent attempts
   across two paid runs reported aggregate `input_tokens` five below the
   platform's assistant-message lower bound, failing closed as
   `invalid_provider_metadata`. Adjudication: the failed sample's retained
   `provider/NNNNNNN` wire captures show the k3 endpoint's stream-start usage
   one token above its final per-request usage on 5 of 6 responses; the CLI
   billing ledger (`modelUsage`, costUSD) exactly matches the per-request
   final sums (415,342), while the transcript assistant events carry the
   start snapshot (input-only, cache fields zero), summing to 415,347 — the
   platform's max-per-message-id lower bound reproduces both exactly, so the
   platform arithmetic is correct and the bill is exact; the skew is
   provider-side. This is **not a 2.1.274 regression**: the wedged run on
   2.1.273 + k3[1m] already recorded 15 such failures among 62 Agent attempts
   (interleaved with successes — retries land on unskewed responses), while
   2.1.272 + k3-256k on 9-16 recorded final split usage (sum == aggregate on
   all 12 attempts) and 2.1.273 + deepseek-v4-pro shows start == final on its
   endpoint. Pinning 2.1.273 is therefore **not** a remedy for k3. Real-run
   implication: any CC version with k3 currently fails ~15–25% of agent
   attempts post-completion, burning node retry budget but not corrupting
   bills; a platform-side tolerance for the start/final skew pattern (or a
   non-skewing endpoint) is a separate decision. Redacted captures retained
   under each sample's `state/diagnostics/`.
2. **Foreign GPU tenancy blocks the approved pair.** All four cards carry
   PID 2833485's allocations (15.2/15.8 GiB on 0/1, 254 MiB on 2/3). G4's
   two failures and the G5-resource deferral follow from it. One G4 rerun
   plus the deferred G5-resource remain admissible once the cards clear.
3. **The repair's own evidence is fully green.** G1's 5,746 passes include
   the new `tests/runtime/test_provider_auth_cooldown.py` and the extended
   observation/budget/lifecycle suites; G2/G3 confirm durable and real-Host
   behavior unchanged. No paid/GPU failure above involves repair code paths.

### Costs and cleanup

Known charged model usage is **11.082952 USD** (3.780730 local + 7.302222
common). The four unknown-reserved attempts carry the standing 1 USD each
convention, a conservative upper bound of **15.082952 USD** — marginally
above the declared 15 USD cohort ceiling on that convention; the in-run
guard on known charges (12.082952 peak) never exceeded it. The unlaunched
resource sample's 5 USD was not spent. Cleanup census
(`cleanup-census.json`): 693 databases across today's four pytest roots,
6,582 CLOSED attempts, 74 OPEN attempts that are intentional
crash/cancel/priority/tamper fixture states, 1,362 recorded process groups
with **zero live**, one intentionally corrupt fixture unreadable. The wedged
run `MERC-causal-202609170117` was never mutated and stayed byte-identical
across G2. No real-Telegram, online-literature or soak lane ran.

## Current development — provider auth cooldown and operator recovery, 2026-09-17

Implemented under the user's repair request after a real incident: run
`MERC-causal-202609170117` hit a provider 403 window (five-hour rolling quota)
at 04:14–05:27 +08; each infrastructure failure retried on the next tick, so
fifteen rapid rejections exhausted all three expert components' node budgets
within seconds each and permanently closed their control loops, leaving the
run ACTIVE with no dispatch, no escalation and a busy-poll Host. The user
acknowledged five-hour-limit and provider-instability 403s are
indistinguishable and asked for both unattended recovery and notified manual
recovery.

The repair reuses existing machinery, no parallel authority: exchange capture
now classifies upstream 401/403 as `provider_failure.code="auth_failed"`
(`provider_observation.py`; status-based, no message sniffing), retry class
stays INFRASTRUCTURE. The existing durable dispatch gate
(`_provider_retry_pending`, previously rate-limit only) now also gates on the
latest auth failure until `budget.provider_auth_cooldown_seconds` (default
18000, zero disables, cap 604800; validated in `agent_budget.py` and
`component_contract.py`), computed read-only from durable rows via
`provider_retry_gate`, so a Host reopen neither clears nor resets it. The new
operator control `PROVIDER_RETRY_CLEAR` (lifecycle CREATED/ACTIVE self-loop,
reason="operator") records a durable clear transition that supersedes only
earlier failures; a still-broken credential re-engages the gate on the next
failure. STATUS exposes `provider_retry`; the TUI shows a PROVIDER COOLDOWN
banner with a `p` confirmation modal (`provider_resume_tui.py`, mirroring the
end-run form); CLI `provider-retry-clear` mirrors pause/resume plumbing.
Derived product events `provider_auth_cooldown` (one per episode, not per
attempt) and `provider_auth_resumed` feed the existing notification tailer and
templates/local Profile event lists; a disabled cooldown emits no events.

Focused evidence: 80 passed across the new
`tests/runtime/test_provider_auth_cooldown.py` (real Host gate blocking,
reopen durability, operator clear, re-engage, expiry, event episode dedup,
Telegram text, TUI form) plus extended `test_provider_observation.py`,
`test_agent_budget_accounting.py` and `test_lifecycle_table.py`; adjacent
suites pass: 162 (batch host, tailer, end-run UI, events, paper reading,
public profiles), 179 (profile admission/v4, observer UI/reliability/services,
human answer), 43 (live status, runtime diagnostics, projection, execution
completion). The physical-LOC budget test's allocation table was explicitly
reallocated for the added workflow-core authority (workflow_host 4500→4550,
lifecycle 550→500; total unchanged), per its designed mechanism. The wedged
run was not mutated, cancelled or restarted; no paid/GPU/online call, full
G1–G5 lane, commit or push. The repair does not revive components whose node
budgets were already exhausted in that run.

## Current development — project-local research-runtime session records, 2026-09-16

Implemented under the user's relocation request after the credentials
consolidation. The Claude Code research runtime stored this project's session
transcripts in the user directory (`~/.claude/projects/-data-Gray-Project-Hypoloom`).
All 9 files (7,121,623 bytes; aggregate checksum verified identical before
and after) now live in the ignored `.local/claude/projects/` directory
(mode 0700), with a compatibility symlink left at the original
`~/.claude/projects/` path so existing and future sessions for this project
keep writing into the project tree. A write probe through the symlink landed
in the project copy, and `git check-ignore` confirms the location is ignored.
Hypoloom's own run records needed no move: Host state already lives in
`<repo>/state/`, and sandboxed agent session transcripts already live under
each run's `sessions/` tree (`agent_sessions.py`), never in the user
directory. Deliberately not moved: shared user-level configuration
(`~/.claude/settings.json` provider settings, `~/.claude.json`), the
cross-project empty `session-env/` residue and history, and the other
project's transcript slugs. Full `CLAUDE_CONFIG_DIR` isolation remains an
optional operator choice; it would duplicate provider settings into the
project and only takes effect for launches that set it, so the symlink
layout stays the single mechanism. No run, Profile or settings mutation; no
commit or push.

## Current development — single dotenv credentials file, 2026-09-16

Implemented under the user's configuration request after the stopped run's
Telegram source drift, then consolidated from per-key files into one file
under the user's follow-up. Local credentials now live in one ignored dotenv
file (`.local/credentials/hypoloom.env`; directory 0700, file 0600): it
holds `OPENALEX_API_KEY` and `UNPAYWALL_EMAIL` (migrated from the
`~/.config/prometheus/` originals, which are unchanged) plus the
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` lines the user fills in. The format
is a strict subset (`KEY=VALUE` lines and `#` comments; no quoting or
expansion) parsed by `hypoloom/credentials.py` with absolute-path,
O_NOFOLLOW, regular-file, UTF-8, ≤8 KiB, duplicate-key and malformed-line
checks; errors name keys and line numbers, never values. The Profile declares
the one path explicitly, and the path string (never secret bytes) is
projected into the durable contract like any other control:

- `methodology.yaml` gains optional `literature.credentials_file`;
  profile_v4 validates it as an absolute, non-literal path. The autoresearch
  SKILL's literature argv guidance passes the same file as
  `--credentials-file` to serve, profile-check and start; the legacy
  single-secret flags `--openalex-api-key-file` / `--unpaywall-email-file`
  remain accepted, never mixed with it.
- `notify.yaml` Telegram gains `credentials_file`; the tailer reads
  `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from it. `bot_token_env` /
  `chat_id_env` remain supported; exactly one source is enforced at
  validation and delivery.
- Public templates (en/zh), the new `profiles/templates/hypoloom.env.example`,
  a rewritten bilingual profiles/README credential section, the
  `docs/guides/notifications.md` and `SETUP.md` credential guidance, and the
  local MERC-causal Profile follow the convention.

Focused evidence: 294 cases across credentials, tailer, profile, operator-skill,
public-template, CLI, Unpaywall, admission and documentation suites pass, plus
69 admission cases standalone and 218 literature-module cases; the local Profile
admits clean through production admission and the offline literature preflight
builds from the real file without network I/O. Two `component_batch_profile`
fixture errors appear only when one pytest invocation mixes `tests/` root
modules with `tests/runtime/` modules in a particular order; the same
selection built from modules this change never touched reproduces them, every
module passes standalone and pairwise, and the standard `scripts/qualify.py`
lanes never mix those roots — a pre-existing pytest-9 collection quirk, not a
regression. The stopped run's frozen contract keeps the old names. Telegram
delivery is unverified until the user fills the two Telegram lines; no paid,
GPU or online call was made.

## Current repairs — provider-stream envelope tolerance, 2026-09-16

Implemented under the user's repair request after they stopped
`MERC-causal-202609161513` (CANCELLED r106 at 20:04:15 +08, all 102 recorded
groups absent, no socket; 85 Agent + 17 Process Attempts durable and
unchanged). Evidence: `.local/qualification/envelope-repair-20260916/`.

- Repaired platform defect: four EXPERIMENT_DESIGN_IMPLEMENT attempts failed
  as `invalid_tool_workspace` ("declared-source capture requires a structured
  candidate") although the Host Stop hook had accepted each candidate.
  `_unpack_provider_stream` required the single result event to be the last
  NDJSON record, but Claude Code 2.1.273 also emits (a) a marked synthetic
  zero-turn result (`origin.kind == "task-notification"`) when a resumed
  session inherits an orphaned background task, and (b) system lifecycle
  notices after the terminal result. The parser now takes the last result as
  authority, tolerates only the exactly marked resume artifact before it and
  only `system` events after it; duplicate real results, unmarked or non-zero
  earlier results, stream_event records and post-terminal provider content
  remain `invalid_provider_envelope`. The original failed Attempts and bills
  are retained unchanged.
- Focused evidence: 15 envelope cases, 210 module-suite cases and 4
  session-CLI cases pass; read-only replay of the four actual failed streams
  now selects the true terminal result with its candidate text. No full
  G1/G4/G5, GPU, paid or online qualification was run; the repair is
  uncommitted.
- Classified but not repaired (by design or model-side): five PROCESS LOST
  `capacity_unavailable` are the designed bounded capacity wait
  (LOST-before-exec plus Host retry, Oracle-validated) amplified by
  `max_active_processes` 6 exceeding the ~3-experiment effective GPU supply
  (two cards foreign-occupied); five EXPERIMENT_EXECUTION Agent 3000 s
  timeouts are the designed long-experiment handoff; two same-chain
  measurement declaration/authorship mismatches (10.637398 USD and
  5.263 USD) are model-side contract violations correctly rejected with
  retry=MODEL; one invalid_structured_output and two invalid_candidate are
  the expected retryable model-output classes. The 3 -> 6 agent-slot change
  caused no concurrency race; its real effects are a ~0.9 USD/min peak cost
  rate (169.8 USD in 3.7 h, null USD caps) and capacity-wait churn. Per the
  user's confirmed packing policy, per-card multi-experiment packing is
  intended and slots are deliberately not matched to card count: admission
  accounts `min_vram_per_experiment` 8192 + `gpu_vram_reserve_mb` 4096 =
  12288 MiB per slot (2 per 24 GiB card, 8 theoretical), while measured
  owned resident peaks of all 8 succeeded Processes are only 364-786 MiB
  (card-level peaks 3.2-7.7 GiB; one 15.6 GiB paired_ceiling_control
  outlier). The declaration paces admission only; lowering it in
  profiles/local/MERC-causal for a future run raises packing density at the
  price of admitting more heavy outliers per card.

## Supplementary qualification — approved repair recheck, 2026-09-16

**The approved supplementary work is complete.** G4, G5-resource and
G5-reading pass; the single public PDF fetch fails. The user approved the
complete bounded proposal below and raised the same cohort threshold to
55 USD. Evidence is in `.local/qualification/qualification-recheck-20260916/`.
The initial 13 source databases still account for 42.434221 USD, including
1.20 USD retained unknown reservations. No old failure, charge or reservation
is reset. The runtime remains `38d80533...` / ACI 20; 518 shipped code, test and
operator files match the repaired source package. Control Python 3.12.14,
SQLite 3.53.4, CC 2.1.273, deepseek-v4-pro/settings-file authentication and the
approved Torch 2.8.0+cu128 environment have been rechecked.

| Approved sample | Current observation |
| --- | --- |
| G4 | **10 passed / zero skips**, 301.71 s. Four Hosts close 72 Agents/eight CUDA Processes; all 80 groups are absent, both private-pool Oracles pass and no socket remains. Real output confirms each Process holds 64 MiB total, with 256 MiB configured allocator ceilings total; two-card Processes use 32/128 MiB per card, contexts additional. Maximum sum of observed per-device allocator peaks is 68 MiB. A real capacity wait precedes later calibrated same-card overlap. |
| G5-resource | **PASS**, 653.22 s / **4.992342 USD**, GPU 3 only. The independent wrapper check, native resource Skill/query, actual CUDA, measured history, publication and report pass. All 11 Agents/one Process succeed and close; all 12 groups are absent, run/pool Oracles pass, no socket or paper staging remains. |
| G5-reading | **PASS**, 603.95 s / **3.946811 USD**, CPU only. Two successful native readers overlap for **1.718 s**, read the exact `0.83` and `5 turns` method details from the synthetic originals, and are collected into the accepted parent synthesis. The CPU result, analysis, final-report Skill, publication and terminal Oracle pass. Fourteen Agents/one Process close; all 15 actual groups are absent. One failed reader is retained beside its successful allowed retry; see below. |
| Public PDF | **FAIL**, `GroundingWebError: timed_out` after **15.003 s** on the same IJCAI URL. One fetch only; no new metadata/model call, retry or timeout increase. This does not invalidate the earlier successful OpenAlex/Unpaywall metadata observations, nor establish full-text availability. |

The initial supplemental G4 census included pytest's symlink aliases in its
pool count and failed its own count assertion. The corrected read-only census
excludes those aliases and records two actual pools. Both observations are
retained; the G4 pytest invocation itself passes without a rerun. The existing
research pool is only queried and reports no active commitments. No formal
Host/topic or notification is authorized by this recheck.

### Failure preservation, cost and cleanup

The reading sample's first method-A reader is rejected as
`invalid_structured_output` at `provider_output`: the diagnostic is
`/: additionalProperties constraint failed`. Its **0.078368 USD** charge and
FAILED Attempt are retained. The existing in-run retry succeeds; this is not a
second qualification invocation. Grounding succeeds without the earlier
uncommissioned-evaluator failure, and this sample reaches analysis/report
closure without a reported threshold overrun. One successful retry does not
guarantee every model response will follow the schema.

The resource sample exposes the actual Process's **379,584,512-byte** owned
resident peak, including context, through the native post-wait resource query.
The CUDA allocator, context and pool admission amounts are distinct; its pool
admission is 2,048 MiB, not a measurement of allocation. Its final report is
6,378 bytes; reading's is 6,264 bytes. All 25 paid native archives identify
`deepseek-v4-pro` in actual assistant messages, with 1,670 archived events and
no observed compaction boundary. Unique tool counts use session/tool-use IDs,
not repeated occurrences in resumed transcripts.

The same cohort now conservatively accounts for **51.373374 / 55 USD**, leaving
**3.626626 USD**. This includes **50.164374 USD** reported model usage,
**0.009 USD** previously metered literature, **1 USD** retained unknown model
usage and **0.20 USD** retained unknown OpenAlex usage. The two new samples add
**8.939153 USD** with no new unknown model charge. All 15 original/new databases
are folded once through existing accounting. This is CLI/Host accounting, not
an upstream invoice or verified credit balance. No further paid retry or GPU
sample is authorized by the remaining balance.

Independent final inspection finds all **107 recorded process groups absent**:
80 from G4 and 27 from G5. The six test Hosts have 97 Agent Attempts and ten
Processes, all CLOSED; the one failed reader remains a failed Attempt. All three
private pools pass their existing Oracles, all nine reservations are RELEASED,
and sockets/paper staging are absent. No live process command belongs to these
test roots, and NVML reports no GPU compute process. The existing research pool
still has zero active commitments and was not reconfigured or replaced. G4's
disposable targets are removed by its fixtures; paid state/source/data and
original failures are intentionally retained read-only. Final live-effect
cleanup is recorded **2,928.82 s (48.81 minutes)** after G4 launch, within the
approved 60-minute window.

### Current evidence scope and readiness

- G0 and the launch-input repairs have their focused reproductions and fixes;
  this invocation verifies the exact candidate, environment, selected provider,
  GPU amounts and unchanged existing-pool binding. There is no new runtime edit.
- G1–G3 reuse the preceding full offline, retained read-only and native CPU
  evidence for unchanged behavior, plus the 949 passing repair identities.
  No full expensive suite was rerun as a debugging loop. The historical
  real-research ACI-20 reader cohort is still missing; fixture/replay coverage
  does not establish unsupported cross-version recovery.
- G4 now has compliant real allocation, waiting, measured calibration,
  same-card overlap and cleanup evidence on the repaired workload. The old
  per-device/per-Process approval discrepancy remains part of the history.
- G5-local's old false negative is closed by the earlier read-only replay;
  G5-common's compatible DeepSeek result is reused. The two newly approved
  resource/reading samples pass. The original local/resource/reading FAIL
  invocations and their costs remain at their original locations.

**The repaired platform has evidence for its bounded native/GPU/reading
closure and can prepare a fresh real topic in CC. It is not an unqualified
“everything passed” conclusion.** The current IJCAI PDF route still fails:
OpenAlex metadata and Unpaywall DOI lookup success do not supply the missing
full text. A topic relying on that source needs reviewed existing local text
or another verified accessible source before relying on its methods/details.
No general-network, long-context-compaction, paid multi-expert debate or long
soak result is inferred from these one-expert canaries and synthetic papers.
Existing native multi-expert CPU/debate evidence retains its narrower scope.

MERC-causal remains admitted with its user-confirmed budgets, language and
experts; it currently allows `web_search`/`web_fetch`, without opting into
`read_papers`. Enabling that optional tool is a Profile choice, not something
this successful canary silently changes. A later CC launch must carry the
already reviewed formal Host's existing-pool socket, independent namespace,
authorized GPU inventory, model/settings and retrieval arguments. Qualification
GPU authorization and this 55 USD cohort are not a new research-run resource
or spending allocation. No formal Host/topic, existing-run mutation, external
notification, commit, push or publication occurred.

## Current repairs — qualification boundaries and launch inputs, 2026-09-16

This section records the earlier repair stage. The supplementary result above
resolves its then-pending approval and live samples; original failures remain.

The user-authorized repairs are implemented and checked with focused offline
tests, one native CC/local-provider CPU closure and read-only replay of the
saved G5-local run. **This is not a new full G0–G5 promotion.** The original
qualification invocations below, including every failure and charge, remain
unchanged. Evidence and the ordinary before/after diff are under
`.local/qualification/qualification-repair-20260916/`.

### Changes and evidence

- Candidate remains HEAD `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` plus the
  imported, uncommitted tree. Four runtime files change: `claude_agent_host.py`,
  `cli.py`, `literature.py` and `profile_v4.py`. The existing runtime identity is
  now `sha256:38d805336b645c47ce3d7e7d620b1e50983e4661b8c7272de6882abb2cefb2b8`,
  ACI 20. The operator manager, qualification fixtures/tests and documentation
  also change. There is no new schema, migration, budget authority or recovery
  mechanism. `workflow_host.py` remains unchanged at 4,500 lines.
- **G4 amount repair:** the existing generated CUDA fixture now divides a
  per-Process total of 64 MiB held tensor and 256 MiB configured allocator
  ceilings over its visible devices. One GPU gets 64/256 MiB; each of two GPUs
  gets 32/128 MiB. CUDA contexts remain additional. Output and closure assertions
  distinguish per-device amounts, per-Process totals and observed per-device
  allocator peaks. Offline execution of the generated source with an injected
  Torch double checks both device counts; this is not real CUDA evidence. The
  original G4 scope deviation is retained and requires a fresh authorized sample.
- **G5-local recognizer repair:** `_has_seeded_repair` now accepts the retained
  validation-file display and `/workspace` prefix before the exact unittest.
  It still requires the real named failure, immediate printed nonzero status,
  matching native tool result and subsequent ordered edit/pass. Regression
  cases reject a different test, intervening command, zero status, echo-only
  text, unrelated tool result and wrong order. Actual local shell execution also
  proves red/edit/green despite echo returning zero.
  The existing `_assert_current_canary_closure` now passes on the saved
  `/var/tmp/hq-g5-local-ljlqtau_/trial`, including native tools, publication,
  report, charges and Oracle. Its 12 Attempts remain CLOSED, all 12 groups
  absent, and database/WAL bytes, original FAIL result and accounting file are
  unchanged. This resolves the old assertion by read-only replay, with no new
  provider call; it does not pretend to be a new-candidate paid invocation.
- **G5-resource fixture clarification:** its instruction supplies an absolute
  `/workspace/experiment.py --local-check` command on a separate line, followed
  by native Read in a separate call. Prior output cleanup also uses a separate
  call. The strict recognizer is unchanged; both retained cleanup-chain forms
  still fail negative tests. The corrected sample has not run on a paid model.
- **Reading commission and budgeting:** the local/reading fixture now explicitly
  requires Grounding's `acceptance: null`, since its declared metric already
  defines evaluation. This targets the observed uncommissioned-evaluator error;
  common-acceptance fixtures still commission their evaluator normally. Existing
  Host/CLI cost accounting and no-further-launch behavior are unchanged.
  Documentation now states response-time overshoot and the need to fund retries,
  analysis and reporting. No code change can establish that 4 USD suffices for
  this model; the original FAILED run and 4.052769 USD charge remain failed.
- **Previously reproduced launch-input defects:** environment authentication now
  forwards the selected `ANTHROPIC_BASE_URL` with its API key through both CLI
  and managed scope. Settings-file transport still excludes ambient credentials
  and gateway settings. The OpenAlex key reader enforces its documented regular,
  non-symlink mode-0600 requirement before reading. Profile defaults, experts,
  Grounding and stage overrides reuse the existing exact-model validator;
  documented `REPLACE_WITH_PINNED_MODEL` and Grounding placeholders are rejected,
  including at the direct CLI. Existing real credentials/Profile are unchanged.

### Verification, retained failures and cleanup

The selected current identities have **949 passing results / five opt-in skips /
zero unresolved failures**, 954 identities total. This is a scoped union, not a
new full-suite count. `selected-test-results.json` preserves each invocation:
the initial reproduction has 19 failures/51 passes; the first focused run has
834 passes/two failures/five skips. The latter failures were a test assertion
using `experts.defaults.model` instead of the actual
`experts.yaml.defaults.model`, and an old dummy OpenAlex key fixture left at
0644. Corrected tests retain the production checks; follow-ups have 217 passes/
three skips and 359 passes. Native/settings/bundle checks have 27 passes,
including actual CC 2.1.273 with a loopback scripted provider, CPU execution,
final report and clean Oracle after the source settings file is removed.
The later addition of the second documented placeholder only rejects that
sentinel; the native fixture's valid selector and execution path are unchanged.
The five skips are two real G4 cases and three paid G5 alternatives, not passes.

Compile/static checks and Git whitespace checks pass. The fresh wheel's 133
runtime/resource files match checkout and its isolated installation; installed
CLI/MCP imports resolve to that installation. The source package includes the
changed operator manager. Public bilingual controls and fully bound admission
pass. Production `profile-check` with the actual OpenAlex/Unpaywall files admits
MERC-causal read-only, with no network probe. An initial maintenance invocation
omitted required `--run-id`/resource-mode arguments and exited at argument
parsing; its stderr is retained separately from the corrected admitted result.

The native CC closure has 12 CLOSED Attempts/12 absent groups; the separate
bundle-restart CPU scenario adds four CLOSED Attempts/two absent process groups.
Together they have 16 CLOSED Attempts and all 14 actual groups absent.
All available group IDs across 11 test databases are absent; no socket or
paper-staging residue remains. Three deliberately invalid database fixtures
are listed separately. The process-command census finds no live test owner.
Twenty-five external preparation fixtures created by these tests are removed;
test logs/state and the original paid source/data remain for inspection.
No GPU workload, public-network retrieval, paid call, notification, formal
Host/topic, old-run mutation, existing-pool change, commit or push occurred.

### Readiness and next bounded verification

The topic can continue preparation in CC. **Formal Start has not gained full
qualification clearance.** The preceding full G1–G3 and G5-common evidence is
retained for unchanged behavior; the launch-input changes have focused evidence
above, not a fabricated full requalification. G5-local's false negative is
resolved by replay. Real G4 with corrected amounts, G5-resource adherence,
reading analysis/report completion and online full-text availability remain
unverified. The PDF timeout has no reproduced code cause; no speculative retry
or timeout increase was added.

No new charge was incurred. The same accounting file remains at
**42.434221 / 50 USD**, including 1.20 USD of retained unknown reservations;
**7.565779 USD** remains. No failed charge or unknown reserve was reset.

One concrete proposal, **pending user approval**, is:

| Sample | Binding and limits |
| --- | --- |
| G4, two existing live cases | Fresh private pools; GPU 0 `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` and GPU 3 `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`. Existing CUDA prefix `/home/gray/miniconda3/envs/erc-cu128`, Torch 2.8.0+cu128. Each Process totals 64 MiB held tensor / 256 MiB allocator ceilings, split over visible cards, context additional. Each case at most 540 s; at most 20 minutes including cleanup. No paid provider. |
| G5-resource, once | GPU 3 only, fresh private pool, same CUDA prefix; one Process with 64 MiB tensor / 256 MiB allocator ceiling, context additional. 6 USD dispatch threshold; each Agent at most 2 USD / 300 s; sample at most 1,200 s plus 30 s cleanup. |
| G5-reading, once | CPU only, at most three Agents concurrently, each at most 1 USD / 300 s; 6 USD sample dispatch threshold, at most 900 s plus 30 s cleanup. Two existing synthetic method documents; no extra OpenAlex query. |
| PDF route, once | Existing public IJCAI PDF for DOI `10.24963/ijcai.2022/628`, through the existing document fetcher; unchanged 15 s fetch / 4 MiB / 5 s extraction bounds, at most 60 s overall. No model, new metadata query, automatic retry or timeout escalation. |

G5 control is project Conda Python 3.12.14, CC 2.1.273, exact
`deepseek-v4-pro` and the existing mode-0600
`/home/gray/.claude/settings.json` binding to `api.deepseek.com`. The current
read-only preflight still validates that selection. The complete proposal is
at most 60 minutes including cleanup and reuses no existing research pool.
It needs a cumulative dispatch ceiling of **55 USD**: current conservative
42.434221 plus 12 USD proposed samples is 54.434221, leaving 0.565779 USD for
response-time overrun. This is not a guaranteed upstream invoice cap; actual
overshoot/unknown charges remain accounted and prevent further unaffordable
launches. No G5-local/common paid repeat is proposed. Approval for this proposal
does not authorize starting the real research topic.

## Current qualification — repaired dispatch candidate, 2026-09-16

**No full G0–G5 promotion.** The full offline suite and all approved paid
samples have finished. G1–G3 pass within the scopes below; G5-common passes. G5-local and
G5-resource retain failed qualification assertions, and G5-reading exhausts
its allocation before analysis/report closure. G4's eight functional checks
pass, but the resource amount used by its dual-GPU Processes differs from the
approval wording; this is a qualification preflight error, not an unqualified
G4 pass. No real research run was started, resumed, migrated or settled.

Evidence is in `.local/qualification/full-20260916-post-dispatch/`. This section
supersedes the earlier current-status claims below, without overwriting their
original invocations, failures or charges. The existing cumulative ledger is
`.local/qualification/full-20260916/cohort-accounting.json`; no new billing or
qualification authority was introduced. Runtime and test code were not changed
during this qualification.

### Candidate, environment and reused evidence

- HEAD is `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` **plus the imported,
  uncommitted tree**, not that commit alone. The existing runtime identity is
  `sha256:68f89d42805cd3db85182baf1d5539b6b6dbbc515815eb7854d5a5c3ddd7b2e7`,
  ACI 20. Seven runtime files differ from the earlier `96d0f3f9...` candidate;
  the focused repair evidence below remains applicable, while old k3 results
  do not establish this model binding's compatibility.
- Control Python is project Conda 3.12.14, SQLite 3.53.4, with imports from this
  checkout. Native tests pin CC 2.1.273. The separate installed reader uses a
  complete Conda environment and a fresh wheel target outside checkout imports.
  Bubblewrap, user-systemd, Chrome and Poppler are present; `Linger=yes`.
  The user manager's existing failed legacy/desktop units were left unchanged.
- Actual paid responses identify **deepseek-v4-pro**, through
  `api.deepseek.com` and the existing mode-0600
  `/home/gray/.claude/settings.json`. This is real-call evidence, not login or
  `--help` evidence. No credential value was printed. GPU execution uses
  `/home/gray/miniconda3/envs/erc-cu128`, Python 3.12.13 / Torch 2.8.0+cu128.
- Public profiles remain exactly `multi-expert-en` and `multi-expert-zh`, each
  with six YAML files. The public-source audit finds no machine-private runtime
  path dependency; the selected legacy-named credential files and existing
  Prometheus pool are explicit operator bindings. No minimum public template
  was introduced.
- Fresh production `profile-check MERC-causal --profiles-root profiles/local`
  with the selected OpenAlex/Unpaywall inputs admits the existing real Profile.
  Its target remains clean at ERC `b4590893f756b9f09e2ff2bcbdcf1b784322801c`.
  Port 8766 can currently bind; this is not a reservation. The confirmed topic
  limits, omitted USD caps, language and experts remain unchanged. It still
  admits `web_search`/`web_fetch`, without opting into `read_papers`.

### Results from this invocation

| Stage | Actual result and limits |
| --- | --- |
| G0 | Current repair reproductions are covered by the retained focused tests and the new full regression selection. Current environment, imports, model/authentication, Profile and existing shared-pool status are checked. The G4 amount-review defect below prevents calling the resource preflight fully correct. |
| G1 | **5,665 passed / 25 skipped / zero failures**, 4,997.54 s, current collection 5,690. Compile/static checks and packaging pass. All 133 runtime/resources, including three migrations, runtime Skills/hooks and Web assets, match checkout in the wheel and fresh installation. The 557-file source package also includes operator Skills and public Profiles, excluding private Profiles/settings/state. Actual installed CLI/MCP, managed user-systemd Host using this candidate's manager script, CPU Process, publication, terminal Oracle and Host exit pass. |
| G2 | **174/174 selected portable checks pass in G1**, without a duplicate run. Five retained Hypoloom MERC roots pass both the current installed reader and the prior accepted reader with matching findings; database/WAL bytes, logical state and artifact inventories are unchanged. The original 0044 bundle retains its two known archive findings. There is still no historical real-research ACI-20 paper-reader cohort; fixtures do not replace that gap. |
| G3 | **7/7 selected scenarios pass in G1**. Seven roots have 183 CLOSED Attempts / 154 absent groups and seven clean Oracles, including current-ACI submit-commit crash/recovery, actual TUI leave/reentry, explicit End run and Host exit. The additional current-ACI three-expert case has 84 Agents plus six CPU Processes, six debate statements and one Judge, all CLOSED and absent groups. The separate debate-restart scenario uses ACI 14; do not conflate the two scopes. |
| G4 | **8 functional checks pass, zero skips, 302.37 s; approval-scope deviation remains.** Two fresh private pools and four Hosts exercise actual CUDA, same-card overlap, one pre-Process capacity wait, measured calibration, later dispatch and release. 72 Agents/eight Processes are CLOSED, all 80 groups absent, both pool Oracles pass and all eight reservations are RELEASED. Existing shared-pool configuration is untouched. See the amount discrepancy below. |
| G5-local | **FAIL**, 576.24 s, **3.485287 USD**. Run COMPLETED, 11 Agents/one CPU Process successful, Oracle clean. The seeded-repair recognizer misses the real red/edit/green sequence because the red command starts with a data display and `cd`; the exact trace is retained. No paid rerun. |
| G5-common | **PASS**, 767.48 s, **5.110453 USD**. Grounding authors/tests the common evaluator; the actual CPU result is independently parsed, accepted and followed by a final report. All 12 Attempts succeed and close; Oracle and cleanup pass. |
| G5-resource | **FAIL**, 719.85 s, **4.278484 USD**. Run COMPLETED and all 12 Attempts succeed/close, but the model combines `rm -f` with the wrapper check despite the sample's separate-call requirement. Actual GPU 3 CUDA, publication and report finish. Independent read-only use of the existing resource trace/history validators passes, as do run/pool Oracles; this does not change the original failed assertion. |
| G5-reading | **FAIL**, 565.68 s, **4.052769 USD** against a 4 USD sample threshold. Two successful readers overlap for 13.85 s, read exact method details and are collected into the accepted parent synthesis. The existing reading-evidence validator passes on those archives, and a CPU experiment succeeds. RESULT_ANALYSIS exceeds its remaining USD threshold; the Host then rejects FINAL_REPORT before launch and ends FAILED. The missing report is not a pass. |

The resource sample's actual CUDA output identifies only
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`, one device, a 64 MiB tensor and
69,206,016 allocator-reserved bytes. Its owned peak is 379,584,512 bytes,
including context, with 13 complete attribution samples and no unknown samples.
The native resource queries expose that same completed measurement. G4 also
uses `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84`; both UUIDs were approved.

The four paid samples retain 46 native transcripts / 3,052 archived events.
No native compaction boundary appears in them, or in the separately inspected
current three-expert and debate-restart archives. This does not qualify
long-context compression quality. Tool counts use existing session/tool-use
identities when deduplicating resumed transcripts; archival occurrences are not
additional experiment effects. No paid debate/Judge or long soak was added.

The 25 G1 skips remain skips: explicit GPU/paid alternatives, the optional
real tmpfs ENOSPC case and real Telegram notification. The approved G4/G5
selections have their own results above; unrelated skipped probes were not
silently promoted. `g1.xml` and `g2-g3-g1-coverage.json` map current test
identities to their actual results. New quarantine tests also pass with real
CC: live affected calls end, submitted CPU work is retained without fabricated
acceptance, no new readers dispatch, and explicit cancellation cleans up.
Five native reading/quarantine roots have 65 CLOSED Attempts / 65 absent groups
and clean Oracles.

### Findings and smallest next steps

1. **Resource preflight deviation — G4 cannot be marked compliant with the
   stated amount.** The approval wording specified a 64 MiB tensor and 256 MiB
   allocator limit per Process. `test_shared_gpu_native_g4.py:102–105` applies
   both values per visible GPU. Four observed dual-GPU Processes therefore each
   hold 128 MiB of tensors and configure allocator ceilings summing to 512 MiB,
   plus contexts. The total allocator peak was not separately recorded; the
   configured ceiling is not a measurement of actual use. This was the audit's
   incorrect preflight wording. All effects have ended and been reclaimed.
   Future review must state per-device and per-Process totals accurately, or
   adjust the existing test workload to the intended total before seeking the
   corresponding approval. No further GPU work or retroactive approval is
   assumed. See `g4-allocation-observations.json`.
2. **Reading budget failure and response-time overshoot.** The first Grounding
   Attempt is rejected as `invalid_candidate` because it invents uncommissioned
   common acceptance; its **0.432200 USD** charge is retained beside the allowed
   successful retry. Later RESULT_ANALYSIS receives a remaining cap of
   **0.534581 USD**, reports **0.587350 USD** and exits with
   `error_max_budget_usd`. The sample exceeds 4 USD by **0.052769 USD**.
   `workflow_host.py:2276` / `2457` reject further dispatch, including the report;
   the no-process report Attempt is not an unknown model bill.
   The existing qualifier explicitly handles post-response overshoot
   (`test_real_current_host_g5.py:198`, `847`). Future proposals must describe
   this threshold honestly and fund recovery/report work within the existing
   budget mechanism; do not invent a hard upstream currency cap or relabel the
   failed run. No additional reading sample is authorized here.
3. **Local qualifier false negative.** At
   `test_real_current_host_g5.py:682` / `1075`, `re.match` requires the failing
   unittest command to start with Python. The retained native call instead
   displays validation data, changes to `/workspace`, runs the exact test and
   immediately prints `exit=1`; a native Write and successful rerun follow.
   Extend that existing recognizer only for evidenced harmless prefixes while
   preserving test identity, failure summary, exit status, tool pairing and
   edit order. Add focused positive/negative cases and replay the saved closed
   canary read-only; a new paid local run is unnecessary for this defect.
4. **Resource sample's explicit call-shape requirement is unmet.**
   `test_real_shared_gpu_g5.py:137` requires setup/cleanup to be separate;
   lines 399/624 reject the actual `cd /tmp && rm -f ... && ... experiment.py
   --local-check` call. The wrapper really runs successfully, but that does not
   satisfy the stated requirement. Keep the original result. Any further
   qualification instruction change belongs to this bounded fixture; there is
   no evidence requiring a generic executor change. A new paid sample would
   require its own approval.
5. **Online full-text availability remains unproven in this invocation.**
   The one approved explicit academic query succeeds through the actual CLI
   wiring with general-web fallback disabled. Unpaywall successfully locates
   DOI `10.24963/ijcai.2022/628`; the single allowed PDF fetch then times out.
   The complete probe ends in 17.56 s, leaves credentials unchanged and creates
   no Host. No retry was made. Keep the successful metadata/DOI evidence separate
   from the failed PDF route; recheck that route or use reviewed existing local
   material before depending on online full text. Current DDG reachability was
   not independently retested. See `online-retrieval.json`.

Earlier documented alternative-path defects remain open: environment-only
gateway URL forwarding (`cli.py:490–497` and the manager's minimal environment),
OpenAlex reader permission enforcement (`literature.py:2896`), and unresolved
model placeholders (`profile_v4.py:72–73`). Their unchanged code and retained
reproductions remain evidence; the reviewed settings-file/0600-key/explicit-model
route avoids those conditions. They were not silently fixed or counted as
negative checks passing in this qualification.

### Costs, cleanup and readiness

The same cohort ceiling is now **50 USD**. Its conservative total is
**42.434221 USD**, leaving **7.565779 USD**: **41.225221 USD** model-reported
cost, **0.009 USD** previously metered literature, **1 USD** retained unknown
model reservation and **0.20 USD** unknown OpenAlex reservations (old/new
0.10 USD each). This round adds 16.926993 USD of reported model usage and the
new 0.10 USD online reserve. The ledger folds all 13 original/new database
sources through the existing accounting code; directory changes do not reset
spend. This is CLI/Host accounting, not an upstream invoice or verified credit
balance. There are no new unknown model charges and no further paid retries.

All four paid roots have **51 CLOSED Attempts / 50 absent process groups**;
one failed report is rejected before a process exists. There are no sockets,
paper-staging entries or live GPU reservations. Failed states and source/data
fixtures are deliberately retained for read-only replay. Four unused
preparation fixtures were removed. G4's 80 groups and installed delivery's two
groups are absent; its managed unit is collected with MainPID 0 and exit 0.
The complete G1 census reads 979 SQLite files; 939 contain the relevant Attempt
PGID columns. All 1,719 distinct available group IDs are absent. Fifteen
intentional non-database negative/display fixtures are listed separately in
the census, not treated as failed production runs. Synthetic unfinished unit
states are not relabeled completed. G3's seven roots independently have 183
CLOSED Attempts / 154 absent groups and clean Oracles. External G1 preparation
fixture directories are removed; internal fixture evidence, paid source/data,
state and bundles remain available for read-only review.

The supplied real topic can continue preparation in CC, but **this is not a
full qualification approval for formal Start**. Close or explicitly review the
G4 scope discrepancy, repair the local evidence recognizer, resolve the two
other failed G5 samples within a separately authorized bounded plan, and
establish the selected full-text source. At the later CC launch, carry the
reviewed existing-pool socket, independent namespace, authorized UUIDs, exact
model/settings and retrieval arguments into the actual Host and verify STATUS.
The topic's previously confirmed configuration does not need to be invented or
reconfirmed. No formal Host, topic, notification, commit or push was created by
this audit.

## Current repairs — dispatch faults and academic binding, 2026-09-16

The paused-topic findings below are repaired in the checkout, with focused
offline and real native CPU verification. **This is a repaired development
candidate, not another full G0–G5 promotion or a paid DeepSeek compatibility
result.** Evidence, original failures and the ordinary before/after patch are
in `.local/qualification/dispatch-repair-20260916/`. No existing run/bundle,
Profile, credentials, shared pool, GPU allocation or external service was
modified; no paid call, notification, commit or push was made.

Candidate: HEAD `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` plus the imported,
uncommitted tree. Seven of 133 runtime/resources differ from the previously
qualified `96d0f3f9...` bundle: domain, workflow_host, host_tools_server,
full_cc_runtime, paper_reading, grounding_web and cli. The existing
`installed_run_version()` runtime identity is
`sha256:68f89d42805cd3db85182baf1d5539b6b6dbbc515815eb7854d5a5c3ddd7b2e7`;
ACI remains 20, with no new schema, migration or evidence authority. Control
Python remains project Conda 3.12.14 / SQLite 3.53.4. Actual native tests pin
CC 2.1.273 and use local scripted model/document transports, CPU only.

Changes and observed behavior:

- `domain.ExecutionContract` validates argv as an ordered, nonempty tuple of
  text while allowing repeated tokens. Data-split and environment uniqueness
  remain strict. Tests submit both array and shell-string commands with repeated
  `32` and `2`, preregister them, execute the exact argv in a real CPU Process,
  collect accepted output and pass the existing Oracle.
- `WorkflowHost._poll_agents` ends live model calls belonging to the
  quarantined run and records non-retryable `dispatch_paused` failures without
  a fabricated scientific outcome or owner workflow transition. Existing
  session archival and accounting retain observations, including unknown cost.
  Fake execution owners close consistently; queued paper readers cannot start.
  `host_tools_server.call_tool` keeps experiment waits bounded while awaiting
  Host shutdown, instead of immediately returning the same blocked state.
  Reader capacity blocks retain their immediate return for serial fallback.
  Submitted Processes still finish; native verification observes a successful
  Process remaining EVALUATING with no accepted outcome until the test explicitly
  cancels. Control, peer-run isolation and STOPPING/report cleanup remain covered.
- `cli` supplies the selected OpenAlex client to explicit academic search even
  with web fallback disabled. `GroundingWeb` records academic-provider and
  general-web-fallback configuration separately, reuses the existing client,
  and preserves labeled errors, deadlines and public HTTPS/DNS restrictions.
  General-web fallback remains opt-in; Unpaywall still means DOI-to-OA lookup.
  This fixes missing client wiring, not the observed external DDG route/DNS.
- CC init now displays one complete literal reviewed Host argv and reuses it,
  including model override, pool/UUID/runtime, web fallback and PDF/contact-file
  choices. Its existing actual-argv/STATUS checks cover all those bindings and
  stop only its newly created Host on a pre-run mismatch. The manager already
  transported argv literally; its regression now passes pool and retrieval
  options together, including paths containing spaces. There is no new launch
  file, automatic pool discovery/join, machine-private default or Profile edit.

| Verification | Actual result and retained limits |
| --- | --- |
| Red reproductions | Eight failures / one pass in `red.xml`, demonstrating the original defects before runtime edits. |
| Focused runtime/configuration/Skill/Profile checks | Original `regression.xml`: 268 passed / ten failed. All ten failures were Unix socket paths exceeding the platform limit under the long evidence-directory prefix; the unchanged endpoint file passes all 21 tests under a short temporary path (`socket-recheck.xml`). Another 62 accounting, retrieval, retry, bundle/restart and wait tests pass (`adjacent.xml`). |
| Native real CC/MCP/CPU | Original `native.xml`: two passed / one failed. The failed reader test counted calls before the dispatch tick, including readers correctly accepted before the fault; its assertion was corrected to count calls still live at the actual quarantine boundary. Only that case was rerun: pass in 21.85 seconds (`native-readers-recheck.xml`). Execution fault/retained CPU result, parent/reader fault cleanup, and unaffected three-reader/CPU/report completion all pass the existing Oracle after their explicit completion/cancel paths. |
| Current focused coverage | 343 distinct selected test identities have passing current evidence, zero skips or remaining failures. Original red invocations are not relabeled green. An intermediate spy used a fabricated PGID inconsistent with its Attempt; its Oracle failure is retained in `focused.xml`, and the corrected spy is covered by the larger regression. |
| Static and structure | Ruff F601/F602/F821/F822/F823 and the existing lifecycle/physical-size check pass; workflow_host remains at its existing 4,500-line limit. No validator, Oracle invariant or size allowance was relaxed. |

Cleanup census: 74 readable test databases contain 75 distinct recorded process
groups; none is live. All four native roots, including the failed original
reader assertion, have **45 CLOSED Attempts / 45 absent groups**, no tool socket
or paper-staging residue. Three successful native roots are COMPLETED/CANCELLED;
the original failed reader root remains ACTIVE with all seven Attempts CLOSED
and its processes absent, retaining the failure instead of being silently
settled. All local provider servers and fixture targets were closed/removed by
their test cleanup. Synthetic unfinished states in ordinary unit fixtures are
not presented as completed research runs.

A separate read-only observation at **10:33 +08:00** finds the old real topic
`MERC-causal-202609160508` has become **CANCELLED r40**: 27 Agents and four
Processes CLOSED, all 31 recorded groups absent, previous Host PID 3344509
absent. This change was outside this repair's actions. Its saved runtime stays
unchanged. The earlier paused-state diagnosis remains historical evidence;
changing checkout code does not upgrade or recover that run.

New-topic preparation may use this repaired candidate and the previously
reviewed Profile/bindings. Before formal use, CC must carry the actual reviewed
launch vector, verify the existing pool binding and selected current model/auth,
and distinguish current online retrieval health from these fixture results.
This repair did not execute GPU/G5/online OpenAlex validation; prior GPU/k3
results apply to their documented candidate and binding, not a new DeepSeek
compatibility claim. Full promotion belongs to a candidate freeze, not this
debugging loop. Existing null real-topic USD caps and reader opt-in choices
were not changed, and no old-run recovery is authorized or implemented.

## Latest observation — paused DeepSeek topic, 2026-09-16

Read-only inspection at **09:20 +08:00** identifies the newest topic as
`MERC-causal-202609160508`. The managed Host is alive (PID 3344509), but the run
is **ACTIVE r37 with a quarantined dispatch error**, not doing research. All
26 Agent Attempts and four Process Attempts are CLOSED; none of their 30
recorded process groups exists. There are no resource waits or open human
decisions. All four GPUs are idle. No cancel, restart, recovery, settlement,
cleanup of this run, provider call or code repair was performed by this review.
Evidence is in `.local/qualification/run-analysis-20260916-0914/`.

All 133 saved runtime/resources and `environment.yml` match the checkout bytes
qualified below. This launch nevertheless selects **CC 2.1.273 and
deepseek-v4-flash via api.deepseek.com**, confirmed by actual served-model
observations. The Host selector overrides the settings file's
`ANTHROPIC_MODEL=deepseek-v4-pro`; the previous k3/CC-2.1.272 paid evidence does
not establish compatibility of this different model binding. No secret value
was printed. The existing topic also exposes a runtime defect absent from the
earlier bounded samples; their historical passing results are not erased.

The main findings, in operational priority order:

1. **Run-wide dispatch is blocked by legal repeated command arguments.** At
   06:35:09, the engineering forward chain's third experiment was accepted as
   DRAFTED, then preregistration raised `ValueError: argv must not contain
   duplicates`. Its exact argv includes `--epochs 32 --batch-size 32`, and `2`
   occurs both in the seed list and `--audit-batch 2`. `domain.py:669` applies
   the set-like `_require_text_tuple` validator to argv; `domain.py:66` rejects
   these normal repeated values. The exact persisted argv reproduces that
   error offline. `workflow_host.py:3477` constructs the contract only during
   dispatch, and `workflow_host.py:692` then skips the quarantined run. This is
   a platform bug independent of model quality. The minimal correction is
   argv-specific validation that permits repeated tokens while retaining string,
   command/sandbox and other collection-uniqueness checks; cover actual submit
   through preregistration with this command shape. Do not deduplicate argv or
   globally relax the shared tuple validator. Editing the checkout cannot repair
   this running saved bundle; simply restarting the unchanged run will encounter
   the same pending command.
2. **Quarantine caused billable polling and stalled result publication.** Two
   previously running experiments finished at 06:40:17 and 06:43:46, but remain
   EVALUATING/ACCEPT_OUTCOME with their output workspaces retained. One engineering
   Agent received 189 immediate `blocked` wait replies and hit 240 model turns
   (`error_max_turns`, 16.087055 USD for that entire Attempt). Two other execution
   Agents timed out at 07:17:34 and 07:21:34, each after about 3,001 seconds.
   `host_tools_server.py:483` returns immediately on blocked, while
   `full_cc_runtime.py:589` directs further waiting and line 609 forbids completion
   in a dispatch-blocked state. This needs a focused review of the existing
   wait/Agent exit path, preserving pending experiments and terminal acceptance;
   it is not a reason to invent successful completion or a second scheduler.
3. **Actual startup omitted the selected shared-pool and retrieval bindings.**
   STATUS reports `gpu_pool=null`; argv contains all four GPU UUIDs, but no pool
   socket/namespace. This is the nonpooled path. Three experiments were bound to
   GPU 3 and one to GPU 2; recorded CUDA resource samples prove actual usage
   before the pause. The shared pool is not causing today's idle GPUs.
   Startup also omitted `--web-search-fallback openalex` and the Unpaywall option.
   The background OpenAlex pipeline supplied retained paper material, but the
   native academic-search client was absent: `cli.py:538` injects it only when
   the web fallback flag is set, while `grounding_web.py:297` uses that same
   client for explicit academic scope. The 15 actual searches failed: six
   academic-client-unavailable, five timeout, four unsafe-DNS refusals. No key
   rejection is established. Carry the already selected bindings into the actual
   CC launch and verify returned STATUS; clarify/test the academic-client wiring
   independently of the general-web fallback choice. Do not weaken DNS checks.
4. **A research implementation error was successfully repaired locally.** The
   engineering backward chain's first Process exited 70: all ten trials reported
   CUDA/CPU tensor-device mismatch. Its replacement Process succeeded. This
   experimental-code error is distinct from the platform quarantine above.

Observed scientific output is still negative against the frozen thresholds
MELD >66.74% and IEMOCAP >73.05%, each a five-seed mean:

| Candidate | MELD weighted F1 | IEMOCAP weighted F1 | Durable result status |
| --- | --- | --- | --- |
| ML prefix/speaker-state core | 64.0182% | 62.2875% | Accepted CLEAN_REFUTATION; hypothesis assessed REFUTED |
| ML supervision-geometry core | 64.2557% | 61.6554% | Process successful and parser output present; not yet accepted/published |
| Engineering backward prefix core, repaired | 63.8330% | 66.6460% | Process successful and parser output present; not yet accepted/published |

The latter two values are read from their existing parser outputs, not promoted
to accepted outcomes by this review. The parser reports its validity checks
true; this is not an independent scientific audit of every causal measurement.
The affective expert finished literature and a hypothesis but has no design or
Process Attempt. No cycle report, Observer, Debate, Judge or final report has
executed. The bundled Oracle currently reports two `owned_work_residue` issues
for the uncollected outputs and no live process residue; it does not pass.

There is real selective reading by the parent experts: Bash/sed/grep traces read
method sections, comparisons and limitations from retained `/papers/.../pages.txt`,
and Grounding checks source text in the target's existing paper collection.
Two direct document fetches succeed; one earlier fetch fails schema validation.
The frozen Profile only admits `web_search`/`web_fetch`, so **zero PAPER_READ
Agents and no parallel reader sample** are expected here. Twenty-six archived
native transcripts contain 18 distinct compaction events, unlike the earlier
short canaries; this observes compaction, not the quality of information retained.

Host/CLI known model accounting is **78.614773 USD plus two unknown-cost timed-out
calls**. Per-role known totals are ML 33.867578, engineering 38.863343, affective
5.883852 USD. The known total includes the failed max-turns call. USD invocation,
run and per-expert caps are absent, and token enforcement is observation-only;
unknown calls cannot be assigned zero or a finite USD ceiling. This is separate
from the 35 USD qualification cohort and is not an upstream invoice. No Agent
is currently active. The immediate platform priority is the argv defect and
quarantine polling behavior; another stronger model or idle GPU cannot advance
this quarantined run on its own.

## Current full qualification — 2026-09-16

The requested G0–G5 batch has completed its authorized selections. **The tested
settings-file / pinned-model route is ready for real-topic preparation in Claude
Code, within the restrictions below.** The additional reading run completed;
its postflight checker then failed on a query-plan field. A minimal test-only
correction passes read-only verification of that same completed sample, without
another model call. Both original failures and their bills remain. This is
bounded compatibility evidence, not a claim that every lane or scientific topic
passes. No formal research Start, existing-run recovery/mutation, external
notification, commit or push occurred.
Detailed evidence stays in ignored `.local/qualification/full-20260916/`.

### Candidate and environment

HEAD is `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` **plus the imported and
uncommitted working tree**. HEAD alone does not identify this candidate. At
entry all 133 runtime/resources matched the final selective-reading wheel.
The full G1 invocation kept those bytes fixed, with ACI 20 and runtime identity
`sha256:30d6d241f7dfe52756ad6e58cde93ae8bd8b1442ba1f8d9e6fee2a986e506727`.
Fixing ACI-20 node projection produced
`sha256:7450c3f4711912eb9a38d36b24b0b660b79a21a299c663c3acfd9cb6d4ae1f84`,
used by G4 and the initial four paid samples. The subsequent batch-citation fix changes
only `grounding_background.py` relative to that candidate. Current identity is
`sha256:96d0f3f99006de9f516812a5f3a8d77f082b4c86388c3de836e26177bce9487f`.
These are existing `installed_run_version()` values, not a new identity scheme.
The additional reading run used the final runtime bytes; the subsequent
postflight correction changes tests only. Wheel/install/bundle resources are
compared byte-for-byte; environment identities also reflect environment files,
interpreter paths and explicitly bound runtime inputs, and need not be identical
between a checkout, installed package and configured Host.

Control Python is project Conda 3.12.14 / SQLite 3.53.4; imports resolve to this
checkout or the explicitly tested independent wheel installation. Native CC is
pinned to `/home/gray/.local/share/claude/versions/2.1.272`. Model `k3-256k` uses
the existing mode-0600 `/home/gray/.claude/settings.json` and `api.kimi.com`;
actual paid responses establish authentication beyond preflight. No key was
printed. CUDA uses `/home/gray/miniconda3/envs/erc-cu128`, Python 3.12.13,
Torch 2.8.0+cu128 / CUDA 12.8. Bubblewrap 0.4.0, Chrome 149 and Poppler are
available. user-systemd responds, with `Linger=yes`; its pre-existing failed
units remain untouched.

The shipped runtime, native assets and two public templates contain no old
package imports, `PROMETHEUS_*` variables or machine-private default paths
(`source-path-audit.json`). Public Profiles remain exactly `multi-expert-en`
and `multi-expert-zh`, six files each. Current wheel, independent installation
and fresh CLI/CPU bundle match all 133 resources; the 557-file source archive
includes hooks, operator Skills and current tests, and excludes private Profiles,
settings, state and local evidence. Installed CLI/MCP, CPU closure and Oracle
pass after both corrections (`installed-post-batch.json`).

### Executed evidence and limits

| Stage | Actual result | Evidence and boundary |
| --- | --- | --- |
| G0 | Scoped readiness established, with two existing entry-point restrictions below | Actual environment/imports, native tools, public layout, Profile admission, installed delivery and read-only pool status. No generic assertion that every authentication route works. |
| G1 | Original full invocation: **5,641 passed / five failed / 25 skipped**, 1,295.52 s | `g1.xml`. One actual ACI-20 projection omission and four incomplete old test doubles were corrected; affected selection: 136 passes. Final collection is 5,675 identities, with **5,650 passing evidence / 25 opt-in skips / no missing identities** after the batch and reading-checker corrections. This is evidence reuse plus focused verification, not an all-green original full invocation. |
| G2 | 174/174 selected portable checks pass; current Oracle reads four retained Hypoloom MERC roots | `g2-g3-g1-coverage.json`, `g2-retained.json`. Retained databases are byte-identical before/after, with unchanged logical rows, heads and artifact inventories. Original 0044 bundle still reports its two known archive errors; other original readers pass. There is no historical ACI-20 paper-reader research cohort. |
| G3 | 7/7 selected cases pass in G1; reader completion and End run also execute | Actual Host/broker/native CC/CPU, same-bundle recovery, subsequent actions, debate/Judge delivery, TUI leave/reentry and cancel cleanup. Nine independently read states: 198 CLOSED Attempts, 169 absent recorded groups. After the batch repair, all three native reader cases pass: single-source closure, batch closure and End run; 46 CLOSED Attempts/46 absent groups. Provider/documents in these native checks are local fixtures. |
| G4 | 8/8 checks pass, including both real CUDA cases, 299.02 s | Exact approved GPU 0 + 3, two fresh private pools, two concurrent Hosts. Actual same-card overlap, one capacity wait, measured recalibration, next dispatch and release are observed. Four roots: 72 CLOSED Agents/eight CLOSED Processes, 80 absent groups and two clean pool audits. This does not claim every multi-expert/GPU topology or long soak. |
| G5-local | **PASS**, 355.18 s, 2.351091 USD | 12 CLOSED Agents/one CLOSED CPU Process, clean Oracle and 13 absent groups. Actual native tools, seeded failing unittest then repair, measured 0.875 result and final-report Skill. One original Grounding failure remains: it proposed common acceptance in the explicit-metric case, then corrected it within the existing allowance. |
| G5-common | **PASS**, 356.85 s, 3.049820 USD | 11 Agents/one CPU Process, all CLOSED and successful; 12 absent groups. Grounding authored and tested the common evaluator; the expert read the actual source; shared-goal result/report and Oracle pass. |
| G5-resource | **PASS**, 342.23 s, 2.591910 USD | GPU 3 only, fresh private pool. Actual resource Skill, two resource queries, independent successful wrapper CPU check, accepted estimate, real CUDA, collection, post-wait own measurements and release pass unchanged strict checks. Admitted 2,048 MiB; observed own peak 379,584,512 bytes, including CUDA context. 11 Agents/one Process all successful and CLOSED; 12 absent groups; request RELEASED and pool/Run Oracles pass. The older failed resource sample and its cost remain in the cohort. |
| G5-reading, original | **FAIL / deliberately stopped after diagnosis**, 198.88 s | Four actual readers (two per parent attempt) read the two exact documents and returned method details; both parent summaries were rejected because batch-document URLs were omitted from citation eligibility. The original state is preserved. Known 0.911025 USD plus **1 USD unknown final-report reservation**; no CPU experiment reached. Later evidence does not turn this sample into a pass. |
| G5-reading, approved recheck | **Runtime COMPLETED; original postflight FAIL; corrected read-only verification PASS**, 350.89 s, 2.398124 USD | 13 Agents/one CPU Process all successful and CLOSED; 14 absent groups and clean Oracle. Two actual readers independently read exact original documents, return 0.83 / 5-turn method details, and are collected by the parent's accepted synthesis; then the actual repair, CPU result and final report close. Peak three Agent Attempts; reader intervals overlap 16.44 s. The checker incorrectly expected `summary` on QUERY_PLAN. Its red regression and original exception remain; MAIN-specific checking passes 29 offline cases and the unchanged completed state's archives, with no new paid call (`g5-reading-recheck-postflight.json`). |

G4 UUIDs were `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` and
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`; resource G5 used only the latter.
All CUDA payloads retained the 64 MiB tensor / 256 MiB allocator ceiling,
with context memory counted separately. Existing shared pools were not replaced
or modified. The final citation change is inactive in the already passed G4
and three G5 scenarios; their original identities/results remain recorded and
are reused for those unchanged paths (`post-batch-evidence-scope.json`).
The paid recheck fetched two documents individually, so it does not claim a
second real-model batch-fetch trace. The exact batch failure is covered by the
retained real replies/candidates and the passing native batch scenario. Both
reading samples use synthetic documents and real k3 responses; they establish
tool/reading/collection compatibility, not literature quality or a throughput
benchmark.

Current G1 also passes the ACI-20 three-expert native interaction case: six
Observer calls, six debate statements, one Judge, six real CPU experiments and
the subsequent actions/report. All 90 Attempts are CLOSED and their 90 groups
absent. Native checks verify the complete Judge statement input and bounded
predecessor projection; existing cooldown/coalescing regressions cover interaction
frequency. The G3 selector's separate three-round restart case explicitly uses
ACI 14; its result is not substituted for this ACI-20 evidence. A read-only scan
of current G3, reader, combined-interaction and five paid samples finds 292 native
transcripts / 14,703 parsed events / **zero observed native compaction events**.
Automatic compaction remains unobserved in these bounded samples; historical
topic compactions are separate evidence (`interaction-context-observations.json`).

Current regressions distinguish `DISPATCH PAUSED (Host error)` from resource
waiting and completion, retain its original error and allow peer/control service
and cancellation. TUI leave/reentry preserves the Host and Process; End run
drains the owned work. Success still requires terminal effects and cleanup,
as observed separately in G3, native reading and the paid closure.

The 25 offline skips include additional/legacy paid or GPU probes, real Telegram
and the opt-in tmpfs fault lane. They are not passes. This batch did not execute
unapproved notifications, universal scientific evaluations, or 24/72-hour soaks.

### Findings and minimal corrections

1. **Batch reading citation omission — repaired; scoped verification complete.**
   `grounding_background.py:124` collected only top-level fetch URLs. A real
   `web_fetch(mode=document, sources=[...])` succeeded and real readers returned
   both exact method excerpts, but legitimate parent `web_sources` failed.
   This consumed repeated model calls and prevented the experiment. The existing
   collector now includes only `available` document members; native pairing,
   source uniqueness, failed-member exclusion and the Oracle remain intact.
   `batch-citations-red` preserves the failing reproduction; `batch-citations-green`
   passes 180 cases. Both retained rejected candidates now validate against their
   exact native replies in a read-only check; their stored failures are unchanged.
   Native batch closure passes, followed by both existing reader scenarios.
   The final runtime's paid reading closure is independently verified as above;
   its two individual fetches do not replace the original batch reproduction.
2. **ACI-20 node visibility — repaired.** `expert_loop.py:663` omitted 20 from
   the read-only projection's supported versions, hiding new research-node views.
   Add 20 to that existing set; keep future-version rejection. The existing
   current-version regression reproduced it and the 136-case affected selection
   passes. No scheduler, schema or persisted node-status mirror was added.
3. **Environment-auth provider URL — existing restriction, unresolved.**
   `cli.py:489` and native `manage_host.py:256` forward the API key but omit
   `ANTHROPIC_BASE_URL`. Offline dummy-credential/captured-serve probes reproduce
   both omissions. Minimal follow-up is forwarding the explicitly selected URL
   at both existing boundaries. The approved settings-file route works and does
   not use this affected path.
4. **Public model placeholder admission — existing restriction, unresolved.**
   A copied Profile with `REPLACE_WITH_PINNED_MODEL` is admitted. Reject that
   sentinel through the existing model validator/admission path
   (`claude_agent_host.py:3427`), without adding another model registry or gate.
   Current paid samples use the actual pinned selector, so this finding did not
   affect them.
5. **Reading postflight phase confusion — test-only defect, repaired.**
   `tests/runtime/test_real_current_host_g5.py:523` treated QUERY_PLAN as a
   Literature MAIN response and accessed a nonexistent synthesis `summary`.
   The paid runtime had already completed and passed its main closure checks.
   Select MAIN through the existing request subkind, keeping the exact-source,
   native-Read, collected-attempt and synthesis-detail checks. The negative
   regression still rejects a plan-only sample with no readers. Original
   `g5-reading-recheck-result.json` remains failed; the separate read-only
   postflight result verifies the unchanged state and runtime without relaunch.

These corrections change no existing live run or response. No full qualification
was used as a debugging loop and no failed paid sample was automatically rerun.
The initial empty pytest selection for reading preparation and a NULL-observation
progress-probe error also remain recorded; neither is platform passing evidence.

### Costs, cleanup and real-topic preparation

The same cohort ceiling is **35 USD**. Earlier model usage remains
12.996258 USD, including the original failed resource sample; metered literature
adds 0.009 USD. The three new passing model samples cost 7.992821 USD. The failed
reading sample has 0.911025 USD known and 1 USD reserved unknown. The explicitly
approved additional reading run adds 2.398124 USD, with no unknown new usage.
Together with the approved 0.10 USD OpenAlex reservation, final conservative
accounting is **25.407228 USD**, leaving **9.592772 USD**. This comprises
24.298228 known model USD, 0.009 metered literature and 1.10 reserved unknown.
It is existing Host/CLI accounting, not an upstream invoice. Directory/model
changes do not reset it. The single additional CPU reading run stayed within
4 USD/900 s plus 30 s cleanup, 1 USD/300 s per Agent and three Agent slots.
Its checker repair/replay added no model charge. No further paid retry is
authorized by that approval.

All completed test Hosts, native Agents, Processes and private pool servers have
stopped. Independent G1 liveness scanning covers 889 readable test databases and
1,678 distinct recorded group IDs (including synthetic fixture IDs), none live;
43 intentionally corrupt/old-schema fixtures are separately listed. G3, G4,
G5 and final native checks have their own cleanup censuses. The stopped paid
reading sample preserves **one OPEN durable FINAL_REPORT Attempt with no live
process group** and unknown cost reserved at 1 USD. It is failure evidence,
not an active task or a closed-run pass. Its paper staging and sockets are empty.
Twenty-eight G1-owned and eight paid/preparation fixture roots were removed;
retained test state, bundles, published source archives and logs remain evidence.
The final installed CPU probe also has four CLOSED Attempts/two absent groups.
Existing research roots/data/credentials,
shared services and local Profiles remain unchanged.

Current `profile-check MERC-causal` passes with the explicitly selected OpenAlex
and Unpaywall files. Same-day bounded online OpenAlex and Unpaywall/PDF evidence
is reused after confirming unchanged provider classes, paths and credential
file times (`online-evidence-scope.json`); unknown billing remains unknown.
General DuckDuckGo health and universal full-text access are not established.
A current real-Poppler offline two-page document extraction also passes.

The actual six-file MERC Profile still targets `/data_ssd/Gray/Project/ERC`,
the admitted MELD/IEMOCAP data and erc-cu128; its three experts inherit the Host
model. It has three Agent/Process slots, 3,000-second Agent and 1,800-minute
Process limits, and invocation/total/per-expert USD caps remain `null` as
previously confirmed. **The qualification's 35 USD cap is not a real-topic cap.**
It currently enables `web_search`/`web_fetch`, not `read_papers`. Using the new
reader mechanism requires reviewing that opt-in and capacity: waiting parent
experts still occupy Agent slots, so three parents can leave no reader slot.
The existing blocked/serial-fallback behavior is tested; this is a configuration
constraint, not evidence that more model concurrency has been provisioned.

Read-only status of the existing Prometheus-rooted shared pool succeeds with
UID 1001. The next actual CC initialization must carry the reviewed socket,
independent namespace and formal GPU UUID set in Host arguments. Qualification's
GPU 0 + 3 approval is scoped to these tests. Formal preparation/Start belongs
in the existing `/autoresearch init` review in CC; no additional Codex Start now
instruction is required. Platform validation for the authorized bounded reading
sample is complete with the explicit original failures and coverage limits above.
Using the already confirmed MERC settings does not require reapproval here;
enabling its new optional readers or changing its real-topic limits remains a
Profile choice to review in CC.

## Current development — selective paper reading, 2026-09-16

The user authorized implementing the consolidated reading design. This local
working candidate advances fresh runs to ACI 20 and adds exact document access,
explicit academic search, own-role reading history and optional Host-managed
paper readers. Existing runs, local Profiles, credentials and shared services
are unchanged. No paid provider, public-network retrieval, GPU or notification
is used in this development. HEAD remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`
and does not identify the imported/uncommitted candidate by itself.

Evidence is in ignored `.local/qualification/paper-reading-20260916/`.
`environment.log` records Python 3.12.14, SQLite 3.53.4 and actual checkout import
paths; at entry all 110 runtime Python files matched the preceding repair wheel.
The older G1/G3 results below apply to their earlier bytes, not this new task.
Native checks use installed CC and a local scripted provider with synthetic
credentials; they are tool/topology evidence, not scientific model evaluation.

Implementation uses existing AuxiliaryTask/AgentAttempt records, per-invocation
reservations and expert/run accounting. `/papers` is read-only; source archives
reuse ordinary Agent publications. Published sources are mounted by verified
reference; children reading an unpublished parent retain their own exact copy.
Recovery reads only surviving admitted material and existing native observations.
There is no new paper registry, SQL table, reader daemon or scientific gate.

The implementation and bounded development verification are complete. This is
not a new full G0–G5 promotion. Compared with the preceding repair wheel, 21
runtime/resource files differ (19 Python files, including two additions, and two
existing native Skills); `candidate-changes.json` records the ordinary byte
comparison. Public templates and the user's MERC-causal Profile are unchanged.

Final evidence and its scope:

| Stage | Actual evidence | Result and boundary |
| --- | --- | --- |
| G1, affected regression | `focused-final-2.xml`, `readers-stop-final.xml`, `backup-context-final-2.xml` | 663 passed; the subsequent stop correction passes 11 reader cases, and the subsequent backup correction passes 59 backup/bundle/CLI cases. These overlap and are not added as distinct coverage. No new full G1 invocation is claimed. |
| G1, public admission/package | `profile-check.json`, `package-verification.json`, `installed-delivery.json` | Both isolated bilingual six-file Profiles admit `read_papers` through the actual offline CLI. All 133 runtime/resources (112 Python files) match the wheel, isolated installation and final CPU bundle. CLI Host, MCP stdio/inspection and Oracle pass from outside the checkout. |
| G2, persistence/source reuse | Reader regressions, `paper-backup.json`, backup/bundle regressions | Exact source reuse, child publication before parent, bounded I/O, same-candidate loss handling and surviving-source recovery pass. The completed native sample's backup and offline restore pass the full Oracle; all 12 original/text/index attachments retain exact bytes. No restored Host was started. |
| G3, normal closure | The `cpu-completion` case in `native-4.xml` | Installed CC observed three simultaneous readers and exact fixture document text; the parent collected them, then hypothesis/CPU experiment/report closed. Eighteen Agent Attempts and one Process succeeded; Oracle passes. The other case in this invocation failed, as recorded below. |
| G3, stop/cleanup | `native-5.xml` | One End-run case passed, one normal-completion case was deselected. Parent plus three readers cancel, the final report succeeds, all eight Agent Attempts close and Oracle passes. No Process was started in this cancellation case. |
| G4/G5 | Not executed | No real GPU or paid-provider evaluation for this feature. Local scripted model responses do not establish scientific comprehension, higher-model compatibility, throughput or monetary cost. |

The 663-case selection includes existing capability, memory/session, budget,
literature, interaction and Oracle checks. A selected method/source/page excerpt
reaches the Judge through its ordinary statement input; this tests delivery,
not whether a real model will select or interpret the right passage. Normal
native closure is reused after the separately tested stop-only and backup
corrections; it was not rerun with every final byte. Final installation/bundle
and CPU smoke checks use the completed runtime candidate. Full candidate
promotion and paid scientific comparison remain separate work.
Final documentation/public-Profile/lifecycle checks pass 23 cases
(`docs-delivery.xml`); critical Ruff checks, compilation and imports pass in the
recorded Conda environment. The Host remains within its existing 4,500-line
limit. The source archive includes current tests, guides, hooks and both public
Profiles, while excluding private Profiles, credentials and local test state.

There is no historical ACI-20 paper-reader research cohort. Same-candidate
fixtures and newly created native samples do not establish historical research
compatibility. Existing research runs were not started, resumed, migrated,
settled or modified.

Retained failures and their corrections:

- `source-red`: two reproduced missing-interface failures; `source-green`: 155
  passes covering document extraction, academic scope and existing retrieval.
- `integration-1`: 188 passed / 30 failed / two errors (reader module import
  integration). Follow-up first-error probes retain each import failure;
  `integration-host-2` passes both actual Host literature/CPU cases.
- `integration-2`: 225 passed / two failed (old closed command/capability
  expectations). The expectations now explicitly retain the reader's narrow
  tools; `components-3` passes 307 relevant cases.
- Reader development preserves its original fixture failures and the Host
  acceptance routing failure. `readers-7`: ten passes covering more-than-two
  readers, refill, distribution between experts, exact reuse, bounded I/O,
  duplicate commands, ownership, blocked waits, backoff/budget, parent failure,
  unknown reservations and same-candidate recovery. `lifecycle-5` retains 127
  passes / one two-expert fixture error; that fixture now uses a complete
  multi-component goal/protocol as required by existing admission.
- `native-1`: three native readers received exact source text, then failed
  archival because transport cleanup removed paper inputs too early. The
  implementation now preserves inputs until Host publication and then removes
  them. This original failure remains evidence, not a pass.
- `native-2`: three readers and the CPU experiment completed, but concurrent
  development changed pinned runtime bytes before the final report. The drift
  guard rejected continuation. This attempt remains failed and is not candidate
  qualification.
- `native-3`: two failures. Normal execution completed, but the Oracle's existing
  auxiliary command matrix lacked PAPER_READ. Adding its explicit packet-only
  and parent/source bindings makes the retained sample's read-only recheck pass;
  the original failed invocation remains. The stop fixture used the nonexistent
  `CANCEL_RUN` command and now uses existing `CANCEL` with a cause.
- `native-4`: one passed / one failed. Cancellation appended a new
  REQUEST_AUXILIARY record after STOPPING, violating the existing final-report
  stop boundary. The implementation now uses the accepted run cancellation for
  those readers. The Oracle rule is unchanged; `native-5` passes the affected
  End-run path.
- `paper-backup.log`: the native sample reproduced a pre-existing backup omission:
  database/artifacts were copied without the expert plugin/memory Git repositories
  and request views required by the Oracle. The existing backup/restore path now
  copies those existing run-owned directories and preserves executable modes,
  excluding native work/settings; it adds no manifest, database or cross-version
  recovery. Five initial regressions fail in `backup-context-red`. The first
  implementation retains seven failures: SQLite reader sidecars crossed the
  exact backup-file boundary, and a context parent directory was missing. The
  next invocation retains only that directory failure. Copied standalone
  databases are now read without WAL sidecars and
  parent directories are created through the existing file-copy path. The final
  59-case selection and full native backup/restore audit pass. The original and
  intermediate failure logs remain.
- Early Judge-excerpt and Profile probes used a wrong fixture field/order and
  a full path where CLI requires a Profile component plus `--profiles-root`.
  Corrected probes pass. Initial census/bundle probes also preserve their
  incorrect column/path assumptions; they did not mutate any run.

Cleanup is independently recorded in `native-cleanup-census.json`,
`cleanup-final.json` and installed smoke records. All 86 recorded groups across
the seven native roots are absent; paper staging is empty, admitted temporary
targets are removed, and no owned mounts or sockets remain. The final successful
native cases have 26 CLOSED Agent Attempts
and one CLOSED Process. The earliest failed native sample deliberately retains
one OPEN database Attempt with an absent process group, and aborted run states
remain unchanged as failure evidence. This is not an active Host/Agent and is
not counted as a terminal test pass. Installed CPU probes also close their own
Agents/Processes and remove their control sockets: three installed probes have
12 CLOSED Attempts and six absent groups, for 92 absent recorded groups across
native and installed probes. No existing research Host,
service, pool or data was altered.

The detailed [implementation and limits](architecture.md#selective-paper-reading--2026-09-16)
include opt-in configuration, source bounds and the separation between I/O and
model capacity. G4/G5 are not run for this change. Reading throughput, scientific
quality, visual PDF understanding and account-specific provider limits remain
unmeasured; a fresh bounded paid-model comparison requires confirmed inputs and
must preserve the existing qualification cohort's spend and unknown reservations.

## Current repairs — responsive retrieval and explicit pool binding, 2026-09-16

The user authorized repairing the stopped topic's pool-review and retrieval
findings, then selected Unpaywall for DOI-to-full-text fallback while retaining
OpenAlex academic search. The repair's offline, native CPU and bounded online
checks are complete, with the limitations below. Existing
research runs, Profiles, credentials and the shared pool remain unchanged;
no research Host, paid model or real GPU experiment is started by this repair.
Evidence is in ignored `.local/qualification/retrieval-repairs-20260916/`.
HEAD remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the candidate includes
the imported, uncommitted working tree and these repairs. The six changed
runtime files are `workflow_host.py`, `literature.py`, `grounding_web.py`,
`host_tools_server.py`, `cli.py` and `full_cc_runtime.py`.

### Changes and observed boundaries

- **Pool review:** the native operator Skill now distinguishes GPU-sharing
  permission from actual pool participation. It carries a supplied known
  socket into the next review and does not treat a limited `/tmp` or
  `/run/user` search as proof that no pool exists. Existing Host argv remain
  the binding source; there is no new Profile field, automatic pool selection,
  replacement service or silent override of the latest approved UUID set.
- **Host responsiveness:** literature retrieval and query-coverage admission
  run on one disposable I/O worker, preserving serialization of mutable adapter
  caches/traces. Only the owning Host thread validates and commits results.
  Existing task/Attempt identities reject stale or stopped completions; no
  persistence schema or cross-version recovery mechanism was added. Backup
  remains busy while a read is in flight. Cancellation checks stop subsequent
  requests/retries; a transport already in progress retains its existing timeout.
  Host shutdown cancels owned Agents/Processes before draining that I/O worker.
  A completed model response discarded during stop still enters the existing
  usage fold through `discarded_response`.
- **Search delivery:** approved public IP attempts share one transport time
  window rather than receiving fresh per-address windows. HTTPS hostname and
  peer checks remain. Expired queued MCP calls are rejected before starting
  their read/effects. Already completed reads still reach the ordinary finish
  path: native transcript delivery determines whether the model observed them;
  an undelivered source remains `response_not_observed`. DNS resolution and PDF extraction still have their
  existing behavior; this is not proof of a universal end-to-end hard deadline.
- **Academic fallback:** explicit Host-only `--web-search-fallback openalex`
  reuses the reviewed OpenAlex transport. It defaults off, performs one bounded
  metadata query without retries/PDF downloads, and preserves the DuckDuckGo
  error with `search_scope=academic_literature`. It neither claims general web
  recovery nor sends the key to another provider. Runtime instructions describe
  these distinctions. No existing run's configuration was changed.
- **PDF fallback:** explicit `--literature-pdf-fallback unpaywall` and
  `--unpaywall-email-file` select one DOI-only fallback in the existing literature
  vector. The response DOI must match; up to four HTTPS OA PDF URLs enter the
  existing download/format/Poppler checks. No title search, fuzzy substitution,
  extra fallback chain or OpenAlex-key forwarding occurs. The existing bounded
  HTTP reader is shared with Semantic Scholar. Both selected fallbacks use the
  same 60-second cross-batch 429 cooldown; direct OA downloads remain available.

### Verification and retained failures

- The complete local Conda environment imports this checkout: Python 3.12.14,
  SQLite 3.53.4, actual native Claude Code 2.1.272. Pytest-xdist 3.8.0 was added
  only to this isolated environment for the existing offline selection.
- Preserve initial red results: two Host-thread blocking failures, two
  cross-batch 429 failures and one expired queued-tool failure. The first Host
  regression run also found two next-action/restart timing failures; moving
  completion processing after dispatch restores the existing next-tick boundary,
  and all four selected restart checks pass. The combined focused selection
  records **382 passes** before the later shutdown/accounting boundary tests.
- The additional shutdown/accounting probes both failed in the initial repair:
  waiting for the I/O worker delays cancelling an owned Agent, and discarding a
  completed model response during cancellation loses its response-based usage
  record. These original failures are retained in `close-accounting-red.log`;
  both corrections pass in the final focused selection.
- Full G1, with four pytest workers and live resource/model opt-ins disabled,
  records **5,599 passed / 5 failed / 25 skipped** in 1,271.02 seconds. Preserve
  `g1.xml`/`g1.log`; it was not a green invocation. Failures were: a Host-file
  physical-line limit (4,531 versus 4,500); two request-capture fixtures assuming
  synchronous literature completion; missing late-web observation in the native
  timeout scenario; and an overlong Unix socket path in a native Bash-policy
  fixture beneath the xdist temporary directory. No permission-policy failure
  was established by that last pre-launch socket error.
- The worker read/trace code now lives in the existing `literature` module;
  Host state transitions remain Host-owned and the existing 4,500-line limit is
  unchanged. Fixtures await actual async completion. A subsequent 74-case run
  had **72 passes / two failures** because its injected clock covered the Host
  but not the relocated I/O clock. Both injected clocks now agree; retain this
  intermediate result in `closure-recheck.xml`. No production clock was changed
  to accommodate the test. The late-read fix preserves undelivered evidence;
  the native permission test runs with an explicitly short disposable root.
- Final affected selection: **432/432 passed** (`focused-final.xml`), including
  16 Unpaywall cases, unchanged line/security checks, pool argv round-trip,
  cancellation/accounting, timing, Profile/Skill/docs and a real native Grounding
  selection. Final native lane: **5/5 passed** (`native-final.xml`), covering
  web sources, real client timeout/late source rejection, literature-to-hypothesis
  CPU closure, TUI leave/re-entry/End run and actual Bash deny/allow behavior.
- Current collection is **347 files / 5,646 distinct cases**. Matching the full
  G1 with affected final regressions gives **5,621 identities with passing
  evidence, 25 explicit skips, no missing identities** (`g1-coverage.json`).
  This reuses unaffected coverage and retains every original failure; it is
  not a claim that all final runtime bytes received another full G1 invocation.
  G2 fixture files and all seven existing G3 selector scenarios passed within
  G1. No real GPU or paid-model lane was rerun; older G4/G5 results do not
  promote these changed runtime files. Compile/import, Ruff and dependency
  consistency checks pass in the recorded complete environment.
- One bounded online search at **00:27:29 +08** returned OpenAlex academic
  metadata in **14.611 seconds**, retaining primary `timed_out`. Its result is
  CauAIN (2022), DOI `10.24963/ijcai.2022/628`; full text was not fetched.
  The key was read only from the already selected private file and was not
  logged. Provider billing was **not returned and remains unknown**, not zero;
  this does not reset earlier qualification charges or permit new paid tests.
- Four retained Hypoloom roots pass current-code read-only Oracle audits:
  `202609150044` CANCELLED r108, `202609150849` CANCELLED r63,
  `202609151555` FAILED r9, `202609151803` CANCELLED r60. Their database
  timestamps/sizes, run heads and Attempt counts are unchanged across the reads.
  All **231 Agent and 35 Process Attempts are CLOSED**. Earlier bundled-reader
  results and original failures remain at their original locations. These are
  retained current-format samples, not a historical release compatibility claim.
- The selected contact source is
  `/home/gray/.config/prometheus/unpaywall.mail`, read only. At **01:00:44 +08**,
  one real Unpaywall lookup of that previously found CauAIN DOI returned
  `https://www.ijcai.org/proceedings/2022/0628.pdf`; one bounded fetch and Poppler
  extraction succeeded in **4.649 seconds total**, returning 2,235 characters
  of abstract/conclusion text. This is one working DOI/PDF sample, not universal
  coverage. No model, GPU or research Host was involved. The contact value was
  not logged and its file content/permissions were not changed. No provider bill
  was returned or inferred (`online-unpaywall.json`).
- Read-only `profile-check MERC-causal --profiles-root .../profiles/local` with
  the selected OpenAlex and Unpaywall files **admits with no blockers**. It reports
  configuration-only preflight, not network success. Preserve the initial
  manual CLI error that passed the entire Profile path where a safe component
  was required (`profile-check.err`); the corrected invocation is a separate
  record. No Profile or prospective run was written.
- The wheel, fresh target installation and installed-wheel CPU bundle all match
  **131 runtime files**. Installed CLI/MCP load outside the checkout; the real
  CPU smoke reaches COMPLETED r9, with **two Agent and two Process Attempts
  CLOSED/SUCCEEDED**, one canonical outcome, clean Oracle, Host exit 0 and no
  socket or recorded live group. Its temporary root is removed. The source
  archive contains native assets, bilingual public Profiles and tests, including
  Unpaywall; private state, local Profiles and environment roots are excluded.
- **Design-only follow-up:** [selective paper reading](architecture.md#selective-paper-reading--2026-09-16)
  now distinguishes bounded document I/O, existing expert concurrency and
  optional Host-managed model readers with owner synthesis. Runtime code is
  unchanged by this design. The focused check records **nine passes** in
  **0.19 seconds**: four existing documentation cases and five existing PDF
  concurrency, reference/cache, fallback-bound, factory and deadline cases
  (`parallel-reading-design-check.log`/`.xml` in the same evidence directory).
  This is evidence for current documentation and adapter behavior, not an
  implementation or qualification of document mode or parallel model reading.
  No model, GPU, network retrieval or research run was started for this check.
  A subsequent design revision removes the fixed per-expert two-reader ceiling
  in favor of dynamic admission within existing run capacity, distribution
  across experts and completion-driven refill. Its four documentation and two
  existing capacity/cost-reservation regressions record **six passes in 0.14
  seconds** (`dynamic-reading-design-check.log`/`.xml`). Those accounting checks
  cover existing independent Agent calls; parent/child budget subdivision,
  dynamic reader dispatch and throughput remain unimplemented and untested.
  The consolidated design now uses ordinary per-invocation ceilings and the
  shared expert/run accounting fold; it does not propose parent/child allowance
  subdivision. It joins question-driven follow-up, explicit academic search,
  original method-page access, source/round-memory reuse and optional managed
  readers into four delivery stages. The final documentation-only selection
  records **four passes in 0.03 seconds** (`connected-reading-design-check.log`/
  `.xml`), with local-link/whitespace checks also passing. The earlier nine/six
  case records keep their original scope and are not counted as validation of
  these new interfaces, model behavior or performance. No runtime code, actual
  Profile, run, model, network retrieval or shared resource was changed or
  exercised by the consolidated design work.

### Cleanup and next-run scope

Five final native/CPU state roots were independently reread: **59 Agent + nine
Process Attempts CLOSED**, clean Oracles, **66 distinct recorded groups absent**
and control sockets gone (`native-durable-counts.json`). This includes the
intentionally CANCELLED TUI run; it is not relabeled COMPLETED. The separate
wheel smoke is described above. A separate process scan found no worker bound
to the recorded test roots. Twenty recorded disposable source mounts and three
remaining test roots were removed; the earlier G1 root had already been collected
by pytest. No independent all-Attempt count is claimed for those already removed
G1 fixtures (`cleanup.json`). Research runs and shared services were not altered.

**The repaired platform supports preparing a fresh CC run with the reviewed
academic-search/full-text routes.** General DuckDuckGo search remains unavailable;
neither OpenAlex nor Unpaywall establishes general-web coverage. The next actual
CC initialization must carry the chosen existing pool socket, independent
namespace and latest reviewed GPU UUIDs in its Host argv, along with the selected
literature options, current model authentication and budgets. There is no new
Codex-only Start now requirement. GPU/paid compatibility for this changed
candidate and the next model remains untested here; no scientific success or
unrestricted readiness is claimed.

## Latest observation — stopped Kimi topic, 2026-09-15

**`state/MERC-causal-202609151803` is CANCELLED r60, its managed Host has
exited, and no owned live work remains. It produced four complete scientific
negative results, three failed executions and one operator-cancelled execution;
it did not achieve the MERC goal.** This began as a read-only review after the
user's manual stop. No run was resumed, changed or deleted; no runtime code, Profile,
key or shared service was changed and no new model/GPU/web call was made in
that initial review. The September 15–16 follow-up below adds bounded,
credential-free connectivity probes and an isolated offline reproduction.

### Durable state, actual candidate and cleanup

All times below are September 15, UTC+08:00. Actual creation was **18:28:27**,
not the 18:03 suffix in the root name. The first TUI cancellation at **23:16:24**
was rejected because the form submitted revision 56 after the Run had advanced
to 57. The next cancellation was accepted at **23:18:10** as r58. Outstanding
Process/Agent work closed by **23:18:13**; the final report took two Attempts,
and `FINISH_STOP` reached CANCELLED r60 at **23:27:19**. Thus the run lasted
about 4 h 59 min, with about 9 min 8 s between accepted cancellation and terminal
closure. Most of that closing interval was report generation, not training.

- All **57 Agent Attempts are CLOSED**: 38 SUCCEEDED, 15 FAILED, 3 TIMED_OUT,
  1 CANCELLED. All **8 Process Attempts are CLOSED**: 4 SUCCEEDED, 3 FAILED,
  1 CANCELLED. All eight Processes actually launched; all eight Experiment
  workflows are TERMINAL. No `GoalVerdictRecorded` fact exists.
- The actual served model was **`kimi-for-coding`** in all 57 diagnostic
  records; native transcripts report Claude Code **2.1.272**. The Host imported
  its saved bundle with local Conda Python; experiments used
  `/home/gray/miniconda3/envs/erc-cu128` and target commit
  `b4590893f756b9f09e2ff2bcbdcf1b784322801c`. All **131 bundled runtime files**
  byte-matched the checkout at that read-only review, including the previous
  repairs; the six September 16 runtime changes above now differ. Git HEAD
  remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the uncommitted worktree
  still defines the candidate. This is distinct from the earlier 15:55 run that
  failed before experiments following the user's reported API exhaustion.
- The run's own bundled Oracle, imported outside the checkout, **passes with
  no issues**: eight valid publications, 57 archived transcripts, zero open
  Attempts/decisions, pending effects, nonterminal experiments, duplicate effects
  or owned process residue. An initial manual probe supplied the state root
  instead of its `artifacts/` child and failed reader path checks. Its result is
  retained as `oracle-wrong-artifact-root-probe.json`; the correctly addressed
  audit is `oracle-bundle.json`. The probe is not a runtime failure or a pass.
- At **23:38:48**, all **65 distinct recorded process groups were absent**, a
  separate process scan found no process bound to the run root, `control.sock`
  was absent, disposable `artifacts/work/` was empty and port 8766 had no
  listener. The exact derived managed service and scope were inactive/not
  loaded, with no queued job; service MainPID was 0. Two remaining GPU processes
  belonged to UID 1023 and were not operated on. Retained bundles, Agent
  repositories, reports, logs and checkpoints are research history, not live
  residue.

### Scientific work and resource behavior

These are the four canonical CLEAN_REFUTATION measurements, shown as weighted
F1 percentages. Each complete publication retains ten checkpoint files and
five-seed results for both datasets. The shared parser reported all eight
validity checks true and maximum future-perturbation difference zero. This
review did not independently rerun those models/checkpoint audits or test the
new generated parser with counterexamples; structural Oracle success does not
certify their scientific implementation.

| Expert / experiment | Device and Process duration | MELD | IEMOCAP |
|---|---|---:|---:|
| ML, experiment 1 | CPU, 38 min 49 s | 63.2148% | 65.8232% |
| Affective, experiment 1 | GPU 1, 27 min 59 s | 63.8767% | 63.1090% |
| Affective, experiment 2 | GPU 1, 52 min 53 s | 63.6153% | 63.1795% |
| Engineering, experiment 4 | CPU, 87 min 24 s | 64.1180% | 53.4999% |

No row meets the required pair of thresholds, MELD **>66.74%** and
IEMOCAP **>73.05%**. The best values from different rows cannot be combined
into a successful candidate. Affective completed one cycle and its two-result
hypothesis was assessed REFUTED. ML's second experiment ran on CPU for
**2 h 32 min 18 s** before cancellation and retained three partial checkpoint
files; it is not a fifth complete measurement. Engineering repaired three
roughly 4–5-second startup failures before experiment 4 completed; its next
design finished, but cancellation interrupted the next execution Agent before
another Experiment was registered. This is a partial first-cycle exploration,
not evidence that stronger models or longer runs cannot improve the methods.

Actual Host argv, recovered from its systemd journal, names all four GPU UUIDs
and has **no `--gpu-pool-socket`, `--gpu-pool-namespace` or pool UID argument**.
The initial RUN record has no pool binding and uses research contract v10.
The earlier Codex-session shared-pool/two-GPU setup was not used. **The follow-up
recovered the actual CC approval: all four GPUs and nonpooled were explicitly
selected in that later session.** The launch matches that approval; missing
flags are not evidence that the helper dropped a reviewed binding. The two
actual GPU Processes used
only **`GPU-e96b09e9-0bcc-d031-acce-bb705dabfa31`** (GPU 1); the other six
Processes received CPU grants.

The reasons for CPU selection differ. ML explicitly submitted **`gpus: 0`**
twice. Engineering submitted AUTO without an explicit count. Its frozen
execution capabilities marked GPU 1 busy; GPU 0 had **11,751 MiB** free,
below the configured **8,192 + 4,096 = 12,288 MiB** admission requirement,
and the other GPUs had less. Existing nonpooled AUTO selection therefore fell
back to CPU. The later Agent snapshot at 23:16 saw GPU 0 with 24,120 MiB free;
that does not retroactively change an already committed CPU grant. These
observations do not demonstrate a shared-pool queue failing to use an idle
authorized card: this Host was not participating in the pool.

### Concrete problems and bounded follow-up

1. **Cross-session pool intent and actual CC approval must be distinguished.**
   Comparing only the earlier pool discussion with `host-journal-commands.json`
   and RUN r1 omitted the later explicit nonpooled choice. The follow-up below
   corrects that inference using `operator-binding-review.json`. For a future
   pooled run, carry the existing socket, independent namespace and selected
   UUIDs into the actual CC review and reuse the managed argv/stored-binding
   display. No launcher forwarding failure was found. Separately review whether
   these training submissions
   should request GPU capacity instead of explicitly selecting CPU or accepting
   AUTO fallback. Do not silently replace the pool, mutate an existing grant,
   reduce the confirmed memory reserve or force every scientific workload onto
   GPU. Relevant implementation: `workflow_host.py:_submission_execution_plan`,
   `compute_resources.py:LocalComputeResources.select`, and the operator Skill.
2. **Long Process waiting still outlives the execution Agent.** Three Agents
   expired at approximately **3,002 seconds**, while the Process deadline was
   108,000 seconds. Affective's second Process continued to successful collection
   after its Agent timed out; Engineering's long Process also completed before
   a subsequent Agent collected/reported it. ML's second Process continued until
   manual cancellation. Thus Agent timeout is not the experiment's terminal
   result. The earlier elapsed repair is observed in five real terminal MCP
   replies: each elapsed value equals stored Process end minus start, including
   all three short failures. Repeated waiting still consumes model sessions;
   any Host-managed model-free waiting redesign remains a separate lifecycle
   change, rather than silently changing deadlines or retries.
3. **Debate paused experts for over an hour without starting a Debate Agent.**
   At **22:10:49**, the accepted OBSERVER proposal selected ML and Affective
   to discuss speaker-state memory; r52/r53 paused those exact participants.
   `DebateHost._drained` / `drive` waits for their existing Attempts and
   Experiment workflows to finish. ML's long CPU experiment remained active
   until 23:18, so the proposal produced **zero Debate statements, zero Judge
   Attempts and zero Leader Attempts**. Engineering continued working during
   that interval. This is an observed drain wait, not proof of a deadlock or
   completed peer review. The smallest next change to assess is deferring the
   pause/proposal until participants reach a suitable boundary, while retaining
   single ownership and safe draining; do not arbitrarily kill training to
   accelerate a debate. Native archives contain 23 distinct compaction UUIDs;
   their presence does not establish context quality or completed interaction.
4. **Generated experiments and responses still need correction.** Engineering
   experiment 1 searches for an original-path suffix in the aliased
   `HYPOLOOM_READ_ROOTS`; experiment 2 treats the code-only candidate checkout
   as the data checkout; experiment 3 looks for an untracked cache beneath that
   code checkout. Published stderr gives the exact missing paths. Experiment 4
   resolves the authorized data alias and runs. Reuse the documented
   `HYPOLOOM_READ_ROOT_MAP` for data and `HYPOLOOM_TARGET_ROOT` for candidate
   imports, with a small real sandbox training/data probe. Do not widen mounts.
   There were also **10 rejected native submit calls alongside eight successful
   registrations**, four malformed structured hypothesis outputs, numeric
   measurement-shape errors and three rejected adjudications. The enhanced
   exact-reference feedback was actually delivered on the third adjudication
   failure; its fourth Attempt succeeded. These are narrower generated-output
   problems, not justification for a new generic validation framework.
5. **The accepted final report is not factually reliable in every sentence.**
   Its table lists the correct four complete negative results, but the prose
   says five; it incorrectly says ML completed a successful repair after its
   failure, although its cancelled second experiment was its last one. It also
   says “3 process / 8 expert” Attempts where SQLite records 8 / 57, and treats
   an empty `final_failures` collection as absence of all terminal failures.
   The first report was rejected with all eight missing outcome IDs named;
   the second included those references and was accepted. Existing
   `final_report.py:run_outcome_context` and `validate_final_report` validate
   context/required references, not every free-text statement. Keep this report
   unchanged as evidence. A minimal future improvement should reuse the existing
   computed counts in its factual table and exercise these exact contradictions
   in focused report tests/instructions, not add another source of run truth.
6. **External retrieval and cancellation have visible limitations.** The 10
   distinct native `web_search` calls all returned transport errors/timeouts;
   two of 17 `web_fetch` calls failed (429 and timeout). Literature attempts
   also encountered unavailable PDFs/rate-limited fallbacks and missing
   requested strata. The follow-up below separates direct network failure,
   PDF fallback limits and a reproduced Host-thread stall; these are not
   evidence of model-key exhaustion. The initial stale TUI CANCEL was safely
   rejected by the existing revision check (`end_run_tui.py`,
   `workflow_host.py:_cancel_run`); a subsequent reviewed request succeeded.
   Preserve this concurrency behavior and make any future usability correction
   reuse the existing conflict/refresh path, rather than remove revision checks.

### Follow-up — pool approval and retrieval, September 15–16

**The pool discrepancy is an operator-context/review issue, not a reproduced
argument-forwarding bug. Retrieval combines a reproducible platform stall with
independent external transport and PDF-availability failures. No repair was
implemented in this follow-up.** Existing runs, bundles, Profile, credentials
and shared services remain unchanged.

The actual operator transcript is
`/home/gray/.claude/projects/-data-Gray-Project-Hypoloom/7b59c74c-016b-4548-9be3-af8869a93774.jsonl`.
On September 15, CC recorded these answers (UTC+08): **18:20:56**, all four
GPUs and sharing of occupied cards; **18:22:56**, “Nonpooled, as before
(Recommended)”; **18:27:18**, Start now after a final review explicitly saying
no pool socket/namespace. Its discovery command searched only `/run/user/UID`
and `/tmp`, at depth two. That search did not cover the previously confirmed
`/data/Gray/Project/Prometheus/.local/gpu-pools/shared-3090x4/control.sock`.
The `manage_host.py start` input already lacked pool flags before the helper
ran. Sharing an occupied device and participating in the coordinating pool
were separate questions; the first answer does not imply the second.
The pool socket's Prometheus directory is the previously authorized external
service location, not evidence of a hidden legacy import or a need to replace
that service. A pool binding also does not turn explicit `gpus: 0` submissions
or existing CPU grants into GPU work.

`CLAUDE.md` separates Codex maintenance memory from CC runtime context. The
six-file Profile has no pool socket/namespace fields; these are explicit Host
`serve` options. The current Skill requires preservation of a pool binding
confirmed **in that session**. `_launch_service` forwards `*host_argv` unchanged,
and the existing managed-launcher literal-argument regression covers that
path. Thus strengthening the helper again would not address this observed
choice. The smallest operator improvement is to supply the known binding to
CC's next review and avoid recommending nonpooled based on a search that never
covered the known endpoint. Do not auto-bind a pool or silently restore an
older GPU subset over a later explicit selection.

| Retrieval path | Observed evidence | Interpretation |
|---|---|---|
| Native `web_search` → DuckDuckGo HTML | All 10 distinct calls failed. Each retained Host exception chain ends in `socket.connect` / `OSError: [Errno 101] Network is unreachable`; eight errors reached the Agent, two Agent replies timed out first. | Failure before an HTTP search response. This path is credential-free and does not use the OpenAlex or model key. |
| Native `web_fetch` → supplied URL | 15/17 succeeded, including arxiv.org, ACL Anthology and GitHub. Both failures used export.arxiv.org: HTTP 429 and a read timeout. | Selected sites work; this is not total Internet unavailability. A different arxiv.org fetch later succeeded. |
| OpenAlex metadata → literature packets | Four packets contain 32/32/29/32 entries: 125 entries, 102 distinct bibliographic identities. No OpenAlex authentication failure is observed. | The metadata route using the configured OpenAlex key worked during this run. Missing requested time strata in earlier narrow queries are coverage errors, not key failures. No new live-key test was made here. |
| Full PDFs and DOI fallback | 24/125 packet entries have retrieved text: 15 direct retrievals and nine cache hits; 101 are PDF_UNAVAILABLE. The unauthenticated Semantic Scholar fallback received eight actual HTTP 429 responses, two per batch; 88 later fallback entries were locally skipped after rate limiting. | Do not count skipped entries as 88 more HTTP requests, or metadata as full-text retrieval. Direct PDFs also encountered absent OA URLs, 403/406 responses and validation/extraction failures. The OpenAlex key is not used by the Semantic Scholar locator. |

The platform defect is in `workflow_host.py:_expert_agent_step`: synchronous
`self.literature_io.retrieve(query)` runs on the same thread that services
Agent MCP endpoints, polls Processes, reconciles the pool and handles control
requests. Query coverage admission also performs synchronous literature I/O
in `_accept_expert_packet`. Existing PDF workers operate inside that blocking
call; they do not free the Host loop. In contrast, native web reads already
use `WebToolOperation` to run network I/O outside that thread, with authorization
and completion serviced by the Host.

The run's four recorded prefetch durations are **102.87, 12.32, 64.83 and
49.08 seconds**. Engineering's prefetch occupied approximately
**18:52:21.869–18:53:26.703**, followed by Affective's at
**18:53:29.432–18:54:18.512**. ML emitted two web-search calls at **18:52:39**;
its first timed out after 30.016 seconds and the second reply arrived 60.025
seconds after emission because the MCP stdio bridge services calls serially.
That second value is not one 60-second HTTP request. Host errors and two
`BrokenPipeError` response writes arrived only at **18:54:36**. The direct
DuckDuckGo failure is real, while delayed Host servicing independently explains
why two callers timed out before receiving that failure. Delayed control,
collection and pool reconciliation under the same conditions are code-level
consequences; this review does not claim an observed cancellation during those
particular prefetch windows.

A fresh offline diagnostic reuses `WorkflowHost`, `AgentToolServer`, the
existing fake catalog and admitted fixture Profile. An immediate web fixture
responded in **0.0047 seconds** with normal owner-thread servicing. Injecting
**0.5 seconds** into literature retrieval made the actual Host tick take
**0.543 seconds**; an otherwise identical socket call with a deliberately
shortened **0.2-second** deadline timed out and its late reply raised
`BrokenPipeError`. This reproduces the servicing defect without external
network failure. It is not native CC, scientific validation or full G3 closure.
Two fake Agent Attempts were CLOSED; no Process was created. All client/server
threads, sockets, handles and the temporary root were cleaned up. Existing
`test_web_tool_delivery.py` covers a slow web read, but not simultaneous slow
literature prefetch; this is the missing regression boundary.

Bounded credential-free checks at **23:55 on September 15** independently
reproduced direct DuckDuckGo failure using the current Conda process and the
existing `PublicHttpsTransport`: resolved IPv4 timed out, IPv6 returned errno
101; arxiv.org and ACL Anthology TCP connections succeeded. Historical DNS and
the specific network/DNS fault are not established by this current probe.
`PublicHttpsTransport` uses `http.client.HTTPSConnection` directly and does not
read HTTP(S)_PROXY. The current shell has proxy variables pointing to a
loopback service at port 7891, but an unauthenticated proxy request and a
separate SOCKS5 greeting also timed out around midnight. The first curl probe
additionally used an unsupported JSON statistics formatter; preserve its
measurement error and timeout, not a successful proxy verdict. No proxy/service
configuration was changed and no working alternate route was established.

Prioritized minimal follow-up:

1. Move the reproduced literature I/O off the Host's servicing thread, reusing
   the existing worker-read / owner-thread-completion pattern. Preserve owner
   checks, cancellation, retries, budgets and late-result rejection. The
   adapter's mutable per-batch traces/cache handling must not be shared across
   unsynchronized concurrent retrievals; simply wrapping each call in a new
   thread is insufficient. Add the slow-prefetch/concurrent-tool regression to
   the existing tests rather than a new qualification mechanism.
2. Establish an actually working bounded search route. Merely changing keys,
   raising retry counts, disabling IPv6 or setting proxy environment variables
   does not fix the reproduced direct IPv4 failure. Any explicit proxy/backend
   support must preserve URL, credential, peer and response bounds. Reuse the
   existing deadline machinery to bound queue plus network plus delivery;
   increasing the MCP timeout alone leaves the Host stall intact.
3. For the next pooled topic, review the known endpoint and actual `serve`
   arguments in CC; reuse the stored binding display. PDF fallback cooldown
   across batches is a lower-priority improvement to assess with the existing
   rate-limit diagnostics. Keep metadata-only entries honest and preserve the
   current per-batch stop-on-429 behavior.

Additional evidence in the same ignored review directory:
`operator-binding-review.json`, `retrieval-exception-chains.json`,
`literature-batches.json`, `web-call-timeline.json`,
`retrieval-prefetch-windows.json`, `retrieval-network-probe.json`,
`retrieval-proxy-probe.json`, `retrieval-proxy-protocol-probe.json`, and
`prefetch-blocking-probe.py` / `.json`. No new paid model, OpenAlex-key or GPU
test was run; these diagnostics do not promote a G4/G5 lane.

### Spend and retained evidence

The existing `fold_agent_attempts` / `usage_projection` reports **68.139247 USD
known model cost plus four unknown calls** (the three timeouts and one cancelled
Agent). **14.745211 USD** of the known amount belongs to failed Agent Attempts;
the two final-report Attempts together report **1.822911 USD**. These are
provider-reported amounts, not an invoice total; unknown calls are not free.
This research run has no invocation USD cap or cumulative USD cap in its frozen
contract and uses token `observe` mode. It is separate from the earlier 20 USD
qualification cohort; cancellation was an operator action, not a recorded
budget-exhaustion termination.

Evidence is retained under ignored
`.local/qualification/merc-1803-review-20260915/`: `snapshot.json`,
`attempts.json`, `research-facts.json`, `oracle-bundle.json`,
`managed-host-and-bundle.json`, `host-journal-commands.json`,
`execution-capabilities.json`, `experiment-grants.json`, `published-results.json`,
`validation-errors.json`, `native-summary.json`, `native-tool-results.json`,
`terminal-elapsed-observations.json`, `usage.json`, `cost-summary.json`,
`final-report.json` and `cleanup-final.json`. Native tool counts above are
deduplicated by existing tool-use IDs across resumed transcript archives;
raw line/occurrence counts are not added as new calls. The initial snapshot's
empty `configuration` field reflects an incomplete extraction; use
`configuration.json`, RUN r1, journal argv and the actual grants for binding
evidence. The accepted original
report remains at
`state/MERC-causal-202609151803/artifacts/MERC-causal-202609151803/agent/agent-de4edb873a4047d5a6cd3cc9fb1e7a4b-2/transcript/FINAL_REPORT.md`.
This review adds actual post-repair CPU/GPU/model observations within this
topic's scope; it does not promote an unexercised shared-pool G4 lane or replace
the public G0–G5 qualification selections.

## Current repairs — dynamic resources and truthful observations, 2026-09-15

**The bounded framework repairs and current offline/CPU verification are
complete. A fresh, bounded higher-model test can now be prepared in Claude
Code with the actual reviewed pool binding. Scientific acceptance quality,
real GPU/model behavior of these changed instructions and a long unattended
topic are not certified by this repair batch.**

The user authorized repairs after the two-topic review, with framework changes
limited to model-independent defects or consequential, reusable improvements.
The runtime changes below use existing SQL facts, pool dispatch, validators and
native instructions. They do not encode MERC thresholds, introduce another
scheduler or change the confirmed Profile. Existing topics, their bundles and
the shared pool remain read-only. This repair batch makes no real GPU or paid
model calls and does not start a research topic.

### Implemented scope

| Reproduced problem | Current repair and evidence |
|---|---|
| Completed experiment duration keeps growing and contaminates reports/SELF_EVOLVE | `_experiment_tool_view` stops elapsed time at the recorded last Process end when all its Attempts are CLOSED; exposes Process start/end timestamps. The focused test advances the observation clock by 60 and 3,600 seconds and observes the same duration. |
| MCP bounded wait changes queued work to `running`, hiding capacity waits | Preserve the actual returned status and expose the existing Host `resource_wait` reason to the owning Agent. No Process means elapsed zero; blocked/terminal semantics and ownership checks remain intact. |
| Pool capacity changes after selection | Reuse existing reserve/activate/release and alternate-device selection. A new regression changes capacity twice between reservation and activation: A → B → A, two pre-exec LOST Attempts, exactly one execution and one canonical outcome, all pool requests RELEASED, both Oracles clean. Existing wait/recovery, same-card overlap and repeated pre-exec loss cases also pass. Observations and execution are simulated in these pool tests; this is not real G4. |
| Reviewed pool parameters were absent from actual research Hosts | Strengthen the existing operator Skill's transport and pre-start inspection of actual `serve` argv. STATUS and the monitor now show the binding from the existing RUN creation transition. The existing managed-launcher argument round-trip test passes; no new launcher, default pool, automatic binding or configuration authority was added. |
| Repeated adjudication/final-report failures give generic reference errors | Existing validators now report exact missing outcome/reference IDs and inadmissible IDs. Native instructions distinguish `outcome_id` from fact/artifact IDs and explain single-chain keep/prune behavior. Accepted reference domains and complete-partition rules are unchanged. |
| Failed Grounding archives and active Process workspaces confuse Oracle | Session Oracle accepts only the already published, bounded evaluator files for the reproduced invalid-provider-envelope failure with no response. Publication identity, digest, file tree and transcript checks remain. Manager Oracle restricts Agent-consumer lookup to Agents; workspace residue checking applies after closure. Negative archive and CLOSED-residue tests still reject corruption. |
| Generated acceptance/training/SELF_EVOLVE mistakes | Improve existing Grounding and expert Skill instructions: isolate each mandatory validity violation with otherwise qualifying evidence, derive checks from raw records, run small admitted training/validation and checkpoint/audit probes, and verify learning premises against original Process facts. GPU probes remain Host-owned and authorized. No topic-specific parser or evaluator in a retained run was modified. Prompt improvements are not evidence that a model now follows them. |

The dynamic-capacity correction depends on actually launching the new Host
with the reviewed existing pool socket, independent namespace and allowed
UUIDs. Nonpooled grants remain fixed as designed; an idle alternate GPU alone
does not authorize rebinding. Pool activation rechecks current capacity, but
cannot prevent an unrelated external workload from growing after activation.
Existing failure/retry and cleanup rules still apply.

### Candidate and verification

Git HEAD remains `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the candidate is the
uncommitted working tree, not that initial-import commit alone. Twelve files
under `hypoloom/` now differ from the retained topic bundles, alongside operator
Skill, focused test and documentation edits. The repair uses the complete local
Conda environment (`.conda/envs/hypoloom/bin/python`, Python 3.12.14,
SQLite 3.53.4) and imports this checkout. The locally resolved Claude Code is
now 2.1.272; the earlier topics used 2.1.270/2.1.271. Ordinary Git comparison and pytest
logs are retained in ignored `.local/qualification/merc-repairs-20260915/`.

- Initial `red.xml`: seven intended old-code failures and one erroneous new
  manager-test fixture (`StopIteration`); do not count the fixture error as a
  reproduced runtime defect. `focused-1.xml` then records 170 passes and that
  one fixture failure. Its correction is included in subsequent passing tests;
  neither original result is overwritten.
- `dynamic-pool.xml`: 10 passes, including the corrected manager test and all
  shared-pool closure regressions. `focused-2.xml`: 133 passes across dispatch,
  native prompts, monitor, managed launch and documentation. Counts overlap
  subsequent qualification and must not be added to it.
- Current-code, read-only Oracle audits of both retained Hypoloom topics pass
  with no issues (`current-oracle-first.json`, `current-oracle-second.json`).
  The first topic's original bundled Oracle still has its recorded two failures;
  the new reader result does not rewrite that evidence or authorize recovery.
- A newly built wheel contains all 131 current runtime files, byte-for-byte;
  the source archive contains all 544 checked runtime/source/guide files and
  excludes private state, local Profiles and environment roots. CLI/MCP entry
  points load from a fresh isolated wheel installation using the complete Conda
  interpreter. A fresh installed-wheel CPU demo reaches COMPLETED with clean
  Oracle, zero OPEN Attempts and no owned process residue; its Host exits and
  its temporary state directory is removed (`wheel-cpu.json`, `wheel-cpu.log`).
  This sample uses the fake Agent and proves package/CPU plumbing, not model
  behavior or Claude authentication. The final source archive is rebuilt after
  the verification guide and memory updates.
- Full current-candidate G1 coverage is complete. The initial serial invocation retains
  631 completed passes and an explicit KeyboardInterrupt after 914.70 seconds;
  it is not an overall pass. The unfinished leader scenario's 36 recorded
  process groups are absent after fixture cleanup. Its unfinished JUnit element
  has no test identity and is not counted. Following the earlier eight-group
  pytest practice, the remaining 4,989 distinct cases ran in eight disjoint
  groups, including that unfinished scenario. Collection is 5,620 cases;
  `g1.xml`/`g1.log` preserve the partial invocation, and `g1-remaining-*.xml`
  preserve the continuation. Their combined first result is **5,584 passed,
  11 failed, 25 skipped**. All eleven failures share the old status-test double
  in `test_shared_acceptance_observation.py`, which allowed only the dialogue
  query and rejected the new stored-binding query. Updating this test double
  preserves its read-only/reused-evidence assertions. The affected module then
  passes **11/11** (`observation-recheck.xml`); runtime bytes did not change
  during or after G1. The original failures remain recorded.
- Final evidence covers **346 files / 5,620 distinct cases: 5,595 with passing
  evidence and 25 explicit opt-in skips**, with no missing or double-counted
  identities (`g1-coverage.json`). This is the full selection plus its targeted
  test-fixture correction, not a claim that the first full selection was green.
  Existing G2 portable persistence/migration/backup/bundle selections pass
  **169/169**, supplemented by both actual retained-topic reader audits above.
  The seven selected G3 cases all pass, including real CLI/local provider,
  broker, CPU Process, publication, same-bundle recovery, next action,
  TUI leave/re-entry and End run. Responses/literature are deterministic local
  fixtures; these are not paid scientific-model results.
- Five G3 states were additionally read before pytest removed temporary roots:
  **150 CLOSED Attempts / 150 absent recorded process groups**, with clean
  current Oracles (`g3-durable-counts.json`). The other two cases retain their
  JUnit and existing teardown assertions; no extra durable-row count is claimed
  for their already removed fixtures. The installed-wheel CPU sample is
  additional. Earlier 5,581 passes and historical G4/G5 results remain scoped
  to their earlier runtime bytes, not substituted for this verification.
- A manual G3 artifact-reader probe incorrectly supplied a relative artifact
  root and failed path/identity checks (`g3-relative-path-probe.json`). Both
  original and retained-copy reads pass when given their absolute paths
  (`g3-serial-reader-locations.json`). Preserve the probe failure separately;
  it is not a passing test, a runtime regression or a recovery operation.

At final cleanup all eight pytest subprocesses have exited and a separate
process scan finds no Host/Agent/worker bound to this batch's temporary roots.
The dynamic-pool fixture, installed-wheel CPU state and remaining G1 temporary
roots were removed; retained logs and selected fixture evidence stay under the
ignored batch directory (`cleanup.json`). A fresh read at **15:20:54 +08:00**
still finds the two real topics CANCELLED at r108/r63, zero OPEN Attempts,
all 193 recorded process groups absent and both control sockets gone
(`retained-liveness.json`). No real shared-pool service was changed.

A Host-managed, model-free waiting redesign remains a separate lifecycle
change. This repair makes waiting truthful without changing the independent
Agent/Process deadlines, increasing retries or silently editing topic budgets.
The generated MERC parser's earlier counterexamples and second parser's seven
CPU cases remain evidence about those retained evaluators, not a universal
scientific validator. A subsequent higher-model comparison must test the new
instructions and generated artifacts through the real bound Host under explicit
resource and cost limits. No real G4/G5 pass is claimed for these changed bytes.

## Earlier synthesis — two completed Hypoloom topics, 2026-09-15

**Both Hypoloom topics are now CANCELLED and their Hosts have exited. Neither
achieved the MERC scientific goal. The platform can execute research, but these
runs expose acceptance, launch-binding and observation defects that should be
resolved before another long unattended topic. A stronger model is a subsequent
bounded comparison, not a substitute for those repairs.** This review changes
documentation only; it does not repair runtime code, operate a retained run,
launch research or make new GPU/model calls.

This section compares `state/MERC-causal-202609150044` and
`state/MERC-causal-202609150849`. The older Prometheus-versus-Hypoloom comparison
below remains historical; its counts are not either of this pair's totals.
All times here are September 15, **UTC+08:00**.

### Final facts and evidence scope

| Observation | First Hypoloom topic, `202609150044` | Second Hypoloom topic, `202609150849` |
|---|---|---|
| Actual provider-reported model / Claude CLI | `mimo-v2.5` / 2.1.270 | `glm-5.3-flash` / 2.1.271 |
| Created / operator CANCEL / terminal | 00:59:01 / 08:22:02 / 08:32:15 | 09:02:21 / 12:52:31 / 12:58:42 |
| Final durable state | CANCELLED r108 | CANCELLED r63 |
| Agent Attempts, all CLOSED | 112: 64 SUCCEEDED, 38 FAILED, 8 TIMED_OUT, 2 CANCELLED | 55: 42 SUCCEEDED, 9 FAILED, 2 TIMED_OUT, 2 CANCELLED |
| Process Attempts, all CLOSED | 21: 9 SUCCEEDED, 11 FAILED, 1 CANCELLED | 6: 4 SUCCEEDED, 1 FAILED, 1 CANCELLED before spawn; only five actual launches |
| Canonical outcomes | 8 CLEAN_REFUTATION, 13 EXECUTION_FAILURE | 4 CLEAN_REFUTATION, 2 EXECUTION_FAILURE; one represents cancellation before spawn |
| Accepted scientific success / goal verdict | None / none | None / none |
| Reported known Agent cost | 151.916606 USD **plus 12 unknown calls** | 84.265526 USD **plus 4 unknown calls** |
| Known cost on FAILED Agent Attempts | 69.547501 USD | 10.262597 USD |
| Final report | Two rejected attempts, third accepted; 3.370622 USD known | First attempt accepted; 1.281467 USD known |
| Distinct native compactions | 57 UUIDs across 112 retained transcripts | 21 UUIDs across 55 retained transcripts |
| Retained-bundle Oracle | FAIL: two `session_archive_invalid` issues | PASS: no issues |

Total known reported model spend is **236.182132 USD plus 16 unknown calls**;
this is not an invoice total and is separate from the earlier 20 USD
qualification cohort. Unknown timed-out/cancelled/failed calls are not free.
The two models ran for different durations, with different generated evaluators,
experiment implementations and CLI versions. Their success fractions or F1
values do not constitute a controlled model ranking.

At the final liveness inspection **12:59:36**, each run's 131 bundled runtime
files byte-matched the then-current `hypoloom/` tree, before the repairs above. Git HEAD remained
`6bd5bb910c8b9385f828f530cf634f3bbd19bbda`; the imported package, tests and docs
are still uncommitted/untracked, with tracked README/.gitignore changes. HEAD
alone is not this candidate. Inspection used the complete local Conda Python
and SQLite 3.53.4. Both actual topic Hosts selected all four local GPU UUIDs
without shared-pool flags, differing from the earlier reviewed two-GPU/pool
preparation. Pool liveness or the isolated preparation test did not bind these
Hosts. The initiating Claude Code approval conversations were not audited here;
this observation does not infer who omitted arguments or a separate permission
violation.

### Scientific progress and reliability

The first topic's engineering experiment 7 recorded IEMOCAP **0.67415719** and
MELD **0.65141933**, still below the strict five-seed targets **>0.7305** and
**>0.6674**. Its generated common evaluator admitted all four previously
reproduced invalid inputs: excessive causal drift, duplicate checkpoint audits,
empty shared-core configuration and missing future-speaker perturbation. Some
other CLEAN_REFUTATION records contain zero results or failed causal checks;
the affective expert's final MELD trials all have NaN test losses. These labels
cannot all be treated as scientifically sound negative experiments.

The second topic's four completed evidence bundles each contain ten retained
checkpoint files and ten audit records with the three named perturbations.
Their shared parser reports all 13 checks true and measured maximum drift 0.
Representative same-experiment IEMOCAP/MELD pairs are **0.63910284/0.63505094**
(ML first experiment), **0.63002001/0.64131610** (its revision),
**0.63632407/0.63384390** (affective first experiment), and
**0.34820477/0.63500501** (repaired engineering experiment). None meets the
joint goal. The engineering IEMOCAP validation/test gap is observed; attributing
it conclusively to session generalization still needs a controlled experiment.

This review ran the second topic's unchanged accepted parser through the
existing `SharedAcceptance` evaluator on seven CPU-only cases: retained actual
evidence, a synthetic high-score control, the four earlier counterexamples and
threshold equality. All seven behaved as expected: only the synthetic control
qualified. This is a material improvement in this generated evaluator, **not a
platform repair or proof that the next Grounding will be equally correct**.
Checkpoint existence and published audit contents were inspected; the forty
checkpoints were not loaded to independently repeat their model-level audits.
Parser aggregation success is not a fresh scientific replication.

### Reproduced issues and development priorities

1. **P0 — generated acceptance can omit the scientific requirements.**
   First-run engineering experiment 7's retained `goal/parser/evaluate.py`
   declares `AUDIT_MAX_DIFF` at line 23 without enforcing it, trusts `passed`
   at lines 111–123, counts rows rather than distinct checkpoints near line
   196, and treats configuration existence as shared-core validity near line
   201. The ordinary counterexamples already reproduce false acceptance;
   neither real run actually produced a false successful goal verdict.
   [Native smoke validation](../hypoloom/native_output.py) executes the parser
   and validates its output shape; it cannot by itself prove commission
   completeness. Minimum next work: use the existing accepted evaluator and
   ordinary goal-specific positive/negative tests for strict thresholds,
   checkpoint coverage, all three perturbations and actual shared-core inputs;
   review the raw audit producer too. Extend the existing Grounding/test path,
   without adding a second scientific authority or replacing retained parsers.

2. **P1 — actual Host binding missed the intended shared-pool path.**
   Second-run affective experiment 2 froze GPU 3 from the 11:38 capability
   input. An external UID 1023 workload occupied that GPU at 11:44; its
   approximately 1,450 MiB free was below the 12,288 MiB requirement while
   GPUs 0/1 were idle. The experiment stayed READY from 11:44:45 until the
   operator stopped it; it never launched. The waiting Agent timed out at
   12:28. [Submission/dispatch](../hypoloom/workflow_host.py), lines 3316–3332
   and 3647–3661, and [selection](../hypoloom/compute_resources.py), lines
   487–573, implement this fixed nonpooled grant. The existing shared-pool
   alternate-device regression passes; no replacement scheduler is needed.
   Minimum next work: carry one reviewed `serve` argv through the existing
   [managed launcher](../.claude/skills/autoresearch/scripts/manage_host.py),
   verifying socket, namespace and allowed UUIDs in its actual launched
   process/status. The [operator Skill](../.claude/skills/autoresearch/SKILL.md),
   lines 781–784, already requires that transport. Surface the existing resource
   wait reason to the experiment Agent as well as the monitor. Do not hot-rebind
   these ended runs or auto-discover/replace a pool.

3. **P1 — terminal experiment elapsed time is wrong and contaminates learning.**
   [WorkflowHost._experiment_tool_view](../hypoloom/workflow_host.py), line
   3074, always subtracts start time from the current clock, even after CLOSED.
   The second engineering failure actually lasted **4.737855 seconds**. Calling
   the unchanged method with its retained SQL rows and a mocked clock 60 or
   3,600 seconds after completion returns **64.737855** or **3604.737855**.
   Its accepted SELF_EVOLVE text instead cites approximately 3,482 seconds;
   the final report cites approximately 2,912 seconds. These are not actual
   training durations. The resulting plugin commit
   `47719839add99fc197fbb247af27dc06bf7c6838` was supplied to seven subsequent
   Agent Attempts. Minimum fix: stop elapsed time at the stored terminal time,
   keep queue age distinct, and test repeated terminal reads using the existing
   Host-tool fixture. Reports/self-evolution should cite existing Process
   timestamps, not turn growing observation age into an optimization premise.

4. **P1 — repeated structured-reference errors spend calls without new science.**
   ADJUDICATE failed nine times in the first run and six in the second; the
   latter includes `pruning requires a selected winner` and three consecutive
   ML evidence-domain rejections before cancellation. A concrete engineering
   retry cited `--fact-outcome-2-2` but omitted the required
   `--component-2--outcome-2`; its next retry included the latter and succeeded.
   [admit_adjudication](../hypoloom/expert_loop.py), lines 994–996, currently
   combines missing and inadmissible references into one message. First-run
   Judge output failed twice, and two final reports omitted exact references
   despite receiving the required context.
   Minimum fix: expose the exact missing/disallowed IDs from the existing
   validator to the existing correction path; supply a compact, explicit
   reference map in the existing request. Improve single-chain keep/prune
   examples and [final-report validation](../hypoloom/final_report.py), lines
   65–78, diagnostics. Preserve reference-domain and complete-partition checks;
   do not weaken them, invent aliases or merely increase retry limits.

5. **P1 — waiting and inference budgets are poorly matched.**
   Both profiles use a 3,000-second Agent limit against a 108,000-second
   experiment limit; first/second runs had eight/two execution-Agent timeouts.
   The second queued Agent spent its lifetime without starting a Process.
   The current per-call USD cap is null and token enforcement is `observe`.
   First expose truthful wait state, selected resource and budget use. A later
   change separating Host-owned waiting from model invocation needs a bounded
   same-bundle submit/wait/timeout/re-entry test. It must preserve exactly-once
   submission and the rule that pending execution is not a successful Agent
   terminal effect. Do not disguise this larger lifecycle change as a timeout
   increase or alter the user-confirmed topic budgets in this review.

6. **P2 — retained-history Oracle coverage has real defects.**
   First-run final Oracle still rejects the two failed Grounding evaluator
   archives documented below. [Session Oracle](../hypoloom/session_oracle.py),
   lines 233–258, derives permitted captured evaluator files from a response
   that may not survive an invalid provider envelope. Earlier active-run
   inspection also reproduced an absent OPEN-Process observation lookup in
   [manager Oracle](../hypoloom/manager_oracle.py), lines 201–205, and active
   work-directory misclassification in [Oracle](../hypoloom/oracle.py), lines
   3635–3657. Minimum fixes belong in these existing validators, with ordinary
   failed-Grounding and OPEN/CLOSED Process regressions; retain identity,
   publication and byte-integrity checks. The second final Oracle passing does
   not erase the first failure.

7. **P2 — generated experiment and self-evolution quality need tighter scope.**
   First-run import/path/shape errors and NaN results, and the second-run
   CUDA/CPU device mismatch, justify small real-data training/validation,
   checkpoint reload and audit probes before five-seed expansion. CPU success
   alone cannot certify GPU placement. GPU probes must still use authorized
   Host-owned Processes. The second evolved `experiment-preflight` Skill also
   recommends design-time full training plus test-set feedback; do not copy it
   into the product unchanged. Reuse validation data for development, keep the
   confirmed test protocol intact, and verify advice against original facts.
   No claim is made that its proposed design-time GPU/test probe actually ran.

First-run interaction did occur: two cycle reports, six Debate statements,
two rejected Judges and one accepted Judge, followed by next-cycle work.
The earlier native-read inspection confirms all six statement inputs reached
the Judge. The second run had one cycle report and an OBSERVER decision of
`none`; it had no Debate/Judge/Leader attempt before cancellation. Different
scientific proposals alone did not force a debate under the current policy.
This is observed trigger behavior, not evidence of a missing input. Compaction
counts are deduplicated native UUIDs, with zero JSONL parse errors; they do not
prove preserved reasoning or explain the model failures. Measure citation
accuracy, correction of factual mistakes and useful subsequent experiments
before increasing interaction frequency or claiming a benefit from compression.

### Recommended bounded development sequence

1. Repair terminal elapsed reporting, explicit managed pool-argument transport
   and exact validation-error feedback with focused regressions. These are
   deterministic platform issues; a paid full-topic rerun is not their debugger.
2. Exercise the existing Grounding/common acceptance path with the reproduced
   scientific counterexamples; audit generated training/audit code on bounded
   real data. Improve existing instructions from verified facts, not the
   erroneous duration-based self-evolution. Preserve retained runs as evidence.
3. Fix the existing Oracle's failed-archive and active-Process cases. Then
   freeze a candidate and use G1/G2 plus bounded G3 to verify the combined
   changes, including timeout/re-entry, report retries and End run cleanup.
   A redesigned model-free waiting path is a separately scoped follow-up.
4. Only then run an explicitly bounded higher-model G4/G5 comparison through
   the actual shared-pool Host, with the same goal, target revision, caches,
   acceptance tests and comparable work allowance. Observe actual provider
   model identity, native tools, meaningful hypothesis changes, cross-expert
   evidence use, final reports and cost. Model choice, reviewed UUIDs/runtime,
   total/per-call bounds and unknown-cost reservations remain explicit inputs;
   no new allowance or zero previous spend is inferred here.

### Verification and final cleanup

This review reused both retained bundles' Oracle and accounting fold, existing
SQL/Agent/result evidence and the previous parser/scheduling reproductions.
It added seven CPU parser cases and the terminal elapsed reproduction, without
starting a Host. The earlier fixed-grant and shared-pool alternate-device tests
remain applicable because their runtime bytes match; retain their initial
overlong-socket fixture ERROR separately from the corrected-path pass. G1's
earlier 5,581 passes/25 opt-in skips remain scoped to that candidate; no full
G1, new paid/GPU qualification or scientific replication ran in this review.
Final documentation navigation/boundary checks passed **4/4**; whitespace checks
passed. Neither real research failure nor a skipped lane becomes a G5 pass.

At 12:59:36, **all 193 recorded process groups (133 first, 60 second) were
absent**, both managed units were inactive/absent with MainPID 0, both control
sockets were gone, no Attempts were OPEN, and both disposable `artifacts/work`
directories were empty. GPU processes on physical cards 2/3 belonged to UID
1023, not either run. GPU 0/1 were idle. Retained result-staging contains only
stdout/stderr logs (42 files /386,428 bytes first; 10 /29,518 bytes second),
which the existing log reader uses; bundles, published results, logs, Git
sessions and checkpoints were preserved. This is complete live-process cleanup,
not deletion of research history. The existing shared pool was untouched.
All seven review-created temporary parser directories were removed.

Detailed observations, accounting, counterexamples, original inspection error,
elapsed reproduction, checkpoint inventory and cleanup evidence are under
`.local/qualification/merc-two-hypoloom-synthesis-20260915/`; earlier logs remain
at their original locations below. An initial inventory probe assumed the
second run's checkpoint JSON layout in a first-run result and failed; the
corrected read follows each published layout. That is an inspection error,
not a product test failure. No commit, push, runtime/Profile change, external
notification, GPU reservation or new model invocation was made.

## Earlier observation — second Hypoloom topic GPU wait, 2026-09-15

At **12:35–12:40 +08:00**, the second Hypoloom topic
`state/MERC-causal-202609150849` is ACTIVE (observed revisions 55–56), with a
responsive Host and `dispatch_error: null`. Its actual model is
`glm-5.3-flash`, Claude Code 2.1.271. This focused read-only scheduling diagnosis
does not reassess its generated scientific evaluator or promote a new G5 lane.
The earlier Mimo topic `202609150044` is now durably CANCELLED r108 at 08:32:15;
the preceding 08:31 stopping observation remains historical.

**The empty GPUs cannot serve the queued experiment because this nonpooled
run already bound that experiment to a different, occupied GPU.** The waiting
work is affective-computing expert experiment 2,
`MERC-causal-202609150849--component-3--experiment-2`. It has been READY since
**11:44:45**, with no Process Attempt created. Its accepted execution contract
contains `resource_grant.gpu_uuid = GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`
(physical GPU 3), and the same UUID in `CUDA_VISIBLE_DEVICES`.

| Physical GPU | Observed free memory | Current ownership / effect |
|---|---|---|
| 0 / `GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84` | 24,121 MiB | Idle; permitted by the Host inventory but not this experiment's fixed grant |
| 1 / `GPU-e96b09e9-0bcc-d031-acce-bb705dabfa31` | 24,118 MiB | Idle; likewise outside this fixed grant |
| 2 / `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` | Approximately 1,450 MiB | Other user's SGLang process, UID 1023 / PID 1434986 |
| 3 / `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a` | Approximately 1,449–1,450 MiB | Other user's SGLang process, UID 1023 / PID 1434985; this is the queued experiment's selected device |

The exact Host wait reason is `granted GPU GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a
has 1449 MiB free; 12288 MiB is required`. The 12,288 MiB floor is the confirmed
8,192 MiB workload minimum plus 4,096 MiB reserve. At the 12:38 observation,
all five preceding Process Attempts are CLOSED (four SUCCEEDED, one FAILED),
and only two Agents are OPEN. The configured three-Process concurrency cap is
not exhausted. A GPU's instantaneous 0% utilization does not make its occupied
memory available. No other user's process or service was modified.

The submitted Agent's original compute input was captured at **11:38:01**:
GPUs 2/3 each had 24,117 MiB free, GPU 0 was marked Host-owned busy, and GPU 1
had 22,954 MiB free. Existing `compute_resources.select` ranks free memory then
UUID, selecting GPU 3. The external SGLang processes started at **11:44:02**;
the experiment was submitted at 11:44:43 and preregistered at 11:44:44 using
the original request input. `workflow_host.py:3316–3332` deliberately reuses
that input; `workflow_host.py:3647–3661` only rechecks the fixed grant at launch
and retains a resource wait when its memory floor fails. Repeated dispatch
does not select another GPU. Replaying the existing selection function on
the original bytes chooses GPU 3; a hypothetical fresh sample chooses GPU 0.
That pure-function comparison made no reservation or Process.

The actual managed Host argv contains all four GPU UUIDs but **no
`--gpu-pool-socket` or `--gpu-pool-namespace`**. The live shared pool has zero
run/request rows for this Run ID. Its existence does not enroll new Hosts.
The operator Skill already specifies forwarding those reviewed flags at
`.claude/skills/autoresearch/SKILL.md:781–784`; the earlier confirmed pool plan
was not used by this launch. All 131 retained runtime files still match the
checkout, so this is not an unobserved alternate scheduler implementation.

The source Agent `agent-5f05798bc0c74a2faefa830e08e1e76a-1` repeatedly waited on
the READY experiment, then TIMED_OUT at **12:28:04** after 3000 seconds. Thus
resource waiting has already outlived an Agent call without starting training.
Its timeout does not remove the registered experiment or free the occupied
external device. Host liveness and progressing peer Agents do not imply that
this waiting experiment can advance.

Two existing focused regressions establish the intended distinction:
`test_mcp_peer_chooses_less_free_unoccupied_gpu_and_replay_keeps_frozen_grant`
passes; `test_queued_work_can_use_a_different_gpu_when_only_that_device_recovers`
passes with a fresh short temporary root and the unchanged shared-pool code.
These use synthetic inventories/jobs, not real CUDA or paid Agents. The initial
second fixture had an **AF_UNIX path too long setup ERROR** under the long log
directory; preserve `focused.xml` (one pass /one error) separately from
`pool-focused-recheck.xml` (one pass, 0.81 s). No test or validator was weakened.
The private test pool request is RELEASED, sockets are removed, and no recorded
test process group remains. The existing production pool was only read.

The smallest supported direction for **fresh runs** is the existing shared-pool
path with an explicitly reviewed namespace and allowed UUID set: it stores
resource alternatives and selects live capacity at dispatch. Using idle GPUs
0/1 requires them to be included in that reviewed set; a pool limited to 2/3
would still wait. Adding pool flags to this already-created nonpooled run is
not an approved migration, and editing its accepted GPU grant is not a
read-only fix. This diagnosis makes neither change and starts no replacement
run. A future visibility improvement can reuse the existing wait reason/UUID
in the current experiment-tool view; it does not require another queue store.

Read-only status, original capability bytes/contract excerpts, GPU ownership,
selection replay, test errors/passes and cleanup evidence are under
`.local/qualification/merc-second-resource-review-20260915/`. The first SQL
inspection used a nonexistent transition `command_type` column; its correction
uses `command_id`. An initial selection probe supplied noncanonical JSON and
was rejected before selection; the corrected probe consumes the original
stored input bytes. These inspection errors are separate from the live wait.
No runtime/Profile, existing run, shared pool or GPU workload was changed.

## Earlier two-run review — 2026-09-15 morning

Historical scope: the following morning comparison pairs the older Prometheus
topic with the first Hypoloom topic, while that Hypoloom topic was still closing.
The two completed Hypoloom topics are compared in the current synthesis above.

**The platform has run real research, but the latest generated common evaluator
does not enforce the confirmed scientific commission. Preparing a new test with
a stronger model is reasonable; another long MERC run must not be presented as
scientifically qualified until this acceptance defect is addressed.** No runtime
repair or new research/model/GPU launch was made during this review. The earlier
bounded qualification remains scoped to its candidate, model and configuration.

### Two-run comparison and evidence scope

This comparison uses the two most recent MERC topic runs, not two qualification
canaries:

- Previous: `/data/Gray/Project/Prometheus/state-vnext/MERC-causal-sota-v16-202609141626`.
- Current: `/data/Gray/Project/Hypoloom/state/MERC-causal-202609150044`.

Both databases were read with SQLite `mode=ro` / `query_only`. Each run was
audited using **its own retained bundle's Oracle and `fold_agent_attempts`**;
the old run was neither migrated nor promoted as Hypoloom qualification.
The Hypoloom candidate is still HEAD
`6bd5bb910c8b9385f828f530cf634f3bbd19bbda` **plus the dirty/untracked working
tree**. Ordinary byte comparison finds all 131 files in its running bundle
equal to the current package. Control Python is the project's complete Conda
environment, SQLite 3.53.4; both topics bind the ERC target and `erc-cu128`
experiment environment already reviewed below. Their lifecycle/research
contract versions and generated acceptance sources differ.

The retained observations below were taken at **08:24 +08:00 on September 15**.
The current run had received an actual TUI CANCEL at **08:22:02**, revision 106;
revision 107 was STOPPING while FINAL_REPORT ran. The previous run was already
CANCELLED at revision 82, September 15 **01:01:08**, after a TUI CANCEL at
00:34:40. These are operator cancellations, not scientific completion.

| Observation | Previous Prometheus topic | Current Hypoloom topic, 08:24 |
|---|---|---|
| Actual model | `glm-5.3-flash`, provider-response identity on 71 calls | `mimo-v2.5`, provider-response identity on 109 closed calls |
| Agent Attempts | 71 CLOSED: 53 SUCCEEDED, 6 FAILED, 11 TIMED_OUT, 1 CANCELLED | 110: 63 SUCCEEDED, 36 FAILED, 8 TIMED_OUT, 2 CANCELLED, 1 OPEN FINAL_REPORT |
| Process Attempts | 14 CLOSED: 11 SUCCEEDED, 1 FAILED, 2 CANCELLED | 21 CLOSED: 9 SUCCEEDED, 11 FAILED, 1 CANCELLED |
| Canonical outcomes | 11 CLEAN_REFUTATION, 3 EXECUTION_FAILURE | 8 CLEAN_REFUTATION, 13 EXECUTION_FAILURE |
| Accepted scientific success | No GoalVerdictRecorded; no candidate exceeds both targets | No GoalVerdictRecorded; no candidate exceeds both targets |
| Research interaction | No completed CYCLE_REPORT, Observer, Debate or Judge | Two cycle reports/graph evolution/self-evolution closures; one Observer; six Debate statements; Judge FAILED twice then SUCCEEDED; designated next-round work actually starts |
| Native compaction | 60 distinct `compact_boundary` UUIDs | 55 distinct UUIDs at this observation |
| CLI/Host reported known model cost | **144.606664 USD**, plus **13 unknown-cost calls** | **148.545984 USD**, plus **12 closed unknown-cost calls and one open call** |
| Known cost on FAILED calls | 7.507903 USD | 67.456295 USD |
| Own bundle Oracle | PASS, zero OPEN/residue | FAIL: two `session_archive_invalid` findings; no Process remains OPEN |

These are observed call charges, not verified provider invoices. Unknown costs
remain unknown. Both topic Profiles omit invocation/total/per-expert USD caps;
the 1,000,000-token setting is `observe`, not a hard spend limit. Their research
costs are separate from the earlier **13.005258 USD /20 USD qualification
cohort**. The user has not yet selected a new model or supplied the next test's
time/cost/resource bounds in this follow-up. Changing directories does not
reset a test cohort or reconcile unknown usage.

The earlier **07:35** read-only observation remains a historical point:
81 closed Agents, 20 Processes including two live GPU trainings, known reported
cost 118.491042 USD plus ten unknown calls, 41 distinct compactions, and a
proposed debate waiting for the ML experiment. Later progress supersedes its
liveness/counts without erasing failures. In particular, debate was proposed at
06:46:25, started at **07:37:47** after the participant drained, reached a verdict
at **08:13:06**, and designated follow-up work at 08:13:09. This wait was not
`DISPATCH PAUSED`, a dead Host, or a completed run.

The actual Hypoloom Host used Claude Code 2.1.270, `mimo-v2.5`, settings-file
authentication and **all four local GPU UUIDs**, with **no shared-pool flags**:
`GPU-d469e765-ad52-350d-2cd4-9b12e5e5bc84`,
`GPU-e96b09e9-0bcc-d031-acce-bb705dabfa31`,
`GPU-91a3b0a9-d48b-1503-2893-33153c3fe405`, and
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`.
This differs from the prospective `glm-5.3` / two-GPU shared-pool binding
reviewed earlier. The old topic has 14 RELEASED requests in the existing pool;
the new topic has no request/run rows there. This is an observed configuration
difference; this audit did not inspect the separate Claude Code authorization
conversation or alter either binding. Authentication contents were not printed.

### Scientific results and reproduced acceptance failure

The completed five-seed pairs below are canonical measurements, not accepted
scientific successes. Dataset columns must stay paired within one candidate.

| Run / candidate | IEMOCAP mean weighted-F1 | MELD mean weighted-F1 | Qualification limit |
|---|---|---|---|
| Previous engineering experiment 1 | 0.6739669820660348 | 0.6440127627163108 | Best old IEMOCAP; both targets missed |
| Previous engineering experiment 2 | 0.6461062942435032 | 0.654213289985976 | Best old MELD; a different candidate |
| Previous engineering experiment 3 | 0.6730438698278683 | 0.6527241940737264 | Both targets missed |
| Current engineering experiment 7, SWAG | 0.6741571949186657 | 0.6514193329909388 | Both targets missed; generated checks do not prove the full causal contract |
| Current ML experiment 5, ECDS | 0.66115365999209 | 0.6500094646041381 | Now published with all five seeds; causal audit false |
| Current affective experiment 8, CEID | 0.6877828339080602 | 0.3126849060381001 | All five MELD test losses are NaN; degenerate prediction and failed audit |

The target remains IEMOCAP **>0.7305** and MELD **>0.6674**, with the same
core candidate and all ten final checkpoints individually measured under all
three future perturbations. The old report's all-zero audit statements were
not independently retrained or remeasured here. Its parser enforces the named
measurements and checkpoint/seed coverage; that structural result alone does
not establish scientific correctness of every producer. The current eight
CLEAN_REFUTATION rows likewise must not be called eight valid scientific
experiments: they include zero-output and failed-audit cases. Process exit 0,
typed acceptance and scientific validity are different observations.

For a concrete comparison, the **unchanged accepted parsers** from both runs
were each executed on five tiny isolated CPU inputs. The controls deliberately
use synthetic 0.9 scores; they are not research results. Existing
`SharedAcceptance.from_dict` / `evaluate_shared_acceptance` decide the outcome:

| Synthetic parser input | Previous acceptance | Current acceptance |
|---|---|---|
| Control | Admits synthetic control | Admits synthetic control |
| Measured future-logit drift 0.001, exceeding 1e-6 | Rejects | **Incorrectly admits** |
| Ten audit entries repeat the same checkpoint | Rejects | **Incorrectly admits** |
| Empty core configuration | Rejects | **Incorrectly admits** |
| Missing future-speaker perturbation | Rejects | **Incorrectly admits**; the current input needs none of the three perturbation records |

All ten parser processes exited; their ten temporary directories were removed.
No GPU, model call, new fact, real-run write or network request was involved.
Inputs, outputs, unchanged parser paths and actual existing-validator results
are retained in `parser-counterexamples.json`. This extends the previous
three-counterexample review with a paired old/new parser comparison. It proves
a **potential false scientific success**, not an already recorded false
GoalVerdict in either topic.

### Combined findings and smallest next steps

| Priority | Evidence, impact and proposed correction; none implemented in this review |
|---|---|
| High: generated common acceptance weakens the goal | Current accepted `goal/parser/evaluate.py` at lines 103, 111–123 and 196–203 counts entries, trusts `passed`, and treats configuration-file existence as shared-core validity. `AUDIT_MAX_DIFF` at line 23 is unused, and no max-drift criterion exists in the accepted contract. The four counterexamples above pass through the actual shared evaluator. Keep the existing Grounding/acceptance path; add topic-derived negative examples to its ordinary local checks and ensure the next generated parser checks the confirmed three perturbations, threshold, exact ten checkpoint identities and actual shared configuration. Do not patch this retained run's accepted source or introduce a second evaluator authority. |
| High: invalid audit evidence propagates into reasoning | The current ERC `full_audit` feature permutation at `src/merc/causal/audit.py:136` explicitly excludes speaker inputs. Current ML's `scripts/ecds_run_all.py:228–233` also contains a missing-checkpoint branch emitting `passed=True`; this review does not claim that branch executed. The real Judge then describes gradient/feature checks as complete causal verification and uses that claim to rank CDF above ECDS. A stronger Judge cannot recover missing measurements from prose. Future experiment code must reload the actual final checkpoints and measure all three required perturbations; missing checkpoints or nonfinite outputs must fail. Reuse existing checkpoint/output paths and tests, with no new digest scheme. |
| High: execution reliability and result validity | The new topic has 11 failed Processes versus one in the old topic. Published failures include missing `merc`/`tqdm`, wrong cache/read-root paths, split-manifest mismatch, non-executable script, missing argument attributes and tensor-shape/empty-list errors. CEID repeats MELD weighted-F1 0.3126849060381001 with NaN loss across all five seeds. Fix the reproduced import/path/shape/numerical problem in the new candidate's existing wrapper, then require a bounded real-data train/validation/output/parser check before expanding the run. Do not count a NaN workaround or a zero-score placeholder as a clean scientific refutation. |
| High before another paid test: unbounded/unknown spend | Both runs have null USD caps and unreported interrupted calls. The current run spent 67.456295 known USD on FAILED calls by 08:24, before final-report retries. Higher model capability alone supplies no cost bound. Select an exact provider model/auth source and explicit invocation, aggregate/per-expert, wall-time and resource limits for the new test; use the existing accounting fold and retain unknowns and original failures. No fee or new authorization is inferred here. |
| Medium: repeated adjudication/reference errors | Old ADJUDICATE fails three times and never completes; new ADJUDICATE fails nine times before two successes. Both repeatedly hit `expert_loop.py:994–996`: the exact outcome reference is missing or references lie outside the admitted domain. Other failures violate keep/prune/winner partitions; both first FINAL_REPORT attempts omit an accepted outcome/verdict reference. The new Judge additionally fails twice before success. Keep these validators. Reuse their existing batch/tip inputs and report exact missing/disallowed IDs in the existing correction diagnostic, with focused regressions; do not solve this by relaxing evidence identity or adding blanket retries. |
| Medium: long execution waits consume Agent lifetimes | Old run: ten EXECUTION timeouts plus one DESIGN timeout; new run: eight EXECUTION timeouts. They occur around 3001–3005 s against a 3000 s Agent limit, while an experiment may run for 108000 s. Ordinary retries and native compactions do not make those calls free. First verify existing submit/wait and same-experiment reuse with a short workload, then review the actual long-training/Agent budget combination. No duplicate-effect finding was observed; the data do not prove a scheduler deadlock or justify a new asynchronous lifecycle. |
| Medium: Oracle does not cover all observed lifecycle states correctly | At 07:35, `manager_oracle.py:201–205` dereferenced absent observations for OPEN Processes, and `oracle.py:3635–3657` classified active work directories as residue. Those three findings disappeared after the Processes closed. Two failed Grounding archives still fail `session_oracle.py:233–258`: evaluator files were preserved without a response/rejected_response, so the expected-path calculation omits them. Both existing archive digests match and 440/500 JSONL records parse. Repair the existing observation/path handling with focused OPEN and failed-capture regressions; retain all identity/digest checks and do not relabel the current Oracle as clean. |
| Medium: actual launch differs from reviewed resource plan | The preceding prepared shared-pool binding was not used by the actual Hypoloom Host. For the next test, review the actual managed-launch argv and pool namespace together with the selected GPU UUIDs. Reuse the user's approved existing pool and independent namespace if that remains the selected plan; never infer a binding from pool liveness or replace the shared service. |

Current parser evidence is the published engineering experiment-7 result under
the current root's `artifacts/<run>/<run>--component-2--experiment-7/` subtree;
the previous parser is the published component-1 experiment-1 result under its
own root. `result_sources` in the ignored observations retains their full paths.
The current commissioned requirements remain in the user-local
`profiles/local/MERC-causal/goal.yaml`.
The source-level findings and proposed fixes are not authorization to change
existing research state, runtime bundles, data or shared services.

The final-report failure is independently reproduced by each retained bundle's
`report_sources` / `validate_final_report`, without another model call. The old
first report abbreviates required outcome identities and fails; its second
report contains all 14 and passes. The current first two reports omit exact
outcome identities; the first also omits `NO_ACCEPTED_VERDICT`. The accepted
source context contains all 21 outcomes, so these failures are not evidence
that the Host lost the report inputs. `final_report.py:65–67` currently reports
the generic omission rather than listing missing IDs. Extend that existing
correction message if repaired; keep the complete-reference requirement.

The real interaction does establish new **observational** coverage: the Judge
received a 108,750-byte context containing exactly six canonical statements;
its native Read result contains all six source Attempt IDs. The existing
interaction Oracle accepts the statement/verdict/restart linkage. It chose
engineering 0.55 versus ML 0.45, and ML reached cycle-2 DESIGN before TUI
cancellation. This closes the earlier "not yet observed" statement for this
Mimo topic, but does not qualify judging quality or the old `glm-5.3` canary.
Compaction UUIDs are deduplicated across resumed transcript archives; event
occurrence does not establish retention of every scientific constraint.

### Recorded state and next model test

At **08:27–08:28**, the old run has no socket, no OPEN Attempts, all 85 recorded
process groups absent and all 14 pool requests RELEASED. The current run has
no OPEN Process or GPU compute process; Host PID 772177 remains active with
zero restarts, and the second FINAL_REPORT Agent is running after the first
failed with `invalid_workspace` / omitted outcome reference. Thus **current
Host/Agent shutdown is not yet complete at this observation**. The existing
shared pool PID 3785437 stays active. This review issued no CANCEL/stop/restart
and did not clean or rewrite either research run.

At the final recorded **08:31:47** observation, the current run is still
STOPPING r107: **112 Agent Attempts**, comprising 63 SUCCEEDED, 38 FAILED,
8 TIMED_OUT, 2 CANCELLED and one OPEN third FINAL_REPORT attempt. The first
two reports remain failed. Known reported cost has risen to **150.637190 USD**
(69.547501 USD on FAILED calls), with the same **12 closed unknown-cost calls
plus one open call**. There are 56 distinct compaction UUIDs. All 21 Processes
remain CLOSED, and the two original Grounding archive Oracle failures remain.
This later observation is saved separately from the 08:24 record; neither
unfinished report generation nor future fees are counted as complete.

Prepare the next model test in this order: resolve the reproduced acceptance
and output-validity defects with focused offline checks; select the exact
model and bounded budget/resource binding; run an authorized bounded native
sample covering Grounding, an actual experiment, result collection, report and
exit; then exercise the existing multi-expert debate/next-action path. Only
after these pass should training duration and search scope expand. Retain the
confirmed MERC targets/data/protocol and count valid candidate pairs, failed
calls, timeouts, unknown cost, real three-perturbation audit and terminal cleanup
as separate outcomes. The observed two runs are not a controlled model ranking:
model, generated code/evaluator, package and GPU-pool topology all differ.

Detailed evidence lives only in the existing ignored qualification tree,
`.local/qualification/merc-two-run-review-20260915/`: per-run observations using
their retained validators, original failure excerpts, parser counterexamples,
Judge input/Read evidence, scientific output excerpts, candidate/binding and
liveness observations. These are review logs, not a new qualification system.
Historical run bytes remain in their original roots. This follow-up changes
only this document, compact Codex memory/INDEX and ignored logs; it does not
start the user's proposed higher-model test, change the Profile or initiate a
new model/GPU call. Existing report-generation calls can continue charging
while this read-only review runs. Formal operations remain in Claude Code.
The existing documentation-structure checks pass **4/4** (`docs.xml`); Git
whitespace checks pass. No full or paid qualification was rerun for these
documentation changes.

## Earlier startup qualification — selected binding ready, 2026-09-15

The following is the **pre-launch audit record**, before the two-run review
above. Its selected `glm-5.3` / shared-pool readiness conclusion must not be
applied to the later Mimo topic's generated scientific evaluator.

**The current candidate is ready to start the user-confirmed MERC-causal topic
with the reviewed Host binding. Formal startup belongs in Claude Code through
`/autoresearch init`; this audit has not started the topic.** No additional
Start now instruction is required in the Codex audit conversation. The final
startup choice belongs to the existing Claude Code flow. The full repaired candidate's offline qualification now
passes **5,581 tests, with 25 explicit opt-in skips and zero failures/errors**.
The approved shared-GPU G4, G5-local, G5-common and G5-resource recheck evidence
remains applicable. The original G5-resource FAIL and its charge are retained.
This is readiness for a fresh run on the selected configuration, not a claim
that every optional compatibility lane or historical-release recovery passed.

The user confirmed the six-file `profiles/local/MERC-causal` configuration,
including its omitted USD caps and existing cadence/time limits. Its only
authorized edit remains Web port **8766**. Production `profile-check` again
admits it without blockers or warnings. A disposable empty managed Host has
now actually started with the reviewed model/authentication, literature,
GPU inventory and existing-pool settings, then stopped cleanly. Only its
state root and namespace differed from the formal arguments. No Agent,
experiment or GPU reservation was created by that smoke test.

On explicit user approval, `loginctl --no-ask-password enable-linger gray`
succeeded and `Linger=yes` was read back. The user manager, shared pool and
existing research Host retained their original PIDs and active sessions.
This establishes the user-manager lifetime policy beyond the last logout;
an actual logout/reboot or a 70-day soak was not performed. The confirmed
shared-pool binding and independent namespace remain unchanged.

The supplied OpenAlex credential has real HTTP-200 authentication and metadata
evidence. A new bounded sample also completes the production search-to-PDF-to-
Poppler path; inaccessible PDFs and the initial overly early query's coverage
error remain recorded. Deep ERC cache checks find no alignment failure but
retain **WARN: source is empty**. Two bounded CPU forwards consume real cached
inputs successfully; they do not establish scientific accuracy or causality.

All four actual model samples still total **12.996258 USD**, including the
original failed resource sample. Total metered OpenAlex usage is now
**0.009 USD**, drawn from free allowance with zero prepaid/cash drawdown.
Conservatively including it gives **13.005258 USD** against the unchanged
**20 USD** cohort ceiling, leaving **6.994742 USD**, with no unknown
reservations. The new directory does not reset the cohort. These are existing
Host/provider reports, not an independently verified upstream invoice.

The authorized interaction repair changes one runtime file and three existing
test files. This final verification changes only current documentation and
ignored logs, plus the separately approved linger policy. Existing runs,
source-project services, target/data and saved bundles remain untouched.
Nothing was committed, pushed or released.

### Candidate and evidence reuse

HEAD is `6bd5bb910c8b9385f828f530cf634f3bbd19bbda`, on `main`. The candidate is
**that HEAD plus the current working tree**, not HEAD alone: `.gitignore` and
`README.md` were modified, and the imported package, tests, Profiles, hooks,
Skills, packaging and guides were untracked. The initial read-only audit changed
only this qualification document outside ignored validation outputs. The later
authorized repair changes `hypoloom/interaction_host.py`, three existing test
files, the ignored local Profile's Web port, and current documentation. This
working tree, rather than HEAD alone or the pre-repair wheel, is the candidate.

Before the repair, ordinary byte comparison found all 131 packaged
runtime files equal across the current wheel and checkout, with no omissions;
the earlier installed CPU run's private bundle also had the same 131 files.
All 527 relevant files in the current source archive matched the checkout.
The earlier extracted source archive differed only in the removed minimal
starters and related Profile test/guide/operator-Skill changes. Runtime/test
file timestamps, the original commands, JUnit case identities, environment
metadata and current collection were checked together. Timestamps alone were
not treated as candidate identity. No source-project qualification was reused.
The archived pre-repair wheel and earlier saved bundles do not contain this
fix; their passes remain evidence for unchanged paths, not repaired binaries.
The disposable wheel-test Conda environment was subsequently updated from the
new wheel. Its installed files and new managed run bundle match all 131 current
runtime files; the new installed CPU closure is recorded below.

The initial Hypoloom full suite recorded **5,562 passed / 43 skipped** in 5,605
cases, followed by an 18-case browser supplement. Pre-repair collection had
5,602 cases after public-template parameter changes. Those original records,
commands and identity comparisons remain retained at their original paths;
their totals are not a new run of the repaired tree.

After focused repair checks and the paid follow-up, the final frozen runtime
candidate was tested once through the existing eight G1 groups. All **346
current test files** occur exactly once; JUnit contains **5,606 distinct case
identities: 5,581 passed / 25 skipped / zero failed or errored**, in 642.3 s
wall time. This freshly includes the browser cases and four new regressions.
The six opt-in cases with separately valid passing evidence are the rootless
ENOSPC case, two shared-native G4 cases, and G5-local/common/resource. Thus
**5,587 current distinct cases have passing evidence and 19 remain unexecuted**.
This union does not erase the original failed resource sample or turn skips
into passes. It is not a substitute for the stage-specific limits below.

The old 63-failure initial focused probe and ordinary-venv CPU timeout remain
in `focused.xml`, `focused.log` and `wheel-smoke.log`; later passing samples did
not replace them. The complete Conda installation addresses the documented
venv interpreter-binding limitation. This audit's initial sandbox-denied
socket/bwrap/NVML probes and incorrectly formed absolute-path `profile-check`
command are retained separately from their successful host/corrected-command
checks. None is silently counted as a pass.

### Observed stages

| Stage/check | Current evidence and limits |
|---|---|
| G0 | The common-acceptance/interaction repair is now authorized and implemented. Four new real CPU cases reproduce both directions of the old predicate error and pass after repair. The original read-only reproduction, four regression failures and a subsequent test-import failure remain retained. The corrected focused selection passes 89/89. Other admission/configuration findings remain separately documented. |
| G1 static/import/configuration | Final full repaired candidate: **5,581 passed / 25 skipped / 0 failures/errors**, 642.3 s, all 346 current test files and 5,606 distinct cases. This includes real native CPU and installed-browser scenarios with remote/GPU opt-ins disabled. Current compileall, Ruff `F601,F602,F821,F822,F823` and Git whitespace pass; the unchanged environment's `pip check` remains applicable. The earlier 89 focused and 156 adjacent passes remain repair evidence, not extra distinct cases. |
| G1 package/install | The repaired wheel is built without dependency downloads and installed into the existing disposable complete Conda environment. All **131 runtime files** match checkout, wheel, installation and the fresh managed bundle. New installed CLI/MCP/user-systemd CPU closure passes in 4.62 s: **2 Agent +2 Process**, one outcome, clean Oracle, COMPLETED, Host exit 0, socket removed and transient unit collected. The smoke Profile is a component fixture, not a production topic. Source distribution details are retained with the package comparison below. |
| G2 | All six existing G2 test files execute again in the final G1. The earlier **169-case selector** and enabled rootless **16 MiB tmpfs ENOSPC test** remain separately retained; persistence, Oracle, migration and bundle code is unchanged. Same-source publication and bundle/session recovery are exercised. There is **no retained Hypoloom release cohort**; fixtures and disposable runs do not establish historical compatibility. |
| G3 | All seven existing selector scenarios pass again within final G1, as does native three-expert combined interaction. Independent inspection of these eight real runs finds **251 Agent +22 Process Attempts all CLOSED**, 21 canonical outcomes and clean Oracles; all 244 distinct recorded process groups are absent. The two LOST, two FAILED and one CANCELLED Attempt are intended fault/cancel observations. The TUI's cancelled run is not labelled COMPLETED. Earlier seven-scenario and repaired native results remain retained. See protocol and interaction limits below. |
| G4 | After explicit authorization, the existing selector passed **8/8, zero skipped**, 306.18 s: two real CUDA cases plus six offline helpers. Four Hosts completed **72 Agent + 8 real GPU Process Attempts**, all 80 CLOSED/SUCCEEDED, with eight canonical outcomes and clean run/pool Oracles. Actual native resource Skills, measurements carried into subsequent cycles, same-card overlap, pre-Process capacity waiting and reclamation were observed. See scope and cleanup below. The earlier CPU-only resource-Skill rehearsal remains separate evidence. |
| G5-local | **1 passed**, 483.00 s: approved `glm-5.3` / settings-file authentication, 11 successful Agent Attempts, one successful real CPU Process, canonical metric **0.875**, published final report and clean Oracle/cleanup. Reported/accounted usage **2.693327 USD**, 44 turns, no unknown reservations, within its 14 USD allocation. |
| G5-common | **1 passed**, 540.59 s, on the repaired working tree: Grounding authored and tested the actual common parser, the expert read that accepted source, and the real CPU outcome satisfies the common accuracy criterion **0.875** and both validity checks. Eleven Agent Attempts and one Process are CLOSED/SUCCEEDED, with an accepted final report and clean Oracle/cleanup. Reported/accounted usage **3.831384 USD**, 49 turns, no unknown reservations, within the 7 USD allocation. This one-expert sample does not cover paid cross-expert interaction or the MERC scientific result. |
| G5-resource | **Original sample: 1 failed**, 604.63 s, at `test_real_shared_gpu_g5.py:624`; cleanup was chained into the wrapper CPU check. Its **3.553527 USD** charge, 52 turns and original failure remain retained despite successful CUDA execution and independent cleanup. **Authorized recheck: 1 passed**, 485.22 s, with unchanged code/instructions/strict recognizer. The standalone wrapper check passes; native resource queries, exact UUID binding, CUDA checksum **6**, measured-history reuse and an 8,957-byte final report are observed. Eleven Agent Attempts and one Process are CLOSED/SUCCEEDED, the run is COMPLETED, both Oracles are clean and the private pool is released/closed. New reported/accounted usage **2.918020 USD**, 33 turns, no unknown reservations, within its 6 USD allocation. No further paid retry or notification occurred. |

### Environment and startup conditions

Control Python is `.conda/envs/hypoloom/bin/python`: complete Conda Python
**3.12.14**, SQLite **3.53.4**, PyYAML 6.0.3, filelock 3.32.4, wsproto 1.3.2,
jsonschema 4.26.0, pytest 9.1.1, Textual 8.2.8, websockets 16.1.1, Ruff 0.16.7
and build 1.6.1. Its resolved interpreter stays inside the prefix and imports
`hypoloom` from this checkout. The separate `.local/wheel-runtime` has the same
Conda Python/SQLite builds and imports the installed package from site-packages
with `-I`, outside the checkout. No extra dependency installation was needed.

The shell's unqualified `python` instead resolves to `/opt/miniconda3/bin/python`
with Python 3.12.4 / SQLite 3.45.3: **do not use it to launch Hypoloom**. Linux
5.15, Claude Code **2.1.270**, bubblewrap **0.4.0**, Google Chrome
**149.0.7827.155**, and Poppler `pdfinfo`/`pdftotext` **0.86.1** were observed.
The native CPU tests prove actual Claude/bwrap/local-broker execution, beyond
version/help output. All 18 browser cases pass again in the final G1 with the
installed browser. Actual online OpenAlex and production Poppler retrieval
results, including failures, are recorded below.

User-systemd is reachable and a real transient Hypoloom service was observed
active, then inactive/collected with MainPID 0 and ExecMainStatus 0 after CPU
completion. The user manager reports `degraded` due to pre-existing unrelated
failed units, which were left untouched. The original `Linger=no` condition
was resolved at 2026-09-15 00:17 +08:00 by the explicitly approved enable-linger
command. `Linger=yes` and unchanged existing service PIDs are verified. The
actual managed empty-Host test and the scope of the lifetime evidence are
recorded in the final startup section below.

There are no inherited Hypoloom live opt-ins or model-auth environment keys.
The conventional user settings file `~/.claude/settings.json` is a regular,
non-symlink mode-0600 file. The existing resolver reports gateway auth, default
`glm-5.3`, valid mappings `haiku -> glm-5.3-flash` and `fable -> glm-5.3`, and
invalid `sonnet`/`opus` mappings. No credential bytes were printed. The real
managed launcher makes its intended private mode-0600 settings copy; the
disposable Host's copy was removed after shutdown, leaving the source intact.
This source and `glm-5.3` were subsequently **approved for the bounded G5
samples below**; actual local/common/resource closures succeeded. The original
resource sample remains failed alongside its successful recheck. The supplied
OpenAlex file is absolute, regular, non-symlink, owned by UID 1001 (the caller),
mode 0600, with a mode-0700 parent. The actual reader accepted its format and
both Poppler executables passed preflight. That original admission-only check
reported `network_probe_performed=false`; the later real network results
establish the selected remote path separately. Interactive OAuth, help output
and local fixtures were not substituted for actual authenticated calls.

The migrated package/launcher/public-template scan found no old repository imports, old
`PROMETHEUS_*` configuration, `state-vnext` dependency or machine-specific
runtime paths in the migrated source. Machine paths in this audit describe
observations only; they are not defaults installed into configuration.
The user-supplied ignored local Profile does retain explicit
`PROMETHEUS_TELEGRAM_BOT_TOKEN` / `PROMETHEUS_TELEGRAM_CHAT_ID` references, with
Telegram disabled. The explicitly selected OpenAlex key also resides in a
`prometheus`-named user configuration directory. These are local user bindings,
not automatic imports or discovery of an old repository; neither was renamed.

### Interaction, context and completion evidence

- The selected three-round debate/restart test explicitly writes **ACI 14**;
  it must not be described as the default ACI 19 path. Separately, the unchanged
  `test_three_experts_native_leader_then_debate_six_cpu_and_final_report` has
  applicable passing evidence under the current default: three experts, six
  stateless Observer sessions, one Leader, six debate statements, one Judge,
  six real CPU experiments (five successes and one clean refutation), both
  designated next actions and the final report. This is scripted-provider
  compatibility evidence, not paid scientific judging quality. Existing
  CPU tests also cover concurrent experts without a global round barrier.
  The now-passing shared-GPU selection exercises two Hosts; it does not by itself
  prove a single Host running the exact future three-expert CUDA Profile.
- The Judge receives the exact file containing all six statements. Native
  reads and tests verify complete statement/source identities; predecessor
  debate inputs project the actual typed response without recursively copying
  earlier requests and remain below the native 256 KiB Read limit in this
  scenario. Large Judge inputs stay complete on disk without duplicate prompt
  inlining. Existing validators and source records remain the authority.
- Observer coalescing includes only closed reports present in its captured
  input. Existing tests cover reopening, late reports and failed Observers.
  Debate throttling requires both the configured **1,800 s** cooldown and
  **two new outcomes** after a completed debate, using durable event times;
  those regression cases passed in the applicable full suite. The first
  debate is not artificially required to wait 1,800 s.
- **Automatic native session compaction was unobserved in these bounded
  qualification samples.** The later real-topic observations above are
  separate evidence. The fresh G3
  artifacts contain 138 transcripts / 6,774 parsed records with no observed
  compaction event. The two paid G5 samples add 22 complete transcript
  archives / 1,594 records, also with no observed compaction marker. Input
  projection, persistent memory and transcript byte
  caps are not evidence that native compaction ran successfully. Existing G5
  local/common canaries also explicitly exclude Observer/Debate and do not
  guarantee a compaction event.
- `DISPATCH PAUSED (Host error)` is a dispatch exception quarantine, not
  completion or an ordinary resource wait. Current tests verify preserved
  original diagnostics, continued peer/control service and cancellation
  cleanup; database/integrity failures still halt. Durable ACTIVE and a live
  Host are separate facts. Terminal completion requires actual terminal state
  and cleanup; the TUI hides the pause badge for terminal states.
- The fresh TUI test proves `q` leaves the real CPU process and Host running
  without a CANCEL, reentry works, and `x / End run` submits exactly one
  accepted CANCEL. The CPU Attempt becomes CANCELLED, every Attempt closes,
  the Host exits 0 and `control.sock` disappears.
- The paid G5-local native archives contain actual Read, Bash, Write, Skill,
  resource-query, experiment-list, submit and wait calls, including the
  literature-summary, resource-planning and final-report Skills. The existing
  seeded-repair validator observed the initial failing local test, repair and
  subsequent pass in the DESIGN session. The real CPU result is 0.875 and the
  published `FINAL_REPORT.md` is 6,378 bytes. This is the existing one-expert,
  one-cycle local-metric fixture; paid debate/Judge and common-goal acceptance
  were not exercised. Its token setting is `observe`, not a hard per-node
  token ceiling; the existing aggregate accounting reports 1,424,199 tokens
  across 44 turns.
- The resource sample's failed assertion precedes some later closure assertions
  in that test. Separate read-only inspection reused the existing resource-trace,
  measured-history, Agent-identity, charge-threshold and Oracle validators. It
  verified the native resource Skill/query results, the post-wait measurement's
  exact own Attempt identity, 2,048 MiB admission, 362 MiB owned resident peak,
  the approved UUID, CUDA checksum 6 and the 8,583-byte submitted final report.
  These are supplemental observations; the original paid pytest result remains
  FAIL. No assertion was bypassed or changed to claim a passed canary.

### Findings and remaining inputs

| Severity | Reproduction, impact and smallest correction |
|---|---|
| High / repaired: common acceptance prematurely closes interaction | The original `interaction_host.py:144` predicate treated any local SUPPORTED assessment as closing, suppressing Observer/Leader/Debate even when common measurements failed a strict threshold. It also missed common success when the local assessment was REFUTED. The retained read-only prefix reproduction and four new real CPU regressions fail on the original code. The authorized fix at `interaction_host.py:144–155` reuses `SharedAcceptanceHost.assessment_qualifies`, the same accepted-evidence decision used by workflow goal handling; ordinary local-metric behavior remains covered. The 89-case corrected focused run passes, including local-only evidence, equal threshold, invalid evidence and common success despite local refutation. Nonqualifying cases observe an actual Observer Attempt. No terminal GOAL_MET reducer, schema or new authority was introduced. Native common acceptance and combined interaction remain separate scenarios; see repair evidence below. |
| Launch preparation resolved; formal Start authorization outstanding | The user confirmed the existing Profile, including invocation `max_cost: null`, observe-only tokens, omitted total/per-expert USD caps, 70-day wall limit, 30-hour Process limit and omitted interaction cooldowns. Do not invent caps or repeatedly request confirmation. Nodes inherit the explicit Host model (`workflow_host.py:2175`). The concrete glm-5.3/settings-file binding, exact two-UUID inventory, existing-pool socket/UID and independent namespace pass the existing argument and budget checks and the actual disposable managed-Host smoke. User-approved linger is now enabled. `safety.yaml:7` uses auto only within Host-authorized GPUs and does not select a pool. The 20 USD approval is the qualification cohort ceiling, not an implicit topic cap. Formal Start remains unauthorized. |
| Original qualification failure / recheck resolved: resource wrapper-check command shape | Retained DESIGN transcript `agent-996de133b4c741b2b73944ce679e3228-1` first records a real `json.dumps(..., flush=True)` TypeError, then the model's repair and successful wrapper CPU checks. Its successful command starts `rm -f /tmp/test_result.json && ... python /workspace/experiment.py --local-check`. The fixture at `test_real_shared_gpu_g5.py:134–137` requires a separate wrapper-test Bash call, while `_has_wrapper_local_check` at lines 399–449 rejects this extra cleanup chain; line 624 fails. The separately authorized recheck uses `HYPOLOOM_TARGET_ROOT=/workspace HYPOLOOM_RESULT_PATH=/tmp/test_result.json python experiment.py --local-check` in a successful standalone call and passes the unchanged qualifier. No runtime/validator edit was needed. Keep the first FAIL and its 3.553527 USD charge; the new PASS and 2.918020 USD are separate observations, not a guarantee that every future model call follows the format. |
| Medium / resolved for preparation: occupied Web port | The original `127.0.0.1:8765` is occupied by existing Python PID 3786571. With the user's authorization, only `MERC-causal/notify.yaml:13` changes to port **8766**. Read-only listener inspection, a temporary successful bind/close and a fresh production `profile-check` pass. No Web server or formal Host was started, and the old listener was untouched. Availability is a preparation observation, not a reservation until a future Host binds. |
| Medium: environment gateway address is dropped | With dummy `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` set, intercept `workflow_host.serve` while calling the ordinary Claude `serve` CLI without a settings file: only the key reaches `provider_environment`. The managed helper's `_minimal_environment(include_api_key=True)` also omits the address. See `cli.py:490–497`, `.claude/skills/autoresearch/scripts/manage_host.py:256–276`, and the default endpoint in `claude_agent_host.py:563`. Contrary to `SETUP.md:34–35`, this environment-auth path cannot select the intended gateway and falls back to the default Anthropic endpoint. Preserve the explicit transport separation and carry the selected allowlisted base URL through both existing paths, with focused forwarding regressions. The approved settings-file path carries its own address and is unaffected. No runtime change or external request was made. |
| Medium: OpenAlex file-mode requirement is not enforced by its reader | A fresh dummy key file with mode 0644 is accepted by `_explicit_api_key(None, path)` at `literature.py:2701–2735`. The reader checks regular-file/symlink/type/size constraints but does not check the documented 0600 permission requirement. This must not be represented as a runtime-enforced permission guard; the init/operator check remains necessary. Add the existing required permission predicate to this reader and a focused permissive-mode rejection test. No real credential was read or exposed by this reproduction. |
| Medium: model placeholder passes admission | Copy a public template, bind valid target/runtime/read roots, leave `experts.defaults.model: REPLACE_WITH_PINNED_MODEL`, then run `profile-check NAME --profiles-root PARENT --cpu-only --run-id ID`. Observed exit 0, `admitted=true`, no blockers/warnings. `profile_v4.py:72–73` only validates model text; `claude_agent_host.py:3422` accepts this non-alias string. A partially prepared Profile can appear ready and later attempt an invalid remote selector. Reject the documented unresolved sentinels through the existing model/Profile validator and add a focused negative regression; do not add a separate readiness mechanism. No code fix was made. |
| Coverage limits | No retained Hypoloom release cohort; no paid debate/Judge sample or qualified automatic compaction. Common-goal and resource canaries pass with one expert each; the first resource sample remains failed. Shared G4 and native three-expert CPU closure provide complementary evidence, not a single-Host three-expert CUDA run of the supplied Profile. ACI 14 debate restart and ACI 19 combined interaction are distinct. These remain unexecuted compatibility scopes; the existing promotion rules do not require every optional sample for a fresh run on this selected binding. Scientific acceptance must still be earned during research. |
| Operational conditions addressed for the selected launch | The shell's default Python/SQLite is unsuitable; the prepared launcher explicitly uses the complete project Conda environment. The user-approved linger command changed only gray's lifetime policy to `Linger=yes`, with unchanged active service PIDs. Full logout/reboot and long-duration endurance were not tested. |
| Low: stale test-module prose | `test_real_shared_gpu_g5.py:10` says the allocation defaults to 6 USD, but its live entry at line 896 requires `HYPOLOOM_RESOURCE_G5_ALLOCATION_USD`. Missing input has no default there. Align the docstring with the existing explicit-input behavior; retain the fail-closed allocation check. The qualification guide already requires the explicit input. |

Only `multi-expert-en` and `multi-expert-zh` are public. Both contain exactly
six regular YAML files and preserve budget/permission/expert-ID controls across
languages. Production `profile-check` accepted the two disposable fully bound
copies without starting research; it rejected both untouched templates for
unbound target/runtime/read-root paths. The separate unresolved-model probe
above is retained as an admission defect, not counted as a successful negative
check.

The supplied topic does not need a newly invented objective, threshold, expert
roster or another A/B/C feature-boundary decision. Its existing configuration
values are user-confirmed. Concrete Host model/authentication/resource bindings
are now prepared, existing-pool reuse is confirmed and linger is enabled.
At this preparation stage, the final startup choice belonged to Claude Code.
The later real-run review above supersedes this stage's current-status claims. The default
public `metric: null` form can stop on
one accepted SUPPORTED result, but MERC-causal instead uses
`evaluation: grounding`: one candidate must satisfy the complete common
acceptance. The interaction repair preserves that distinction.

### Supplied MERC-causal Profile and OpenAlex follow-up

Production command (read-only; `--allow-gpu` here checks the admission branch,
does not launch or reserve a GPU):

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.cli profile-check MERC-causal \
  --profiles-root /data/Gray/Project/Hypoloom/profiles/local \
  --run-id MERC-causal-preflight-20260914 --allow-gpu \
  --literature-adapter openalex \
  --openalex-api-key-file /home/gray/.config/prometheus/openalex.key \
  --literature-pdfinfo /usr/bin/pdfinfo --literature-pdftotext /usr/bin/pdftotext \
  --literature-timeout 15 --literature-max-response-bytes 2097152 \
  --literature-max-pdf-bytes 16777216 --literature-max-pdf-pages 2000
```

Observed exit **0**, `admitted=true`, no blockers or warnings, in 0.30 s.
At that read-only check, the six regular YAML files were unchanged and
Git-ignored. After the user's configuration confirmation, only `notify.yaml`
port 8765 changes to 8766; the same production check passes again, exit 0 in
0.29 s, with a fresh preflight-only run ID and no blockers or warnings. The first
operator command incorrectly used `-m hypoloom`, which has no `__main__`; its
exit-1 stderr and command are retained separately from the corrected CLI call.
The literature preflight accepts the file credential and Poppler configuration
only. It reports 15 s request timeout, 60 s search wall bound, two retries,
2 MiB metadata/text and 16 MiB PDF bounds, plus an unauthenticated Semantic
Scholar DOI fallback preflight (5 s, 64 KiB). That earlier admission check made
no network requests. The authorized online follow-up below now establishes key
validity, remote quota/cost and real metadata retrieval, with explicit PDF
coverage limits. No key contents were printed, logged, copied or moved.

| Item | Observed configuration and evidence |
|---|---|
| Goal and completion | MELD five-seed mean weighted-F1 strictly >0.6674 and IEMOCAP strictly >0.7305, seeds 0–4, one shared core candidate; incomplete/duplicate seeds and combined best results from different candidates are excluded. All ten final checkpoints require future-feature, future-speaker and future-participant-set perturbation checks with maximum logit difference <=1e-6. The commission asks Grounding to implement and locally test common acceptance, rather than accepting self-reported success. |
| Scientific reference scope | Local Poppler extraction succeeds on both supplied papers. MultiEMO Table 2 (PDF page 8) reports MELD 66.74; AO-FL Table II (PDF page 7) reports IEMOCAP w-F1 73.05. The ERC table agrees. These are reference thresholds, not verified equivalent causal-protocol baselines, statistical significance or current public SOTA. Bounded online retrieval checks are recorded separately; no training or scientific qualification was performed. |
| Target and data | `/data_ssd/Gray/Project/ERC` is a clean Git checkout at `b4590893f756b9f09e2ff2bcbdcf1b784322801c`; admission binds that commit. Both raw-data roots, C_multiemo caches, split manifests, IEMOCAP validation list and referenced papers exist and are readable. Existing `SplitAssignment.validate` and `FeatureCacheReader.validate_against` pass; split IDs exactly cover cache IDs without duplicates. IEMOCAP validation IDs and Session1–4/Session5 partition agree. MELD train/val/test dialogue counts are 1038/114/280; IEMOCAP 108/12/31. |
| Cache limits | Existing `audit_cache_for_method(..., deep=True)` now reads all 1,432 MELD and 151 IEMOCAP dialogues and finds no alignment/schema failure. Both retain **WARN: source is empty**, as in the earlier shallow check. This does not establish feature causality. The Profile expressly accepts fixed cached inputs and their provenance/context uncertainty; no cache regeneration or new feature-level acceptance condition is introduced. |
| Experiment environment and integration | `/home/gray/miniconda3/envs/erc-cu128` is complete Conda Python 3.12.13 / Torch 2.8.0+cu128, NumPy 2.5.2, h5py 3.16.0; `pip check` passes. Plain environment discovery finds no installed `merc`. With the reviewed checkout's `src` explicitly on PYTHONPATH, real imports of `merc.causal.train_causal`, its audit, splits, Torch and h5py succeed; CUDA remains uninitialized. Future experiment wrappers must import the private candidate's source and map read-only cache paths through the existing workspace read-root projection, not silently import an old/global checkout. |
| Acceptance integration still to be done during research | Existing ERC `src/merc/causal/audit.py:133–155` excludes speaker from perturbed inputs and only randomizes floating feature tensors. `train_causal.py:243–276` writes single-seed metrics and checkpoints; it does not supply this complete ten-checkpoint/three-perturbation acceptance evidence. Grounding must adapt the existing interfaces in its private workspace and test the common evaluator before any success verdict. This is a declared research task, not a requirement to prewrite a topic evaluator during this audit or modify the read-only target. |
| Experts and language | Three independent full-goal experts: machine learning, experiment engineering and affective computing. Shared effort high; up to two forks and two hypotheses per round. Prompts/reports zh-CN, code comments en. No exact model is present in the Profile; omission inherits the chosen Host model, and admission does not prove a particular remote model binding. |
| User-confirmed limits | Run wall limit **1680 hours (70 days)**; each Process **1800 minutes (30 hours)**. Each Agent invocation: 240 turns, 3000 s, 1,000,000 observed tokens, 1,310,720 output bytes, no USD ceiling. Total experiments 360; role caps 20 Grounding +110 ML +110 engineering +100 affective. Concurrent Agent/Process/review limits each 3. No total/per-expert dollar caps or run-wide turn/token caps are supplied. These values are confirmed and preserved. Linger is now enabled for the desired user-manager lifetime; no 70-day endurance claim follows. |
| User-confirmed interaction and context | Leader is enabled after one closed expert round; Debate is enabled for three rounds. This local Profile omits `min_interval_seconds` and `min_new_outcomes`; `interaction_host.py:157–160` applies **0 / 0**, with no cooldown/new-outcome deferral beyond ordinary eligibility checks. These values are confirmed; template settings are not inherited. Retention is retain, transcript/memory/plugin bounds 128/8/1 MiB. These settings and token observe mode do not establish automatic context compaction. The applicable interaction predicate bug is repaired and separately verified below. |
| GPU and notifications | Up to one GPU per experiment, 8192 MiB workload floor and 4096 MiB reserve. `gpus:auto` only narrows to the future Host's explicitly authorized inventory; no topic pool binding was created or changed. Telegram is disabled and its two legacy-named environment references are unset in this audit process; no token/chat value is in the Profile. Web is enabled on the user-authorized replacement **127.0.0.1:8766**, with a successful temporary bind and admission check. No notification was sent. |

The original common-interaction probe is a production-predicate reproduction over a
prefix of retained current-ACI G3 evidence, with the supplied interaction flags
selected only in memory. It is **not** a new end-to-end research run or an extra
case in the 5,602-case collection. Its original failed assertion and all earlier
qualification failures remain retained. The authorized minimal repair and its
focused/native CPU regressions are now complete. The subsequently authorized
bounded `g5-common` and separately recorded `g5-resource` recheck both passed
within their concrete allocations and the same cohort accounting. A common
one-expert canary still does not prove paid multi-expert Debate/Judge or the
exact MERC scientific outcome.

### Authorized interaction repair and verification

The runtime change is limited to `InteractionHost._closing`. It retains the
shared-goal guard and passes accepted assessments, linked evidence and canonical
outcomes to the existing `SharedAcceptanceHost.assessment_qualifies` decision.
There is no new reducer, schema, migration or compatibility layer. The tests
extend the existing shared-acceptance fixture and the existing native common
scenario, and give the existing stored Host seam its production acceptance
helper.

All commands use the explicit complete Conda Python from this checkout, fresh
isolated roots, and cleared `HYPOLOOM_REAL_*` flags. The two native scenarios
use the installed Claude CLI, bwrap, production Host/broker/Process and loopback
scripted providers; they make no paid model or external literature requests.

| Check | Observed result |
|---|---|
| Original-code regression | **4 failed**, 11.93 s. Real CPU outcomes reproduce local-only support, an equal strict threshold, invalid common evidence, and common success despite local refutation. The original failures remain in `red.log/xml` and `red-evidence/`. |
| First focused run after runtime repair | **88 passed, 1 failed**, 49.11 s. A new top-level test-fixture import broke the existing recovery test's plain-Python child import. This was a test-only regression; move that import into the applicable fixture branch. Preserve `focused.log/xml`. |
| Corrected focused run | **89 passed**, 51.55 s; shared acceptance, interaction, grounded goal policy, recovery and control Oracle. The three nonqualifying new cases produce real Observer Attempts; qualifying common evidence closes interaction despite local REFUTED. |
| Adjacent regression | **156 passed**, 22.47 s; debate Host, Profile runtime consumers, expert interaction, interaction Oracle, public Profiles and documentation. |
| Native CLI CPU integration | **2 passed**, 341.01 s. Common Grounding authors/tests one accepted evaluator; the first locally supported but nonqualifying result does not close interaction. Separately, three experts complete Leader, six Debate turns, Judge, six CPU experiments, designated follow-up work and the final report. The existing native common fixture disables interaction; these are complementary scenarios, not a claim of native common acceptance plus a full debate in one run. |
| Static/environment/collection | compileall, Ruff `F601,F602,F821,F822,F823`, `pip check` and Git whitespace pass. **5,606 cases collected**; collection does not execute opt-in lanes. |
| Local Profile | User-confirmed six-file contents retained except Web port **8766**. Temporary bind/close and production `profile-check` pass; no Host/run or network key probe is started by admission. |
| Installed package | Fresh wheel built and installed with no dependency downloads. Reuse the existing managed-wheel smoke with only its log destination changed: installed CLI/MCP and actual user-systemd CPU closure pass, **2 Agent +2 Process CLOSED/SUCCEEDED**, one outcome, clean Oracle, COMPLETED, MainPID 0, ExecMainStatus 0, unit collected and socket gone. Ordinary byte comparison checks all 131 runtime files against checkout, installation and this new bundle. `package.json` also records source-distribution contents. |

Independent read-only inspection uses the existing Oracle on the four corrected
CPU regression runs and two native runs: **six COMPLETED runs, 166 Agent +
12 Process Attempts, all 178 CLOSED/SUCCEEDED**, with clean Oracles. Across
all five fresh test roots, all **149 recorded process groups** are absent;
the native common test's six additional parser-smoke process groups are also
absent. No matching live process, socket or private mount remains. No existing
run, GPU pool, research target/data or service was modified.

The additional installed-wheel run also closes cleanly: both recorded real
process groups are absent; the two fake-Agent Attempts have no owned process
group. Its actual managed Host has exited and its transient unit is collected.
This brings the seven inspected completed runs to **168 Agent +14 Process
Attempts, all 182 CLOSED/SUCCEEDED**. The existing disposable wheel-test
environment is the only updated environment; old run bundles remain untouched.

An initial package inspection incorrectly assumed the in-process native fixture
Hosts had `state/bundle` directories. Its failed path comparison is retained
in `package-initial-path-probe.json`; those fixtures import the checkout.
The corrected comparison uses the actual managed CLI-created bundle and passes.
No fixture was rerun or package requirement weakened to hide that inspection
error.

Detailed commands, original red/failed/corrected JUnit logs, static checks,
cleanup inspection and retained native/regression evidence are under
`.local/qualification/interaction-fix-20260914/`. `repair.diff` is an ordinary
review diff against the existing preflight source archive. No new qualification
authority or frozen-candidate mechanism was added. This repair adds **0 USD**
in model charges; cohort reported/accounted usage remains **6.246854 USD**.

### Additional paid batch — completed within the existing ceiling

The earlier approval and 20 USD ceiling remain in force for the qualification
cohort. The user explicitly authorized the additional common sample and resource
recheck below. Both samples passed; the original resource failure is retained.
Profile values do not need confirmation again.
Use the same complete Hypoloom Conda Python, Claude CLI 2.1.270, **glm-5.3** and
regular mode-0600 `/home/gray/.claude/settings.json` gateway authentication.
Key contents must never enter commands, logs or the Profile.

| Order | Concrete test and limits |
|---|---|
| 1 | Existing `scripts/qualify.py g5-common`, once, CPU only: **7 USD allocation**, 900 s run +30 s drain, 240 aggregate turns, at most 24 turns /300 s /2 USD per Agent invocation. Carry exact prior 6.24685399999999991 USD and use the existing canary cumulative-limit input 13.24685399999999991 USD to bound this sample to 7 USD within the outer 20 USD cohort. |
| 2 | Existing `scripts/qualify.py g5-resource`, once after re-reading prior charges/reservations: **at most 6 USD**, 1200 s run +30 s drain, 384 turns, 32 turns /300 s /2 USD per Agent; one 30 s CUDA experiment, 64 MiB tensor and 256 MiB allocator cap (context excluded). Exact UUID **GPU-91a3b0a9-d48b-1503-2893-33153c3fe405**, runtime `/home/gray/miniconda3/envs/erc-cu128`, fresh private pool, existing 512 MiB reserve and at least 6144 MiB available at admission. No existing pool replacement. Keep the existing standalone wrapper-check instruction and strict recognizer; preserve the first failed sample. |

The two allocations total 13 USD; together with the pre-batch accounted usage
their planned ceiling was **19.246854 USD**, below the unchanged 20 USD limit.
At the end of that paid follow-up, model usage was **12.996258 USD**, or
**12.999258 USD** including the then-observed free OpenAlex usage. The final
startup verification below updates literature usage to **13.005258 USD**
conservatively accounted overall; it adds no model charges. Current
unknown reservations are empty; if new usage is unreported, retain it as
unknown/reserved and stop dependent launches until it is reconciled. The
existing accounting is CLI/Host reported usage, not an independently verified
upstream invoice; an in-flight response may overshoot, never funding another
launch. No directory change resets the budget, and no automatic further retry
is included. These model canaries do not validate paid multi-expert debate,
automatic compaction or the actual MERC scientific result. OpenAlex key/quota
are checked separately below.

### Online literature and prospective formal Host binding — 2026-09-14

Outputs from this earlier follow-up are in ignored
`.local/qualification/readiness-20260914/`. Before the common launch, ordinary
byte comparison found no differences from the repaired source archive. The
resource launch separately checks all runtime, test, Skill and script bytes;
no code or fixture instruction was changed for either paid sample. Current HEAD
still denotes a dirty/untracked working-tree candidate, not a committed release.

**Common acceptance:** the existing `g5-common` selector passed **1/1**,
540.59 s. Eleven Agent Attempts and one CPU Process are CLOSED/SUCCEEDED,
the run is COMPLETED, the existing Oracle is clean, and all twelve recorded
process groups are absent. Grounding authored and tested the actual common
parser; the expert read the same accepted source and submitted GOAL evidence.
Observed accuracy is **0.875**, both `complete_pairs` and
`computed_accuracy_matches` are true, and the final report is accepted.
Native transcripts contain actual Read/Bash/Write/Edit/Skill, resource-query,
submit and wait calls; the accepted report has 8,059 bytes. No compaction event
was observed in 776 parsed transcript records, with no parse errors. This is
an observation, not a passed automatic-compaction test.
Reported/accounted usage is **3.83138399999999992 USD**, 49 turns and
2,088,612 observed tokens, with no unknown reservations. This remains the
existing one-expert compatibility fixture, not MERC training or paid debate.
Original command, JUnit, full retained state/transcripts and independent cleanup
are `g5-common-command.json`, `g5-common.log/xml`, `g5-common-evidence/` and
`g5-common-cleanup.json`.

**Resource recheck:** the unchanged `g5-resource` selector passed **1/1**,
485.22 s. The model's standalone wrapper CPU check satisfies the existing
recognizer. Its admitted workload is 2,048 MiB, with a measured own peak of
362 MiB on **GPU-91a3b0a9-d48b-1503-2893-33153c3fe405**. The actual CUDA probe
reports checksum **6**, exactly one visible device and a held 64 MiB tensor;
the original supplied probe is unchanged. Native resource-planning Skill/query
results carry the exact completed Attempt's measurement into the execution
session. The accepted final report is 8,957 bytes. Eleven Agent Attempts and
one GPU Process are CLOSED/SUCCEEDED and the run is COMPLETED.

Reported/accounted usage is **2.91801999999999990 USD**, 33 turns and 1,509,000
observed tokens; unknown reservations are empty. Independent run/pool Oracles
are clean, all twelve recorded process groups are absent, the private pool's
only request is RELEASED with no queued/reserved/launching/bound requests, and
its socket is gone. The fixture records no cleanup error. There are no observed
compaction events in 606 parsed native transcript records, with no parse errors.
See `g5-resource-recheck-command.json`, `g5-resource-recheck.log/xml`,
`g5-resource-recheck-evidence/`, `g5-resource-recheck-cleanup.json` and the native
observation file. The earlier resource FAIL/log/transcript/bill under
`live-20260914/` remains intact; no third resource sample was launched.

The existing `fold_agent_attempts` / `charged_cost` functions were applied to
all four distinct original sample databases, including the failed sample.
`cohort-accounting.json` records **12.99625799999999973 USD** model usage plus
0.003 USD free literature usage, leaving **7.00074200000000027 USD** below the
20 USD cohort ceiling. No copied database is counted again, and no old research
cost is imported. Charges are Host/CLI reporting, not a verified upstream invoice.

Both new test Hosts have exited, all **22 Agent +2 Process Attempts** are
closed, and all **24 recorded process groups** are absent. Retained fixture
files and transcripts are evidence, not running services. After the tests,
three remaining GPU processes were traced through their ancestors to the
existing Prometheus Host PID 3786571 and its pool requests; none belongs to the
qualification. Those processes and their shared service were left untouched.
The first direct process-group lookup found no match because bubblewrap uses
nested process groups; `unrelated-gpu-ownership.json` retains that lookup and
the complete ancestor/pool mapping instead of treating an empty lookup as proof.

**OpenAlex:** a real Bearer-authenticated `/rate-limit` request returned HTTP
200. No key was put in a URL or retained response. The service reported a
1 USD daily allowance, 8,840 credits remaining before the probe and 8,810
afterward, with prepaid balance/drawdown zero. The current official
[authentication reference](https://help.openalex.org/api/authentication/) and
[operation prices](https://help.openalex.org/access/example-costs/) describe the
same API and free daily allowance. The production OpenAlex/Poppler adapter
performed three stratified searches for `emotion recognition in conversation`,
returning three valid records and three HTTP-200 responses, each reporting
`meta.cost_usd=0.001`. The 30-credit delta agrees with this 0.003 USD metered
usage; unrelated earlier daily usage is not imported into this cohort.

The live search retained **three PDF_UNAVAILABLE results**: no open-access URL
for the first record, `POPPLER_INVALID` for a JAIR PDF, and the bounded work
deadline for the remaining direct/fallback work. These are not full-text
passes. The local logging wrapper then accessed the nonexistent `artifacts`
attribute instead of `LiteratureRetrieval.pdf_artifacts`; that AttributeError
is preserved in `openalex-live.json`. The returned packet passes the existing
`LiteraturePacket.from_dict` validator in a separate read-only review, without
repeating the API queries. See `openalex-packet-review.json` and both quota
responses. No failure was relabelled as a successful PDF retrieval.

A separate check used the actual publisher link for the
[MELD paper](https://aclanthology.org/P19-1050/). The existing guarded PDF fetch
path downloaded 2,950,595 bytes in one HTTP call; the production Poppler
extractor read ten pages, an abstract, conclusions and two supported-claim
records in 3.86 s. The PDF and `publisher-pdf.json` are retained. This proves a
real public PDF path while preserving the unavailable original three results.

**Prospective Host:** `formal-host-binding.json` contains complete argument
vectors for the existing managed launcher and CLI, validated through
`_validate_host_argv`, the actual `serve` argument projection intercepted before
Host construction, `validate_new_run_budget` and `profile_agent_budget`.
There is no second launcher or readiness authority. The prospective root
`state/MERC-causal-202609142341` does not exist; no formal Host, unit or run was
created. The real Profile again passes production admission.

| Binding | Checked value and scope |
|---|---|
| Control/runtime | `/data/Gray/Project/Hypoloom/.conda/envs/hypoloom/bin/python`, Python 3.12.14 / SQLite 3.53.4; current checkout import. Experiment prefix `/home/gray/miniconda3/envs/erc-cu128`. |
| Model/auth | Exact `glm-5.3`, resolved Claude 2.1.270 and `/usr/bin/bwrap`; settings authentication from regular mode-0600 `/home/gray/.claude/settings.json`. Actual paid common execution proves this selected path. Unused invalid sonnet/opus mappings are not selected. |
| Agent budget | Exact Profile projection: 240 turns, 1,000,000 observed tokens, 1,310,720 output bytes, 3000 s, `max_cost: null`. The transport explicitly includes `--agent-max-cost-usd null`; the effective Profile budget is observe mode despite the transport object's default enforce mode. Existing budget validators pass. |
| Target/data | `/data_ssd/Gray/Project/ERC` remains clean at `b4590893f756b9f09e2ff2bcbdcf1b784322801c`; the admitted target/runtime/read roots remain the user-confirmed values. No target or data write occurred. |
| Candidate GPU subset | `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` and `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`; actual `LocalComputeResources.snapshot` returns exactly this subset. This is a prospective launch binding, not a formal GPU reservation. |
| Confirmed existing shared pool | `/data/Gray/Project/Prometheus/.local/gpu-pools/shared-3090x4/control.sock`, expected server UID 1001. The user explicitly confirmed reuse with an independent Hypoloom namespace and only the reviewed UUID pair. Hypoloom's existing `gpu-pool-status` succeeds read-only. Each candidate GPU initially had 20,024 MiB allocatable and no commitment. At the final observation each has one original-research commitment and **11,823 MiB allocatable**, with a 4096 MiB pool reserve and zero unknown attribution. These are time-specific capacity observations, not MERC memory estimates or reservations. This external service deliberately remains rooted in the old directory and serves existing research. No pool was replaced, started or joined by a formal Hypoloom run. |
| Namespace / observer / lifetime | Prospective namespace `hypoloom-MERC-causal-202609142341` has zero existing pool run/request rows. Web bind/close on 127.0.0.1:8766 succeeds. Telegram remains disabled. This initial follow-up observed `Linger=no`; the later authorized change and actual managed-Host test resolve that preparation condition, as recorded below. |

The resource launch's first local path lookup mistakenly excluded every fixture
directory containing `current`; its StopIteration is recorded in
`resource-launch-path-probe-failure.json`. It occurred before any resource/model
launch. Correcting that lookup and independently auditing the completed common
run established exact prior model cost **10.07823799999999983 USD**. The live
resource recheck started with a conservative prior **10.08123799999999983 USD**
including free OpenAlex usage, and a fresh allocation of at most 6 USD; this
does not reset the original cohort or overwrite its first resource failure.

Final source comparison finds no runtime, test, Skill or script drift from the
repaired source archive; only this document and Codex CURRENT memory changed
during the follow-up. The existing four documentation tests pass, and Git
whitespace checks report no errors. Current checkout imports, the clean ERC
target, Web bind/close on 8766 and the absence of the formal state root were
rechecked. Detailed final checks are retained with the same batch evidence.
No runtime code, local Profile, shared service or system setting was changed
in this follow-up, and no commit, push or formal Start occurred.

### Final startup verification — 2026-09-15

Evidence is in the existing ignored tree
`.local/qualification/start-readiness-20260915/`. This is a continuation of
the same qualification cohort. No runtime, test, Skill, script or local Profile
changed. Ordinary comparison against the repaired source archive checks 544
source files with none missing; only this document and Codex CURRENT memory
differ. HEAD plus the reviewed working tree remains the candidate.

**Final G1 and closure:** reuse the existing eight file groups with a fresh
`/tmp/hl-final-g1-ownv8bk2` root, complete project Conda Python and no inherited
`HYPOLOOM_REAL_*` opt-ins. All groups return 0. The 5,606 distinct JUnit cases
give **5,581 passed / 25 skipped / 0 failures/errors**; static checks also pass.
No complete qualification was used as a repair debugging loop. The paid and
real-GPU cases are reused only where their unchanged bytes, environment and
explicit configuration match. `g1-command.json`, `g1-[0-7]-command.json`,
logs/XML, `g1-result.json` and `g1-skips.json` retain the exact execution.

The 19 remaining opt-in cases include older/alternative native contracts,
stage-specific paid probes, exclusive-GPU and two-GPU-per-experiment scenarios,
and the unapproved Telegram notification test. They remain unexecuted. In
particular, `test_native_gpu_g4.py` and the real two-GPU goal-stop case require
exclusive idle devices outside the selected shared-pool topology; the plural
experiment also differs from MERC's maximum one GPU per experiment. Existing
research occupies the reviewed inventory intermittently. No old process was
stopped, idle check weakened or different GPU substituted to clear these skips.
The already passed shared-pool G4 scenarios are the applicable resource lane.

Independent read-only Oracle/count inspection of the seven existing G3 cases
plus native combined interaction finds **251 Agent +22 Process Attempts all
CLOSED**, **21 canonical outcomes**, and eight clean Oracles. The five intended
non-success terminal reasons are two LOST, two FAILED and one CANCELLED; the
TUI cancellation remains distinct from successful completion. All **244
distinct recorded process groups** are absent. No related process is found
by `/proc` command/cwd/root/fd inspection, no selected control socket remains,
and no related private mount remains. These checks do not reinterpret the full
suite's deliberately corrupt/open unit-test fixtures as historical runs.
`g1-cleanup.json` retains the results. The first supplemental SQL probe used
a nonexistent `workflow_runs.status` column and failed before any mutation;
`cleanup-probe-error.json` retains it. The corrected inspection uses the
current schema and existing Oracle; this probe error is not a G1 test failure.

**Actual managed Host and lifetime:** the existing launcher starts an empty
Host with all reviewed formal model/authentication/literature/GPU/pool flags,
using only a fresh state root and `hypoloom-standby-d01fj66j` namespace. Its
user-systemd service stays active after the launcher exits. The private bundle
matches all **131 current runtime files**, the settings copy is mode 0600 and
UMask is 0077. Durable counts are zero runs, zero Attempts and zero research
facts; pool namespace run/request counts remain zero before, during and after.
The existing stop command succeeds, Host PID 558577 disappears, the control
socket is removed and the transient unit is collected with MainPID 0. Only
this test's private settings copy is removed afterward. See
`managed-binding-smoke-command.json` and `managed-binding-smoke.json`.

The user then explicitly authorized `enable-linger gray`. The noninteractive
command exits 0 and subsequent `loginctl show-user gray` reports `Linger=yes`.
The user manager PID **1355633**, shared-pool PID **3785437**, original-research
Host PID **3786571** and login-session list are unchanged. The
[installed-generation loginctl manual](https://manpages.ubuntu.com/manpages/focal/man1/loginctl.1.html)
documents the user-manager lifetime beyond logout. This verifies the setting
and actual managed service topology; it does not simulate last-session logout,
reboot, automatic research restart after reboot or 70-day endurance. Existing
unrelated failed units remain untouched. See `linger-change.json` and
`long-running-preflight.json`.

**Read-only ERC integration:** the existing deep cache auditor reads **1,432
MELD dialogues /13,708 utterances** and **151 IEMOCAP dialogues /7,433
utterances** in 3.97 s. Both results retain `WARN: source is empty`; there is
no alignment/schema failure. The existing dataset/dataloader and causal
MultiEMOStrict wrapper then perform one CPU inference per dataset using at
most eight real cached utterances. Logit shapes are `[1,8,7]` and `[1,8,6]`,
with finite logits/auxiliary loss and CUDA uninitialized, in 3.48 s. No
optimizer, checkpoint, training, scientific score or source/data write occurs.
The commands retain 180/90 s bounds, two CPU threads and temporary working
directories. See `erc-deep-cache*` and `erc-cpu-forward*`. Provenance and causal
acceptance still require the user-confirmed research work; no new feature
boundary or scientific metric was invented.

**Production OpenAlex/PDF path:** the first targeted MELD query spans
1990–2026. All three search requests return HTTP 200, but the two early strata
have zero records and the existing validator raises `LiteratureCoverageError`
at `literature.py:3366–3375`. The sample remains failed in
`openalex-topical-live.json`; its 0.003 USD metered usage is counted. This is
an unsuitable query range for that dataset, not an authentication failure.
Existing `test_literature.py` covers empty-stratum rejection and subsequent
query adjustment; no validator was weakened.

One corrected sample changes only the test query's first year to 2019, keeping
the production transport, 15 s request timeout, three one-paper strata, two
retry maximum, 60 s PDF work window and existing byte/page bounds. It returns
three HTTP-200 search results and a valid packet in **43.84 s**, with no retry.
One result, DOI **10.1109/tmm.2023.3271019**, is retrieved from arxiv.org and
parsed by the actual Poppler extractor: **2,925,625 bytes, 13 pages and two
supported-claim records**. IEEE/MDPI access failures and Semantic Scholar 429s
leave the other two records `PDF_UNAVAILABLE`. They remain unavailable, as do
the original earlier sample's three PDF results. This corrected packet proves
one complete production retrieval path, not universal publisher access. See
`openalex-topical-corrected.json` and `topical-literature-pdf-002.pdf`.

The two new three-request samples report **0.006 USD** total metered free
usage; `/rate-limit` changes from 0.119 to 0.125 USD used, leaving 0.875 USD
daily allowance at observation. Prepaid/cash drawdown is zero and no charge
is unknown. Reapplying existing `fold_agent_attempts` / `charged_cost` to the
four distinct original paid databases yields unchanged model usage
**12.99625799999999973 USD**. Including all three literature samples gives
**13.00525799999999973 USD**, leaving **6.99474200000000027 USD** below the same
20 USD ceiling. `cohort-accounting.json` retains each sample, including
failures; simulated G1 provider costs and unrelated research spend are excluded.

**Final admission:** `final-readiness.json` records another successful
production Profile check, unchanged clean ERC HEAD, correct checkout import,
regular mode-0600 credential files, `Linger=yes`, successful Web bind/close on
8766, and absent formal state root. At 00:21 +08:00, the existing pool reports
**11,823 MiB allocatable** on `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` with
one old-research commitment, and **20,024 MiB** on
`GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a` with none. Both have zero unknown
attribution. Capacity is a live observation, not a reservation or a model
memory estimate. The formal namespace still has zero run/request rows.
Actual Start must use the already reviewed arguments in
`readiness-20260914/formal-host-binding.json` and ordinary live pool admission.
No further configuration input is missing. Continue in Claude Code with
`/autoresearch init`, adopting the existing MERC-causal Profile and reviewed
bindings; the existing flow's final `Start now` choice initiates the formal
topic there. This is not an additional approval step in the Codex audit
conversation. The user's intended operating interface is Claude Code.

### Approved bounded resource/model validation

The user confirmed the following resources and limits after the initial audit
on 2026-09-14. This authorizes these disposable tests, not a formal topic run.
All use control Python `.conda/envs/hypoloom/bin/python` and the resolved Claude
binary `/home/gray/.local/share/claude/versions/2.1.270`. No existing GPU pool
will be replaced or used. The approved pair is devices 2 and 3. The fresh
inventory found 24,121 / 23,404 MiB free respectively, above the required
6,144 MiB; the existing PID 216670 on device 3 is left untouched. G4 uses
fresh private test pools. The later explicit prior accounted usage declaration
was 0 USD; it applies to this qualification cohort, not to old research bills.

Both G4 cases completed on their first execution within the approved 20-minute
total: 306.18 s pytest / 306.72 s including the launcher. The initial-estimate
case observed `capacity_unavailable` before a Process existed. The ordinary
case captured 33 pool snapshots with two same-card commitments and fully known
owned attribution/credit; the initial-wait case captured four such snapshots
after calibration. Earlier transient snapshots with unknown attribution were
not counted as established owned credit. Each of the eight distinct Process
Attempts has its own CUDA output with the exact visible UUIDs and checksum 6
per device; projected copies of those logs are not extra experiments.

| Conditional test | Confirmed resources and limits |
|---|---|
| G4 | Existing `test_shared_gpu_native_g4.py` two live cases; fresh private pool and local scripted providers. Approved RTX 3090 UUIDs: `GPU-91a3b0a9-d48b-1503-2893-33153c3fe405` and `GPU-20ca7d4a-89d1-ec85-9bee-690afdd3283a`. CUDA prefix `/home/gray/miniconda3/envs/erc-cu128`, complete Conda / Torch 2.8.0+cu128. Require at least 6,144 MiB free per device at admission, preserve the test's 512 MiB reserve, 540 s drive bound per case and allow up to 20 min total including cleanup. The cases include multi-Host sharing, same-card overlap, initial 16,384 MiB estimate waiting and calibrated follow-up work. No paid model/auth source is used. |
| G5 CPU | Selected the existing `g5-local` platform fixture because no topic completion semantics were supplied. Approved selector `glm-5.3` and reviewed `/home/gray/.claude/settings.json`. CPU only; 900 s run bound plus 30 s drain margin, 240 run turns, at most 24 turns / 300 s / 2 USD per invocation. Overall cohort ceiling **20 USD**, explicit prior **0 USD**, CPU allocation **14 USD** reserving up to 6 USD for the resource sample. Passed on its first execution. |
| G5 resource | Existing `g5-resource` sample on the first approved UUID above, same CUDA prefix/model/auth source, fresh private pool. 1,200 s run bound plus 30 s drain margin, 384 run turns, 32 turns / 300 s / 2 USD per invocation, 30 s GPU experiment bound; allocation **at most 6 USD within the same overall 20 USD ceiling**, not an additional allowance. |

G5 was executed sequentially. The GPU launch re-read the CPU Attempt rows and
used the existing accounting fold to carry forward 2.69332700000000004 USD;
its 6 USD allocation did not reset the cohort. The resource sample reported
3.55352699999999987 USD. The exact accumulated amount is
6.24685399999999991 USD (about **6.246854 USD**), with no declared/unknown
reservations, leaving about **13.753146 USD** under the approved ceiling.
This unused amount did not authorize a retry. Intra-session tool failures in
both samples, including the repaired CPU-check error, remain in their transcripts.

The cohort's prior charged amount including unknown reservations was initially
unknown and was not treated as zero; the user subsequently explicitly supplied
**0 USD**. New actual model fees are learned from the existing accounting,
while unreported Attempts retain their declared reservations. Every later
sample's allowance must be reduced by all
prior accounted usage; changing directories does not reset that amount. The
existing runtime limits admission using declared/reported charges; a provider's
already billed final response can exceed its requested invocation ceiling, so
this is not a guarantee about external billing. Do not automatically retry a
failed paid sample. Real notifications are excluded. The OpenAlex key-file
source was supplied and locally checked at that initial stage; the later online
sections record actual authentication, retrieval, quota and usage. The initial
preflight alone was not a network result.

### Cleanup and local evidence

After the fresh G3 and installed-wheel runs, separate host-level inspection
and read-only SQLite counts found **all 156 recorded process groups absent**,
no related live Hypoloom process, no remaining socket and no private tmpfs
mount. All eight run Oracles and the CPU-only pool Oracle were clean, with no
open Attempts, pending effects or owned process residue. The managed service
was collected automatically. Test directories, transcripts and the debate's
26-file disposable target under `/var/tmp` are retained as evidence; they are
not live processes or reserved compute resources. No pre-existing run was
started, resumed, cancelled, migrated, settled or deleted.

After the authorized G4 run, independent read-only inspection found four
`COMPLETED` runs, all 80 recorded process groups absent, and no related process,
socket or private mount. Both private pools have exactly four RELEASED
requests and zero QUEUED/RESERVED/LAUNCHING/BOUND requests; their services
closed. All four disposable G4 target repositories were removed by the tests.
The pre-existing PID 216670 remained present on device 3, and no test CUDA
process remained in NVML. No existing pool or research run was changed.

Both paid G5 Hosts have also closed. Their combined **22 Agent + 2 Process
Attempts** are CLOSED/SUCCEEDED, both run Oracles are clean, and all **24
recorded process groups** are absent. The resource pool has one RELEASED
request and zero pending/bound requests, a clean pool Oracle, no socket and
no live server or test CUDA process. The pre-existing process 291734 on the
selected GPU remained present in the after-inventory. Temporary target/data
fixtures remain as evidence at the paths recorded by the existing tests; they
hold no live processes or reserved GPU capacity.

Detailed audit outputs are under ignored
`.local/qualification/preflight-20260914/`: `environment.json`,
`prior-evidence-compare.json`, `evidence-reuse.json`, `current-coverage-map.json`,
`static.json`, `g2.xml`, `g3.xml`, `focused.xml`, `enospc.xml`,
`managed-wheel.json`, `g3-durable-counts.json`, `cleanup.json`,
`compaction-observation.json`, `profile-checks-corrected.json`,
`placeholder-model-probe.json`, auth/resource metadata, command logs and
`g3-evidence/`. Original import/build/browser/failure logs remain beside this
subdirectory. These are local outputs, not published artifacts or a new
qualification authority.

The authorized follow-up is retained under
`.local/qualification/live-20260914/`: `g4-command.json`, `g4.log`, `g4.xml`,
`g4-cleanup.json`, complete `g4-evidence/`, before/after GPU inventories,
`candidate-before.json` and the credential-free approved-auth preflight.
Source comparison against the previous final archive found no checkout
candidate differences before this follow-up's documentation edits; the only
raw absent file was the build-generated `setup.cfg`, not a runtime source.
No new runtime or test dependency was installed for G4.

Paid evidence is in the same `live-20260914/` directory: `g5-local.log/xml`,
`g5-resource.log/xml`, both command/cleanup JSON files, complete
`g5-local-evidence/` and `g5-resource-evidence/`,
`g5-local-native-observations.json`, and `g5-resource-wrapper-replay.json`.
The GPU failure's original JUnit/log was not replaced. The existing focused
wrapper selection then passed **19 tests, 48 deselected**, 1.22 s, offline;
it verifies the current recognizer, not the paid canary's promotion. See
`g5-wrapper-focused.log/xml`. No test/runtime code or dependency was changed.

The later configuration-source question was checked against the actual CLI,
managed launcher, private-settings lifecycle and OpenAlex reader. The local
credential-free reproductions are in `live-20260914/config-source-probes.json`:
the Host call was intercepted before startup and the OpenAlex probe used a
temporary dummy key. Neither probe made a network request; the temporary root
was removed. The two resulting configuration findings above remain unfixed.

At the end of the initial preflight, the Profile/Skill/documentation selection
passed **43/43**. Wheel and source distribution builds succeeded without
build dependency downloads or dependency installation. The rebuilt wheel's 131
runtime files match both the checkout and installed environment; the source
archive has 552 files, all required public assets, and only the two multi-expert
template directories. See `docs-final.xml`, `build-final.log` and
`package-final.json` in the same ignored evidence directory.

After the authorized G4 follow-up, the documentation checks passed **4/4**
and Git whitespace checks passed. The final ordinary source comparison found
only `docs/qualification.md` changed from that earlier source archive; runtime
and test code stayed unchanged. See `live-20260914/docs-final.xml` and
`live-20260914/candidate-after.json` under the ignored qualification directory.

The final G5 report update also passed all four documentation checks and Git
whitespace checks. The final source comparison still differs from the initial
preflight archive only in this document; see `g5-docs-final.xml` and
`g5-candidate-after.json` in `live-20260914/`.

The supplied Profile/key follow-up is retained in
`.local/qualification/merc-causal-20260914/`: the original and corrected
`profile-check` commands/stdout/stderr, `candidate-profile-environment.json`,
`erc-runtime.json`, `erc-source-import.json`, `erc-data-check.log` and its
command, shallow cache/split metadata, local PDF extraction logs,
`paper-reference-check.json`, and `read-only-resource-inventory.json`.
`repro_common_interaction.py`, `common-interaction-defect.json`, its original
failed assertion log and command reproduce the applicable high-priority defect
without creating a Host or acquiring writer authority on the retained G3 DB.
The follow-up made **zero model calls, zero network literature requests and
zero notification deliveries**, and created no Host/Agent/experiment/pool,
listener or GPU allocation. Its ordinary inspection subprocesses exited.
Prior test cleanup remains as recorded above; the occupied Web listener and
other existing services, original Profile, key, target/data and runs were left
unchanged. This follow-up adds no charge to the **6.246854 USD** cohort usage.

## Promotion rules

| Stage | Required outcome |
|---|---|
| G0 | Close each reproduced repair failure with focused regression and an observable outcome. |
| G1 | Freeze the intended code/test candidate; compile, inspect imports/schemas/contracts/Skills/docs, run the full offline suite, and validate installation/package assets. Unexpected failure is NO-GO. |
| G2 | Audit every retained run in the declared supported cohort without mutation; compare expected findings, logical rows and artifacts, and replay applicable persistence/recovery fixtures. Lost or new unexplained findings are NO-GO. |
| G3 | Close actions through the real resident Host, private runtime bundle, broker, Agent, Process, collection, publication, recovery and next action. Include injected exits and complete descendant cleanup. |
| G4 | Exercise several bounded hypothesis-evolution cycles with real CPU/GPU jobs when applicable, including sharing, waits, failures and recovery. Count externally observed effects and require no silent stall or owned residue. |
| G5 | Run conditional, short real-model compatibility canaries under explicit wall/turn/cost/resource limits. These sample native prompt/tool/adapter compatibility, not scientific quality or deterministic endurance. |

G3/G4 closure is the principal engineering signal. A long scientific run or
24/72-hour soak is optional and risk-triggered, not the default integration
test. A later gate cannot compensate for an earlier failure. Preserve original
failures and unknown charges; a retry is a separate sample. Source changes
require refreshing the affected evidence and final candidate checks.

Record the Git candidate, environment, commands, test results/skips, action
counts, resource/cost limits, observed cleanup and remaining gaps in the current
qualification report. Use ordinary Git, pytest JUnit output and the existing
run/pool Oracles; do not create a parallel authority or checksum protocol.

## Install and offline checks

Use the pinned [environment](../environment.yml), then install the project
with the development extras shown in [SETUP](../SETUP.md):

```bash
python -m pip install -e '.[dev,monitor]'
python scripts/qualify.py g1 --junitxml .local/qualification/g1.xml
python -m build --outdir .local/dist
```

Also install the wheel into a separate environment and run its CLI/MCP and a
fresh disposable bundle from outside the checkout. Check migrations, native
Skills/plugins and Web assets are present. Source tests deliberately remain
outside the wheel. Import-cycle, Schema, tool, Skill and documentation tests
are part of the offline suite. Missing optional facilities are explicit skips.

## G2: fixtures and retained runs

```bash
python scripts/qualify.py g2 --junitxml .local/qualification/g2-fixtures.xml
```

This replays existing Oracle, transaction, schema migration, backup/restore and
bundle tests. **It does not replace the retained-run cohort.** Public parser
fixtures live under `tests/fixtures/profiles/`; private research data is not
distributed.

For every retained Hypoloom run in the candidate's supported format, use an
isolated installed reader and the accepted baseline reader to perform the same
read-only audit, for example:

```bash
/absolute/reader/bin/python -B -m hypoloom.cli oracle \
  --state-root /absolute/retained/root --run-id EXACT_RUN_ID
```

Compare existing audit results and expected historical findings; independently
check that logical state and artifact contents did not change. An existing
OPEN Attempt is a historical fact, not permission to settle it. Do not start a
Host to perform G2. If the format requires its original bundle, use that reader
and record this narrower scope. Old-project runs remain in the source project
with their own readers; Hypoloom does not claim to migrate or resume them.

The first import has no retained Hypoloom release cohort. Record that coverage
gap explicitly; do not manufacture a legacy compatibility pass or treat fixture
replay as full G2. Future retained Hypoloom controls use their existing stable
run identities and outcomes, with failures retained as well as successes.

## G3: production-process closure

```bash
python scripts/qualify.py g3 --junitxml .local/qualification/g3.xml
```

The seven selected scenarios cover two CPU cycles and observation, committed
submit crash/reentry, three-round debate restart, common acceptance and goal
stop, premature execution completion, resource-skill learning, and TUI
leave/reentry/cancel/Host exit. They use temporary run roots and local scripted
providers, with actual native CLI/bwrap/Host and CPU processes where applicable.
Install Claude Code and bubblewrap first; absent dependencies and resulting
skips mean the affected production boundary is unproven.

## G4: shared CUDA loops

First authorize exact GPU UUIDs and a runtime with PyTorch/CUDA/NVML support.
The existing shared tests need two selected UUIDs and enough available memory;
they prove concurrent work on an individual card as part of their cross-Host
cohort. No permanent resource service is installed by the tests.

```bash
export HYPOLOOM_REAL_GPU=1
export HYPOLOOM_REAL_NATIVE_GPU=1
export HYPOLOOM_REAL_SHARED_GPU=1
export HYPOLOOM_REAL_GPU_UUID=GPU-REPLACE_WITH_AUTHORIZED_UUID
export HYPOLOOM_REAL_GPU_SECOND_UUID=GPU-REPLACE_WITH_SECOND_AUTHORIZED_UUID
export HYPOLOOM_REAL_GPU_RUNTIME=/absolute/approved/cuda-environment
python scripts/qualify.py g4 --junitxml .local/qualification/g4.xml
```

The cases cover ordinary measured learning and an initial memory estimate that
waits for capacity, then calibrates into same-card overlap. Require terminal
run/pool Oracles, actual CUDA results, complete attributable samples and zero
owned process/resource residue. Existing research pools are not test resources
unless that exact use was separately authorized.

## G5: real-model compatibility

All live tests remain disabled by default. Select an exact executable, existing
regular mode-0600 settings file and model. Resource availability is not spending
authorization. Review each cohort's caps first, including prior charged costs
and unknown reservations; missing cost is never zero.

Shared authentication inputs are `HYPOLOOM_CLAUDE_EXECUTABLE`,
`HYPOLOOM_CLAUDE_SETTINGS`, `HYPOLOOM_CLAUDE_MODEL` and the explicit
`HYPOLOOM_REAL_CLAUDE=1` opt-in. For the local or common cohort, provide
`HYPOLOOM_G5_CUMULATIVE_LIMIT_USD` (positive and no more than the existing 20 USD
canary ceiling) and `HYPOLOOM_G5_PRIOR_CHARGED_USD` (nonnegative and below that
limit). Declare zero only for a genuinely unspent cohort. The remaining amount
becomes the run budget, with existing invocation, turn and wall limits retained.

USD values are CLI/Host dispatch thresholds, not guaranteed upstream billing
caps. An in-flight response can report more than its remaining threshold; retain
that charge and stop further dispatch. Fund retries, analysis and the final
report when reviewing each sample, and keep unknown costs reserved in the same
cohort. A failed 4 USD reading sample is not evidence that another 4 USD will
complete; increasing its allocation requires explicit approval, not a changed
directory or a weaker closure check.

Select exactly one mode per invocation:

| Selection | Additional opt-in | Test |
|---|---|---|
| `g5-local` | `HYPOLOOM_REAL_CURRENT_G5=1` | Native file tools, CPU execution, sessions and final report |
| `g5-common` | `HYPOLOOM_REAL_CURRENT_COMMON_G5=1` | Grounding-authored common parser, goal evidence and final report |
| `g5-resource` | `HYPOLOOM_REAL_SHARED_RESOURCE_G5=1`, GPU/native GPU opt-ins, exact UUID/runtime and explicit `HYPOLOOM_RESOURCE_G5_ALLOCATION_USD` | Native resource Skill, real CUDA execution and learning |

The local/common flags are mutually exclusive. Run `python scripts/qualify.py
g5-local`, `g5-common` or `g5-resource` after reviewing and setting the inputs.
Each reports its original outcomes and actual charges; preserve reservations
for unknown usage. Do not automatically rerun a failed paid sample or reset its
cost when choosing a new directory. After success or failure, verify all owned
Hosts, Agents, experiments and pool requests are closed.

## Listing tests without effects

```bash
python scripts/qualify.py g3 --collect-only
python scripts/qualify.py g4 --collect-only
python scripts/qualify.py g5-local --collect-only
```

Collection does not enable resource/paid flags. Clear live opt-ins before G1,
G2 fixture replay or G3; the selector rejects those accidental inherited flags.
For a focused repair, invoke pytest directly on its relevant tests before
freezing a candidate for the full funnel.
