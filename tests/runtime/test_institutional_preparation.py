import json
import subprocess
import sys
from time import monotonic

import pytest

from hypoloom.institutional_access import InstitutionalAccess, PROVIDERS, article_url, provider_catalog
from hypoloom.institutional_browser import PlaywrightInstitutionalBrowser, windows_browser_command
from hypoloom.institutional_prepare import run
from tests.runtime.test_institutional_access import ALL_PROVIDERS_AUTO, Backend, URL, pump


class PreparationBackend(Backend):
    def fetch(self, url, **kwargs):
        result = super().fetch(url, **kwargs)
        if url.startswith('https://doi.org/'):
            doi = url.removeprefix('https://doi.org/')
            result['document']['pages'] = (f'Synthetic full text. DOI {doi}',)
        return result

    def check_connection(self, **kwargs):
        return {"state": "ready"}

    def prepare(self, provider, **kwargs):
        self.calls.append((provider, kwargs))
        return {"state": "opened"}


def test_all_nine_publishers_advance_on_one_worker_with_bounded_owned_tabs(tmp_path, monkeypatch):
    """Real queue/preparation/browser tab lifecycle, synthetic page transport."""
    import threading
    from hypoloom.cli import build_parser
    class Page:
        def __init__(self): self.closed = False
        def is_closed(self): return self.closed
        def close(self, **kwargs): self.closed = True
        def set_default_timeout(self, *args): pass
        def bring_to_front(self): pass
        def evaluate(self, *args): return {}
    class Browser(PlaywrightInstitutionalBrowser):
        def __init__(self):
            super().__init__("http://127.0.0.1:9222")
            self.threads, self.visited, self.peaks = set(), [], []
            self.foreign = Page()
        def check_connection(self, **kwargs): return {"state": "ready"}
        def _connect(self): return self
        def new_page(self): return Page()
        def _navigate(self, *args): return None
        def prepare(self, provider, **kwargs):
            self.threads.add(threading.get_ident())
            result = super().prepare(provider, **kwargs)
            self.visited.append(provider); self.peaks.append(len(self._tabs))
            return result
        def finish_preparation(self, provider):
            self.threads.add(threading.get_ident())
            super().finish_preparation(provider)
    browser = Browser()
    access = InstitutionalAccess(browser, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    providers = [p["provider"] for p in provider_catalog()]
    args = build_parser().parse_args(["institutional", "prepare", "--repository", str(tmp_path),
        "--institutional-browser-url", "http://127.0.0.1:9222",
        *[part for provider in providers for part in ("--provider", provider)]])
    output = []
    assert run(args, input_fn=lambda _: "p", output=output.append) == 0
    summary = json.loads(output[-1])
    assert browser.visited == providers and len(browser.threads) == 1
    assert threading.get_ident() not in browser.threads and max(browser.peaks) == 8
    assert [row["provider"] for row in summary["preparation_summary"]] == providers
    assert set(summary["provider_modes"].values()) == {"public_only"}
    assert summary["remaining_providers"] == [] and not browser.foreign.closed


def test_empty_catalog_opens_platform_without_admitting_homepage_or_sample_bytes():
    backend = PreparationBackend(); backend.state = "available"
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
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


def test_springer_entry_is_institution_discovery_not_a_document_source():
    from hypoloom.institutional_access import publisher
    entry = 'https://wayf.springernature.com/?redirect_uri=https%3A%2F%2Flink.springer.com%2F'
    selected = next(p for p in provider_catalog() if p['provider'] == 'Springer')
    assert selected['entry_url'] == entry
    access = InstitutionalAccess(PreparationBackend(), provider_modes=ALL_PROVIDERS_AUTO)
    try:
        result = access.control('preparation', 'Springer', 'open')
        assert next(g for g in result['groups'] if g['provider'] == 'Springer')['entry_url'] == entry
        assert next(iter(access._items.values())).url == entry
        assert publisher(entry) is None
        with pytest.raises(ValueError): article_url(entry)
        with pytest.raises(ValueError): access.control('preparation', 'Springer', 'verify', url=entry)
    finally:
        access.close()


def test_standalone_preparation_has_no_research_or_filesystem_side_effect(tmp_path, monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend(); backend.state = "available"
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
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
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--sample", URL,
                                      "--institutional-browser-url", "http://127.0.0.1:9222"])
    output = []
    assert run(args, input_fn=lambda _: pytest.fail("readable sample prompted login"), output=output.append) == 0
    assert [call[0] for call in backend.calls] == [URL]
    assert json.loads(output[-1])["preparation_summary"][0]["state"] == "sample_verified"
    assert not list(tmp_path.iterdir()) and backend.closed


