"""Checks of the RYO HTTP transport and the recorded transport against a fake server on loopback, runnable against a mutant."""

from __future__ import annotations

import json
import tempfile
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori.capture import CaptureStore, RawCapture, new_capture_id
from tests import ryo_data as D
from tests.ryo_fake_server import KEY, TRACE, Behaviour, FakeRyo, Seen, error

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def clock() -> datetime:
    return T0 + timedelta(seconds=1)


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
        self.transport = self.mod.RyoTransport(self.server.url, lambda: KEY, clock, allow_insecure_loopback=True)
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


def part_request(mod: types.ModuleType, bad: list[str], rig: Rig) -> None:
    E = mod.ErrorClass
    server, transport, fetch, fails = rig.server, rig.transport, rig.fetch, rig.fails
    # --- a tool call ----------------------------------------------------------------------------------------------------------------
    cap = fetch("analyze_token", {"symbol": "SOL"})
    seen = server.seen[-1]
    if (cap.status, cap.source, cap.t_send, cap.trace_id) != (200, "ryo", T0, TRACE):
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

    by_name = mod.RyoTransport(server.url.replace("127.0.0.1", "localhost"), lambda: KEY, clock, allow_insecure_loopback=True)
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
    nokey = mod.RyoTransport(server.url, lambda: None, clock, allow_insecure_loopback=True)
    if fetch("_health", {}, tp=nokey).status != 200:
        bad.append("health needs no key")
    fails("meta with arguments", E.INVALID_ARGS, "_health", {"x": 1}, requests=0)
    for op in ("../x", "A", "", "a b", "tools/../../etc", "analyze_token/call"):
        fails(f"the operation {op!r}", E.INVALID_ARGS, op, {}, requests=0)

    # --- the key --------------------------------------------------------------------------------------------------------------------
    for label, key in (
        ("no key", None),
        ("an empty key", ""),
        ("a short key", "abc"),
        ("a key with a space", "abcdefgh ijkl"),
        ("a key with a newline", "abcdefgh\r\nX-Evil: 1"),
        ("a non-ascii key", "abcdefghéé"),
        ("a key that is too long", "a" * 513),
    ):
        tp = mod.RyoTransport(server.url, lambda k=key: k, clock, allow_insecure_loopback=True)  # type: ignore[misc]
        fails(label, E.AUTH, tp=tp, requests=0)
    if (
        fetch(
            "analyze_token", {"symbol": "SOL"}, tp=mod.RyoTransport(server.url, lambda: "a" * 512, clock, allow_insecure_loopback=True)
        ).status
        != 401
    ):
        bad.append("a 512-character key is a usable header value; the server refuses it")
    if (
        fetch(
            "analyze_token", {"symbol": "SOL"}, tp=mod.RyoTransport(server.url, lambda: "a" * 8, clock, allow_insecure_loopback=True)
        ).status
        != 401
    ):
        bad.append("an 8-character key is a usable header value; the server refuses it")
    wrong = mod.RyoTransport(server.url, lambda: "fake-wrong-key-0123456789", clock, allow_insecure_loopback=True)
    cap = fetch("analyze_token", {"symbol": "SOL"}, tp=wrong)
    if cap.status != 401 or "www-authenticate" in cap.headers:
        bad.append(f"a wrong key is a 401 capture, not an exception: {cap.status}")
    if "fake-wrong-key" in repr(wrong) or KEY in repr(transport) or "127.0.0.1" not in repr(transport):
        bad.append(f"the representation: {transport!r}")

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


