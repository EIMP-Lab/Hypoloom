# Grounding

Start from supplied context indexes, goal and persisted clarification answers.
Inspect retained drafts and work notes before rediscovering interfaces. Verify
actual data and executable interfaces where evidence is missing or uncertain;
do not traverse the whole repository without a relevant question.

If commissioned to author a shared evaluator, implement the current task's
metrics, aggregation and validity conditions under /acceptance. Reuse suitable
existing implementations after checking their scientific assumptions. Preserve
the requested splits, thresholds, directions and other confirmed constraints.
Background-only Grounding does not invent an evaluator. Ask for clarification
only when consequential ambiguity cannot be resolved from the supplied record.

Test raw-to-measurement calculations, scientific boundaries, missing evidence
and every mandatory validity condition. For a validity mutation, assert its
specific check changes from true to false and unrelated checks remain as
expected; a low overall score alone proves nothing about validity. Keep a
qualifying case for each scientific condition. The Host already tests numeric
comparisons, strict threshold equality and ALL/ANY logic: do not reverse-engineer
raw samples solely to repeat those comparisons. Task-specific boundaries still
need tests. Derive checks from raw evidence rather than a producer's passed flag.
Test identities and configuration contents when required, not just row counts
or file existence. Do not assume classification, particular seeds or a layout.

Use the exact parser interface and smoke locations in the runtime instruction.
The native Stop hook already checks final JSON and runs the declared parser in
production isolation. Inspect /runtime/output-schema.json directly when needed;
it is the same schema as the prompt. Do not extract it from conversation logs or
implement another generic final-output validator.
Smoke completion proves interface compatibility, not scientific correctness.
Once scientific checks and complete smoke inputs are ready, submit the full
final JSON promptly and correct actual Stop errors in this session. Leave time
for that correction; writing a draft file does not submit it. Avoid unrelated
exploration or a new test framework after the required checks are complete.

During evaluator authoring, concise /acceptance/WORK_NOTES.md may retain verified paths,
checks, unresolved errors and the next step. Keep reusable fixture generators,
test helpers and draft final JSON under /acceptance/work/, not disposable /tmp.
These remain unaccepted working material; omit them from parser_files. After
clarification or context recovery, check retained bytes against current inputs,
then repair remaining defects instead of recreating completed work. Break large
source generation into coherent edits so an output-token cutoff does not discard
an entire implementation; keep using the existing model and invocation budgets.
