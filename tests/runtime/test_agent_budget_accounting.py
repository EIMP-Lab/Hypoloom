from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from hypoloom.agent_aci import AgentBudget
from hypoloom.agent_budget import (
    fold_agent_attempts,
    remaining_agent_budget,
    usage_projection,
    validate_new_run_budget,
    wallclock_expired,
)


def _row(
    attempt_id: str,
    *,
    role: str,
    state: str = "CLOSED",
    budget: tuple[int, int, str] = (100, 3, "0.50"),
    token_mode: str | None = None,
    terminal: dict[str, object] | None = None,
) -> dict[str, object]:
    tokens, turns, cost = budget
    return {
        "attempt_id": attempt_id,
        "kind": "AGENT",
        "state": state,
        "observation_json": json.dumps(
            {
                **(terminal or {}),
                "request": {
                    "role": role,
                    "budget": {
                        "max_tokens": tokens,
                        "max_turns": turns,
                        "max_cost": cost,
                        **({"token_enforcement_mode": token_mode}
                           if token_mode is not None else {}),
                    },
                },
            }
        ),
    }


def _dollar_budget():
    return AgentBudget(max_turns=3, max_tokens=100, max_output_bytes=4096, wall_seconds=30, max_cost="1")


def test_compact_charge_cache_tracks_closure_and_does_not_cache_rolled_back_closes():
    from hypoloom.agent_budget import AgentChargeCache
    rows = [_row("closed", role="ml"), _row("open", role="critic", state="OPEN")]
    reads = []

    class Reader:
        def list_attempts(self, **filters):
            assert filters == {"run_id": "run", "kind": "AGENT", "include_observation": False}
            return [{key: value for key, value in row.items() if key != "observation_json"} for row in rows]

        def get_attempt(self, identity):
            reads.append(identity)
            return next(row for row in rows if row["attempt_id"] == identity)

    cache, reader = AgentChargeCache(), Reader()
    assert cache.read(reader, "run", committed=True) == fold_agent_attempts(rows)
    assert cache.read(reader, "run", committed=True) == fold_agent_attempts(rows)
    assert reads.count("closed") == 1 and reads.count("open") == 2
    rows[1]["state"] = "CLOSED"
    assert cache.read(reader, "run") == fold_agent_attempts(rows)
    rows[1]["state"] = "OPEN"  # Roll back the transaction that attempted closure.
    assert cache.read(reader, "run", committed=True) == fold_agent_attempts(rows)
    rows[1]["state"] = "CLOSED"
    assert cache.read(reader, "run", committed=True) == fold_agent_attempts(rows)
    reads.clear()
    assert cache.read(reader, "run", committed=True) == fold_agent_attempts(rows)
    assert reads == []


@pytest.mark.parametrize("initial", [1024, 8192])
def test_initial_vram_budget_is_separate_from_operator_minimum(initial):
    document = {"roles": [], "budget": {"agent_invocation": _dollar_budget().to_dict(),
        "min_vram_per_experiment": 1024, "initial_vram_per_experiment": initial}}
    assert validate_new_run_budget(document, _dollar_budget()) == _dollar_budget()


@pytest.mark.parametrize("minimum,initial", [
    (1024, None), (1024, True), (1024, 1023), (1024, 1.5),
    (1024, 2**53), (0, 8192),
])
def test_invalid_initial_vram_configuration_is_rejected(minimum, initial):
    document = {"roles": [], "budget": {"agent_invocation": _dollar_budget().to_dict(),
        "min_vram_per_experiment": minimum, "initial_vram_per_experiment": initial}}
    with pytest.raises(ValueError, match="initial_vram_per_experiment"):
        validate_new_run_budget(document, _dollar_budget())


def test_unknown_observed_tokens_raise_reservation_without_settling_cost():
    row = _row("timeout", role="ml", terminal={"diagnostics": {
        "provider_observed_usage": {"total_tokens": 150}}})
    charge, = fold_agent_attempts([row])
    assert charge.source == "declared" and charge.tokens == 150 and str(charge.cost) == "0.50"
    value = json.loads(row["observation_json"])
    value["response"] = {"token_usage": {"total_tokens": 160}, "turn_count": 1, "cost": "0.1"}
    row["observation_json"] = json.dumps(value)
    charge, = fold_agent_attempts([row])
    assert charge.source == "response" and charge.tokens == 160 and str(charge.cost) == "0.1"


