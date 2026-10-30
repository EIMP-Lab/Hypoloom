# GPU readiness, recovery and runtime observations

English · [简体中文](runtime-recovery.zh-CN.md)

Existing runs keep their original runtime; updating the
checkout does not migrate a retained run or restart its GPU service.

## Web observation freshness

The Web observer reads its last complete observation while the next one is
prepared in the background. **Preparing snapshot** means the first read is still
in progress. **Connected · updating** means the connection works and a replacement
is being prepared. **Data …s old** reports the age of the data, even when HTTP
requests succeed. **Refresh failed** preserves the previous observation while
the server retries. **Poll timeout** describes the browser request, not an
experiment deadline or proof that the Host has stopped.

The browser polls every three seconds after each response, allows eight seconds
per request, and marks data older than 15 seconds as stale. Hidden tabs pause
polling. Returning to the tab requests an update. History and selected details
load independently, so a slow detail does not stop status polling.

A busy Host or a large history can delay observations. Activity and RPM may
remain unavailable until a fresh Host reply arrives. Tool counts and evidence
coverage can initially be unknown while history loads; unknown does not mean
zero activity or a missed action.

After updating the observer, reopen the monitor; reloading the browser alone
does not update its backend. Use the same state root and run ID and use its new startup URL. Leaving the monitor with
`q` does not stop the Host or its experiments. Do not resume or migrate a run to
update its observer.

## Scientific iterations and execution repairs

Expert cards separate initial experiments, scientific iterations and execution
repairs in the latest accepted cycle. Parallel hypotheses and successful scientific
iterations do not increase another execution node's repair count. Expert details
show each chain/iteration's recorded repairs, its admitted repair allowance,
experiment identities and recorded outcome or validation code.

An allowance-used observation requires both the scoped count and a recorded
repairable failure. Reaching the count while an experiment is pending is not a
failure; successful or cleanly refuted execution clears the repair warning while
retaining its history. Older iterations and stopped runs label warnings historical.
Counts do not establish scientific convergence or identify a model-related cause.

Batch runs use their recorded version, execution-node identities and frozen Profile
budget. Legacy single-chain runs retain their original cycle thresholds; missing
batch identity is unknown, not a zero count or a guessed legacy chain.
An independent observer can read supported retained records without replacing
the run bundle or changing its resource limits and scientific criteria.

## Completion and final-report evidence

Web shows recorded final node failures separately from durable run state. Expand
the list for owner, stage, chain, time, reason and Attempt details. These nodes
have exhausted automatic recovery; unrelated work may continue. Check Host
liveness, dispatch errors and Process observations to assess the whole run.
Failures remain visible after completion, and TUI uses the same node count.

New runs keep bounded diagnostic log excerpts valid UTF-8, including progress
bars cut at the byte limit; non-text log bytes appear as replacement characters.
Original logs remain available unchanged. This prevents a log excerpt from
blocking an otherwise valid analysis request before the model is called.

Complete responses must be one JSON object as ordinary final text. Let the native
Stop hook validate that message; calling its internal socket manually does not
finish the response. A connection closed before a complete request is recorded
without disabling later validation. A changed invalid completion revokes prior
acceptance and can be corrected within the same configured limit and deadline.
The Host requires the actual final message to match the accepted text; it never
substitutes an earlier candidate or removes surrounding prose. Invalid session
identity, malformed complete transport frames and validator failures remain fatal.
These changes apply to new runs and do not rewrite historical red error records.

Local parser `value` and optional `baseline` must be finite scalar numbers;
per-dataset detail belongs in raw artifacts. Invalid fields are named as
`result.value` or `result.baseline`. Native Stop rejections survive into retry
inputs without a secondary missing-source error replacing the cause. VALUE
comparison may omit baseline; other declared comparisons still require it.

Implementation completion checks the actual sources and cumulative Git patch
before the native Agent exits. Oversized output is returned to that same call
for correction within the configured Stop limit. The invocation's output-byte
budget includes the candidate and Base64-encoded files: tracked and untracked
changes both count, and a captured source also appears in its patch. Reduce the
actual files when this feedback appears. The final response still undergoes full
validation, including usage and diagnostics that become available only at exit;
leave space for that metadata. Updating the checkout does not change old runs.

