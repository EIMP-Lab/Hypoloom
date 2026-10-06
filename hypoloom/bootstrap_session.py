"""Program-owned client preparation/review, using the existing private dialogue.

No persisted launch manifest: admitted Profile, ordinary argv and the live
preparation owner are the existing sources of truth. Only an explicit Start now
answer can invoke the existing managed launch operation.
"""
from copy import deepcopy
from pathlib import Path
import sys

from . import bootstrap


def supported(args, review):
    selected = review.get("institutional_preparation", {}).get("selected", [])
    from .institutional_options import options
    modes = options(args)["provider_modes"]
    return (args.institutional_browser_mode == "client" and bool(selected)
            and any(modes.get(name) not in {"public_only", "disabled"} for name in selected))


class LaunchSession:
    def __init__(self, args, repository, report, result):
        self.args = deepcopy(args)
        self.repository = repository
        self.report = report
        self.result = deepcopy(result)
        self.ready = False

    def review(self, summary):
        """Combine accepted publisher choices before starting the human clock."""
        self.ready = False
        try:
            from .institutional_options import options
            from .bootstrap_review import render
            current = bootstrap.admit_profile(self.report.profile_path, run_id=self.args.run_id,
                allow_gpu=self.args.allow_gpu, delay_seconds=self.args.process_delay)
            if not current.admitted or current.create_run_payload != self.report.create_run_payload:
                raise ValueError("Profile changed during preparation; review it in a new init")
            modes = {**options(self.args)["provider_modes"], **summary["provider_modes"]}
            self.args.institutional_provider_mode = [f"{name}={mode}" for name, mode in sorted(modes.items())]
            review = {}
            commands = bootstrap.launch_arguments(self.args, self.repository, self.report, review=review)
            if commands.get("pool_preflight_available") is False or (
                "pool_prepare" in commands and not (
                    commands.get("pool_action_reviewed") and commands.get("pool_prepare_available"))):
                raise ValueError("GPU pool plan needs review; no pool action has been performed")
            self.result.update(commands=commands, effective_configuration=review)
            text = render(self.args, self.result, summary)
            self.ready = True
            return {"status": "ready_for_review", "text": text}
        except (ValueError, OSError) as exc:
            return {"status": "blocked", "text": "Launch review blocked: " + str(exc)
                    + "\nNo run has started. Prepare only releases this connection; revise the affected configuration before a new init."}

    def start(self):
        if not self.ready:
            raise ValueError("live launch review is required")
        # The dialogue consumes Start now once. Consume the callback too, even
        # if managed launch returns an ambiguous result requiring observation.
        self.ready = False
        self.result["launch"] = bootstrap.start_fresh(self.result["commands"], self.report.create_run_payload)
        if self.result["launch"]["status"] == "started":
            from .monitor_launch import monitor_commands
            self.result["monitor"] = monitor_commands(python=Path(self.args.control_python),
                working_directory=self.repository, state_root=Path(self.args.state_root), run_id=self.args.run_id)
        return bootstrap.compact_result(self.result)


def run(args, repository, report, result, *, output=None):
    if Path(args.control_python).resolve() != Path(sys.executable).resolve():
        raise ValueError("run session with the selected control Python")
    if args.institutional_prepare_provider is None:
        raise ValueError("session requires an explicit publisher selection from init; "
                         "answer institutional_prepare_provider and re-inspect")
    if not supported(args, result["effective_configuration"]):
        raise ValueError("session requires selected Windows client preparation; use the generated launch_argv otherwise")
    from .bootstrap_inspect import supervisor_check
    supervisor = supervisor_check()
    if supervisor["status"] != "ready":
        raise ValueError("managed launch unavailable: " + supervisor["reason"])
    result["supervisor"] = supervisor
    from .cli import build_parser
    from .institutional_prepare import run as prepare
    argv = result["effective_configuration"]["institutional_preparation_argv"]
    # Reuse the ordinary institutional parser, not a second options model.
    preparation = build_parser().parse_args(argv[argv.index("institutional"):])
    if output is None:
        output = lambda line: print(line, flush=True)
    return prepare(preparation, output=output, launch_session=LaunchSession(args, repository, report, result))
