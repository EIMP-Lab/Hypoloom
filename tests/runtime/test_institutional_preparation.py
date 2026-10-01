import json

import pytest

from hypoloom.institutional_access import InstitutionalAccess, PROVIDERS, article_url
from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser, windows_browser_command
from hypoloom.institutional_prepare import run
from tests.runtime.test_institutional_access import Backend, URL, pump


class PreparationBackend(Backend):
    def prepare(self, provider, **kwargs):
        self.calls.append((provider, kwargs))
        return {"state": "opened"}


def test_empty_catalog_opens_platform_without_admitting_homepage_or_sample_bytes():
    backend = PreparationBackend(); backend.state = "available"
    access = InstitutionalAccess(backend)
    try:
        assert len(access.status("preparation")["groups"]) == len(PROVIDERS)
        access.control("preparation", "Nature", "open")
        pump(access, lambda: access.status("preparation")["groups"][0]["preparation"]["state"] == "opened", runs=("preparation",))
        assert backend.calls[0][0] == "Nature"
        access.control("preparation", "Nature", "verify", url=URL)
        pump(access, lambda: access.status("preparation")["groups"][0]["preparation"]["state"] == "sample_verified", runs=("preparation",))
        status = access.status("preparation")
        assert status["available"] == 0 and all(not g["papers"] for g in status["groups"])
        assert all(p.document is None for p in access._items.values())
        assert "content" not in json.dumps(status) and "Methods" not in json.dumps(status)
        assert len(access._items) == 1, "one preparation per platform"
        with pytest.raises(ValueError): article_url("https://nature.com/")
    finally:
        access.close()


def test_standalone_preparation_has_no_research_or_filesystem_side_effect(tmp_path, monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend(); backend.state = "available"
    access = InstitutionalAccess(backend)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--non-interactive", "--sample", URL,
                                      "--institutional-browser-url", "http://127.0.0.1:9222"])
    output = []
    assert run(args, output=output.append) == 0
    assert '"state": "sample_verified"' in output[-1]
    assert not list(tmp_path.iterdir()) and backend.closed


def test_list_is_read_only_and_browser_options_reject_saml_metadata(monkeypatch):
    from hypoloom.cli import build_parser
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: pytest.fail("catalog connected a browser"))
    output = []
    assert run(build_parser().parse_args(["institutional", "prepare", "--list"]), output=output.append) == 0
    assert len(json.loads(output[0])) == len(PROVIDERS)
    with pytest.raises(ValueError, match="SAML metadata"):
        PlaywrightInstitutionalBrowser("http://127.0.0.1:9222", library_url="https://idp.xidian.edu.cn/idp/shibboleth")
    assert "msedge.exe" in windows_browser_command(9222) and "Test-Path" in windows_browser_command(9222)
