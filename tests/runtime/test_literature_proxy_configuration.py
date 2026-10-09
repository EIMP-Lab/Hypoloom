"""Proxy policy is public; its endpoint stays in the existing private source."""
import json

import pytest
import yaml

from hypoloom import bootstrap, cli
from hypoloom.credentials import CredentialError, read_credentials_file
from hypoloom.institutional_options import build
from hypoloom.institutional_prepare import _remaining_command
from hypoloom.literature_proxy import LiteratureProxyError, proxy_router_from_args
from hypoloom.profile_admission import admit_profile
from test_bootstrap import checkout as checkout, arguments
from test_profile_inline_credentials import private_profile


PROXY = "http://127.0.0.1:17891"
DOMAINS = ["api.openalex.org", "api.crossref.org", "api.semanticscholar.org"]


def _private(tmp_path, proxy=PROXY):
    path = tmp_path / "literature.env"
    path.write_text("OPENALEX_API_KEY=synthetic-openalex\n" + (
        "HYPOLOOM_LITERATURE_PROXY=" + proxy + "\n" if proxy is not None else ""))
    path.chmod(0o600)
    return path


def _args(path=None, domains=(), *, online=True, browser=False):
    argv = ["serve", "--state-root", "/tmp/synthetic-proxy-state",
        "--literature-adapter", "openalex" if online else "offline"]
    if path is not None: argv += ["--credentials-file", str(path)]
    if browser: argv += ["--institutional-browser-mode", "client"]
    argv += [item for domain in domains for item in ("--literature-proxy-domain", domain)]
    return cli.build_parser().parse_args(argv)


def _profile(checkout, capsys, domains=DOMAINS):
    root = private_profile(checkout, capsys)
    path = root / "methodology.yaml"
    value = yaml.safe_load(path.read_text())
    value["literature"].update(proxy_url=PROXY, proxy_domains=domains)
    path.write_text(yaml.safe_dump(value))
    return root, path


def test_inline_proxy_projects_domains_and_source_only(checkout, capsys):
    root, path = _profile(checkout, capsys)
    report = admit_profile(root, run_id="private-proxy")
    assert report.admitted, report.to_dict()
    projection = json.dumps(report.to_dict())
    assert PROXY not in projection and "proxy_url" not in projection
    assert read_credentials_file(str(path))["HYPOLOOM_LITERATURE_PROXY"] == PROXY
    product = report.create_run_payload["research_contract"]["methodology"]["controls"]["product"]
    settings = product["methodology"]["literature"]
    assert settings["proxy_domains"] == DOMAINS
    assert settings["credentials_file"] == str(path)
    args = _args()
    cli._profile_literature_source(args, report)
    assert args.credentials_file == str(path) and args.literature_proxy_domain == DOMAINS
    adapter = cli._literature_adapter(args)
    router = adapter.fetcher._proxy_router
    assert router.for_url("https://api.openalex.org/works") == PROXY
    assert router.for_url("https://sub.api.openalex.org/works") is None
    assert adapter.fallback_locator._proxy_router is router
    assert PROXY not in json.dumps(adapter.configuration) + json.dumps(adapter.preflight())


@pytest.mark.parametrize("domains", ["api.openalex.org", ["*.openalex.org"],
    ["https://api.openalex.org"], ["api.openalex.org:443"], ["api.openalex.org/works"],
    ["127.0.0.1"], ["api.openalex.org."], [""], [False], None])
def test_profile_rejects_non_exact_proxy_domains_without_exposing_endpoint(checkout, capsys, domains):
    root, _ = _profile(checkout, capsys, domains)
    report = admit_profile(root, run_id="invalid-proxy-domains")
    assert not report.admitted
    assert PROXY not in json.dumps(report.to_dict())


def test_init_launch_first_preparation_and_retry_keep_only_domains_and_source(checkout, capsys):
    _, path = _profile(checkout, capsys)
    argv = [*arguments(checkout[0], [], "launch", template=None),
        "--literature-adapter", "openalex", "--institutional-browser-mode", "client",
        "--institutional-prepare-provider", "IEEE"]
    assert bootstrap.main(argv) == 0
    raw = capsys.readouterr().out
    assert PROXY not in raw and "HYPOLOOM_LITERATURE_PROXY" not in raw
    result = json.loads(raw)
    commands = [result["commands"][name] for name in ("host_start", "profile_check", "start")]
    commands += [result["effective_configuration"]["institutional_preparation_argv"]]
    for command in commands:
        assert command[command.index("--credentials-file") + 1] == str(path)
        assert [command[i + 1] for i, value in enumerate(command) if value == "--literature-proxy-domain"] == DOMAINS
    args = cli.build_parser().parse_args(commands[-1][3:])
    retry = _remaining_command(args, ["IEEE"], {"IEEE": "auto"})
    retry_args = cli.build_parser().parse_args(retry[4:])
    assert retry_args.credentials_file == str(path)
    assert retry_args.literature_proxy_domain == DOMAINS
    assert PROXY not in json.dumps(retry)
    access = build(retry_args)
    try:
        access.sync({"preparation"})
        locator = access.instances["preparation"].backend._pdf_locator
        assert locator._proxy_router.for_url("https://api.semanticscholar.org/graph/v1/paper/example") == PROXY
        assert locator._proxy_router.for_url("https://other.example/paper") is None
        assert PROXY not in json.dumps(access.configuration)
    finally:
        access.close()


