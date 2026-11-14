# Verified engineering guidance and recall

The user authorized implementation after actual CC optimization trials, followed
by dev publication and the corresponding selective main update on November 15.
New runs use ACI 37 and native adapter 94; existing runs retain frozen code and
policy. The implementation builds on ACI 36's bounded probe recovery.

## Failure and authority boundary

The trials produced useful raw-feature caching and loss optimizations, but also
an omitted Dataset interface, an unsupported author claim of passing checks,
and a spawn-from-stdin check that blocked on an empty queue after child failure.
Long local validation could consume the entire invocation before its Stop hook.
Repeated verifier findings also obscured the current unresolved state in TUI.

Git captures and submission identities already bind candidate bytes. Commands,
Attempts and the existing probe/review validators remain the only admission
authority. They do not make a model's claimed pass true, preserve unfinished
check detail, or put relevant evidence into a later model request automatically.
No scientific fact, approval ledger, cross-run pass cache or background reviewer
service is added. Unsubmitted working drafts are never auto-promoted.

## Smallest changes

- Inline the short engineering reference from the expert's bound plugin. Cover
  deterministic raw caching, byte limits, worker handles, zero versus absent
  gradients, pairwise loss reduction and causal history reuse. Retained plugins
  fall back to their own detailed reference without seed backfill.
- Extend the existing probe helper with paired timing. Keep separate correctness
  assertions, actual samples, alternating order and explicit CUDA synchronization.
  Validate statistics against their samples; no universal speed threshold.
- Atomically replace the existing probe result file before and after each check.
  An interrupted check remains ERROR, and Process success plus independent review
  are still required. There is no new checkpoint store or recovery owner.
- Derive a read-only input from accepted reviews and their exact Process/capture
  records. Limit it to the same run, expert and component, four whole records and
  6 KiB from a bounded review window. Include coverage and measurement conditions.
  The content version is a deterministic presentation identifier, not admission
  authority; the Oracle independently resolves existing primary keys and results.
- Apply a stdlib native PreToolUse hook to engineering Bash calls: cap at 60
  seconds and remaining time, reserve up to 15 seconds for handoff, and require
  foreground execution. The hook neither rewrites commands nor grants permission.
  Host deadlines remain authoritative. Provider thinking defaults are unchanged.
- Count explicit follow-ups once in the current TUI summary while retaining
  individual reports and diagnostics in history. Do not merge by text similarity.

The maintenance cost is two small stdlib helpers plus shared result validation
and an independent evidence-binding check. The total counted authority budget
remains 27,550 lines, including both helpers. These projections can be removed
without migrating scientific state; old ACI rules remain readable. The Bash hook
can be retired when equivalent deadline propagation exists in the native tool.

## Validation scope

See [the qualification record](../qualification.md#verified-engineering-recall-and-probe-recovery-2026-11-15).
Public regression tests and the local native CC hook sample validate framework
behavior. The earlier real MiMo trials retain their original private evidence;
this publication does not establish model-weight learning, universal speedup or
full-training gains. Native fixtures use temporary roots and local providers;
existing research, Profiles and resource pools remain unchanged.
