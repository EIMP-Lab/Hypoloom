"""Single accounting fold for durable Agent attempts.

The frozen request is the reservation.  A valid terminal response or provider
accounting replaces that reservation with actual usage; unknown outcomes keep
the declared ceiling so retries cannot oversubscribe a profile budget.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .agent_aci import AgentACIError, AgentBudget
from .agent_context import (
    RETRYABLE_AGENT_FAILURES,
    agent_retry_class,
    candidate_failure_retryable,
)


PROFILE_BUDGET_FIELDS = frozenset({
    "agent_invocation", "run_tokens", "wallclock_hours", "per_expert_tokens",
    "max_experiments", "max_papers", "experiment_timeout", "max_retries",
    "max_infrastructure_retries", "max_process_lost_retries",
    "max_active_hypotheses", "min_vram_per_experiment",
    "gpu_vram_reserve_mb", "initial_vram_per_experiment", "max_turns",
    "total_budget_usd", "per_expert_budget_usd", "max_active_agents",
    "max_active_processes", "max_active_reviews", "provider_auth_cooldown_seconds",
})
ROLE_BUDGET_FIELDS = frozenset({
    "per_expert_tokens", "max_experiments", "max_papers", "timeout", "max_turns", "per_expert_budget_usd",
})
AGENT_INVOCATION_BUDGET_FIELDS = frozenset({
    "max_turns", "max_tokens", "max_output_bytes", "wall_seconds", "max_cost",
    "token_enforcement_mode",
})
# Dispatch pause after a provider authentication rejection (rolling quota
# windows included). Zero disables the gate; seven days caps operator input.
DEFAULT_PROVIDER_AUTH_COOLDOWN_SECONDS = 5 * 3600.0
MAX_PROVIDER_AUTH_COOLDOWN_SECONDS = 7 * 86400.0


@dataclass(frozen=True, slots=True)
class AgentAttemptCharge:
    attempt_id: str
    role: str | None
    state: str
    source: str
    tokens: int | None
    turns: int | None
    cost: Decimal | None
    budget: Mapping[str, Any] | None
    run_scoped: bool = False


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _cost(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _usd_cap(value: object, name: str) -> Decimal | None:
    if value is None: return None
    parsed = _cost(value)
    if parsed is None or parsed <= 0: raise ValueError(f"{name} must be finite positive USD")
    return parsed


def charged_cost(charges: Iterable[AgentAttemptCharge], role: str | None = None) -> Decimal:
    selected = tuple(item for item in charges if role is None or item.role == role and not item.run_scoped)
    if any(item.cost is None or item.role is None for item in selected):
        raise ValueError("Agent attempt has no valid frozen cost identity")
    return sum((item.cost for item in selected), Decimal("0"))


def profile_effect_capacity(document: Mapping[str, Any], kind: str) -> int:
    field = {"AGENT": "max_active_agents", "PROCESS": "max_active_processes", "REVIEW": "max_active_reviews"}[kind]
    budget = document.get("budget", {})
    value = budget.get(field, budget.get("max_active_hypotheses", 3))
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"budget.{field} must be positive")
    return value


def _declared(budget: Mapping[str, Any] | None) -> tuple[int | None, int | None, Decimal | None]:
    if budget is None:
        return None, None, None
    return _integer(budget.get("max_tokens")), _integer(budget.get("max_turns")), _cost(budget.get("max_cost"))


def _reported(value: object, *, provider: bool = False) -> tuple[int, int, Decimal] | None:
    if not isinstance(value, Mapping):
        return None
    usage = value.get("token_usage")
    tokens = _integer(usage.get("total_tokens")) if isinstance(usage, Mapping) else None
    turns = _integer(value.get("turn_count")); cost = _cost(value.get("cost"))
    if provider and value.get("cost_currency") != "USD":
        return None
    return None if tokens is None or turns is None or cost is None else (tokens, turns, cost)


def fold_agent_attempts(rows: Iterable[Mapping[str, Any]]) -> tuple[AgentAttemptCharge, ...]:
    """Return the conservative charge for every durable AGENT attempt."""

    charges: list[AgentAttemptCharge] = []
    for row in rows:
        if row.get("kind") != "AGENT":
            continue
        try:
            observation = json.loads(row.get("observation_json"))
        except (json.JSONDecodeError, TypeError):
            observation = {}
        observation = observation if isinstance(observation, Mapping) else {}
        request = observation.get("request")
        request = request if isinstance(request, Mapping) else {}
        budget = request.get("budget")
        budget = budget if isinstance(budget, Mapping) else None
        role = request.get("role"); role = role if isinstance(role, str) and role else None
        state = str(row.get("state", "")); source = "declared"
        charge = _declared(budget)
        if (state == "CLOSED"
                and observation.get("failure_code") == "profile_budget_exhausted"):
            source, charge = "rejected", (0, 0, Decimal("0"))
        elif state == "CLOSED" and (reported := _reported(observation.get("response"))) is not None:
            source, charge = "response", reported
        elif state == "CLOSED" and (reported := _reported(observation.get("rejected_response"))) is not None:
            source, charge = "rejected_response", reported
        elif state == "CLOSED" and (reported := _reported(observation.get("discarded_response"))) is not None:
            source, charge = "discarded_response", reported
        elif state == "CLOSED":
            diagnostics = observation.get("diagnostics")
            accounting = diagnostics.get("provider_accounting") if isinstance(diagnostics, Mapping) else None
            if (reported := _reported(accounting, provider=True)) is not None:
                source, charge = "provider", reported
        if source == "declared":
            diagnostics = observation.get("diagnostics")
            observed = diagnostics.get("provider_observed_usage") if isinstance(diagnostics, Mapping) else None
            lower_bound = _integer(observed.get("total_tokens")) if isinstance(observed, Mapping) else None
            if lower_bound is not None and charge[0] is not None:
                # Unknown cost remains reserved; observed tokens can increase,
                # but never reduce, the unknown Attempt's conservative charge.
                charge = (max(charge[0], lower_bound), charge[1], charge[2])
        task = request.get("task_context")
        expected = {"GRAPH_EVOLVE": "GRAPH_EVOLVE", "OBSERVER": "OBSERVER", "FINAL_REPORT": "FINAL_REPORT", "DEBATE_JUDGE": "DEBATE_JUDGE",
            "GROUNDING": "GROUNDING", "LITERATURE_REVIEW": "GROUNDING_QUERY_PLAN"}.get(request.get("command"))
        run_scoped = (type(request.get("schema_version")) is int and request["schema_version"] >= 14
            and expected is not None and isinstance(task, Mapping) and task.get("kind") == "AUXILIARY"
            and task.get("request_kind") == expected and task.get("component_id") is None and task.get("expert_slot") is None)
        charges.append(AgentAttemptCharge(str(row.get("attempt_id", "")), role, state, source, *charge, budget, run_scoped))
    return tuple(charges)


class AgentChargeCache:
    """Disposable compact charges; only committed CLOSED rows are immutable.

    A transaction may consume cached committed charges, but never cache its
    own closes: rollback must not leave an uncommitted charge behind.
    """

    def __init__(self):
        self._closed: dict[str, AgentAttemptCharge] = {}

    def read(self, reader, run_id: str, *, committed: bool = False) -> tuple[AgentAttemptCharge, ...]:
        if hasattr(reader, "readonly_connection"):
            from .state_store import StateTransaction
            with reader.readonly_connection() as connection:
                connection.execute("BEGIN")
                return self.read(StateTransaction(connection), run_id, committed=committed)
        charges = []
        for row in reader.list_attempts(run_id=run_id, kind="AGENT", include_observation=False):
            charge = self._closed.get(row["attempt_id"]) if row["state"] == "CLOSED" else None
            if charge is None:
                charge, = fold_agent_attempts((reader.get_attempt(row["attempt_id"]),))
                if committed and row["state"] == "CLOSED":
                    self._closed[row["attempt_id"]] = charge
            charges.append(charge)
        return tuple(charges)


def agent_attempt_limits(
    document: Mapping[str, Any], *, model_fallback: int, infrastructure_fallback: int,
) -> tuple[int, int]:
    budget = document.get("budget", {})
    if not isinstance(budget, Mapping): raise ValueError("profile budget is invalid")
    limits: list[int] = []
    for field, fallback in (("max_retries", model_fallback),
                            ("max_infrastructure_retries", infrastructure_fallback)):
        configured = budget.get(field)
        if configured is None: limits.append(fallback)
        elif isinstance(configured, bool) or not isinstance(configured, int) or configured < 0:
            raise ValueError(f"profile {field} is invalid")
        else: limits.append(configured + 1)
    return limits[0], limits[1]


def effect_attempt_cap(
    document: Mapping[str, Any], kind: str, *, model_fallback: int,
    infrastructure_fallback: int, process_fallback: int,
) -> int:
    if kind == "agent":
        model, infrastructure = agent_attempt_limits(
            document, model_fallback=model_fallback,
            infrastructure_fallback=infrastructure_fallback,
        )
        return model + infrastructure - 1
    if kind != "process": raise ValueError(f"unknown effect kind {kind!r}")
    budget = document.get("budget", {})
    configured = budget.get("max_process_lost_retries") if isinstance(budget, Mapping) else None
    if configured is None: return process_fallback
    if isinstance(configured, bool) or not isinstance(configured, int) or configured < 0:
        raise ValueError("profile max_process_lost_retries is invalid")
    return configured + 1


def agent_attempts_exhausted(
    document: Mapping[str, Any], attempts: Iterable[Mapping[str, Any]], *,
    model_fallback: int, infrastructure_fallback: int,
) -> bool:
    closed = sorted((row for row in attempts if row["state"] == "CLOSED"),
                    key=lambda row: int(row["attempt_ordinal"]))
    if not closed: return False
    limits = agent_attempt_limits(document, model_fallback=model_fallback,
                                  infrastructure_fallback=infrastructure_fallback)
    counts = {"MODEL": 0, "INFRASTRUCTURE": 0}; latest: str | None = None
    for row in closed:
        observation = json.loads(row["observation_json"])
        if row["terminal_reason"] == "SUCCEEDED": retry_class = "MODEL"
        elif "retry_class" in observation:
            recorded = observation["retry_class"]
            retry_class = str(recorded) if recorded in counts else None
        else:
            code = str(observation.get("failure_code", ""))
            retryable = (code in RETRYABLE_AGENT_FAILURES or candidate_failure_retryable(code)
                         or code in {"host_restart", "agent_workspace_preparation"}
                         or code.startswith("literature_") and code != "literature_query_invalid")
            retry_class = agent_retry_class(code, observation, retryable=retryable)
        latest = retry_class
        if retry_class in counts: counts[retry_class] += 1
    if latest is None: return True
    return counts[latest] >= limits[0 if latest == "MODEL" else 1]


def charged_totals(charges: Iterable[AgentAttemptCharge], role: str | None = None) -> tuple[int, int]:
    selected = tuple(item for item in charges if role is None or item.role == role and not item.run_scoped)
    if any(item.tokens is None or item.turns is None or item.role is None for item in selected):
        raise ValueError("Agent attempt has no valid frozen budget identity")
    return sum(item.tokens or 0 for item in selected), sum(item.turns or 0 for item in selected)


def profile_agent_budget(document: Mapping[str, Any], fallback: AgentBudget) -> AgentBudget:
    raw_budget = document.get("budget")
    invocation = raw_budget.get("agent_invocation") if isinstance(raw_budget, Mapping) else None
    if invocation is None:
        return fallback
    budget = AgentBudget.from_dict(invocation)
    if budget.max_cost is not None:
        canonical_cost = format(Decimal(budget.max_cost).normalize(), "f")
        if canonical_cost != budget.max_cost or budget.max_cost.startswith("-"):
            raise AgentACIError("invalid_budget", "max_cost must be canonical nonnegative decimal text")
    return budget


def profile_role_cap(document: Mapping[str, Any], role: str, field: str) -> int | None:
    budget = document.get("budget"); roles = document.get("roles")
    if not isinstance(budget, Mapping) or not isinstance(roles, (list, tuple)):
        raise ValueError("research contract budget/roles are invalid")
    record = next((item for item in roles if isinstance(item, Mapping) and item.get("id") == role), None)
    if record is None or not isinstance(record.get("budget", {}), Mapping):
        raise ValueError(f"role {role!r} has no valid budget record")
    value = record.get("budget", {}).get(field, budget.get(field))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def validate_initial_vram_budget(budget: Mapping[str, Any]) -> None:
    if "initial_vram_per_experiment" not in budget:
        return
    initial = budget["initial_vram_per_experiment"]
    minimum = budget.get("min_vram_per_experiment", 0)
    if (type(minimum) is not int or minimum <= 0 or type(initial) is not int
            or not minimum <= initial < 2**53):
        raise ValueError("budget.initial_vram_per_experiment must be a JSON-safe integer "
                         "at least the positive min_vram_per_experiment")


def validate_new_run_budget(document: Mapping[str, Any], fallback: AgentBudget) -> AgentBudget:
    """Validate every profile budget value consumed by a newly created run."""

    budget = document.get("budget")
    if not isinstance(budget, Mapping) or "agent_invocation" not in budget:
        raise ValueError("new runs require budget.agent_invocation")
    unknown = set(budget) - PROFILE_BUDGET_FIELDS
    if unknown:
        raise ValueError(f"budget has unknown fields: {sorted(unknown)!r}")
    validate_initial_vram_budget(budget)
    if not isinstance(budget["agent_invocation"], Mapping):
        raise ValueError("new runs require budget.agent_invocation to be an object")
    if "token_enforcement_mode" not in budget["agent_invocation"]:
        raise ValueError(
            "new runs require budget.agent_invocation.token_enforcement_mode"
        )
    invocation = profile_agent_budget(document, fallback)
    positive = {"run_tokens", "per_expert_tokens", "max_experiments", "max_papers",
                "experiment_timeout", "max_active_hypotheses", "max_turns",
                "max_active_agents", "max_active_processes", "max_active_reviews"}
    nonnegative = {
        "max_retries",
        "max_infrastructure_retries",
        "max_process_lost_retries",
        "min_vram_per_experiment",
        "gpu_vram_reserve_mb",
    }
    for name in positive | nonnegative:
        value = budget.get(name)
        if name in budget and (isinstance(value, bool) or not isinstance(value, int)
                               or value < (1 if name in positive else 0)):
            raise ValueError(f"budget.{name} has an invalid bound")
    for name in ("total_budget_usd", "per_expert_budget_usd"):
        if name in budget and budget[name] is None: raise ValueError(f"budget.{name} cannot be null")
        _usd_cap(budget.get(name), name)
    hours = budget.get("wallclock_hours")
    if "wallclock_hours" in budget and (isinstance(hours, bool) or not isinstance(hours, (int, float))
                                         or not 0 < float(hours) < float("inf")):
        raise ValueError("budget.wallclock_hours must be finite and positive")
    cooldown = budget.get("provider_auth_cooldown_seconds")
    if "provider_auth_cooldown_seconds" in budget and (
            isinstance(cooldown, bool) or not isinstance(cooldown, (int, float))
            or not math.isfinite(float(cooldown))
            or not 0 <= float(cooldown) <= MAX_PROVIDER_AUTH_COOLDOWN_SECONDS):
        raise ValueError("budget.provider_auth_cooldown_seconds must be a finite number "
                         f"from 0 to {int(MAX_PROVIDER_AUTH_COOLDOWN_SECONDS)}")
    for name, maximum in (("run_tokens", invocation.max_tokens), ("per_expert_tokens", invocation.max_tokens),
                          ("max_turns", invocation.max_turns)):
        if isinstance(budget.get(name), int) and budget[name] < maximum:
            raise ValueError(f"budget.{name} is below one Agent invocation")
    roles = document.get("roles")
    if not isinstance(roles, (list, tuple)):
        raise ValueError("research contract roles must be a list")
    role_records = [(item.get("id"), item.get("budget", {})) for item in roles if isinstance(item, Mapping)]
    support = document.get("support_role_budgets", {})
    if isinstance(support, Mapping): role_records.extend(support.items())
    for role, role_budget in role_records:
        if not isinstance(role, str) or not isinstance(role_budget, Mapping):
            raise ValueError("role budget identity is invalid")
        unknown = set(role_budget) - ROLE_BUDGET_FIELDS
        if unknown:
            raise ValueError(f"role {role!r} budget has unknown fields: {sorted(unknown)!r}")
        if "per_expert_budget_usd" in role_budget and role_budget["per_expert_budget_usd"] is None:
            raise ValueError("per_expert_budget_usd cannot be null")
        _usd_cap(role_budget.get("per_expert_budget_usd"), "per_expert_budget_usd")
        for name in ("per_expert_tokens", "max_experiments", "max_papers", "timeout", "max_turns"):
            value = role_budget.get(name)
            if name in role_budget and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"role {role!r} budget.{name} must be a positive integer")
        role_tokens = role_budget.get("per_expert_tokens", budget.get("per_expert_tokens"))
        role_turns = role_budget.get("max_turns", budget.get("max_turns"))
        if isinstance(role_tokens, int) and (role_tokens < invocation.max_tokens
                or isinstance(budget.get("run_tokens"), int) and role_tokens > budget["run_tokens"]):
            raise ValueError(f"role {role!r} token cap conflicts with the run/invocation cap")
        if isinstance(role_turns, int) and role_turns < invocation.max_turns:
            raise ValueError(f"role {role!r} turn cap is below one Agent invocation")
    return invocation


def remaining_agent_budget(
    document: Mapping[str, Any],
    role: str,
    charges: Iterable[AgentAttemptCharge],
    fallback: AgentBudget,
    *, run_scoped: bool = False,
) -> AgentBudget | None:
    """Derive one request ceiling from frozen profile caps and reservations."""

    base = profile_agent_budget(document, fallback); budget = document.get("budget")
    if not isinstance(budget, Mapping):
        raise ValueError("research contract budget must be an object")
    roles = document.get("roles")
    role_record = next((item for item in roles if isinstance(item, Mapping) and item.get("id") == role), None) if isinstance(roles, (list, tuple)) else None
    if type(run_scoped) is not bool: raise ValueError("run_scoped budget ownership must be explicit boolean")
    if role_record is None and not run_scoped:
        raise ValueError(f"Agent role {role!r} is absent from the frozen roster")
    role_budget = {} if run_scoped else role_record.get("budget", {})
    if not isinstance(role_budget, Mapping):
        raise ValueError("role budget must be an object")

    def cap(value: object, name: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    all_charges = tuple(charges); run_tokens, run_turns = charged_totals(all_charges)
    role_tokens, role_turns = charged_totals(all_charges, role)
    run_token_cap = cap(budget.get("run_tokens"), "run_tokens")
    run_turn_cap = cap(budget.get("max_turns"), "max_turns")
    role_token_cap = None if run_scoped else cap(role_budget.get("per_expert_tokens", budget.get("per_expert_tokens")), "per_expert_tokens")
    role_turn_cap = None if run_scoped else cap(role_budget.get("max_turns", budget.get("max_turns")), "role max_turns")
    token_limits = [base.max_tokens]
    turn_limits = [base.max_turns]
    if base.token_enforcement_mode == "enforce":
        if run_token_cap is not None: token_limits.append(run_token_cap - run_tokens)
        if role_token_cap is not None: token_limits.append(role_token_cap - role_tokens)
    if run_turn_cap is not None: turn_limits.append(run_turn_cap - run_turns)
    if role_turn_cap is not None: turn_limits.append(role_turn_cap - role_turns)
    max_tokens, max_turns = min(token_limits), min(turn_limits)
    if max_tokens <= 0 or max_turns <= 0:
        return None
    cost_limits = [] if base.max_cost is None else [Decimal(base.max_cost)]
    for configured, owner, field in (
        (budget.get("total_budget_usd"), None, "total_budget_usd"),
        (None if run_scoped else role_budget.get("per_expert_budget_usd", budget.get("per_expert_budget_usd")), role, "per_expert_budget_usd"),
    ):
        usd_limit = _usd_cap(configured, field)
        if usd_limit is not None:
            remaining = usd_limit - charged_cost(all_charges, owner)
            if remaining <= 0: return None
            cost_limits.append(remaining)
    return AgentBudget(max_turns=max_turns, max_tokens=max_tokens,
        max_output_bytes=base.max_output_bytes, wall_seconds=base.wall_seconds,
        max_cost=format(min(cost_limits).normalize(), "f") if cost_limits else None,
        token_enforcement_mode=base.token_enforcement_mode)


def role_agent_budget_state(
    document: Mapping[str, Any], role: str, charges: Iterable[AgentAttemptCharge], fallback: AgentBudget,
    *, wallclock_is_expired: bool = False,
) -> str:
    """Project whether a role can reserve one more Agent request."""

    selected = tuple(charges)
    closed = tuple(item for item in selected if item.state == "CLOSED")
    if wallclock_is_expired or remaining_agent_budget(document, role, closed, fallback) is None:
        return "exhausted"
    return "blocked" if remaining_agent_budget(document, role, selected, fallback) is None else "available"


def wallclock_remaining_seconds(document: Mapping[str, Any], created_at: str, *, now: datetime | None = None) -> float | None:
    budget = document.get("budget")
    hours = budget.get("wallclock_hours") if isinstance(budget, Mapping) else None
    if hours is None:
        return None
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < float(hours) < float("inf"):
        raise ValueError("wallclock_hours must be finite and positive")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("run created_at is invalid") from exc
    current = now or datetime.now(timezone.utc)
    return float(hours) * 3600 - (current - created).total_seconds()


def wallclock_expired(document: Mapping[str, Any], created_at: str, *, now: datetime | None = None) -> bool:
    remaining = wallclock_remaining_seconds(document, created_at, now=now)
    return remaining is not None and remaining <= 0


def usage_projection(
    rows: Iterable[Mapping[str, Any]],
    *,
    future_attempts: int,
    future_budget: Mapping[str, Any] | None,
    document: Mapping[str, Any] | None = None,
    charges: tuple[AgentAttemptCharge, ...] | None = None,
) -> dict[str, Any]:
    """Render status from the same charges used by dispatch admission."""

    charges = fold_agent_attempts(rows) if charges is None else charges
    actual = tuple(
        item for item in charges
        if item.source in {
            "response", "provider", "rejected_response", "discarded_response",
        }
    )
    declared = tuple(item for item in charges if item.source == "declared")
    open_declared = tuple(item for item in declared if item.state != "CLOSED")
    closed_unknown = tuple(item for item in declared if item.state == "CLOSED")
    budgets = [item.budget for item in charges]
    token_enforcement_modes: set[str] = set()
    invocation = None
    if document is not None:
        profile_budget = document.get("budget")
        if isinstance(profile_budget, Mapping):
            invocation = profile_budget.get("agent_invocation")
    for budget in (*budgets, invocation, future_budget):
        if not isinstance(budget, Mapping):
            continue
        mode = budget.get("token_enforcement_mode", "enforce")
        if isinstance(mode, str) and mode in {"enforce", "observe"}:
            token_enforcement_modes.add(str(mode))
    observed_overages = []
    for item in actual:
        limit = _integer(item.budget.get("max_tokens")) if isinstance(item.budget, Mapping) else None
        if limit is not None and item.tokens is not None and item.tokens > limit:
            observed_overages.append(item.tokens - limit)

    def limits(items: Iterable[Mapping[str, Any] | None], future: int = 0) -> dict[str, Any]:
        values = list(items)
        if any(item is None for item in values) or (future and future_budget is None):
            return {"attempts": len(values) + future, "tokens": None, "turns": None, "cost": None, "currency": "USD"}
        try:
            token_limit = sum(int(item["max_tokens"]) for item in values if item is not None)
            turn_limit = sum(int(item["max_turns"]) for item in values if item is not None)
            costs = [_cost(item["max_cost"]) for item in values if item is not None]
            if future:
                assert future_budget is not None
                token_limit += future * int(future_budget["max_tokens"])
                turn_limit += future * int(future_budget["max_turns"])
                next_cost = _cost(future_budget["max_cost"])
                costs.append(None if next_cost is None else future * next_cost)
            cost_limit = None if any(cost is None for cost in costs) else sum(costs, Decimal("0"))
            return {"attempts": len(values) + future, "tokens": token_limit, "turns": turn_limit,
                    "cost": None if cost_limit is None else str(cost_limit), "currency": "USD"}
        except (KeyError, TypeError, ValueError):
            return {"attempts": len(values) + future, "tokens": None, "turns": None, "cost": None, "currency": "USD"}

    result = {
        "observed_lower_bound": {
            "responses": sum(item.source == "response" for item in charges),
            "provider_error_reports": sum(item.source == "provider" for item in charges),
            "rejected_responses": sum(
                item.source == "rejected_response" for item in charges
            ),
            "discarded_responses": sum(
                item.source == "discarded_response" for item in charges
            ),
            "tokens": sum(item.tokens or 0 for item in actual),
            "turns": sum(item.turns or 0 for item in actual),
            "cost": str(sum((item.cost or Decimal("0") for item in actual), Decimal("0"))),
            "currency": "USD",
        },
        "unobserved_closed_attempts": sum(item.state == "CLOSED" for item in declared),
        "open_attempts": sum(item.state != "CLOSED" for item in declared),
        "budget_rejected_attempts": sum(item.source == "rejected" for item in charges),
        "budget_unknown_attempts": sum(item is None for item in budgets),
        "open_declared_limits": limits(item.budget for item in open_declared),
        "closed_unknown_declared_limits": limits(item.budget for item in closed_unknown),
        "unobserved_declared_limits": limits(item.budget for item in declared),
        "declared_limits": limits(budgets, future_attempts),
        "remaining_declared_limits": limits((), future_attempts),
        "token_enforcement": {
            "modes": sorted(token_enforcement_modes),
            "observed_overage_attempts": len(observed_overages),
            "overage_tokens": sum(observed_overages),
        },
    }
    try:
        if document is None:
            raise ValueError("no profile contract")
        budget = document["budget"]
        if not isinstance(budget, Mapping):
            raise ValueError("invalid profile budget")
        observe_tokens = (
            isinstance(invocation, Mapping)
            and invocation.get("token_enforcement_mode", "enforce") == "observe"
        )
        spent_tokens, spent_turns = charged_totals(charges)
        run_limits = {"tokens": budget.get("run_tokens"), "turns": budget.get("max_turns")}
        roles = {}
        for item in document["roles"]:
            role = str(item["id"]); role_tokens, role_turns = charged_totals(charges, role)
            roles[role] = {
                "tokens": {"limit": profile_role_cap(document, role, "per_expert_tokens"), "charged_or_reserved": role_tokens},
                "turns": {"limit": profile_role_cap(document, role, "max_turns"), "charged_or_reserved": role_turns},
            }
        for values in roles.values():
            for name, value in values.items():
                value["remaining"] = None if value["limit"] is None else max(0, value["limit"] - value["charged_or_reserved"])
                if name == "tokens" and value["limit"] is not None and value["charged_or_reserved"] > value["limit"]:
                    value["overage"] = value["charged_or_reserved"] - value["limit"]
        cumulative = {"tokens": {"limit": run_limits["tokens"], "charged_or_reserved": spent_tokens},
                      "turns": {"limit": run_limits["turns"], "charged_or_reserved": spent_turns}, "roles": roles}
        for name in ("tokens", "turns"):
            values = cumulative[name]; values["remaining"] = None if values["limit"] is None else max(0, values["limit"] - values["charged_or_reserved"])
            if name == "tokens" and values["limit"] is not None and values["charged_or_reserved"] > values["limit"]:
                values["overage"] = values["charged_or_reserved"] - values["limit"]
            if name == "tokens" and observe_tokens:
                continue
            current = result["remaining_declared_limits"][name]
            role_remaining = [item[name]["remaining"] for item in roles.values()]
            bounds = [current, values["remaining"], None if any(item is None for item in role_remaining) else sum(role_remaining)]
            if current is not None:
                remaining = min(item for item in bounds if item is not None)
                result["remaining_declared_limits"][name] = remaining
                result["declared_limits"][name] = values["charged_or_reserved"] + remaining
        result["profile_cumulative"] = cumulative
        def usd_projection(limit, owner=None):
            ceiling = _usd_cap(limit, "cumulative USD")
            charged = charged_cost(charges, owner)
            return {"limit": None if ceiling is None else format(ceiling.normalize(), "f"),
                    "charged_or_reserved": format(charged.normalize(), "f"), "currency": "USD",
                    "remaining": None if ceiling is None else format(max(Decimal("0"), ceiling - charged).normalize(), "f")}
        cumulative["cost"] = usd_projection(budget.get("total_budget_usd"))
        for item in document["roles"]:
            roles[str(item["id"])]["cost"] = usd_projection(item.get("budget", {}).get("per_expert_budget_usd", budget.get("per_expert_budget_usd")), str(item["id"]))
    except (KeyError, TypeError, ValueError):
        result["profile_cumulative"] = None
    return result


__all__ = ["AGENT_INVOCATION_BUDGET_FIELDS", "PROFILE_BUDGET_FIELDS",
           "ROLE_BUDGET_FIELDS", "AgentAttemptCharge", "charged_totals", "charged_cost", "fold_agent_attempts", "profile_effect_capacity",
           "agent_attempt_limits", "agent_attempts_exhausted", "effect_attempt_cap",
           "profile_agent_budget", "profile_role_cap", "remaining_agent_budget", "role_agent_budget_state",
           "usage_projection", "validate_new_run_budget", "wallclock_expired",
           "wallclock_remaining_seconds"]
