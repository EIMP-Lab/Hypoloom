# Structured Verifier output and adoption checks

The November 15 read-only inspection of the ACI 38 MERC run observes two
Verifier invocations lasting 44.9 and 41.6 minutes with three failed Stop
validations each. Final messages contain 19–49 thousand characters, including
Python embedded in hand-written JSON. Failures include prose before JSON,
unterminated strings, trailing data and wrong fields. Both truncated and normally
ended provider responses occur. Input paging already works; it cannot repair
output serialization. Separate inspection shows the active training candidate
still uses uncached dialogue reads and per-batch CPU transfers after its author
read the optimization bundle. A different engineering candidate applied it.

The failed boundary is native model completion to the existing per-Attempt
output validator. Git identities, transactions and atomic writes protect captured
code and accepted artifacts, but cannot make model-authored JSON syntactically
valid. Increasing retries repeats the same long serialization. ACI 39 adds one
Verifier-only MCP tool, `submit_review`, with the **existing full AgentResponse
schema** as its input schema. The stdlib bridge serializes the arguments and
uses the existing native output socket, semantic validator, correction cap,
deadline and atomic output file. No second report store or scientific authority
is introduced. The existing MCP stdio loop is shared with Host tools.

A successful tool call prepares the output; it does not accept it. The native
final message `{"review_submitted":true}` selects that prepared output. Stop
revalidates it before adapter consumption. This small completion selector is
needed because native Stop requires a final message while reproducing the report
would recreate the observed failure. An unstaged selector, a failed replacement,
wrong session, transport failure, deadline or cancellation cannot recover an old
file. An ordinary complete final JSON object remains supported. Old ACI versions
retain their original tools and instructions. The selector and transport can be
removed together if native CC offers a qualified equivalent that validates
structured tool output and final completion within the same Attempt.

ACI 39 bounds individual narrative fields and total explanatory text (16 KiB)
through the same review validator; schema limits expose local constraints early.
Probe source retains its existing 256 KiB byte bound. Concise probes are guidance,
not an arbitrary new execution limit. Errors identify the failed field/constraint
without exposing candidate values in redacted diagnostics. Maintenance consists
of versioned wiring, the existing validator, and regressions for preflight/Stop,
replacement invalidation, correction exhaustion, native MCP and legacy reading.
The review contract remains the single schema/semantic owner. Its physical-line
allocation increases by 100 using unused expert-memory allocation (425 to 325,
230 actual lines); the existing 27,550-line authority ceiling is unchanged.

The bound engineering brief directs authors to inspect active entrypoints,
document adopted/deferred changes in existing implementation descriptions, and
test values/gradients/order plus bounded comparable timing. New Verifier guidance
traces loader and training calls and uses existing probe checks and coverage to
record adoption and its limits. Reading a patch, or timing a disconnected helper,
does not prove adoption. Neither a missing optimization nor low GPU utilization
alone is a correctness blocker. Memory/dispatch claims require concrete evidence;
no static method checklist or mandatory universal speed threshold is added.

Existing research state, frozen runtime, profiles and training are read-only.
Qualification uses captured failures, offline tests and native CC with a local
deterministic provider/CPU processes. It does not establish MiMo adherence or
end-to-end GPU speedup and spends no new paid-model budget.
