"""Checks of ``exit_liquidity_check.transport``, runnable against a mutant: Retry-After, status classification, the caller's retry, back-off,
cool-down and budget rules with a scripted clock, and the urllib transport against a real server on 127.0.0.1 (nothing leaves this machine)."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import ssl
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.checks_support import Case

NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)


class Clock:
    """A clock that only moves when told to."""

    def __init__(self) -> None:
        self.t = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _script(mod: types.ModuleType, clock: Clock, steps: list[Any], took: float = 0.0) -> tuple[Any, list[tuple[Any, ...]]]:
    """A transport that plays ``steps`` in order (a reply, a (status, headers, body) tuple, or an exception to raise)."""
    seen: list[tuple[Any, ...]] = []
    queue = list(steps)

    def transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> Any:
        seen.append((method, url, headers, body, timeout))
        clock.t += took
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, tuple):
            return mod.HttpReply(step[0], step[1], step[2])
        return step

    return transport, seen


def _caller(
    mod: types.ModuleType,
    clock: Clock,
    transport: Any,
    *,
    budget: float = 60.0,
    cooldowns: dict[str, float] | None = None,
    jitter: Any = None,
) -> Any:
    return mod.Caller(
        transport,
        now=NOW,
        deadline=clock.t + budget,
        clock=clock,
        sleep=clock.sleep,
        jitter=jitter or (lambda cap: cap),
        cooldowns={} if cooldowns is None else cooldowns,
    )


def _reason(fn: Any) -> tuple[str, int | None, float | None] | None:
    try:
        fn()
    except Exception as err:
        reason = getattr(err, "reason", None)
        if reason is None:
            return (type(err).__name__, None, None)
        return reason, err.http_status, err.retry_after_s
    return None


OK = (200, {}, b'{"a": 1}')


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "constants",
        (mod.MAX_BODY_BYTES, mod.MAX_RETRY_AFTER_S, mod.COOLDOWN_MIN_S, mod.MIN_LEFT_S, mod.LOCAL_HOSTS),
        (16_000_000, 5.0, 1.0, 0.2, ("127.0.0.1", "localhost")),
    )
    c.eq("retryable", mod.RETRYABLE, frozenset({"rate_limited", "server_error", "network_error", "timeout"}))
    c.eq("user agent", mod.USER_AGENT, "exit-liquidity-check/0.1.0 (read-only research skill)")

    # parse_retry_after
    pra = mod.parse_retry_after
    for label, value, want in (
        ("none", None, None),
        ("empty", "", None),
        ("seconds", "3", 3.0),
        ("fraction with spaces", " 2.5 ", 2.5),
        ("zero", "0", 0.0),
        ("negative", "-5", 0.0),
        ("cap", "100", 5.0),
        ("exactly the cap", "5", 5.0),
        ("just under the cap", "4.9", 4.9),
        ("nan", "nan", None),
        ("inf", "inf", None),
        ("-inf", "-inf", None),
        ("garbage", "soon", None),
        ("date in 3 s", "Mon, 09 Nov 2026 12:00:03 GMT", 3.0),
        ("date in the past", "Mon, 09 Nov 2026 11:59:00 GMT", 0.0),
        ("date far ahead", "Mon, 09 Nov 2026 13:00:00 GMT", 5.0),
        ("date without a zone", "Mon, 09 Nov 2026 12:00:03 -0000", 3.0),
        ("date with an offset", "Mon, 09 Nov 2026 14:00:03 +0200", 3.0),
        ("date with spaces", "  Mon, 09 Nov 2026 12:00:03 GMT  ", 3.0),
    ):
        c.eq(f"Retry-After {label}", c.run(f"Retry-After {label}", lambda value=value: pra(value, NOW)), want)

    # _classify
    cl = mod._classify
    for status in (200, 201, 204, 299):
        c.eq(f"{status} is an answer", cl(mod.HttpReply(status, {}, b""), NOW), None)
    for status in (100, 199, 300, 301, 302, 304, 400, 401, 403, 404, 418):
        err = cl(mod.HttpReply(status, {}, b""), NOW)
        c.eq(f"{status} is an unusable reply", (err.reason, err.http_status, err.retry_after_s), ("bad_response", status, None))
    err = cl(mod.HttpReply(429, {"retry-after": "2"}, b""), NOW)
    c.eq("429", (err.reason, err.http_status, err.retry_after_s), ("rate_limited", 429, 2.0))
    err = cl(mod.HttpReply(429, {}, b""), NOW)
    c.eq("429 without Retry-After", (err.reason, err.retry_after_s), ("rate_limited", None))
    for status in (500, 502, 503, 599):
        err = cl(mod.HttpReply(status, {"retry-after": "1"}, b""), NOW)
        c.eq(f"{status}", (err.reason, err.http_status, err.retry_after_s), ("server_error", status, 1.0))

    # Caller.request: the happy path and its record
    clock = Clock()
    transport, seen = _script(mod, clock, [(200, {}, b'{"a": 1}')], took=2.0)
    caller = _caller(mod, clock, transport)
    reply = caller.request("src", "GET", "https://h.example/x?y=1", headers={"A": "b"}, body=b"B", timeout_s=3.0)
    c.eq("reply", (reply.status, reply.body), (200, b'{"a": 1}'))
    c.eq(
        "the transport is called with the method, url, headers, body and timeout",
        seen,
        [("GET", "https://h.example/x?y=1", {"A": "b"}, b"B", 3.0)],
    )
    c.eq(
        "a record per attempt",
        [r.as_dict() for r in caller.records],
        [
            {
                "source": "src",
                "url": "https://h.example/x?y=1",
                "method": "GET",
                "attempt": 1,
                "http_status": 200,
                "latency_ms": 2000,
                "bytes": 8,
                "sha256": hashlib.sha256(b'{"a": 1}').hexdigest(),
                "fetched_at": "2026-11-09T12:00:00Z",
                "error_kind": None,
            }
        ],
    )
    c.eq("remaining time", caller.remaining(), 60.0 - 2.0)
    c.eq("the caller keeps what it was given", (caller.now, caller.deadline), (NOW, 160.0))

    clock = Clock()
    transport, seen = _script(mod, clock, [OK])
    caller = _caller(mod, clock, transport)
    caller.request("src", "GET", "https://h.example/x", shown_url="https://h.example/shown", headers={"A": "1"})
    seen[0][2]["B"] = "2"
    c.eq("a shown url replaces the real one in the record", caller.records[0].url, "https://h.example/shown")
    c.eq("the transport gets its own copy of the headers", {"A": "1"}.keys() <= seen[0][2].keys(), True)
    c.eq("no headers is an empty dict, no body is none", (_script(mod, Clock(), [OK])[1],), ([],))
    clock = Clock()
    transport, seen = _script(mod, clock, [OK])
    _caller(mod, clock, transport).request("src", "GET", "https://h.example/")
    c.eq("defaults: no headers, no body, timeout 8 s", seen, [("GET", "https://h.example/", {}, None, 8.0)])
    clock = Clock()
    transport, seen = _script(mod, clock, [OK])
    _caller(mod, clock, transport, budget=2.0).request("src", "GET", "https://h.example/")
    c.eq("the timeout is the smaller of the request's and the time left", seen[0][4], 2.0)
    clock = Clock()
    transport, seen = _script(mod, clock, [(200, {}, b"")])
    caller = _caller(mod, clock, transport)
    caller.request("src", "GET", "https://h.example/")
    c.eq("an empty body has no hash and no bytes", (caller.records[0].bytes, caller.records[0].sha256), (0, None))
    clock = Clock()
    transport, seen = _script(mod, clock, [OK])
    caller = _caller(mod, clock, transport)
    clock.t += 5
    caller.request("src", "GET", "https://h.example/")
    c.eq("fetched_at follows the clock", caller.records[0].fetched_at, "2026-11-09T12:00:05Z")

    # retries
    c.eq(
        "negative retries",
        _reason(lambda: _caller(mod, Clock(), _script(mod, Clock(), [])[0]).request("s", "GET", "https://h.example/", retries=-1)),
        ("ValueError", None, None),
    )
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {}, b"x"), (200, {}, b"ok")])
    jitters: list[float] = []

    def jitter(cap: float) -> float:
        jitters.append(cap)
        return cap

    caller = _caller(mod, clock, transport, jitter=jitter)
    reply = caller.request("s", "GET", "https://h.example/")
    c.eq(
        "a 503 is retried once and then succeeds",
        (reply.body, [r.attempt for r in caller.records], [r.error_kind for r in caller.records], [r.http_status for r in caller.records]),
        (b"ok", [1, 2], ["server_error", None], [503, 200]),
    )
    c.eq("back-off: jitter over min(4, 0.5 * 2**attempt), slept as drawn", (jitters, clock.slept), ([0.5], [0.5]))
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {}, b""), (503, {}, b""), (503, {}, b"")])
    jitters.clear()
    caller = _caller(mod, clock, transport, jitter=jitter)
    c.eq("retries run out", _reason(lambda: caller.request("s", "GET", "https://h.example/")), ("server_error", 503, None))
    c.eq("three attempts, back-off doubles", (len(seen), jitters, clock.slept), (3, [0.5, 1.0], [0.5, 1.0]))
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {}, b""), (503, {}, b"")])
    c.eq(
        "retries=1 makes two attempts",
        (_reason(lambda: _caller(mod, clock, transport).request("s", "GET", "https://h.example/", retries=1)), len(seen)),
        (("server_error", 503, None), 2),
    )
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {}, b"")])
    c.eq(
        "retries=0 makes one attempt and does not sleep",
        (_reason(lambda: _caller(mod, clock, transport).request("s", "GET", "https://h.example/", retries=0)), len(seen), clock.slept),
        (("server_error", 503, None), 1, []),
    )
    clock = Clock()
    steps: list[Any] = [(503, {}, b"")] * 8
    transport, seen = _script(mod, clock, steps)
    jitters.clear()
    _reason(lambda: _caller(mod, clock, transport, jitter=jitter).request("s", "GET", "https://h.example/", retries=7))
    c.eq("the back-off cap is 4 s", jitters, [0.5, 1.0, 2.0, 4.0, 4.0, 4.0, 4.0])
    for label, step, reason in (
        ("a 404", (404, {}, b"nope"), "bad_response"),
        ("a 302", (302, {}, b""), "bad_response"),
        ("a body too large", mod.BodyTooLarge("x"), "bad_response"),
        ("a certificate failure", mod.CertificateError("x"), "tls_error"),
    ):
        clock = Clock()
        transport, seen = _script(mod, clock, [step, OK])
        got = _reason(lambda transport=transport, clock=clock: _caller(mod, clock, transport).request("s", "GET", "https://h.example/"))
        c.eq(f"{label} is not retried", (got is not None and got[0], len(seen)), (reason, 1))
    for label, step, reason in (
        ("a time-out", TimeoutError("x"), "timeout"),
        ("a network error", OSError("x"), "network_error"),
        ("a 429", (429, {}, b""), "rate_limited"),
    ):
        clock = Clock()
        transport, seen = _script(mod, clock, [step, OK])
        caller = _caller(mod, clock, transport)
        reply = caller.request("s", "GET", "https://h.example/")
        c.eq(
            f"{label} is retried",
            (reply.status, len(seen), caller.records[0].error_kind, caller.records[0].http_status),
            (200, 2, reason, 429 if reason == "rate_limited" else None),
        )
    clock = Clock()
    transport, seen = _script(mod, clock, [mod.BodyTooLarge("x")])
    caller = _caller(mod, clock, transport)
    _reason(lambda: caller.request("s", "GET", "https://h.example/"))
    c.eq(
        "a transport that raised leaves a record with no status, no bytes and no hash",
        (caller.records[0].http_status, caller.records[0].bytes, caller.records[0].sha256),
        (None, 0, None),
    )
    clock = Clock()
    transport, seen = _script(mod, clock, [(200, {}, b"x")])
    c.eq(
        "the first 2xx is returned, not retried",
        len(seen) + 0 if _caller(mod, clock, transport).request("s", "GET", "https://h.example/") else -1,
        1,
    )

    # Retry-After and the wait
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {"retry-after": "3"}, b""), OK])
    _caller(mod, clock, transport, jitter=lambda cap: 0.0).request("s", "GET", "https://h.example/")
    c.eq("the wait is the longer of Retry-After and the back-off", clock.slept, [3.0])
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {"retry-after": "0.1"}, b""), OK])
    _caller(mod, clock, transport, jitter=lambda cap: 0.5).request("s", "GET", "https://h.example/")
    c.eq("a short Retry-After does not shorten the back-off", clock.slept, [0.5])
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {"retry-after": "5"}, b""), OK])
    got = _reason(lambda: _caller(mod, clock, transport, budget=3.0, jitter=lambda cap: 0.0).request("s", "GET", "https://h.example/"))
    c.eq(
        "the wait is cut to leave 0.2 s of the budget, and the call then ends",
        (clock.slept, got, len(seen)),
        ([3.0 - 0.2], ("server_error", 503, 5.0), 1),
    )
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {"retry-after": "1"}, b""), OK])
    _caller(mod, clock, transport, budget=3.0, jitter=lambda cap: 0.0).request("s", "GET", "https://h.example/")
    c.eq("a wait inside the budget is taken whole", clock.slept, [1.0])
    clock = Clock()
    transport, seen = _script(mod, clock, [(503, {}, b""), OK])
    c.eq(
        "a wait that would spend the whole budget ends the call",
        (
            _reason(lambda: _caller(mod, clock, transport, budget=0.5, jitter=lambda cap: 9.0).request("s", "GET", "https://h.example/")),
            len(seen),
        ),
        (("server_error", 503, None), 1),
    )

    # budget
    for budget, want in ((0.0, "budget_exhausted"), (0.2, "budget_exhausted"), (0.25, None)):
        clock = Clock()
        transport, seen = _script(mod, clock, [OK])
        got = _reason(
            lambda transport=transport, clock=clock, budget=budget: _caller(mod, clock, transport, budget=budget).request(
                "s", "GET", "https://h.example/"
            )
        )
        c.eq(f"a budget of {budget} s", got and got[0], want)
    clock = Clock()
    clock.t = 0.0
    transport, seen = _script(mod, clock, [OK])
    c.eq(
        "a budget of exactly 0.2 s plus the float tolerance is spent",
        _reason(lambda: _caller(mod, clock, transport, budget=0.2 + 1e-9).request("s", "GET", "https://h.example/")),
        ("budget_exhausted", None, None),
    )
    clock = Clock()
    transport, seen = _script(mod, clock, [OK])
    caller = _caller(mod, clock, transport, budget=0.0)
    c.eq(
        "an exhausted budget makes no call and no record",
        (_reason(lambda: caller.request("s", "GET", "https://h.example/")), seen, caller.records),
        (("budget_exhausted", None, None), [], []),
    )

    # cool-down
    clock = Clock()
    cool: dict[str, float] = {}
    transport, seen = _script(mod, clock, [(429, {}, b""), (429, {}, b""), (429, {}, b"")])
    caller = _caller(mod, clock, transport, cooldowns=cool)
    c.eq(
        "a 429 without Retry-After cools the host down for at least 1 s",
        (_reason(lambda: caller.request("s", "GET", "https://h.example/")), len(seen)),
        (("rate_limited", 429, None), 3),
    )
    c.eq("the cool-down is keyed by host", list(cool), ["h.example"])
    clock = Clock()
    cool = {}
    transport, seen = _script(mod, clock, [(429, {"retry-after": "4"}, b"")])
    _reason(lambda: _caller(mod, clock, transport, cooldowns=cool).request("s", "GET", "https://h.example/", retries=0))
    c.eq("the cool-down follows Retry-After", cool, {"h.example": 104.0})
    clock = Clock()
    cool = {}
    transport, seen = _script(mod, clock, [(429, {"retry-after": "0.2"}, b"")])
    _reason(lambda: _caller(mod, clock, transport, cooldowns=cool).request("s", "GET", "https://h.example/", retries=0))
    c.eq("the cool-down is at least 1 s", cool, {"h.example": 101.0})
    clock = Clock()
    cool = {"h.example": 103.46}
    transport, seen = _script(mod, clock, [OK])
    caller = _caller(mod, clock, transport, cooldowns=cool)
    c.eq(
        "a host in cool-down is not called",
        (_reason(lambda: caller.request("s", "GET", "https://h.example/y")), seen, caller.records),
        (("cooling_down", None, 3.5), [], []),
    )
    c.eq("another host is called", _caller(mod, clock, transport, cooldowns=cool).request("s", "GET", "https://other.example/").status, 200)
    clock = Clock()
    cool = {"h.example": 100.0}
    transport, seen = _script(mod, clock, [OK])
    c.eq(
        "a cool-down that has just ended is over",
        _caller(mod, clock, transport, cooldowns=cool).request("s", "GET", "https://h.example/").status,
        200,
    )
    clock = Clock()
    transport, seen = _script(mod, clock, [(500, {}, b""), (500, {}, b""), (500, {}, b"")])
    cool = {}
    _reason(lambda: _caller(mod, clock, transport, cooldowns=cool).request("s", "GET", "https://h.example/"))
    c.eq("only a rate limit cools a host down", cool, {})
    mod.reset_cooldowns()
    mod._NOT_BEFORE["x"] = 1.0
    mod.reset_cooldowns()
    c.eq("reset_cooldowns", dict(mod._NOT_BEFORE), {})
    shared = mod.Caller(lambda *a: None, now=NOW, deadline=0.0)  # type: ignore[arg-type]
    c.eq("a caller without its own cool-downs shares the process's", shared.cooldowns is mod._NOT_BEFORE, True)
    c.eq("a caller defaults to the real clock", shared.clock is time.monotonic, True)
    own = mod.Caller(lambda *a: None, now=NOW, deadline=0.0, cooldowns={})  # type: ignore[arg-type]
    c.eq("a caller with an empty cool-down dict keeps it", own.cooldowns is not mod._NOT_BEFORE, True)
    c.eq("the default jitter is within [0, cap]", all(0.0 <= shared.jitter(2.0) <= 2.0 for _ in range(50)), True)

    c.failed.extend(_urllib_checks(mod))
    return c.failed


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _reply(self, status: int, body: bytes, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _any(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        data = self.rfile.read(length) if length else b""
        if self.path == "/echo":
            seen = {"method": self.command, "ua": self.headers.get("User-Agent"), "x": self.headers.get("X-Test"), "body": data.decode()}
            self._reply(200, json.dumps(seen).encode(), {"X-Reply": "Yes"})
        elif self.path == "/redirect":
            self._reply(302, b"moved", {"Location": "/echo"})
        elif self.path == "/missing":
            self._reply(404, b"nothing here", {"X-Err": "1"})
        elif self.path == "/big":
            self._reply(200, b"x" * 100)
        elif self.path == "/slow":
            time.sleep(1.5)
            self._reply(200, b"late")
        else:
            self._reply(200, b"")

    do_GET = do_POST = _any


class _Resp:
    """A reply body that hands out ``chunks``, then ends."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks, self.sizes = list(chunks), []

    def read(self, n: int) -> bytes:
        self.sizes.append(n)
        return self.chunks.pop(0) if self.chunks else b""


