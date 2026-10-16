"""The six and only six bounded startup-recovery queries from ADR-0002."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Protocol
class QueryTransaction(Protocol):
    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any: ...
Rows = tuple[Mapping[str, Any], ...]
def _rows(tx: QueryTransaction, sql: str, parameters: tuple[Any, ...] = ()) -> Rows:
    return tuple(dict(row) for row in tx.execute(sql, parameters).fetchall())
def abandoned_attempt_to_lost(tx: QueryTransaction, current_generation: str) -> Rows:
    """Candidates only; Host must prove the exact call/process group absent first."""
    return _rows(tx, "SELECT * FROM execution_attempts WHERE state='OPEN' AND host_generation<>? ORDER BY run_id,workflow_id,command_id,attempt_ordinal", (current_generation,))
def due_dispatch(tx: QueryTransaction, now: float) -> Rows:
    return _rows(tx, "SELECT t.* FROM workflow_transitions t WHERE t.next_command IS NOT NULL AND (t.due_at IS NULL OR t.due_at<=?) AND NOT EXISTS (SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision) AND NOT EXISTS (SELECT 1 FROM workflow_human_decisions d WHERE d.workflow_id=t.workflow_id AND d.state='OPEN') ORDER BY t.run_id,t.workflow_id", (now,))
def expired_deadline(tx: QueryTransaction, now: float) -> Rows:
    return _rows(tx, "SELECT * FROM workflow_human_decisions WHERE state='OPEN' AND deadline IS NOT NULL AND deadline<=? ORDER BY run_id,workflow_id,decision_id", (now,))
def interrupted_evaluation(tx: QueryTransaction) -> Rows:
    return _rows(tx, "SELECT t.* FROM workflow_transitions t WHERE t.workflow_kind='EXPERIMENT' AND t.state='EVALUATING' AND NOT EXISTS (SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision) AND NOT EXISTS (SELECT 1 FROM research_facts f WHERE f.run_id=t.run_id AND f.fact_type='CanonicalOutcomeAccepted' AND f.experiment_id=t.workflow_id) ORDER BY t.run_id,t.workflow_id")
def resume_waiting_decision(tx: QueryTransaction) -> Rows:
    return _rows(tx, "SELECT * FROM workflow_human_decisions WHERE state='OPEN' ORDER BY run_id,workflow_id,decision_id")
def resume_terminalizing_run(tx: QueryTransaction) -> Rows:
    return _rows(tx, "SELECT t.* FROM workflow_transitions t WHERE t.workflow_kind='RUN' AND t.state IN ('COMPLETING','STOPPING') AND NOT EXISTS (SELECT 1 FROM workflow_transitions n WHERE n.workflow_id=t.workflow_id AND n.revision>t.revision) ORDER BY t.run_id")
