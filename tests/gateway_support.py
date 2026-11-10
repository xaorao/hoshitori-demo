"""A simulated provider and a discrete-event driver for the gateway, so every scenario runs on simulated time.

Everything takes the gateway module as a parameter so the same scenarios run against the real module and against mutants of it.
"""

from __future__ import annotations

import heapq
import random
import types
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.governor import Governor, Priority, RateModel

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def seconds_of(moment: datetime) -> float:
    return (moment - T0).total_seconds()


class FakeAdapter:
    """An adapter for a provider with operations ``analyze`` (cost 1) and ``deep`` (cost 3); arguments are ``symbol`` text."""

    def __init__(
        self,
        mod: types.ModuleType,
        provider: str = "ryo",
        operations: dict[str, float] | None = None,
        cap_s: float = 60.0,
        min_s: float = 5.0,
    ) -> None:
        self.mod = mod
        self.provider = provider
        self._ops = operations or {"analyze": 1.0, "deep": 3.0}
        self._cap, self._min = cap_s, min_s

    def operations(self) -> frozenset[str]:
        return frozenset(self._ops)

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if set(args) - {"symbol", "note"} or not isinstance(args.get("symbol"), str) or not args["symbol"]:
            raise ConnectorError(ErrorClass.INVALID_ARGS, "symbol is required text")
        out = {"symbol": args["symbol"].upper()}
        if "note" in args:
            out["note"] = str(args["note"])
        return out

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return self._ops[operation]

    def attempt_cap_s(self, operation: str) -> float:
        return self._cap

    def min_attempt_s(self, operation: str) -> float:
        return self._min

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no response")
        cls = classify_status(capture.status)
        if cls is None:
            return None
        return ConnectorError(cls, f"HTTP {capture.status}", retry_after_s=parse_retry_after(capture.headers.get("retry-after"), now))

    def rate_observation(self, capture: RawCapture) -> Any:
        if capture.status is None:
            raise AssertionError("rate headers were read from a failure that had no response")
        h = capture.headers
        if "x-ratelimit-remaining" not in h:
            return None
        return self.mod.RateObservation(remaining=float(h["x-ratelimit-remaining"]))


def ok(body: bytes = b'{"status":"ok"}', headers: dict[str, str] | None = None, latency: float = 2.0) -> tuple[float, Any]:
    return latency, ("response", 200, body, headers or {})


def response(status: int, headers: dict[str, str] | None = None, body: bytes = b"", latency: float = 1.0) -> tuple[float, Any]:
    return latency, ("response", status, body, headers or {})


def transport(error_class: ErrorClass, latency: float = 1.0) -> tuple[float, Any]:
    return latency, ("transport", error_class)


class Harness:
    """The gateway under test plus a scripted provider, driven in simulated seconds from ``T0``."""

    def __init__(
        self,
        mod: types.ModuleType,
        tmp: Path,
        script: Callable[[Any], tuple[float, Any]] | None = None,
        *,
        limit: float = 6,
        window: float = 60,
        model: RateModel = RateModel.FIXED_WINDOW,
        concurrency: int = 3,
        breaker: BreakerConfig | None = None,
        seed: int = 1,
        adapter: FakeAdapter | None = None,
        rng: random.Random | None = None,
        start: datetime = T0,
        **kwargs: Any,
    ) -> None:
        self.mod = mod
        self.tmp = tmp
        self.store = CaptureStore(tmp / "captures")
        self.journal = mod.DispatchJournal(tmp / "dispatches.jsonl")
        self.gateway = mod.Gateway(self.store, self.journal, rng=rng or random.Random(seed), **kwargs)
        self.adapter = adapter or FakeAdapter(mod)
        cfg = breaker or BreakerConfig(consecutive_failures=3, cooldown_s=30)
        self.governor = Governor(self.adapter.provider, model, limit, window, start)
        self.breakers = BreakerSet(Breaker(self.adapter.provider, cfg, start), cfg)
        self.gateway.register(self.adapter, self.governor, self.breakers, concurrency=concurrency)
        self.script = script or (lambda d: ok())
        self.now = 0.0
        self.dispatched: list[tuple[float, Any]] = []
        self._heap: list[tuple[float, int, Any, Any]] = []
        self._n = 0
        self.holds: set[int] = set()  # call ids whose responses never arrive (a killed process)

    def call(
        self,
        op: str = "analyze",
        symbol: str = "SOL",
        priority: Priority = Priority.P0,
        deadline: float = 600,
        caller: str = "cycle",
        namespace: str | None = None,
        **kwargs: Any,
    ) -> Any:
        return self.mod.Call("ryo", op, {"symbol": symbol}, priority, at(deadline), caller, namespace or self.mod.LEAGUE, **kwargs)

    def submit(self, *args: Any, **kwargs: Any) -> int:
        call = args[0] if args and not isinstance(args[0], str) else self.call(*args, **kwargs)
        return self.gateway.submit(call, at(self.now))

    def result(self, call_id: int) -> Any:
        return self.gateway.result(call_id)

    def _launch(self, dispatches: list[Any]) -> None:
        for d in dispatches:
            self.dispatched.append((self.now, d))
            latency, kind = self.script(d)
            if d.call_id in self.holds:
                continue
            if latency > d.timeout_s:  # the client gives up
                self._push(round(self.now + d.timeout_s, 6), d, ("transport", ErrorClass.TIMEOUT))
            else:
                self._push(round(self.now + latency, 6), d, kind)

    def _push(self, when: float, d: Any, kind: Any) -> None:
        self._n += 1
        heapq.heappush(self._heap, (when, self._n, d, kind))

    def _deliver(self, d: Any, kind: Any) -> None:
        if kind[0] == "transport":
            self.gateway.finish(d, at(self.now), self.mod.TransportFailure(kind[1], "simulated"))
            return
        _, status, body, headers = kind
        capture = RawCapture(
            d.capture_id,
            d.provider,
            {"operation": d.operation},
            status,
            {k.lower(): v for k, v in headers.items()},
            body,
            d.started_at,
            at(self.now),
            None,
        )
        self.gateway.finish(d, at(self.now), capture)

    def run(self, until: float) -> None:
        """Advance simulated time to ``until``, dispatching and delivering as the gateway and the script dictate."""
        until = round(until, 6)  # datetimes have microseconds; a simulated second must be one the gateway can see
        for _ in range(10_000):
            self._launch(self.gateway.tick(at(self.now)))
            candidates = []
            if self._heap:
                candidates.append(self._heap[0][0])
            wake = self.gateway.next_wakeup(at(self.now))
            if wake is not None and wake > at(self.now):
                candidates.append(seconds_of(wake))
            candidates = [round(c, 6) for c in candidates if round(c, 6) >= self.now]
            if not candidates or min(candidates) > until:
                self.now = until
                self._launch(self.gateway.tick(at(self.now)))
                return
            self.now = round(max(self.now, min(candidates)), 6)
            while self._heap and self._heap[0][0] <= self.now:
                _, _, d, kind = heapq.heappop(self._heap)
                self._deliver(d, kind)
        raise RuntimeError("the simulation did not settle")

    def tick_at(self, seconds: float) -> None:
        """Tick the gateway at an exact time and hand any dispatches to the scripted provider (``run`` only ticks at events)."""
        self.now = round(max(self.now, seconds), 6)
        self._launch(self.gateway.tick(at(seconds)))

    def sent_times(self) -> list[float]:
        return [t for t, _ in self.dispatched]
