"""Offline fault operations executed by the real disposable network worker."""

import io
import os
from pathlib import Path
import time


def request(payload):
    from hypoloom import grounding_web as web
    from hypoloom.network_io import report_network_progress

    Path(payload["pid_file"]).write_text(str(os.getpid()))
    mode = payload["mode"]
    if mode == "environment":
        return {"secret_seen": payload["secret"] == "private-test-key",
                "ambient_seen": "HYP_NETWORK_TEST_SECRET" in os.environ}
    if mode == "flood":
        return {"value": "x" * 100000}
    if mode == "exception":
        raise OSError("private-test-key must never be returned")
    if mode == "dns_error":
        report_network_progress("dns")
        raise web.socket.gaierror("private hostname must never be returned")
    if mode == "closed_stdout":
        os.close(1)
        time.sleep(30)
    if mode == "hang":
        report_network_progress("dns")
        time.sleep(30)
    if mode == "dns":
        web.socket.getaddrinfo = lambda *a, **k: time.sleep(30)
        return web._resolve_in_process({"hostname": "example.org"})

    class Socket:
        def getpeername(self): return ("8.8.8.8", 443)
        def settimeout(self, seconds): pass

    class DripHeaders(io.BytesIO):
        def readline(self, limit=-1):
            # HTTPResponse.begin() keeps receiving headers while every single
            # read is below the socket idle timeout.
            time.sleep(0.07)
            return b"X-Drip: still here\r\n"

    class Body:
        status = 200
        def getheader(self, name): return "application/pdf" if name == "Content-Type" else None
        def read1(self, size):
            time.sleep(0.06)
            return b"x"

    class Connection:
        def __init__(self, *args, **kwargs): self.sock = Socket()
        def connect(self):
            if mode == "tls_handshake":
                report_network_progress("tls_handshake")
                time.sleep(30)
        def request(self, *args, **kwargs): pass
        def getresponse(self):
            if mode == "response_headers":
                raw = DripHeaders()
                original = raw.readline
                first = [True]
                def readline(limit=-1):
                    if first[0]:
                        first[0] = False
                        return b"HTTP/1.1 200 OK\r\n"
                    return original(limit)
                raw.readline = readline
                response = web.http.client.HTTPResponse(type("Wire", (), {"makefile": lambda *args: raw})())
                response.begin()
                return response
            return Body()
        def close(self): pass

    web.http.client.HTTPSConnection = Connection
    return web._fetch_in_process({"uri": "https://example.org/paper", "approved_addresses": ["8.8.8.8"],
        "max_bytes": 1000000, "deadline": payload["deadline"], "headers": {}})