@pytest.mark.parametrize("code,expected", [("input_too_long", None),
    ("token_budget_exceeded", None), ("rate_limited", "INFRASTRUCTURE"),
    ("auth_failed", "INFRASTRUCTURE")])
def test_transport_failures_do_not_blindly_replay_identical_context(code, expected):
    from hypoloom.agent_context import agent_retry_class
    assert agent_retry_class("nonzero_exit", {"diagnostics": {"provider_failure": {"code": code}}},
        retryable=True) == expected
    assert agent_retry_class("nonzero_exit", {"diagnostics": {"provider_subtype": "error_max_budget_usd",
        "provider_failure": {"code": code}}}, retryable=True) is None


def _dollar_document():
    return {"budget": {"agent_invocation": _dollar_budget().to_dict(),
                       "total_budget_usd": "1.2", "per_expert_budget_usd": "0.8"},
            "roles": [{"id": "ml", "budget": {}}, {"id": "critic", "budget": {}}]}


def test_unbounded_invocation_keeps_global_and_role_cost_caps():
    document = _dollar_document()
    document["budget"]["agent_invocation"]["max_cost"] = None
    remaining = remaining_agent_budget(document, "ml", (), _dollar_budget())
    assert remaining.max_cost == "0.8"
    document["budget"].pop("total_budget_usd")
    document["budget"].pop("per_expert_budget_usd")
    remaining = remaining_agent_budget(document, "ml", (), _dollar_budget())
    assert remaining.max_cost is None
    assert AgentBudget.from_dict(remaining.to_dict()) == remaining


def test_unbounded_unknown_cost_does_not_erase_known_turn_and_token_limits():
    document = _dollar_document()
    document["budget"] = {"agent_invocation": {**_dollar_budget().to_dict(), "max_cost": None}}
    rows = [_row("unknown", role="ml", budget=(100, 3, None))]
    charges = fold_agent_attempts(rows)
    assert charges[0].cost is None
    projection = usage_projection(rows, document=document, future_attempts=0, future_budget=None)
    assert projection["closed_unknown_declared_limits"]["cost"] is None
    assert projection["closed_unknown_declared_limits"]["tokens"] == 100
    assert projection["closed_unknown_declared_limits"]["turns"] == 3


def test_cumulative_dollars_reserve_open_and_lost_and_clamp_next_request():
    document = _dollar_document()
    rows = [_row("lost", role="ml", budget=(1, 1, "0.5")),
            _row("open", role="critic", state="OPEN", budget=(1, 1, "0.6"))]
    remaining = remaining_agent_budget(document, "ml", fold_agent_attempts(rows), _dollar_budget())
    assert remaining is not None and remaining.max_cost == "0.1"
    rows.append(_row("reserved", role="ml", state="OPEN", budget=(1, 1, "0.1")))
    assert remaining_agent_budget(document, "ml", fold_agent_attempts(rows), _dollar_budget()) is None


def test_run_scoped_manager_is_charged_to_run_not_a_same_named_expert():
    document = _dollar_document()
    document["roles"].append({"id": "grounding", "budget": {"per_expert_budget_usd": "0.1"}})
    row = _row("manager", role="grounding", budget=(40, 1, "0.6"), state="OPEN")
    observation = json.loads(row["observation_json"])
    observation["request"].update(schema_version=14, command="GRAPH_EVOLVE", component_id="grounding",
        task_context={"kind": "AUXILIARY", "request_kind": "GRAPH_EVOLVE", "expert_slot": None,
            "component_id": None, "cycle": 1})
    row["observation_json"] = json.dumps(observation)
    charges = fold_agent_attempts([row])
    manager = remaining_agent_budget(document, "grounding", charges, _dollar_budget(), run_scoped=True)
    assert manager.max_cost == "0.6"
    expert = remaining_agent_budget(document, "grounding", charges, _dollar_budget())
    assert expert.max_cost == "0.1"
    from hypoloom.agent_budget import charged_cost, charged_totals
    assert str(charged_cost(charges)) == "0.6" and charged_totals(charges) == (40, 1)
    assert charged_cost(charges, "grounding") == 0 and charged_totals(charges, "grounding") == (0, 0)


