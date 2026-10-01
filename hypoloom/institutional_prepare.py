"""Local, interactive session preparation without a research Host or archive."""
from __future__ import annotations

import json
from time import monotonic, sleep

from .institutional_access import PROVIDERS, article_url, publisher
from .institutional_options import add_arguments, build


def add_parser(subparsers):
    parser = subparsers.add_parser("institutional", help="prepare browser sessions without starting research")
    commands = parser.add_subparsers(dest="institutional_action", required=True)
    prepare = commands.add_parser("prepare", help="open selected platforms and optionally verify sample full text")
    add_arguments(prepare, connection=True)
    prepare.add_argument("--repository", help="checkout containing the server Compose configuration")
    prepare.add_argument("--provider", action="append", choices=tuple(PROVIDERS.values()), default=[])
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


def run(args, *, input_fn=input, output=print):
    if getattr(args, "institutional_action", None) in {"connections", "forward-ready", "watch"}:
        from .institutional_client import run as client
        return client(args, output=output)
    if getattr(args, "institutional_action", None) == "connect":
        from .institutional_connection import run as connect
        return connect(args, output=output)
    if args.list:
        output(json.dumps([{"provider": name, "entry_url": "https://" + domain + "/"}
                           for domain, name in PROVIDERS.items()], ensure_ascii=False))
        return 0
    samples = [sample_url(value) for value in args.sample]
    selected = list(dict.fromkeys([*args.provider, *(publisher(url) for url in samples)]))
    if not selected: raise ValueError("select --provider or --sample; use --list to see platforms")
    if len(selected) > 8: raise ValueError("prepare at most eight platforms per batch; run another batch afterward")
    if args.non_interactive and not samples: raise ValueError("non-interactive preparation requires a sample")
    access = build(args)
    if access is None: raise ValueError("preparation requires --institutional-browser-url")
    failures = 0
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
            if not args.non_interactive:
                access.control("preparation", provider, "open")
                opened = _observe(access, provider)
                output(json.dumps({"provider": provider, **opened}, ensure_ascii=False))
                if opened["state"] != "opened":
                    failures += 1
                    continue
                answer = input_fn(f"{provider}: complete login, then Enter to continue; or enter a sample DOI/URL (q to finish): ").strip()
                if answer.lower() == "q": break
                if answer:
                    url = sample_url(answer)
                    if publisher(url) != provider: raise ValueError("sample belongs to a different selected platform")
                    if url not in samples: samples.append(url)
            for url in [url for url in samples if publisher(url) == provider]:
                access.control("preparation", provider, "verify", url=url)
                result = _observe(access, provider)
                output(json.dumps({"provider": provider, **result}, ensure_ascii=False))
                failures += result["state"] != "sample_verified"
        return 1 if failures else 0
    finally:
        try:
            if control is not None: control.close()
            access.close()
        finally:
            if directory is not None: directory.cleanup()