def _urllib_checks(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        t = mod.UrllibTransport()
        c.eq("live", t.live, True)
        r = t("GET", base + "/echo", {"X-Test": "v"}, None, 5.0)
        want = {"method": "GET", "ua": mod.USER_AGENT, "x": "v", "body": ""}
        c.eq("a GET", (r.status, json.loads(r.body), r.headers.get("x-reply")), (200, want, "Yes"))
        c.eq("header names are lower case", all(k == k.lower() for k in r.headers), True)
        r = t("POST", base + "/echo", {"User-Agent": "custom/1"}, b"payload", 5.0)
        c.eq(
            "a POST with a body and an overridden User-Agent",
            (json.loads(r.body)["method"], json.loads(r.body)["body"], json.loads(r.body)["ua"]),
            ("POST", "payload", "custom/1"),
        )
        r = t("GET", base + "/redirect", {}, None, 5.0)
        c.eq("a redirect is returned, not followed", (r.status, r.headers.get("location"), r.body), (302, "/echo", b"moved"))
        r = t("GET", base + "/missing", {}, None, 5.0)
        c.eq(
            "an error status is a reply with its headers and body", (r.status, r.headers.get("x-err"), r.body), (404, "1", b"nothing here")
        )
        r = mod.UrllibTransport(max_body=100)("GET", base + "/big", {}, None, 5.0)
        c.eq("a body of exactly the limit is read", (r.status, len(r.body)), (200, 100))
        try:
            mod.UrllibTransport(max_body=99)("GET", base + "/big", {}, None, 5.0)
        except mod.BodyTooLarge as err:
            c.eq("a body past the limit", str(err), "the reply is larger than the allowed size")
        else:
            c.failed.append("a body one byte past the limit was read")
        started = time.monotonic()
        try:
            t("GET", base + "/slow", {}, None, 0.3)
        except TimeoutError:
            pass
        except Exception as err:
            c.failed.append(f"a slow source raised {type(err).__name__}: {err}")
        else:
            c.failed.append("a slow source was waited for")
        c.eq("... and the wait ended at the time-out", time.monotonic() - started < 1.2, True)
        r = mod.UrllibTransport(context=ssl.create_default_context())("GET", base + "/echo", {}, None, 5.0)
        c.eq("a transport with a TLS context still reaches a local server", r.status, 200)
        r = t("GET", "http://localhost:" + str(srv.server_address[1]) + "/echo", {}, None, 5.0)
        c.eq("localhost is allowed over http", r.status, 200)
    finally:
        srv.shutdown()
        srv.server_close()
    # a closed port
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    try:
        mod.UrllibTransport()("GET", f"http://127.0.0.1:{closed}/", {}, None, 2.0)
    except TimeoutError:
        c.failed.append("a refused connection was reported as a time-out")
    except OSError as err:
        c.eq("a refused connection", str(err), "the source could not be reached")
    else:
        c.failed.append("a refused connection answered")
    # the scheme rule: nothing that is not https, or http to this machine, is fetched (the host names never resolve)
    for url in (
        "http://example.invalid/",
        "http://127.0.0.1.invalid/",
        "ftp://127.0.0.1/x",
        "file:///etc/hostname",
        "//127.0.0.1/",
        "127.0.0.1",
        "http://localhost.invalid/",
    ):
        try:
            mod.UrllibTransport()("GET", url, {}, None, 1.0)
        except ValueError as err:
            c.eq(f"{url} is refused", str(err), "only https URLs (or http to this machine) are fetched")
        except Exception as err:
            c.failed.append(f"{url}: raised {type(err).__name__}, want ValueError")
        else:
            c.failed.append(f"{url}: was fetched")
    # TLS and network failures, with a stand-in opener
    real_build = mod.urllib.request.build_opener
    built: list[tuple[Any, ...]] = []

    class Opener:
        def __init__(self, reason: BaseException) -> None:
            self.reason = reason

        def open(self, request: Any, timeout: float) -> Any:
            raise urllib.error.URLError(self.reason)

    try:
        for label, reason, kind, text in (
            (
                "a certificate that cannot be verified",
                ssl.SSLCertVerificationError(),
                "CertificateError",
                "the source's certificate could not be verified",
            ),
            ("a time-out inside the stack", TimeoutError(), "TimeoutError", "the source did not answer in time"),
            ("another network failure", ConnectionResetError(), "OSError", "the source could not be reached"),
            ("a name that does not resolve", "no such host", "OSError", "the source could not be reached"),
        ):
            mod.urllib.request.build_opener = lambda *h, reason=reason: (built.append(h), Opener(reason))[1]  # type: ignore[assignment]
            try:
                mod.UrllibTransport()("GET", "https://h.example/", {}, None, 1.0)
            except Exception as err:
                c.eq(label, (type(err).__name__, str(err)), (kind, text))
            else:
                c.failed.append(f"{label}: no error")
        ctx = ssl.create_default_context()
        built.clear()
        with contextlib.suppress(Exception):
            mod.UrllibTransport(context=ctx)("GET", "https://h.example/", {}, None, 1.0)
        c.eq(
            "a TLS context adds an HTTPS handler that carries it",
            (len(built[0]), isinstance(built[0][1], urllib.request.HTTPSHandler)),
            (2, True),
        )
        built.clear()
        with contextlib.suppress(Exception):
            mod.UrllibTransport()("GET", "https://h.example/", {}, None, 1.0)
        c.eq("no context adds no handler beyond the redirect refuser", len(built[0]), 1)
    finally:
        mod.urllib.request.build_opener = real_build
    # the body reader on its own
    t = mod.UrllibTransport(max_body=5)
    resp = _Resp([b"ab", b"cde"])
    c.eq("the reader joins chunks up to the limit", t._read(resp, time.monotonic() + 60), b"abcde")
    c.eq("... reading 64 KiB at a time", resp.sizes, [65536, 65536, 65536])
    try:
        t._read(_Resp([b"abc", b"def"]), time.monotonic() + 60)
    except mod.BodyTooLarge:
        pass
    else:
        c.failed.append("the reader passed the limit")
    try:
        t._read(_Resp([b"a"]), time.monotonic() - 1)
    except TimeoutError as err:
        c.eq("a reply past its deadline", str(err), "the reply took too long")
    else:
        c.failed.append("the reader ignored the deadline")
    c.eq("an empty reply", t._read(_Resp([]), time.monotonic() + 60), b"")
    c.eq("the default body limit is 16 MB", mod.UrllibTransport().max_body, 16_000_000)
    real_time = mod.time
    mod.time = types.SimpleNamespace(monotonic=lambda: 5.0)
    try:
        c.eq("a reply read at exactly its deadline is still read", t._read(_Resp([b"ab"]), 5.0), b"ab")
    except TimeoutError:
        c.failed.append("a reply read at exactly its deadline was refused")
    finally:
        mod.time = real_time
    try:
        mod.HttpReply(200, {}, b"").status = 500  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        c.failed.append("HttpReply must be frozen")
    c.eq("the redirect refuser refuses", mod._NoRedirect().redirect_request(None, None, 302, "x", {}, "http://x/"), None)
    return c.failed
