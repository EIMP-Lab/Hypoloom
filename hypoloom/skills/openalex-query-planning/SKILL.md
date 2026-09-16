---
name: openalex-query-planning
description: Derive one bounded OpenAlex LiteratureQuery/v2 plan from a frozen research goal and either role-neutral Grounding or scoped expert context without granting network or ranking authority.
---

# OpenAlex query planning

Use this Host-injected skill only for a `QUERY_PLAN` literature task: either the single Run-scoped, role-neutral Grounding plan or one component expert's plan.
It is instructional input, not an ambient Claude skill, network tool, or retrieval authority. The Host performs OpenAlex I/O after validation.
For Grounding, read the frozen goal, components, protocols, and bounded repository/data context without adopting the transport role as an expert persona.
For an expert plan, also read the grounding result, executing role, expert background in its canonical `focus`, and component.
Use the supplied `LiteratureQuery/v2` template. Return one mutable proposal containing `query` and optional `preferred_venues`; omission means no venue suggestion.
The mutable proposal is the value of `typed_candidate`. The StructuredOutput root contains only `typed_candidate`.
Put `query` and any permitted `preferred_venues` inside `typed_candidate`; never duplicate them at the root.
If the supplied schema excludes `preferred_venues` (ACI8), omit it.
`type`, `properties`, and `required` are schema keywords, not output fields.
The Host combines those fields with the frozen template; do not repeat or change filters, limits, source classes, strata, date, schema version, or context refs.

On retry, read `--candidate-validation-error` and correct it exactly. If the prior query found no papers or names missing time strata, issue a materially broader query; do not resubmit the same query.
For missing foundational or intermediate coverage, remove recent method, dataset, benchmark, model, and product modifiers until only the enduring field concept remains. Merely translating the same narrow conjunction is not recovery.
Field-anchor broadening for missing foundational or intermediate coverage takes precedence over the normal specificity guidance below.
Use established parent or adjacent field terms in the same OR group when they describe the historical lineage; they need not be exact synonyms, but every term must remain recognizably relevant to the scoped goal or component.
Remove AND-attached modern task, population, modality, and mechanism groups until the query can span the frozen historical window; add one back only when it is itself stable through that window.

Write a compact OpenAlex classic-search expression. Normalize a non-English goal into English scholarly terminology required by the frozen language filter, while preserving scientific names and identifiers.
Use uppercase `AND`, `OR`, and `NOT`, and quote only meaningful exact phrases. Do not use wildcards, fuzzy-search suffixes, filesystem or URL paths, Markdown syntax, implementation identifiers, or prose instructions.
Do not force a recent dataset, benchmark, model, or product into every historical stratum.
For an initial plan, anchor the expression on one to three discriminative concept groups naming the central phenomenon, population or task; join them explicitly instead of using a flat bag of loosely related generic terms.
Training, temporal, validity, evaluation, reproducibility, framework, and model are modifiers, not a field-specific anchor.
Prefer stable scholarly terms; a matching title or abstract should remain recognizably about the scoped goal or component.

`preferred_venues` may contain at most six entries confidently named from the topic/background, with `source_class` set to `JOURNAL` or `CONFERENCE`;
omit an uncertain venue instead of guessing. They are retrieval preferences, not an authoritative venue ranking, and never replace the broad retrieval lane.

For a `MAIN` literature task, never plan or rewrite a query. Read only the Host-supplied validated `LiteraturePacket` and summarize it with exact refs.
