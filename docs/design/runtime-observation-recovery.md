# Runtime preparation and observation repairs

The November 5 investigation found three separate failures: one logical task
exhausted its infrastructure allowance on repeated local Git preparation
timeouts; bounded history reads could repeatedly show only the oldest RPM
buckets; and a twelve-hour briefing silently fell back to text because its
control interpreter lacked the optional plotting dependency.

## User-facing behavior

- Retry allowances belong to the exact logical task, not the expert's lifetime
  or an individual HTTP exchange. A completed task does not spend another
  task's allowance. Retain recovered failures for accounting. Preserve bounded
  query-plan corrections and execution handoffs; neither is a new completed
  task. Distinguish a nonretryable terminal failure from exhausted retries.
- Prepare a private repository by fetching the pinned commit and its reachable
  history. Do not copy unrelated refs, unreachable objects, source configuration
  or working-tree edits. Keep independent objects, no alternates or remotes,
  exact commit verification, bounded processes and existing cleanup.
- Reuse parsed historical request metadata and continue bounded reads between
  requests. Recent activity is inspected first. Show loading separately from
  genuinely missing capture; refresh incomplete reads promptly. Completed
  history must not disappear because a later refresh has a smaller time budget.
- Default control-environment installation includes notification plotting.
  Minimal installations may still send text. Preflight reports chart capability;
  a fallback message and delivery diagnostics give a safe, actionable reason.
  Successful text delivery keeps the existing acknowledgement/cursor semantics.

## Cache boundary and cost

The failed asset is a read-only diagnostic projection, not authoritative research
state. SQLite transactions already provide a consistent ownership/interval view,
and atomic exchange writes provide complete metadata. They do not retain parsing
progress across the HTTP inspection deadline. Repeating the same filesystem scan
therefore spends the next request's budget on work already done.

Use an observer-owned, bounded, disposable memory cache of parsed exchange
metadata, keyed by the existing run/Attempt/file identities and ordinary file
metadata. No durable table, digest, collector or second research counter is
introduced. Completed captures can reuse their parsed timestamps; active or
changed captures are inspected again, and missing sources remain unknown.
Cache eviction or observer restart only causes a fresh read. Exports continue to
support independent bounded reads. Remove the cache if diagnostics acquire an
existing indexed query path with equivalent bounds and freshness.

Each reader retains at most 100,000 parsed file records across 4,096 Attempts.
Complete closed captures use directory identity/mtime and a 60-second recheck
interval; active captures retain only unchanged finalized records. No raw prompt
or response content enters this cache. A reader-local lock protects concurrent
access, while the existing Web request lock continues to bound HTTP work.

The slow left-to-right display is reproducible by shortening the old reader's
inspection deadline. No historical HTTP response was retained to establish the
exact deadline hit in the reported browser session. There is no line-drawing
animation in the shipped SVG renderer. The repair addresses repeated bounded
history scans and makes incomplete reads explicit; it does not claim that all
possible browser slowness has the same cause.

## Verification and publication

Use invented disposable fixtures for retry recovery, pinned Git history with
large unreachable objects, interrupted scans, growing/rewritten metadata,
missing capture, bilingual plots, safe fallback reasons and acknowledged local
delivery. Run affected Host/session/browser checks, then freeze the candidate
for offline qualification and package/bundle parity. No paid provider call,
Telegram message, production Git mutation or retained-run migration is required.
Publish implementation, portable regressions and bilingual user documentation
to dev and synchronize the public counterpart to main. Keep private evidence
and development-only records out of main.
