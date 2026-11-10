"""Checks of ``address_flag_check.transport``, runnable against a mutant: retries with full-jitter backoff, Retry-After honoured and capped,
the cool-down after a rate limit, the time budget, the fixed failure reasons, the provenance record of every attempt, and the real HTTP
transport against a server on 127.0.0.1 (redirects are returned, never followed; size and time are bounded).

The fake clock advances only when the code sleeps or a fake reply says it took time, so every expected number is computed by hand.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import socket
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from address_flag_check import __version__
from tests.checks_support import Case

NOW = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 6))
        self.t += seconds


class Fake:
    """Scripted replies: each item is (status, headers, body, seconds) or an exception to raise after ``seconds``."""

    live = False

    def __init__(self, mod: types.ModuleType, clock: Clock, script: list[Any]) -> None:
        self.mod, self.clock, self.script = mod, clock, list(script)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> Any:
        self.calls.append((method, url, dict(headers), body, round(timeout, 6)))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, hdrs, data, seconds = item
        self.clock.t += seconds
        return self.mod.HttpReply(status, hdrs, data)


def _caller(
    mod: types.ModuleType, clock: Clock, script: list[Any], budget: float = 25.0, cooldowns: dict[str, float] | None = None
) -> tuple[Any, Fake]:
    fake = Fake(mod, clock, script)
    caller = mod.Caller(
        fake,
        now=NOW,
        deadline=clock() + budget,
        clock=clock,
        sleep=clock.sleep,
        jitter=lambda cap: cap,
        cooldowns={} if cooldowns is None else cooldowns,
    )
    return caller, fake


def _reason(mod: types.ModuleType, call: Any) -> object:
    try:
        call()
    except mod.SourceError as exc:
        return exc.reason
    return "no error"


def check_success_and_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock = Clock()
    caller, fake = _caller(mod, clock, [(200, {"content-type": "application/json"}, b'{"ok":1}', 0.25)])
    reply = c.run(
        "a plain success",
        lambda: caller.request(
            "ethereum_node", "POST", "https://node.example/rpc", body=b"{}", headers={"Content-Type": "application/json"}, timeout_s=8.0
        ),
    )
    if reply is not None:
        c.eq("reply status", reply.status, 200)
        c.eq("reply body", reply.body, b'{"ok":1}')
    c.eq("one upstream call", len(fake.calls), 1)
    c.eq(
        "the request as sent",
        fake.calls[0] if fake.calls else None,
        ("POST", "https://node.example/rpc", {"Content-Type": "application/json"}, b"{}", 8.0),
    )
    c.eq(
        "the provenance record",
        [r.as_dict() for r in caller.records],
        [
            {
                "source": "ethereum_node",
                "url": "https://node.example/rpc",
                "method": "POST",
                "attempt": 1,
                "http_status": 200,
                "latency_ms": 250,
                "bytes": 8,
                "sha256": hashlib.sha256(b'{"ok":1}').hexdigest(),
                "fetched_at": "2026-01-02T03:04:05Z",
                "error_kind": None,
            }
        ],
    )
    clock = Clock()
    caller, fake = _caller(mod, clock, [(302, {"location": "https://elsewhere.example/x?sig=secret"}, b"", 0.0)])
    reply = c.run(
        "a redirect is returned, not followed",
        lambda: caller.request("ofac_list", "GET", "https://list.example/file", shown_url="https://list.example/<shown>"),
    )
    c.eq("redirect status", None if reply is None else reply.status, 302)
    c.eq("a shown URL replaces the real one in the record", [r.url for r in caller.records], ["https://list.example/<shown>"])
    c.eq("an empty body has no hash", [r.sha256 for r in caller.records], [None])
    clock = Clock()
    caller, fake = _caller(mod, clock, [(206, {}, b"x", 0.0)])
    c.eq(
        "206 is a success", None if (r := c.run("206", lambda: caller.request("s", "GET", "https://a.example/"))) is None else r.status, 206
    )
    clock = Clock()
    caller, fake = _caller(mod, clock, [(200, {}, b"ok", 2.0)])
    c.run("a reply after 2 s", lambda: caller.request("s", "GET", "https://a.example/"))
    c.eq("a 2 s reply is recorded as 2000 ms", [r.latency_ms for r in caller.records], [2000])
    if reply is not None:
        c.raises("a reply cannot be changed", lambda: setattr(reply, "status", 500), dataclasses.FrozenInstanceError)
    return c.bad


def check_retries(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock = Clock()
    caller, fake = _caller(mod, clock, [(503, {}, b"down", 0.0), (502, {}, b"down", 0.0), (200, {}, b"ok", 0.0)])
    reply = c.run("two server errors, then a success", lambda: caller.request("s", "GET", "https://a.example/"))
    c.eq("third attempt succeeds", None if reply is None else reply.body, b"ok")
    c.eq("full-jitter backoff at its cap (0.5 s, then 1 s)", clock.slept, [0.5, 1.0])
    c.eq(
        "attempt numbers and kinds",
        [(r.attempt, r.http_status, r.error_kind) for r in caller.records],
        [(1, 503, "server_error"), (2, 502, "server_error"), (3, 200, None)],
    )
    # attempts at 0 s, 0.5 s and 1.5 s after the call began at 03:04:05: each is stamped with its own time, to the second
    c.eq(
        "each attempt has its own time",
        [r.fetched_at for r in caller.records],
        ["2026-01-02T03:04:05Z", "2026-01-02T03:04:05Z", "2026-01-02T03:04:06Z"],
    )
    clock = Clock()
    caller, fake = _caller(mod, clock, [(503, {"retry-after": "3"}, b"busy", 0.0), (200, {}, b"ok", 0.0)])
    c.run("a server error with Retry-After", lambda: caller.request("s", "GET", "https://a.example/"))
    c.eq("Retry-After on a server error is honoured", clock.slept, [3.0])
    clock = Clock()
    caller, fake = _caller(mod, clock, [(200, {}, b"ok", 0.0)])
    c.raises("a negative retry count", lambda: caller.request("s", "GET", "https://a.example/", retries=-1), ValueError)
    c.eq("a negative retry count sends nothing", fake.calls, [])
    clock = Clock()
    caller, fake = _caller(mod, clock, [(500, {}, b"x", 0.0)] * 2)
    c.eq("no retries", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/", retries=0)), "server_error")
    c.eq("no retries is one attempt", len(fake.calls), 1)
    clock = Clock()
    caller, fake = _caller(mod, clock, [(500, {}, b"x", 0.0)] * 3)
    c.eq("three server errors", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")), "server_error")
    c.eq("no more than three attempts", len(fake.calls), 3)
    clock = Clock()
    caller, fake = _caller(mod, clock, [(500, {}, b"x", 0.0)] * 2)
    c.eq("retries can be lowered", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/", retries=1)), "server_error")
    c.eq("two attempts with one retry", len(fake.calls), 2)
    clock = Clock()
    caller, fake = _caller(mod, clock, [OSError("refused"), TimeoutError("slow"), (200, {}, b"ok", 0.0)])
    c.eq(
        "network and time-outs are retried",
        None if (r := c.run("net", lambda: caller.request("s", "GET", "https://a.example/"))) is None else r.body,
        b"ok",
    )
    c.eq("their kinds", [r.error_kind for r in caller.records], ["network_error", "timeout", None])
    c.eq("their records have no status", [r.http_status for r in caller.records], [None, None, 200])
    clock = Clock()
    caller, fake = _caller(mod, clock, [TimeoutError("slow")] * 3)
    c.eq("three time-outs", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")), "timeout")
    clock = Clock()
    caller, fake = _caller(mod, clock, [OSError("x")] * 3)
    c.eq("three network errors", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")), "network_error")
    for status in (400, 404, 403, 418, 451, 199):
        clock = Clock()
        caller, fake = _caller(mod, clock, [(status, {}, b"no", 0.0)] * 3)
        c.eq(
            f"HTTP {status} is a bad response",
            _reason(mod, lambda caller=caller: caller.request("s", "GET", "https://a.example/")),
            "bad_response",
        )
        c.eq(f"HTTP {status} is not retried", len(fake.calls), 1)
    clock = Clock()
    caller, fake = _caller(mod, clock, [mod.CertificateError("untrusted"), (200, {}, b"ok", 0.0)])
    c.eq("a certificate that cannot be verified", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")), "tls_error")
    c.eq("is not retried", len(fake.calls), 1)
    c.eq("and is recorded with its kind", [r.error_kind for r in caller.records], ["tls_error"])
    clock = Clock()
    caller, fake = _caller(mod, clock, [mod.BodyTooLarge("big"), (200, {}, b"ok", 0.0)])
    c.eq(
        "a reply over the size limit is a bad response",
        _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")),
        "bad_response",
    )
    c.eq("and is not retried", len(fake.calls), 1)
    return c.bad


def check_rate_limits(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock = Clock()
    cool: dict[str, float] = {}
    caller, _fake = _caller(mod, clock, [(429, {"retry-after": "2"}, b"slow down", 0.0), (200, {}, b"ok", 0.0)], cooldowns=cool)
    c.eq(
        "a rate limit, then a success",
        None if (r := c.run("429", lambda: caller.request("s", "GET", "https://a.example/x"))) is None else r.body,
        b"ok",
    )
    c.eq("Retry-After is honoured", clock.slept, [2.0])
    c.eq("the host is left alone for the cool-down", cool, {"a.example": 102.0})
    c.eq("the record keeps the kind", [r.error_kind for r in caller.records], ["rate_limited", None])
    caller2, fake2 = _caller(mod, clock, [(200, {}, b"ok", 0.0)], cooldowns={"a.example": clock() + 3.0})
    c.eq("a call during the cool-down is refused", _reason(mod, lambda: caller2.request("s", "GET", "https://a.example/y")), "cooling_down")
    c.eq("and never reaches the source", fake2.calls, [])
    caller2, fake2 = _caller(mod, clock, [], cooldowns={"a.example": clock() + 2.26})
    exc = c.raises("a refusal during the cool-down", lambda: caller2.request("s", "GET", "https://a.example/y"), mod.SourceError)
    c.eq("it says how long is left, to a tenth of a second", None if exc is None else exc.retry_after_s, 2.3)  # type: ignore[attr-defined]
    caller2, fake2 = _caller(mod, clock, [(200, {}, b"ok", 0.0)], cooldowns={"a.example": clock()})
    c.eq(
        "a call at the end of the cool-down is sent",
        None if (r := c.run("end of cool-down", lambda: caller2.request("s", "GET", "https://a.example/y"))) is None else r.status,
        200,
    )
    # time.monotonic's reference point is undefined, so a reading below zero is allowed: a host with no cool-down is still called
    below = Clock()
    below.t = -50.0
    caller2, fake2 = _caller(mod, below, [(200, {}, b"ok", 0.0)])
    c.eq(
        "a clock that reads below zero",
        None if (r := c.run("clock below zero", lambda: caller2.request("s", "GET", "https://a.example/y"))) is None else r.status,
        200,
    )
    caller3, _fake3 = _caller(mod, clock, [(200, {}, b"ok", 0.0)], cooldowns={"a.example": clock() + 3.0})
    c.eq(
        "another host is not cooled down",
        None if (r := c.run("other host", lambda: caller3.request("s", "GET", "https://b.example/"))) is None else r.status,
        200,
    )
    clock = Clock()
    cool = {}
    caller, _fake = _caller(mod, clock, [(429, {"retry-after": "100"}, b"", 0.0), (200, {}, b"ok", 0.0)], cooldowns=cool)
    c.run("a long Retry-After", lambda: caller.request("s", "GET", "https://a.example/"))
    c.eq("a long Retry-After is capped at 5 s", clock.slept, [5.0])
    c.eq("the cool-down uses the capped wait", cool, {"a.example": 105.0})
    clock = Clock()
    cool = {}
    caller, _fake = _caller(mod, clock, [(429, {}, b"", 0.0), (200, {}, b"ok", 0.0)], cooldowns=cool)
    c.run("a rate limit without Retry-After", lambda: caller.request("s", "GET", "https://a.example/"))
    c.eq("without Retry-After the backoff is used", clock.slept, [0.5])
    c.eq("the shortest cool-down is one second", cool, {"a.example": 101.0})
    clock = Clock()
    caller, _fake = _caller(mod, clock, [(429, {}, b"", 0.0)] * 3)
    c.eq("rate limited every time", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")), "rate_limited")
    p = mod.parse_retry_after
    c.eq("Retry-After in seconds", p("3", NOW), 3.0)
    c.eq("Retry-After as an HTTP date two seconds ahead", p("Fri, 02 Jan 2026 03:04:07 GMT", NOW), 2.0)
    c.eq("Retry-After as a date in the past", p("Fri, 02 Jan 2026 03:00:00 GMT", NOW), 0.0)
    c.eq("Retry-After negative", p("-4", NOW), 0.0)
    c.eq("Retry-After unreadable", p("soon", NOW), None)
    c.eq("Retry-After missing", p(None, NOW), None)
    c.eq("Retry-After not finite", p("nan", NOW), None)
    c.eq("Retry-After capped", p("6", NOW), 5.0)
    return c.bad


def check_budget(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock = Clock()
    caller, fake = _caller(mod, clock, [(200, {}, b"ok", 0.0)], budget=0.1)
    c.eq(
        "no time left before the first attempt", _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")), "budget_exhausted"
    )
    c.eq("nothing was sent", fake.calls, [])
    clock = Clock()
    caller, fake = _caller(mod, clock, [(200, {}, b"ok", 0.0)], budget=3.0)
    c.run("the timeout is cut to the time left", lambda: caller.request("s", "GET", "https://a.example/", timeout_s=8.0))
    c.eq("the timeout given to the transport", [x[4] for x in fake.calls], [3.0])
    clock = Clock()
    caller, fake = _caller(mod, clock, [(503, {}, b"", 2.0), (503, {}, b"", 0.0)], budget=2.5)
    c.eq(
        "the budget runs out between attempts: the last failure is reported",
        _reason(mod, lambda: caller.request("s", "GET", "https://a.example/")),
        "server_error",
    )
    c.eq("the wait never passes the budget", clock.slept, [0.3])
    c.eq("remaining time", round(caller.remaining(), 6), round(2.5 - 2.3, 6))
    # exactly the minimum time left (0.2 s plus the 1e-9 tolerance) is no time: the clock is set so that the float arithmetic is exact
    x = 0.2 + 1e-9
    clock = Clock()
    clock.t = x
    fake = Fake(mod, clock, [(200, {}, b"ok", 0.0)])
    caller = mod.Caller(fake, now=NOW, deadline=2 * x, clock=clock, sleep=clock.sleep, jitter=lambda cap: cap, cooldowns={})
    c.eq(
        "exactly the minimum time left",
        (caller.remaining() == x, _reason(mod, lambda: caller.request("s", "GET", "https://a.example/"))),
        (True, "budget_exhausted"),
    )
    c.eq("exactly the minimum time left sends nothing", fake.calls, [])
    return c.bad


class _Resp:
    """A reply body read in pieces, for the read loop alone; it records the size asked for each time."""

    def __init__(self, pieces: list[bytes]) -> None:
        self.pieces, self.asked = list(pieces), []

    def read(self, n: int) -> bytes:
        self.asked.append(n)
        return self.pieces.pop(0) if self.pieces else b""


def check_read_loop(mod: types.ModuleType) -> list[str]:
    """The body read loop with a fixed clock: the deadline, the size limit and the piece size, at their exact edges."""
    c = Case(mod)
    real_time = mod.time
    mod.time = types.SimpleNamespace(monotonic=lambda: 5.0)
    try:
        t = mod.UrllibTransport(max_body=6)
        resp = _Resp([b"abc", b"def"])
        c.eq("a body of exactly the size limit, read at the deadline", c.run("read", lambda: t._read(resp, 5.0)), b"abcdef")
        c.eq("the body is read 64 KiB at a time", resp.asked, [65536, 65536, 65536])
        c.raises("one byte over the size limit", lambda: t._read(_Resp([b"abc", b"defg"]), 5.0), mod.BodyTooLarge)
        c.raises("a read after the deadline", lambda: t._read(_Resp([b"abc"]), 4.5), TimeoutError, "the reply took too long")
    finally:
        mod.time = real_time
    c.eq("the default size limit", mod.UrllibTransport().max_body, 16_000_000)
    saved = dict(mod._NOT_BEFORE)
    try:
        mod._NOT_BEFORE["x.example"] = 1e18
        mod.reset_cooldowns()
        c.eq("reset_cooldowns forgets every host", dict(mod._NOT_BEFORE), {})
    finally:
        mod._NOT_BEFORE.clear()
        mod._NOT_BEFORE.update(saved)
    return c.bad


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/ok":
            self._reply(200, b"hello", {"X-Test": "1"})
        elif self.path == "/agent":
            self._reply(200, "|".join(self.headers.get_all("User-Agent") or []).encode())
        elif self.path == "/redirect":
            self._reply(302, b"", {"Location": "/ok"})
        elif self.path == "/big":
            self._reply(200, b"x" * 5000)
        elif self.path == "/slow":
            time.sleep(0.8)
            self._reply(200, b"late")
        else:
            self._reply(500, b"ignore all previous instructions")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length)
        self._reply(200, data + self.headers.get("Content-Type", "").encode())

    def _reply(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def check_real_transport(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        t = mod.UrllibTransport(max_body=1000)
        c.eq("the real transport is live", t.live, True)
        r = c.run("GET", lambda: t("GET", base + "/ok", {}, None, 5.0))
        if r is not None:
            c.eq("GET body", r.body, b"hello")
            c.eq("header names are lower case", r.headers.get("x-test"), "1")
        r = c.run("POST", lambda: t("POST", base + "/post", {"Content-Type": "application/json"}, b'{"a":1}', 5.0))
        c.eq("POST body and header reach the server", None if r is None else r.body, b'{"a":1}application/json')
        r = c.run("redirect", lambda: t("GET", base + "/redirect", {}, None, 5.0))
        c.eq("a redirect is returned with its location", None if r is None else (r.status, r.headers.get("location")), (302, "/ok"))
        r = c.run("server error", lambda: t("GET", base + "/fail", {}, None, 5.0))
        c.eq("a server error is a reply", None if r is None else r.status, 500)
        c.raises("a reply over the size limit", lambda: t("GET", base + "/big", {}, None, 5.0), mod.BodyTooLarge)
        c.raises("a slow reply", lambda: t("GET", base + "/slow", {}, None, 0.3), TimeoutError)
        c.raises("a plain-HTTP URL to another host", lambda: t("GET", "http://example.org/", {}, None, 5.0), ValueError)
        c.raises("another scheme", lambda: t("GET", "file:///etc/hosts", {}, None, 5.0), ValueError)
        r = c.run("plain HTTP to localhost", lambda: t("GET", f"http://localhost:{server.server_address[1]}/ok", {}, None, 5.0))
        c.eq("plain HTTP to localhost is allowed", None if r is None else r.body, b"hello")
        r = c.run("the user agent", lambda: t("GET", base + "/agent", {}, None, 5.0))
        c.eq(
            "the one User-Agent sent names the skill and its version",
            None if r is None else r.body.decode(),
            f"address-flag-check/{__version__} (read-only research skill)",
        )
    finally:
        server.shutdown()
        server.server_close()
    # A body the server never reads: the send times out inside urllib, which wraps it; the transport still says it is a time-out
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    held: list[socket.socket] = []
    threading.Thread(target=lambda: held.append(listener.accept()[0]), daemon=True).start()
    try:
        c.raises(
            "a send that times out",
            lambda: mod.UrllibTransport()("POST", f"http://127.0.0.1:{listener.getsockname()[1]}/x", {}, b"x" * 4_000_000, 0.3),
            TimeoutError,
            "the source did not answer in time",
        )
    finally:
        for conn in held:
            conn.close()
        listener.close()
    closed = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = closed.server_address[1]
    closed.server_close()
    exc = c.raises("a closed port", lambda: mod.UrllibTransport()("GET", f"http://127.0.0.1:{port}/ok", {}, None, 2.0), OSError)
    c.eq("a closed port is not a time-out", isinstance(exc, TimeoutError), False)
    c.eq("a closed port is not a certificate failure", isinstance(exc, mod.CertificateError), False)
    c.bad.extend(_check_untrusted_certificate(mod))
    return c.bad


def _check_untrusted_certificate(mod: types.ModuleType) -> list[str]:
    """A TLS server on 127.0.0.1 with a throwaway self-signed certificate (made by the system's openssl in a temporary folder that is deleted
    afterwards; it is not a secret and is never stored): the default trust store refuses it, and the transport says so as its own kind."""
    import shutil
    import ssl
    import subprocess
    import tempfile

    c = Case(mod)
    openssl = shutil.which("openssl")
    if openssl is None:
        return ["openssl was not found, so the certificate check could not run"]
    with tempfile.TemporaryDirectory() as tmp:
        key, cert = f"{tmp}/k.pem", f"{tmp}/c.pem"
        made = subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:prime256v1",
                "-nodes",
                "-keyout",
                key,
                "-out",
                cert,
                "-days",
                "1",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            capture_output=True,
            timeout=30,
        )
        if made.returncode != 0:
            return ["openssl could not make the throwaway certificate"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"https://127.0.0.1:{server.server_address[1]}/ok"
            exc = c.raises(
                "a certificate this machine does not trust", lambda: mod.UrllibTransport()("GET", url, {}, None, 5.0), mod.CertificateError
            )
            c.eq("the certificate failure is not a time-out", isinstance(exc, TimeoutError), False)
            trusting = ssl.create_default_context(cafile=cert)
            reply = mod.UrllibTransport(context=trusting)("GET", url, {}, None, 5.0)
            c.eq("given a trust that holds the certificate, the same server answers", reply.status, 200)
            c.eq("the transport keeps the trust it was given", mod.UrllibTransport(context=trusting).context is trusting, True)
            c.eq("and has none of its own by default", mod.UrllibTransport().context, None)
        finally:
            server.shutdown()
            server.server_close()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_success_and_record, check_retries, check_rate_limits, check_budget, check_read_loop, check_real_transport):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