After the CLI exits, new runs accept identical completion replies caused by same-session
background-task notifications only after identity, current-output validation and
cumulative accounting checks. The final cumulative ledger is charged once.
Changed or unaccounted extra completions remain failures.

Final reports receive Agent attempt status counts and unsuccessful-attempt
references separately from exhausted/nonretryable nodes. Recovered failures and
operator cancellations still belong in the history. Bounded accepted summaries
and declared raw text measurements come from verified publications; the report
index identifies omitted files. Missing GOAL acceptance is not evidence that no
raw test observations exist. LOCAL or failed-work observations cannot become a
new accepted verdict through the report.

Per-outcome Process rows and the response summary use the same
`process_attempt_details` and `process_attempts_by_returncode` inputs. Explicit
return codes, terminal reasons and validation codes are checked against those
records in both the Markdown report and the response summary. A canonical
`EXECUTION_FAILURE` can coexist with a `CANCELLED` Process; it does not establish
`raw_execution_failed` or rc=70. Retained reports are never rewritten to make
them pass a newer audit.

When Grounding retrieval loses required literature strata after a valid query
probe, the Host records the actual pre-model retrieval failure and replans that
exact query within the existing retry limits. It neither invents an Agent call
nor copies earlier model charges. Host restart reconstructs the same decision
from durable Attempts. No existing run is migrated by updating this checkout.


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

Init uses `bootstrap inspect` to separate present readiness from a safe
preparation plan. A missing or incompatible project pool can offer Start now
when its creation or update is reviewable. The Host requires
`resource_replanning/1` before Grounding or any research Agent. Busy compatible
devices are a capacity wait.

`--managed-pool` connects bootstrap to the managed service helper. Review the
exact `--pool-action` (create, reuse, restore, upgrade or reconfigure), full pool
membership and reserve. The run's UUID subset is separate from pool membership.
A compatible pool is reused without restart; an upgrade preserves its known
configuration and database. A stopped service whose configuration is unknown
requires explicit reconfiguration of those values. Plain `ensure` retains its
create/reuse-only behavior; replacement requires the reviewed action.

Maintenance checks CREATED bindings, terminal run states, controller/process
ownership and every unreleased pool request. CPU work, Grounding, API waits and
paused runs remain dependencies even with no GPU requests. Unknown ownership or
an unreadable database blocks replacement. Short-lived directory coordination
covers maintenance and new Host admission; known controller locks prevent old
roots from resuming during maintenance. These are project-local checks, not a
machine-wide registry. The canonical pool requires new state roots under its
repository's `state/`; external roots and cross-project sharing use explicitly
configured external services. External services are not automatically replaced.

Bootstrap prepares the pool, verifies the live service, then starts the Host
and creates the run. Failures preserve the current init choices and offer
recheck, revised selection, Prepare only or cancellation. There is no silent
nonpooled/CPU fallback. Prepare only performs no pool or Host lifecycle action.

For an exact reviewed `RUN_ID:REVISION`, init can use the original frozen Host
to close a retained run whose Host/process ownership is absent and whose pool
requests are released. Original launch inputs must be available. CANCEL precedes
Attempt recovery; only closure and the original budgeted final report may run.
The stopped pool need not acknowledge already released requests again. Existing
dependency checks still run after closure. Legacy bundles lacking this capability
remain blocked rather than being migrated; see [Initialization](initialization.md).
A healthy pool survives a later Host-start failure. Service history is retained;
there is no automatic database migration or guaranteed binary rollback.
See [initialization](initialization.md) for the compact entry point and timings.

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

Context measurement retains complete provider usage when the only
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

Web/TUI usage indicators use tokens throughout: reported usage, failed/recovery
invocations, reservations and unknown usage stay separate. Missing model prices
are not operational alerts. Historical cost evidence and configured financial
limits remain available internally; this does not imply verified savings.

A Host tick counts an Agent dispatch only after admitting its Attempt. Visiting
an execution controller whose owned experiment is still pending does not consume
the tick's dispatch allowance, so ready peer tasks can continue. Idle authorized
GPUs do not by themselves prove a scheduler problem: inspect runnable requests,
actual grants and Process liveness separately.
