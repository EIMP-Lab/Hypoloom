"""Shared launch arguments; reviewing configuration never connects a browser."""
from .institutional_access import InstitutionalAccess, PROVIDERS, provider_catalog, provider_name
from .institutional_browser import PlaywrightInstitutionalBrowser


def add_arguments(parser, *, connection=False):
    parser.add_argument("--institutional-browser-url", help="opt-in loopback Chromium CDP endpoint")
    parser.add_argument("--institutional-browser-ui-url", help="loopback server-browser GUI origin")
    parser.add_argument("--institutional-library-url", help="HTTPS interactive library entry, never a SAML metadata endpoint")
    parser.add_argument("--institutional-provider-mode", action="append", default=[], metavar="PROVIDER=MODE",
                        help="auto or public_only; repeat for different providers")
    parser.add_argument("--institutional-acquisition-seconds", type=float, default=120)
    parser.add_argument("--institutional-manual-seconds", type=float, default=600)
    if connection:
        from .institutional_connection import add_arguments as add_connection
        add_connection(parser)


def options(args):
    modes = {}
    for value in args.institutional_provider_mode:
        name, separator, mode = value.partition("=")
        name = provider_name(name)
        if not separator or name not in PROVIDERS.values() or mode not in {"auto", "public_only"}:
            raise ValueError("institutional provider mode requires a supported PROVIDER=auto or PROVIDER=public_only")
        if name in modes and modes[name] != mode:
            raise ValueError("conflicting institutional provider modes")
        modes[name] = mode
    return {"provider_modes": modes, "acquisition_seconds": args.institutional_acquisition_seconds,
            "manual_seconds": args.institutional_manual_seconds}


def add_preparation_argument(parser, flag="--institutional-prepare-provider"):
    parser.add_argument(flag, action="append", type=provider_name, choices=(*PROVIDERS.values(), "none"),
                        help="replace common preparation defaults; repeat for platforms, Elsevier aliases ScienceDirect; none skips preparation")


def preparation_selection(values, modes):
    if values is None:
        return [p["provider"] for p in provider_catalog()
                if p["recommended_prepare"] and modes.get(p["provider"]) != "public_only"]
    selected = list(dict.fromkeys(values))
    if "none" in selected:
        if len(selected) != 1: raise ValueError("preparation none cannot be combined with other platforms or samples")
        return []
    if any(name not in PROVIDERS.values() for name in selected):
        raise ValueError("unsupported preparation platform")
    if len(selected) > 8:
        raise ValueError("prepare at most eight platforms per batch; run another batch afterward")
    return selected


def preparation(args):
    modes = options(args)["provider_modes"]
    selected = preparation_selection(getattr(args, "institutional_prepare_provider", None), modes)
    return {"selected": selected, "library_url": args.institutional_library_url,
            "catalog": [{**p, "selected": p["provider"] in selected,
                         "mode": modes.get(p["provider"], "auto"), "state": "not_checked"}
                        for p in provider_catalog()]}


def build(args):
    from .institutional_connection import configure
    configure(args)
    settings = options(args)
    if getattr(args, "institutional_browser_mode", None) not in (None, "off"):
        from .institutional_connection import runtime_plan
        from .institutional_runtime import RunAccess
        return RunAccess(runtime_plan(args), **settings)
    if not args.institutional_browser_url:
        if args.institutional_browser_ui_url:
            raise ValueError("institutional browser UI requires a browser endpoint")
        if (args.institutional_library_url or args.institutional_browser_ui_url or settings["provider_modes"]
                or settings["acquisition_seconds"] != 120 or settings["manual_seconds"] != 600):
            raise ValueError("institutional options require an enabled browser mode or --institutional-browser-url")
        return None
    return InstitutionalAccess(PlaywrightInstitutionalBrowser(args.institutional_browser_url,
        library_url=args.institutional_library_url, ui_url=args.institutional_browser_ui_url), **settings)


def preview(args, repository=None):
    """The same validated, resource-free configuration for inspect and launch."""
    from pathlib import Path
    from .institutional_connection import plan, runtime_plan
    mode = getattr(args, "institutional_browser_mode", None)
    selection = getattr(args, "institutional_prepare_provider", None)
    if selection is not None:
        selected = preparation_selection(selection, options(args)["provider_modes"])
        if selected and (mode == "off" or not mode and not args.institutional_browser_url):
            raise ValueError("platform preparation requires an enabled institutional browser mode")
    if mode == "server" and not args.institutional_compose_file:
        args.institutional_compose_file = str(Path(repository or Path.cwd()) / "config/institutional-browser.compose.yaml")
    access = build(args)
    if access is None:
        return {"institutional_connection": {"enabled": False, "mode": "off"}} if mode == "off" else {}
    try:
        return {"institutional_access": access.configuration, "institutional_preparation": preparation(args),
                "institutional_connection": runtime_plan(args) if mode else plan(args, repository)}
    finally:
        access.close()


def launch_arguments(args, *, include_connection=False):
    result = []
    mode = getattr(args, "institutional_browser_mode", None)
    if args.institutional_browser_url or mode not in (None, "off"):
        for name in ("browser_url", "browser_ui_url", "library_url", "acquisition_seconds", "manual_seconds"):
            value = getattr(args, "institutional_" + name)
            if value is not None: result += ["--institutional-" + name.replace("_", "-"), str(value)]
        for value in args.institutional_provider_mode:
            result += ["--institutional-provider-mode", value]
    if include_connection:
        from .institutional_connection import arguments
        result += arguments(args)
    return result