@pytest.mark.parametrize('provider', ['IEEE', 'ScienceDirect', 'Springer', 'ACM'])
def test_completion_automatically_checks_one_public_doi_without_claiming_subscription(tmp_path, monkeypatch, provider):
    from hypoloom.cli import build_parser
    from hypoloom.institutional_access import publisher
    backend = PreparationBackend(); backend.state = 'available'
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    sample = next(p['default_sample_url'] for p in provider_catalog() if p['provider'] == provider)
    assert article_url(sample) == sample and publisher(sample) == provider
    prompts = []
    def complete(prompt):
        assert [c[0] for c in backend.calls] == [provider], 'default OA probe must not bypass manual preparation'
        assert sample in prompt
        prompts.append(prompt)
        return ''
    output = []
    assert run(build_parser().parse_args(['institutional', 'prepare', '--provider', provider]),
               input_fn=complete, output=output.append) == 0
    assert len(prompts) == 1 and [c[0] for c in backend.calls] == [provider, sample]
    summary = json.loads(output[-1])
    row = summary['preparation_summary'][0]
    assert row['state'] == 'sample_verified' and len(row['samples']) == 1
    assert row['samples'][0]['sample_scope'] == 'public_full_text'
    assert summary['provider_modes'] == {provider: 'auto'} and 'not established' in summary['verification_scope']
    assert all(p.document is None for p in access._items.values())
    assert not list(tmp_path.iterdir()) and backend.closed


@pytest.mark.parametrize('explicit', [False, True])
def test_user_sample_overrides_fixed_sample_for_its_platform(monkeypatch, explicit):
    from hypoloom.cli import build_parser
    backend = PreparationBackend(); backend.state = 'available'
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    custom = 'https://doi.org/10.1109/fixture.2026.001'
    output = []
    args = build_parser().parse_args(['institutional', 'prepare', '--provider', 'IEEE',
        *(['--sample', custom] if explicit else [])])
    assert run(args, input_fn=lambda _: custom, output=output.append) == 0
    assert [c[0] for c in backend.calls] == ([custom] if explicit else ['IEEE', custom])
    assert json.loads(output[-1])['preparation_summary'][0]['samples'][0]['sample_scope'] == 'article_full_text'


def test_default_probe_rejects_wrong_document_and_retains_failed_provider_for_retry(monkeypatch):
    from hypoloom.cli import build_parser
    class WrongPDF(PreparationBackend):
        def fetch(self, url, **kwargs):
            result = super().fetch(url, **kwargs)
            result['document']['pages'] = ('DOI 10.1109/wrong.2026.001',)
            return result
    backend = WrongPDF(); backend.state = 'available'
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    parser = build_parser(); output = []
    assert run(parser.parse_args(['institutional', 'prepare', '--provider', 'IEEE']),
               input_fn=lambda _: '', output=output.append) == 1
    summary = json.loads(output[-1])
    assert summary['preparation_summary'][0]['state'] == 'identity_unconfirmed'
    assert summary['preparation_summary'][0]['samples'][0]['reason_code'] == 'document_validation_failed'
    assert '论文身份' in output[-2] and '机构账号' in output[-2]
    assert '取得内容后' in output[-2]
    assert parser.parse_args(summary['remaining_preparation_argv'][4:]).provider == ['IEEE']
    assert len(backend.calls) == 2 and backend.closed


