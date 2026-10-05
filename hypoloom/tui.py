"""Textual monitor with explicitly confirmed operator actions."""

from __future__ import annotations

from .ui_i18n import tr

import base64
import math
import os
import shlex
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
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
[b]Global tasks / Expert lanes[/]: run-wide preparation and coordination are separate from each expert's research. d shows execution identities; w opens full history and sharing details.
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
p: model API recovery · g: GPU recheck · l: literature retry · n: Telegram delivery retry.
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
        tr(
            "🔁 Retry ≥{0} tok · res {1} · closed ?{2}",
            _compact(costs.get('retry_reported_tokens')),
            _compact(costs.get('retry_reserved_tokens')),
            _count(costs.get('retry_unknown_closed_attempts')),
        ),
        tr(
            "Retry nodes ?{0} · tok ?{1} · elapsed {2} ?{3}",
            _count(costs.get('retry_unknown_nodes')),
            _count(costs.get('retry_unavailable_token_attempts')),
            _duration(costs.get('retry_elapsed_seconds')),
            _count(costs.get('retry_elapsed_unknown_attempts')),
        )]


def _metric_lines(metrics: Mapping[str, object]) -> list[str]:
    experiments = metrics.get("experiments", {})
    literature = metrics.get("literature", {})
    skills = metrics.get("skills", {})
    evolution = metrics.get("evolutions", {})
    return [
        tr(
            "Experiments {0} · hypotheses {1} · chains {2} / active {3}",
            _count(experiments.get('total')),
            _count(metrics.get('hypotheses')),
            _count(metrics.get('chains')),
            _count(metrics.get('active_chains')),
        ),
        tr(
            "Literature {0} · PDF {1} ({2}) · Skills {3} ({4})",
            _count(literature.get('retrieved')),
            _count(literature.get('pdf_available')),
            _value(literature.get('coverage', 'unknown')),
            _count(skills.get('count')),
            _value(skills.get('coverage', 'unknown')),
        ),
        tr(
            "Self-evolution completed {0} · effective evolution {1} · failed Attempts {2}",
            _count(evolution.get('completed')),
            _count(evolution.get('changed')),
            _count(evolution.get('failed')),
        ),
    ]


def _interaction_line(name: str, metrics: Mapping[str, object]) -> str:
    return (tr(
        "{0}: started {1} · active {2} · completed {3} · aborted {4} · failed {5}",
        name,
        _count(metrics.get('started')),
        _count(metrics.get('active')),
        _count(metrics.get('completed')),
        _count(metrics.get('aborted')),
        _count(metrics.get('failed')),
    ))


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
    return " + ".join(tr(_PHASE_LABELS.get(part, part.replace("_", " "))) for part in parts)


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
        tr("{0} revisions in cycle {1}", component.cycle_revisions, component.current_cycle)
    )
    if component.revision_warning == "general":
        text = tr("⚠️ repeated revisions without convergence: {0}; cause unverified", revisions)
    elif component.revision_warning == "severe":
        text = (
            tr(
                "🛑 repeated revisions without convergence: {0}; cause unverified; this lineage "
                "self-terminates after 30 single-point failures",
                revisions,
            )
        )
    else:
        text = (
            tr("⛔ revision circuit breaker tripped: {0}; further revisions and cycles are suppressed", revisions)
        )
    return f"[b {color}]{escape(text)}[/]"


def _due_label(due_at: float | None, observed_at: float) -> str:
    if due_at is None:
        return tr("not scheduled")
    delta = due_at - observed_at
    if abs(delta) < 1:
        return tr("due now")
    if delta < 0:
        return tr("overdue by {0}s", f'{abs(delta):.0f}')
    return tr("due in {0}s", f'{delta:.0f}')


def _has_host_status(snapshot: MonitorSnapshot) -> bool:
    return snapshot.status_source.endswith("+resident-host-status")


def _agent_usage_lines(status: Mapping[str, object], accounting=None) -> tuple[str, ...]:
    # Keep the token icon compatible with tmux 3.0: it counts the newer coin
    # emoji as one cell while Rich reserves two, shifting the panel borders.
    accounting = accounting or {}
    extra = accounting.get("observed_unsettled_tokens", 0)
    observed_note = (tr(" · +≥{0} observed, unsettled", _compact(extra)) if extra else "")
    usage = status.get("agent_usage")
    if not isinstance(usage, Mapping):
        accounting = accounting or {}
        return (tr(
            "🔢 ≥{0} tok{1} · res {2}",
            _compact(accounting.get('reported_tokens')),
            observed_note,
            _compact(accounting.get('reserved_tokens')),
        ),
            tr(
                "Closed ?{0} · tok ?{1} · limits: Host STATUS unavailable",
                _count(accounting.get('unknown_closed_attempts')),
                _count(accounting.get('unavailable_token_attempts')),
            ))
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
    token_limit_label = tr("Tok ref") if modes == ("observe",) else tr("Tok limit")
    token_limit = tokens.get("limit")
    turn_limit = turns.get("limit")
    lines = [tr(
        "🔢 ≥{0} tok{1} · {2} turns",
        _compact(observed.get('tokens')),
        observed_note,
        _compact(observed.get('turns')),
    ),
        _limit_line(token_limit_label, tokens.get('charged_or_reserved'), token_limit, known='limit' in tokens),
        _limit_line("Turns", turns.get('charged_or_reserved'), turn_limit, known='limit' in turns),
        tr(
            "Res ≤{0} tok ({1} open) · closed ?{2} ≤{3} tok",
            _compact(open_declared.get('tokens')),
            _count(open_declared.get('attempts')),
            _count(usage.get('unobserved_closed_attempts')),
            _compact(closed_unknown.get('tokens')),
        )]
    # Limits above retain overage percentages. Only surface extra policy detail
    # when it has something to explain; the legend defines the admission bars.
    if enforcement.get('observed_overage_attempts') or usage.get('budget_rejected_attempts'):
        lines.append(tr(
            "{0}: call overages {1} (+{2} tok) · rejected {3}",
            _value(mode),
            _count(enforcement.get('observed_overage_attempts')),
            _compact(enforcement.get('overage_tokens')),
            _count(usage.get('budget_rejected_attempts')),
        ))
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
            source = tr("[b #70e1c1]READ-ONLY / HOST STATUS[/]")
            note = tr("Host answered this poll · not continuous liveness")
        else:
            source = tr("[b #70e1c1]READ-ONLY / OFFLINE[/]")
            note = tr("polling observation · not a receipt or Host-liveness signal")
        self.update(
            tr("{0}  [b]SNAPSHOT[/] {1}s old  [dim]{2}[/dim]", source, age, note)
        )

    def show_failure(self, exc: Exception, *, has_snapshot: bool) -> None:
        retained = (
            tr("showing last successful snapshot")
            if has_snapshot
            else tr("no successful snapshot available")
        )
        self.add_class("stale")
        self.update(
            tr("[b #ffb86b]STALE · READ-ONLY[/]  refresh failed: {0}  [dim]{1}[/dim]", _value(exc), retained)
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
                        tr("Copy URL via tmux")
                        if self._inside_tmux
                        else tr("Copy browser URL")
                    )
                    yield Button(label, id="copy-web-url")
                yield Button(tr("Close"), id="close-web-access", variant="primary")

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
                    tr(
                        "Clipboard request sent through tmux; paste to verify. If empty, use Shift+drag and the "
                        "SSH client's Copy action."
                    ),
                    timeout=10,
                )
            elif transport == "terminal":
                self.app.notify(
                    tr(
                        "Clipboard request sent to the terminal; paste to verify. If empty, use terminal text "
                        "selection."
                    ),
                    timeout=10,
                )
            else:
                self.app.notify(
                    tr(
                        "Clipboard request unavailable; use Shift+drag and the SSH client's Copy action on the "
                        "displayed URL."
                    ),
                    severity="warning",
                    timeout=10,
                )


