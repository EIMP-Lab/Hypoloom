"""Local, interactive session preparation without a research Host or archive."""
from __future__ import annotations

import json
from time import monotonic, sleep

from .institutional_access import article_url, publisher, provider_catalog
from .institutional_options import add_arguments, add_preparation_argument, build, options, preparation_selection


def add_parser(subparsers):
    parser = subparsers.add_parser("institutional", help="prepare browser sessions without starting research")
    commands = parser.add_subparsers(dest="institutional_action", required=True)
    prepare = commands.add_parser("prepare", help="open selected platforms and optionally verify sample full text")
    add_arguments(prepare, connection=True)
    prepare.add_argument("--repository", help="checkout containing the server Compose configuration")
    add_preparation_argument(prepare, "--provider")
    prepare.add_argument("--sample", action="append", default=[], metavar="DOI_OR_URL",
                         help="verify a sample through the same browser and PDF identity validator")
    prepare.add_argument("--list", action="store_true", help="print the platform catalog without connecting")
    prepare.add_argument("--non-interactive", action="store_true", help="verify supplied samples using existing sessions; never wait for input")
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


def _observe(access, provider):
    deadline = monotonic() + access.acquisition_seconds + 1
    while monotonic() < deadline:
        access.tick({"preparation"})
        group = next(g for g in access.status("preparation")["groups"] if g["provider"] == provider)
        result = group["preparation"]
        if result["state"] not in {"queued", "checking", "browser_unavailable", "fetch_unavailable"}:
            return result
        sleep(.1)
    return {"state": "acquisition_timeout"}


def _verify(access, provider, samples, output):
    results = []
    for url in samples:
        access.control("preparation", provider, "verify", url=url)
        result = {"provider": provider, "sample_url": url, **_observe(access, provider)}
        output(json.dumps(result, ensure_ascii=False))
        results.append(result)
    return results


def _summary(rows, modes, output):
    # This is a transient review, not saved proof of authentication or subscription.
    output(json.dumps({"preparation_summary": rows, "provider_modes": modes,
        "provider_mode_argv": [part for name, mode in modes.items()
                               for part in ("--institutional-provider-mode", f"{name}={mode}")]}, ensure_ascii=False))


def _prepare_platform(access, provider, samples, *, interactive, input_fn, output, modes):
    row = {"provider": provider, "state": "not_checked", "samples": []}
    if modes.get(provider) == "public_only":
        return {**row, "state": "public_only"}, False
    if samples:
        row["samples"] = _verify(access, provider, samples, output)
        if all(r["state"] == "sample_verified" for r in row["samples"]):
            return {**row, "state": "sample_verified"}, False
        row["state"] = next(r["state"] for r in row["samples"] if r["state"] != "sample_verified")
    if not interactive:
        return row, False
    # Preparing an entry is not a login assertion. Reuse cookies in the same browser.
    access.control("preparation", provider, "open")
    opened = _observe(access, provider)
    output(json.dumps({"provider": provider, **opened}, ensure_ascii=False))
    if opened["state"] != "opened":
        return {**row, "state": opened["state"]}, False
    while True:
        answer = input_fn(f"{provider}: use the existing session or authenticate if needed. "
            "Enter to finish/recheck samples; DOI/URL to verify; s skip; p public only; q finish batch: ").strip()
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
        if samples:
            # Keep successful results; only recheck gaps after the operator's action.
            verified = {r["sample_url"] for r in row["samples"] if r["state"] == "sample_verified"}
            row["samples"] = [r for r in row["samples"] if r["state"] == "sample_verified"] + _verify(
                access, provider, [url for url in samples if url not in verified], output)
            row["state"] = next((r["state"] for r in row["samples"] if r["state"] != "sample_verified"), "sample_verified")
        return row, False


def run(args, *, input_fn=input, output=print):
    if getattr(args, "institutional_action", None) in {"connections", "forward-ready", "watch"}:
        from .institutional_client import run as client
        return client(args, output=output)
    if getattr(args, "institutional_action", None) == "connect":
        from .institutional_connection import run as connect
        return connect(args, output=output)
    if args.list:
        output(json.dumps(provider_catalog(), ensure_ascii=False))
        return 0
    samples = list(dict.fromkeys(sample_url(value) for value in args.sample))
    modes = options(args)["provider_modes"]
    values = args.provider if args.provider is not None else ([publisher(url) for url in samples] if samples else None)
    selected = preparation_selection(values, modes)
    if values and "none" in values and samples:
        raise ValueError("preparation none cannot be combined with samples")
    selected = list(dict.fromkeys([*selected, *(publisher(url) for url in samples)]))
    if not selected:
        _summary([], modes, output)
        return 0
    if len(selected) > 8: raise ValueError("prepare at most eight platforms per batch; run another batch afterward")
    if args.non_interactive and not samples: raise ValueError("non-interactive preparation requires a sample")
    if all(modes.get(provider) == "public_only" for provider in selected):
        _summary([{"provider": provider, "state": "public_only", "samples": []} for provider in selected], modes, output)
        return 0
    access = build(args)
    if access is None: raise ValueError("preparation requires an enabled browser mode or --institutional-browser-url")
    rows = []
    control = directory = None
    try:
        if args.institutional_browser_mode:
            from pathlib import Path
            import tempfile
            from .institutional_client import PreparationControl
            access.sync({"preparation"})
            root = Path(args.repository or Path.cwd()) / "state"
            root.mkdir(parents=True, exist_ok=True)
            directory = tempfile.TemporaryDirectory(prefix="institutional-prepare-", dir=root)
            control = PreparationControl(directory.name, access)
            output(json.dumps({"state_root": directory.name,
                "connection": access.status("preparation")["connection"]}, ensure_ascii=False))
        output("Preparation only: no research run or source archive. Login only in the dedicated browser. Keep this terminal open.")
        for provider in selected:
            row, stop = _prepare_platform(access, provider, [url for url in samples if publisher(url) == provider],
                interactive=not args.non_interactive, input_fn=input_fn, output=output, modes=modes)
            rows.append(row)
            if stop:
                rows.extend({"provider": name, "state": "not_checked", "samples": []} for name in selected[len(rows):])
                break
        _summary(rows, modes, output)
        return int(any(row["state"] not in {"sample_verified", "not_checked", "skipped", "public_only"} for row in rows))
    finally:
        try:
            if control is not None: control.close()
            access.close()
        finally:
            if directory is not None: directory.cleanup()
