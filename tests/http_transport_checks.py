"""Checks of the shared HTTP exchange (``http_transport``) against a fake server on loopback, runnable against a mutant.

The checks use a minimal subclass that routes like RYO, so the base class is exercised through the same fake server as the RYO transport."""

from __future__ import annotations

import contextlib
import gc
import http.client
import json
import socket
import ssl
import threading
import time
import types
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori import canonical
from hoshitori.capture import new_capture_id
from tests import ryo_data as D
from tests.ryo_fake_server import KEY, TRACE, Behaviour, FakeRyo, Seen, error

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def clock() -> datetime:
    return T0 + timedelta(seconds=1)


def Probe(mod: types.ModuleType) -> Any:
    """A transport on the (possibly mutated) base class that sends what the fake server expects."""
    routes = {"_health": "/health", "_whoami": "/whoami", "_catalogue": "/tools"}

    class _Probe(mod.HttpTransport):  # type: ignore[name-defined,misc]
        def __init__(self, base_url: str, key: Any, clock: Any, *, provider: str = "probe", **kwargs: Any) -> None:
            super().__init__(base_url, clock, provider=provider, **kwargs)
            self._key = key

        def fetch(self, operation: str, args: Any, timeout_s: float, capture_id: str, sent_at: datetime) -> Any:
            if operation in routes:
                method, path, payload = "GET", self._prefix + routes[operation], None
            else:
                method, path, payload = "POST", f"{self._prefix}/tools/{operation}/call", canonical.dumps(dict(args))
            key = self._key()
            headers = {"Authorization": f"Bearer {key}"} if key and operation != "_health" else {}
            return self._exchange(operation, args, method, path, payload, headers, timeout_s, capture_id, sent_at)

    return _Probe


class Rig:
    """A fake server with a script slot, and the helpers the scenarios share."""

    def __init__(self, mod: types.ModuleType, bad: list[str]) -> None:
        self.mod, self.bad = mod, bad
        self.script: dict[str, Any] = {"next": None}
        self.server = FakeRyo(script=self._play)
        self.transport: Any = None

    def _play(self, seen: Seen) -> Behaviour | None:
        step = self.script["next"]
        return step(seen) if callable(step) else step

    def __enter__(self) -> Rig:
        self.server.start()
        self.transport = Probe(self.mod)(self.server.url, lambda: KEY, clock, allow_insecure_loopback=True)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.stop()

    def fetch(self, operation: str, args: dict[str, Any] | None = None, timeout: float = 5.0, behaviour: Any = None, tp: Any = None) -> Any:
        self.script["next"] = behaviour
        return (tp or self.transport).fetch(operation, args or {}, timeout, new_capture_id(), T0)

    def fails(
        self,
        label: str,
        error_class: Any,
        operation: str = "analyze_token",
        args: dict[str, Any] | None = None,
        timeout: float = 5.0,
        behaviour: Any = None,
        tp: Any = None,
        requests: int | None = None,
    ) -> Any:
        before = len(self.server.seen)
        try:
            self.fetch(operation, args if args is not None else {"symbol": "SOL"}, timeout, behaviour, tp)
            self.bad.append(f"{label}: no failure")
        except self.mod.TransportFailure as exc:
            if exc.error_class is not error_class:
                self.bad.append(f"{label}: {exc.error_class.value} ({exc.detail}), expected {error_class.value}")
            if KEY in str(exc) or KEY in exc.detail:
                self.bad.append(f"{label}: the key is in the message")
            if requests is not None and len(self.server.seen) - before != requests:
                self.bad.append(f"{label}: {len(self.server.seen) - before} requests were made, expected {requests}")
            return exc
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
        return None


