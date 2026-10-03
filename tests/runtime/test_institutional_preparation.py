import json
import subprocess
import sys
from time import monotonic

import pytest

from hypoloom.institutional_access import InstitutionalAccess, PROVIDERS, article_url
from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser, windows_browser_command
from hypoloom.institutional_prepare import run
from tests.runtime.test_institutional_access import Backend, URL, pump


class PreparationBackend(Backend):
    def check_connection(self, **kwargs):
        return {"state": "ready"}

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


def test_documented_module_entrypoint_lists_platforms_outside_repository(tmp_path):
    result = subprocess.run([sys.executable, "-I", "-B", "-m", "hypoloom", "institutional", "prepare", "--list"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert {item["provider"] for item in json.loads(result.stdout)} == set(PROVIDERS.values())
    assert not list(tmp_path.iterdir())


def test_readable_sample_reuses_session_without_login_prompt(tmp_path, monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend(); backend.state = "available"
    access = InstitutionalAccess(backend)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--sample", URL,
                                      "--institutional-browser-url", "http://127.0.0.1:9222"])
    output = []
    assert run(args, input_fn=lambda _: pytest.fail("readable sample prompted login"), output=output.append) == 0
    assert [call[0] for call in backend.calls] == [URL]
    assert json.loads(output[-1])["preparation_summary"][0]["state"] == "sample_verified"
    assert not list(tmp_path.iterdir()) and backend.closed


def test_common_preparation_supports_skip_public_only_and_no_false_login(monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend()
    access = InstitutionalAccess(backend)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--institutional-browser-url", "http://127.0.0.1:9222"])
    answers = iter(["s", "p", ""])
    output = []
    assert run(args, input_fn=lambda _: next(answers), output=output.append) == 0
    assert [call[0] for call in backend.calls] == ["IEEE", "ScienceDirect", "Springer"]
    summary = json.loads(output[-1])
    assert [(p["provider"], p["state"]) for p in summary["preparation_summary"]] == [
        ("IEEE", "skipped"), ("ScienceDirect", "public_only"), ("Springer", "not_checked")]
    assert summary["provider_mode_argv"] == ["--institutional-provider-mode", "ScienceDirect=public_only"]
    assert backend.closed


def test_prepare_none_is_read_only_and_rejects_mixed_selection(monkeypatch):
    from hypoloom.cli import build_parser
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: pytest.fail("skip connected browser"))
    parser = build_parser()
    assert run(parser.parse_args(["institutional", "prepare", "--provider", "none"]), output=lambda _: None) == 0
    with pytest.raises(ValueError, match="none"):
        run(parser.parse_args(["institutional", "prepare", "--provider", "none", "--provider", "IEEE"]))


def test_partial_samples_retry_only_gaps_after_operator_action(monkeypatch):
    from hypoloom.cli import build_parser
    from tests.runtime.test_institutional_access import SECOND, document
    class Mixed(PreparationBackend):
        def fetch(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return {"state": "available" if url == URL or self.state == "available" else "login_required",
                    "document": document(url)}
    backend = Mixed()
    access = InstitutionalAccess(backend)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--sample", URL, "--sample", SECOND])
    prompts = []
    def login(prompt):
        prompts.append(prompt)
        backend.state = "available"
        return ""
    output = []
    assert run(args, input_fn=login, output=output.append) == 0
    assert [call[0] for call in backend.calls] == [URL, SECOND, "Nature", SECOND]
    assert len(prompts) == 1
    row = json.loads(output[-1])["preparation_summary"][0]
    assert row["state"] == "sample_verified" and len(row["samples"]) == 2
    assert all(p.document is None for p in access._items.values())


@pytest.mark.parametrize("state", ["access_denied", "login_required", "verification_required", "no_full_text"])
def test_unavailable_sample_is_reported_and_other_platforms_continue(monkeypatch, state):
    from hypoloom.cli import build_parser
    backend = PreparationBackend(); backend.state = state
    access = InstitutionalAccess(backend)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--provider", "Nature", "--provider", "IEEE", "--sample", URL])
    output = []
    assert run(args, input_fn=lambda _: "", output=output.append) == 1
    rows = json.loads(output[-1])["preparation_summary"]
    assert [(r["provider"], r["state"]) for r in rows] == [("Nature", state), ("IEEE", "not_checked")]
    remaining = build_parser().parse_args(json.loads(output[-1])["remaining_preparation_argv"][4:])
    assert remaining.provider == ["Nature"] and remaining.sample == [URL]
    assert backend.closed


def test_public_only_platform_never_connects_and_quit_leaves_others_unchecked(monkeypatch):
    from hypoloom.cli import build_parser
    parser = build_parser()
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: pytest.fail("public only opened browser"))
    output = []
    args = parser.parse_args(["institutional", "prepare", "--provider", "Elsevier",
                              "--institutional-provider-mode", "Elsevier=public_only"])
    assert run(args, output=output.append) == 0
    assert json.loads(output[-1])["provider_modes"] == {"ScienceDirect": "public_only"}
    backend = PreparationBackend()
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: InstitutionalAccess(backend))
    assert run(parser.parse_args(["institutional", "prepare"]), input_fn=lambda _: "q", output=output.append) == 0
    assert [call[0] for call in backend.calls] == ["IEEE"]
    assert [r["state"] for r in json.loads(output[-1])["preparation_summary"]] == ["skipped", "not_checked", "not_checked"]
    assert backend.closed


