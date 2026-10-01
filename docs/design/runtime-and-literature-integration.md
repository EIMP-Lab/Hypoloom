# Runtime and literature integration (2026-10-01)

The stopped run exposed framework defects independent of its research topic:
an accepted native completion was rejected after a background-task notification;
report inputs conflated exhausted nodes with unsuccessful Agent attempts; and
an expensive experiment reached an untested implementation dispatch path.
The run is closed, with independently checked process and pool release. Its
state, frozen runtime, artifacts and Profiles remain read-only.

## Run findings and priorities

The stopped run closed all 83 Agent and nine Process Attempts. Six hypotheses
produced nine LOCAL experiments: five clean refutations, one success and three
execution failures (two caused by the operator's cancellation). There was no
accepted GOAL verdict. Reported usage was 162,692,126 tokens including cache;
four closed calls had incomplete accounting. Implementation/execution consumed
62.3% of reported tokens, versus 4.5% for Debate/Judge. These are usage records,
not a provider invoice or evidence that discussion should be removed.

The priorities follow reproduced boundaries: correct completion/accounting;
exercise actual candidate dispatch before long experiments; and give reporting
the unsuccessful-attempt history and published measurements it previously
lacked. A successful LOCAL configuration does not establish a GOAL result or
isolate a mechanism when training schedules/weights also changed. Process
failure, scientific refutation and operator cancellation remain distinct.

## Combined PR review

| PR | Retained contribution | Required correction |
| --- | --- | --- |
| #3 | Required implementation lineage, optional configuration omission, exact query causation and accepted summaries | Synthetic failed query-plan Attempts copied successful model usage and doubled charges. Derive replanning from actual pre-model retrieval failures and retain existing retry limits. |
| #2 | Optional institutional browser, scoped operator queue, reusable sessions, PDF import and TUI/Web controls | Public-source safety rejections were requeued through the browser; landing-page DOI metadata could identify the wrong PDF. Reuse exact retrieval rejection evidence and verify identity from downloaded PDF content. |

PR #3 is integrated first because it establishes retrieval recovery and report
evidence behavior; PR #2 then adds optional acquisition without bypassing those
boundaries. Both retain their existing publication and validation paths.

## Delivery batches

1. Repair native completion: accept only identical successful completions from
   the same session followed by identified task notifications, with consistent
   cumulative accounting. Revalidate repeated Stop calls through the existing
   validator, including current source/report checks. Preserve original transport
   failures before workspace capture. Refine existing implementation/execution
   guidance to exercise selected arms, registrations and data paths early.
2. Merge PR #3 with its lineage and accepted-summary delivery. Replace synthetic
   query-plan failures carrying copied model charges with actual pre-model
   Grounding failures. Derive bounded replanning from existing Attempts and exact
   plan references. Give final reports unsuccessful-attempt history and bounded
   declared raw measurement inputs, keeping LOCAL observations distinct from
   accepted GOAL evidence and absent evidence distinct from omitted evidence.
3. Merge PR #2 with optional institutional access. Preserve public-retrieval
   security rejection across browser fallback. Validate PDF identity from its
   content rather than borrowing the landing page's DOI. Exercise the actual
   browser locally, operator controls and existing publication path. Real
   institutional authentication is a separate, user-assisted compatibility check.

Each batch receives focused regression coverage before commit and push to dev.
PR merges retain their original ancestry and include the reviewed corrections.
Freeze the combined candidate for the full offline suite and bounded native
Host/CPU closure. Paid scientific quality and real subscription coverage must be
reported separately; neither is inferred from scripted tests.

## Existing authorities and validation

No extra persistent schema, digest, receipt, checkpoint, gate or authority is
needed. Native output validation and provider accounting already own completion;
SQLite Attempts and existing retry limits own failures and replanning; published
artifacts and their existing resolvers own evidence; literature download traces
own network rejection. Ordinary regressions reproduce each defect at these
boundaries. Git retains implementation and PR history. New diagnostic fields
are projections of these facts, not a second reducer or a scientific verdict.

Full-suite compatibility repairs keep the existing workflow size allocations:
request/fact-context construction moves into the existing `agent_context` module,
and transcript usage observation into `provider_observation`. Dispatch,
transactions and acceptance remain with their existing owners. Native tests
check the actual Skill body delivered by the CLI, rather than frontmatter that
the CLI no longer delivers; legacy packet-only prompts remain restricted.

Scientific preflight remains a method in the existing workflow guidance, not a
universal mandatory gate. Check the real changed path before costly work; label
multi-factor comparisons as configuration effects unless controlled evidence
supports mechanism attribution. Do not add topic-specific validators or claim
that technical failures refute a scientific hypothesis.

Original private evidence remains at
`.local/analysis/stopped-run-and-prs-20261001/`; qualification results are recorded
in `docs/qualification.md` as each batch completes.
