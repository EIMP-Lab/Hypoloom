# Reading assistants and parallel proposals

English · [简体中文](reading-and-proposals.zh-CN.md)

New ACI-26 runs can execute one expert's FORWARD and BACKWARD arguments at the
same time. The Host records FORWARD first, so both wings retain the same admitted
Leader advice. It starts BACKWARD when ordinary Agent capacity and budgets permit.
SYNTHESIS requires both accepted results. A single available slot runs the wings
serially. Existing runs keep their pinned runtime; there is no migration.

These are two fixed tasks, each with its own session and workspace. They do not
create two hypotheses. SYNTHESIS may propose one or several independently testable
mechanisms. One candidate remains appropriate when evidence is limited, work is
dependent, or alternatives only change hyperparameters. Explain this choice in
the existing candidate's `claim` or `assumptions`; do not add a new output field.

| Setting | Meaning |
| --- | --- |
| `max_hypotheses_per_round` | Configured candidate ceiling per expert/round |
| `effective_max_forks` in the actual request | Admitted candidate ceiling for this proposal |
| `max_forks` | Active research-chain ceiling for the expert |
| `max_active_agents` | Shared Agent capacity, including arguments and readers |

A successful wing is retained while its sibling retries. If a required wing
finally fails, the Host cancels and drains its still-running sibling; it does
not invent a scientific refutation. Stop, provider recovery and accounting use
the existing mechanisms.

## Preparing and delegating a paper

Enable `read_papers` in the Profile's literature tool list when desired. A reader
needs a source already admitted to its parent Attempt. Existing entries are in
`/papers/INDEX.md`; prepare a new original through the existing document tool:

```text
web_fetch(mode="document", url="https://example.org/paper")
  -> status="available", source_ref=...
read_papers(action="submit", readings=[{
  "reading_id": "method-check",
  "paper_ref": <returned source_ref>,
  "question": "Which assumptions support the reported mechanism?"
}])
read_papers(action="wait", max_wait_seconds=10)
```

Copy `source_ref` exactly into `paper_ref`; a URL or DOI is not an admitted
reference. The tool does not guess a paper or silently fetch a mistyped reference.
Submit accepts only `action` and `readings`; use separate status, bounded wait or
cancel calls. Unknown fields are named without echoing their values. Correct the
reported field and resubmit. Invalid batches admit no readers; identical reading
IDs and questions reuse the original command, while conflicting reuse is rejected.

Readers consume ordinary Agent and financial budgets. The parent checks their
evidence and incorporates it into its own summary. When no reader can start and
wait reports capacity or budget limits, cancel unstarted questions and read the
original serially or report the limitation. Readers cannot delegate recursively.

## Understanding the expert card

The overview card shows current work, Agent activity, token usage and operational
notices. Choose **Expert details** for active-chain limits, this cycle's accepted
candidates, FORWARD/BACKWARD/SYNTHESIS status, reading tasks and trace coverage.
The drawer follows the selected expert and updates with observation polling.
**Browse expert history** opens that expert's sessions and tools. Reader counts use
admitted command identities; retries increase Attempts, not tasks. Rejected reading
calls do not count as assistants or incur invented child charges. Actual calls,
including failed retries, remain in the existing token/cost fold.

Tool observations use the current invocation's captured tool/result events,
deduplicated by existing IDs. Incomplete or missing traces show a lower bound and
unknown coverage. A recorded wait reason may have changed; a missing reason does
not establish why dispatch is waiting. OPEN is durable state, not a heartbeat.
CLI/support projections use the same read-only summary.

This change does not guarantee a fixed speedup or better scientific conclusions.
See [qualification](../qualification.md) for measured coverage and limitations.
