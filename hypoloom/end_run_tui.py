"""Explicit run cancellation through the existing Host control protocol."""
from __future__ import annotations

import asyncio
import uuid

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .control_protocol import SCHEMA_VERSION, ControlRequest, ProtocolError, request_over_socket
from .lifecycle import Machine, RunState, is_terminal


class EndRunScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Keep monitoring")]
    DEFAULT_CSS = """
    EndRunScreen { align: center middle; background: $background 70%; }
    #end-run-form { width: 90%; max-width: 100; height: 85%; max-height: 26; border: round #ffb86b; padding: 1 2; background: $surface; }
    #end-run-scroll { height: 1fr; }
    #end-run-form Static { height: auto; margin-bottom: 1; }
    #end-run-actions { height: auto; }
    #end-run-actions Button { width: 1fr; min-width: 8; }
    """

    def __init__(self, status: dict, draft: dict):
        super().__init__()
        self.status = dict(status)
        self.revision = status["run_revision"]
        self.draft = draft
        self.busy = False
        self.writable = True

    def compose(self) -> ComposeResult:
        with Vertical(id="end-run-form"):
            with VerticalScroll(id="end-run-scroll"):
                yield Static(f"End run\n{self.app.run_id}\nReviewed state: {self.status['run_state']}",
                    markup=False, id="end-run-target")
                yield Static("End this run cancels remaining tasks and preserves recorded results. "
                    "The Host then finishes cleanup and the final report; this can take time.", markup=False)
                yield Static("A managed Host exits once all its runs and tasks have finished. "
                    "A manually resident Host must be stopped separately.", markup=False)
                yield Static("To leave only the monitor, close this form and press q. "
                    "The Host and its tasks will continue.", markup=False)
                yield Static("Confirm only if you want to end this run.", markup=False, id="end-run-status")
            with Horizontal(id="end-run-actions"):
                yield Button("Keep monitoring", id="end-run-close")
                yield Button("Retry same request" if self.draft.get("pending") else "End this run",
                    id="end-run-confirm", variant="error")

    def on_mount(self) -> None:
        self.update_status(self.status)
        if self.draft.get("pending"):
            self._message("Previous Host reply was not confirmed. Retry sends the same request.")
        self.query_one("#end-run-close", Button).focus()

    def _message(self, text: str) -> None:
        self.query_one("#end-run-status", Static).update(text)

    def _buttons(self) -> None:
        self.query_one("#end-run-confirm", Button).disabled = self.busy or not self.writable

    def update_status(self, status: dict) -> None:
        self.status = dict(status)
        state = status["run_state"]
        if is_terminal(Machine.RUN_WORKFLOW, state):
            self.writable = False
            self.draft.pop("pending", None)
            self._message(f"Run is {state}. No further cancellation is needed. "
                "Host has not been confirmed stopped by this monitor.")
        elif state == RunState.STOPPING or self.draft.get("accepted_revision") is not None:
            self.writable = False
            self.draft.pop("pending", None)
            self._message("STOPPING: waiting for task cleanup and the final report. "
                "Host has not been confirmed stopped.")
        elif status["run_revision"] != self.revision and not self.draft.get("pending"):
            self.writable = False
            self._message("The run changed. Close and reopen this form to review its current state.")
        self._buttons()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "end-run-close":
            self.action_close()
            return
        if event.button.id != "end-run-confirm" or self.busy or not self.writable:
            return
        request = self.draft.setdefault("pending", ControlRequest(
            SCHEMA_VERSION, "end-ui-" + str(uuid.uuid4()), self.revision, "CANCEL",
            {"run_id": self.app.run_id, "cause": "operator_request_from_tui"}))
        self.busy = True
        self._buttons()
        self._message("Requesting cancellation from Host…")
        try:
            response = await asyncio.to_thread(request_over_socket,
                self.app.state_root / "control.sock", request, timeout=5)
        except (OSError, ProtocolError):
            self._message("Host reply not confirmed. The run may still be active or already stopping. "
                "Retry sends the same request; refresh to inspect progress.")
            self.query_one("#end-run-confirm", Button).label = "Retry same request"
        else:
            if response.status == "accepted":
                self.draft.pop("pending", None)
                self.draft["accepted_revision"] = response.revision
                self.writable = False
                self.update_status(self.status)
            elif response.status in {"conflict", "rejected"}:
                self.draft.pop("pending", None)
                self.writable = False
                self._message("Cancellation not accepted: "
                    + str((response.error or {}).get("message", response.status))
                    + ". Close and reopen to review current state.")
            else:
                self._message("Host reply did not confirm acceptance. Retry the same request or refresh to inspect progress.")
                self.query_one("#end-run-confirm", Button).label = "Retry same request"
        finally:
            self.busy = False
            self._buttons()
            self.app.action_refresh()

    def action_close(self) -> None:
        if self.busy:
            self._message("Waiting for the Host reply…")
        else:
            self.dismiss()
