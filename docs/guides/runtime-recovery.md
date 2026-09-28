# GPU readiness, recovery and runtime observations

English · [简体中文](runtime-recovery.zh-CN.md)

New runs use ACI 28. Existing runs keep their pinned runtime; updating the
checkout does not migrate a retained run or restart its GPU service.

## Initialize GPU research

Refresh device observations on every init and review the exact UUID subset.
A previous selection is a default suggestion, not authorization for another
run. GPU occupancy describes capacity; it does not grant permission.

The standard managed pool lives under the project at
`.local/gpu-pools/shared/control.sock`. It is not a system-wide public pool.
Overlapping runs share the controller, while each run has its own namespace
and an explicitly allowed GPU subset. A compatible pool may contain more GPUs
than the subset selected for a run. Cross-project or multi-user sharing requires
an explicitly configured common service.

Init preview reports actual service capabilities and membership. Preparation
can retain a blocked/unavailable service for later repair; it does not reserve
resources. Host creation checks again before Grounding or any research Agent.
An ACI 28 GPU run requires `resource_replanning/1`. Busy compatible devices are
a capacity wait; an incompatible service or missing membership blocks launch.

Managed `ensure` creates the standard pool or reuses a compatible live service.
It refuses incompatible services, membership conflicts and overlapping named
pools. A stopped pool with an existing database requires explicit operator
recovery before init can proceed. It does not automatically replace or reconfigure an existing service:
a run can still depend on it while doing CPU work, waiting for an API, or paused.
An empty GPU or an empty request queue is insufficient evidence for replacement.
Pool stop/reconfiguration remains a separate operator action after dependency
and process cleanup checks. An unreachable or unauthorized endpoint is not
proof that its owner has gone away.

## Recover a pre-submit pool failure

If the Host observes an incompatible, inaccessible or unauthorized pool during
resource inspection or GPU submission, it records the affected node and Attempt
in the existing workflow history. A transport outage gets one bounded read-only
recheck. Persistent faults stop that Agent, archive its diagnostics and account
for its invocation as a technical failure. They do not produce an experiment,
scientific result or successful execution node. Native Stop can exit on this
Host-confirmed state without demanding a fabricated submission.

The affected node waits for resource recovery. It cannot bypass the fault by
submitting CPU work or `gpus: 0`. Unrelated nodes and already submitted experiments
retain their existing scheduling. Scientific candidate errors, insufficient
capacity requiring a smaller workload, and normal capacity queues keep their
existing handling. API recovery does not clear a GPU fault.

Repair the same bound service, then choose **Recheck GPU resources** in the Web
operator recovery link, press **g** in the TUI, or run:

```bash
hypoloom resource-recheck --state-root /absolute/state/root --run-id RUN_ID
```

The Host checks capability and allowed UUID membership and requires the faulting
Agent's cleanup to finish. It clears only the observed blocked nodes. Existing
retry, wall-clock and monetary budgets remain in force. A repeated request ID is
idempotent; after an uncertain reply, retry that request rather than creating
another action. Web control requires the existing scoped operator recovery link
(`--allow-provider-control`) and same-origin authentication. Monitoring links
remain read-only. This operation does not change devices, pool binding or API.

`resource_blocked` and `resource_rechecked` are available in the existing product
event stream and notification event selection. Adding either to an external sink
is an explicit configuration choice; existing subscriptions are not expanded.

## Interpret progress, context and costs

STATUS and Web show actual CPU/GPU grants, owned-process liveness, log byte
counts and last-write ages. Durable `OPEN` means unresolved ownership. It is not
a heartbeat. An alive process with silent logs has unknown scientific progress;
absence of output alone does not cause cancellation. Offline views cannot
confirm OS liveness. Support reports retain the available derived observations.

Training scripts should flush meaningful stage, trial/seed, epoch and elapsed-time
messages to stdout/stderr. Use bounded LOCAL screening before full GOAL evaluation
where the admitted methodology permits it. Do not silently reduce frozen trials,
change the parser or lower the goal to make a slow experiment finish.

Context measurement in ACI 28 retains complete provider usage when the only
change is appended text in the last user message (possibly followed by unchanged
CLI system messages), additional text messages, or known cache/text representation
changes. Model, tools, protocol, signed thinking and existing message content
remain exact. The original observation plus conservative new bytes supplies an
estimate; estimates never become measured anchors. Unknown changes retain the
conservative fallback and bounded reconstruction path.

Native compaction starts and completed boundaries are separate observations.
Local context refusals, provider context failures and incomplete forwarded requests
are also reported separately. Zero completed compactions does not prove no attempt
occurred; missing events and failure attribution remain unknown. These counters
are not inferred from a prompt mentioning “summary”.
The Web view separates transcript coverage from start-event coverage. With
`stdout_partial`, starts are only those retained in the log tail; missing earlier
events remain unknown, so starts and total completions cannot simply be subtracted.

CLI dollar values are reported estimates. Unknown model pricing means they are
not verified vendor bills or a monetary lower bound. Token usage, failed/recovery
invocations, reserved budgets and unknown charges stay separate; no automatic
cost-saving or model-quality claim follows from the repair.
