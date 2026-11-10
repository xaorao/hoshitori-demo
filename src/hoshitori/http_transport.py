"""One HTTP exchange, done safely: the base of the RYO transport and of every venue transport.

It uses ``http.client`` from the standard library, which follows no redirects, reads no proxy variables and sends nothing the code did not
write. What it guarantees:

* **One overall deadline.** ``timeout_s`` bounds the whole exchange, not each socket operation: a watchdog closes the socket when it
  passes, so a server that sends one byte a minute cannot hold a worker for hours.
* **A bounded body.** The answer is read in chunks and refused beyond ``max_body_bytes``; a body shorter than its ``Content-Length`` is a
  failure, not a short answer (``http.client`` returns it silently).
* **Only the headers worth keeping**, each cut to a sane length, so a hostile server cannot fill the capture store with junk or smuggle in
  something that looks like a credential. A request header is never stored.
* **TLS is verified** (the default context, or one trusting a named certificate file). A plain ``http`` URL is accepted only for a
  loopback host and only when told to, for tests, or for any host when ``allow_plain_http`` is set. Only the time-stamp transport sets
  it: several public authorities offer nothing else, a token is signed and checked against a pinned root, and the request holds a digest.
* **The connection is closed** after every exchange, and the watchdog is cancelled, so nothing is left behind.

A subclass decides what to ask (``fetch``: the route, the body, any credential) and calls ``_exchange``. Failures are ``TransportFailure``
with a class from the connector taxonomy; no message ever holds a credential, because none is passed to this class.
"""

from __future__ import annotations

import contextlib
import http.client
import socket
import ssl
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit

from hoshitori import canonical
from hoshitori.capture import RawCapture, redact_url
from hoshitori.connector import ErrorClass
from hoshitori.gateway import TransportFailure

KEPT_HEADERS: Final = frozenset({"content-type", "content-length", "date", "retry-after", "x-trace-id", "server", "via"})
KEPT_PREFIXES: Final = ("x-ratelimit-", "x-mbx-used-weight", "ratelimit-")
MAX_HEADER_VALUE: Final = 512
MAX_BODY_BYTES: Final = 4 * 1024 * 1024
CHUNK: Final = 64 * 1024
BACKSTOP: Final = 1.25
LOOPBACK: Final = frozenset({"127.0.0.1", "localhost", "::1"})
USER_AGENT: Final = "hoshitori-gateway/0"


class TransportConfigError(ValueError):
    """The transport was configured with something it must not use."""