def part_exchange(mod: types.ModuleType, bad: list[str], rig: Rig) -> None:
    server, fetch = rig.server, rig.fetch
    # --- a tool call ----------------------------------------------------------------------------------------------------------------
    cap = fetch("analyze_token", {"symbol": "SOL"})
    seen = server.seen[-1]
    if (cap.status, cap.source, cap.t_send, cap.trace_id) != (200, "probe", T0, TRACE):
        bad.append(f"the capture: {cap.status} {cap.source} {cap.t_send} {cap.trace_id}")
    if cap.received_at != clock():
        bad.append("received_at is the clock at the end")
    if cap.body != D.rest_body("analyze_token", D.result("analyze_token", request={"symbol": "SOL"})):
        bad.append("the body is not the bytes the server sent")
    expected_request = {
        "arguments": {"symbol": "SOL"},
        "method": "POST",
        "operation": "analyze_token",
        "surface": "rest",
        "url": f"{server.url}/tools/analyze_token/call",
    }
    if dict(cap.request) != expected_request:
        bad.append(f"the request: {dict(cap.request)}")
    for name in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-trace-id", "content-type", "content-length", "date"):
        if name not in cap.headers:
            bad.append(f"the header {name} was dropped")
    if any("auth" in name or "cookie" in name for name in cap.headers):
        bad.append("a credential-like header was kept")
    if (seen.method, seen.path) != ("POST", "/api/mcp/tools/analyze_token/call") or seen.body != b'{"symbol":"SOL"}':
        bad.append(f"what the server saw: {seen.method} {seen.path} {seen.body!r}")
    h = seen.headers
    if h.get("authorization") != f"Bearer {KEY}" or h.get("content-type") != "application/json" or h.get("accept") != "application/json":
        bad.append(f"request headers: {h}")
    if h.get("accept-encoding") != "identity" or h.get("connection") != "close" or not h.get("user-agent", "").startswith("hoshitori"):
        bad.append(f"more request headers: {h}")
    fetch("deep_analysis", {"symbol": "SOL", "include_perp": True, "chain": "eth"})
    if server.seen[-1].body != b'{"chain":"eth","include_perp":true,"symbol":"SOL"}':
        bad.append(f"arguments are sent in canonical form: {server.seen[-1].body!r}")
    fetch("market_overview", {})
    if server.seen[-1].body != b"{}" or server.seen[-1].method != "POST":
        bad.append("a tool with no arguments sends {}")

    rig.transport._exchange(
        "stamp", {}, "POST", rig.transport._prefix + "/tsr", b"\x30\x00", {"Content-Type": "application/timestamp-query"}, 5.0,
        new_capture_id(), T0,
    )  # fmt: skip
    if server.seen[-1].headers.get("content-type") != "application/timestamp-query":
        bad.append(f"a body that an adapter has named keeps its content type: {server.seen[-1].headers.get('content-type')}")

    by_name = Probe(mod)(server.url.replace("127.0.0.1", "localhost"), lambda: KEY, clock, allow_insecure_loopback=True)
    if fetch("_health", {}, tp=by_name).status != 200 or not server.seen[-1].headers.get("host", "").startswith("localhost:"):
        bad.append(f"the host of the request is the configured one: {server.seen[-1].headers.get('host')}")

    # --- free reads ------------------------------------------------------------------------------------------------------------------
    for op, path, authed in (
        ("_health", "/api/mcp/health", False),
        ("_whoami", "/api/mcp/whoami", True),
        ("_catalogue", "/api/mcp/tools", True),
    ):
        cap = fetch(op, {})
        seen = server.seen[-1]
        if (seen.method, seen.path, seen.body) != ("GET", path, b"") or cap.status != 200:
            bad.append(f"{op}: {seen.method} {seen.path} {cap.status}")
        if ("authorization" in seen.headers) is not authed:
            bad.append(f"{op}: the key {'was not' if authed else 'must not be'} sent")
    nokey = Probe(mod)(server.url, lambda: None, clock, allow_insecure_loopback=True)
    if fetch("_health", {}, tp=nokey).status != 200:
        bad.append("health needs no key")

    # --- statuses and headers pass through, headers are screened ------------------------------------------------------------------
    cap = fetch(
        "analyze_token",
        {"symbol": "SOL"},
        behaviour=error(
            429, "RATE_LIMITED", "slow down", {"retry-after": "60", "set-cookie": "a=b", "x-evil": "1", "x-ratelimit-remaining": "0"}
        ),
    )
    if cap.status != 429 or cap.headers.get("retry-after") != "60" or cap.headers.get("x-ratelimit-remaining") != "0":
        bad.append(f"a 429: {cap.status} {dict(cap.headers)}")
    if "set-cookie" in cap.headers or "x-evil" in cap.headers:
        bad.append("headers that are not on the list were kept")
    cap = fetch(
        "analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(200, b"{}", {"x-ratelimit-remaining": "9" * 900, "retry-after": "  7  "})
    )
    if len(cap.headers["x-ratelimit-remaining"]) != 512 or cap.headers["retry-after"] != "7":
        bad.append("header values are trimmed and cut at 512")
    cap = fetch(
        "analyze_token",
        {"symbol": "SOL"},
        behaviour=Behaviour(
            raw=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-RateLimit-Remaining: 1\r\nx-ratelimit-remaining: 2\r\nConnection: close\r\n\r\n{}"
        ),
    )
    if cap.headers.get("x-ratelimit-remaining") != "1, 2":
        bad.append(f"repeated headers are joined: {cap.headers.get('x-ratelimit-remaining')}")
    long_pair = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nx-ratelimit-remaining: "
        + b"7" * 400
        + b"\r\nx-ratelimit-remaining: "
        + b"8" * 400
        + b"\r\nConnection: close\r\n\r\n{}"
    )
    cap = fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(raw=long_pair))
    if len(cap.headers["x-ratelimit-remaining"]) != 512:
        bad.append(f"two long repeated headers are joined and cut: {len(cap.headers['x-ratelimit-remaining'])}")
    before = len(server.seen)
    cap = fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(302, b"", {"location": "http://127.0.0.1:1/elsewhere"}))
    if cap.status != 302 or len(server.seen) - before != 1 or "location" in cap.headers:
        bad.append(f"a redirect is a 302 capture and is not followed: {cap.status} {len(server.seen) - before}")
    cap = fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(502, b"<html>Bad Gateway</html>", {"content-type": "text/html"}))
    if (cap.status, cap.body) != (502, b"<html>Bad Gateway</html>"):
        bad.append("an HTML error page is kept as it came")
    cap = fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(204))
    if (cap.status, cap.body) != (204, b""):
        bad.append("an empty answer is an empty body")
    cap = fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(200, b'{"caf\xc3\xa9":1}'))
    if cap.body != b'{"caf\xc3\xa9":1}':
        bad.append("the body is bytes, not text")
    cap = fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(200, b"x", {"x-trace-id": "abc"}))
    if cap.trace_id != "abc":
        bad.append("the trace id is taken from the header")
    cap = fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(200, b"x", {}))
    if cap.trace_id is not None:
        bad.append("no trace header, no trace id")