def _oracle_observation_label(oracle):
    status = oracle.get('observation', {})
    if oracle.get('ok') is None:
        return '[b #f5c36b]' + (tr("AUDIT ERROR") if status.get('state') == 'error' else tr("AUDIT PENDING")) + '[/]'
    label = 'CLEAN' if oracle.get('ok') else 'ISSUES'
    if status:
        stamp = status.get('audited_at')
        label = 'LAST ' + label + (' · ' + time.strftime('%H:%M:%S', time.localtime(stamp)) if stamp is not None else '')
    return '[b ' + ('#70e1c1' if oracle.get('ok') else '#ff6b7a') + ']' + label + '[/]'


class SummaryPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        status, overview = snapshot.status, snapshot.overview or {}
        run_state = str(status.get("run_state") or "UNKNOWN")
        state_label = tr(_RUN_STATE_LABELS.get(run_state, run_state.replace("_", " ").title()))
        host = overview.get("host_observation", {})
        if run_state not in {"COMPLETED", "CANCELLED", "FAILED"}:
            if host.get("availability") == "absent":
                state_label += tr(" · HOST ABSENT (orphaned)")
            if status.get("dispatch_error"):
                state_label += tr(" · DISPATCH PAUSED (Host error)")
            provider_retry = status.get("provider_retry") or {}
            if provider_retry.get("pending") or provider_retry.get("state") == "recovering":
                state_label += " · API " + str(provider_retry.get("state", "cooldown")).replace("_", " ").upper()
        lines = [tr("[b #78dcff]RUN AT A GLANCE[/]  [dim]? legend · d details[/dim]"),
            f"[b]{_value(state_label)}[/]",
            tr("Goal: {0}", _goal_objective(snapshot)),
            tr(
                "Verdict {0} · {1}/{2} assessed",
                _assessment_badge(str(status.get('goal_verdict') or 'PENDING')),
                sum((item.assessment is not None for item in snapshot.hypotheses)),
                len(snapshot.hypotheses),
            )]
        failures = [item for item in snapshot.attempts if item.operational_status == "recovery_exhausted"]
        if failures:
            codes = list(dict.fromkeys(item.operational_reason or "agent_final_failure" for item in failures))
            label = tr("BUDGET LIMIT") if "error_max_budget_usd" in codes else tr("AGENT FINAL FAILURE")
            lines.insert(2, tr("[b #ff6b7a]⚠ {0} · {1} calls terminated · ", label, len(failures))
                + escape(", ".join(codes[-3:])) + tr(" · d inspect calls[/]"))
        activity, budget_lines, reliability = [], [], []
        if overview:
            totals = overview.get("totals", {})
            budget = snapshot.goal.get("research_contract", {}).get("budget", {})
            hours = budget.get("wallclock_hours")
            if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
                budget_lines.append(_limit_line(tr("⏱ Hours"), overview.get("elapsed_seconds", 0) / 3600
                    if overview.get("elapsed_seconds") is not None else None, hours))
            else:
                budget_lines.append(f"⏱ {_duration(overview.get('elapsed_seconds'))}")
            if overview.get("waiting_human_seconds"):
                budget_lines.append(tr("Human wait {0} (included)", _duration(overview['waiting_human_seconds'])))
            experiments = totals.get("experiments", {}).get("total")
            activity.append(tr(
                "🧪 Exp {0} · chains {1} / {2} open",
                _count(experiments),
                _count(totals.get('chains')),
                _count(totals.get('active_chains')),
            ))
            if budget.get("max_experiments") is not None:
                budget_lines.append(_limit_line(tr("🧪 Exp"), experiments, budget['max_experiments']))
            reliability.extend(_reliability_lines(overview.get("reliability")))
            retry = overview.get("reliability", {}).get("costs", {})
            if retry.get("retry_attempts") != 0 or retry.get("retry_unknown_nodes"):
                reliability.append(tr(
                    "🔁 Retry ≥{0} tok · res {1} · closed ?{2}",
                    _compact(retry.get('retry_reported_tokens')),
                    _compact(retry.get('retry_reserved_tokens')),
                    _count(retry.get('retry_unknown_closed_attempts')),
                ))
            activity.append(tr(
                "📚 Lit {0} / PDF {1} · Skills {2} · EVO {3}",
                _count(totals.get('literature', {}).get('retrieved')),
                _count(totals.get('literature', {}).get('pdf_available')),
                _count(totals.get('skills', {}).get('count')),
                _count(totals.get('evolutions', {}).get('changed')),
            ))
            if totals.get('leader', {}).get('started') or totals.get('debate', {}).get('started'):
                activity.append(tr(
                    "Leader {0}/{1} · Debate {2}/{3}",
                    _count(totals.get('leader', {}).get('completed')),
                    _count(totals.get('leader', {}).get('started')),
                    _count(totals.get('debate', {}).get('completed')),
                    _count(totals.get('debate', {}).get('started')),
                ))
        budget_lines.extend(_agent_usage_lines(status, overview.get("accounting")))
        health = [f"OPEN {_count(status.get('open_attempts'))} · Oracle {_oracle_observation_label(snapshot.oracle)}"]
        pending = sum(item.get("state") == "OPEN" for item in status.get("grounding_dialogue", []))
        if pending:
            health.append(tr("[yellow]Grounding: {0} questions pending · w Web[/]", pending))
        for process in (status.get("process_activity") or [])[:5]:
            alive = "alive" if process.get("process_alive") is True else "exited" if process.get("process_alive") is False else tr(
                "liveness unknown"
            )
            grant = (process.get("resource_grant") or {}).get("kind", tr("resource unknown"))
            log_bytes = sum(row.get("bytes", 0) for row in process.get("logs", []))
            health.append(escape(tr(
                "Process {0}: {1} · {2} · logs {3} bytes · scientific progress unknown",
                process['experiment_id'],
                grant,
                alive,
                log_bytes,
            )))
        blocks = status.get("resource_blocked") or []
        if blocks: health.append(tr("[yellow]GPU infrastructure blocked · ") + escape(", ".join(str(b["code"]) for b in blocks)) + tr(
            " · g GPU recheck[/]"
        ))
        provider_retry = status.get("provider_retry") or {}
        if provider_retry.get("pending") or provider_retry.get("state") == "recovering":
            from .provider_observation import provider_status_text
            health.append("[yellow]" + escape(provider_status_text(provider_retry, language=self.app.ui_language)) + tr(
                " · p API recovery[/]"
            ))
        literature = status.get("literature_retry") or {}
        if literature.get("state") in {"cooldown", "waiting_operator", "recovering"}:
            health.append(escape(tr(
                "OpenAlex: {0} · {1} · next retry {2} · l Literature retry",
                literature['state'],
                literature.get('reason'),
                _service_time(literature.get('next_retry_at')),
            )))
        notifications = status.get("notifications")
        if not notifications or notifications.get("state") == "unavailable":
            health.append(tr("Notifications: delivery status unknown"))
        elif notifications.get("error"):
            health.append(escape(tr("Notifications: ") + notifications["error"]))
        else:
            for sink, item in notifications.get("sinks", {}).items():
                if sink == "log": continue
                failure = item.get("failure") or {}
                health.append(escape(tr(
                    "{0}: {1} · pending {2} · last success {3} · {4} · next retry {5} · n Telegram retry",
                    sink,
                    item.get('state', 'unverified'),
                    item.get('pending', '?'),
                    _service_time(item.get('last_success_at')),
                    failure.get('code', ''),
                    _service_time(item.get('next_retry_at')),
                )))
        literature_access = status.get("literature_access") or {}
        if literature_access:
            from .literature_access_tui import access_summary
            health.append(escape(tr("Institutional access: ") + access_summary(status) + tr(" · i Status")))
        for title, section in ((tr("RESEARCH ACTIVITY"), activity), (tr("BUDGET & TOKENS"), budget_lines),
                (tr("EXECUTION RELIABILITY"), reliability), (tr("RUN HEALTH"), health)):
            if section:
                lines.extend(("", f"[#24566a]──[/] [b #9ab0bf]{title}[/]", *section))
        self.update("\n".join(lines))


class ExpertsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot, role_filter: str = "") -> None:
        lines = []
        cards = []
        overview = snapshot.overview or {}
        for key, title in (("global_tasks", tr("GLOBAL TASKS")), ("unknown_tasks", tr("OWNERSHIP UNKNOWN"))):
            group = overview.get(key, {})
            if not group.get("stages"):
                continue
            usage = group.get("accounting", {})
            lines.append(tr(
                "[b #78dcff]{0}[/] · ≥{1} tok · res {2} · d details / w history",
                title,
                _compact(usage.get('reported_tokens')),
                _compact(usage.get('reserved_tokens')),
            ))
            for stage in group.get("activity", []):
                lines.append(f"  {_value(stage.get('node'))} · {_stage_badge(stage, overview.get('host_observation', {}).get('availability', 'unconfirmed'))} "
                             f"· {_duration(stage.get('elapsed_seconds'))}")
        sharing = overview.get("grounding_sharing", {})
        if sharing.get("state") == "accepted":
            supplied = sum(item.get("provided", False) for item in sharing.get("experts", []))
            lines.append(tr(
                "Grounding accepted · supplied to {0}/{1} experts' task inputs",
                supplied,
                len(sharing.get('experts', [])),
            ))
        elif sharing.get("state") == "waiting_human":
            lines.append(tr("[yellow]Grounding awaiting clarification · h decisions[/]"))
        lines.append(tr("\n[b #78dcff]EXPERT LANES[/]  [dim]current work · ? legend[/dim]"))
        host = overview.get("host_observation", {}).get("availability", "unconfirmed")
        if _has_host_status(snapshot):
            host = "responded"
        if host != "responded":
            lines.append(tr("[yellow]Host {0} · OPEN? = unconfirmed activity[/]", _value(host)))
        experts = overview.get("experts", [])
        if not experts:
            for expert in snapshot.experts:
                if role_filter and expert.role != role_filter:
                    continue
                cards.append(tr(
                    "[b]{0}[/]\n{1} OPEN · {2} agent / {3} process",
                    _role(snapshot, expert.role),
                    len(expert.open_attempt_ids),
                    expert.agent_attempts,
                    expert.process_attempts,
                ))
            if not snapshot.experts:
                lines.append(tr("[dim]No frozen roster found[/dim]"))
        for expert in experts:
            role = expert["role"]
            if role_filter and role != role_filter:
                continue
            stages = expert.get("activity", expert.get("stages", []))
            opened = sum(stage.get("state") == "OPEN" for stage in stages)
            exhausted = any(stage.get("operational_status") == "recovery_exhausted" for stage in stages)
            label = f"{opened} OPEN" if opened else tr("automatic recovery ended") if exhausted else tr(
                "last recorded"
            ) if stages else tr(
                "not started"
            )
            card = [f"[b]{_role(snapshot, role)}[/] · C{_count(expert.get('cycle'))} · {label}",
                _lane_line(stages, host)]
            for stage in stages:
                node = dict(_LANE_STAGES).get(stage.get("node"), _short_text(stage.get("node"), limit=30))
                if stage.get("failure_stage") == "literature_retrieval":
                    node = tr("Literature preparation")
                scope = stage.get("chain") or stage.get("experiment_id") or stage.get("subkind") or "—"
                kind = " job" if stage.get("kind") == "PROCESS" else ""
                card.append(f"  {node}{kind} · {_short_text(scope, limit=28)} · {_stage_badge(stage, host)} "
                    f"· {_duration(stage.get('elapsed_seconds'))}")
                if stage.get("operational_status") == "recovery_exhausted":
                    card.append(tr(
                        "  {0} attempts · automatic recovery ended · d details",
                        _count(stage.get('attempts')),
                    ))
                if stage.get("failure_code"):
                    card.append(f"  {_value(stage['failure_code'])}")
            card.extend(("", *_reliability_lines(expert.get("reliability"))))
            accounting = expert.get("accounting", {})
            recovery = expert.get("context_recovery", {})
            if recovery.get("context_failure_attempts"):
                card.append(tr(
                    "Context recovery: {0} attempts · reported ≥{1} tokens · usage unknown {2}",
                    _count(recovery.get('recovery_attempts')),
                    _compact(recovery.get('recovery_reported_tokens')),
                    _count(recovery.get('recovery_unknown_token_attempts')),
                ))
            extra = accounting.get("observed_unsettled_tokens", 0)
            if extra:
                card.append(tr("+≥{0} observed tokens, unsettled", _compact(extra)))
            card.append(tr(
                "🔢 ≥{0} tok · res {1} · closed ?{2} · tok ?{3}",
                _compact(accounting.get('reported_tokens')),
                _compact(accounting.get('reserved_tokens')),
                _count(accounting.get('unknown_closed_attempts')),
                _count(accounting.get('unavailable_token_attempts')),
            ))
            cards.append("\n".join(card))
        if role_filter and not cards:
            lines.append(tr("[dim]No experts match this filter[/dim]"))
        rendered = [Text.from_markup("\n".join(lines))]
        for card in cards:
            rendered.extend((Text(""), Panel(Text.from_markup(card), border_style="#24566a", padding=(0, 1))))
        # Reuse the ordinary content equality check so polling retains scroll
        # and avoids rebuilding unchanged visuals. Rich sizes cards on resize.
        self.update("\n\n".join((*lines, *cards)), renderable=Group(*rendered))


class ComponentsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        lines = [tr("[b #78dcff]COMPONENT WORKFLOWS[/]  [dim]independent lanes[/dim]")]
        if not snapshot.components:
            lines.append(tr("[dim]No frozen components found[/dim]"))
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
                tr(
                    "   [b]{0}[/b]  [dim]durable state={1}[/dim]",
                    _value(_phase_label(component.phase)),
                    _value(component.state),
                )
            )
            lines.append(
                tr(
                    "   owner={0}  proposers={1}  blocked={2}",
                    _role(snapshot, component.owner_role),
                    ', '.join((_role(snapshot, role) for role in component.proposer_roles)),
                    blocked,
                )
            )
            lines.append(tr("   open={0}", _value(open_attempts)))
            warning = _revision_warning_badge(component)
            if warning is not None:
                lines.append(f"   {warning}")
        self.update("\n".join(lines))


class HypothesesPanel(CockpitPanel):
    def __init__(self, **kwargs) -> None:
        self.chain_tree = Tree(tr("Experts → chains → hypotheses / stages"), id="hypothesis-tree")
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
                rows.append((key, expert_key, tr(
                    "{0} · {1} · C{2} · {3} nodes",
                    _value(chain.get('chain')),
                    _value(chain.get('component_id')),
                    _count(chain.get('cycle')),
                    len(chain.get('nodes', [])),
                ), False))
                for node in chain.get("nodes", []):
                    placed.add(node["hypothesis_id"])
                    label = f"{_value(node['hypothesis_id'])} · {_assessment_badge(node.get('assessment'))}"
                    if node.get("pruned"):
                        label += " · [#ffb86b]PRUNED (control)[/]"
                    rows.append((("hypothesis", node["hypothesis_id"]), key, label, False))
                for stage in chain.get("stages", []):
                    rows.append((("stage", stage["attempt_id"]), key,
                        tr(
                            "{0}/{1} · {2} · {3} Attempts",
                            _value(stage.get('node')),
                            _value(stage.get('subkind')),
                            _attempt_badge(str(stage.get('terminal_reason') or stage.get('state'))),
                            _count(stage.get('attempts')),
                        ), False))
            unplaced = [item for item in hypotheses if item.proposer_role == role and item.hypothesis_id not in placed]
            if unplaced:
                unknown = ("unassigned", role)
                rows.append((unknown, expert_key, tr("Accepted nodes · chain evidence unavailable"), True))
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
        self.chain_tree.root.set_label(tr("Experts → chains → hypotheses / stages"))
        if snapshot.overview:
            totals = snapshot.overview.get("totals", {})
            self.update(tr("[b #78dcff]HYPOTHESIS CHAINS[/] · Left/Right expand · Web for claims/evidence\n")
                + (tr("No hypotheses proposed yet") if not snapshot.hypotheses else
                   tr(
                       "{0} accepted nodes · chain coverage {1}",
                       len(snapshot.hypotheses),
                       _value(totals.get('chain_coverage', 'unknown')),
                   )))
            self._update_tree(snapshot.overview, role_filter, snapshot.hypotheses)
            return
        assessed = sum(item.assessment is not None for item in snapshot.hypotheses)
        lines = [
            tr(
                "[b #78dcff]HYPOTHESES / ASSESSMENTS[/]  [dim]{0}/{1} assessed · latest 12[/dim]",
                assessed,
                len(snapshot.hypotheses),
            )
        ]
        if not snapshot.hypotheses:
            lines.append(tr("[dim]No hypotheses proposed yet[/dim]"))
        for hypothesis in snapshot.hypotheses[-12:]:
            lines.append(
                f"{_assessment_badge(hypothesis.assessment)}  "
                f"[b]{_value(hypothesis.hypothesis_id)}[/b]  "
                f"[dim]{_value(hypothesis.component_id)}[/dim]"
            )
            lines.append(
                tr(
                    "   {0} · method={1}",
                    _role(snapshot, hypothesis.proposer_role),
                    _value(hypothesis.method_family_id),
                )
            )
            lines.append(f"   {_short_text(hypothesis.claim, limit=220)}")
            if hypothesis.parent_hypothesis_id is not None:
                lines.append(
                    tr("   lineage={0} {1}", _value(hypothesis.relation), _value(hypothesis.parent_hypothesis_id))
                )
            if hypothesis.evidence_ids:
                lines.append(
                    tr("   evidence={0}", _value(', '.join(hypothesis.evidence_ids)))
                )
            elif hypothesis.assessment is not None:
                lines.append(tr("   [dim]assessment has no displayed evidence IDs[/dim]"))
        self.update("\n".join(lines))


class WorkflowsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        with_next_command = sum(
            item.next_command is not None for item in snapshot.workflows
        )
        lines = [
            tr(
                "[b #78dcff]NEXT DURABLE COMMANDS[/]  [dim]{0}/{1} expose a next command[/dim]",
                with_next_command,
                len(snapshot.workflows),
            )
        ]
        if not snapshot.workflows:
            lines.append(tr("[dim]No workflow heads found[/dim]"))
        ordered = sorted(
            snapshot.workflows,
            key=lambda item: (
                item.next_command is None,
                item.kind != tr("RUN"),
                item.component_id or item.workflow_id,
            ),
        )
        for workflow in ordered[:12]:
            subject = workflow.component_id or workflow.workflow_id
            command = workflow.next_command or tr("no command")
            lines.append(
                tr(
                    "[b]{0}[/b] {1}  [dim]{2} · rev {3}[/dim]",
                    _value(workflow.kind),
                    _value(subject),
                    _value(workflow.state),
                    workflow.revision,
                )
            )
            lines.append(
                tr("   next={0}  ·  {1}", _value(command), _value(_due_label(workflow.due_at, snapshot.observed_at)))
            )
        if len(ordered) > 12:
            lines.append(tr("[dim]… {0} more workflow heads[/dim]", len(ordered) - 12))
        open_rendezvous = sum(item.status != "RESOLVED" for item in snapshot.rendezvous)
        lines.extend((
            "",
            tr(
                "[b #78dcff]PEER RENDEZVOUS[/]  [dim]{0}/{1} unresolved · latest 6[/dim]",
                open_rendezvous,
                len(snapshot.rendezvous),
            ),
        ))
        if not snapshot.rendezvous:
            lines.append(tr("[dim]No typed peer rendezvous recorded[/dim]"))
        for rendezvous in snapshot.rendezvous[-6:]:
            target = rendezvous.target_role or tr("no target")
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
                responder = rendezvous.responder_role or tr("unknown responder")
                lines.append(
                    tr(
                        "   {0} by {1} · {2}",
                        _value(rendezvous.disposition),
                        _role(snapshot, responder),
                        _short_text(rendezvous.summary, limit=160),
                    )
                )
        waits = snapshot.status.get("resource_waits")
        lines.extend(("", tr("[b #78dcff]LIVE RESOURCE WAITS[/]")))
        binding = snapshot.status.get("gpu_pool")
        if isinstance(binding, Mapping):
            lines.append(tr("Shared GPU pool: {0}", _value(binding.get('socket_path'))))
            lines.append(tr("Namespace: {0}", _value(binding.get('namespace'))))
        elif "gpu_pool" in snapshot.status:
            lines.append(tr("[dim]Shared GPU pool: not configured for this run[/dim]"))
        if waits is None:
            lines.append(tr("[dim]Host STATUS unavailable[/dim]"))
        elif not waits:
            lines.append(tr("[dim]No resource wait reported by this Host poll[/dim]"))
        else:
            for wait in waits:
                if not isinstance(wait, Mapping):
                    continue
                lines.append(
                    f"[yellow]{_value(wait.get('experiment_id'))}[/yellow] · "
                    f"{_short_text(wait.get('message') or wait.get('reason'), limit=200)}"
                )
                planning = wait.get("resource_planning") or {}
                intent = planning.get("resource_intent") or {}
                if intent:
                    lines.append(tr(
                        "   GPU budget {0} MiB + reserve {1} MiB",
                        _value(intent.get('workload_memory_mib')),
                        _value(intent.get('reserve_memory_mib')),
                    ))
        self.update("\n".join(lines))


class AttemptsPanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        lines = [tr("[b #78dcff]ATTEMPT TELEMETRY[/]  [dim]latest 12 durable records[/dim]")]
        if not snapshot.attempts:
            lines.append(tr("[dim]No attempts recorded[/dim]"))
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
                tr(
                    "   {0}s · scope={1} · execution role={2} · phase={3} · attempt={4}#{5}",
                    f'{elapsed:.1f}',
                    _value(attempt.task_scope),
                    _value(attempt.role),
                    _value(_phase_label(attempt.phase)),
                    _value(attempt.attempt_id),
                    attempt.attempt_ordinal,
                )
            )
            if attempt.failure_code is not None or attempt.returncode is not None:
                lines.append(
                    tr(
                        "   [red]failure={0}[/red] · returncode={1}",
                        _value(attempt.failure_code),
                        _value(attempt.returncode),
                    )
                )
            if attempt.diagnostic_summary is not None:
                lines.append(
                    f"   [yellow]{_short_text(attempt.diagnostic_summary, limit=220)}[/yellow]"
                )
            if attempt.kind == "AGENT":
                context = attempt.context or {}
                if "current_request_exchange" in context:
                    lines.append(tr(
                        "   current request exchange={0} · provider input={1} · source={2}",
                        _value(context.get('current_request_exchange')),
                        _value(attempt.context_tokens),
                        _value(attempt.context_source),
                    ))
                    earlier = context.get("last_observed_exchange") != context.get("current_request_exchange")
                    lines.append(tr(
                        "   last measured input={0} · exchange={1}",
                        _value(context.get('last_observed_input_tokens')),
                        _value(context.get('last_observed_exchange')),
                    )
                        + (tr(" (earlier request)") if earlier and context.get("last_observed_input_tokens") is not None else ""))
                    lines.append(tr(
                        "   current request estimate={0} · source={1} · fits={2} · basis={3}",
                        _value(context.get('current_request_estimated_input_tokens')),
                        _value(context.get('current_request_estimate_source')),
                        _value(context.get('current_request_fits')),
                        _value(context.get('current_request_estimate_reason')),
                    ))
                else:
                    lines.append(tr(
                        "   last input={0} · source={1}",
                        _value(attempt.context_tokens),
                        _value(attempt.context_source),
                    ))
                    lines.append(tr(
                        "   estimated input={0} · source={1}",
                        _value(context.get('estimated_input_tokens')),
                        _value(context.get('estimate_source')),
                    ))
                lines.append(tr(
                    "   working limit={0} ({1}) · cumulative tokens={2}",
                    _value(attempt.context_window_tokens),
                    _value(attempt.context_window_source),
                    _value(attempt.cumulative_tokens),
                ))
                metrics = attempt.context_observation or {}
                if "native_compaction_count" in metrics:
                    lines.append(tr(
                        "   native compactions={0} · duration={1} ms · unknown durations={2} · coverage={3}",
                        _count(metrics.get('native_compaction_count')),
                        _count(metrics.get('native_compaction_duration_ms')),
                        _count(metrics.get('native_compaction_duration_unknown')),
                        _value(metrics.get('coverage', 'unavailable')),
                    ))
                    compact = metrics.get("last_native_compaction")
                    if compact:
                        lines.append(tr(
                            "   last native compaction input={0} → {1} · {2} ms",
                            _value(compact.get('pre_tokens')),
                            _value(compact.get('post_tokens')),
                            _value(compact.get('duration_ms')),
                        ))
                if metrics:
                    # Stream events can precede the transcript; read counts are
                    # independently observed and must not default to zero.
                    lines.append(tr(
                        "   successful Read={0} · exact frozen-input rereads={1} (shell reads excluded)",
                        _count(metrics.get('read_calls')),
                        _count(metrics.get('exact_reread_calls')),
                    ))
                lines.append(tr(
                    "   declared window={0} · cache read/create={1}/{2} · session scope={3}",
                    _value(context.get('declared_context_tokens')),
                    _value(context.get('cache_read_input_tokens')),
                    _value(context.get('cache_creation_input_tokens')),
                    _value(attempt.session_scope),
                ))
                if attempt.operational_reason:
                    lines.append(tr("   operational reason={0}", _value(attempt.operational_reason)))
                if attempt.operational_status:
                    lines.append(f"   {_value(attempt.operational_status)}" + (tr(
                        " · API retry does not reopen a terminal node"
                    ) if attempt.operational_status == "recovery_exhausted" else tr(
                        " · last recorded CLI status"
                    )))
            if attempt.stdout_path is not None or attempt.stderr_path is not None:
                lines.append(
                    tr("   logs: stdout={0} · stderr={1}", _value(attempt.stdout_path), _value(attempt.stderr_path))
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
                    tr(" [red]reserve pressure[/red]")
                    if device.get("pressure_exceeded")
                    else ""
                )
                lines.append(
                    tr(
                        "   gpu={0} min/latest-free={1}/{2} MiB util={3}%{4}",
                        _value(device.get('gpu_uuid')),
                        _value(device.get('minimum_free_memory_mib')),
                        _value(device.get('latest_free_memory_mib')),
                        _value(device.get('latest_utilization_percent')),
                        pressure,
                    )
                )
                owned = device.get("owned_memory")
                if isinstance(owned, Mapping):
                    lines.append(
                        tr(
                            "      own sampled latest/peak={0}/{1} MiB · coverage={2} · unknown samples={3}/{4}",
                            _value(owned.get('latest_sample_memory_mib')),
                            _value(owned.get('sampled_peak_memory_mib')),
                            _value(owned.get('coverage')),
                            _value(owned.get('unknown_sample_count')),
                            _value(owned.get('sample_count')),
                        )
                    )
        self.update("\n".join(lines))


