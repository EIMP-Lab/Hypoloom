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
    add_arguments(prepare)
    prepare.add_argument("--provider", action="append", choices=tuple(PROVIDERS.values()), default=[])
    prepare.add_argument("--sample", action="append", default=[], metavar="DOI_OR_URL",
                         help="verify a sample through the same browser and PDF identity validator")
    prepare.add_argument("--list", action="store_true", help="print the platform catalog without connecting")
    prepare.add_argument("--non-interactive", action="store_true", help="verify supplied samples using existing sessions; never wait for input")


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
    try:
        output("Preparation only: no research run or source archive. Login only in the dedicated browser. Keep this terminal open.")
        for provider in selected:
            if not args.non_interactive:
                access.control("preparation", provider, "open")
                output(json.dumps({"provider": provider, **_observe(access, provider)}, ensure_ascii=False))
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
        access.close()
