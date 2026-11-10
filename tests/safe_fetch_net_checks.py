"""Checks of the network side of the safe fetch client, runnable against a mutant of ``hoshitori.safe_fetch_net``.

The connection class and the sockets are replaced by scripts, so every rule of the module is exercised at speed and without a packet.
Real servers are used by ``test_safe_fetch_net.py``.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from hoshitori.fetch_types import FetchCode, FetchError, Target

HTTPS = Target("https://files.example/a?b=1", "https", "files.example", 443, "/a?b=1")
PLAIN = Target("http://files.example/a", "http", "files.example", 80, "/a")
PUBLIC = "93.184.216.34"
HEADERS = (("Content-Type", "text/html"), ("Content-Length", "5"))


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refused(self, label: str, call: Callable[[], object], code: FetchCode) -> None:
        try:
            call()
        except FetchError as exc:
            if exc.code is not code:
                self.bad.append(f"{label}: failed with {exc.code.value}, expected {code.value}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: did not fail (expected {code.value})")


class FakeSock:
    def __init__(self, owner: Any = None) -> None:
        self.owner = owner
        self.timeout: float | None = None
        self.closed = False
        self.shutdowns: list[int] = []

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def shutdown(self, how: int) -> None:
        self.shutdowns.append(how)
        if self.owner is not None:
            self.owner.aborted = True

    def close(self) -> None:
        self.closed = True


@dataclass
class Plan:
    status: int = 200
    headers: tuple[tuple[str, str], ...] = HEADERS
    chunks: list[bytes | Exception] = field(default_factory=lambda: [b"hello"])
    connect_error: Exception | None = None
    request_error: Exception | None = None
    response_error: Exception | None = None
    pause: float = 0.0  # a response that is slow to start, and ignores being cut off
    slow: bool = False  # a body that never ends, until the connection is cut


class FakeResponse:
    def __init__(self, plan: Plan, conn: FakeConn) -> None:
        self.status = plan.status
        self._headers = plan.headers
        self._chunks = list(plan.chunks)
        self._plan = plan
        self._conn = conn
        self.reads: list[int] = []

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)

    def read(self, amount: int) -> bytes:
        self.reads.append(amount)
        if self._plan.slow:
            for _ in range(300):
                if self._conn.aborted:
                    raise OSError("the connection was cut")
                time.sleep(0.01)
            return b""
        if not self._chunks:
            return b""
        item = self._chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeConn:
    plan: ClassVar[Plan] = Plan()
    made: ClassVar[list[FakeConn]] = []
    timers: ClassVar[list[bool]] = []
    before: ClassVar[set[threading.Thread]] = set()  # timers alive before the request: other tests may leave some behind

    def __init__(self, target: Target, addresses: Sequence[str], timeout_s: float, connect_s: float, context: Any) -> None:
        self.args = (target, tuple(addresses), timeout_s, connect_s, context)
        self.sock: FakeSock | None = None
        self.made_sock: FakeSock | None = None
        self.address = ""
        self.aborted = False
        self.closed = 0
        self.requests: list[tuple[str, str, dict[str, str] | None]] = []
        self.response: FakeResponse | None = None
        FakeConn.made.append(self)

    def connect(self) -> None:
        FakeConn.timers = [t.daemon for t in threading.enumerate() if isinstance(t, threading.Timer) and t not in FakeConn.before]
        if FakeConn.plan.connect_error is not None:
            raise FakeConn.plan.connect_error
        self.sock = FakeSock(self)
        self.made_sock = self.sock
        self.address = self.args[1][0] if self.args[1] else ""

    def request(self, method: str, path: str, headers: dict[str, str] | None = None) -> None:
        self.requests.append((method, path, headers))
        if FakeConn.plan.request_error is not None:
            raise FakeConn.plan.request_error

    def getresponse(self) -> FakeResponse:
        if FakeConn.plan.pause:
            time.sleep(FakeConn.plan.pause)
        if FakeConn.plan.response_error is not None:
            raise FakeConn.plan.response_error
        self.response = FakeResponse(FakeConn.plan, self)
        self.sock = None  # http.client forgets the socket once the response starts, when the server will close the connection
        return self.response

    def close(self) -> None:
        self.closed += 1


def run(
    mod: types.ModuleType,
    plan: Plan,
    *,
    target: Target = HTTPS,
    timeout_s: float = 5.0,
    limit: int = 1024,
    screen: Callable[..., bool] | None = None,
    hop: Any = None,
) -> Any:
    FakeConn.plan = plan
    FakeConn.made = []
    original = mod._Pinned
    mod._Pinned = FakeConn
    try:
        return (hop or mod.PinnedHop())(target, [PUBLIC, "8.8.8.8"], timeout_s, screen or (lambda s, h: True), limit)
    finally:
        mod._Pinned = original


# --- the connection --------------------------------------------------------------------------------------------------------------------
def check_connection(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    original = socket.create_connection
    calls: list[tuple[tuple[str, int], float | None]] = []
    socks: list[FakeSock] = []
    failing: set[str] = set()

    def create(address: tuple[str, int], timeout: float | None = None, **kwargs: Any) -> FakeSock:
        calls.append((address, timeout))
        if address[0] in failing:
            raise ConnectionRefusedError("refused")
        sock = FakeSock()
        socks.append(sock)
        return sock

    class Context:
        def __init__(self, error: Exception | None = None) -> None:
            self.error = error
            self.wrapped: list[tuple[Any, str | None]] = []

        def wrap_socket(self, sock: Any, server_hostname: str | None = None) -> Any:
            self.wrapped.append((sock, server_hostname))
            if self.error is not None:
                raise self.error
            return FakeSock()

    try:
        socket.create_connection = create  # type: ignore[assignment]
        context = Context()
        conn = mod._Pinned(HTTPS, ["1.1.1.1", "2.2.2.2"], 20.0, 5.0, context)
        c.eq("the Host header carries no port for https", (conn.default_port, conn.host, conn.port), (443, "files.example", 443))
        conn.connect()
        c.eq("the first address is tried first, with the connect limit", (calls, conn.address), ([(("1.1.1.1", 443), 5.0)], "1.1.1.1"))
        c.eq("the socket is given the whole limit", socks[0].timeout, 20.0)
        c.eq(
            "TLS is made over it for the host name that was asked for",
            (len(context.wrapped), context.wrapped[0][0] is socks[0], context.wrapped[0][1]),
            (1, True, "files.example"),
        )
        c.eq("the connection uses the wrapped socket", (conn.sock is not None and conn.sock is not socks[0]), True)
        calls.clear()
        socks.clear()
        failing.add("1.1.1.1")
        conn = mod._Pinned(HTTPS, ["1.1.1.1", "2.2.2.2"], 20.0, 5.0, context)
        conn.connect()
        c.eq("the next address is tried when one fails", ([a[0] for a, _ in calls], conn.address), (["1.1.1.1", "2.2.2.2"], "2.2.2.2"))
        calls.clear()
        conn = mod._Pinned(HTTPS, ["1.1.1.1"], 20.0, 5.0, context)
        try:
            conn.connect()
            c.bad.append("a connection to the only address that fails did not fail")
        except ConnectionRefusedError:
            pass
        except OSError as exc:
            c.bad.append(f"the last error is lost: {type(exc).__name__}")
        conn = mod._Pinned(HTTPS, [], 20.0, 5.0, context)
        try:
            conn.connect()
            c.bad.append("a connection to no address succeeded")
        except OSError as exc:
            c.eq("no address", str(exc), "no address to connect to")
        calls.clear()
        socks.clear()
        failing.clear()
        short = mod._Pinned(HTTPS, ["1.1.1.1"], 2.0, 5.0, context)
        short.connect()
        c.eq("the connect limit never exceeds the total", calls, [(("1.1.1.1", 443), 2.0)])
        calls.clear()
        socks.clear()
        refusing = Context(ssl.SSLError("bad certificate"))
        conn = mod._Pinned(HTTPS, ["1.1.1.1"], 20.0, 5.0, refusing)
        try:
            conn.connect()
            c.bad.append("a failed handshake did not fail")
        except ssl.SSLError:
            pass
        c.eq("a socket whose handshake failed is closed", [s.closed for s in socks], [True])
        c.eq("and the connection holds nothing", conn.sock, None)
        calls.clear()
        socks.clear()
        plain = mod._Pinned(PLAIN, ["1.1.1.1"], 20.0, 5.0, None)
        plain.connect()
        c.eq(
            "plain http: port 80 is the default and no TLS is made",
            (plain.default_port, calls, plain.sock is socks[0]),
            (80, [(("1.1.1.1", 80), 5.0)], True),
        )
    finally:
        socket.create_connection = original  # type: ignore[assignment]
    return c.bad


# --- one request -----------------------------------------------------------------------------------------------------------------------
def check_request(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    R = FetchCode
    c.eq(
        "the request headers",
        mod.REQUEST_HEADERS,
        {
            "User-Agent": "hoshitori-fetch/0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,text/plain;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    c.eq("the constants", (mod.CHUNK, mod.BACKSTOP, mod.USER_AGENT), (64 * 1024, 1.25, "hoshitori-fetch/0"))
    seen: list[tuple[int, tuple[tuple[str, str], ...]]] = []
    FakeConn.before = {t for t in threading.enumerate() if isinstance(t, threading.Timer)}
    got = run(mod, Plan(), timeout_s=4.0, screen=lambda s, h: seen.append((s, h)) or True)
    conn = FakeConn.made[0]
    c.eq("the answer", (got.status, got.headers, got.body, got.address), (200, HEADERS, b"hello", PUBLIC))
    c.eq("it was asked once, with a GET for the path and the fixed headers", conn.requests, [("GET", "/a?b=1", mod.REQUEST_HEADERS)])
    c.eq(
        "the connection got the target, the addresses, the time and the connect limit",
        conn.args[:4],
        (HTTPS, (PUBLIC, "8.8.8.8"), 6.0, 5.0),
    )
    c.eq("a TLS context for https", conn.args[4] is not None and isinstance(conn.args[4], ssl.SSLContext), True)
    c.eq("the screen saw the status and headers", seen, [(200, HEADERS)])
    c.eq("the connection was closed", conn.closed >= 1, True)
    c.eq("the watchdog is a daemon, so it cannot hold the process", FakeConn.timers, [True])
    run(mod, Plan(), target=PLAIN)
    c.eq("no TLS context for plain http", FakeConn.made[0].args[4], None)
    c.eq("the body is read in pieces of at most 64 KiB", FakeConn.made[0].response.reads[0], 64 * 1024)  # type: ignore[union-attr]
    got = run(mod, Plan(chunks=[b"he", b"l", b"lo"]))
    c.eq("a body in pieces", got.body, b"hello")
    got = run(
        mod, Plan(status=302, headers=(("Location", "/x"), ("Content-Length", "200")), chunks=[b"x" * 200]), screen=lambda s, h: False
    )
    c.eq("a body that is not wanted is not read", (got.status, got.body, FakeConn.made[0].response.reads), (302, b"", []))  # type: ignore[union-attr]
    c.refused("a screen that refuses", lambda: run(mod, Plan(), screen=_refusing), R.CONTENT_TYPE)
    c.eq("the connection is closed when the screen refuses", FakeConn.made[0].closed >= 1, True)

    # the body limit
    got = run(mod, Plan(headers=(), chunks=[b"x" * 512, b"y" * 512]), limit=1024)
    c.eq("a body of exactly the limit", len(got.body), 1024)
    c.refused(
        "a body one byte over the limit", lambda: run(mod, Plan(headers=(), chunks=[b"x" * 512, b"y" * 513]), limit=1024), R.TOO_LARGE
    )
    c.refused("a body over the limit in one piece", lambda: run(mod, Plan(headers=(), chunks=[b"x" * 5000]), limit=1024), R.TOO_LARGE)
    c.refused("a body that never ends", lambda: run(mod, Plan(headers=(), chunks=[b"x" * 100] * 100), limit=1024), R.TOO_LARGE)

    # a body shorter than it said
    c.refused("a short body", lambda: run(mod, Plan(headers=(("Content-Length", "100"),), chunks=[b"x" * 10])), R.TRUNCATED)
    c.eq("a body as long as announced", len(run(mod, Plan(headers=(("Content-Length", "10"),), chunks=[b"x" * 10])).body), 10)
    c.refused("an empty body that was announced", lambda: run(mod, Plan(headers=(("Content-Length", "10"),), chunks=[])), R.TRUNCATED)
    c.eq("a body of zero as announced", run(mod, Plan(headers=(("Content-Length", "0"),), chunks=[])).body, b"")
    c.eq("no announcement, no check", run(mod, Plan(headers=(), chunks=[b"abc"])).body, b"abc")
    c.eq("the name of the length is not case sensitive", len(run(mod, Plan(headers=(("content-length", "3"),), chunks=[b"abc"])).body), 3)
    c.refused(
        "a short body whatever the case of the name",
        lambda: run(mod, Plan(headers=(("CONTENT-LENGTH", "9"),), chunks=[b"abc"])),
        R.TRUNCATED,
    )
    c.eq(
        "a length that is not a number is the screen's business",
        run(mod, Plan(headers=(("Content-Length", "abc"),), chunks=[b"abc"])).body,
        b"abc",
    )
    c.eq(
        "a body that is not wanted may be shorter than announced",
        run(mod, Plan(status=301, headers=(("Content-Length", "50"),), chunks=[]), screen=lambda s, h: False).body,
        b"",
    )

    # failures, by where they happen
    c.refused("a refused connection", lambda: run(mod, Plan(connect_error=ConnectionRefusedError("no"))), R.CONNECT)
    c.refused("a failed handshake", lambda: run(mod, Plan(connect_error=ssl.SSLCertVerificationError("bad"))), R.TLS)
    c.refused("a request that fails", lambda: run(mod, Plan(request_error=BrokenPipeError("gone"))), R.CONNECT)
    c.refused("an answer that is not HTTP", lambda: run(mod, Plan(response_error=http.client.BadStatusLine("junk"))), R.PROTOCOL)
    c.refused("an answer that stops", lambda: run(mod, Plan(chunks=[b"he", http.client.IncompleteRead(b"he", 3)])), R.TRUNCATED)
    c.refused("a connection lost while reading", lambda: run(mod, Plan(chunks=[b"he", ConnectionResetError("reset")])), R.CONNECT)
    c.refused("a timeout from the socket", lambda: run(mod, Plan(chunks=[TimeoutError("slow")])), R.TIMEOUT)
    c.refused("a failure of our own code is not hidden", lambda: run(mod, Plan(chunks=[RuntimeError("bug")])), R.PROTOCOL)
    error = None
    try:
        run(mod, Plan(connect_error=ConnectionRefusedError("secret detail")))
    except FetchError as exc:
        error = exc
    c.eq(
        "the cause is not carried",
        (error is not None and error.__cause__ is None, error is not None and "secret detail" in str(error)),
        (True, False),
    )
    c.eq("the connection is closed after a failure", FakeConn.made[0].closed >= 1, True)

    # the deadline
    started = time.monotonic()
    c.refused("a body that never comes", lambda: run(mod, Plan(slow=True), timeout_s=0.2), R.TIMEOUT)
    c.eq("it is cut off at the deadline", time.monotonic() - started < 1.5, True)
    c.eq(
        "by shutting the socket, though the connection has forgotten it",
        (FakeConn.made[0].sock, FakeConn.made[0].made_sock is not None and FakeConn.made[0].made_sock.shutdowns != []),
        (None, True),
    )
    c.eq("and the connection is closed in the end", FakeConn.made[0].closed >= 1, True)
    c.refused(
        "a slow answer that finishes after the deadline is still too late", lambda: run(mod, Plan(pause=0.3), timeout_s=0.1), R.TIMEOUT
    )
    started = time.monotonic()
    c.eq("a fast answer is not delayed by the watchdog", run(mod, Plan(), timeout_s=5.0).status, 200)
    c.eq("and does not wait for it", time.monotonic() - started < 1.0, True)
    run(mod, Plan(), timeout_s=0.1)
    time.sleep(0.3)
    c.eq("the watchdog of a finished request does not cut anything later", FakeConn.made[0].made_sock.shutdowns, [])  # type: ignore[union-attr]
    return c.bad


def _refusing(status: int, headers: tuple[tuple[str, str], ...]) -> bool:
    raise FetchError(FetchCode.CONTENT_TYPE, "no")


def check_failures_and_setup(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    R = FetchCode
    failure = mod.PinnedHop._failure
    table: list[tuple[str, Exception, bool, FetchCode]] = [
        ("an expired watchdog beats everything", ConnectionResetError("x"), True, R.TIMEOUT),
        ("a socket timeout", TimeoutError("x"), False, R.TIMEOUT),
        ("a legacy socket timeout", TimeoutError("x"), False, R.TIMEOUT),
        ("an SSL error", ssl.SSLError("x"), False, R.TLS),
        ("a failed certificate", ssl.SSLCertVerificationError("x"), False, R.TLS),
        ("an incomplete read", http.client.IncompleteRead(b"a", 2), False, R.TRUNCATED),
        ("a bad status line", http.client.BadStatusLine("x"), False, R.PROTOCOL),
        ("too many headers", http.client.HTTPException("got more than 100 headers"), False, R.PROTOCOL),
        ("a value error", ValueError("x"), False, R.PROTOCOL),
        ("a refused connection", ConnectionRefusedError("x"), False, R.CONNECT),
        ("a reset", ConnectionResetError("x"), False, R.CONNECT),
        ("a server that hung up without answering", http.client.RemoteDisconnected("x"), False, R.CONNECT),
        ("a broken pipe", BrokenPipeError("x"), False, R.CONNECT),
        ("any other OS error", OSError("x"), False, R.CONNECT),
        ("a bug of ours", KeyError("x"), False, R.PROTOCOL),
    ]
    for label, exc, expired, code in table:
        got = failure(exc, expired, 3.0)
        c.eq(label, got.code, code)
    c.eq("an incomplete read says so", failure(http.client.IncompleteRead(b"a", 2), False, 3.0).detail, "the answer stopped before its end")
    c.eq("the time in a timeout", failure(TimeoutError(), False, 2.5).detail, "no complete answer within 2.5 s")
    c.eq("the cause of a TLS failure is named, not its text", failure(ssl.SSLError("secret"), False, 3.0).detail, "TLS failed: SSLError")
    c.eq(
        "the cause of a connection failure is named, not its text",
        failure(ConnectionResetError("secret"), False, 3.0).detail,
        "the connection failed: ConnectionResetError",
    )
    for value in (0, -1, -0.5):
        try:
            mod.PinnedHop(connect_timeout_s=value)
            c.bad.append(f"a connect timeout of {value} is accepted")
        except ValueError:
            pass
    try:
        mod.PinnedHop(connect_timeout_s=0.001)
    except ValueError:
        c.bad.append("a connect timeout of a millisecond is refused")
    try:
        mod.PinnedHop(ca_file="/nonexistent/certificate.pem")
        c.bad.append("a certificate file that is not there is ignored")
    except OSError:
        pass
    hop = mod.PinnedHop()
    c.eq("the default context verifies", (hop._context.verify_mode == ssl.CERT_REQUIRED, hop._context.check_hostname), (True, True))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_connection, check_request, check_failures_and_setup):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
