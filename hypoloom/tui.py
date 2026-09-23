"""Textual monitor with explicitly confirmed operator actions."""

from __future__ import annotations

import base64
import math
import os
import shlex
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Select, Static, Tree

from .monitor import ComponentView, MonitorSnapshot, build_observer_snapshot
from .observer_refresh import ObserverRefresh
from .web_observer import DEFAULT_PORT, WebObserverServer, create_server


_EXPERT_COLORS = (
    "#ff79c6",
    "#6ea8fe",
    "#f5c36b",
    "#63f2b0",
    "#ff7488",
    "#63d7f2",
    "#e2f5ee",
)

_RUN_STATE_LABELS = {
    "CREATED": "Run created",
    "ACTIVE": "Research workflow active",
    "WAITING_HUMAN": "Operator decision required",
    "COMPLETING": "Completion evaluation",
    "STOPPING": "Stop / cleanup requested",
    "COMPLETED": "Lifecycle settled",
    "FAILED": "Run failed",
    "CANCELLED": "Run cancelled",
}
_COMPONENT_STAGE = {
    "PENDING": 1,
    "DRAFTED": 2,
    "PREREGISTERED": 3,
    "READY": 4,
    "EXECUTING": 5,
    "EVALUATING": 6,
    "TERMINAL": 7,
}
_PHASE_LABELS = {
    "proposal": "hypothesis proposal",
    "specification": "experiment specification",
    "validation": "candidate validation",
    "prepare": "execution preparation",
    "execute": "experiment execution",
    "analyze": "outcome assessment",
    "terminal": "terminal",
    "help": "peer help",
    "debate": "peer debate",
    "rendezvous": "peer rendezvous",
}
_ATTEMPT_BADGES = {
    "OPEN": ("#63d9ff", "●"),
    "SUCCEEDED": ("#70e1c1", "✓"),
    "FAILED": ("#ff6b7a", "!"),
    "TIMED_OUT": ("#ff6b7a", "⌛"),
    "CANCELLED": ("#ffb86b", "⊘"),
    "LOST": ("#ffb86b", "↻"),
}
_ASSESSMENT_BADGES = {
    "SUPPORTED": "#70e1c1",
    "REFUTED": "#ff6b7a",
    "GOAL_MET": "#70e1c1",
    "GOAL_NOT_MET": "#ff6b7a",
    "INCONCLUSIVE": "#ffb86b",
    "PENDING": "#63d9ff",
}
_RENDEZVOUS_BADGES = {
    "ORIGINATED": "#ffb86b",
    "OPEN": "#63d9ff",
    "RESOLVED": "#70e1c1",
}
_REVISION_WARNING_COLORS = {
    "general": "#ffb86b",
    "severe": "#ff6b7a",
    "tripped": "#ff6b7a",
}


class SnapshotRefreshCompleted(Message):
    """Return one read-only snapshot result to the Textual event loop."""

    def __init__(
        self,
        snapshot: MonitorSnapshot | None,
        error: Exception | None,
    ) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.error = error


_HELP_TEXT = """\
[b #78dcff]WINDOW GUIDE[/]  Help / ? close · arrows / PgUp / PgDn scroll
[b]Summary[/]: research activity, budget/token usage, execution reliability and run health.
[b]Expert lanes[/]: each expert's current stages, open work and cumulative metrics.
[b]Workflows[/]: next commands, expert interactions and resource waits.
[b]Hypothesis chains[/]: branches, assessments and pruning; Left/Right/Enter fold the tree.
[b]Diagnostics (d)[/]: components, Attempts, facts and Oracle findings. The expert filter changes lanes/tree only; run totals stay cumulative.

[b #78dcff]EXECUTION RELIABILITY[/]  execution quality, not scientific success or a model intelligence score
🎯 FP first output pass: accepted output on the first validation, without correction.
🔧 CR correction recovery: rejected output corrected within the same Agent call.
🔁 RT nodes needing retry: logical nodes that needed another Host Attempt; lower is better.
📦 DL node delivery: logical nodes that ultimately delivered an accepted result.
🧰 RA required actions: completion of required resource Skill loading and resource queries.
n/d (%) uses eligible records · ?n unknown · −n excluded · — no eligible denominator.

[b #78dcff]READING THE DISPLAY[/]
🔢 tok: reported ≥ lower bound; res = declared reserve, not spend · K/M/B/T = powers of 1,000.
Bars: admission usage (reported + reserves) / limit; ref = observe-only reference · ∞ unbounded · ? unavailable.
LIT literature → HYP hypothesis → DES design → RUN execution → ANA analysis → UPD update; REP report / JDG adjudicate / EVO evolve.
[b #63d9ff]● OPEN[/] is recorded work, not a heartbeat; amber OPEN means Host unconfirmed/absent. Stages show current work, not goal progress.
[b #70e1c1]✓ SUCCEEDED[/] effect closed · [b #ff6b7a]! FAILED / ⌛ TIMED_OUT[/] · [b #ffb86b]⊘ CANCELLED / ↻ LOST[/].
SUPPORTED/REFUTED/PENDING are scientific assessments; PRUNED is control. COMPLETED means lifecycle settled, not scientific success.
q leaves Host and tasks running; End run / x opens confirmation to cancel the run and finish cleanup.
"""


def _value(value: object) -> str:
    return escape("-" if value is None else str(value))


def _short_text(value: object, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()) or "-"
    if len(text) > limit:
        text = f"{text[: limit - 1]}…"
    return escape(text)


