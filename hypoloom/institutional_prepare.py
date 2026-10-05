"""Local, interactive session preparation without a research Host or archive."""
from __future__ import annotations

import json
import math
import os
from copy import copy
from pathlib import Path
import sys
from time import monotonic, sleep

from .institutional_access import article_url, publisher, provider_catalog
from .institutional_options import add_arguments, add_preparation_argument, build, options, preparation_selection
from .institutional_dialogue import PreparationDialogue, PreparationEnded


_COMPLETED_STATES = {"sample_verified", "not_checked", "skipped", "public_only", "disabled"}
_WATCH_SECONDS = 30


def add_parser(subparsers):
    parser = subparsers.add_parser("institutional", help="prepare browser sessions without starting research")
    commands = parser.add_subparsers(dest="institutional_action", required=True)
    prepare = commands.add_parser("prepare", help="open selected platforms and verify fixed or supplied full-text samples")
    add_arguments(prepare, connection=True)
    prepare.add_argument("--credentials-file", help="existing private literature Profile or dotenv; secret values never belong in argv")
    prepare.add_argument("--literature-pdf-fallback", choices=("semantic-scholar", "unpaywall"), default="semantic-scholar")
    prepare.add_argument("--unpaywall-email-file", help="existing contact-email file for the explicitly selected Unpaywall fallback")
    prepare.add_argument("--repository", help="checkout containing the server Compose configuration")
    add_preparation_argument(prepare, "--provider")
    prepare.add_argument("--sample", action="append", default=[], metavar="DOI_OR_URL",
                         help="override this platform's default probe; verify through the same browser and document identity validator")
    prepare.add_argument("--list", action="store_true", help="print the platform catalog without connecting")
    prepare.add_argument("--non-interactive", action="store_true", help="verify supplied samples using existing sessions; never wait for input")
    prepare.add_argument("--agent-session", action="store_true", help="receive init answers over the private preparation socket instead of stdin")
    dialogue = commands.add_parser("preparation", help="observe or answer a live init preparation session")
    dialogue.add_argument("preparation_action", choices=("status", "instructions", "watch", "reply", "complete", "verify", "skip", "public_only", "retry_connection", "finish"))
    dialogue.add_argument("--state-root", required=True)
    dialogue.add_argument("--provider")
    dialogue.add_argument("--sample")
    dialogue.add_argument("--text", help="exact user reply from the program's current instructions")
    dialogue.add_argument("--wait-seconds", type=float,
                          help="watch only: observation limit up to 3600 seconds (default 30); never extends preparation deadlines")
    connect = commands.add_parser("connect", help="inspect, start or stop an operator-owned browser connection")
    connect.add_argument("connection_action", choices=("status", "start", "stop"))
    connect.add_argument("--repository")
    add_arguments(connect, connection=True)
    listing = commands.add_parser("connections", help="read current run endpoints through private Host control sockets")
    scope = listing.add_mutually_exclusive_group(required=True)
    scope.add_argument("--state-root")
    scope.add_argument("--runs-root")
    listing.add_argument("--include-ui-credentials", action="store_true", help="show private container GUI access credentials locally")
    for name in ("forward-ready", "watch"):
        watch = commands.add_parser(name, help="check or hold the private run-owned SSH forwarding session")
        watch.add_argument("--state-root", required=True)
        watch.add_argument("--run-id", required=True)
        watch.add_argument("--socket-path", required=True)
    watch.add_argument("--browser-path", required=True)


def sample_url(value):
    value = value.strip()
    if value.lower().startswith("doi:"): value = value[4:]
    if value.startswith("10."): value = "https://doi.org/" + value
    return article_url(value)


def _observe(access, provider, *, cancelled=lambda: False, output=lambda _: None, wait_for_auth=False):
    deadline = monotonic() + access.acquisition_seconds + 1
    reported = set()
    while monotonic() < deadline:
        if cancelled(): raise PreparationEnded("finished")
        access.tick({"preparation"})
        group = next(g for g in access.status("preparation")["groups"] if g["provider"] == provider)
        result = group["preparation"]
        if result["state"] in {"browser_unavailable", "fetch_unavailable"}:
            failure = (result["state"], result.get("reason_code"))
            if failure not in reported:
                # Queue reason codes are sanitized by _acquire; never export browser exceptions.
                output(json.dumps({"provider": provider, **result}, ensure_ascii=False))
                reported.add(failure)
        if result["state"] not in ({"queued", "checking", "browser_unavailable", "fetch_unavailable"}
                | ({"verification_required", "login_required"} if wait_for_auth else set())):
            return result
        sleep(.1)
    return {"state": "acquisition_timeout"}