def part_headers(mod: types.ModuleType, bad: list[str]) -> None:
    kept = mod._kept
    got = kept(
        [
            ("Content-Type", "application/json"),
            ("Content-Length", "10"),
            ("Date", "x"),
            ("Retry-After", " 5 "),
            ("X-Trace-Id", "abc"),
            ("Server", "s"),
            ("Via", "v"),
        ]
    )
    if got != {
        "content-type": "application/json",
        "content-length": "10",
        "date": "x",
        "retry-after": "5",
        "x-trace-id": "abc",
        "server": "s",
        "via": "v",
    }:
        bad.append(f"the kept headers: {got}")
    got = kept([("X-RateLimit-Limit", "60"), ("x-ratelimit-remaining", "5"), ("X-RateLimit-Reset", "9"), ("X-RateLimit-Anything", "1")])
    if sorted(got) != ["x-ratelimit-anything", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"]:
        bad.append(f"the rate headers: {sorted(got)}")
    for dropped in ("Set-Cookie", "Authorization", "X-Evil", "Location", "WWW-Authenticate", "Cookie", "X-Api-Key", "Transfer-Encoding"):
        if kept([(dropped, "1")]):
            bad.append(f"{dropped} must not be kept")
    if kept([("RETRY-AFTER", "5")]) != {"retry-after": "5"}:
        bad.append("header names are lower-cased")
    if kept([("x-ratelimit-remaining", "1"), ("X-RateLimit-Remaining", "2"), ("x-ratelimit-remaining", "3")]) != {
        "x-ratelimit-remaining": "1, 2, 3"
    }:
        bad.append("repeated headers are joined in order with a comma and a space")
    if len(kept([("x-ratelimit-remaining", "9" * 900)])["x-ratelimit-remaining"]) != 512:
        bad.append("a header value is cut at 512")
    if len(kept([("x-ratelimit-remaining", "7" * 400), ("x-ratelimit-remaining", "8" * 400)])["x-ratelimit-remaining"]) != 512:
        bad.append("a joined header value is cut at 512")
    if (
        kept([("x-ratelimit-remaining", "7" * 511)])["x-ratelimit-remaining"] != "7" * 511
        or len(kept([("x-ratelimit-remaining", "7" * 512)])["x-ratelimit-remaining"]) != 512
    ):
        bad.append("values up to the limit are kept whole")
    if kept([("retry-after", "  7  ")]) != {"retry-after": "7"}:
        bad.append("values are trimmed")
    if kept([]) != {}:
        bad.append("no headers, none kept")
    for name in ("content-type", "content-length", "date", "retry-after", "x-trace-id", "server", "via"):
        if name not in mod.KEPT_HEADERS:
            bad.append(f"{name} is on the list")
    if mod.KEPT_PREFIXES != ("x-ratelimit-", "x-mbx-used-weight", "ratelimit-"):
        bad.append(f"the prefixes that are kept: {mod.KEPT_PREFIXES}")
    got = kept(
        [
            ("X-MBX-USED-WEIGHT", "3"),
            ("x-mbx-used-weight-1m", "4"),
            ("X-MBX-UUID", "u"),
            ("x-mbx-order-count-10s", "1"),
            ("RateLimit-Remaining", "5"),
            ("ratelimit-reset", "9"),
        ]
    )
    if sorted(got) != ["ratelimit-remaining", "ratelimit-reset", "x-mbx-used-weight", "x-mbx-used-weight-1m"]:
        bad.append(f"the venues' rate headers: {sorted(got)}")


def part_config(mod: types.ModuleType, bad: list[str]) -> None:
    # --- configuration ------------------------------------------------------------------------------------------------------------------
    def config_refused(label: str, url: str, **kwargs: Any) -> None:
        try:
            Probe(mod)(url, lambda: KEY, clock, **kwargs)
            bad.append(f"{label}: accepted")
        except mod.TransportConfigError:
            pass

    config_refused("plain http to a remote host", "http://app-ryochan.com/api/mcp", allow_insecure_loopback=True)
    config_refused("plain http to loopback without asking", "http://127.0.0.1:1/api/mcp")
    config_refused("plain http to a look-alike of loopback", "http://127.0.0.1.evil.example/api/mcp", allow_insecure_loopback=True)
    config_refused("another scheme", "ftp://app-ryochan.com/api/mcp")
    config_refused("credentials in the URL", "https://user:pw@app-ryochan.com/api/mcp")
    config_refused("a user name in the URL", "https://user@app-ryochan.com/api/mcp")
    config_refused("a password alone in the URL", "https://:secret@app-ryochan.com/api/mcp")
    config_refused("a query", "https://app-ryochan.com/api/mcp?x=1")
    config_refused("a fragment", "https://app-ryochan.com/api/mcp#x")
    config_refused("no host", "https:///api/mcp")
    config_refused("a tiny body limit", "https://app-ryochan.com/api/mcp", max_body_bytes=100)
    for url in (
        "https://app-ryochan.com/api/mcp",
        "https://app-ryochan.com/api/mcp/",
        "http://localhost:8080/api/mcp",
        "http://[::1]:8080/api/mcp",
    ):
        try:
            Probe(mod)(url, lambda: KEY, clock, allow_insecure_loopback=True)
        except mod.TransportConfigError as exc:
            bad.append(f"{url}: refused ({exc})")
    try:
        Probe(mod)("https://app-ryochan.com/api/mcp", lambda: KEY, clock, max_body_bytes=1024)
    except mod.TransportConfigError:
        bad.append("a body limit of 1024 is allowed")
    for url in ("http://time.example.org/tsr", "http://127.0.0.1:1/tsr", "https://time.example.org/tsr"):
        try:
            Probe(mod)(url, lambda: KEY, clock, allow_plain_http=True)
        except mod.TransportConfigError as exc:
            bad.append(f"{url} with plain http allowed: refused ({exc})")
    config_refused("plain http to a remote host with only the loopback option", "http://time.example.org/tsr", allow_insecure_loopback=True)
    config_refused("another scheme with plain http allowed", "ftp://time.example.org/tsr", allow_plain_http=True)
    shown = Probe(mod)("https://app-ryochan.com/api/mcp/", lambda: KEY, clock)
    if repr(shown) != f"{type(shown).__name__}(https://app-ryochan.com/api/mcp)":
        bad.append(f"the representation drops the trailing slash and holds no key: {shown!r}")


def part_faults(mod: types.ModuleType, bad: list[str], rig: Rig) -> None:
    E = mod.ErrorClass
    server, fetch, fails = rig.server, rig.fetch, rig.fails
    small = Probe(mod)(server.url, lambda: KEY, clock, allow_insecure_loopback=True, max_body_bytes=4096)
    # --- failures of the exchange -----------------------------------------------------------------------------------------------------
    started = time.monotonic()
    exc = fails("a slow answer", E.TIMEOUT, timeout=0.3, behaviour=Behaviour(200, b"{}", delay_s=2.0))
    if time.monotonic() - started > 1.5 or exc is None:
        bad.append(f"the timeout was not enforced promptly: {time.monotonic() - started:.2f} s")
    started = time.monotonic()
    fails("a body that drips", E.TIMEOUT, timeout=0.4, behaviour=Behaviour(200, b"x" * 100, drip_s=0.05))
    if time.monotonic() - started > 1.5:
        bad.append(f"the deadline covers the whole exchange, not each read: {time.monotonic() - started:.2f} s")
    fails("a hang-up", E.UPSTREAM, behaviour=Behaviour(hang_up=True))
    fails("garbage instead of HTTP", E.UPSTREAM, behaviour=Behaviour(raw=b"this is not http\r\n\r\n"))
    fails("a body that stops early", E.UPSTREAM, behaviour=Behaviour(200, b"x" * 100, announce=500))
    fails(
        "a truncated chunked body",
        E.UPSTREAM,
        behaviour=Behaviour(raw=b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n10\r\nabc"),
    )
    if len(fetch("analyze_token", {"symbol": "SOL"}, behaviour=Behaviour(200, b"q" * 150_000)).body) != 150_000:
        bad.append("a body of several chunks is read whole")
    named = Probe(mod)(server.url, lambda: KEY, clock, provider="ryo-x", allow_insecure_loopback=True)
    if fetch("_health", {}, tp=named).source != "ryo-x":
        bad.append("the provider name is a parameter of the transport")
    slashed = Probe(mod)(server.url + "/", lambda: KEY, clock, allow_insecure_loopback=True)
    if fetch("analyze_token", {"symbol": "SOL"}, tp=slashed).status != 200 or server.seen[-1].path != "/api/mcp/tools/analyze_token/call":
        bad.append(f"a trailing slash in the base URL: {server.seen[-1].path}")
    if len(fetch("analyze_token", {"symbol": "SOL"}, tp=small, behaviour=Behaviour(200, b"x" * 4096)).body) != 4096:
        bad.append("a body at the limit is accepted")
    fails("a body one byte over the limit", E.MALFORMED, tp=small, behaviour=Behaviour(200, b"x" * 4097))
    fails("an enormous body", E.MALFORMED, tp=small, behaviour=Behaviour(200, b"y" * 1000, repeat=1000))
    if len(fetch("analyze_token", {"symbol": "SOL"}, tp=small, behaviour=Behaviour(200, b"z" * 300, repeat=10)).body) != 3000:
        bad.append("several chunks add up")
    tls = Probe(mod)(server.url.replace("http://", "https://"), lambda: KEY, clock)
    fails("TLS to a plain server", E.UNCLASSIFIED, tp=tls)
    if KEY in str(fails("a second TLS failure", E.UNCLASSIFIED, tp=tls)):
        bad.append("the key is in a TLS failure")


def part_hygiene(mod: types.ModuleType, bad: list[str], rig: Rig) -> None:
    server, script, fetch = rig.server, rig.script, rig.fetch
    small = Probe(mod)(server.url, lambda: KEY, clock, allow_insecure_loopback=True, max_body_bytes=4096)

    # --- the connection is bounded, closed and does not leave timers behind ----------------------------------------------------------------
    class Spy(Probe(mod)):
        timeouts: list[float] = []  # noqa: RUF012

        def _connection(self, timeout_s: float) -> Any:
            Spy.timeouts.append(timeout_s)
            return super()._connection(timeout_s)

    spy = Spy(server.url, lambda: KEY, clock, allow_insecure_loopback=True)
    fetch("_health", {}, timeout=7.0, tp=spy)
    if not Spy.timeouts or not 7.0 < Spy.timeouts[0] <= 7.0 * 2 + 2:
        bad.append(f"the connection's own timeout should back up the deadline, not replace it: {Spy.timeouts}")
    opened: list[http.client.HTTPConnection] = []

    class Tracked(Probe(mod)):
        def _connection(self, timeout_s: float) -> Any:
            conn = super()._connection(timeout_s)
            opened.append(conn)
            return conn

    tracked = Tracked(server.url, lambda: KEY, clock, allow_insecure_loopback=True)
    for behaviour in (Behaviour(hang_up=True), Behaviour(raw=b"garbage\r\n\r\n"), Behaviour(200, b"x" * 100, announce=500), None):
        with contextlib.suppress(mod.TransportFailure):
            fetch("analyze_token", {"symbol": "SOL"}, timeout=2.0, behaviour=behaviour, tp=tracked)
    if len(opened) != 4 or any(conn.sock is not None for conn in opened):
        bad.append(f"every connection is closed after its exchange: {[conn.sock is not None for conn in opened]}")
    gc.collect()
    threads_before = threading.active_count()
    for _ in range(12):
        fetch("_health", {}, timeout=60.0)
    time.sleep(0.2)
    if threading.active_count() - threads_before > 3:
        bad.append(f"timers are left behind: {threading.active_count() - threads_before} more threads")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for behaviour in (Behaviour(200, b"x" * 100, announce=500), Behaviour(hang_up=True), Behaviour(200, b"x" * 20_000), None):
            with contextlib.suppress(mod.TransportFailure):
                fetch("analyze_token", {"symbol": "SOL"}, timeout=2.0, behaviour=behaviour, tp=small)
        script["next"] = None
        time.sleep(0.3)  # let the cancelled timers finish and drop what they hold
        gc.collect()
        gc.collect()
    leaked = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
    if leaked:
        bad.append(f"sockets were left open: {leaked[:2]}")


def part_failure_map(mod: types.ModuleType, bad: list[str]) -> None:
    E = mod.ErrorClass
    # --- how exceptions become failures, one by one -------------------------------------------------------------------------------
    failure = mod.HttpTransport._failure
    for label, exc, expired, expected in (
        ("a timeout", TimeoutError(), False, E.TIMEOUT),
        ("an old-style timeout", TimeoutError(), False, E.TIMEOUT),
        ("the watchdog having fired", ConnectionResetError(), True, E.TIMEOUT),
        ("the watchdog having fired on any error", ValueError("x"), True, E.TIMEOUT),
        ("a TLS error", ssl.SSLError("x"), False, E.UNCLASSIFIED),
        ("a certificate error", ssl.SSLCertVerificationError("x"), False, E.UNCLASSIFIED),
        ("a name that does not resolve", socket.gaierror(8, "x"), False, E.UPSTREAM),
        ("a refused connection", ConnectionRefusedError(), False, E.UPSTREAM),
        ("a reset", ConnectionResetError(), False, E.UPSTREAM),
        ("a broken pipe", BrokenPipeError(), False, E.UPSTREAM),
        ("a remote that hung up", http.client.RemoteDisconnected("x"), False, E.UPSTREAM),
        ("a short read", http.client.IncompleteRead(b"a", 5), False, E.UPSTREAM),
        ("a bad status line", http.client.BadStatusLine("x"), False, E.UPSTREAM),
        ("another OS error", OSError(5, "x"), False, E.UPSTREAM),
        ("an unexpected error", ValueError("x"), False, E.UNCLASSIFIED),
        ("a runtime error", RuntimeError("x"), False, E.UNCLASSIFIED),
    ):
        got = failure(exc, expired, 3.0)
        if got.error_class is not expected:
            bad.append(f"{label}: {got.error_class.value}, expected {expected.value}")
        if KEY in got.detail:
            bad.append(f"{label}: the key is in the detail")
    if "3 s" not in failure(TimeoutError(), False, 3.0).detail:
        bad.append("a timeout says how long it waited")


def part_tls(mod: types.ModuleType, bad: list[str]) -> None:
    E = mod.ErrorClass
    # --- TLS ---------------------------------------------------------------------------------------------------------------------------------
    with FakeRyo(tls=True) as secure:
        trusting = Probe(mod)(secure.url, lambda: KEY, clock, ca_file=secure.cert_path)
        cap = trusting.fetch("analyze_token", {"symbol": "SOL"}, 5.0, new_capture_id(), T0)
        if cap.status != 200 or not str(cap.request["url"]).startswith("https://127.0.0.1:"):
            bad.append(f"a verified TLS exchange: {cap.status}")
        plain = Probe(mod)(secure.url, lambda: KEY, clock)
        try:
            plain.fetch("analyze_token", {"symbol": "SOL"}, 5.0, new_capture_id(), T0)
            bad.append("a certificate nobody trusts was accepted")
        except mod.TransportFailure as exc:
            if exc.error_class is not E.UNCLASSIFIED or KEY in exc.detail:
                bad.append(f"an untrusted certificate: {exc.error_class.value} {exc.detail}")
        if len(secure.seen) != 1:
            bad.append(f"the key must not reach a server whose certificate failed: {len(secure.seen)} requests")
    with FakeRyo(tls=True, cert_for_ip=False) as wrong_name:
        mismatched = Probe(mod)(wrong_name.url, lambda: KEY, clock, ca_file=wrong_name.cert_path)
        try:
            mismatched.fetch("analyze_token", {"symbol": "SOL"}, 5.0, new_capture_id(), T0)
            bad.append("a certificate for another name was accepted")
        except mod.TransportFailure as exc:
            if exc.error_class is not E.UNCLASSIFIED:
                bad.append(f"a certificate for another name: {exc.error_class.value}")
        if wrong_name.seen:
            bad.append("the key must not reach a server whose name did not match")


def part_closed_port(mod: types.ModuleType, bad: list[str]) -> None:
    E = mod.ErrorClass
    # --- a closed port ------------------------------------------------------------------------------------------------------------------
    with FakeRyo() as gone:
        dead_url = gone.url
    dead = Probe(mod)(dead_url, lambda: KEY, clock, allow_insecure_loopback=True)
    try:
        dead.fetch("analyze_token", {"symbol": "SOL"}, 2.0, new_capture_id(), T0)
        bad.append("a closed port answered")
    except mod.TransportFailure as exc:
        if exc.error_class is not E.UPSTREAM:
            bad.append(f"a closed port: {exc.error_class.value}")


def part_parallel(mod: types.ModuleType, bad: list[str], rig: Rig) -> None:
    server, script, transport, fetch = rig.server, rig.script, rig.transport, rig.fetch
    # --- the clock and parallel use ---------------------------------------------------------------------------------------------------------
    early = Probe(mod)(server.url, lambda: KEY, lambda: T0 - timedelta(seconds=30), allow_insecure_loopback=True)
    cap = fetch("_health", {}, tp=early)
    if cap.received_at != cap.t_send:
        bad.append("received_at is never before t_send")
    script["next"] = None
    with ThreadPoolExecutor(8) as pool:
        caps = list(pool.map(lambda i: transport.fetch("analyze_token", {"symbol": f"S{i}"}, 5.0, new_capture_id(), T0), range(8)))
    if len({c.capture_id for c in caps}) != 8 or any(c.status != 200 for c in caps):
        bad.append("eight parallel exchanges")
    if sorted(json.loads(c.body)["result"]["request"]["symbol"] for c in caps) != sorted(f"S{i}" for i in range(8)):
        bad.append("parallel exchanges got each other's answers")


RIG_PARTS = {"exchange": part_exchange, "faults": part_faults, "hygiene": part_hygiene, "parallel": part_parallel}
PLAIN_PARTS = {
    "headers": part_headers,
    "config": part_config,
    "failure_map": part_failure_map,
    "tls": part_tls,
    "closed_port": part_closed_port,
}
ALL_PARTS = (*RIG_PARTS, *PLAIN_PARTS)


def failures(mod: types.ModuleType, parts: tuple[str, ...] = ALL_PARTS) -> list[str]:
    bad: list[str] = []
    for name in parts:
        if name in RIG_PARTS:
            with Rig(mod, bad) as rig:
                RIG_PARTS[name](mod, bad, rig)
        else:
            PLAIN_PARTS[name](mod, bad)
    return bad
