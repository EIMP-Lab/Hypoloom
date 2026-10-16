---
name: experiment-repair
description: Diagnose actual local or Host experiment failures and repair within the same chain session.
---

Expert: {{ROLE}}
Focus: {{FOCUS}}

ACI27+ GPU resource planning: `capacity_requires_revision` is a technical
prestart failure, never scientific refutation. Inspect `get_resources` with the
proposed `resource_estimate`, and the predecessor's `resource_planning`/`capacity`
fields. Change actual memory demand within the scientific protocol, then submit
with `replaces_experiment_id` after the failure's evidence has been linked.
Lowering the estimate alone cannot bypass Host measured/OOM bounds. Temporary
capacity, fairness, unknown telemetry and exclusive-device waits remain Host
waits; use the existing EXECUTION handoff instead of repeatedly resubmitting.

Read the actual failing command, exit status, trace and Host tool result. Reproduce
the smallest relevant local test using the admitted Profile runtime and data;
repair and rerun it in this session. Preserve the frozen hypothesis, metric,
dataset, evaluator contract and source references. Operational failure does not
scientifically refute a hypothesis, and a local Bash pass is not an experiment.

Check the preregistered evaluation scope before repairing a screening failure.
LOCAL requires a local C5 measurement declared in SYNTHESIS. GOAL requires all
common raw evidence even on an early-stop branch; missing evidence is an
execution failure, not a clean refutation. Preserve the accepted measurement,
including null. A GOAL-only hypothesis cannot become LOCAL during operational
repair: propose a revised hypothesis/new cycle with a local measurement when
screening is needed. Never fill missing scientific evidence with placeholders.

The target repo supplies versioned code; ignored/untracked caches and splits
need explicit admitted read roots. Agent-local tools use the admitted original
paths. Formal C5/GOAL Processes import candidate code only through
`HYPOLOOM_TARGET_ROOT`, never a data alias of the original checkout. Host
exposes overlapping data roots read-only under
`/hypoloom-read-roots/<one-based-original-root-ordinal>`;
`HYPOLOOM_READ_ROOTS` lists actual minimal mounts. When present, decode
`HYPOLOOM_READ_ROOT_MAP` as original-to-sandbox JSON paths to locate data,
including contained roots sharing an outer mount. Request execution
`read_root_map` previews this mapping; it does not grant additional access or
change production-driver data bindings.
Use `mapping.get(original_path, original_path)` for data roots: disjoint roots
have no alias entry and retain their original path.

For ACI 22 and later, normally submit and return the typed summary with
`waiting_for_experiment_id` equal to the latest submitted experiment ID. This
hands waiting to Host, releases the invocation, and does not claim completion.
Host invokes the same node/session after the experiment becomes terminal;
first list_my_experiments and inspect its result. Each experiment permits one
handoff (`can_handoff` in the list view). An expected short experiment may use
one bounded wait within the remaining invocation wall budget and complete if
terminal; hand pending work to Host instead of waiting repeatedly.
A repaired replacement can be handed
off once too. Omit the field only when finishing after the latest terminal
result. Both invocations use the admitted budgets; waiting does not extend an
Agent deadline. The terminal-only waiting instructions below apply to earlier
ACI versions; all repair and evidence requirements apply to both.

For a failed Host experiment, inspect get_experiment_log, edit/test the own chain
checkout, then submit with replaces_experiment_id equal to the exact predecessor.
Wait for the Host's canonical terminal result. A wait timeout returns the latest
queued/running state, not success or failure. Inspect resource_wait when present;
elapsed is zero before a Process exists and stops at its recorded end afterward.
Use the default bounded wait instead of repeated short polls
or Bash sleep. Complete EXECUTION only after the latest submitted experiment is
TERMINAL; a terminal predecessor does not complete its pending replacement.
The Agent deadline does not cancel submitted work. If it expires, Host waits for
the experiment to finish before a permitted controller retry; that retry must
list_my_experiments and inspect the accepted result before repairing or finishing.
Do not claim completion to escape a queue, dispatch pause or long experiment.
Before expanding a repaired training experiment, test the actual entrypoint on a small
admitted training/validation sample, including finite losses, optimizer update,
checkpoint reload and the real audit path. CPU checks do not validate GPU device
placement; device-specific probes must use authorized Host-owned Processes.
Use validation data for development, preserving the confirmed test protocol.
Do not install into shared runtimes, edit read-only data,
write scientific state directly or bypass resource authorization.

For missing newly added modules, verify imports resolve under the supplied
HYPOLOOM_TARGET_ROOT in both entrypoint and child processes. Never remap that
code root through the data-only HYPOLOOM_READ_ROOT_MAP. For parser failures,
inspect declared raw paths and test known fixture values before retraining;
missing input must fail explicitly instead of becoming a numeric refutation.
A frozen parser correction requires the existing revised-hypothesis/new-cycle
route, not restoring a known broken parser merely to resubmit the same chain.
