"""Operator recheck of the existing pool binding; no allocator or run migration."""
import asyncio
import uuid

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .control_protocol import ControlRequest, ProtocolError, SCHEMA_VERSION, request_over_socket


class ResourceRecheckScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    ResourceRecheckScreen { align: center middle; background: $background 70%; }
    #resource-form { width: 90%; max-width: 100; height: auto; border: round #ffb86b; padding: 1 2; background: $surface; }
    #resource-form Static { height: auto; margin-bottom: 1; }
    """

    def __init__(self, status, draft, *, service="gpu"):
        super().__init__()
        self.status, self.draft, self.busy = status, draft, False
        self.service = service
        if service not in {"gpu", "openalex", "telegram"}: raise ValueError("unknown recovery service")

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-form"):
            yield Static({"gpu": "GPU resource recheck", "openalex": "Literature service retry", "telegram": "Telegram delivery retry"}[self.service], markup=False)
            if self.service == "gpu":
                yield Static("\n".join(f"{b['role']}: {b['code']}" for b in self.status.get("resource_blocked", [])), markup=False)
            yield Static(("Repair the bound pool first. Host checks capability and GPU membership. "
                "This releases affected nodes within their existing budgets; it does not switch devices or restart the pool.") if self.service == "gpu" else
                "Repair the connection or credentials in the original Profile first. Release the current wait so pending work can retry. A retry request does not establish recovery; actual successful responses do.", markup=False)
            yield Static("", id="resource-result", markup=False)
            yield Button("Retry same request" if self.draft.get("pending") else "Recheck GPU resources" if self.service == "gpu" else "Request retry", id="resource-confirm")
            yield Button("Close", id="resource-close")

    async def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "resource-close": self.action_close(); return
        if event.button.id != "resource-confirm" or self.busy: return
        request = self.draft.setdefault("pending", ControlRequest(SCHEMA_VERSION,
            "resource-ui-" + str(uuid.uuid4()), None,
            {"gpu": "RESOURCE_RECHECK", "openalex": "PROVIDER_RETRY_CLEAR", "telegram": "NOTIFICATION_RETRY"}[self.service],
            {"run_id": self.app.run_id, **({"sink": "telegram"} if self.service == "telegram" else {"reason": "operator"}),
             **({"service": "openalex"} if self.service == "openalex" else {})}))
        self.busy = True
        self.query_one("#resource-confirm", Button).disabled = True
        try:
            response = await asyncio.to_thread(request_over_socket, self.app.state_root / "control.sock", request, timeout=15)
            if response.status in {"accepted", "rejected", "conflict"}: self.draft.pop("pending", None)
            text = (("GPU resources rechecked; existing task and retry budgets remain in effect." if self.service == "gpu"
                     else "Retry requested. Await an actual successful response before assuming recovery.")
                if response.status == "accepted" else "Recheck not accepted: " + str((response.error or {}).get("message", response.status)))
        except (OSError, ProtocolError):
            text = "Host reply not confirmed. Retry sends the same request."
        finally:
            self.busy = False
            self.query_one("#resource-confirm", Button).disabled = False
        self.query_one("#resource-result", Static).update(text)
        self.app.action_refresh()

    def action_close(self):
        if not self.busy: self.dismiss()
