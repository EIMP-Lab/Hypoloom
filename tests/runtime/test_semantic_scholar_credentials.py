"""Optional S2 credentials share the existing private source and never enter argv."""
import json

import pytest
import yaml

from hypoloom import bootstrap, cli
from hypoloom.credentials import CredentialError, read_credentials_file
from hypoloom.profile_admission import admit_profile
from test_bootstrap import checkout as checkout, arguments
from test_profile_inline_credentials import private_profile


KEY = "synthetic-semantic-scholar-private"


def _private(tmp_path, *, key=KEY):
    path = tmp_path / "credentials.env"
    path.write_text("OPENALEX_API_KEY=synthetic-openalex\nUNPAYWALL_EMAIL=contact@example.invalid\n"
                    + ("SEMANTIC_SCHOLAR_API_KEY=" + key + "\n" if key is not None else ""))
    path.chmod(0o600)
    return path


def _args(path=None, *, fallback="semantic-scholar", browser=False):
    values = ["serve", "--state-root", "/tmp/synthetic-state", "--literature-adapter", "openalex",
              "--literature-pdf-fallback", fallback]
    if path is not None:
        values += ["--credentials-file", str(path)]
    if browser:
        values += ["--institutional-browser-mode", "client"]
    return cli.build_parser().parse_args(values)


class Transport:
    def __init__(self):
        self.requests = []

    def get(self, **request):
        from hypoloom.literature import OpenAlexResponse
        self.requests.append(request)
        return OpenAlexResponse(status_code=404, media_type="application/json", content_length=2, body=b"{}")


def _lookup(locator, transport):
    locator._transport = transport
    assert locator.locate_doi("10.1234/example") == ()


def test_profile_key_is_private_and_projection_only_retains_existing_source(checkout, capsys):
    root = private_profile(checkout, capsys)
    path = root / "methodology.yaml"
    document = yaml.safe_load(path.read_text())
    document["literature"]["semantic_scholar_api_key"] = KEY
    path.write_text(yaml.safe_dump(document))
    report = admit_profile(root, run_id="s2-private")
    assert report.admitted, report.to_dict()
    projection = json.dumps(report.to_dict())
    assert KEY not in projection
    assert "semantic_scholar_api_key" not in projection
    assert read_credentials_file(str(path))["SEMANTIC_SCHOLAR_API_KEY"] == KEY
    args = _args()
    cli._profile_literature_source(args, report)
    assert args.credentials_file == str(path)
    adapter = cli._literature_adapter(args)
    transport = Transport()
    _lookup(adapter.fallback_locator, transport)
    assert transport.requests[0]["headers"]["x-api-key"] == KEY
    assert KEY not in json.dumps(adapter.configuration)


def test_runtime_rereads_only_explicit_source_and_never_downgrades_a_configured_key(tmp_path, monkeypatch):
    path = _private(tmp_path)
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "ambient-must-not-be-used")
    adapter = cli._literature_adapter(_args(path))
    transport = Transport()
    _lookup(adapter.fallback_locator, transport)
    assert transport.requests[0]["headers"]["x-api-key"] == KEY
    path.write_text(path.read_text().replace(KEY, "rotated-private-key"))
    _lookup(adapter.fallback_locator, transport)
    assert transport.requests[-1]["headers"]["x-api-key"] == "rotated-private-key"
    path.write_text("OPENALEX_API_KEY=synthetic-openalex\n")
    with pytest.raises(ValueError) as error:
        _lookup(adapter.fallback_locator, transport)
    assert len(transport.requests) == 2
    assert KEY not in str(error.value) and "rotated-private-key" not in str(error.value)


@pytest.mark.parametrize("key", [None, ""])
def test_missing_optional_key_stays_anonymous_despite_environment(tmp_path, monkeypatch, key):
    path = _private(tmp_path, key=key)
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", KEY)
    adapter = cli._literature_adapter(_args(path))
    transport = Transport()
    _lookup(adapter.fallback_locator, transport)
    assert "x-api-key" not in transport.requests[0]["headers"]


def test_private_permission_and_source_loss_do_not_leak_or_send_anonymous(tmp_path):
    path = _private(tmp_path)
    adapter = cli._literature_adapter(_args(path))
    transport = Transport()
    path.chmod(0o644)
    with pytest.raises(ValueError) as error:
        _lookup(adapter.fallback_locator, transport)
    assert not transport.requests and KEY not in str(error.value)
    with pytest.raises(CredentialError, match="0600"):
        cli._literature_adapter(_args(path))


def test_run_browser_receives_private_locator_without_changing_configuration(tmp_path):
    from hypoloom.institutional_options import build
    path = _private(tmp_path)
    access = build(_args(path, browser=True))
    try:
        access.sync({"fixture"})
        backend = access.instances["fixture"].backend
        transport = Transport()
        _lookup(backend._pdf_locator, transport)
        assert transport.requests[0]["headers"]["x-api-key"] == KEY
        assert KEY not in json.dumps(access.configuration)
        assert "pdf_locator" not in access.configuration
    finally:
        access.close()


def test_host_reuses_literature_locator_across_browser_runs(tmp_path):
    from hypoloom.institutional_options import build
    path = _private(tmp_path)
    args = _args(path, browser=True)
    locator = cli._literature_adapter(args).fallback_locator
    access = build(args, pdf_locator=locator)
    try:
        access.sync({"first", "second"})
        assert all(item.backend._pdf_locator is locator for item in access.instances.values())
    finally:
        access.close()


def test_prepare_and_retry_argv_preserve_source_and_selected_fallback(checkout, capsys):
    from hypoloom.institutional_options import build
    from hypoloom.institutional_prepare import _remaining_command
    root = private_profile(checkout, capsys)
    path = root / "methodology.yaml"
    document = yaml.safe_load(path.read_text())
    document["literature"]["semantic_scholar_api_key"] = KEY
    path.write_text(yaml.safe_dump(document))
    values = [*arguments(checkout[0], [], "launch", template=None), "--literature-adapter", "openalex",
              "--institutional-browser-mode", "client", "--institutional-prepare-provider", "IEEE"]
    assert bootstrap.main(values) == 0
    raw = capsys.readouterr().out
    assert KEY not in raw
    command = json.loads(raw)["effective_configuration"]["institutional_preparation_argv"]
    args = cli.build_parser().parse_args(command[3:])
    assert args.credentials_file == str(path)
    assert args.literature_pdf_fallback == "semantic-scholar"
    retry = _remaining_command(args, ["IEEE"], {"IEEE": "auto"})
    retry_args = cli.build_parser().parse_args(retry[4:])
    assert retry_args.credentials_file == str(path)
    access = build(retry_args)
    try:
        access.sync({"preparation"})
        transport = Transport()
        _lookup(access.instances["preparation"].backend._pdf_locator, transport)
        assert transport.requests[0]["headers"]["x-api-key"] == KEY
    finally:
        access.close()


def test_unselected_s2_credentials_do_not_reach_either_locator(tmp_path):
    from hypoloom.institutional_options import build
    from hypoloom.literature import UnpaywallPdfLocator
    path = _private(tmp_path)
    args = _args(path, fallback="unpaywall", browser=True)
    assert isinstance(cli._literature_adapter(args).fallback_locator, UnpaywallPdfLocator)
    access = build(args)
    try:
        access.sync({"fixture"})
        locator = access.instances["fixture"].backend._pdf_locator
        assert isinstance(locator, UnpaywallPdfLocator)
        transport = Transport()
        _lookup(locator, transport)
        assert "x-api-key" not in transport.requests[0]["headers"]
    finally:
        access.close()