def part_keyfile(mod: types.ModuleType, bad: list[str]) -> None:
    # --- the key file ------------------------------------------------------------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        if mod.key_from_env_file(env) is not None:
            bad.append("a missing file has no key")
        env.write_text("# comment\nOTHER=1\n\nRYO_MCP_KEY = 'quoted-key-value'\nRYO_MCP_KEY=second\nNO_EQUALS\nEMPTY=\n")
        if mod.key_from_env_file(env) != "quoted-key-value":
            bad.append(f"a quoted key with spaces around: {mod.key_from_env_file(env)!r}")
        env.write_text('RYO_MCP_KEY="double"\n')
        if mod.key_from_env_file(env) != "double":
            bad.append("double quotes are stripped")
        env.write_text("RYO_MCP_KEY=plain\n")
        if mod.key_from_env_file(env) != "plain":
            bad.append("a plain value")
        env.write_text("RYO_MCP_KEY=\n")
        if mod.key_from_env_file(env) is not None:
            bad.append("an empty value is no key")
        env.write_text("RYO_MCP_KEY=''\n")
        if mod.key_from_env_file(env) is not None:
            bad.append("an empty quoted value is no key")
        env.write_text("#RYO_MCP_KEY=commented\nXRYO_MCP_KEY=other\n")
        if mod.key_from_env_file(env) is not None:
            bad.append("a commented line and a longer name are not the key")
        env.write_text("RYO_MCP_KEY\n# a line with no equals sign names it, then the real one\nRYO_MCP_KEY=real\n")
        if mod.key_from_env_file(env) != "real":
            bad.append(f"a line with no equals sign is skipped: {mod.key_from_env_file(env)!r}")
        env.write_text("RYO_MCP_KEY='mismatched\"\n")
        if mod.key_from_env_file(env) != "'mismatched\"":
            bad.append("mismatched quotes are left alone")
        env.write_text("RYO_MCP_KEY='\n")
        if mod.key_from_env_file(env) != "'":
            bad.append("a single quote character is a value")
        env.write_text("OTHER_KEY=abc\n")
        if mod.key_from_env_file(env, "OTHER_KEY") != "abc" or mod.key_from_env_file(env) is not None:
            bad.append("the name is a parameter")
        if mod.key_from_env_file(Path(tmp)) is not None:
            bad.append("a directory is not a key file")


