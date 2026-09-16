---
name: experiment-repair
description: Diagnose actual local or Host experiment failures and repair within the same chain session.
---

Expert: {{ROLE}}
Focus: {{FOCUS}}

Read the actual failing command, exit status, trace and Host tool result. Reproduce
the smallest relevant local test using the admitted Profile runtime and data;
repair and rerun it in this session. Preserve the frozen hypothesis, metric,
dataset, evaluator contract and source references. Operational failure does not
scientifically refute a hypothesis, and a local Bash pass is not an experiment.

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
