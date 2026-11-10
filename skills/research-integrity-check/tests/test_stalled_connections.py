"""Eight connections that promise a body and send none must not keep every other caller out for good."""

from __future__ import annotations

import socket
import threading
import time
from http.server import ThreadingHTTPServer

from research_integrity_check import server

TOOL = "research_integrity_check"


def _post(port: int, length: int, first: bytes) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(f"POST /tools/{TOOL}/call HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {length}\r\n\r\n".encode() + first)
    return s


def test_stalled_connections_give_their_slot_back() -> None:
    handler = server.make_http_handler()
    handler.timeout = 1
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    stalled = [_post(port, 100, b"x") for _ in range(server.MAX_CONCURRENT)]
    try:
        time.sleep(0.3)
        busy = _post(port, 2, b"{}")
        assert busy.recv(64).startswith(b"HTTP/1.0 503")
        time.sleep(1.8)
        later = _post(port, 2, b"{}")
        assert not later.recv(64).startswith(b"HTTP/1.0 503")
        later.close()
        busy.close()
    finally:
        for s in stalled:
            s.close()
        httpd.shutdown()
        httpd.server_close()