class EvidencePanel(CockpitPanel):
    def update_snapshot(self, snapshot: MonitorSnapshot) -> None:
        lines = [tr("[b #78dcff]CANONICAL FACTS / ORACLE[/]")]
        status, overview = snapshot.status, snapshot.overview or {}
        lines.extend(_reliability_lines(overview.get("reliability"), details=True))
        lines.extend((tr(
            "[dim]TO timeout · CX cancelled · TE tool errors/calls (? traces). Correction-only "
            "tokens unavailable.[/dim]"
        ),
            tr(
                "Required assessed {0}/{1}",
                _count(status.get('required_assessment_count')),
                _count(status.get('required_component_count')),
            ),
            tr(
                "State-root OPEN {0} · backup {1}",
                snapshot.durable_open_attempts_state_root,
                _count(status.get('quiescent_for_backup')),
            ),
            tr("Observation: {0}", tr('Host STATUS') if _has_host_status(snapshot) else tr('offline state store'))))
        if overview:
            lines.extend(_metric_lines(overview.get("totals", {})))
            if overview.get("stage_accounting"):
                lines.append(tr("[b]Stage tokens (reported lower bounds; failed/recovery subsets may overlap)[/b]"))
                for stage in overview["stage_accounting"]:
                    usage, recovery = stage.get("accounting", {}), stage.get("context_recovery", {})
                    label = str(stage.get("node", "unknown")) + ("/" + stage["subkind"] if stage.get("subkind") else "")
                    lines.append(tr(
                        "{0}: reported {1} · failed {2} · recovery {3} · open observed {4} · reserved {5} · "
                        "unknown closed {6}",
                        _value(label),
                        _count(usage.get('reported_tokens')),
                        _count(usage.get('failed_reported_tokens')),
                        _count(recovery.get('recovery_reported_tokens')),
                        _count(usage.get('observed_unsettled_tokens')),
                        _count(usage.get('reserved_tokens')),
                        _count(usage.get('unknown_closed_attempts')),
                    ))
                lines.append(tr("[dim]Open observed usage is unsettled; reserved tokens are not consumption.[/dim]"))
            for name in ("leader", "debate"):
                lines.append(_interaction_line(name.title(), overview.get("totals", {}).get(name, {})))
            for notice in overview.get("notices", []):
                lines.append(_value(notice))
            last_error = overview.get("host_observation", {}).get("last_recorded_exception") or {}
            if last_error:
                lines.append(tr(
                    "Historical Host exception: {0} at {1} · diagnostics/host.jsonl",
                    _value(last_error.get('exception_type')),
                    _value(last_error.get('timestamp')),
                ))
        facts = "  ".join(f"{name}={count}" for name, count in snapshot.fact_counts.items())
        lines.append(_value(facts or tr("No facts recorded")))
        issues = snapshot.oracle.get("issues", ())
        lines.extend(
            (
                "",
                _oracle_observation_label(snapshot.oracle) + tr(" integrity/residue checks"),
                tr("[dim]Oracle CLEAN is not a scientific-success verdict.[/dim]"),
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


def _service_time(value):
    return time.strftime("%m-%d %H:%M:%S", time.localtime(value)) if type(value) in (int, float) else "unknown"


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
        Binding("g", "resource_recheck", "GPU recheck"),
        Binding("l", "literature_retry", "Literature retry", show=False),
        Binding("n", "notification_retry", "Telegram retry", show=False),
        Binding("i", "literature_access", "Institutional access"),
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
        height: auto;
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

    #language-button, #help-button, #support-help-button, #end-run-button {
        width: auto;
        height: 3;
        min-width: 8;
        padding: 0 1;
        margin: 0 1;
        border: none;
        background: #123f53;
        color: #a9efff;
    }

    #language-button { width: 6; min-width: 6; }
    #language-button:focus { text-style: bold; }
    #end-run-button { color: #ffb86b; }
    #monitor-exit-help { height: 1; padding: 0 1; color: #9ab0bf; }

    #human-decision-button { display: none; width: 1fr; height: 3; background: #513519; color: #ffdb9e; }

    #language-button:hover,
    #language-button:focus,
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
        self.ui_language = "en"
        self._last_refresh_error: Exception | None = None
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
                tr("[b #70e1c1]READ-ONLY / OFFLINE[/]  [dim]waiting for snapshot[/dim]"),
                id="freshness",
            )
            yield Button("CN", id="language-button", tooltip="Switch to Chinese")
            yield Button(tr("Help"), id="help-button", tooltip=tr("Window guide and execution reliability (?)"))
            yield Button(tr("Report Issue"), id="support-help-button", tooltip=tr(
                "Preview and export a support bundle (b)"
            ))
            yield Button(tr("End run"), id="end-run-button", tooltip=tr(
                "Review cancellation (x); q only leaves the monitor"
            ))
        yield Static(tr("q: leave monitor; Host and tasks continue. x: end run…"), markup=False, id="monitor-exit-help")
        legend = HelpLegend(id="help-legend")
        legend.update(tr(_HELP_TEXT))
        yield legend
        yield Button(tr("Waiting for your decision · press h"), id="human-decision-button")
        yield Select([(tr("All experts · run totals remain global"), "")], value="", allow_blank=False, id="expert-filter")
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
        self.sub_title = tr("{0} | read-only/offline | {1}", self.run_id, self.state_root)
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
        human_button.label = tr("{0} decision pending · Review and answer · h", len(pending))
        from .human_decision_tui import HumanDecisionScreen
        from .end_run_tui import EndRunScreen
        from .provider_resume_tui import ProviderResumeScreen
        from .literature_access_tui import LiteratureAccessScreen
        for screen in self.screen_stack:
            if isinstance(screen, (HumanDecisionScreen, EndRunScreen, ProviderResumeScreen, LiteratureAccessScreen)) and screen.is_mounted:
                screen.update_status(snapshot.status)
        selector = self.query_one("#expert-filter", Select)
        roles = tuple(item["role"] for item in snapshot.overview.get("experts", []))
        if roles != self._expert_roles:
            previous = selector.value
            selector.set_options([(tr("All experts · run totals remain global"), ""), *((role, role) for role in roles)])
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
            tr(
                "{0} | {1} | read-only/{2} | snapshot {3}s old",
                snapshot.run_id,
                snapshot.status.get('run_state'),
                observation,
                age,
            )
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "expert-filter" and self._last_snapshot is not None:
            self._render(self._last_snapshot)

    def action_toggle_diagnostics(self) -> None:
        self.screen.toggle_class("diagnostics")

    def action_toggle_language(self) -> None:
        """Redraw the retained observation; changing language sends no request."""
        self.ui_language = "zh-CN" if self.ui_language == "en" else "en"
        self.title = tr(self.TITLE)
        button = self.query_one("#language-button", Button)
        button.label = "EN" if self.ui_language == "zh-CN" else "CN"
        button.tooltip = tr("Switch to English" if self.ui_language == "zh-CN" else "Switch to Chinese")
        button.refresh(layout=True)
        for name, label, tooltip in (
            ("help-button", "Help", "Window guide and execution reliability (?)"),
            ("support-help-button", "Report Issue", "Preview and export a support bundle (b)"),
            ("end-run-button", "End run", "Review cancellation (x); q only leaves the monitor"),
        ):
            control = self.query_one(f"#{name}", Button)
            control.label, control.tooltip = tr(label), tr(tooltip)
            control.refresh(layout=True)
        for binding in self.BINDINGS:
            # Update the existing binding instead of appending a duplicate that
            # leaves the first (English) description authoritative in Footer.
            bindings = self._bindings.key_to_bindings.get(binding.key, [])
            bindings[:] = [replace(item, description=tr(binding.description))
                          if item.action == binding.action else item for item in bindings]
        self.refresh_bindings()
        self.query_one("#monitor-exit-help", Static).update(tr(
            "q: leave monitor; Host and tasks continue. x: end run…"
        ))
        self.query_one("#help-legend", HelpLegend).update(tr(_HELP_TEXT))
        with self.prevent(Select.Changed):
            selector = self.query_one("#expert-filter", Select)
            previous = selector.value
            selector.set_options([(tr("All experts · run totals remain global"), ""),
                                  *((role, role) for role in self._expert_roles)])
            selector.value = previous
            selector.mutate_reactive(Select.value)
            if self._last_snapshot is not None:
                self._render(self._last_snapshot)
        if self._last_refresh_error is not None:
            self.query_one("#freshness", FreshnessBanner).show_failure(
                self._last_refresh_error, has_snapshot=self._has_snapshot)
            self.sub_title = tr("{0} | STALE | read-only", self.run_id)
        elif self._last_snapshot is None:
            self.query_one("#freshness", FreshnessBanner).update(
                tr("[b #70e1c1]READ-ONLY / OFFLINE[/]  [dim]waiting for snapshot[/dim]"))

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
        self._last_refresh_error = message.error
        if message.error is not None:
            self.query_one("#freshness", FreshnessBanner).show_failure(
                message.error, has_snapshot=self._has_snapshot,
            )
            self.sub_title = tr("{0} | STALE | read-only", self.run_id)
            self.notify(
                tr("Refresh failed: {0}", message.error), severity="error", timeout=10,
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
                tr("Web observer unavailable: {0}. Press w for access help.", exc),
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
            port_help = tr("OS-selected port; the address may change after restart.\n\n") if self.web_port == 0 else ""
            clipboard_help = (
                tr(
                    "Clipboard: the button sends one explicit tmux passthrough request and does not change "
                    "tmux settings. Paste to verify; terminals do not acknowledge OSC 52 delivery. If the "
                    "SSH client blocks it, hold Shift while dragging over the URL and use the client's Copy "
                    "action. tmux copy-mode alone guarantees only a tmux paste buffer, not the workstation "
                    "clipboard.\n\n"
                )
                if inside_tmux
                else
                tr(
                    "Clipboard: the button sends an unconfirmed terminal request. Paste to verify; if the "
                    "terminal blocks it, select the URL and use the terminal's Copy action.\n\n"
                )
            )
            return (
                tr(
                    "[b #78dcff]WEB OBSERVER READY[/]  [dim]TUI-owned · loopback-only · "
                    "read-only[/dim]\n"
                    "\n"
                    "{0}Browser URL (contains the ephemeral token):\n"
                    "{1}\n"
                    "\n"
                    "Run this on your local workstation:\n"
                    "{2}\n"
                    "\n"
                    "Then open the exact Browser URL locally. The Web observer stays available while this "
                    "TUI is running and stops when the TUI exits.\n"
                    "\n"
                    "{3}If that local port is already occupied, use:\n"
                    "{4}\n"
                    "and replace only the port in the Browser URL with <local-port>.\n"
                    "\n"
                    "[dim]Do not share the token URL. The SSH tunnel must be started on the browser "
                    "workstation; the server cannot create that local tunnel for you.[/dim]",
                    port_help,
                    escape(url),
                    escape(tunnel),
                    clipboard_help,
                    escape(alternate),
                )
            )
        if not self.web_enabled:
            return (
                tr(
                    "[b #ffb86b]WEB OBSERVER DISABLED[/]\n"
                    "\n"
                    "This TUI was started with --no-web. Restart monitor without --no-web to couple the "
                    "loopback Web observer to the TUI, or run the standalone `hypoloom.cli web` command for "
                    "a Web-only view."
                )
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
            tr(
                "[b #ff6b7a]WEB OBSERVER UNAVAILABLE[/]\n"
                "\n"
                "Requested loopback port: {0}\n"
                "Start error: {1}\n\nThe TUI remains usable. Restart with an OS-selected free port:\n{2}",
                self.web_port,
                escape(self._web_error or tr('unknown error')),
                escape(restart),
            )
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
        content = (tr(
            "[b #78dcff]REPORT AN ISSUE[/]\n"
            "\n"
            "Open the browser link to preview evidence and download a ZIP. Nothing is "
            "uploaded.\n"
            "Default copies omit research bodies and local paths. Private attachments require "
            "explicit selection.\n"
            "\n"
            "Preview shows run state/revision, failure groups, evidence scope and omissions.\n"
            "The browser also accepts an optional issue description; CLI: --issue-actual, "
            "--issue-expected, --issue-steps, --issue-occurred-at.\n"
            "Descriptions are included in public copies too; review them before sharing.\n"
            "\n"
            "Offline preview command:\n"
            "{0}\n"
            "\n"
            "To save it, replace --preview with --archive --output "
            "/absolute/private/path/new-support.zip.\n\n",
            escape(command),
        )
            + self._web_access_content())
        if url is not None:
            content = content.replace(escape(self.web_access_url), escape(url))
        self.push_screen(WebAccessScreen(content, access_url=url, inside_tmux=bool(os.environ.get("TMUX"))))

    def action_end_run(self) -> None:
        from .end_run_tui import EndRunScreen
        if self._last_snapshot is None:
            self.notify(tr("Wait for a current snapshot, then press x."))
            return
        if not isinstance(self.screen, EndRunScreen):
            self.push_screen(EndRunScreen(self._last_snapshot.status, self._end_run_draft))

    def action_human_decision(self) -> None:
        from .human_decision_tui import HumanDecisionScreen
        if self._last_snapshot is None:
            self.notify(tr("Wait for a current snapshot, then press h."))
            return
        status = self._last_snapshot.status
        history = list(status.get("grounding_dialogue", []))
        pending = [item for item in history if item["state"] == "OPEN"]
        if not history:
            self.notify(tr("No human decisions have been recorded."))
            return
        decision = pending[0] if pending else history[-1]
        self.push_screen(HumanDecisionScreen(decision, status,
            self._human_drafts.setdefault(decision["decision_id"], {}),
            [item for item in history if item["decision_id"] != decision["decision_id"]]))

    def action_resource_recheck(self) -> None:
        from .resource_recheck_tui import ResourceRecheckScreen
        if (self._last_snapshot is None or self._last_snapshot.status.get("run_state") != "ACTIVE"
                or not self._last_snapshot.status.get("resource_blocked")):
            self.notify(tr("No blocked GPU node is visible. Refresh after repairing the pool."))
            return
        if not hasattr(self, "_resource_recheck_draft"): self._resource_recheck_draft = {}
        if not isinstance(self.screen, ResourceRecheckScreen):
            self.push_screen(ResourceRecheckScreen(self._last_snapshot.status, self._resource_recheck_draft))

    def action_literature_access(self) -> None:
        from .literature_access_tui import LiteratureAccessScreen
        if self._last_snapshot is None:
            self.notify(tr("Wait for current Host status, then press i."))
            return
        if not isinstance(self.screen, LiteratureAccessScreen):
            self.push_screen(LiteratureAccessScreen(self._last_snapshot.status))

    def action_provider_resume(self) -> None:
        from .provider_resume_tui import ProviderResumeScreen
        if self._last_snapshot is None:
            self.notify(tr("Wait for a current snapshot, then press p."))
            return
        status = self._last_snapshot.status
        if not status.get("provider_retry"):
            self.notify(tr("Provider cooldown state needs a live Host answer; the offline view cannot confirm it."))
            return
        if not isinstance(self.screen, ProviderResumeScreen):
            self.push_screen(ProviderResumeScreen(status, self._provider_resume_draft))

    def _dependency_retry(self, service):
        from .resource_recheck_tui import ResourceRecheckScreen
        status = {} if self._last_snapshot is None else self._last_snapshot.status
        available = status.get("literature_retry") if service == "openalex" else (status.get("notifications") or {}).get("sinks", {}).get("telegram")
        if not available or status.get("run_state") != "ACTIVE":
            self.notify(tr("A live active Host with this service is required.")); return
        if not hasattr(self, "_dependency_drafts"): self._dependency_drafts = {}
        if not isinstance(self.screen, ResourceRecheckScreen):
            self.push_screen(ResourceRecheckScreen(status, self._dependency_drafts.setdefault(service, {}), service=service))

    def action_literature_retry(self):
        self._dependency_retry("openalex")

    def action_notification_retry(self):
        self._dependency_retry("telegram")

    def action_provider_browser(self) -> None:
        server = self._web_server
        if server is None:
            self.notify(tr("Web is unavailable. Use API recovery in this TUI."))
            return
        url = server.provider_control_url()
        content = (tr(
            "[b #ffb86b]OPERATOR API LINK[/]\n"
            "This link permits API retry, connection switching, GPU recheck, literature and "
            "notification retry. Keep it private.\n\n"
        )
            + self._web_access_content().replace(escape(server.startup_url()), escape(url)))
        self.push_screen(WebAccessScreen(content, access_url=url, inside_tmux=bool(os.environ.get("TMUX"))))

    def action_human_browser(self) -> None:
        server = self._web_server
        if server is None:
            self.notify(tr("Web is unavailable. You can answer directly in this TUI."))
            return
        url = server.human_answer_url()
        content = (tr(
            "[b #ffb86b]OPERATOR ANSWER LINK[/]\n"
            "This link permits reviewed Grounding answers for this run. Keep it private.\n\n"
        )
            + self._web_access_content().replace(escape(server.startup_url()), escape(url)))
        self.push_screen(WebAccessScreen(content, access_url=url, inside_tmux=bool(os.environ.get("TMUX"))))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "language-button":
            self.action_toggle_language()
        elif event.button.id == "help-button":
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
