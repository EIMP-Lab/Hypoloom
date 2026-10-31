# Configuration studies and expert guidance

Status: implemented, 2026-10-31; [validation](../qualification.md#configuration-studies-and-expert-guidance-2026-10-31).
The user authorized development, dev
publication and selective public synchronization to main. Profiles and retained
research runs remain unchanged; scientific synthesis and a future Profile are
separate work.

## Failure and boundary

A retained-run review found inherited training settings presented without a
bounded comparison, a search interrupted after only its first configuration,
and cross-dataset averages hiding opposite effects. Synthetic reproductions
must distinguish an unrun configuration from a losing one, detect a training
configuration differing from its plan, and detect a final evaluation changing
the selected recipe. A component TrialPlan already identifies seeds and arms;
it is not a hyperparameter study and must not acquire a second scheduler.

The failed boundary is the interpretation of design and training evidence.
Git, admitted source, raw artifacts and existing publication already preserve
bytes and lifecycle identity. They cannot by themselves compare configuration
values, planned coverage and validation-only rankings. A small, stateless
standard-library reader supplies that comparison. Its explicit JSON input
format is necessary to make these relationships executable; it is not a new
database, receipt, admission contract or scientific success gate. No new hash,
checkpoint, persisted verdict, Process state or migration is introduced.

## Smallest mechanism

`hypoloom.configuration_study` audits a finite candidate matrix from separate
plan, trial, optional selection and optional evaluation documents. It checks
actual resolved configurations, source/data references, seeds, attempts and
trial budget; reports missing/failed/interrupted work; ranks only complete
candidates using validation measurements and explicit dataset weights; and
compares final evaluation configurations with the selection. Ties retain plan
order. An incomplete matrix yields only a provisional selection, never a claim
of an optimal parameter or shared-goal success. Each dataset's score remains
visible. The task, not this reader, determines scientific acceptance and the
scope of transfer to unseen datasets. Finite candidate enumeration is deliberate;
adaptive search and pruning require their own declared study, not invented rows.

Architecture and training sharing are explicit plan choices, with separate
adaptation and runtime dictionaries. This supports a common architecture with
dataset-specific output dimensions and optionally different training settings,
without imposing this policy on unrelated tasks. Configuration equality is
recursive JSON type/value equality, not a digest. Failed attempts remain in the
budget and cannot be silently replaced by a retry; duplicate successes and
noncontiguous attempt histories are rejected.

The reader verifies supplied evidence relationships. It cannot prove that an
agent's self-reported configuration was executed or that its plan predates the
results. Training must emit resolved settings; the plan and selection must use
ordinary admitted source/Git and existing artifact provenance. A future task
may include the helper in its frozen measurement source and use the report in
its existing parser. This change adds no universal acceptance requirement and
does not alter any Profile. No artifact-validator kind or Oracle reducer is
needed for an advisory tool that owns no accepted outcomes.

One module is the code source. Fresh expert and Grounding plugins receive a
standalone copy under the existing skill scripts directory; it follows their
ordinary Git binding, evolution and byte budget. Existing plugins are untouched.
The bound parameter-study reference reaches DESIGN, EXECUTION and ANALYSIS
through the existing bounded prompt projection, sharing its context allowance
with performance guidance. Receiving guidance proves neither its execution nor
scientific adequacy. Other stages follow the skill's references as needed.

## Cost, removal and verification

Maintenance is one pure reader/CLI, one seed copy, one reference and prompt
projection tests. Remove the reader if the accepted experiment-result contract
later provides these same comparisons, and remove special projection if native
skill delivery reliably supplies the applicable full text. Do not retain two
authorities. Tests cover partial and failed searches, retries, independent
per-dataset results, strict configuration equality, test leakage, frozen final
evaluation, CLI errors, plugin version isolation and context budgets. Existing
real Host/CPU scenarios verify delivery, collection, recovery and closure.
Qualification records distinguish those checks from paid-model behavior, GPU
performance or the scientific quality of any new method.

Full regression exposed a pre-existing budget mismatch amplified by the larger
plugin: expert view reads without an explicit plugin limit used the request's
inline input limit, whereas seeding, Grounding reads and evolution already used
the independent 1 MiB plugin default. Align expert reads with that same default;
explicit `max_plugin_bytes` remains enforced. The input and output budgets are
unchanged. A real pinned/unpinned native plugin test reproduces the failure and
focused cases cover default loading and explicit rejection. No new budget or
permission is introduced.
