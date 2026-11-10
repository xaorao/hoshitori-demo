"""The network side of the safe fetch client: one request to an address that has already been checked.

Everything that decides whether a fetch is allowed is in ``safe_fetch``. This module only does what it is told, and does it
without trusting the server: the connection goes to a checked address and never to a name (so the name is never looked up again,
which is what a rebinding server counts on); TLS is verified against the host name that was asked for; the request is a plain
``GET`` with nothing in it from the user; one overall deadline closes the socket, whatever the server is doing; the answer is read
in pieces and refused beyond the limit; and an answer shorter than it said it would be is a failure, not a short answer.

It uses ``http.client``, which follows no redirects, reads no proxy variable and keeps no cookie.
"""

from __future__ import annotations

import contextlib
import http.client
import socket
import ssl
import threading
from collections.abc import Sequence
from typing import Final

from hoshitori.fetch_types import FetchCode, FetchError, HopResponse, Screen, Target

CHUNK: Final = 64 * 1024
BACKSTOP: Final = 1.25
USER_AGENT: Final = "hoshitori-fetch/0"
REQUEST_HEADERS: Final = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,text/plain;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "close",
}


class _Pinned(http.client.HTTPConnection):
    """A connection that goes to one of the given addresses and to nothing else. Over TLS it checks the certificate against the name."""

    address: str  # the checked address the connection was made to; set by ``connect``

    def __init__(
        self, target: Target, addresses: Sequence[str], timeout_s: float, connect_s: float, context: ssl.SSLContext | None
    ) -> None:
        super().__init__(target.host, target.port, timeout=timeout_s)
        self._addresses = tuple(addresses)
        self.default_port = 443 if context is not None else 80  # the Host header carries a port only when it is not the scheme's own
        self._connect_s = connect_s
        self._total_s = timeout_s
        self._context = context

    def connect(self) -> None:
        last: OSError | None = None
        for address in self._addresses:
            try:
                sock = socket.create_connection((address, self.port), timeout=min(self._connect_s, self._total_s))
            except OSError as exc:
                last = exc
                continue
            sock.settimeout(self._total_s)
            if self._context is not None:
                try:
                    sock = self._context.wrap_socket(sock, server_hostname=self.host)
                except OSError:
                    sock.close()
                    raise
            self.sock = sock
            self.address = address
            return
        raise last or OSError("no address to connect to")


class PinnedHop:
    def __init__(self, *, connect_timeout_s: float = 5.0, ca_file: str | None = None) -> None:
        if connect_timeout_s <= 0:
            raise ValueError("the connect timeout is positive")
        self._connect_s = connect_timeout_s
        self._context = ssl.create_default_context(cafile=ca_file)

    def __call__(self, target: Target, addresses: Sequence[str], timeout_s: float, screen: Screen, max_body_bytes: int) -> HopResponse:
        conn = _Pinned(target, addresses, timeout_s * BACKSTOP + 1.0, self._connect_s, self._context if target.scheme == "https" else None)
        expired = threading.Event()
        connected: list[socket.socket] = []

        def abort() -> None:
            expired.set()
            for sock in connected:  # http.client forgets the socket once the response starts; the watchdog must not
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)

        watchdog = threading.Timer(timeout_s, abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            conn.connect()
            if conn.sock is not None:
                connected.append(conn.sock)
            conn.request("GET", target.path_query, headers=REQUEST_HEADERS)
            response = conn.getresponse()
            status = response.status
            headers = tuple(response.getheaders())
            wanted = screen(status, headers)
            body = self._read(response, max_body_bytes) if wanted else b""
        except FetchError:
            raise
        except Exception as exc:
            raise self._failure(exc, expired.is_set(), timeout_s) from None
        finally:
            watchdog.cancel()
            conn.close()
        if expired.is_set():
            raise FetchError(FetchCode.TIMEOUT, f"no complete answer within {timeout_s:g} s")
        declared = [value.strip() for name, value in headers if name.lower() == "content-length"]
        if wanted and declared and declared[0].isdigit() and int(declared[0]) != len(body):
            raise FetchError(FetchCode.TRUNCATED, f"the body is {len(body)} bytes but {declared[0]} were announced")
        return HopResponse(status, headers, body, conn.address)

    @staticmethod
    def _read(response: http.client.HTTPResponse, limit: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > limit:
                raise FetchError(FetchCode.TOO_LARGE, f"the answer is longer than {limit} bytes")
            chunks.append(chunk)

    @staticmethod
    def _failure(exc: Exception, expired: bool, timeout_s: float) -> FetchError:
        if expired or isinstance(exc, TimeoutError | socket.timeout):
            return FetchError(FetchCode.TIMEOUT, f"no complete answer within {timeout_s:g} s")
        if isinstance(exc, ssl.SSLError):
            return FetchError(FetchCode.TLS, f"TLS failed: {type(exc).__name__}")
        if isinstance(exc, http.client.IncompleteRead):
            return FetchError(FetchCode.TRUNCATED, "the answer stopped before its end")
        if isinstance(
            exc, ConnectionError
        ):  # includes a server that hung up without answering (RemoteDisconnected is also an HTTPException)
            return FetchError(FetchCode.CONNECT, f"the connection failed: {type(exc).__name__}")
        if isinstance(exc, http.client.HTTPException | ValueError):
            return FetchError(FetchCode.PROTOCOL, f"the answer was not valid HTTP: {type(exc).__name__}")
        if isinstance(exc, OSError):
            return FetchError(FetchCode.CONNECT, f"the connection failed: {type(exc).__name__}")
        return FetchError(FetchCode.PROTOCOL, f"unexpected {type(exc).__name__}")
