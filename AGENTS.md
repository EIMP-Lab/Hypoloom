# Hypoloom development instructions

Before changing code, reviewing an implementation or diagnosing a run, read
`docs/codex-memory/CURRENT.md`, use `docs/codex-memory/INDEX.md` to select relevant
sources, and consult `docs/README.md`. This development context is separate from
`CLAUDE.md` and `.claude/`, which configure the Claude Code research runtime.

## Scope and authority

Existing research runs are read-only unless the user authorizes the exact
state-changing operation. Inspection and code repair do not authorize starting,
resuming, migrating, settling or deleting a run. Verify durable state and actual
Host/process liveness separately. Cross-version run migration is not supported.
Preserve user-owned changes. Commit and push only when explicitly requested.

## Engineering and evidence

Prefer one existing source of truth and its validator. Before adding a hash,
digest, frozen contract, schema, snapshot, gate, checkpoint, receipt, marker or
parallel authority, document: (1) the reproducible failure and impact;
(2) the failed asset and lifecycle/trust boundary; (3) why Git, primary keys,
transactions, atomic writes, unique constraints, types and ordinary tests are
insufficient; (4) the smallest extra mechanism, maintenance cost and removal
condition. If that justification cannot be completed, reuse existing machinery.

The default failure model includes crashes, partial writes, retries, stale
workers, benign concurrency and candidate drift. Do not expand it to a hostile
same-user process or compromised host without a concrete incident or explicit
requirement. Preserve authentication, authorization, privacy, append-only
writer boundaries and controls on irreversible operations and formal releases.

Follow the existing G0–G5 promotion model in `docs/qualification.md`. Focus on
bounded closure through the real Host, broker, Process, collection, publication,
recovery and next-action topology. Include actual CPU/GPU hypothesis evolution
when relevant. Scientific topics sample compatibility and scientific quality;
24/72-hour soaks are optional and risk-triggered.

Use focused regressions during implementation. Run full offline/resource/paid
qualification when freezing a candidate, not as a debugging loop. Independently
count observable ownership, terminal effects and residue. Do not build a second
reducer in the oracle or weaken a check to turn a failure into a pass. Adjacent
speculative hardening is a separate optional task. Do not spawn recursive review
waves; delegate only when the user explicitly asks for agent parallelism.

## Memory and documentation

Keep compact current decisions in `docs/codex-memory/CURRENT.md` and route
supporting material through INDEX. Current human-facing guides belong under
`docs/`; runtime instructions belong in CLAUDE and `.claude/`. Do not put Codex
maintenance transcripts into runtime memory. Preserve historical evidence at
its original location instead of importing obsolete development ledgers.