def _verify(access, provider, samples, output, *, cancelled=lambda: False, dialogue=None):
    results = []
    default = next(p["default_sample_url"] for p in provider_catalog() if p["provider"] == provider)
    for url in samples:
        deadline = monotonic() + access.acquisition_seconds
        access.control("preparation", provider, "verify", url=url)
        observed = _observe(access, provider, cancelled=cancelled, output=output)
        if url == default and observed["state"] in {"verification_required", "login_required"}:
            access.discard_preparation("preparation", provider)
        # Fixed samples test unattended acquisition after setup. A challenge is
        # a result, not a request to manually download every paper. Explicit
        # alternative samples retain the optional manual recovery dialogue.
        if dialogue and url != default and observed["state"] in {"verification_required", "login_required"}:
            output(json.dumps({"provider": provider, **observed}, ensure_ascii=False))
            try:
                answer = dialogue.wait(provider=provider, reason_code=observed["state"], sample_url=url,
                                       wait_seconds=max(0, deadline - monotonic()))
            except PreparationEnded as exc:
                if str(exc) != "input_timeout": raise
                state = "acquisition_timeout" if monotonic() >= deadline else "input_timeout"
                observed = {"state": state, "reason_code": state}
                answer = None  # Expiry is not an implicit completion reply.
            if answer in {"s", "p"}:
                state = "skipped" if answer == "s" else "public_only"
                observed = {"state": state, "reason_code": "operator_" + state}
            elif answer == "":
                # Keep the same acquisition, deadline and retained challenge tab.
                # Do not send control(verify) again or force browser navigation.
                observed = _observe(access, provider, cancelled=cancelled, output=output, wait_for_auth=True)
            if observed["state"] in {"skipped", "public_only", "input_timeout", "acquisition_timeout"}:
                access.discard_preparation("preparation", provider)
        result = {"provider": provider, "sample_url": url,
                  "sample_scope": "public_full_text" if url == default else "article_full_text",
                  **observed}
        output(json.dumps(result, ensure_ascii=False))
        results.append(result)
        if observed["state"] in {"skipped", "public_only"}: break
    return results


def _summary(rows, modes, output, **extra):
    # This is a transient review, not saved proof of authentication or subscription.
    from .institutional_instructions import render_results
    output(render_results(rows, modes))
    output(json.dumps({"preparation_summary": rows, "provider_modes": modes, **extra,
        "verification_scope": "Sample readability only; institutional login and platform-wide subscription are not established.",
        "provider_mode_argv": [part for name, mode in modes.items()
                               for part in ("--institutional-provider-mode", f"{name}={mode}")]}, ensure_ascii=False))


def _remaining_command(args, remaining, modes):
    if not remaining: return []
    from .institutional_options import launch_arguments, preparation_literature_arguments
    selected = copy(args)
    selected.institutional_provider_mode = [f"{name}={mode}" for name, mode in modes.items()]
    return [sys.executable, "-B", "-m", "hypoloom", "institutional", "prepare",
        "--repository", str(Path(args.repository or Path.cwd()).resolve()),
        *(["--agent-session"] if args.agent_session else []),
        *(["--non-interactive"] if args.non_interactive else []),
        *launch_arguments(selected, include_connection=True, include_preparation=True),
        *preparation_literature_arguments(selected),
        *[part for provider in remaining for part in ("--provider", provider)],
        *[part for sample in args.sample if publisher(sample_url(sample)) in remaining
          for part in ("--sample", sample)]]


def _remaining_providers(selected, rows):
    # Completed manual preparation may be not_checked. Only the unvisited tail
    # and actual failures need another batch; a timeout is not completion.
    return [row["provider"] for row in rows if row["state"] not in _COMPLETED_STATES] + selected[len(rows):]


