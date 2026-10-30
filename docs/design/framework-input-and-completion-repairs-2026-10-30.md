# Framework input and completion repairs (2026-10-30)

The user requests general framework repairs, implementation and dev publication
followed by public main synchronization. The existing research run was manually
stopped and remains read-only, including its frozen runtime and private plugins.
Its observed STOPPING state and process disappearance are distinct observations;
this change does not settle, migrate or restart it.

## Reproduced failures and boundaries

1. RESULT_ANALYSIS never reached the provider in three attempts. The original
   614,326-byte experiment stderr is valid UTF-8, but the last 65,536 bytes begin
   with `0x88`, inside a progress-bar glyph. `agent_context._attempt_log_tail`
   sliced bytes and declared the result text; strict Agent input rendering then
   rejected it. The experiment itself had succeeded. The same log-tail helper
   supplies other analysis/repair contexts.
2. An incomplete connection to native output validation permanently failed the
   invocation's validator. Later valid output could not recover. On subsequent
   attempts, a candidate passed validation, then the final message prefixed that
   JSON with prose. Revalidation rejected it by permanently failing the validator,
   despite remaining correction capacity. Three ADJUDICATE attempts ended without
   an accepted response. Returning an earlier candidate or stripping prose would
   hide the actual final-message failure and is not an acceptable repair.
3. Another expert exhausted experiment repairs on a missing CLI parameter, an
   undefined label mapping, then a producer/consumer filename mismatch. Its tiny
   model forward/backward checks did not cover the actual training invocation,
   metric serialization and result loading. These are project implementation
   responsibilities; framework guidance must make the relevant integration path
   concrete without hardcoding a model, CLI flag or filename.

## Minimal changes

- Read bounded log tails with seek/read. Skip a clipped leading UTF-8 fragment,
  replace genuinely undecodable diagnostic bytes visibly, and keep the encoded
  excerpt within its current byte limit. Raw logs remain unchanged. Scientific
  JSON, authored source and accepted output retain their existing strict rules.
- Treat a peer that closes before a complete request frame as a failed connection,
  recording the transport diagnostic without poisoning other requests in that
  invocation. Do not accept its input or consume a candidate correction round.
  Session mismatch, malformed complete requests, oversized input, exhausted
  deadlines and actual validator/storage failure remain fail-closed.
- Revalidate every completed message. If previously accepted output becomes
  invalid, revoke acceptance and use the same bounded rejection/correction path
  as an initial invalid candidate. Recheck current source and experiment state on
  correction. Never deliver the old accepted file after rejection, skip schema or
  ownership checks, extend the deadline, or increase the correction limit.
- Explain the normal final-message/Stop path directly in existing runtime
  instructions. Agents should not call internal validation sockets as a substitute
  for completing their response. Keep exact final-text matching at Host delivery.
- Strengthen the existing DESIGN/EXECUTION instructions and repair reference:
  verify exact child-process argv, actual emitted artifact names, and the consumer
  that reads them using a bounded admitted test. Record checks or unresolved device
  work in existing notes. Synthetic parser fixtures and a forward-only probe do
  not establish that this path ran. Project-specific code remains project-owned.

Ordinary byte decoding, request-local exception handling, the existing native
validator, atomic output replacement, Git versioning and focused regressions are
sufficient. No new ledger, hash, schema, checkpoint, preflight interface or gate
is introduced. Increment the native adapter version for its changed behavior;
retained versions are not rewritten. Guidance is not a guarantee of model
compliance, and scientific criteria remain unchanged.

## Validation

First reproduce Unicode-tail and native-validation failures with deterministic
fixtures, then exercise the real input renderer, correction caps, wrong-session
and malformed-request rejection, source drift, stale output files, deadlines and
diagnostic recording. Run affected context/adapter/Host tests and a bounded real
CLI/broker/CPU closure with the existing local provider fixture. Check guidance
delivery and source/package/public-branch parity. Reuse retained diagnostic bytes
read-only, record original failures, and report focused qualification limits.
