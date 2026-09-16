"""Web -> real Host socket -> durable answers -> native resumed research and CPU."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
from urllib.parse import parse_qs, urlsplit

from hypoloom.control_protocol import ControlRequest, receive_message, socket_address
from hypoloom.web_observer import create_server
from hypoloom.workflow_host import _send_control_response

import test_claude_grounding_dialogue_g3 as native
from test_human_answer_ui import post_answer
from test_web_observer import _TOKEN, _start, _stop


def answer_via_web(host, decision_id, answer, ordinal):
    values = {"decision_id": decision_id, "answer": answer, "request_id": f"web-answer-{ordinal}",
        "expected_revision": host.store.latest_transition(native._RUN)["revision"]}
    expected = ControlRequest(1, values["request_id"], values["expected_revision"], "ANSWER",
        {"run_id": native._RUN, "decision_id": decision_id, "answer": answer})
    web = create_server(host.state_root, native._RUN, port=0, token=_TOKEN)
    thread = _start(web)
    token = parse_qs(urlsplit(web.human_answer_url()).fragment)["answer_token"][0]
    path = host.state_root / "control.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            address, descriptor = socket_address(path)
            try:
                server.bind(address)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            path.chmod(0o600)
            server.listen(2)
            server.settimeout(15)

            def browser_requests():
                status, reply = post_answer(web, values, answer_token=token)
                assert status == 503 and reply["error"] == "delivery_unknown"
                status, reply = post_answer(web, values, answer_token=token)
                assert status == 200
                return reply

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(browser_requests)
                responses = []
                for index in range(2):
                    peer, _ = server.accept()
                    with peer:
                        request = ControlRequest.from_mapping(receive_message(peer))
                        assert request == expected
                        response = host.handle(request)
                        assert response.status == "accepted", response
                        responses.append(response)
                        # First request commits, but its reply is lost. The exact
                        # retry must replay the durable result, not answer twice.
                        if index:
                            _send_control_response(peer, response)
                assert responses[0] == responses[1]
                reply = future.result(timeout=15)
                assert reply == responses[1].to_mapping()
                return reply
    finally:
        _stop(web, thread)
        path.unlink(missing_ok=True)


def test_native_two_web_answers_lost_replies_restart_and_cpu_closure(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "_answer_via_cli", answer_via_web)
    native.test_native_grounding_two_questions_restart_answers_and_cpu_closure(tmp_path)
    evidence = json.loads((tmp_path / "grounding-dialogue-evidence.json").read_text())
    evidence["answer_transport"] = "authenticated_web_to_host_socket"
    evidence["lost_replies_after_commit"] = 2
    evidence["answer_requests"] = 4
    (tmp_path / "human-answer-web-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