def _finish_summary(args, selected, rows, modes, output, **extra):
    # Apply an explicit preparation policy to the existing launch preferences.
    # Keep observations (including failure/timeout and untouched platforms) intact.
    remaining = [name for name in _remaining_providers(selected, rows) if modes.get(name) != "disabled"]
    failure_mode = args.institutional_prepare_failure_mode
    if failure_mode == "public_only":
        modes.update(dict.fromkeys(remaining, "public_only"))
        if "next_actions" in extra:
            extra["next_actions"] = ["review_launch_configuration"]
    remaining = [name for name in remaining if modes.get(name) not in {"public_only", "disabled"}]
    rows.extend({"provider": name, "state": "not_checked", "samples": []} for name in selected[len(rows):])
    _summary(rows, modes, output, preparation_failure_mode=failure_mode,
             remaining_preparation_argv=_remaining_command(args, remaining, modes), **extra)


def _sample_state(results):
    # A subsequent explicit platform choice takes precedence over an earlier gap.
    chosen = next((r["state"] for r in results if r["state"] in {"skipped", "public_only"}), None)
    return chosen or next((r["state"] for r in results if r["state"] != "sample_verified"), "sample_verified")


def _prepare_platform(access, provider, samples, *, interactive, input_fn, output, modes, cancelled=lambda: False,
                      dialogue=None):
    row = {"provider": provider, "state": "not_checked", "samples": []}
    if modes.get(provider) in {"public_only", "disabled"}:
        return {**row, "state": modes[provider]}, False
    if samples:
        row["samples"] = _verify(access, provider, samples, output, cancelled=cancelled, dialogue=dialogue)
        row["state"] = _sample_state(row["samples"])
        if row["state"] in {"sample_verified", "skipped", "public_only"}:
            if row["state"] == "public_only": modes[provider] = "public_only"
            return row, False
    if not interactive:
        return row, False
    default = next(p["default_sample_url"] for p in provider_catalog() if p["provider"] == provider)
    # Default probes run only after manual completion, so an OA success cannot
    # silently bypass the requested opportunity to authenticate at the platform.
    completion_samples = samples or ([default] if default else [])
    # Preparing an entry is not a login assertion. Reuse cookies in the same browser.
    access.control("preparation", provider, "open")
    opened = _observe(access, provider, cancelled=cancelled, output=output)
    output(json.dumps({"provider": provider, **opened}, ensure_ascii=False))
    if opened["state"] != "opened":
        return {**row, "state": opened["state"]}, False
    if opened.get("reason_code") == "verification_required":
        output("Human verification was detected when opening this page. Complete it only in the browser; "
               "if it keeps repeating, skip this platform. The existing answer deadline still applies.")
    while True:
        try:
            if dialogue:
                answer = dialogue.wait(provider=provider, reason_code=opened.get("reason_code"),
                                       completion_samples=completion_samples)
            else:
                completion = (f"verify {', '.join(completion_samples)}" if completion_samples
                              else "finish without a sample")
                answer = input_fn(f"{provider}: use the existing session or authenticate if needed. "
                    f"Enter to {completion} "
                    "(sample readability only, not proof of login/subscription); "
                    "DOI/URL to use your sample; s skip; p public only; q finish batch: ").strip()
        except EOFError:
            output("Interactive input is unavailable; init must use --agent-session and the preparation commands.")
            return {**row, "state": "input_required"}, True
        if answer.lower() in {"s", "q"}:
            return {**row, "state": "skipped"}, answer.lower() == "q"
        if answer.lower() == "p":
            modes[provider] = "public_only"
            return {**row, "state": "public_only"}, False
        if answer:
            try:
                url = sample_url(answer)
                if publisher(url) != provider: raise ValueError("sample belongs to a different platform")
            except ValueError:
                output("Use a supported DOI/canonical URL for this platform, or s/p/q. No login credentials here.")
                continue
            if url not in samples: samples.append(url)
        elif not samples:
            samples = list(completion_samples)
        if samples:
            # Keep successful results; only recheck gaps after the operator's action.
            verified = {r["sample_url"] for r in row["samples"] if r["state"] == "sample_verified"}
            row["samples"] = [r for r in row["samples"] if r["state"] == "sample_verified"] + _verify(
                access, provider, [url for url in samples if url not in verified], output,
                cancelled=cancelled, dialogue=dialogue)
            row["state"] = _sample_state(row["samples"])
            if row["state"] == "public_only": modes[provider] = "public_only"
        return row, False