def test_run_scoped_budget_requires_explicit_host_flag_and_retains_global_caps():
    document = _dollar_document()
    with pytest.raises(ValueError, match="absent"):
        remaining_agent_budget(document, "grounding", (), _dollar_budget())
    request = remaining_agent_budget(document, "grounding", (), _dollar_budget(), run_scoped=True)
    assert request.max_cost == "1"
    document["budget"]["run_tokens"] = 110
    rows = fold_agent_attempts([_row("peer", role="ml", budget=(100, 1, "1.1"))])
    request = remaining_agent_budget(document, "grounding", rows, _dollar_budget(), run_scoped=True)
    assert request.max_cost == "0.1" and request.max_tokens == 10
    document["budget"]["run_tokens"] = 100
    assert remaining_agent_budget(document, "grounding", rows, _dollar_budget(), run_scoped=True) is None


def test_cumulative_dollars_use_actual_and_role_override_even_in_observe_mode():
    document = _dollar_document()
    document["budget"]["agent_invocation"]["token_enforcement_mode"] = "observe"
    document["roles"][0]["budget"]["per_expert_budget_usd"] = "0.25"
    rows = [_row("closed", role="ml", terminal={"response": {"token_usage": {"total_tokens": 10000}, "turn_count": 1, "cost": "0.2"}})]
    remaining = remaining_agent_budget(document, "ml", fold_agent_attempts(rows), _dollar_budget())
    assert remaining is not None and remaining.max_cost == "0.05"
    projection = usage_projection(rows, future_attempts=2, future_budget=document["budget"]["agent_invocation"], document=document)
    assert projection["profile_cumulative"]["cost"]["remaining"] == "1"
    assert projection["profile_cumulative"]["roles"]["ml"]["cost"]["remaining"] == "0.05"


@pytest.mark.parametrize("value", [True, 0, -1, "NaN", "Infinity", "bad"])
def test_invalid_cumulative_dollar_caps_fail_closed(value):
    document = _dollar_document()
    document["budget"]["total_budget_usd"] = value
    with pytest.raises(ValueError, match="total_budget_usd"):
        validate_new_run_budget(document, _dollar_budget())


def test_explicit_effect_capacities_do_not_repurpose_hypothesis_limit():
    from hypoloom.agent_budget import profile_effect_capacity
    document = _dollar_document()
    document["budget"].update(max_active_hypotheses=7, max_active_agents=4,
                              max_active_processes=2, max_active_reviews=1)
    assert [profile_effect_capacity(document, kind) for kind in ("AGENT", "PROCESS", "REVIEW")] == [4, 2, 1]
    document["budget"].pop("max_active_agents")
    assert profile_effect_capacity(document, "AGENT") == 7


def test_fold_charges_actual_usage_and_reserves_unknown_attempts() -> None:
    charges = fold_agent_attempts(
        (
            _row(
                "success",
                role="ml",
                terminal={
                    "response": {
                        "token_usage": {"total_tokens": 40},
                        "turn_count": 2,
                        "cost": "0.20",
                    }
                },
            ),
            _row(
                "provider-error",
                role="ml",
                terminal={
                    "diagnostics": {
                        "provider_accounting": {
                            "token_usage": {"total_tokens": 70},
                            "turn_count": 3,
                            "cost": "0.31",
                            "cost_currency": "USD",
                        }
                    }
                },
            ),
            _row(
                "host-rejected-candidate",
                role="ml",
                terminal={
                    "rejected_response": {
                        "token_usage": {"total_tokens": 55},
                        "turn_count": 2,
                        "cost": "0.22",
                    }
                },
            ),
            _row("open", role="critic", state="OPEN"),
            _row("unknown-closed", role="critic"),
            _row(
                "local-rejection",
                role="critic",
                terminal={
                    "failure_code": "profile_budget_exhausted",
                },
            ),
        )
    )

    assert [(item.attempt_id, item.source, item.tokens, item.turns) for item in charges] == [
        ("success", "response", 40, 2),
        ("provider-error", "provider", 70, 3),
        ("host-rejected-candidate", "rejected_response", 55, 2),
        ("open", "declared", 100, 3),
        ("unknown-closed", "declared", 100, 3),
        ("local-rejection", "rejected", 0, 0),
    ]
    assert sum(item.tokens or 0 for item in charges) == 365