def _duration(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    seconds = max(0, int(value))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def _count(value: object) -> str:
    return "?" if value is None else _value(value)


def _compact(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return "?"
    amount = float(value)
    for unit in ("", "K", "M", "B", "T"):
        if amount < 999.995 or unit == "T":
            return f"{amount:.2f}".rstrip("0").rstrip(".") + unit
        amount /= 1000
    return "?"


def _limit_line(label: str, used: object, limit: object, *, known: bool = True) -> str:
    ceiling = _compact(limit) if limit is not None else "∞" if known else "?"
    value = f"{label} {_compact(used)}/{ceiling}"
    if (isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(used) and used >= 0
            and isinstance(limit, (int, float)) and not isinstance(limit, bool) and math.isfinite(limit) and limit > 0):
        ratio = used / limit
        filled = min(8, int(ratio * 8))
        color = "#ffb86b" if ratio > 1 else "#70e1c1"
        value += f" [{color}]{'━' * filled}[/][dim]{'╌' * (8 - filled)}[/] {ratio:.0%}"
    return value


_LANE_STAGES = (("LITERATURE_REVIEW", "LIT"), ("HYPOTHESIS_PROPOSAL", "HYP"),
    ("EXPERIMENT_DESIGN_IMPLEMENT", "DES"), ("EXPERIMENT_EXECUTION", "RUN"),
    ("RESULT_ANALYSIS", "ANA"), ("HYPOTHESIS_UPDATE", "UPD"),
    ("CYCLE_REPORT", "REP"), ("ADJUDICATE", "JDG"), ("SELF_EVOLVE", "EVO"))


def _stage_badge(stage, host: str) -> str:
    state = stage.get("terminal_reason") or stage.get("state") or "UNKNOWN"
    if state == "OPEN" and host != "responded":
        return "[b #ffb86b]● OPEN?[/]"
    return _attempt_badge(str(state))


def _lane_line(stages, host: str) -> str:
    chips = []
    for node, label in _LANE_STAGES:
        matches = [stage for stage in stages if stage.get("node") == node]
        if not matches:
            chips.append(f"[dim]{label}[/]")
            continue
        active = [stage for stage in matches if stage.get("state") == "OPEN"]
        state = "OPEN" if active else matches[-1].get("terminal_reason") or "CLOSED"
        color, icon = _ATTEMPT_BADGES.get(state, ("#c8d7e3", "·"))
        if state == "OPEN" and host != "responded":
            color = "#ffb86b"
        count = f"×{len(active)}" if len(active) > 1 else ""
        chips.append(f"[b {color}]{icon}{label}{count}[/]")
    return " ".join(chips)


def _reliability_lines(reliability: Mapping[str, object] | None, *, details: bool = False) -> list[str]:
    if not reliability:
        return ["[dim]🎯 FP ? · 🔧 CR ? · 🔁 RT ? · 📦 DL ? · 🧰 RA ?[/]"]
    metrics = reliability.get("metrics", {})
    labels = (("output_first_pass", "🎯 FP"), ("correction_recovery", "🔧 CR"),
              ("node_retry", "🔁 RT"), ("node_delivery", "📦 DL"), ("required_actions", "🧰 RA"))
    values = []
    for key, label in labels:
        metric = metrics.get(key, {})
        rate = metric.get("rate")
        percentage = f"{rate:.0%}" if isinstance(rate, (int, float)) else "—"
        coverage = "".join(f" {symbol}{_count(metric.get(key))}" for key, symbol in
            (("unknown", "?"), ("excluded", "−")) if metric.get(key) != 0)
        values.append(f"{label} {_count(metric.get('numerator'))}/{_count(metric.get('denominator'))} "
                      f"({percentage}){coverage}")
    lines = [" · ".join(values[:2]), " · ".join(values[2:])]
    if not details:
        return lines
    counts, costs = reliability.get("counts", {}), reliability.get("costs", {})
    return lines + [f"⌛ TO {_count(counts.get('timed_out_attempts'))} · ⊘ CX {_count(counts.get('cancelled_attempts'))} "
        f"· 🛠 TE {_count(counts.get('tool_errors'))}/{_count(counts.get('tool_calls'))} ?{_count(counts.get('trace_unknown_attempts'))}",
        f"🔁 Retry ≥{_compact(costs.get('retry_reported_tokens'))} tok · res {_compact(costs.get('retry_reserved_tokens'))} "
        f"· closed ?{_count(costs.get('retry_unknown_closed_attempts'))}",
        f"Retry nodes ?{_count(costs.get('retry_unknown_nodes'))} · tok ?{_count(costs.get('retry_unavailable_token_attempts'))} "
        f"· elapsed {_duration(costs.get('retry_elapsed_seconds'))} ?{_count(costs.get('retry_elapsed_unknown_attempts'))}"]


def _metric_lines(metrics: Mapping[str, object]) -> list[str]:
    experiments = metrics.get("experiments", {})
    literature = metrics.get("literature", {})
    skills = metrics.get("skills", {})
    evolution = metrics.get("evolutions", {})
    return [
        f"Experiments {_count(experiments.get('total'))} · hypotheses {_count(metrics.get('hypotheses'))} · "
        f"chains {_count(metrics.get('chains'))} / active {_count(metrics.get('active_chains'))}",
        f"Literature {_count(literature.get('retrieved'))} · PDF {_count(literature.get('pdf_available'))} "
        f"({_value(literature.get('coverage', 'unknown'))}) · Skills {_count(skills.get('count'))} "
        f"({_value(skills.get('coverage', 'unknown'))})",
        f"Self-evolution completed {_count(evolution.get('completed'))} · effective evolution {_count(evolution.get('changed'))} "
        f"· failed Attempts {_count(evolution.get('failed'))}",
    ]


def _interaction_line(name: str, metrics: Mapping[str, object]) -> str:
    return (f"{name}: started {_count(metrics.get('started'))} · active {_count(metrics.get('active'))} · "
        f"completed {_count(metrics.get('completed'))} · aborted {_count(metrics.get('aborted'))} · failed {_count(metrics.get('failed'))}")


def _goal_objective(snapshot: MonitorSnapshot) -> str:
    contract = snapshot.goal.get("research_contract")
    if not isinstance(contract, Mapping):
        return "-"
    goal = contract.get("goal")
    if not isinstance(goal, Mapping):
        return "-"
    return _short_text(goal.get("objective"), limit=84)


def _phase_label(phase: str) -> str:
    parts = phase.split("+")
    return " + ".join(_PHASE_LABELS.get(part, part.replace("_", " ")) for part in parts)


def _component_progress(state: str) -> str:
    stage = _COMPONENT_STAGE.get(state)
    if stage is None:
        return "[dim]······· ?/7[/dim]"
    filled = "●" * stage
    pending = "·" * (7 - stage)
    return f"[#70e1c1]{filled}[/][dim]{pending}[/] {stage}/7"


def _attempt_badge(value: str) -> str:
    color, icon = _ATTEMPT_BADGES.get(value, ("#c8d7e3", "·"))
    return f"[b {color}]{icon} {_value(value)}[/]"


def _assessment_badge(value: str | None) -> str:
    assessment = value or "PENDING"
    color = _ASSESSMENT_BADGES.get(assessment, "#c8d7e3")
    return f"[b {color}]{_value(assessment)}[/]"


def _rendezvous_badge(status: str) -> str:
    color = _RENDEZVOUS_BADGES.get(status, "#c8d7e3")
    return f"[b {color}]{_value(status)}[/]"


def _revision_warning_badge(component: ComponentView) -> str | None:
    if component.revision_warning is None:
        return None
    color = _REVISION_WARNING_COLORS.get(component.revision_warning, "#c8d7e3")
    revisions = (
        f"{component.cycle_revisions} revisions in cycle {component.current_cycle}"
    )
    if component.revision_warning == "general":
        text = f"⚠️ repeated revisions without convergence: {revisions}; cause unverified"
    elif component.revision_warning == "severe":
        text = (
            f"🛑 repeated revisions without convergence: {revisions}; cause unverified; "
            "this lineage self-terminates after 30 single-point failures"
        )
    else:
        text = (
            f"⛔ revision circuit breaker tripped: {revisions}; "
            "further revisions and cycles are suppressed"
        )
    return f"[b {color}]{escape(text)}[/]"


def _due_label(due_at: float | None, observed_at: float) -> str:
    if due_at is None:
        return "not scheduled"
    delta = due_at - observed_at
    if abs(delta) < 1:
        return "due now"
    if delta < 0:
        return f"overdue by {abs(delta):.0f}s"
    return f"due in {delta:.0f}s"


def _has_host_status(snapshot: MonitorSnapshot) -> bool:
    return snapshot.status_source.endswith("+resident-host-status")


def _agent_usage_lines(status: Mapping[str, object], accounting=None) -> tuple[str, ...]:
    # Keep the token icon compatible with tmux 3.0: it counts the newer coin
    # emoji as one cell while Rich reserves two, shifting the panel borders.
    accounting = accounting or {}
    extra = accounting.get("observed_unsettled_tokens", 0)
    observed_note = (f" · +≥{_compact(extra)} observed, unsettled" if extra else "")
    usage = status.get("agent_usage")
    if not isinstance(usage, Mapping):
        accounting = accounting or {}
        return (f"🔢 ≥{_compact(accounting.get('reported_tokens'))} tok{observed_note} · res {_compact(accounting.get('reserved_tokens'))}",
            f"Closed ?{_count(accounting.get('unknown_closed_attempts'))} · tok ?{_count(accounting.get('unavailable_token_attempts'))} "
            "· limits: Host STATUS unavailable")
    observed = usage.get("observed_lower_bound")
    observed = observed if isinstance(observed, Mapping) else {}
    cumulative = usage.get("profile_cumulative")
    has_cumulative = isinstance(cumulative, Mapping)
    cumulative = cumulative if has_cumulative else {}
    tokens = cumulative.get("tokens")
    tokens = tokens if isinstance(tokens, Mapping) else {}
    turns = cumulative.get("turns")
    turns = turns if isinstance(turns, Mapping) else {}
    enforcement = usage.get("token_enforcement")
    enforcement = enforcement if isinstance(enforcement, Mapping) else {}
    open_declared = usage.get("open_declared_limits")
    open_declared = open_declared if isinstance(open_declared, Mapping) else {}
    closed_unknown = usage.get("closed_unknown_declared_limits")
    closed_unknown = closed_unknown if isinstance(closed_unknown, Mapping) else {}
    raw_modes = enforcement.get("modes")
    modes = tuple(str(item) for item in raw_modes) if isinstance(raw_modes, list) else ()
    mode = "+".join(modes) if modes else "unknown"
    token_limit_label = "Tok ref" if modes == ("observe",) else "Tok limit"
    token_limit = tokens.get("limit")
    turn_limit = turns.get("limit")
    lines = [f"🔢 ≥{_compact(observed.get('tokens'))} tok{observed_note} · {_compact(observed.get('turns'))} turns",
        _limit_line(token_limit_label, tokens.get('charged_or_reserved'), token_limit, known='limit' in tokens),
        _limit_line("Turns", turns.get('charged_or_reserved'), turn_limit, known='limit' in turns),
        f"Res ≤{_compact(open_declared.get('tokens'))} tok ({_count(open_declared.get('attempts'))} open) "
        f"· closed ?{_count(usage.get('unobserved_closed_attempts'))} ≤{_compact(closed_unknown.get('tokens'))} tok"]
    # Limits above retain overage percentages. Only surface extra policy detail
    # when it has something to explain; the legend defines the admission bars.
    if enforcement.get('observed_overage_attempts') or usage.get('budget_rejected_attempts'):
        lines.append(f"{_value(mode)}: call overages {_count(enforcement.get('observed_overage_attempts'))} "
            f"(+{_compact(enforcement.get('overage_tokens'))} tok) · rejected {_count(usage.get('budget_rejected_attempts'))}")
    return tuple(lines)


def _role(snapshot: MonitorSnapshot, role: str) -> str:
    expert = next((item for item in snapshot.experts if item.role == role), None)
    if expert is None:
        return _value(role)
    color = _EXPERT_COLORS[(expert.slot_ordinal - 1) % len(_EXPERT_COLORS)]
    return f"[{color}]{_value(role)}[/]"


class CockpitPanel(VerticalScroll):
    """Focusable read-only panel with conventional keyboard scrolling."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        extra_children: tuple = (),
    ) -> None:
        self._panel_content = Static(classes="panel-content")
        self._rendered_content: str | None = None
        super().__init__(
            self._panel_content,
            *extra_children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )

    def update(self, content: str, *, renderable: RenderableType | None = None) -> None:
        if content == self._rendered_content:
            return
        self._rendered_content = content
        self._panel_content.update(content if renderable is None else renderable)


class FreshnessBanner(Static):
    """Persistent snapshot freshness and monitor-authority notice."""

    def show_snapshot(self, snapshot: MonitorSnapshot) -> None:
        age = max(0, int(time.time() - snapshot.observed_at))
        self.remove_class("stale")
        if _has_host_status(snapshot):
            source = "[b #70e1c1]READ-ONLY / HOST STATUS[/]"
            note = "Host answered this poll · not continuous liveness"
        else:
            source = "[b #70e1c1]READ-ONLY / OFFLINE[/]"
            note = "polling observation · not a receipt or Host-liveness signal"
        self.update(
            f"{source}  [b]SNAPSHOT[/] {age}s old  [dim]{note}[/dim]"
        )

    def show_failure(self, exc: Exception, *, has_snapshot: bool) -> None:
        retained = (
            "showing last successful snapshot"
            if has_snapshot
            else "no successful snapshot available"
        )
        self.add_class("stale")
        self.update(
            "[b #ffb86b]STALE · READ-ONLY[/]  "
            f"refresh failed: {_value(exc)}  "
            f"[dim]{retained}[/dim]"
        )


class HelpLegend(CockpitPanel):
    """Scrollable window guide and execution reliability legend."""


def _send_terminal_clipboard_request(
    app: App[object], text: str, *, inside_tmux: bool,
) -> str | None:
    """Send one explicit clipboard request without claiming delivery."""

    if inside_tmux:
        driver = getattr(app, "_driver", None)
        write = getattr(driver, "write", None)
        if not callable(write):
            return None
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        # tmux 3.0 with set-clipboard=external filters pane OSC 52. A DCS
        # wrapper carries this user-triggered request to the outer terminal
        # without changing the server-wide clipboard policy.
        write(f"\x1bPtmux;\x1b\x1b]52;c;{payload}\a\x1b\\")
        return "tmux"

    copy = getattr(app, "copy_to_clipboard", None)
    if not callable(copy):
        return None
    copy(text)
    return "terminal"


class WebAccessScreen(ModalScreen[None]):
    """Ephemeral Web URL and workstation tunnel instructions."""

    BINDINGS = (
        Binding("escape", "close", "Close"),
        Binding("q", "quit_app", "Leave monitor"),
    )
    CSS = """
    WebAccessScreen {
        align: center middle;
        background: rgba(1, 8, 14, 0.82);
    }

    #web-access-dialog {
        width: 94%;
        max-width: 104;
        height: 88%;
        max-height: 88%;
        padding: 1 2;
        border: round #63d9ff;
        background: #0a1924;
    }

    #web-access-scroll {
        height: 1fr;
        overflow-y: auto;
        scrollbar-color: #2f8198;
        scrollbar-background: #07131d;
    }

    #web-access-content {
        width: 1fr;
        height: auto;
        color: #d8e6ed;
    }

    #web-access-actions {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #web-access-actions Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        content: str,
        *,
        access_url: str | None,
        inside_tmux: bool,
    ) -> None:
        super().__init__()
        self._content = content
        self._access_url = access_url
        self._inside_tmux = inside_tmux

    def compose(self) -> ComposeResult:
        with Vertical(id="web-access-dialog"):
            with VerticalScroll(id="web-access-scroll"):
                yield Static(self._content, id="web-access-content")
            with Horizontal(id="web-access-actions"):
                if self._access_url is not None:
                    label = (
                        "Copy URL via tmux"
                        if self._inside_tmux
                        else "Copy browser URL"
                    )
                    yield Button(label, id="copy-web-url")
                yield Button("Close", id="close-web-access", variant="primary")

    def action_close(self) -> None:
        self.dismiss()

    def action_quit_app(self) -> None:
        self.app.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-web-access":
            self.dismiss()
            return
        if event.button.id == "copy-web-url" and self._access_url is not None:
            try:
                transport = _send_terminal_clipboard_request(
                    self.app,
                    self._access_url,
                    inside_tmux=self._inside_tmux,
                )
            except (OSError, RuntimeError):
                transport = None
            if transport == "tmux":
                self.app.notify(
                    "Clipboard request sent through tmux; paste to verify. "
                    "If empty, use Shift+drag and the SSH client's Copy action.",
                    timeout=10,
                )
            elif transport == "terminal":
                self.app.notify(
                    "Clipboard request sent to the terminal; paste to verify. "
                    "If empty, use terminal text selection.",
                    timeout=10,
                )
            else:
                self.app.notify(
                    "Clipboard request unavailable; use Shift+drag and the "
                    "SSH client's Copy action on the displayed URL.",
                    severity="warning",
                    timeout=10,
                )


