"""In-memory init answers carried by the existing private preparation socket."""
from copy import deepcopy
import threading
from time import monotonic

from .control_protocol import ControlResponse


class PreparationEnded(Exception):
    pass


class PreparationDialogue:
    def __init__(self, seconds):
        self.seconds = seconds
        self.condition = threading.Condition()
        self.view = {"state": "checking_connection"}
        self.answer = None
        self.finished = False

    def cancelled(self):
        with self.condition: return self.finished

    def update(self, **values):
        with self.condition:
            self.view = values

    def handle(self, request):
        with self.condition:
            if request.command == "PREPARATION_STATUS" and not request.payload:
                return ControlResponse.accepted(request.request_id, None, deepcopy(self.view))
            payload = request.payload
            if request.command != "PREPARATION_ANSWER" or set(payload) - {"provider", "action", "sample"}:
                raise ValueError("unsupported preparation command")
            action = payload.get("action")
            if action == "finish":
                self.finished = True
                self.condition.notify_all()
                return ControlResponse.accepted(request.request_id, None, {"state": "finishing"})
            if (action not in self.view.get("actions", ()) or self.answer is not None
                    or payload.get("provider") != self.view.get("provider")):
                raise ValueError("preparation is not waiting for this platform or action")
            if action == "verify":
                from .institutional_prepare import sample_url
                from .institutional_access import publisher
                sample = payload.get("sample")
                if not isinstance(sample, str) or len(sample) > 2048:
                    raise ValueError("bounded sample DOI or URL required")
                sample = sample_url(sample)
                if publisher(sample) != payload.get("provider"):
                    raise ValueError("sample belongs to a different platform")
            elif "sample" in payload:
                raise ValueError("sample is only valid for verification")
            else: sample = None
            self.answer = {"complete": "", "skip": "s", "public_only": "p",
                "retry_connection": "retry", "verify": sample}[action]
            self.view = {**self.view, "state": "processing", "actions": ["finish"]}
            self.condition.notify_all()
            return ControlResponse.accepted(request.request_id, None, deepcopy(self.view))

    def wait(self, *, provider=None, connection=None):
        deadline = monotonic() + self.seconds
        with self.condition:
            self.answer = None
            self.view = ({"state": "waiting_for_connection", "connection": connection,
                          "actions": ["retry_connection", "finish"]} if connection else
                         {"state": "waiting_for_user", "provider": provider,
                          "actions": ["complete", "verify", "skip", "public_only", "finish"]})
            while self.answer is None:
                if self.finished: raise PreparationEnded("finished")
                remaining = deadline - monotonic()
                if remaining <= 0: raise PreparationEnded("input_timeout")
                self.condition.wait(remaining)
            return self.answer
