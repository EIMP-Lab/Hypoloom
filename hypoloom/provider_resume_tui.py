"""Explicit provider-retry gate clear through the existing Host control protocol."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .control_protocol import SCHEMA_VERSION, ControlRequest, ProtocolError, request_over_socket
from .lifecycle import Machine, RunState, is_terminal


def _until_text(status: dict) -> str:
    retry = status.get("provider_retry") or {}
    pending = [item for item in retry.get("gates", []) if item.get("pending")]
    if not pending:
        return "unknown"
    latest = max(float(item["until"]) for item in pending)
    return datetime.fromtimestamp(latest, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class ProviderResumeScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Keep waiting")]
    DEFAULT_CSS = """
    ProviderResumeScreen { align: center middle; background: $background 70%; }
    #provider-resume-form { width: 90%; max-width: 100; height: 85%; max-height: 26; border: round #ffb86b; padding: 1 2; background: $surface; }
    #provider-resume-scroll { height: 1fr; }
    #provider-resume-form Static { height: auto; margin-bottom: 1; }
    #provider-resume-actions { height: auto; }
    #provider-resume-actions Button { width: 1fr; min-width: 8; }
    """

    def __init__(self, status: dict, draft: dict):
        super().__init__()
        self.status = dict(status)
        self.draft = draft
        self.busy = False
        self.writable = True

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-resume-form"):
            with VerticalScroll(id="provider-resume-scroll"):
                yield Static(f"Provider dispatch cooldown\n{self.app.run_id}\nReviewed state: {self.status['run_state']}",
                    markup=False, id="provider-resume-target")
                yield Static("The provider rejected authentication (HTTP 401/403), so new Agent dispatch "
                    f"is paused until {_until_text(self.status)}. A rolling quota window or a rotated key "
                    "both look like this; the pause protects retry budgets from a failing provider.", markup=False)
                yield Static("Confirm only after you restored access (for example a fresh key or a reset "
                    "quota window). Confirming clears the pause immediately; without it, dispatch resumes "
                    "automatically when the cooldown ends. A still-broken credential simply re-enters cooldown.",
                    markup=False)
                yield Static("", markup=False, id="provider-resume-status")
            with Horizontal(id="provider-resume-actions"):
                yield Button("Keep waiting", id="provider-resume-close")
                yield Button("Retry same request" if self.draft.get("pending") else "Credential restored — resume now",
                    id="provider-resume-confirm", variant="warning")

    def on_mount(self) -> None:
        self.update_status(self.status)
        if self.draft.get("pending"):
            self._message("Previous Host reply was not confirmed. Retry sends the same request.")
        self.query_one("#provider-resume-close", Button).focus()

    def _message(self, text: str) -> None:
        self.query_one("#provider-resume-status", Static).update(text)

    def _buttons(self) -> None:
        self.query_one("#provider-resume-confirm", Button).disabled = self.busy or not self.writable

    def update_status(self, status: dict) -> None:
        self.status = dict(status)
        state = status["run_state"]
        if is_terminal(Machine.RUN_WORKFLOW, state) or state == RunState.STOPPING:
            self.writable = False
            self.draft.pop("pending", None)
            self._message(f"Run is {state}. No provider retry clear is needed.")
        elif not (status.get("provider_retry") or {}).get("pending") and self.draft.get("accepted_revision") is not None:
            self.writable = False
            self.draft.pop("pending", None)
            self._message("Cooldown cleared. Dispatch resumes on the next Host tick.")
        elif not (status.get("provider_retry") or {}).get("pending"):
            self._message("No provider cooldown is currently pending; confirming is harmless but unnecessary.")
        self._buttons()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "provider-resume-close":
            self.action_close()
            return
        if event.button.id != "provider-resume-confirm" or self.busy or not self.writable:
            return
        request = self.draft.setdefault("pending", ControlRequest(
            SCHEMA_VERSION, "provider-resume-ui-" + str(uuid.uuid4()), None, "PROVIDER_RETRY_CLEAR",
            {"run_id": self.app.run_id, "reason": "operator"}))
        self.busy = True
        self._buttons()
        self._message("Requesting cooldown clear from Host…")
        try:
            response = await asyncio.to_thread(request_over_socket,
                self.app.state_root / "control.sock", request, timeout=5)
        except (OSError, ProtocolError):
            self._message("Host reply not confirmed. The cooldown may still be active. "
                "Retry sends the same request; refresh to inspect progress.")
            self.query_one("#provider-resume-confirm", Button).label = "Retry same request"
        else:
            if response.status == "accepted":
                self.draft.pop("pending", None)
                self.draft["accepted_revision"] = response.revision
                self.writable = False
                self._message("Cooldown cleared. Dispatch resumes on the next Host tick.")
            elif response.status in {"conflict", "rejected"}:
                self.draft.pop("pending", None)
                self.writable = False
                self._message("Clear not accepted: "
                    + str((response.error or {}).get("message", response.status))
                    + ". Close and reopen to review current state.")
            else:
                self._message("Host reply did not confirm acceptance. Retry the same request or refresh to inspect progress.")
                self.query_one("#provider-resume-confirm", Button).label = "Retry same request"
        finally:
            self.busy = False
            self._buttons()
            self.app.action_refresh()

    def action_close(self) -> None:
        if self.busy:
            self._message("Waiting for the Host reply…")
        else:
            self.dismiss()
