"""Explicit provider-retry gate clear through the existing Host control protocol."""
from __future__ import annotations

from .ui_i18n import tr

import asyncio
import uuid

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .control_protocol import SCHEMA_VERSION, ControlRequest, ProtocolError, request_over_socket
from .lifecycle import Machine, RunState, is_terminal
from .provider_observation import provider_status_text


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
                yield Static(tr(
                    "Provider dispatch cooldown\n{0}\nReviewed state: {1}",
                    self.app.run_id,
                    self.status['run_state'],
                ),
                    markup=False, id="provider-resume-target")
                yield Static(provider_status_text(self.status.get("provider_retry"), language=getattr(self.app, "ui_language", "en")), markup=False, id="provider-current-status")
                yield Static(tr(
                    "Try the current API after access is restored, or select a Host-side 0600 JSON "
                    "connection file to switch API. The file may contain only env: API key/auth token and "
                    "base URL. The model stays fixed. Do not enter credentials here. A request allows one "
                    "recovery attempt; it does not prove access."
                ), markup=False)
                yield Input(placeholder=tr("Absolute Host-side connection file path"), id="provider-settings-path")
                yield Static("", markup=False, id="provider-resume-status")
            with Horizontal(id="provider-resume-actions"):
                yield Button(tr("Keep waiting"), id="provider-resume-close")
                yield Button(tr("Retry same request") if self.draft.get("pending") else tr("Try current API"),
                    id="provider-resume-confirm", variant="warning")
                yield Button(tr("Switch API"), id="provider-switch", variant="warning")
                yield Button(tr("Browser"), id="provider-browser")

    def on_mount(self) -> None:
        self.update_status(self.status)
        if self.draft.get("pending"):
            self._message(tr("Previous Host reply was not confirmed. Retry sends the same request."))
        self.query_one("#provider-resume-close", Button).focus()

    def _message(self, text: str) -> None:
        self.query_one("#provider-resume-status", Static).update(text)

    def _buttons(self) -> None:
        self.query_one("#provider-resume-confirm", Button).disabled = self.busy or not self.writable
        self.query_one("#provider-switch", Button).disabled = self.busy or not self.writable or bool(self.draft.get("pending"))
        self.query_one("#provider-settings-path", Input).disabled = self.busy or bool(self.draft.get("pending"))

    def update_status(self, status: dict) -> None:
        self.status = dict(status)
        self.query_one("#provider-current-status", Static).update(provider_status_text(status.get("provider_retry"), language=getattr(self.app, "ui_language", "en")))
        state = status["run_state"]
        if is_terminal(Machine.RUN_WORKFLOW, state) or state == RunState.STOPPING:
            self.writable = False
            self.draft.pop("pending", None)
            self._message(tr("Run is {0}. No provider retry clear is needed.", state))
        elif not (status.get("provider_retry") or {}).get("pending") and self.draft.get("accepted_revision") is not None:
            self.writable = False
            self.draft.pop("pending", None)
            self._message(tr("Recovery requested. Awaiting a successful API response; exhausted nodes are not reset."))
        elif not (status.get("provider_retry") or {}).get("pending"):
            self._message(tr("No provider cooldown is currently pending; confirming is harmless but unnecessary."))
        self._buttons()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "provider-resume-close":
            self.action_close()
            return
        if event.button.id == "provider-browser":
            self.app.action_provider_browser()
            return
        if event.button.id not in {"provider-resume-confirm", "provider-switch"} or self.busy or not self.writable:
            return
        payload = {"run_id": self.app.run_id, "reason": "operator"}
        if event.button.id == "provider-switch":
            path = self.query_one("#provider-settings-path", Input).value.strip()
            if not path.startswith("/"):
                self._message(tr("Select an absolute Host-side connection file path."))
                return
            payload["settings_path"] = path
        request = self.draft.setdefault("pending", ControlRequest(
            SCHEMA_VERSION, "provider-resume-ui-" + str(uuid.uuid4()), None, "PROVIDER_RETRY_CLEAR",
            payload))
        self.busy = True
        self._buttons()
        self._message(tr("Requesting cooldown clear from Host…"))
        try:
            response = await asyncio.to_thread(request_over_socket,
                self.app.state_root / "control.sock", request, timeout=5)
        except (OSError, ProtocolError):
            self._message(tr(
                "Host reply not confirmed. The cooldown may still be active. Retry sends the same "
                "request; refresh to inspect progress."
            ))
            self.query_one("#provider-resume-confirm", Button).label = tr("Retry same request")
        else:
            if response.status == "accepted":
                self.draft.pop("pending", None)
                self.draft["accepted_revision"] = response.revision
                self.writable = False
                self._message(tr(
                    "Recovery requested. Awaiting a successful API response; exhausted nodes are not reset."
                ))
            elif response.status in {"conflict", "rejected"}:
                self.draft.pop("pending", None)
                self.writable = False
                self._message(tr("Clear not accepted: ")
                    + str((response.error or {}).get("message", response.status))
                    + ". Close and reopen to review current state.")
            else:
                self._message(tr(
                    "Host reply did not confirm acceptance. Retry the same request or refresh to inspect "
                    "progress."
                ))
                self.query_one("#provider-resume-confirm", Button).label = tr("Retry same request")
        finally:
            self.busy = False
            self._buttons()
            self.app.action_refresh()

    def action_close(self) -> None:
        if self.busy:
            self._message(tr("Waiting for the Host reply…"))
        else:
            self.dismiss()
