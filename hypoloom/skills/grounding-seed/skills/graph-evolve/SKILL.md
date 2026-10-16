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
evidence. Compare the supplied exploration_progress with the admitted limits:
recent_batches reports actual accepted hypothesis counts, not configured width;
active_chains is a request-time OPEN-work observation, not GPU utilization.
Raising a limit that the expert does not use cannot by itself increase supply.
Explain the unused width and encourage independent, evidence-backed alternatives
within existing limits before requesting more capacity. A valid negative result
that eliminates an explanation is verified exploration, not automatically
unproductive work. Repeated unchanged tests, unresolved dependencies or actual
duplication may justify reducing future chain counts; consistent accepted
progress may justify increasing them. Empty GPU slots alone are not scientific
evidence and do not justify weak hypotheses. Keep every proposed value in
[1, max_hypotheses_per_round] using the admitted request bound. Avoid rewarding
operational failures as discoveries or replacing scientific assessments with
manager scores. Preserve all already accepted batches and current requests.

Return the supplied graph_evolution schema. Changes take effect only after Host
acceptance for future cycles. If observations justify editing research-workflow,
leader-trigger or debate-trigger, use self-evolve only in an explicitly writable plugin update
request. The same Host frontmatter/path/size validation and Git commit path
applies. This manager is not the stateless Observer and cannot directly pause,
prune, schedule experiments, grant resources or rewrite scientific facts.
