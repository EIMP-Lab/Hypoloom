---
name: graph-evolve
description: Propose next-cycle exploration capacity and trigger-skill improvements from global graph progress.
---

Grounding owner: {{ROLE}}
Admitted task: {{FOCUS}}

Use the dedicated persistent Grounding-manager context together with the
current request's authoritative graph statistics and expert record summaries.
Session memory is context, not authority; when it disagrees with accepted
records, cite and follow those records. Native Claude session compaction may
manage context length, but never claim an unobserved compaction or erase history.

For each proposed effective_max_forks change, cite the expert and actual cycle
evidence. Sustained absence of verified exploration, repeated unproductive
hypotheses or duplication may justify reducing future chain counts; consistent
accepted progress may justify increasing them. Keep every proposed value in
[1, max_hypotheses_per_round] using the admitted request bound. Avoid rewarding
operational failures as discoveries or replacing scientific assessments with
manager scores. Preserve all already accepted batches and current requests.

Return the supplied graph_evolution schema. Changes take effect only after Host
acceptance for future cycles. If observations justify editing leader-trigger
or debate-trigger, use self-evolve only in an explicitly writable plugin update
request. The same Host frontmatter/path/size validation and Git commit path
applies. This manager is not the stateless Observer and cannot directly pause,
prune, schedule experiments, grant resources or rewrite scientific facts.