def test_projection_separates_open_closed_unknown_and_local_rejection() -> None:
    rows = (
        _row("open", role="ml", state="OPEN", budget=(100, 3, "0.50")),
        _row("closed-unknown", role="ml", budget=(80, 2, "0.25")),
        _row("local-rejection", role="ml", budget=(60, 1, "0.10"), terminal={
            "failure_code": "profile_budget_exhausted",
        }),
    )

    projection = usage_projection(rows, future_attempts=0, future_budget=None)

    assert projection["open_attempts"] == 1
    assert projection["unobserved_closed_attempts"] == 1
    assert projection["budget_rejected_attempts"] == 1
    assert projection["open_declared_limits"] == {
        "attempts": 1,
        "tokens": 100,
        "turns": 3,
        "cost": "0.50",
        "currency": "USD",
    }
    assert projection["closed_unknown_declared_limits"] == {
        "attempts": 1,
        "tokens": 80,
        "turns": 2,
        "cost": "0.25",
        "currency": "USD",
    }


def test_closed_unknown_does_not_exhaust_an_absent_cumulative_turn_cap() -> None:
    document = {
        "budget": {
            "agent_invocation": {
                "max_turns": 3,
                "max_tokens": 100,
                "max_output_bytes": 4096,
                "wall_seconds": 30,
                "max_cost": "0.25",
                "token_enforcement_mode": "observe",
            },
        },
        "roles": [{"id": "ml", "budget": {}}],
    }
    charges = fold_agent_attempts((_row("closed-unknown", role="ml"),))

    remaining = remaining_agent_budget(
        document,
        "ml",
        charges,
        AgentBudget(
            max_turns=9,
            max_tokens=999,
            max_output_bytes=9999,
            wall_seconds=99,
            max_cost="9.99",
        ),
    )

    assert remaining is not None
    assert remaining.max_turns == 3


def test_enforced_turn_cap_charges_closed_unknown_and_open_reservation() -> None:
    document = {
        "budget": {
            "agent_invocation": {
                "max_turns": 3,
                "max_tokens": 100,
                "max_output_bytes": 4096,
                "wall_seconds": 30,
                "max_cost": "0.25",
                "token_enforcement_mode": "enforce",
            },
            "run_tokens": 1_000,
            "per_expert_tokens": 1_000,
            "max_turns": 6,
        },
        "roles": [{"id": "ml", "budget": {"max_turns": 6}}],
    }
    fallback = AgentBudget(
        max_turns=9,
        max_tokens=999,
        max_output_bytes=9999,
        wall_seconds=99,
        max_cost="9.99",
    )
    closed_unknown = _row("closed-unknown", role="ml")
    open_reservation = _row("open", role="ml", state="OPEN")

    after_closed = remaining_agent_budget(
        document, "ml", fold_agent_attempts((closed_unknown,)), fallback,
    )
    after_both = remaining_agent_budget(
        document,
        "ml",
        fold_agent_attempts((closed_unknown, open_reservation)),
        fallback,
    )

    assert after_closed is not None
    assert after_closed.max_turns == 3
    assert after_both is None


def test_remaining_budget_intersects_invocation_run_and_role_caps() -> None:
    document = {
        "budget": {
            "agent_invocation": {
                "max_turns": 3,
                "max_tokens": 100,
                "max_output_bytes": 4096,
                "wall_seconds": 30,
                "max_cost": "0.25",
                "token_enforcement_mode": "enforce",
            },
            "run_tokens": 250,
            "per_expert_tokens": 180,
            "max_turns": 8,
        },
        "roles": [
            {"id": "ml", "budget": {"per_expert_tokens": 150, "max_turns": 5}},
            {"id": "critic", "budget": {}},
        ],
    }
    fallback = AgentBudget(
        max_turns=9,
        max_tokens=999,
        max_output_bytes=9999,
        wall_seconds=99,
        max_cost="9.99",
    )
    charges = fold_agent_attempts(
        (
            _row("ml-used", role="ml", terminal={"response": {
                "token_usage": {"total_tokens": 120}, "turn_count": 4, "cost": "0.20"}}),
            _row("critic-open", role="critic", state="OPEN", budget=(80, 2, "0.25")),
        )
    )

    remaining = remaining_agent_budget(document, "ml", charges, fallback)
    assert remaining is not None
    assert remaining.to_dict() == {
        "max_turns": 1,
        "max_tokens": 30,
        "max_output_bytes": 4096,
        "wall_seconds": 30,
        "max_cost": "0.25",
        "token_enforcement_mode": "enforce",
    }