def _kept(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers:
        lowered = name.lower()
        if lowered in KEPT_HEADERS or lowered.startswith(KEPT_PREFIXES):
            text = value.strip()[:MAX_HEADER_VALUE]
            out[lowered] = f"{out[lowered]}, {text}" if lowered in out else text
    return {k: v[:MAX_HEADER_VALUE] for k, v in out.items()}


class HttpTransport:
    def __init__(
        self,
        base_url: str,
        clock: Callable[[], datetime],
        *,
        provider: str,
        allow_insecure_loopback: bool = False,
        allow_plain_http: bool = False,
        max_body_bytes: int = MAX_BODY_BYTES,
        ca_file: str | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise TransportConfigError("the base URL must be an http(s) URL with a host and no credentials, query or fragment")
        if parts.scheme == "http" and not allow_plain_http and not (allow_insecure_loopback and parts.hostname in LOOPBACK):
            raise TransportConfigError("plain http is allowed only for a loopback host, and only when asked for")
        if max_body_bytes < 1024:
            raise TransportConfigError("the body limit is too small to hold an answer")
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port
        self._prefix = parts.path.rstrip("/")
        self._clock = clock
        self._provider = provider
        self._max_body = max_body_bytes
        self._context = ssl.create_default_context(cafile=ca_file) if parts.scheme == "https" else None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._scheme}://{self._host}{self._prefix})"

    def _connection(self, timeout_s: float) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(self._host, self._port, timeout=timeout_s, context=self._context)
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout_s)

    # --- one exchange ------------------------------------------------------------------------------------------------------------
    def _exchange(
        self,
        operation: str,
        args: Mapping[str, Any],
        method: str,
        path: str,
        payload: bytes | None,
        extra_headers: Mapping[str, str],
        timeout_s: float,
        capture_id: str,
        sent_at: datetime,
    ) -> RawCapture:
        """Send one request and return the capture. ``path`` is the path with its query, below the host."""
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "User-Agent": USER_AGENT,
            **extra_headers,
        }
        if payload is not None:
            # unless the adapter says what its body is (a time-stamp query is not JSON)
            headers.setdefault("Content-Type", "application/json")
        conn = self._connection(timeout_s * BACKSTOP + 1.0)  # the watchdog is the deadline; this only bounds a connect that it cannot cut
        expired = threading.Event()
        connected: list[socket.socket] = []  # http.client forgets the socket once the response starts; the watchdog must not

        def abort() -> None:
            expired.set()
            for sock in connected or ([conn.sock] if conn.sock is not None else []):
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
            conn.close()

        watchdog = threading.Timer(timeout_s, abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            conn.connect()
            if conn.sock is not None:
                connected.append(conn.sock)
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            status = response.status
            response_headers = response.getheaders()
            body = self._read_body(response)
        except TransportFailure:
            raise
        except Exception as exc:
            raise self._failure(exc, expired.is_set(), timeout_s) from None
        finally:
            watchdog.cancel()
            conn.close()
        if expired.is_set():
            raise TransportFailure(ErrorClass.TIMEOUT, f"no complete answer within {timeout_s:g} s")
        kept = _kept(response_headers)
        declared = kept.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) != len(body):
            raise TransportFailure(ErrorClass.UPSTREAM, f"the body is {len(body)} bytes but {declared} were announced")
        scheme_host = f"{self._scheme}://{self._host}" + (f":{self._port}" if self._port else "")
        request: dict[str, canonical.JsonValue] = {
            "arguments": dict(args),
            "method": method,
            "operation": operation,
            "surface": "rest",
            "url": redact_url(scheme_host + path),
        }
        received = max(self._clock(), sent_at)
        return RawCapture(capture_id, self._provider, request, status, kept, body, sent_at, received, kept.get("x-trace-id"))

    def _read_body(self, response: http.client.HTTPResponse) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > self._max_body:
                raise TransportFailure(ErrorClass.MALFORMED, f"the answer is longer than {self._max_body} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _failure(exc: Exception, expired: bool, timeout_s: float) -> TransportFailure:
        if expired or isinstance(exc, TimeoutError | socket.timeout):
            return TransportFailure(ErrorClass.TIMEOUT, f"no complete answer within {timeout_s:g} s")
        if isinstance(exc, ssl.SSLError):
            return TransportFailure(ErrorClass.UNCLASSIFIED, f"TLS failure: {type(exc).__name__}")
        if isinstance(exc, socket.gaierror):
            return TransportFailure(ErrorClass.UPSTREAM, "the host name did not resolve")
        if isinstance(exc, ConnectionRefusedError):
            return TransportFailure(ErrorClass.UPSTREAM, "the connection was refused")
        if isinstance(exc, ConnectionError | http.client.RemoteDisconnected):
            return TransportFailure(ErrorClass.UPSTREAM, f"the connection was lost: {type(exc).__name__}")
        if isinstance(exc, http.client.HTTPException):
            return TransportFailure(ErrorClass.UPSTREAM, f"the answer was not valid HTTP: {type(exc).__name__}")
        if isinstance(exc, OSError):
            return TransportFailure(ErrorClass.UPSTREAM, f"network error: {type(exc).__name__}")
        return TransportFailure(ErrorClass.UNCLASSIFIED, f"unexpected {type(exc).__name__}")
