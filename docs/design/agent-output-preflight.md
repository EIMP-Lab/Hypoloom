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

## Streaming transport and progress events (2026-10-11)

The retained GLM run has 30 `transport_output_too_large` Attempts: three proposal
calls finish with 3,988–6,424-byte answers and accepted native Stop, but their
4.2–4.5 MB stdout exceeds the 4 MiB transport parser allowance. Twenty-seven
design calls also hit the separate aggregate relay response limit. Across the
30 stdout files, 91% of bytes are CLI `system/thinking_tokens` progress records.
One relay sample contains 28,395 thinking deltas with only 120,695 content bytes;
SSE framing and other responses exhaust 4 MiB during the 28th request. These are
transport-volume failures, not evidence of a model token/window overflow.

The 4 MiB allowance belongs to that reproduction. Previously, full-CC API
responses used `max(4 MiB, max_tokens * 32)`; stdout parsing also considered
the encoded final-output allowance. A 1,000,000-token invocation therefore
already allowed 32,000,000 bytes (about 30.52 MiB), even in `observe` mode.
Progress inflation can exist without reaching that larger limit. The repair
applies to the shared Claude Code/Anthropic-compatible transport, with no GLM
model-name or endpoint branch; its narrow progress exemption recognizes the
CLI event shape. It does not establish a failure rate for other providers.

Keep the original raw stdout under the existing full-CC 64 MiB file allowance.
Read it incrementally, omitting only the known complete thinking-count record
shape from the parser's byte allowance. Count omitted records/bytes in ordinary
diagnostics. Preserve every other record and the existing complete-stream,
completion, native Stop, candidate and accounting validators. Unknown, malformed,
content-bearing or oversized progress-shaped records do not gain an exemption.
The effective-content and raw-file bounds remain independent and enforced.

Give full-CC relay responses a separate 64 MiB cumulative wire allowance,
independent of an invocation's observe/enforce token policy. Keep upstream bytes
unchanged, the existing request count/deadline and token policy, and the final
candidate/artifact limit. Reuse the relay's existing byte counter and rejection
path: latch actual exhaustion, record its scope/observed bytes/limit, and reject
subsequent requests locally before upstream connection. Surface the local
transport failure instead of a generic provider 502. Do not route it through
provider-outage cooldown or spend more ordinary retries on unchanged capacity.

No new persistent authority, schema, digest or control gate is needed. Existing
raw diagnostics, ordinary transient parsing/counters, frozen runtime versioning
and focused/native regressions cover this boundary. DNS recovery and retained
run mutation are outside this repair. Original truncated streams cannot prove
what the provider would have generated later; complete local streamed responses
must verify the new allowance and its upper bound before claiming closure.
