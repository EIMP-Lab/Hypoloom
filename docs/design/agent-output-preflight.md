# Agent output size feedback

Implementation candidates can pass native Stop validation and then fail the
Host's workspace capture or serialized response limit. This is a framework
problem independent of the research topic: a synthetic 52,000-byte source
produces a 104,202-byte source/patch package, but the Base64 response exceeds a
131,072-byte invocation limit. A larger source also reproduces the Git stdout
bound. Retrying can shrink the candidate, but delayed and opaque feedback can
consume many complete invocations without recovery.

The repair reuses the existing native Stop callback, workspace capture,
canonical serialization and invocation limit. Before releasing an implementation
completion, capture its actual declared sources and cumulative Git patch. Check
the known candidate, encoded artifacts and effects against the same byte
validator used by final postflight. This is a lower bound on the final envelope:
provider usage and diagnostics do not exist yet and must not be fabricated or
reserved under an invented allowance. Final postflight still checks the entire
response, including those fields, against the unchanged limit.

Return capture/size errors through the existing Stop correction loop, with the
limit, known package sizes and an explanation that cumulative patches include
tracked and untracked changes, including separately captured source files.
Recapture every completion; a prior successful check never authorizes changed
files. Preserve configured correction/retry limits, path and Git validation,
accounting, final publication checks and frozen old runtimes. No additional
schema, persistent marker, acceptance record or parallel validator is needed.

Validation uses task-independent fixed/deferred design cases, actual Git and
native Stop correction, negative controls for source drift and exhausted
correction limits, and a local-provider/real-CLI/CPU workflow where available.
Existing research runs and submitted support packages remain read-only. Do not
relax context or financial limits, automatically enlarge output budgets, or
attribute a separate transport failure to telemetry without its own evidence.