def _oracle_observation_label(oracle):
    status = oracle.get('observation', {})
    if oracle.get('ok') is None:
        return '[b #f5c36b]' + ('AUDIT ERROR' if status.get('state') == 'error' else 'AUDIT PENDING') + '[/]'
    label = 'CLEAN' if oracle.get('ok') else 'ISSUES'
    if status:
        stamp = status.get('audited_at')
        label = 'LAST ' + label + (' · ' + time.strftime('%H:%M:%S', time.localtime(stamp)) if stamp is not None else '')
    return '[b ' + ('#70e1c1' if oracle.get('ok') else '#ff6b7a') + ']' + label + '[/]'


class SummaryPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        status, overview = snapshot.status, snapshot.overview or {}
        run_state = str(status.get("run_state") or "UNKNOWN")
        state_label = _RUN_STATE_LABELS.get(run_state, run_state.replace("_", " ").title())
        host = overview.get("host_observation", {})
        if run_state not in {"COMPLETED", "CANCELLED", "FAILED"}:
            if host.get("availability") == "absent":
                state_label += " · HOST ABSENT (orphaned)"
            if status.get("dispatch_error"):
                state_label += " · DISPATCH PAUSED (Host error)"
            provider_retry = status.get("provider_retry") or {}
            if provider_retry.get("pending"):
                state_label += " · PROVIDER COOLDOWN (auth)"
        lines = ["[b #78dcff]RUN AT A GLANCE[/]  [dim]? legend · d details[/dim]",
            f"[b]{_value(state_label)}[/]",
            f"Goal: {_goal_objective(snapshot)}",
            f"Verdict {_assessment_badge(str(status.get('goal_verdict') or 'PENDING'))} · "
            f"{sum(item.assessment is not None for item in snapshot.hypotheses)}/{len(snapshot.hypotheses)} assessed"]
        activity, budget_lines, reliability = [], [], []
        if overview:
            totals = overview.get("totals", {})
            budget = snapshot.goal.get("research_contract", {}).get("budget", {})
            hours = budget.get("wallclock_hours")
            if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
                budget_lines.append(_limit_line("⏱ Hours", overview.get("elapsed_seconds", 0) / 3600
                    if overview.get("elapsed_seconds") is not None else None, hours))
            else:
                budget_lines.append(f"⏱ {_duration(overview.get('elapsed_seconds'))}")
            if overview.get("waiting_human_seconds"):
                budget_lines.append(f"Human wait {_duration(overview['waiting_human_seconds'])} (included)")
            experiments = totals.get("experiments", {}).get("total")
            activity.append(f"🧪 Exp {_count(experiments)} · chains {_count(totals.get('chains'))} "
                f"/ {_count(totals.get('active_chains'))} open")
            if budget.get("max_experiments") is not None:
                budget_lines.append(_limit_line("🧪 Exp", experiments, budget['max_experiments']))
            reliability.extend(_reliability_lines(overview.get("reliability")))
            retry = overview.get("reliability", {}).get("costs", {})
            if retry.get("retry_attempts") != 0 or retry.get("retry_unknown_nodes"):
                reliability.append(f"🔁 Retry ≥{_compact(retry.get('retry_reported_tokens'))} tok "
                    f"· res {_compact(retry.get('retry_reserved_tokens'))} · closed ?{_count(retry.get('retry_unknown_closed_attempts'))}")
            activity.append(f"📚 Lit {_count(totals.get('literature', {}).get('retrieved'))} "
                f"/ PDF {_count(totals.get('literature', {}).get('pdf_available'))} · "
                f"Skills {_count(totals.get('skills', {}).get('count'))} · EVO {_count(totals.get('evolutions', {}).get('changed'))}")
            if totals.get('leader', {}).get('started') or totals.get('debate', {}).get('started'):
                activity.append(f"Leader {_count(totals.get('leader', {}).get('completed'))}/{_count(totals.get('leader', {}).get('started'))} "
                    f"· Debate {_count(totals.get('debate', {}).get('completed'))}/{_count(totals.get('debate', {}).get('started'))}")
        budget_lines.extend(_agent_usage_lines(status, overview.get("accounting")))
        health = [f"OPEN {_count(status.get('open_attempts'))} · Oracle {_oracle_observation_label(snapshot.oracle)}"]
        pending = sum(item.get("state") == "OPEN" for item in status.get("grounding_dialogue", []))
        if pending:
            health.append(f"[yellow]Grounding: {pending} questions pending · w Web[/]")
        provider_retry = status.get("provider_retry") or {}
        if provider_retry.get("pending"):
            gates = [item for item in provider_retry.get("gates", []) if item.get("pending")]
            latest = max((float(item["until"]) for item in gates), default=None)
            until = time.strftime("%H:%M", time.localtime(latest)) if latest is not None else "unknown"
            health.append(f"[yellow]Provider auth rejected (401/403): dispatch paused until {until} · "
                "p resume after credential restore[/]")
        for title, section in (("RESEARCH ACTIVITY", activity), ("BUDGET & TOKENS", budget_lines),
                ("EXECUTION RELIABILITY", reliability), ("RUN HEALTH", health)):
            if section:
                lines.extend(("", f"[#24566a]──[/] [b #9ab0bf]{title}[/]", *section))
        self.update("\n".join(lines))


class ExpertsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot, role_filter: str = "") -> None:
        lines = ["[b #78dcff]EXPERT LANES[/]  [dim]current work · ? legend[/dim]"]
        cards = []
        overview = snapshot.overview or {}
        host = overview.get("host_observation", {}).get("availability", "unconfirmed")
        if _has_host_status(snapshot):
            host = "responded"
        if host != "responded":
            lines.append(f"[yellow]Host {_value(host)} · OPEN? = unconfirmed activity[/]")
        experts = overview.get("experts", [])
        if not experts:
            for expert in snapshot.experts:
                if role_filter and expert.role != role_filter:
                    continue
                cards.append(f"[b]{_role(snapshot, expert.role)}[/]\n{len(expert.open_attempt_ids)} OPEN · "
                    f"{expert.agent_attempts} agent / {expert.process_attempts} process")
            if not snapshot.experts:
                lines.append("[dim]No frozen roster found[/dim]")
        for expert in experts:
            role = expert["role"]
            if role_filter and role != role_filter:
                continue
            stages = expert.get("activity", expert.get("stages", []))
            opened = sum(stage.get("state") == "OPEN" for stage in stages)
            label = f"{opened} OPEN" if opened else "last recorded" if stages else "not started"
            card = [f"[b]{_role(snapshot, role)}[/] · C{_count(expert.get('cycle'))} · {label}",
                _lane_line(stages, host)]
            for stage in stages:
                node = dict(_LANE_STAGES).get(stage.get("node"), _short_text(stage.get("node"), limit=30))
                scope = stage.get("chain") or stage.get("experiment_id") or stage.get("subkind") or "—"
                kind = " job" if stage.get("kind") == "PROCESS" else ""
                card.append(f"  {node}{kind} · {_short_text(scope, limit=28)} · {_stage_badge(stage, host)} "
                    f"· {_duration(stage.get('elapsed_seconds'))}")
            card.extend(("", *_reliability_lines(expert.get("reliability"))))
            accounting = expert.get("accounting", {})
            extra = accounting.get("observed_unsettled_tokens", 0)
            if extra:
                card.append(f"+≥{_compact(extra)} observed tokens, unsettled")
            card.append(f"🔢 ≥{_compact(accounting.get('reported_tokens'))} tok · res {_compact(accounting.get('reserved_tokens'))} "
                f"· closed ?{_count(accounting.get('unknown_closed_attempts'))} · tok ?{_count(accounting.get('unavailable_token_attempts'))}")
            cards.append("\n".join(card))
        if role_filter and not cards:
            lines.append("[dim]No experts match this filter[/dim]")
        rendered = [Text.from_markup("\n".join(lines))]
        for card in cards:
            rendered.extend((Text(""), Panel(Text.from_markup(card), border_style="#24566a", padding=(0, 1))))
        # Reuse the ordinary content equality check so polling retains scroll
        # and avoids rebuilding unchanged visuals. Rich sizes cards on resize.
        self.update("\n\n".join((*lines, *cards)), renderable=Group(*rendered))


class ComponentsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        lines = ["[b #78dcff]COMPONENT WORKFLOWS[/]  [dim]independent lanes[/dim]"]
        if not snapshot.components:
            lines.append("[dim]No frozen components found[/dim]")
        for component in snapshot.components:
            blocked = (
                f"[yellow]{_value(', '.join(component.blocked_reasons))}[/yellow]"
                if component.blocked else "no"
            )
            open_attempts = ", ".join(component.open_attempt_ids) or "-"
            lines.append(
                f"{component.display_ordinal}. {_component_progress(component.state)}  "
                f"[b]{_value(component.component_id)}[/b]  [dim]{_value(component.tier)}[/dim]"
            )
            lines.append(
                f"   [b]{_value(_phase_label(component.phase))}[/b]  "
                f"[dim]durable state={_value(component.state)}[/dim]"
            )
            lines.append(
                f"   owner={_role(snapshot, component.owner_role)}  "
                "proposers="
                f"{', '.join(_role(snapshot, role) for role in component.proposer_roles)}  "
                f"blocked={blocked}"
            )
            lines.append(f"   open={_value(open_attempts)}")
            warning = _revision_warning_badge(component)
            if warning is not None:
                lines.append(f"   {warning}")
        self.update("\n".join(lines))


