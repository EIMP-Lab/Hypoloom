"""Operator GPU selection with stable request replay after uncertain replies."""
import asyncio
import uuid

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, SelectionList, Static

from .control_protocol import ControlRequest, ProtocolError, SCHEMA_VERSION, request_over_socket
from .ui_i18n import tr


class ResourceSelectionScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    ResourceSelectionScreen { align: center middle; background: $background 70%; }
    #gpu-selection-form { width: 95%; max-width: 120; height: auto; max-height: 95%; border: round #ffb86b; padding: 1 2; background: $surface; }
    #gpu-selection-form Static { height: auto; margin-bottom: 1; }
    #gpu-choices { height: 12; }
    """

    def __init__(self, draft):
        super().__init__()
        self.draft, self.busy, self.ready = draft, False, False

    def compose(self):
        with Vertical(id="gpu-selection-form"):
            yield Static(tr("Select GPUs"), markup=False)
            yield Static(tr("New jobs use the selected GPUs. Active jobs finish on their original GPUs. An empty selection pauses new GPU jobs."), markup=False)
            yield SelectionList(id="gpu-choices")
            yield Static("", id="gpu-selection-result", markup=False)
            yield Button(tr("Retry same request") if self.draft.get("pending") else tr("Apply GPU selection"), id="gpu-apply", disabled=True)
            yield Button(tr("Refresh"), id="gpu-refresh")
            yield Button(tr("Close"), id="gpu-close")

    async def on_mount(self):
        await self.refresh_options()

    async def send(self, request):
        return await asyncio.to_thread(request_over_socket, self.app.state_root / "control.sock", request, timeout=15)

    async def refresh_options(self):
        if self.busy: return
        self.busy = True
        try:
            response = await self.send(ControlRequest(SCHEMA_VERSION, "gpu-options-" + str(uuid.uuid4()), None,
                "RESOURCE_OPTIONS", {"run_id": self.app.run_id}))
            if response.status != "accepted":
                raise ValueError((response.error or {}).get("message", response.status))
            result = response.result
            selected = set(result["selection"]["gpu_uuids"])
            if self.draft.get("pending"):
                selected = set(self.draft["pending"].payload["gpu_uuids"])
            choices = self.query_one("#gpu-choices", SelectionList)
            choices.clear_options()
            choices.add_options([(f"{gpu['index']} · {gpu['uuid']} · {gpu['name']}", gpu["uuid"], gpu["uuid"] in selected)
                for gpu in result["devices"]])
            draining = result["selection"]["draining_gpu_uuids"]
            self.query_one("#gpu-selection-result", Static).update(tr("Draining GPUs: {0}", ", ".join(draining)) if draining else "")
            self.ready = True
        except (OSError, ProtocolError, ValueError) as exc:
            self.query_one("#gpu-selection-result", Static).update(str(exc))
        finally:
            self.busy = False
            self.buttons()

    def buttons(self):
        pending = bool(self.draft.get("pending"))
        button = self.query_one("#gpu-apply", Button)
        button.disabled = self.busy or not (self.ready or pending)
        button.label = tr("Retry same request") if pending else tr("Apply GPU selection")
        self.query_one("#gpu-choices", SelectionList).disabled = self.busy or pending

    async def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "gpu-close": self.action_close(); return
        if event.button.id == "gpu-refresh": await self.refresh_options(); return
        if event.button.id != "gpu-apply" or self.busy: return
        selected = sorted(self.query_one("#gpu-choices", SelectionList).selected)
        request = self.draft.setdefault("pending", ControlRequest(SCHEMA_VERSION,
            "gpu-select-" + str(uuid.uuid4()), None, "RESOURCE_SELECT", {"run_id": self.app.run_id,
                "mode": "set" if selected else "none", "gpu_uuids": selected}))
        self.busy = True
        self.buttons()
        try:
            response = await self.send(request)
            if response.status in {"accepted", "rejected", "conflict"}: self.draft.pop("pending", None)
            message = (tr("GPU selection saved. Active jobs continue; pending reservations are reconciled by the Host.")
                if response.status == "accepted" else str((response.error or {}).get("message", response.status)))
        except (OSError, ProtocolError):
            message = tr("Host reply not confirmed. Retry sends the same request.")
        finally:
            self.busy = False
            self.buttons()
        self.query_one("#gpu-selection-result", Static).update(message)
        self.app.action_refresh()

    def action_close(self):
        if not self.busy: self.dismiss()
