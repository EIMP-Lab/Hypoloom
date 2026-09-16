"""Native Bash HTTP/TLS through the production per-Attempt opt-in proxy."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest

import hypoloom.claude_agent_host as adapter_module
import hypoloom.network_proxy as proxy_module
from hypoloom.agent_aci import EmbeddedInput
from hypoloom.claude_agent_host import ClaudeAgentHost
from hypoloom.expert_loop import ResearchNodeSubkind

from test_agent_aci import _pincer_request
from test_agent_sessions import _manager
from test_claude_file_tool_cli import _installed_claude, _result_text, _tool_results
from test_full_cc_adapter import _invoke_adapter, _request as native_request
from test_full_cc_host_g3 import _start_provider
from test_git_authoring_workspace import make_target
from test_network_proxy import _Transport, _upstream
from test_network_proxy import tls_pair as tls_pair


class _NetworkScenario:
    def __init__(self, enabled, http_nonce, tls_nonce):
        self.enabled, self.http_nonce, self.tls_nonce = enabled, http_nonce, tls_nonce
        self.requests, self.errors = [], []
        self.structured_output, self.evidence = {}, {}
        self.stage_step = 0

    def record(self, document):
        self.requests.append(document)
        self.stage_step += 1
        try:
            if self.stage_step == 1:
                assert self.http_nonce not in json.dumps(document)
                assert self.tls_nonce not in json.dumps(document)
            if self.stage_step == 2:
                result = _tool_results(document)["native_network"]
                assert not result.get("is_error"), result
                self.evidence = {"tool_result": _result_text(result)}
                assert "NATIVE_PROXY_AND_BOUNDARIES_OK" in self.evidence["tool_result"]
                for nonce in (self.http_nonce, self.tls_nonce):
                    assert (nonce in self.evidence["tool_result"]) is self.enabled
        except Exception as exc:
            self.errors.append(f"network/{self.stage_step}: {type(exc).__name__}: {exc}")
        return len(self.requests)

    def tool_calls(self, number):
        if self.errors:
            return ()
        if self.stage_step == 1:
            script = (
                "import errno, http.client, os, socket, ssl, subprocess, urllib.request\nfrom pathlib import Path\n"
                "assert not Path('/state').exists() and not Path('/dev/nvidia0').exists()\n"
                "assert os.statvfs('/workspace').f_flag & os.ST_RDONLY\n"
                "try:\n    socket.create_connection(('93.184.216.34', 80), timeout=0.2)\n"
                "except OSError:\n    pass\n"
                "else:\n    raise AssertionError('direct public network was shared')\n"
            )
            if self.enabled:
                script += (
                    "assert Path('/network.sock').exists()\n"
                    "assert os.environ['http_proxy'] == os.environ['HTTPS_PROXY']\n"
                    "assert Path(os.environ['CURL_CA_BUNDLE']).is_file()\n"
                    "with urllib.request.urlopen('http://example.org/http-science', timeout=5) as response:\n"
                    "    print(response.read().decode())\n"
                    "context = ssl.create_default_context(cafile='/inputs/0001.txt')\n"
                    "with urllib.request.urlopen('https://example.org/tls-science', context=context, timeout=5) as response:\n"
                    "    print(response.read().decode())\n"
                    "print(subprocess.check_output(['curl', '--fail', '--silent', '--show-error', '--max-time', '5', 'http://example.org/curl-http'], text=True))\n"
                    "print(subprocess.check_output(['curl', '--fail', '--silent', '--show-error', '--max-time', '5', '--cacert', '/inputs/0001.txt', 'https://example.org/curl-tls'], text=True))\n"
                    "endpoint = urllib.parse.urlsplit(os.environ['http_proxy'])\n"
                    "client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=5)\n"
                    "client.request('GET', 'http://127.0.0.1/operator')\n"
                    "response = client.getresponse()\nassert response.status == 403\nresponse.read()\nclient.close()\n"
                )
            else:
                script += (
                    "assert not Path('/network.sock').exists()\n"
                    "assert not any(name.lower().endswith('_proxy') for name in os.environ)\n"
                    "try:\n    urllib.request.urlopen('http://93.184.216.34/blocked', timeout=0.2)\n"
                    "except OSError:\n    pass\n"
                    "else:\n    raise AssertionError('disabled network unexpectedly worked')\n"
                )
            script += "print('NATIVE_PROXY_AND_BOUNDARIES_OK')\n"
            return (("native_network", "Bash", {"command": "python3 - <<'PY'\n" + script + "PY", "timeout": 20000}),)
        return (("network_finish", "StructuredOutput", self.structured_output),)


@pytest.mark.parametrize("enabled", [True, False], ids=["public-http-tls", "disabled"])
def test_native_bash_uses_scoped_public_proxy_without_direct_network(tmp_path: Path, monkeypatch, tls_pair, enabled):
    executable = _installed_claude()
    _, target = make_target(tmp_path)
    manager = _manager(tmp_path)
    context, certificate = tls_pair
    http_nonce, tls_nonce = "http-tool-only-" + secrets.token_hex(16), "tls-tool-only-" + secrets.token_hex(16)
    scenario = _NetworkScenario(enabled, http_nonce, tls_nonce)
    request = _pincer_request(ResearchNodeSubkind.SYNTHESIS)
    inputs = (EmbeddedInput.from_bytes("native--expert-config", "application/json", json.dumps({
        "node_runtime": {"safety": {"bash": {"network_allowed": enabled}}}}).encode()),
        EmbeddedInput.from_bytes("native-test-public-ca", "text/plain", certificate.read_bytes()))
    from hypoloom.agent_aci import required_output_schema
    request = native_request(replace(request, schema_version=14, input_artifacts=inputs,
        immutable_context_refs=tuple(item.ref for item in inputs),
        required_output_schema=required_output_schema(request.command, request.task_context, schema_version=14)), "network", None)
    proxy_factory = proxy_module.AttemptNetworkProxy
    proxies = []
    with _upstream(body=http_nonce.encode()) as (http_port, http_observed), _upstream(
            tls=context, body=tls_nonce.encode()) as (tls_port, tls_observed):
        http_transport, tls_transport = _Transport(http_port), _Transport(tls_port)
        class Transport:
            def resolve(self, host, port):
                return (tls_transport if port == 443 else http_transport).resolve(host, port)
            def connect(self, host, port, **kwargs):
                return (tls_transport if port == 443 else http_transport).connect(host, port, **kwargs)
        def factory(*args, **kwargs):
            proxy = proxy_factory(*args, **kwargs, transport=Transport())
            proxies.append(proxy)
            return proxy
        monkeypatch.setattr(proxy_module, "AttemptNetworkProxy", factory)
        monkeypatch.setattr(adapter_module, "AttemptNetworkProxy", factory, raising=False)
        key = "host-only-provider-" + secrets.token_hex(16)
        provider, thread = _start_provider(scenario, key)
        workspace = manager.prepare(request, target)
        try:
            _, metadata = _invoke_adapter(ClaudeAgentHost(claude_executable=executable), request, workspace,
                scenario, {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}"}, tmp_path / "native")
            assert len(proxies) == int(enabled)
            assert len(http_observed) == len(tls_observed) == 2 * int(enabled)
            assert len(scenario.requests) == 2
            assert metadata["transcript_complete"]
            assert not scenario.errors
            assert all(not proxy.socket_path.exists() and not proxy._sockets for proxy in proxies)
            transcript = manager.publisher.root / metadata["transcript"]["path"] / "transcript.jsonl"
            assert key.encode() not in transcript.read_bytes()
            (tmp_path / "network-tool-evidence.json").write_text(json.dumps(scenario.evidence, indent=2))
            (tmp_path / "public-destination-calls.json").write_text(json.dumps({"http": http_transport.connections, "https": tls_transport.connections}, indent=2))
        finally:
            (tmp_path / "provider-requests.json").write_text(json.dumps(scenario.requests, indent=2))
            (tmp_path / "provider-errors.json").write_text(json.dumps(scenario.errors, indent=2))
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=5)
