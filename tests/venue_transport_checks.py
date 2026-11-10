"""Checks of the venue transport against a fake server on loopback, runnable against a mutant of ``venue_transport``."""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori import venue_base
from hoshitori.capture import new_capture_id
from hoshitori.http_transport import TransportConfigError
from hoshitori.venue_data import VenueDataError
from tests.ryo_fake_server import Behaviour, FakeRyo, Seen
from tests.venue_toy import toy_class

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def clock() -> datetime:
    return T0 + timedelta(seconds=1)


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    E = mod.ErrorClass
    Toy = toy_class(venue_base)
    toy = Toy()
    script: dict[str, Any] = {"next": None}

    def play(seen: Seen) -> Behaviour:
        step = script["next"]
        return step(seen) if callable(step) else (step or Behaviour(200, b"{}", {"content-type": "application/json"}))

    with FakeRyo(script=play) as server:
        transport = mod.VenueTransport(server.url, toy, clock, allow_insecure_loopback=True)
        args_c = toy.normalise(
            "candles", {"instrument": "BTCUSDT", "interval_s": 60, "start": "2026-10-29T12:00:00.000Z", "end": "2026-10-29T12:05:00.000Z"}
        )

        def fetch(operation: str, args: dict[str, Any], tp: Any = None) -> Any:
            return (tp or transport).fetch(operation, args, 5.0, new_capture_id(), T0)

        big_toy = type("Big", (Toy,), {"max_answer_bytes": 8 * 1024 * 1024})()
        sizes = (
            transport._max_body,
            mod.VenueTransport(server.url, big_toy, clock, allow_insecure_loopback=True)._max_body,
            mod.VenueTransport(server.url, big_toy, clock, allow_insecure_loopback=True, max_body_bytes=4096)._max_body,
        )
        if sizes != (4 * 1024 * 1024, 8 * 1024 * 1024, 4096):
            bad.append(f"the body limit comes from the adapter unless it is given: {sizes}")
        cap = fetch("time", {})
        seen = server.seen[-1]
        if (seen.method, seen.path, seen.body) != ("GET", "/api/mcp/time", b""):
            bad.append(f"time: {seen.method} {seen.path} {seen.body!r}")
        if "authorization" in seen.headers or "cookie" in seen.headers or "x-api-key" in seen.headers:
            bad.append(f"a public read must carry no credential: {sorted(seen.headers)}")
        if (
            seen.headers.get("accept") != "application/json"
            or seen.headers.get("accept-encoding") != "identity"
            or "content-type" in seen.headers
        ):
            bad.append(f"headers of a GET: {seen.headers}")
        if (cap.source, cap.status, cap.body) != ("toy", 200, b"{}") or cap.t_send != T0 or cap.received_at != clock():
            bad.append(f"the capture: {cap.source} {cap.status} {cap.body!r}")
        expected = {"arguments": {}, "method": "GET", "operation": "time", "surface": "rest", "url": f"{server.url}/time"}
        if dict(cap.request) != expected:
            bad.append(f"the capture's request: {dict(cap.request)}")
        cap = fetch("candles", args_c)
        if cap.request["operation"] != "candles" or dict(cap.request)["arguments"] != args_c:
            bad.append(f"the capture records the operation and the arguments asked for: {dict(cap.request)}")
        seen = server.seen[-1]
        want = "/api/mcp/candles?id=BTCUSDT&i=1m&from=2026-10-29T12:00:00.000Z&to=2026-10-29T12:05:00.000Z"
        if (seen.method, seen.path) != ("GET", want):
            bad.append(f"candles: {seen.method} {seen.path}")
        cap = fetch("instruments", {"kind": "perp"})
        seen = server.seen[-1]
        if (seen.method, seen.path, seen.body) != ("POST", "/api/mcp/instruments", b'{"kind":"perp"}') or seen.headers.get(
            "content-type"
        ) != "application/json":
            bad.append(f"instruments: {seen.method} {seen.path} {seen.body!r} {seen.headers.get('content-type')}")
        if dict(cap.request)["method"] != "POST" or dict(cap.request)["arguments"] != {"kind": "perp"}:
            bad.append("the request of a POST is recorded with its arguments")
        named = mod.VenueTransport(server.url, Toy(provider="toy-x"), clock, allow_insecure_loopback=True)
        if fetch("time", {}, named).source != "toy-x":
            bad.append("the capture carries the adapter's provider name")
        script["next"] = Behaviour(429, b'{"error":"slow"}', {"retry-after": "9", "x-mbx-used-weight-1m": "11", "set-cookie": "a=b"})
        cap = fetch("time", {})
        if (
            cap.status != 429
            or cap.headers.get("retry-after") != "9"
            or cap.headers.get("x-mbx-used-weight-1m") != "11"
            or "set-cookie" in cap.headers
        ):
            bad.append(f"a 429 passes through with screened headers: {cap.status} {dict(cap.headers)}")
        script["next"] = None

        # --- a request that cannot be built --------------------------------------------------------------------------------------------
        before = len(server.seen)
        for label, exc in (
            ("a data error", VenueDataError("SECRET-TEXT")),
            ("a key error", KeyError("SECRET-TEXT")),
            ("a value error", ValueError("SECRET-TEXT")),
            ("a type error", TypeError("SECRET-TEXT")),
            ("a connector error", mod.ConnectorError(E.INVALID_ARGS, "SECRET-TEXT")),
        ):

            class Breaks(Toy):  # type: ignore[misc,valid-type]
                def build_request(self, operation: str, args: Any, e: Exception = exc) -> Any:
                    raise e

            tp = mod.VenueTransport(server.url, Breaks(), clock, allow_insecure_loopback=True)
            try:
                fetch("time", {}, tp)
                bad.append(f"{label}: no failure")
            except mod.TransportFailure as failure:
                if failure.error_class is not E.INVALID_ARGS:
                    bad.append(f"{label}: classed {failure.error_class.value}")
                if "SECRET-TEXT" in failure.detail or failure.detail != f"the request could not be built: {type(exc).__name__}":
                    bad.append(f"{label}: the detail is {failure.detail!r}")
            except Exception as other:
                bad.append(f"{label}: raised {type(other).__name__}")
        if len(server.seen) != before:
            bad.append("a request that cannot be built must not be sent")

        class Crashes(Toy):  # type: ignore[misc,valid-type]
            def build_request(self, operation: str, args: Any) -> Any:
                raise RuntimeError("a bug in the adapter")

        try:
            fetch("time", {}, mod.VenueTransport(server.url, Crashes(), clock, allow_insecure_loopback=True))
            bad.append("a bug in the adapter was swallowed")
        except RuntimeError:
            pass
        except Exception as other:
            bad.append(f"a bug in the adapter raised {type(other).__name__}")

        # --- the base URL's prefix and the representation ---------------------------------------------------------------------------------------
        prefixed = mod.VenueTransport(server.url.replace("/api/mcp", "/v2/"), toy, clock, allow_insecure_loopback=True)
        script["next"] = None
        fetch("time", {}, prefixed)
        if server.seen[-1].path != "/v2/time":
            bad.append(f"a prefix in the base URL: {server.seen[-1].path}")
        if "VenueTransport(" not in repr(transport) or "127.0.0.1" not in repr(transport):
            bad.append(f"the representation: {transport!r}")
    try:
        mod.VenueTransport("http://app.example/api", toy, clock)
        bad.append("plain http to a remote host was accepted")
    except TransportConfigError:
        pass
    return bad