def _connection(access, seconds, dialogue):
    if dialogue: dialogue.update(state="checking_connection", actions=["finish"], automatic_connection_checks=True)
    future = access.check_connection("preparation", timeout_seconds=seconds)
    while not future.done():
        if dialogue and dialogue.cancelled(): raise PreparationEnded("finished")
        sleep(.05)
    return future.result()


def _answer(args, output):
    from .control_protocol import ControlRequest, request_over_socket
    from .institutional_instructions import render
    root = Path(args.state_root)
    watching = args.preparation_action == "watch"
    wait_seconds = args.wait_seconds
    if wait_seconds is not None and (not watching or not math.isfinite(wait_seconds) or not 0 < wait_seconds <= 3600):
        raise ValueError("wait-seconds is only valid for watch and must be finite, positive and at most 3600")
    instructions = args.preparation_action in {"instructions", "watch"}
    status = args.preparation_action in {"status", "instructions", "watch"}
    if status and (args.provider or args.sample or args.text): raise ValueError("status/instructions do not take answers")
    if (args.preparation_action == "reply") != (args.text is not None):
        raise ValueError("reply requires --text; other actions do not accept --text")
    payload = {} if status else ({"reply": args.text} if args.preparation_action == "reply" else {"action": args.preparation_action})
    if args.provider:
        from .institutional_access import provider_name
        payload["provider"] = provider_name(args.provider)
    if args.sample: payload["sample"] = args.sample
    deadline = monotonic() + (_WATCH_SECONDS if wait_seconds is None else wait_seconds)
    response = None
    while True:
        if watching and response is not None and monotonic() >= deadline: break
        try:
            info = root.stat()
            if root.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("preparation root must be private and owned by the current user")
            response = request_over_socket(root / "control.sock", ControlRequest(1, "institutional-preparation", None,
                "PREPARATION_STATUS" if status else "PREPARATION_ANSWER", payload),
                timeout=min(3, max(.001, deadline - monotonic())) if watching else 3)
        except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError, TimeoutError):
            if not instructions: raise
            output(render({"state": "inactive"}))
            return 1
        if not watching or response.status != "accepted": break
        view = response.result
        if view["state"] not in {"checking_connection", "preparing_platform", "processing", "waiting_for_connection"}: break
        # An older preparation waits for an answer rather than automatic checks.
        if view["state"] == "waiting_for_connection" and not view.get("automatic_connection_checks"): break
        remaining = deadline - monotonic()
        if remaining <= 0: break
        sleep(min(.25, remaining))
    output(render(response.result) if instructions and response.status == "accepted"
           else json.dumps(response.to_mapping(), ensure_ascii=False))
    return 0 if response.status == "accepted" else 1


