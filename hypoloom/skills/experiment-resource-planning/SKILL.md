# Experiment resource planning

This Host-injected compatibility guidance is not a callable Skill. Use it while
designing or executing an experiment. Read the executing role from
`agent_request_metadata.role`; keep
that role unchanged and scope every recommendation to its assigned component.
Do not hardcode a role or expect a per-role copy of this skill.

This is advice, not resource authority. Return only the fields in the supplied
Agent schema and explicitly exposed Host tools. The Host alone validates
availability, chooses actual resources and controls execution. If your native
plugin provides resource planning, invoke the exact name from the available Skill list
identified by this request's bound plugin for scoped strategy and learning.
Injected instructions are not an editable plugin or a live resource sample.

## Choose one preference

| Scientific condition | `resource_preference` |
| --- | --- |
| Pure documentation, report, checklist, or other non-production artifact work; or a method that is CPU-suitable and GPU acceleration is not material. | `CPU` |
| CPU remains scientifically valid, while a GPU may reduce runtime. | `AUTO` |
| CPU would make the method invalid or infeasible, supported by concrete evidence. | `GPU_REQUIRED` |

Use `GPU_REQUIRED` only for a real scientific or implementation requirement,
such as a demonstrated CUDA-only primitive or a measured CPU infeasibility.
GPU convenience is not enough. If CPU remains valid, use `AUTO` and accept that
the Host may grant CPU. If CPU is invalid, do not disguise unavailable GPU
capacity as a CPU-capable experiment; let the Host reject or defer it.

Pure documentation, report, checklist, and other non-production artifact work
must use `CPU`. Available GPU capacity does not make that work `AUTO`, and such
work must not request `GPU_REQUIRED`.

## Estimate VRAM from evidence

Estimate peak VRAM in MiB before relying on GPU execution. Prefer evidence in
this order:

1. a measured peak from the same model, method, precision, shapes, batch size,
   and software stack;
2. a nearby measurement with an explicit scaling relation for every changed
   dimension; or
3. a conservative accounting of parameters, gradients, optimizer state,
   activations, temporary workspaces, and framework overhead.

State the evidence reference, assumptions, calculation, uncertainty, and
headroom. Keep the workload estimate separate from the Host's reserve and from
a device's point-in-time free memory. Do not turn a GPU model name, total VRAM,
one transient sample, or an unsupported guess into a precise requirement. When
the evidence is insufficient, say so and prefer a bounded measurement rather
than fabricating precision.

## Respect shared GPU ownership

Retained nonpooled execution uses fixed grants: the Host observes
operator-allowlisted identities plus request-time same-root
busy UUIDs and prefers greatest live free VRAM, using stable UUID only as a
deterministic tie-break. For `AUTO`, it considers only non-busy identities and
grants GPU only when that device also meets the
operator-reviewed peak VRAM estimate plus reserve; otherwise it grants CPU.
For `GPU_REQUIRED`, it prefers the best non-busy identity even when current
capacity is low. Only when every authorized identity is already busy in this
state root does it freeze the best authorized UUID and wait for it rather than
rejecting the scientific requirement. That grant remains fixed for its retries.

Explicitly pooled execution instead freezes authorized intent and a per-device
workload budget. Shared occupied devices may qualify when capacity fits; the pool
chooses actual devices per Attempt, and a retry may move cards. GPU capacity wait
does not invent a scientific failure or authorize a private allocator fallback.
The old same-root non-busy/fixed-grant rule does not govern pooled scheduling.
When exposed in DESIGN or EXECUTION, `mcp__host-tools__get_resources` provides
timestamped authorized capacity and this expert's bounded measured history.
Initial injected snapshots remain historical; live queries do not rewrite them.
DESIGN receives only that read-only query, never submit or cancel privileges.

Never choose, name, rank, or request a UUID, device
index, device node, runtime prefix, mount, or environment variable; those
remain Host decisions.

Treat injected total/free/used MiB and utilization as device-level observations
only. Do not infer process ownership, user identity, or per-worker VRAM from
them. A Host pressure summary may report minimum/latest free memory, latest
utilization, sample count, or sustained pressure after a grace period; it is
persisted only as a closed-Attempt observation and is neither a resource grant
nor proof about another process.

Never kill, signal, reset, suspend, renice, or otherwise control an external
process to obtain capacity, and never recommend that the expert do so. If shared
capacity changes, return the scientific preference and let the Host fall back,
defer, or fail closed according to that preference.

## Submit estimates and retain scoped learning

On explicitly pooled runs, ACI18 EXECUTION may pass `resource_estimate` to
`submit_experiment`: `workload`
is a bounded flat string map identifying comparable configuration;
`peak_memory_mib` is a positive per-device estimate including workload headroom;
`basis` explains evidence and uncertainty; optional `measurement_attempt_ids`
names exact comparable CLOSED Process Attempts from this run/role/component/
method-family. This field belongs to the submit tool, not the scientific
candidate. Omission keeps the Profile initial workload budget when configured,
otherwise its floor, and marks the workload unestimated.
The Host resolves measured references at first acceptance and preserves them in
the existing source. The admitted budget is never below the operator floor,
submitted estimate or compatible measured/OOM lower bound with Host headroom.

For new explicitly pooled runs, optional `initial_vram_per_experiment` is a
conservative starting budget separate from the hard `min_vram_per_experiment`.
Without a complete successful comparable Process measurement, admission also
keeps this initial value. After such evidence, a new submission may reduce the
estimate while retaining the hard minimum, measured/OOM bounds and device
reserve. Explain reductions and cite successful Attempt IDs. Failed/interrupted
work or partial/unknown samples cannot release the initial budget. Updating a
skill affects later decisions; it never changes an accepted experiment's budget.

If the current interface has no estimate field, emit no unsupported proposal
and do not overload another field. Base resource lessons
on durable, comparable evidence such as a measured peak, an observed OOM, or a
completed same-workload trial. Identify the executing role, workload scope,
evidence references, old assumption, proposed value or preference, uncertainty,
and reason.

Do not update a profile, reserve, allowlist, runtime, Host policy, or current
grant. Do not generalize from another role or materially different workload,
and do not learn ownership claims from device-level telemetry. The operator and
Host retain resource authority. CYCLE_REPORT can retain scoped measured lessons;
only SELF_EVOLVE's writable native plugin permits a strategy update, selectable
for a later cycle. Preserve unknown own usage as unknown and distinguish an
explicit observed OOM from pressure cancellation. Never turn transient live
capacity into a permanent skill rule.