def test_usage_projection_reports_modes_and_actual_token_overages() -> None:
    rows = (
        _row(
            "observed-response-overage",
            role="ml",
            terminal={
                "response": {
                    "token_usage": {"total_tokens": 130},
                    "turn_count": 2,
                    "cost": "0.20",
                }
            },
        ),
        _row(
            "enforced-provider-overage",
            role="ml",
            budget=(80, 3, "0.50"),
            terminal={
                "diagnostics": {
                    "provider_accounting": {
                        "token_usage": {"total_tokens": 95},
                        "turn_count": 2,
                        "cost": "0.30",
                        "cost_currency": "USD",
                    }
                }
            },
        ),
    )
    first = json.loads(rows[0]["observation_json"])
    first["request"]["budget"]["token_enforcement_mode"] = "observe"
    rows[0]["observation_json"] = json.dumps(first)
    document = {
        "budget": {
            "agent_invocation": {
                "max_tokens": 100,
                "token_enforcement_mode": "observe",
            },
        },
        "roles": [],
    }

    projection = usage_projection(
        rows,
        future_attempts=0,
        future_budget=None,
        document=document,
    )

    assert projection["observed_lower_bound"]["tokens"] == 225
    assert projection["token_enforcement"] == {
        "modes": ["enforce", "observe"],
        "observed_overage_attempts": 2,
        "overage_tokens": 45,
    }


def test_usage_projection_counts_rejected_and_discarded_response_usage() -> None:
    rows = (
        _row("rejected", role="ml", terminal={"rejected_response": {
            "token_usage": {"total_tokens": 42},
            "turn_count": 2,
            "cost": "0.12",
        }}),
        _row("discarded", role="ml", terminal={"discarded_response": {
            "token_usage": {"total_tokens": 18},
            "turn_count": 1,
            "cost": "0.03",
        }}),
    )

    observed = usage_projection(
        rows, future_attempts=0, future_budget=None,
    )["observed_lower_bound"]

    assert observed == {
        "responses": 0,
        "provider_error_reports": 0,
        "rejected_responses": 1,
        "discarded_responses": 1,
        "tokens": 60,
        "turns": 3,
        "cost": "0.15",
        "currency": "USD",
    }


def test_observe_mode_records_actual_overage_without_token_dispatch_stop() -> None:
    document = {
        "budget": {
            "agent_invocation": {
                "max_turns": 3,
                "max_tokens": 100,
                "max_output_bytes": 4096,
                "wall_seconds": 30,
                "max_cost": "0.25",
                "token_enforcement_mode": "observe",
            },
            "run_tokens": 150,
            "per_expert_tokens": 150,
            "max_turns": 8,
        },
        "roles": [
            {"id": "ml", "budget": {"per_expert_tokens": 150, "max_turns": 8}},
        ],
    }
    rows = (
        _row(
            "observe-overage",
            role="ml",
            token_mode="observe",
            terminal={"response": {
                "token_usage": {"total_tokens": 151},
                "turn_count": 2,
                "cost": "0.20",
            }},
        ),
    )
    charges = fold_agent_attempts(rows)

    assert charges[0].source == "response"
    assert charges[0].tokens == 151
    remaining = remaining_agent_budget(
        document,
        "ml",
        charges,
        AgentBudget(
            max_turns=9,
            max_tokens=999,
            max_output_bytes=9999,
            wall_seconds=99,
            max_cost="9.99",
        ),
    )
    assert remaining is not None
    assert remaining.max_tokens == 100
    assert remaining.token_enforcement_mode == "observe"

    projection = usage_projection(
        rows,
        future_attempts=1,
        future_budget=remaining.to_dict(),
        document=document,
    )
    assert projection["profile_cumulative"]["tokens"] == {
        "limit": 150,
        "charged_or_reserved": 151,
        "remaining": 0,
        "overage": 1,
    }
    assert projection["profile_cumulative"]["roles"]["ml"]["tokens"] == {
        "limit": 150,
        "charged_or_reserved": 151,
        "remaining": 0,
        "overage": 1,
    }
    assert projection["remaining_declared_limits"]["tokens"] == 100

    turn_exhausted = json.loads(json.dumps(document))
    turn_exhausted["budget"]["max_turns"] = 2
    turn_exhausted["roles"][0]["budget"]["max_turns"] = 2
    assert remaining_agent_budget(
        turn_exhausted,
        "ml",
        charges,
        AgentBudget(
            max_turns=9,
            max_tokens=999,
            max_output_bytes=9999,
            wall_seconds=99,
            max_cost="9.99",
        ),
    ) is None


