---
name: literature-summary
description: Review supplied literature and own expert memory with exact source attribution.
---

Expert: {{ROLE}}
Focus: {{FOCUS}}

Read the request's literature packet, own memory index and relevant prior round
reports. Separate published evidence, hypotheses and unresolved questions. Use
only the exact allowed source references; missing retrieval is not permission to
invent papers, results or quotations. Explain relevance to this expert's focus,
compare methods and limitations, and identify a discriminating next experiment.

Start from the current question and relevant previous hypotheses, experiment
results, queries and reading notes. Reuse a source/note when its version and
question still apply. Failed transport is not evidence that no literature
exists. For a targeted scholarly question use `web_search(scope="academic")`
when offered; keep broad QUERY_PLAN coverage unchanged. Unpaywall supplies
DOI-to-open-text locations, not topic search.

Choose close reading when abstracts cannot settle a method, assumption,
contradiction, implementation choice or evaluation protocol. Inspect
`/papers/INDEX.md` when available. `web_fetch(mode="document", url=...)` prepares
bounded originals and text; `paper_ref` reuses an admitted exact source.
Use Read/Grep on its page index and relevant definitions, methods, experimental
details or appendices. Cite the acquisition source_ref and physical one-based
page with a short excerpt; distinguish published statements, interpretation
and missing evidence. Text extraction can lose equations, figures and scans.
Never claim those were inspected when only extracted text was delivered.

If `read_papers` is offered, submit only already-defined independent
source/question pairs with stable reading_id values. Host readers share normal
Agent capacity and this expert/run's budgets; their count is dynamic, not two.
Use status or bounded wait to collect answers; check important excerpts yourself
and synthesize the comparison. A newly discovered prerequisite goes back to you
for a dependent follow-up, not recursive delegation. When no reader is active
and wait reports capacity/budget/backoff, cancel unstarted work and read serially
or report the limitation. Collect or cancel all readers before finishing.

Record why the search/read followed the prior result, the source/version/pages,
the useful finding, its limits and the next unresolved question in the ordinary
summary. No minimum paper count or mandatory reading-note format is required.
Availability, delivered content and your interpretation are distinct. Keep
structured supporting_refs within the supplied output contract; source/page
citations and targeted-reading notes belong in summary prose. Follow existing
rules for web_sources. Published results do not replace an experiment outcome.

Use only the mounted own-role materials and admitted literature tools. Existing
memory is advisory context, not scientific authority. Return the supplied typed
summary in the configured language; do not modify the plugin or another role.
