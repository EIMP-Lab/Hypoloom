# Remove the expert research-round limit

The user requests removal of `stop_conditions.max_rounds_per_expert`, updated
Profile templates and publication on dev and public main. Previously, omitting
the field silently used the experiment count as a cycle ceiling. Ordinary
revision and Debate restarts could end with resources still available. Retained
older Leader research controllers also use that ceiling; current Leader guidance
does not itself grant research rounds. The ACI 30 input exposed a remaining-round
allowance even when the Profile omitted the field.

Fresh Profiles reject the removed field with a migration message. Their default
global experiment allowance is 50 times the number of declared experts, matching
the previous omitted-field default, without deriving it from research rounds.
This is a run-wide ceiling, not a separate allocation per expert. Templates omit the
field. Existing experiment, time, cost, invocation, iteration and repair limits
remain authoritative, along with goal acceptance, lineage termination and
operator cancellation. Round identifiers, ancestry, reports and evolution stay.

Use the existing ACI version boundary (31) to remove round checks for new runs.
Older accepted contracts and their independent audits retain their former
explicit/fallback limits. Strict fresh admission and historical contract reading
are separate uses of the existing Profile validator. No new persisted state,
schema table, counter, policy snapshot or unlimited-value sentinel is needed.
New expert inputs contain progress and inner-loop budgets without a round cap;
the adapter and seeded repair instructions explain the remaining stop conditions.

Validate removed-field rejection, direct CREATE_RUN admission, legacy replay,
ordinary continuation past the former implicit cap, Leader and Debate restart,
and actual termination on experiment/time/cost/repair budgets. Native CPU
multi-cycle closure exercises the same scheduler used for GPU work. Retained
research remains read-only; new behavior requires a fresh run. Validation and
publication evidence is recorded in the qualification guide.
