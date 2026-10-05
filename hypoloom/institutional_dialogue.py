"""In-memory init answers carried by the existing private preparation socket."""
from copy import deepcopy
import math
import threading
from time import monotonic

from .control_protocol import ControlResponse


CONNECTION_RETRY_SECONDS = 5


class PreparationEnded(Exception):
    pass


class PreparationDialogue:
    def __init__(self, seconds):
        self.seconds = seconds
        self.condition = threading.Condition()
        self.view = {"state": "checking_connection"}
        self.answer = None
        self.finished = False
        self.deadline = None
        self.context = {}

    def cancelled(self):
        with self.condition: return self.finished

    def update(self, **values):
        with self.condition:
            self.view = values
            self.deadline = None

    def snapshot(self):
        result = deepcopy({**self.context, **self.view})
        if result.get("provider") and "reply_argv" in result:
            result["reply_argv"].extend(["--provider", result["provider"]])
        if self.deadline is not None:
            result["remaining_seconds"] = max(0, math.ceil(self.deadline - monotonic()))
        return result

    def handle(self, request):
        with self.condition:
            if request.command == "PREPARATION_STATUS" and not request.payload:
                return ControlResponse.accepted(request.request_id, None, self.snapshot())
            payload = dict(request.payload)
            if request.command == "PREPARATION_ANSWER" and "reply" in payload:
                from .institutional_instructions import REPLIES, reply_choices
                reply = payload.pop("reply")
                if "action" in payload or not isinstance(reply, str) or reply not in reply_choices(self.view):
                    raise ValueError("use an exact reply offered by the current preparation instructions")
                payload["action"] = REPLIES[reply]
            if request.command != "PREPARATION_ANSWER" or set(payload) - {"provider", "action", "sample"}:
                raise ValueError("unsupported preparation command")
            action = payload.get("action")
            if action == "finish":
                self.finished = True
                self.view = {"state": "finishing"}
                self.deadline = None
                self.condition.notify_all()
                return ControlResponse.accepted(request.request_id, None, {"state": "finishing"})
            if (self.finished or (self.deadline is not None and monotonic() >= self.deadline)
                    or action not in self.view.get("actions", ()) or self.answer is not None
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
                "retry_connection": "retry", "retry": "retry", "verify": sample}[action]
            if action not in {"retry_connection", "retry"}:
                self.view = {**self.view, "state": "processing", "actions": ["finish"]}
                self.deadline = None
            self.condition.notify_all()
            return ControlResponse.accepted(request.request_id, None, deepcopy(self.view))

    def await_launch(self, summary, check, *, seconds=600):
        """Retain this existing control endpoint through review, never start a run."""
        with self.condition:
            self.answer = None
            self.deadline = monotonic() + seconds
            self.view = {"state": "awaiting_launch", "summary": summary,
                         "actions": ["finish"] + (["retry"] if summary["remaining_preparation_argv"] else []),
                         "finish_argv": summary["finish_argv"],
                         "remaining_preparation_argv": summary["remaining_preparation_argv"]}
        while True:
            with self.condition:
                if self.finished or monotonic() >= self.deadline: return "finished"
                if self.answer == "retry":
                    self.answer = None
                    self.view = {"state": "checking_connection", "actions": ["finish"]}
                    self.deadline = None
                    return "retry"
            # The formal Host's existing command and transition are the authority.
            # This read cannot create, resume or cancel research.
            if check(): return "handoff"
            with self.condition:
                if self.deadline is not None:
                    self.condition.wait(min(.5, max(0, self.deadline - monotonic())))

    def wait(self, *, provider=None, connection=None, check=None, check_seconds=30, observed=lambda _: None,
             reason_code=None, completion_samples=(), sample_url=None, wait_seconds=None):
        deadline = monotonic() + (self.seconds if wait_seconds is None else min(self.seconds, max(0, wait_seconds)))
        future = None
        next_check = monotonic() + CONNECTION_RETRY_SECONDS
        with self.condition:
            self.answer = None
            self.deadline = deadline
            self.view = ({"state": "waiting_for_connection", "connection": connection,
                          "actions": ["retry_connection", "finish"]} if connection else
                         {"state": "waiting_for_user", "provider": provider,
                          "actions": ["complete", "verify", "skip", "public_only", "finish"]})
            if connection is None and reason_code in {"verification_required", "login_required"}:
                self.view["reason_code"] = reason_code
            if sample_url:
                self.view["sample_url"] = sample_url
                self.view["actions"].remove("verify")
            if connection is None and completion_samples:
                self.view["completion_samples"] = list(completion_samples)
            if check is not None:
                self.view["automatic_connection_checks"] = True
            while True:
                if self.finished: raise PreparationEnded("finished")
                remaining = deadline - monotonic()
                if remaining <= 0: raise PreparationEnded("input_timeout")
                if check is None:
                    if self.answer is not None: return self.answer
                    self.condition.wait(remaining)
                    continue
                # Connection recovery belongs to the preparation program. A reply
                # can expedite the next probe, but cannot extend this window or
                # queue a second probe while one is still running.
                if self.answer == "retry":
                    self.answer = None
                    next_check = monotonic()
                if future is not None and future.done():
                    connection = future.result()
                    future = None
                    self.view["connection"] = connection
                    observed(connection)
                    if connection["state"] == "ready":
                        self.view = {"state": "processing", "actions": ["finish"]}
                        self.deadline = None
                        return "ready"
                    next_check = monotonic() + CONNECTION_RETRY_SECONDS
                if future is None and monotonic() >= next_check:
                    future = check(timeout_seconds=min(check_seconds, max(.001, deadline - monotonic())))
                self.condition.wait(min(remaining, .1 if future is not None else max(.001, next_check - monotonic())))