def test_init_uses_production_public_pdf_and_reports_source_format_without_retaining_bytes(monkeypatch):
    from hypoloom.cli import build_parser
    from hypoloom.institutional_instructions import render_results
    from tests.runtime.test_literature_probe import system, DOI, PDF
    from tests.runtime.test_grounding_web import _response
    from tests.runtime.test_institutional_browser import _pdf
    web, transport, _ = system()
    transport.responses[PDF] = _response(PDF, _pdf(DOI), media_type='application/pdf')
    monkeypatch.setattr('hypoloom.grounding_web.GroundingWeb', lambda **kwargs: web)
    class Browser(PlaywrightInstitutionalBrowser):
        closed = False
        def check_connection(self, **kwargs): return {'state': 'ready'}
        def fetch(self, *args, **kwargs): pytest.fail('public PDF must not require a browser download')
        def close(self): self.closed = True
    backend = Browser('http://127.0.0.1:9222')
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output = []
    args = build_parser().parse_args(['institutional', 'prepare', '--non-interactive', '--sample', DOI])
    assert run(args, output=output.append) == 0
    summary = json.loads(output[-1]); rows = summary['preparation_summary']
    sample = rows[0]['samples'][0]
    assert sample['state'] == 'sample_verified' and sample['media_type'] == 'application/pdf'
    assert sample['acquisition_source'] == 'public_pdf' and sample['source_url'] == PDF
    assert sample['page_count'] == 1 and summary['provider_modes'] == {'IEEE': 'auto'}
    assert '公开来源' in render_results(rows) and 'PDF（1 页）' in render_results(rows)
    assert 'not established' in summary['verification_scope'] and 'content' not in sample
    assert not access._items and backend.closed


@pytest.mark.parametrize('state', ['verification_required', 'identity_unconfirmed', 'browser_unavailable'])
def test_public_probe_failure_preserves_browser_failure_and_remaining_deadline(monkeypatch, state):
    import hypoloom.institutional_browser as module
    from hypoloom.grounding_web import GroundingWebError
    clock = [0.]
    class Public:
        max_seconds = 25
        def document(self, url):
            clock[0] = 25.
            raise GroundingWebError('timed_out', 'SECRET URL must not escape')
    monkeypatch.setattr('hypoloom.grounding_web.GroundingWeb', lambda **kwargs: Public())
    monkeypatch.setattr('hypoloom.document_fallback.monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0])
    class Browser(PlaywrightInstitutionalBrowser):
        calls = []
        def fetch(self, url, **kwargs):
            self.calls.append(kwargs)
            return {'state': state}
    backend = Browser('http://127.0.0.1:9222')
    result = backend.fetch_sample('https://doi.org/10.1109/example', force=True, timeout_seconds=120)
    assert result == {'state': state, 'acquisition_source': 'browser'}
    assert backend.calls == [{'force': True, 'focus': False, 'timeout_seconds': 95}]


def test_summary_separates_browser_html_from_pdf_and_strips_source_queries():
    from hypoloom.institutional_instructions import render_results
    from hypoloom.document_fallback import public_url
    source = public_url('https://publisher.example/paper?token=PRIVATE#secret')
    assert source == 'https://publisher.example/paper'
    text = render_results([{'provider': 'Springer', 'state': 'sample_verified', 'samples': [
        {'state': 'sample_verified', 'media_type': 'text/plain', 'source_url': source,
         'page_count': 1, 'acquisition_source': 'browser'}]}])
    assert '网页全文，不代表已下载 PDF' in text and 'PRIVATE' not in text


@pytest.mark.parametrize('failure_mode', ['review', 'public_only'])
def test_mixed_sample_results_explain_document_identity_without_claiming_download(monkeypatch, failure_mode):
    from hypoloom.cli import build_parser
    from hypoloom.institutional_access import publisher
    class Mixed(PreparationBackend):
        def fetch(self, url, **kwargs):
            name = publisher(url)
            if name == 'Springer': return super().fetch(url, **kwargs)
            if name == 'ScienceDirect': return {'state': 'verification_required'}
            return {'state': 'identity_unconfirmed', **({'reason': 'article_identity_unconfirmed'} if name == 'IEEE' else {})}
    backend = Mixed(); backend.state = 'available'
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO))
    parser = build_parser(); output = []
    args = parser.parse_args(['institutional', 'prepare', '--provider', 'IEEE', '--provider', 'ScienceDirect',
                             '--provider', 'Springer', '--provider', 'ACM',
                             '--institutional-prepare-failure-mode', failure_mode])
    assert run(args, input_fn=lambda _: '', output=output.append) == 1
    summary = json.loads(output[-1]); text = output[-2]
    assert [r['state'] for r in summary['preparation_summary']] == [
        'identity_unconfirmed', 'verification_required', 'sample_verified', 'identity_unconfirmed']
    assert summary['preparation_summary'][0]['samples'][0]['reason_code'] == 'article_identity_unconfirmed'
    assert '论文页面身份校验未通过' in text and '尚不能证明已下载全文' in text
    assert '机构账号' in text and '结果未区分校验阶段' in text
    assert '人机验证' in text and '只证明该样本当前可读' in text
    assert summary['preparation_failure_mode'] == failure_mode
    if failure_mode == 'public_only':
        assert summary['remaining_preparation_argv'] == []
        assert summary['provider_modes'] == {**dict.fromkeys(['IEEE', 'ScienceDirect', 'ACM'], 'public_only'), 'Springer': 'auto'}
        assert text.count('运行策略：仅公开') == 3
    else:
        assert parser.parse_args(summary['remaining_preparation_argv'][4:]).provider == ['IEEE', 'ScienceDirect', 'ACM']
        assert summary['provider_modes'] == dict.fromkeys(['IEEE', 'ScienceDirect', 'Springer', 'ACM'], 'auto')
    assert backend.closed


