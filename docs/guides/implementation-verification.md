# Implementation verification

English · [简体中文](implementation-verification.zh-CN.md)

The Host commissions an independent implementation verifier when an expert submits
captured code. It checks correctness, engineering choices and computational efficiency.
It does not generate hypotheses or judge scientific merit. Checks come from the
current implementation, without a predefined research-method checklist.

Choose an exact provider model ID during `/autoresearch init`, independently of the
research models. Using the base model requires an explicit choice. Optional effort:
`low`, `medium`, `high`, `xhigh`, `max`. You can instead set `verifier.model` and
`verifier.effort` beside `grounding` in `experts.yaml`, or pass
`--verifier-model MODEL_ID --verifier-effort high` to bootstrap or direct
`hypoloom.cli start`. Flags override the Profile in memory through the same compiler;
init shows the effective model and source. Profile files stay unchanged. The verifier
is not an `experts[]` member and inherits no author's private memory or model overrides.

Inspection produces sourced findings and a small Python probe when appropriate.
The Host executes it against read-only candidate code in the normal sandbox, with
authorized resources and a maximum of 120 seconds. A separate session reviews actual
observations before formal execution. Probe records and retry ordinals are separate
from scientific executions. Failed checks and insufficient evidence return through
engineering repair. The author releases Agent capacity using execution handoff while waiting.

Unchanged captures may reuse recent evidence within the same run. Code, entrypoint,
environment or resource changes require another inspection. There is no universal
vectorization threshold, mandatory baseline equivalence or cross-run pass cache.
A small probe samples behavior; it does not prove complete program correctness.

TUI and Web show an **Implementation Verifier** card beside Grounding with per-expert
deliveries, triggers, reuse, completed first-pass results, blockers and advisories.
Report generation, delivery in an expert task input, response, repair submission and
independently verified resolution are distinct. Delivery does not establish reading
or understanding. Finding resolution requires an explicit prior report/finding reference.
Verifier accounting is global; subject attribution does not bill the work twice.
Press **v** in TUI or **Verification history** in Web for paginated evidence.
Stale Host observations remain unconfirmed; retained runs show unsupported status.

This applies to new runs. Existing frozen runs are not hot-upgraded or migrated.
