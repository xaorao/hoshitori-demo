"""A fake RYO on 127.0.0.1 for the transport and full-stack tests.

It speaks the REST surface described in ``RYO_API_COMPATIBILITY`` with the synthetic data of ``ryo_data``: ``GET /health``, ``/whoami``,
``/tools`` and ``POST /tools/<name>/call``, a bearer key, rate-limit headers and a trace id on every answer. The answers are synthetic
(nothing here is a recording), and the error body for a bad argument is invented because the real one has not been observed.

A ``script`` can replace any answer with a ``Behaviour`` to play the failures the live service has shown or could show: a slow answer, a
503 after a delay, a 429, an HTML page, a body that stops early, a connection that closes, a drip of one byte at a time, an enormous body,
a redirect, garbage instead of HTTP.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import ipaddress
import json
import ssl
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests import ryo_data as D
from tests.ryo_support import catalogue_body, synthetic_tools

KEY = "fake-key-0123456789abcdef"
TRACE = "0123456789abcdef0123456789abcdef"


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    number: int = 0


@dataclass
class Behaviour:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0.0  # before any byte is sent
    drip_s: float = 0.0  # a pause between single bytes of the body
    announce: int | None = None  # a Content-Length different from the body's length
    hang_up: bool = False  # close the connection without a word
    raw: bytes | None = None  # send these bytes instead of a response
    repeat: int | None = None  # send the body this many times over (an enormous answer)


def self_signed(directory: str, *, with_ip: bool = True, dns_name: str | None = None) -> tuple[str, str]:
    """A throw-away certificate for 127.0.0.1 and localhost (or, with ``with_ip=False``, for another name only) and its key, as file paths."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-ryo")])
    names: list[x509.GeneralName] = (
        [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))] if with_ip else [x509.DNSName("other.example")]
    )
    if dns_name is not None:
        names = [x509.DNSName(dns_name)]
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = f"{directory}/cert.pem", f"{directory}/key.pem"
    with open(cert_path, "wb") as handle:
        handle.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


def error(status: int, code: str, message: str, headers: dict[str, str] | None = None, details: Any = None) -> Behaviour:
    return Behaviour(status, D.error_body(code, message, TRACE, details), {"content-type": "application/json", **(headers or {})})


class FakeRyo:
    def __init__(
        self,
        key: str = KEY,
        script: Callable[[Seen], Behaviour | None] | None = None,
        *,
        tls: bool = False,
        cert_for_ip: bool = True,
        cert_name: str | None = None,
    ) -> None:
        self.key = key
        self.tls = tls
        self._directory = tempfile.TemporaryDirectory() if tls else None
        self.cert_path = ""
        self.script = script
        self.seen: list[Seen] = []
        self._lock = threading.Lock()
        self._remaining = 59
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                return

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                with outer._lock:
                    seen = Seen(self.command, self.path, headers, body, len(outer.seen) + 1)
                    outer.seen.append(seen)
                behaviour = outer.script(seen) if outer.script else None
                if behaviour is None:
                    behaviour = outer.default(seen)
                with contextlib.suppress(OSError):  # a client that gave up is not the server's problem
                    self._send(behaviour)

            def _send(self, b: Behaviour) -> None:
                if b.delay_s:
                    time.sleep(b.delay_s)
                self.close_connection = True
                if b.hang_up:
                    return
                if b.raw is not None:
                    self.wfile.write(b.raw)
                    self.wfile.flush()
                    return
                body = b.body * b.repeat if b.repeat else b.body
                self.send_response(b.status)
                for name, value in b.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body) if b.announce is None else b.announce))
                self.send_header("Connection", "close")
                self.end_headers()
                if b.drip_s:
                    for i in range(len(body)):
                        self.wfile.write(body[i : i + 1])
                        self.wfile.flush()
                        time.sleep(b.drip_s)
                else:
                    self.wfile.write(body)
                self.wfile.flush()

            do_GET = _handle
            do_POST = _handle

        class Quiet(ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:  # a client that fails the handshake is part of the test
                return

        self._server = Quiet(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        if self._directory is not None:
            self.cert_path, key_path = self_signed(self._directory.name, with_ip=cert_for_ip, dns_name=cert_name)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert_path, key_path)
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # --- lifecycle --------------------------------------------------------------------------------------------------------------
    @property
    def url(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self._server.server_address[1]}/api/mcp"

    def start(self) -> FakeRyo:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        if self._directory is not None:
            self._directory.cleanup()

    def __enter__(self) -> FakeRyo:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # --- the default service --------------------------------------------------------------------------------------------------------
    def default(self, seen: Seen) -> Behaviour:
        base = "/api/mcp"
        path = seen.path
        json_headers = {"content-type": "application/json", "x-trace-id": TRACE}
        if seen.method == "GET" and path == base + "/health":
            return Behaviour(200, b'{"status":"ok","protocol_version":"2024-11-05","server":"ryo-chan","tools":6}', json_headers)
        if seen.headers.get("authorization") != f"Bearer {self.key}":
            return error(401, "UNAUTHENTICATED", "Missing or invalid credential.", {"www-authenticate": "Bearer"})
        if seen.method == "GET" and path == base + "/whoami":
            body = json.dumps(
                {
                    "key_id": "5ebd7286",
                    "label": "test",
                    "scopes": ["tools:read"],
                    "expires_at": "2026-12-17T02:33:00Z",
                    "rate_limit_per_min": 60,
                    "rate_limit_remaining": 60,
                    "call_allowance": {"allowed": True},
                    "tools": [t["name"] for t in synthetic_tools()],
                }
            ).encode()
            return Behaviour(200, body, json_headers)
        if seen.method == "GET" and path == base + "/tools":
            return Behaviour(200, catalogue_body(), json_headers)
        prefix, suffix = base + "/tools/", "/call"
        if seen.method == "POST" and path.startswith(prefix) and path.endswith(suffix):
            name = path[len(prefix) : -len(suffix)]
            tool = next((t for t in synthetic_tools() if t["name"] == name), None)
            if tool is None:
                return error(404, "NOT_FOUND", f"Unknown tool '{name}'.")
            try:
                args = json.loads(seen.body or b"{}")
            except ValueError:
                return error(400, "BAD_REQUEST", "The body is not JSON.")
            schema = tool["inputSchema"]
            problems = [k for k in args if k not in schema["properties"]] + [k for k in schema.get("required", []) if k not in args]
            if not isinstance(args, dict) or problems:
                return error(400, "BAD_REQUEST", f"Invalid arguments: {sorted(problems)}")
            with self._lock:
                self._remaining = max(self._remaining - 1, 0)
                remaining = self._remaining
            result = D.result(name, request=args)
            headers = {
                **json_headers,
                "x-ratelimit-limit": "60",
                "x-ratelimit-remaining": str(remaining),
                "x-ratelimit-reset": "1793278239",
            }
            return Behaviour(200, D.rest_body(name, result), headers)
        return error(404, "NOT_FOUND", "No such route.")
