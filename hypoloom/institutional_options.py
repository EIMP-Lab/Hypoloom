"""Shared launch arguments; reviewing configuration never connects a browser."""
from .institutional_access import InstitutionalAccess, PROVIDERS
from .institutional_browser import PlaywrightInstitutionalBrowser


def add_arguments(parser):
    parser.add_argument("--institutional-browser-url", help="opt-in loopback Chromium CDP endpoint")
    parser.add_argument("--institutional-browser-ui-url", help="loopback server-browser GUI origin")
    parser.add_argument("--institutional-library-url", help="HTTPS interactive library entry, never a SAML metadata endpoint")
    parser.add_argument("--institutional-provider-mode", action="append", default=[], metavar="PROVIDER=MODE",
                        help="auto or public_only; repeat for different providers")
    parser.add_argument("--institutional-acquisition-seconds", type=float, default=120)
    parser.add_argument("--institutional-manual-seconds", type=float, default=600)


def options(args):
    modes = {}
    for value in args.institutional_provider_mode:
        name, separator, mode = value.partition("=")
        if not separator or name not in PROVIDERS.values() or mode not in {"auto", "public_only"}:
            raise ValueError("institutional provider mode requires a supported PROVIDER=auto or PROVIDER=public_only")
        if name in modes and modes[name] != mode:
            raise ValueError("conflicting institutional provider modes")
        modes[name] = mode
    return {"provider_modes": modes, "acquisition_seconds": args.institutional_acquisition_seconds,
            "manual_seconds": args.institutional_manual_seconds}


def build(args):
    settings = options(args)
    if not args.institutional_browser_url:
        if args.institutional_browser_ui_url:
            raise ValueError("institutional browser UI requires a browser endpoint")
        if (args.institutional_library_url or args.institutional_browser_ui_url or settings["provider_modes"]
                or settings["acquisition_seconds"] != 120 or settings["manual_seconds"] != 600):
            raise ValueError("institutional options require --institutional-browser-url")
        return None
    return InstitutionalAccess(PlaywrightInstitutionalBrowser(args.institutional_browser_url,
        library_url=args.institutional_library_url, ui_url=args.institutional_browser_ui_url), **settings)


def launch_arguments(args):
    result = []
    if args.institutional_browser_url:
        for name in ("browser_url", "browser_ui_url", "library_url", "acquisition_seconds", "manual_seconds"):
            value = getattr(args, "institutional_" + name)
            if value is not None: result += ["--institutional-" + name.replace("_", "-"), str(value)]
        for value in args.institutional_provider_mode:
            result += ["--institutional-provider-mode", value]
    return result
