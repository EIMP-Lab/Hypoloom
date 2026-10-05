"""An explicit operator answer form; polling and drafts never submit a decision."""
from __future__ import annotations

from .ui_i18n import tr

import asyncio
import uuid

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from .control_protocol import ProtocolError
from .human_answers import submit_answer


class HumanDecisionScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    HumanDecisionScreen { align: center middle; background: $background 70%; }
    #human-form { width: 90%; max-width: 110; height: 92%; border: round #ffb86b; padding: 1 2; background: $surface; }
    #human-form-scroll { height: 1fr; }
    #human-form Static { height: auto; margin-bottom: 1; }
    #human-form TextArea { height: 9; min-height: 5; margin-bottom: 1; }
    #human-form .human-option { width: 1fr; margin-bottom: 1; }
    #human-actions { height: auto; layout: horizontal; }
    #human-actions Button { min-width: 8; width: 1fr; }
    #human-history { color: $text-muted; }
    """

    def __init__(self, decision, status, draft, history):
        super().__init__()
        self.decision = dict(decision)
        self.revision = status["run_revision"]
        self.draft = draft
        self.history = history
        self.busy = False
        self.reviewed = bool(draft.get("pending"))
        self.writable = decision["state"] == "OPEN" and status["run_state"] == "WAITING_HUMAN"

    def compose(self) -> ComposeResult:
        with Vertical(id="human-form"):
            with VerticalScroll(id="human-form-scroll"):
                yield Static(tr("Human decision · question {0}\n{1}", self.decision['ordinal'], self.app.run_id), markup=False)
                yield Static(self.decision["question"], markup=False, id="human-question")
                yield Static(self.decision["rationale"], markup=False)
                for index, option in enumerate(self.decision.get("options", [])):
                    yield Static(option, markup=False)
                    yield Button(tr("Use option {0}", index + 1), id=f"human-option-{index}", classes="human-option", disabled=not self.writable or self.reviewed)
                yield Static(tr("Your answer · choose an option or write your own; nothing is preselected."), markup=False)
                yield TextArea(self.draft.get("text", self.decision.get("answer") or ""), id="human-answer", read_only=not self.writable or self.reviewed)
                yield Static(tr("Review your answer before submitting. Submission continues this run."), markup=False, id="human-form-status")
                if self.history:
                    yield Static(tr("Previous decisions\n\n") + "\n\n".join(
                        f"{item['ordinal']}. {item['question']}\n{item.get('answer') or item.get('resolution') or item['state']}"
                        for item in self.history), markup=False, id="human-history")
            with Horizontal(id="human-actions"):
                yield Button(tr("Retry same answer") if self.draft.get("pending") else tr("Review answer"), id="human-submit", variant="primary", disabled=not self.writable)
                yield Button(tr("Edit"), id="human-edit", disabled=True)
                yield Button(tr("Browser"), id="human-browser", disabled=self.app._web_server is None)
                yield Button(tr("Close"), id="human-close")

    def on_mount(self):
        self._buttons()
        if self.draft.get("pending"):
            self._status(tr(
                "Previous delivery was not confirmed. Retry the same answer or inspect the recorded "
                "history."
            ))

    def _status(self, message):
        self.query_one("#human-form-status", Static).update(message)

    def _buttons(self):
        pending = bool(self.draft.get("pending"))
        self.query_one("#human-answer", TextArea).read_only = self.busy or self.reviewed or not self.writable
        self.query_one("#human-submit", Button).disabled = self.busy or not self.writable or not self.query_one("#human-answer", TextArea).text.strip()
        self.query_one("#human-edit", Button).disabled = self.busy or pending or not self.writable or not self.reviewed
        for button in self.query(".human-option"):
            button.disabled = self.busy or pending or not self.writable or self.reviewed

    def on_text_area_changed(self, event):
        self.draft["text"] = event.text_area.text
        self._buttons()

    def update_status(self, status):
        current = next((item for item in status.get("grounding_dialogue", []) if item["decision_id"] == self.decision["decision_id"]), None)
        if current is None or current["state"] != "OPEN" or status.get("run_state") != "WAITING_HUMAN":
            self.writable = False
            self.draft.pop("pending", None)
            self._status(tr("Decision is no longer pending. Recorded answer: ") + str((current or {}).get("answer") or (current or {}).get("resolution") or "unavailable"))
        elif status.get("run_revision") != self.revision and not self.draft.get("pending"):
            self.writable = False
            self._status(tr(
                "The run changed. Close and reopen this form to review the current decision. Your draft "
                "is retained."
            ))
        self._buttons()

    async def on_button_pressed(self, event):
        event.stop()
        selected = event.button.id or ""
        if selected == "human-close":
            self.action_close()
        elif selected == "human-browser":
            self.app.action_human_browser()
        elif selected.startswith("human-option-") and self.writable and not self.reviewed and not self.busy:
            option = self.decision["options"][int(selected.rsplit("-", 1)[1])]
            editor = self.query_one("#human-answer", TextArea)
            editor.load_text(option)
            editor.focus()
            editor.scroll_visible()
        elif selected == "human-edit" and not self.busy and not self.draft.get("pending"):
            self.reviewed = False
            self.query_one("#human-submit", Button).label = tr("Review answer")
            self._status(tr("Edit your answer, then review it again."))
            self._buttons()
        elif selected == "human-submit" and self.writable and not self.busy:
            text = self.query_one("#human-answer", TextArea).text
            if not text.strip():
                return
            if not self.reviewed:
                self.reviewed = True
                self.query_one("#human-submit", Button).label = tr("Submit answer")
                self._status(tr(
                    "Review the answer above. Submit answer records it and continues this run; Edit returns "
                    "to the draft."
                ))
                self._buttons()
                self.query_one("#human-answer", TextArea).scroll_visible()
                return
            values = self.draft.setdefault("pending", {"decision_id": self.decision["decision_id"], "answer": text,
                "expected_revision": self.revision, "request_id": "human-ui-" + str(uuid.uuid4())})
            self.busy = True
            self._buttons()
            self._status(tr("Submitting to Host…"))
            try:
                response = await asyncio.to_thread(submit_answer, self.app.state_root, self.app.run_id, values)
            except (OSError, ProtocolError):
                self._status(tr(
                    "Host reply not confirmed. Retry sends the same answer safely; no Host is started "
                    "automatically."
                ))
                self.query_one("#human-submit", Button).label = tr("Retry same answer")
            except ValueError as exc:
                self.draft.pop("pending", None)
                self._status(str(exc))
            else:
                if response.status == "accepted":
                    self.draft.pop("pending", None)
                    self.writable = False
                    self._status(tr("Answer recorded. The Host can continue this run."))
                elif response.status in {"conflict", "rejected"}:
                    self.draft.pop("pending", None)
                    self.writable = False
                    self._status(tr("Answer not accepted: ") + str((response.error or {}).get("message", response.status)) + ". Close and reopen to review current state.")
                else:
                    self._status(tr(
                        "Host reply did not confirm acceptance. Retry the same answer or inspect current state."
                    ))
                    self.query_one("#human-submit", Button).label = tr("Retry same answer")
                self.app.action_refresh()
            finally:
                self.busy = False
                self._buttons()

    def action_close(self):
        if self.busy:
            self._status(tr("Waiting for the Host reply…"))
            return
        self.draft["text"] = self.query_one("#human-answer", TextArea).text
        self.dismiss()
