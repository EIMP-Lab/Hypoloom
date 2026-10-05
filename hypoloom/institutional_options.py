"""Shared launch arguments; reviewing configuration never connects a browser."""
from .institutional_access import InstitutionalAccess, PROVIDERS, PROVIDER_MODES, provider_catalog, provider_name, publisher
from .institutional_browser import PlaywrightInstitutionalBrowser


def add_arguments(parser, *, connection=False):
    parser.add_argument("--institutional-browser-url", help="opt-in loopback Chromium CDP endpoint")
    parser.add_argument("--institutional-browser-ui-url", help="loopback server-browser GUI origin")
    parser.add_argument("--institutional-library-url", help="HTTPS interactive library entry, never a SAML metadata endpoint")
    parser.add_argument("--institutional-provider-mode", action="append", default=[], metavar="PROVIDER=MODE",
                        help="auto, public_only or disabled; unselected providers are disabled")
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
        if not separator or name not in PROVIDERS.values() or mode not in PROVIDER_MODES:
            raise ValueError("institutional provider mode requires a supported PROVIDER=auto, public_only or disabled")
        if name in modes and modes[name] != mode:
            raise ValueError("conflicting institutional provider modes")
        modes[name] = mode
    # Preparation selection must survive the generated launch/Host argv. The
    # routing catalog itself does not opt the remaining publishers in.
    if getattr(args, "institutional_action", None) == "prepare":
        from .institutional_prepare import sample_url
        samples = [publisher(sample_url(value)) for value in args.sample]
        values = args.provider if args.provider is not None else (samples or None)
        selected = [*preparation_selection(values, modes), *samples]
    elif hasattr(args, "institutional_prepare_provider") and (
        args.institutional_browser_url or getattr(args, "institutional_browser_mode", None) not in (None, "off")
    ):
        selected = preparation_selection(args.institutional_prepare_provider, modes)
    else:
        selected = []
    for name in selected:
        modes.setdefault(name, "auto")
    return {"provider_modes": modes, "acquisition_seconds": args.institutional_acquisition_seconds,
            "manual_seconds": args.institutional_manual_seconds}


def add_preparation_argument(parser, flag="--institutional-prepare-provider"):
    parser.add_argument(flag, action="append", type=provider_name, choices=(*PROVIDERS.values(), "none"),
                        help="replace common preparation defaults; repeat for platforms, Elsevier aliases ScienceDirect; none skips preparation")
    parser.add_argument("--institutional-prepare-failure-mode", choices=("review", "public_only"), default="review",
                        help="review failures, or use public_only for failed/unvisited platforms before launch review")


def preparation_selection(values, modes):
    if values is None:
        return [p["provider"] for p in provider_catalog()
                if p["recommended_prepare"] and modes.get(p["provider"]) not in {"public_only", "disabled"}]
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
    selection = getattr(args, "institutional_prepare_provider", None)
    selected = preparation_selection(selection, modes)
    api_providers = []
    source = getattr(args, "credentials_file", None)
    if source and getattr(args, "literature_adapter", None) == "openalex":
        from .credentials import ELSEVIER_API_KEY, read_credentials_file
        if read_credentials_file(source).get(ELSEVIER_API_KEY):
            api_providers = ["ScienceDirect"]
            if selection is None:
                selected = [name for name in selected if name not in api_providers]
    return {"selected": selected, "library_url": args.institutional_library_url,
            "failure_mode": args.institutional_prepare_failure_mode,
            **({"api_providers": api_providers, "api_full_text_access": "not_checked"} if api_providers else {}),
            "catalog": [{**p, "selected": p["provider"] in selected,
                         "mode": modes.get(p["provider"], "disabled"), "state": "not_checked"}
                        for p in provider_catalog()]}


def build(args, *, pdf_locator=None):
    from .institutional_connection import configure
    configure(args)
    settings = options(args)
    if getattr(args, "institutional_browser_mode", None) not in (None, "off"):
        from .institutional_connection import runtime_plan
        from .institutional_runtime import RunAccess
        return RunAccess(runtime_plan(args), pdf_locator=pdf_locator if pdf_locator is not None else _pdf_locator(args), **settings)
    if not args.institutional_browser_url:
        if args.institutional_browser_ui_url:
            raise ValueError("institutional browser UI requires a browser endpoint")
        if (args.institutional_library_url or args.institutional_browser_ui_url or settings["provider_modes"]
                or settings["acquisition_seconds"] != 120 or settings["manual_seconds"] != 600):
            raise ValueError("institutional options require an enabled browser mode or --institutional-browser-url")
        return None
    return InstitutionalAccess(PlaywrightInstitutionalBrowser(args.institutional_browser_url,
        library_url=args.institutional_library_url, ui_url=args.institutional_browser_ui_url,
        pdf_locator=pdf_locator if pdf_locator is not None else _pdf_locator(args)), **settings)


def _pdf_locator(args):
    """Runtime and preparation use the selected locator with Host-only secrets."""
    from .credentials import (SEMANTIC_SCHOLAR_API_KEY_KEY, UNPAYWALL_EMAIL_KEY,
        credential, optional_credential_loader, read_credentials_file)
    from .literature import SemanticScholarPdfLocator, UnpaywallPdfLocator
    source = getattr(args, "credentials_file", None)
    if getattr(args, "literature_pdf_fallback", "semantic-scholar") == "unpaywall":
        email_file = getattr(args, "unpaywall_email_file", None)
        if source and email_file:
            raise ValueError("use exactly one of --unpaywall-email-file or UNPAYWALL_EMAIL in --credentials-file")
        return UnpaywallPdfLocator(email=credential(read_credentials_file(source), UNPAYWALL_EMAIL_KEY)
            if source else None, email_file=email_file, timeout_seconds=5)
    if getattr(args, "unpaywall_email_file", None) is not None:
        raise ValueError("Unpaywall options require pdf_fallback='unpaywall'")
    return SemanticScholarPdfLocator(timeout_seconds=5,
        api_key_loader=optional_credential_loader(source, SEMANTIC_SCHOLAR_API_KEY_KEY))


def preparation_literature_arguments(args):
    """Carry only existing source paths and fallback choice into prepare/retry."""
    result = []
    for name in ("credentials_file", "literature_pdf_fallback", "unpaywall_email_file"):
        value = getattr(args, name, None)
        if value is not None:
            result += ["--" + name.replace("_", "-"), str(value)]
    return result


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


def launch_arguments(args, *, include_connection=False, include_preparation=False):
    result = []
    mode = getattr(args, "institutional_browser_mode", None)
    if args.institutional_browser_url or mode not in (None, "off"):
        for name in ("browser_url", "browser_ui_url", "library_url", "acquisition_seconds", "manual_seconds"):
            value = getattr(args, "institutional_" + name)
            if value is not None: result += ["--institutional-" + name.replace("_", "-"), str(value)]
        for name, value in options(args)["provider_modes"].items():
            result += ["--institutional-provider-mode", f"{name}={value}"]
    if include_connection:
        from .institutional_connection import arguments
        result += arguments(args)
    if include_preparation and args.institutional_prepare_failure_mode != "review":
        result += ["--institutional-prepare-failure-mode", args.institutional_prepare_failure_mode]
    return result
