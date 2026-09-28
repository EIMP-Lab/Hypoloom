---
name: final-report
description: Write an evidence-cited final research report without replacing the accepted goal verdict.
---

Grounding owner: {{ROLE}}
Admitted task: {{FOCUS}}

Read the supplied goal, Step-1 background, accepted hypotheses and assessments,
preregistered measurement declarations, canonical outcomes, experiment logs and
the accepted terminal goal verdict. Cite actual experiment, hypothesis and
outcome identifiers. Separate supported contributions, clean scientific
refutations, inconclusive work and operational failures. Do not count local Bash
tests, Observer scores or an Agent's prose as accepted scientific experiments.

Write FINAL_REPORT.md at the exact output path assigned by this request. Cover
the goal and approach, evidence-backed findings, comparable measurements and
their interpretation, important failures and repairs, limitations and unresolved
questions, reproducibility pointers, and the honest reason work stopped. Do not
manufacture sources, claim unrun GPU/provider work or relabel a cancelled run
as success. Missing source bytes must be reported rather than guessed.

Parse every supplied `--accepted-evaluation-summary` JSON input and use it for
the corresponding outcome's measured cells, coverage and diagnostic details,
including when a later CYCLE_REPORT was never published. The
`accepted_evaluation_summaries` index in `--final-report-context` identifies
included and byte-budget-omitted summaries. A screen or partial matrix remains
diagnostic evidence: do not upgrade it to formal qualification, replace its
canonical metric, or infer unmeasured cells.

Extract the complete citation list from --final-report-context with a JSON
parser. Include every canonical outcome_id (including failures), the accepted
verdict fact_id and verdict text, or NO_ACCEPTED_VERDICT when absent, plus each
terminal Agent failure's attempt_id and failure_code. A compact appendix is
appropriate. Check that every required identifier appears in FINAL_REPORT.md.
The native Stop hook runs the same citation validator as Host acceptance;
repair its reported omissions in this file before finishing the current call.

The report is a narrative artifact derived from accepted records, not a new
GoalVerdictRecorded or permission to reopen research. The Host captures and
publishes the actual report bytes. Finish with the supplied structured response
and exact references; do not write canonical state, modify facts or send external
notifications yourself.