class HypothesesPanel(CockpitPanel):
    def __init__(self, **kwargs) -> None:
        self.chain_tree = Tree("Experts → chains → hypotheses / stages", id="hypothesis-tree")
        self._tree_rows = None
        super().__init__(extra_children=(self.chain_tree,), **kwargs)

    def _update_tree(self, overview: Mapping[str, object], role_filter: str, hypotheses=()) -> None:
        rows = []
        for expert in overview.get("experts", []):
            role = expert["role"]
            if role_filter and role != role_filter:
                continue
            expert_key = ("expert", role)
            rows.append((expert_key, None, _value(role), True))
            placed = set()
            for chain in expert.get("chains", []):
                key = ("chain", role, chain.get("component_id"), chain.get("cycle"), chain.get("chain"))
                rows.append((key, expert_key, f"{_value(chain.get('chain'))} · {_value(chain.get('component_id'))} "
                    f"· C{_count(chain.get('cycle'))} · {len(chain.get('nodes', []))} nodes", False))
                for node in chain.get("nodes", []):
                    placed.add(node["hypothesis_id"])
                    label = f"{_value(node['hypothesis_id'])} · {_assessment_badge(node.get('assessment'))}"
                    if node.get("pruned"):
                        label += " · [#ffb86b]PRUNED (control)[/]"
                    rows.append((("hypothesis", node["hypothesis_id"]), key, label, False))
                for stage in chain.get("stages", []):
                    rows.append((("stage", stage["attempt_id"]), key,
                        f"{_value(stage.get('node'))}/{_value(stage.get('subkind'))} · "
                        f"{_attempt_badge(str(stage.get('terminal_reason') or stage.get('state')))} "
                        f"· {_count(stage.get('attempts'))} Attempts", False))
            unplaced = [item for item in hypotheses if item.proposer_role == role and item.hypothesis_id not in placed]
            if unplaced:
                unknown = ("unassigned", role)
                rows.append((unknown, expert_key, "Accepted nodes · chain evidence unavailable", True))
                for item in unplaced:
                    rows.append((("hypothesis", item.hypothesis_id), unknown,
                        f"{_value(item.hypothesis_id)} · {_assessment_badge(item.assessment)}", False))
        if rows == self._tree_rows:
            return
        tree = self.chain_tree
        expanded, known = set(), set()
        def remember(node):
            known.add(node.data)
            if node.is_expanded:
                expanded.add(node.data)
            for child in node.children:
                remember(child)
        remember(tree.root)
        selected = tree.cursor_node.data if tree.cursor_node else None
        scroll = (tree.scroll_x, tree.scroll_y)
        tree.clear()
        nodes = {}
        for key, parent, label, default_expand in rows:
            parent_node = nodes.get(parent, tree.root)
            nodes[key] = parent_node.add(Text.from_markup(label), data=key,
                expand=key in expanded or (key not in known and default_expand),
                allow_expand=key[0] in {"expert", "chain", "unassigned"})
        tree.root.expand()
        root = tree.root
        def restore_cursor():
            # Textual assigns new node line numbers during layout. Restoring
            # before that moves the selection to line -1 on every changed poll.
            if tree.root is root:
                if selected in nodes:
                    tree.move_cursor(nodes[selected])
                tree.scroll_to(x=scroll[0], y=scroll[1], animate=False)
        tree.call_after_refresh(restore_cursor)
        self._tree_rows = rows

    def update_snapshot(self, snapshot: MonitorSnapshot, role_filter: str = "") -> None:
        self.chain_tree.display = bool(snapshot.overview)
        if snapshot.overview:
            totals = snapshot.overview.get("totals", {})
            self.update("[b #78dcff]HYPOTHESIS CHAINS[/] · Left/Right expand · Web for claims/evidence\n"
                + ("No hypotheses proposed yet" if not snapshot.hypotheses else
                   f"{len(snapshot.hypotheses)} accepted nodes · chain coverage {_value(totals.get('chain_coverage', 'unknown'))}"))
            self._update_tree(snapshot.overview, role_filter, snapshot.hypotheses)
            return
        assessed = sum(item.assessment is not None for item in snapshot.hypotheses)
        lines = [
            "[b #78dcff]HYPOTHESES / ASSESSMENTS[/]  "
            f"[dim]{assessed}/{len(snapshot.hypotheses)} assessed · latest 12[/dim]"
        ]
        if not snapshot.hypotheses:
            lines.append("[dim]No hypotheses proposed yet[/dim]")
        for hypothesis in snapshot.hypotheses[-12:]:
            lines.append(
                f"{_assessment_badge(hypothesis.assessment)}  "
                f"[b]{_value(hypothesis.hypothesis_id)}[/b]  "
                f"[dim]{_value(hypothesis.component_id)}[/dim]"
            )
            lines.append(
                f"   {_role(snapshot, hypothesis.proposer_role)} · "
                f"method={_value(hypothesis.method_family_id)}"
            )
            lines.append(f"   {_short_text(hypothesis.claim, limit=220)}")
            if hypothesis.parent_hypothesis_id is not None:
                lines.append(
                    f"   lineage={_value(hypothesis.relation)} "
                    f"{_value(hypothesis.parent_hypothesis_id)}"
                )
            if hypothesis.evidence_ids:
                lines.append(
                    f"   evidence={_value(', '.join(hypothesis.evidence_ids))}"
                )
            elif hypothesis.assessment is not None:
                lines.append("   [dim]assessment has no displayed evidence IDs[/dim]")
        self.update("\n".join(lines))


class WorkflowsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        with_next_command = sum(
            item.next_command is not None for item in snapshot.workflows
        )
        lines = [
            "[b #78dcff]NEXT DURABLE COMMANDS[/]  "
            f"[dim]{with_next_command}/{len(snapshot.workflows)} expose a next command[/dim]"
        ]
        if not snapshot.workflows:
            lines.append("[dim]No workflow heads found[/dim]")
        ordered = sorted(
            snapshot.workflows,
            key=lambda item: (
                item.next_command is None,
                item.kind != "RUN",
                item.component_id or item.workflow_id,
            ),
        )
        for workflow in ordered[:12]:
            subject = workflow.component_id or workflow.workflow_id
            command = workflow.next_command or "no command"
            lines.append(
                f"[b]{_value(workflow.kind)}[/b] {_value(subject)}  "
                f"[dim]{_value(workflow.state)} · rev {workflow.revision}[/dim]"
            )
            lines.append(
                f"   next={_value(command)}  ·  "
                f"{_value(_due_label(workflow.due_at, snapshot.observed_at))}"
            )
        if len(ordered) > 12:
            lines.append(f"[dim]… {len(ordered) - 12} more workflow heads[/dim]")
        open_rendezvous = sum(item.status != "RESOLVED" for item in snapshot.rendezvous)
        lines.extend((
            "",
            "[b #78dcff]PEER RENDEZVOUS[/]  "
            f"[dim]{open_rendezvous}/{len(snapshot.rendezvous)} unresolved · latest 6[/dim]",
        ))
        if not snapshot.rendezvous:
            lines.append("[dim]No typed peer rendezvous recorded[/dim]")
        for rendezvous in snapshot.rendezvous[-6:]:
            target = rendezvous.target_role or "no target"
            lines.append(
                f"{_rendezvous_badge(rendezvous.status)}  "
                f"[b]{_value(rendezvous.kind)}[/b] · {_value(_phase_label(rendezvous.phase))}"
            )
            lines.append(
                f"   {_role(snapshot, rendezvous.requester_role)} → "
                f"{_role(snapshot, target)} · {_value(rendezvous.rendezvous_id)}"
            )
            lines.append(f"   {_short_text(rendezvous.question, limit=180)}")
            if rendezvous.disposition is not None:
                responder = rendezvous.responder_role or "unknown responder"
                lines.append(
                    f"   {_value(rendezvous.disposition)} by {_role(snapshot, responder)} · "
                    f"{_short_text(rendezvous.summary, limit=160)}"
                )
        waits = snapshot.status.get("resource_waits")
        lines.extend(("", "[b #78dcff]LIVE RESOURCE WAITS[/]"))
        binding = snapshot.status.get("gpu_pool")
        if isinstance(binding, Mapping):
            lines.append(f"Shared GPU pool: {_value(binding.get('socket_path'))}")
            lines.append(f"Namespace: {_value(binding.get('namespace'))}")
        elif "gpu_pool" in snapshot.status:
            lines.append("[dim]Shared GPU pool: not configured for this run[/dim]")
        if waits is None:
            lines.append("[dim]Host STATUS unavailable[/dim]")
        elif not waits:
            lines.append("[dim]No resource wait reported by this Host poll[/dim]")
        else:
            for wait in waits:
                if not isinstance(wait, Mapping):
                    continue
                lines.append(
                    f"[yellow]{_value(wait.get('experiment_id'))}[/yellow] · "
                    f"{_short_text(wait.get('reason'), limit=200)}"
                )
        self.update("\n".join(lines))


class AttemptsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        lines = ["[b #78dcff]ATTEMPT TELEMETRY[/]  [dim]latest 12 durable records[/dim]"]
        if not snapshot.attempts:
            lines.append("[dim]No attempts recorded[/dim]")
        now = snapshot.observed_at
        for attempt in snapshot.attempts[-12:]:
            end = attempt.ended_at if attempt.ended_at is not None else now
            elapsed = max(0.0, end - attempt.started_at)
            terminal = attempt.terminal_reason or attempt.state
            lines.append(
                f"{_attempt_badge(terminal)}  {_value(attempt.kind)}  "
                f"{_value(attempt.component_id or attempt.workflow_id)}"
            )
            lines.append(
                f"   {elapsed:.1f}s · owner={_value(attempt.role)} · "
                f"phase={_value(_phase_label(attempt.phase))} · "
                f"attempt={_value(attempt.attempt_id)}#{attempt.attempt_ordinal}"
            )
            if attempt.failure_code is not None or attempt.returncode is not None:
                lines.append(
                    f"   [red]failure={_value(attempt.failure_code)}[/red] · "
                    f"returncode={_value(attempt.returncode)}"
                )
            if attempt.diagnostic_summary is not None:
                lines.append(
                    f"   [yellow]{_short_text(attempt.diagnostic_summary, limit=220)}[/yellow]"
                )
            if attempt.stdout_path is not None or attempt.stderr_path is not None:
                lines.append(
                    f"   logs: stdout={_value(attempt.stdout_path)} · "
                    f"stderr={_value(attempt.stderr_path)}"
                )
            devices = attempt.gpu_usage or (() if attempt.gpu_uuid is None else ({
                "gpu_uuid": attempt.gpu_uuid,
                "minimum_free_memory_mib": attempt.minimum_free_memory_mib,
                "latest_free_memory_mib": attempt.latest_free_memory_mib,
                "latest_utilization_percent": attempt.latest_utilization_percent,
                "pressure_exceeded": attempt.resource_pressure_exceeded,
            },))
            for device in devices:
                pressure = (
                    " [red]reserve pressure[/red]"
                    if device.get("pressure_exceeded")
                    else ""
                )
                lines.append(
                    f"   gpu={_value(device.get('gpu_uuid'))} "
                    f"min/latest-free={_value(device.get('minimum_free_memory_mib'))}/"
                    f"{_value(device.get('latest_free_memory_mib'))} MiB "
                    f"util={_value(device.get('latest_utilization_percent'))}%{pressure}"
                )
                owned = device.get("owned_memory")
                if isinstance(owned, Mapping):
                    lines.append(
                        f"      own sampled latest/peak={_value(owned.get('latest_sample_memory_mib'))}/"
                        f"{_value(owned.get('sampled_peak_memory_mib'))} MiB · coverage={_value(owned.get('coverage'))} · "
                        f"unknown samples={_value(owned.get('unknown_sample_count'))}/{_value(owned.get('sample_count'))}"
                    )
        self.update("\n".join(lines))


class EvidencePanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        lines = ["[b #78dcff]CANONICAL FACTS / ORACLE[/]"]
        status, overview = snapshot.status, snapshot.overview or {}
        lines.extend(_reliability_lines(overview.get("reliability"), details=True))
        lines.extend(("[dim]TO timeout · CX cancelled · TE tool errors/calls (? traces). Correction-only tokens unavailable.[/dim]",
            f"Required assessed {_count(status.get('required_assessment_count'))}/{_count(status.get('required_component_count'))}",
            f"State-root OPEN {snapshot.durable_open_attempts_state_root} · backup {_count(status.get('quiescent_for_backup'))}",
            f"Observation: {'Host STATUS' if _has_host_status(snapshot) else 'offline state store'}"))
        if overview:
            lines.extend(_metric_lines(overview.get("totals", {})))
            for name in ("leader", "debate"):
                lines.append(_interaction_line(name.title(), overview.get("totals", {}).get(name, {})))
            for notice in overview.get("notices", []):
                lines.append(_value(notice))
            last_error = overview.get("host_observation", {}).get("last_recorded_exception") or {}
            if last_error:
                lines.append(f"Historical Host exception: {_value(last_error.get('exception_type'))} "
                    f"at {_value(last_error.get('timestamp'))} · diagnostics/host.jsonl")
        facts = "  ".join(f"{name}={count}" for name, count in snapshot.fact_counts.items())
        lines.append(_value(facts or "No facts recorded"))
        issues = snapshot.oracle.get("issues", ())
        lines.extend(
            (
                "",
                _oracle_observation_label(snapshot.oracle) + " integrity/residue checks",
                "[dim]Oracle CLEAN is not a scientific-success verdict.[/dim]",
            )
        )
        if issues:
            for issue in list(issues)[:8]:
                if isinstance(issue, dict):
                    lines.append(
                        f"[red]{_value(issue.get('code'))}[/red]: {_value(issue.get('message'))}"
                    )
                else:
                    lines.append(f"[red]{_value(issue)}[/red]")
        self.update("\n".join(lines))


