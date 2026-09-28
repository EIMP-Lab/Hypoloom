# Execution and repair

Use the Host's current experiment state, returned diagnostics and exact ownership
before deciding whether to submit, replace, wait or hand off. Reuse successful
preparation and inspect the original failure evidence before editing. Distinguish
resource queueing, provider faults, implementation failures and scientific results.

Follow existing resource-planning, experiment-repair and Host tool instructions.
Do not launch GPU experiments directly, create competing schedulers or repeatedly
poll a Host-owned running Process. A waiting handoff is not a scientific outcome.
Repair only the demonstrated cause; preserve accepted measurements and submitted
resource/experiment ownership. Do not repeat an unchanged attempt when its stated
prerequisite is still absent.

Carry the experiment identity, relevant evidence and unresolved next action in
the current node's output. Use existing Host records rather than a second ledger.
