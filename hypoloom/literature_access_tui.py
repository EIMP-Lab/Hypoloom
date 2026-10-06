"""Read-only publisher access observations from the existing Host status."""
from __future__ import annotations

from .ui_i18n import tr

from rich import box
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

LABELS = {
    "queued": "Queued", "checking": "Fetching", "login_required": "Login required",
    "verification_required": "Human verification required", "browser_unavailable": "Browser unavailable",
    "fetch_unavailable": "Fetch unavailable", "access_required": "Subscription required",
    "available": "Full text ready", "access_denied": "Access denied", "no_full_text": "No full text found",
    "identity_unconfirmed": "Paper identity unconfirmed", "failed": "Fetch or parsing failed",
    "skipped": "Skipped", "public_only": "Public/OA only", "disabled": "Institutional route not enabled", "queue_timeout": "Queue timed out",
    "acquisition_timeout": "Acquisition timed out", "manual_timeout": "Manual wait ended",
    "sample_verified": "Sample readable", "opened": "Preparation page opened", "unchecked": "Not checked",
    "not_checked": "Sample not verified", "tab_limit": "Browser tab limit reached",
}
REASON_LABELS = {
    "network_changed": "Network changed", "network_error": "Network error", "dns_failed": "DNS resolution failed",
    "connection_failed": "Connection failed", "navigation_timeout": "Navigation timed out",
    "redirect_unresolved": "Redirect unresolved", "http_server_error": "Publisher server error",
    "http_rate_limited": "Publisher rate limited", "request_blocked": "Request blocked", "http_error": "HTTP request failed",
    "pdf_response_not_pdf": "PDF endpoint returned other content", "cdp_endpoint_unavailable": "Browser control unavailable",
    "browser_target_closed": "Browser target closed", "article_identity_unconfirmed": "Article identity unconfirmed",
    "pdf_target_unconfirmed": "PDF target unconfirmed", "document_validation_failed": "Document validation failed",
}


def access_summary(status):
    access = status.get("literature_access")
    if not isinstance(access, dict) or "enabled" not in access:
        return tr("Institutional status unknown: no current Host observation.")
    if not access["enabled"]:
        return tr("Institutional browser not enabled for this run. Public/OA retrieval remains available.")
    groups = access.get("groups", [])
    automatic = sum(g.get("mode") == "auto" for g in groups)
    public = sum(g.get("mode") == "public_only" for g in groups)
    disabled = sum(g.get("mode") == "disabled" for g in groups)
    return tr("Institutional routes: {0} allowed · {1} off (public only) · {2} not enabled.", automatic, public, disabled)


def connection_summary(status):
    access = status.get("literature_access") or {}
    if not access.get("enabled"):
        return ""
    connection = access.get("connection") or {}
    mode = {"client": tr("Windows client"), "local": tr("Linux desktop"), "server": tr("Server browser"),
            "existing": tr("External browser")}.get(connection.get("mode"), tr("Configured browser"))
    state = connection.get("state")
    detail = {
        "allocated": tr("Connection allocated; browser availability unconfirmed"),
        "waiting_for_client": tr("Waiting for client connection"),
        "client_socket_present": tr("Client endpoint present; browser availability unconfirmed"),
        "ready": tr("Connection check passed"), "unavailable": tr("Connection unavailable"),
        "closed": tr("Run connection closed"), "inactive": tr("Run connection inactive"),
    }.get(state, tr("Live connection status unknown"))
    if status.get("run_state") in {"COMPLETED", "FAILED", "CANCELLED"}:
        return tr("Run ended. Queue counts are not download history.")
    return f"{mode} · {detail}"