def test_interrupted_preparation_closes_access(monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend()
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: InstitutionalAccess(backend))
    def interrupt(_): raise EOFError()
    assert run(build_parser().parse_args(["institutional", "prepare"]),
        input_fn=interrupt, output=lambda _: None) == 1
    assert backend.closed


@pytest.mark.parametrize("ending", ["timeout", "eof", "finish", "input_timeout"])
def test_failed_platforms_remain_retryable_after_completion_or_interruption(tmp_path, monkeypatch, ending):
    from hypoloom.cli import build_parser
    from hypoloom.institutional_dialogue import PreparationEnded
    class Failing(PreparationBackend):
        def prepare(self, provider, **kwargs):
            self.calls.append((provider, kwargs))
            return {"state": "browser_unavailable"} if provider == "ScienceDirect" or (
                ending == "timeout" and provider == "Springer") else {"state": "opened"}
    backend = Failing()
    access = InstitutionalAccess(backend, clock=lambda: monotonic() * 100, acquisition_seconds=30, poll_seconds=1)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda _: access)
    parser = build_parser()
    args = parser.parse_args(["institutional", "prepare", "--repository", str(tmp_path),
        "--institutional-browser-url", "http://127.0.0.1:9222", "--institutional-manual-seconds", "80",
        "--institutional-provider-mode", "ACM=public_only"])
    prompts, output = [], []
    def answer(prompt):
        prompts.append(prompt.split(':')[0])
        if len(prompts) == 1: return ""  # IEEE completion without a sample remains not_checked.
        if ending == "eof": raise EOFError
        raise PreparationEnded("finished" if ending == "finish" else "input_timeout")
    assert run(args, input_fn=answer, output=output.append) == (0 if ending == "finish" else 1)
    summary = json.loads(output[-1])
    remaining = parser.parse_args(summary["remaining_preparation_argv"][4:])
    assert remaining.provider == ["ScienceDirect", "Springer"]
    assert remaining.institutional_provider_mode == ["ACM=public_only"]
    assert remaining.institutional_browser_url == args.institutional_browser_url
    assert remaining.institutional_manual_seconds == 80 and remaining.repository == str(tmp_path)
    assert summary["preparation_summary"][0] == {"provider": "IEEE", "state": "not_checked", "samples": []}
    assert summary["preparation_summary"][1]["state"] == "acquisition_timeout"
    assert backend.closed and not list(tmp_path.iterdir())
    if ending == "timeout":
        recovered = PreparationBackend()
        monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda _: InstitutionalAccess(recovered))
        retried = []
        assert run(remaining, input_fn=lambda _: "", output=retried.append) == 0
        assert [call[0] for call in recovered.calls] == ["ScienceDirect", "Springer"]
        assert json.loads(retried[-1])["remaining_preparation_argv"] == []
        assert recovered.closed and not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["browser", "navigation", "cdp_timeout", "cdp_connection_closed",
    "cdp_endpoint_unavailable", "browser_target_closed", "browser_operation_timeout", "browser_operation_failed",
    "https://sso.invalid/?secret=never-export-this"])
def test_preparation_reports_safe_intermediate_failure_before_timeout(monkeypatch, failure):
    from hypoloom.cli import build_parser
    class Failing(PreparationBackend):
        def prepare(self, provider, **kwargs):
            if failure == "browser": raise RuntimeError("https://sso.invalid/?secret=never-export-this")
            if failure == "navigation": return {"state": "fetch_unavailable", "reason": "dns_failed"}
            return {"state": "browser_unavailable", "reason": failure}
    backend = Failing()
    access = InstitutionalAccess(backend, clock=lambda: monotonic() * 100, acquisition_seconds=30, poll_seconds=1)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda _: access)
    output = []
    args = build_parser().parse_args(["institutional", "prepare", "--provider", "ScienceDirect"])
    assert run(args, input_fn=lambda _: pytest.fail("failed browser requested login"), output=output.append) == 1
    events = [json.loads(v) for v in output if v.startswith('{')]
    state, reason = ("fetch_unavailable", "dns_failed") if failure == "navigation" else (
        "browser_unavailable", "browser_unavailable" if failure == "browser" or failure.startswith("https:") else failure)
    failures = [e for e in events if e.get("state") == state]
    assert len(failures) == 1 and failures[0]["reason_code"] == reason
    assert failures[0]["provider"] == "ScienceDirect"
    assert events[-1]["preparation_summary"][0]["state"] == "acquisition_timeout"
    assert "never-export-this" not in "\n".join(output) and backend.closed
