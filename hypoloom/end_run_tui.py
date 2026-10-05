"""Explicit run cancellation through the existing Host control protocol."""
from __future__ import annotations

from .ui_i18n import tr

import asyncio
import sqlite3
import uuid

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .control_protocol import SCHEMA_VERSION, ControlRequest, ProtocolError, request_over_socket
from .lifecycle import Machine, RunState, is_terminal
from .state_store import StateStore, StateStoreError, StateTransaction


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
        self.draft.pop("rejection", None)

    def compose(self) -> ComposeResult:
        with Vertical(id="end-run-form"):
            with VerticalScroll(id="end-run-scroll"):
                yield Static(tr("End run\n{0}\nReviewed state: {1}", self.app.run_id, self.status['run_state']),
                    markup=False, id="end-run-target")
                yield Static(tr(
                    "End this run cancels remaining tasks and preserves recorded results. The Host then "
                    "finishes cleanup and the final report; this can take time."
                ), markup=False)
                yield Static(tr(
                    "A managed Host exits once all its runs and tasks have finished. A manually resident "
                    "Host must be stopped separately."
                ), markup=False)
                yield Static(tr(
                    "To leave only the monitor, close this form and press q. The Host and its tasks will "
                    "continue."
                ), markup=False)
                yield Static(tr("Confirm only if you want to end this run."), markup=False, id="end-run-status")
            with Horizontal(id="end-run-actions"):
                yield Button(tr("Keep monitoring"), id="end-run-close")
                yield Button(tr("Retry same request") if self.draft.get("pending") else tr("End this run"),
                    id="end-run-confirm", variant="error")

    def on_mount(self) -> None:
        self.update_status(self.status)
        if self.draft.get("pending"):
            self._message(tr("Previous Host reply was not confirmed. Retry sends the same request."))
        self.query_one("#end-run-close", Button).focus()

    def _message(self, text: str) -> None:
        self.query_one("#end-run-status", Static).update(text)

    def _buttons(self) -> None:
        self.query_one("#end-run-confirm", Button).disabled = self.busy or not self.writable

    def _durable_acceptance(self, request: ControlRequest) -> int | None:
        """Return the accepted revision when the Host persisted this exact request.

        The command row and the RUN transition commit in one transaction, so a
        matching accepted command plus a stopping-or-terminal head proves the
        cancellation is durable even though its reply never arrived. The store
        is opened read-only and only briefly; any read failure leaves the
        idempotent retry path in place.
        """
        try:
            with StateStore(self.app.state_root).readonly_connection() as connection:
                transaction = StateTransaction(connection)
                command = transaction.get_command(request.request_id)
                if (
                    command is None
                    or command["run_id"] != self.app.run_id
                    or command["command_type"] != "CANCEL"
                    or command["response_status"] != "accepted"
                    or command["accepted_revision"] is None
                ):
                    return None
                accepted_revision = int(command["accepted_revision"])
                head = transaction.latest_transition(self.app.run_id)
                if (
                    head is None
                    or head["workflow_kind"] != tr("RUN")
                    or int(head["revision"]) < accepted_revision
                    or not (head["state"] == RunState.STOPPING
                        or is_terminal(Machine.RUN_WORKFLOW, head["state"]))
                ):
                    return None
                return accepted_revision
        except (OSError, sqlite3.Error, StateStoreError, ValueError):
            return None

    def update_status(self, status: dict) -> None:
        self.status = dict(status)
        state = status["run_state"]
        if is_terminal(Machine.RUN_WORKFLOW, state):
            self.writable = False
            self.draft.pop("pending", None)
            self._message(tr(
                "Run is {0}. No further cancellation is needed. Host has not been confirmed stopped by "
                "this monitor.",
                state,
            ))
        elif state == RunState.STOPPING or self.draft.get("accepted_revision") is not None:
            self.writable = False
            self.draft.pop("pending", None)
            self._message(tr(
                "STOPPING: waiting for task cleanup and the final report. Host has not been confirmed "
                "stopped."
            ))
        elif status["run_revision"] != self.revision and not self.draft.get("pending") and not self.draft.get("rejection"):
            self.writable = False
            self._message(tr("The run changed. Close and reopen this form to review its current state."))
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
        self._message(tr("Requesting cancellation from Host…"))
        try:
            response = await asyncio.to_thread(request_over_socket,
                self.app.state_root / "control.sock", request, timeout=30)
        except (OSError, ProtocolError):
            accepted_revision = await asyncio.to_thread(self._durable_acceptance, request)
            if accepted_revision is None:
                self._message(tr(
                    "Host reply not confirmed. The run may still be active or already stopping. Retry sends "
                    "the same request; refresh to inspect progress."
                ))
                self.query_one("#end-run-confirm", Button).label = tr("Retry same request")
            else:
                self.draft.pop("pending", None)
                self.draft["accepted_revision"] = accepted_revision
                self.writable = False
                self.update_status(self.status)
                if not is_terminal(Machine.RUN_WORKFLOW, self.status["run_state"]):
                    self._message(tr(
                        "The Host durably accepted this cancellation; its reply arrived after the timeout. "
                        "Waiting for task cleanup and the final report; the Host has not been confirmed stopped."
                    ))
        else:
            if response.status == "accepted":
                self.draft.pop("pending", None)
                self.draft["accepted_revision"] = response.revision
                self.writable = False
                self.update_status(self.status)
            elif response.status in {"conflict", "rejected"}:
                self.draft.pop("pending", None)
                self.writable = False
                self.draft["rejection"] = tr("Cancellation not accepted: ") + str((response.error or {}).get("message", response.status))
                self._message(self.draft["rejection"] + ". Close and reopen to review current state.")
            else:
                self._message(tr(
                    "Host reply did not confirm acceptance. Retry the same request or refresh to inspect "
                    "progress."
                ))
                self.query_one("#end-run-confirm", Button).label = tr("Retry same request")
        finally:
            self.busy = False
            self._buttons()
            self.app.action_refresh()

    def action_close(self) -> None:
        if self.busy:
            self._message(tr("Waiting for the Host reply…"))
        else:
            self.dismiss()