def publisher_table(status):
    """Format live queue observations; no probes, commands or new stored counters."""
    table = Table(box=box.SIMPLE, expand=True, padding=(0, 1), pad_edge=False)
    table.add_column(tr("Publisher"), min_width=8, max_width=17, ratio=2, overflow="fold")
    table.add_column(tr("Institutional route"), min_width=8, max_width=20, ratio=2, overflow="fold")
    table.add_column(tr("Work\nA / R / G"), width=9, no_wrap=True)
    table.add_column(tr("Latest observation"), min_width=16, ratio=3, overflow="fold")
    access = status.get("literature_access") or {}
    if not access.get("enabled"):
        return table
    from .institutional_access import provider_catalog
    order = {p["provider"]: i for i, p in enumerate(provider_catalog())}
    for group in sorted(access.get("groups", []), key=lambda g: order.get(g["provider"], len(order))):
        papers = group.get("papers", [])
        # remaining_seconds comes from the Host's deadline/queue validator.
        # An expired login challenge is a gap, not a request for TUI intervention.
        pending = sum(p.get("remaining_seconds") is not None for p in papers)
        ready = sum(p.get("state") == "available" for p in papers)
        gaps = sum(p.get("remaining_seconds") is None and p.get("state") not in
                   {"available", "skipped", "public_only", "disabled"} for p in papers)
        observations = [p for p in papers if p.get("last_checked_at")]
        preparation = group.get("preparation") or {}
        if preparation.get("last_checked_at"):
            observations.append(preparation)
        latest = max(observations, key=lambda p: p["last_checked_at"], default=None)
        if latest:
            state = latest.get("state", "unchecked")
            reason = latest.get("reason_code")
            recent = tr(REASON_LABELS.get(reason, LABELS.get(state, state)))
        elif pending:
            recent = tr("Waiting for first result")
        elif papers:
            recent = tr("Observation time unknown")
        else:
            recent = tr("No institutional requests")
        mode = {"auto": tr("Allowed"), "public_only": tr("Off (public only)"), "disabled": tr("Not enabled")}.get(group.get("mode"), tr(
            "Policy unknown"
        ))
        history = access.get("history") or {}
        if history.get("state") == "observed":
            count = history.get("document_verified", {}).get(group["provider"], 0)
            recent += tr("; {0} verified in log", count) if count else tr("; no verification in log")
        table.add_row(Text(group["provider"]), Text(mode), Text(f"{pending} / {ready} / {gaps}"), Text(recent))
    return table


class LiteratureAccessScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Back to research")]
    DEFAULT_CSS = """
    LiteratureAccessScreen { align: center middle; background: $background 70%; }
    #literature-access-form { width: 95%; max-width: 120; height: 85%; border: round #63d7f2; padding: 1 2; background: $surface; }
    #literature-access-scroll { height: 1fr; }
    #literature-access-form Static { height: auto; }
    #literature-publishers { margin-top: 1; }
    #literature-access-footer { height: 3; align-horizontal: right; }
    """

    def __init__(self, status):
        super().__init__()
        self.status = status

    def compose(self) -> ComposeResult:
        with Vertical(id="literature-access-form"):
            with VerticalScroll(id="literature-access-scroll"):
                yield Static(tr("Institutional access · Read only · Refreshes with the main view"), markup=False)
                yield Static(tr(
                    "Public/OA retrieval is available for all publishers, including those not listed here. "
                    "Institutional settings below do not guarantee login, subscription or successful downloads."
                ), markup=False, id="literature-public-scope")
                yield Static("", markup=False, id="literature-summary")
                yield Static("", markup=False, id="literature-connection")
                yield Static("", id="literature-publishers")
                yield Static(tr(
                    "A / R / G = Active / Ready / Gaps in the institutional queue only; excludes public downloads "
                    "and history. An empty cache does not "
                    "mean no past success. Log verifications cover at most the latest 1 MiB; missing history "
                    "is unknown. Verification does not prove PDF archival or reading. Gaps do not block "
                    "other research."
                ),
                    markup=False, id="literature-scope")
            with Horizontal(id="literature-access-footer"):
                yield Button(tr("Close"), id="literature-close", variant="primary")

    def on_mount(self):
        self.update_status(self.status)

    def update_status(self, status):
        self.status = status
        self.query_one("#literature-summary", Static).update(access_summary(status))
        connection = self.query_one("#literature-connection", Static)
        connection.update(connection_summary(status))
        connection.display = bool((status.get("literature_access") or {}).get("enabled"))
        table = self.query_one("#literature-publishers", Static)
        table.update(publisher_table(status))
        table.display = bool((status.get("literature_access") or {}).get("groups"))

    def on_button_pressed(self, event):
        if event.button.id == "literature-close":
            event.stop()
            self.action_close()

    def action_close(self):
        self.dismiss()