def part_recorded(mod: types.ModuleType, bad: list[str]) -> None:
    E = mod.ErrorClass
    # --- recorded mode ---------------------------------------------------------------------------------------------------------------------------
    rec = mod.Recordings()
    good_body = D.rest_body("analyze_token")
    rec.add(
        "analyze_token",
        {"symbol": "SOL", "chain": "eth"},
        mod.Recorded(200, {"x-trace-id": "aabbccdd"}, good_body, "aabbccdd", "cap-" + "1" * 32),
    )
    if (
        len(rec) != 1
        or rec.lookup("analyze_token", {"chain": "eth", "symbol": "SOL"}) is None
        or rec.lookup("analyze_token", {"symbol": "SOL"}) is not None
    ):
        bad.append("recordings are looked up by operation and canonical arguments")
    if rec.lookup("scan_market", {"symbol": "SOL", "chain": "eth"}) is not None:
        bad.append("recordings are looked up by operation")
    recorded = mod.RecordedTransport(rec, clock)
    cap = recorded.fetch("analyze_token", {"symbol": "SOL", "chain": "eth"}, 1.0, new_capture_id(), T0)
    expected_request = {
        "arguments": {"chain": "eth", "symbol": "SOL"},
        "mode": "recorded",
        "operation": "analyze_token",
        "recorded_from": "cap-" + "1" * 32,
        "surface": "rest",
    }
    if (cap.source, cap.status, cap.body, cap.trace_id, dict(cap.headers)) != (
        "ryo-recorded",
        200,
        good_body,
        "aabbccdd",
        {"x-trace-id": "aabbccdd"},
    ) or dict(cap.request) != expected_request:
        bad.append(f"a recorded capture: {cap.source} {dict(cap.request)}")
    if cap.t_send != T0 or cap.received_at != clock():
        bad.append("a recorded capture is timed now, not when it was first recorded")
    try:
        recorded.fetch("analyze_token", {"symbol": "AVAX"}, 1.0, new_capture_id(), T0)
        bad.append("a request with no recording was answered")
    except mod.TransportFailure as exc:
        if exc.error_class is not E.NOT_FOUND:
            bad.append(f"no recording: {exc.error_class.value}")
    late = mod.RecordedTransport(rec, lambda: T0 - timedelta(seconds=5))
    if late.fetch("analyze_token", {"symbol": "SOL", "chain": "eth"}, 1.0, new_capture_id(), T0).received_at != T0:
        bad.append("a recorded capture is never received before it was sent")
    if (
        mod.RecordedTransport(rec, clock, provider="x")
        .fetch("analyze_token", {"symbol": "SOL", "chain": "eth"}, 1.0, new_capture_id(), T0)
        .source
        != "x"
    ):
        bad.append("the provider name of a recorded transport")

    with tempfile.TemporaryDirectory() as tmp, CaptureStore(Path(tmp) / "c") as store:

        def put(
            operation: Any,
            arguments: Any,
            status: int,
            body: bytes,
            source: str = "ryo",
            headers: dict[str, str] | None = None,
            trace: str | None = None,
        ) -> str:
            request: dict[str, Any] = {}
            if operation is not None:
                request["operation"] = operation
            if arguments is not None:
                request["arguments"] = arguments
            cap = RawCapture(new_capture_id(), source, request, status, headers or {}, body, T0, T0, trace)
            store.append(cap)
            return cap.capture_id

        first = put("analyze_token", {"symbol": "SOL"}, 200, D.rest_body("analyze_token", D.result("analyze_token", warnings=["first"])))
        last = put(
            "analyze_token",
            {"symbol": "SOL"},
            200,
            D.rest_body("analyze_token", D.result("analyze_token", warnings=["last"])),
            headers={"x-trace-id": "bb"},
            trace="bb",
        )
        put("analyze_token", {"symbol": "SOL"}, 503, b"{}")  # a later failure does not replace the last good one
        put(
            "analyze_token", {"symbol": "SOL"}, 200, D.rest_body("analyze_token", D.result("analyze_token", schema_version="2.0"))
        )  # nor does drift
        put(
            "analyze_token",
            {"symbol": "AVAX"},
            200,
            D.rest_body("analyze_token", D.result("analyze_token", request={"symbol": "AVAX"})),
            source="someone-else",
        )
        put("scan_market", {"top_n": 5}, 200, D.rest_body("scan_market"))
        put("scan_market", {"top_n": 3}, 200, b"<html>")
        put("deep_analysis", {"symbol": "SOL"}, 200, D.rest_body("deep_analysis", D.result("deep_analysis", data={"asset": "no"})))
        put("_health", {}, 200, b'{"status":"ok"}')
        put("_health", {}, 503, b'{"status":"ok","note":"from a failure"}')  # a later failure does not replace the good read
        put("_catalogue", {}, 200, b"[]")
        put(
            "_whoami",
            {},
            200,
            b'{"key_id":"5ebd7286","scopes":["tools:read"],"expires_at":"2026-12-17T02:33:00Z","rate_limit_per_min":60,"tools":["a"]}',
        )
        put("_whoami", {"x": 1}, 200, b'{"key_id":5}')
        put("check_safety", {"symbol": "SOL"}, 200, D.rest_body("analyze_token"))
        put(None, {"symbol": "SOL"}, 200, D.rest_body("analyze_token"))
        put("analyze_token", None, 200, D.rest_body("analyze_token"))
        put("market_overview", {}, 200, D.rest_body("market_overview"), source="ryo")
        recs = mod.Recordings.from_store(store)
        found = recs.lookup("analyze_token", {"symbol": "SOL"})
        if (
            found is None
            or found.capture_id != last
            or json.loads(found.body)["result"]["warnings"] != ["last"]
            or found.trace_id != "bb"
            or found.capture_id == first
        ):
            bad.append("the last good capture of a request is the recording")
        for operation, arguments, expect in (
            ("analyze_token", {"symbol": "AVAX"}, False),
            ("scan_market", {"top_n": 5}, True),
            ("scan_market", {"top_n": 3}, False),
            ("deep_analysis", {"symbol": "SOL"}, False),
            ("_health", {}, True),
            ("_catalogue", {}, False),
            ("_whoami", {}, True),
            ("_whoami", {"x": 1}, False),
            ("check_safety", {"symbol": "SOL"}, False),
            ("market_overview", {}, True),
        ):
            if (recs.lookup(operation, arguments) is not None) is not expect:
                bad.append(f"from_store: {operation} {arguments} should {'be' if expect else 'not be'} a recording")
        if len(recs) != 5:
            bad.append(f"from_store kept {len(recs)} recordings, expected 5")
        health = recs.lookup("_health", {})
        if health is None or health.status != 200 or health.body != b'{"status":"ok"}':
            bad.append("a failed read does not replace a good one")
        other = mod.Recordings.from_store(store, provider="someone-else")
        if len(other) != 1 or other.lookup("analyze_token", {"symbol": "AVAX"}) is None:
            bad.append("from_store takes the provider as a parameter")
    return bad


RIG_PARTS = {"request": part_request}
PLAIN_PARTS = {"keyfile": part_keyfile, "recorded": part_recorded}
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