@pytest.mark.parametrize("path_present", [False, True])
def test_no_proxy_configuration_stays_direct_despite_ambient_proxy(tmp_path, monkeypatch, path_present):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "HYPOLOOM_LITERATURE_PROXY"):
        monkeypatch.setenv(name, PROXY)
    path = _private(tmp_path, proxy=None) if path_present else None
    args = _args(path)
    assert proxy_router_from_args(args) is None
    if path_present:
        adapter = cli._literature_adapter(args)
        assert adapter.fetcher._proxy_router is None
        assert adapter.fallback_locator._proxy_router is None
        assert "proxy_domains" not in adapter.configuration


@pytest.mark.parametrize("kind", ["domains-only", "endpoint-only", "offline-domains"])
def test_incomplete_proxy_configuration_fails_before_network(tmp_path, kind):
    path = _private(tmp_path, proxy=None if kind == "domains-only" else PROXY)
    args = _args(path, DOMAINS if kind != "endpoint-only" else (), online=kind != "offline-domains")
    with pytest.raises(ValueError) as error:
        cli._literature_adapter(args)
    assert PROXY not in str(error.value)


@pytest.mark.parametrize("damage", ["remove-key", "permissions", "invalid-origin", "missing-file"])
def test_proxy_rereads_private_source_and_never_falls_back_direct(tmp_path, monkeypatch, damage):
    path = _private(tmp_path)
    monkeypatch.setenv("HYPOLOOM_LITERATURE_PROXY", "http://127.0.0.1:18999")
    router = proxy_router_from_args(_args(path, DOMAINS))
    uri = "https://api.openalex.org/works"
    assert router.for_url(uri) == PROXY
    rotated = "http://127.0.0.1:17892"
    path.write_text(path.read_text().replace(PROXY, rotated))
    assert router.for_url(uri) == rotated
    if damage == "remove-key": path.write_text("OPENALEX_API_KEY=synthetic-openalex\n")
    elif damage == "permissions": path.chmod(0o644)
    elif damage == "invalid-origin": path.write_text(path.read_text().replace(rotated, "http://private:secret@127.0.0.1:17892"))
    else: path.unlink()
    assert router.for_url("https://unselected.example/paper") is None
    with pytest.raises(LiteratureProxyError) as error:
        router.for_url(uri)
    assert all(secret not in str(error.value) for secret in (PROXY, rotated, "private:secret"))


def test_proxy_private_source_permissions_are_checked_before_build(tmp_path):
    path = _private(tmp_path)
    path.chmod(0o644)
    with pytest.raises(CredentialError, match="0600"):
        cli._literature_adapter(_args(path, DOMAINS))


def test_cli_domains_cannot_silently_override_profile_policy(checkout, capsys):
    root, _ = _profile(checkout, capsys)
    report = admit_profile(root, run_id="proxy-conflict")
    assert report.admitted, report.to_dict()
    with pytest.raises(ValueError, match="conflict"):
        cli._profile_literature_source(_args(domains=["different.example"]), report)


def test_proxy_endpoint_cannot_be_put_in_bootstrap_set(checkout, capsys):
    repo, overrides = checkout
    assert bootstrap.main(arguments(repo, [*overrides, "methodology.literature.proxy_url=" + PROXY])) == 2
    output = capsys.readouterr()
    assert PROXY not in output.err + output.out
    assert "private Profile" in output.err


@pytest.mark.parametrize("mode", ["auto", "proxy_only"])
def test_default_route_and_direct_overrides_survive_profile_launch_and_preparation(checkout, capsys, mode):
    root, path = _profile(checkout, capsys, domains=["required.example.org"])
    content = yaml.safe_load(path.read_text())
    content["literature"].update(default_route=mode, direct_domains=["direct.example.org"])
    path.write_text(yaml.safe_dump(content))
    report = admit_profile(root, run_id="auto-routing")
    assert report.admitted, report.to_dict()
    argv = [*arguments(checkout[0], [], "launch", template=None),
        "--literature-adapter", "openalex", "--institutional-browser-mode", "client",
        "--institutional-prepare-provider", "IEEE"]
    assert bootstrap.main(argv) == 0
    raw = capsys.readouterr().out
    assert PROXY not in raw
    result = json.loads(raw)
    commands = [result["commands"][key] for key in ("host_start", "profile_check", "start")]
    commands.append(result["effective_configuration"]["institutional_preparation_argv"])
    for command in commands:
        assert command[command.index("--literature-default-route")+1] == mode
        assert command[command.index("--literature-direct-domain")+1] == "direct.example.org"
    args = cli.build_parser().parse_args(commands[-1][3:])
    retry = _remaining_command(args, ["IEEE"], {"IEEE": "auto"})
    router = proxy_router_from_args(cli.build_parser().parse_args(retry[4:]))
    assert router.policy("https://required.example.org") == "proxy_only"
    assert router.policy("https://direct.example.org") == "direct"
    assert router.policy("https://unknown.example.org") == mode


def test_auto_with_endpoint_needs_no_user_domain_inventory(tmp_path):
    args = _args(_private(tmp_path))
    args.literature_default_route = "auto"
    router = cli._literature_adapter(args).fetcher._proxy_router
    assert router.policy("https://arxiv.org") == "auto"
    assert router.configuration["proxy_domains"] == []
    args.literature_direct_domain = ["arxiv.org"]
    args.literature_proxy_domain = ["arxiv.org"]
    with pytest.raises(LiteratureProxyError, match="overlap"):
        proxy_router_from_args(args)


def test_profile_rejects_conflicting_domain_routes_before_launch(checkout, capsys):
    root, path = _profile(checkout, capsys, domains=["Arxiv.org"])
    value = yaml.safe_load(path.read_text())
    value["literature"]["direct_domains"] = ["arxiv.org"]
    path.write_text(yaml.safe_dump(value))
    report = admit_profile(root, run_id="conflicting-routes")
    assert not report.admitted
    assert "overlap" in json.dumps(report.to_dict()) and PROXY not in json.dumps(report.to_dict())