def test_platform_without_fixed_sample_keeps_manual_completion_unverified(monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend()
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO))
    output = []
    assert run(build_parser().parse_args(['institutional', 'prepare', '--provider', 'Nature']),
               input_fn=lambda _: '', output=output.append) == 0
    assert [c[0] for c in backend.calls] == ['Nature']
    assert json.loads(output[-1])['preparation_summary'][0]['state'] == 'not_checked'


def test_common_preparation_supports_skip_public_only_and_no_false_login(monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend(); backend.state = "available"
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--institutional-browser-url", "http://127.0.0.1:9222"])
    answers = iter(["s", "p", ""])
    output = []
    assert run(args, input_fn=lambda _: next(answers), output=output.append) == 0
    assert [call[0] for call in backend.calls[:3]] == ["IEEE", "ScienceDirect", "Springer"]
    assert len(backend.calls) == 4
    summary = json.loads(output[-1])
    assert [(p["provider"], p["state"]) for p in summary["preparation_summary"]] == [
        ("IEEE", "skipped"), ("ScienceDirect", "public_only"), ("Springer", "sample_verified")]
    assert summary['preparation_summary'][2]['samples'][0]['sample_scope'] == 'public_full_text'
    assert summary["provider_mode_argv"] == ["--institutional-provider-mode", "IEEE=auto",
        "--institutional-provider-mode", "ScienceDirect=public_only", "--institutional-provider-mode", "Springer=auto"]
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
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
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
    access = InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: access)
    args = build_parser().parse_args(["institutional", "prepare", "--provider", "Nature", "--provider", "IEEE", "--sample", URL])
    output = []
    assert run(args, input_fn=lambda _: "", output=output.append) == 1
    rows = json.loads(output[-1])["preparation_summary"]
    assert [(r["provider"], r["state"]) for r in rows] == [("Nature", state), ("IEEE", state)]
    remaining = build_parser().parse_args(json.loads(output[-1])["remaining_preparation_argv"][4:])
    assert remaining.provider == ["Nature", "IEEE"] and remaining.sample == [URL]
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
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO))
    assert run(parser.parse_args(["institutional", "prepare"]), input_fn=lambda _: "q", output=output.append) == 0
    assert [call[0] for call in backend.calls] == ["IEEE"]
    assert [r["state"] for r in json.loads(output[-1])["preparation_summary"]] == ["skipped", "not_checked", "not_checked"]
    assert backend.closed


def test_interrupted_preparation_closes_access(monkeypatch):
    from hypoloom.cli import build_parser
    backend = PreparationBackend()
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda args: InstitutionalAccess(backend, provider_modes=ALL_PROVIDERS_AUTO))
    def interrupt(_): raise EOFError()
    assert run(build_parser().parse_args(["institutional", "prepare"]),
        input_fn=interrupt, output=lambda _: None) == 1
    assert backend.closed