def test_wallclock_budget_is_measured_from_durable_run_creation() -> None:
    document = {"budget": {"wallclock_hours": 2}}
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    assert not wallclock_expired(document, "2026-08-27T10:00:00.001Z", now=now)
    assert wallclock_expired(document, "2026-08-27T09:59:59.999Z", now=now)


def test_new_run_requires_explicit_frozen_token_enforcement_mode() -> None:
    fallback = AgentBudget(
        max_turns=1,
        max_tokens=100,
        max_output_bytes=4096,
        wall_seconds=30,
        max_cost="0.25",
    )
    document = {
        "budget": {"agent_invocation": {
            "max_turns": 1,
            "max_tokens": 100,
            "max_output_bytes": 4096,
            "wall_seconds": 30,
            "max_cost": "0.25",
        }},
        "roles": [],
    }

    with pytest.raises(
        ValueError,
        match="budget.agent_invocation.token_enforcement_mode",
    ):
        validate_new_run_budget(document, fallback)


@pytest.mark.parametrize(
    "field",
    ("max_retries", "max_infrastructure_retries", "max_process_lost_retries"),
)
def test_new_run_retry_budgets_are_optional_nonnegative_integers(
    field: str,
) -> None:
    fallback = AgentBudget(
        max_turns=1,
        max_tokens=100,
        max_output_bytes=4096,
        wall_seconds=30,
        max_cost="0.25",
    )
    document = {
        "budget": {
            "agent_invocation": {
                **fallback.to_dict(),
                "token_enforcement_mode": "enforce",
            },
            field: 0,
        },
        "roles": [],
    }

    assert validate_new_run_budget(document, fallback) == fallback

    document["budget"][field] = -1
    with pytest.raises(ValueError, match=rf"budget\.{field}"):
        validate_new_run_budget(document, fallback)

    document["budget"][field] = True
    with pytest.raises(ValueError, match=rf"budget\.{field}"):
        validate_new_run_budget(document, fallback)


@pytest.mark.parametrize("value", [0, 18000, 18000.5, 604800])
def test_provider_auth_cooldown_budget_accepts_bounded_numbers(value) -> None:
    fallback = AgentBudget(max_turns=1, max_tokens=100, max_output_bytes=4096, wall_seconds=30, max_cost="0.25")
    document = {"budget": {"agent_invocation": {**fallback.to_dict(), "token_enforcement_mode": "enforce"},
        "provider_auth_cooldown_seconds": value}, "roles": []}
    assert validate_new_run_budget(document, fallback) == fallback


@pytest.mark.parametrize("value", [-1, True, "18000", 604801, float("inf"), float("nan")])
def test_provider_auth_cooldown_budget_rejects_unbounded_values(value) -> None:
    fallback = AgentBudget(max_turns=1, max_tokens=100, max_output_bytes=4096, wall_seconds=30, max_cost="0.25")
    document = {"budget": {"agent_invocation": {**fallback.to_dict(), "token_enforcement_mode": "enforce"},
        "provider_auth_cooldown_seconds": value}, "roles": []}
    with pytest.raises(ValueError, match=r"budget\.provider_auth_cooldown_seconds"):
        validate_new_run_budget(document, fallback)
