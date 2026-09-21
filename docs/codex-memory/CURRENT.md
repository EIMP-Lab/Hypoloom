# Current Hypoloom development state

- September 22: local init/configuration repair adds `hypoloom.bootstrap`
  paths/prepare/launch. Filled template previews reuse ordinary admission;
  only explicit --write creates a new Profile, and --start uses the existing
  managed Host/CLI sequence with payload rechecks and ambiguous-create STATUS
  reconciliation. No launch manifest, protocol version or state authority is
  added. Credentials now enforce 0600 on the opened file; the single public
  template moves to config/hypoloom.env.example with visible bilingual entry
  points. Private credentials and local Profile parameters remain unchanged;
  MERC-causal still admits read-only. Final review also fixes malformed nested
  credential references and runs helper/CLI subprocesses in the selected
  checkout. Final focused/CPU selection: 379 passed. Source distribution includes
  all new entry assets. No paid/GPU/systemd-live qualification or research-run
  mutation; the user explicitly authorized committing and pushing this repair
  to dev after rechecking. See the
  [init repair evidence](../qualification.md#current-development--deterministic-init-and-configuration-entry-2026-09-22).

- September 22: local repair candidate uses ACI 22 for an optional EXECUTION
  handoff to Host waiting. It closes/charges the invocation, resumes the same
  node/session only after collection, and permits one handoff per experiment;
  short experiments can still complete directly. Existing budgets, IDs and
  lifecycle state remain authoritative. Tracked directory deletion now passes
  Git capture/commit without relaxing live path checks; failed commit/archive
  closure retains available transcript, metadata and provider accounting.
  GPU reserve attribution is corrected in local-compute/7; unknown ownership
  and reserve/grace protections remain. Leader/Debate skills compare actual
  evaluation protocols; local/public Profile parameter choices are unchanged.
  Final focused selection: 134 passed; a strengthened native CC/local-provider
  CPU timing case also passes (same identity refreshed), proving controller
  release before Process exit and collection afterward. Evidence is in the
  [repair entry](../qualification.md#current-development--execution-handoff-and-session-repair-2026-09-22).
  New runs only; no live-run mutation or paid/GPU qualification. The user reports
  the retained run is terminated and authorizes committing/pushing this repair
  to dev; this does not authorize restarting or migrating that run.

- September 22: Web listeners default to OS-assigned port 0 across TUI,
  standalone CLI and automatic dashboards. Profile v4 now accepts 0; public
  en/zh templates and ignored local MERC notify configuration are updated.
  Explicit fixed ports keep conflict/retry behavior. Notifications use the
  owned socket port or local-access guidance without bearer tokens. Existing
  socket/dashboard ownership is reused; no port registry or run migration.
  Focused validation: 273 passed (42.88 s), local MERC projection has no issues;
  no real remote delivery, paid/GPU run or new full qualification. See
  [port repair evidence](../qualification.md#current-development--dynamic-web-ports-2026-09-22).

- September 21 readiness continuation: the user approved `glm-5.3-flash`,
  a fresh 13 USD cohort (CPU 7 / GPU 6). Following the user's report that GPU
  0+1 are free, G4 now uses GPU 0+1 and G5 GPU 1; the original GPU 2+3 wait
  stopped before either lane started. G1 current coverage is 5,867 passed / 26
  opt-in skips after retained
  fixture-failure refreshes; installed delivery, ten unchanged retained-run
  audits (one known historical issue), and native ACI-21 Leader/Debate/six-CPU
  closure pass. Sdist now includes the six previously omitted public guide
  assets (dotenv example/five screenshots). The pre-batch ACI-13
  pending-update/early-verdict defect is fixed
  and all 15 legacy integration cases pass. CPU G5 passes: GOAL_MET, 11 Agents
  plus one actual CPU Process, 4.283047 USD recorded, no unknown charges or
  process residue. Initial GPU G4: 9 passed / 1 failed, one final
  `process_exited` attribution sample; all eight CUDA Processes succeeded,
  four run Oracles and two pools are clean, no process residue. The service
  stopped before G5. One unchanged unpaid recheck passes in 140.54 s with
  complete samples, clean Oracles/pool and 40 closed Attempts. G4 now has
  all ten identities covered, retaining the original failed sample. G5 GPU 1
  passes in 743.60 s: COMPLETED r18, 11 Agent + one CUDA Process, checksum 6,
  13 complete measurements / zero unknown, 362 MiB owned peak. Independent
  post-close Oracle/pool checks pass, all 12 groups and sockets are absent;
  the service exited zero. GPU cost 3.633895 USD, total cohort 7.916942 USD
  against 13 USD, no unknown charges. Current evidence supports a fresh bounded
  scientific-topic run; paid multi-expert decision quality, historical TUI-crash
  attribution and concurrent-client latency remain unqualified. No long topic
  was launched, and no old-run mutation is authorized. Evidence lives under
  `.local/qualification/aci21-20260921/`. See [continuation evidence](../qualification.md#readiness-qualification-continuation-2026-09-21).

- September 21: uncommitted ACI-21 repair implements the user's chosen Leader
  responsibility: coordinate from verified intermediate contributions before
  final goal completion, including evidence-linked clean refutations. Exact
  accepted Leader/Debate parents authorize research despite null best_chain;
  each debater progresses after its own source cycle closes. Control reads,
  charge caching and bounded asynchronous view preparation reduce historical
  decoding on the Host thread. End-run rejection text persists; TUI exceptions
  enter existing diagnostics; final-report citation validation runs inside the
  native Stop hook. See the [repair evidence and limits](../qualification.md#current-development--responsive-control-and-evidence-directed-research-2026-09-21).
  Validation: 859 focused passes, two real CPU interaction cases, the negative
  case with two same-version Host reconstructions, and native CLI final report;
  independent audits and recorded process-group cleanup passed.
  The prior MERC run was explicitly closed with its original bundle; it remains
  read-only. No migration or new paid/GPU research was authorized or performed.
  The historical TUI exit cause and concurrent-load latency targets remain
  unconfirmed; this is development evidence, not a frozen release qualification.

- September 20 (thirteenth round, uncommitted): homepage badge row gains two
  fact-based shields — `platform-linux` (hard requirement: bubblewrap, user
  namespaces, user-systemd) and `requires-Claude Code` (the agent runtime
  dependency). Deliberately not added: build/coverage/code-style badges (no
  CI workflows and no ruff config exist to back them), version badges
  (0.0.1, no frozen release). No runtime change; documentation-structure
  4/4.

- September 20 (twelfth round, uncommitted): CONTRIBUTING (en/zh) gains a
  "Reporting issues / 报告 Issue" section combining the GitHub issue rules
  with the project's Report issue machinery and its three disclosure levels:
  local-only `debug inspect` (payloads stay on the machine), the default
  support bundle (run/Attempt IDs pseudonymized; research text, free-form
  errors, local paths and attachment names filtered/reduced — public-issue
  safe after user review), and `--include-private-data` (sanitized prompts,
  transcripts and research attachments inside; known credentials and raw
  environment variables still excluded — private channel only). It also
  states preview-before-export, review-before-attaching, no auto-upload, and
  no keys/credentials/private datasets anywhere. A follow-up sentence states
  that sensitive bundles may be sent privately to the project authors, who
  use submitted material only for diagnosing the reported issue, restrict it
  to the maintainers involved, never redistribute it, and delete it after
  resolution or on request — a joint commitment to personal-information
  safety; the bug-report template links the section and carries the short
  bilingual form. No runtime change; documentation-structure 4/4.

- September 20 (eleventh round, uncommitted): profiles/README.md intro now
  explains `schema_version` in one bilingual sentence — v4 is the format of
  this open-source release; earlier versions predate it and survive only as
  parser test fixtures — so a public reader is not left wondering what v1–v3
  were. The version number and the v3 parser/fixtures stay: they are honest
  lineage and live regression tests. No runtime change;
  documentation-structure 4/4.

- September 20 (tenth round, uncommitted): Start here gains a compact
  Profile/credentials orientation instead of duplicated rules — six YAML
  files (goal, experts, methodology, language, notify, safety) copied by
  init into `profiles/local/<name>`, placeholders bound by the user, and a
  single mode-0600 dotenv credentials file in an ignored directory
  (`profiles/templates/hypoloom.env.example`) referenced by absolute path —
  pointing to profiles/README.md and SETUP as the authoritative
  construction rules. The now-redundant "template paths are placeholders"
  sentence is folded in. No runtime change; documentation-structure 4/4.

- September 20 (ninth round, uncommitted): homepage section headers (## and
  ###, both languages) gain one semantic emoji each — 🧩 capabilities,
  🔍 inside a run, 📊 overview, 🧑‍🔬 experts, 🧬 lineage, 📈 metrics,
  💻 terminal, 🚀 start, 📚 docs, 🗺️ roadmap, 🚧 status, ⚖️ license —
  applied by an exact-match script (12 unique headers per file); no in-repo
  anchor links to README sections exist, so GitHub anchor changes are safe.
  No runtime change; documentation-structure 4/4.

- September 20 (eighth round, uncommitted): both homepages gain a Roadmap /
  后续计划 section (placed before Development status) naming two directions
  in no fixed order, phrased as plans-not-commitments: extending the
  Claude-Code-driven expert runtime to other coding-agent runtimes (Codex,
  Kimi Code, OpenCode and similar) behind the existing per-binding
  qualification path, and academic follow-up around the emerging AutoResearch
  benchmark line (adaptation plus comparative evaluation design). No runtime
  change; documentation-structure 4/4.

- September 20 (seventh round, uncommitted): README polish. Development
  status sections (en/zh) now state the concrete meaning of "development
  framework" — no frozen release, interfaces/Profile schemas/internal
  contracts can change between candidates — and add that model/provider
  compatibility is established per sampled binding, not in general; all
  original caveats (no scientific benchmarks/claims, per-candidate
  qualification, no cross-version migration) are kept. License notice moves
  from the individual "Hui Li" to the organization identity the user named:
  `Copyright (c) 2026 EIMP Lab and the Hypoloom Authors`, identical in
  LICENSE and both homepages (legal holder string kept verbatim, untranslated
  in the Chinese README). No runtime change; documentation-structure 4/4.

- September 20 (sixth round, uncommitted): user-facing docs are now bilingual
  pairs following the README convention (`X.md` = English, `X.zh-CN.md` =
  中文), each with a language-switcher line at the top. The Chinese-primary
  SETUP/USAGE moved to SETUP.zh-CN.md / USAGE.zh-CN.md (git mv, internal
  links re-pointed) and gained faithful English versions as SETUP.md /
  USAGE.md; English docs gained Chinese translations:
  docs/operator-guide.zh-CN.md, docs/architecture.zh-CN.md (full),
  docs/qualification.zh-CN.md (frame, promotion rules and G2–G5 lanes fully
  translated, plus a current-status summary of the 2026-09-19 entry and a
  dated index linking the English ledger, which stays the canonical single
  source for full evidence), docs/README.zh-CN.md and
  CONTRIBUTING.zh-CN.md. Both homepages now link same-language docs from
  their Documentation sections (English README → English files, 中文主页 →
  zh-CN files; profiles/README.md stays one bilingual file; AGENTS.md /
  CLAUDE.md stay English-only maintainer/runtime references, labeled as
  such). MANIFEST.in includes the new root zh files. No runtime change;
  evidence: documentation-structure 4/4, public-profiles 3/3 and
  operator-skills 36/36 suites pass (43 total).

- September 20 (fifth round, uncommitted): README homepage gains an "Inside a
  run" section (中文 README 对应 "运行实况") showcasing the two read-only
  observation surfaces with the five user-supplied screenshots, moved from the
  repo root into `docs/assets/`: web-overview.png (Operations overview:
  durable state/revision/Oracle verdict, counters, execution reliability,
  filters), web-expert.png (expert cards with stage pipeline, breathing-light
  agent tiles, debate pause and leader crown), Hypo.png (hypothesis lineage
  with edge semantics and pruning as control decision), web-metrics.png
  (metric trends per initial-hypothesis chain against preregistered target
  lines) and TUI.png (terminal Research Operations snapshot and operator
  actions). Both files point to USAGE for `hypoloom web` / `hypoloom monitor`.
  No runtime change; `tests/test_documentation_structure.py` 4/4 passes.

- September 20 (fourth round, uncommitted): debate/leader presentation on
  expert cards, all derived from existing snapshot fields (no backend
  change). The red `.blocked` pause text is replaced by a structured
  `.pause-notice`: amber "Debate proposed · dispatch paused" while a
  debate-reason pause (`debate:<attempt id>`) has no OPEN DEBATE attempt,
  violet "Debate in progress · this expert is a debater" with a
  Leader & Debate workbench link once that expert holds an OPEN
  DEBATE/DEBATE_JUDGE attempt, neutral gray for non-debate reasons;
  tooltip keeps the full reason, scope and dispatch-only semantics. An
  amber ♛ `.leader-crown` marks the expert heading while it holds an OPEN
  LEADER attempt; debate tiles get a violet border, leader tiles amber;
  EXPERT_STAGES gains Debate round / Debate judge / Leader round labels.
  Verified live against MERC-causal-202609200023: the two active pauses
  (engineering + affective, from OBSERVER attempt 5e40190f at 03:12 UTC)
  are durable participant_control records with no resume yet and no DEBATE
  attempt dispatched — the pre-debate state is genuine run dynamics, not a
  display defect. Evidence: browser/monitor/web suites 74/74 including new
  notice-state, crown and tile-class assertions.

- September 20 (third round, uncommitted): Working rows replaced by compact
  agent tiles per the user's reviewed design. `monitor.AttemptView` gains
  `budget_max_tokens` (durable `request.budget.max_tokens`, AGENT-only,
  positive int); asdict flows it to the web snapshot. Each tile is a square
  breathing light (expert round dot stays aggregate) plus a capsule bar:
  fill = context_tokens / budget_max_tokens capped at 100% (observe mode
  may exceed; a compaction shows as a visible drop), embedded right-aligned
  tabular K/M value (`—` when unknown), PROCESS tiles show elapsed instead.
  Light encodes the poll's host observation (expert-accent breathing with a
  deterministic negative-delay phase hashed from attempt_id when
  `overview.host_observation.availability == "responded"`, amber static
  otherwise; reduced-motion static); overdue = amber capsule border; tiles
  are keyed by attempt_id and reused across renders, sorted by
  started_at/attempt_id, enter with opacity/scale only, fade out 300 ms on
  close, cap 6 + dashed "+N more" tile; click opens expert history.
  Evidence: monitor 44 + web 13 + observer-summary/live-status/support 41 +
  browser 17/17 (rewritten working-subagents case asserts order, fill
  widths, overdue/host-unconfirmed classes, per-tile breath phases); live
  read-only snapshot of MERC-causal-202609200023 yields context 81.8–164.5K
  with 1M budgets for all five OPEN agents. Same caveat as round two: a
  running observer keeps its imported monitor.py until restarted; run
  bundles stay read-only.

- September 20 (second round, uncommitted): expert-card "Working" rows
  always showed `context —` / `GPU —`. Root cause: an OPEN attempt's
  durable observation carries only `request` + `session_binding`
  (state_store/oracle validated); `observation["session"]` and
  `resource_usage` exist only after close, and
  `monitor._session_transcript_path` read only `session` — so the live
  transcript lookup never resolved for OPEN agents since the feature
  shipped in f2de65a. Fix: `_session_transcript_path` falls back to the
  `session_binding` identity (same confinement/is_file validation;
  verified against run MERC-causal-202609200023's live transcripts, e.g.
  ml-expert CYCLE_REPORT ctx=133,629 from the tail). Per user direction,
  PROCESS rows no longer show GPU (utilization is close-time data anyway)
  and now show `elapsed <duration>`; AGENT rows keep
  `context ≥<K/M>`. Evidence: monitor/web-observer/browser-subagent
  suites 58/58 including new binding-fallback cases; real-run read-only
  snapshot now yields live context. The running observer keeps its
  already-imported monitor.py until restarted; run bundles stay
  read-only.

- September 20: Web observer UI fixes (committed 3ffdb5b on dev, `web_observer_assets`
  only, no runtime/server change): single `HYPOLOOM` eyebrow (was the
  duplicated `HYPOLOOM / HYPOLOOM`); Report issue + Help / terms grouped in
  one right-flush `.help-actions` cluster (both had `margin-left: auto`,
  which split the free space between them) with amber `#201C12/#EAC6A5`
  styling distinct from the blue controls; added a Text size select
  (Compact 87.5% / Standard 100% / Comfortable 112.5% / Large 125%) that
  scales the rem-based page via root font-size, defaults to Comfortable
  (`html { font-size: 112.5% }`), persists in `localStorage`
  (`hypoloom.text-size`, storage-disabled contexts keep the session-only
  default). Evidence: `test_web_observer.py` 13/13, two CDP browser tests,
  headless-Chrome screenshots of the data-URL fixture at both scales.

- September 19 (repair round, uncommitted on dev @ f2de65a): read-only
  analysis of the terminated run MERC-causal-202609172306 found four
  defect classes; two independent audits (local records under `.local/`)
  turned them into eight directives that are now implemented with their
  audit must-dos. ①a oversized prompt inputs render as /inputs
  advertisements (request bytes and oracles unchanged; OBSERVER-class
  prompts 775→93 KB in replay), ①b CLI-uncompactable failures classify
  as input_too_long only for stateless commands, ②c TERMINATE_LINEAGE
  prompt text now matches the enforced permanent-close semantics, ①d
  per-tick worktree retirement is history-gated and dirty-memorized
  (~20 s/tick → flat ~5 ms), ②e End-Run modal reconciles durable
  acceptance and monitor STATUS backs off, ③ the GPU pressure backstop
  spares externally-attributed pressure (`free + owned ≥ reserve`,
  exp-10's 3,901+552 ≥ 4,096), records attribution/grace in the renamed
  payload, and uses a 30 s terminate grace with the zombie-reap fix.
  Adapter /62, compute /6, runner /3; new runs only. Evidence:
  qualification's top section — final-tree G1 5,843 passed (3 harness
  failures fixed and refreshed), G2 retained 8/8 (7 clean + 1 known
  historical, bytes unchanged), G4 pressure attribution lane green on
  the two authorized UUIDs (main lane 9/10 with the documented
  zero-unknown flake), G5-local paid sample passed (GOAL_MET, exact
  2.83 USD accounting, real model reads offloaded /inputs).
  Freeze leftovers: commit grouping C1–C4 awaits the user's explicit
  request; the recorded residuals (single-sample unknown attribution,
  non-blocking cancel, remaining full-table scans) are follow-ups.

- September 19: the local Claude Code auto-updated to 2.1.277 (binary
  replaced 10:07), whose headless sessions now save cumulative cost/usage
  totals at exit and seed `--resume`/`--fork-session` launches from them.
  Four native G3 tests failed on the unmodified tree (all green under
  2.1.275): same-session retries double-billed their predecessor
  (`assert 35 == 29`, `assert 11 == 5`), and a chain fork inheriting the
  synthesis parent's different-model ledger failed
  `multiple_model_identities`, completing a run with zero outcomes. Root
  cause was external, not one of the five in-flight workstreams. Repair:
  `agent_sessions.prepare` records the newest `cost-state` record of the
  transcript it hands CC as `session_usage_baseline` (none for older
  CC/fresh sessions); `claude_agent_host` deducts that exact carry
  (`_session_usage_view`) before per-Attempt accounting, drops models the
  invocation did not use, and records `provider_session_carryover` in
  diagnostics. The two harnesses asserting on the raw native ledger
  (model-usage, structured-output-recovery) derive the same invocation view
  from the archived prior transcript; no assertion was weakened. All four
  native files plus `test_full_cc_host_g3.py` are green under both 2.1.277
  and 2.1.275; 306 focused cases pass. Details in qualification's top
  section.
- September 17 evening, under the user's direction: `/autoresearch init` now
  detects and — only through the reviewed plan — deploys this project's own
  GPU pool, replacing the reliance on an operator-supplied external endpoint.
  Trigger: a new run's intake found no socket in its searched locations and
  wrongly called `MERC-causal-202609170117` nonpooled (its durable CREATED
  payload carries the `gpu_pool` binding); the user exited because that pool
  belongs to the Prometheus project. New helper
  `.claude/skills/autoresearch/scripts/manage_gpu_pool.py` mirrors
  manage_host.py's user-systemd discipline: read-only `status` (conventional
  `.local/gpu-pools/<name>/control.sock` liveness plus newest-first recall of
  stored bindings from mode-ro state DBs) and bounded `ensure` (pool root only
  beneath the repo's `.local/gpu-pools/`, invoking UID only,
  `hypoloom-gpu-pool@<escaped-root>.service`, idempotent `reused` on an exact
  match, `foreign_pool_root`/`service_busy`/`pool_conflict` refusals, new unit
  stopped on readiness failure; no stop/replace). SKILL.md keeps all existing
  pool cautions; deployment is helper-only, after Start now, before the
  managed Host start; Prepare only never runs it. Evidence: 13 new helper
  cases + 36/23/4/3/27 adjacent suites pass; details in qualification's top
  section. The Prometheus pool, existing runs, Profiles and credentials are
  unchanged; the first real project-pool creation happens at the next actual
  init as the operator-approved action.

- September 17 morning: read-only analysis found run `MERC-causal-202609170117`
  (ACTIVE r48, Host PID 2669721 alive but idle-spinning ~100% CPU by serve-loop
  design) wedged since 05:27:35 +08: a provider 403 window (five-hour rolling
  quota, user-confirmed) produced 15 rapid auth failures (04:14–05:27) that
  exhausted infrastructure retries on all three experts' nodes within seconds
  each (next-tick redispatch), permanently closing every component's control
  loop; durable totals: 3 CLEAN_REFUTATION experiments (all below the
  MELD>0.6674 / IEMOCAP>0.7305 targets), 2 REFUTED assessments, 59 Agent + 3
  Process Attempts all CLOSED, 23.242795 USD known model cost, pool
  reservations all RELEASED. The run cannot resume science (final failures are
  durable; Host restart does not un-exhaust them); cancel/restart awaits the
  user's decision. Under the user's repair authorization (unattended 5h
  cooldown plus TG/TUI alert with manual "credential restored" resume;
  5h-limit and instability 403s are treated as one class), the uncommitted
  tree now: classifies upstream 401/403 as `auth_failed` in
  provider_observation; extends the durable `_provider_retry_pending` dispatch
  gate (via `provider_retry_gate`) to hold new Agent dispatch until
  `budget.provider_auth_cooldown_seconds` (default 18000, 0 disables, cap
  604800; admitted through agent_budget/component_contract); adds the operator
  control `PROVIDER_RETRY_CLEAR` (CREATED/ACTIVE self-loop) whose durable
  transition supersedes only earlier failures, exposed as CLI
  `provider-retry-clear`, TUI banner + `p` modal (provider_resume_tui.py), and
  STATUS `provider_retry`; derives one-per-episode `provider_auth_cooldown`
  and `provider_auth_resumed` product events (none when disabled) through the
  existing tailer into the template/local Profile telegram lists. Focused
  suites pass (80 new/extended + 162 + 179 + 43 adjacent); the LOC budget
  allocation was explicitly reallocated (workflow_host 4500→4550, lifecycle
  550→500). Evening: full G0–G5 batch ran at the user's request (evidence in
  qualification's top section): G1 5,746/0 failed, G2 174/174 with the
  retained cohort byte-identical, G3 7/7. G4 failed 2/10 purely on a foreign
  tenant pinning GPU 0/3 memory (zero provider/auth involvement, roots
  retained); both paid samples failed on a k3 token-accounting mismatch
  (`invalid_provider_metadata`) unrelated to the repair — G5-resource was
  then left unlaunched, preserving its 5 USD. Known paid spend 11.082952 USD.
  Read-only adjudication after the batch: the k3 endpoint's stream-start
  usage runs a few tokens above its final per-request usage (wire-confirmed,
  5/6 responses by 1), transcript assistant events carry that start snapshot
  while `modelUsage`/cost match the exact final sums; the platform lower
  bound reproduces both exactly, so the skew is provider-side, not a 2.1.274
  regression — the wedged run itself (2.1.273 + k3[1m]) already had 15 such
  failures in 62 Agent attempts, and pinning 2.1.273 is no remedy for k3.
  The repair does not revive the wedged run's exhausted components; that run
  was never mutated and stayed byte-identical across G2. Night, under the
  user's follow-up authorization, the wire-final floor repair: the accounting
  guard now prefers `provider_observation.observed_input_floor` (per-request
  final usage from the retained exchange capture, only when complete and
  single-model) over the transcript bound, labeled
  `provider_accounting_floor="wire_exchange"`; incomplete captures keep the
  transcript guard, and a ledger below the wire still fails closed. Evidence:
  14 new wire-floor cases including a real-CLI skewed-session red→green
  (transcript bound 12 > ledger 6 == wire floor 6, attempt SUCCEEDED),
  adjacent 165 + 196, full G1 5,760/0 failed (`g1-wirefloor.xml`); details in
  qualification's top section.

- Same evening, research-runtime session records relocated under the user's
  request: this project's Claude Code transcripts moved from
  `~/.claude/projects/-data-Gray-Project-Hypoloom` into ignored
  `.local/claude/projects/` (0700), byte-identical (9 files, 7,121,623 bytes,
  same aggregate checksum), with a compatibility symlink at the original path
  so future sessions keep writing into the project tree (write probe passed;
  `git check-ignore` confirms ignored). Hypoloom's own records needed no move:
  Host state is `<repo>/state/`, sandboxed agent transcripts live under each
  run's `sessions/` tree, never in the user directory. Not moved: shared
  user-level `~/.claude/settings.json` / `~/.claude.json`, cross-project
  empty `session-env/` residue and history, and the other project's slugs.
  Full `CLAUDE_CONFIG_DIR` isolation documented as an optional operator
  choice, not a second mechanism. CLAUDE.md notes the location.
- Same evening, unified credential storage implemented under the user's
  request and consolidated into one dotenv file under the user's follow-up:
  `.local/credentials/hypoloom.env` (directory 0700, file 0600) now holds
  `OPENALEX_API_KEY` and `UNPAYWALL_EMAIL` (migrated from the
  `~/.config/prometheus/` originals, untouched) plus the
  `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` lines the user fills in.
  `hypoloom/credentials.py` parses the strict subset (`KEY=VALUE`, `#`
  comments) with absolute-path/O_NOFOLLOW/regular/UTF-8/≤8 KiB/duplicate-key
  checks; errors name keys and line numbers, never values. The Profile
  declares the single path: `methodology.yaml` `literature.credentials_file`
  (the autoresearch SKILL passes it as `--credentials-file` to serve,
  profile-check and start; the legacy `--openalex-api-key-file` /
  `--unpaywall-email-file` flags remain accepted, never mixed) and
  `notify.yaml` `sinks.telegram.credentials_file` (the tailer reads the two
  Telegram keys; `bot_token_env` / `chat_id_env` remain supported, exactly
  one source enforced at validation and delivery). The path string (never
  secret bytes) is projected into the contract. Public templates (en/zh),
  `profiles/templates/hypoloom.env.example`, the rewritten bilingual
  profiles/README section, the `docs/guides/notifications.md` and `SETUP.md`
  credential guidance, and the local MERC-causal Profile follow the
  convention. Local Profile admits clean; the offline literature preflight
  builds from the real file without network I/O; focused suites pass (294
  combined credentials/tailer/profile/operator-skill/template/CLI/Unpaywall/
  admission/docs + 69 admission standalone + 218 literature-module; two
  `component_batch_profile` fixture errors surface only when one pytest
  invocation mixes `tests/` root modules with `tests/runtime/` modules —
  reproduced with modules this change never touched, green standalone and
  pairwise, and the standard qualify lanes never mix those roots, so a
  pre-existing pytest-9 collection quirk rather than a regression). The
  stopped run's frozen contract is unchanged; the new sources take effect on
  the next run. Telegram delivery is unverified until the user fills the two
  Telegram lines; no paid/online call was made.
- Earlier: the stopped run's Telegram errors were the local Profile's
  notify sink still pointing at the historical Prometheus project's
  `PROMETHEUS_TELEGRAM_BOT_TOKEN` / `PROMETHEUS_TELEGRAM_CHAT_ID` variables
  (absent in the Host environment; earlier qualification documented them as
  an accepted binding with Telegram disabled, but the sink was later enabled
  without renaming). The env-rename first aid was superseded the same
  evening by the unified credential-file design above.
- September 16 evening: the user stopped `MERC-causal-202609161513`
  (CANCELLED r106 at 20:04:15 +08; 85 Agent + 17 Process Attempts, all 102
  recorded groups absent, no socket) and authorized failure analysis plus
  repair, explicitly asking about the 3→6 agent-slot change. One platform
  defect is repaired in the uncommitted tree: `_unpack_provider_stream` now
  tolerates the Claude Code 2.1.273 marked zero-turn resume result
  (`origin.kind == "task-notification"`) before, and `system` lifecycle
  notices after, the terminal result, which had discarded four Stop-hook
  accepted EXPERIMENT_DESIGN_IMPLEMENT candidates as
  `invalid_tool_workspace`. Strict rejection is kept for duplicate real
  results, unmarked/non-zero earlier results, stream_event records and
  post-terminal provider content. Focused 15+210+4 cases pass; read-only
  replay of the four actual failed streams now selects the true terminal
  result. Not repaired (by design or model-side): five capacity_unavailable
  PROCESS LOST (designed bounded wait, amplified by max_active_processes 6
  over ~3 effective GPU slots), five execution-Agent 3000 s timeouts
  (designed handoff), two same-chain measurement declaration mismatches
  (10.637398 + 5.263 USD, correctly retry=MODEL), and the expected
  retryable model-output classes. No concurrency race was found; the slot
  change's real effects are the ~0.9 USD/min peak burn (169.803377 USD
  closed-Agent known cost, null USD caps) and capacity-wait churn. The user
  confirmed per-card multi-experiment packing is intended, so slots are not
  matched to card count: admission accounts 8192+4096=12288 MiB per slot
  (2 per card, 8 theoretical) while the 8 succeeded Processes measured only
  364-786 MiB owned resident peaks (card peaks 3.2-7.7 GiB; one 15.6 GiB
  paired_ceiling_control outlier). Lowering `min_vram_per_experiment` in
  profiles/local/MERC-causal for a future run raises packing density but
  only paces admission, never physically caps usage. Evidence:
  qualification's envelope section and
  `.local/qualification/envelope-repair-20260916/`. No full G1/G4/G5, GPU,
  paid or online run; no Profile/pool/settings mutation, commit or push.

- The user-approved 55 USD supplementary qualification is complete; evidence is
  `.local/qualification/qualification-recheck-20260916/`, qualification's first
  section. Candidate remains 38d80533... / ACI 20, 518 shipped code/test/operator
  files match the repair source package. No new runtime edit. G4 10 passed in
  301.71s: total 64 MiB tensor/256 MiB allocator ceilings per Process, two GPUs
  split 32/128 per card, context additional; real wait/calibration/overlap,
  80 absent groups and two clean pools. Keep original scope deviation and the
  supplemental census's initial pytest-symlink duplicate-count error.
- DeepSeek resource PASS: 653.22s/4.992342 USD, standalone wrapper check, actual
  GPU3 CUDA, 362 MiB owned peak, native measurement history and report. Reading
  PASS: 603.95s/3.946811 USD, two successful readers overlap 1.718s, exact source
  details/parent synthesis, CPU/analysis/report. Preserve first reader's FAILED
  invalid_structured_output/additionalProperties and 0.078368 USD charge; its
  allowed in-run retry succeeds. All 27 paid Attempts are CLOSED/27 groups
  absent. Both paid samples actually report deepseek-v4-pro; 25 native archives,
  no compaction boundary. One approved IJCAI PDF fetch still times out in 15.003s;
  no extra metadata/model query, retry or timeout increase. Online full text
  remains a source-specific gap, not established by synthetic reading success.
- All 107 available test groups are absent, all three private pools clean/nine
  reservations RELEASED, no socket/staging/live owner/GPU compute residue. Paid
  fixtures/state are retained; existing research pool only queried, unchanged.
  Live-effect cleanup finishes 48.81 minutes after G4 launch. Same ledger folds
  15 databases once: 50.164374 model-reported +0.009 literature +1 unknown model
  +0.20 unknown OpenAlex =51.373374/55 USD; 3.626626 remains, no new unknown call.
  No more paid/GPU retry is authorized by that balance. Compatible prior G1–G3,
  G5-common and local read-only replay remain scoped evidence, not a new full
  invocation. Can prepare fresh research in CC, but selected full-text source
  still needs to be available; MERC read_papers remains off. Formal topic/Host,
  old runs/Profile/pool, notifications, commits and pushes were not changed.

- Earlier September 16 qualification/launch-input repairs were implemented
  under the user's repair request. See qualification's repair section and
  `.local/qualification/qualification-repair-20260916/`. Four runtime files
  (claude_agent_host, cli, literature, profile_v4), the operator manager and
  qualification fixtures/tests change. Current runtime is 38d80533... / ACI 20,
  still HEAD 6bd5bb9 plus the uncommitted tree. G4 now splits a total 64 MiB
  tensor / 256 MiB allocator ceilings per Process across one/two cards; contexts
  are additional and original scope deviation is retained. Local recognizer
  accepts the evidenced inspection/cd prefix with exact red/edit/green checks;
  complete read-only replay of the old G5-local passes, original FAIL/3.485287
  bill/database unchanged. Resource fixture is clearer but its strict checker
  and original FAIL remain. Local/reading Grounding now explicitly receives the
  uncommissioned acceptance:null boundary; no new budget mechanism or 4 USD
  success guarantee. Environment auth forwards selected base URL; settings auth
  remains separate. OpenAlex enforces 0600. Existing model validator rejects both
  documented placeholders and is reused for all Profile model overrides.
- Focused evidence: 949 passing identities/five opt-in skips/zero unresolved,
  not a full G1 repeat. Preserve red 19 failures and first-focused two fixture/
  expectation failures; corrected follow-ups pass. Native local-provider CC
  CPU/settings/bundle checks pass; 16 CLOSED Attempts/14 absent groups,
  no sockets/staging/live test owners; 25 external preparation fixtures removed.
  Fresh wheel's 133 runtime/resources and installed CLI/MCP imports pass; real
  MERC Profile admission remains valid. No paid/GPU/public-network call, real
  Profile/run/pool mutation, commit or push. Same conservative cohort stays
  42.434221/50 USD, remaining 7.565779, unknown reserves 1.20 unchanged.
  Those figures describe the repair stage before the supplementary approval.
  The subsequently approved G4 GPU 0+3, resource GPU3/6 USD, reading CPU/6 USD
  and single PDF samples are now complete; use the current results and 55 USD
  accounting at the top of this file. No formal research was started.

- September 16 post-dispatch qualification is in
  `.local/qualification/full-20260916-post-dispatch/`; the earlier results below
  are superseded where stated by the current result above. That candidate was
  68f89d42... / ACI 20, HEAD
  6bd5bb9 plus the imported uncommitted tree. No runtime/test code changed here.
  G1 finishes 5665 passed/25 skipped/zero failures (5690 collected, 4997.54s),
  including G2 174/174 and G3 7/7; no duplicate runs or overall promotion.
  Fresh package/install/managed CPU closure and five-root retained read-only
  cohort pass. G3 has 183 CLOSED Attempts/154 absent groups/seven clean Oracles;
  five native reading/quarantine roots have 65 CLOSED/65 absent groups. All
  1719 available groups across 979 readable G1 SQLite files are absent; keep
  synthetic/invalid unit fixtures distinct from production completion.
  G4 has eight passing
  functional checks, but preflight wrongly described per-GPU 64 MiB/256 MiB
  amounts as per-Process: four dual-GPU Processes held 128 MiB tensors each,
  with summed configured allocator ceilings of 512 MiB plus context. Preserve
  this approval-scope deviation; all 80 groups and both private pools are clean.
  No more GPU execution is authorized by the completed sample.
- Current paid binding is CC 2.1.273, deepseek-v4-pro, api.deepseek.com, existing
  mode-0600 settings file. G5-common PASS: 767.48s / 5.110453 USD. Local FAIL:
  recognizer misses real prefixed red/edit/green trace, 576.24s / 3.485287 USD;
  propose a focused checker correction and read-only replay, not another call.
  Resource FAIL: model combines cleanup and the required standalone wrapper
  check, 719.85s / 4.278484 USD. Actual GPU 3 execution, measured history,
  publication/report and pool/run Oracles pass independently; don't erase FAIL.
  Reading FAIL: 565.68s / 4.052769 USD, exceeding its 4 USD threshold by 0.052769.
  Keep Grounding's invalid-candidate failure and 0.432200 bill. Two readers
  overlap 13.85s and their source/detail/parent collection checks pass, CPU
  succeeds, but analysis hits error_max_budget_usd and report is rejected before
  launch. FAILED remains valid, not COMPLETED. All 51 Attempts close; 50 actual
  groups are absent, no sockets/staging/reservations. Four preparation fixtures
  removed; paid source/data retained for read-only replay.
- At that earlier stage the qualification cohort ceiling was 50 USD and
  cohort-accounting.json folded 13 source DBs: 41.225221 known model +0.009
  metered literature +1 unknown model +0.20 unknown OpenAlex =42.434221 USD;
  7.565779 remains. This is Host/CLI accounting, not an invoice. No additional
  paid rerun is authorized; no directory/model reset. One online academic
  OpenAlex query and Unpaywall DOI lookup succeed; the single PDF fetch times
  out, with no retry. Actual MERC Profile remains admitted and unchanged;
  read_papers remains off, topic USD caps remain user-confirmed null/omitted.
  Existing pool is only inspected; no formal Host/topic, notification, commit,
  push, old-run mutation or automatic code repair occurred.

- September 16 dispatch/academic repair is implemented under the later user
  authorization. Seven runtime files change from 96d0f3f9...; current identity
  is sha256:68f89d42805cd3db85182baf1d5539b6b6dbbc515815eb7854d5a5c3ddd7b2e7,
  still ACI 20. Repeated argv survives real CPU submit/preregistration; quarantine
  ends live model calls with non-retryable dispatch_paused, preserves submitted
  work and honest costs, and blocks new readers. Explicit academic search is
  wired independently of optional general-web fallback. CC init reuses/reviews
  complete Host argv including pool and retrieval options; no auto-join/config file.
  343 selected identities have passing evidence, including real CC 2.1.273 with
  local scripted provider, CPU/MCP, readers, cancel/report and Oracle. Retain
  original red runs, long socket-path failures, spy PGID mismatch and premature
  native reader census; focused corrected reruns are documented, not erased.
  Evidence: .local/qualification/dispatch-repair-20260916/. Four native roots:
  45 CLOSED Attempts/45 absent groups; all 75 groups in 74 test DBs absent, no
  socket/paper staging. Original failed reader root remains ACTIVE/no live work.
  No full G0–G5/GPU/paid/online requalification; earlier k3 evidence does not
  establish DeepSeek compatibility. No real Profile/run/pool/settings mutation.
  Read-only 10:33 +08 observes old topic now CANCELLED r40, 31 CLOSED Attempts,
  all 31 groups and previous Host absent; change occurred outside repair actions.
  Existing bundles remain unchanged; no cross-version recovery or paid retry.

- September 16 09:20 +08 read-only review: newest topic
  MERC-causal-202609160508 is ACTIVE r37 but dispatch-quarantined since 06:35.
  Host PID 3344509 lives; all 26 Agents/four Processes are CLOSED, 30 PGIDs absent,
  four GPUs idle. Exact submitted argv repeats normal values 32 and 2;
  domain.py:669 wrongly applies a unique-text tuple validator. This is a platform
  defect reproduced with the actual argv, not a weak-model/GPU-capacity failure.
  No repair, cancel, restart, recovery or state mutation is authorized by this
  analysis request. Do not modify the saved bundle or settle pending outputs.
  Two successful Processes remain EVALUATING with owned output residue; one
  Agent got 189 blocked wait replies and hit max turns, two timed out after 3001s.
  Three scored candidates remain below both targets; only the ML first result
  is accepted CLEAN_REFUTATION, the other two are uncollected parser output.
  No Debate/Judge/report yet. Known model cost 78.614773 USD plus two unknown
  calls, separate from qualification's 35 USD; real-topic USD caps are null.
  Actual binding is deepseek-v4-flash/api.deepseek.com, CC 2.1.273, overriding
  settings' deepseek-v4-pro; all 133 bundle resources match checkout. No pool
  socket/namespace, web OpenAlex fallback or Unpaywall startup option is present.
  Native searches: six academic-client-missing/five timeout/four unsafe-DNS;
  background OpenAlex material and two direct fetches exist. Parent experts
  actually read method/page excerpts, but read_papers is disabled and no readers
  ran. Eighteen distinct native compactions observed. Details in qualification's
  latest paused-topic section and .local/qualification/run-analysis-20260916-0914/.

- Full G0–G5 work is recorded in `.local/qualification/full-20260916/` and
  qualification's first section. Current candidate is HEAD 6bd5bb9 plus the
  uncommitted/imported tree, ACI 20. Runtime identity is now
  sha256:96d0f3f99006de9f516812a5f3a8d77f082b4c86388c3de836e26177bce9487f.
  Full G1 retained 5,641 passed/five failed/25 skipped. ACI-20 node projection
  omitted 20; fixed in expert_loop. Four old test doubles were corrected.
  Later paid reading exposed batch-document URLs missing from the existing
  citation collector; grounding_background now includes available members.
  Keep native pairing, unavailable-member exclusion and existing Oracle rules.
  Final collection: 5,675 identities, 5,650 passing evidence/25 opt-in skips,
  none missing; do not rewrite the original full invocation as green.
- G2's 174 and G3's seven selections pass in G1. Four retained MERC roots pass
  current read-only Oracle with unchanged database bytes/rows/artifact inventory;
  original 0044 bundled reader retains two known archive errors. No historical
  ACI-20 reader research cohort. G3/native-reader census: 198 CLOSED Attempts,
  169 absent groups. All available G1 database group IDs were also checked:
  889 readable databases / 1,678 distinct IDs (includes synthetic IDs), none live.
- Approved G4 GPU 0 + 3/private pools passes all eight checks/two live cases in
  299 seconds: real same-card overlap, capacity wait and calibrated dispatch.
  Four roots have 80 CLOSED Attempts/80 absent groups; both pool audits pass.
  GPU3-only G5-resource passes in 342.23 seconds, including strict standalone
  wrapper CPU check, resource Skill, real CUDA, measured history and release.
  Cost 2.591910 USD; 12 CLOSED Attempts/12 absent groups, clean Oracles.
- Real k3-256k with existing settings-file auth passes G5-local (355.18s,
  2.351091 USD, 13 CLOSED Attempts/13 absent groups) and G5-common (356.85s,
  3.049820 USD, 12 CLOSED Attempts/12 absent groups). Retain local's first
  Grounding invalid-candidate failure and its bill; common/resource have no
  failed Attempts. These samples used runtime 7450c3f4..., and made no batch
  document calls; their unaffected evidence is reused after the citation fix.
- G5-reading FAIL was deliberately stopped after identifying the runtime bug:
  four real readers succeeded, but two parent summaries with valid batch URLs
  were rejected and no CPU experiment ran. Known cost 0.911025 USD plus a
  1 USD unknown FINAL_REPORT reservation. Its state remains original, including
  one OPEN durable report Attempt with an absent process group; no staging or
  socket residue. Do not settle, resume, overwrite or label it passed.
  The fix retains red reproduction, passes 180 focused cases and all three
  native reader cases (46 CLOSED Attempts/46 absent groups). Read-only replay
  validates both original rejected citations against their exact native replies.
  Final wheel/install/CLI/MCP/CPU bundle match 133 resources and pass Oracle.
- The same cohort ceiling is 35 USD. Current conservative total is
  25.407228 USD: 24.298228 known model USD, 0.009 metered literature, 1 USD
  unknown report reservation and 0.10 USD unknown OpenAlex reservation.
  This is Host/CLI accounting, not an invoice. No directory/model budget reset.
  The ONE approved additional CPU reading run completed in 350.89s, costing
  2.398124 USD: 13 Agents/one CPU Process all successful and CLOSED, 14 absent
  groups, no sockets/staging and Oracle pass. Two actual readers overlap 16.44s,
  read exact method details, and are collected into an accepted parent synthesis;
  the experiment and report close. Runtime identity above is unchanged.
  Original postflight failed because the test expected summary on QUERY_PLAN.
  MAIN-specific test correction retains all reading requirements; red regression,
  29 offline passes and read-only completed-state verification are retained.
  Do not overwrite the original failed result or call this another paid run.
  Real recheck fetched documents individually; exact batch repair remains covered
  by the original native replies/candidates and passing native batch case.
  No further paid retry is authorized; 9.592772 USD remains in this cohort.
- Current G1's ACI-20 three-expert native case has 90 CLOSED Attempts/90 absent
  groups, six CPU experiments, six debate statements and one Judge; the separate
  G3 restart scenario still uses ACI 14. A scan of 292 native transcripts and
  14,703 events across current G3/readers/interaction/five paid samples observes
  no native compaction. Keep that limit separate from historical topic evidence.
  Eight paid/preparation fixture roots are removed, in addition to 28 G1 roots;
  states/bundles/published source archives remain. Final installed CPU census:
  four CLOSED Attempts/two absent groups.
- Current real MERC Profile admission and existing shared-pool status pass
  read-only. Public templates remain only en/zh six-file multi-expert forms.
  Source/private-path and package checks pass; existing settings-file/exact-model
  route is proven. Environment-auth URL forwarding and placeholder admission
  are reproduced existing restrictions, still unresolved. Same-day OpenAlex/
  Unpaywall online observations remain scoped evidence after source/config checks;
  current real Poppler two-page offline extraction passes. No new public request.
  MERC still has null invocation/total/per-expert USD caps, three Agent slots,
  and no read_papers opt-in, as previously confirmed. Qualification's cap does
  not constrain that topic. Review reading opt-in/parent-slot capacity and actual
  Host pool argv in CC before using readers. No research Start, existing-state,
  Profile, credential or shared-service mutation, commit, push or notification.

- September 16 selective-reading implementation and bounded development checks
  are complete in the uncommitted ACI-20 candidate. Retain exact originals and
  page text under read-only /papers, reuse existing Agent publications, expose
  explicit academic search/document mode and own-role reading history. Optional
  read_papers belongs only to Literature MAIN; independent Read/Glob/Grep readers
  inherit its model and use ordinary Agent capacity and expert/run accounting.
  No fixed two-reader model ceiling, extra daemon/ledger, paper database or
  scientific gate. Parent synthesis, question origins and notes use existing
  summaries/MEMORY/cycle reports. Waiting parents still occupy Agent slots.
  Source availability is not evidence of reading or understanding.
- Evidence is .local/qualification/paper-reading-20260916/ and qualification's
  current section. HEAD 6bd5bb9 excludes imported/uncommitted changes. Relative
  to the previous repair wheel, 21 runtime/resources differ, including two new
  modules. Focused 663 cases pass; later stop and backup corrections pass 11 and
  59 overlapping cases. Native-4 normal closure passes three simultaneous real
  CC readers, parent collection, CPU experiment and report; native-5 End run
  passes. The provider/documents are local fixtures, not paid scientific models.
  Both bilingual opt-in Profile checks and the installed CLI/MCP/CPU bundle pass.
  Current runtime resources total 133 (112 Python files). This is not full G1
  or G4/G5 promotion, a historical ACI-20 cohort, or proof of reading speed/quality.
- Preserve failures: premature source deletion before archive, development
  drift during native-2, missing PAPER_READ Oracle matrix, incorrect fixture
  CANCEL_RUN, and new auxiliary cancellation records after STOPPING. Source
  cleanup now follows publication; the existing Oracle and stop boundary remain.
  G2 additionally reproduced backup omitting expert Git memory/plugins/views.
  Existing backup/restore now copies those run-owned resources, retaining Git,
  exact allowed directories, modes and full Oracle checks; no cross-version
  recovery. Native backup/restore retains all 12 paper attachments; its restored
  Host was never started. Initial/intermediate failures remain in the logs.
- All 86 recorded native process groups are absent; final native cases have 26
  CLOSED Agents and one CLOSED Process, with no owned paper staging, mounts,
  temporary targets or sockets. The first failed sample retains one OPEN durable
  Attempt with no live group, deliberately unmodified failure evidence. Installed
  CPU probes close their own tasks and Host. Existing research runs, local
  Profiles, keys, services and pools are unchanged. No commit/push, paid provider,
  public-network retrieval or real-GPU experiment was performed for this feature.

- September 16 authorized retrieval/pool-review repair is verified offline and
  through native CPU paths, with bounded online retrieval samples.
  Six runtime files changed: workflow_host, literature, grounding_web,
  host_tools_server, cli and full_cc_runtime. Literature I/O now uses one worker
  with owner-thread commits; semantic PDF fallback shares a 60-second 429
  cooldown; expired queued web calls do not start new reads. Preserve late
  observations and cancellation accounting. Initial focused 382 passes precede
  new shutdown/accounting regressions. Full G1 retains 5,599 passes / five
  failures / 25 skips; affected final selections pass 432 and five cases. Final
  collection has 5,646 identities across 347 files, 5,621 with passing evidence
  and 25 skips; no missing identities. Do not call the original invocation green.
  Current evidence belongs in qualification's responsive-retrieval section and
  `.local/qualification/retrieval-repairs-20260916/`, not the older repair cohort.
- Native init now distinguishes shared-card permission from pool participation;
  a limited socket search is not absence evidence. Carry an explicitly supplied
  existing socket into CC's actual review, use an independent namespace and
  preserve the latest approved UUID set. No new scheduler, pool service,
  Profile field or automatic binding was introduced; retained runs are unchanged.
- One current online OpenAlex fallback returned correctly labeled academic
  metadata in 14.611 seconds, preserving DuckDuckGo `timed_out`. Explicit
  `--web-search-fallback openalex` is Host-only and defaults off. Provider billing
  was not returned and remains unknown; do not reset prior qualification spend.
  No new paid-model or real-GPU test is authorized by this repair.
- User selected Unpaywall DOI/full-text fallback and OpenAlex academic search.
  Existing contact file `/home/gray/.config/prometheus/unpaywall.mail` stays
  unchanged; never log its value. Explicit `--literature-pdf-fallback unpaywall`
  plus `--unpaywall-email-file` travel in the common literature argv for serve,
  profile-check and start. No Unpaywall title search or extra fallback chain.
  One real CauAIN DOI/PDF lookup and extraction passed in 4.649 seconds; generic
  web health and universal full-text coverage remain separate/unproven.
- Current MERC-causal read-only admission with the selected literature options
  passes. Preserve the earlier manual path-as-component CLI error. The wheel,
  installation and CPU bundle match 131 runtime files; its two Agents/two
  Processes close with clean Oracle and Host exit. Five other final state reads
  show 59 Agent + nine Process Attempts CLOSED and 66 groups absent. All recorded
  final temporary roots/mounts are removed; no test-bound worker remains. Original
  G1 fixtures already collected by pytest do not receive an invented row census.
  New real-GPU/paid-model qualification was not run; do not reuse old G4/G5 as
  promotion of the six changed files. Preparation in CC is supported; actual
  binding/model/budget review stays in its existing initialization flow.
- Current read-only Oracle audits pass for all four retained September 15 roots
  (0044/0849/1555/1803), with unchanged heads/database metadata/Attempt counts:
  231 Agent + 35 Process Attempts CLOSED. This does not authorize recovery,
  replace original bundled-reader failures or establish release-era compatibility.

- Latest read-only review, September 15 at 23:38 +08: user manually stopped
  `MERC-causal-202609151803`; CANCELLED r60 at 23:27:19 after accepted TUI cancel
  at 23:18:10. All 57 Agents (38 success/15 failed/3 timed out/1 cancelled) and
  eight Processes (4 success/3 failed/1 cancelled) CLOSED; all 65 PGIDs absent, exact managed units
  inactive, no socket/work residue/Web listener. Own bundled Oracle passes;
  preserve the separate wrong-artifact-root manual probe failure. No run state,
  code, Profile, key or service was changed and no new paid/GPU/web test was made.
- Actual model `kimi-for-coding` / CC2.1.272; 131 bundled runtime files matched the
  worktree before the September 16 retrieval repairs. Four complete CLEAN_REFUTATIONs, three execution
  faults and one cancelled experiment; no goal verdict. Known model cost
  68.139247 USD plus four unknown calls; failed Agents account for 14.745211 USD
  known. Frozen invocation/total USD caps are absent. Independent scientific
  checkpoint audits/parser counterexamples were not rerun.
- September 15–16 follow-up corrects the prior pool inference: the actual CC
  operator transcript explicitly approved all four UUIDs at 18:20, nonpooled
  at 18:22 and Start now at 18:27. The helper input already lacked pool flags;
  no forwarding loss was found. CC's limited /run/user and /tmp search missed
  the known Prometheus-local socket. Codex memory is not CC runtime context;
  pool socket/namespace are Host argv, not six-file Profile fields. Carry the
  known binding into the next CC review without silently overriding later choices.
- Only two Affective Processes used GPU1; ML explicitly submitted gpus=0, Engineering
  AUTO fell back to CPU when its frozen capacity excluded busy GPU1 and GPU0
  had 11751 MiB free below the configured 12288 MiB threshold. Future idle
  capacity does not change a committed CPU grant. Do not mutate this run or
  automatically force GPU.
- Retrieval follow-up: all ten DDG searches failed at socket.connect with
  errno 101; 15/17 web_fetch calls succeeded. OpenAlex produced four packets
  (125 entries/102 identities), with 24 retrieved-text entries including nine
  cache hits. Semantic Scholar fallback received eight actual 429s and skipped
  88 later lookups locally; that fallback does not use the OpenAlex key.
- At the initial diagnosis, reproduced model-independent defect: synchronous literature
  retrieve and query-coverage admission occupy the Host's servicing thread.
  Actual prefetch took up to 102.87 seconds; overlapping 64.83/49.08-second
  prefetch windows delayed ML web calls past the 30-second MCP deadline, then
  produced late BrokenPipe replies. Isolated real-Host/socket diagnostic with
  existing fake fixtures reproduces timeout with a 0.5-second prefetch and
  0.2-second client bound; responsive control returns in 0.0047 seconds. Two
  fake Agents CLOSED, zero Processes; threads/socket/temp root cleaned. Reuse
  worker I/O plus owner-thread completion; guard mutable adapter batch traces.
- New credential-free connectivity probes reproduce direct DDG IPv4 timeout /
  IPv6 errno 101 while arxiv/ACL TCP connects work. GroundingWeb ignores proxy
  environment variables; current loopback proxy probes also timed out, so no
  working alternate route is established. Preserve curl's unsupported JSON
  formatter error. No key, paid-model, GPU or service mutation occurred. Evidence
  and detailed follow-up remain in qualification's latest-observation section.
- OBSERVER proposed debate at 22:10:49 and paused ML/Affective, but ML's long
  CPU experiment prevented draining until cancellation; no Debate/Judge/Leader
  Attempt occurred. Three execution Agents hit 3000-second deadlines while
  Processes continued; five real terminal MCP replies show repaired elapsed
  values. Engineering fixed three data-alias startup failures before a completed
  CPU experiment. Structural/reference failures and web transport failures persist.
- Accepted final report contains count/chronology errors (five instead of four
  complete results, invented ML post-failure success, wrong Attempt counts).
  First report was rejected with exact missing refs; second structurally passed.
  Preserve it as evidence; Oracle is not a free-text scientific fact checker.
  Current findings and bounded follow-up are in qualification's latest-observation
  section; local evidence is `.local/qualification/merc-1803-review-20260915/`.

- Earlier operation, September 15 at 17:19 +08: the user requested stopping the
  latest Host after reported API exhaustion, intending a new-key fresh run.
  `MERC-causal-202609151555` had already reached FAILED r9 at 17:14:05 and its
  managed Host had exited. Exact service/scope are inactive/not loaded, MainPID
  0, no queued supervisor jobs, no socket or live recorded process groups.
  All seven Agents are CLOSED (one success/six failed); no experiment workflow
  or Process was created. No stop signal, run-state change, key change or new
  run was needed/performed. Existing data remain preserved. Read-only evidence:
  `.local/qualification/host-stop-20260915/`.

- Source import is implemented from Git snapshot
  `7b881b28020e7903f7e14f4584952c976268ec0f`. Keep work local until commit/push
  is requested. HEAD `6bd5bb910c8b9385f828f530cf634f3bbd19bbda` alone does not
  represent the imported, still uncommitted/untracked working candidate.
- Canonical package/CLI is `hypoloom`, operator MCP `hypoloom-mcp`, environment
  prefix `HYPOLOOM_*`, operator skill `/autoresearch`, fresh roots `state/`.
  Public v4 starters contain only `multi-expert-en` and `multi-expert-zh`;
  older parser fixtures stay under tests. Preserve native assets, migrations
  and G0–G5 capabilities, without legacy bridges or historical data imports.
  Cross-version recovery is outside scope; existing runs/data/services remain
  read-only absent authorization for the exact operation.
- Evidence belongs in `docs/qualification.md`, detailed local logs in ignored
  `.local/qualification/`. Reuse existing facts, validators, Oracle and Git;
  no parallel qualification mechanism or recursive agent reviews. Source-project
  qualification does not promote Hypoloom.
- Use the complete local Conda environment. Standalone wheel CLI/MCP and real
  CPU closure work there. A venv whose interpreter symlink escapes its prefix
  is outside the current sandbox binding; no venv feature was introduced.
- The user confirmed `profiles/local/MERC-causal` values; Web port is 8766.
  Formal operations belong in Claude Code through `/autoresearch init`, without
  an extra Codex Start now approval. Settings source is
  `/home/gray/.claude/settings.json`; OpenAlex key source is
  `/home/gray/.config/prometheus/openalex.key`. Never print their secrets.
- The authorized common-goal interaction repair reuses
  `SharedAcceptanceHost.assessment_qualifies`; local SUPPORTED/REFUTED alone
  does not determine common-goal closure. Preserve its red/green regressions.
- Earlier authorized G5-common/G5-resource rechecks passed; retain the original
  resource FAIL and bill. Qualification model spend is 12.996258 USD,
  conservatively 13.005258 USD including recorded free OpenAlex usage, within
  the same 20 USD cohort ceiling, with no unknown reservations. Research costs
  are separate; unknown costs never become zero or reset with a new root.
- Online OpenAlex/PDF/Poppler retrieval was bounded and observed. The user
  confirmed the existing Prometheus pool, independent Hypoloom namespace and
  reviewed two-GPU set. An isolated empty Host started with that binding and
  was cleaned up. Approved linger enablement succeeded; no logout/reboot or
  long-soak coverage is implied. Pool flags must reach the actual Host; do not
  infer binding from a running pool or replace the service.
- Earlier repaired-candidate G1: 5,581 passed/25 opt-in skips, no failures/errors,
  346 files/5,606 cases. Eight real CPU closure scenarios have clean Oracles,
  273 CLOSED Attempts and no recorded process residue. ERC cache checks retain
  source-provenance warnings. These results qualify their selected glm/pool
  scope, not every later model, generated evaluator or scientific topic.
- Completed repair authorization: repair the two-topic findings while preserving framework
  generality/stability; use generic changes for model-independent defects or
  consequential reusable improvements. Runtime repairs and offline/CPU
  verification are complete. No Profile/run-state change, real GPU/paid model call, research
  start or commit. Evidence: qualification's current-repairs section and ignored
  `.local/qualification/merc-repairs-20260915/`. The earlier synthesis and its
  original evidence remain under `merc-two-hypoloom-synthesis-20260915/`.
- First Hypoloom `202609150044`: actual `mimo-v2.5`/CC2.1.270, CANCELLED r108 at
  08:32:15 +08 on September 15. 112 closed Agents (64 success/38 failed/8 timed
  out/2 cancelled), 21 closed Processes; 8 CLEAN_REFUTATION/13 EXECUTION_FAILURE,
  no goal success. Known cost 151.916606 USD plus 12 unknown calls. Third final
  report accepted after two failures; 57 distinct compaction UUIDs.
- Second Hypoloom `202609150849`: actual `glm-5.3-flash`/CC2.1.271, CANCELLED r63
  at 12:58:42. 55 closed Agents (42/9/2/2), six closed Process records (4 success,
  1 failure, 1 cancelled before spawn), four clean refutations/two execution
  failures, no goal success. Known cost 84.265526 USD plus four unknown calls;
  first final report accepted; 21 compaction UUIDs. Combined known research cost
  236.182132 USD plus 16 unknown calls is not an invoice total.
- Both actual Hosts used all four local GPU UUIDs without pool flags; each
  bundle's 131 runtime files matched the checkout before this repair. Second affective experiment 2
  froze GPU3 before external UID1023 occupancy, stayed READY despite idle GPUs0/1,
  and never launched. Existing fixed-grant/pool alternate-card regressions pass;
  preserve initial overlong socket fixture ERROR. No hot grant mutation/migration.
- First generated parser accepts excessive drift, duplicate checkpoint audits,
  empty core config and missing future-speaker perturbation. Second unchanged
  parser rejects these; seven CPU controls/counterexamples behave correctly.
  Its four completed bundles retain 40 checkpoint files and declared audit
  records, but no independent checkpoint audit was rerun. Neither run meets
  the joint F1 goal; their differing runs do not support a model ranking.
- Repaired terminal elapsed reporting using stored Process end timestamps;
  before repair a 4.737855-second failure appeared as 3604.737855 seconds an hour
  later. Wrong durations entered second-run reports and SELF_EVOLVE plugin
  `47719839add99fc197fbb247af27dc06bf7c6838`, used by seven later Agents.
  Existing instructions now require checking original facts and validation-data
  probes; retained learned plugins were not altered.
- Both runs repeat adjudication/reference errors and execution-Agent 3000-second
  timeouts against 108000-second Process limits. First had six real Debate
  statements and a Judge after two failures; second OBSERVER chose none, with
  no Debate/Judge/Leader. Native inputs/reads are observed; compaction counts
  alone do not prove preserved context or useful scientific judgment.
- Repaired existing Oracle's response-less failed Grounding archive path and
  OPEN-Process lookup/workspace cases. Publication identity, digest, transcript
  and CLOSED-residue checks remain; corruption tests reject invalid evidence.
  Both retained runs now pass current-code read-only Oracle. Preserve first
  bundled Oracle's original two failures; this is not cross-version recovery.
- Final liveness at 12:59:36: both Hosts inactive/absent, sockets gone, all 193
  recorded PGIDs absent, no OPEN Attempts or disposable work directories.
  Remaining GPU processes belong to UID1023. Retained staging is stdout/stderr
  logs only; research history is preserved. Seven temporary parser probes were
  removed. This is the earlier research cleanup census, not the later repair
  tests' residue count.
- Bounded MCP wait now preserves queued/running state and exposes Host resource
  waits. The existing pool handles new observations at reserve/activate; a new
  capacity-flip regression selects A → B → A, executes once, publishes once and
  releases every reservation. No replacement scheduler, hot grant rebind or
  automatic pool choice was introduced. Operator Skill preserves reviewed argv;
  STATUS/TUI show binding from the existing RUN creation record.
- Adjudication/final-report validators identify exact missing/disallowed refs;
  original acceptance rules remain. Grounding/experiment-repair/self-evolve
  instructions improve counterexample isolation, raw evidence checks and small
  training/validation probes without MERC-specific runtime rules. Their impact
  on the next generated evaluator still needs bounded model testing.
- Repair regressions retain original red and fixture-error results: seven valid
  reproductions plus one bad manager fixture; 170 passes/one fixture failure,
  then 10 dynamic-pool/manager passes and 133 other focused passes. Full G1 first
  produced 5,584 passes/11 failures/25 skips; the eleven failures were one old
  status-test double rejecting the added binding read. Only that fixture was
  corrected and its 11 cases passed. Final 346-file/5,620-case coverage therefore
  has 5,595 passing identities/25 skips, retaining the original failures and
  serial interruption. Runtime bytes did not change during G1. Existing G2
  selectors pass 169/169 and G3 7/7; five independently reread G3 states have
  150 CLOSED Attempts/150 absent groups. Earlier GPU/paid results do not qualify
  the twelve changed runtime files.
- Current environment is complete local Conda Python 3.12.14/SQLite 3.53.4,
  actual Claude Code 2.1.272 (also observed in native transcripts). The new wheel
  contains all 131 runtime files and its separate installed CPU sample closes
  cleanly; the source archive covers 544 checked files. Final process inspection
  finds no worker bound to test roots; test-created live resources are cleaned.
  At 15:20:54 +08 the retained research states/revisions were still unchanged,
  with all 193 original groups absent. A relative-path manual Oracle probe failed;
  absolute-path reads pass; preserve both probe records.
- A Host-managed model-free waiting redesign is a separate lifecycle change;
  independent deadlines and confirmed budgets remain. Next higher-model G4/G5
  work needs explicit model/resource/cost inputs. No new allowance or scientific
  success has been inferred.