def run(args, *, input_fn=input, output=print):
    if output is print:
        output = lambda text: print(text, flush=True)
    if getattr(args, "institutional_action", None) == "preparation":
        return _answer(args, output)
    if getattr(args, "institutional_action", None) in {"connections", "forward-ready", "watch"}:
        from .institutional_client import run as client
        return client(args, output=output)
    if getattr(args, "institutional_action", None) == "connect":
        from .institutional_connection import run as connect
        return connect(args, output=output)
    if args.list:
        output(json.dumps(provider_catalog(), ensure_ascii=False))
        return 0
    if args.agent_session and args.non_interactive:
        raise ValueError("--agent-session cannot be combined with --non-interactive")
    samples = list(dict.fromkeys(sample_url(value) for value in args.sample))
    modes = options(args)["provider_modes"]
    values = args.provider if args.provider is not None else ([publisher(url) for url in samples] if samples else None)
    selected = preparation_selection(values, modes)
    if values and "none" in values and samples:
        raise ValueError("preparation none cannot be combined with samples")
    selected = list(dict.fromkeys([*selected, *(publisher(url) for url in samples)]))
    if not selected:
        _finish_summary(args, [], [], modes, output)
        return 0
    if len(selected) > 8: raise ValueError("prepare at most eight platforms per batch; run another batch afterward")
    if args.non_interactive and not samples: raise ValueError("non-interactive preparation requires a sample")
    if all(modes.get(provider) in {"public_only", "disabled"} for provider in selected):
        _finish_summary(args, selected, [{"provider": provider, "state": modes[provider], "samples": []}
                                       for provider in selected], modes, output)
        return 0
    access = build(args)
    if access is None: raise ValueError("preparation requires an enabled browser mode or --institutional-browser-url")
    rows = []
    control = directory = None
    dialogue = PreparationDialogue(args.institutional_manual_seconds) if args.agent_session else None
    cancelled = dialogue.cancelled if dialogue else lambda: False
    try:
        if args.institutional_browser_mode or dialogue:
            import tempfile
            from .institutional_client import PreparationControl
            if args.institutional_browser_mode: access.sync({"preparation"})
            root = Path(args.repository or Path.cwd()) / "state"
            root.mkdir(parents=True, exist_ok=True)
            directory = tempfile.TemporaryDirectory(prefix="institutional-prepare-", dir=root)
            command = [sys.executable, "-B", "-m", "hypoloom", "institutional", "preparation"]
            if dialogue:
                dialogue.context = {"reply_argv": [*command, "reply", "--state-root", directory.name]}
                if args.institutional_browser_mode == "client":
                    from .institutional_instructions import client_entry
                    dialogue.context["client_entry"] = client_entry(root.parent, sys.executable)
            control = PreparationControl(directory.name, access, dialogue.handle if dialogue else None)
            output(json.dumps({"state_root": directory.name,
                "connection": access.status("preparation").get("connection", {"mode": "existing", "state": "unchecked"}),
                "status_argv": [*command, "status", "--state-root", directory.name],
                "instructions_argv": [*command, "instructions", "--state-root", directory.name],
                # One background observer survives a late client attachment.
                # Actual operation/answer deadlines still end the preparation;
                # this upper bound belongs only to the read-only observer.
                "watch_argv": [*command, "watch", "--state-root", directory.name, "--wait-seconds", "3600"],
                "answer_argv": [*command, "<action>", "--state-root", directory.name]}, ensure_ascii=False))
        output("Preparation only: no research run or source archive. Login only in the dedicated browser. Keep this terminal open.")
        while True:
            connection = _connection(access, args.institutional_connect_timeout, dialogue)
            output(json.dumps({"connection": connection}, ensure_ascii=False))
            if connection["state"] == "ready": break
            if not dialogue:
                _finish_summary(args, selected, rows, modes, output, connection=connection,
                    next_actions=["prepare_client_connection", "retry_connection", "skip_preparation"])
                return 1
            output("Browser connection is unavailable. Preparation automatically rechecks within this fixed waiting window; no platform login was attempted.")
            if dialogue.wait(connection=connection,
                    check=lambda **kwargs: access.check_connection("preparation", **kwargs),
                    check_seconds=args.institutional_connect_timeout,
                    observed=lambda value: output(json.dumps({"connection": value}, ensure_ascii=False))) == "ready":
                break
        for provider in selected:
            if cancelled(): raise PreparationEnded("finished")
            if dialogue: dialogue.update(state="preparing_platform", provider=provider, actions=["finish"])
            row, stop = _prepare_platform(access, provider, [url for url in samples if publisher(url) == provider],
                interactive=not args.non_interactive,
                input_fn=input_fn, dialogue=dialogue,
                output=output, modes=modes, cancelled=cancelled)
            rows.append(row)
            if stop:
                break
        _finish_summary(args, selected, rows, modes, output)
        return int(any(row["state"] not in _COMPLETED_STATES for row in rows))
    except PreparationEnded as exc:
        connection = dialogue.snapshot().get("connection") if dialogue else None
        _finish_summary(args, selected, rows, modes, output, preparation_state=str(exc),
                        **({"connection": connection} if connection else {}))
        return int(str(exc) != "finished")
    finally:
        try:
            if control is not None: control.close()
            access.close()
        finally:
            if directory is not None: directory.cleanup()