@pytest.mark.parametrize("ending", ["timeout", "eof", "finish", "input_timeout"])
@pytest.mark.parametrize('failure_mode', ['review', 'public_only'])
def test_failed_platforms_remain_retryable_after_completion_or_interruption(tmp_path, monkeypatch, ending, failure_mode):
    from hypoloom.cli import build_parser
    from hypoloom.institutional_dialogue import PreparationEnded
    class Failing(PreparationBackend):
        def prepare(self, provider, **kwargs):
            self.calls.append((provider, kwargs))
            return {"state": "browser_unavailable"} if provider == "ScienceDirect" or (
                ending == "timeout" and provider == "Springer") else {"state": "opened"}
    backend = Failing(); backend.state = "available"
    access = InstitutionalAccess(backend, clock=lambda: monotonic() * 100, acquisition_seconds=30, poll_seconds=1, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda _: access)
    parser = build_parser()
    args = parser.parse_args(["institutional", "prepare", "--repository", str(tmp_path),
        "--institutional-browser-url", "http://127.0.0.1:9222", "--institutional-manual-seconds", "80",
        "--institutional-provider-mode", "ACM=public_only",
        "--institutional-prepare-failure-mode", failure_mode])
    prompts, output = [], []
    def answer(prompt):
        prompts.append(prompt.split(':')[0])
        if len(prompts) == 1: return ""  # Completion checks the fixed IEEE sample.
        if ending == "eof": raise EOFError
        raise PreparationEnded("finished" if ending == "finish" else "input_timeout")
    assert run(args, input_fn=answer, output=output.append) == (0 if ending == "finish" else 1)
    summary = json.loads(output[-1])
    if failure_mode == 'public_only':
        assert summary['remaining_preparation_argv'] == []
        assert summary['provider_modes'] == {**dict.fromkeys(['ACM', 'ScienceDirect', 'Springer'], 'public_only'), 'IEEE': 'auto'}
    else:
        remaining = parser.parse_args(summary["remaining_preparation_argv"][4:])
        assert remaining.provider == ["ScienceDirect", "Springer"]
        assert remaining.institutional_provider_mode == ["ACM=public_only", "IEEE=auto", "ScienceDirect=auto", "Springer=auto"]
        assert remaining.institutional_browser_url == args.institutional_browser_url
        assert remaining.institutional_manual_seconds == 80 and remaining.repository == str(tmp_path)
    assert summary["preparation_summary"][0]['state'] == 'sample_verified'
    assert len(summary["preparation_summary"][0]['samples']) == 1
    assert summary["preparation_summary"][1]["state"] == "acquisition_timeout"
    assert backend.closed and not list(tmp_path.iterdir())
    if ending == "timeout" and failure_mode == 'review':
        recovered = PreparationBackend(); recovered.state = "available"
        monkeypatch.setattr("hypoloom.institutional_prepare.build", lambda _: InstitutionalAccess(recovered, provider_modes=ALL_PROVIDERS_AUTO))
        retried = []
        assert run(remaining, input_fn=lambda _: "", output=retried.append) == 0
        assert [call[0] for call in recovered.calls[::2]] == ["ScienceDirect", "Springer"]
        assert len(recovered.calls) == 4
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
    access = InstitutionalAccess(backend, clock=lambda: monotonic() * 100, acquisition_seconds=30, poll_seconds=1, provider_modes=ALL_PROVIDERS_AUTO)
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


def test_automatic_sample_retains_cdp_failure_through_queue_and_retry_summary(monkeypatch):
    from hypoloom.cli import build_parser
    browser = PlaywrightInstitutionalBrowser('http://127.0.0.1:9222')
    def disconnected(): raise RuntimeError('Unexpected status 503 SECRET')
    browser._connect = disconnected
    backend = PreparationBackend()
    backend.fetch = browser.fetch
    access = InstitutionalAccess(backend, clock=lambda: monotonic() * 100, acquisition_seconds=30, poll_seconds=1, provider_modes=ALL_PROVIDERS_AUTO)
    monkeypatch.setattr('hypoloom.institutional_prepare.build', lambda _: access)
    output = []; parser = build_parser()
    assert run(parser.parse_args(['institutional', 'prepare', '--provider', 'IEEE']),
               input_fn=lambda _: '', output=output.append) == 1
    events = [json.loads(v) for v in output if v.startswith('{')]
    errors = [v for v in events if v.get('state') == 'browser_unavailable']
    assert len(errors) == 1 and errors[0]['reason_code'] == 'cdp_endpoint_unavailable'
    assert errors[0]['sample_url'].startswith('https://doi.org/10.1109/')
    assert events[-1]['preparation_summary'][0]['state'] == 'acquisition_timeout'
    assert parser.parse_args(events[-1]['remaining_preparation_argv'][4:]).provider == ['IEEE']
    assert 'SECRET' not in '\n'.join(output) and backend.closed