class HypoloomVNextMonitor(App):
    """Read-only polling plus explicit operator forms and a loopback Web observer."""

    def _handle_exception(self, error: Exception) -> None:
        from .runtime_diagnostics import record_host_exception
        try:
            record_host_exception(self.state_root, error, phase="tui", run_id=self.run_id,
                secrets=tuple(value for key, value in os.environ.items()
                    if key in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}))
        finally:
            super()._handle_exception(error)

    TITLE = "Hypoloom · Research Operations"
    NARROW_WIDTH = 88
    BINDINGS = (
        Binding("q", "quit", "Leave monitor"),
        Binding("x", "end_run", "End run"),
        Binding("r", "refresh", "Refresh"),
        Binding("w", "web_access", "Web / local access"),
        Binding("b", "support_export", "Report Issue", show=False),
        Binding("h", "human_decision", "Human decision"),
        Binding("p", "provider_resume", "Provider resume"),
        Binding("d", "toggle_diagnostics", "Diagnostics"),
        Binding("question_mark", "toggle_help", "Help", show=False),
    )
    CSS = """
    Screen {
        layout: vertical;
        background: #06111b;
        color: #c8d7e3;
    }

    Header {
        background: #081c29;
        color: #8be7ff;
    }

    HeaderIcon { display: none; }

    Footer {
        background: #081c29;
        color: #9ab0bf;
    }

    Footer > .footer--key {
        background: #123348;
        color: #8be7ff;
    }

    #observer-bar {
        height: 3;
        background: #0a2530;
        border-bottom: solid #24566a;
    }

    #freshness {
        width: 1fr;
        height: 3;
        padding: 0 2;
        content-align: left middle;
        background: #0a2530;
        color: #d8e6ed;
    }

    #freshness.stale {
        background: #352519;
    }

    #help-button, #support-help-button, #end-run-button {
        width: auto;
        height: 3;
        min-width: 8;
        padding: 0 1;
        margin: 0 1;
        border: none;
        background: #123f53;
        color: #a9efff;
    }

    #end-run-button { color: #ffb86b; }
    #monitor-exit-help { height: 1; padding: 0 1; color: #9ab0bf; }

    #human-decision-button { display: none; width: 1fr; height: 3; background: #513519; color: #ffdb9e; }

    #help-button:hover,
    #help-button:focus,
    #support-help-button:hover,
    #support-help-button:focus {
        background: #1a6077;
        color: #ffffff;
    }

    #help-legend {
        display: none;
        height: auto;
        max-height: 16;
        overflow-y: auto;
        padding: 0 2;
        background: #102331;
        color: #d8e6ed;
        border-bottom: solid #2f8198;
    }

    #help-legend.visible {
        display: block;
    }

    Screen.narrow #help-legend { max-height: 40%; }

    #expert-filter {
        height: 3;
        margin: 0 1;
    }

    #hypothesis-tree {
        height: auto;
        min-height: 8;
        background: #0a1924;
    }

    #components, #attempts, #evidence { display: none; }
    Screen.diagnostics #components, Screen.diagnostics #attempts, Screen.diagnostics #evidence { display: block; }

    #cockpit {
        height: 1fr;
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-rows: 2fr 1fr;
        grid-gutter: 1 1;
        padding: 1;
        overflow-y: hidden;
        background: #06111b;
    }

    .cockpit-panel {
        width: 1fr;
        height: 1fr;
        min-height: 5;
        border: round #24566a;
        padding: 0 1;
        background: #0a1924;
        color: #c8d7e3;
        overflow-x: auto;
        overflow-y: auto;
        scrollbar-color: #2f8198;
        scrollbar-background: #07131d;
    }

    .cockpit-panel:focus {
        border: solid #63d9ff;
        background: #0d2230;
    }

    #evidence {
        column-span: 2;
    }

    Screen.diagnostics #cockpit { grid-rows: 1fr; }

    Screen.narrow #cockpit {
        grid-size: 1;
        grid-columns: 1fr;
        grid-rows: 14;
        overflow-y: auto;
    }

    Screen.narrow .cockpit-panel {
        min-height: 14;
    }

    Screen.narrow #evidence {
        column-span: 1;
    }
    """

    def __init__(
        self,
        state_root: Path,
        run_id: str,
        *,
        interval: float = 2.0,
        initial_snapshot: MonitorSnapshot | None = None,
        web_enabled: bool = True,
        web_port: int = DEFAULT_PORT,
        gpu_pool_socket: str | Path | None = None,
        gpu_pool_server_uid: int | None = None,
    ) -> None:
        super().__init__()
        self.state_root = state_root.expanduser().resolve()
        self.run_id = run_id
        self.interval = interval
        self.web_enabled = web_enabled
        self.web_port = web_port
        self.gpu_pool_socket = gpu_pool_socket
        self.gpu_pool_server_uid = gpu_pool_server_uid
        self._initial_snapshot = initial_snapshot
        self._observer_reader = ObserverRefresh(self.state_root, self.run_id, refresh_seconds=interval)
        self._has_snapshot = False
        self._web_server: WebObserverServer | None = None
        self._web_thread: threading.Thread | None = None
        self._web_error: str | None = None
        self._refresh_thread: threading.Thread | None = None
        self._manual_refresh_pending = False
        self._accept_refreshes = False
        self._last_snapshot: MonitorSnapshot | None = None
        self._expert_roles: tuple[str, ...] = ()
        self._human_drafts: dict[str, dict] = {}
        self._end_run_draft: dict = {}
        self._provider_resume_draft: dict = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="observer-bar"):
            yield FreshnessBanner(
                "[b #70e1c1]READ-ONLY / OFFLINE[/]  [dim]waiting for snapshot[/dim]",
                id="freshness",
            )
            yield Button("Help", id="help-button", tooltip="Window guide and execution reliability (?)")
            yield Button("Report Issue", id="support-help-button", tooltip="Preview and export a support bundle (b)")
            yield Button("End run", id="end-run-button", tooltip="Review cancellation (x); q only leaves the monitor")
        yield Static("q: leave monitor; Host and tasks continue. x: end run…", markup=False, id="monitor-exit-help")
        legend = HelpLegend(id="help-legend")
        legend.update(_HELP_TEXT)
        yield legend
        yield Button("Waiting for your decision · press h", id="human-decision-button")
        yield Select([("All experts · run totals remain global", "")], value="", allow_blank=False, id="expert-filter")
        with Grid(id="cockpit"):
            yield SummaryPanel(id="summary", classes="cockpit-panel")
            yield ExpertsPanel(id="experts", classes="cockpit-panel")
            yield WorkflowsPanel(id="workflows", classes="cockpit-panel")
            yield HypothesesPanel(id="hypotheses", classes="cockpit-panel")
            yield ComponentsPanel(id="components", classes="cockpit-panel")
            yield AttemptsPanel(id="attempts", classes="cockpit-panel")
            yield EvidencePanel(id="evidence", classes="cockpit-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._accept_refreshes = True
        self._start_web_observer()
        self._set_responsive_layout(self.size.width)
        self.sub_title = f"{self.run_id} | read-only/offline | {self.state_root}"
        if self._initial_snapshot is not None:
            self._render(self._initial_snapshot)
            self._initial_snapshot = None
        else:
            self._refresh_snapshot()
        self.set_interval(self.interval, self._refresh_snapshot)

    def on_unmount(self) -> None:
        self._accept_refreshes = False
        self._manual_refresh_pending = False
        self._refresh_thread = None
        self.close_web_observer()
        self._observer_reader.close()

    def on_resize(self, event: events.Resize) -> None:
        self._set_responsive_layout(event.size.width)

    def _set_responsive_layout(self, width: int) -> None:
        self.screen.set_class(width < self.NARROW_WIDTH, "narrow")

    def _render(self, snapshot: MonitorSnapshot) -> None:
        self._last_snapshot = snapshot
        pending = [item for item in snapshot.status.get("grounding_dialogue", []) if item.get("state") == "OPEN"]
        human_button = self.query_one("#human-decision-button", Button)
        human_button.display = bool(pending)
        human_button.label = f"{len(pending)} decision pending · Review and answer · h"
        from .human_decision_tui import HumanDecisionScreen
        from .end_run_tui import EndRunScreen
        from .provider_resume_tui import ProviderResumeScreen
        for screen in self.screen_stack:
            if isinstance(screen, (HumanDecisionScreen, EndRunScreen, ProviderResumeScreen)) and screen.is_mounted:
                screen.update_status(snapshot.status)
        selector = self.query_one("#expert-filter", Select)
        roles = tuple(item["role"] for item in snapshot.overview.get("experts", []))
        if roles != self._expert_roles:
            previous = selector.value
            selector.set_options([("All experts · run totals remain global", ""), *((role, role) for role in roles)])
            selector.value = previous if previous in roles else ""
            self._expert_roles = roles
        role_filter = selector.value if isinstance(selector.value, str) else ""
        self.query_one("#summary", SummaryPanel).update_snapshot(snapshot)
        self.query_one("#experts", ExpertsPanel).update_snapshot(snapshot, role_filter)
        self.query_one("#components", ComponentsPanel).update_snapshot(snapshot)
        self.query_one("#workflows", WorkflowsPanel).update_snapshot(snapshot)
        self.query_one("#hypotheses", HypothesesPanel).update_snapshot(snapshot, role_filter)
        self.query_one("#attempts", AttemptsPanel).update_snapshot(snapshot)
        self.query_one("#evidence", EvidencePanel).update_snapshot(snapshot)
        self.query_one("#freshness", FreshnessBanner).show_snapshot(snapshot)
        self._has_snapshot = True
        age = max(0, int(time.time() - snapshot.observed_at))
        observation = "host-status" if _has_host_status(snapshot) else "offline"
        self.sub_title = (
            f"{snapshot.run_id} | {snapshot.status.get('run_state')} | "
            f"read-only/{observation} | snapshot {age}s old"
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "expert-filter" and self._last_snapshot is not None:
            self._render(self._last_snapshot)

    def action_toggle_diagnostics(self) -> None:
        self.screen.toggle_class("diagnostics")

    def _load_snapshot(self) -> None:
        try:
            server = self._web_server
            snapshot = (server.refresh_reader.get()['snapshot'] if server is not None and server.shared_refresh
                else self._observer_reader.get()['snapshot'])
        except Exception as exc:
            completion = SnapshotRefreshCompleted(None, exc)
        else:
            completion = SnapshotRefreshCompleted(snapshot, None)
        try:
            self.post_message(completion)
        except RuntimeError:
            # The daemon reader may finish after the Textual loop has closed.
            pass

    def on_snapshot_refresh_completed(
        self, message: SnapshotRefreshCompleted,
    ) -> None:
        if not self._accept_refreshes:
            return
        self._refresh_thread = None
        if message.error is not None:
            self.query_one("#freshness", FreshnessBanner).show_failure(
                message.error, has_snapshot=self._has_snapshot,
            )
            self.sub_title = f"{self.run_id} | STALE | read-only"
            self.notify(
                f"Refresh failed: {message.error}", severity="error", timeout=10,
            )
        elif message.snapshot is not None:
            self._render(message.snapshot)
        if self._manual_refresh_pending:
            self._manual_refresh_pending = False
            self.call_later(self._refresh_snapshot)

    def _refresh_snapshot(self) -> None:
        if not self._accept_refreshes:
            return
        if self._refresh_thread is not None:
            return
        self._refresh_thread = threading.Thread(
            target=self._load_snapshot,
            name="hypoloom-monitor-snapshot",
            daemon=True,
        )
        self._refresh_thread.start()

    def action_refresh(self) -> None:
        if self._refresh_thread is not None:
            self._manual_refresh_pending = True
            return
        self._refresh_snapshot()

    def action_toggle_help(self) -> None:
        legend = self.query_one("#help-legend", HelpLegend)
        legend.toggle_class("visible")
        if legend.has_class("visible"):
            legend.focus()
        else:
            self.query_one("#help-button", Button).focus()

    @property
    def web_access_url(self) -> str | None:
        server = self._web_server
        return server.startup_url() if server is not None else None

    @property
    def web_error(self) -> str | None:
        return self._web_error

    def _start_web_observer(self) -> None:
        if not self.web_enabled:
            return
        server: WebObserverServer | None = None
        try:
            server = create_server(
                self.state_root,
                self.run_id,
                port=self.web_port,
                snapshot_builder=build_observer_snapshot,
                **({"gpu_pool_socket": self.gpu_pool_socket, "gpu_pool_server_uid": self.gpu_pool_server_uid}
                   if self.gpu_pool_socket is not None else {}),
            )
            server.log_requests = False
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.1},
                name="hypoloom-web-observer",
                daemon=True,
            )
            thread.start()
        except (OSError, RuntimeError, ValueError) as exc:
            if server is not None:
                server.server_close()
            self._web_error = str(exc)
            self.notify(
                f"Web observer unavailable: {exc}. Press w for access help.",
                severity="warning",
                timeout=12,
            )
            return
        self._web_server = server
        self._web_thread = thread
        self._web_error = None

    def close_web_observer(self) -> None:
        """Stop the TUI-owned server; safe to call more than once."""

        server = self._web_server
        thread = self._web_thread
        self._web_server = None
        self._web_thread = None
        if server is None:
            return
        if thread is not None and thread.is_alive():
            server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _web_access_content(self) -> str:
        url = self.web_access_url
        if url is not None and self._web_server is not None:
            port = int(self._web_server.server_address[1])
            inside_tmux = bool(os.environ.get("TMUX"))
            tunnel = (
                f"ssh -N -L {port}:127.0.0.1:{port} "
                "<ssh-user>@<research-server>"
            )
            alternate = (
                f"ssh -N -L <local-port>:127.0.0.1:{port} "
                "<ssh-user>@<research-server>"
            )
            port_help = "OS-selected port; the address may change after restart.\n\n" if self.web_port == 0 else ""
            clipboard_help = (
                "Clipboard: the button sends one explicit tmux passthrough "
                "request and does not change tmux settings. Paste to verify; "
                "terminals do not acknowledge OSC 52 delivery. If the SSH "
                "client blocks it, hold Shift while dragging over the URL and "
                "use the client's Copy action. tmux copy-mode alone guarantees "
                "only a tmux paste buffer, not the workstation clipboard.\n\n"
                if inside_tmux
                else
                "Clipboard: the button sends an unconfirmed terminal request. "
                "Paste to verify; if the terminal blocks it, select the URL "
                "and use the terminal's Copy action.\n\n"
            )
            return (
                "[b #78dcff]WEB OBSERVER READY[/]  "
                "[dim]TUI-owned · loopback-only · read-only[/dim]\n\n"
                f"{port_help}"
                f"Browser URL (contains the ephemeral token):\n{escape(url)}\n\n"
                "Run this on your local workstation:\n"
                f"{escape(tunnel)}\n\n"
                "Then open the exact Browser URL locally. The Web observer stays "
                "available while this TUI is running and stops when the TUI exits.\n\n"
                f"{clipboard_help}"
                "If that local port is already occupied, use:\n"
                f"{escape(alternate)}\n"
                "and replace only the port in the Browser URL with <local-port>.\n\n"
                "[dim]Do not share the token URL. The SSH tunnel must be started "
                "on the browser workstation; the server cannot create that local "
                "tunnel for you.[/dim]"
            )
        if not self.web_enabled:
            return (
                "[b #ffb86b]WEB OBSERVER DISABLED[/]\n\n"
                "This TUI was started with --no-web. Restart monitor without "
                "--no-web to couple the loopback Web observer to the TUI, or run "
                "the standalone `hypoloom.cli web` command for a Web-only view."
            )
        restart = shlex.join((
            sys.executable,
            "-m",
            "hypoloom.cli",
            "monitor",
            "--state-root",
            str(self.state_root),
            "--run-id",
            self.run_id,
            "--web-port",
            "0",
        ))
        return (
            "[b #ff6b7a]WEB OBSERVER UNAVAILABLE[/]\n\n"
            f"Requested loopback port: {self.web_port}\n"
            f"Start error: {escape(self._web_error or 'unknown error')}\n\n"
            "The TUI remains usable. Restart with an OS-selected free port:\n"
            f"{escape(restart)}"
        )

    def action_web_access(self) -> None:
        self.push_screen(
            WebAccessScreen(
                self._web_access_content(),
                access_url=self.web_access_url,
                inside_tmux=bool(os.environ.get("TMUX")),
            )
        )

    def action_support_export(self) -> None:
        url = None if self.web_access_url is None else self.web_access_url + "&support=1"
        command = shlex.join((sys.executable, "-m", "hypoloom.cli", "debug", "export",
            "--state-root", str(self.state_root), "--run-id", self.run_id, "--support-bundle", "--recent-failures", "--preview"))
        content = ("[b #78dcff]REPORT AN ISSUE[/]\n\n"
            "Open the browser link to preview evidence and download a ZIP. Nothing is uploaded.\n"
            "Default copies omit research bodies and local paths. Private attachments require explicit selection.\n\n"
            "Preview shows run state/revision, failure groups, evidence scope and omissions.\n"
            "The browser also accepts an optional issue description; CLI: --issue-actual, --issue-expected, --issue-steps, --issue-occurred-at.\n"
            "Descriptions are included in public copies too; review them before sharing.\n\n"
            f"Offline preview command:\n{escape(command)}\n\n"
            "To save it, replace --preview with --archive --output /absolute/private/path/new-support.zip.\n\n"
            + self._web_access_content())
        if url is not None:
            content = content.replace(escape(self.web_access_url), escape(url))
        self.push_screen(WebAccessScreen(content, access_url=url, inside_tmux=bool(os.environ.get("TMUX"))))

    def action_end_run(self) -> None:
        from .end_run_tui import EndRunScreen
        if self._last_snapshot is None:
            self.notify("Wait for a current snapshot, then press x.")
            return
        if not isinstance(self.screen, EndRunScreen):
            self.push_screen(EndRunScreen(self._last_snapshot.status, self._end_run_draft))

    def action_human_decision(self) -> None:
        from .human_decision_tui import HumanDecisionScreen
        if self._last_snapshot is None:
            self.notify("Wait for a current snapshot, then press h.")
            return
        status = self._last_snapshot.status
        history = list(status.get("grounding_dialogue", []))
        pending = [item for item in history if item["state"] == "OPEN"]
        if not history:
            self.notify("No human decisions have been recorded.")
            return
        decision = pending[0] if pending else history[-1]
        self.push_screen(HumanDecisionScreen(decision, status,
            self._human_drafts.setdefault(decision["decision_id"], {}),
            [item for item in history if item["decision_id"] != decision["decision_id"]]))

    def action_provider_resume(self) -> None:
        from .provider_resume_tui import ProviderResumeScreen
        if self._last_snapshot is None:
            self.notify("Wait for a current snapshot, then press p.")
            return
        status = self._last_snapshot.status
        if not status.get("provider_retry"):
            self.notify("Provider cooldown state needs a live Host answer; the offline view cannot confirm it.")
            return
        if not isinstance(self.screen, ProviderResumeScreen):
            self.push_screen(ProviderResumeScreen(status, self._provider_resume_draft))

    def action_human_browser(self) -> None:
        server = self._web_server
        if server is None:
            self.notify("Web is unavailable. You can answer directly in this TUI.")
            return
        url = server.human_answer_url()
        content = ("[b #ffb86b]OPERATOR ANSWER LINK[/]\nThis link permits reviewed Grounding answers for this run. Keep it private.\n\n"
            + self._web_access_content().replace(escape(server.startup_url()), escape(url)))
        self.push_screen(WebAccessScreen(content, access_url=url, inside_tmux=bool(os.environ.get("TMUX"))))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help-button":
            self.action_toggle_help()
        elif event.button.id == "support-help-button":
            self.action_support_export()
        elif event.button.id == "human-decision-button":
            self.action_human_decision()
        elif event.button.id == "end-run-button":
            self.action_end_run()


def run_tui(
    state_root: Path,
    run_id: str,
    *,
    interval: float = 2.0,
    initial_snapshot: MonitorSnapshot | None = None,
    web_enabled: bool = True,
    web_port: int = DEFAULT_PORT,
    gpu_pool_socket: str | Path | None = None,
    gpu_pool_server_uid: int | None = None,
) -> None:
    app = HypoloomVNextMonitor(
        state_root,
        run_id,
        interval=interval,
        initial_snapshot=initial_snapshot,
        web_enabled=web_enabled,
        web_port=web_port,
        gpu_pool_socket=gpu_pool_socket,
        gpu_pool_server_uid=gpu_pool_server_uid,
    )
    try:
        app.run()
        if app.return_code:
            raise SystemExit(app.return_code)
    finally:
        app.close_web_observer()


__all__ = ["HypoloomVNextMonitor", "WebAccessScreen", "run_tui"]
